"""End-to-end tests for companies, the cash-flow register and the bilag archive.

The whole stack runs: HTTP, the service layer, the posting engine and SQLite.
That is the point — the arithmetic is already covered by
``test_cashflow_domain``, so what these check is that a payment survives the
round trip as a balanced, immutable, correctly-scoped voucher.
"""

import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from myntos.core.clock import utc_now
from myntos.domain.bilag import classification
from myntos.domain.ledger.cashflow import CashFlowDirection
from myntos.integrations import loynd, norgesbank
from tests.conftest import PASSWORD, join_company, register, write

ORGNR = "910000004"
OTHER_ORGNR = "910000012"
INVALID_ORGNR = "910000005"

# The smallest thing that is unambiguously a PDF. Validation reads the magic
# number, not the extension or the declared content type.
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"

THIS_YEAR = utc_now().year


def create_company(
    client: TestClient,
    organization_id: int,
    *,
    orgnr: str = ORGNR,
    name: str = "Nordmann Consulting AS",
    vat_registered: bool = True,
) -> dict[str, Any]:
    # Registered by default, because most of what this file tests is how VAT
    # is split. An unregistered company gets no split at all
    # (myntos.domain.ledger.vatstatus), and the tests about that say so.
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": organization_id,
            "orgnr": orgnr,
            "name": name,
            "first_financial_year": THIS_YEAR,
            "vat_registered": vat_registered,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def signed_in_company(client: TestClient, **kwargs: Any) -> dict[str, Any]:
    """Register an account and give it a company to book into."""
    session = register(client, **kwargs)
    organization_id = session["memberships"][0]["organization_id"]
    return create_company(client, organization_id)


def record(
    client: TestClient,
    company_id: int,
    *,
    type_code: str,
    gross_amount: str,
    description: str = "Test",
    occurred_on: str | None = None,
    tax_code: str | None = None,
    payment_method: str = "bank",
    party_id: int | None = None,
) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/cashflow",
        json={
            "type_code": type_code,
            "occurred_on": occurred_on or date(THIS_YEAR, 3, 14).isoformat(),
            "gross_amount": gross_amount,
            "description": description,
            "tax_code": tax_code,
            "payment_method": payment_method,
            "party_id": party_id,
        },
    )


# --- Companies ------------------------------------------------------------------


def test_creating_a_company_seeds_it_ready_to_book_into(client: TestClient) -> None:
    company = signed_in_company(client)
    assert company["orgnr"] == ORGNR
    # A payment posts immediately, which is only possible if the kontoplan, the
    # VAT codes and the financial year were all created with the company.
    assert (
        record(client, company["id"], type_code="varekjop", gross_amount="100.00").status_code
        == 201
    )


def test_orgnr_check_digit_is_verified(client: TestClient) -> None:
    session = register(client)
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": session["memberships"][0]["organization_id"],
            "orgnr": INVALID_ORGNR,
            "name": "Feil AS",
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_orgnr"


def test_the_same_orgnr_twice_in_one_organization_is_rejected(client: TestClient) -> None:
    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    create_company(client, organization_id)
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={"organization_id": organization_id, "orgnr": ORGNR, "name": "Igjen AS"},
    )
    assert response.status_code == 409


def test_two_organizations_may_keep_books_for_the_same_orgnr(client: TestClient) -> None:
    """A client can change accountant without the old one blocking the new one."""
    first = register(client, "kari@example.no")
    create_company(client, first["memberships"][0]["organization_id"])
    write(client, "POST", "/api/v1/auth/logout")

    second = register(client, "ola@example.no", full_name="Ola Nordmann")
    company = create_company(client, second["memberships"][0]["organization_id"])
    assert company["orgnr"] == ORGNR


def test_another_organizations_company_is_not_found(client: TestClient) -> None:
    owner = register(client, "kari@example.no")
    company = create_company(client, owner["memberships"][0]["organization_id"])
    write(client, "POST", "/api/v1/auth/logout")

    register(client, "fremmed@example.no", full_name="Fremmed Person")
    assert client.get(f"/api/v1/companies/{company['id']}").status_code == 404
    assert client.get(f"/api/v1/companies/{company['id']}/cashflow").status_code == 404


def test_one_account_lists_every_company_it_keeps_books_for(client: TestClient) -> None:
    """The regnskapsfører case, and what the company picker in the UI is drawn from."""
    session = register(client, "kari@example.no", organization_name="Fjord Regnskap AS")
    organization_id = session["memberships"][0]["organization_id"]
    first = create_company(client, organization_id, orgnr=ORGNR, name="Klient En AS")
    second = create_company(client, organization_id, orgnr=OTHER_ORGNR, name="Klient To AS")

    listed = client.get("/api/v1/companies").json()
    assert {company["orgnr"] for company in listed} == {ORGNR, OTHER_ORGNR}
    # Both reachable on their own, and each with its own set of books: an entry
    # booked in one must not turn up in the other.
    for company in (first, second):
        assert client.get(f"/api/v1/companies/{company['id']}").status_code == 200

    record(client, first["id"], type_code="salg_hoy_sats", gross_amount="1250.00")
    assert client.get(f"/api/v1/companies/{first['id']}/cashflow").json() != []
    assert client.get(f"/api/v1/companies/{second['id']}/cashflow").json() == []


def test_listing_companies_only_shows_your_own(client: TestClient) -> None:
    owner = register(client, "kari@example.no")
    create_company(client, owner["memberships"][0]["organization_id"])
    write(client, "POST", "/api/v1/auth/logout")

    other = register(client, "ola@example.no", full_name="Ola Nordmann")
    create_company(client, other["memberships"][0]["organization_id"], orgnr=OTHER_ORGNR)

    listed = client.get("/api/v1/companies").json()
    assert [company["orgnr"] for company in listed] == [OTHER_ORGNR]


# --- Recording payments ---------------------------------------------------------


def test_money_in_splits_the_gross_amount_into_net_and_vat(client: TestClient) -> None:
    company = signed_in_company(client)
    response = record(
        client,
        company["id"],
        type_code="salg_hoy_sats",
        gross_amount="1250.00",
        description="Faktura 1",
    )
    assert response.status_code == 201, response.text
    entry = response.json()

    assert entry["direction"] == "inn"
    assert entry["gross_amount"] == "1250.00"
    assert entry["net_amount"] == "1000.00"
    assert entry["vat_amount"] == "250.00"
    assert entry["account_number"] == "3000"
    assert entry["tax_code"] == "3"
    assert entry["voucher_no"] == f"{THIS_YEAR}-0001"


def test_money_out_books_to_the_cost_account(client: TestClient) -> None:
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="kontorrekvisita", gross_amount="625.00").json()

    assert entry["direction"] == "ut"
    assert entry["net_amount"] == "500.00"
    assert entry["vat_amount"] == "125.00"
    assert entry["account_number"] == "6800"
    assert entry["tax_code"] == "1"


def test_a_refund_reduces_the_cost_and_takes_the_deduction_back(client: TestClient) -> None:
    """Money in that is not income, which the account number alone cannot say.

    A refund goes back to the account the purchase came from, so the cost goes
    down rather than turnover going up, and the VAT on it is the deduction
    returning rather than tax owed on a sale. Booked as "Salg med 25 % mva" the
    eBay refund would have reported 5 253 kroner of output VAT on a purchase
    that was cancelled.
    """
    company = signed_in_company(client)
    purchase = record(client, company["id"], type_code="varekjop", gross_amount="1250.00").json()
    assert purchase["vat_amount"] == "250.00"

    refund = record(
        client,
        company["id"],
        type_code="refusjon_varekjop",
        gross_amount="1250.00",
        description="Refundert av leverandøren",
    ).json()

    # Money in, and on the same account as the purchase it undoes.
    assert refund["direction"] == "inn"
    assert refund["account_number"] == "4000"
    assert refund["net_amount"] == "1000.00"
    assert refund["vat_amount"] == "250.00"
    assert refund["tax_code"] == "1"

    # The deduction is gone, and nothing is owed: 2710 was credited, not 2700.
    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    assert summary["input_vat"] == "0.00"
    assert summary["output_vat"] == "0.00"


def test_a_kreditnota_reduces_the_sale_and_gives_the_output_vat_back(
    client: TestClient,
) -> None:
    """Money out that is not a cost, which is the refund case in reverse.

    A cancelled sale has to leave turnover and take its output VAT with it. The
    old rule picked the VAT account off the bank's direction — money out meant
    2710 — so a kreditnota claimed a deduction on goods the company had sold
    while leaving the 250 kroner it charged the customer standing as tax owed.
    Both halves are wrong by the same amount, so the summary is the test.
    """
    company = signed_in_company(client)
    sale = record(client, company["id"], type_code="salg_hoy_sats", gross_amount="1250.00").json()
    assert sale["vat_amount"] == "250.00"

    note = record(
        client,
        company["id"],
        type_code="kreditnota_salg_hoy_sats",
        gross_amount="1250.00",
        description="Kreditnota 1, kunden returnerte varen",
    ).json()

    # Money out, and on the same account as the sale it undoes.
    assert note["direction"] == "ut"
    assert note["account_number"] == "3000"
    assert note["net_amount"] == "1000.00"
    assert note["vat_amount"] == "250.00"
    assert note["tax_code"] == "3"

    # Nothing owed and no deduction invented: 2700 was debited, not 2710.
    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    assert summary["output_vat"] == "0.00"
    assert summary["input_vat"] == "0.00"


def test_every_sales_category_has_a_kreditnota_that_matches_it(client: TestClient) -> None:
    """The mirror of the refund catalogue, and derived for the same reason."""
    register(client)
    catalogue = client.get("/api/v1/cashflow/types").json()
    by_code = {entry["code"]: entry for entry in catalogue["types"]}

    notes = {code: entry for code, entry in by_code.items() if code.startswith("kreditnota_")}
    assert len(notes) == 6
    for code, entry in notes.items():
        sale = by_code[code.removeprefix("kreditnota_")]
        assert entry["direction"] == "ut"
        assert sale["direction"] == "inn"
        assert entry["account_number"] == sale["account_number"]
        assert entry["default_tax_code"] == sale["default_tax_code"]
        assert entry["reverses"] == sale["code"]


def test_settling_a_filed_mva_melding_is_not_a_new_cost(client: TestClient) -> None:
    """Paying the MVA-melding moves the debt, not the result or a new deduction.

    Before 2740 existed the only categories that fitted were a cost, which
    deducts VAT already deducted, or a private withdrawal, which turns the
    company's tax into the owner's spending money.
    """
    company = signed_in_company(client)
    entry = record(
        client,
        company["id"],
        type_code="mva_oppgjor",
        gross_amount="12500.00",
        description="Mva-melding 3. termin",
    ).json()

    assert entry["account_number"] == "2740"
    assert entry["net_amount"] == "12500.00"
    assert entry["vat_amount"] == "0.00"

    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    assert summary["input_vat"] == "0.00"
    assert summary["output_vat"] == "0.00"


def test_a_loan_and_its_instalments_never_reach_the_result(client: TestClient) -> None:
    """Borrowed money is not income and repaying it is not a cost.

    Both legs land on 2220, so drawing 50 000 and repaying 5 000 of it leaves
    45 000 of debt and nothing at all in the income statement. Booked as income
    and cost the same 5 000 would be deducted twice: once when the loan bought
    something and again when it was paid back.
    """
    company = signed_in_company(client)
    drawn = record(client, company["id"], type_code="laneopptak", gross_amount="50000.00").json()
    repaid = record(client, company["id"], type_code="avdrag_lan", gross_amount="5000.00").json()

    assert drawn["account_number"] == "2220"
    assert repaid["account_number"] == "2220"
    assert (drawn["vat_amount"], repaid["vat_amount"]) == ("0.00", "0.00")


