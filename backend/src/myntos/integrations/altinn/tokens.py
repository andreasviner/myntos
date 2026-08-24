"""Exchanging a Digdir token for an Altinn token.

For an Altinn 3 app — skattemelding, MVA-melding, brreg årsregnskap — neither a
Maskinporten nor an ID-porten token is the credential the app accepts. Both have
to be exchanged first:

    GET {platform}/authentication/api/v1/exchange/{maskinporten|id-porten}
        Authorization: Bearer <the Digdir token>

and the Altinn JWT that comes back is what the instance APIs take. A raw
ID-porten token against an app answers 401, which reads like an authorization
problem and is really this missing step — the single most confusing failure in
the whole chain, and the reason this is its own module with its own name.

Which of the two paths is used is not a preference. A Maskinporten-derived
Altinn token can fetch, create an instance and upload data; the **final
submission** of an MVA-melding or a skattemelding requires the ID-porten-derived
one, because the state requires a person behind it. The two are therefore
separate functions rather than one with a flag, so that a call site reads as the
claim it is making about who is acting.

Tokens are cached in memory by their own expiry, read from the JWT's ``exp``:
the exchange is a round trip per call otherwise, and an Altinn token is short
lived enough that caching it forever is not on the table.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

import httpx
import jwt

from myntos.auth.maskinporten import TokenProvider, get_token_provider
from myntos.core.clock import utc_now
from myntos.core.config import get_settings

# The scopes an end-user system needs to work with instances. The
# ``altinn:serviceowner/*`` family is for service owners and fails delegation
# checks for a system like this one — requesting it is a common wrong turn.
INSTANCE_SCOPES = ("altinn:instances.read", "altinn:instances.write")

_HTTP_TIMEOUT = httpx.Timeout(30.0)

# Altinn tokens are short lived (minutes). The margin only has to cover the call
# the token was fetched for.
_EXPIRY_MARGIN = timedelta(seconds=30)

_Plane = Literal["maskinporten", "id-porten"]


def _client() -> httpx.AsyncClient:
    """The HTTP client one request runs on.

    Its own function so tests have a seam to replace with a stub transport, and
    so the timeout is decided in exactly one place.
    """
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT)


class AltinnError(Exception):
    """Base error for Altinn platform failures."""


class AltinnAuthError(AltinnError):
    """Altinn refused the token, or would not exchange it."""


@dataclass(frozen=True)
class AltinnToken:
    """An Altinn JWT and the instant it stops being safe to use."""

    value: str
    usable_until: datetime

    def is_usable(self, *, at: datetime | None = None) -> bool:
        return (at or utc_now()) < self.usable_until


async def exchange_token(token: str, *, plane: _Plane) -> AltinnToken:
    """Exchange a Digdir token for an Altinn token.

    ``plane`` decides the endpoint and nothing else; it is spelled the way
    Altinn spells it in the URL so the two cannot drift apart.

    On a test platform the request carries ``test=true``, which Altinn's
    documentation requires there. Whether to send it is derived from the platform
    host rather than configured: it is not a preference, it is a fact about which
    Altinn is being talked to, and an ID-porten exchange without it is answered
    403 with nothing said about the missing parameter.
    """
    base = get_settings().altinn_platform_base_url.rstrip("/")
    url = f"{base}/authentication/api/v1/exchange/{plane}"
    production = _is_production(base)
    params = {} if production else {"test": "true"}
    async with _client() as http:
        try:
            response = await http.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except httpx.HTTPError as error:
            raise AltinnError(f"could not reach Altinn at {base}") from error

    if response.status_code != httpx.codes.OK:
        detail = ""
        if (
            not production
            and plane == "id-porten"
            and response.status_code in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}
        ):
            # The cause, confirmed 2026-08-24: an ID-porten token carrying
            # only `openid` has no Altinn scope and no audience, so there is
            # nothing for Altinn to authorize - and it says 403 without naming
            # the scope. Adding altinn:enduser to the client fixed it with no
            # other change, so this names one cause rather than a list.
            detail = (
                " - the ID-porten client needs the altinn:enduser scope,"
                " registered on it in Samarbeidsportalen and requested in"
                " IDPORTEN_SCOPES. A token carrying only 'openid' has no"
                " audience Altinn recognises"
            )
        raise AltinnAuthError(
            f"Altinn would not exchange the {plane} token ({response.status_code}){detail}"
        )

    exchanged = _token_from(response)
    return AltinnToken(value=exchanged, usable_until=_expiry_of(exchanged))


# The one production platform host. Everything else - tt02, a local stub - is a
# test environment, and Altinn's exchange wants to be told so.
_PRODUCTION_HOST = "platform.altinn.no"


def _is_production(base_url: str) -> bool:
    """Whether ``base_url`` is Altinn's production platform."""
    return httpx.URL(base_url).host == _PRODUCTION_HOST


