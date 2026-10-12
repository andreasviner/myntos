"""Composite entries: one bank movement, several trades behind it.

A settlement from the dairy is milk sold less the levy, the share in Tine and the
feed set off against it; a Vipps payout is sales less a fee. Booked under one
category each is wrong somewhere, so a composite entry books each trade as a
line of its own, by the same rules as an ordinary entry, and one payment line
for what they net to.

What these pin down is that the lines land where their categories say, the VAT
on each goes to the side its trade belongs to, the voucher balances and nets to
the bank movement, and a composite entry reverses, corrects, reaches the
MVA-melding and the SAF-T file like any other.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import write
from tests.test_cashflow_api import THIS_YEAR, record, signed_in_company
from tests.test_landbruk import farm_company
from tests.test_mva_api import LAST_YEAR, books, first_period


def composite(
    client: TestClient,
    company_id: int,
    lines: list[dict[str, Any]],
    *,
    description: str = "Oppgjør",
    occurred_on: date | None = None,
    party_id: int | None = None,
) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/cashflow/sammensatt",
        json={
            "occurred_on": (occurred_on or date(THIS_YEAR, 3, 14)).isoformat(),
            "description": description,
            "lines": lines,
            "party_id": party_id,
        },
    )


#: A month's milk from Tine: 12 500 of milk at 25 %, less a levy of 100, 600
#: into the share in Tine and 5 000 of kraftfôr Tine sold the farm.
MILK_SETTLEMENT = [
    {"type_code": "landbruk_melk", "gross_amount": "12500.00"},
    {"type_code": "landbruk_trekk_avgift", "gross_amount": "100.00"},
    {"type_code": "landbruk_trekk_andel", "gross_amount": "600.00"},
    {"type_code": "landbruk_kraftfor", "gross_amount": "5000.00"},
]


def test_a_milk_settlement_books_every_line_where_it_belongs(client: TestClient) -> None:
    company = farm_company(client)
    response = composite(client, company["id"], MILK_SETTLEMENT, description="Melk oktober")
    assert response.status_code == 201, response.text
    entry = response.json()

    # The bank saw the net: 12 500 - 100 - 600 - 5 000.
    assert entry["type_code"] == "sammensatt"
    assert entry["type_label"] == "Sammensatt bilag"
    assert entry["direction"] == "inn"
    assert entry["gross_amount"] == "6800.00"
    # The VAT that moved with the money: output on the milk less input on the
    # feed. The levy and the share carry none.
    assert entry["vat_amount"] == "1500.00"
    assert entry["net_amount"] == "5300.00"
    assert entry["tax_code"] is None

    parts = [
        (
            p["type_code"],
            p["direction"],
            p["account_number"],
            p["tax_code"],
            p["net_amount"],
            p["vat_amount"],
            p["gross_amount"],
        )
        for p in entry["parts"]
    ]
    assert parts == [
        ("landbruk_melk", "inn", "3071", "3", "10000.00", "2500.00", "12500.00"),
        # Against the sale, without VAT: the levy is the farmer's own debt.
        ("landbruk_trekk_avgift", "ut", "3071", "0", "100.00", "0.00", "100.00"),
        # On the balance sheet: the share is still the farmer's money.
        ("landbruk_trekk_andel", "ut", "1355", "0", "600.00", "0.00", "600.00"),
        ("landbruk_kraftfor", "ut", "4020", "1", "4000.00", "1000.00", "5000.00"),
    ]

    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    assert summary["output_vat"] == "2500.00"
    assert summary["input_vat"] == "1000.00"
    assert summary["total_in"] == "6800.00"
    assert summary["by_type"][0]["type_label"] == "Sammensatt bilag"


def test_a_settlement_reaches_the_mva_melding_line_by_line(client: TestClient) -> None:
    """The sale's VAT basis is the gross price: the levy deducted is not a discount."""
    company = books(client)
    enabled = write(
        client, "PUT", f"/api/v1/companies/{company['id']}/landbruk", json={"enabled": True}
    )
    assert enabled.status_code == 200, enabled.text
    response = composite(client, company["id"], MILK_SETTLEMENT, occurred_on=date(LAST_YEAR, 2, 8))
    assert response.status_code == 201, response.text

    period = first_period(client, company["id"])
    lines = [(ln["mva_kode"], ln["grunnlag"], ln["merverdiavgift"]) for ln in period["lines"]]
    assert lines == [("3", "10000.00", "2500.00"), ("1", None, "-1000.00")]
    assert period["fastsatt_merverdiavgift"] == "1500.00"
    assert period["reconciliation"]["difference"] == "0.00"


def test_a_deduction_with_vat_gives_back_output_vat_rather_than_claiming_input_vat(
    client: TestClient,
) -> None:
    """A quality deduction on the price reduces the sale and the VAT charged on it.

    Booked as a purchase it would put 50 kroner on 2710 as a deduction for tax
    nobody ever charged the farm.
    """
    company = farm_company(client)
    response = composite(
        client,
        company["id"],
        [
            {"type_code": "landbruk_melk", "gross_amount": "1250.00"},
            {"type_code": "landbruk_trekk_avgift", "gross_amount": "250.00", "tax_code": "3"},
        ],
    )
    assert response.status_code == 201, response.text
    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    assert summary["output_vat"] == "200.00"
    assert summary["input_vat"] == "0.00"


