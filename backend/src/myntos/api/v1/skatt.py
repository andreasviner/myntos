"""The ID-porten login a submission to Skatteetaten requires.

Not a sign-in. The caller is already logged into Myntos; this is the *second*
authentication Skatteetaten insists on before anything may be filed in a
person's name, and it produces a short-lived authorization rather than a
session. A Myntos session is never evidence of anything to Skatteetaten, which
is why these are two flows and two tables.

Three endpoints, and the shape follows the federated sign-in in
:mod:`myntos.api.v1.auth` deliberately - same PKCE, same single-use state row,
same open-redirect guard - so there is one pattern to understand rather than
two:

``GET /skatt/idporten/start``
    Redirects the browser to ID-porten. Requires a Myntos login: the person who
    starts the flow is stamped on the attempt and is the only one who can finish
    it.

``GET /skatt/idporten/callback``
    ID-porten sends the browser here. Verifies the attempt, exchanges the code,
    checks the assurance level actually reached, seals the token and sends the
    browser back into the frontend.

``GET /skatt/status``
    Whether a usable government login exists, and who it belongs to. Never the
    token: nothing outside the server has any use for it.

And one endpoint that spends the login rather than creating it:

``GET /skatt/altinn/authorized-parties``
    The companies the logged-in person may report for, straight from Altinn.
    The first thing the government login is good for that is not a submission,
    and the reason it is worth doing before anything can be filed: it turns
    "type your clients' organisasjonsnumre" into a list to pick from.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select

from myntos.api.deps import CurrentUser, DbSession
from myntos.auth.idporten.client import (
    IdPortenError,
    get_client,
    is_configured,
    new_pkce_pair,
)
from myntos.auth.idporten.vault import TokenVaultError, open_sealed, seal
from myntos.auth.idporten.vault import is_configured as vault_is_configured
from myntos.auth.tokens import generate_token
from myntos.core.clock import as_utc, utc_now
from myntos.core.config import get_settings
from myntos.domain.identity.models import GovernmentLoginAttempt, User
from myntos.domain.ledger import service as ledger_service
from myntos.integrations.altinn.authorized_parties import (
    AuthorizedParty,
    fetch_authorized_parties,
    flatten,
)
from myntos.integrations.altinn.tokens import AltinnError, exchange_id_porten_token

router = APIRouter(prefix="/skatt", tags=["skatt"])

# The round trip through ID-porten happens in one sitting. Shorter than the
# sign-in flow's window: this one ends at a BankID prompt the person is already
# holding their phone for, and a narrower window is a smaller replay target.
ATTEMPT_LIFETIME = timedelta(minutes=10)


class GovernmentLoginStatus(BaseModel):
    """What the frontend needs to decide whether to offer a submission."""

    # False when ID-porten or the token key is unconfigured. The UI must not
    # offer a login it cannot complete.
    available: bool
    # Whether a usable authorization exists right now.
    logged_in: bool
    # The person who authorized, when there is one. Shown so a user can see
    # *who* is about to file - a regnskapsforer may hold several logins.
    pid: str | None = None
    acr: str | None = None
    expires_at: str | None = None


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="innlogging med ID-porten er ikke satt opp",
    )


def _safe_redirect_path(candidate: str | None) -> str | None:
    """Keep only site-relative paths, for the reason auth.py spells out."""
    if candidate is None:
        return None
    if not candidate.startswith("/") or candidate.startswith(("//", "/\\")):
        return None
    return candidate[:512]


def _frontend_url(path: str) -> str:
    return f"{get_settings().frontend_base_url.rstrip('/')}{path}"


def _failed(marker: str) -> RedirectResponse:
    """Back to the frontend with our own error code, never ID-porten's text."""
    return RedirectResponse(
        _frontend_url(f"/skatt/?error={marker}"), status_code=status.HTTP_303_SEE_OTHER
    )


async def _active_attempt(db: DbSession, user: User) -> GovernmentLoginAttempt | None:
    """The caller's usable government login, if they have one.

    "Usable" is four conditions, and every one of them has to hold: a token was
    stored, it has not been spent, ID-porten's own expiry has not passed, and it
    belongs to this user. Anything less is not an authorization.
    """
    now = utc_now()
    rows = await db.scalars(
        select(GovernmentLoginAttempt)
        .where(
            GovernmentLoginAttempt.user_id == user.id,
            GovernmentLoginAttempt.sealed_token.is_not(None),
            GovernmentLoginAttempt.used_at.is_(None),
        )
        .order_by(GovernmentLoginAttempt.id.desc())
        .limit(5)
    )
    for row in rows:
        if row.token_expires_at is not None and as_utc(row.token_expires_at) > now:
            return row
    return None


