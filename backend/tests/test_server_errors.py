"""The server's failures, kept without anybody's data (docs/operator.md).

What these hold: a failure is kept as its code (types, frames, the log line's
template, the route's template) and never as its data (the message, the logged
values, the address asked for); the same failure is counted rather than
repeated; a resolved one opens again when it happens again; nothing is lost
when the database is down; and old ones are purged.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from myntos.core.clock import utc_now
from myntos.domain.server_errors import recorder, service
from myntos.domain.server_errors.models import ServerError
from myntos.domain.server_errors.recorder import RECORDER, Occurrence, boil_down
from myntos.domain.server_errors.service import ServerErrorStatus
from tests.conftest import TEST_ORIGIN

# What a failure's data looks like: a company's name, which must never be kept.
NAME = "Ola Nordmann AS"

log = logging.getLogger("myntos.tests.server_errors")


def _record(error: BaseException | None, msg: object, *args: object) -> logging.LogRecord:
    exc_info = (type(error), error, error.__traceback__) if error is not None else None
    return log.makeRecord(log.name, logging.ERROR, __file__, 1, msg, args, exc_info)


def _raised(error: BaseException) -> BaseException:
    try:
        raise error
    except BaseException as caught:
        return caught


def _kept(occurrence: Occurrence) -> str:
    return json.dumps(asdict(occurrence), default=str)


def _in_database(database_url: str, work: Any) -> Any:
    """Run ``work(db)`` against the test's database in a session of its own, and commit."""

    async def go() -> Any:
        engine = create_async_engine(database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                result = await work(db)
                await db.commit()
                return result
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _store_pending(database_url: str) -> int:
    async def go() -> int:
        engine = create_async_engine(database_url)
        try:
            return await recorder.store_pending(async_sessionmaker(engine, expire_on_commit=False))
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _rows(database_url: str) -> list[ServerError]:
    async def work(db: AsyncSession) -> list[ServerError]:
        return await service.listing(db)

    rows: list[ServerError] = _in_database(database_url, work)
    return rows


# --- Code is kept, data is not ---------------------------------------------------


def test_the_table_has_no_column_that_could_name_anybody() -> None:
    columns = set(ServerError.__table__.columns.keys())

    assert columns == {
        "id",
        "fingerprint",
        "logger",
        "template",
        "route",
        "exception",
        "causes",
        "frames",
        "count",
        "first_seen_at",
        "last_seen_at",
        "resolved_at",
    }
    assert not ServerError.__table__.foreign_keys


def test_the_message_and_the_logged_values_are_left_out() -> None:
    name = NAME
    error = _raised(KeyError(name))

    kept = boil_down(_record(error, "SAF-T-innlesing %s feilet for %s", 12, name))

    assert kept.template == "SAF-T-innlesing %s feilet for %s"
    assert kept.exception == "KeyError"
    assert NAME not in _kept(kept)
    # The frame is where it was raised, read from the code: the variable's
    # name is there, its value never.
    assert kept.frames[-1]["function"] == "_raised"
    assert kept.frames[-1]["code"] == "raise error"


def test_an_object_logged_instead_of_a_template_is_kept_as_its_type() -> None:
    kept = boil_down(_record(None, {"company": NAME}))

    assert kept.template == "<dict>"
    assert NAME not in _kept(kept)


def test_what_it_was_raised_from_is_kept_as_types() -> None:
    name = NAME
    try:
        try:
            raise KeyError(name)
        except KeyError as inner:
            raise ValueError(name) from inner
    except ValueError as caught:
        kept = boil_down(_record(caught, "failed"))

    assert kept.exception == "ValueError"
    assert kept.causes == ["KeyError"]
    assert NAME not in _kept(kept)


def test_a_failed_request_is_kept_with_the_routes_template(client: TestClient) -> None:
    app: FastAPI = client.app  # type: ignore[assignment]

    @app.get("/api/v1/companies/{company_id}/_test_boom")
    async def boom(company_id: int) -> None:
        raise RuntimeError(f"{NAME} ({company_id})")

    response = client.get("/api/v1/companies/4711/_test_boom", headers={"Origin": TEST_ORIGIN})

    assert response.status_code == 500
    [kept] = RECORDER.take()
    assert kept.route == "GET /api/v1/companies/{company_id}/_test_boom"
    assert kept.exception == "RuntimeError"
    assert kept.template == "unhandled error on %s %s"
    assert "4711" not in _kept(kept)
    assert NAME not in _kept(kept)


# --- Counted, not repeated ---------------------------------------------------------


def test_the_same_failure_is_counted_and_a_different_one_is_not() -> None:
    def fails(value: str) -> None:
        raise KeyError(value)

    for value in ("a", "b", "c"):
        try:
            fails(value)
        except KeyError:
            log.exception("could not read %s", value)
    try:
        fails("d")
    except KeyError as error:
        log.error("something else entirely", exc_info=error)

    pending = sorted(RECORDER.take(), key=lambda seen: seen.count)

    assert [seen.count for seen in pending] == [1, 3]
    assert pending[1].template == "could not read %s"


def test_only_errors_are_recorded_and_never_the_recorders_own_lines() -> None:
    log.warning("a warning is not a failure")
    logging.getLogger(recorder.__name__).error("a failure to store a failure")

    assert RECORDER.take() == []


def test_a_resolved_failure_that_happens_again_opens_again(database_url: str) -> None:
    def fails() -> None:
        raise KeyError(NAME)

    for _ in range(2):
        try:
            fails()
        except KeyError:
            log.exception("could not read")
    assert _store_pending(database_url) == 1

    async def resolve(db: AsyncSession) -> None:
        [row] = await service.listing(db)
        assert row.count == 2
        service.set_status(row, ServerErrorStatus.RESOLVED, now=utc_now() - timedelta(minutes=1))

    _in_database(database_url, resolve)
    assert [service.status_of(row) for row in _rows(database_url)] == [ServerErrorStatus.RESOLVED]

    try:
        fails()
    except KeyError:
        log.exception("could not read")
    _store_pending(database_url)

    [row] = _rows(database_url)
    assert row.count == 3
    assert service.status_of(row) is ServerErrorStatus.OPEN


def test_what_could_not_be_stored_is_kept_for_the_next_try(
    database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    log.error("the database is the failure")

    def unreachable() -> Any:
        raise ConnectionError("Neon is asleep")

    with caplog.at_level(logging.WARNING, logger=recorder.__name__):
        stored = asyncio.run(recorder.store_pending(unreachable))  # type: ignore[arg-type]

    assert stored == 0
    assert "Could not store 1 recorded failures" in caplog.text
    assert _store_pending(database_url) == 1
    assert [row.template for row in _rows(database_url)] == ["the database is the failure"]


# --- Purged when it stops happening --------------------------------------------------


def test_failures_not_seen_for_the_retention_are_deleted(database_url: str) -> None:
    now = utc_now()
    log.error("old")
    log.error("recent")
    old, recent = sorted(RECORDER.take(), key=lambda seen: seen.template)
    old.first_seen_at = old.last_seen_at = now - timedelta(days=91)

    async def store(db: AsyncSession) -> None:
        await service.store(db, [old, recent])

    _in_database(database_url, store)

    async def purge(db: AsyncSession) -> int:
        return await service.purge(db, retention_days=90, now=now)

    assert _in_database(database_url, purge) == 1
    assert [row.template for row in _rows(database_url)] == ["recent"]
