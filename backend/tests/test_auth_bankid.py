"""BankID sign-in through a broker, end to end against a stub of the broker.

Unlike test_auth_oidc.py, which replaces the whole OIDC client, these tests run
the real one: discovery, the key set, the token request with a
``private_key_jwt`` assertion, the ID token's signature, issuer, audience and
nonce. Only the network is replaced, by :class:`StubBroker`, which answers the
way Idura's documentation and its public sample domain's discovery document
say Idura answers (docs/ARCHITECTURE.md, "Federated providers").

What is under test is the set of rules a provider without an email address
needs, because none of the address-based ones can apply to it:

- a login button signs in a linked account and never creates one;
- linking happens from a signed-in account, and only finishes on that account;
- a signup asks for an address, refuses one that already has an account, and
  sends the ordinary confirmation mail to one that does not;
- the fødselsnummer is never asked for, and never kept if it arrives anyway;

and the one BankID's merchant terms add (docs/integrations/bankid.md): an
account that signs in with BankID signs in with nothing else. No password from a
reset, no Google beside it, and the two switches between BankID and e-mail each
take away what the other way in was.
"""

import base64
import hashlib
import logging
import time
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from myntos.api.v1 import auth as auth_endpoints
from myntos.auth.oidc import OidcClient, OidcProfile, reset_clients
from myntos.auth.oidc import client as oidc_client
from myntos.auth.oidc.providers import bankid_issuer, bankid_profile
from myntos.auth.tokens import hash_token
from myntos.core.clock import utc_now
from myntos.core.config import get_settings
from tests.conftest import PASSWORD, TEST_ORIGIN, RecordingMailer, register, write
from tests.test_auth_oidc import StubProvider

ISSUER = "https://myntos.test.idura.broker"
CLIENT_ID = "urn:my:application:myntos-test"
REDIRECT_URI = "https://testserver/api/v1/auth/bankid/callback"
CLIENT_KID = "myntos-test-key"
BROKER_KID = "broker-key-1"
SUBSTANTIAL = "urn:grn:authn:no:bankid:substantial"

# The claims Idura's documentation shows for a BankID Biometrics login, minus
# the fødselsnummer, which only comes with the ``ssn`` scope. Used as given, so
# a test that wants one more claim adds it rather than rebuilding the list.
KARI_BANKID: dict[str, Any] = {
    "identityscheme": "nobankid-oidc",
    "authenticationtype": SUBSTANTIAL,
    "sub": "{cde37629-c67b-4318-988c-a0b378931e7d}",
    "uniqueuserid": "9578-6000-4-476957",
    "birthdate": "1941-08-16",
    "family_name": "Nordmann",
    "given_name": "Kari",
    "name": "Kari Nordmann",
    "country": "NO",
}
OLA_BANKID: dict[str, Any] = {
    **KARI_BANKID,
    "sub": "{ee9b1bb9-05a6-458e-9f3b-9d068f1a3765}",
    "uniqueuserid": "9578-6000-4-351726",
    "given_name": "Ola",
    "name": "Ola Nordmann",
}


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _public_jwk(key: rsa.RSAPrivateKey, kid: str) -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }


