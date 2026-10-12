"""A provider-agnostic OpenID Connect client.

One flow, several providers. The differences between Google, Microsoft and the
BankID broker are data — scopes, extra authorization parameters, how the issuer
is checked, which claims carry the email, whether there is an email at all —
and live in :mod:`.providers`. The protocol itself is here, once.

The flow, and why each piece is there:

1. ``/auth/{provider}/start`` sends the browser to the provider's authorization
   endpoint with a ``state`` (ties the callback to the request that began it),
   a ``nonce`` (ties the returned ID token to that same request) and a PKCE
   challenge (makes an intercepted authorization code useless without the
   verifier). All three are stored in
   :class:`~myntos.domain.identity.models.OAuthLoginAttempt`.
2. The provider redirects back with a code; the backend exchanges it for tokens
   over TLS, authenticating as the client: with a ``private_key_jwt``
   assertion when a key is configured (the BankID broker recommends it, and no
   secret then exists to leak), with the client secret otherwise.
3. The ID token's signature, issuer, audience, expiry and nonce are all
   verified before a single claim is trusted.

Step 3 is arguably redundant — the token came straight from the token endpoint
over an authenticated TLS connection, and OIDC Core §3.1.3.7 permits skipping
signature verification in exactly that case. It is done anyway, because the
nonce still has to be checked, the code is the same either way, and "we trusted
this token because of how it arrived" is a comment that stops being true the
first time someone reuses this function elsewhere.

Endpoint URLs come from each provider's discovery document rather than being
hardcoded, and signing keys from the ``jwks_uri`` in it. Providers rotate those
keys, so an unknown key id triggers exactly one refetch before the token is
rejected.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from myntos.auth.signing_keys import SigningKey, client_assertion
from myntos.core.clock import utc_now

# Discovery documents and key sets are stable for hours and every provider asks
# that they be cached. An hour keeps key rotation cheap without hammering them
# on every login.
_METADATA_TTL = timedelta(hours=1)

_HTTP_TIMEOUT = httpx.Timeout(10.0)

_CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


def _http() -> httpx.AsyncClient:
    """The HTTP client one request to a provider runs on.

    Its own function for the reason the ID-porten client has one: it is the
    seam a test replaces with a stub broker, so the whole flow (discovery, key
    set, token request, signature) runs for real against it.
    """
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT)


class OidcError(Exception):
    """Any failure in a federated sign-in flow."""


class ProviderNotConfiguredError(OidcError):
    """Sign-in was attempted with a provider this deployment has not set up."""


@dataclass(frozen=True)
class OidcProfile:
    """The claims we take from a verified ID token.

    ``email_verified`` is load-bearing rather than informational: it decides
    whether this identity may be attached to an **existing** Myntos account.
    See :func:`myntos.domain.identity.service.sign_in_with_provider`.

    Providers differ on how they say it, and one of them (Microsoft) does not
    say it at all unless configured to — which is why deriving this value is
    each provider's own job in :mod:`.providers`, and why every one of them
    treats absence as *not verified*.

    ``email`` is ``None`` for a provider that identifies a person without
    knowing how to reach them (BankID). Such a profile can never be matched to
    an account by address, verified or not; see
    :attr:`ProviderConfig.asserts_email`.
    """

    subject: str
    email: str | None
    email_verified: bool
    full_name: str | None


@dataclass(frozen=True)
class PkcePair:
    """A PKCE verifier and its S256 challenge (RFC 7636)."""

    verifier: str
    challenge: str


def new_pkce_pair() -> PkcePair:
    """Generate a fresh PKCE verifier/challenge pair."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return PkcePair(verifier=verifier, challenge=challenge)


def _b64url(raw: bytes) -> str:
    """Base64url without padding, as every OAuth/JOSE spec requires."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def default_profile(claims: dict[str, Any]) -> OidcProfile:
    """Read a profile from the standard OIDC claims.

    Used by providers that emit ``email`` and ``email_verified`` the way the
    specification describes. A missing ``email_verified`` reads as **false**;
    treating "the provider said nothing" as "the provider vouched for it" is
    the whole account-takeover bug.
    """
    subject = claims.get("sub")
    email = claims.get("email")
    if not isinstance(subject, str) or not isinstance(email, str) or not email:
        raise OidcError("id_token is missing 'sub' or 'email'")
    name = claims.get("name")
    return OidcProfile(
        subject=subject,
        email=email,
        email_verified=claims.get("email_verified") is True,
        full_name=name if isinstance(name, str) and name.strip() else None,
    )


def exact_issuer(metadata_issuer: str, claims: dict[str, Any]) -> bool:
    """The ordinary rule: the token's issuer equals the discovered one."""
    return claims.get("iss") == metadata_issuer


