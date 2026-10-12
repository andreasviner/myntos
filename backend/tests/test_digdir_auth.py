"""Maskinporten and ID-porten, with Digdir itself stubbed.

The stub replaces the network and nothing else. What the tests then check is the
part that cannot be checked against a stub of our own making — the grant this
code *sends*: its ``aud``, its lifetime, its single-use ``jti``, the ``kid`` in
its header. Every one of those is a documented way for a real token request to
fail with an error message that names none of them, which is exactly why they
are worth asserting here rather than discovering against test.maskinporten.no.

The signatures are verified for real: the tests hold the public half of the key
the code signs with, so "the assertion is signed with the configured key" is a
checked claim and not an assumption.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from myntos.auth import digdir_check, idporten
from myntos.auth.idporten import testid
from myntos.auth.idporten.client import (
    GovernmentLogin,
    IdPortenClient,
    IdPortenError,
    new_pkce_pair,
)
from myntos.auth.maskinporten import client as maskinporten
from myntos.auth.maskinporten.client import (
    JwtGrantTokenProvider,
    MaskinportenError,
    MaskinportenNotConfiguredError,
    NotConfiguredTokenProvider,
)
from myntos.auth.signing_keys import (
    SigningKey,
    SigningKeyError,
    load_signing_key,
    public_jwk,
    thumbprint,
)
from myntos.core.config import Settings
from myntos.integrations.skatteetaten.mva import MvaError, MvaValidation

ISSUER = "https://test.maskinporten.no/"
TOKEN_ENDPOINT = "https://test.maskinporten.no/token"
WELL_KNOWN = "https://test.maskinporten.no/.well-known/oauth-authorization-server"
CLIENT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
KID = "myntos-test-key"

IDPORTEN_ISSUER = "https://test.idporten.no"
IDPORTEN_WELL_KNOWN = "https://test.idporten.no/.well-known/openid-configuration"
IDPORTEN_CLIENT_ID = "be4eeea2-63b3-46cb-aaad-aa7d80857eb6"
REDIRECT_URI = "http://localhost:8000/api/v1/skatt/idporten/callback"
ACR = "idporten-loa-high"
PID = "01015450301"  # A Tenor-style synthetic fødselsnummer. Never a real one.


def _settings(**overrides: Any) -> Settings:
    """Settings with every Digdir variable blank unless the test sets it.

    ``Settings()`` reads the repository ``.env``, so a developer who has filled
    in half a Maskinporten client would otherwise change what these tests mean.
    The blanks are the interesting starting point anyway: "not configured" is the
    state most deployments are in.
    """
    blank: dict[str, Any] = {
        "maskinporten_client_id": None,
        "maskinporten_jwk_path": None,
        "maskinporten_kid": None,
        "maskinporten_consumer_org": None,
        "maskinporten_scopes": "",
        "idporten_client_id": None,
        "idporten_redirect_uri": None,
        "idporten_scopes": "",
        "idporten_jwk_path": None,
        "idporten_kid": None,
        "idporten_client_secret": None,
    }
    return Settings(**{**blank, **overrides})


# 2048 bits, generated once per session: the tests sign a handful of tokens and
# a 4096-bit keygen is seconds of pure waiting.
@pytest.fixture(scope="session")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def signing_key(rsa_key: rsa.RSAPrivateKey) -> SigningKey:
    return SigningKey(key=rsa_key, kid=KID)


def _verify(assertion: str, key: rsa.RSAPrivateKey, *, audience: str) -> dict[str, Any]:
    """Decode an assertion against the public half of the signing key."""
    return jwt.decode(assertion, key=key.public_key(), algorithms=["RS256"], audience=audience)


# --- Key loading ------------------------------------------------------------------


def test_reads_a_private_jwk_and_prefers_its_own_kid(
    tmp_path: Path, rsa_key: rsa.RSAPrivateKey
) -> None:
    """A JWK's ``kid`` is the registered one, so configuration cannot override it."""
    document = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(rsa_key))
    document["kid"] = "from-the-file"
    path = tmp_path / "key.jwk.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    loaded = load_signing_key(path, kid="from-the-config")
    assert loaded.kid == "from-the-file"


