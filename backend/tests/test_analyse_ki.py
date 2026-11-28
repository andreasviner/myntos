"""KI reading the books in the background, against a fake Løynd (docs/analyse.md, WP5).

The books are built through the API the way a person builds them; the runs
are driven through :func:`myntos.domain.analyse.jobs.start_merking` and
:func:`~myntos.domain.analyse.jobs.execute`, with a fake model that answers
from the prompt it is shown, so each test can say exactly what Løynd did.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from myntos.core.clock import utc_now
from myntos.core.config import get_settings
from myntos.domain.analyse import freshness, items, jobs, tagging, vocabulary
from myntos.domain.analyse.items import ItemKind
from myntos.domain.analyse.models import (
    AnalysisSource,
    AnalysisStatus,
    Facet,
    KiLineTag,
    KiRun,
    KiTag,
    KiVoucherAnalysis,
    RunStatus,
    TagOrigin,
)
from myntos.domain.ledger.chart import (
    DEPRECIATION_JOURNAL_DESCRIPTION,
    DEPRECIATION_JOURNAL_ID,
    DEPRECIATION_JOURNAL_TYPE,
)
from myntos.domain.ledger.models import Company, Journal, Transaction
from myntos.integrations import loynd
from myntos.integrations.loynd import Level
from tests.conftest import register, write
from tests.test_cashflow_api import OTHER_ORGNR, THIS_YEAR, create_company, record

FRA = date(THIS_YEAR, 1, 1)
TIL = date(THIS_YEAR, 12, 31)

#: The hva the fake gives each side, unless a test says otherwise.
INCOME = "salg-av-varer"
COST = "varer-for-videresalg"


# --- A fake Løynd -------------------------------------------------------------------------


@dataclass
class Count:
    total: int = 0


def vouchers_in(prompt: str) -> list[dict[str, Any]]:
    """The voucher objects of a batch: every line after the data sentence."""
    _, _, batch = prompt.partition(tagging.DATA_INTRO + "\n")
    return [json.loads(line) for line in batch.splitlines() if line.strip()]


def head_of(prompt: str) -> str:
    return prompt.partition(tagging.DATA_INTRO)[0]


def tagged(voucher: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """A right answer for one voucher: each side's usual hva, no counterparty."""
    return {
        "bilag": voucher["bilag"],
        "oppsummering": "Et bilag",
        "linjer": [
            {
                "nr": line["nr"],
                "hva": INCOME if line["retning"] == "inntekt" else COST,
                "motpart": None,
                "flagg": [],
            }
            for line in voucher["linjer"]
        ],
        "nye_merker": [],
        **extra,
    }


Answerer = Callable[[list[dict[str, Any]], int], list[dict[str, Any] | str]]


def answer_all(vouchers: list[dict[str, Any]], _call: int) -> list[dict[str, Any] | str]:
    return [tagged(voucher) for voucher in vouchers]


@dataclass
class FakeModel:
    """Løynd at one level: answers each prompt with what ``answer`` makes of its vouchers."""

    answer: Answerer
    level: Level = Level.FREE
    fell_back: bool = False
    answered_model: str | None = None
    answered_location: str | None = None
    usage: Count = field(default_factory=Count)
    reported: Count = field(default_factory=Count)
    prompts: list[str] = field(default_factory=list)
    before: Callable[[int], Any] | None = None

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int,
        history: Sequence[tuple[str, str]] = (),
    ) -> str:
        del history
        assert system == tagging.SYSTEM
        assert max_tokens <= get_settings().ki_analysis_answer_tokens
        call = len(self.prompts)
        self.prompts.append(prompt)
        if self.before is not None:
            await self.before(call)
        self.answered_model = "loynd-lite"
        self.answered_location = "eu"
        self.reported.total += 100
        if self.level.is_paid:
            self.usage.total += 100
        found = self.answer(vouchers_in(prompt), call)
        return "\n".join(
            entry if isinstance(entry, str) else json.dumps(entry, ensure_ascii=False)
            for entry in found
        )

    async def cost_nok(self) -> Decimal:
        if not self.level.is_paid or self.usage.total <= 0:
            return Decimal("0.000000")
        return (Decimal(self.usage.total) * Decimal("0.000010")).quantize(Decimal("0.000001"))


class LoyndDownError(Exception):
    pass