def test_every_cost_category_has_a_refund_that_matches_it(client: TestClient) -> None:
    """Derived, not written out, so the two cannot drift apart.

    A refund booked to a different account than its purchase leaves both the
    cost and the deduction overstated, and nothing in the books says so.
    """
    register(client)
    catalogue = client.get("/api/v1/cashflow/types").json()
    by_code = {entry["code"]: entry for entry in catalogue["types"]}

    refunds = {code: entry for code, entry in by_code.items() if code.startswith("refusjon_")}
    # Seventeen since border VAT got a category of its own: Tolletaten does pay
    # import VAT back, on goods returned or over-declared, and that refund
    # reduces the deduction exactly like any other.
    assert len(refunds) == 17
    for code, entry in refunds.items():
        cost = by_code[code.removeprefix("refusjon_")]
        assert entry["direction"] == "inn"
        assert cost["direction"] == "ut"
        assert entry["account_number"] == cost["account_number"]
        assert entry["default_tax_code"] == cost["default_tax_code"]
        # Sent so the form can group them apart from the ways of earning money.
        assert entry["reverses"] == cost["code"]
        assert cost["reverses"] is None

    # Payroll is left out on purpose: money back on wages is refusjon av
    # sykepenger, which belongs on its own income account and not as a credit
    # to 5000. Equity already has its own money-in twin.
    assert "refusjon_lonn" not in by_code
    assert "refusjon_arbeidsgiveravgift" not in by_code
    assert "refusjon_privatuttak" not in by_code


def test_a_type_without_vat_books_the_whole_amount(client: TestClient) -> None:
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="lonn", gross_amount="30000.00").json()

    assert entry["net_amount"] == "30000.00"
    assert entry["vat_amount"] == "0.00"
    assert entry["tax_code"] == "0"


def test_the_default_vat_code_can_be_overridden(client: TestClient) -> None:
    """Rented premises without VAT — the note on the type says to expect this."""
    company = signed_in_company(client)
    entry = record(
        client, company["id"], type_code="husleie", gross_amount="10000.00", tax_code="0"
    ).json()

    assert entry["tax_code"] == "0"
    assert entry["vat_amount"] == "0.00"


def test_cash_payments_use_the_cash_account(client: TestClient) -> None:
    company = signed_in_company(client)
    entry = record(
        client,
        company["id"],
        type_code="reise",
        gross_amount="450.00",
        payment_method="kontant",
    ).json()
    assert entry["payment_account"] == "1900"


def test_voucher_numbers_are_sequential(client: TestClient) -> None:
    company = signed_in_company(client)
    numbers = [
        record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()[
            "voucher_no"
        ]
        for _ in range(3)
    ]
    assert numbers == [f"{THIS_YEAR}-000{n}" for n in (1, 2, 3)]


def test_an_unknown_type_is_refused(client: TestClient) -> None:
    company = signed_in_company(client)
    response = record(client, company["id"], type_code="pengevask", gross_amount="100.00")
    assert response.status_code == 422
    assert response.json()["code"] == "unknown_cash_flow_type"


def test_an_unknown_vat_code_is_refused(client: TestClient) -> None:
    company = signed_in_company(client)
    response = record(
        client, company["id"], type_code="varekjop", gross_amount="100.00", tax_code="99"
    )
    assert response.status_code == 422
    assert response.json()["code"] == "unknown_tax_code"


def test_a_date_before_the_first_financial_year_is_refused(client: TestClient) -> None:
    """The seeded VAT rates are not claimed to hold before the books start."""
    company = signed_in_company(client)
    response = record(
        client,
        company["id"],
        type_code="varekjop",
        gross_amount="100.00",
        occurred_on=date(THIS_YEAR - 3, 6, 1).isoformat(),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "period_outside_books"


def test_a_later_year_opens_itself(client: TestClient) -> None:
    company = signed_in_company(client)
    response = record(
        client,
        company["id"],
        type_code="varekjop",
        gross_amount="100.00",
        occurred_on=date(THIS_YEAR + 1, 2, 1).isoformat(),
    )
    assert response.status_code == 201
    assert response.json()["voucher_no"] == f"{THIS_YEAR + 1}-0001"


def test_a_zero_amount_is_refused(client: TestClient) -> None:
    company = signed_in_company(client)
    assert (
        record(client, company["id"], type_code="varekjop", gross_amount="0.00").status_code == 422
    )


# --- Listing and summary --------------------------------------------------------


def test_the_register_lists_what_was_recorded(client: TestClient) -> None:
    company = signed_in_company(client)
    record(client, company["id"], type_code="salg_hoy_sats", gross_amount="1250.00")
    record(client, company["id"], type_code="varekjop", gross_amount="500.00")

    entries = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    assert len(entries) == 2
    assert {entry["direction"] for entry in entries} == {"inn", "ut"}
    assert all(entry["type_label"] for entry in entries)


def test_the_summary_adds_up(client: TestClient) -> None:
    company = signed_in_company(client)
    record(client, company["id"], type_code="salg_hoy_sats", gross_amount="1250.00")
    record(client, company["id"], type_code="salg_hoy_sats", gross_amount="2500.00")
    record(client, company["id"], type_code="kontorrekvisita", gross_amount="625.00")

    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    assert summary["total_in"] == "3750.00"
    assert summary["total_out"] == "625.00"
    assert summary["net"] == "3125.00"
    assert summary["entry_count"] == 3
    assert summary["output_vat"] == "750.00"
    assert summary["input_vat"] == "125.00"
    # What would be owed to Skatteetaten for the period.
    assert summary["vat_position"] == "625.00"
    assert summary["cash_position"] == "3125.00"


def test_the_summary_breaks_down_by_type(client: TestClient) -> None:
    company = signed_in_company(client)
    record(client, company["id"], type_code="salg_hoy_sats", gross_amount="1000.00")
    record(client, company["id"], type_code="lonn", gross_amount="4000.00")

    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    by_type = {item["type_code"]: item for item in summary["by_type"]}
    assert by_type["lonn"]["total"] == "4000.00"
    assert by_type["lonn"]["direction"] == "ut"
    assert by_type["salg_hoy_sats"]["type_label"] == "Salg med 25 % mva"


def test_the_summary_is_scoped_to_the_financial_year(client: TestClient) -> None:
    company = signed_in_company(client)
    record(client, company["id"], type_code="salg_hoy_sats", gross_amount="1000.00")
    record(
        client,
        company["id"],
        type_code="salg_hoy_sats",
        gross_amount="8000.00",
        occurred_on=date(THIS_YEAR + 1, 1, 5).isoformat(),
    )

    this_year = client.get(
        f"/api/v1/companies/{company['id']}/cashflow/summary?financial_year={THIS_YEAR}"
    ).json()
    assert this_year["total_in"] == "1000.00"
    # The bank balance is a position, not a movement: it spans both years.
    assert this_year["cash_position"] == "9000.00"


# --- Corrections ----------------------------------------------------------------


def test_a_reversal_nets_the_entry_out(client: TestClient) -> None:
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="salg_hoy_sats", gross_amount="1250.00").json()

    response = write(
        client, "POST", f"/api/v1/companies/{company['id']}/cashflow/{entry['id']}/reversal"
    )
    assert response.status_code == 201, response.text
    reversal = response.json()
    assert reversal["is_reversal"] is True
    assert reversal["direction"] == "ut"
    assert reversal["gross_amount"] == "1250.00"

    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    assert summary["net"] == "0.00"
    assert summary["output_vat"] == "0.00"


def test_an_entry_cannot_be_reversed_twice(client: TestClient) -> None:
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()
    url = f"/api/v1/companies/{company['id']}/cashflow/{entry['id']}/reversal"

    assert write(client, "POST", url).status_code == 201
    second = write(client, "POST", url)
    assert second.status_code == 409
    assert second.json()["code"] == "already_reversed"


def test_a_reversed_entry_says_so_in_the_listing(client: TestClient) -> None:
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()
    write(client, "POST", f"/api/v1/companies/{company['id']}/cashflow/{entry['id']}/reversal")

    entries = {e["id"]: e for e in client.get(f"/api/v1/companies/{company['id']}/cashflow").json()}
    assert entries[entry["id"]]["reversed_by_id"] is not None


def test_reversing_a_voucher_from_another_company_is_not_found(client: TestClient) -> None:
    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    first = create_company(client, organization_id)
    second = create_company(client, organization_id, orgnr=OTHER_ORGNR, name="Andre AS")
    entry = record(client, first["id"], type_code="varekjop", gross_amount="100.00").json()

    response = write(
        client, "POST", f"/api/v1/companies/{second['id']}/cashflow/{entry['id']}/reversal"
    )
    assert response.status_code == 404


# --- Who entered it -------------------------------------------------------------


def sign_in(client: TestClient, email: str) -> None:
    """Become an account that already exists — ``join_company`` left us as the
    invitee, and some of these tests have to read the register as the owner."""
    write(client, "POST", "/api/v1/auth/logout")
    response = write(
        client, "POST", "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text


def listing(client: TestClient, company_id: int) -> dict[int, Any]:
    """The register keyed by voucher id, which is how these tests read it."""
    response = client.get(f"/api/v1/companies/{company_id}/cashflow")
    assert response.status_code == 200, response.text
    return {entry["id"]: entry for entry in response.json()}


def test_an_entry_names_the_person_who_recorded_it(client: TestClient) -> None:
    company = signed_in_company(client, email="kari@example.no", full_name="Kari Nordmann")
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()

    assert entry["created_by_name"] == "Kari Nordmann"
    assert entry["created_by_id"] is not None
    # And the same answer when read back, not only in the echo of the write.
    assert listing(client, company["id"])[entry["id"]]["created_by_name"] == "Kari Nordmann"


def test_two_colleagues_entries_are_told_apart(client: TestClient) -> None:
    """The whole reason the column exists: one journal, two people writing in it."""
    owner = register(client, "kari@example.no", full_name="Kari Nordmann")
    company = create_company(client, owner["memberships"][0]["organization_id"])
    mine = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()

    join_company(client, company["id"], email="ola@example.no", full_name="Ola Nordmann")
    theirs = record(client, company["id"], type_code="varekjop", gross_amount="200.00").json()

    entries = listing(client, company["id"])
    assert entries[mine["id"]]["created_by_name"] == "Kari Nordmann"
    assert entries[theirs["id"]]["created_by_name"] == "Ola Nordmann"
    assert entries[mine["id"]]["created_by_id"] != entries[theirs["id"]]["created_by_id"]


def test_a_reversal_names_whoever_undid_it_not_whoever_booked_it(client: TestClient) -> None:
    """The two are different people often enough that copying would misattribute."""
    owner = register(client, "kari@example.no", full_name="Kari Nordmann")
    company = create_company(client, owner["memberships"][0]["organization_id"])
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()

    join_company(client, company["id"], email="ola@example.no", full_name="Ola Nordmann")
    reversal = write(
        client, "POST", f"/api/v1/companies/{company['id']}/cashflow/{entry['id']}/reversal"
    ).json()

    assert reversal["created_by_name"] == "Ola Nordmann"
    entries = listing(client, company["id"])
    assert entries[entry["id"]]["created_by_name"] == "Kari Nordmann"


def test_a_correction_names_whoever_corrected_it(client: TestClient) -> None:
    """All three vouchers are attributed, and the two new ones to the corrector."""
    owner = register(client, "kari@example.no", full_name="Kari Nordmann")
    company = create_company(client, owner["memberships"][0]["organization_id"])
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()

    join_company(client, company["id"], email="ola@example.no", full_name="Ola Nordmann")
    corrected = write(
        client,
        "PUT",
        f"/api/v1/companies/{company['id']}/cashflow/{entry['id']}",
        json={
            "type_code": "varekjop",
            "occurred_on": date(THIS_YEAR, 3, 14).isoformat(),
            "gross_amount": "125.00",
            "description": "Rettet",
            "tax_code": None,
            "payment_method": "bank",
            "party_id": None,
        },
    ).json()

    assert corrected["created_by_name"] == "Ola Nordmann"
    entries = listing(client, company["id"])
    assert entries[entry["id"]]["created_by_name"] == "Kari Nordmann"
    # The reversal the correction posted is Ola's too, so a reader can see that
    # one person made the entry and another decided it was wrong.
    reversal_id = entries[entry["id"]]["reversed_by_id"]
    assert entries[reversal_id]["created_by_name"] == "Ola Nordmann"


def test_an_entry_still_names_a_colleague_who_has_since_left(client: TestClient) -> None:
    """Removing somebody's access does not unsay what they did.

    The membership is what decides who may open the books; the entry records who
    wrote in them. Dropping the name along with the access would leave the
    register unable to answer "who booked this" for exactly the person somebody
    is asking about.
    """
    owner = register(client, "kari@example.no", full_name="Kari Nordmann")
    company = create_company(client, owner["memberships"][0]["organization_id"])
    colleague = join_company(
        client, company["id"], email="ola@example.no", full_name="Ola Nordmann"
    )
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()

    sign_in(client, "kari@example.no")
    removed = write(
        client,
        "DELETE",
        f"/api/v1/companies/{company['id']}/members/{colleague['user']['id']}",
    )
    assert removed.status_code == 204, removed.text

    assert listing(client, company["id"])[entry["id"]]["created_by_name"] == "Ola Nordmann"


# --- Bilag documents ------------------------------------------------------------


def upload(client: TestClient, company_id: int, voucher_id: int, content: bytes, name: str) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/cashflow/{voucher_id}/dokument",
        files={"file": (name, content, "application/pdf")},
    )