def test_reads_a_pem_with_a_configured_kid(tmp_path: Path, rsa_key: rsa.RSAPrivateKey) -> None:
    """A PEM is as valid as a JWK — ``secrets/maskinporten.jwk.json`` is one."""
    path = tmp_path / "key.pem"
    path.write_bytes(rsa_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    assert load_signing_key(path, kid=KID).kid == KID


def test_a_pem_without_a_kid_says_which_variable_to_set(
    tmp_path: Path, rsa_key: rsa.RSAPrivateKey
) -> None:
    """The failure a PEM key produces has to name its own remedy.

    Without the ``kid`` Digdir answers ``invalid_client``, which says nothing
    about key ids at all — so this error is the only place the connection gets
    made.
    """
    path = tmp_path / "key.pem"
    path.write_bytes(rsa_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    with pytest.raises(SigningKeyError, match="_KID"):
        load_signing_key(path)


def test_a_public_key_is_refused_as_a_signing_key(
    tmp_path: Path, rsa_key: rsa.RSAPrivateKey
) -> None:
    document = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(rsa_key.public_key()))
    path = tmp_path / "public.jwk.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(SigningKeyError, match="public key"):
        load_signing_key(path, kid=KID)


def test_a_too_small_key_is_refused(tmp_path: Path) -> None:
    """Digdir's floor is 2048 bits; failing here beats failing at the portal."""
    # A deliberately weak key: it exists so the loader can refuse it, which
    # is the only way to know that it does.
    small = rsa.generate_private_key(public_exponent=65537, key_size=1024)  # noqa: S505
    path = tmp_path / "small.pem"
    path.write_bytes(small.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    with pytest.raises(SigningKeyError, match="2048"):
        load_signing_key(path, kid=KID)


def test_public_jwk_carries_only_the_public_half(rsa_key: rsa.RSAPrivateKey) -> None:
    """What a person is told to paste into a portal must not contain a secret."""
    jwk = public_jwk(rsa_key)
    assert set(jwk) == {"kty", "n", "e", "kid", "alg", "use"}
    assert jwk["kid"] == thumbprint(jwk)
    # The private exponent and the primes, by their JWK names.
    assert not {"d", "p", "q", "dp", "dq", "qi"} & set(jwk)


# --- Maskinporten -----------------------------------------------------------------


class StubMaskinporten:
    """Maskinporten's well-known document and token endpoint."""

    def __init__(self) -> None:
        self.assertions: list[str] = []
        self.expires_in = 120
        self.status = httpx.codes.OK

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("oauth-authorization-server"):
            return httpx.Response(200, json={"issuer": ISSUER, "token_endpoint": TOKEN_ENDPOINT})
        if str(request.url) == TOKEN_ENDPOINT:
            form = dict(
                pair.split("=", 1) for pair in request.content.decode().split("&") if "=" in pair
            )
            assert form["grant_type"] == ("urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer")
            self.assertions.append(form["assertion"])
            if self.status != httpx.codes.OK:
                return httpx.Response(
                    self.status,
                    json={"error": "invalid_scope", "error_description": "no such scope"},
                )
            return httpx.Response(
                200,
                json={
                    "access_token": f"machine-token-{len(self.assertions)}",
                    "token_type": "Bearer",
                    "expires_in": self.expires_in,
                    "scope": "altinn:instances.read",
                },
            )
        raise AssertionError(f"unexpected request to {request.url}")


@pytest.fixture
def maskinporten_stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[StubMaskinporten]:
    stub = StubMaskinporten()
    monkeypatch.setattr(
        maskinporten,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(stub)),
    )
    yield stub


def _provider(signing_key: SigningKey) -> JwtGrantTokenProvider:
    return JwtGrantTokenProvider(
        client_id=CLIENT_ID, signing_key=signing_key, well_known_url=WELL_KNOWN
    )


async def test_a_systembruker_grant_carries_authorization_details(
    maskinporten_stub: StubMaskinporten, signing_key: SigningKey, rsa_key: rsa.RSAPrivateKey
) -> None:
    """RFC 9396, inside the assertion rather than on the token request.

    This is the claim that gives the token a subject - the customer's system
    user - which is what an ordinary client-credentials token lacks and what
    Skatteetaten answers SME-EKSTERN-005 about. Maskinporten says invalid_grant
    whether it is in the wrong place or absent, so the position is asserted.
    """
    token = await _provider(signing_key).get_access_token(
        ["altinn:instances.read"], systemuser_org="314067630"
    )
    assert token == "machine-token-1"

    claims = jwt.decode(
        maskinporten_stub.assertions[0],
        rsa_key.public_key(),
        algorithms=["RS256"],
        audience=ISSUER,
    )
    assert claims["authorization_details"] == [
        {
            "type": "urn:altinn:systemuser",
            "systemuser_org": {
                "authority": "iso6523-actorid-upis",
                # The prefix is required; Altinn rejects a bare orgnr.
                "ID": "0192:314067630",
            },
        }
    ]
    # Not a delegation. The two mechanisms answer the same need and must not be
    # confused in one grant.
    assert "consumer_org" not in claims


async def test_an_external_ref_is_sent_only_when_given(
    maskinporten_stub: StubMaskinporten, signing_key: SigningKey, rsa_key: rsa.RSAPrivateKey
) -> None:
    """Needed only when one customer has several system users on this system.

    Sent unconditionally it would be a value Altinn matches against and we
    invented, so its absence is part of the contract.
    """
    provider = _provider(signing_key)
    await provider.get_access_token(
        ["altinn:instances.read"], systemuser_org="314067630", external_ref="myntos-1"
    )
    claims = jwt.decode(
        maskinporten_stub.assertions[0],
        rsa_key.public_key(),
        algorithms=["RS256"],
        audience=ISSUER,
    )
    assert claims["authorization_details"][0]["externalRef"] == "myntos-1"


async def test_a_delegation_and_a_system_user_cannot_be_combined(
    maskinporten_stub: StubMaskinporten, signing_key: SigningKey
) -> None:
    """Two ways to act for a customer, and a grant is one or the other.

    Refused locally rather than sent: Maskinporten would answer invalid_grant,
    which says nothing about which of the two the caller actually meant.
    """
    with pytest.raises(MaskinportenError, match="never both"):
        await _provider(signing_key).get_access_token(
            ["altinn:instances.read"],
            consumer_org="938193045",
            systemuser_org="314067630",
        )
    assert maskinporten_stub.assertions == []


async def test_tokens_for_two_customers_are_not_shared(
    maskinporten_stub: StubMaskinporten, signing_key: SigningKey
) -> None:
    """The cache key includes the system user's organisation.

    A token carries its authority in its claims, so handing one company's token
    to a caller acting for another is acting with the wrong authority - the
    single worst bug this cache could have.
    """
    provider = _provider(signing_key)
    first = await provider.get_access_token(["altinn:instances.read"], systemuser_org="314067630")
    second = await provider.get_access_token(["altinn:instances.read"], systemuser_org="311124226")
    assert first != second
    assert len(maskinporten_stub.assertions) == 2

    # And the same customer twice is still one request.
    again = await provider.get_access_token(["altinn:instances.read"], systemuser_org="314067630")
    assert again == first
    assert len(maskinporten_stub.assertions) == 2


async def test_the_grant_is_built_the_way_maskinporten_requires(
    maskinporten_stub: StubMaskinporten, signing_key: SigningKey, rsa_key: rsa.RSAPrivateKey
) -> None:
    """Every claim here is a documented rejection when it is wrong."""
    token = await _provider(signing_key).get_access_token(["altinn:instances.read"])
    assert token == "machine-token-1"

    assertion = maskinporten_stub.assertions[0]
    assert jwt.get_unverified_header(assertion)["kid"] == KID
    claims = _verify(assertion, rsa_key, audience=ISSUER)
    # The issuer exactly, trailing slash included: the single most common cause
    # of an unexplained invalid_grant.
    assert claims["aud"] == ISSUER
    assert claims["iss"] == CLIENT_ID
    assert claims["scope"] == "altinn:instances.read"
    assert claims["exp"] - claims["iat"] <= 120
    assert "consumer_org" not in claims


async def test_each_token_request_gets_a_fresh_single_use_jti(
    maskinporten_stub: StubMaskinporten, signing_key: SigningKey, rsa_key: rsa.RSAPrivateKey
) -> None:
    """Grants are single-use, so two requests may never share an assertion."""
    provider = _provider(signing_key)
    await provider.get_access_token(["altinn:instances.read"])
    await provider.get_access_token(["altinn:instances.write"])

    identifiers = {
        _verify(assertion, rsa_key, audience=ISSUER)["jti"]
        for assertion in maskinporten_stub.assertions
    }
    assert len(identifiers) == 2


async def test_a_usable_token_is_reused_rather_than_re_requested(
    maskinporten_stub: StubMaskinporten, signing_key: SigningKey
) -> None:
    provider = _provider(signing_key)
    first = await provider.get_access_token(["altinn:instances.read"])
    second = await provider.get_access_token(["altinn:instances.read"])
    assert first == second
    assert len(maskinporten_stub.assertions) == 1


async def test_scope_order_does_not_split_the_cache(
    maskinporten_stub: StubMaskinporten, signing_key: SigningKey
) -> None:
    """The same scopes in another order are the same token, not another one."""
    provider = _provider(signing_key)
    await provider.get_access_token(["altinn:instances.read", "altinn:instances.write"])
    await provider.get_access_token(["altinn:instances.write", "altinn:instances.read"])
    assert len(maskinporten_stub.assertions) == 1


async def test_a_token_about_to_expire_is_not_reused(
    maskinporten_stub: StubMaskinporten, signing_key: SigningKey
) -> None:
    """The 30-second margin means a short-lived token is never cached at all.

    Skatteetaten's scopes issue eight-hour tokens and Altinn's issue minutes; a
    cache that ignored ``expires_in`` would work in test and fail in production.
    """
    maskinporten_stub.expires_in = 10
    provider = _provider(signing_key)
    await provider.get_access_token(["altinn:instances.read"])
    await provider.get_access_token(["altinn:instances.read"])
    assert len(maskinporten_stub.assertions) == 2


async def test_delegation_is_part_of_the_cache_key(
    maskinporten_stub: StubMaskinporten, signing_key: SigningKey, rsa_key: rsa.RSAPrivateKey
) -> None:
    """A token obtained for one client company must not serve another.

    ``consumer_org`` is what makes the token act under that company's
    delegation, so sharing one across companies would be acting with authority
    nobody granted.
    """
    provider = _provider(signing_key)
    await provider.get_access_token(["altinn:instances.read"], consumer_org="811074582")
    await provider.get_access_token(["altinn:instances.read"], consumer_org="810514442")
    assert len(maskinporten_stub.assertions) == 2
    organizations = [
        _verify(assertion, rsa_key, audience=ISSUER)["consumer_org"]
        for assertion in maskinporten_stub.assertions
    ]
    assert organizations == ["811074582", "810514442"]


async def test_a_refused_grant_reports_what_maskinporten_said(
    maskinporten_stub: StubMaskinporten, signing_key: SigningKey
) -> None:
    """``invalid_scope`` and ``invalid_client`` point at different fixes."""
    maskinporten_stub.status = httpx.codes.BAD_REQUEST
    with pytest.raises(MaskinportenError, match="invalid_scope"):
        await _provider(signing_key).get_access_token(["skatteetaten:formueinntekt/skattemelding"])


async def test_an_unconfigured_deployment_fails_with_the_variables_to_set() -> None:
    with pytest.raises(MaskinportenNotConfiguredError, match="MASKINPORTEN_CLIENT_ID"):
        await NotConfiguredTokenProvider().get_access_token(["altinn:instances.read"])


def test_missing_credentials_are_not_configured_rather_than_broken() -> None:
    """Most of the application has no government API in it and must still run."""
    provider = maskinporten.build_token_provider(_settings(maskinporten_client_id=None))
    assert isinstance(provider, NotConfiguredTokenProvider)


def test_a_configured_but_unreadable_key_is_an_error_not_a_silent_downgrade(
    tmp_path: Path,
) -> None:
    """ "Configured and broken" must never be reported as "feature is off"."""
    path = tmp_path / "nonsense.jwk.json"
    path.write_text("this is not a key", encoding="utf-8")
    with pytest.raises(MaskinportenError, match="unusable"):
        maskinporten.build_token_provider(
            _settings(maskinporten_client_id=CLIENT_ID, maskinporten_jwk_path=path)
        )


# --- ID-porten --------------------------------------------------------------------


class StubIdPorten:
    """ID-porten's discovery document, key set and token endpoint."""

    def __init__(self, key: rsa.RSAPrivateKey) -> None:
        self._key = key
        self.nonce = "the-nonce"
        self.acr = ACR
        self.forms: list[dict[str, str]] = []

    def id_token(self, *, nonce: str | None = None, acr: str | None = None) -> str:
        now = datetime.now(UTC)
        return jwt.encode(
            {
                "iss": IDPORTEN_ISSUER,
                "aud": IDPORTEN_CLIENT_ID,
                "sub": "sub-1",
                "pid": PID,
                "acr": acr or self.acr,
                "nonce": nonce or self.nonce,
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(minutes=5)).timestamp()),
            },
            self._key,
            algorithm="RS256",
            headers={"kid": "idporten-key"},
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": IDPORTEN_ISSUER,
                    "authorization_endpoint": f"{IDPORTEN_ISSUER}/authorize",
                    "token_endpoint": f"{IDPORTEN_ISSUER}/token",
                    "jwks_uri": f"{IDPORTEN_ISSUER}/jwks.json",
                },
            )
        if path.endswith("jwks.json"):
            document = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self._key.public_key()))
            document["kid"] = "idporten-key"
            document["alg"] = "RS256"
            return httpx.Response(200, json={"keys": [document]})
        if path.endswith("/token"):
            form = dict(
                pair.split("=", 1) for pair in request.content.decode().split("&") if "=" in pair
            )
            self.forms.append(form)
            return httpx.Response(
                200,
                json={
                    "access_token": "idporten-access-token",
                    "id_token": self.id_token(),
                    "refresh_token": "idporten-refresh-token",
                    "expires_in": 28800,
                    "token_type": "Bearer",
                },
            )
        raise AssertionError(f"unexpected request to {request.url}")