def fake_ki(model: FakeModel) -> jobs.Ki:
    async def current() -> dict[str, loynd.Served]:
        return {"free": loynd.Served(model="loynd-lite", revision="RedHatAI/test-INT4")}

    def handle(level: Level) -> FakeModel:
        model.level = level
        return model

    return jobs.Ki(model=handle, current=current, unavailable=(LoyndDownError,))


# --- Books --------------------------------------------------------------------------------


@dataclass
class Books:
    client: TestClient
    company_id: int
    organization_id: int
    sessions: async_sessionmaker[AsyncSession]

    async def run(
        self, model: FakeModel, *, company_id: int | None = None, level: Level = Level.FREE
    ) -> KiRun | None:
        """Start a run and do its work to the end, as the request's background would."""
        background = BackgroundTasks()
        async with self.sessions() as db:
            company = await db.get(Company, company_id or self.company_id)
            assert company is not None
            run = await jobs.start_merking(
                db,
                self.sessions,
                fake_ki(model),
                company,
                fra=FRA,
                til=TIL,
                level=level,
                background=background,
            )
        await background()
        if run is None:
            return None
        return await self.reload(run.id)

    async def reload(self, run_id: int) -> KiRun:
        async with self.sessions() as db:
            found = await db.get(KiRun, run_id)
            assert found is not None
            return found

    async def analyses(self, company_id: int | None = None) -> dict[int, KiVoucherAnalysis]:
        async with self.sessions() as db:
            rows = await db.scalars(
                select(KiVoucherAnalysis).where(
                    KiVoucherAnalysis.company_id == (company_id or self.company_id)
                )
            )
            return {row.transaction_pk: row for row in rows.all()}

    async def hva_of(self, transaction_pk: int) -> list[str]:
        """The hva slug of each line of a voucher, in line order."""
        async with self.sessions() as db:
            rows = await db.execute(
                select(KiTag.slug)
                .join(KiLineTag, KiLineTag.tag_id == KiTag.id)
                .where(KiLineTag.transaction_pk == transaction_pk, KiTag.facet == Facet.HVA)
                .order_by(KiLineTag.line_pk)
            )
            return list(rows.scalars().all())

    async def set_ki(self, enabled: bool, company_id: int | None = None) -> None:
        async with self.sessions() as db:
            await db.execute(
                update(Company)
                .where(Company.id == (company_id or self.company_id))
                .values(ki_enabled=enabled)
            )
            await db.commit()


@pytest.fixture
async def books(client: TestClient, database_url: str) -> AsyncIterator[Books]:
    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    company = create_company(client, organization_id)
    engine = create_async_engine(database_url)

    @sa.event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    yield Books(
        client, company["id"], organization_id, async_sessionmaker(engine, expire_on_commit=False)
    )
    await engine.dispose()


def entry(
    books: Books, type_code: str, amount: str, day: int, company_id: int | None = None
) -> int:
    response = record(
        books.client,
        company_id or books.company_id,
        type_code=type_code,
        gross_amount=amount,
        occurred_on=date(THIS_YEAR, 3, day).isoformat(),
    )
    assert response.status_code == 201, response.text
    pk: int = response.json()["id"]
    return pk


def reverse(books: Books, transaction_pk: int) -> int:
    response = write(
        books.client,
        "POST",
        f"/api/v1/companies/{books.company_id}/cashflow/{transaction_pk}/reversal",
    )
    assert response.status_code == 201, response.text
    pk: int = response.json()["id"]
    return pk


async def into_depreciation(books: Books, transaction_pk: int) -> None:
    """Move a cost voucher into ``AVSKR``: as the year's depreciation looks to the reading."""
    async with books.sessions() as db:
        journal = Journal(
            company_id=books.company_id,
            journal_id=DEPRECIATION_JOURNAL_ID,
            description=DEPRECIATION_JOURNAL_DESCRIPTION,
            journal_type=DEPRECIATION_JOURNAL_TYPE,
        )
        db.add(journal)
        await db.flush()
        await db.execute(
            update(Transaction)
            .where(Transaction.id == transaction_pk)
            .values(journal_id=journal.id)
        )
        await db.commit()


@pytest.fixture
def one_per_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ki_analysis_batch_size", 1)


# --- Reading the answer -------------------------------------------------------------------