def test_the_register_is_told_what_each_attachment_actually_is(client: TestClient) -> None:
    """The badge beside a filed document names its kind, and it has to be true.

    Nine screenshots of an AliExpress order came back labelled "PDF 394 kB" and
    so on, because the badge was a constant and the response carried no type at
    all. The archive is the part of this that has to be right in five years.
    """
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()

    upload(client, company["id"], entry["id"], PDF_BYTES, "kvittering.pdf")
    upload(client, company["id"], entry["id"], png_bytes(), "skjermbilde.png")

    listed = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    kinds = {row["filename"]: row["content_type"] for row in listed[0]["attachments"]}
    assert kinds == {"kvittering.pdf": "application/pdf", "skjermbilde.png": "image/png"}


def test_a_pdf_can_be_attached_and_read_back(client: TestClient) -> None:
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()

    response = upload(client, company["id"], entry["id"], PDF_BYTES, "kvittering.pdf")
    assert response.status_code == 201, response.text
    attachment = response.json()
    assert attachment["filename"] == "kvittering.pdf"
    assert attachment["content_type"] == "application/pdf"
    assert attachment["byte_size"] == len(PDF_BYTES)

    download = client.get(f"/api/v1/companies/{company['id']}/dokument/{attachment['id']}")
    assert download.status_code == 200
    assert download.content == PDF_BYTES
    # Never inline: the bytes came from a user.
    assert download.headers["content-disposition"].startswith("attachment;")


def test_an_attached_document_shows_up_on_the_entry(client: TestClient) -> None:
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()
    upload(client, company["id"], entry["id"], PDF_BYTES, "bilag.pdf")

    listed = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    assert len(listed[0]["attachments"]) == 1


def test_a_file_that_is_not_a_pdf_is_refused(client: TestClient) -> None:
    """The magic number decides, not the declared content type."""
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()

    response = upload(client, company["id"], entry["id"], b"MZ\x90\x00 not a pdf", "faktura.pdf")
    assert response.status_code == 415
    assert response.json()["code"] == "unsupported_document"


def test_a_dangerous_filename_is_neutralised(client: TestClient) -> None:
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()

    attachment = upload(
        client, company["id"], entry["id"], PDF_BYTES, '../../etc/pas"swd.pdf'
    ).json()
    assert "/" not in attachment["filename"]
    assert '"' not in attachment["filename"]


def test_another_organizations_document_is_not_reachable(client: TestClient) -> None:
    owner = register(client, "kari@example.no")
    company = create_company(client, owner["memberships"][0]["organization_id"])
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()
    attachment = upload(client, company["id"], entry["id"], PDF_BYTES, "bilag.pdf").json()
    write(client, "POST", "/api/v1/auth/logout")

    stranger = register(client, "fremmed@example.no", full_name="Fremmed Person")
    own = create_company(client, stranger["memberships"][0]["organization_id"], orgnr=OTHER_ORGNR)
    # Both the stranger's own company id and the real one must fail.
    assert (
        client.get(f"/api/v1/companies/{own['id']}/dokument/{attachment['id']}").status_code == 404
    )
    assert (
        client.get(f"/api/v1/companies/{company['id']}/dokument/{attachment['id']}").status_code
        == 404
    )


# --- Reading a faktura ----------------------------------------------------------


def text_pdf(lines: list[str]) -> bytes:
    """A one-page PDF whose text layer holds ``lines``.

    Written out by hand rather than with a PDF library: the point of these tests
    is that an ordinary invoice survives the round trip through pypdf, and a
    generator from the same ecosystem would prove less than it appears to.
    WinAnsi encoding, so ``Å`` and ``ø`` come back as themselves.
    """
    drawn = ["BT", "/F1 11 Tf", "14 TL", "40 800 Td"]
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        drawn.append(f"({escaped}) Tj T*")
    drawn.append("ET")
    stream = "\n".join(drawn).encode("cp1252")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    start_xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start_xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


# The supplier carries OTHER_ORGNR: ORGNR belongs to the company being booked
# for, and an invoice printing our own number is one we issued ourselves.
INVOICE_LINES = [
    "Telenor Norge AS",
    "Snarøyveien 30",
    "1360 Fornebu",
    f"Org.nr. {OTHER_ORGNR} MVA",
    "FAKTURA",
    "Fakturanummer: 104588",
    f"Fakturadato: 14.03.{THIS_YEAR}",
    f"Forfallsdato: 28.03.{THIS_YEAR}",
    "Kunde: Nordmann Consulting AS",
    "Mobilabonnement mars              798,00",
    "Sum eks. mva                   1 447,00",
    "Mva 25 %                         361,75",
    "Å betale                       1 808,75",
]


def interpret(
    client: TestClient, company_id: int, content: bytes, name: str = "faktura.pdf"
) -> Any:
    """The ordinary one-document upload, which is still the common case."""
    return interpret_many(client, company_id, [(name, content)])


def interpret_many(
    client: TestClient,
    company_id: int,
    documents: list[tuple[str, bytes]],
    *,
    forklaring: str | None = None,
) -> Any:
    """Several files, and optionally the sentence the user typed about them.

    The field is ``files`` and repeats, which is how a multipart form carries a
    list — the same shape ``<input type="file" multiple>`` produces.
    """
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/cashflow/forslag",
        files=[("files", (name, content, _content_type(content))) for name, content in documents],
        **({"data": {"forklaring": forklaring}} if forklaring is not None else {}),
    )


def _content_type(content: bytes) -> str:
    """What the fixture bytes are, so a test never has to say it twice."""
    if content.startswith(b"\x89PNG"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return "application/pdf"


def test_an_uploaded_invoice_proposes_the_whole_entry(client: TestClient) -> None:
    company = signed_in_company(client)

    response = interpret(client, company["id"], text_pdf(INVOICE_LINES))
    assert response.status_code == 200, response.text
    suggestion = response.json()

    assert suggestion["direction"] == "ut"
    assert suggestion["occurred_on"] == date(THIS_YEAR, 3, 14).isoformat()
    assert suggestion["gross_amount"] == "1808.75"
    assert suggestion["vat_amount"] == "361.75"
    assert suggestion["type_code"] == "telefon_internett"
    assert suggestion["type_label"] == "Telefon og internett"
    assert suggestion["tax_code"] == "1"
    assert suggestion["counterparty_orgnr"] == OTHER_ORGNR
    assert suggestion["description"] == "Faktura 104588, Telenor Norge AS"
    assert "gross_amount" in suggestion["filled"]


def test_a_proposal_can_be_posted_unchanged(client: TestClient) -> None:
    """The whole point: what comes back fits the form it is going into."""
    company = signed_in_company(client)
    suggestion = interpret(client, company["id"], text_pdf(INVOICE_LINES)).json()

    entry = record(
        client,
        company["id"],
        type_code=suggestion["type_code"],
        occurred_on=suggestion["occurred_on"],
        gross_amount=suggestion["gross_amount"],
        description=suggestion["description"],
        tax_code=suggestion["tax_code"],
    ).json()

    assert entry["gross_amount"] == "1808.75"
    assert entry["vat_amount"] == "361.75"
    assert entry["account_number"] == "6900"


def test_a_registered_supplier_is_recognised_and_proposed(client: TestClient) -> None:
    company = signed_in_company(client)
    party = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/parties",
        json={"party_type": "leverandor", "name": "Telenor Norge AS", "orgnr": OTHER_ORGNR},
    ).json()

    suggestion = interpret(client, company["id"], text_pdf(INVOICE_LINES)).json()
    assert suggestion["party_id"] == party["id"]


def test_reading_a_document_stores_nothing(client: TestClient) -> None:
    """A user who uploads the wrong file leaves nothing in an append-only archive."""
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()

    interpret(client, company["id"], text_pdf(INVOICE_LINES))

    listed = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    assert [e["id"] for e in listed] == [entry["id"]]
    assert listed[0]["attachments"] == []


def test_a_pdf_with_no_text_layer_proposes_nothing_and_explains(client: TestClient) -> None:
    """A photographed receipt. Not an error — the user fills the form in by hand."""
    company = signed_in_company(client)

    suggestion = interpret(client, company["id"], PDF_BYTES).json()
    assert suggestion["gross_amount"] is None
    assert suggestion["filled"] == []
    assert suggestion["warnings"] != []


def test_a_file_that_is_not_a_pdf_is_refused_before_it_is_read(client: TestClient) -> None:
    company = signed_in_company(client)

    response = interpret(client, company["id"], b"MZ\x90\x00 not a pdf")
    assert response.status_code == 415
    assert response.json()["code"] == "unsupported_document"