@router.get("/status")
async def government_login_status(user: CurrentUser, db: DbSession) -> GovernmentLoginStatus:
    """Whether this user may submit something right now."""
    available = is_configured() and vault_is_configured()
    if not available:
        return GovernmentLoginStatus(available=False, logged_in=False)
    attempt = await _active_attempt(db, user)
    if attempt is None:
        return GovernmentLoginStatus(available=True, logged_in=False)
    return GovernmentLoginStatus(
        available=True,
        logged_in=True,
        pid=attempt.pid,
        acr=attempt.acr,
        expires_at=(
            as_utc(attempt.token_expires_at).isoformat()
            if attempt.token_expires_at is not None
            else None
        ),
    )


class AuthorizedPartyResponse(BaseModel):
    """One company the logged-in person may report for.

    Deliberately narrower than what Altinn returns. Three things are dropped on
    the way through:

    - **``personId``.** Altinn lists person parties too, and identifies them by
      fødselsnummer. A picker of companies has no use for one, and a
      fødselsnummer that reaches a browser is a fødselsnummer in a screenshot,
      a bug report and a proxy log.
    - **Parties with no organisasjonsnummer.** Myntos keeps books for entities
      that have one. Everything else in the list is a person, and a person's
      skattemelding is not a set of books.
    - **Parties the person cannot actually act for** — deleted ones, and the
      hierarchy placeholders Altinn includes so a tree can be drawn. Offering
      those would be offering a 403 dressed up as a choice.

    ``roles`` is passed on as Altinn's own codes (``DAGL``, ``REGN``,
    ``LEDE``…). Untranslated on purpose: they are what Altinn shows the same
    person in its own interface, and a helpful paraphrase here would be a second
    vocabulary for one fact.
    """

    orgnr: str
    name: str
    # Altinn's own key for the party. The instance a filing eventually lands in
    # is created under it, so it travels with the choice rather than being
    # looked up again later.
    party_id: int
    # "AS", "ENK", "BEDR"… the same code Enhetsregisteret uses, when Altinn
    # knows it. Shown next to the name because a main unit and its subunit
    # frequently share one.
    unit_type: str | None
    # The main unit this is a subunit of, when it is one. A regnskapsfører
    # engaged for one department of a group needs to see which.
    parent_orgnr: str | None
    roles: list[str]
    # True when another organization has already confirmed ownership of this
    # orgnr — the "selskapet har allerede et regnskap registrert" case. Said
    # here, next to the choice, rather than left for the confirmation to refuse
    # afterwards. Which organization holds the claim is never disclosed: the
    # caller is not a member of it, and the way in is to ask them, not us.
    claimed: bool = False


def _authorized_party_response(
    party: AuthorizedParty, *, parent_orgnr: str | None, claimed: bool
) -> AuthorizedPartyResponse:
    return AuthorizedPartyResponse(
        orgnr=party.orgnr or "",
        name=party.name,
        party_id=party.party_id,
        unit_type=party.unit_type,
        parent_orgnr=parent_orgnr,
        roles=list(party.authorized_roles),
        claimed=claimed,
    )


def _reportable(
    parties: list[AuthorizedParty], *, claimed: set[str]
) -> list[AuthorizedPartyResponse]:
    """The parties worth offering, flattened, each knowing its main unit.

    The parent is tracked here rather than in the integration because it is a
    presentation fact: Altinn's tree already says it by nesting, and this is the
    one place that turns the tree into a list and therefore has to say it in
    words instead.
    """
    offered: list[AuthorizedPartyResponse] = []
    for main_unit in parties:
        for party in flatten([main_unit]):
            if not party.is_usable or not party.is_organization:
                continue
            parent = main_unit.orgnr if party is not main_unit else None
            offered.append(
                _authorized_party_response(
                    party, parent_orgnr=parent, claimed=(party.orgnr or "") in claimed
                )
            )
    return offered


