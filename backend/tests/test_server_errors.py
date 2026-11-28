"""The server's failures, kept without anybody's data (docs/operator.md).

What these hold: a failure is kept as its code (types, frames, the log line's
template, the route's template) and never as its data (the message, the logged
values, the address asked for); the same failure is counted rather than
repeated; a resolved one opens again when it happens again; nothing is lost
when the database is down; old ones are purged; and the operator is mailed,
once an hour at most, the code of a failure that is news and never its values.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from myntos.core.clock import utc_now
from myntos.core.config import get_settings
from myntos.domain.server_errors import recorder, service
from myntos.domain.server_errors.models import ServerError
from myntos.domain.server_errors.recorder import RECORDER, Occurrence, boil_down
from myntos.domain.server_errors.service import ServerErrorStatus
from tests.conftest import TEST_ORIGIN, RecordingMailer

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


def _store_pending(database_url: str, mailer: RecordingMailer | None = None) -> int:
    async def go() -> int:
        engine = create_async_engine(database_url)
        try:
            return await recorder.store_pending(
                async_sessionmaker(engine, expire_on_commit=False), mailer
            )
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


def test_a_request_validation_error_is_recorded_without_input_values(client: TestClient) -> None:
    """FastAPI 422 contract mismatches (e.g. missing Content-Type) land on /drift/ minus data."""
    app: FastAPI = client.app  # type: ignore[assignment]
    from pydantic import BaseModel

    class Toggle(BaseModel):
        enabled: bool

    @app.put("/api/v1/companies/{company_id}/_test_toggle")
    async def toggle(company_id: int, payload: Toggle) -> dict[str, bool]:
        return {"enabled": payload.enabled}

    # Simulate sending a JSON string without Content-Type: application/json
    response = client.put(
        "/api/v1/companies/4711/_test_toggle",
        content=f'{{"enabled": "{NAME}"}}',
        headers={"Origin": TEST_ORIGIN, "Content-Type": "text/plain"},
    )

    assert response.status_code == 422
    [kept] = RECORDER.take()
    assert kept.route == "PUT /api/v1/companies/{company_id}/_test_toggle"
    assert kept.exception == "fastapi.exceptions.RequestValidationError"
    assert kept.template == "request validation failed on %s (%s)"
    assert "4711" not in _kept(kept)
    assert NAME not in _kept(kept)


def test_handled_5xx_errors_are_recorded_and_4xx_refusals_stay_warnings(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """502/503 integration failures land on /drift/; 4xx domain refusals log WARNING only."""
    app: FastAPI = client.app  # type: ignore[assignment]
    from myntos.domain.peppol.errors import AccessPointRefusedError, NotRegisteredError

    @app.post("/api/v1/companies/{company_id}/_test_502")
    async def upstream_fail(company_id: int) -> None:
        raise AccessPointRefusedError(f"upstream refused {NAME} ({company_id})", problems=["bad"])

    @app.post("/api/v1/companies/{company_id}/_test_409")
    async def domain_refusal(company_id: int) -> None:
        raise NotRegisteredError(f"{NAME} ({company_id}) not registered")

    with caplog.at_level(logging.WARNING, logger="myntos.api.errors"):
        refused = client.post("/api/v1/companies/4711/_test_409", headers={"Origin": TEST_ORIGIN})
    assert refused.status_code == 409
    assert RECORDER.take() == []
    assert "handled 409 refusal (not_registered)" in caplog.text
    assert "4711" not in caplog.text
    assert NAME not in caplog.text

    failed = client.post("/api/v1/companies/4711/_test_502", headers={"Origin": TEST_ORIGIN})
    assert failed.status_code == 502
    [kept] = RECORDER.take()
    assert kept.route == "POST /api/v1/companies/{company_id}/_test_502"
    assert kept.exception == "myntos.domain.peppol.errors.AccessPointRefusedError"
    assert kept.template == "handled %d error (%s) on %s"
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


# --- The operator is told -------------------------------------------------------------

OPERATOR = "drift@example.test"


@pytest.fixture
def notify(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A deployment that mails the operator; the settings cache is cleared both ways."""
    monkeypatch.setenv("SERVER_ERROR_NOTIFY_TO", OPERATOR)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.usefixtures("notify")
