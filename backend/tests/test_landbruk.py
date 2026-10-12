"""The farming add-on: who has it, what it adds, and that nobody else sees it.

Myntos is for every business. A farm gets NIBIO's accounts, its own categories
and a bilag reader that knows a settlement from Tine is a sale; a company
without the add-on must see exactly what it saw before. These pin both halves
down, and the VAT rates that are the reason the farm categories exist at all.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.bilag import interpretation
from myntos.domain.ledger import landbruk
from myntos.domain.ledger.cashflow import CashFlowDirection, get_type
from myntos.domain.ledger.chart import LANDBRUK_ACCOUNTS, LANDBRUK_ACCOUNTS_BY_NUMBER
from myntos.domain.ledger.models import Account, Company
from myntos.saft import codes
from tests.conftest import join_company, write
from tests.test_cashflow_api import THIS_YEAR, record, signed_in_company
from tests.test_kontoplan import in_app


def set_industry(client: TestClient, company_id: int, code: str | None) -> None:
    """What a refresh from Enhetsregisteret would have stored."""

    async def work(session: AsyncSession) -> None:
        await session.execute(
            update(Company).where(Company.id == company_id).values(industry_code=code)
        )

    in_app(client, work)


def account_numbers(client: TestClient, company_id: int) -> set[str]:
    async def work(session: AsyncSession) -> set[str]:
        return set(
            await session.scalars(
                select(Account.account_number).where(Account.company_id == company_id)
            )
        )

    return in_app(client, work)


def farm_company(client: TestClient, **kwargs: Any) -> dict[str, Any]:
    """A company registered under næringskode 01.410, melkeproduksjon på storfe."""
    company = signed_in_company(client, **kwargs)
    set_industry(client, company["id"], "01.410")
    return company


def catalogue_codes(client: TestClient, company_id: int) -> set[str]:
    body = client.get(f"/api/v1/companies/{company_id}/cashflow/types").json()
    return {entry["code"] for entry in body["types"]}


# --- Who has it ---------------------------------------------------------------------


def test_the_naeringskode_decides_until_a_person_does() -> None:
    assert landbruk.by_industry("01.410")
    assert landbruk.by_industry("02.100")
    # Fishing is primærnæring, but none of a farm's categories fit it.
    assert not landbruk.by_industry("03.111")
    assert not landbruk.by_industry("62.010")
    assert not landbruk.by_industry(None)

    company = Company(industry_code="01.410", landbruk=None)
    assert landbruk.is_enabled(company)
    company.landbruk = False
    assert not landbruk.is_enabled(company)
    company = Company(industry_code="96.020", landbruk=True)
    assert landbruk.is_enabled(company)


def test_a_company_that_is_not_a_farm_sees_nothing_of_it(client: TestClient) -> None:
    company = signed_in_company(client)
    codes_seen = catalogue_codes(client, company["id"])
    assert not any(code.startswith("landbruk_") for code in codes_seen)
    body = client.get(f"/api/v1/companies/{company['id']}/cashflow/types").json()
    assert body["segments"] == []
    assert not account_numbers(client, company["id"]) & set(LANDBRUK_ACCOUNTS_BY_NUMBER)

    state = client.get(f"/api/v1/companies/{company['id']}/landbruk").json()
    assert state["enabled"] is False
    assert state["source"] == "naeringskode"


def test_a_farm_by_naeringskode_gets_the_categories_before_any_account_exists(
    client: TestClient,
) -> None:
    """Offered at once, and the account is added the first time it is used."""
    company = farm_company(client)
    body = client.get(f"/api/v1/companies/{company['id']}/cashflow/types").json()
    assert body["segments"] == ["landbruk"]
    melk = next(entry for entry in body["types"] if entry["code"] == "landbruk_melk")
    assert melk["segment"] == "landbruk"
    assert melk["default_tax_code"] == "3"
    assert "3041" in {account["number"] for account in body["accounts"]}

    assert "3071" not in account_numbers(client, company["id"])
    response = record(client, company["id"], type_code="landbruk_melk", gross_amount="1250.00")
    assert response.status_code == 201, response.text
    assert response.json()["account_number"] == "3071"
    assert "3071" in account_numbers(client, company["id"])


def test_switching_it_on_adds_every_account_and_keeps_one_that_means_something_else(
    client: TestClient,
) -> None:
    company = signed_in_company(client)
    # The company already uses 4020 for something of its own.
    added = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/kontoplan",
        json={"number": "4020", "description": "Varekjøp utland"},
    )
    assert added.status_code == 201, added.text

    response = write(
        client, "PUT", f"/api/v1/companies/{company['id']}/landbruk", json={"enabled": True}
    )
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["enabled"] is True
    assert state["source"] == "valgt"
    assert state["kept_accounts"] == ["4020"]
    assert "3071" in state["added_accounts"]
    assert state["missing_accounts"] == []

    numbers = account_numbers(client, company["id"])
    assert set(LANDBRUK_ACCOUNTS_BY_NUMBER) <= numbers
    kontoplan = client.get(f"/api/v1/companies/{company['id']}/kontoplan").json()
    by_number = {a["number"]: a for a in kontoplan["accounts"]}
    assert by_number["4020"]["description"] == "Varekjøp utland"
    # Myntos's proposal, not the person's choice: a later correction may reach it.
    assert by_number["3071"]["grouping_code"] == "3000"
    assert by_number["3071"]["grouping_chosen_at"] is None

    # Switching it on twice adds nothing twice.
    again = write(
        client, "PUT", f"/api/v1/companies/{company['id']}/landbruk", json={"enabled": True}
    ).json()
    assert again["added_accounts"] == []


def test_switching_it_off_hides_the_categories_and_keeps_the_books(client: TestClient) -> None:
    company = farm_company(client)
    entry = record(client, company["id"], type_code="landbruk_kraftfor", gross_amount="500.00")
    assert entry.status_code == 201

    off = write(
        client, "PUT", f"/api/v1/companies/{company['id']}/landbruk", json={"enabled": False}
    )
    assert off.status_code == 200, off.text
    assert off.json()["enabled"] is False
    assert not any(code.startswith("landbruk_") for code in catalogue_codes(client, company["id"]))
    # The posted entry stays, under its name, and its account stays.
    listed = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    assert listed[0]["type_label"] == "Kraftfôr"
    assert "4020" in account_numbers(client, company["id"])

    # And null hands the decision back to the næringskode.
    back = write(
        client, "PUT", f"/api/v1/companies/{company['id']}/landbruk", json={"enabled": None}
    ).json()
    assert back["enabled"] is True
    assert back["source"] == "naeringskode"


def test_only_whoever_administers_the_books_may_switch_it(client: TestClient) -> None:
    owner = farm_company(client)
    join_company(client, owner["id"], email="medlem@example.no", role="member")
    response = write(
        client, "PUT", f"/api/v1/companies/{owner['id']}/landbruk", json={"enabled": False}
    )
    assert response.status_code == 403, response.text
    assert client.get(f"/api/v1/companies/{owner['id']}/landbruk").status_code == 200


# --- What the add-on brings -----------------------------------------------------


def test_every_farm_account_has_a_post_in_skatteetatens_list() -> None:
    for seed in LANDBRUK_ACCOUNTS:
        assert codes.is_choosable_grouping(THIS_YEAR, seed.grouping_category, seed.grouping_code), (
            seed.number
        )


def test_the_rates_are_the_ones_the_law_gives_and_not_foods() -> None:
    """Milk, slaughter animals, live animals and grain are not food yet: 25 %.

    Merverdiavgiftshåndboken M-5-2.4. The mistake this prevents is booking the
    dairy's milk at 15 % because milk is food on a shop shelf.
    """
    for code in ("landbruk_melk", "landbruk_slakt", "landbruk_livdyr", "landbruk_korn"):
        assert get_type(code).default_tax_code == "3", code
    for code in ("landbruk_poteter", "landbruk_gront", "landbruk_egg"):
        assert get_type(code).default_tax_code == "31", code
    # Feed bought is not food either.
    assert get_type("landbruk_kraftfor").default_tax_code == "1"
    # Subsidies are not sales, and land rent is exempt; hunting rights are not.
    assert get_type("landbruk_tilskudd_areal").default_tax_code == "0"
    assert get_type("landbruk_jordleie_inn").default_tax_code == "6"
    assert get_type("landbruk_jakt").default_tax_code == "3"


def test_a_settlement_deduction_books_as_the_sales_side() -> None:
    assert get_type("landbruk_trekk_avgift").books_as is CashFlowDirection.INN
    assert get_type("landbruk_trekk_tilskudd").books_as is CashFlowDirection.INN
    # The share is the farmer's money on the balance sheet, not a sale reduced.
    assert get_type("landbruk_trekk_andel").books_as is CashFlowDirection.UT


# --- The bilag reader -------------------------------------------------------------

MELKEOPPGJOR = """TINE SA
Org.nr 977 350 952 MVA
Melkeoppgjør oktober
Produsent: Reime Gard
Levert melk 20 000 liter
Sum å utbetale 6 800,00
"""


def test_a_farm_reads_a_melkeoppgjor_as_money_in() -> None:
    """With Tine's name at the top, every other rule reads it as a bill from Tine."""
    farm = interpretation.suggest(
        MELKEOPPGJOR,
        today=date(THIS_YEAR, 11, 10),
        own_orgnr="910000004",
        own_name="Reime Gard",
        farm=True,
    )
    assert farm.direction is CashFlowDirection.INN
    assert any("salgsdokumentet ditt" in warning for warning in farm.warnings)

    # The same paper in books without the add-on is left to the ordinary rules:
    # "oppgjør" is also the word on a power company's bill.
    other = interpretation.suggest(
        MELKEOPPGJOR,
        today=date(THIS_YEAR, 11, 10),
        own_orgnr="910000004",
        own_name="Reime Gard",
    )
    assert other.direction is not CashFlowDirection.INN


def test_self_billing_is_money_in_for_everyone() -> None:
    assert interpretation.issued_by_buyer("Selvfaktura nr 12 utstedt av kjøper")
    assert not interpretation.issued_by_buyer("Avregning strøm oktober")