async def authorized_parties_for(db: DbSession, user: User) -> list[AuthorizedParty]:
    """Altinn's answer to "which parties may this person act for", for ``user``.

    The mechanics of spending a government login live here rather than being
    repeated by every caller: the ID-porten token is unsealed, exchanged for an
    Altinn token, and the list is fetched. ``used_at`` is left alone — reading
    who you may act for is not acting, and a person who looked at the list
    should not need a second BankID before confirming a company from it.

    Raises :class:`HTTPException` rather than a domain error because every
    failure here is about the *login*, which is an API-layer fact: unconfigured
    deployment (503), no usable government login (409), Altinn refusing (502).
    """
    if not is_configured() or not vault_is_configured():
        raise _unavailable()
    attempt = await _active_attempt(db, user)
    if attempt is None or attempt.sealed_token is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="logg inn med ID-porten for å hente selskapene fra Altinn",
        )

    try:
        government_token = open_sealed(attempt.sealed_token)
    except TokenVaultError:
        # The key changed under a stored row. Treated as "log in again", which
        # is what actually fixes it, rather than as a 500 nobody can act on.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="innloggingen i ID-porten må gjøres på nytt",
        ) from None

    try:
        altinn_token = await exchange_id_porten_token(government_token)
        return await fetch_authorized_parties(altinn_token.value)
    except AltinnError as error:
        # Altinn's own words, not ours: every failure here is a scope or a
        # registration, and the message the integration writes names which.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error


@router.get("/altinn/authorized-parties")
async def list_authorized_parties(
    user: CurrentUser, db: DbSession
) -> list[AuthorizedPartyResponse]:
    """The companies this person may report for, according to Altinn.

    Requires the government login, and **does not spend it**: ``used_at`` stays
    empty. Reading who you are allowed to act for is not acting, and a person
    who looked at the list should not have to log in again before filing.

    409 rather than 401 when there is no government login: the caller *is*
    authenticated to Myntos, and a 401 would send the frontend to the sign-in
    page it just came from. The status the frontend actually branches on is
    ``GET /skatt/status``; this code is the answer to a race, not the normal
    path.
    """
    parties = await authorized_parties_for(db, user)
    offered = _reportable(parties, claimed=set())
    claimed = await ledger_service.claimed_elsewhere(
        db, [party.orgnr for party in offered], user_id=user.id
    )
    for party in offered:
        party.claimed = party.orgnr in claimed
    return offered


@router.get("/idporten/start")
async def idporten_start(
    user: CurrentUser,
    db: DbSession,
    redirect_to: str | None = None,
) -> RedirectResponse:
    """Begin the ID-porten login that authorizes a submission.

    503 rather than a redirect when the feature is not configured: sending a
    person to BankID and failing afterwards wastes a real authentication.
    """
    if not is_configured() or not vault_is_configured():
        raise _unavailable()

    pkce = new_pkce_pair()
    now = utc_now()
    attempt = GovernmentLoginAttempt(
        user_id=user.id,
        state=generate_token(),
        nonce=generate_token(),
        code_verifier=pkce.verifier,
        redirect_to=_safe_redirect_path(redirect_to),
        created_at=now,
        expires_at=now + ATTEMPT_LIFETIME,
    )
    db.add(attempt)
    await db.flush()

    url = await get_client().authorization_url(
        state=attempt.state, nonce=attempt.nonce, code_challenge=pkce.challenge
    )
    # 303: follow with GET however this was reached.
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/idporten/callback")
async def idporten_callback(
    request: Request,
    db: DbSession,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Finish the login and store the authorization.

    No ``CurrentUser`` dependency: ID-porten redirects a browser here, and the
    owner comes off the attempt row rather than off the session, so a user whose
    Myntos cookie has lapsed mid-flow still lands somewhere sensible. The
    attempt's ``user_id`` is what binds the token to a person.
    """
    del request  # Present for symmetry with auth.py, which records the caller.
    if error is not None or code is None or state is None:
        return _failed("innlogging_avbrutt")

    attempt = await db.scalar(
        select(GovernmentLoginAttempt).where(GovernmentLoginAttempt.state == state)
    )
    now = utc_now()
    if attempt is None or attempt.consumed_at is not None or as_utc(attempt.expires_at) <= now:
        return _failed("innlogging_utlopt")

    # Stamped before the exchange: a replayed callback must fail even if the
    # first exchange is still in flight.
    attempt.consumed_at = now
    await db.flush()

    try:
        login = await get_client().exchange_code(
            code=code, code_verifier=attempt.code_verifier, nonce=attempt.nonce
        )
    except IdPortenError:
        return _failed("innlogging_feilet")

    # The assurance level actually reached, not the one requested. Skatteetaten
    # requires high for a submission, and a token that did not reach it must not
    # be stored as though it had - the check belongs here, once, rather than at
    # every call site that later trusts the row.
    required = get_settings().idporten_acr_values.strip()
    if required and login.acr != required:
        return _failed("for_lav_sikkerhet")

    attempt.sealed_token = seal(login.access_token)
    attempt.token_expires_at = login.expires_at
    attempt.pid = login.pid
    attempt.acr = login.acr
    await db.flush()

    return RedirectResponse(
        _frontend_url(attempt.redirect_to or "/skatt/"), status_code=status.HTTP_303_SEE_OTHER
    )
