"""Federated sign-in, with the providers themselves stubbed.

The stub replaces exactly one thing: the two network calls to the provider.
Everything else — the ``state``/``nonce``/PKCE round trip, the single-use
attempt row, account linking, session issuance — is the real code path, because
that is where the interesting mistakes live.

The parts a stub cannot check — ID-token signature, issuer, audience — are
covered by the pure tests at the bottom, which exercise the issuer and profile
rules directly. Verifying a real signature would mean minting our own tokens
and re-implementing the verifier in the test.
"""

from collections.abc import Iterator
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from myntos.api.v1 import auth as auth_endpoints
from myntos.auth.oidc import OidcClient, OidcError, OidcProfile
from myntos.auth.oidc.providers import (
    GOOGLE_ISSUERS,
    microsoft_issuer,
    microsoft_profile,
    microsoft_well_known,
)
from tests.conftest import PASSWORD, TEST_ORIGIN, register, write

MICROSOFT_TEMPLATE = "https://login.microsoftonline.com/{tenantid}/v2.0"
TENANT = "72f988bf-86f1-41af-91ab-2d7cd011db47"


class StubProvider:
    """Stands in for a provider's authorization and token endpoints."""

    def __init__(self, code: str = "google") -> None:
        self.code = code
        self.profile = OidcProfile(
            subject=f"{code}-sub-1",
            email="kari@gmail.com",
            email_verified=True,
            full_name="Kari Nordmann",
        )
        self.expected_nonce: str | None = None
        self.exchanges: list[str] = []
        self.fail_exchange = False

    async def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        self.expected_nonce = nonce
        assert code_challenge  # PKCE challenge must be present
        return f"https://{self.code}.test/authorize?state={state}"

    async def exchange_code(self, *, code: str, code_verifier: str, nonce: str) -> OidcProfile:
        self.exchanges.append(code)
        if self.fail_exchange:
            raise OidcError("simulated failure")
        # The backend must send back the verifier and nonce belonging to the
        # attempt that started the flow.
        assert code_verifier
        assert nonce == self.expected_nonce
        return self.profile


@pytest.fixture
def provider(client: TestClient) -> Iterator[StubProvider]:
    stub = StubProvider()
    app = cast(Any, client.app)
    app.dependency_overrides[auth_endpoints._client_for] = lambda: cast(OidcClient, stub)
    yield stub
    app.dependency_overrides.pop(auth_endpoints._client_for, None)


def _start(client: TestClient, provider: str = "google", **params: str) -> str:
    """Begin the flow and return the ``state`` the backend generated."""
    response = client.get(f"/api/v1/auth/{provider}/start", params=params, follow_redirects=False)
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    state: str = parse_qs(urlparse(location).query)["state"][0]
    return state


def _callback(client: TestClient, provider: str = "google", **params: str) -> Any:
    return client.get(f"/api/v1/auth/{provider}/callback", params=params, follow_redirects=False)


# --- The flow ---------------------------------------------------------------------


def test_first_login_creates_account_and_organization(
    client: TestClient, provider: StubProvider
) -> None:
    state = _start(client)
    callback = _callback(client, code="auth-code", state=state)
    assert callback.status_code == 303
    assert callback.headers["location"] == "https://app.example/"

    body = client.get("/api/v1/auth/me").json()
    assert body["user"]["email"] == "kari@gmail.com"
    # The provider verified the address, so there is nothing left to confirm.
    assert body["user"]["email_verified"] is True
    # No password: this account can only be reached through the provider so far.
    assert body["user"]["has_password"] is False
    assert body["user"]["providers"] == ["google"]
    # Signing in federated still lands you in an organization.
    assert len(body["memberships"]) == 1
    assert body["memberships"][0]["role"] == "owner"


def test_second_login_reuses_the_same_account(client: TestClient, provider: StubProvider) -> None:
    _callback(client, code="code-1", state=_start(client))
    first_user_id = client.get("/api/v1/auth/me").json()["user"]["id"]
    client.cookies.clear()

    # Same account at the provider, different email there: the subject is what
    # identifies the account, so this must not create a second one.
    provider.profile = OidcProfile(
        subject="google-sub-1",
        email="kari.nordmann@gmail.com",
        email_verified=True,
        full_name="Kari Nordmann",
    )
    _callback(client, code="code-2", state=_start(client))

    body = client.get("/api/v1/auth/me").json()
    assert body["user"]["id"] == first_user_id
    assert body["user"]["email"] == "kari@gmail.com"
    assert len(body["memberships"]) == 1