@pytest.fixture
def idporten_stub(
    monkeypatch: pytest.MonkeyPatch, rsa_key: rsa.RSAPrivateKey
) -> Iterator[StubIdPorten]:
    stub = StubIdPorten(rsa_key)
    monkeypatch.setattr(
        idporten.client,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(stub)),
    )
    yield stub


def _idporten_client(signing_key: SigningKey) -> IdPortenClient:
    return IdPortenClient(
        client_id=IDPORTEN_CLIENT_ID,
        redirect_uri=REDIRECT_URI,
        well_known_url=IDPORTEN_WELL_KNOWN,
        scope="openid skatteetaten:mvameldingvalidering",
        acr_values=ACR,
        signing_key=signing_key,
    )


async def test_the_authorization_url_demands_pkce_and_a_level_of_assurance(
    idporten_stub: StubIdPorten, signing_key: SigningKey
) -> None:
    pkce = new_pkce_pair()
    url = await _idporten_client(signing_key).authorization_url(
        state="the-state", nonce="the-nonce", code_challenge=pkce.challenge
    )
    query = dict(httpx.URL(url).params)
    assert query["code_challenge_method"] == "S256"
    assert query["code_challenge"] == pkce.challenge
    assert query["acr_values"] == ACR
    assert query["redirect_uri"] == REDIRECT_URI
    assert "skatteetaten:mvameldingvalidering" in query["scope"]


