"""TestID: an ID-porten login without a person, in the test environment only.

ID-porten's test environment can authenticate a **synthetic** user with no
browser and no interaction. One extra parameter on the authorization request::

    login_hint=testid:<synthetic pid>_idporten-loa-high

and it authenticates immediately, redirecting straight back with a code. The
tokens that come out are ordinary ones — refreshable, introspectable — marked
``amr=TestID`` so a token from this path is recognisable as one.

Digdir built this for exactly the use this module puts it to: making an
API-level submission chain testable end to end. Without it, every step past
"the credentials load" would wait on a human with a BankID, which means in
practice that it would go untested — and the chain past that point is the part
with legal consequences.

**Test only, and enforced rather than trusted.** :func:`headless_login` refuses
to run unless the configured ID-porten issuer is a test host. Production has no
equivalent endpoint, so the guard costs nothing there; what it prevents is this
code being reached, by a stray configuration or a copied line, in a deployment
where a submission is real. A synthetic personnummer is likewise required: they
carry +80 on the month digits, which is checked here, so a real number cannot be
passed in even by accident.

Test data comes from Tenor Testdatasøk. A person existing in the synthetic
Folkeregister is not enough to fetch a skattemelding — they also need synthetic
tax data for the income year (Tenor's ``skattemelding`` relation), and
``test_data/README.md`` records which of ours have it.
"""

from __future__ import annotations

import secrets
from urllib.parse import parse_qs, urlparse

import httpx

from myntos.auth.idporten.client import (
    GovernmentLogin,
    IdPortenClient,
    IdPortenError,
    new_pkce_pair,
)

# Hosts where an automated login is a legitimate thing to do. Matched against
# the issuer from the discovery document rather than against configuration, so
# pointing the client at production cannot be talked out of by an env var.
_TEST_ISSUERS = ("https://test.idporten.no", "https://idporten.dev")

_HTTP_TIMEOUT = httpx.Timeout(30.0)


class NotATestEnvironmentError(IdPortenError):
    """A headless login was attempted somewhere it must never be attempted."""


def is_synthetic(pid: str) -> bool:
    """Whether ``pid`` is a synthetic (Tenor) personnummer.

    Synthetic numbers add 80 to the month digits, so a real number can be told
    from a test one by arithmetic rather than by a list. A D-number adds 40 to
    the day digits as well, which does not affect this check.
    """
    digits = pid.strip()
    if len(digits) != 11 or not digits.isdigit():
        return False
    return 81 <= int(digits[2:4]) <= 92


async def headless_login(
    client: IdPortenClient,
    *,
    pid: str,
    acr: str = "idporten-loa-high",
) -> GovernmentLogin:
    """Log in as a synthetic test person, and return the resulting tokens.

    The flow is the real one — the same authorization request, the same PKCE,
    the same ``private_key_jwt`` token call, the same ID-token verification —
    with the single difference that ID-porten authenticates from the
    ``login_hint`` instead of asking somebody. That is what makes it worth
    using: what it exercises is the production code path, including the ``kid``,
    which nothing short of a real login otherwise touches.
    """
    if not is_synthetic(pid):
        raise NotATestEnvironmentError(
            f"'{pid}' is not a synthetic personnummer. Test logins use Tenor "
            "test data, whose month digits carry +80 - a real number must never "
            "be sent to a test environment."
        )

    issuer = await client.issuer()
    if not any(issuer.rstrip("/").startswith(host) for host in _TEST_ISSUERS):
        raise NotATestEnvironmentError(
            f"the configured ID-porten issuer is {issuer}, which is not a test "
            "environment. A headless login is a test-only facility and must "
            "never be attempted against a real one."
        )

    pkce = new_pkce_pair()
    state = secrets.token_urlsafe(16)
    nonce = secrets.token_urlsafe(16)
    url = await client.authorization_url(
        state=state,
        nonce=nonce,
        code_challenge=pkce.challenge,
        login_hint=f"testid:{pid}_{acr}",
    )

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
        try:
            # Deliberately not followed: the redirect carries the code, and its
            # target is a loopback address with nothing listening on it.
            response = await http.get(url, follow_redirects=False)
        except httpx.HTTPError as error:
            raise IdPortenError("could not reach ID-porten") from error

    code = _code_from(response, redirect_uri=client.redirect_uri, state=state)
    return await client.exchange_code(code=code, code_verifier=pkce.verifier, nonce=nonce)


def _code_from(response: httpx.Response, *, redirect_uri: str, state: str) -> str:
    """The authorization code out of ID-porten's redirect.

    A login page instead of a redirect means the ``login_hint`` was not accepted
    — a non-synthetic pid, an assurance level the client may not ask for, or a
    test facility that is switched off — and saying so beats leaving the caller
    to wonder why an automated login is showing them HTML.
    """
    location = response.headers.get("location", "")
    if not location.startswith(redirect_uri):
        raise IdPortenError(
            "ID-porten did not complete the headless login. It answered "
            f"{response.status_code}"
            + (f" towards {location.split('?', 1)[0]}" if location else " with no redirect")
            + " - check that the pid is synthetic and that the client may "
            "request this acr_values level."
        )

    query = parse_qs(urlparse(location).query)
    error = query.get("error", [None])[0]
    if error is not None:
        description = query.get("error_description", [""])[0]
        raise IdPortenError(f"ID-porten refused the login: {error} {description}".strip())

    # The state check is the same one the real callback route has to make: it
    # ties this response to the request that started it.
    if query.get("state", [None])[0] != state:
        raise IdPortenError("the state in ID-porten's redirect is not the one sent")

    code = query.get("code", [None])[0]
    if not isinstance(code, str) or not code:
        raise IdPortenError("ID-porten's redirect carried no authorization code")
    return code