@dataclass(frozen=True)
class ProviderConfig:
    """Everything that differs between one OIDC provider and the next."""

    # Stable machine name, used in URLs and stored on user_identity rows.
    code: str
    # What the button says.
    label: str
    scope: str
    client_id: str
    redirect_uri: str
    well_known_url: str
    # The client credential: a secret, or a key for ``private_key_jwt``. At
    # least one; the key wins when both are set, because the point of having
    # one is that no shared secret crosses the wire.
    client_secret: str | None = None
    signing_key: SigningKey | None = None
    # Provider-specific query parameters on the authorization request.
    authorization_params: dict[str, str] = field(default_factory=dict)
    # Compares the token's issuer against the discovered one. Not always
    # equality: Microsoft's multi-tenant discovery document carries a template.
    issuer_matches: Callable[[str, dict[str, Any]], bool] = exact_issuer
    profile_from: Callable[[dict[str, Any]], OidcProfile] = default_profile
    # Whether this provider says how to reach the person. False for BankID,
    # which says who they are and nothing else; every rule that finds or makes
    # an account by address is then off, and the callback follows
    # myntos.domain.identity.provider_signup instead.
    asserts_email: bool = True

    def __post_init__(self) -> None:
        if self.client_secret is None and self.signing_key is None:
            raise ProviderNotConfiguredError(
                f"{self.code} has neither a client secret nor a signing key"
            )


