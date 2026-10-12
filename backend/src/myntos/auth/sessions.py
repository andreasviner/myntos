"""Login session lifecycle.

A session is a row in :class:`~myntos.domain.identity.models.UserSession` plus
an opaque token the client returns on every request. The database is the
authority on whether it is still valid, which is the whole reason for not using
a self-contained JWT here: revocation has to be immediate.

Expiry is evaluated in Python rather than in the ``WHERE`` clause on purpose.
The lookup is by token hash — a unique index — so the row is fetched either way,
and doing the four checks in code lets each one be named and tested instead of
collapsing into one unreadable predicate.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.auth.tokens import generate_token, hash_token
from myntos.core.clock import as_utc, utc_now
from myntos.core.config import get_settings
from myntos.domain.identity.errors import SessionNotFoundError
from myntos.domain.identity.models import User, UserSession


@dataclass(frozen=True)
class IssuedSession:
    """A newly created session and the token to hand to the client.

    The token exists only here and in the response. It is not recoverable from
    ``session``, which holds nothing but its digest.
    """

    token: str
    session: UserSession


async def issue_session(
    db: AsyncSession,
    user: User,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> IssuedSession:
    """Create a session for ``user`` and return it with its plaintext token."""
    settings = get_settings()
    now = utc_now()
    token = generate_token()
    session = UserSession(
        user_id=user.id,
        token_hash=hash_token(token),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(days=settings.session_lifetime_days),
        # Long user-agent strings exist; the column is for recognising a device,
        # so truncating is better than failing the login.
        user_agent=user_agent[:255] if user_agent else None,
        ip_address=ip_address[:45] if ip_address else None,
    )
    db.add(session)
    await db.flush()
    return IssuedSession(token=token, session=session)


async def resolve_session(db: AsyncSession, token: str) -> UserSession | None:
    """Return the live session ``token`` identifies, or ``None``.

    Sliding the idle window is a write on a read path, so it only happens when
    the stored value is more than a minute stale. Without that guard a busy
    client would issue an ``UPDATE`` per request and every session row would
    become a write hotspot.
    """
    session = await db.scalar(
        select(UserSession).where(UserSession.token_hash == hash_token(token))
    )
    if session is None:
        return None
    if session.revoked_at is not None:
        return None

    now = utc_now()
    if as_utc(session.expires_at) <= now:
        return None

    settings = get_settings()
    idle_deadline = as_utc(session.last_seen_at) + timedelta(
        hours=settings.session_idle_timeout_hours
    )
    if idle_deadline <= now:
        # Retire it explicitly. Leaving it merely "stale" would let the session
        # come back to life the moment the timeout is reconfigured upwards.
        session.revoked_at = now
        return None

    if now - as_utc(session.last_seen_at) > timedelta(minutes=1):
        session.last_seen_at = now
    return session


def revoke_session(session: UserSession) -> None:
    """Mark one session as logged out. Idempotent."""
    if session.revoked_at is None:
        session.revoked_at = utc_now()


async def revoke_all_sessions(
    db: AsyncSession, user_id: int, *, except_session_id: int | None = None
) -> None:
    """Log a user out everywhere, optionally sparing the session in hand.

    Used after a password change: whoever changed it keeps their session, and
    anyone who was signed in with the old password loses theirs.
    """
    statement = (
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utc_now())
    )
    if except_session_id is not None:
        statement = statement.where(UserSession.id != except_session_id)
    await db.execute(statement)


def is_live(session: UserSession, *, now: datetime) -> bool:
    """Whether ``session`` would still authenticate at ``now``.

    The same three deadlines :func:`resolve_session` applies, without its side
    effects: listing somebody's sessions must not retire the idle ones as it
    reads them, and must not slide the window of every row it touches.
    """
    if session.revoked_at is not None or as_utc(session.expires_at) <= now:
        return False
    idle_deadline = as_utc(session.last_seen_at) + timedelta(
        hours=get_settings().session_idle_timeout_hours
    )
    return idle_deadline > now


async def list_live_sessions(db: AsyncSession, user_id: int) -> list[UserSession]:
    """The sessions ``user_id`` could still be signed in with, latest use first.

    Revoked and expired rows stay in the table for their audit value (see
    :class:`UserSession`) but are not offered here: a list of "your logins"
    that included the ones already ended would invite ending them again.
    """
    now = utc_now()
    rows = await db.scalars(
        select(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .order_by(UserSession.last_seen_at.desc(), UserSession.id.desc())
    )
    return [session for session in rows if is_live(session, now=now)]


async def get_own_session(db: AsyncSession, *, user_id: int, session_id: int) -> UserSession:
    """One of the caller's own sessions, by id.

    Looked up with the owner in the ``WHERE`` clause, never by primary key
    alone: a session id belonging to somebody else is answered exactly like
    one that does not exist, so the endpoint can neither sign other people out
    nor tell anybody which ids are live.
    """
    session = await db.scalar(
        select(UserSession).where(UserSession.id == session_id, UserSession.user_id == user_id)
    )
    if session is None:
        raise SessionNotFoundError("fant ingen slik innlogging")
    return session
