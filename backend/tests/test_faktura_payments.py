"""A customer paying an invoice, end to end: by hand, and from a bank line.

The whole stack on SQLite. What only the round trip shows is that the payment
lands in the register as a voucher of its own, that the invoice reads its state
off the ledger (so a reversal in the register makes it unpaid again without
the register knowing invoices exist), and that nothing can mark an invoice paid
beyond what is owed, before the money came, or on an invoice nobody owes.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from myntos.domain.faktura import kid
from myntos.domain.faktura.payments import today_in_norway
from tests.conftest import join_company, register, write
from tests.test_faktura_api import (
    THIS_YEAR,
    create_draft,
    issue,
    make_company,
    signed_in_company,
)

TODAY = today_in_norway()
# A day the money may have come: after the invoice where the calendar allows,
# never after today, whatever day of the year the suite runs.
PAID_ON = min(date(THIS_YEAR, 4, 2), TODAY)

# Ten hours at 1 500 kr and 25 % VAT, the draft test_faktura_api builds.
GROSS = Decimal("18750.00")

BANK_FIXTURE = Path(__file__).parent / "fixtures" / "bank" / "camt053_v02_mars.xml"
# The KID the fixture's incoming 12 500 kr carries; swapped for the invoice's.
FIXTURE_KID = b"000100000100234"
# The fixture is dated 2026, so the bank tests' books start then.
BANK_YEAR = 2026


def _issued(client: TestClient, company_id: int, **overrides: Any) -> dict[str, Any]:
    draft = create_draft(client, company_id, **overrides)
    response = issue(client, company_id, draft["id"])
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _pay(
    client: TestClient,
    company_id: int,
    document_id: int,
    amount: Decimal | str,
    *,
    paid_on: date = PAID_ON,
    payment_method: str = "bank",
) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/faktura/{document_id}/innbetalinger",
        json={
            "paid_on": paid_on.isoformat(),
            "amount": str(amount),
            "payment_method": payment_method,
        },
    )


def _invoice(client: TestClient, company_id: int, document_id: int) -> dict[str, Any]:
    response = client.get(f"/api/v1/companies/{company_id}/faktura/{document_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# --- By hand --------------------------------------------------------------------------


def test_an_issued_invoice_starts_unpaid_and_a_draft_has_no_state(client: TestClient) -> None:
    company = signed_in_company(client)
    draft = create_draft(client, company["id"])
    assert draft["payment_state"] is None
    assert draft["outstanding"] is None

    issued = issue(client, company["id"], draft["id"]).json()
    assert issued["payment_state"] == "ubetalt"
    assert Decimal(issued["outstanding"]) == GROSS
    assert Decimal(issued["paid_amount"]) == Decimal("0")
    assert issued["payments"] == []


def test_a_full_payment_settles_the_invoice_and_is_its_own_voucher(client: TestClient) -> None:
    company = signed_in_company(client)
    invoice = _issued(client, company["id"])

    response = _pay(client, company["id"], invoice["id"], GROSS)
    assert response.status_code == 201, response.text
    paid = response.json()
    assert paid["payment_state"] == "betalt"
    assert Decimal(paid["outstanding"]) == Decimal("0")
    (payment,) = paid["payments"]
    assert payment["paid_on"] == PAID_ON.isoformat()
    assert Decimal(payment["amount"]) == GROSS
    assert payment["payment_account"] == "1920"

    # The list says the same as the invoice itself.
    (listed,) = client.get(f"/api/v1/companies/{company['id']}/faktura").json()
    assert listed["payment_state"] == "betalt"

    # In the register it is money in, against 1500 rather than a sale: the
    # revenue was booked the day the invoice was issued, and counting it here
    # again is the mistake this feature exists to stop.
    entries = {e["id"]: e for e in client.get(f"/api/v1/companies/{company['id']}/cashflow").json()}
    entry = entries[payment["voucher_id"]]
    assert entry["direction"] == "inn"
    assert entry["account_number"] == "1500"
    assert entry["type_label"] == "Innbetaling på faktura"
    assert Decimal(entry["vat_amount"]) == Decimal("0")

    # «Inn i år» counts it; the output VAT is the invoice's and does not move.
    summary = client.get(
        f"/api/v1/companies/{company['id']}/cashflow/summary",
        params={"financial_year": PAID_ON.year},
    ).json()
    assert Decimal(summary["total_in"]) == GROSS
    assert Decimal(summary["output_vat"]) == Decimal("3750.00")


def test_part_payments_leave_the_rest_open(client: TestClient) -> None:
    company = signed_in_company(client)
    invoice = _issued(client, company["id"])

    first = _pay(client, company["id"], invoice["id"], "5000.00").json()
    assert first["payment_state"] == "delvis_betalt"
    assert Decimal(first["outstanding"]) == GROSS - Decimal("5000.00")
    assert Decimal(first["paid_amount"]) == Decimal("5000.00")

    rest = _pay(client, company["id"], invoice["id"], first["outstanding"]).json()
    assert rest["payment_state"] == "betalt"
    assert len(rest["payments"]) == 2


def test_more_than_is_owed_is_refused(client: TestClient) -> None:
    company = signed_in_company(client)
    invoice = _issued(client, company["id"])

    too_much = _pay(client, company["id"], invoice["id"], GROSS + Decimal("0.01"))
    assert too_much.status_code == 422
    assert too_much.json()["code"] == "payment_exceeds_outstanding"
    assert "18 750,00 kr igjen" in too_much.json()["detail"]
    assert _invoice(client, company["id"], invoice["id"])["payment_state"] == "ubetalt"

    _pay(client, company["id"], invoice["id"], GROSS)
    again = _pay(client, company["id"], invoice["id"], "1.00")
    assert again.status_code == 422
    assert "allerede betalt" in again.json()["detail"]


def test_a_payment_cannot_be_dated_after_today(client: TestClient) -> None:
    company = signed_in_company(client)
    invoice = _issued(client, company["id"])

    response = _pay(client, company["id"], invoice["id"], GROSS, paid_on=TODAY + timedelta(days=1))
    assert response.status_code == 422
    assert response.json()["code"] == "payment_in_future"


def test_a_zero_or_negative_amount_is_not_a_payment(client: TestClient) -> None:
    company = signed_in_company(client)
    invoice = _issued(client, company["id"])

    assert _pay(client, company["id"], invoice["id"], "0").status_code == 422
    assert _pay(client, company["id"], invoice["id"], "-100.00").status_code == 422


def test_nobody_owes_anything_on_a_draft_a_kreditnota_or_a_credited_invoice(
    client: TestClient,
) -> None:
    company = signed_in_company(client)
    draft = create_draft(client, company["id"])
    refused = _pay(client, company["id"], draft["id"], "100.00")
    assert refused.status_code == 409
    assert refused.json()["code"] == "not_payable"

    invoice = _issued(client, company["id"])
    credit = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/faktura/{invoice['id']}/kreditnota",
        json={},
    ).json()
    assert credit["payment_state"] is None

    for document_id in (credit["id"], invoice["id"]):
        response = _pay(client, company["id"], document_id, "100.00")
        assert response.status_code == 409, document_id
        assert response.json()["code"] == "not_payable"


def test_a_paid_invoice_credited_is_owed_back_to_the_customer(client: TestClient) -> None:
    company = signed_in_company(client)
    invoice = _issued(client, company["id"])
    _pay(client, company["id"], invoice["id"], "5000.00")

    write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/faktura/{invoice['id']}/kreditnota",
        json={},
    )
    credited = _invoice(client, company["id"], invoice["id"])
    assert credited["payment_state"] == "kreditert"
    assert Decimal(credited["outstanding"]) == Decimal("-5000.00")
    assert credited["overdue"] is False


def test_cash_goes_to_the_cash_account(client: TestClient) -> None:
    company = signed_in_company(client)
    invoice = _issued(client, company["id"])

    paid = _pay(client, company["id"], invoice["id"], GROSS, payment_method="kontant").json()
    assert paid["payments"][0]["payment_account"] == "1900"


def test_an_unpaid_invoice_past_its_due_date_is_overdue_until_paid(client: TestClient) -> None:
    company = signed_in_company(client)
    due = TODAY - timedelta(days=1)
    invoice = _issued(
        client,
        company["id"],
        delivery_date=(due - timedelta(days=14)).isoformat(),
        due_date=due.isoformat(),
    )
    assert invoice["overdue"] is True

    paid = _pay(client, company["id"], invoice["id"], GROSS, paid_on=TODAY).json()
    assert paid["overdue"] is False


# --- Undoing a payment ----------------------------------------------------------------


def test_reversing_the_payment_makes_the_invoice_unpaid_again(client: TestClient) -> None:
    company = signed_in_company(client)
    invoice = _issued(client, company["id"])
    paid = _pay(client, company["id"], invoice["id"], GROSS).json()
    voucher_id = paid["payments"][0]["voucher_id"]

    reversal = write(
        client, "POST", f"/api/v1/companies/{company['id']}/cashflow/{voucher_id}/reversal"
    )
    assert reversal.status_code == 201, reversal.text

    # Neither the payment nor its reversal is money the invoice has had.
    again = _invoice(client, company["id"], invoice["id"])
    assert again["payment_state"] == "ubetalt"
    assert Decimal(again["outstanding"]) == GROSS
    assert again["payments"] == []

    # And it can be paid again, which is how a wrong amount or date is put right.
    assert _pay(client, company["id"], invoice["id"], GROSS).status_code == 201


def test_a_payment_is_not_corrected_in_the_register(client: TestClient) -> None:
    """A correction there would post a new voucher that no longer names the invoice."""
    company = signed_in_company(client)
    invoice = _issued(client, company["id"])
    paid = _pay(client, company["id"], invoice["id"], GROSS).json()
    voucher_id = paid["payments"][0]["voucher_id"]

    response = write(
        client,
        "PUT",
        f"/api/v1/companies/{company['id']}/cashflow/{voucher_id}",
        json={
            "type_code": "salg_hoy_sats",
            "occurred_on": PAID_ON.isoformat(),
            "gross_amount": "100.00",
            "description": "Rettet",
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "cannot_reverse_here"
    assert _invoice(client, company["id"], invoice["id"])["payment_state"] == "betalt"


# --- Access ---------------------------------------------------------------------------


def test_another_companys_invoice_cannot_be_paid(client: TestClient) -> None:
    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    first = make_company(client, organization_id)
    second = make_company(client, organization_id, orgnr="910000012", name="Andre AS")
    invoice = _issued(client, first["id"])

    response = _pay(client, second["id"], invoice["id"], GROSS)
    assert response.status_code == 404
    assert _invoice(client, first["id"], invoice["id"])["payment_state"] == "ubetalt"


def test_a_viewer_cannot_register_a_payment(client: TestClient) -> None:
    owner = register(client)
    company = make_company(client, owner["memberships"][0]["organization_id"])
    invoice = _issued(client, company["id"])

    join_company(client, company["id"], email="revisor@example.no", role="viewer")
    assert _pay(client, company["id"], invoice["id"], GROSS).status_code == 403


# --- From a bank line -----------------------------------------------------------------


def _bank_company(client: TestClient, **overrides: Any) -> dict[str, Any]:
    session = register(client)
    return make_company(
        client,
        session["memberships"][0]["organization_id"],
        first_financial_year=BANK_YEAR,
        **overrides,
    )


def _bank_invoice(client: TestClient, company_id: int, unit_price: str) -> dict[str, Any]:
    return _issued(
        client,
        company_id,
        delivery_date=f"{BANK_YEAR}-02-20",
        due_date=f"{BANK_YEAR}-03-06",
        lines=[
            {
                "description": "Rådgivning februar",
                "quantity": "10",
                "unit_price": unit_price,
                "tax_code": "3",
            }
        ],
    )


def _import_paying(client: TestClient, company_id: int, payment_reference: str) -> None:
    """March's statement, its incoming 12 500 kr carrying ``payment_reference``."""
    content = BANK_FIXTURE.read_bytes().replace(FIXTURE_KID, payment_reference.encode())
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/bank/kontoutskrifter",
        files={"file": ("mars.xml", content, "application/xml")},
    )
    assert response.status_code == 201, response.text


