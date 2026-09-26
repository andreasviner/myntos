"""ID-porten end-user authentication (OIDC authorization-code flow).

As of 2026 Skatteetaten requires a personal ID-porten login for **validating
and submitting** both the MVA-melding and the skattemelding; Maskinporten is
accepted only for retrieval and information APIs. The submission path is:

1. Send the browser to ID-porten (authorization code + PKCE, scopes such as
   ``openid skatteetaten:mvameldingvalidering skatteetaten:mvameldinginnsending``).
2. Exchange the code here for an ID-porten **access token**.
3. Exchange that for an Altinn token
   (:func:`myntos.integrations.altinn.tokens.exchange_id_porten_token`).
4. Drive the instance API with the Altinn token: validate, submit, feedback.

**This is not a way to sign into Myntos, and it never becomes one.** The
federated login in :mod:`myntos.auth.oidc` answers "who is this user of our
application"; this answers "which person is standing behind this submission to
the state, right now". Sharing an implementation would invite exactly the
conflation the two planes exist to prevent — a Myntos session must never count
as evidence towards Skatteetaten, and an ID-porten login must never create or
attach an account. So the profile handling, the account linking and the
provider registry of that module are all absent here, and what comes back is a
short-lived token rather than an identity.

What *is* shared is the shape of the flow, and the two deliberate differences
from the sign-in client are worth stating:

- **Client authentication is ``private_key_jwt``.** Digdir's default for
  ID-porten integrations, and the only option for a client registered without a
  secret. A ``client_secret`` client is supported too, because some
  registrations have one — but the key is the path this is built around.
- **``acr_values`` is sent, and the returned ``acr`` is checked.** Asking for
  ``idporten-loa-high`` and not verifying what came back would leave the level
  of assurance up to the provider's defaults, and a signature legally attached
  to a personnummer is not the place for that.

The ``pid`` claim (the person's fødselsnummer) is read out of the ID token
because Skatteetaten and Altinn identify the submitter by it. It is returned to
the caller and never stored by this module.

Flow details: docs/integrations/mva.md and docs/integrations/maskinporten.md.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from myntos.auth.signing_keys import (
    SigningKey,
    SigningKeyError,
    load_signing_key,
    load_signing_key_material,
)
from myntos.core.clock import utc_now
from myntos.core.config import Settings, get_settings

_METADATA_TTL = timedelta(hours=1)
_HTTP_TIMEOUT = httpx.Timeout(10.0)

# The client assertion's lifetime. ID-porten is less strict than Maskinporten
# about the ceiling, but a client-authentication JWT that outlives the request
# it authenticates has no reason to.
_ASSERTION_LIFETIME = timedelta(seconds=60)

_CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

# ID-porten always needs openid; the interesting scopes are configured, because
# they are ordered from Skatteetaten one at a time and inert until granted.
_BASE_SCOPE = "openid"


def _client() -> httpx.AsyncClient:
    """The HTTP client one request runs on.

    Its own function so tests have a seam to replace with a stub transport, and
    so the timeout is decided in exactly one place.
    """
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT)


class IdPortenError(Exception):
    """Any failure in the ID-porten flow."""


class IdPortenNotConfiguredError(IdPortenError):
    """ID-porten was used in a deployment that has not been set up for it."""


@dataclass(frozen=True)
class PkcePair:
    """A PKCE verifier and its S256 challenge (RFC 7636)."""

    verifier: str
    challenge: str


def new_pkce_pair() -> PkcePair:
    """Generate a fresh PKCE verifier/challenge pair."""
    verifier = _b64url(secrets.token_bytes(32))
    return PkcePair(
        verifier=verifier,
        challenge=_b64url(hashlib.sha256(verifier.encode("ascii")).digest()),
    )


@dataclass(frozen=True)
class GovernmentLogin:
    """What an ID-porten login yields: a token, a person, and a deadline.

    Not an account and not a session — the caller uses this to make the calls
    the person just authorized, for as long as ``expires_at`` allows, and then
    it is gone. ``refresh_token`` is carried because ID-porten issues one (90
    days) and a submission flow can outlive a single access token, but nothing
    in this module persists it: where a government token may be stored, and for
    how long, is a decision for the submission code that holds it.
    """

    access_token: str
    expires_at: datetime
    # The person's fødselsnummer, from the ``pid`` claim. Skatteetaten and
    # Altinn identify the submitter by it.
    pid: str
    # Level of assurance actually reached, from the ``acr`` claim.
    acr: str
    subject: str
    refresh_token: str | None
    # The person's name, from Folkeregisteret by way of the ``name`` claim.
    # ``None`` unless the ``profile`` scope was requested *and* granted on this
    # client, which is a per-deployment fact rather than something to assume:
    # scopes are ticked onto a client in Samarbeidsportalen one at a time. Its
    # absence must never fail a login: a submission is attributed to the pid,
    # and the name is only ever used to recognise the same person in
    # Enhetsregisteret.
    name: str | None = None

    def is_usable(self, *, at: datetime | None = None) -> bool:
        return (at or utc_now()) < self.expires_at


class IdPortenClient:
    """Talks to one ID-porten integration. Caches discovery and signing keys."""

    def __init__(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        well_known_url: str,
        scope: str,
        acr_values: str,
        signing_key: SigningKey | None = None,
        client_secret: str | None = None,
    ) -> None:
        if signing_key is None and client_secret is None:
            raise IdPortenNotConfiguredError(
                "an ID-porten client authenticates with either a private key or "
                "a client secret; neither is configured"
            )
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self._well_known_url = well_known_url
        self._scope = scope
        self._acr_values = acr_values
        self._signing_key = signing_key
        self._client_secret = client_secret
        self._metadata: dict[str, Any] | None = None
        self._metadata_fetched_at = utc_now() - _METADATA_TTL
        self._jwks: dict[str, Any] | None = None

    @property
    def scope(self) -> str:
        return self._scope

    @property
    def redirect_uri(self) -> str:
        return self._redirect_uri

    async def issuer(self) -> str:
        """The issuer from the discovery document.

        Exposed because whether this client points at a test environment is a
        question worth being able to ask of the *provider* rather than of our own
        configuration — see :mod:`myntos.auth.idporten.testid`.
        """
        metadata = await self._get_metadata()
        return str(metadata["issuer"])

    async def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
        login_hint: str | None = None,
    ) -> str:
        """The URL to send the browser to.

        ``state``, ``nonce`` and the PKCE challenge all have to survive the
        redirect and come back matched; storing them is the caller's job, the
        same way it is for the sign-in flow.

        ``login_hint`` is only for TestID's automated login in the test
        environment (:mod:`myntos.auth.idporten.testid`), which is also where the
        guards against using it anywhere else live. A real login never sets it:
        which person is signing in is theirs to decide, not ours to hint at.
        """
        metadata = await self._get_metadata()
        query = {
            "client_id": self._client_id,
            "response_type": "code",
            "redirect_uri": self._redirect_uri,
            "scope": self._scope,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            # The level of assurance this login must reach. Checked again on the
            # way back — asking is not the same as getting.
            "acr_values": self._acr_values,
            # Norwegian first. ID-porten's own default follows the browser, and
            # a person filing a Norwegian tax return should not land on an
            # English consent screen because of a stray Accept-Language.
            "ui_locales": "nb",
        }
        if login_hint is not None:
            query["login_hint"] = login_hint
        return f"{metadata['authorization_endpoint']}?{urlencode(query)}"

    async def exchange_code(self, *, code: str, code_verifier: str, nonce: str) -> GovernmentLogin:
        """Trade an authorization code for a verified :class:`GovernmentLogin`."""
        metadata = await self._get_metadata()
        form: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._redirect_uri,
            "client_id": self._client_id,
            "code_verifier": code_verifier,
        }
        form.update(self._client_authentication(issuer=str(metadata["issuer"])))

        async with _client() as http:
            try:
                response = await http.post(
                    str(metadata["token_endpoint"]),
                    data=form,
                    headers={"Accept": "application/json"},
                )
            except httpx.HTTPError as error:
                raise IdPortenError("could not reach ID-porten") from error

        if response.status_code != httpx.codes.OK:
            # The body can echo request parameters; report nothing but the status.
            raise IdPortenError(
                f"ID-porten rejected the authorization code ({response.status_code})"
            )

        try:
            payload: dict[str, Any] = response.json()
        except ValueError as error:
            raise IdPortenError("ID-porten's token response was not JSON") from error

        access_token = payload.get("access_token")
        id_token = payload.get("id_token")
        if not isinstance(access_token, str) or not access_token:
            raise IdPortenError("ID-porten's token response carried no access_token")
        if not isinstance(id_token, str):
            raise IdPortenError("ID-porten's token response carried no id_token")

        claims = await self._verify_id_token(id_token, nonce=nonce)
        refresh_token = payload.get("refresh_token")
        try:
            lifetime = timedelta(seconds=int(payload.get("expires_in", 0)))
        except (TypeError, ValueError):
            lifetime = timedelta(0)

        return GovernmentLogin(
            access_token=access_token,
            expires_at=utc_now() + lifetime,
            pid=_pid_from(claims),
            acr=str(claims.get("acr", "")),
            subject=str(claims["sub"]),
            refresh_token=refresh_token if isinstance(refresh_token, str) else None,
            name=_name_from(claims),
        )

    def _client_authentication(self, *, issuer: str) -> dict[str, str]:
        """The client's own credentials on the token request.

        ``private_key_jwt`` when a key is configured — ``aud`` is the issuer,
        which is what Digdir expects, and ``iss``/``sub`` are both the client id
        as RFC 7523 §2.2 requires for client authentication.
        """
        if self._signing_key is not None:
            issued_at = utc_now()
            assertion = jwt.encode(
                {
                    "iss": self._client_id,
                    "sub": self._client_id,
                    "aud": issuer,
                    "iat": int(issued_at.timestamp()),
                    "exp": int((issued_at + _ASSERTION_LIFETIME).timestamp()),
                    "jti": str(uuid.uuid4()),
                },
                self._signing_key.key,
                algorithm=self._signing_key.algorithm,
                headers={"kid": self._signing_key.kid},
            )
            return {
                "client_assertion_type": _CLIENT_ASSERTION_TYPE,
                "client_assertion": assertion,
            }
        # Only reachable when a secret was configured: the constructor refuses a
        # client with neither.
        return {"client_secret": str(self._client_secret)}

    async def _verify_id_token(self, id_token: str, *, nonce: str) -> dict[str, Any]:
        """Verify signature, issuer, audience, nonce and level of assurance."""
        try:
            key_id = jwt.get_unverified_header(id_token).get("kid")
        except jwt.PyJWTError as error:
            raise IdPortenError("id_token is not a well-formed JWT") from error
        if not isinstance(key_id, str):
            raise IdPortenError("id_token header has no key id")

        signing_key = self._find_key(await self._get_jwks(), key_id)
        if signing_key is None:
            # Key rotation: refetch once before rejecting the token.
            signing_key = self._find_key(await self._get_jwks(refresh=True), key_id)
        if signing_key is None:
            raise IdPortenError("id_token was signed with an unknown key")

        metadata = await self._get_metadata()
        try:
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key=signing_key.key,
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=str(metadata["issuer"]),
                options={"require": ["exp", "iat", "aud", "iss", "sub"]},
            )
        except jwt.PyJWTError as error:
            raise IdPortenError(f"id_token failed verification: {error}") from error

        if claims.get("nonce") != nonce:
            raise IdPortenError("id_token nonce does not match the login attempt")
        # The requested level of assurance is a requirement, not a preference.
        # Accepting a lower one would mean a MinID login signing a tax return.
        if self._acr_values and claims.get("acr") != self._acr_values:
            raise IdPortenError(
                f"ID-porten authenticated at '{claims.get('acr')}', "
                f"but '{self._acr_values}' was required"
            )
        return claims

    async def _get_metadata(self) -> dict[str, Any]:
        if self._metadata is not None and utc_now() - self._metadata_fetched_at < _METADATA_TTL:
            return self._metadata
        async with _client() as http:
            try:
                response = await http.get(self._well_known_url)
            except httpx.HTTPError as error:
                raise IdPortenError("could not read ID-porten's discovery document") from error
        if response.status_code != httpx.codes.OK:
            raise IdPortenError(
                f"could not read ID-porten's discovery document ({response.status_code})"
            )
        try:
            metadata: dict[str, Any] = response.json()
        except ValueError as error:
            raise IdPortenError("ID-porten's discovery document was not JSON") from error
        for required in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
            if not isinstance(metadata.get(required), str):
                raise IdPortenError(f"discovery document is missing '{required}'")
        self._metadata = metadata
        self._metadata_fetched_at = utc_now()
        return metadata

    async def _get_jwks(self, *, refresh: bool = False) -> dict[str, Any]:
        if self._jwks is not None and not refresh:
            return self._jwks
        metadata = await self._get_metadata()
        async with _client() as http:
            try:
                response = await http.get(str(metadata["jwks_uri"]))
            except httpx.HTTPError as error:
                raise IdPortenError("could not read ID-porten's key set") from error
        if response.status_code != httpx.codes.OK:
            raise IdPortenError(f"could not read ID-porten's key set ({response.status_code})")
        try:
            jwks: dict[str, Any] = response.json()
        except ValueError as error:
            raise IdPortenError("ID-porten's key set was not JSON") from error
        self._jwks = jwks
        return jwks

    @staticmethod
    def _find_key(jwks: dict[str, Any], key_id: str) -> jwt.PyJWK | None:
        for key in jwt.PyJWKSet.from_dict(jwks).keys:
            if key.key_id == key_id:
                return key
        return None


def _pid_from(claims: dict[str, Any]) -> str:
    """The submitter's fødselsnummer.

    ID-porten puts it in ``pid``. Without it there is nobody to attribute a
    submission to, so its absence is a failure rather than a missing field:
    Altinn and Skatteetaten both key the instance owner on this number.
    """
    pid = claims.get("pid")
    if not isinstance(pid, str) or not pid.strip():
        raise IdPortenError("id_token carries no 'pid' — the login identifies no person")
    return pid.strip()


def _name_from(claims: dict[str, Any]) -> str | None:
    """The person's name, if this login carried one.

    Unlike :func:`_pid_from`, an absence is not a failure. The ``name`` claim
    comes with the ``profile`` scope, which is granted per client, so a
    deployment that has not been given it gets a perfectly usable login with no
    name on it.
    """
    name = claims.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    return name.strip()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def configured_scope(settings: Settings | None = None) -> str:
    """The scope string this deployment asks ID-porten for.

    ``openid`` always, plus whatever Skatteetaten has granted. Duplicates are
    removed and the order preserved, so a configured value that repeats
    ``openid`` is not sent twice.
    """
    settings = settings or get_settings()
    scopes = [_BASE_SCOPE, *settings.idporten_scopes.split()]
    return " ".join(dict.fromkeys(scopes))


# One client per process: it caches the discovery document and the key set, and
# discarding that per login would add two round trips to every one.
_CLIENT: IdPortenClient | None = None


def reset_client() -> None:
    """Drop the cached client. For tests that change the configuration."""
    global _CLIENT
    _CLIENT = None


def is_configured(settings: Settings | None = None) -> bool:
    """Whether this deployment can start an ID-porten login at all.

    The submission features are offered on the strength of this, the same way
    the sign-in buttons are: a half-configured integration should be absent
    rather than a button that fails on click.
    """
    settings = settings or get_settings()
    # A blank value counts as unset. ``.env.example`` ships every one of these
    # empty, so reading "the variable exists" as "the credential is there" would
    # offer a submission flow that fails at the first token request.
    return bool(
        (settings.idporten_client_id or "").strip()
        and (settings.idporten_redirect_uri or "").strip()
        and (
            settings.idporten_key_file is not None
            or settings.idporten_key_material is not None
            or _secret(settings)
        )
    )


def _secret(settings: Settings) -> str | None:
    """The configured client secret, or ``None`` when it is blank or absent."""
    secret = settings.idporten_client_secret
    if secret is None:
        return None
    return secret.get_secret_value().strip() or None


def build_client(settings: Settings) -> IdPortenClient:
    """The configured ID-porten client, or raise a readable error."""
    client_id = (settings.idporten_client_id or "").strip()
    redirect_uri = (settings.idporten_redirect_uri or "").strip()
    if not client_id or not redirect_uri:
        raise IdPortenNotConfiguredError(
            "ID-porten is not configured. Set IDPORTEN_CLIENT_ID and "
            "IDPORTEN_REDIRECT_URI (registered identically in "
            "Samarbeidsportalen) — see docs/integrations/mva.md."
        )

    signing_key: SigningKey | None = None
    material = settings.idporten_key_material
    key_path = settings.idporten_key_file
    try:
        if material is not None:
            signing_key = load_signing_key_material(
                material, kid=settings.idporten_kid, source="IDPORTEN_JWK"
            )
        elif key_path is not None:
            signing_key = load_signing_key(key_path, kid=settings.idporten_kid)
    except SigningKeyError as error:
        raise IdPortenError(f"the ID-porten client key is unusable: {error}") from error

    client_secret = _secret(settings)
    if signing_key is None and not client_secret:
        raise IdPortenNotConfiguredError(
            "ID-porten needs a client credential: set IDPORTEN_JWK_PATH (with "
            "IDPORTEN_KID for a PEM) for private_key_jwt, or "
            "IDPORTEN_CLIENT_SECRET for a secret-based client."
        )

    return IdPortenClient(
        client_id=client_id,
        redirect_uri=redirect_uri,
        well_known_url=settings.idporten_well_known_url,
        scope=configured_scope(settings),
        acr_values=settings.idporten_acr_values,
        signing_key=signing_key,
        client_secret=client_secret or None,
    )


def get_client() -> IdPortenClient:
    """The process-wide ID-porten client."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = build_client(get_settings())
    return _CLIENT