def test_an_answer_wrapped_in_prose_and_fences_still_gives_its_objects() -> None:
    text = (
        "Her er svaret:\n```json\n"
        '{"bilag": "1", "linjer": [{"nr": 1, "hva": "{ikke et objekt}"}]}\n'
        '{"bilag": "2", "oppsummering": "avbrutt'
        "\n```\n"
        '{"bilag": "3"} og så litt prat {ødelagt'
    )
    found = list(tagging.read_objects(text))
    assert [entry["bilag"] for entry in found] == ["1", "3"]
    assert found[0]["linjer"][0]["hva"] == "{ikke et objekt}"


def test_a_summary_loses_amounts_dates_and_links() -> None:
    cleaned = tagging.clean_summary(
        "Kjøp hos www.example.com 14. mars for 1 250,00 kr\x07 https://x.no/a"
    )
    assert "example" not in cleaned
    assert "x.no" not in cleaned
    assert "1 250" not in cleaned
    assert "mars" not in cleaned
    assert "\x07" not in cleaned


def _item(pk: int, bilag: str, lines: int) -> items.Item:
    return items.Item(
        transaction_pk=pk,
        bilag=bilag,
        journal="BANK",
        dato=date(THIS_YEAR, 3, 1),
        kind=ItemKind.KI,
        lines=tuple(
            items.ItemLine(
                nr=nr,
                line_pk=pk * 10 + nr,
                record_id=str(nr),
                account_number="4000",
                retning="kostnad",
                amount=Decimal("10.00"),
                has_party=False,
            )
            for nr in range(1, lines + 1)
        ),
        has_party=False,
        reverses_pk=None,
        canonical={"bilag": bilag},
    )


def test_batches_are_packed_to_the_answer_budget_and_never_repeat_a_number() -> None:
    many = [_item(pk, str(pk), 1) for pk in range(1, 41)]
    packed = list(tagging.batches(many, limit=20, budget=1024))
    # The probe: 20 one-line vouchers do not fit Løynd's 1024 tokens.
    assert all(tagging.answer_tokens(batch) <= 1024 for batch in packed)
    assert all(len(batch) <= 20 for batch in packed)
    assert [item for batch in packed for item in batch] == many
    assert len(packed[0]) < 20

    twice = [_item(1, "7", 1), _item(2, "7", 1), _item(3, "8", 1)]
    assert [
        [item.transaction_pk for item in batch]
        for batch in tagging.batches(twice, limit=20, budget=1024)
    ] == [[1], [2, 3]]

    assert tagging.max_tokens(many[:3], 1024) <= 1024
    assert tagging.max_tokens(many, 1024) == 1024


def test_a_cheaper_level_never_reads_a_dearer_answer_again() -> None:
    @dataclass
    class Row:
        status: str = AnalysisStatus.OK.value
        source: str = AnalysisSource.KI.value
        input_hash: str = "h"
        prompt_version: int = freshness.PROMPT_VERSION
        requested_level: str = "thinking"
        level: str = "thinking"
        model: str | None = "big"
        model_revision: str | None = None
        analysed_at: Any = field(default_factory=utc_now)
        failures: int = 0

    current = {"thinking": loynd.Served(model="bigger", revision=None)}
    at_free = freshness.verdict(Row(), input_hash="h", wanted="free", current=current)
    # A newer model behind the tier is no reason for a free run to touch it.
    assert not freshness.acts_on(at_free, person=False)
    assert not freshness.acts_on(at_free, person=True)
    changed = freshness.verdict(Row(), input_hash="other", wanted="free", current=current)
    assert freshness.acts_on(changed, person=False)
    assert isinstance(changed, freshness.Stale) and changed.level == "free"


# --- Runs over the books ------------------------------------------------------------------