def test_the_model_picks_the_category_and_the_answer_says_so(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Løynd chooses from the catalogue; the entry form gets the whole consequence.

    The invoice says "Mobilabonnement", which our own keyword list reads as
    telephony. The model is stubbed into answering "kontorrekvisita" instead, so
    the category in the response can only have come from it — along with the
    account that follows from it and the label saying where it came from.
    """
    # The menu the endpoint builds, not the whole catalogue: a purchase is
    # offered only the expense half, so the numbers differ and a stub that
    # answered by position on the full list would name a different category.
    menu = classification.build_menu(CashFlowDirection.UT)
    rekvisita = next(item for item in menu if item.type_code == "kontorrekvisita")

    async def fake_complete(*, system: str, prompt: str, max_tokens: int = 16) -> str:
        del system, prompt, max_tokens
        return str(rekvisita.number)

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, fake_complete)

    company = signed_in_company(client)
    suggestion = interpret(client, company["id"], text_pdf(INVOICE_LINES)).json()

    assert suggestion["type_code"] == "kontorrekvisita"
    assert suggestion["type_source"] == "ki"
    # The rest of the reading is untouched: amounts and dates are printed facts.
    assert suggestion["gross_amount"] == "1808.75"
    assert suggestion["occurred_on"] == date(THIS_YEAR, 3, 14).isoformat()


def test_a_document_the_model_cannot_place_falls_back_to_our_own_reading(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refusing(**kwargs: Any) -> str:
        raise loynd.LoyndUnavailableError("nede")

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, refusing)

    company = signed_in_company(client)
    suggestion = interpret(client, company["id"], text_pdf(INVOICE_LINES)).json()

    assert suggestion["type_code"] == "telefon_internett"
    assert suggestion["type_source"] == "dokument"


# What a receipt from a foreign webshop looks like once pypdf is done with it.
# Not one of the words our own labels look for, a date written month first, and a
# total in pounds — see tests/test_bilag_extraction.py for the reading itself.
RECEIPT_LINES = [
    "Order information",
    "Buyer andreali_7",
    "Seller daisyaus-0",
    f"Placed on Aug 23, {THIS_YEAR}",
    "Payment method Credit Card",
    "Order total",
    "1 item GBP 2,137.00",
    "Order total GBP 2,137.00",
    "Items bought from daisyaus-0",
    "Order number: 10-15068-44333",
    "NEW Apple Mac Studio M3 Ultra 28-Core 96GB RAM 1TB SSD 60-Core GPU",
]


# The model's two answers, as it would give them for the receipt above.
RECEIPT_ANSWER = f"""DATO: Aug 23, {THIS_YEAR}
VALUTA: GBP
MOTPART: daisyaus-0
BESKRIVELSE: Apple Mac Studio til kontoret"""

# The same four lines, every one of them about a document other than this one.
INVENTED_ANSWER = f"""DATO: 01.01.{THIS_YEAR}
VALUTA: USD
MOTPART: eBay Inc
BESKRIVELSE: UKJENT"""


def answering(category: str, fields: str) -> Any:
    """A Løynd that answers both questions, told apart by what was asked."""

    async def complete(*, system: str, prompt: str, max_tokens: int = 16) -> str:
        del system, max_tokens
        return fields if "BESKRIVELSE" in prompt else category

    return complete


def stub_loynd(monkeypatch: pytest.MonkeyPatch, complete: Any) -> None:
    """Stand in for both ways of asking, with one answer behind them.

    The extraction question goes through ``loynd.stream`` in the running
    application, because the description is typed onto the page as it is
    written. Deriving the streaming stub from the non-streaming one keeps every
    test here honest about that — and it means each of them exercises the
    incremental parser rather than leaving it to the handful of tests written
    for it, since the answer arrives in pieces that split labels down the
    middle.
    """
    monkeypatch.setattr(loynd, "complete", complete)

    async def stream(*, system: str, prompt: str, max_tokens: int = 16) -> Any:
        answer = await complete(system=system, prompt=prompt, max_tokens=max_tokens)
        # Seven characters at a time, which lands a chunk boundary inside
        # "BESKRIVELSE" and inside the sentence after it.
        for start in range(0, len(answer), 7):
            yield answer[start : start + 7]

    monkeypatch.setattr(loynd, "stream", stream)


def test_a_receipt_with_none_of_our_words_still_fills_the_form(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case the reading alone could not carry: no labels, no kroner, no orgnr.

    The date and the seller are on the page, so the parser gets them once it
    knows the shapes. What it cannot get is what the payment was for, and that is
    both of Løynd's answers here: the category, and the sentence that goes in the
    ledger next to the order number.
    """
    menu = classification.build_menu(CashFlowDirection.UT)
    inventar = next(item for item in menu if item.type_code == "inventar")
    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, answering(str(inventar.number), RECEIPT_ANSWER))

    company = signed_in_company(client)
    suggestion = interpret(client, company["id"], text_pdf(RECEIPT_LINES)).json()

    assert suggestion["occurred_on"] == date(THIS_YEAR, 8, 23).isoformat()
    assert suggestion["counterparty_name"] == "daisyaus-0"
    assert suggestion["type_code"] == "inventar"
    assert suggestion["description"] == "Ordre 10-15068-44333, Apple Mac Studio til kontoret"
    assert suggestion["sources"]["description"] == "ki"
    assert suggestion["sources"]["occurred_on"] == "dokument"
    # In pounds, so the kroner field stays empty and the total is shown instead.
    assert suggestion["currency_code"] == "GBP"
    assert suggestion["foreign_amount"] == "2137.00"
    assert suggestion["gross_amount"] is None
    # No Norwegian VAT on a GBP receipt, so 0 rather than empty: an empty code
    # becomes the category's 25 % in the form.
    assert suggestion["tax_code"] == "0"
    # The separator is a non-breaking space; the part worth asserting on is that
    # the sentence names the total in the currency the document actually used.
    assert any("137,00 GBP" in warning for warning in suggestion["warnings"])


def test_a_model_answer_the_document_does_not_support_changes_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invented is not the same as unavailable, but it ends the same way."""
    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, answering("0", INVENTED_ANSWER))

    company = signed_in_company(client)
    suggestion = interpret(client, company["id"], text_pdf(RECEIPT_LINES)).json()

    assert suggestion["occurred_on"] == date(THIS_YEAR, 8, 23).isoformat()
    assert suggestion["currency_code"] == "GBP"
    assert suggestion["counterparty_name"] == "daisyaus-0"
    assert suggestion["description"] == "Ordre 10-15068-44333, daisyaus-0"
    assert suggestion["sources"]["description"] == "dokument"


# --- Permissions ----------------------------------------------------------------


def test_a_viewer_may_read_the_register_but_not_add_to_it(client: TestClient) -> None:
    owner = register(client, "kari@example.no")
    organization_id = owner["memberships"][0]["organization_id"]
    company = create_company(client, organization_id)

    join_company(client, company["id"], email="viewer@example.no", role="viewer")

    assert client.get(f"/api/v1/companies/{company['id']}/cashflow").status_code == 200
    denied = record(client, company["id"], type_code="varekjop", gross_amount="100.00")
    assert denied.status_code == 403
    assert denied.json()["code"] == "permission_denied"


def test_an_invited_colleague_cannot_add_books_to_the_account(client: TestClient) -> None:
    """Being asked to help with one company is not a key to the account.

    Setting up a set of books is an administrative act on an *account*, and an
    invitation grants access to one company and nothing else: no membership of
    the sender's account, so no way to add a second company to it. 404 rather
    than 403, because a non-member is told nothing about whether the account
    exists.
    """
    owner = register(client, "kari@example.no")
    organization_id = owner["memberships"][0]["organization_id"]
    company = create_company(client, organization_id)

    join_company(client, company["id"], email="medlem@example.no", role="member")

    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={"organization_id": organization_id, "orgnr": ORGNR, "name": "Snik AS"},
    )
    assert response.status_code == 404


def test_the_register_needs_a_session(client: TestClient) -> None:
    assert client.get("/api/v1/companies").status_code == 401
    assert client.get("/api/v1/cashflow/types").status_code == 401


def test_the_catalogue_lists_types_with_their_notes(client: TestClient) -> None:
    register(client)
    catalogue = client.get("/api/v1/cashflow/types").json()

    assert catalogue["max_attachment_bytes"] == 25 * 1024 * 1024
    assert {entry["direction"] for entry in catalogue["types"]} == {"inn", "ut"}
    assert all(entry["note"] for entry in catalogue["types"])
    assert any(code["code"] == "3" for code in catalogue["tax_codes"])


# --- Kroner, the file's own name, and the switch that turns the model off ---------


def quoting(rate: str, quoted_on: date) -> Any:
    """A Norges Bank that answers with one fixed rate.

    ``quoted_on`` is the day the rate is *published* for, which is not the day
    asked about: the receipt is dated a Sunday and the last quote before it is
    the Friday's. The real lookup does the same walk backwards, and the stub
    ignores its ``on`` argument for exactly that reason.
    """

    async def rate_for(currency_code: str, *, on: date) -> norgesbank.Rate:
        del on
        return norgesbank.Rate(
            currency_code=currency_code,
            quoted_on=quoted_on,
            kroner_per_unit=Decimal(rate),
        )

    return rate_for


def test_a_receipt_in_pounds_comes_back_in_kroner(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The field that used to be left empty, with the arithmetic next to it.

    Marked "beregnet" rather than "dokument": the document says 2 137,00 GBP and
    says nothing at all about kroner. The user still has to check it against the
    statement, because the card issuer's spread is not on the invoice, but they
    start from a number that is within a percent instead of from nothing.
    """
    monkeypatch.setattr(norgesbank, "rate_for", quoting("12.6853", date(THIS_YEAR, 8, 21)))

    company = signed_in_company(client)
    suggestion = interpret(client, company["id"], text_pdf(RECEIPT_LINES)).json()

    assert suggestion["currency_code"] == "GBP"
    assert suggestion["foreign_amount"] == "2137.00"
    assert suggestion["gross_amount"] == "27108.49"
    assert suggestion["sources"]["gross_amount"] == "beregnet"
    assert suggestion["exchange_rate"] == "12.6853"
    assert suggestion["rate_quoted_on"] == date(THIS_YEAR, 8, 21).isoformat()
    assert any("12,6853" in warning for warning in suggestion["warnings"])
    # 27 108 kroner is under the 30 000 a driftsmiddel starts at from 2024
    # (skatteloven § 14-40), so it may be expensed and nothing is said. The
    # conversion re-running the check is test_bilag_interpretation's.
    assert not any("driftsmiddel" in warning for warning in suggestion["warnings"])


def test_a_rate_that_does_not_arrive_leaves_the_amount_to_the_user(
    client: TestClient,
) -> None:
    """Best-effort. Norges Bank being down is not an error for the user.

    The autouse fixture refuses every lookup, so this is the unconfigured and
    unreachable case at once, and the answer is the one the register gave before
    conversion existed: an empty field and a sentence saying why.
    """
    company = signed_in_company(client)
    suggestion = interpret(client, company["id"], text_pdf(RECEIPT_LINES)).json()

    assert suggestion["gross_amount"] is None
    assert suggestion["exchange_rate"] is None
    assert any("står tomt med vilje" in warning for warning in suggestion["warnings"])


def test_the_filename_names_the_shop_when_the_page_does_not(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A browser names a downloaded receipt after the page it came from.

    On this document that is the only occurrence of the word eBay anywhere the
    text layer can reach, so it is put in front of the model with the excerpt.
    Asserted on the prompt rather than on the answer, because what is being
    tested is that the model was not asked with the shop's name withheld.

    The model gets the title whole. The trimming that turns "Order details eBay"
    into "eBay" is for the seller field, where a name is wanted and "Order
    details" is furniture; here the furniture is a fact about the document and
    worth knowing.
    """
    seen: list[str] = []

    async def complete(*, system: str, prompt: str, max_tokens: int = 16) -> str:
        del system, max_tokens
        seen.append(prompt)
        return "UKJENT"

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, complete)

    company = signed_in_company(client)
    interpret(client, company["id"], text_pdf(RECEIPT_LINES), name="Order details _ eBay.pdf")

    assert seen
    assert all("Dokumentets tittel: Order details eBay" in prompt for prompt in seen)


def test_a_company_with_ki_off_is_never_asked_and_says_so(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off means the document does not leave the building, not a hidden button.

    Neither of the two questions is asked and no file is uploaded to Løynd's
    extractor either — a switch that still sent the bilag and merely ignored the
    answer would be worse than no switch, because it would be a promise that is
    not kept.
    """

    async def refuse(**_: Any) -> str:
        raise AssertionError("the model was asked despite KI being off")

    async def refuse_upload(**_: Any) -> Any:
        raise AssertionError("the document was uploaded despite KI being off")

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, refuse)
    monkeypatch.setattr(loynd, "extract_file", refuse_upload)

    company = signed_in_company(client)
    write(client, "PATCH", f"/api/v1/companies/{company['id']}", json={"ki_enabled": False})

    suggestion = interpret(client, company["id"], text_pdf(RECEIPT_LINES)).json()

    assert suggestion["ki_used"] is False
    # The parser still did its work; only the two questions went unasked.
    assert suggestion["occurred_on"] == date(THIS_YEAR, 8, 23).isoformat()
    assert suggestion["type_code"] is None


