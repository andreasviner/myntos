"""The overview's figures: where the company's money is, and what VAT is owed now.

Two things the farm pilot's overview got wrong after its Tripletex books came
over (docs/landbruk.md). Its only bank account was a driftskreditt on 2390, so
«Inn i år» and «Ut i år» counted nothing; and the VAT card summed the year's
VAT with the filed terms left out, so terms the old program had settled read as
money owed to the company. The files here are synthetic and Tripletex-shaped,
from ``test_saft_import`` and ``test_saft_innlesing``.
"""

from __future__ import annotations

import contextlib
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from myntos.db.session import get_session
from tests.conftest import write
from tests.test_arsoppgjor_api import LAST_YEAR, _books, _post_opening
from tests.test_arsoppgjor_api import _line as opening_line
from tests.test_cashflow_api import THIS_YEAR, record, signed_in_company
from tests.test_saft_import import ACCOUNTS, _create_missing, saft_file, upload
from tests.test_saft_innlesing import EARLIER, _codes, _get, _post, _upload, _voucher
from tests.test_saft_innlesing import _line as file_line
from tests.test_saft_voucher_import import EXTRA_ACCOUNTS

# --- A bank that is not on 1920 ----------------------------------------------------------

#: The year's money through the driftskreditt, and nothing on 1920: milk sold
#: into it, feed paid out of it.
BANK_ON_2390 = [
    _voucher(
        "1",
        "03-10",
        "Melk solgt",
        [
            file_line("1", "239010", "1250.00", "03-10"),
            file_line("2", "307120", "-1000.00", "03-10", tax="3/1000.00/250.00"),
            file_line("3", "2700", "-250.00", "03-10"),
        ],
    ),
    _voucher(
        "2",
        "04-02",
        "Kraftfôr",
        [
            file_line("1", "402020", "400.00", "04-02"),
            file_line("2", "239010", "-400.00", "04-02"),
        ],
    ),
]


def _bank_on_2390_file() -> bytes:
    """A year whose books began in it, so the file opens at zero, like the farm's."""
    zero = [
        (account_id, text, category, code, "0", "0")
        for account_id, text, category, code, _, _ in ACCOUNTS
    ]
    entries = (
        f"<GeneralLedgerEntries><NumberOfEntries>{len(BANK_ON_2390)}</NumberOfEntries>"
        "<TotalDebit>1650.00</TotalDebit><TotalCredit>1650.00</TotalCredit>"
        "<Journal><JournalID>GL</JournalID><Description>Hovedbok</Description><Type>GL</Type>"
        f"{''.join(BANK_ON_2390)}</Journal></GeneralLedgerEntries>"
    )
    return saft_file(
        year=EARLIER,
        accounts=zero + EXTRA_ACCOUNTS,
        customers=[],
        suppliers=[],
        after_master=entries,
    )