def test_a_provider_links_to_an_existing_password_account(
    client: TestClient, provider: StubProvider
) -> None:
    """One account, two ways in — not two accounts."""
    registered = register(client, "kari@gmail.com", organization_name="Fjord Regnskap AS")
    client.cookies.clear()

    _callback(client, code="auth-code", state=_start(client))
    body = client.get("/api/v1/auth/me").json()

    assert body["user"]["id"] == registered["user"]["id"]
    assert body["user"]["has_password"] is True
    assert body["user"]["providers"] == ["google"]
    assert body["memberships"][0]["organization_name"] == "Fjord Regnskap AS"

    # And the password still works afterwards.
    client.cookies.clear()
    relogin = write(
        client,
        "POST",
        "/api/v1/auth/login",
        json={"email": "kari@gmail.com", "password": PASSWORD},
    )
    assert relogin.status_code == 200


def test_unverified_email_cannot_take_over_an_existing_account(
    client: TestClient, provider: StubProvider
) -> None:
    """The account-takeover case, refused. This is nOAuth.

    If an unverified provider email were trusted, anyone able to set another
    person's address on an account at the provider could sign in as them.
    """
    register(client, "kari@gmail.com")
    client.cookies.clear()

    provider.profile = OidcProfile(
        subject="attacker-sub",
        email="kari@gmail.com",
        email_verified=False,
        full_name="Ikke Kari",
    )
    callback = _callback(client, code="auth-code", state=_start(client))
    assert callback.status_code == 303
    assert "error=unverified_provider_email" in callback.headers["location"]
    # No session was issued.
    assert client.get("/api/v1/auth/me").status_code == 401


def test_unverified_email_still_gets_its_own_new_account(
    client: TestClient, provider: StubProvider
) -> None:
    """Nothing to take over, so nothing to refuse.

    Microsoft never asserts email verification unless ``xms_edov`` is switched
    on, and refusing here would block those users entirely while buying no
    security: the address is not attached to anything. The account is recorded
    as unverified, which is the honest state.
    """
    provider.profile = OidcProfile(
        subject="ny-sub",
        email="ingen@example.no",
        email_verified=False,
        full_name="Ingen Nordmann",
    )
    callback = _callback(client, code="auth-code", state=_start(client))
    assert callback.status_code == 303

    body = client.get("/api/v1/auth/me").json()
    assert body["user"]["email"] == "ingen@example.no"
    assert body["user"]["email_verified"] is False


def test_callback_is_single_use(client: TestClient, provider: StubProvider) -> None:
    """Replaying the callback URL must not mint a second session."""
    state = _start(client)
    assert _callback(client, code="auth-code", state=state).status_code == 303
    client.cookies.clear()

    replay = _callback(client, code="auth-code", state=state)
    assert "error=innlogging_utlopt" in replay.headers["location"]
    assert client.get("/api/v1/auth/me").status_code == 401
    # The code was never exchanged a second time.
    assert provider.exchanges == ["auth-code"]


def test_unknown_state_is_rejected(client: TestClient, provider: StubProvider) -> None:
    # A callback the backend never started — no attempt row, so no exchange.
    response = _callback(client, code="auth-code", state="fabricated-state")
    assert "error=innlogging_utlopt" in response.headers["location"]
    assert provider.exchanges == []


def test_an_attempt_cannot_be_completed_with_a_different_provider(
    client: TestClient, provider: StubProvider
) -> None:
    """A state issued for Google must not be redeemable at Microsoft's callback."""
    state = _start(client, "google")
    response = _callback(client, "microsoft", code="auth-code", state=state)
    assert "error=innlogging_utlopt" in response.headers["location"]
    assert provider.exchanges == []
    assert client.get("/api/v1/auth/me").status_code == 401


def test_user_declining_at_the_provider_lands_on_the_login_page(
    client: TestClient, provider: StubProvider
) -> None:
    response = _callback(client, error="access_denied")
    assert response.status_code == 303
    assert "error=innlogging_avbrutt" in response.headers["location"]


def test_failed_token_exchange_does_not_sign_anyone_in(
    client: TestClient, provider: StubProvider
) -> None:
    provider.fail_exchange = True
    response = _callback(client, code="auth-code", state=_start(client))
    assert "error=innlogging_feilet" in response.headers["location"]
    assert client.get("/api/v1/auth/me").status_code == 401


def test_relative_redirect_is_honoured(client: TestClient, provider: StubProvider) -> None:
    state = _start(client, "google", redirect_to="/regnskap/2026/")
    callback = _callback(client, code="auth-code", state=state)
    assert callback.headers["location"] == "https://app.example/regnskap/2026/"


