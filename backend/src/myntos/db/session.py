"""Async engine and session management.

The engine is created lazily so importing the application never opens a
database connection (tests and tooling stay DB-free until needed).

Every setting below is about one fact: the database is not on this machine. It
is Neon in eu-central-1 and the round trip is roughly 33 ms, so a request costs
however many times it talks to Frankfurt, and that is the number worth keeping
down — not the SQL.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from myntos.core.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


# Under Neon's own idle timeout, so a connection is retired by age before the
# far end can close it underneath us. Five minutes is when Neon suspends an idle
# compute; four gives the check somewhere to stand.
POOL_RECYCLE_SECONDS = 240


def get_engine() -> AsyncEngine:
    """The one engine, built on first use.

    ``pool_recycle`` rather than ``pool_pre_ping``, and the difference is
    visible to the user. Pre-ping proves a pooled connection is alive by using
    it — a rollback and a ``SELECT 1`` before the request's own work — which is
    three extra round trips to Frankfurt on *every* request. Measured from this
    machine: 200 ms to check out a connection and run one query with pre-ping,
    96 ms without. The front page makes four requests, so it was paying about
    four hundred milliseconds to ask a question it can answer another way.

    Recycling by age answers it another way. Neon closes a connection when it
    suspends the compute, which it does after five minutes idle; a connection
    older than four minutes is discarded and replaced without asking anybody, so
    the dead one is never handed out in the first place. Replacing costs about
    240 ms — but only to whoever arrives after four idle minutes, and that
    request was going to be slow regardless, because a compute that has been
    idle that long is about to be woken up too. What matters is that somebody
    working in the app, request after request, now pays nothing for either.

    Two ways this is weaker than pre-ping, both bounded. A connection killed
    from the far side for some reason *other* than idleness, inside four
    minutes, is handed out dead and costs one failed request. And a request that
    lands between the fourth and fifth minute reconnects when it did not have
    to. Neither is worth 100 ms on every request that is not in those cases.
    """
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            get_settings().database_url,
            pool_recycle=POOL_RECYCLE_SECONDS,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a database session.

    One transaction per request: committed when the handler returns, rolled back
    when it raises. That is what lets a multi-step operation — create the user,
    create their organization, make them its owner — be all-or-nothing without
    any endpoint managing the transaction itself.

    A handler that must persist something *while still failing* (recording a
    failed login attempt, for instance) commits it deliberately before raising;
    see :func:`myntos.domain.identity.service.authenticate_with_password`.
    """
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
