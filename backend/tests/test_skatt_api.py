"""The ID-porten login that authorizes a submission.

What these tests are really guarding is that a *government* authorization cannot
be obtained by accident: not from a Myntos session alone, not from a replayed
callback URL, not for a different user, and not at an assurance level below the
one Skatteetaten requires. Each of those is a single ``if`` in the route, and
each would be silently absent if nothing asserted it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from myntos.api.v1 import skatt as skatt_endpoints
from myntos.auth.idporten.client import GovernmentLogin, IdPortenError
from myntos.auth.idporten.vault import (
    TokenVaultError,
    VaultNotConfiguredError,
    open_sealed,
    seal,
)
from myntos.core.clock import utc_now
from myntos.core.config import get_settings
from tests.conftest import register

PID = "08866995401"
KEY = Fernet.generate_key().decode()


# --- The vault -------------------------------------------------------------------


def test_a_sealed_token_comes_back_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """The round trip, which is the only thing the rest of the flow relies on."""
    monkeypatch.setenv("GOVERNMENT_TOKEN_KEY", KEY)
    get_settings.cache_clear()
    blob = seal("government-token")
    assert blob != b"government-token"
    assert open_sealed(blob) == "government-token"
    get_settings.cache_clear()


def test_no_key_means_no_storage_rather_than_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing key disables the feature; it never falls back to storing it raw.

    This is the whole security posture of the module in one assertion. A helpful
    fallback here would put credentials that authorize filing a tax return into
    a readable column.
    """
    monkeypatch.setenv("GOVERNMENT_TOKEN_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(VaultNotConfiguredError):
        seal("government-token")
    get_settings.cache_clear()


def test_a_token_sealed_under_another_key_is_an_error_not_a_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rotating the key invalidates in-flight logins, and says so.

    Returning ``None`` would look exactly like "not logged in", which would send
    somebody hunting through the auth flow for a key change.
    """
    monkeypatch.setenv("GOVERNMENT_TOKEN_KEY", KEY)
    get_settings.cache_clear()
    blob = seal("government-token")
    monkeypatch.setenv("GOVERNMENT_TOKEN_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    with pytest.raises(TokenVaultError):
        open_sealed(blob)
    get_settings.cache_clear()


# --- An unconfigured deployment --------------------------------------------------


def test_status_needs_a_myntos_login(client: TestClient) -> None:
    """The government login is an addition to being signed in, not an alternative."""
    assert client.get("/api/v1/skatt/status").status_code == 401


def test_an_unconfigured_deployment_says_unavailable(client: TestClient) -> None:
    """The frontend must not offer a login it cannot finish.

    Reported rather than 500 or an empty answer: "we do not do this here" is a
    different thing from "you are not logged in", and the UI renders them
    differently.
    """
    register(client)
    body = client.get("/api/v1/skatt/status").json()
    assert body == {
        "available": False,
        "logged_in": False,
        "pid": None,
        "acr": None,
        "expires_at": None,
        # The durable link is reported whatever the deployment does with
        # ID-porten today: turning the feature off does not un-prove what
        # somebody already showed.
        "linked": False,
        "linked_name": None,
        "linked_at": None,
    }


def test_starting_a_login_that_cannot_finish_is_refused_up_front(
    client: TestClient,
) -> None:
    """503 before the redirect, not a failure after BankID.

    Sending somebody through a real authentication and then failing wastes an
    action they cannot undo, and teaches them the product is broken.
    """
    register(client)
    response = client.get("/api/v1/skatt/idporten/start", follow_redirects=False)
    assert response.status_code == 503


# --- A configured deployment -----------------------------------------------------


class StubIdPorten:
    """ID-porten, as much of it as these routes touch."""

    def __init__(self) -> None:
        self.login = GovernmentLogin(
            access_token="government-token",
            expires_at=utc_now() + timedelta(minutes=30),
            pid=PID,
            acr="idporten-loa-high",
            subject="subject-1",
            refresh_token=None,
        )
        self.error: Exception | None = None
        self.seen: dict[str, Any] = {}

    async def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        self.seen["state"] = state
        self.seen["nonce"] = nonce
        self.seen["code_challenge"] = code_challenge
        return f"https://test.idporten.no/authorize?state={state}"

    async def exchange_code(self, *, code: str, code_verifier: str, nonce: str) -> GovernmentLogin:
        self.seen["code"] = code
        self.seen["code_verifier"] = code_verifier
        if self.error is not None:
            raise self.error
        return self.login


@pytest.fixture
def idporten(monkeypatch: pytest.MonkeyPatch) -> Iterator[StubIdPorten]:
    """A configured government login, with ID-porten itself stubbed."""
    monkeypatch.setenv("GOVERNMENT_TOKEN_KEY", KEY)
    get_settings.cache_clear()
    stub = StubIdPorten()
    monkeypatch.setattr(skatt_endpoints, "get_client", lambda: stub)
    monkeypatch.setattr(skatt_endpoints, "is_configured", lambda *a, **k: True)
    yield stub
    get_settings.cache_clear()


def _start(client: TestClient) -> str:
    """Begin a login and return the ``state`` it parked."""
    response = client.get("/api/v1/skatt/idporten/start", follow_redirects=False)
    assert response.status_code == 303, response.text
    location = str(response.headers["location"])
    assert location.startswith("https://test.idporten.no/authorize")
    return location.split("state=")[1]


def test_a_started_login_redirects_to_idporten_with_pkce(
    client: TestClient, idporten: StubIdPorten
) -> None:
    """State, nonce and a PKCE challenge all have to reach ID-porten."""
    register(client)
    _start(client)
    assert idporten.seen["state"]
    assert idporten.seen["nonce"]
    assert idporten.seen["code_challenge"]
    # Distinct values, not one token reused for three purposes.
    assert idporten.seen["state"] != idporten.seen["nonce"]


def test_a_completed_login_is_usable_and_names_the_person(
    client: TestClient, idporten: StubIdPorten
) -> None:
    """The point of the whole flow: an authorization the submission code can use."""
    register(client)
    state = _start(client)
    response = client.get(
        f"/api/v1/skatt/idporten/callback?code=auth-code&state={state}",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "https://app.example/organisasjon/"

    body = client.get("/api/v1/skatt/status").json()
    assert body["available"] is True
    assert body["logged_in"] is True
    assert body["pid"] == PID
    assert body["acr"] == "idporten-loa-high"


def test_the_status_endpoint_never_returns_the_token(
    client: TestClient, idporten: StubIdPorten
) -> None:
    """Nothing outside the server has any use for it.

    Asserted on the serialised body rather than the model, because a field added
    later would leak through the response before anybody re-read the schema.
    """
    register(client)
    state = _start(client)
    client.get(
        f"/api/v1/skatt/idporten/callback?code=auth-code&state={state}",
        follow_redirects=False,
    )
    raw = client.get("/api/v1/skatt/status").text
    assert "government-token" not in raw


def test_a_replayed_callback_is_refused(client: TestClient, idporten: StubIdPorten) -> None:
    """The attempt is single use, stamped before the code is exchanged."""
    register(client)
    state = _start(client)
    url = f"/api/v1/skatt/idporten/callback?code=auth-code&state={state}"
    assert client.get(url, follow_redirects=False).status_code == 303
    replay = client.get(url, follow_redirects=False)
    assert replay.headers["location"].endswith("error=innlogging_utlopt")


def test_an_unknown_state_is_refused(client: TestClient, idporten: StubIdPorten) -> None:
    """A callback nobody started cannot produce an authorization."""
    register(client)
    response = client.get(
        "/api/v1/skatt/idporten/callback?code=auth-code&state=never-issued",
        follow_redirects=False,
    )
    assert response.headers["location"].endswith("error=innlogging_utlopt")


def test_too_low_an_assurance_level_stores_nothing(
    client: TestClient, idporten: StubIdPorten, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skatteetaten requires high, and the check is on what was *reached*.

    A MinID login returns a perfectly valid token at a level that may not file
    anything. Storing it would push the failure to submission time, where the
    person has already reviewed a melding and pressed send.
    """
    monkeypatch.setenv("IDPORTEN_ACR_VALUES", "idporten-loa-high")
    get_settings.cache_clear()
    register(client)
    idporten.login = GovernmentLogin(
        access_token="government-token",
        expires_at=utc_now() + timedelta(minutes=30),
        pid=PID,
        acr="idporten-loa-substantial",
        subject="subject-1",
        refresh_token=None,
    )
    state = _start(client)
    response = client.get(
        f"/api/v1/skatt/idporten/callback?code=auth-code&state={state}",
        follow_redirects=False,
    )
    assert response.headers["location"].endswith("error=for_lav_sikkerhet")
    assert client.get("/api/v1/skatt/status").json()["logged_in"] is False


def test_a_failed_exchange_reports_our_own_error_code(
    client: TestClient, idporten: StubIdPorten
) -> None:
    """ID-porten's text is never reflected into our page."""
    register(client)
    state = _start(client)
    idporten.error = IdPortenError("token endpoint said something detailed")
    response = client.get(
        f"/api/v1/skatt/idporten/callback?code=auth-code&state={state}",
        follow_redirects=False,
    )
    location = response.headers["location"]
    assert location.endswith("error=innlogging_feilet")
    assert "detailed" not in location


def test_a_refusal_is_not_reported_as_a_cancelled_login(
    client: TestClient, idporten: StubIdPorten
) -> None:
    """An unregistered scope is our fault, and must not read as the user's.

    ID-porten refuses the whole authorization request when any requested scope
    is not on the client, and answers by redirecting here with
    ``error=invalid_scope``. Reporting that as "innloggingen ble avbrutt" tells
    the person to try again at something that cannot work, and tells whoever
    has to fix it to look at the wrong thing entirely.
    """
    register(client)
    _start(client)
    response = client.get(
        "/api/v1/skatt/idporten/callback"
        "?error=invalid_scope&error_description=Scope+altinn%3Aenduser+not+registered",
        follow_redirects=False,
    )
    location = response.headers["location"]
    assert location.endswith("error=innlogging_ikke_mulig")
    # ID-porten's own wording names scopes and client ids. It belongs in the
    # log, not on a page in front of a customer.
    assert "altinn" not in location
    assert "Scope" not in location


def test_a_failure_returns_to_where_the_login_started(
    client: TestClient, idporten: StubIdPorten
) -> None:
    """The success path always honoured redirect_to; the failure path did not.

    Somebody who pressed "Bekreft selskapet" on /organisasjon/ was dropped on a
    different page holding a message about something they had not done.
    """
    register(client)
    started = client.get(
        "/api/v1/skatt/idporten/start?redirect_to=/organisasjon/%23selskap",
        follow_redirects=False,
    )
    state = started.headers["location"].split("state=")[1]
    # ID-porten returns ``state`` on a refusal too, which is the only reason a
    # failure can find its way back to the page the login started from.
    response = client.get(
        f"/api/v1/skatt/idporten/callback?error=access_denied&state={state}",
        follow_redirects=False,
    )
    # The fragment stays last, or the browser keeps it and drops the query.
    assert response.headers["location"] == (
        "https://app.example/organisasjon/?error=innlogging_avbrutt#selskap"
    )


def test_a_cancelled_login_is_not_an_error_page(client: TestClient, idporten: StubIdPorten) -> None:
    """ID-porten returns ``error`` when the person backs out. Ordinary, not broken."""
    register(client)
    _start(client)
    response = client.get(
        "/api/v1/skatt/idporten/callback?error=access_denied", follow_redirects=False
    )
    assert response.headers["location"].endswith("error=innlogging_avbrutt")


def test_one_user_s_authorization_is_not_another_s(
    client: TestClient, idporten: StubIdPorten
) -> None:
    """The token belongs to the person who authorized it, not to the deployment.

    A regnskapsforer's colleague signing in must not inherit a government login
    somebody else completed - that would be filing in a name nobody consented
    to.
    """
    register(client, "kari@example.no")
    state = _start(client)
    client.get(
        f"/api/v1/skatt/idporten/callback?code=auth-code&state={state}",
        follow_redirects=False,
    )
    assert client.get("/api/v1/skatt/status").json()["logged_in"] is True

    client.cookies.clear()
    register(client, "ola@example.no", organization_name="Ola AS")
    assert client.get("/api/v1/skatt/status").json()["logged_in"] is False


def test_an_expired_token_is_not_a_login(client: TestClient, idporten: StubIdPorten) -> None:
    """ID-porten's own expiry is respected rather than re-decided locally."""
    register(client)
    idporten.login = GovernmentLogin(
        access_token="government-token",
        expires_at=utc_now() - timedelta(seconds=1),
        pid=PID,
        acr="idporten-loa-high",
        subject="subject-1",
        refresh_token=None,
    )
    state = _start(client)
    client.get(
        f"/api/v1/skatt/idporten/callback?code=auth-code&state={state}",
        follow_redirects=False,
    )
    assert client.get("/api/v1/skatt/status").json()["logged_in"] is False


def test_a_crafted_redirect_target_is_dropped(client: TestClient, idporten: StubIdPorten) -> None:
    """Otherwise the callback is an open redirect on our own domain."""
    register(client)
    response = client.get(
        "/api/v1/skatt/idporten/start?redirect_to=https://evil.example",
        follow_redirects=False,
    )
    state = response.headers["location"].split("state=")[1]
    done = client.get(
        f"/api/v1/skatt/idporten/callback?code=auth-code&state={state}",
        follow_redirects=False,
    )
    assert done.headers["location"] == "https://app.example/organisasjon/"


def test_a_site_relative_redirect_target_is_kept(
    client: TestClient, idporten: StubIdPorten
) -> None:
    """The legitimate use: back to the melding the person was working on."""
    register(client)
    response = client.get(
        "/api/v1/skatt/idporten/start?redirect_to=/skatt/mva/2025-1",
        follow_redirects=False,
    )
    state = response.headers["location"].split("state=")[1]
    done = client.get(
        f"/api/v1/skatt/idporten/callback?code=auth-code&state={state}",
        follow_redirects=False,
    )
    assert done.headers["location"] == "https://app.example/skatt/mva/2025-1"


def test_the_stored_token_is_not_readable_in_the_database(
    client: TestClient, idporten: StubIdPorten, database_url: str
) -> None:
    """The reason the column exists at all is that this holds.

    Read straight out of the table rather than through the vault: the assertion
    is about what a database dump would contain.
    """
    import sqlalchemy as sa

    register(client)
    state = _start(client)
    client.get(
        f"/api/v1/skatt/idporten/callback?code=auth-code&state={state}",
        follow_redirects=False,
    )
    engine = sa.create_engine(database_url.replace("sqlite+aiosqlite", "sqlite"))
    with engine.connect() as connection:
        stored = connection.execute(
            sa.text("SELECT sealed_token FROM government_login_attempt")
        ).scalar_one()
    engine.dispose()
    assert stored is not None
    assert b"government-token" not in stored
    assert open_sealed(stored) == "government-token"


def test_the_origin_check_does_not_block_the_callback(
    client: TestClient, idporten: StubIdPorten
) -> None:
    """ID-porten sends a cross-site top-level GET with no Origin we control.

    A GET is a safe method so the CSRF guard does not apply, and this asserts it
    stays that way - tightening the guard to all methods would break the flow in
    a way only a live login would reveal.
    """
    register(client)
    state = _start(client)
    response = client.get(
        f"/api/v1/skatt/idporten/callback?code=auth-code&state={state}",
        headers={"Origin": "https://test.idporten.no"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "https://app.example/organisasjon/"