def test_the_switch_is_on_by_default_and_survives_a_round_trip(client: TestClient) -> None:
    company = signed_in_company(client)
    assert company["ki_enabled"] is True

    off = write(
        client, "PATCH", f"/api/v1/companies/{company['id']}", json={"ki_enabled": False}
    ).json()
    assert off["ki_enabled"] is False

    # An unrelated edit leaves it alone, which is the whole contract of PATCH
    # here: a field nobody mentioned is a field nobody changed.
    renamed = write(
        client, "PATCH", f"/api/v1/companies/{company['id']}", json={"name": "Nytt navn AS"}
    ).json()
    assert renamed["ki_enabled"] is False


def test_a_purchase_is_never_offered_a_sales_category(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The menu matches the direction the parser read, so it cannot reverse it.

    Given all 26 categories, Løynd files this eBay receipt under "Salg til
    utlandet": a Mac Studio the company bought, booked as export revenue it
    earned. `apply_type` takes the direction from whichever category comes back,
    so the wrong answer does not merely mislabel the entry, it turns money out
    into money in and lands it in næringsspesifikasjonen as income.

    The direction is a reading and the category is the question, so the menu is
    narrowed to what the reading concluded. A reading that is wrong is corrected
    by the user flipping a radio button, which is visible; a direction reversed
    by a category is not.
    """
    seen: list[str] = []

    async def complete(*, system: str, prompt: str, max_tokens: int = 16) -> str:
        del system, max_tokens
        seen.append(prompt)
        return "UKJENT"

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, complete)

    company = signed_in_company(client)
    suggestion = interpret(client, company["id"], text_pdf(RECEIPT_LINES)).json()

    assert suggestion["direction"] == "ut"
    menu_prompt = next(prompt for prompt in seen if "Svar med tallet" in prompt)
    assert "Salg til utlandet" not in menu_prompt
    assert "Verktøy og inventar" in menu_prompt
    # And the direction is stated once rather than repeated on every line, which
    # is what moved loynd-lite off "Uttak til eier" for this document.
    assert "Dette er et kjøp selskapet har gjort" in menu_prompt
    assert "(penger ut)" not in menu_prompt


# --- Editing a posted entry ------------------------------------------------------


def test_editing_an_entry_reverses_it_and_posts_the_correction(client: TestClient) -> None:
    """What the user sees as an edit, and what the ledger keeps.

    The case this exists for: a purchase booked with 25 % inngående mva that
    carried none, because it came from a foreign seller in a foreign currency.
    That is not a payment to undo, it is a payment to record correctly, and
    "25 %" is not a field to overwrite: it decides whether the entry has a line
    on 2710 at all, and 2710 is what the MVA-melding aggregates.
    """
    company = signed_in_company(client)
    original = record(
        client,
        company["id"],
        type_code="inventar",
        gross_amount="27108.49",
        description="Ordre 10-15068-44333, Apple Mac Studio",
    ).json()
    assert original["vat_amount"] == "5421.70"
    assert original["net_amount"] == "21686.79"

    corrected = write(
        client,
        "PUT",
        f"/api/v1/companies/{company['id']}/cashflow/{original['id']}",
        json={
            "type_code": "inventar",
            "occurred_on": original["occurred_on"],
            "gross_amount": "27108.49",
            "description": "Ordre 10-15068-44333, Apple Mac Studio",
            # "0: Ingen merverdiavgiftsbehandling". Not None, which means
            # "use the type default" and would put the 25 % straight back.
            "tax_code": "0",
            "payment_method": "bank",
            "party_id": None,
        },
    )
    assert corrected.status_code == 201, corrected.text
    fixed = corrected.json()
    assert fixed["vat_amount"] == "0.00"
    assert fixed["net_amount"] == "27108.49"

    entries = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    by_voucher = {entry["voucher_no"]: entry for entry in entries}
    # All three, which is the point: the original, the proof it was undone, and
    # the right one. An auditor can follow the correction rather than find a
    # number that quietly changed.
    assert len(by_voucher) == 3
    assert by_voucher[original["voucher_no"]]["reversed_by_id"] is not None
    assert sum(entry["is_reversal"] for entry in entries) == 1
    # And the third link, which is what lets the three be read back as one
    # correction. Without it the replacement is indistinguishable from an
    # unrelated payment posted the same day, and the register can only pair
    # them by guessing from dates and amounts.
    assert fixed["corrects_id"] == original["id"]
    assert by_voucher[original["voucher_no"]]["corrects_id"] is None
    assert [e["corrects_id"] for e in entries if e["is_reversal"]] == [None]
    # The VAT the books claim is now zero: the reversal cancels the 5 421,70 the
    # first posting put on 2710, and the correction adds none back.
    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    assert summary["input_vat"] == "0.00"


def test_an_entry_cannot_be_edited_twice(client: TestClient) -> None:
    """The second attempt finds an entry that is already reversed.

    Worth pinning because the form offers "Rediger" per row and a stale page is
    one back-button away from sending the same edit again. The guard is the
    unique index on the reversal link, not the interface.
    """
    company = signed_in_company(client)
    original = record(client, company["id"], type_code="varekjop", gross_amount="500.00").json()
    payload = {
        "type_code": "varekjop",
        "occurred_on": original["occurred_on"],
        "gross_amount": "600.00",
        "description": "Rettet",
        "tax_code": None,
        "payment_method": "bank",
        "party_id": None,
    }
    path = f"/api/v1/companies/{company['id']}/cashflow/{original['id']}"
    assert write(client, "PUT", path, json=payload).status_code == 201
    assert write(client, "PUT", path, json=payload).status_code == 409


def test_correcting_a_correction_chains_back_to_the_first_entry(client: TestClient) -> None:
    """Rettings can be rettet, and the trail has to survive it.

    The entry that is already reversed cannot be edited again, but the
    replacement can — it is an ordinary posted voucher. Each correction points
    at the one before it, so the register can walk the chain back and show five
    vouchers as one line rather than as three separate payments plus a pair.

    No unique index on the correction link, unlike the reversal one, and this is
    why: the same chain would violate it at the second step if there were.
    """
    company = signed_in_company(client)
    first = record(client, company["id"], type_code="varekjop", gross_amount="500.00").json()

    def correct(voucher_id: int, amount: str) -> dict[str, Any]:
        response = write(
            client,
            "PUT",
            f"/api/v1/companies/{company['id']}/cashflow/{voucher_id}",
            json={
                "type_code": "varekjop",
                "occurred_on": first["occurred_on"],
                "gross_amount": amount,
                "description": f"Rettet til {amount}",
                "tax_code": None,
                "payment_method": "bank",
                "party_id": None,
            },
        )
        assert response.status_code == 201, response.text
        return dict(response.json())

    second = correct(int(first["id"]), "600.00")
    third = correct(int(second["id"]), "700.00")

    assert second["corrects_id"] == first["id"]
    assert third["corrects_id"] == second["id"]

    entries = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    # Five vouchers: three postings and the two reversals between them.
    assert len(entries) == 5
    assert sum(entry["is_reversal"] for entry in entries) == 2
    # Only the last one is still standing, and it is the only one the register
    # shows without being asked.
    live = [
        entry for entry in entries if not entry["is_reversal"] and entry["reversed_by_id"] is None
    ]
    assert [entry["id"] for entry in live] == [third["id"]]


def test_a_correction_is_not_counted_three_times_in_the_years_figures(
    client: TestClient,
) -> None:
    """The overview after an edit, which is where this went wrong in the wild.

    Reproduces the reported case exactly: a 27 108,49 kroner import booked with
    25 % inngående mva it never carried, corrected to innførsel uten
    fradragsrett, plus one small unrelated purchase.

    Before the fix the front page read *inn i år 27 108,49* for a company that
    had been paid nothing all year. Reversing a purchase debits the bank, so
    the reversal looked exactly like income, and the same purchase appeared in
    *ut i år* twice. Only the net was right, because the pair does genuinely
    cancel — which is precisely what makes the error easy to miss.
    """
    company = signed_in_company(client)
    original = record(
        client,
        company["id"],
        type_code="inventar",
        gross_amount="27108.49",
        description="Apple Mac Studio",
    ).json()
    record(client, company["id"], type_code="varekjop", gross_amount="48.86").json()

    corrected = write(
        client,
        "PUT",
        f"/api/v1/companies/{company['id']}/cashflow/{original['id']}",
        json={
            "type_code": "driftsmiddel",
            "occurred_on": original["occurred_on"],
            "gross_amount": "27108.49",
            "description": "Apple Mac Studio",
            "tax_code": "82",
            "payment_method": "bank",
            "party_id": None,
        },
    )
    assert corrected.status_code == 201, corrected.text

    # Four vouchers in the ledger; two payments actually happened.
    assert len(client.get(f"/api/v1/companies/{company['id']}/cashflow").json()) == 4

    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    assert summary["total_in"] == "0.00"
    assert summary["total_out"] == "27157.35"
    assert summary["net"] == "-27157.35"
    assert summary["entry_count"] == 2
    assert summary["cash_position"] == "-27157.35"
    # The purchase is one line under one type, not two lines that disagree.
    assert [(t["type_code"], t["total"]) for t in summary["by_type"]] == [
        ("driftsmiddel", "27108.49"),
        ("varekjop", "48.86"),
    ]
    # 6 777,12 owed on the import with no deduction against it, less the 9,77
    # inngående mva on the small purchase. The reversal cancelled the 5 421,70
    # the first posting had wrongly claimed on 2710.
    assert summary["output_vat"] == "6777.12"
    assert summary["input_vat"] == "9.77"
    assert summary["vat_position"] == "6767.35"


def test_a_failed_correction_leaves_the_original_alone(client: TestClient) -> None:
    """The reason the reversal and the replacement are one request.

    Two calls from the browser can be interrupted between them, and the state
    that leaves behind — reversed, with no replacement — is worse than the
    mistake being corrected. Here the replacement is rejected, and the original
    has to come back untouched rather than reversed.
    """
    company = signed_in_company(client)
    original = record(client, company["id"], type_code="varekjop", gross_amount="500.00").json()

    refused = write(
        client,
        "PUT",
        f"/api/v1/companies/{company['id']}/cashflow/{original['id']}",
        json={
            "type_code": "finnes_ikke",
            "occurred_on": original["occurred_on"],
            "gross_amount": "600.00",
            "description": "Rettet",
            "tax_code": None,
            "payment_method": "bank",
            "party_id": None,
        },
    )
    assert refused.status_code >= 400

    entries = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    assert len(entries) == 1
    assert entries[0]["reversed_by_id"] is None


# --- Innførselsmva and capitalised purchases -------------------------------------


def test_an_import_computes_vat_on_top_and_posts_both_sides(client: TestClient) -> None:
    """The eBay Mac Studio, booked the way merverdiavgiftsloven wants it.

    A foreign seller charged no Norwegian VAT, so nothing is carved out of the
    27 108,49: the whole sum is the cost of the machine. The import VAT is
    computed on top, and it goes on *both* sides — owed on 2700, reclaimed on
    2710 — because the MVA-melding reports it twice and a single net-zero line
    would report it nowhere.
    """
    company = signed_in_company(client)
    entry = record(
        client,
        company["id"],
        type_code="driftsmiddel",
        gross_amount="27108.49",
        tax_code="81",
        description="Ordre 10-15068-44333, Apple Mac Studio",
    ).json()

    # Not 21 686,79: the cost is the whole amount that left the account.
    assert entry["net_amount"] == "27108.49"
    assert entry["gross_amount"] == "27108.49"
    assert entry["vat_amount"] == "6777.12"
    # A balance-sheet account, because nothing was consumed.
    assert entry["account_number"] == "1250"

    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    # Both sides reported, and they cancel: the import costs the business
    # nothing in VAT but is still on the return twice.
    assert summary["input_vat"] == "6777.12"
    assert summary["output_vat"] == "6777.12"
    assert summary["vat_position"] == "0.00"


def test_an_ordinary_purchase_still_carves_the_vat_out(client: TestClient) -> None:
    """The contrast that makes the case above worth a flag rather than a rate."""
    company = signed_in_company(client)
    entry = record(
        client,
        company["id"],
        type_code="kontorrekvisita",
        gross_amount="27108.49",
        tax_code="1",
    ).json()

    assert entry["net_amount"] == "21686.79"
    assert entry["vat_amount"] == "5421.70"


def test_a_non_deductible_import_is_a_real_cost(client: TestClient) -> None:
    """Code 82: owed and not reclaimed, so it lands on the purchase's account."""
    company = signed_in_company(client)
    entry = record(
        client,
        company["id"],
        type_code="driftsmiddel",
        gross_amount="10000.00",
        tax_code="82",
    ).json()

    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    assert entry["vat_amount"] == "2500.00"
    assert summary["output_vat"] == "2500.00"
    # Nothing to reclaim, so it is owed rather than a wash.
    assert summary["input_vat"] == "0.00"
    assert summary["vat_position"] == "2500.00"


def test_the_catalogue_offers_the_import_codes_and_the_asset_category(
    client: TestClient,
) -> None:
    """The options exist on the website, not only in the posting engine."""
    signed_in_company(client)
    catalogue = client.get("/api/v1/cashflow/types").json()

    codes = {code["code"] for code in catalogue["tax_codes"]}
    assert {"81", "82", "83", "86", "87"} <= codes
    types = {entry["code"]: entry for entry in catalogue["types"]}
    assert types["driftsmiddel"]["account_number"] == "1250"
    assert types["driftsmiddel"]["direction"] == "ut"


# --- Several documents, and the sentence that explains them ------------------
#
# Every test below is drawn from a real bilag in `regnaskaps_tests/`, because
# the shapes that broke the one-file endpoint were not hypothetical. A FINN
# purchase arrives as an advert, a chat, a FINN receipt and a bank line; an
# AliExpress order arrives as nine screenshots; and one of them cannot be
# decided from the paper at all.

# The FINN receipt for a power supply, as the vision model transcribes it. The
# advert's asking price (3 500) and the agreed price (3 250) are both on the
# page, which is exactly why the totals line matters.
FINN_RECEIPT_LINES = [
    '"SEASONIC TX1600W Titanium"',
    "Hei. Takk for kjøpet ditt.",
    "FINN-kode:",
    "473 945 486",
    "Kjøpsdato:",
    "30.08.2026, kl 10:08",
    "Frakt med PostNord              109,00 kr",
    "Avgift for Trygg betaling       206,00 kr",
    "Totalt                          315,00 kr",
]

# The bank line for the same purchase, from a different screenshot.
FINN_BANK_LINES = [
    "Outgoing payment",
    "Finnseasonic Tx1600w",
    "30.08.2026",
    "-NOK 3 565,00",
    "LINDEMAN TECHNOLOGIES AS",
    "1520.34.91244",
]


def png_bytes() -> bytes:
    """The smallest thing that sniffs as a PNG.

    The endpoint never decodes it — Løynd does, and Løynd is stubbed in these
    tests — so a valid header is the whole requirement. Writing a real image
    here would test Pillow rather than the route.
    """
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_several_documents_are_read_into_one_proposal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bilag is what the documents say together, not what the first one says.

    The FINN receipt carries the date and the fees; the bank line carries the
    amount that actually left the account. Neither alone is the entry.
    """
    seen: list[str] = []

    async def fake_complete(*, system: str, prompt: str, max_tokens: int = 16) -> str:
        del system, max_tokens
        seen.append(prompt)
        return "1"

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, fake_complete)

    company = signed_in_company(client)
    response = interpret_many(
        client,
        company["id"],
        [
            ("kvittering.pdf", text_pdf(FINN_RECEIPT_LINES)),
            ("bank.pdf", text_pdf(FINN_BANK_LINES)),
        ],
    )
    assert response.status_code == 200
    suggestion = response.json()

    # Both documents reached the model, each under its own heading.
    assert any("Dokument 1" in prompt and "Dokument 2" in prompt for prompt in seen)
    assert any("SEASONIC" in prompt and "1520.34.91244" in prompt for prompt in seen)

    # And both are accounted for in the answer, in the order they were uploaded.
    assert [row["filename"] for row in suggestion["documents_read"]] == [
        "kvittering.pdf",
        "bank.pdf",
    ]
    assert all(row["chars"] > 0 for row in suggestion["documents_read"])


def test_a_single_document_is_not_given_a_heading(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The common case must send the prompt it always sent.

    One invoice, one text, no "Dokument 1:" preamble — so nothing measured
    against the old prompt has to be measured again.
    """
    seen: list[str] = []

    async def fake_complete(*, system: str, prompt: str, max_tokens: int = 16) -> str:
        del system, max_tokens
        seen.append(prompt)
        return "1"

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, fake_complete)

    company = signed_in_company(client)
    interpret(client, company["id"], text_pdf(INVOICE_LINES))

    assert seen and not any("--- Dokument" in prompt for prompt in seen)