class OidcClient:
    """Talks to one provider. One instance per provider; caches discovery and keys."""

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config
        self._metadata: dict[str, Any] | None = None
        self._metadata_fetched_at = utc_now() - _METADATA_TTL
        self._jwks: dict[str, Any] | None = None

    @property
    def code(self) -> str:
        return self._config.code

    @property
    def label(self) -> str:
        return self._config.label

    @property
    def redirect_uri(self) -> str:
        return self._config.redirect_uri

    @property
    def asserts_email(self) -> bool:
        return self._config.asserts_email

    async def _get(self, url: str, what: str) -> dict[str, Any]:
        """GET a JSON document from the provider, or raise :class:`OidcError`.

        An unreachable provider is an ``OidcError`` like any other failure, so
        the browser lands back on our page with a marker instead of on a 500.
        """
        try:
            async with _http() as http:
                response = await http.get(url)
        except httpx.HTTPError as error:
            raise OidcError(f"could not reach {self._config.code} for its {what}") from error
        if response.status_code != httpx.codes.OK:
            raise OidcError(
                f"could not read the {what} for {self._config.code} ({response.status_code})"
            )
        try:
            document: dict[str, Any] = response.json()
        except ValueError as error:
            raise OidcError(f"the {what} for {self._config.code} was not JSON") from error
        return document

    async def _get_metadata(self) -> dict[str, Any]:
        if self._metadata is not None and utc_now() - self._metadata_fetched_at < _METADATA_TTL:
            return self._metadata
        metadata = await self._get(self._config.well_known_url, "discovery document")
        for required in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
            if required not in metadata:
                raise OidcError(f"discovery document is missing '{required}'")
        self._metadata = metadata
        self._metadata_fetched_at = utc_now()
        return metadata

    async def _get_jwks(self, *, refresh: bool = False) -> dict[str, Any]:
        if self._jwks is not None and not refresh:
            return self._jwks
        metadata = await self._get_metadata()
        jwks = await self._get(str(metadata["jwks_uri"]), "key set")
        self._jwks = jwks
        return jwks

    async def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        """The URL to send the browser to."""
        metadata = await self._get_metadata()
        query = {
            "client_id": self._config.client_id,
            "response_type": "code",
            "redirect_uri": self._config.redirect_uri,
            # Identity only. Never access to mail, files or anything else the
            # provider would happily hand over.
            "scope": self._config.scope,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            **self._config.authorization_params,
        }
        return f"{metadata['authorization_endpoint']}?{urlencode(query)}"

    def _client_authentication(self, *, issuer: str) -> dict[str, str]:
        """The client's own credential on the token request.

        The assertion's ``aud`` is the issuer: Idura's guide asks for "your
        Idura Verify domain", which is its issuer, and ID-porten asks for the
        same. A secret goes in the body (``client_secret_post``), which every
        provider here accepts.
        """
        if self._config.signing_key is not None:
            return {
                "client_assertion_type": _CLIENT_ASSERTION_TYPE,
                "client_assertion": client_assertion(
                    self._config.signing_key,
                    client_id=self._config.client_id,
                    audience=issuer,
                ),
            }
        # Only reachable with a secret: ProviderConfig refuses neither.
        return {"client_secret": str(self._config.client_secret)}

    async def exchange_code(self, *, code: str, code_verifier: str, nonce: str) -> OidcProfile:
        """Trade an authorization code for a verified :class:`OidcProfile`."""
        metadata = await self._get_metadata()
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._config.redirect_uri,
            "client_id": self._config.client_id,
            "code_verifier": code_verifier,
            **self._client_authentication(issuer=str(metadata["issuer"])),
        }
        try:
            async with _http() as http:
                response = await http.post(
                    str(metadata["token_endpoint"]),
                    data=form,
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as error:
            raise OidcError(f"could not reach {self._config.code} to redeem the code") from error
        if response.status_code != httpx.codes.OK:
            # The body carries a machine-readable reason but can echo request
            # parameters; report nothing but the status.
            raise OidcError(
                f"{self._config.code} rejected the authorization code ({response.status_code})"
            )

        try:
            payload: dict[str, Any] = response.json()
        except ValueError as error:
            raise OidcError(f"{self._config.code}'s token response was not JSON") from error
        id_token = payload.get("id_token")
        if not isinstance(id_token, str):
            raise OidcError("token response contained no id_token")
        claims = await self._verify_id_token(id_token, nonce=nonce)
        return self._config.profile_from(claims)

    async def _verify_id_token(self, id_token: str, *, nonce: str) -> dict[str, Any]:
        try:
            key_id = jwt.get_unverified_header(id_token).get("kid")
        except jwt.PyJWTError as error:
            raise OidcError("id_token is not a well-formed JWT") from error
        if not isinstance(key_id, str):
            raise OidcError("id_token header has no key id")

        signing_key = self._find_key(await self._get_jwks(), key_id)
        if signing_key is None:
            # Key rotation: refetch once before rejecting the token.
            signing_key = self._find_key(await self._get_jwks(refresh=True), key_id)
        if signing_key is None:
            raise OidcError("id_token was signed with an unknown key")

        try:
            claims: dict[str, Any] = jwt.decode(
                id_token,
                # ``.key`` rather than the PyJWK itself: passing the JWK object
                # straight to decode() is only supported in newer PyJWT.
                key=signing_key.key,
                algorithms=["RS256"],
                audience=self._config.client_id,
                # Checked below instead: PyJWT compares the issuer to a single
                # string, and not every provider's is a single string.
                options={
                    "verify_iss": False,
                    "require": ["exp", "iat", "aud", "iss", "sub"],
                },
            )
        except jwt.PyJWTError as error:
            raise OidcError(f"id_token failed verification: {error}") from error

        metadata = await self._get_metadata()
        if not self._config.issuer_matches(str(metadata["issuer"]), claims):
            raise OidcError(f"id_token was not issued by {self._config.code}")
        # The nonce binds this token to the sign-in attempt that started the
        # flow. Without it, a token minted for another session of the same
        # client would be accepted here.
        if claims.get("nonce") != nonce:
            raise OidcError("id_token nonce does not match the sign-in attempt")
        return claims

    @staticmethod
    def _find_key(jwks: dict[str, Any], key_id: str) -> jwt.PyJWK | None:
        for key in jwt.PyJWKSet.from_dict(jwks).keys:
            if key.key_id == key_id:
                return key
        return None