async def test_a_run_over_the_books_fills_the_tables_and_a_second_asks_nothing(
    books: Books,
) -> None:
    sale = entry(books, "salg_hoy_sats", "1250.00", 2)
    purchase = entry(books, "varekjop", "500.00", 3)
    rent = entry(books, "husleie", "8000.00", 4)
    undone = reverse(books, purchase)
    depreciation = entry(books, "kontorrekvisita", "300.00", 5)
    await into_depreciation(books, depreciation)
    model = FakeModel(answer_all)

    run = await books.run(model)

    assert run is not None
    assert run.status == RunStatus.DONE
    assert run.error_code is None
    assert run.done == run.total == 5
    assert run.tokens_total == 100 * len(model.prompts)
    assert run.tokens_charged == 0  # the free level counts against no plan
    assert run.notices == []
    asked = {voucher["bilag"] for prompt in model.prompts for voucher in vouchers_in(prompt)}
    assert len(asked) == 3  # the reversal and the depreciation are not asked

    rows = await books.analyses()
    assert set(rows) == {sale, purchase, rent, undone, depreciation}
    for pk in (sale, purchase, rent):
        row = rows[pk]
        assert row.status == AnalysisStatus.OK
        assert row.source == AnalysisSource.KI
        assert row.summary == "Et bilag"
        assert (row.requested_level, row.level) == ("free", "free")
        assert (row.model, row.model_revision, row.location) == (
            "loynd-lite",
            "RedHatAI/test-INT4",
            "eu",
        )
        assert row.run_id == run.id
    assert await books.hva_of(sale) == [INCOME]
    assert await books.hva_of(purchase) == [COST]

    # The reversal mirrors its original, and no model was asked about it.
    mirrored = rows[undone]
    assert (mirrored.source, mirrored.model) == (AnalysisSource.REGNSKAP, None)
    assert mirrored.summary is not None and mirrored.summary.startswith("Tilbakeføring av bilag")
    assert await books.hva_of(undone) == [COST]
    # Depreciation is the software's own.
    written = rows[depreciation]
    assert (written.source, written.model, written.level) == (AnalysisSource.REGNSKAP, None, "free")
    assert written.summary == items.DEPRECIATION_SUMMARY
    assert await books.hva_of(depreciation) == [tagging.DEPRECIATION_HVA]

    again = FakeModel(answer_all)
    assert await books.run(again) is None
    assert again.prompts == []


async def test_a_second_start_while_one_runs_finds_that_one(books: Books) -> None:
    entry(books, "varekjop", "500.00", 3)
    background = BackgroundTasks()
    async with books.sessions() as db:
        company = await db.get(Company, books.company_id)
        assert company is not None
        first = await jobs.start_merking(
            db,
            books.sessions,
            fake_ki(FakeModel(answer_all)),
            company,
            fra=FRA,
            til=TIL,
            background=background,
        )
        second = await jobs.start_merking(
            db,
            books.sessions,
            fake_ki(FakeModel(answer_all)),
            company,
            fra=FRA,
            til=TIL,
            background=BackgroundTasks(),
        )
    assert first is not None and second is not None
    assert second.id == first.id
    async with books.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(KiRun)) == 1
        got = await jobs.get_run(db, books.company_id, first.id)
        assert got is not None and got.status == RunStatus.QUEUED
        assert await jobs.get_run(db, books.company_id + 1, first.id) is None
    await background()
    assert (await books.reload(first.id)).status == RunStatus.DONE


async def test_a_voucher_the_answer_missed_is_asked_alone_then_left_to_its_back_off(
    books: Books,
) -> None:
    kept = entry(books, "varekjop", "500.00", 3)
    missed = entry(books, "husleie", "8000.00", 4)
    async with books.sessions() as db:
        voucher = await db.get(Transaction, missed)
        assert voucher is not None
        bilag = voucher.transaction_id

    def without_one(vouchers: list[dict[str, Any]], _call: int) -> list[dict[str, Any] | str]:
        return [tagged(voucher) for voucher in vouchers if voucher["bilag"] != bilag]

    model = FakeModel(without_one)
    run = await books.run(model)

    assert run is not None and run.status == RunStatus.DONE
    assert len(model.prompts) == 2
    assert [voucher["bilag"] for voucher in vouchers_in(model.prompts[1])] == [bilag]
    rows = await books.analyses()
    assert rows[kept].status == AnalysisStatus.OK
    assert (rows[missed].status, rows[missed].failures, rows[missed].summary) == (
        AnalysisStatus.FAILED,
        1,
        None,
    )
    assert await books.hva_of(missed) == []
    # Inside its back-off, nothing to read: no run, no question.
    later = FakeModel(answer_all)
    assert await books.run(later) is None
    # After it, asked again, and a right answer replaces the failure.
    async with books.sessions() as db:
        await db.execute(
            update(KiVoucherAnalysis)
            .where(KiVoucherAnalysis.transaction_pk == missed)
            .values(analysed_at=utc_now() - timedelta(hours=2))
        )
        await db.commit()
    run = await books.run(later)
    assert run is not None and len(later.prompts) == 1
    assert (await books.analyses())[missed].status == AnalysisStatus.OK