def _token_from(response: httpx.Response) -> str:
    """The token out of the response body.

    Altinn answers with the JWT as a bare JSON string — that is, the token
    wrapped in quotes — and has at times answered with it unquoted. Both are
    accepted: stripping quotes is cheaper than a token that is silently wrong at
    both ends.
    """
    body = response.text.strip()
    if body.startswith('"') and body.endswith('"') and len(body) > 1:
        body = body[1:-1]
    if not body or body.count(".") != 2:
        raise AltinnAuthError("Altinn's exchange did not return a JWT")
    return body


def _expiry_of(token: str) -> datetime:
    """When the Altinn token expires, from its own ``exp`` claim.

    The token is not verified here — it is a credential to present, not a claim
    to trust, and Altinn verifies it on the way back in. Reading ``exp`` is only
    about not caching it past its usefulness, so an unreadable one means "do not
    cache", never "reject".
    """
    try:
        claims: dict[str, Any] = jwt.decode(token, options={"verify_signature": False})
        expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=utc_now().tzinfo)
    except (jwt.PyJWTError, KeyError, TypeError, ValueError, OSError):
        return utc_now()
    return expires_at - _EXPIRY_MARGIN


class MachineTokenSource:
    """Altinn tokens for the work a machine may do on its own.

    Fetching, creating an instance and uploading data all count; submitting does
    not. One instance per process, caching both the Maskinporten token (in the
    provider) and the exchanged Altinn token.
    """

    def __init__(self, token_provider: TokenProvider | None = None) -> None:
        self._token_provider = token_provider or get_token_provider()
        self._cached: dict[str | None, AltinnToken] = {}
        self._lock = asyncio.Lock()

    async def get_token(self, *, consumer_org: str | None = None) -> str:
        """A usable Altinn token, exchanging a fresh Maskinporten one if needed."""
        cached = self._cached.get(consumer_org)
        if cached is not None and cached.is_usable():
            return cached.value
        async with self._lock:
            cached = self._cached.get(consumer_org)
            if cached is not None and cached.is_usable():
                return cached.value
            machine_token = await self._token_provider.get_access_token(
                INSTANCE_SCOPES, consumer_org=consumer_org
            )
            token = await exchange_token(machine_token, plane="maskinporten")
            self._cached[consumer_org] = token
            return token.value


async def exchange_id_porten_token(access_token: str) -> AltinnToken:
    """Exchange a person's ID-porten access token for an Altinn token.

    Not cached: this token belongs to one person's one login, and a cache keyed
    on anything less than that is a way to act as the wrong person. The caller
    holds it for as long as the submission takes.
    """
    return await exchange_token(access_token, plane="id-porten")


_MACHINE_TOKENS: MachineTokenSource | None = None


def reset_machine_tokens() -> None:
    """Drop the cached machine token source. For tests."""
    global _MACHINE_TOKENS
    _MACHINE_TOKENS = None


def machine_tokens() -> MachineTokenSource:
    """The process-wide source of machine Altinn tokens."""
    global _MACHINE_TOKENS
    if _MACHINE_TOKENS is None:
        _MACHINE_TOKENS = MachineTokenSource()
    return _MACHINE_TOKENS
