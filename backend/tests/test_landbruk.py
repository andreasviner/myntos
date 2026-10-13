"""The farming add-on: who has it, what it adds, and that nobody else sees it.

Myntos is for every business. A farm gets NIBIO's accounts, its own categories
and a bilag reader that knows a settlement from Tine is a sale; a company
without the add-on must see exactly what it saw before. These pin both halves
down, and the VAT rates that are the reason the farm categories exist at all.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
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


#: A dairy's avregningsoppgave in the shape Q-Meieriene prints it: the period's
#: date beside the payout date, the posts summed to a net with the VAT on that
#: net, a membership fee below it with VAT of its own, and the letterhead in a
#: footer five lines above the organisasjonsnummer. Figures invented.
AVREGNINGSOPPGAVE = """Avregningsoppgave
Bilagsnr:  1234 Periode: Oktober , 2026
Produsentnr: 1111 2222 00 Avregningsdato: 31.10.2026
Gårds/Bruksnr: 1-2/3/0 Utbetalingsdato: 09.11.2026
Nordmann Gard
Varenr Tekst Mengde Pris Beløp
1001 Kumelk innenfor kvote 10.000,0 6,0000 60.000,00
1005 Omsetningsavgift -10.000,0 ,1000 -1.000,00
Netto avregnet: 59.000,00
25,0% merverdiavgift av 59.000,00 14.750,00
Beløp inkl. mva.: 73.750,00 *
7001 Medlemsavg HK -1,0 1.000,0000 -1.000,00
Beløp inkl. mva.: -1.250,00 *
Godskrives, overført til bankkonto: 1234.56.78903 72.500,00
Bygdemeieriet AS
Meierivn 1
4353  Klepp st
Tlf:  51000000
Meierinr.:  123
Org.nr.:  NO910000020 MVA
"""


def test_a_dairys_avregningsoppgave_is_read_as_the_settlement_it_is() -> None:
    """Q-Meieriene's September 2026 settlement, the pilot's first, in its shape.

    Every field below came back wrong the first time: «Avregningsoppgave» was not
    a settlement word, the payout date was read as the bilag's date because
    «utbetalingsdato» contains «betalingsdato», the larger «Beløp inkl. mva.»
    (the sale before the fee) was the amount, the street was the counterparty,
    and the sum check told the farmer to check a total that was right.
    """
    found = interpretation.suggest(
        AVREGNINGSOPPGAVE,
        today=date(THIS_YEAR, 11, 12),
        own_orgnr="910000012",
        own_name="Nordmann Gard",
        farm=True,
    )
    assert found.direction is CashFlowDirection.INN
    assert found.issued_by_buyer
    assert any("salgsdokumentet ditt" in warning for warning in found.warnings)
    # The day the period was settled: a December settlement paid in January is
    # still the year before's sale.
    assert found.occurred_on == date(2026, 10, 31)
    # What reached the bank: the sale less the fee.
    assert found.gross_amount == Decimal("72500.00")
    assert found.counterparty_name == "Bygdemeieriet AS"
    assert not any("summerer seg ikke" in warning for warning in found.warnings)


#: A slaughterhouse's settlement in the shape Fatland prints its «Avregning»:
#: the name set by a logo over two lines, the register spelt «Foretagsreg» with
#: the number spaced and «MVA» behind it, no word for the trade anywhere, the
#: varenr and the skrottnr as the first two columns, and the sum, the VAT and the
#: total in a column too narrow for six figures, so each wraps at its thousands
#: space. Figures invented; the layout is the one that came back wrong.
SLAKTEAVREGNING = """BYGDE
Slakteri AS
Slakterivegen 250
4300 Sandnes
Telefon: 51 00 00 00
Telefaks: 51 00 00 01
Foretagsreg: NO 910 000 039 MVA
Avregning nr. 01234567
Dato: 2026-10-08
Leverandørnr: 1234
Produsentnr: 1111222200
Kommune 1119
Utbetalingsdato:2026-10-20
NORDMANN GARD Bankkonto: 12345678903
NORDMANN 1
4365 - NÆRBØ
Varenr
Øre/
Skrottnr Tekst USR
Kjøtt/
Fett% Antall Vekt Pris Beløp
162060 341 UNG OKSE O+ 3- 1,00 344,00 65,60 22 566,40
162050 346 UNG OKSE O 3 1,00 325,90 63,20 20 596,88
RetInmstor Rett innmelding storfe 2,00 669,90 2,00 1 339,80
SfKuO_Bedr Kvalitetstilskudd storfe 2,00 669,90 9,20 6 163,08
OmsAvgStor Omsetningsavgift Storfe 2,00 669,90 -0,50 -334,95
Sum brutto 50
331,21
Moms 12 582,80
Total 62
914,01
Vedtaket om tilskudd kan påklages til Statens
landbruks-
forvaltning, jf. forvaltningsloven kap. VI.
Utbetalt:
62 914,01
Hittil i år avregnet: 740 109,52 Hittil i år utbetalt bank: 490 323,82
"""


def test_a_slaughterhouses_avregning_is_read_as_the_settlement_it_is() -> None:
    """Fatland's slakteavregning for September 2026, the pilot's second settlement.

    It came back as a purchase of 255 kroner, with 913 251 342 kroner of VAT,
    from «Foretagsreg», and the model, offered only money-out categories, chose
    a credit note on a slaughter settlement. Every field was ours to get wrong.
    """
    found = interpretation.read(
        SLAKTEAVREGNING, today=date(THIS_YEAR, 11, 12), own_orgnr="910000012"
    )
    # «Total 62» over «914,01» is one figure, and the orgnr is not VAT.
    assert found.gross_amount == Decimal("62914.01")
    assert found.vat_amount == Decimal("12582.80")
    assert found.net_amount == Decimal("50331.21")
    assert found.vat_rate == Decimal("0.25")
    # Varenr 162060 and skrottnr 341 pass the check digit together.
    assert found.orgnrs == ("910000039",)
    assert found.seller_name == "BYGDE Slakteri AS"
    assert (found.invoice_number, found.number_kind) == ("01234567", "Avregning")
    assert found.invoice_date == date(2026, 10, 8)
    assert found.due_date == date(2026, 10, 20)

    farm = interpretation.suggest(
        SLAKTEAVREGNING,
        today=date(THIS_YEAR, 11, 12),
        own_orgnr="910000012",
        own_name="Nordmann Gard",
        reading=found,
        farm=True,
    )
    assert farm.direction is CashFlowDirection.INN
    assert farm.issued_by_buyer
    assert farm.tax_code == "3"
    assert farm.counterparty_orgnr == "910000039"
    assert farm.description is not None
    assert farm.description.startswith("Avregning 01234567")


def test_an_avregning_is_a_settlement_only_when_it_pays_out_and_only_on_a_farm() -> None:
    """«Avregning» is also the power company's word; what it says about the money differs."""
    assert interpretation.issued_by_buyer(SLAKTEAVREGNING, farm=True)
    assert not interpretation.issued_by_buyer(SLAKTEAVREGNING)
    power = "Avregning strøm oktober\nÅ betale 1 200,00\nForfallsdato 20.11.2026"
    assert not interpretation.issued_by_buyer(power, farm=True)
