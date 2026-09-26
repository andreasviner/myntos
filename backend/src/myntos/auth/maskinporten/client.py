"""Maskinporten machine-to-machine authentication.

Retrieval APIs at Skatteetaten, and every Altinn 3 instance API, want an access
token this installation can obtain without a person present. Maskinporten issues
one against a signed JWT grant (RFC 7523):

1. Build a JWT assertion (``iss`` = client id, ``aud`` = the issuer from the
   well-known document, ``scope`` = requested scopes, short lifetime, fresh
   ``jti``).
2. Sign it with the client's private key — see
   :mod:`myntos.auth.signing_keys`.
3. POST it to the token endpoint and cache the access token in memory until
   shortly before it expires.

The rest of the codebase depends on :class:`TokenProvider`, never on a concrete
client, so a deployment without Maskinporten credentials still imports and runs:
:func:`get_token_provider` hands back a provider that raises a readable error
instead of a client that half-works.

**What Maskinporten does not buy you.** Retrieval and information only. As of
2026 Skatteetaten requires an ID-porten-logged-in person to validate or submit
the MVA-melding or the skattemelding, so nothing here should be read as a way to
file anything headlessly — see :mod:`myntos.auth.idporten.client`.

Sharp edges, each of which is a real failure mode rather than a hypothetical
(documented at docs/integrations/maskinporten.md):

- ``aud`` is the issuer **exactly**, trailing slash included. A trailing-slash
  difference is ``invalid_grant`` with no further explanation.
- ``exp - iat`` must be at most 120 seconds, and ``iat`` is checked against
  Maskinporten's clock with only seconds of tolerance — a container with a
  drifting clock fails every request.
- A grant is **single use**. Retrying with the same assertion fails; the retry
  has to build a new one with a new ``jti``, which is why the assertion is built
  inside the request path and never cached.
- Requesting a scope the organization has not been granted fails the *whole*
  token request, so scopes are configuration and asking for more than the
  operation needs is not free.

Tokens are cached in memory only, never written to disk and never logged. The
cache key includes ``consumer_org``, because a token obtained under one
company's delegation must never be handed to a caller acting for another.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

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

# The grant's own lifetime. Maskinporten caps exp - iat at 120 seconds; 60 is
# comfortably inside that and still tolerant of a slow request.
_GRANT_LIFETIME = timedelta(seconds=60)

# How much of a token's remaining life is treated as already gone. A request
# that starts with 30 seconds left should not fail because it finished with
# none, and actual lifetimes vary per scope (minutes for some, eight hours for
# Skatteetaten's skattemelding scope), so this is a margin, not a schedule.
_EXPIRY_MARGIN = timedelta(seconds=30)

# The discovery document changes about never, and Digdir asks that it be cached.
_METADATA_TTL = timedelta(hours=1)

_HTTP_TIMEOUT = httpx.Timeout(10.0)

_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"

# RFC 9396 rich authorization requests, as Altinn defines them for systembruker.
# A token carrying this has a *subject* - the customer's system user - which is
# what an ordinary client-credentials token lacks and what Skatteetaten's
# SME-EKSTERN-005 is complaining about.
_SYSTEMUSER_TYPE = "urn:altinn:systemuser"
_ISO6523_ORG = "iso6523-actorid-upis"
_ORG_PREFIX = "0192:"


def systemuser_authorization_details(
    systemuser_org: str, *, external_ref: str | None = None
) -> list[dict[str, Any]]:
    """The ``authorization_details`` for acting as a customer's system user.

    ``systemuser_org`` is the *customer's* orgnr, not ours: this claim says whose
    system user we are, and getting it wrong means acting for the wrong company.
    ``external_ref`` is only needed when one customer has several system users
    against the same system.
    """
    detail: dict[str, Any] = {
        "type": _SYSTEMUSER_TYPE,
        "systemuser_org": {
            "authority": _ISO6523_ORG,
            "ID": f"{_ORG_PREFIX}{systemuser_org}",
        },
    }
    if external_ref is not None:
        detail["externalRef"] = external_ref
    return [detail]


def _client() -> httpx.AsyncClient:
    """The HTTP client one request runs on.

    Its own function so tests have a seam to replace with a stub transport, and
    so the timeout is decided in exactly one place.
    """
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT)


class MaskinportenError(Exception):
    """Base error for Maskinporten failures."""


class MaskinportenNotConfiguredError(MaskinportenError):
    """Raised when no real token provider has been configured."""


class TokenProvider(Protocol):
    """Anything that can produce a Maskinporten access token."""

    async def get_access_token(
        self,
        scopes: Sequence[str],
        *,
        consumer_org: str | None = None,
        systemuser_org: str | None = None,
        external_ref: str | None = None,
    ) -> str:
        """Return a valid access token covering ``scopes``."""
        ...


class NotConfiguredTokenProvider:
    """Placeholder that fails loudly until Maskinporten is wired up."""

    async def get_access_token(
        self,
        scopes: Sequence[str],
        *,
        consumer_org: str | None = None,
        systemuser_org: str | None = None,
        external_ref: str | None = None,
    ) -> str:
        del scopes, consumer_org, systemuser_org, external_ref
        raise MaskinportenNotConfiguredError(
            "Maskinporten is not configured. Set MASKINPORTEN_CLIENT_ID and "
            "MASKINPORTEN_JWK_PATH (plus MASKINPORTEN_KID if the key is a PEM) "
            "in .env — see docs/integrations/maskinporten.md."
        )


@dataclass(frozen=True)
class _CachedToken:
    """An access token and the instant it stops being safe to use."""

    access_token: str
    usable_until: datetime

    def is_usable(self, now: datetime) -> bool:
        return now < self.usable_until


class JwtGrantTokenProvider:
    """Obtains and caches Maskinporten tokens with a signed JWT grant.

    One instance per client. Holding on to it is the point: it caches the
    discovery document and the tokens, and a fresh instance per request would
    turn one API call into three.
    """

    def __init__(
        self,
        *,
        client_id: str,
        signing_key: SigningKey,
        well_known_url: str,
        default_consumer_org: str | None = None,
    ) -> None:
        self._client_id = client_id
        self._signing_key = signing_key
        self._well_known_url = well_known_url
        self._default_consumer_org = default_consumer_org
        self._metadata: dict[str, Any] | None = None
        self._metadata_fetched_at = utc_now() - _METADATA_TTL
        self._tokens: dict[tuple[str, str | None, str | None, str | None], _CachedToken] = {}
        # Two concurrent requests needing the same scopes should produce one
        # token request, not two — grants are single-use and Maskinporten
        # rate-limits, so the second would be waste at best.
        self._lock = asyncio.Lock()

    async def get_access_token(
        self,
        scopes: Sequence[str],
        *,
        consumer_org: str | None = None,
        systemuser_org: str | None = None,
        external_ref: str | None = None,
    ) -> str:
        """A token covering ``scopes``, from the cache when one is still good.

        ``consumer_org`` is the orgnr of the company whose delegation is being
        used (the classic regnskapsfører case). ``systemuser_org`` is the orgnr
        of a company that has approved a **system user** for this system, which
        is the newer mechanism for the same goal and the only one that works
        with nobody logged in.

        Both are part of the cache key, never details applied after the fact:
        the token that comes back carries the authority in its claims, and
        handing it to a caller acting for another company would be acting with
        the wrong authority. They are also mutually exclusive - a grant cannot
        both borrow a delegation and be a system user - and asking for both is
        an error rather than a silent preference.
        """
        if not scopes:
            raise MaskinportenError("a Maskinporten token needs at least one scope")
        if consumer_org is not None and systemuser_org is not None:
            raise MaskinportenError(
                "a grant is either a delegation (consumer_org) or a system user "
                "(systemuser_org), never both"
            )
        scope = " ".join(sorted(scopes))
        organization = consumer_org or self._default_consumer_org
        if systemuser_org is not None:
            # A systembruker grant carries its own authority, so the configured
            # default delegation must not be smuggled in alongside it.
            organization = None
        key = (scope, organization, systemuser_org, external_ref)

        cached = self._tokens.get(key)
        now = utc_now()
        if cached is not None and cached.is_usable(now):
            return cached.access_token

        async with self._lock:
            # Another caller may have filled the cache while this one waited.
            cached = self._tokens.get(key)
            if cached is not None and cached.is_usable(utc_now()):
                return cached.access_token
            token = await self._request_token(scope, organization, systemuser_org, external_ref)
            self._tokens[key] = token
            return token.access_token

    async def _request_token(
        self,
        scope: str,
        consumer_org: str | None,
        systemuser_org: str | None = None,
        external_ref: str | None = None,
    ) -> _CachedToken:
        metadata = await self._get_metadata()
        assertion = self._build_assertion(
            issuer=str(metadata["issuer"]),
            scope=scope,
            consumer_org=consumer_org,
            systemuser_org=systemuser_org,
            external_ref=external_ref,
        )
        async with _client() as http:
            try:
                response = await http.post(
                    str(metadata["token_endpoint"]),
                    data={"grant_type": _GRANT_TYPE, "assertion": assertion},
                    headers={"Accept": "application/json"},
                )
            except httpx.HTTPError as error:
                raise MaskinportenError("could not reach Maskinporten") from error

        if response.status_code != httpx.codes.OK:
            # The error body names the reason (invalid_grant, invalid_scope,
            # invalid_client) and is worth having: every one of them points at a
            # different piece of configuration. It cannot contain the assertion,
            # so it is safe to pass on — but the token, if there were one, would
            # not be in an error response either way.
            raise MaskinportenError(
                f"Maskinporten refused the grant ({response.status_code}): "
                f"{_error_summary(response)}"
            )

        try:
            payload: dict[str, Any] = response.json()
        except ValueError as error:
            raise MaskinportenError("Maskinporten's answer was not JSON") from error

        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise MaskinportenError("Maskinporten's answer carried no access_token")

        # A missing or unreadable expires_in is treated as "expires now": the
        # token still works for this one call, and the alternative is caching it
        # forever on a guess.
        try:
            lifetime = timedelta(seconds=int(payload.get("expires_in", 0)))
        except (TypeError, ValueError):
            lifetime = timedelta(0)
        return _CachedToken(
            access_token=access_token,
            usable_until=utc_now() + max(lifetime - _EXPIRY_MARGIN, timedelta(0)),
        )

    def _build_assertion(
        self,
        *,
        issuer: str,
        scope: str,
        consumer_org: str | None,
        systemuser_org: str | None = None,
        external_ref: str | None = None,
    ) -> str:
        """The single-use JWT grant.

        Built fresh every time, deliberately: the ``jti`` may not be reused and
        ``iat`` is checked against Maskinporten's clock, so a cached assertion
        is a rejected one.
        """
        issued_at = utc_now()
        claims: dict[str, Any] = {
            # Exactly the issuer string from the discovery document. Not the
            # token endpoint, not the issuer with the trailing slash trimmed.
            "aud": issuer,
            "iss": self._client_id,
            "scope": scope,
            "iat": int(issued_at.timestamp()),
            "exp": int((issued_at + _GRANT_LIFETIME).timestamp()),
            "jti": str(uuid.uuid4()),
        }
        if consumer_org is not None:
            claims["consumer_org"] = consumer_org
        if systemuser_org is not None:
            # Inside the assertion, not a form parameter on the token request.
            # Maskinporten answers invalid_grant either way, so the difference
            # is invisible from the error.
            claims["authorization_details"] = systemuser_authorization_details(
                systemuser_org, external_ref=external_ref
            )
        return jwt.encode(
            claims,
            self._signing_key.key,
            algorithm=self._signing_key.algorithm,
            headers={"kid": self._signing_key.kid},
        )

    async def _get_metadata(self) -> dict[str, Any]:
        """The well-known document, cached.

        Digdir's recommendation is to read the issuer and token endpoint from
        here rather than hardcode them — which also means an environment change
        is one URL, and that ``ver2.maskinporten.no`` (dead since 2024) cannot
        be baked into the code.
        """
        if self._metadata is not None and utc_now() - self._metadata_fetched_at < _METADATA_TTL:
            return self._metadata
        async with _client() as http:
            try:
                response = await http.get(self._well_known_url)
            except httpx.HTTPError as error:
                raise MaskinportenError(
                    f"could not read Maskinporten's discovery document at {self._well_known_url}"
                ) from error
        if response.status_code != httpx.codes.OK:
            raise MaskinportenError(
                f"could not read Maskinporten's discovery document ({response.status_code})"
            )
        try:
            metadata: dict[str, Any] = response.json()
        except ValueError as error:
            raise MaskinportenError("Maskinporten's discovery document was not JSON") from error
        for required in ("issuer", "token_endpoint"):
            if not isinstance(metadata.get(required), str):
                raise MaskinportenError(f"discovery document is missing '{required}'")
        self._metadata = metadata
        self._metadata_fetched_at = utc_now()
        return metadata


def _error_summary(response: httpx.Response) -> str:
    """The ``error``/``error_description`` pair, or a short slice of the body."""
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    if not isinstance(payload, dict):
        return response.text[:200]
    error = payload.get("error")
    description = payload.get("error_description")
    return " - ".join(str(part) for part in (error, description) if part) or response.text[:200]


def configured_scopes(settings: Settings | None = None) -> list[str]:
    """The scopes this deployment is set up to request.

    Whitespace-separated in the environment, because that is how OAuth spells a
    scope list and how Digdir's portal shows them.
    """
    settings = settings or get_settings()
    return settings.maskinporten_scopes.split()


# One provider per process. The token cache is the reason: throwing it away
# between requests would mean a new grant, and a new round trip, for every call.
_PROVIDER: TokenProvider | None = None


def reset_provider() -> None:
    """Drop the cached provider. For tests that change the configuration."""
    global _PROVIDER
    _PROVIDER = None


def build_token_provider(settings: Settings) -> TokenProvider:
    """A real provider when the credentials are configured, or the placeholder.

    A half-configured Maskinporten (a client id but no key, say) is treated as
    unconfigured rather than as an error at import time: the application has
    plenty of work that does not touch a government API, and it should still
    start. What it must not do is look configured and fail later at a point
    where the reason is hard to see — hence a placeholder whose error names the
    missing variables.
    """
    client_id = (settings.maskinporten_client_id or "").strip()
    material = settings.maskinporten_key_material
    key_path = settings.maskinporten_key_file
    if not client_id:
        return NotConfiguredTokenProvider()

    try:
        # Inline wins over the path: a deployment whose credentials arrive as
        # secret bindings has the key material and no file to point at.
        if material is not None:
            signing_key = load_signing_key_material(
                material, kid=settings.maskinporten_kid, source="MASKINPORTEN_JWK"
            )
        elif key_path is not None:
            signing_key = load_signing_key(key_path, kid=settings.maskinporten_kid)
        else:
            return NotConfiguredTokenProvider()
    except SigningKeyError as error:
        # Configured but unusable is a different situation from not configured,
        # and it must not be silently downgraded into "the feature is off".
        raise MaskinportenError(f"the Maskinporten client key is unusable: {error}") from error

    return JwtGrantTokenProvider(
        client_id=client_id,
        signing_key=signing_key,
        well_known_url=settings.maskinporten_well_known_url,
        default_consumer_org=(settings.maskinporten_consumer_org or "").strip() or None,
    )


def get_token_provider() -> TokenProvider:
    """The process-wide Maskinporten token provider."""
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = build_token_provider(get_settings())
    return _PROVIDER
