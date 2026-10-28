"""Storing, listing, resolving and purging the server's recorded failures.

:func:`store` is what the recorder's regular write calls
(``myntos.domain.server_errors.recorder``). The rest is the operator's, called
by the routes under ``/api/v1/drift`` with the operator key and by nothing a
person's session or an agent's key can reach (docs/operator.md).
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.core.clock import as_utc, utc_now
from myntos.domain.server_errors.errors import ServerErrorNotFoundError
from myntos.domain.server_errors.models import ServerError

if TYPE_CHECKING:
    from myntos.domain.server_errors.recorder import Occurrence


class ServerErrorStatus(enum.StrEnum):
    """Whether the operator has called it fixed. ``resolved_at`` is the stored form."""

    OPEN = "open"
    RESOLVED = "resolved"


def status_of(row: ServerError) -> ServerErrorStatus:
    return ServerErrorStatus.OPEN if row.resolved_at is None else ServerErrorStatus.RESOLVED


async def store(db: AsyncSession, occurrences: Iterable[Occurrence]) -> None:
    """Add each occurrence to its kind of failure, or start one.

    A failure the operator had resolved and that happens again is open again:
    the fix did not hold, or was not deployed.
    """
    for seen in occurrences:
        row = await db.scalar(
            select(ServerError).where(ServerError.fingerprint == seen.fingerprint)
        )
        if row is None:
            db.add(
                ServerError(
                    fingerprint=seen.fingerprint,
                    logger=seen.logger,
                    template=seen.template,
                    route=seen.route,
                    exception=seen.exception,
                    causes=seen.causes,
                    frames=seen.frames,
                    count=seen.count,
                    first_seen_at=seen.first_seen_at,
                    last_seen_at=seen.last_seen_at,
                )
            )
            continue
        row.count += seen.count
        if seen.last_seen_at >= as_utc(row.last_seen_at):
            row.last_seen_at = seen.last_seen_at
            row.frames = seen.frames
        if row.resolved_at is not None and seen.last_seen_at > as_utc(row.resolved_at):
            row.resolved_at = None


async def listing(
    db: AsyncSession, *, status: ServerErrorStatus | None = None, limit: int = 200
) -> list[ServerError]:
    """Failures, the most recent first: what broke last is what somebody is writing about."""
    query = select(ServerError).order_by(ServerError.last_seen_at.desc(), ServerError.id.desc())
    if status is ServerErrorStatus.OPEN:
        query = query.where(ServerError.resolved_at.is_(None))
    elif status is ServerErrorStatus.RESOLVED:
        query = query.where(ServerError.resolved_at.is_not(None))
    return list(await db.scalars(query.limit(limit)))


async def open_count(db: AsyncSession) -> int:
    return int(
        await db.scalar(
            select(func.count()).select_from(ServerError).where(ServerError.resolved_at.is_(None))
        )
        or 0
    )


async def get(db: AsyncSession, number: int) -> ServerError:
    # A select rather than db.get, as in feedback.service.get: not
    # company-scoped either way, and only the operator names a row by number.
    row = await db.scalar(select(ServerError).where(ServerError.id == number))
    if row is None:
        raise ServerErrorNotFoundError(f"Ingen feil har nummer {number}.")
    return row


def set_status(row: ServerError, status: ServerErrorStatus, *, now: datetime | None = None) -> None:
    row.resolved_at = (now or utc_now()) if status is ServerErrorStatus.RESOLVED else None


async def purge(db: AsyncSession, *, retention_days: int, now: datetime | None = None) -> int:
    """Delete every kind of failure not seen for ``retention_days``, resolved or not.

    One that has not happened for that long has stopped happening, and the
    journal has gone further back than anybody will look. The count is
    returned for the log line.
    """
    cutoff = (now or utc_now()) - timedelta(days=retention_days)
    result = await db.execute(delete(ServerError).where(ServerError.last_seen_at < cutoff))
    return int(getattr(result, "rowcount", 0) or 0)