async def test_the_code_exchange_authenticates_with_a_signed_client_assertion(
    idporten_stub: StubIdPorten, signing_key: SigningKey, rsa_key: rsa.RSAPrivateKey
) -> None:
    """``private_key_jwt``: ``iss`` and ``sub`` are both the client id."""
    login = await _idporten_client(signing_key).exchange_code(
        code="the-code", code_verifier="the-verifier", nonce="the-nonce"
    )
    assert login.access_token == "idporten-access-token"
    assert login.pid == PID
    assert login.acr == ACR
    assert login.refresh_token == "idporten-refresh-token"
    assert login.is_usable()

    form = idporten_stub.forms[0]
    assert form["client_assertion_type"] == (
        "urn%3Aietf%3Aparams%3Aoauth%3Aclient-assertion-type%3Ajwt-bearer"
    )
    claims = _verify(form["client_assertion"], rsa_key, audience=IDPORTEN_ISSUER)
    assert claims["iss"] == claims["sub"] == IDPORTEN_CLIENT_ID
    # The verifier has to travel with the code, or PKCE protects nothing.
    assert form["code_verifier"] == "the-verifier"


async def test_a_login_at_a_lower_assurance_level_is_refused(
    idporten_stub: StubIdPorten, signing_key: SigningKey
) -> None:
    """Asking for loa-high and accepting MinID would defeat asking at all."""
    idporten_stub.acr = "idporten-loa-substantial"
    with pytest.raises(IdPortenError, match="idporten-loa-high"):
        await _idporten_client(signing_key).exchange_code(
            code="the-code", code_verifier="v", nonce="the-nonce"
        )