def test_there_is_no_ceiling_on_how_many_documents_a_bilag_may_have(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The AliExpress case: nine screenshots for one order.

    A limit here would be a limit on what may be documented, and
    bokføringsloven has an opinion about that which our convenience does not
    outrank.
    """

    async def fake_complete(*, system: str, prompt: str, max_tokens: int = 16) -> str:
        del system, prompt, max_tokens
        return "1"

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, fake_complete)

    company = signed_in_company(client)
    response = interpret_many(
        client,
        company["id"],
        [(f"skjerm-{n}.pdf", text_pdf([f"Ordre {n}", "Totalt 146,23 kr"])) for n in range(12)],
    )
    assert response.status_code == 200
    assert len(response.json()["documents_read"]) == 12


def test_an_empty_upload_is_refused_rather_than_read_as_an_empty_bilag(
    client: TestClient,
) -> None:
    company = signed_in_company(client)
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/cashflow/forslag",
        files=[("files", ("tom.pdf", b"", "application/pdf"))],
    )
    assert response.status_code == 415


def test_a_photograph_is_accepted_where_it_used_to_be_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Most small-company bilag are a photograph, and used to be unfileable.

    The route sniffs the bytes and hands Løynd the real content type; the
    transcription comes back as text and is read exactly like a text layer.
    """
    sent: list[str] = []

    async def fake_extract(*, content: bytes, filename: str, content_type: str) -> Any:
        del content, filename
        sent.append(content_type)
        return loynd.ExtractedDocument(text="\n".join(FINN_RECEIPT_LINES))

    async def fake_complete(*, system: str, prompt: str, max_tokens: int = 16) -> str:
        del system, prompt, max_tokens
        return "1"

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "extract_file", fake_extract)
    stub_loynd(monkeypatch, fake_complete)

    company = signed_in_company(client)
    response = interpret_many(client, company["id"], [("IMG_9355.png", png_bytes())])

    assert response.status_code == 200
    # The real type, not the hardcoded "application/pdf" this used to send.
    assert sent == ["image/png"]
    assert response.json()["documents_read"][0]["content_type"] == "image/png"


def test_an_image_that_could_not_be_read_says_so_in_its_own_words(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "The PDF may be damaged" is the wrong advice about a photograph.

    There is no local fallback for pixels, so an image Løynd could not read is
    simply unread, and the sentence has to send the user somewhere useful.
    """

    async def empty(**_: Any) -> Any:
        return loynd.ExtractedDocument(text="", empty=True)

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "extract_file", empty)

    company = signed_in_company(client)
    suggestion = interpret_many(client, company["id"], [("uskarp.png", png_bytes())]).json()

    warnings = " ".join(suggestion["warnings"])
    assert "bildene" in warnings
    assert "skadet" not in warnings


def test_one_unreadable_document_among_several_is_named_not_counted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def selective(*, content: bytes, filename: str, content_type: str) -> Any:
        del content, content_type
        if filename == "uskarp.png":
            return loynd.ExtractedDocument(text="", empty=True)
        return loynd.ExtractedDocument(text="\n".join(FINN_RECEIPT_LINES))

    async def fake_complete(**_: Any) -> str:
        return "1"

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "extract_file", selective)
    stub_loynd(monkeypatch, fake_complete)

    company = signed_in_company(client)
    suggestion = interpret_many(
        client,
        company["id"],
        [("kvittering.png", png_bytes()), ("uskarp.png", png_bytes())],
    ).json()

    warnings = " ".join(suggestion["warnings"])
    assert "uskarp.png" in warnings
    # The readable one still filled the form in.
    assert suggestion["occurred_on"] == date(2026, 8, 30).isoformat()


def test_the_users_own_explanation_reaches_the_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `finn/` case: 3 000 kroner that the documents cannot classify.

    An outgoing 3 000 and an incoming 3 000 on the same day are an owner
    covering a purchase out of pocket, a loan, or a sale. Only the person who
    made the payment knows which, and the box is where they say so.
    """
    seen: list[str] = []

    async def fake_complete(*, system: str, prompt: str, max_tokens: int = 16) -> str:
        del system, max_tokens
        seen.append(prompt)
        return "1"

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, fake_complete)

    company = signed_in_company(client)
    note = "kjøpt powersupply, la ut 3000 av pengene privat, ikke refundert enda"
    suggestion = interpret_many(
        client,
        company["id"],
        [("kvittering.pdf", text_pdf(FINN_RECEIPT_LINES))],
        forklaring=note,
    ).json()

    assert any(note in prompt for prompt in seen)
    assert any("Brukerens egen forklaring" in prompt for prompt in seen)
    assert suggestion["context_used"] is True


def test_the_explanation_cannot_put_words_in_the_documents_mouth(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The note is context, never evidence.

    Quoted fields are accepted only when the *document* contains them. If the
    note counted as document text, anyone could set the counterparty by typing
    it in the box — and the check that makes every other model answer
    trustworthy would be gone.
    """

    async def fake_complete(*, system: str, prompt: str, max_tokens: int = 16) -> str:
        del system, max_tokens
        if "MOTPART" not in prompt:
            return "1"
        return "DATO: UKJENT\nVALUTA: UKJENT\nMOTPART: Oppdiktet Leverandør AS\nBESKRIVELSE: noe"

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, fake_complete)

    company = signed_in_company(client)
    suggestion = interpret_many(
        client,
        company["id"],
        [("kvittering.pdf", text_pdf(FINN_RECEIPT_LINES))],
        forklaring="Kjøpt fra Oppdiktet Leverandør AS",
    ).json()

    # The name is in the note and nowhere in the document, so it is discarded.
    assert suggestion["counterparty_name"] != "Oppdiktet Leverandør AS"


def test_a_company_with_ki_off_never_has_its_explanation_read(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off means off, including the box the user typed in."""

    async def refuse(**_: Any) -> str:
        raise AssertionError("the model was asked despite KI being off")

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, refuse)

    company = signed_in_company(client)
    write(client, "PATCH", f"/api/v1/companies/{company['id']}", json={"ki_enabled": False})

    suggestion = interpret_many(
        client,
        company["id"],
        [("faktura.pdf", text_pdf(INVOICE_LINES))],
        forklaring="noe personlig",
    ).json()

    assert suggestion["ki_used"] is False
    assert suggestion["context_used"] is False