def test_a_new_failure_mails_the_operator_its_code_and_not_its_values(
    database_url: str,
) -> None:
    outbox = RecordingMailer()
    try:
        raise KeyError(NAME)
    except KeyError:
        log.exception("could not read the books of %s", NAME)

    _store_pending(database_url, outbox)

    mail = outbox.only
    [row] = _rows(database_url)
    assert mail.to == OPERATOR
    assert f"nr. {row.id}:" in mail.text
    assert "«could not read the books of %s»" in mail.text
    assert "KeyError" in mail.text
    assert "/drift/" in mail.text
    assert NAME not in mail.text + mail.subject


@pytest.mark.usefixtures("notify")
def test_a_failure_that_is_still_open_is_not_news(database_url: str) -> None:
    outbox = RecordingMailer()
    log.error("the same thing")
    _store_pending(database_url, outbox)
    recorder.RECORDER.notified_at = None

    log.error("the same thing")
    _store_pending(database_url, outbox)

    assert len(outbox.sent) == 1
    assert [row.count for row in _rows(database_url)] == [2]


@pytest.mark.usefixtures("notify")
def test_one_mail_an_hour_however_much_breaks(database_url: str) -> None:
    outbox = RecordingMailer()
    log.error("first")
    _store_pending(database_url, outbox)
    log.error("second, a minute later")
    _store_pending(database_url, outbox)

    assert len(outbox.sent) == 1

    recorder.RECORDER.notified_at = utc_now() - recorder.QUIET
    log.error("third, after a quiet hour")
    _store_pending(database_url, outbox)

    assert len(outbox.sent) == 2
    assert "«third, after a quiet hour»" in outbox.sent[1].text
    assert "3 er åpne nå." in outbox.sent[1].text


@pytest.mark.usefixtures("notify")
def test_a_failure_that_opens_again_mails_again(database_url: str) -> None:
    outbox = RecordingMailer()
    log.error("fixed, or so it seemed")
    _store_pending(database_url, outbox)

    async def resolve(db: AsyncSession) -> None:
        [row] = await service.listing(db)
        service.set_status(row, ServerErrorStatus.RESOLVED, now=utc_now() - timedelta(minutes=1))

    _in_database(database_url, resolve)
    recorder.RECORDER.notified_at = None
    log.error("fixed, or so it seemed")
    _store_pending(database_url, outbox)

    assert len(outbox.sent) == 2


def test_nobody_is_mailed_unless_somebody_is_named(database_url: str) -> None:
    outbox = RecordingMailer()
    log.error("nobody to tell")

    assert _store_pending(database_url, outbox) == 1
    assert outbox.sent == []


@pytest.mark.usefixtures("notify")
def test_a_burst_names_a_few_and_counts_the_rest(database_url: str) -> None:
    outbox = RecordingMailer()
    for number in range(8):
        log.error(f"route {number} broke")  # noqa: G004 - eight templates on purpose

    _store_pending(database_url, outbox)

    mail = outbox.only
    assert mail.subject == "8 nye feil på serveren i Myntos"
    assert mail.text.count("nr. ") == 5
    assert "… og 3 til" in mail.text