class StubBroker:
    """Idura, as far as the backend can tell.

    The browser's visit is :meth:`login`: it reads the authorization request
    the backend built, the way the broker would, and hands back the code and
    state the broker would redirect with. The backend's own calls arrive at
    :meth:`handle`, and the token endpoint refuses exactly what a real one
    refuses: a client assertion that does not verify, and a PKCE verifier that
    does not match the challenge.
    """

    def __init__(self, broker_key: rsa.RSAPrivateKey, client_key: rsa.RSAPrivateKey) -> None:
        self.broker_key = broker_key
        self.client_public = client_key.public_key()
        self.codes: dict[str, dict[str, Any]] = {}
        self.authorize_requests: list[dict[str, str]] = []
        self.token_requests: list[dict[str, str]] = []

    def login(self, authorize_url: str, claims: dict[str, Any]) -> dict[str, str]:
        parsed = urlparse(authorize_url)
        assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == f"{ISSUER}/oauth2/authorize"
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        self.authorize_requests.append(query)
        code = uuid.uuid4().hex
        self.codes[code] = {
            "claims": claims,
            "nonce": query["nonce"],
            "challenge": query["code_challenge"],
        }
        return {"code": code, "state": query["state"]}

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{ISSUER}/.well-known/openid-configuration":
            # The fields the backend reads, with the values Idura's sample
            # domain publishes, moved onto this test's domain.
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/oauth2/authorize",
                    "token_endpoint": f"{ISSUER}/oauth2/token",
                    "jwks_uri": f"{ISSUER}/.well-known/jwks",
                    "token_endpoint_auth_methods_supported": [
                        "client_secret_basic",
                        "client_secret_post",
                        "private_key_jwt",
                    ],
                    "id_token_signing_alg_values_supported": ["RS256"],
                    "code_challenge_methods_supported": ["S256", "plain"],
                    "acr_values_supported": ["urn:grn:authn:no:bankid", SUBSTANTIAL],
                },
            )
        if url == f"{ISSUER}/.well-known/jwks":
            return httpx.Response(200, json={"keys": [_public_jwk(self.broker_key, BROKER_KID)]})
        if url == f"{ISSUER}/oauth2/token" and request.method == "POST":
            return self._token(request)
        return httpx.Response(404)

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        self.token_requests.append(form)
        if form.get("client_assertion_type") != (
            "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        ):
            return httpx.Response(401, json={"error": "invalid_client"})
        try:
            assertion = jwt.decode(
                form["client_assertion"],
                self.client_public,
                algorithms=["RS256"],
                audience=ISSUER,
                issuer=CLIENT_ID,
            )
        except jwt.PyJWTError:
            return httpx.Response(401, json={"error": "invalid_client"})
        if assertion["sub"] != CLIENT_ID or not assertion.get("jti"):
            return httpx.Response(401, json={"error": "invalid_client"})
        if jwt.get_unverified_header(form["client_assertion"]).get("kid") != CLIENT_KID:
            return httpx.Response(401, json={"error": "invalid_client"})

        issued = self.codes.pop(form.get("code", ""), None)
        if issued is None or form.get("redirect_uri") != REDIRECT_URI:
            return httpx.Response(400, json={"error": "invalid_grant"})
        verifier = form.get("code_verifier", "")
        if _b64url(hashlib.sha256(verifier.encode("ascii")).digest()) != issued["challenge"]:
            return httpx.Response(400, json={"error": "invalid_grant"})

        now = int(time.time())
        id_token = jwt.encode(
            {
                **issued["claims"],
                "iss": ISSUER,
                "aud": CLIENT_ID,
                "iat": now,
                "nbf": now,
                "exp": now + 1200,
                "nonce": issued["nonce"],
            },
            self.broker_key,
            algorithm="RS256",
            headers={"kid": BROKER_KID},
        )
        return httpx.Response(
            200, json={"id_token": id_token, "access_token": "opaque", "token_type": "Bearer"}
        )


@pytest.fixture(scope="module")
def keys() -> tuple[rsa.RSAPrivateKey, rsa.RSAPrivateKey]:
    """The broker's signing key and our client key. Made once: RSA is slow."""
    return _rsa_key(), _rsa_key()


@pytest.fixture
def broker(
    client: TestClient,
    keys: tuple[rsa.RSAPrivateKey, rsa.RSAPrivateKey],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[StubBroker]:
    """A deployment with BankID configured, and the broker it talks to."""
    broker_key, client_key = keys
    stub = StubBroker(broker_key, client_key)
    pem = client_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    # The bare domain, the way Idura's dashboard shows it.
    monkeypatch.setenv("BANKID_ISSUER", "myntos.test.idura.broker")
    monkeypatch.setenv("BANKID_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("BANKID_REDIRECT_URI", REDIRECT_URI)
    monkeypatch.setenv("BANKID_JWK", pem)
    monkeypatch.setenv("BANKID_KID", CLIENT_KID)
    monkeypatch.setattr(
        oidc_client,
        "_http",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(stub.handle)),
    )
    get_settings.cache_clear()
    reset_clients()
    yield stub
    get_settings.cache_clear()
    reset_clients()


@pytest.fixture
def google(client: TestClient, broker: StubBroker) -> Iterator[StubProvider]:
    """Google beside BankID, for the rules about the two on one account.

    Google is the stub test_auth_oidc.py uses, and BankID stays the real client
    against the stub broker: the override answers only for Google and hands
    every other provider to the real dependency.
    """
    stub = StubProvider("google")
    app = cast(Any, client.app)

    def pick(provider: str) -> OidcClient:
        if provider == "google":
            return cast(OidcClient, stub)
        return auth_endpoints._client_for(provider)

    app.dependency_overrides[auth_endpoints._client_for] = pick
    yield stub
    app.dependency_overrides.pop(auth_endpoints._client_for, None)


@pytest.fixture
def database(database_url: str) -> Iterator[sa.Engine]:
    engine = sa.create_engine(database_url.replace("+aiosqlite", ""))
    yield engine
    engine.dispose()


def scalar(database: sa.Engine, statement: str, **params: Any) -> Any:
    with database.connect() as connection:
        return connection.execute(sa.text(statement), params).scalar()


def run(database: sa.Engine, statement: str, **params: Any) -> None:
    with database.begin() as connection:
        connection.execute(sa.text(statement), params)


def _start(client: TestClient, **params: str) -> str:
    """Begin a BankID flow and return where the backend sent the browser."""
    response = client.get("/api/v1/auth/bankid/start", params=params, follow_redirects=False)
    assert response.status_code == 303, response.text
    location: str = response.headers["location"]
    return location


def _bankid(
    client: TestClient, broker: StubBroker, claims: dict[str, Any], **params: str
) -> httpx.Response:
    """The whole trip: start, authenticate at the broker, come back."""
    callback = broker.login(_start(client, **params), claims)
    response: httpx.Response = client.get(
        "/api/v1/auth/bankid/callback", params=callback, follow_redirects=False
    )
    return response


def _hold_token(response: httpx.Response) -> str:
    location = response.headers["location"]
    assert location.startswith("https://app.example/fullfor-registrering/#token="), location
    return location.split("#token=", 1)[1]


def _sign_up(
    client: TestClient, broker: StubBroker, claims: dict[str, Any], email: str
) -> dict[str, Any]:
    token = _hold_token(_bankid(client, broker, claims, intent="signup"))
    done = write(
        client, "POST", "/api/v1/auth/provider-signup", json={"token": token, "email": email}
    )
    assert done.status_code == 201, done.text
    body: dict[str, Any] = done.json()
    return body


def _switch_url(
    client: TestClient, *, password: str | None = PASSWORD, redirect_to: str = "/konto/"
) -> str:
    """Start the switch to BankID from /konto/ and return the BankID URL."""
    started = write(
        client,
        "POST",
        "/api/v1/auth/bankid/link",
        json={"password": password, "redirect_to": redirect_to},
    )
    assert started.status_code == 200, started.text
    url: str = started.json()["authorization_url"]
    return url


def _link(
    client: TestClient,
    broker: StubBroker,
    claims: dict[str, Any],
    *,
    password: str | None = PASSWORD,
) -> httpx.Response:
    """Switch the signed-in account to BankID: confirm, authenticate, come back."""
    callback = broker.login(_switch_url(client, password=password), claims)
    response: httpx.Response = client.get(
        "/api/v1/auth/bankid/callback", params=callback, follow_redirects=False
    )
    return response


def _google(client: TestClient, **params: str) -> httpx.Response:
    """A whole Google trip through the stub (the ``google`` fixture): start, then callback.

    A refusal at the start comes back as it is, without a trip to Google.
    """
    started: httpx.Response = client.get(
        "/api/v1/auth/google/start", params=params, follow_redirects=False
    )
    assert started.status_code == 303, started.text
    location = started.headers["location"]
    if not location.startswith("https://google.test/"):
        return started
    state = parse_qs(urlparse(location).query)["state"][0]
    response: httpx.Response = client.get(
        "/api/v1/auth/google/callback",
        params={"code": "google-code", "state": state},
        follow_redirects=False,
    )
    return response


def _login(client: TestClient, email: str, password: str = PASSWORD) -> Any:
    return write(client, "POST", "/api/v1/auth/login", json={"email": email, "password": password})


def _token_in(text: str, page: str) -> str:
    marker = f"https://app.example{page}#token="
    assert marker in text, text
    return text.split(marker, maxsplit=1)[1].split()[0]


# --- Configuration ------------------------------------------------------------------


def test_bankid_is_offered_only_when_fully_configured(
    client: TestClient, broker: StubBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    providers = client.get("/api/v1/auth/providers").json()["providers"]
    assert providers == [{"code": "bankid", "label": "BankID"}]

    # No client credential at all: not offered, rather than a button that
    # sends somebody through BankID to fail at the token request.
    monkeypatch.setenv("BANKID_JWK", "")
    get_settings.cache_clear()
    assert client.get("/api/v1/auth/providers").json()["providers"] == []
    assert client.get("/api/v1/auth/bankid/start", follow_redirects=False).status_code == 503

    # A secret is a credential too.
    monkeypatch.setenv("BANKID_CLIENT_SECRET", "hemmelig")
    get_settings.cache_clear()
    assert [p["code"] for p in client.get("/api/v1/auth/providers").json()["providers"]] == [
        "bankid"
    ]


def test_an_unreadable_key_is_not_offered(
    client: TestClient,
    broker: StubBroker,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("BANKID_JWK", "ikke en nøkkel")
    get_settings.cache_clear()
    with caplog.at_level(logging.ERROR):
        assert client.get("/api/v1/auth/providers").json()["providers"] == []
    assert "bankid sign-in is configured but unusable" in caplog.text


def test_a_bare_domain_reads_as_the_issuer() -> None:
    assert bankid_issuer("myntos.test.idura.broker") == ISSUER
    assert bankid_issuer(f"{ISSUER}/") == ISSUER


# --- The authorization request ----------------------------------------------------


def test_the_authorization_request_asks_for_bankid_and_nothing_more(
    client: TestClient, broker: StubBroker
) -> None:
    response = _bankid(client, broker, KARI_BANKID, intent="signup")
    assert response.status_code == 303

    query = broker.authorize_requests[0]
    assert query["client_id"] == CLIENT_ID
    assert query["redirect_uri"] == REDIRECT_URI
    assert query["response_type"] == "code"
    assert query["acr_values"] == SUBSTANTIAL
    # Identity only. Never "ssn": the fødselsnummer is not asked for.
    assert query["scope"] == "openid"
    # A real BankID authentication every time, never a reused broker session.
    assert query["prompt"] == "login"
    assert query["ui_locales"] == "nb"
    assert query["code_challenge_method"] == "S256"
    assert len(query["code_challenge"]) == 43
    assert query["nonce"] and query["state"] and query["nonce"] != query["state"]

    # The broker accepted the PKCE verifier and our signed client assertion,
    # and no secret went over the wire.
    token_request = broker.token_requests[0]
    assert "client_secret" not in token_request
    assert token_request["client_id"] == CLIENT_ID


def test_a_different_assurance_level_is_configuration(
    client: TestClient, broker: StubBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BANKID_ACR_VALUES", "urn:grn:authn:no:bankid")
    get_settings.cache_clear()
    reset_clients()
    broker.login(_start(client), KARI_BANKID)
    assert broker.authorize_requests[0]["acr_values"] == "urn:grn:authn:no:bankid"


# --- Signing in -----------------------------------------------------------------------


def test_an_unlinked_bankid_is_refused_at_login(
    client: TestClient, broker: StubBroker, database: sa.Engine
) -> None:
    """A login button never creates an account, and BankID has no address to find one by."""
    register(client, "kari@example.no")
    write(client, "POST", "/api/v1/auth/logout")

    response = _bankid(client, broker, KARI_BANKID)
    assert response.headers["location"] == "https://app.example/logg-inn/?error=no_linked_account"
    assert client.get("/api/v1/auth/me").status_code == 401
    # Not attached to the account whose owner happens to have the same name,
    # and no new account either.
    assert scalar(database, "SELECT count(*) FROM user_identity") == 0
    assert scalar(database, "SELECT count(*) FROM app_user") == 1
    assert scalar(database, "SELECT count(*) FROM pending_provider_signup") == 0


def test_a_linked_bankid_signs_in_its_account(client: TestClient, broker: StubBroker) -> None:
    kari = register(client, "kari@example.no")
    assert _link(client, broker, KARI_BANKID).headers["location"] == "https://app.example/konto/"
    write(client, "POST", "/api/v1/auth/logout")

    response = _bankid(client, broker, KARI_BANKID, redirect_to="/kontantstrom/")
    assert response.headers["location"] == "https://app.example/kontantstrom/"
    me = client.get("/api/v1/auth/me").json()
    assert me["user"]["id"] == kari["user"]["id"]
    assert me["user"]["providers"] == ["bankid"]
    # Linking BankID was a switch: the password went with it.
    assert me["user"]["has_password"] is False


def test_another_eid_through_the_same_broker_is_refused(
    client: TestClient, broker: StubBroker
) -> None:
    """acr_values is in the browser's address bar; the token says what actually ran."""
    register(client, "kari@example.no")
    _link(client, broker, KARI_BANKID)
    write(client, "POST", "/api/v1/auth/logout")

    mitid = {**KARI_BANKID, "identityscheme": "dkmitid"}
    response = _bankid(client, broker, mitid)
    assert response.headers["location"] == "https://app.example/logg-inn/?error=innlogging_feilet"
    assert client.get("/api/v1/auth/me").status_code == 401


# --- Switching to BankID from /konto/ ----------------------------------------------


def test_linking_needs_somebody_signed_in(client: TestClient, broker: StubBroker) -> None:
    response = client.get(
        "/api/v1/auth/bankid/start", params={"intent": "link"}, follow_redirects=False
    )
    assert response.headers["location"] == "https://app.example/konto/?error=innlogging_utlopt"
    started = write(client, "POST", "/api/v1/auth/bankid/link", json={"password": PASSWORD})
    assert started.status_code == 401
    assert broker.authorize_requests == []


def test_a_bankid_link_does_not_start_from_a_get(
    client: TestClient, broker: StubBroker, database: sa.Engine
) -> None:
    """A GET is anything a page can put in front of a signed-in browser.

    Linking BankID removes the password and every other way in, so it starts
    from the POST that asks for the password, which the cookie's origin check
    also guards.
    """
    register(client, "kari@example.no")
    response = client.get(
        "/api/v1/auth/bankid/start", params={"intent": "link"}, follow_redirects=False
    )
    assert response.headers["location"] == "https://app.example/konto/?error=bytte_ikke_bekreftet"
    assert broker.authorize_requests == []
    assert scalar(database, "SELECT count(*) FROM oauth_login_attempt") == 0


def test_the_switch_needs_the_password(client: TestClient, broker: StubBroker) -> None:
    """A hijacked session must not be able to swap the owner's password for its own BankID."""
    register(client, "kari@example.no")

    missing = write(client, "POST", "/api/v1/auth/bankid/link", json={})
    assert missing.status_code == 403
    assert missing.json()["code"] == "reauthentication_required"
    wrong = write(client, "POST", "/api/v1/auth/bankid/link", json={"password": "feil-passord"})
    assert wrong.status_code == 403
    assert wrong.json()["code"] == "reauthentication_failed"
    assert broker.authorize_requests == []
    # Still signed in, and still a password account.
    assert client.get("/api/v1/auth/me").json()["user"]["has_password"] is True


def test_an_account_without_a_password_needs_a_fresh_login_to_switch(
    client: TestClient, broker: StubBroker, google: StubProvider, database: sa.Engine
) -> None:
    _google(client)
    assert client.get("/api/v1/auth/me").json()["user"]["has_password"] is False
    run(database, "UPDATE user_session SET created_at = :t", t=utc_now() - timedelta(hours=1))

    stale = write(client, "POST", "/api/v1/auth/bankid/link", json={})
    assert stale.status_code == 403
    assert stale.json()["code"] == "reauthentication_required"

    run(database, "UPDATE user_session SET created_at = :t", t=utc_now())
    assert _link(client, broker, KARI_BANKID, password=None).status_code == 303
    assert client.get("/api/v1/auth/me").json()["user"]["providers"] == ["bankid"]


def test_switching_to_bankid_takes_every_other_way_in(
    client: TestClient,
    broker: StubBroker,
    google: StubProvider,
    outbox: RecordingMailer,
    database: sa.Engine,
) -> None:
    """The password, Google and every other session go; the session that switched stays."""
    register(client, "kari@gmail.com")
    assert _google(client, intent="link").headers["location"] == ("https://app.example/konto/")
    assert client.get("/api/v1/auth/me").json()["user"]["providers"] == ["google"]
    elsewhere = TestClient(cast(Any, client.app), base_url=TEST_ORIGIN)
    assert _login(elsewhere, "kari@gmail.com").status_code == 200
    outbox.sent.clear()

    done = _link(client, broker, KARI_BANKID)
    assert done.headers["location"] == "https://app.example/konto/"

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["providers"] == ["bankid"]
    assert me.json()["user"]["has_password"] is False
    assert elsewhere.get("/api/v1/auth/me").status_code == 401
    assert scalar(database, "SELECT count(*) FROM user_identity WHERE provider = 'google'") == 0

    # Neither the old password nor Google opens it any more.
    stranger = TestClient(cast(Any, client.app), base_url=TEST_ORIGIN)
    assert _login(stranger, "kari@gmail.com").status_code == 401
    refused = _google(stranger)
    assert refused.headers["location"] == (
        "https://app.example/logg-inn/?error=email_belongs_to_bankid_account"
    )
    assert stranger.get("/api/v1/auth/me").status_code == 401

    # And the address is told, after the fact.
    assert outbox.only.to == "kari@gmail.com"
    assert outbox.only.subject == "Du logger nå inn på Myntos med BankID"
    assert "#token=" not in outbox.only.text


def test_switching_to_bankid_retires_a_reset_link_already_in_the_inbox(
    client: TestClient, broker: StubBroker, outbox: RecordingMailer
) -> None:
    register(client, "kari@example.no")
    outbox.sent.clear()
    write(client, "POST", "/api/v1/auth/password/forgot", json={"email": "kari@example.no"})
    token = _token_in(outbox.only.text, "/nytt-passord/")

    _link(client, broker, KARI_BANKID)

    stale = write(
        client,
        "POST",
        "/api/v1/auth/password/reset",
        json={"token": token, "new_password": "et-helt-nytt-langt-passord"},
    )
    assert stale.status_code == 400
    assert stale.json()["code"] == "invalid_email_token"


def test_a_link_finished_in_somebody_elses_browser_is_refused(
    client: TestClient, broker: StubBroker, database: sa.Engine
) -> None:
    """The attack: start a link on your own account, hand the BankID page to a victim.

    Without the check, the victim's BankID would be linked to the attacker's
    account, and their next BankID login would open the attacker's books.
    """
    register(client, "angriper@example.no")
    authorize_url = _switch_url(client)

    victim = TestClient(cast(Any, client.app), base_url=TEST_ORIGIN)
    register(victim, "kari@example.no")
    callback = broker.login(authorize_url, KARI_BANKID)
    response = victim.get("/api/v1/auth/bankid/callback", params=callback, follow_redirects=False)
    # Refused already because the victim's browser did not start the flow
    # (deps.bind_flow); the check that the session is the starter's stays
    # behind it in _finish_link.
    assert response.headers["location"] == (
        "https://app.example/konto/?error=innlogging_annen_nettleser"
    )
    assert scalar(database, "SELECT count(*) FROM user_identity") == 0
    # Neither account lost its password over it.
    assert scalar(database, "SELECT count(*) FROM app_user WHERE password_hash IS NULL") == 0


def test_a_bankid_linked_to_one_account_cannot_be_linked_to_another(
    client: TestClient, broker: StubBroker
) -> None:
    register(client, "kari@example.no")
    _link(client, broker, KARI_BANKID)
    write(client, "POST", "/api/v1/auth/logout")

    register(client, "ola@example.no")
    response = _link(client, broker, KARI_BANKID)
    assert (
        response.headers["location"] == "https://app.example/konto/?error=identity_already_linked"
    )
    # Refused before anything changed: Ola keeps his password.
    user = client.get("/api/v1/auth/me").json()["user"]
    assert user["providers"] == []
    assert user["has_password"] is True


def test_a_bankid_account_does_not_start_a_second_bankid_link(
    client: TestClient, broker: StubBroker
) -> None:
    _sign_up(client, broker, KARI_BANKID, "kari@example.no")
    again = write(client, "POST", "/api/v1/auth/bankid/link", json={})
    assert again.status_code == 409
    assert again.json()["code"] == "identity_already_linked"


# --- Nothing else on a BankID account ---------------------------------------------


def test_google_cannot_be_linked_to_a_bankid_account(
    client: TestClient, broker: StubBroker, google: StubProvider, database: sa.Engine
) -> None:
    _sign_up(client, broker, KARI_BANKID, "kari@example.no")

    refused = client.get(
        "/api/v1/auth/google/start", params={"intent": "link"}, follow_redirects=False
    )
    assert refused.headers["location"] == "https://app.example/konto/?error=bankid_account"
    # Refused before the trip to Google, not after it.
    assert (
        scalar(database, "SELECT count(*) FROM oauth_login_attempt WHERE provider = 'google'") == 0
    )
    assert client.get("/api/v1/auth/me").json()["user"]["providers"] == ["bankid"]


def test_a_google_link_started_before_the_switch_is_refused_after_it(
    client: TestClient, broker: StubBroker, google: StubProvider
) -> None:
    """The callback asks again: the account may have become a BankID account in between."""
    register(client, "kari@example.no")
    started = client.get(
        "/api/v1/auth/google/start", params={"intent": "link"}, follow_redirects=False
    )
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]

    _link(client, broker, KARI_BANKID)
    response = client.get(
        "/api/v1/auth/google/callback",
        params={"code": "google-code", "state": state},
        follow_redirects=False,
    )
    assert response.headers["location"] == "https://app.example/konto/?error=bankid_account"
    assert client.get("/api/v1/auth/me").json()["user"]["providers"] == ["bankid"]


def test_google_with_the_same_verified_address_does_not_open_a_bankid_account(
    client: TestClient, broker: StubBroker, google: StubProvider, database: sa.Engine
) -> None:
    """The rule that links a provider-verified address to its account stops at BankID."""
    _sign_up(client, broker, KARI_BANKID, "kari@gmail.com")
    write(client, "POST", "/api/v1/auth/logout")

    login = _google(client)
    assert login.headers["location"] == (
        "https://app.example/logg-inn/?error=email_belongs_to_bankid_account"
    )
    signup = _google(client, intent="signup")
    assert signup.headers["location"] == (
        "https://app.example/registrer/?error=email_belongs_to_bankid_account"
    )
    assert client.get("/api/v1/auth/me").status_code == 401
    assert scalar(database, "SELECT count(*) FROM user_identity WHERE provider = 'google'") == 0
    # Refused before anything was written: Google's word did not confirm the
    # address on an account it was not let into.
    assert scalar(database, "SELECT count(*) FROM app_user WHERE email_verified_at IS NULL") == 1


def test_an_unverified_google_address_is_refused_by_the_older_rule_first(
    client: TestClient, broker: StubBroker, google: StubProvider
) -> None:
    """Somebody who has not shown the address is theirs learns nothing about BankID."""
    _sign_up(client, broker, KARI_BANKID, "kari@gmail.com")
    write(client, "POST", "/api/v1/auth/logout")
    google.profile = OidcProfile(
        subject="ikke-kari", email="kari@gmail.com", email_verified=False, full_name="Ikke Kari"
    )
    response = _google(client)
    assert response.headers["location"] == (
        "https://app.example/logg-inn/?error=unverified_provider_email"
    )


def test_a_bankid_account_has_no_password_to_change(client: TestClient, broker: StubBroker) -> None:
    _sign_up(client, broker, KARI_BANKID, "kari@example.no")
    refused = write(
        client,
        "POST",
        "/api/v1/auth/password",
        json={"current_password": "", "new_password": "et-helt-nytt-langt-passord"},
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "bankid_account"


def test_a_password_left_on_a_bankid_account_does_not_sign_in(
    client: TestClient, broker: StubBroker, database: sa.Engine
) -> None:
    """An account linked before the rule existed may still hold both. The password loses."""
    kari = register(client, "kari@example.no")
    write(client, "POST", "/api/v1/auth/logout")
    run(
        database,
        "INSERT INTO user_identity (user_id, provider, subject, created_at) "
        "VALUES (:u, 'bankid', :s, :t)",
        u=kari["user"]["id"],
        s=KARI_BANKID["sub"],
        t=utc_now(),
    )

    refused = _login(client, "kari@example.no")
    assert refused.status_code == 409
    assert refused.json()["code"] == "bankid_account"
    assert client.get("/api/v1/auth/me").status_code == 401
    # A wrong password is still the ordinary refusal, so this says nothing to
    # somebody who does not know the right one.
    assert _login(client, "kari@example.no", "feil-passord").status_code == 401


# --- A reset never reaches a BankID account ----------------------------------------


def test_a_reset_request_for_a_bankid_account_looks_like_any_other(
    client: TestClient, broker: StubBroker, outbox: RecordingMailer, database: sa.Engine
) -> None:
    """Same answer for a BankID account, a password account and no account at all.

    And what the BankID account's inbox gets is a notice that it signs in with
    BankID, with nothing in it that sets a password.
    """
    _sign_up(client, broker, KARI_BANKID, "kari@example.no")
    write(client, "POST", "/api/v1/auth/logout")
    register(client, "ola@example.no")
    write(client, "POST", "/api/v1/auth/logout")
    outbox.sent.clear()

    def forgot(email: str) -> Any:
        return write(client, "POST", "/api/v1/auth/password/forgot", json={"email": email})

    answers = [forgot(email) for email in ("kari@example.no", "ola@example.no", "ingen@x.no")]
    assert {answer.status_code for answer in answers} == {202}
    assert len({answer.content for answer in answers}) == 1
    assert len({frozenset(answer.headers) for answer in answers}) == 1

    notice, reset = outbox.sent
    assert notice.to == "kari@example.no"
    assert notice.subject == "Du logger inn på Myntos med BankID"
    assert "#token=" not in notice.text
    assert "https://app.example/logg-inn/" in notice.text
    assert notice.html is not None and "#token=" not in notice.html
    assert reset.to == "ola@example.no"
    assert "/nytt-passord/#token=" in reset.text

    # Recorded for the cap, and nothing that could set a password exists.
    kari_id = scalar(database, "SELECT id FROM app_user WHERE email = 'kari@example.no'")
    purposes = scalar(
        database,
        "SELECT group_concat(purpose) FROM user_email_token "
        "WHERE user_id = :u AND purpose != 'verify_email'",
        u=kari_id,
    )
    assert purposes == "bankid_notice"


def test_the_bankid_notice_is_capped_like_a_link(
    client: TestClient, broker: StubBroker, outbox: RecordingMailer
) -> None:
    _sign_up(client, broker, KARI_BANKID, "kari@example.no")
    outbox.sent.clear()
    answers = [
        write(client, "POST", "/api/v1/auth/password/forgot", json={"email": "kari@example.no"})
        for _ in range(5)
    ]
    assert {answer.status_code for answer in answers} == {202}
    assert len(outbox.sent) == 3


def test_a_reset_link_for_a_bankid_account_is_refused_however_it_exists(
    client: TestClient, broker: StubBroker, database: sa.Engine
) -> None:
    _sign_up(client, broker, KARI_BANKID, "kari@example.no")
    kari_id = scalar(database, "SELECT id FROM app_user WHERE email = 'kari@example.no'")
    now = utc_now()
    run(
        database,
        "INSERT INTO user_email_token (user_id, purpose, email, token_hash, created_at, "
        "expires_at) VALUES (:u, 'reset_password', 'kari@example.no', :h, :c, :e)",
        u=kari_id,
        h=hash_token("smuglet-lenke"),
        c=now,
        e=now + timedelta(minutes=30),
    )

    refused = write(
        client,
        "POST",
        "/api/v1/auth/password/reset",
        json={"token": "smuglet-lenke", "new_password": "et-helt-nytt-langt-passord"},
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "bankid_account"
    assert scalar(database, "SELECT password_hash FROM app_user WHERE id = :u", u=kari_id) is None
    # Nobody was signed out over a link that did nothing.
    assert client.get("/api/v1/auth/me").status_code == 200


# --- Switching from BankID to e-mail ----------------------------------------------


def test_switching_to_email_waits_for_the_mailed_link(
    client: TestClient, broker: StubBroker, outbox: RecordingMailer
) -> None:
    """Asking changes nothing. Using the link is the switch, and then BankID is gone."""
    kari = _sign_up(client, broker, KARI_BANKID, "kari@example.no")
    outbox.sent.clear()

    asked = write(client, "POST", "/api/v1/account/switch-to-email")
    assert asked.status_code == 202, asked.text
    assert asked.json() == {"sent": True, "email": "kari@example.no"}
    message = outbox.only
    assert message.to == "kari@example.no"
    assert message.subject == "Bytt til innlogging med e-post i Myntos"
    token = _token_in(message.text, "/bytt-til-epost/")

    # Until the link is used, BankID is how this account signs in.
    write(client, "POST", "/api/v1/auth/logout")
    _bankid(client, broker, KARI_BANKID)
    me = client.get("/api/v1/auth/me").json()["user"]
    assert me["id"] == kari["user"]["id"]
    assert me["providers"] == ["bankid"]
    assert me["has_password"] is False

    # The link is the proof, from any browser, with no session.
    inbox = TestClient(cast(Any, client.app), base_url=TEST_ORIGIN)
    weak = write(
        inbox,
        "POST",
        "/api/v1/auth/switch-to-email/confirm",
        json={"token": token, "new_password": "kort"},
    )
    assert weak.status_code == 422
    done = write(
        inbox,
        "POST",
        "/api/v1/auth/switch-to-email/confirm",
        json={"token": token, "new_password": "et-helt-nytt-langt-passord"},
    )
    assert done.status_code == 204, done.text

    # Every session ended, the BankID one included, and BankID opens nothing.
    assert client.get("/api/v1/auth/me").status_code == 401
    assert "error=no_linked_account" in _bankid(client, broker, KARI_BANKID).headers["location"]
    signed_in = _login(client, "kari@example.no", "et-helt-nytt-langt-passord")
    assert signed_in.status_code == 200
    assert signed_in.json()["user"]["id"] == kari["user"]["id"]
    assert signed_in.json()["user"]["providers"] == []
    # Reading the inbox is what confirming an address means.
    assert signed_in.json()["user"]["email_verified"] is True

    replayed = write(
        inbox,
        "POST",
        "/api/v1/auth/switch-to-email/confirm",
        json={"token": token, "new_password": "nok-et-langt-passord"},
    )
    assert replayed.status_code == 400


def test_a_switch_link_is_not_a_reset_link(
    client: TestClient, broker: StubBroker, outbox: RecordingMailer
) -> None:
    _sign_up(client, broker, KARI_BANKID, "kari@example.no")
    outbox.sent.clear()
    write(client, "POST", "/api/v1/account/switch-to-email")
    token = _token_in(outbox.only.text, "/bytt-til-epost/")

    as_reset = write(
        client,
        "POST",
        "/api/v1/auth/password/reset",
        json={"token": token, "new_password": "et-helt-nytt-langt-passord"},
    )
    assert as_reset.status_code == 400
    assert client.get("/api/v1/auth/me").json()["user"]["providers"] == ["bankid"]


def test_only_a_bankid_account_has_anything_to_switch_from(client: TestClient) -> None:
    register(client, "kari@example.no")
    refused = write(client, "POST", "/api/v1/account/switch-to-email")
    assert refused.status_code == 404
    assert refused.json()["code"] == "identity_not_found"
    assert write(client, "POST", "/api/v1/auth/logout").status_code == 204
    assert write(client, "POST", "/api/v1/account/switch-to-email").status_code == 401


def test_without_mail_there_is_no_switch_to_email(
    client: TestClient, broker: StubBroker, outbox: RecordingMailer
) -> None:
    _sign_up(client, broker, KARI_BANKID, "kari@example.no")
    outbox.configured = False
    assert write(client, "POST", "/api/v1/account/switch-to-email").status_code == 503


# --- Unlinking ---------------------------------------------------------------------


def test_bankid_is_never_removed_directly(client: TestClient, broker: StubBroker) -> None:
    """The way out of BankID is the switch to e-mail, never a button that removes it."""
    _sign_up(client, broker, KARI_BANKID, "kari@example.no")

    listed = client.get("/api/v1/account/identities").json()
    assert [(row["provider"], row["label"], row["removable"]) for row in listed] == [
        ("bankid", "BankID", False)
    ]
    refused = write(client, "DELETE", "/api/v1/account/identities/bankid")
    assert refused.status_code == 409
    assert refused.json()["code"] == "bankid_account"
    assert client.get("/api/v1/auth/me").json()["user"]["providers"] == ["bankid"]


def test_removing_a_provider_the_account_does_not_have_is_not_found(
    client: TestClient, broker: StubBroker
) -> None:
    register(client, "kari@example.no")
    assert write(client, "DELETE", "/api/v1/account/identities/bankid").status_code == 404


# --- Signing up ----------------------------------------------------------------------


def test_signup_takes_the_name_from_bankid_and_mails_the_typed_address(
    client: TestClient, broker: StubBroker, outbox: RecordingMailer, database: sa.Engine
) -> None:
    response = _bankid(
        client, broker, KARI_BANKID, intent="signup", redirect_to="/invitasjon/?token=abc"
    )
    token = _hold_token(response)
    # Nobody is signed in until the address is given.
    assert client.get("/api/v1/auth/me").status_code == 401

    held = write(client, "POST", "/api/v1/auth/provider-signup/lookup", json={"token": token})
    assert held.status_code == 200
    assert held.json()["full_name"] == "Kari Nordmann"
    assert held.json()["provider_label"] == "BankID"
    assert held.json()["redirect_to"] == "/invitasjon/?token=abc"

    done = write(
        client,
        "POST",
        "/api/v1/auth/provider-signup",
        json={"token": token, "email": "Kari@Example.no"},
    )
    assert done.status_code == 201, done.text
    user = done.json()["user"]
    assert user["email"] == "kari@example.no"
    assert user["full_name"] == "Kari Nordmann"
    # The address is only what was typed: unconfirmed, and the ordinary
    # confirmation mail is on its way to it.
    assert user["email_verified"] is False
    assert user["has_password"] is False
    assert user["providers"] == ["bankid"]
    assert len(done.json()["memberships"]) == 1
    assert outbox.only.to == "kari@example.no"
    assert "/bekreft-epost/#token=" in outbox.only.text

    # The hold is gone, and the token with it.
    assert scalar(database, "SELECT count(*) FROM pending_provider_signup") == 0
    again = write(
        client,
        "POST",
        "/api/v1/auth/provider-signup",
        json={"token": token, "email": "noen.annen@example.no"},
    )
    assert again.status_code == 400
    assert again.json()["code"] == "invalid_provider_signup"

    # BankID now signs in to the new account.
    write(client, "POST", "/api/v1/auth/logout")
    _bankid(client, broker, KARI_BANKID)
    assert client.get("/api/v1/auth/me").json()["user"]["id"] == user["id"]


def test_signup_never_joins_an_existing_account_by_typed_address(
    client: TestClient, broker: StubBroker, database: sa.Engine
) -> None:
    """The takeover this whole flow is shaped around, refused.

    Joining by typed address would let anybody with a BankID into anybody's
    account whose address they know.
    """
    kari = register(client, "kari@example.no")
    write(client, "POST", "/api/v1/auth/logout")

    token = _hold_token(_bankid(client, broker, OLA_BANKID, intent="signup"))
    refused = write(
        client,
        "POST",
        "/api/v1/auth/provider-signup",
        json={"token": token, "email": "kari@example.no"},
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "email_already_registered"
    assert "koble til BankID" in refused.json()["detail"]
    assert client.get("/api/v1/auth/me").status_code == 401
    assert scalar(database, "SELECT count(*) FROM user_identity") == 0

    # The hold survives the refusal, so another address works without a
    # second trip through BankID, and it makes a new account, not Kari's.
    done = write(
        client,
        "POST",
        "/api/v1/auth/provider-signup",
        json={"token": token, "email": "ola@example.no"},
    )
    assert done.status_code == 201
    assert done.json()["user"]["id"] != kari["user"]["id"]


def test_a_known_bankid_at_signup_simply_signs_in(client: TestClient, broker: StubBroker) -> None:
    first = _sign_up(client, broker, KARI_BANKID, "kari@example.no")
    write(client, "POST", "/api/v1/auth/logout")

    response = _bankid(client, broker, KARI_BANKID, intent="signup")
    assert response.headers["location"] == "https://app.example/"
    assert client.get("/api/v1/auth/me").json()["user"]["id"] == first["user"]["id"]


def test_an_expired_hold_is_refused_and_swept(
    client: TestClient, broker: StubBroker, database: sa.Engine
) -> None:
    token = _hold_token(_bankid(client, broker, KARI_BANKID, intent="signup"))
    with database.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE pending_provider_signup SET created_at = '2026-01-01 00:00:00', "
                "expires_at = '2026-01-01 00:30:00'"
            )
        )
    looked_up = write(client, "POST", "/api/v1/auth/provider-signup/lookup", json={"token": token})
    assert looked_up.status_code == 400
    assert looked_up.json()["code"] == "invalid_provider_signup"

    # The next hold sweeps the dead one, name and pseudonym with it.
    _hold_token(_bankid(client, broker, OLA_BANKID, intent="signup"))
    assert scalar(database, "SELECT count(*) FROM pending_provider_signup") == 1
    assert scalar(database, "SELECT full_name FROM pending_provider_signup") == "Ola Nordmann"


# --- What is kept ----------------------------------------------------------------------


def test_the_fodselsnummer_is_never_kept_even_when_sent(
    client: TestClient,
    broker: StubBroker,
    database_url: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An application on Idura's static scope strategy could send it unasked."""
    # Synthetic: month plus 80 is Tenor's mark, so no real person has it.
    with_ssn = {**KARI_BANKID, "socialno": "16884140076"}
    with caplog.at_level(logging.WARNING):
        _sign_up(client, broker, with_ssn, "kari@example.no")
    assert "fødselsnummer that was not asked for" in caplog.text
    assert "16884140076" not in caplog.text

    stored = Path(database_url.removeprefix("sqlite+aiosqlite:///")).read_bytes()
    assert KARI_BANKID["sub"].encode() in stored  # the one thing that is kept
    for never in ("16884140076", KARI_BANKID["uniqueuserid"], KARI_BANKID["birthdate"]):
        assert never.encode() not in stored


def test_deleting_the_account_takes_the_bankid_link_with_it(
    client: TestClient, broker: StubBroker, outbox: RecordingMailer, database: sa.Engine
) -> None:
    """Erasure (docs/gdpr.md) covers a BankID identity like any other."""
    register(client, "kari@example.no")
    # A switch started and never finished still names the account, and so
    # does a link out of BankID that was mailed and never used.
    _switch_url(client)
    _link(client, broker, KARI_BANKID)
    write(client, "POST", "/api/v1/account/switch-to-email")

    # No password on a BankID account, so the fresh login is the proof.
    assert write(client, "POST", "/api/v1/account/erasure", json={}).status_code == 204
    assert scalar(database, "SELECT count(*) FROM user_identity") == 0
    assert (
        scalar(database, "SELECT count(*) FROM oauth_login_attempt WHERE user_id IS NOT NULL") == 0
    )
    assert scalar(database, "SELECT count(*) FROM user_email_token") == 0
    assert scalar(database, "SELECT count(*) FROM app_user") == 0


# --- The profile rule, without a broker -------------------------------------------------


def test_a_bankid_profile_carries_no_address() -> None:
    profile = bankid_profile(KARI_BANKID)
    assert profile.subject == KARI_BANKID["sub"]
    assert profile.email is None
    assert profile.email_verified is False
    assert profile.full_name == "Kari Nordmann"


def test_a_bankid_profile_falls_back_to_the_name_parts() -> None:
    claims = {key: value for key, value in KARI_BANKID.items() if key != "name"}
    assert bankid_profile(claims).full_name == "Kari Nordmann"
