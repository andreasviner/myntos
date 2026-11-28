"""The Analyse page with KI's tags: the splits by ``hva`` (docs/analyse.md, WP5b).

KI on is enough for the page to read the books (D2): the first visit starts
a free ``merking`` run in the background and answers at once from what is
read, and a side switches from its accounts to its ``hva`` once 90 % of its
amount is read (D5). With KI off nothing of this happens, whatever is stored
(rule 10). The runs are WP5's, answered by the fake Løynd of
``tests.test_analyse_ki``; the real one is never asked.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from myntos.api.analyse_ki import get_analysis_ki
from myntos.core.clock import utc_now
from myntos.domain.analyse import datasets, figures, jobs, sentences
from myntos.domain.analyse.figures import Part, Period
from myntos.domain.analyse.models import (
    Facet,
    KiLineTag,
    KiRun,
    KiTag,
    KiVoucherAnalysis,
    RunKind,
    RunStatus,
)
from myntos.domain.ledger.models import Company
from myntos.integrations.loynd import Level
from tests.conftest import agent_header, register
from tests.test_analyse_ki import (
    COST,
    INCOME,
    Answerer,
    Books,
    FakeModel,
    LoyndDownError,
    answer_all,
    fake_ki,
    tagged,
)
from tests.test_arsoppgjor_api import LAST_YEAR, _company, _party
from tests.test_cashflow_api import OTHER_ORGNR, THIS_YEAR, create_company, record

NBSP = "\N{NO-BREAK SPACE}"
WHOLE_YEAR = {"fra": f"{THIS_YEAR}-01-01", "til": f"{THIS_YEAR}-12-31"}


# --- Books and a fake Løynd behind the route ------------------------------------------


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
    """A company whose books start this year, KI on (the default for a new one)."""
    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    company = create_company(client, organization_id)
    return Books(client, company["id"], organization_id, sessions)


@pytest.fixture
def two_years(client: TestClient, sessions: async_sessionmaker[AsyncSession]) -> Books:
    """A company whose books start last year, so this year has a comparison."""
    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    company = _company(client, organization_id)
    return Books(client, company["id"], organization_id, sessions)


@pytest.fixture
def loynd(client: TestClient) -> Iterator[FakeModel]:
    """The fake Løynd every run the route starts asks; a test changes ``answer``."""
    model = FakeModel(answer_all)
    client.app.dependency_overrides[get_analysis_ki] = lambda: fake_ki(model)  # type: ignore[attr-defined]
    yield model
    client.app.dependency_overrides.pop(get_analysis_ki, None)  # type: ignore[attr-defined]


def book(books: Books, type_code: str, gross: str, on: str, description: str = "Test") -> int:
    response = record(
        books.client,
        books.company_id,
        type_code=type_code,
        gross_amount=gross,
        occurred_on=on,
        description=description,
    )
    assert response.status_code == 201, response.text
    pk: int = response.json()["id"]
    return pk


def march(day: int) -> str:
    return date(THIS_YEAR, 3, day).isoformat()


def figures_of(
    books: Books, *, headers: dict[str, str] | None = None, **params: Any
) -> dict[str, Any]:
    response = books.client.get(
        f"/api/v1/companies/{books.company_id}/analyse",
        params=params or WHOLE_YEAR,
        headers=headers or {},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def split(books: Books, name: str, **params: Any) -> dict[str, Any]:
    response = books.client.get(
        f"/api/v1/companies/{books.company_id}/analyse/datasett/{name}",
        params={**WHOLE_YEAR, **params},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def names(body: dict[str, Any]) -> list[str]:
    return [chart["dataset"]["name"] for chart in body["diagrammer"]]


def pies(body: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = body["diagrammer"][2:]
    return found


def skipping(*descriptions: str) -> Answerer:
    """Answers every voucher but those with these descriptions, which stay unread."""

    def answer(vouchers: list[dict[str, Any]], _call: int) -> list[dict[str, Any] | str]:
        return [tagged(v) for v in vouchers if v["beskrivelse"] not in descriptions]

    return answer


def hva_by_description(hva: dict[str, str]) -> Answerer:
    """Each cost line's ``hva`` chosen by its voucher's description."""

    def answer(vouchers: list[dict[str, Any]], _call: int) -> list[dict[str, Any] | str]:
        return [
            tagged(
                voucher,
                linjer=[
                    {
                        "nr": line["nr"],
                        "hva": (
                            INCOME
                            if line["retning"] == "inntekt"
                            else hva.get(voucher["beskrivelse"], COST)
                        ),
                        "motpart": None,
                        "flagg": [],
                    }
                    for line in voucher["linjer"]
                ],
            )
            for voucher in vouchers
        ]

    return answer


async def runs(books: Books, company_id: int | None = None) -> list[KiRun]:
    async with books.sessions() as db:
        found = await db.scalars(
            select(KiRun)
            .where(KiRun.company_id == (company_id or books.company_id))
            .order_by(KiRun.id)
        )
        return list(found.all())


# --- With KI off ----------------------------------------------------------------------


async def test_with_ki_off_no_stored_tag_is_used_and_no_run_starts(
    books: Books, loynd: FakeModel
) -> None:
    book(books, "salg_hoy_sats", "1250.00", march(2))
    book(books, "varekjop", "500.00", march(3))
    # Read while KI was on: the tags are there.
    assert (await books.run(FakeModel(answer_all))) is not None
    await books.set_ki(False)

    for headers in ({}, agent_header(books.client, books.company_id)):
        body = figures_of(books, headers=headers)
        assert body["ki"] is None
        assert names(body)[2:] == ["inntekter_per_konto", "kostnader_per_konto"]
        assert all(chart.get("caption") is None for chart in pies(body))
    assert len(await runs(books)) == 1
    assert loynd.prompts == []

    for name in (
        "inntekter_per_hva",
        "kostnader_per_hva",
        "kostnader_per_motpart",
        "hva_per_maned",
    ):
        response = books.client.get(
            f"/api/v1/companies/{books.company_id}/analyse/datasett/{name}", params=WHOLE_YEAR
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "ki_turned_off"
    # The menu still lists them, saying they need KI.
    listed = books.client.get(f"/api/v1/companies/{books.company_id}/analyse/datasett").json()
    assert {d["name"] for d in listed["datasets"] if d["needs_tags"]} == {
        "inntekter_per_hva",
        "kostnader_per_hva",
        "kostnader_per_motpart",
        "hva_per_maned",
    }


async def test_with_ki_off_the_splits_themselves_read_no_tag(books: Books) -> None:
    """The domain holds rule 10 too, not only the route: for any caller."""
    book(books, "varekjop", "500.00", march(3))
    await books.run(FakeModel(answer_all))
    await books.set_ki(False)
    async with books.sessions() as db:
        company = await db.get(Company, books.company_id)
        assert company is not None
        chosen = Period(date(THIS_YEAR, 1, 1), date(THIS_YEAR, 12, 31))
        reading = await figures.per_hva(
            db, company=company, chosen=chosen, side=figures.Side.KOSTNADER
        )
        by_party = await figures.per_counterparty(db, company=company, chosen=chosen)
    assert reading.parts == []
    assert reading.unread == Decimal("400.00")
    assert reading.dekning == Decimal("0.00")
    assert [p.key for p in by_party] == [figures.NO_PARTY_KEY]


# --- The first visit, and the one after ------------------------------------------------


async def test_the_first_visit_has_account_pies_and_reads_and_the_next_has_hva_pies(
    books: Books, loynd: FakeModel
) -> None:
    book(books, "salg_hoy_sats", "2500.00", march(2))
    book(books, "varekjop", "1250.00", march(3))
    book(books, "varekjop", "625.00", march(4))

    first = figures_of(books)

    assert names(first)[2:] == ["inntekter_per_konto", "kostnader_per_konto"]
    reading = first["ki"]["lesing"]
    assert reading is not None
    assert (reading["status"], reading["step"], reading["done"], reading["total"]) == (
        "queued",
        jobs.STEP_WAITING,
        0,
        3,
    )
    assert first["ki"]["dekning"] == {"inntekter": "0.00", "kostnader": "0.00"}
    # The run went on after the answer, at «Gratis», and cost nothing of the plan.
    (run,) = await runs(books)
    assert run.id == reading["id"]
    assert (run.kind, run.status, run.requested_level) == (
        RunKind.MERKING,
        RunStatus.DONE,
        Level.FREE.value,
    )
    assert run.tokens_charged == 0
    assert run.via_agent_key_id is None and run.requested_by_user_id is not None
    assert loynd.level == Level.FREE
    assert loynd.prompts

    done = books.client.get(f"/api/v1/companies/{books.company_id}/analyse/kjoringer/{run.id}")
    assert done.status_code == 200, done.text
    assert {k: done.json()[k] for k in ("id", "type", "status", "done", "total", "niva")} == {
        "id": run.id,
        "type": "merking",
        "status": "done",
        "done": 3,
        "total": 3,
        "niva": "free",
    }

    second = figures_of(books)

    assert second["ki"] == {"lesing": None, "dekning": {"inntekter": "1.00", "kostnader": "1.00"}}
    assert names(second)[2:] == ["inntekter_per_hva", "kostnader_per_hva"]
    income, costs = pies(second)
    assert [(s["key"], s["label"], s["value"]) for s in costs["slices"]] == [
        (COST, "Varer for videresalg", "1500.00")
    ]
    assert income["slices"][0]["label"] == "Salg av varer"
    assert income["caption"] == costs["caption"] == datasets.HVA_CAPTION
    assert income["title"] == "Hva inntektene kom fra"
    assert costs["title"] == "Hva kostnadene gikk til"
    # The amounts are the books', to the øre.
    assert second["nokkeltall"] == first["nokkeltall"]
    # Everything read: the second visit started nothing.
    assert len(await runs(books)) == 1


async def test_a_keys_visit_starts_the_reading_too_and_may_watch_it(
    books: Books, loynd: FakeModel
) -> None:
    book(books, "varekjop", "1250.00", march(3))
    key = agent_header(books.client, books.company_id)

    body = figures_of(books, headers=key)

    reading = body["ki"]["lesing"]
    assert reading is not None
    (run,) = await runs(books)
    assert run.via_agent_key_id is not None
    assert run.requested_by_user_id is not None
    watched = books.client.get(
        f"/api/v1/companies/{books.company_id}/analyse/kjoringer/{run.id}", headers=key
    )
    assert watched.status_code == 200, watched.text
    assert watched.json()["status"] == "done"


# --- The 90 % threshold ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("read", "unread", "dekning", "costs_split"),
    [
        # 9 000 of 10 000 kr read, net of VAT: exactly 90 %, so by hva.
        ("11250.00", "1250.00", "0.90", "kostnader_per_hva"),
        # 8 990 of 10 000: 89.9 %, which rounds to 0.90 but is not 90 %.
        ("11237.50", "1262.50", "0.89", "kostnader_per_konto"),
        # 8 999.99: still under.
        ("11249.99", "1250.01", "0.89", "kostnader_per_konto"),
    ],
)
async def test_each_side_is_by_hva_from_90_percent_read_and_never_rounds_up(
    books: Books, loynd: FakeModel, read: str, unread: str, dekning: str, costs_split: str
) -> None:
    book(books, "salg_hoy_sats", "1250.00", march(2), "Salg")
    book(books, "varekjop", read, march(3), "Lest")
    book(books, "varekjop", unread, march(4), "Ulest")
    loynd.answer = skipping("Ulest")

    figures_of(books)
    body = figures_of(books)

    assert body["ki"]["dekning"] == {"inntekter": "1.00", "kostnader": dekning}
    # One side by hva, the other by account, each on its own coverage.
    assert names(body)[2:] == ["inntekter_per_hva", costs_split]
    # The unread voucher is in its back-off: nothing going, so no line.
    assert body["ki"]["lesing"] is None


async def test_the_unread_slice_and_the_others_add_up_to_the_side_to_the_ore(
    books: Books, loynd: FakeModel
) -> None:
    kinds = {
        "Husleie": "husleie-og-lokaler",
        "Strøm": "strom-og-oppvarming",
        "PC": "datautstyr",
        "Lisens": "programvare-og-abonnementer",
        "Diesel": "drivstoff",
        "Verktøy": "verktoy-og-utstyr",
    }
    for day, (description, gross) in enumerate(
        zip(kinds, ("12345.67", "2345.61", "1111.11", "987.65", "333.33", "100.01"), strict=True),
        start=1,
    ):
        book(books, "varekjop", gross, march(day), description)
    book(books, "varekjop", "123.45", march(20), "Ulest")

    def answer(vouchers: list[dict[str, Any]], call: int) -> list[dict[str, Any] | str]:
        return hva_by_description(kinds)([v for v in vouchers if v["beskrivelse"] != "Ulest"], call)

    loynd.answer = answer
    figures_of(books)
    body = figures_of(books)
    costs = Decimal(body["nokkeltall"]["kostnader"])

    assert names(body)[3] == "kostnader_per_hva"
    for chart in (pies(body)[1], split(books, "kostnader_per_hva", topp=4)):
        slices = chart["slices"]
        assert sum(Decimal(s["value"]) for s in slices) == costs
        assert Decimal(chart["total"]) == costs
        assert sum(Decimal(s["share"]) for s in slices) == 1
        # Last, by itself, never in «Andre».
        assert slices[-1]["key"] == figures.UNREAD_KEY
        assert slices[-1]["label"] == "Ikke lest ennå"
        assert Decimal(slices[-1]["value"]) == Decimal("98.76")
    # «Ikke lest ennå» takes one of the named places: as many slices at
    # topp=4 as a split without it, three named, «Andre» and the unread.
    keys = [s["key"] for s in split(books, "kostnader_per_hva", topp=4)["slices"]]
    assert keys == [
        "husleie-og-lokaler",
        "strom-og-oppvarming",
        "datautstyr",
        "andre",
        "ikke_lest",
    ]


def test_the_unread_part_takes_a_place_and_goes_last() -> None:
    parts = [Part(f"k{i}", f"Merke {i}", Decimal("100")) for i in range(3)]
    unread = Part(figures.UNREAD_KEY, figures.UNREAD_LABEL, Decimal("1000"))

    slices = datasets.top_slices(parts, topp=3, apart=unread)

    assert [s.key for s in slices] == ["k0", "k1", "k2", "ikke_lest"]
    assert sum(s.share for s in slices) == 1
    # With no «Andre», the largest takes the rounding, and that may be the unread.
    assert [s.share for s in slices] == [Decimal("0.0769")] * 3 + [Decimal("0.7693")]
    folded = datasets.top_slices([*parts, Part("k3", "Merke 3", Decimal("50"))], 3, apart=unread)
    assert [s.key for s in folded] == ["k0", "k1", "andre", "ikke_lest"]
    # Nothing unread: no slice for it.
    zero = Part(figures.UNREAD_KEY, figures.UNREAD_LABEL, Decimal("0"))
    assert [s.key for s in datasets.top_slices(parts, 3, apart=zero)] == ["k0", "k1", "k2"]


def test_coverage_is_rounded_down_and_null_for_a_side_of_nothing() -> None:
    def reading(read: str, unread: str) -> figures.Reading:
        return figures.Reading(parts=[Part("x", "X", Decimal(read))], unread=Decimal(unread))

    assert reading("8999.99", "1000.01").dekning == Decimal("0.89")
    assert reading("9000", "1000").dekning == Decimal("0.90")
    assert reading("1000", "0").dekning == Decimal("1.00")
    assert reading("0", "0").dekning is None
    # A refund read larger than the side: never more than all of it.
    assert reading("1000", "-200").dekning == Decimal("1.00")


# --- While a run is going ---------------------------------------------------------------


async def test_a_visit_while_a_run_is_going_starts_none_and_names_it(
    books: Books, loynd: FakeModel
) -> None:
    book(books, "varekjop", "1250.00", march(3))
    async with books.sessions() as db:
        going = KiRun(
            company_id=books.company_id,
            kind=RunKind.MERKING.value,
            status=RunStatus.QUEUED.value,
            requested_level=Level.FREE.value,
            step=jobs.STEP_WAITING,
            total=3,
        )
        db.add(going)
        await db.commit()

    for _ in range(2):
        body = figures_of(books)
        assert body["ki"]["lesing"] == {
            "id": going.id,
            "status": "queued",
            "step": jobs.STEP_WAITING,
            "done": 0,
            "total": 3,
        }
    assert [run.id for run in await runs(books)] == [going.id]
    assert loynd.prompts == []

    async with books.sessions() as db:
        await db.execute(
            update(KiRun)
            .where(KiRun.id == going.id)
            .values(status=RunStatus.RUNNING.value, step=jobs.STEP_READING, done=1)
        )
        await db.commit()
    reading = figures_of(books)["ki"]["lesing"]
    assert (reading["status"], reading["step"], reading["done"]) == ("running", "Leser bilagene", 1)
    watched = books.client.get(
        f"/api/v1/companies/{books.company_id}/analyse/kjoringer/{going.id}"
    ).json()
    assert (watched["status"], watched["done"], watched["total"]) == ("running", 1, 3)
    assert watched["finished_at"] is None


async def test_after_loynd_did_not_answer_the_page_asks_again_only_after_ten_minutes(
    books: Books, loynd: FakeModel
) -> None:
    book(books, "varekjop", "1250.00", march(3))

    def down(_vouchers: list[dict[str, Any]], _call: int) -> list[dict[str, Any] | str]:
        raise LoyndDownError

    loynd.answer = down
    assert figures_of(books)["ki"]["lesing"] is not None
    (failed,) = await runs(books)
    assert (failed.status, failed.error_code) == (RunStatus.FAILED, jobs.KI_UNAVAILABLE)

    # Inside the pause: the figures as they are, nothing started, nothing alarming.
    loynd.answer = answer_all
    asked = len(loynd.prompts)
    paused = figures_of(books)
    assert paused["ki"] == {"lesing": None, "dekning": {"inntekter": None, "kostnader": "0.00"}}
    assert names(paused)[3] == "kostnader_per_konto"
    assert len(await runs(books)) == 1
    assert len(loynd.prompts) == asked

    async with books.sessions() as db:
        await db.execute(
            update(KiRun).values(finished_at=utc_now() - jobs.UNAVAILABLE_PAUSE - timedelta(1))
        )
        await db.commit()
    assert figures_of(books)["ki"]["lesing"] is not None
    assert len(await runs(books)) == 2
    assert names(figures_of(books))[3] == "kostnader_per_hva"


# --- Another company -------------------------------------------------------------------


async def test_another_companys_tags_are_never_in_a_split_and_its_run_is_a_404(
    books: Books, loynd: FakeModel
) -> None:
    other = create_company(
        books.client, books.organization_id, orgnr=OTHER_ORGNR, name="Annen Drift AS"
    )["id"]
    mine = book(books, "varekjop", "1250.00", march(3))
    record(books.client, other, type_code="varekjop", gross_amount="700.00", occurred_on=march(3))

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
                        "beskrivelse": "Bare for det andre selskapet.",
                    }
                ],
            )
            for voucher in vouchers
        ]

    theirs = await books.run(FakeModel(naming), company_id=other)
    assert theirs is not None and theirs.status == RunStatus.DONE
    figures_of(books)  # mine read by the fake's usual answer

    # A row that should never exist: their tags on my line, under either
    # company's id. The split must not follow it.
    async with books.sessions() as db:
        # My cost line: the one my own reading tagged.
        line = await db.scalar(
            select(KiLineTag.line_pk).where(KiLineTag.transaction_pk == mine).limit(1)
        )
        their_analysis = await db.scalar(
            select(KiVoucherAnalysis.id).where(KiVoucherAnalysis.company_id == other).limit(1)
        )
        their_tags = (await db.scalars(select(KiTag).where(KiTag.company_id == other))).all()
        hva = next(t for t in their_tags if t.facet == Facet.HVA and t.slug == "hemmelig-merke")
        party = next(t for t in their_tags if t.facet == Facet.MOTPART)
        assert line is not None and their_analysis is not None
        for company_id, tag in ((books.company_id, hva), (other, party)):
            db.add(
                KiLineTag(
                    company_id=company_id,
                    line_pk=line,
                    transaction_pk=mine,
                    analysis_id=their_analysis,
                    tag_id=tag.id,
                )
            )
        await db.commit()

    body = figures_of(books)
    shown = [
        *pies(body),
        split(books, "kostnader_per_hva"),
        split(books, "kostnader_per_motpart"),
    ]
    text = repr(shown).casefold()
    assert "hemmelig" not in text and "ola nordmann" not in text
    assert [s["key"] for s in shown[1]["slices"]] == [COST]

    their_run = books.client.get(
        f"/api/v1/companies/{books.company_id}/analyse/kjoringer/{theirs.id}"
    )
    assert their_run.status_code == 404
    assert their_run.json()["code"] == "analysis_run_not_found"
    nowhere = books.client.get(f"/api/v1/companies/{books.company_id}/analyse/kjoringer/999999")
    assert nowhere.status_code == 404


async def test_a_stranger_may_not_watch_a_companys_run(books: Books, loynd: FakeModel) -> None:
    book(books, "varekjop", "1250.00", march(3))
    reading = figures_of(books)["ki"]["lesing"]
    url = f"/api/v1/companies/{books.company_id}/analyse/kjoringer/{reading['id']}"

    register(books.client, "per@example.no", full_name="Per Hansen")
    response = books.client.get(url)

    assert response.status_code == 404
    assert response.json()["code"] == "company_not_found"


# --- Who the money went to --------------------------------------------------------------


async def test_costs_per_counterparty_take_the_books_party_first_then_ki_s(
    books: Books, loynd: FakeModel
) -> None:
    supplier = _party(books.client, books.company_id, "leverandor", "Circle K AS")
    response = record(
        books.client,
        books.company_id,
        type_code="varekjop",
        gross_amount="1250.00",
        occurred_on=march(2),
        description="Med part",
        party_id=supplier,
    )
    assert response.status_code == 201, response.text
    book(books, "varekjop", "625.00", march(3), "Samme navn")
    book(books, "varekjop", "250.00", march(4), "Person")
    book(books, "varekjop", "125.00", march(5), "Ingen")
    who = {"Samme navn": "Circle K AS", "Person": "Ola Nordmann"}

    def answer(vouchers: list[dict[str, Any]], _call: int) -> list[dict[str, Any] | str]:
        return [
            tagged(
                voucher,
                linjer=[
                    {
                        "nr": line["nr"],
                        "hva": COST,
                        "motpart": who.get(voucher["beskrivelse"]),
                        "flagg": [],
                    }
                    for line in voucher["linjer"]
                ],
            )
            for voucher in vouchers
        ]

    loynd.answer = answer
    figures_of(books)
    chart = split(books, "kostnader_per_motpart", visning="tabell")

    rows = [(s["key"], s["label"], s["value"]) for s in chart["slices"]]
    assert rows == [
        (str(supplier), "Circle K AS", "1500.00"),
        ("motpart:ola-nordmann", "Ola Nordmann", "200.00"),
        (figures.NO_PARTY_KEY, "Uten motpart", "100.00"),
    ]
    assert chart["caption"] == datasets.COUNTERPARTY_CAPTION
    assert sum(Decimal(s["value"]) for s in chart["slices"]) == Decimal("1800.00")


# --- The sentences ----------------------------------------------------------------------


def test_the_hva_that_grew_most_and_its_thresholds() -> None:
    today = date(THIS_YEAR, 11, 26)
    ytd = Period(date(THIS_YEAR, 1, 1), today)
    change = sentences.Change

    assert sentences.grew_most(
        [change("Drivstoff", Decimal("21000"), Decimal("15000"))], ytd, today
    ) == (f"Drivstoff har økt med 40{NBSP}% fra samme tid i fjor.")
    # More than 5 000 kr and more than 25 %, both strictly.
    assert (
        sentences.grew_most([change("a", Decimal("25000"), Decimal("20000"))], ytd, today) is None
    )
    assert (
        sentences.grew_most([change("a", Decimal("50000"), Decimal("40000"))], ytd, today) is None
    )
    assert sentences.grew_most(
        [change("husleie", Decimal("50000.01"), Decimal("40000"))], ytd, today
    ) == (f"Husleie har økt med 25{NBSP}% fra samme tid i fjor.")
    # From nothing is no percentage.
    assert sentences.grew_most([change("a", Decimal("9000"), Decimal("0"))], ytd, today) is None
    # The most kroner wins, not the highest percentage.
    both = [
        change("Programvare", Decimal("20000"), Decimal("10000")),
        change("Husleie og lokaler", Decimal("130000"), Decimal("100000")),
    ]
    assert sentences.grew_most(both, ytd, today) == (
        f"Husleie og lokaler har økt med 30{NBSP}% fra samme tid i fjor."
    )
    # Two that grew by as much: neither is «the most».
    tie = [
        change("a", Decimal("20000"), Decimal("10000")),
        change("b", Decimal("40000"), Decimal("30000")),
    ]
    assert sentences.grew_most(tie, ytd, today) is None
    last_year = Period(date(THIS_YEAR - 1, 1, 1), date(THIS_YEAR - 1, 12, 31))
    assert sentences.grew_most(
        [change("Drivstoff", Decimal("21000"), Decimal("15000"))], last_year, today
    ) == (f"Drivstoff har økt med 40{NBSP}% fra {THIS_YEAR - 2}.")


def test_the_largest_cost_is_left_out_while_the_unread_could_be_larger() -> None:
    parts = [
        sentences.Named("Husleie og lokaler", Decimal("38000")),
        sentences.Named("Drivstoff", Decimal("12000")),
    ]
    assert sentences.largest_cost(parts, Decimal("60000"), Decimal("10000")) == (
        f"Den største kostnaden er Husleie og lokaler, 63{NBSP}% av kostnadene."
    )
    assert sentences.largest_cost(parts, Decimal("88000"), Decimal("38000")) is None


async def test_the_page_names_the_largest_hva_and_the_one_that_grew(
    two_years: Books, loynd: FakeModel
) -> None:
    books = two_years
    for on, description, type_code, gross in (
        (f"{LAST_YEAR}-01-10", "Diesel", "varekjop", "12500.00"),
        (f"{LAST_YEAR}-02-10", "Husleie", "husleie", "25000.00"),
        (f"{THIS_YEAR}-01-10", "Diesel", "varekjop", "20000.00"),
        (f"{THIS_YEAR}-02-10", "Husleie", "husleie", "26250.00"),
    ):
        book(books, type_code, gross, on, description)
    loynd.answer = hva_by_description({"Diesel": "drivstoff", "Husleie": "husleie-og-lokaler"})
    period: dict[str, Any] = {"fra": f"{THIS_YEAR}-01-01", "til": f"{THIS_YEAR}-03-31"}

    first = figures_of(books, **period)
    assert not any("har økt" in s for s in first["tekst"])
    # One run read the period and its comparison.
    (run,) = await runs(books)
    assert (run.status, run.total) == (RunStatus.DONE, 4)

    body = figures_of(books, **period)

    assert body["sammenligning"] == {"fra": f"{LAST_YEAR}-01-01", "til": f"{LAST_YEAR}-03-31"}
    assert f"Den største kostnaden er Husleie og lokaler, 57{NBSP}% av kostnadene." in body["tekst"]
    assert f"Drivstoff har økt med 60{NBSP}% fra samme tid året før." in body["tekst"]
    assert not any("\N{EM DASH}" in s for s in body["tekst"])

    # With KI off, the same books in the accounts' words, and no hva anywhere.
    await books.set_ki(False)
    off = figures_of(books, **period)
    assert off["ki"] is None
    assert not any("Drivstoff" in s or "Husleie og" in s for s in off["tekst"])
    assert any(s.startswith("Den største kostnaden er 6300") for s in off["tekst"])


async def test_no_growth_sentence_while_the_comparison_is_not_read_enough(
    two_years: Books, loynd: FakeModel
) -> None:
    books = two_years
    book(books, "varekjop", "12500.00", f"{LAST_YEAR}-01-10", "Diesel")
    book(books, "husleie", "2500.00", f"{LAST_YEAR}-01-11", "Ulest")
    book(books, "varekjop", "20000.00", f"{THIS_YEAR}-01-10", "Diesel")
    book(books, "husleie", "1250.00", f"{THIS_YEAR}-01-11", "Husleie")

    def answer(vouchers: list[dict[str, Any]], call: int) -> list[dict[str, Any] | str]:
        hva = hva_by_description({"Diesel": "drivstoff", "Husleie": "husleie-og-lokaler"})
        return hva([v for v in vouchers if v["beskrivelse"] != "Ulest"], call)

    loynd.answer = answer
    period: dict[str, Any] = {"fra": f"{THIS_YEAR}-01-01", "til": f"{THIS_YEAR}-03-31"}
    figures_of(books, **period)
    body = figures_of(books, **period)

    # This year is read (by hva); last year 10 000 of 12 000 kr, under 90 %.
    assert body["ki"]["dekning"]["kostnader"] == "1.00"
    assert names(body)[3] == "kostnader_per_hva"
    assert not any("har økt" in s for s in body["tekst"])


async def test_tags_group_lines_and_never_make_a_number(books: Books) -> None:
    """The split by hva comes to the split by account, to the øre."""
    book(books, "salg_hoy_sats", "1250.00", march(2))
    book(books, "varekjop", "1250.00", march(3))
    await books.run(FakeModel(answer_all))
    by_account = split(books, "kostnader_per_konto")
    by_hva = split(books, "kostnader_per_hva")
    assert by_account["total"] == by_hva["total"] == "1000.00"
    async with books.sessions() as db:
        tagged_lines = await db.scalar(
            select(func.count())
            .select_from(KiLineTag)
            .where(KiLineTag.company_id == books.company_id)
        )
    assert tagged_lines == 2


async def test_a_merged_tag_is_counted_under_the_tag_it_was_merged_into(
    books: Books, loynd: FakeModel
) -> None:
    """Tags merged later (WP8) combine into one slice rather than splitting the same cost."""
    book(books, "varekjop", "1250.00", march(2), "A")
    book(books, "varekjop", "625.00", march(3), "B")
    loynd.answer = hva_by_description({"A": "drivstoff", "B": "bompenger-og-parkering"})
    figures_of(books)

    async with books.sessions() as db:
        tags = {
            t.slug: t.id
            for t in (
                await db.scalars(
                    select(KiTag).where(KiTag.company_id == books.company_id, KiTag.facet == "hva")
                )
            ).all()
        }
        await db.execute(
            update(KiTag)
            .where(KiTag.id == tags["bompenger-og-parkering"])
            .values(merged_into_id=tags["drivstoff"])
        )
        await db.commit()

    chart = split(books, "kostnader_per_hva")
    assert [(s["key"], s["label"], s["value"]) for s in chart["slices"]] == [
        ("drivstoff", "Drivstoff", "1500.00")
    ]