async def test_malformed_answers_and_unknown_slugs_store_no_tag(books: Books) -> None:
    pk = entry(books, "varekjop", "500.00", 3)

    def nonsense(vouchers: list[dict[str, Any]], call: int) -> list[dict[str, Any] | str]:
        if call == 0:
            return ["Beklager, jeg kan ikke svare på det.", "```", "{ødelagt"]
        # An hva nobody proposed, and one on the wrong side of the books.
        return [
            tagged(vouchers[0], linjer=[{"nr": 1, "hva": "finnes-ikke", "flagg": []}]),
        ]

    model = FakeModel(nonsense)
    run = await books.run(model)

    assert run is not None and run.status == RunStatus.DONE
    row = (await books.analyses())[pk]
    assert row.status == AnalysisStatus.FAILED
    async with books.sessions() as db:
        slugs = set((await db.scalars(select(KiTag.slug))).all())
        assert "finnes-ikke" not in slugs
        assert await db.scalar(select(func.count()).select_from(KiLineTag)) == 0

    wrong_side = FakeModel(
        lambda vouchers, _call: [
            tagged(vouchers[0], linjer=[{"nr": 1, "hva": INCOME, "flagg": []}])
        ]
    )
    async with books.sessions() as db:
        await db.execute(
            update(KiVoucherAnalysis).values(analysed_at=utc_now() - timedelta(hours=2))
        )
        await db.commit()
    await books.run(wrong_side)
    assert (await books.analyses())[pk].status == AnalysisStatus.FAILED
    assert (await books.analyses())[pk].failures == 2


async def test_a_new_tag_is_stored_once_and_the_caps_hold(
    books: Books, monkeypatch: pytest.MonkeyPatch, one_per_batch: None
) -> None:
    monkeypatch.setattr(vocabulary, "MAX_NEW_HVA_PER_RUN", 1)
    first = entry(books, "varekjop", "500.00", 3)
    second = entry(books, "husleie", "8000.00", 4)

    def proposing(vouchers: list[dict[str, Any]], call: int) -> list[dict[str, Any] | str]:
        slug = f"nytt-merke-{call}"
        return [
            tagged(
                vouchers[0],
                linjer=[{"nr": 1, "hva": slug, "flagg": ["mulig-privat", "ukjent-flagg"]}],
                nye_merker=[
                    {
                        "fasett": "hva",
                        "merke": slug,
                        "navn": f"Nytt merke {call}",
                        "beskrivelse": "Når det brukes.",
                    }
                ],
            )
        ]

    model = FakeModel(proposing)
    run = await books.run(model)
    assert run is not None and run.status == RunStatus.DONE

    async with books.sessions() as db:
        own = (
            await db.scalars(
                select(KiTag).where(
                    KiTag.company_id == books.company_id, KiTag.origin == TagOrigin.KI.value
                )
            )
        ).all()
    assert [(tag.slug, tag.created_by_run_id) for tag in own] == [("nytt-merke-0", run.id)]
    # Newest first, so the later voucher proposed the tag; the earlier one's
    # proposal was over the cap, and its line got «annet» instead.
    assert await books.hva_of(second) == ["nytt-merke-0"]
    assert await books.hva_of(first) == [vocabulary.ANNET]
    async with books.sessions() as db:
        flags = (
            await db.scalars(
                select(KiTag.slug)
                .join(KiLineTag, KiLineTag.tag_id == KiTag.id)
                .where(KiTag.facet == Facet.FLAGG)
            )
        ).all()
    assert flags == ["mulig-privat", "mulig-privat"]  # the unknown flag is dropped
    # A tag added mid-run is appended: the second prompt begins with the first one's head.
    assert head_of(model.prompts[1]).startswith(head_of(model.prompts[0]).rstrip("\n"))
    assert "nytt-merke-0" in head_of(model.prompts[1])


async def test_every_batch_shares_the_prompt_prefix(books: Books, one_per_batch: None) -> None:
    for day in range(2, 6):
        entry(books, "varekjop", f"{day}00.00", day)
    model = FakeModel(answer_all)

    await books.run(model)

    assert len(model.prompts) == 4
    heads = {head_of(prompt) for prompt in model.prompts}
    assert len(heads) == 1
    assert [len(vouchers_in(prompt)) for prompt in model.prompts] == [1, 1, 1, 1]