@pytest.mark.parametrize("hostile", ["https://evil.example/", "//evil.example/", "/\\evil.example"])
def test_absolute_redirect_is_discarded(
    client: TestClient, provider: StubProvider, hostile: str
) -> None:
    """No open redirect: the sign-in link must not be able to point elsewhere.

    It would be a redirect that fires at the exact moment a fresh session
    cookie is set, from our own domain.
    """
    state = _start(client, "google", redirect_to=hostile)
    callback = _callback(client, code="auth-code", state=state)
    assert callback.headers["location"] == "https://app.example/"


def test_federated_signin_does_not_need_an_origin_header(
    client: TestClient, provider: StubProvider
) -> None:
    # The whole flow is GET navigation from the provider's domain, so the
    # origin check must not apply to it.
    state = _start(client)
    callback = client.get(
        "/api/v1/auth/google/callback",
        params={"code": "auth-code", "state": state},
        headers={"Origin": "https://accounts.google.test"},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert client.get("/api/v1/auth/me").status_code == 200


def test_a_federated_account_can_join_an_organization_by_invitation(
    client: TestClient, provider: StubProvider
) -> None:
    owner = register(client, "kari@example.no", organization_name="Fjord Regnskap AS")
    organization_id = owner["memberships"][0]["organization_id"]
    invitation = write(
        client,
        "POST",
        f"/api/v1/organizations/{organization_id}/invitations",
        json={"email": "kari@gmail.com", "role": "admin"},
    ).json()

    invitee = TestClient(cast(Any, client.app), base_url=TEST_ORIGIN)
    _callback(invitee, code="auth-code", state=_start(invitee))
    token = invitation["invite_url"].split("token=", maxsplit=1)[1]
    accepted = write(invitee, "POST", "/api/v1/auth/invitations/accept", json={"token": token})
    assert accepted.status_code == 200
    roles = {
        membership["organization_id"]: membership["role"]
        for membership in accepted.json()["memberships"]
    }
    assert roles[organization_id] == "admin"


# --- Provider rules (pure) ---------------------------------------------------------


def test_google_accepts_both_spellings_of_its_issuer() -> None:
    assert {"https://accounts.google.com", "accounts.google.com"} == GOOGLE_ISSUERS


def test_microsoft_issuer_resolves_the_tenant_template() -> None:
    claims = {"tid": TENANT, "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0"}
    assert microsoft_issuer(MICROSOFT_TEMPLATE, claims)


@pytest.mark.parametrize(
    "claims",
    [
        # Issuer from a different tenant than the token claims.
        {"tid": TENANT, "iss": "https://login.microsoftonline.com/other/v2.0"},
        # No tenant at all.
        {"iss": "https://login.microsoftonline.com/anything/v2.0"},
        # A tid that is not a GUID — the guard that stops a crafted value from
        # rewriting the rest of the issuer URL.
        {
            "tid": "evil.example/v2.0#",
            "iss": "https://login.microsoftonline.com/evil.example/v2.0#/v2.0",
        },
        # The unresolved template itself must never match.
        {"tid": TENANT, "iss": MICROSOFT_TEMPLATE},
    ],
)
def test_microsoft_issuer_rejects_a_mismatch(claims: dict[str, Any]) -> None:
    assert not microsoft_issuer(MICROSOFT_TEMPLATE, claims)


def test_microsoft_single_tenant_issuer_is_plain_equality() -> None:
    concrete = f"https://login.microsoftonline.com/{TENANT}/v2.0"
    assert microsoft_issuer(concrete, {"iss": concrete})
    assert not microsoft_issuer(concrete, {"iss": "https://login.microsoftonline.com/x/v2.0"})


def test_microsoft_email_is_unverified_without_xms_edov() -> None:
    """Entra ID never sends email_verified; absence must not read as verified."""
    profile = microsoft_profile({"sub": "abc", "email": "kari@firma.no", "name": "Kari"})
    assert profile.email_verified is False
    assert profile.email == "kari@firma.no"


def test_microsoft_email_is_verified_with_xms_edov() -> None:
    profile = microsoft_profile({"sub": "abc", "email": "kari@firma.no", "xms_edov": True})
    assert profile.email_verified is True


def test_microsoft_falls_back_to_preferred_username() -> None:
    """Work accounts frequently carry the address there and not in 'email'."""
    profile = microsoft_profile({"sub": "abc", "preferred_username": "kari@firma.no"})
    assert profile.email == "kari@firma.no"


def test_microsoft_without_any_address_is_refused() -> None:
    with pytest.raises(OidcError):
        microsoft_profile({"sub": "abc", "preferred_username": "kari"})


def test_microsoft_well_known_follows_the_tenant() -> None:
    assert microsoft_well_known("common").startswith(
        "https://login.microsoftonline.com/common/v2.0/"
    )
    assert TENANT in microsoft_well_known(TENANT)
