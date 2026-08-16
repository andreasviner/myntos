"""Async engine and session management.

The engine is created lazily so importing the application never opens a
database connection (tests and tooling stay DB-free until needed).
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


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
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