async def test_switching_ki_off_mid_run_stops_before_the_next_batch(
    books: Books, one_per_batch: None
) -> None:
    for day in range(2, 5):
        entry(books, "varekjop", f"{day}00.00", day)

    async def switch_off(call: int) -> None:
        if call == 0:
            await books.set_ki(False)

    model = FakeModel(answer_all, before=switch_off)
    run = await books.run(model)

    assert run is not None
    assert (run.status, run.error_code) == (RunStatus.CANCELLED, jobs.KI_TURNED_OFF)
    assert len(model.prompts) == 1
    assert run.done == 1 and run.finished_at is not None
    assert len(await books.analyses()) == 1
    # With KI off, nothing starts.
    assert await books.run(FakeModel(answer_all)) is None


async def test_a_run_stopped_by_a_restart_resumes_where_it_stopped(
    books: Books, one_per_batch: None
) -> None:
    for day in range(2, 5):
        entry(books, "varekjop", f"{day}00.00", day)

    class RestartError(BaseException):
        """The process going away: not an error the run can catch."""

    def dies_second(vouchers: list[dict[str, Any]], call: int) -> list[dict[str, Any] | str]:
        if call == 1:
            raise RestartError
        return answer_all(vouchers, call)

    model = FakeModel(dies_second)
    background = BackgroundTasks()
    async with books.sessions() as db:
        company = await db.get(Company, books.company_id)
        assert company is not None
        run = await jobs.start_merking(
            db, books.sessions, fake_ki(model), company, fra=FRA, til=TIL, background=background
        )
    assert run is not None
    with pytest.raises(RestartError):
        await background()
    stopped = await books.reload(run.id)
    assert (stopped.status, stopped.done, stopped.total) == (RunStatus.RUNNING, 1, 3)

    after = FakeModel(answer_all)
    await jobs.resume(books.sessions, fake_ki(after))

    resumed = await books.reload(run.id)
    assert (resumed.status, resumed.done, resumed.total) == (RunStatus.DONE, 3, 3)
    assert len(after.prompts) == 2  # only what the stopped run had not stored
    assert resumed.tokens_total == 100 + 200
    assert len(await books.analyses()) == 3


async def test_loynd_not_answering_fails_the_run_and_pauses_the_next(books: Books) -> None:
    entry(books, "varekjop", "500.00", 3)

    def down(_vouchers: list[dict[str, Any]], _call: int) -> list[dict[str, Any] | str]:
        raise LoyndDownError

    run = await books.run(FakeModel(down))

    assert run is not None
    assert (run.status, run.error_code) == (RunStatus.FAILED, jobs.KI_UNAVAILABLE)
    assert await books.analyses() == {}
    assert await books.run(FakeModel(answer_all)) is None  # paused a while
    async with books.sessions() as db:
        await db.execute(
            update(KiRun).values(finished_at=utc_now() - jobs.UNAVAILABLE_PAUSE - timedelta(1))
        )
        await db.commit()
    assert await books.run(FakeModel(answer_all)) is not None


async def _bilag_of(books: Books, *pks: int) -> list[str]:
    async with books.sessions() as db:
        found = []
        for pk in pks:
            voucher = await db.get(Transaction, pk)
            assert voucher is not None
            found.append(voucher.transaction_id)
        return found


async def test_answers_validated_before_loynd_went_are_kept_and_never_asked_again(
    books: Books,
) -> None:
    # Three vouchers in one batch. The answer misses the middle one, and
    # Løynd is gone when it is asked alone: the other two were answered and
    # validate, so they are stored, and only the middle one is asked again.
    pks = [entry(books, "varekjop", f"{day}00.00", day) for day in (2, 3, 4)]
    middle: list[str] = []

    def misses_one_then_down(
        vouchers: list[dict[str, Any]], call: int
    ) -> list[dict[str, Any] | str]:
        if call == 1:
            raise LoyndDownError
        middle.append(vouchers[1]["bilag"])
        return [tagged(voucher) for voucher in vouchers if voucher["bilag"] != middle[0]]

    model = FakeModel(misses_one_then_down)
    run = await books.run(model)

    assert run is not None
    assert (run.status, run.error_code) == (RunStatus.FAILED, jobs.KI_UNAVAILABLE)
    assert len(model.prompts) == 2
    assert [voucher["bilag"] for voucher in vouchers_in(model.prompts[1])] == middle
    assert run.done == 2
    rows = await books.analyses()
    bilag = dict(zip(pks, await _bilag_of(books, *pks), strict=True))
    assert {bilag[pk] for pk in rows} == set(bilag.values()) - set(middle)
    assert all(row.status == AnalysisStatus.OK and row.run_id == run.id for row in rows.values())

    async with books.sessions() as db:
        await db.execute(
            update(KiRun).values(finished_at=utc_now() - jobs.UNAVAILABLE_PAUSE - timedelta(1))
        )
        await db.commit()
    after = FakeModel(answer_all)
    again = await books.run(after)
    assert again is not None and again.status == RunStatus.DONE
    assert [[voucher["bilag"] for voucher in vouchers_in(prompt)] for prompt in after.prompts] == [
        middle
    ]
    assert len(await books.analyses()) == 3