def test_developer_mail_receives_error_notices_when_server_error_notify_to_is_unset(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SERVER_ERROR_NOTIFY_TO", "")
    monkeypatch.setenv("DEVELOPER_MAIL", "dev@example.test")
    get_settings.cache_clear()
    try:
        outbox = RecordingMailer()
        try:
            raise KeyError(NAME)
        except KeyError:
            log.exception("developer alert for %s", NAME)

        _store_pending(database_url, outbox)

        mail = outbox.only
        assert mail.to == "dev@example.test"
        assert "«developer alert for %s»" in mail.text
        assert "KeyError" in mail.text
        assert NAME not in mail.text + mail.subject
    finally:
        get_settings.cache_clear()


def test_server_error_notify_to_takes_precedence_over_developer_mail(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SERVER_ERROR_NOTIFY_TO", OPERATOR)
    monkeypatch.setenv("DEVELOPER_MAIL", "dev@example.test")
    get_settings.cache_clear()
    try:
        outbox = RecordingMailer()
        log.error("both addresses configured")

        _store_pending(database_url, outbox)

        assert outbox.only.to == OPERATOR
    finally:
        get_settings.cache_clear()


def test_blank_developer_mail_sends_nothing(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SERVER_ERROR_NOTIFY_TO", "   ")
    monkeypatch.setenv("DEVELOPER_MAIL", "   ")
    get_settings.cache_clear()
    try:
        outbox = RecordingMailer()
        log.error("blank addresses")

        assert _store_pending(database_url, outbox) == 1
        assert outbox.sent == []
    finally:
        get_settings.cache_clear()


# --- Every failure is logged (Løynd, HTTPException 5xx, integrations) -----------------

SECRET_LOYND_KEY = "loynd_sk_never_log_this_key"


def _mock_loynd_http(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
) -> None:
    import httpx

    from myntos.integrations import loynd

    real = httpx.AsyncClient

    def client(**options: Any) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(handler), **options)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(loynd, "_key", lambda: SECRET_LOYND_KEY)
    monkeypatch.setattr(loynd, "_models_cache", None)


def test_explicit_http_exception_5xx_is_recorded_and_4xx_stays_warning(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Explicit 502/503 HTTPExceptions land on /drift/; 4xx stay warnings without leaking detail."""
    from fastapi import HTTPException

    app: FastAPI = client.app  # type: ignore[assignment]

    @app.get("/api/v1/companies/{company_id}/_test_http_502")
    async def http_502(company_id: int) -> None:
        raise HTTPException(status_code=502, detail=f"{NAME} ({company_id})")

    @app.get("/api/v1/companies/{company_id}/_test_http_404")
    async def http_404(company_id: int) -> None:
        raise HTTPException(status_code=404, detail=f"{NAME} ({company_id})")

    with caplog.at_level(logging.WARNING, logger="myntos.api.errors"):
        missing = client.get(
            "/api/v1/companies/4711/_test_http_404", headers={"Origin": TEST_ORIGIN}
        )
    assert missing.status_code == 404
    assert RECORDER.take() == []
    assert "http 404 on GET /api/v1/companies/{company_id}/_test_http_404" in caplog.text
    assert "4711" not in caplog.text
    assert NAME not in caplog.text

    failed = client.get("/api/v1/companies/4711/_test_http_502", headers={"Origin": TEST_ORIGIN})
    assert failed.status_code == 502
    [kept] = RECORDER.take()
    assert kept.route == "GET /api/v1/companies/{company_id}/_test_http_502"
    assert kept.template == "http %d error on %s"
    assert "4711" not in _kept(kept)
    assert NAME not in _kept(kept)


async def test_loynd_complete_failures_are_logged_and_recorded_without_prompt_or_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """HTTP errors, network errors and broken shapes from Løynd all land on /drift/."""
    import httpx

    from myntos.integrations import loynd

    cases: list[Any] = [
        lambda request: httpx.Response(502, text=f"upstream body {NAME} {SECRET_LOYND_KEY}"),
        lambda request: (_ for _ in ()).throw(
            httpx.ConnectError("connection refused", request=request)
        ),
        lambda request: httpx.Response(200, json={"choices": []}),
    ]
    for handler in cases:
        RECORDER.take()
        caplog.clear()
        _mock_loynd_http(monkeypatch, handler)
        with (
            caplog.at_level(logging.ERROR, logger="myntos.integrations.loynd"),
            pytest.raises(loynd.LoyndUnavailableError) as raised,
        ):
            await loynd.complete(system=f"system for {NAME}", prompt=f"bilag for {NAME}")
        assert raised.value.logged is True
        [kept] = RECORDER.take()
        assert kept.logger == "myntos.integrations.loynd"
        assert NAME not in caplog.text + _kept(kept)
        assert SECRET_LOYND_KEY not in caplog.text + _kept(kept)


async def test_loynd_stream_and_error_frame_are_logged_and_recorded(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Both a failed stream status and an in-stream error frame are logged at ERROR."""
    import httpx

    from myntos.integrations import loynd

    handlers: list[Any] = [
        lambda request: httpx.Response(503, text=f"busy {SECRET_LOYND_KEY}"),
        lambda request: httpx.Response(
            200,
            text='data: {"error":{"message":"overloaded"}}\n\n',
            headers={"content-type": "text/event-stream"},
        ),
    ]
    for handler in handlers:
        RECORDER.take()
        caplog.clear()
        _mock_loynd_http(monkeypatch, handler)
        with (
            caplog.at_level(logging.ERROR, logger="myntos.integrations.loynd"),
            pytest.raises(loynd.LoyndUnavailableError) as raised,
        ):
            async for _ in loynd.stream(system="s", prompt=f"bilag for {NAME}"):
                pass
        assert raised.value.logged is True
        [kept] = RECORDER.take()
        assert kept.logger == "myntos.integrations.loynd"
        assert NAME not in caplog.text + _kept(kept)
        assert SECRET_LOYND_KEY not in caplog.text + _kept(kept)


async def test_loynd_paid_refusal_and_402_are_logged_even_when_falling_back_to_free(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Falling back to the free model never hides a paid-model outage or empty balance."""
    import httpx

    from myntos.integrations import loynd

    for status_code in (402, 503):
        RECORDER.take()
        caplog.clear()
        calls = 0

        def handler(request: httpx.Request, code: int = status_code) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(code, text=f"refused {SECRET_LOYND_KEY}")
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        _mock_loynd_http(monkeypatch, handler)
        model = loynd.Model(loynd.Level.MEDIUM)
        with caplog.at_level(logging.ERROR, logger="myntos.integrations.loynd"):
            answer = await model.complete(system="s", prompt=f"bilag for {NAME}")
        assert answer == "ok"
        assert model.fell_back is True
        [kept] = RECORDER.take()
        assert kept.logger == "myntos.integrations.loynd"
        assert NAME not in caplog.text + _kept(kept)
        assert SECRET_LOYND_KEY not in caplog.text + _kept(kept)


async def test_loynd_extract_image_and_models_failures_are_logged_without_filename_or_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """File extraction, vision and /v1/models failures all log ERROR with zero private data."""
    import httpx

    from myntos.integrations import loynd

    _mock_loynd_http(
        monkeypatch,
        lambda request: httpx.Response(502, text=f"bad gateway {SECRET_LOYND_KEY}"),
    )
    with (
        caplog.at_level(logging.ERROR, logger="myntos.integrations.loynd"),
        pytest.raises(loynd.LoyndUnavailableError) as extract_err,
    ):
        await loynd.extract_file(
            content=b"%PDF-1.4", filename=f"faktura-{NAME}.pdf", content_type="application/pdf"
        )
    assert extract_err.value.logged is True
    [extract_kept] = RECORDER.take()
    assert extract_kept.template == "Løynd could not read a document: %s"
    assert NAME not in caplog.text + _kept(extract_kept)
    assert SECRET_LOYND_KEY not in caplog.text + _kept(extract_kept)

    caplog.clear()
    with (
        caplog.at_level(logging.ERROR, logger="myntos.integrations.loynd"),
        pytest.raises(loynd.LoyndUnavailableError) as image_err,
    ):
        await loynd.ask_about_image(
            b"\x89PNG", content_type="image/png", question=f"Hvem solgte til {NAME}?"
        )
    assert image_err.value.logged is True
    [image_kept] = RECORDER.take()
    assert image_kept.template == "Løynd did not answer about an image: %s"
    assert NAME not in caplog.text + _kept(image_kept)
    assert SECRET_LOYND_KEY not in caplog.text + _kept(image_kept)

    caplog.clear()
    _mock_loynd_http(
        monkeypatch,
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("down", request=request)),
    )
    with caplog.at_level(logging.ERROR, logger="myntos.integrations.loynd"):
        assert await loynd.current_models() == {}
    [models_kept] = RECORDER.take()
    assert models_kept.template == "Løynd did not say what it serves: %s"


def test_graceful_fallback_route_still_logs_when_loynd_does_not_answer(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 200 graceful-fallback route still records an ERROR when Løynd fails to answer."""
    import httpx

    from myntos.integrations import loynd
    from tests.conftest import register, write
    from tests.test_cashflow_api import create_company

    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    company = create_company(client, organization_id)
    RECORDER.take()  # clear the expected test-stub brreg lookup during company creation

    # Case 1: real wire failure through loynd.complete -> logged once inside loynd.py
    _mock_loynd_http(monkeypatch, lambda request: httpx.Response(502, text="down"))
    with caplog.at_level(logging.ERROR, logger="myntos"):
        reply = write(
            client,
            "POST",
            f"/api/v1/companies/{company['id']}/faktura/linjeforslag",
            json={"text": f"10 timer utvikling til {NAME}"},
        )
    assert reply.status_code == 200
    assert any("Løynd" in n for n in reply.json()["notices"])
    recorded = RECORDER.take()
    assert len(recorded) == 1
    assert recorded[0].logger == "myntos.integrations.loynd"
    assert NAME not in caplog.text + _kept(recorded[0])

    # Case 2: an unlogged LoyndUnavailableError raised directly -> route fallback logs it
    caplog.clear()

    async def broken_complete(**_kwargs: Any) -> str:
        raise loynd.LoyndUnavailableError(f"broken for {NAME}")

    monkeypatch.setattr(loynd, "complete", broken_complete)
    with caplog.at_level(logging.ERROR, logger="myntos.api.v1.faktura"):
        reply2 = write(
            client,
            "POST",
            f"/api/v1/companies/{company['id']}/faktura/linjeforslag",
            json={"text": f"5 timer rådgivning til {NAME}"},
        )
    assert reply2.status_code == 200
    [fallback_kept] = RECORDER.take()
    assert fallback_kept.logger == "myntos.api.v1.faktura"
    assert fallback_kept.route == "POST /api/v1/companies/{company_id}/faktura/linjeforslag"
    assert NAME not in caplog.text + _kept(fallback_kept)


def test_failed_ki_analysis_run_logs_error_to_recorder(
    client: TestClient,
    database_url: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When Løynd fails during a background KI analysis run, the failure lands on /drift/."""
    from datetime import date

    from myntos.domain.analyse import jobs
    from myntos.domain.analyse.models import KiRun, RunKind, RunStatus
    from myntos.integrations.loynd import Level, LoyndUnavailableError
    from tests.conftest import register
    from tests.test_cashflow_api import THIS_YEAR, create_company, record

    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    company = create_company(client, organization_id)
    record(
        client,
        company["id"],
        type_code="varekjop",
        occurred_on=f"{THIS_YEAR}-03-10",
        gross_amount="1250.00",
        description=f"Utstyr fra {NAME}",
    )
    RECORDER.take()

    class DeadAsker:
        level = Level.FREE
        fell_back = False
        answered_model = None
        answered_location = None

        class _Zero:
            total = 0

        usage = _Zero()
        reported = _Zero()

        async def complete(self, **_kwargs: Any) -> str:
            raise LoyndUnavailableError(f"down for {NAME}")

        async def cost_nok(self) -> Decimal:
            return Decimal("0.000000")

    async def current() -> dict[str, Any]:
        return {}

    dead_ki = jobs.Ki(
        model=lambda _level: DeadAsker(),
        current=current,
        unavailable=(LoyndUnavailableError,),
    )

    async def run_and_check() -> tuple[str, str | None]:
        engine = create_async_engine(database_url)
        try:
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as db:
                row = KiRun(
                    company_id=company["id"],
                    kind=RunKind.MERKING.value,
                    status=RunStatus.QUEUED.value,
                    requested_level=Level.FREE.value,
                    period_from=date(THIS_YEAR, 1, 1),
                    period_to=date(THIS_YEAR, 12, 31),
                )
                db.add(row)
                await db.commit()
                run_id = row.id
            await jobs.execute(sessions, dead_ki, run_id)
            async with sessions() as db:
                finished = await db.get(KiRun, run_id)
                assert finished is not None
                return finished.status, finished.error_code
        finally:
            await engine.dispose()

    with caplog.at_level(logging.ERROR, logger="myntos.domain.analyse.jobs"):
        status_after, error_code = asyncio.run(run_and_check())
    assert status_after == RunStatus.FAILED.value
    assert error_code == jobs.KI_UNAVAILABLE
    [kept] = RECORDER.take()
    assert kept.logger == "myntos.domain.analyse.jobs"
    assert kept.template == "KI run %s failed (%s): Løynd did not answer (%s)"
    assert NAME not in caplog.text + _kept(kept)


async def test_brreg_failure_is_logged_and_recorded(caplog: pytest.LogCaptureFixture) -> None:
    """Enhetsregisteret outages log ERROR (never silent) and never leak the orgnr."""
    from myntos.integrations import brreg

    RECORDER.take()
    with (
        caplog.at_level(logging.ERROR, logger="myntos.integrations.brreg"),
        pytest.raises(brreg.RegisterUnavailableError),
    ):
        await brreg.lookup_unit("987654321")
    [kept] = RECORDER.take()
    assert kept.logger == "myntos.integrations.brreg"
    assert "987654321" not in caplog.text + _kept(kept)