def test_a_bilag_the_documents_cannot_settle_comes_back_with_a_question(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The register asks, rather than proposing something it cannot know.

    A receipt with no total is not a failure to report — it is a question with
    an answer, and the answer has a box to go in.
    """

    async def fake_complete(**_: Any) -> str:
        return "1"

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, fake_complete)

    company = signed_in_company(client)
    suggestion = interpret(
        client, company["id"], text_pdf(["Kvittering", "Takk for handelen"])
    ).json()

    assert suggestion["questions"]
    assert suggestion["questions"][0]["field"] == "gross_amount"
    assert suggestion["questions"][0]["text"].endswith("?")


def test_the_model_may_ask_one_question_of_its_own(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the same round trip as everything else it is asked.

    A fifth line rather than a second call: the question is worth having and
    not worth a second wait.
    """

    async def fake_complete(*, system: str, prompt: str, max_tokens: int = 16) -> str:
        del system, max_tokens
        if "MOTPART" not in prompt:
            return "1"
        return (
            "DATO: UKJENT\nVALUTA: UKJENT\nMOTPART: UKJENT\n"
            "BESKRIVELSE: noe\n"
            "SPØRSMÅL: Ble strømforsyningen kjøpt til selskapet eller privat?"
        )

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, fake_complete)

    company = signed_in_company(client)
    suggestion = interpret(client, company["id"], text_pdf(FINN_RECEIPT_LINES)).json()

    asked = [q for q in suggestion["questions"] if q["asked_by_model"]]
    assert len(asked) == 1
    assert asked[0]["text"] == "Ble strømforsyningen kjøpt til selskapet eller privat?"


def test_the_models_question_is_checked_before_it_is_shown(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It fills no field, so the check is only about shape — but it is a check.

    A model that answers with a statement instead of a question has not asked
    anything, and putting a statement under a question mark heading would be
    the interface telling a small lie about what it holds.
    """

    async def fake_complete(*, system: str, prompt: str, max_tokens: int = 16) -> str:
        del system, max_tokens
        if "MOTPART" not in prompt:
            return "1"
        return (
            "DATO: UKJENT\nVALUTA: UKJENT\nMOTPART: UKJENT\n"
            "BESKRIVELSE: noe\nSPØRSMÅL: Dette er nok et privatkjøp."
        )

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, fake_complete)

    company = signed_in_company(client)
    suggestion = interpret(client, company["id"], text_pdf(FINN_RECEIPT_LINES)).json()

    assert not any(q["asked_by_model"] for q in suggestion["questions"])


def test_answering_in_the_box_changes_what_comes_back(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loop the whole feature is for: ask, answer, read again.

    The second read sends the sentence to the model, and the question that
    prompted it is no longer asked.
    """

    async def fake_complete(**_: Any) -> str:
        return "1"

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, fake_complete)

    company = signed_in_company(client)
    bank = text_pdf(["Innbetaling", "30.08.2026", "Totalt 3 000,00", "Andreas Lindeman"])

    first = interpret_many(client, company["id"], [("bank.pdf", bank)]).json()
    second = interpret_many(
        client,
        company["id"],
        [("bank.pdf", bank)],
        forklaring="dette er penger jeg la ut privat og fikk igjen",
    ).json()

    assert second["context_used"] is True
    # Whatever else changed, the register stopped asking a question the user
    # has now answered.
    assert len(second["questions"]) <= len(first["questions"])


ELEKTRA_LINES = [
    "LINJEFORENINGEN ELEKTRA",
    "Org.nr.:    NO 991 107 711 MVA",
    "FAKTURA",
    "Fakturanr.:                      1075",
    f"Fakturadato:              {THIS_YEAR}-09-06",
    "Beskrivelse Antall Enh.pris",
    "(ekskl. mva)",
    "Beløp",
    "(ekskl. mva)",
    "Mva",
    "(25 %)",
    "Beløp",
    "(inkl. mva)",
    "Batteri 1 1 152,00 1 152,00 288,00 1 440,00",
    "Betales til bankkonto 4202.11.39496 NOK  1 440,00",
]


def test_a_battery_is_asked_about_rather_than_filed_under_motoring(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole chain, on the bilag that occasioned it.

    An item line reading "Batteri" and nothing else came back as *Drivstoff og
    bilkostnad* with a description of "Faktura 1075, LINJEFORENINGEN ELEKTRA".
    Three things were wrong with that and all three are here: the item line is
    read off the table now, the model's runner-up becomes a question the user
    can answer, and a "description" that is only the sender is refused.
    """
    menu = classification.build_menu(CashFlowDirection.UT)
    bilkostnad = next(item for item in menu if item.type_code == "bilkostnad")
    varekjop = next(item for item in menu if item.type_code == "varekjop")

    async def fake_complete(*, system: str, prompt: str, max_tokens: int = 16) -> str:
        del system, max_tokens
        if "MOTPART" not in prompt:
            return f"{bilkostnad.number}/{varekjop.number}"
        return (
            "DATO: UKJENT\nVALUTA: UKJENT\nMOTPART: LINJEFORENINGEN ELEKTRA\n"
            "BESKRIVELSE: LINJEFORENINGEN ELEKTRA\nSPØRSMÅL: UKJENT"
        )

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, fake_complete)

    company = signed_in_company(client)
    suggestion = interpret(client, company["id"], text_pdf(ELEKTRA_LINES)).json()

    # The category the model led with still stands as the proposal — the doubt is
    # a question, not a veto.
    assert suggestion["type_code"] == "bilkostnad"
    asked = [q for q in suggestion["questions"] if q["field"] == "type_code"]
    assert len(asked) == 1
    assert asked[0]["text"] == (
        "Er dette «Drivstoff og bilkostnad» eller «Varekjøp»? Skriv gjerne hva som ble kjøpt."
    )
    assert asked[0]["asked_by_model"]
    # And the description names the thing rather than the sender.
    assert suggestion["description"] == "Faktura 1075, Batteri"


# --- watching the proposal arrive -------------------------------------------------


def stream_events(client: TestClient, company_id: int, content: bytes, *, name: str) -> list[Any]:
    """Every frame the streaming route sends, parsed, in order."""
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/cashflow/forslag/strom",
        files=[("files", (name, content, _content_type(content)))],
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events: list[Any] = []
    event: str | None = None
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: ") and event is not None:
            events.append((event, json.loads(line[len("data: ") :])))
    return events


def test_the_description_arrives_while_it_is_being_written(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of the route.

    Nine screenshots take a minute and a half, and what used to happen in that
    minute was a spinner and then a form that filled itself in all at once. The
    sentence is now typed out as the model writes it — and only the sentence:
    the date, the currency and the seller each have a box of their own.
    """
    menu = classification.build_menu(CashFlowDirection.UT)
    inventar = next(item for item in menu if item.type_code == "inventar")
    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, answering(str(inventar.number), RECEIPT_ANSWER))

    company = signed_in_company(client)
    events = stream_events(client, company["id"], text_pdf(RECEIPT_LINES), name="Order details.pdf")

    names = [name for name, _ in events]
    # The file list first, the sentence in pieces, the proposal last.
    assert names[0] == "dokumenter"
    assert names[-1] == "forslag"
    assert names.count("beskrivelse") > 1

    written = "".join(payload["tekst"] for name, payload in events if name == "beskrivelse")
    assert written == "Apple Mac Studio til kontoret"
    # None of the other three lines went past on the way.
    assert "GBP" not in written
    assert "daisyaus-0" not in written


def test_the_last_frame_is_what_the_json_route_would_have_returned(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One pipeline, two routes, and no room for them to disagree.

    A client that ignores every frame but the last has the plain JSON answer —
    which is what makes the streaming route an improvement to the wait rather
    than a second implementation of the proposal.
    """
    menu = classification.build_menu(CashFlowDirection.UT)
    inventar = next(item for item in menu if item.type_code == "inventar")
    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, answering(str(inventar.number), RECEIPT_ANSWER))

    company = signed_in_company(client)
    streamed = stream_events(
        client, company["id"], text_pdf(RECEIPT_LINES), name="Order details.pdf"
    )[-1][1]
    plain = interpret(
        client, company["id"], text_pdf(RECEIPT_LINES), name="Order details.pdf"
    ).json()

    # Each read is a reading of its own, kept under its own id for the answers,
    # so the one field that names it is the one field that must differ.
    assert streamed.pop("reading_id") != plain.pop("reading_id")
    assert streamed == plain


def test_the_files_are_reported_before_the_proposal_exists(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A photograph that came back blank is worth knowing about immediately.

    It is the one thing in the answer the user can act on straight away — take
    the picture again — and waiting for the rest of the proposal to arrive
    before saying so wastes the only part of the wait that had a use.
    """

    async def blank(*, content: bytes, filename: str, content_type: str) -> Any:
        del content, filename, content_type
        return loynd.ExtractedDocument(text="", empty=True)

    async def one(**_: Any) -> str:
        return "1"

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "extract_file", blank)
    stub_loynd(monkeypatch, one)

    company = signed_in_company(client)
    events = stream_events(client, company["id"], png_bytes(), name="uskarp.png")

    name, payload = events[0]
    assert name == "dokumenter"
    assert payload == [{"filename": "uskarp.png", "content_type": "image/png", "chars": 0}]


def _stream_many(client: TestClient, company_id: int, count: int) -> list[Any]:
    """Every frame for ``count`` screenshots, each with bytes of its own."""
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/cashflow/forslag/strom",
        files=[
            ("files", (f"ordre{n}.png", png_bytes() + bytes([n]), "image/png"))
            for n in range(count)
        ],
    )
    assert response.status_code == 200, response.text
    events: list[Any] = []
    event: str | None = None
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: ") and event is not None:
            events.append((event, json.loads(line[len("data: ") :])))
    return events


def _an_order_per_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """A full page of text from every file, different on every line."""

    async def page(*, content: bytes, filename: str, content_type: str) -> Any:
        del content, content_type
        text = "\n".join(f"{filename} linje {n} vare {n * 7}" for n in range(120))
        return loynd.ExtractedDocument(text=text)

    async def one(**_: Any) -> str:
        return "1"

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "extract_file", page)
    stub_loynd(monkeypatch, one)


