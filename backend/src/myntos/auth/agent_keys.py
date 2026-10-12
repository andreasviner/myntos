"""Keys for agents and integrations: issue, exchange, refresh, resolve, stop.

The rules are docs/agents-and-api.md 3 to 6 and 10; this module is the
mechanism, and the shape of the two tables it works on
(:class:`~myntos.domain.identity.models.AgentKey`,
:class:`~myntos.domain.identity.models.AgentToken`) carries most of the
reasoning.

The one idea to hold on to: **every key a person sees will end up in a chat.**
Most people will paste it into a conversation with their agent, and that
conversation is stored by the model provider and may be shared. So the value
on the Tilgang page is a *connection key* that does one thing, once: it is
exchanged (:func:`exchange_connection_key`) for a 15-minute access token and a
refresh token, and from then on the copy in the chat opens nothing. Refresh
tokens rotate (:func:`refresh`), and a spent one coming back stops the key.
The person does not have to understand any of it; ``llms.txt`` teaches the
agent to do it.

Every credential carries a prefix (``myntos_ck_``, ``myntos_rt_``,
``myntos_at_``) so an agent can tell what it was handed, a request with the
wrong one can be answered with the fix rather than "unknown", and a secret
scanner can recognise a leaked one. Each is 256 random bits stored as a
SHA-256 digest, like a session (:mod:`myntos.auth.tokens` says why that is
enough).
"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import and_, delete, event, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, UOWTransaction, selectinload

from myntos.auth.tokens import generate_token, hash_token
from myntos.core.clock import as_utc, utc_now
from myntos.core.config import get_settings
from myntos.domain.identity.access import Permission, has_permission
from myntos.domain.identity.errors import (
    AgentAccessDisabledError,
    AgentKeyNotFoundError,
    AgentKeyStoppedError,
    BankidAccountError,
    ConnectionKeyExpiredError,
    ConnectionKeySpentError,
    InvalidAgentScopeError,
    InvalidAgentTokenError,
    InvalidGrantError,
    NotAnAccessTokenError,
    RefreshTokenReusedError,
)
from myntos.domain.identity.models import (
    AgentKey,
    AgentKeyScope,
    AgentKeyStop,
    AgentToken,
    AgentTokenKind,
    CompanyMembership,
    OrganizationRole,
    User,
)
from myntos.domain.identity.service import is_bankid_account
from myntos.domain.ledger.models import Company, Transaction

CONNECTION_KEY_PREFIX = "myntos_ck_"
REFRESH_TOKEN_PREFIX = "myntos_rt_"  # noqa: S105 - a prefix, not a secret
ACCESS_TOKEN_PREFIX = "myntos_at_"  # noqa: S105 - a prefix, not a secret
AGENT_CREDENTIAL_PREFIXES = (CONNECTION_KEY_PREFIX, REFRESH_TOKEN_PREFIX, ACCESS_TOKEN_PREFIX)

# What each scope asks of the person issuing it. A key narrows its person and
# never widens them, so a viewer can make a key that reads and nothing else.
_SCOPE_NEEDS: Mapping[AgentKeyScope, Permission] = {
    AgentKeyScope.READ: Permission.LEDGER_READ,
    AgentKeyScope.WRITE: Permission.LEDGER_WRITE,
    AgentKeyScope.POST: Permission.LEDGER_WRITE,
}

# A refresh token presented again this soon after it was spent is answered with
# a fresh pair rather than treated as stolen. An agent running two tool calls
# at once will see both access tokens expire together and refresh twice, and
# killing the key for that would punish the commonest agent behaviour there
# is. A token lifted from a transcript is replayed minutes or days later, not
# within seconds of its own rotation, so the window costs the detection
# nothing it was for.
REFRESH_REUSE_GRACE = timedelta(seconds=30)

# Where the key a request was made with is kept for the length of the request,
# so the voucher stamp below can find it without every caller passing it down.
AGENT_KEY_INFO = "myntos.agent_key_id"


def is_agent_credential(token: str) -> bool:
    return token.startswith(AGENT_CREDENTIAL_PREFIXES)


def _mint(prefix: str) -> str:
    return prefix + generate_token()


def _scopes_text(scopes: Iterable[AgentKeyScope]) -> str:
    return " ".join(sorted(scope.value for scope in scopes))


# --- Issuing ----------------------------------------------------------------------


@dataclass(frozen=True)
class IssuedKey:
    """A new key and the connection key to show its person, once.

    The connection key exists only here and in the response; the row holds its
    digest.
    """

    key: AgentKey
    connection_key: str


def parse_scopes(words: Iterable[str]) -> frozenset[AgentKeyScope]:
    """The scopes asked for, with ``read`` added: every key can read.

    A key that could write but not read would be a key whose agent cannot see
    what it just did, and offering that as a choice would be offering a
    mistake.
    """
    found: set[AgentKeyScope] = {AgentKeyScope.READ}
    for word in words:
        try:
            found.add(AgentKeyScope(word))
        except ValueError:
            raise InvalidAgentScopeError(f"ukjent tilgang for en nøkkel: «{word}»") from None
    return frozenset(found)


def parse_approval_scopes(
    words: Iterable[str], *, scopes: frozenset[AgentKeyScope]
) -> frozenset[AgentKeyScope]:
    """What the key may only ask to do, checked against what it may do on its own.

    Three answers per scope, in one order: on its own, only with approval, not
    at all. So a scope is never both, ``read`` is never asked for (every key
    reads on its own), and ``post`` never reaches further than ``write``: a key
    that could issue an invoice on its own but had to ask to make the draft
    would be a key whose person meant something else.
    """
    found: set[AgentKeyScope] = set()
    for word in words:
        try:
            found.add(AgentKeyScope(word))
        except ValueError:
            raise InvalidAgentScopeError(f"ukjent tilgang for en nøkkel: «{word}»") from None
    if AgentKeyScope.READ in found:
        raise InvalidAgentScopeError("å lese trenger ingen godkjenning; det gjør hver nøkkel selv")
    if found & scopes:
        raise InvalidAgentScopeError(
            "en tilgang kan ikke både brukes fritt og kreve godkjenning på samme nøkkel"
        )

    def reach(scope: AgentKeyScope) -> int:
        return 2 if scope in scopes else 1 if scope in found else 0

    if reach(AgentKeyScope.POST) > reach(AgentKeyScope.WRITE):
        raise InvalidAgentScopeError(
            "å bokføre kan ikke være friere enn å forberede: velg minst like mye for "
            "«forberede» som for «bokføre»"
        )
    return frozenset(found)


def require_role_allows(role: OrganizationRole, scopes: Iterable[AgentKeyScope]) -> None:
    for scope in scopes:
        if not has_permission(role, _SCOPE_NEEDS[scope]):
            raise InvalidAgentScopeError(
                "rollen din i selskapet kan ikke gi en nøkkel denne tilgangen. En nøkkel kan "
                "aldri gjøre mer enn den som lager den."
            )


async def issue_key(
    db: AsyncSession,
    *,
    company: Company,
    membership: CompanyMembership,
    user: User,
    name: str,
    scopes: frozenset[AgentKeyScope],
    approval_scopes: frozenset[AgentKeyScope] = frozenset(),
) -> IssuedKey:
    """Make a key for ``company``, working with ``user``'s permissions.

    Refused while the company has keys switched off, and for an account that
    signs in with BankID. The second is not settled law but an open question
    (docs/integrations/bankid.md, the third question for Stø): a key issued
    from a session BankID opened can go on working for a year without BankID
    being used again, which reads close to the "derived identity" BankID's
    terms forbid. Until Stø answers, a BankID account makes no keys.
    """
    if not company.agent_access_enabled:
        raise AgentAccessDisabledError(
            "nøkler for KI-agenter og API er ikke slått på for dette selskapet. En eier eller "
            "administrator slår dem på under Tilgang."
        )
    if await is_bankid_account(db, user.id):
        raise BankidAccountError(
            "en konto som logger inn med BankID kan ikke lage nøkler ennå. Vi har spurt BankID "
            "om vilkårene deres tillater det, og slår det på hvis svaret er ja."
        )
    # Asking needs the same role as doing: approving is the person doing it,
    # so a key may not ask for what its person could not do themselves.
    require_role_allows(membership.role, scopes | approval_scopes)

    settings = get_settings()
    now = utc_now()
    connection_key = _mint(CONNECTION_KEY_PREFIX)
    key = AgentKey(
        company_id=company.id,
        user_id=user.id,
        name=name,
        scopes=_scopes_text(scopes),
        approval_scopes=_scopes_text(approval_scopes),
        connection_key_hash=hash_token(connection_key),
        connection_key_expires_at=now
        + timedelta(hours=settings.agent_connection_key_lifetime_hours),
        created_at=now,
        expires_at=now + timedelta(days=settings.agent_key_lifetime_days),
    )
    db.add(key)
    await db.flush()
    return IssuedKey(key=key, connection_key=connection_key)


# --- Exchanging and refreshing ----------------------------------------------------


@dataclass(frozen=True)
class TokenPair:
    """What an exchange or a refresh hands the agent. The tokens exist only here."""

    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    key: AgentKey
    company: Company
    person: User


def _require_usable(key: AgentKey, company: Company, now: datetime) -> None:
    """The checks every grant shares, each named so a refusal says which one."""
    if key.revoked_at is not None:
        raise AgentKeyStoppedError(
            "nøkkelen er stoppet. Be personen lage en ny nøkkel i Myntos under Selskapet → "
            "Tilgang, og lim den inn på nytt."
        )
    if as_utc(key.expires_at) <= now:
        raise AgentKeyStoppedError(
            "nøkkelen er over et år gammel og har sluttet å virke. Be personen lage en ny i "
            "Myntos under Innstillinger for selskapet → Tilgang."
        )
    if not company.agent_access_enabled:
        raise AgentAccessDisabledError(
            "selskapet har slått av nøkler for KI-agenter og API. Bare en eier eller "
            "administrator kan slå dem på igjen."
        )


async def _require_person(db: AsyncSession, key: AgentKey) -> None:
    """The key's person must still be active and still on these books.

    Leaving the books stops the key where it happens (the member routes), so
    this is the backstop for a membership that went some other way, and for
    an account that was deactivated.
    """
    row = (
        await db.execute(
            select(User.is_active, CompanyMembership.id)
            .select_from(User)
            .outerjoin(
                CompanyMembership,
                and_(
                    CompanyMembership.user_id == User.id,
                    CompanyMembership.company_id == key.company_id,
                ),
            )
            .where(User.id == key.user_id)
        )
    ).first()
    if row is None or not row.is_active or row.id is None:
        raise AgentKeyStoppedError(
            "personen som laget nøkkelen har ikke lenger tilgang til regnskapet, så nøkkelen "
            "virker ikke. Noen med tilgang må lage en ny."
        )


def _note_use(key: AgentKey, client: str | None, now: datetime) -> None:
    """Slide ``last_used_at``, at most once a minute, as a session does."""
    if key.last_used_at is None or now - as_utc(key.last_used_at) > timedelta(minutes=1):
        key.last_used_at = now
        if client:
            key.last_used_client = client[:255]


async def _issue_pair(
    db: AsyncSession, key: AgentKey, company: Company, person: User, now: datetime
) -> TokenPair:
    settings = get_settings()
    key_expires = as_utc(key.expires_at)
    access_expires = min(
        now + timedelta(minutes=settings.agent_access_token_lifetime_minutes), key_expires
    )
    refresh_expires = min(now + timedelta(days=settings.agent_refresh_idle_days), key_expires)
    access = _mint(ACCESS_TOKEN_PREFIX)
    refresh_token = _mint(REFRESH_TOKEN_PREFIX)
    # Tokens that can no longer be presented, whatever they were: an expired
    # access token opens nothing, and a spent refresh token past its own
    # deadline would be refused for that before reuse is even looked at.
    # No session synchronisation: the rows it removes are never read again in
    # this request, and evaluating the criteria against the loaded ones would
    # compare SQLite's naive timestamps with an aware one (myntos.core.clock).
    await db.execute(
        delete(AgentToken)
        .where(AgentToken.key_id == key.id, AgentToken.expires_at <= now)
        .execution_options(synchronize_session=False)
    )
    db.add_all(
        [
            AgentToken(
                key_id=key.id,
                kind=AgentTokenKind.ACCESS,
                token_hash=hash_token(access),
                created_at=now,
                expires_at=access_expires,
            ),
            AgentToken(
                key_id=key.id,
                kind=AgentTokenKind.REFRESH,
                token_hash=hash_token(refresh_token),
                created_at=now,
                expires_at=refresh_expires,
            ),
        ]
    )
    await db.flush()
    return TokenPair(
        access_token=access,
        access_expires_at=access_expires,
        refresh_token=refresh_token,
        refresh_expires_at=refresh_expires,
        key=key,
        company=company,
        person=person,
    )


def _wrong_kind(token: str, wanted: str) -> InvalidGrantError | None:
    if token.startswith(ACCESS_TOKEN_PREFIX):
        return InvalidGrantError(
            f"dette er en tilgangsnøkkel (myntos_at_), ikke {wanted}. Den brukes i "
            "Authorization-headeren, ikke her."
        )
    return None


async def exchange_connection_key(
    db: AsyncSession, connection_key: str, *, client: str | None
) -> TokenPair:
    """Spend a connection key on the first pair of tokens in its chain.

    The spend is a conditional ``UPDATE ... WHERE exchanged_at IS NULL``, the way
    a mailed link is redeemed, so two exchanges racing for one key cannot both
    start a chain: the second finds the row already spent.
    """
    wrong = _wrong_kind(connection_key, "en tilkoblingsnøkkel")
    if wrong is not None:
        raise wrong
    if connection_key.startswith(REFRESH_TOKEN_PREFIX):
        raise InvalidGrantError(
            "dette er en fornyelsesnøkkel (myntos_rt_). Send den med grant_type «refresh_token»."
        )
    row = (
        await db.execute(
            select(AgentKey, Company, User)
            .join(Company, Company.id == AgentKey.company_id)
            .join(User, User.id == AgentKey.user_id)
            .where(AgentKey.connection_key_hash == hash_token(connection_key))
        )
    ).first()
    if row is None:
        raise InvalidGrantError(
            "ukjent tilkoblingsnøkkel. Sjekk at hele nøkkelen ble kopiert, med myntos_ck_ foran."
        )
    key, company, person = row
    now = utc_now()
    if key.exchanged_at is not None:
        raise ConnectionKeySpentError(
            "tilkoblingsnøkkelen er allerede brukt én gang, og virker bare én gang. Har du "
            "fornyelsesnøkkelen fra den gangen, bruk den. Ellers må personen lage en ny nøkkel "
            "i Myntos under Innstillinger for selskapet → Tilgang."
        )
    _require_usable(key, company, now)
    if as_utc(key.connection_key_expires_at) <= now:
        raise ConnectionKeyExpiredError(
            "tilkoblingsnøkkelen ble ikke brukt innen et døgn og har gått ut. Be personen lage "
            "en ny i Myntos under Innstillinger for selskapet → Tilgang."
        )
    await _require_person(db, key)
    spent = await db.execute(
        update(AgentKey)
        .where(AgentKey.id == key.id, AgentKey.exchanged_at.is_(None))
        .values(exchanged_at=now)
        .execution_options(synchronize_session=False)
    )
    if getattr(spent, "rowcount", 0) != 1:
        raise ConnectionKeySpentError(
            "tilkoblingsnøkkelen ble brukt i samme øyeblikk et annet sted."
        )
    key.exchanged_at = now
    _note_use(key, client, now)
    return await _issue_pair(db, key, company, person, now)


@dataclass(frozen=True)
class StoppedForReuse:
    """What the caller needs to tell the person their key was stopped."""

    key: AgentKey
    person: User
    company: Company


async def refresh(
    db: AsyncSession, refresh_token: str, *, client: str | None
) -> TokenPair | StoppedForReuse:
    """Spend a refresh token on the next pair, or stop the key if it was spent already.

    Returns :class:`StoppedForReuse` rather than raising when a spent token
    comes back, because the stop has to be committed and the person mailed
    before the refusal goes out, and both of those belong to the caller. The
    caller raises :class:`RefreshTokenReusedError` afterwards.
    """
    wrong = _wrong_kind(refresh_token, "en fornyelsesnøkkel")
    if wrong is not None:
        raise wrong
    if refresh_token.startswith(CONNECTION_KEY_PREFIX):
        raise InvalidGrantError(
            "dette er en tilkoblingsnøkkel (myntos_ck_). Send den med grant_type «connection_key»."
        )
    row = (
        await db.execute(
            select(AgentToken, AgentKey, Company, User)
            .join(AgentKey, AgentKey.id == AgentToken.key_id)
            .join(Company, Company.id == AgentKey.company_id)
            .join(User, User.id == AgentKey.user_id)
            .where(
                AgentToken.token_hash == hash_token(refresh_token),
                AgentToken.kind == AgentTokenKind.REFRESH,
            )
        )
    ).first()
    if row is None:
        raise InvalidGrantError(
            "ukjent eller utløpt fornyelsesnøkkel. Be personen lage en ny nøkkel i Myntos under "
            "Innstillinger for selskapet → Tilgang."
        )
    token, key, company, person = row
    now = utc_now()
    if token.used_at is not None and now - as_utc(token.used_at) > REFRESH_REUSE_GRACE:
        if key.revoked_at is not None:
            # Already stopped, by this or anything else: say so without telling
            # the person a second time.
            _require_usable(key, company, now)
        stop_key(key, AgentKeyStop.REUSE, now=now)
        return StoppedForReuse(key=key, person=person, company=company)
    _require_usable(key, company, now)
    if as_utc(token.expires_at) <= now:
        raise AgentKeyStoppedError(
            "nøkkelen har ikke vært brukt på 30 dager og har sluttet å virke. Be personen lage "
            "en ny i Myntos under Innstillinger for selskapet → Tilgang."
        )
    await _require_person(db, key)
    if token.used_at is None:
        token.used_at = now
    _note_use(key, client, now)
    return await _issue_pair(db, key, company, person, now)


def reused_error() -> RefreshTokenReusedError:
    return RefreshTokenReusedError(
        "denne fornyelsesnøkkelen var allerede brukt. Det betyr at noen andre kan ha en kopi, "
        "så nøkkelen er stoppet og personen har fått beskjed. Be personen lage en ny nøkkel i "
        "Myntos under Innstillinger for selskapet → Tilgang, og ta vare på den nye "
        "fornyelsesnøkkelen hver gang du får en."
    )


# --- Every request ----------------------------------------------------------------


@dataclass(frozen=True)
class KeyAccess:
    """The key a request was made with, and the person it works for.

    ``role`` is the person's role on the key's company *now*. It is carried for
    the record; each route still asks the membership it resolves itself, so a
    role changed a second ago applies to the next request.
    """

    key: AgentKey
    user: User
    role: OrganizationRole
    # For the one message the gate may have to send about the key (a stop for
    # probing), so that sending it needs no lookup by id of its own.
    company_name: str


async def resolve_access_token(db: AsyncSession, token: str) -> KeyAccess:
    """The live key behind an access token, or a refusal that says what to do.

    One round trip for the token, the key, the person, the company switch and
    the person's membership, because this runs on every request a key makes
    and the database is in Frankfurt (myntos.db.session).

    Every way an access token can be dead is the same 401: the agent's next
    move is the same for all of them, which is to refresh, and the refresh
    says precisely why when it cannot.
    """
    if token.startswith((CONNECTION_KEY_PREFIX, REFRESH_TOKEN_PREFIX)):
        raise NotAnAccessTokenError(
            "dette er ikke en tilgangsnøkkel. Bytt tilkoblingsnøkkelen (myntos_ck_) eller "
            "fornyelsesnøkkelen (myntos_rt_) mot en tilgangsnøkkel med POST /api/v1/agent/token "
            "først, og send den (myntos_at_) i Authorization-headeren."
        )
    row = (
        await db.execute(
            select(
                AgentToken,
                AgentKey,
                User,
                Company.agent_access_enabled,
                Company.name,
                CompanyMembership.role,
            )
            .join(AgentKey, AgentKey.id == AgentToken.key_id)
            .join(User, User.id == AgentKey.user_id)
            .join(Company, Company.id == AgentKey.company_id)
            .outerjoin(
                CompanyMembership,
                and_(
                    CompanyMembership.company_id == AgentKey.company_id,
                    CompanyMembership.user_id == AgentKey.user_id,
                ),
            )
            .where(
                AgentToken.token_hash == hash_token(token),
                AgentToken.kind == AgentTokenKind.ACCESS,
            )
        )
    ).first()
    expired = InvalidAgentTokenError(
        "tilgangsnøkkelen er ukjent eller utløpt. Forny den med fornyelsesnøkkelen "
        "(POST /api/v1/agent/token, grant_type «refresh_token») og prøv igjen."
    )
    if row is None:
        raise expired
    access_token, key, user, enabled, company_name, role = row
    now = utc_now()
    if as_utc(access_token.expires_at) <= now or key.revoked_at is not None:
        raise expired
    if as_utc(key.expires_at) <= now or role is None or not user.is_active:
        raise expired
    if not enabled:
        raise AgentAccessDisabledError(
            "selskapet har slått av nøkler for KI-agenter og API. Bare en eier eller "
            "administrator kan slå dem på igjen."
        )
    _note_use(key, None, now)
    return KeyAccess(key=key, user=user, role=role, company_name=company_name)


async def resolve_key_for_replay(db: AsyncSession, key_id: int) -> KeyAccess:
    """The key a request approved on /godkjenning/ runs as, if it is still alive.

    Everything :func:`resolve_access_token` checks except the token, which the
    replay does not have and does not need: it was started by the key's person,
    signed in, pressing «Godkjenn». A key stopped since the request was made
    runs nothing, whoever approves; the page offers no button for it, and this
    is the check behind the page.
    """
    row = (
        await db.execute(
            select(
                AgentKey, User, Company.agent_access_enabled, Company.name, CompanyMembership.role
            )
            .join(User, User.id == AgentKey.user_id)
            .join(Company, Company.id == AgentKey.company_id)
            .outerjoin(
                CompanyMembership,
                and_(
                    CompanyMembership.company_id == AgentKey.company_id,
                    CompanyMembership.user_id == AgentKey.user_id,
                ),
            )
            .where(AgentKey.id == key_id)
        )
    ).first()
    if row is None:
        raise AgentKeyStoppedError("nøkkelen finnes ikke lenger")
    key, user, enabled, company_name, role = row
    now = utc_now()
    if key.revoked_at is not None or as_utc(key.expires_at) <= now:
        raise AgentKeyStoppedError("nøkkelen er stoppet, så det den ba om kan ikke kjøres")
    if role is None or not user.is_active:
        raise AgentKeyStoppedError("personen bak nøkkelen har ikke lenger tilgang til regnskapet")
    if not enabled:
        raise AgentAccessDisabledError("selskapet har slått av nøkler for KI-agenter og API")
    return KeyAccess(key=key, user=user, role=role, company_name=company_name)


# --- Stopping ---------------------------------------------------------------------


def stop_key(key: AgentKey, reason: AgentKeyStop, *, now: datetime | None = None) -> None:
    """Stop one key. Idempotent, and the first reason is the one kept."""
    if key.revoked_at is None:
        key.revoked_at = now or utc_now()
        key.revoked_reason = reason


async def stop_keys(
    db: AsyncSession,
    *,
    reason: AgentKeyStop,
    user_id: int | None = None,
    company_id: int | None = None,
) -> None:
    """Stop every live key of a person, of a company, or of a person in a company."""
    if user_id is None and company_id is None:
        raise ValueError("refusing to stop every key there is")
    statement = (
        update(AgentKey)
        .where(AgentKey.revoked_at.is_(None))
        .values(revoked_at=utc_now(), revoked_reason=reason)
        .execution_options(synchronize_session="fetch")
    )
    if user_id is not None:
        statement = statement.where(AgentKey.user_id == user_id)
    if company_id is not None:
        statement = statement.where(AgentKey.company_id == company_id)
    await db.execute(statement)


async def stop_by_token(db: AsyncSession, token: str) -> None:
    """Stop the key any of its credentials belongs to, as the agent asked.

    Possession is the proof, as RFC 7009 has it for revocation: whoever holds
    a credential may always make it worthless. An unknown token is not an
    error, so the answer cannot be used to test which tokens exist.
    """
    digest = hash_token(token)
    key: AgentKey | None
    if token.startswith(CONNECTION_KEY_PREFIX):
        key = await db.scalar(select(AgentKey).where(AgentKey.connection_key_hash == digest))
    else:
        key = await db.scalar(
            select(AgentKey)
            .join(AgentToken, AgentToken.key_id == AgentKey.id)
            .where(AgentToken.token_hash == digest)
        )
    if key is not None:
        stop_key(key, AgentKeyStop.AGENT)


# --- Reading the list -------------------------------------------------------------


async def list_keys(
    db: AsyncSession, *, company_id: int, user_id: int | None = None
) -> list[AgentKey]:
    """The company's keys, newest first, with their people loaded.

    ``user_id`` narrows it to one person's own, which is what a member without
    ``member:manage`` sees: their keys are theirs to know about, everybody
    else's are access management.
    """
    query = (
        select(AgentKey)
        .where(AgentKey.company_id == company_id)
        .options(selectinload(AgentKey.user))
        .order_by(AgentKey.created_at.desc(), AgentKey.id.desc())
    )
    if user_id is not None:
        query = query.where(AgentKey.user_id == user_id)
    return list(await db.scalars(query))


async def get_key(db: AsyncSession, *, company_id: int, key_id: int) -> AgentKey:
    """One key, looked up with its company like every other sub-resource."""
    key = await db.scalar(
        select(AgentKey).where(AgentKey.id == key_id, AgentKey.company_id == company_id)
    )
    if key is None:
        raise AgentKeyNotFoundError("fant ingen slik nøkkel")
    return key


KeyState = Literal["waiting", "active", "stopped", "expired"]


def key_state(key: AgentKey, *, now: datetime) -> KeyState:
    """``waiting``, ``active``, ``stopped`` or ``expired``, as the Tilgang page shows it.

    Derived on read and never stored, like a session's liveness: a stored
    state would be one more thing for the clock to make wrong. The idle rule
    reads ``last_used_at``, which an agent in use slides at least every fifteen
    minutes, so it is the refresh chain's own deadline to within that.
    """
    if key.revoked_at is not None:
        return "stopped"
    if as_utc(key.expires_at) <= now:
        return "expired"
    if key.exchanged_at is None:
        return "expired" if as_utc(key.connection_key_expires_at) <= now else "waiting"
    idle = timedelta(days=get_settings().agent_refresh_idle_days)
    last = key.last_used_at or key.exchanged_at
    if as_utc(last) + idle <= now:
        return "expired"
    return "active"


# Products every browser-shaped User-Agent carries, and which therefore say
# nothing about which agent this is. ChatGPT's tools, for one, send a string
# that starts "Mozilla/5.0 AppleWebKit/537.36" and names itself only further in.
_GENERIC_PRODUCTS = frozenset(
    {
        "mozilla",
        "applewebkit",
        "chrome",
        "safari",
        "gecko",
        "firefox",
        "version",
        "mobile",
        "edg",
        "opr",
        "khtml",
    }
)


def client_name(user_agent: str | None) -> str | None:
    """The agent's software, coarsely: the first product its User-Agent names.

    ``python-httpx/0.28.1`` becomes ``python-httpx``, and a browser-shaped
    string from an agent's tool becomes the tool's own name (``ChatGPT-User``)
    rather than ``Mozilla``. Enough for a person to tell their own agent from
    somebody else's, without putting on screen the whole string, which for a
    browser is closer to a fingerprint than a name (docs/gdpr.md, the session
    list).
    """
    if not user_agent:
        return None
    products = [
        word.split("/")[0]
        for word in re.split(r"[\s;()]+", user_agent)
        if "/" in word and not word.startswith(("+", "http"))
    ]
    named = [product for product in products if product.lower() not in _GENERIC_PRODUCTS]
    chosen = named[0] if named else (products[0] if products else user_agent.split()[0])
    return chosen[:64] or None


# --- Limits (docs/agents-and-api.md rule 10) --------------------------------------
#
# Counted in the process rather than the database: a row written on every
# request would make the limit itself the load. Exact with the one uvicorn
# process this deployment runs; with more, each worker counts its own share.

_WINDOW = 60.0
_REFUSAL_WINDOW = 600.0
_requests: dict[int, deque[float]] = {}
_refusals: dict[int, deque[float]] = {}


def _count(store: dict[int, deque[float]], key_id: int, window: float) -> int:
    now = time.monotonic()
    seen = store.setdefault(key_id, deque())
    seen.append(now)
    while seen and seen[0] <= now - window:
        seen.popleft()
    return len(seen)


def admit_request(key_id: int) -> bool:
    """Count one request; ``False`` once the key is past its limit for the minute."""
    return _count(_requests, key_id, _WINDOW) <= get_settings().agent_key_requests_per_minute


def count_refusal(key_id: int) -> bool:
    """Count one refusal; ``True`` when it is the one that should stop the key."""
    limit = get_settings().agent_key_refusals_before_suspension
    return _count(_refusals, key_id, _REFUSAL_WINDOW) >= limit


def forget_counts() -> None:
    """Empty the counters. For the tests, where every database starts its ids at 1."""
    _requests.clear()
    _refusals.clear()


# --- The record on the voucher ----------------------------------------------------


@event.listens_for(Session, "before_flush")
def _stamp_vouchers(session: Session, _context: UOWTransaction, _instances: object) -> None:
    """Write the request's key on every voucher it creates (rule 6).

    Here rather than in the code that builds vouchers, so that no way of
    posting can forget it: payments, corrections, reversals, invoices, credit
    notes, bank lines, opening balances and year ends all post their own, and
    the next one will be written by somebody thinking about something else. A request made in a
    browser has no key in ``session.info``, so a voucher posted there can
    never claim one.
    """
    key_id = session.info.get(AGENT_KEY_INFO)
    if key_id is None:
        return
    for instance in session.new:
        if isinstance(instance, Transaction) and instance.created_via_agent_key_id is None:
            instance.created_via_agent_key_id = key_id