def _bank_line(client: TestClient, company_id: int, amount: str) -> dict[str, Any]:
    lines = client.get(
        f"/api/v1/companies/{company_id}/bank/linjer", params={"status": "alle"}
    ).json()
    line: dict[str, Any] = next(line for line in lines if line["amount"] == amount)
    return line


def _settle(client: TestClient, company_id: int, line_id: int, document_id: int) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/bank/linjer/{line_id}/faktura/{document_id}",
    )


def test_a_bank_line_with_the_invoices_kid_pays_it(client: TestClient) -> None:
    company = _bank_company(client)
    # Ten hours at 1 000 kr and 25 % VAT: the 12 500 kr the statement brings in.
    invoice = _bank_invoice(client, company["id"], "1000.00")
    assert invoice["payment_reference"] == kid.for_document_number(
        "1", issue_date=date.fromisoformat(invoice["issue_date"])
    )
    _import_paying(client, company["id"], invoice["payment_reference"])

    line = _bank_line(client, company["id"], "12500.00")
    assert line["invoice"]["document_id"] == invoice["id"]
    assert line["invoice"]["payment_state"] == "ubetalt"
    assert Decimal(line["invoice"]["outstanding"]) == Decimal("12500.00")

    response = _settle(client, company["id"], line["id"], invoice["id"])
    assert response.status_code == 201, response.text
    settled = response.json()
    assert settled["match_kind"] == "opprettet"

    paid = _invoice(client, company["id"], invoice["id"])
    assert paid["payment_state"] == "betalt"
    (payment,) = paid["payments"]
    # The bank's date and amount, and the line points at the payment's voucher.
    assert payment["voucher_id"] == settled["voucher_id"]
    assert payment["paid_on"] == f"{BANK_YEAR}-03-02"
    assert payment["payment_account"] == "1920"
    assert settled["invoice"]["payment_state"] == "betalt"

    again = _settle(client, company["id"], line["id"], invoice["id"])
    assert again.status_code == 409
    assert again.json()["code"] == "bank_line_already_matched"