async def test_a_token_from_another_login_attempt_is_refused(
    idporten_stub: StubIdPorten, signing_key: SigningKey
) -> None:
    """The nonce binds the token to the attempt that started the flow."""
    with pytest.raises(IdPortenError, match="nonce"):
        await _idporten_client(signing_key).exchange_code(
            code="the-code", code_verifier="v", nonce="a-different-nonce"
        )


def test_openid_is_always_asked_for_and_never_asked_for_twice() -> None:
    assert idporten.configured_scope(_settings(idporten_scopes="")) == "openid"
    scope = idporten.configured_scope(
        _settings(idporten_scopes="openid skatteetaten:mvameldingvalidering")
    )
    assert scope == "openid skatteetaten:mvameldingvalidering"


def test_a_half_configured_idporten_is_absent_rather_than_broken() -> None:
    """The same rule the sign-in buttons follow: no credential, no feature."""
    assert not idporten.is_configured(_settings(idporten_client_id=IDPORTEN_CLIENT_ID))
    assert not idporten.is_configured(
        _settings(idporten_client_id=IDPORTEN_CLIENT_ID, idporten_redirect_uri=REDIRECT_URI)
    )
    assert idporten.is_configured(
        _settings(
            idporten_client_id=IDPORTEN_CLIENT_ID,
            idporten_redirect_uri=REDIRECT_URI,
            idporten_client_secret="a-secret",
        )
    )