def test_a_long_bilag_is_said_while_the_user_waits(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before the questions, because "this takes a while" is useless afterwards.

    And again with the proposal, where what matters is that the model read each
    part of a long text less closely.
    """
    _an_order_per_file(monkeypatch)
    company = signed_in_company(client)
    events = _stream_many(client, company["id"], 8)

    names = [name for name, _ in events]
    assert names[:2] == ["dokumenter", "advarsel"]
    assert "lengre tid" in events[1][1]["melding"]
    suggestion = events[-1][1]
    assert any("leser lange tekster mindre nøye" in w for w in suggestion["warnings"])


def test_a_bilag_past_the_ceiling_says_what_the_model_did_not_see(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _an_order_per_file(monkeypatch)
    company = signed_in_company(client)
    events = _stream_many(client, company["id"], 20)

    suggestion = events[-1][1]
    assert any("de første 28 000 tegnene" in w for w in suggestion["warnings"])


def test_an_ordinary_bilag_is_not_called_long(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _an_order_per_file(monkeypatch)
    company = signed_in_company(client)
    events = _stream_many(client, company["id"], 2)

    assert "advarsel" not in [name for name, _ in events]


def test_a_file_we_cannot_read_is_refused_before_the_stream_opens(
    client: TestClient,
) -> None:
    """A status code, not a frame inside a 200.

    Once the first byte of a streamed response is out the status line is spent,
    so everything that can be refused is refused first.
    """
    company = signed_in_company(client)
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/cashflow/forslag/strom",
        files=[("files", ("notater.txt", b"ikke et dokument", "text/plain"))],
    )

    assert response.status_code == 415


def test_reading_the_same_bilag_again_does_not_transcribe_it_again(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What makes re-reading on every edit affordable.

    Editing the description re-reads the bilag, and transcribing nine
    screenshots costs ninety seconds of a graphics card. The text is remembered
    by the bytes it came from, so the second read costs a prompt.
    """
    reads: list[str] = []

    async def counting(*, content: bytes, filename: str, content_type: str) -> Any:
        del content, content_type
        reads.append(filename)
        return loynd.ExtractedDocument(text="\n".join(FINN_RECEIPT_LINES))

    async def one(**_: Any) -> str:
        return "1"

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "extract_file", counting)
    stub_loynd(monkeypatch, one)

    company = signed_in_company(client)
    first = interpret_many(client, company["id"], [("kvittering.png", png_bytes())])
    second = interpret_many(
        client,
        company["id"],
        [("kvittering.png", png_bytes())],
        forklaring="kjøpt strømforsyning, la ut privat",
    )

    assert first.status_code == 200
    assert second.status_code == 200
    # Read once, proposed twice.
    assert reads == ["kvittering.png"]
    # And the second proposal did see the sentence.
    assert second.json()["context_used"] is True


# --- answering the questions -------------------------------------------------------
#
# An answer continues the reading instead of starting it again: no upload, no
# transcription, no second look at the pictures, and a model shown its own
# question with the answer under it. See myntos.domain.bilag.followup.

RECEIPT_WITH_A_QUESTION = RECEIPT_ANSWER + "\nSPØRSMÅL: Er dette til selskapets drift?"


class Conversing:
    """A Løynd that answers both questions and keeps every conversation it is handed.

    A follow-up is told apart from a first question by its history, and the two
    questions by what the first one asked for.
    """

    def __init__(self, category: str, fields: str, *, again: str | None = None) -> None:
        self.category = category
        self.fields = fields
        self.again = again if again is not None else fields
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self, *, system: str, prompt: str, max_tokens: int = 16, history: Any = ()
    ) -> str:
        del max_tokens
        self.calls.append({"system": system, "prompt": prompt, "history": list(history)})
        asked = history[0][0] if history else prompt
        if "BESKRIVELSE" in asked:
            return self.again if history else self.fields
        return self.category

    def follow_ups(self) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["history"]]


def counting_extractor(monkeypatch: pytest.MonkeyPatch, text: str) -> list[str]:
    """Løynd's file reader, counting what it is handed."""
    read: list[str] = []

    async def extract(*, content: bytes, filename: str, content_type: str) -> Any:
        del content, content_type
        read.append(filename)
        return loynd.ExtractedDocument(text=text)

    monkeypatch.setattr(loynd, "extract_file", extract)
    return read


def answer(
    client: TestClient, company_id: int, reading_id: str, answers: list[dict[str, Any]], **body: Any
) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/cashflow/forslag/{reading_id}/svar",
        json={"answers": answers, **body},
    )


def receipt_model(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> Conversing:
    menu = classification.build_menu(CashFlowDirection.UT)
    inventar = next(item for item in menu if item.type_code == "inventar")
    model = Conversing(str(inventar.number), RECEIPT_WITH_A_QUESTION, **kwargs)
    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, model.complete)
    return model


def test_an_answer_continues_the_conversation_without_reading_the_bilag_again(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: the files are not read again, and the model sees what it asked.

    Both follow-ups start with the first question and the model's answer to it,
    word for word, which is what lets Løynd reuse its work on the bilag. The
    answer is in the one new turn after them.
    """
    model = receipt_model(monkeypatch)
    read = counting_extractor(monkeypatch, "\n".join(RECEIPT_LINES))
    company = signed_in_company(client)

    first = interpret(client, company["id"], text_pdf(RECEIPT_LINES), name="ordre.pdf").json()
    assert read == ["ordre.pdf"]
    question = next(q for q in first["questions"] if q["key"] == "ki")
    assert question["text"] == "Er dette til selskapets drift?"
    firsts = [call for call in model.calls if not call["history"]]

    again = answer(
        client,
        company["id"],
        first["reading_id"],
        [{"key": "ki", "question": question["text"], "answer": "Ja, til kontoret"}],
    )

    assert again.status_code == 200, again.text
    assert read == ["ordre.pdf"]
    follow_ups = model.follow_ups()
    assert len(follow_ups) == 2
    for call in follow_ups:
        ((asked, replied),) = call["history"]
        assert any(first_call["prompt"] == asked for first_call in firsts)
        assert replied in {RECEIPT_WITH_A_QUESTION, model.category}
        assert "«Er dette til selskapets drift?» Svar: «Ja, til kontoret»" in call["prompt"]
    proposal = again.json()
    assert proposal["reading_id"] == first["reading_id"]
    assert proposal["type_code"] == "inventar"
    assert proposal["occurred_on"] == date(THIS_YEAR, 8, 23).isoformat()


def test_a_question_that_was_answered_is_not_asked_again(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The report that started this, end to end.

    The model asks the same thing on the follow-up as it did the first time,
    which a small model will. The answer the user gave is what keeps it off the
    screen, not the model's good manners.
    """
    receipt_model(monkeypatch)
    counting_extractor(monkeypatch, "\n".join(RECEIPT_LINES))
    company = signed_in_company(client)
    first = interpret(client, company["id"], text_pdf(RECEIPT_LINES)).json()

    again = answer(
        client,
        company["id"],
        first["reading_id"],
        [{"key": "ki", "question": "Er dette til selskapets drift?", "answer": "Ja"}],
    ).json()

    assert "ki" not in {question["key"] for question in again["questions"]}


def test_the_pictures_are_not_asked_for_a_name_again(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The app's name came off the pixels once; an answer has no pixels and needs none."""
    model = Conversing("1", "DATO: UKJENT\nVALUTA: NOK\nMOTPART: UKJENT\nBESKRIVELSE: Servoer")
    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, model.complete)
    counting_extractor(monkeypatch, "Order total 1 234,00 kr\nServo x 4")
    looked: list[str] = []

    async def look(content: bytes, *, content_type: str, question: str, **_: Any) -> str:
        del content, content_type
        looked.append(question)
        return "AliExpress"

    monkeypatch.setattr(loynd, "ask_about_image", look)
    company = signed_in_company(client)
    first = interpret(client, company["id"], png_bytes(), name="ordre.png").json()
    assert first["counterparty_name"] == "AliExpress"
    seen = len(looked)

    again = answer(client, company["id"], first["reading_id"], [], forklaring="Servoer").json()

    assert len(looked) == seen
    assert again["counterparty_name"] == "AliExpress"


def test_a_question_never_answered_the_first_time_is_asked_afresh(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Løynd dropped the first read's answer, so there is no conversation to continue.

    The answers then go where the user's sentence goes on a first read, and
    the question is asked as a first question.
    """
    model = receipt_model(monkeypatch)
    counting_extractor(monkeypatch, "\n".join(RECEIPT_LINES))
    prompts: list[str] = []

    async def stream(*, system: str, prompt: str, max_tokens: int = 16) -> Any:
        prompts.append(prompt)
        if len(prompts) == 1:
            raise loynd.LoyndUnavailableError("borte")
        yield await model.complete(system=system, prompt=prompt, max_tokens=max_tokens)

    monkeypatch.setattr(loynd, "stream", stream)
    company = signed_in_company(client)
    first = interpret(client, company["id"], text_pdf(RECEIPT_LINES)).json()

    again = answer(
        client,
        company["id"],
        first["reading_id"],
        [{"key": "kategori", "question": "Hva ble kjøpt?", "answer": "En datamaskin"}],
        forklaring="Mac til kontoret",
    )

    assert again.status_code == 200, again.text
    assert len(prompts) == 2
    assert "Mac til kontoret Hva ble kjøpt? En datamaskin" in prompts[1]
    assert again.json()["description"] is not None


def test_no_reading_is_kept_when_no_model_was_asked(client: TestClient) -> None:
    """Without a model there is no conversation to continue, and reading again is free."""
    company = signed_in_company(client)
    first = interpret(client, company["id"], text_pdf(RECEIPT_LINES)).json()
    assert first["ki_used"] is False
    assert first["reading_id"] is None


def test_an_unknown_reading_is_not_found(client: TestClient) -> None:
    company = signed_in_company(client)
    response = answer(client, company["id"], "finnes-ikke", [])
    assert response.status_code == 404
    assert response.json()["code"] == "reading_not_found"


def test_a_colleagues_reading_is_not_yours_to_answer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same company, same role, another person: the reading is the one who read it.

    The id is random and nobody else was shown it, but an id ends up in logs and
    browser histories, and the books being the same is not the person being the
    same.
    """
    receipt_model(monkeypatch)
    counting_extractor(monkeypatch, "\n".join(RECEIPT_LINES))
    company = signed_in_company(client)
    first = interpret(client, company["id"], text_pdf(RECEIPT_LINES)).json()

    join_company(client, company["id"], email="kollega@example.no")
    response = answer(client, company["id"], first["reading_id"], [])

    assert response.status_code == 404
    assert response.json()["code"] == "reading_not_found"


def test_switching_ki_off_ends_every_reading(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off means the documents go to no model, including through a reading kept from before."""
    model = receipt_model(monkeypatch)
    counting_extractor(monkeypatch, "\n".join(RECEIPT_LINES))
    company = signed_in_company(client)
    first = interpret(client, company["id"], text_pdf(RECEIPT_LINES)).json()
    asked = len(model.calls)

    write(client, "PATCH", f"/api/v1/companies/{company['id']}", json={"ki_enabled": False})
    response = answer(client, company["id"], first["reading_id"], [])

    assert response.status_code == 404
    assert len(model.calls) == asked


def test_the_model_cannot_make_an_asset_of_a_payment_under_the_threshold(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A driftsmiddel costs 30 000 or more, so a smaller payment holds none.

    Found when two graphics cards for 25 278 kroner came back as an asset after
    the person answered «til selskapets drift»: the model read "drift", and the
    menu has a "driftsmiddel" on it. Its runner-up stands in, and it is not
    asked about, since the doubt was about a category that could not be right.
    """
    menu = classification.build_menu(CashFlowDirection.UT)
    number = {item.type_code: item.number for item in menu}
    model = Conversing(
        f"{number['driftsmiddel']}/{number['datakostnad']}",
        "DATO: UKJENT\nVALUTA: NOK\nMOTPART: UKJENT\nBESKRIVELSE: To skjermkort",
    )
    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    stub_loynd(monkeypatch, model.complete)
    counting_extractor(monkeypatch, "Kvittering\nTo skjermkort\nTotalt 25 278,00 kr")
    company = signed_in_company(client)

    proposal = interpret(client, company["id"], text_pdf(["Kvittering"])).json()

    assert proposal["gross_amount"] == "25278.00"
    assert proposal["type_code"] == "datakostnad"
    assert "kategori_tvil" not in {question["key"] for question in proposal["questions"]}