async def test_loynd_going_before_anything_validated_stores_nothing(books: Books) -> None:
    entry(books, "varekjop", "500.00", 3)

    def empty_then_down(_vouchers: list[dict[str, Any]], call: int) -> list[dict[str, Any] | str]:
        if call == 1:
            raise LoyndDownError
        return []

    run = await books.run(FakeModel(empty_then_down))

    assert run is not None
    assert (run.status, run.error_code, run.done) == (RunStatus.FAILED, jobs.KI_UNAVAILABLE, 0)
    assert await books.analyses() == {}


async def test_a_run_cancelled_while_its_batch_is_asked_keeps_that_batch(
    books: Books, one_per_batch: None
) -> None:
    for day in range(2, 5):
        entry(books, "varekjop", f"{day}00.00", day)
    started: list[int] = []

    async def cancel_first(call: int) -> None:
        if call == 0:
            async with books.sessions() as db:
                await jobs.cancel(db, books.company_id, started[0])
                await db.commit()

    model = FakeModel(answer_all, before=cancel_first)
    background = BackgroundTasks()
    async with books.sessions() as db:
        company = await db.get(Company, books.company_id)
        assert company is not None
        run = await jobs.start_merking(
            db, books.sessions, fake_ki(model), company, fra=FRA, til=TIL, background=background
        )
    assert run is not None
    started.append(run.id)
    await background()

    stopped = await books.reload(run.id)
    assert (stopped.status, stopped.error_code) == (RunStatus.CANCELLED, None)
    assert len(model.prompts) == 1
    assert stopped.done == 1
    rows = await books.analyses()
    assert len(rows) == 1
    assert next(iter(rows.values())).status == AnalysisStatus.OK


async def test_another_companys_tags_never_reach_a_prompt(books: Books) -> None:
    other = create_company(
        books.client, books.organization_id, orgnr=OTHER_ORGNR, name="Annen Drift AS"
    )["id"]
    entry(books, "varekjop", "500.00", 3)
    entry(books, "varekjop", "700.00", 3, company_id=other)

    def naming(vouchers: list[dict[str, Any]], _call: int) -> list[dict[str, Any] | str]:
        return [
            tagged(
                voucher,
                linjer=[{"nr": 1, "hva": "hemmelig-merke", "motpart": "Ola Nordmann", "flagg": []}],
                nye_merker=[
                    {
                        "fasett": "hva",
                        "merke": "hemmelig-merke",
                        "navn": "Hemmelig merke",
                        "beskrivelse": "Bare for det første selskapet.",
                    }
                ],
            )
            for voucher in vouchers
        ]

    await books.run(FakeModel(naming))
    async with books.sessions() as db:
        mine = set((await db.scalars(select(KiTag.label))).all())
    assert {"Hemmelig merke", "Ola Nordmann"} <= mine

    model = FakeModel(answer_all)
    run = await books.run(model, company_id=other)

    assert run is not None and run.status == RunStatus.DONE
    assert model.prompts
    for prompt in model.prompts:
        assert "hemmelig" not in prompt.casefold()
        assert "Ola Nordmann" not in prompt
    async with books.sessions() as db:
        theirs = (await db.scalars(select(KiTag.slug).where(KiTag.company_id == other))).all()
        assert "hemmelig-merke" not in theirs
        crossed = await db.scalar(
            select(func.count())
            .select_from(KiLineTag)
            .join(KiTag, KiTag.id == KiLineTag.tag_id)
            .where(KiLineTag.company_id != KiTag.company_id)
        )
        assert crossed == 0


