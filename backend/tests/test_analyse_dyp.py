"""WP6 («Dyp KI-analyse»): stacked charts, recurring costs, packet, number check, runs & reports.

Every test uses fake models only (never live Løynd) and exercises both domain
invariants (rule 2 number check, rule 6 zero-token cache reuse, rule 10 KI off,
never-downgrade, live chart hydration) and HTTP API contracts.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from myntos.api.analyse_ki import get_analysis_ki
from myntos.api.v1.saft_innlesing import get_import_sessions
from myntos.core.clock import utc_now
from myntos.domain.analyse import figures, jobs, packet, report, tagging
from myntos.domain.analyse.models import KiRun, RunKind, RunStatus
from myntos.domain.ki.models import KiPeriod, KiPeriodStatus
from myntos.domain.ledger.models import Company
from myntos.integrations import loynd
from myntos.integrations.loynd import Level
from tests.conftest import join_company, register, write
from tests.test_analyse_ki import (
    Books,
    Count,
    LoyndDownError,
    answer_all,
    vouchers_in,
)
from tests.test_cashflow_api import OTHER_ORGNR, THIS_YEAR, create_company, record

FRA = date(THIS_YEAR, 1, 1)
TIL = date(THIS_YEAR, 12, 31)


@pytest.fixture
async def sessions(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)

    @sa.event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def books(client: TestClient, sessions: async_sessionmaker[AsyncSession]) -> Books:
    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    company = create_company(client, organization_id)
    return Books(client, company["id"], organization_id, sessions)


@dataclass
class DeepFakeModel:
    """Fake Løynd handling both ``merking`` JSONL batches and ``dyp`` JSON reports."""

    report_answers: list[str]
    level: Level = Level.FREE
    fell_back: bool = False
    answered_model: str | None = None
    answered_location: str | None = None
    usage: Count = field(default_factory=Count)
    reported: Count = field(default_factory=Count)
    tagging_calls: int = 0
    report_calls: list[tuple[str, Sequence[tuple[str, str]]]] = field(default_factory=list)
    fail_report: bool = False

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int,
        history: Sequence[tuple[str, str]] = (),
    ) -> str:
        del max_tokens
        self.answered_model = f"fake-{self.level.value}"
        self.answered_location = "no-local" if self.level is Level.FREE else "eu"
        if system == tagging.SYSTEM:
            self.tagging_calls += 1
            self.reported.total += 100
            if self.level.is_paid:
                self.usage.total += 100
            found = answer_all(vouchers_in(prompt), self.tagging_calls - 1)
            return "\n".join(
                entry if isinstance(entry, str) else json.dumps(entry, ensure_ascii=False)
                for entry in found
            )
        assert system == report.SYSTEM
        idx = len(self.report_calls)
        self.report_calls.append((prompt, list(history)))
        if self.fail_report:
            raise LoyndDownError("model down")
        self.reported.total += 500
        if self.level.is_paid:
            self.usage.total += 500
        return self.report_answers[min(idx, len(self.report_answers) - 1)]

    async def cost_nok(self) -> Decimal:
        if not self.level.is_paid or self.usage.total <= 0:
            return Decimal("0.000000")
        return Decimal("1.500000")


def deep_ki(model: DeepFakeModel) -> jobs.Ki:
    async def current() -> dict[str, loynd.Served]:
        return {
            "free": loynd.Served(model="fake-free", revision="rev-1"),
            "medium": loynd.Served(model="fake-medium", revision=None),
            "thinking": loynd.Served(model="fake-thinking", revision=None),
        }

    def handle(level: Level) -> DeepFakeModel:
        # Separate handle per level so free tagging never mixes into paid usage.
        if level is Level.FREE and model.level is not Level.FREE:
            return DeepFakeModel(
                report_answers=model.report_answers,
                level=Level.FREE,
            )
        model.level = level
        return model

    return jobs.Ki(model=handle, current=current, unavailable=(LoyndDownError,))


def _add_party(client: TestClient, company_id: int, name: str) -> int:
    res = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/parties",
        json={"party_type": "leverandor", "name": name},
    )
    assert res.status_code == 201, res.text
    return int(res.json()["id"])


async def _add_paid_plan(
    sessions: async_sessionmaker[AsyncSession],
    company_id: int,
    *,
    value_allowance_nok: Decimal = Decimal("36.00"),
    value_used_nok: Decimal = Decimal("0.000000"),
) -> int:
    now = utc_now()
    async with sessions() as db:
        period = KiPeriod(
            company_id=company_id,
            plan="start",
            status=KiPeriodStatus.PAID.value,
            reference=f"ref-{company_id}-{value_allowance_nok}",
            mollie_mode="test",
            amount=Decimal("61.25"),
            vat_amount=Decimal("12.25"),
            currency="NOK",
            value_allowance_nok=value_allowance_nok,
            value_used_nok=value_used_nok,
            tokens_used=0,
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=29),
            paid_at=now - timedelta(days=1),
        )
        db.add(period)
        await db.commit()
        return int(period.id)


async def _company(sessions: async_sessionmaker[AsyncSession], company_id: int) -> Company:
    async with sessions() as db:
        row = await db.get(Company, company_id)
        assert row is not None
        return row


# --- WP6 datasets: hva_per_maned and faste_kostnader ------------------------------------


@pytest.mark.anyio
async def test_faste_kostnader_detects_three_consecutive_months_with_ki_off(
    books: Books,
) -> None:
    """Three consecutive similar monthly payments to one supplier appear; erratic ones do not."""
    telenor_id = _add_party(books.client, books.company_id, "Telenor Norge AS")
    erratic_id = _add_party(books.client, books.company_id, "Variabel Leverandør AS")

    # Telenor: 4 consecutive months around 1 000 kr (within 20% tolerance)
    for month, amount in ((1, "1000.00"), (2, "1050.00"), (3, "980.00"), (4, "1020.00")):
        record(
            books.client,
            books.company_id,
            type_code="kontorrekvisita",
            gross_amount=amount,
            occurred_on=f"{THIS_YEAR}-0{month}-15",
            description="Mobilabonnement",
            party_id=telenor_id,
        )
    # Variabel: wild jumps (100 kr, 5 000 kr, 100 kr), not recurring
    for month, amount in ((1, "100.00"), (2, "5000.00"), (3, "100.00")):
        record(
            books.client,
            books.company_id,
            type_code="kontorrekvisita",
            gross_amount=amount,
            occurred_on=f"{THIS_YEAR}-0{month}-20",
            description="Diverse",
            party_id=erratic_id,
        )

    # Switch KI off: faste_kostnader has needs_tags=False and must still work.
    off = write(
        books.client,
        "PATCH",
        f"/api/v1/companies/{books.company_id}",
        json={"ki_enabled": False},
    )
    assert off.status_code == 200, off.text

    res = books.client.get(
        f"/api/v1/companies/{books.company_id}/analyse/datasett/faste_kostnader",
        params={"fra": f"{THIS_YEAR}-01-01", "til": f"{THIS_YEAR}-06-30"},
    )
    assert res.status_code == 200, res.text
    chart = res.json()
    assert chart["view"] == "tabell"
    assert chart["x"]["labels"] == ["Telenor Norge AS (4 mnd.)"]
    assert len(chart["series"]) == 2
    assert chart["series"][0]["key"] == "snitt"
    assert chart["series"][1]["key"] == "totalt"


@pytest.mark.anyio
async def test_hva_per_maned_stacks_monthly_costs_and_refuses_when_ki_off(
    books: Books,
) -> None:
    record(
        books.client,
        books.company_id,
        type_code="varekjop",
        gross_amount="12500.00",
        occurred_on=f"{THIS_YEAR}-01-10",
        description="Varekjøp jan",
    )
    record(
        books.client,
        books.company_id,
        type_code="varekjop",
        gross_amount="25000.00",
        occurred_on=f"{THIS_YEAR}-03-10",
        description="Varekjøp mar",
    )
    model = DeepFakeModel(report_answers=[])
    ki = deep_ki(model)
    async with books.sessions() as db:
        comp = await db.get(Company, books.company_id)
        assert comp is not None
        run = await jobs.start_merking(db, books.sessions, ki, comp, fra=FRA, til=TIL)
        assert run is not None
    await jobs.execute(books.sessions, ki, run.id)

    res = books.client.get(
        f"/api/v1/companies/{books.company_id}/analyse/datasett/hva_per_maned",
        params={"fra": f"{THIS_YEAR}-01-01", "til": f"{THIS_YEAR}-03-31"},
    )
    assert res.status_code == 200, res.text
    chart = res.json()
    assert chart["view"] == "kolonner"
    assert chart["stacked"] is True
    assert chart["x"]["labels"] == [f"{THIS_YEAR}-01", f"{THIS_YEAR}-02", f"{THIS_YEAR}-03"]
    # Net of 25% VAT: 10 000 + 0 + 20 000 = 30 000
    assert Decimal(chart["total"]) == Decimal("30000.00")
    assert chart["series"][0]["values"] == ["10000.00", "0.00", "20000.00"]

    # topp > 5 is refused (max 6 stacked series including Andre)
    too_many = books.client.get(
        f"/api/v1/companies/{books.company_id}/analyse/datasett/hva_per_maned",
        params={"fra": f"{THIS_YEAR}-01-01", "til": f"{THIS_YEAR}-03-31", "topp": 6},
    )
    assert too_many.status_code == 422
    assert too_many.json()["code"] == "chart_request"

    # KI off refuses needs_tags dataset
    write(
        books.client,
        "PATCH",
        f"/api/v1/companies/{books.company_id}",
        json={"ki_enabled": False},
    )
    refused = books.client.get(
        f"/api/v1/companies/{books.company_id}/analyse/datasett/hva_per_maned",
        params={"fra": f"{THIS_YEAR}-01-01", "til": f"{THIS_YEAR}-03-31"},
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "ki_turned_off"


# --- Number check & report validation ---------------------------------------------------


@pytest.mark.anyio
async def test_number_check_retries_once_and_drops_still_invented_paragraphs(
    books: Books,
) -> None:
    """A model that invents a number gets one chance to fix it; stubborn paragraphs are dropped."""
    record(
        books.client,
        books.company_id,
        type_code="salg_hoy_sats",
        gross_amount="125000.00",
        occurred_on=f"{THIS_YEAR}-02-15",
        description="Konsulenthonorar",
    )
    record(
        books.client,
        books.company_id,
        type_code="varekjop",
        gross_amount="50000.00",
        occurred_on=f"{THIS_YEAR}-02-20",
        description="Varekjøp",
    )
    async with books.sessions() as db:
        comp = await db.get(Company, books.company_id)
        assert comp is not None
        pkt = await packet.build(
            db,
            company=comp,
            chosen=figures.period(date(THIS_YEAR, 1, 1), date(THIS_YEAR, 3, 31)),
            prompt_version=report.PROMPT_VERSION,
            schema_version=report.SCHEMA_VERSION,
        )

    # First answer invents "999 000 kr" in section 2 and uses an invalid dataset in section 1.
    first_answer = json.dumps(
        {
            "overskrift": "Solid overskudd på 60 000 kr",
            "sammendrag": "Inntektene var 100 000 kr og kostnadene 40 000 kr.",
            "avsnitt": [
                {
                    "tittel": "Inntekter og kostnader",
                    "tekst": "Resultatet før skatt ble 60 000 kr \u2014 et godt kvartal.",
                    "diagram": {
                        "datasett": "finnes_ikke",
                        "visning": "smultring",
                        "periode": "valgt",
                    },
                },
                {
                    "tittel": "Fantasiavsnitt",
                    "tekst": "Du brukte 999 000 kr på raketter.",
                    "diagram": None,
                },
            ],
            "forslag": [
                {"tittel": "Følg opp kostnadene", "tekst": "Kostnadene var 40 000 kr."},
                {"tittel": "Oppdiktede prosenter", "tekst": "Kutt med 77 % neste år."},
            ],
        },
        ensure_ascii=False,
    )
    # Retry still insists on "888 000 kr" in section 2, fixes the suggestion to real 40 000 kr.
    retry_answer = json.dumps(
        {
            "overskrift": "Solid overskudd på 60 000 kr",
            "sammendrag": "Inntektene var 100 000 kr og kostnadene 40 000 kr.",
            "avsnitt": [
                {
                    "tittel": "Inntekter og kostnader",
                    "tekst": "Resultatet før skatt ble 60 000 kr \u2014 et godt kvartal.",
                    "diagram": {
                        "datasett": "finnes_ikke",
                        "visning": "smultring",
                        "periode": "valgt",
                    },
                },
                {
                    "tittel": "Fantasiavsnitt",
                    "tekst": "Du brukte 888 000 kr på raketter.",
                    "diagram": None,
                },
            ],
            "forslag": [
                {"tittel": "Følg opp kostnadene", "tekst": "Kostnadene var 40 000 kr."},
            ],
        },
        ensure_ascii=False,
    )

    model = DeepFakeModel(report_answers=[first_answer, retry_answer])
    validated = await report.generate(model.complete, pkt)

    # Exactly two calls: initial + one follow-up with conversation history.
    assert len(model.report_calls) == 2
    retry_prompt, history = model.report_calls[1]
    assert "999 000" in retry_prompt
    assert len(history) == 1

    # Section 1 kept (with invalid diagram dropped and em-dash stripped); section 2 dropped!
    assert len(validated.avsnitt) == 1
    assert validated.avsnitt[0].tittel == "Inntekter og kostnader"
    assert validated.avsnitt[0].diagram is None
    assert "\u2014" not in validated.avsnitt[0].tekst
    assert validated.dropped_paragraphs == 1
    assert len(validated.forslag) == 1


# --- Full HTTP API workflow, caching, upgrades, cancellation, staleness, isolation ------


@pytest.mark.anyio
async def test_dyp_run_end_to_end_cache_reuse_upgrade_and_staleness(books: Books) -> None:
    record(
        books.client,
        books.company_id,
        type_code="salg_hoy_sats",
        gross_amount="125000.00",
        occurred_on=f"{THIS_YEAR}-03-10",
        description="Prosjektleveranse",
    )
    record(
        books.client,
        books.company_id,
        type_code="varekjop",
        gross_amount="25000.00",
        occurred_on=f"{THIS_YEAR}-03-12",
        description="Innkjøp av varer",
    )

    valid_report = json.dumps(
        {
            "overskrift": "Overskudd på 80 000 kr",
            "sammendrag": "Inntektene var 100 000 kr og kostnadene 20 000 kr.",
            "avsnitt": [
                {
                    "tittel": "Kostnadsbilde",
                    "tekst": "Kostnadene utgjorde 20 000 kr i perioden.",
                    "diagram": {
                        "datasett": "kostnader_per_hva",
                        "visning": "smultring",
                        "periode": "valgt",
                    },
                }
            ],
            "forslag": [
                {"tittel": "Hold god margin", "tekst": "Resultatet ble 80 000 kr."},
            ],
        },
        ensure_ascii=False,
    )

    model = DeepFakeModel(report_answers=[valid_report])
    books.client.app.dependency_overrides[get_analysis_ki] = lambda: deep_ki(model)  # type: ignore[attr-defined]
    books.client.app.dependency_overrides[get_import_sessions] = lambda: books.sessions  # type: ignore[attr-defined]

    # 1. Start a free deep analysis -> 202 with kjoring (BackgroundTasks runs in TestClient).
    started = write(
        books.client,
        "POST",
        f"/api/v1/companies/{books.company_id}/analyse/kjoringer",
        json={
            "type": "dyp",
            "fra": f"{THIS_YEAR}-01-01",
            "til": f"{THIS_YEAR}-06-30",
            "niva": "free",
        },
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["kjoring"]["id"]
    assert started.json()["rapport"] is None

    # Poll run -> done with report_id.
    polled = books.client.get(f"/api/v1/companies/{books.company_id}/analyse/kjoringer/{run_id}")
    assert polled.status_code == 200, polled.text
    run_body = polled.json()
    assert run_body["status"] == "done"
    report_id = run_body["report_id"]
    assert isinstance(report_id, int)
    assert len(model.report_calls) == 1

    # Fetch hydrated report -> chart filled live from the books!
    fetched = books.client.get(
        f"/api/v1/companies/{books.company_id}/analyse/rapporter/{report_id}"
    )
    assert fetched.status_code == 200, fetched.text
    rep = fetched.json()
    assert rep["overskrift"] == "Overskudd på 80 000 kr"
    assert rep["niva"] == "free"
    assert rep["utdatert"] is None
    assert len(rep["avsnitt"]) == 1
    assert rep["avsnitt"][0]["diagram"]["dataset"]["name"] == "kostnader_per_hva"
    assert Decimal(rep["avsnitt"][0]["diagram"]["total"]) == Decimal("20000.00")

    # 2. Second press with same period & level -> 200 cached report, ZERO new model calls!
    again = write(
        books.client,
        "POST",
        f"/api/v1/companies/{books.company_id}/analyse/kjoringer",
        json={
            "type": "dyp",
            "fra": f"{THIS_YEAR}-01-01",
            "til": f"{THIS_YEAR}-06-30",
            "niva": "free",
        },
    )
    assert again.status_code == 200, again.text
    assert again.json()["rapport"]["id"] == report_id
    assert again.json()["kjoring"] is None
    assert len(model.report_calls) == 1

    # 3. Asking for paid level without a plan resolves to free -> hits cache (200, 0 tokens)!
    no_plan = write(
        books.client,
        "POST",
        f"/api/v1/companies/{books.company_id}/analyse/kjoringer",
        json={
            "type": "dyp",
            "fra": f"{THIS_YEAR}-01-01",
            "til": f"{THIS_YEAR}-06-30",
            "niva": "thinking",
        },
    )
    assert no_plan.status_code == 200, no_plan.text
    assert no_plan.json()["rapport"]["id"] == report_id
    assert len(model.report_calls) == 1

    # 4. With an active paid plan, asking for thinking upgrades (202), spends paid tokens,
    #    and afterwards asking for free reuses the thinking report (never downgrades)!
    period_id = await _add_paid_plan(books.sessions, books.company_id)
    upgraded = write(
        books.client,
        "POST",
        f"/api/v1/companies/{books.company_id}/analyse/kjoringer",
        json={
            "type": "dyp",
            "fra": f"{THIS_YEAR}-01-01",
            "til": f"{THIS_YEAR}-06-30",
            "niva": "thinking",
        },
    )
    assert upgraded.status_code == 202, upgraded.text
    up_run_id = upgraded.json()["kjoring"]["id"]
    up_polled = books.client.get(
        f"/api/v1/companies/{books.company_id}/analyse/kjoringer/{up_run_id}"
    ).json()
    assert up_polled["status"] == "done"
    thinking_report_id = up_polled["report_id"]
    assert thinking_report_id != report_id
    assert len(model.report_calls) == 2

    # Paid usage was recorded on the plan month (NOK value + internal token telemetry).
    async with books.sessions() as db:
        plan_row = await db.get(KiPeriod, period_id)
        assert plan_row is not None
        assert Decimal(str(plan_row.value_used_nok)) == Decimal("1.500000")
        assert plan_row.tokens_used == 500

    # Asking at free now reuses the thinking report (200, 0 new calls).
    reuse_higher = write(
        books.client,
        "POST",
        f"/api/v1/companies/{books.company_id}/analyse/kjoringer",
        json={
            "type": "dyp",
            "fra": f"{THIS_YEAR}-01-01",
            "til": f"{THIS_YEAR}-06-30",
            "niva": "free",
        },
    )
    assert reuse_higher.status_code == 200
    assert reuse_higher.json()["rapport"]["id"] == thinking_report_id
    assert len(model.report_calls) == 2

    # 5. List reports newest first.
    listed = books.client.get(f"/api/v1/companies/{books.company_id}/analyse/rapporter")
    assert listed.status_code == 200
    items = listed.json()["rapporter"]
    assert [r["id"] for r in items] == [thinking_report_id, report_id]
    assert items[0]["utdatert"] is None

    # 6. Post a new voucher in the period -> utdatert reports 1 new voucher!
    record(
        books.client,
        books.company_id,
        type_code="kontorrekvisita",
        gross_amount="1250.00",
        occurred_on=f"{THIS_YEAR}-04-01",
        description="Papir og toner",
    )
    stale_list = books.client.get(f"/api/v1/companies/{books.company_id}/analyse/rapporter").json()[
        "rapporter"
    ]
    assert stale_list[0]["utdatert"] == {"nye_bilag": 1}

    # 7. Switching KI off refuses starting a new run (409) and hides tag charts on read (rule 10).
    write(
        books.client,
        "PATCH",
        f"/api/v1/companies/{books.company_id}",
        json={"ki_enabled": False},
    )
    refused = write(
        books.client,
        "POST",
        f"/api/v1/companies/{books.company_id}/analyse/kjoringer",
        json={"type": "dyp", "fra": f"{THIS_YEAR}-01-01", "til": f"{THIS_YEAR}-06-30"},
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "ki_turned_off"

    read_off = books.client.get(
        f"/api/v1/companies/{books.company_id}/analyse/rapporter/{thinking_report_id}"
    )
    assert read_off.status_code == 200
    # kostnader_per_hva has needs_tags=True, so while KI is off the chart is omitted.
    assert read_off.json()["avsnitt"][0]["diagram"] is None


@pytest.mark.anyio
async def test_cancel_analysis_run_and_cross_company_isolation(books: Books) -> None:
    # Create a queued run directly without executing it, then cancel via API.
    async with books.sessions() as db:
        run = KiRun(
            company_id=books.company_id,
            kind=RunKind.DYP.value,
            status=RunStatus.QUEUED.value,
            requested_level=Level.FREE.value,
            period_from=FRA,
            period_to=TIL,
            step=jobs.STEP_WAITING,
            notices=[],
        )
        db.add(run)
        await db.commit()
        run_id = int(run.id)

    cancelled = write(
        books.client,
        "POST",
        f"/api/v1/companies/{books.company_id}/analyse/kjoringer/{run_id}/avbryt",
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"

    # Sibling company cannot see or cancel the run or list reports.
    sibling = create_company(books.client, books.organization_id, orgnr=OTHER_ORGNR)
    other_cancel = write(
        books.client,
        "POST",
        f"/api/v1/companies/{sibling['id']}/analyse/kjoringer/{run_id}/avbryt",
    )
    assert other_cancel.status_code == 404
    assert other_cancel.json()["code"] == "analysis_run_not_found"

    other_report = books.client.get(f"/api/v1/companies/{sibling['id']}/analyse/rapporter/99999")
    assert other_report.status_code == 404
    assert other_report.json()["code"] == "analysis_report_not_found"


def test_reader_role_may_list_reports_but_not_start_or_cancel_runs(client: TestClient) -> None:
    """D4: ledger:write starts/cancels runs; ledger:read lists and reads finished reports."""
    owner = register(client, "eier@example.no", full_name="Eier")
    org_id = owner["memberships"][0]["organization_id"]
    company = create_company(client, org_id)

    join_company(client, company["id"], email="leser@example.no", role="viewer", full_name="Leser")

    # Reader can read reports list (200), cannot start (403) or cancel (403).
    listed = client.get(f"/api/v1/companies/{company['id']}/analyse/rapporter")
    assert listed.status_code == 200
    assert listed.json()["rapporter"] == []

    start = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/analyse/kjoringer",
        json={"type": "dyp"},
    )
    assert start.status_code == 403

    cancel = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/analyse/kjoringer/1/avbryt",
    )
    assert cancel.status_code == 403
