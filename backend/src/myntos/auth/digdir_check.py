"""Check the Digdir setup, one step at a time, and say which step broke.

    uv run python -m myntos.auth.digdir_check

Every failure in this chain arrives as a status code with no explanation.
Maskinporten answers `invalid_client` whether the client id is wrong, the key was
never registered, or the `kid` does not match. Altinn answers 401 both for a
token it will not accept and for a token that was never exchanged. Skatteetaten
answers 403 for a scope that exists but has not been granted to your
organization. None of those tell you which of the possibilities you are looking
at, and all of them are configuration.

So this walks the chain in dependency order and stops at the first failure,
reporting what was actually attempted and what would fix it:

1. configuration present
2. the Maskinporten key loads, and its `kid` is the one that will be sent
3. Maskinporten's discovery document is reachable
4. Maskinporten issues a token for the Altinn instance scopes
5. Altinn exchanges that token
6. Skatteetaten's skattemelding scope (optional: fails until granted, and a
   failure here is information rather than a broken setup)
7. the ID-porten key loads
8. ID-porten accepts the client id and redirect URI when a login is started

Step 8 is as far as this goes without test data. Given one, it goes further::

    uv run python -m myntos.auth.digdir_check --person <synthetic fnr>

which logs in as that synthetic person through TestID, exchanges the token for
an Altinn one, lists the companies they may report for, and reads their
skattemelding — the steps a machine token cannot do, and the only way the
ID-porten `kid` is ever exercised. The person
must be synthetic (Tenor, +80 on the month digits) and have tax data for the
income year; `test_data/README.md` records which of ours do.

It is a diagnostic, not a test: it talks to the real services with the real
credentials, and requesting a token is free and leaves no trace worth worrying
about. Nothing is submitted, created or written anywhere — every call is a read.
Tokens are never printed; a token's presence is reported, never its value.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from dataclasses import dataclass

import httpx

from myntos.auth.idporten.client import (
    GovernmentLogin,
    IdPortenError,
    build_client,
    new_pkce_pair,
)
from myntos.auth.idporten.client import is_configured as idporten_is_configured
from myntos.auth.idporten.testid import headless_login
from myntos.auth.maskinporten.client import (
    JwtGrantTokenProvider,
    MaskinportenError,
    build_token_provider,
)
from myntos.auth.signing_keys import SigningKeyError, load_signing_key
from myntos.core.config import Settings, get_settings
from myntos.integrations.altinn.authorized_parties import fetch_authorized_parties, flatten
from myntos.integrations.altinn.tokens import (
    INSTANCE_SCOPES,
    AltinnError,
    exchange_id_porten_token,
    exchange_token,
)
from myntos.integrations.skatteetaten.client import SCOPE as SKATTEMELDING_SCOPE
from myntos.integrations.skatteetaten.client import SkatteetatenError
from myntos.integrations.skatteetaten.client import build_client as build_skattemelding_client

_OK = "  ok  "
_FAIL = " fail "
_SKIP = " skip "
_WARN = " warn "

# The income year --person reads unless told otherwise. Which years exist differs
# per test person and per entity (test_data/README.md lists ours), and asking for
# a year a taxpayer has no melding for returns nothing - which reads like a
# broken integration and is a calendar. Hence an argument, with the year most of
# our synthetic people actually have as the default.
_DEFAULT_INCOME_YEAR = 2024


@dataclass
class Report:
    """What happened, accumulated so the summary can be written at the end."""

    failed = False
    warned = False

    def ok(self, step: str, detail: str = "") -> None:
        self._line(_OK, step, detail)

    def fail(self, step: str, detail: str) -> None:
        self.failed = True
        self._line(_FAIL, step, detail)

    def skip(self, step: str, detail: str) -> None:
        self._line(_SKIP, step, detail)

    def warn(self, step: str, detail: str) -> None:
        self.warned = True
        self._line(_WARN, step, detail)

    @staticmethod
    def _line(status: str, step: str, detail: str) -> None:
        print(f"[{status}] {step}")
        if detail:
            for line in detail.splitlines():
                print(f"         {line}")


def _check_configuration(settings: Settings, report: Report) -> bool:
    """Is there enough configuration to attempt anything at all?"""
    missing = []
    if not (settings.maskinporten_client_id or "").strip():
        missing.append("MASKINPORTEN_CLIENT_ID (Detaljer tab of the client)")
    if settings.maskinporten_key_file is None:
        missing.append("MASKINPORTEN_JWK_PATH (path to the private key)")
    if missing:
        report.fail("configuration", "not set:\n" + "\n".join(f"- {name}" for name in missing))
        return False
    report.ok("configuration", f"client {settings.maskinporten_client_id}")
    return True


def _check_key(settings: Settings, report: Report) -> bool:
    """Does the key load, and which `kid` will the grant actually send?"""
    path = settings.maskinporten_key_file
    if path is None:
        # Unreachable: _check_configuration returns False first. Handled rather
        # than asserted so a reordering of the steps degrades into a message.
        report.fail("client key", "MASKINPORTEN_JWK_PATH is not set")
        return False
    try:
        key = load_signing_key(path, kid=settings.maskinporten_kid)
    except SigningKeyError as error:
        report.fail("client key", str(error))
        return False
    report.ok(
        "client key",
        f"{path.name}, {key.key.key_size}-bit\n"
        f"kid sent in the JWT header: {key.kid}\n"
        "this must be the kid the key is registered under in Samarbeidsportalen",
    )
    return True


async def _check_maskinporten(settings: Settings, report: Report) -> str | None:
    """Discovery, then a real token for the Altinn instance scopes."""
    try:
        provider = build_token_provider(settings)
    except MaskinportenError as error:
        report.fail("maskinporten client", str(error))
        return None
    if not isinstance(provider, JwtGrantTokenProvider):
        report.fail("maskinporten client", "no real token provider was built")
        return None

    try:
        token = await provider.get_access_token(INSTANCE_SCOPES)
    except MaskinportenError as error:
        report.fail(
            "maskinporten token",
            f"{error}\n"
            "invalid_client: the client id, or the kid above, is not what the\n"
            "  portal has - check the Detaljer and Nokler tabs\n"
            "invalid_grant: a clock problem, or aud is not the issuer exactly\n"
            "invalid_scope: a requested scope is not granted to this client",
        )
        return None
    report.ok("maskinporten token", f"issued for {' '.join(INSTANCE_SCOPES)}")
    return token


async def _check_altinn(token: str, settings: Settings, report: Report) -> None:
    """The exchange every Altinn 3 app call depends on."""
    try:
        exchanged = await exchange_token(token, plane="maskinporten")
    except AltinnError as error:
        report.fail(
            "altinn exchange",
            f"{error}\n"
            f"platform: {settings.altinn_platform_base_url}\n"
            "a 401 here usually means the Maskinporten token lacks the\n"
            "  altinn:instances scopes, or the platform URL is the wrong environment",
        )
        return
    report.ok(
        "altinn exchange",
        f"altinn token valid until {exchanged.usable_until:%H:%M:%S} UTC",
    )


async def _check_skattemelding_scope(settings: Settings, report: Report) -> None:
    """Optional: whether Skatteetaten has granted the skattemelding scope yet.

    Not part of a working setup. The scope can sit on the client marked "Tilgang
    mangler" for weeks while the ekstern-Jira application is processed, in test
    as well as production, so a refusal here is a status report and not a fault.
    """
    if SKATTEMELDING_SCOPE not in settings.maskinporten_scopes.split():
        report.skip(
            "skattemelding scope",
            f"{SKATTEMELDING_SCOPE} is not in MASKINPORTEN_SCOPES\n"
            "add it once Skatteetaten grants access; requesting an ungranted\n"
            "  scope fails the whole token request, so leaving it out is correct",
        )
        return
    try:
        provider = build_token_provider(settings)
        await provider.get_access_token((SKATTEMELDING_SCOPE,))
    except MaskinportenError as error:
        report.warn(
            "skattemelding scope",
            f"not usable yet: {error}\n"
            "expected until Skatteetaten grants the scope to your organization",
        )
        return
    report.ok("skattemelding scope", "granted and usable")


async def _check_idporten(settings: Settings, report: Report) -> None:
    """ID-porten cannot be checked without a person, so only report readiness.

    A login needs a browser and a Tenor test user at the other end, which is not
    something a command can stand in for. What can be checked is whether the
    configuration would let one start.
    """
    if not idporten_is_configured(settings):
        report.skip(
            "id-porten",
            "not fully configured. A submission needs it; a read does not.\n"
            "needs IDPORTEN_CLIENT_ID, IDPORTEN_REDIRECT_URI, and either\n"
            "  IDPORTEN_JWK_PATH (+ IDPORTEN_KID) or IDPORTEN_CLIENT_SECRET",
        )
        return
    path = settings.idporten_key_file
    if path is not None:
        try:
            key = load_signing_key(path, kid=settings.idporten_kid)
        except SigningKeyError as error:
            report.fail("id-porten", str(error))
            return
        report.ok("id-porten key", f"{path.name}\nkid: {key.kid}")
    else:
        report.ok("id-porten key", "authenticating with a client secret")
    await _check_idporten_authorization(settings, report)


async def _check_idporten_authorization(settings: Settings, report: Report) -> None:
    """Ask ID-porten whether it recognises the client and the redirect URI.

    A login cannot be automated — it wants a browser and a Tenor test user at the
    other end. But the *start* of one can be: the authorization endpoint has to
    decide whether this ``client_id`` exists and whether the ``redirect_uri``
    matches what was registered, and it decides that before any person is
    involved.

    The distinction in the answer is the useful part, and it comes from OAuth's
    own rules. An unknown client or an unregistered redirect URI cannot be
    reported *to* that URI — the server has no reason to trust it — so ID-porten
    answers with an error of its own. Anything else (a bad scope, say) is
    reported by redirecting to the URI with ``error=`` on it. So a redirect back
    to our own URI proves the client and the URI are both right, and an error
    page proves one of them is not.

    The ``kid`` is not exercised here: it is only checked when a code is
    exchanged for a token, which needs a real login.
    """
    try:
        client = build_client(settings)
    except IdPortenError as error:
        report.fail("id-porten authorization", str(error))
        return

    pkce = new_pkce_pair()
    try:
        url = await client.authorization_url(
            state="digdir-check", nonce="digdir-check", code_challenge=pkce.challenge
        )
    except IdPortenError as error:
        report.fail("id-porten authorization", str(error))
        return

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as http:
        try:
            # Not followed: the answer we want is this one response, and following
            # it would start walking through a login UI.
            response = await http.get(url, follow_redirects=False)
        except httpx.HTTPError as error:
            report.warn("id-porten authorization", f"could not reach ID-porten: {error}")
            return

    location = response.headers.get("location", "")
    redirect_uri = client.redirect_uri
    if location.startswith(redirect_uri):
        # Reached our own URI, so both it and the client id are registered. An
        # error carried on it is about the request's contents - the scopes.
        if "error=" in location:
            report.warn(
                "id-porten authorization",
                "the client and redirect URI are registered, but the request was\n"
                f"  refused: {location.split('?', 1)[-1][:160]}\n"
                "a scope in IDPORTEN_SCOPES is probably not granted yet",
            )
            return
        report.ok("id-porten authorization", "client and redirect URI accepted")
        return

    if response.status_code in {httpx.codes.FOUND, httpx.codes.SEE_OTHER, httpx.codes.OK}:
        report.ok(
            "id-porten authorization",
            "client and redirect URI accepted (ID-porten offered a login)",
        )
        return

    report.fail(
        "id-porten authorization",
        f"ID-porten refused to start the login ({response.status_code})\n"
        f"client_id: {settings.idporten_client_id}\n"
        f"redirect_uri: {redirect_uri}\n"
        "one of those two is not what the portal has - the redirect URI must\n"
        "  match byte for byte, and ID-porten wants the loopback literal\n"
        "  127.0.0.1 rather than the name localhost\n"
        f"{response.text[:200]}",
    )


async def _check_person_chain(
    settings: Settings, report: Report, pid: str, year: int, org: str | None = None
) -> None:
    """The person-present half, end to end, for one synthetic test person.

    Only reachable with ``--person``, and only in the test environment: it logs
    in as a synthetic person through TestID, exchanges that for an Altinn token,
    and reads their skattemelding. Those three steps are the ones a machine token
    cannot do, and until they run once nothing has proven that the ID-porten
    ``kid`` matches, that Altinn will accept a person's token, or that
    Skatteetaten will answer for a taxpayer.
    """
    try:
        client = build_client(settings)
        login = await headless_login(client, pid=pid, acr=settings.idporten_acr_values)
    except IdPortenError as error:
        report.fail("testid login", str(error))
        return
    report.ok("testid login", f"pid={login.pid} acr={login.acr}")

    try:
        altinn = await exchange_id_porten_token(login.access_token)
    except AltinnError as error:
        report.fail(
            "altinn exchange (person)",
            f"{error}\n"
            "the scope is the cause, not the person: altinn:enduser has to be\n"
            "  registered on the ID-porten client and requested in IDPORTEN_SCOPES.\n"
            "  A browser session at tt02.altinn.no is not a prerequisite - that\n"
            "  was tried and changed nothing",
        )
        # Deliberately not a return. Only the Altinn *submission* path needs this
        # token; Skatteetaten's REST API takes the ID-porten token directly, so
        # the fetch below is worth attempting either way - and it is the step
        # that answers whether this person may represent the taxpayer at all.
    else:
        report.ok("altinn exchange (person)", f"usable until {altinn.usable_until:%H:%M:%S} UTC")
        await _check_authorized_parties(altinn.value, report)

    await _check_fetch(settings, report, login, pid, year, label="skattemelding fetch")
    if org is not None:
        await _check_fetch(settings, report, login, org, year, label="skattemelding fetch (org)")


async def _check_authorized_parties(altinn_token: str, report: Report) -> None:
    """Which companies this person may report for, as Altinn sees it.

    A read, and the cheapest possible proof that
    ``altinn:accessmanagement/authorizedparties`` is actually granted: the scope
    is inert until Digdir grants it, and the failure without it is a 403 that
    names nothing. The count is reported rather than the list — a synthetic
    person can hold rights for a good many companies, and the interesting answer
    is whether the call is answered at all.

    Not a ``return`` on failure, and not fatal to the run: the list is a
    convenience in the setup form, and everything after this step works without
    it.
    """
    try:
        parties = await fetch_authorized_parties(altinn_token)
    except AltinnError as error:
        report.warn("altinn authorized parties", str(error))
        return
    reportable = [party for party in flatten(parties) if party.is_usable and party.is_organization]
    if not reportable:
        # Answered, so the scope is there. An empty list is a fact about the
        # person, not a fault: a synthetic person with no Altinn roles has none.
        report.ok("altinn authorized parties", "answered, but this person holds no companies")
        return
    first = reportable[0]
    report.ok(
        "altinn authorized parties",
        f"{len(reportable)} companies, e.g. {first.name} ({first.orgnr})",
    )


async def _check_fetch(
    settings: Settings,
    report: Report,
    login: GovernmentLogin,
    identifier: str,
    year: int,
    *,
    label: str,
) -> None:
    """Read one taxpayer's skattemelding with the logged-in person's token.

    The same call for a person and for an organisation, because it is the same
    call - only the identifier differs, and which of the two succeeds is the
    interesting part. A machine token gets SME-EKSTERN-005 here ("cannot check
    whether the logged-in user may represent the taxpayer"); a person's token
    carrying digdir:skattemelding answers it, for every organisation they hold
    Altinn rights for.
    """
    try:
        skattemelding = build_skattemelding_client(settings)
        content = await skattemelding.fetch(
            inntektsaar=year,
            identifier=identifier,
            variant="utkast",
            id_porten_token=login.access_token,
        )
    except SkatteetatenError as error:
        report.warn(label, f"{error}\n{_reading(str(error), identifier, year)}")
        return
    report.ok(label, f"utkast {year} for {identifier}: {len(content)} bytes of XML")


# A 5xx carries no meaning about the request, so it is matched before the
# table below rather than being given a phrase of its own.
_SERVER_ERROR = re.compile(r"failed \(5\d\d\)")

# What Skatteetaten's own words mean, for the three answers seen so far. Worth
# translating because two of them are 403s that say nothing about
# authorization: the auth chain can be entirely correct and still land here.
_MEANINGS = (
    (
        "SME-EKSTERN-005",
        "the client is missing the digdir:skattemelding scope - it carries the\n"
        "  Altinn rights to represent a taxpayer, and without it Skatteetaten\n"
        "  cannot check those rights at all",
    ),
    (
        "ikke tilgjengelig",
        "authorized, and refused on data grounds: no melding this API will serve.\n"
        "  Expected for a person's own melding - none of our synthetic people\n"
        "  have had one served, for any year. The org path works: pass --org\n"
        "  with a company this person holds Altinn rights for",
    ),
    (
        "Skatteplikt ikke registrert",
        "authorized, and this identifier is not a taxpayer for that year. An ENK\n"
        "  files on its innehaver's fodselsnummer, not on the orgnr",
    ),
)


def _reading(error: str, pid: str, year: int) -> str:
    """A line saying what Skatteetaten's answer actually means."""
    if _SERVER_ERROR.search(error):
        return (
            "Skatteetaten's own fault, not the setup: a 5xx means the test API\n"
            "  is degraded. Seen as both 504s and HTML error pages from the\n"
            "  gateway. Wait and run this again before changing anything"
        )
    for phrase, meaning in _MEANINGS:
        if phrase in error:
            return meaning
    return (
        f"if this is 'not found', check that {pid} has a melding for {year} -\n"
        "  test_data/README.md lists which years ours have"
    )


async def run(
    pid: str | None = None,
    year: int = _DEFAULT_INCOME_YEAR,
    org: str | None = None,
) -> int:
    settings = get_settings()
    report = Report()
    print(f"Maskinporten: {settings.maskinporten_well_known_url}")
    print(f"Altinn:       {settings.altinn_platform_base_url}")
    print()

    if _check_configuration(settings, report) and _check_key(settings, report):
        token = await _check_maskinporten(settings, report)
        if token is not None:
            await _check_altinn(token, settings, report)
            await _check_skattemelding_scope(settings, report)
    await _check_idporten(settings, report)
    if pid is not None:
        await _check_person_chain(settings, report, pid, year, org)

    print()
    if report.failed:
        print("Not working yet - fix the first failure above and run this again.")
        return 1
    if pid is None:
        print("The machine half of the chain works: token, exchange, instance APIs.")
        print("Add --person <synthetic fnr> to test the person-present half too.")
        return 0
    if report.warned:
        print("Every credential works. Something above answered, but not with what")
        print("was asked for - read the warnings before treating this as green.")
        return 0
    print("The whole chain works, for a machine and for a person.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m myntos.auth.digdir_check",
        description="Check the Digdir setup against the real services.",
    )
    parser.add_argument(
        "--person",
        metavar="FNR",
        help="a synthetic (Tenor) fodselsnummer to run a TestID login for, "
        "taking the check through the person-present half of the chain",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=_DEFAULT_INCOME_YEAR,
        help=f"income year to read the skattemelding for (default {_DEFAULT_INCOME_YEAR})",
    )
    parser.add_argument(
        "--org",
        metavar="ORGNR",
        help="an organisasjonsnummer to read the skattemelding for, as the "
        "--person given above. Needs digdir:skattemelding on the ID-porten "
        "client and that person to hold Altinn rights for the company - being "
        "daglig leder in Enhetsregisteret is enough",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.person, args.year, args.org))


if __name__ == "__main__":  # pragma: no cover - entry point
    sys.exit(main())