async def test_a_lower_answer_never_replaces_a_dearer_one(books: Books) -> None:
    pk = entry(books, "varekjop", "500.00", 3)
    await books.run(FakeModel(answer_all))
    async with books.sessions() as db:
        await db.execute(
            update(KiVoucherAnalysis)
            .where(KiVoucherAnalysis.transaction_pk == pk)
            .values(level="thinking", requested_level="thinking", summary="Tenkt grundig")
        )
        await db.commit()

    # A free run finds nothing to read.
    assert await books.run(FakeModel(answer_all)) is None

    # And a paid run that was answered free after all keeps the dearer answer.
    async with books.sessions() as db:
        company = await db.get(Company, books.company_id)
        assert company is not None
        item = (await items.by_pk(db, books.company_id, [pk]))[0]
        vocab = tagging.Vocabulary(await vocabulary.load(db, books.company_id))
        answer = tagging.validate(tagged(json.loads(item.rendered)), item, vocab)
        assert answer is not None
        stamp = tagging.Stamp("thinking", "free", "loynd-lite", None, "eu")
        await tagging.store(
            db,
            company_id=books.company_id,
            run_id=None,
            outcomes=[
                tagging.Outcome(item, answer, stamp),
                tagging.Outcome(item, None, stamp),
            ],
            vocab=vocab,
        )
        await db.commit()
    row = (await books.analyses())[pk]
    assert (row.level, row.summary, row.status) == ("thinking", "Tenkt grundig", AnalysisStatus.OK)


async def test_a_reversal_of_a_voucher_read_later_mirrors_the_new_reading(books: Books) -> None:
    purchase = entry(books, "varekjop", "500.00", 3)
    undone = reverse(books, purchase)
    await books.run(FakeModel(answer_all))
    assert await books.hva_of(undone) == [COST]

    # The original's input changes (a written explanation, say): read again,
    # and the mirror follows it in the same run.
    async with books.sessions() as db:
        await db.execute(
            update(KiVoucherAnalysis)
            .where(KiVoucherAnalysis.transaction_pk == purchase)
            .values(input_hash="0" * 64)
        )
        await db.commit()

    def as_tools(vouchers: list[dict[str, Any]], _call: int) -> list[dict[str, Any] | str]:
        return [
            tagged(voucher, linjer=[{"nr": 1, "hva": "verktoy-og-utstyr", "flagg": []}])
            for voucher in vouchers
        ]

    await books.run(FakeModel(as_tools))
    assert await books.hva_of(purchase) == ["verktoy-og-utstyr"]
    assert await books.hva_of(undone) == ["verktoy-og-utstyr"]


# --- What Løynd says about who answered ----------------------------------------------------


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Løynd on the wire: a chat answer with headers and usage, and the two model routes."""
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "loynd-lite", "kind": "model"},
                        {"id": "medium", "kind": "tier", "resolves_to": "gpt-oss-120b"},
                    ]
                },
            )
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"model": "RedHatAI/Qwen-INT4"})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "1"}}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 5},
            },
            headers={loynd.MODEL_HEADER: "loynd-lite", loynd.LOCATION_HEADER: "eu"},
        )

    real = httpx.AsyncClient

    def client(**options: Any) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(handler), **options)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(loynd, "_key", lambda: "loynd_sk_test")
    monkeypatch.setattr(loynd, "_models_cache", None)
    return asked


async def test_a_handle_keeps_who_answered_and_every_token_reported(wire: list[str]) -> None:
    model = loynd.Model(Level.FREE, read_timeout=120.0)
    await model.complete(system="s", prompt="p")
    await model.complete(system="s", prompt="p")

    assert (model.answered_model, model.answered_location) == ("loynd-lite", "eu")
    assert model.reported.total == 70
    assert model.usage.total == 0  # the free level is charged to nobody


async def test_current_models_names_the_model_behind_each_level(
    wire: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        loynd,
        "model_name",
        lambda level: {Level.FREE: "loynd-lite", Level.MEDIUM: "medium", Level.THINKING: "high"}[
            level
        ],
    )

    served = await loynd.current_models()

    assert served == {
        "free": loynd.Served(model="loynd-lite", revision="RedHatAI/Qwen-INT4"),
        "medium": loynd.Served(model="gpt-oss-120b", revision=None),
    }
    asked = len(wire)
    assert await loynd.current_models() == served
    assert len(wire) == asked  # cached


async def test_current_models_is_empty_when_loynd_cannot_say(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loynd, "_models_cache", None)
    monkeypatch.setattr(loynd, "_key", lambda: None)
    assert await loynd.current_models() == {}