def _summary(client: TestClient, company_id: int, year: int) -> dict[str, Any]:
    response = client.get(
        f"/api/v1/companies/{company_id}/cashflow/summary", params={"financial_year": year}
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _account(client: TestClient, company_id: int, number: str, year: int) -> dict[str, Any]:
    plan = client.get(f"/api/v1/companies/{company_id}/kontoplan", params={"aar": year}).json()
    found: dict[str, Any] = next(a for a in plan["accounts"] if a["number"] == number)
    return found


def _mark(client: TestClient, company_id: int, account_id: int, value: bool, year: int) -> Any:
    return write(
        client,
        "PATCH",
        f"/api/v1/companies/{company_id}/kontoplan/{account_id}?aar={year}",
        json={"is_bank_account": value},
    )


def test_an_import_marks_the_bank_the_file_names_and_the_overview_counts_it(
    client: TestClient,
) -> None:
    company = _books(client)
    uploaded = _upload(client, company["id"], (f"{EARLIER}.xml", _bank_on_2390_file()))
    review = _get(client, company["id"], uploaded["id"])["review"]
    assert review["postable"] is True, review["findings"]
    assert "bankkonto_merket" in _codes(review)
    assert _post(client, company["id"], uploaded["id"]).status_code == 202

    account = _account(client, company["id"], "2390", EARLIER)
    assert account["is_bank_account"] is True
    # The vouchers stay where the old program put them.
    bank = client.get(
        f"/api/v1/companies/{company['id']}/bilag", params={"aar": EARLIER, "konto": "2390"}
    ).json()
    assert Decimal(bank["closing_balance"]) == Decimal("850.00")

    summary = _summary(client, company["id"], EARLIER)
    assert Decimal(summary["total_in"]) == Decimal("1250.00")
    assert Decimal(summary["total_out"]) == Decimal("400.00")
    assert Decimal(summary["cash_position"]) == Decimal("850.00")

    # Unmarked, the money is nowhere the overview looks; marked again, it is back.
    assert _mark(client, company["id"], account["id"], False, EARLIER).status_code == 200
    unmarked = _summary(client, company["id"], EARLIER)
    assert Decimal(unmarked["total_in"]) == 0
    assert Decimal(unmarked["cash_position"]) == 0
    assert _account(client, company["id"], "2390", EARLIER)["is_bank_account"] is False

    again = _mark(client, company["id"], account["id"], True, EARLIER)
    assert again.status_code == 200, again.text
    assert Decimal(_summary(client, company["id"], EARLIER)["total_in"]) == Decimal("1250.00")


def test_only_an_account_that_can_hold_money_is_marked(client: TestClient) -> None:
    company = signed_in_company(client)
    plan = client.get(f"/api/v1/companies/{company['id']}/kontoplan").json()
    by_number = {account["number"]: account for account in plan["accounts"]}
    assert by_number["1920"]["is_bank_account"] is True
    assert by_number["1900"]["is_bank_account"] is True
    assert by_number["1500"]["is_bank_account"] is False

    # 1920 is where payments go; it always counts.
    refused = _mark(client, company["id"], by_number["1920"]["id"], False, THIS_YEAR)
    assert refused.status_code == 409
    assert refused.json()["code"] == "account_in_use"
    # Income counted as the bank would count every sale twice.
    income = next(a for a in plan["accounts"] if a["number"].startswith("3"))
    refused = _mark(client, company["id"], income["id"], True, THIS_YEAR)
    assert refused.status_code == 422
    assert refused.json()["code"] == "invalid_account_number"
    # 2740 already means something else.
    refused = _mark(client, company["id"], by_number["2740"]["id"], True, THIS_YEAR)
    assert refused.status_code == 409


def test_the_opening_balance_review_stops_asking_once_the_bank_is_marked(
    client: TestClient,
) -> None:
    """The older flow stores nothing, so it asks; once marked it has nothing to ask."""
    company = _books(client)
    first = upload(client, company["id"], saft_file()).json()
    assert "bankkonto_utenfor_1920" in _codes(first)
    _create_missing(client, company["id"], first)
    account = _account(client, company["id"], "2390", LAST_YEAR)
    assert _mark(client, company["id"], account["id"], True, LAST_YEAR).status_code == 200
    second = upload(client, company["id"], saft_file()).json()
    assert "bankkonto_utenfor_1920" not in _codes(second)


# --- What VAT is owed now -------------------------------------------------------------------


def _settle(client: TestClient, company_id: int, year: int, index: int) -> None:
    """Settle a term onto 2740 as filing it does, on the app's own loop and session.

    The pattern of ``tests.conftest.insert_mva_filing``: standing up ID-porten
    and Skatteetaten to file a term is not what these tests are about.
    """
    from myntos.domain.ledger.models import Company
    from myntos.domain.mva import naering
    from myntos.domain.mva import service as mva_service

    override = client.app.dependency_overrides[get_session]  # type: ignore[attr-defined]

    async def settle() -> None:
        sessions = override()
        session = await sessions.__anext__()
        company = await session.get(Company, company_id)
        assert company is not None
        setup = await naering.load(session, company)
        await mva_service.post_settlement(
            session,
            company=company,
            termin=setup.main.termin(year, index),
            created_by_user_id=None,
        )
        with contextlib.suppress(StopAsyncIteration):
            await sessions.__anext__()

    portal = client.portal
    assert portal is not None
    portal.call(settle)


def test_the_vat_card_is_what_is_owed_now(client: TestClient) -> None:
    company = signed_in_company(client)
    january = date(THIS_YEAR, 1, 15).isoformat()
    sold = record(
        client,
        company["id"],
        type_code="salg_hoy_sats",
        gross_amount="1250.00",
        occurred_on=january,
    )
    assert sold.status_code == 201, sold.text

    # An open term: its VAT is owed.
    summary = _summary(client, company["id"], THIS_YEAR)
    assert summary["vat_position"] == "250.00"

    # Filed and settled onto 2740, not yet paid: still owed, all of it.
    _settle(client, company["id"], THIS_YEAR, 1)
    waiting = client.get(
        f"/api/v1/companies/{company['id']}/bilag", params={"aar": THIS_YEAR, "konto": "2740"}
    ).json()
    assert Decimal(waiting["closing_balance"]) == Decimal("-250.00")
    summary = _summary(client, company["id"], THIS_YEAR)
    assert summary["vat_position"] == "250.00"
    # And the overview can say which part is filed and waits for its payment.
    assert summary["vat_settled"] == "250.00"
    # The year's VAT is still the year's VAT.
    assert summary["output_vat"] == "250.00"

    # Paid: nothing owed for it any more.
    paid = record(
        client,
        company["id"],
        type_code="mva_oppgjor",
        gross_amount="250.00",
        occurred_on=date(THIS_YEAR, 4, 10).isoformat(),
    )
    assert paid.status_code == 201, paid.text
    summary = _summary(client, company["id"], THIS_YEAR)
    assert summary["vat_position"] == "0.00"
    assert summary["vat_settled"] == "0.00"

    # A later term still open is owed beside the paid one.
    record(client, company["id"], type_code="salg_hoy_sats", gross_amount="2500.00")
    summary = _summary(client, company["id"], THIS_YEAR)
    assert summary["vat_position"] == "500.00"
    assert summary["output_vat"] == "750.00"


def test_vat_owed_when_the_books_arrived_counts_until_it_is_paid(client: TestClient) -> None:
    """An opening balance on 2740 is last year's VAT, filed and not yet paid."""
    company = _books(client)
    posted = _post_opening(
        client,
        company["id"],
        [
            opening_line("1920", debit="3000.00"),
            opening_line("2740", credit="1000.00"),
            opening_line("2050", credit="2000.00"),
        ],
    )
    assert posted.status_code == 201, posted.text
    summary = _summary(client, company["id"], LAST_YEAR)
    assert summary["vat_position"] == "1000.00"
    assert summary["vat_settled"] == "1000.00"
    # Nothing of it is this year's VAT.
    assert summary["output_vat"] == "0.00"
    assert summary["input_vat"] == "0.00"
