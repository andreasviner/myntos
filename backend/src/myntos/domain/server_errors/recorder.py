"""Turn every ERROR logged under ``myntos`` into a row of the error log, minus the data.

A logging handler rather than a call at each place that fails, because the
failures that matter most are the ones nobody thought to report: a request
that crashed (``api.errors.unhandled_error_middleware``), an import that died
in the background (``saft.jobs``), the daily purge. They all log already, at
ERROR, so catching the log catches them all, and a new one is caught without
anybody remembering this exists.

The handler only boils the record down (:func:`boil_down`) and counts it in
memory. Writing to the database from inside ``emit`` would put a database
round trip, or a failure of one, in the middle of whatever was logging, so
the pending counts are stored every few seconds by :func:`store_regularly`,
which the application's lifespan runs, and once more when it stops. A restart
in between loses at most those seconds, which the journal still has.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import traceback
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from myntos.domain.server_errors import service
from myntos.domain.server_errors.models import (
    EXCEPTION_LENGTH,
    LOGGER_LENGTH,
    ROUTE_LENGTH,
    TEMPLATE_LENGTH,
)

logger = logging.getLogger(__name__)

# How often what was counted is written. Short enough that the operator's page
# shows a failure the person who met it is still looking at.
STORE_EVERY_SECONDS = 10.0

# The innermost frames of a traceback. A request's goes through some twenty
# frames of Starlette before it reaches Myntos's own, and the failure is at the
# far end.
MAX_FRAMES = 40

# Kinds of failure held in memory between two writes. A deploy that breaks
# every route at once makes a few dozen; past this the rest are in the journal
# only, which is better than a process that grows while its database is down.
MAX_PENDING = 200

# How far down ``raise … from …`` the causes are named.
MAX_CAUSES = 3

# The attribute ``unhandled_error_middleware`` puts on its record, through
# ``extra``: the method and the route's template.
ROUTE_ATTRIBUTE = "myntos_route"

_OWN = "myntos/"


@dataclass
class Occurrence:
    """What may be kept of one or more ERROR records that are the same failure."""

    fingerprint: str
    logger: str
    template: str
    route: str | None
    exception: str | None
    causes: list[str]
    frames: list[dict[str, Any]]
    first_seen_at: datetime
    last_seen_at: datetime
    count: int = 1

    def absorb(self, later: Occurrence) -> None:
        """Count ``later`` into this one, keeping its frames: they are of the code running now."""
        self.count += later.count
        self.first_seen_at = min(self.first_seen_at, later.first_seen_at)
        if later.last_seen_at >= self.last_seen_at:
            self.last_seen_at = later.last_seen_at
            self.frames = later.frames


def _short_path(path: str) -> str:
    """Where a frame's file is, relative to what installed it, without the machine's own paths."""
    normalized = path.replace("\\", "/")
    for marker in ("/site-packages/", "/src/"):
        if marker in normalized:
            return normalized.rsplit(marker, 1)[1]
    parts = normalized.rsplit("/", 2)
    return "/".join(parts[-2:])


def _qualified(kind: type[BaseException]) -> str:
    name = kind.__qualname__
    module = kind.__module__
    qualified = name if module in {"builtins", "__main__"} else f"{module}.{name}"
    return qualified[:EXCEPTION_LENGTH]


def _causes(error: BaseException) -> list[str]:
    """The types the error was raised from, outermost first. Types only: never their words."""
    found: list[str] = []
    current: BaseException | None = error
    while current is not None and len(found) < MAX_CAUSES:
        if isinstance(current, BaseExceptionGroup) and current.exceptions:
            nxt: BaseException | None = current.exceptions[0]
        elif current.__cause__ is not None:
            nxt = current.__cause__
        elif current.__context__ is not None and not current.__suppress_context__:
            nxt = current.__context__
        else:
            nxt = None
        if nxt is not None:
            found.append(_qualified(type(nxt)))
        current = nxt
    return found


def _frames(error: BaseException | None, record: logging.LogRecord) -> list[dict[str, Any]]:
    """The traceback's frames, or where the line was logged when there is no exception.

    ``extract_tb`` is called without ``capture_locals``, so what comes back is
    the file, the line number, the function and the source line read from the
    file on disk. Nothing in it was a value at the time.
    """
    if error is not None and error.__traceback__ is not None:
        summary = traceback.extract_tb(error.__traceback__)[-MAX_FRAMES:]
        return [
            {
                "file": _short_path(frame.filename),
                "line": frame.lineno,
                "function": frame.name,
                "code": (frame.line or "").strip() or None,
            }
            for frame in summary
        ]
    return [
        {
            "file": _short_path(record.pathname),
            "line": record.lineno,
            "function": record.funcName,
            "code": None,
        }
    ]


def _fingerprint(
    name: str,
    template: str,
    route: str | None,
    exception: str | None,
    causes: list[str],
    frames: list[dict[str, Any]],
) -> str:
    own = [frame for frame in frames if str(frame["file"]).startswith(_OWN)] or frames
    where = [(frame["file"], frame["function"]) for frame in own]
    material = json.dumps([name, template, route, exception, causes, where])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def boil_down(record: logging.LogRecord) -> Occurrence:
    """What may be kept of ``record``: its code, never its data. See the models docstring.

    ``record.msg`` is the template, before ``record.args`` were put into it,
    and only that is kept. A record whose ``msg`` is not a string was logged as
    ``logger.error(some_object)``, and the object's text is data, so only its
    type is kept.
    """
    error = record.exc_info[1] if record.exc_info else None
    template = (record.msg if isinstance(record.msg, str) else f"<{type(record.msg).__name__}>")[
        :TEMPLATE_LENGTH
    ]
    route = getattr(record, ROUTE_ATTRIBUTE, None)
    route = route[:ROUTE_LENGTH] if isinstance(route, str) else None
    exception = _qualified(type(error)) if error is not None else None
    causes = _causes(error) if error is not None else []
    frames = _frames(error, record)
    name = record.name[:LOGGER_LENGTH]
    when = datetime.fromtimestamp(record.created, UTC)
    return Occurrence(
        fingerprint=_fingerprint(name, template, route, exception, causes, frames),
        logger=name,
        template=template,
        route=route,
        exception=exception,
        causes=causes,
        frames=frames,
        first_seen_at=when,
        last_seen_at=when,
    )


class ErrorRecorder(logging.Handler):
    """Counts ERROR records by kind of failure until :meth:`take` hands them over.

    ``emit`` may run on any thread (work handed to ``asyncio.to_thread`` logs
    too), so the pending counts sit behind a lock of their own, which
    :meth:`take` and :meth:`put_back` share.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self._pending: dict[str, Occurrence] = {}
        self._pending_lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        # The recorder's own complaints (a write that failed) are warnings,
        # below this handler's level; this is the second guard against a
        # failure to store a failure becoming a failure to store.
        if record.name == __name__:
            return
        try:
            seen = boil_down(record)
        except Exception:  # a log call must never fail because of what it is recorded in
            return
        with self._pending_lock:
            self._add(seen)

    def _add(self, seen: Occurrence) -> None:
        existing = self._pending.get(seen.fingerprint)
        if existing is not None:
            existing.absorb(seen)
        elif len(self._pending) < MAX_PENDING:
            self._pending[seen.fingerprint] = seen

    def take(self) -> list[Occurrence]:
        """Everything counted since the last call, emptied out of the recorder."""
        with self._pending_lock:
            taken = list(self._pending.values())
            self._pending.clear()
        return taken

    def put_back(self, occurrences: list[Occurrence]) -> None:
        """Return what could not be stored, counted together with anything newer."""
        with self._pending_lock:
            for seen in occurrences:
                self._add(replace(seen))


