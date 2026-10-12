"""VAT registration as a property of a date, deciding new entries and nothing else.

Before this, an unregistered company booking a 1 250 kroner purchase got 250 of
it carved onto 2710, a deduction no melding would ever claim, and could pick any
code it liked. What these pin down:

- an unregistered company books gross, with code 0, and is refused a deduction
  or output code with a reason;
- the registration date counts, on the entry's own date;
- switching the status moves the defaults for *new* entries and leaves every
  posted line exactly as it was.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.ledger.models import Journal, Transaction, TransactionLine
from myntos.domain.ledger.vatstatus import UNREGISTERED_TAX_CODES
from tests.conftest import register, write
from tests.test_cashflow_api import THIS_YEAR, create_company
from tests.test_kontoplan import in_app


def books(client: TestClient, *, vat_registered: bool) -> dict[str, Any]:
    session = register(client)
    return create_company(
        client, session["memberships"][0]["organization_id"], vat_registered=vat_registered
    )


def pay(
    client: TestClient,
    company_id: int,
    type_code: str,
    *,
    tax_code: str | None = None,
    on: date | None = None,
    gross: str = "1250.00",
) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/cashflow",
        json={
            "type_code": type_code,
            "occurred_on": (on or date(THIS_YEAR, 3, 14)).isoformat(),
            "gross_amount": gross,
            "description": type_code,
            "tax_code": tax_code,
        },
    )


def patch(client: TestClient, company_id: int, **fields: Any) -> Any:
    return write(client, "PATCH", f"/api/v1/companies/{company_id}", json=fields)


def ledger_lines(client: TestClient, company_id: int) -> list[tuple[Any, ...]]:
    """Every posted line, raw, for comparing before and after."""

    async def read(session: AsyncSession) -> list[tuple[Any, ...]]:
        rows = await session.execute(
            select(
                TransactionLine.id,
                TransactionLine.account_id,
                TransactionLine.debit,
                TransactionLine.credit,
                TransactionLine.tax_code_id,
                TransactionLine.tax_rate,
                TransactionLine.tax_base,
                TransactionLine.debit_tax_amount,
                TransactionLine.credit_tax_amount,
            )
            .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
            .join(Journal, Journal.id == Transaction.journal_id)
            .where(Journal.company_id == company_id)
            .order_by(TransactionLine.id)
        )
        return [tuple(row) for row in rows]

    return in_app(client, read)


def test_an_unregistered_purchase_is_booked_gross_to_the_cost(client: TestClient) -> None:
    company = books(client, vat_registered=False)
    response = pay(client, company["id"], "kontorrekvisita")
    assert response.status_code == 201, response.text
    entry = response.json()
    assert entry["tax_code"] == "0"
    assert Decimal(entry["net_amount"]) == Decimal("1250.00")
    assert Decimal(entry["vat_amount"]) == Decimal("0.00")
    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    assert Decimal(summary["input_vat"]) == Decimal("0.00")


def test_an_unregistered_sale_carries_no_output_vat(client: TestClient) -> None:
    company = books(client, vat_registered=False)
    entry = pay(client, company["id"], "salg_hoy_sats").json()
    assert entry["tax_code"] == "0"
    assert Decimal(entry["vat_amount"]) == Decimal("0.00")


def test_an_unregistered_company_cannot_claim_a_deduction(client: TestClient) -> None:
    company = books(client, vat_registered=False)
    for code in ("1", "3", "14", "81"):
        response = pay(client, company["id"], "kontorrekvisita", tax_code=code)
        assert response.status_code == 422, code
        assert response.json()["code"] == "tax_code_not_allowed"
    # Border VAT is part of what the goods cost when nothing can be deducted.
    tollpass = pay(client, company["id"], "mva_innforsel")
    assert tollpass.status_code == 422
    # Remote services bought from abroad are still owed without registration.
    owed = pay(client, company["id"], "datakostnad", tax_code="87")
    assert owed.status_code == 201, owed.text


def test_the_companys_catalogue_follows_its_status(client: TestClient) -> None:
    company = books(client, vat_registered=False)
    catalogue = client.get(f"/api/v1/companies/{company['id']}/cashflow/types").json()
    assert catalogue["vat_registered"] is False
    assert {t["default_tax_code"] for t in catalogue["types"]} == {"0"}
    assert {c["code"] for c in catalogue["tax_codes"]} == set(UNREGISTERED_TAX_CODES)
    assert "mva_innforsel" not in {t["code"] for t in catalogue["types"]}
    # Locked accounts are never offered as an alternative.
    assert "2710" not in {a["number"] for a in catalogue["accounts"]}

    assert patch(client, company["id"], vat_registered=True).status_code == 200
    registered = client.get(f"/api/v1/companies/{company['id']}/cashflow/types").json()
    assert registered["vat_registered"] is True
    varekjop = next(t for t in registered["types"] if t["code"] == "varekjop")
    assert varekjop["default_tax_code"] == "1"
    assert varekjop["account_type"] == "kostnad"


def test_switching_status_never_touches_a_posted_entry(client: TestClient) -> None:
    company = books(client, vat_registered=True)
    pay(client, company["id"], "kontorrekvisita")
    pay(client, company["id"], "salg_hoy_sats")
    before = ledger_lines(client, company["id"])
    listed_before = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()

    assert patch(client, company["id"], vat_registered=False).status_code == 200
    after_new = pay(client, company["id"], "kontorrekvisita").json()
    assert after_new["tax_code"] == "0"
    assert patch(client, company["id"], vat_registered=True).status_code == 200

    after = ledger_lines(client, company["id"])
    assert after[: len(before)] == before
    listed_after = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    old = {e["id"]: e for e in listed_after if e["id"] in {x["id"] for x in listed_before}}
    for entry in listed_before:
        assert old[entry["id"]] == entry


def test_the_registration_date_decides_by_the_entrys_own_date(client: TestClient) -> None:
    company = books(client, vat_registered=True)
    since = date(THIS_YEAR, 5, 1)
    response = patch(client, company["id"], vat_registered_from=since.isoformat())
    assert response.status_code == 200, response.text
    assert response.json()["vat_registered_from"] == since.isoformat()

    before = pay(client, company["id"], "kontorrekvisita", on=date(THIS_YEAR, 4, 30)).json()
    after = pay(client, company["id"], "kontorrekvisita", on=since).json()
    assert before["tax_code"] == "0"
    assert after["tax_code"] == "1"
    assert (
        pay(
            client, company["id"], "kontorrekvisita", tax_code="1", on=date(THIS_YEAR, 4, 2)
        ).status_code
        == 422
    )

    # Sent as null, the date is cleared and the whole book is registered.
    cleared = patch(client, company["id"], vat_registered_from=None)
    assert cleared.json()["vat_registered_from"] is None
    assert (
        pay(client, company["id"], "kontorrekvisita", on=date(THIS_YEAR, 4, 2)).json()["tax_code"]
        == "1"
    )


def test_a_proposal_from_an_invoice_does_not_offer_an_unregistered_company_its_vat(
    client: TestClient,
) -> None:
    """The invoice says 25 %; this company cannot deduct it, and the form says so."""
    from tests.test_cashflow_api import INVOICE_LINES, interpret, text_pdf

    company = books(client, vat_registered=False)
    suggestion = interpret(client, company["id"], text_pdf(INVOICE_LINES)).json()
    assert suggestion["tax_code"] == "0"
    assert any("ikke mva-registrert" in warning for warning in suggestion["warnings"])
    posted = pay(
        client,
        company["id"],
        suggestion["type_code"],
        tax_code=suggestion["tax_code"],
        gross=suggestion["gross_amount"],
    )
    assert posted.status_code == 201, posted.text


def test_a_patch_without_the_date_leaves_it_alone(client: TestClient) -> None:
    company = books(client, vat_registered=True)
    patch(client, company["id"], vat_registered_from=f"{THIS_YEAR}-05-01")
    response = patch(client, company["id"], name="Nytt Navn AS")
    assert response.json()["vat_registered_from"] == f"{THIS_YEAR}-05-01"