def test_a_blank_credential_does_not_count_as_a_credential() -> None:
    """``.env.example`` ships these empty, and an empty line is not a secret.

    Reading "the variable exists" as "the credential is there" would offer a
    submission flow that fails at the first token request — which is the failure
    the sign-in providers are careful to avoid, for the same reason.
    """
    settings = _settings(
        idporten_client_id=IDPORTEN_CLIENT_ID,
        idporten_redirect_uri=REDIRECT_URI,
        idporten_client_secret="   ",
    )
    assert not idporten.is_configured(settings)


def test_a_client_with_no_credential_at_all_says_which_ones_would_do() -> None:
    with pytest.raises(idporten.IdPortenNotConfiguredError, match="IDPORTEN_JWK_PATH"):
        idporten.build_client(
            _settings(idporten_client_id=IDPORTEN_CLIENT_ID, idporten_redirect_uri=REDIRECT_URI)
        )


# --- The setup diagnostic ---------------------------------------------------------


def test_the_diagnostic_names_the_variable_that_is_missing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Its whole purpose is to be more specific than the services are.

    Maskinporten answers ``invalid_client`` for a wrong client id, an
    unregistered key and a mismatched ``kid`` alike, so a check that only said
    "not working" would leave the reader exactly where they started.
    """
    report = digdir_check.Report()
    assert not digdir_check._check_configuration(_settings(), report)
    assert report.failed
    assert "MASKINPORTEN_CLIENT_ID" in capsys.readouterr().out


def test_the_diagnostic_reports_the_kid_it_will_send(
    tmp_path: Path, rsa_key: rsa.RSAPrivateKey, capsys: pytest.CaptureFixture[str]
) -> None:
    """The kid is the value to compare against the portal, so it gets printed."""
    path = tmp_path / "key.pem"
    path.write_bytes(rsa_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    report = digdir_check.Report()
    assert digdir_check._check_key(
        _settings(maskinporten_jwk_path=path, maskinporten_kid=KID), report
    )
    assert not report.failed
    assert KID in capsys.readouterr().out


async def test_the_diagnostic_walks_the_chain_and_stops_at_the_first_failure(
    maskinporten_stub: StubMaskinporten,
    tmp_path: Path,
    rsa_key: rsa.RSAPrivateKey,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A token that Maskinporten refuses must not be followed by an Altinn call.

    Reporting an Altinn failure caused by a missing token would point at the
    wrong service, which is the confusion this command exists to remove.
    """
    path = tmp_path / "key.pem"
    path.write_bytes(rsa_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    maskinporten_stub.status = httpx.codes.BAD_REQUEST

    def exploding_exchange(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Altinn must not be called without a token")

    monkeypatch.setattr(digdir_check, "exchange_token", exploding_exchange)
    monkeypatch.setattr(
        digdir_check,
        "get_settings",
        lambda: _settings(
            maskinporten_client_id=CLIENT_ID,
            maskinporten_jwk_path=path,
            maskinporten_kid=KID,
        ),
    )
    assert await digdir_check.run() == 1
    output = capsys.readouterr().out
    assert "maskinporten token" in output
    assert "invalid_scope" in output


# --- TestID (the test-environment automated login) --------------------------------


def test_a_synthetic_personnummer_is_told_apart_by_arithmetic() -> None:
    """+80 on the month digits, so no list of allowed numbers is needed."""
    assert testid.is_synthetic("12869599229")  # month 86 = June + 80
    assert testid.is_synthetic("60905701315")  # a D-number: day +40 as well
    assert not testid.is_synthetic("12069599229")  # a real-shaped June number
    assert not testid.is_synthetic("not-a-number")
    assert not testid.is_synthetic("1206959922")  # ten digits


async def test_a_real_personnummer_is_never_sent_to_a_test_environment(
    idporten_stub: StubIdPorten, signing_key: SigningKey
) -> None:
    """The guard is the point: test data is the only data test may ever see."""
    with pytest.raises(testid.NotATestEnvironmentError, match="synthetic"):
        await testid.headless_login(_idporten_client(signing_key), pid="12069599229")


async def test_a_headless_login_is_refused_outside_a_test_environment(
    monkeypatch: pytest.MonkeyPatch, signing_key: SigningKey
) -> None:
    """Checked against the provider's own issuer, not against our configuration.

    A deployment that pointed an env var at production must not be able to talk
    this into running there — so the decision comes from the discovery document.
    The stub refuses every request past discovery, which is what proves the guard
    stops the flow rather than merely reporting on it afterwards.
    """

    def production(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": "https://idporten.no",
                    "authorization_endpoint": "https://idporten.no/authorize",
                    "token_endpoint": "https://idporten.no/token",
                    "jwks_uri": "https://idporten.no/jwks.json",
                },
            )
        raise AssertionError("nothing past discovery may be reached")

    monkeypatch.setattr(
        idporten.client,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(production)),
    )
    with pytest.raises(testid.NotATestEnvironmentError, match="not a test environment"):
        await testid.headless_login(_idporten_client(signing_key), pid="12869599229")


