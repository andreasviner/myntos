"""The deadline list over HTTP: which deadlines a company has, and which Myntos knows are met.

The day is fixed at 6 November 2026 (``frister_api._today``), so every date
below can be read off a calendar. What these hold on to:

- the right deadlines for the form: an AS's five a year, an ENK owner's four
  forskuddsskatt terms in the year itself, and nothing but MVA for a form the
  list does not know yet;
- «missed» only when Myntos knows: an MVA term inside the books with nothing
  filed, an årsregnskap Brønnøysund had not received when asked after the
  deadline. A term from before the books, or a skattemelding Myntos did not
  send, is not known and is not shown as late.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from myntos.api.v1 import frister as frister_api
from myntos.db.session import get_session
from myntos.domain.ledger.models import Company, CompanyRegisterSnapshot
from tests.conftest import (
    insert_mva_filing,
    insert_skattemelding_filing,
    join_company,
    register,
    write,
)

TODAY = date(2026, 11, 6)
ORGNR = "910000004"


@pytest.fixture(autouse=True)
def fixed_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(frister_api, "_today", lambda: TODAY)


def books(
    client: TestClient,
    *,
    form: str | None = "AS",
    vat_registered: bool = True,
    first_year: int = 2026,
) -> dict[str, Any]:
    session = register(client)
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": session["memberships"][0]["organization_id"],
            "orgnr": ORGNR,
            "name": "Fjordferje AS",
            "first_financial_year": first_year,
            "vat_registered": vat_registered,
            "organisation_form": form,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _in_app_session(client: TestClient, work: Any) -> None:
    """Run ``work(session)`` in the app's own session and loop, and commit.

    The reasoning of ``insert_mva_filing`` in conftest: the engine's
    connections belong to the app's loop.
    """
    override = client.app.dependency_overrides[get_session]  # type: ignore[attr-defined]

    async def run() -> None:
        sessions = override()
        session = await sessions.__anext__()
        await work(session)
        await session.flush()
        with contextlib.suppress(StopAsyncIteration):
            await sessions.__anext__()

    portal = client.portal
    assert portal is not None
    portal.call(run)


def set_company(client: TestClient, company_id: int, **values: Any) -> None:
    async def work(session: Any) -> None:
        await session.execute(update(Company).where(Company.id == company_id).values(**values))

    _in_app_session(client, work)


def register_answer(
    client: TestClient, company_id: int, *, fetched_at: datetime, **unit: Any
) -> None:
    async def work(session: Any) -> None:
        session.add(
            CompanyRegisterSnapshot(
                company_id=company_id, orgnr=ORGNR, fetched_at=fetched_at, unit=unit, roles=[]
            )
        )

    _in_app_session(client, work)


def frister(client: TestClient, company_id: int) -> dict[str, Any]:
    response = client.get(f"/api/v1/companies/{company_id}/frister")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def by_label(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["label"]: row for row in body["frister"]}


def test_an_aksjeselskap_sees_its_year_soonest_first(client: TestClient) -> None:
    company = books(client)
    # The September-October term is over and filed; its deadline is ahead.
    insert_mva_filing(client, company["id"], year=2026, index=5, status="mottatt")
    body = frister(client, company["id"])

    assert body["today"] == "2026-11-06"
    assert body["until"] == "2027-11-06"
    assert body["covers_yearly"] is True
    deadlines = [row["deadline"] for row in body["frister"]]
    assert deadlines == sorted(deadlines)

    rows = by_label(body)
    filed = rows["Mva-melding for 5. termin 2026 (september-oktober)"]
    assert filed["deadline"] == "2026-12-10"
    assert (filed["done"], filed["done_by"], filed["is_overdue"]) == (True, "myntos", False)
    assert filed["termin"] == 5 and filed["year"] == 2026

    # The yearly duties for 2026, all in 2027, and none of 2025's: those dates
    # have passed and Myntos cannot know whether they were met.
    yearly = {
        row["kind"]: row["deadline"]
        for row in body["frister"]
        if row["kind"] != "mva_melding" and row["year"] == 2026
    }
    assert yearly == {
        "aksjonaerregisteroppgave": "2027-01-31",
        "forskuddsskatt": "2027-04-15",
        "skattemelding": "2027-05-31",
        "arsregnskap": "2027-07-31",
    }
    assert rows["Forskuddsskatt for 2026, 1. termin"]["deadline"] == "2027-02-15"
    yearly_rows = [row for row in body["frister"] if row["kind"] != "mva_melding"]
    assert not [row for row in yearly_rows if row["year"] < 2026]

    skattemelding = rows["Skattemeldingen for 2026"]
    assert skattemelding["files_from_myntos"] is True
    assert skattemelding["done"] is None
    assert rows["Årsregnskapet for 2026"]["recipient"] == "bronnoysund"
    assert rows["Aksjonærregisteroppgaven for 2026"]["files_from_myntos"] is False


def test_a_term_in_the_books_with_nothing_filed_is_overdue(client: TestClient) -> None:
    company = books(client)
    insert_mva_filing(client, company["id"], year=2026, index=2, status="annet_program")
    # Checked is not filed.
    insert_mva_filing(client, company["id"], year=2026, index=3, status="validert")
    rows = by_label(frister(client, company["id"]))

    first = rows["Mva-melding for 1. termin 2026 (januar-februar)"]
    assert (first["done"], first["is_overdue"]) == (False, True)
    # Filed from another program, by the person's word: met, so off the list.
    assert "Mva-melding for 2. termin 2026 (mars-april)" not in rows
    assert rows["Mva-melding for 3. termin 2026 (mai-juni)"]["is_overdue"] is True
    # 10 October 2026 was a Saturday.
    assert rows["Mva-melding for 4. termin 2026 (juli-august)"]["deadline"] == "2026-10-12"
    # The next year's terms, up to the window's end; the fifth is due after it.
    assert rows["Mva-melding for 4. termin 2027 (juli-august)"]["deadline"] == "2027-10-11"
    assert "Mva-melding for 5. termin 2027 (september-oktober)" not in rows


def test_a_term_from_before_the_books_is_not_called_missed(client: TestClient) -> None:
    # Books from 2027: the old program had 2026.
    company = books(client, first_year=2027)
    rows = by_label(frister(client, company["id"]))
    assert "Mva-melding for 1. termin 2026 (januar-februar)" not in rows
    upcoming = rows["Mva-melding for 5. termin 2026 (september-oktober)"]
    assert upcoming["done"] is None
    assert upcoming["is_overdue"] is False
    assert "fra før regnskapet" in upcoming["note"]


def test_only_terms_after_vat_registration(client: TestClient) -> None:
    company = books(client)
    set_company(client, company["id"], vat_registered_from=date(2026, 7, 15))
    labels = by_label(frister(client, company["id"]))
    assert not [label for label in labels if "2026 (mai-juni)" in label]
    # The term registration starts in is the first melding.
    assert "Mva-melding for 4. termin 2026 (juli-august)" in labels


def test_not_vat_registered_has_no_mva_terms(client: TestClient) -> None:
    company = books(client, vat_registered=False)
    body = frister(client, company["id"])
    assert body["vat_registered"] is False
    assert {row["kind"] for row in body["frister"]} == {
        "aksjonaerregisteroppgave",
        "forskuddsskatt",
        "skattemelding",
        "arsregnskap",
    }


def test_a_skattemelding_sent_from_myntos_is_met(client: TestClient) -> None:
    company = books(client, vat_registered=False)
    insert_skattemelding_filing(client, company["id"], year=2026, status="mottatt")
    rows = by_label(frister(client, company["id"]))
    row = rows["Skattemeldingen for 2026"]
    assert (row["done"], row["done_by"]) == (True, "myntos")


def test_a_checked_skattemelding_is_not_met(client: TestClient) -> None:
    company = books(client, vat_registered=False)
    insert_skattemelding_filing(client, company["id"], year=2026, status="validert")
    assert by_label(frister(client, company["id"]))["Skattemeldingen for 2026"]["done"] is None


def test_annual_accounts_brreg_has_not_received_after_the_deadline(client: TestClient) -> None:
    company = books(client, vat_registered=False)
    register_answer(
        client,
        company["id"],
        fetched_at=datetime(2026, 9, 1, 8, tzinfo=UTC),
        last_submitted_annual_accounts="2024",
    )
    body = frister(client, company["id"])
    assert body["register_checked_at"].startswith("2026-09-01")
    row = by_label(body)["Årsregnskapet for 2025"]
    assert (row["done"], row["is_overdue"]) == (False, True)
    assert row["deadline"] == "2026-07-31"


def test_annual_accounts_received_are_met(client: TestClient) -> None:
    company = books(client, vat_registered=False)
    register_answer(
        client,
        company["id"],
        fetched_at=datetime(2026, 9, 1, 8, tzinfo=UTC),
        last_submitted_annual_accounts="2025",
    )
    assert "Årsregnskapet for 2025" not in by_label(frister(client, company["id"]))


def test_asked_before_the_deadline_is_not_an_answer(client: TestClient) -> None:
    company = books(client, vat_registered=False)
    register_answer(
        client,
        company["id"],
        fetched_at=datetime(2026, 7, 1, 8, tzinfo=UTC),
        last_submitted_annual_accounts="2024",
    )
    assert "Årsregnskapet for 2025" not in by_label(frister(client, company["id"]))


def test_no_duties_for_a_year_before_the_company_existed(client: TestClient) -> None:
    company = books(client, vat_registered=False)
    register_answer(
        client,
        company["id"],
        fetched_at=datetime(2026, 9, 1, 8, tzinfo=UTC),
        established_on="2026-03-01",
        last_submitted_annual_accounts="2019",
    )
    assert not [row for row in frister(client, company["id"])["frister"] if row["year"] < 2026]


def test_an_enk_owner_pays_forskuddsskatt_in_the_year(client: TestClient) -> None:
    company = books(client, form="ENK", vat_registered=False)
    body = frister(client, company["id"])
    assert body["covers_yearly"] is True
    assert [(row["kind"], row["deadline"]) for row in body["frister"]] == [
        ("forskuddsskatt", "2026-12-15"),
        ("forskuddsskatt", "2027-03-15"),
        ("skattemelding", "2027-05-31"),
        ("forskuddsskatt", "2027-06-15"),
        ("forskuddsskatt", "2027-09-15"),
    ]
    skattemelding = by_label(body)["Skattemeldingen for 2026"]
    assert skattemelding["files_from_myntos"] is False
    assert "innehaverens egen" in skattemelding["note"]


def test_a_form_the_list_does_not_know_gets_its_mva_terms_only(client: TestClient) -> None:
    company = books(client, form="NUF")
    body = frister(client, company["id"])
    assert body["covers_yearly"] is False
    assert {row["kind"] for row in body["frister"]} == {"mva_melding"}


def test_a_wound_up_company_has_no_dates_after_its_last_day(client: TestClient) -> None:
    company = books(client)
    set_company(client, company["id"], wound_up_on=date(2026, 6, 1))
    body = frister(client, company["id"])
    assert body["wound_up_on"] == "2026-06-01"
    # The May-June term holds the last day and is still owed; nothing after it.
    assert {(row["year"], row["termin"]) for row in body["frister"]} == {
        (2026, 1),
        (2026, 2),
        (2026, 3),
    }


def test_every_role_reads_it_and_nobody_else_does(client: TestClient) -> None:
    company = books(client)
    join_company(client, company["id"], email="regnskap@example.no", role="member")
    assert client.get(f"/api/v1/companies/{company['id']}/frister").status_code == 200

    write(client, "POST", "/api/v1/auth/logout")
    register(client, "fremmed@example.no")
    assert client.get(f"/api/v1/companies/{company['id']}/frister").status_code == 404