def test_an_outgoing_line_cannot_pay_an_invoice(client: TestClient) -> None:
    company = _bank_company(client)
    invoice = _bank_invoice(client, company["id"], "1000.00")
    _import_paying(client, company["id"], invoice["payment_reference"])
    fee = _bank_line(client, company["id"], "49.00")

    response = _settle(client, company["id"], fee["id"], invoice["id"])
    assert response.status_code == 422
    assert response.json()["code"] == "bank_match_mismatch"
    assert _invoice(client, company["id"], invoice["id"])["payment_state"] == "ubetalt"


def test_a_line_for_more_than_is_owed_leaves_both_alone(client: TestClient) -> None:
    company = _bank_company(client)
    # 10 000 kr owed, 12 500 kr in: the rest is not a payment of this invoice.
    invoice = _bank_invoice(client, company["id"], "800.00")
    _import_paying(client, company["id"], invoice["payment_reference"])
    line = _bank_line(client, company["id"], "12500.00")

    response = _settle(client, company["id"], line["id"], invoice["id"])
    assert response.status_code == 422
    assert response.json()["code"] == "payment_exceeds_outstanding"
    assert _bank_line(client, company["id"], "12500.00")["voucher_id"] is None
    assert _invoice(client, company["id"], invoice["id"])["payment_state"] == "ubetalt"


def test_a_bank_line_cannot_pay_a_sibling_companys_invoice(client: TestClient) -> None:
    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    ours = make_company(client, organization_id, first_financial_year=BANK_YEAR)
    sibling = make_company(
        client,
        organization_id,
        orgnr="910000012",
        name="Søster AS",
        first_financial_year=BANK_YEAR,
        bank_account="12345678903",
    )
    theirs = _bank_invoice(client, sibling["id"], "1000.00")
    _import_paying(client, ours["id"], theirs["payment_reference"])
    line = _bank_line(client, ours["id"], "12500.00")
    # The KID points nowhere in our books, so no hint either.
    assert line["invoice"] is None

    response = _settle(client, ours["id"], line["id"], theirs["id"])
    assert response.status_code == 404
    assert _invoice(client, sibling["id"], theirs["id"])["payment_state"] == "ubetalt"