RECORDER = ErrorRecorder()


def install() -> None:
    """Put :data:`RECORDER` on the ``myntos`` logger, once however often ``create_app`` runs.

    On ``myntos`` and not on the root logger, so that what is recorded is
    Myntos's own failures: a library that logs an error it then handled is
    not one, and the request that it broke is recorded anyway, by the
    middleware, if it failed.
    """
    package = logging.getLogger("myntos")
    if RECORDER not in package.handlers:
        package.addHandler(RECORDER)


async def store_pending(sessions: async_sessionmaker[AsyncSession]) -> int:
    """Write what the recorder has counted, and return how many kinds of failure that was.

    On failure what was taken goes back, to be tried with the next batch: the
    database being unreachable is itself often the failure being recorded.
    """
    taken = RECORDER.take()
    if not taken:
        return 0
    try:
        async with sessions() as db:
            await service.store(db, taken)
            await db.commit()
    except Exception as error:
        RECORDER.put_back(taken)
        # A warning, not an error: an error here would be recorded, fail to be
        # stored, and be recorded again.
        logger.warning("Could not store %d recorded failures: %s", len(taken), type(error).__name__)
        return 0
    return len(taken)


async def store_regularly(sessions: async_sessionmaker[AsyncSession]) -> None:
    """Store what was recorded every :data:`STORE_EVERY_SECONDS`, until cancelled."""
    while True:
        await asyncio.sleep(STORE_EVERY_SECONDS)
        await store_pending(sessions)