async def test_the_login_hint_carries_the_person_and_the_assurance_level(
    idporten_stub: StubIdPorten, signing_key: SigningKey
) -> None:
    """``testid:<pid>_<acr>`` is what makes ID-porten authenticate on its own."""
    url = await _idporten_client(signing_key).authorization_url(
        state="s", nonce="n", code_challenge="c", login_hint="testid:12869599229_x"
    )
    assert dict(httpx.URL(url).params)["login_hint"] == "testid:12869599229_x"


async def test_a_real_login_sends_no_login_hint(
    idporten_stub: StubIdPorten, signing_key: SigningKey
) -> None:
    """Which person is signing in is theirs to decide, never ours to suggest."""
    url = await _idporten_client(signing_key).authorization_url(
        state="s", nonce="n", code_challenge="c"
    )
    assert "login_hint" not in dict(httpx.URL(url).params)


# --- A refused token request says why ---------------------------------------------


async def test_a_refused_code_exchange_says_what_id_porten_said(
    monkeypatch: pytest.MonkeyPatch, signing_key: SigningKey
) -> None:
    """``invalid_client`` alone covers a wrong kid, an unregistered key and more.

    The TT02 client's kid was wrong on 2026-10-09 and the only thing printed was
    "401"; ID-porten's description named the cause outright.
    """

    def refusing(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": IDPORTEN_ISSUER,
                    "authorization_endpoint": f"{IDPORTEN_ISSUER}/authorize",
                    "token_endpoint": f"{IDPORTEN_ISSUER}/token",
                    "jwks_uri": f"{IDPORTEN_ISSUER}/jwks.json",
                },
            )
        return httpx.Response(
            401,
            json={
                "error": "invalid_client",
                "error_description": "Invalid JWT. Key identifier (kid) reference not found.",
            },
        )

    monkeypatch.setattr(
        idporten.client,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(refusing)),
    )
    with pytest.raises(IdPortenError, match=r"invalid_client - Invalid JWT\. Key identifier"):
        await _idporten_client(signing_key).exchange_code(code="c", code_verifier="v", nonce="n")


def test_a_refusal_outside_the_oauth_vocabulary_is_not_repeated() -> None:
    """Only RFC 6749's own error codes are echoed; anything else is dropped."""
    response = httpx.Response(400, json={"error": "code=abc123", "error_description": "x"})
    assert idporten.client._token_error(response) == ""
    assert idporten.client._token_error(httpx.Response(400, text="<html>")) == ""


# --- --mva: validation only -------------------------------------------------------


