"""Sign-up, sign-in and session handling over HTTP."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from myntos.core.config import get_settings
from tests.conftest import PASSWORD, TEST_ORIGIN, register, write


def test_register_creates_user_organization_and_session(client: TestClient) -> None:
    body = register(client, organization_name="Nordmann Regnskap AS")

    assert body["user"]["email"] == "kari@example.no"
    # A password signup is not proof of the address; verification is a separate
    # step (and the mail is not sent yet).
    assert body["user"]["email_verified"] is False
    assert body["user"]["has_password"] is True

    # Signup always lands the user on an account: it is what a company is
    # created under, and there is nowhere to keep books otherwise.
    assert len(body["memberships"]) == 1
    membership = body["memberships"][0]
    assert membership["role"] == "owner"
    # And that is very nearly all the session says about it. The account's name
    # is deliberately not here: nothing displays it, because an account is
    # routinely called after a company and a name like that shown next to a
    # different company reads as a leak. What a person may do with a set of
    # books, and what it costs, come back with the books.
    assert set(membership) == {"organization_id", "role", "permissions"}

    account = client.get("/api/v1/organizations").json()[0]
    assert account["name"] == "Nordmann Regnskap AS"
    assert account["slug"] == "nordmann-regnskap-as"
    assert "plan" not in account

    # The token is in an httpOnly cookie, not in the body.
    assert body["session_token"] is None
    assert client.cookies.get("myntos_session")


def test_register_without_organization_name_still_gets_one(client: TestClient) -> None:
    body = register(client)
    assert len(body["memberships"]) == 1
    assert client.get("/api/v1/organizations").json()[0]["slug"]


def test_norwegian_letters_survive_slug_generation(client: TestClient) -> None:
    register(client, organization_name="Håkon Bærum Ølsen AS")
    account = client.get("/api/v1/organizations").json()[0]
    # "å" transliterates to "aa" rather than being stripped to "a".
    assert account["slug"] == "haakon-baerum-oelsen-as"


def test_duplicate_email_is_rejected(client: TestClient) -> None:
    register(client)
    response = write(
        client,
        "POST",
        "/api/v1/auth/register",
        json={"email": "KARI@example.no", "password": PASSWORD, "full_name": "Kari"},
    )
    # Case-folded: the address is the same account even in different case.
    assert response.status_code == 409
    assert response.json()["code"] == "email_already_registered"


def test_weak_password_is_rejected_before_anything_is_created(client: TestClient) -> None:
    response = write(
        client,
        "POST",
        "/api/v1/auth/register",
        json={"email": "kari@example.no", "password": "kort", "full_name": "Kari"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "weak_password"
    # And the address is still free afterwards.
    register(client)


def test_login_and_me(client: TestClient) -> None:
    register(client)
    client.cookies.clear()

    response = write(
        client,
        "POST",
        "/api/v1/auth/login",
        json={"email": "kari@example.no", "password": PASSWORD},
    )
    assert response.status_code == 200, response.text

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "kari@example.no"
    assert me.json()["memberships"][0]["role"] == "owner"


def test_wrong_password_and_unknown_email_are_indistinguishable(client: TestClient) -> None:
    """The same 401 body for both, so the API cannot be used to enumerate accounts."""
    register(client)
    wrong = write(
        client,
        "POST",
        "/api/v1/auth/login",
        json={"email": "kari@example.no", "password": "feil-passord-her"},
    )
    unknown = write(
        client,
        "POST",
        "/api/v1/auth/login",
        json={"email": "ingen@example.no", "password": "feil-passord-her"},
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.headers["WWW-Authenticate"] == "Bearer"


def test_repeated_failures_lock_the_account(client: TestClient) -> None:
    """The failed-login counter has to survive the requests that failed.

    The request-scoped transaction rolls back when the handler raises, so
    without a deliberate commit the counter would reset on every attempt and the
    lockout would never trigger. This test is what catches that regression.
    """
    register(client)
    for _ in range(3):  # LOGIN_MAX_FAILED_ATTEMPTS in conftest
        response = write(
            client,
            "POST",
            "/api/v1/auth/login",
            json={"email": "kari@example.no", "password": "feil-passord-her"},
        )
        assert response.status_code == 401

    locked = write(
        client,
        "POST",
        "/api/v1/auth/login",
        json={"email": "kari@example.no", "password": PASSWORD},
    )
    # Correct password, still refused: the lock is on the account, not the guess.
    assert locked.status_code == 429
    assert locked.json()["code"] == "account_locked"


def test_me_requires_a_session(client: TestClient) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401


def test_logout_revokes_the_session(client: TestClient) -> None:
    register(client)
    assert write(client, "POST", "/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_a_logged_out_token_stays_dead(client: TestClient) -> None:
    """Revocation is server-side, so replaying the cookie must not work.

    This is the property a stateless JWT could not give us.
    """
    body = register(client)
    assert body["session_token"] is None
    token = client.cookies.get("myntos_session")
    write(client, "POST", "/api/v1/auth/logout")
    client.cookies.clear()

    replayed = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert replayed.status_code == 401


def test_bearer_token_works_for_non_browser_clients(client: TestClient) -> None:
    register(client)
    client.cookies.clear()
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "kari@example.no", "password": PASSWORD, "issue_bearer_token": True},
        headers={"Origin": TEST_ORIGIN},
    )
    token = response.json()["session_token"]
    assert token
    client.cookies.clear()

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200


def test_cookie_writes_require_a_trusted_origin(client: TestClient) -> None:
    """CSRF defence: a cookie-authorised write with a foreign Origin is refused."""
    register(client)
    response = client.post(
        "/api/v1/organizations",
        json={"name": "Smuglet inn"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403

    # Reads are unaffected — they change nothing.
    assert (
        client.get("/api/v1/auth/me", headers={"Origin": "https://evil.example"}).status_code == 200
    )


def test_cookie_writes_require_an_origin_at_all(client: TestClient) -> None:
    # No Origin header on a state-changing request fails closed.
    register(client)
    response = client.post("/api/v1/organizations", json={"name": "Uten opphav"})
    assert response.status_code == 403


def test_bearer_writes_need_no_origin(client: TestClient) -> None:
    # A bearer token has to be attached deliberately, so it is not CSRF-exposed.
    register(client)
    client.cookies.clear()
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "kari@example.no", "password": PASSWORD, "issue_bearer_token": True},
        headers={"Origin": TEST_ORIGIN},
    )
    token = login.json()["session_token"]
    client.cookies.clear()

    response = client.post(
        "/api/v1/organizations",
        json={"name": "Via bearer"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201


def test_password_change_keeps_this_session_and_kills_the_others(client: TestClient) -> None:
    register(client)
    other = TestClient(client.app, base_url=TEST_ORIGIN)
    other_login = other.post(
        "/api/v1/auth/login",
        json={"email": "kari@example.no", "password": PASSWORD},
        headers={"Origin": TEST_ORIGIN},
    )
    assert other_login.status_code == 200

    changed = write(
        client,
        "POST",
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "et-helt-nytt-passord-2026"},
    )
    assert changed.status_code == 204

    # The session that made the change survives...
    assert client.get("/api/v1/auth/me").status_code == 200
    # ...and every other session is gone, which is the point after a compromise.
    assert other.get("/api/v1/auth/me").status_code == 401

    client.cookies.clear()
    relogin = write(
        client,
        "POST",
        "/api/v1/auth/login",
        json={"email": "kari@example.no", "password": "et-helt-nytt-passord-2026"},
    )
    assert relogin.status_code == 200


def test_password_change_requires_the_current_password(client: TestClient) -> None:
    register(client)
    response = write(
        client,
        "POST",
        "/api/v1/auth/password",
        json={"current_password": "ikke-riktig-passord", "new_password": "et-helt-nytt-passord"},
    )
    assert response.status_code == 401


def test_providers_endpoint_reports_nothing_unconfigured(client: TestClient) -> None:
    # No provider credentials in the test environment, so the UI must be told
    # not to offer buttons rather than showing ones that 503.
    body: dict[str, Any] = client.get("/api/v1/auth/providers").json()
    assert body == {
        "password": True,
        "password_min_length": get_settings().password_min_length,
        "providers": [],
        # The test mailer counts as configured; see RecordingMailer.
        "email_delivery": True,
    }


@pytest.mark.parametrize("provider", ["google", "microsoft"])
def test_provider_start_is_unavailable_without_configuration(
    client: TestClient, provider: str
) -> None:
    response = client.get(f"/api/v1/auth/{provider}/start", follow_redirects=False)
    assert response.status_code == 503


def test_an_unknown_provider_is_not_a_route(client: TestClient) -> None:
    assert client.get("/api/v1/auth/facebook/start", follow_redirects=False).status_code == 404