def test_any_business_can_split_a_payout_into_sales_and_a_fee(client: TestClient) -> None:
    """Not a farm feature: a Vipps payout is sales less the fee, for everyone."""
    company = signed_in_company(client)
    response = composite(
        client,
        company["id"],
        [
            {"type_code": "salg_hoy_sats", "gross_amount": "1250.00", "description": "Salg"},
            {"type_code": "bankgebyr", "gross_amount": "21.88", "description": "Vipps-gebyr"},
        ],
        description="Vipps-utbetaling",
    )
    assert response.status_code == 201, response.text
    entry = response.json()
    assert entry["gross_amount"] == "1228.12"
    assert [p["description"] for p in entry["parts"]] == ["Salg", "Vipps-gebyr"]
    assert entry["parts"][1]["account_number"] == "7770"


def test_a_composite_entry_can_go_out_of_the_bank(client: TestClient) -> None:
    """Deductions larger than the sale: the settlement is money out."""
    company = signed_in_company(client)
    entry = composite(
        client,
        company["id"],
        [
            {"type_code": "salg_hoy_sats", "gross_amount": "1000.00"},
            {"type_code": "varekjop", "gross_amount": "2500.00"},
        ],
    ).json()
    assert entry["direction"] == "ut"
    assert entry["gross_amount"] == "1500.00"
    # 200 out on the sale's side, 500 in on the purchase's: net 300 of VAT
    # moved in the direction of the money.
    assert entry["vat_amount"] == "300.00"


def test_one_line_or_lines_that_cancel_are_refused(client: TestClient) -> None:
    company = signed_in_company(client)
    single = composite(client, company["id"], [{"type_code": "varekjop", "gross_amount": "1"}])
    assert single.status_code == 422

    nothing = composite(
        client,
        company["id"],
        [
            {"type_code": "salg_hoy_sats", "gross_amount": "500.00"},
            {"type_code": "varekjop", "gross_amount": "500.00"},
        ],
    )
    assert nothing.status_code == 422, nothing.text
    assert nothing.json()["code"] == "invalid_composite_entry"
    # Nothing was posted.
    assert client.get(f"/api/v1/companies/{company['id']}/cashflow").json() == []


def test_a_farm_category_needs_the_farming_add_on(client: TestClient) -> None:
    company = signed_in_company(client)
    response = composite(client, company["id"], MILK_SETTLEMENT)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "segment_not_enabled"
    single = record(client, company["id"], type_code="landbruk_melk", gross_amount="100.00")
    assert single.status_code == 409
    assert single.json()["code"] == "segment_not_enabled"


def test_a_composite_entry_reverses_as_a_whole(client: TestClient) -> None:
    company = farm_company(client)
    entry = composite(client, company["id"], MILK_SETTLEMENT).json()

    response = write(
        client, "POST", f"/api/v1/companies/{company['id']}/cashflow/{entry['id']}/reversal"
    )
    assert response.status_code == 201, response.text
    reversal = response.json()
    assert reversal["type_code"] == "sammensatt"
    assert reversal["direction"] == "ut"
    assert [p["direction"] for p in reversal["parts"]] == ["ut", "inn", "inn", "inn"]

    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    assert summary["output_vat"] == "0.00"
    assert summary["input_vat"] == "0.00"
    assert summary["entry_count"] == 0


def test_an_ordinary_entry_can_be_split_after_the_fact(client: TestClient) -> None:
    """The net milk money booked as one sale, then corrected into its lines."""
    company = farm_company(client)
    wrong = record(client, company["id"], type_code="landbruk_melk", gross_amount="6800.00")
    assert wrong.status_code == 201, wrong.text

    response = write(
        client,
        "PUT",
        f"/api/v1/companies/{company['id']}/cashflow/sammensatt/{wrong.json()['id']}",
        json={
            "occurred_on": date(THIS_YEAR, 3, 14).isoformat(),
            "description": "Melk oktober",
            "lines": MILK_SETTLEMENT,
        },
    )
    assert response.status_code == 201, response.text
    corrected = response.json()
    assert corrected["corrects_id"] == wrong.json()["id"]
    assert corrected["gross_amount"] == "6800.00"

    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    assert summary["output_vat"] == "2500.00"
    assert summary["entry_count"] == 1


def test_a_composite_entry_exports_as_a_valid_saft_file(client: TestClient) -> None:
    company = farm_company(client)
    assert composite(client, company["id"], MILK_SETTLEMENT).status_code == 201
    checked = client.get(f"/api/v1/companies/{company['id']}/saft/kontroll?aar={THIS_YEAR}")
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["exportable"] is True, body["findings"]
    assert Decimal(body["total_debit"]) == Decimal(body["total_credit"])


def test_the_listing_reads_a_composite_entry_back_line_by_line(client: TestClient) -> None:
    company = farm_company(client)
    composite(client, company["id"], MILK_SETTLEMENT)
    listed = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    assert len(listed) == 1
    assert [p["type_label"] for p in listed[0]["parts"]] == [
        "Melkeoppgjør",
        "Trekk: omsetningsavgift og forskningsavgift",
        "Trekk: andelsinnskudd i samvirke",
        "Kraftfôr",
    ]