MVA_ORGNR = "311124226"  # Tenor, MVA-registered AS (test_data/README.md).


def test_the_mva_check_reports_the_period_that_has_ended() -> None:
    assert digdir_check.previous_termin(date(2026, 10, 9)).name == "juli-august"
    # In January the last finished period is last year's sixth.
    january = digdir_check.previous_termin(date(2027, 1, 15))
    assert (january.year, january.index) == (2026, 6)


def test_the_mva_check_builds_its_melding_with_the_real_aggregation() -> None:
    """A sale and a deduction, through the code a company's melding goes through."""
    melding = digdir_check.sample_melding(MVA_ORGNR, date(2026, 10, 9))
    assert [(line.mva_kode, line.merverdiavgift) for line in melding.lines] == [
        ("3", Decimal("2500.00")),
        ("1", Decimal("-500.00")),
    ]
    assert melding.fastsatt_merverdiavgift == Decimal("2000.00")


class _ValidationOnly:
    """An MVA client that can validate and can do nothing else.

    Any attribute but ``validate`` raises, so a check that so much as looked up
    ``create_instance`` or ``submit`` fails here rather than in tt02.
    """

    def __init__(self, answer: bytes) -> None:
        self.answer = answer
        self.sent: list[bytes] = []

    async def validate(self, melding: bytes, *, id_porten_token: str) -> MvaValidation:
        assert id_porten_token == "person-token"
        self.sent.append(melding)
        return MvaValidation(content=self.answer, content_type="application/xml")

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the --mva check must not touch {name}")


def _login() -> GovernmentLogin:
    return GovernmentLogin(
        access_token="person-token",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        pid=PID,
        acr=ACR,
        subject="sub-1",
        refresh_token=None,
    )


async def test_the_mva_check_validates_and_keeps_both_documents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    answer = (
        b"<valideringsresultat xmlns='no:skatteetaten:fastsetting:avgift:mva:"
        b"valideringsresultat:v1'><avvikVedMeldingslevering>ingen avvik"
        b"</avvikVedMeldingslevering></valideringsresultat>"
    )
    client = _ValidationOnly(answer)
    monkeypatch.setattr(digdir_check, "build_mva_client", lambda settings: client)
    report = digdir_check.Report()
    await digdir_check._check_mva_validation(
        _settings(), report, _login(), MVA_ORGNR, save_to=tmp_path, today=date(2026, 10, 9)
    )
    assert not report.failed and not report.warned
    assert MVA_ORGNR.encode() in client.sent[0]
    assert (tmp_path / "mva-melding.xml").read_bytes() == client.sent[0]
    assert (tmp_path / "valideringsresultat.xml").read_bytes() == answer
    assert "ingen avvik" in capsys.readouterr().out


async def test_a_blocking_verdict_is_reported_with_its_findings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The call worked; what Skatteetaten says is the result, so it is a warning."""
    answer = (
        b"<valideringsresultat><avvikVedMeldingslevering>ugyldig skattemelding"
        b"</avvikVedMeldingslevering><avvik><stiTilAvvik>/mvaKode</stiTilAvvik>"
        b"<avviksinformasjon><avvikstype>avvikende</avvikstype><avvikKode>R-1</avvikKode>"
        b"<begrunnelse>kode finnes ikke</begrunnelse></avviksinformasjon></avvik>"
        b"</valideringsresultat>"
    )
    monkeypatch.setattr(digdir_check, "build_mva_client", lambda settings: _ValidationOnly(answer))
    report = digdir_check.Report()
    await digdir_check._check_mva_validation(_settings(), report, _login(), MVA_ORGNR)
    assert report.warned and not report.failed
    output = capsys.readouterr().out
    assert "ugyldig skattemelding" in output
    assert "R-1 /mvaKode: kode finnes ikke" in output


async def test_a_validation_host_that_refuses_is_a_failure_naming_the_host(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Refusing(_ValidationOnly):
        async def validate(self, melding: bytes, *, id_porten_token: str) -> MvaValidation:
            raise MvaError("could not reach the MVA validation API at https://x")

    monkeypatch.setattr(digdir_check, "build_mva_client", lambda settings: Refusing(b""))
    report = digdir_check.Report()
    await digdir_check._check_mva_validation(
        _settings(skatteetaten_mva_api_base_url="https://x"), report, _login(), MVA_ORGNR
    )
    assert report.failed
    assert "host: https://x" in capsys.readouterr().out
