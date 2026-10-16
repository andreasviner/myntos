"""Resultat og balanse for every year: the books read the way an årsregnskap is.

The page exists because a company that brought last year over from its old
program read «Inn i år 0,00» on the overview and had nowhere to see the year it
brought. So the tests are about years side by side: a closed year and the one
after it, the result in equity once it is disposed and as «Udisponert
resultat» before, and the balance sheet balancing in each.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from myntos.domain.ledger.models import AccountType, Company
from myntos.domain.ledger.report import group_of
from myntos.domain.mva.codes import Meldingskategori
from myntos.domain.mva.naering import books_behind
from myntos.domain.mva.periods import PeriodType
from tests.test_arsoppgjor_api import (
    LAST_YEAR,
    _a_profitable_year,
    _books,
    _close,
    _last_year,
    _line,
    _post_opening,
)
from tests.test_cashflow_api import THIS_YEAR, record
from tests.test_overview_figures import _bank_on_2390_file
from tests.test_saft_innlesing import EARLIER, _post, _upload


def _report(client: TestClient, company_id: int) -> list[dict[str, Any]]:
    response = client.get(f"/api/v1/companies/{company_id}/regnskap")
    assert response.status_code == 200, response.text
    years: list[dict[str, Any]] = response.json()["years"]
    return years


def _amounts(lines: list[dict[str, Any]]) -> dict[str, Decimal]:
    return {line["key"]: Decimal(line["amount"]) for line in lines}


def _year(years: list[dict[str, Any]], year: int) -> dict[str, Any]:
    found: dict[str, Any] = next(y for y in years if y["year"] == year)
    return found


def test_a_closed_year_and_the_next_side_by_side(client: TestClient) -> None:
    company = _books(client)
    _post_opening(
        client, company["id"], [_line("1920", debit="30000.00"), _line("2000", credit="30000.00")]
    )
    # 10 000 sold and 1 000 spent, net of VAT; closed with a tax cost of 1 980.
    _a_profitable_year(client, company["id"])
    assert _close(client, company["id"], skattekostnad="1980.00").status_code == 201
    sold = record(client, company["id"], type_code="salg_hoy_sats", gross_amount="2500.00")
    assert sold.status_code == 201, sold.text

    years = _report(client, company["id"])
    assert [y["year"] for y in years] == [THIS_YEAR, LAST_YEAR]

    last = _year(years, LAST_YEAR)
    assert last["closed"] is True
    result = _amounts(last["resultat"])
    assert result["salgsinntekter"] == Decimal("10000.00")
    assert result["driftsinntekter"] == Decimal("10000.00")
    assert result["driftskostnader"] == Decimal("1000.00")
    assert result["driftsresultat"] == Decimal("9000.00")
    assert result["skattekostnad"] == Decimal("1980.00")
    assert result["arsresultat"] == Decimal("7020.00")
    # The disposition moved the result; it is not a cost and not in the result.
    assert "disponering" not in result

    balance = _amounts(last["balanse"])
    assert balance["bankinnskudd"] == Decimal("41250.00")
    assert balance["eiendeler"] == balance["egenkapital_og_gjeld"] == Decimal("41250.00")
    # Disposed: on equity, nothing left undisposed.
    assert balance["egenkapital"] == Decimal("37020.00")
    assert balance["udisponert_resultat"] == 0
    assert balance["kortsiktig_gjeld"] == Decimal("4230.00")

    this = _year(years, THIS_YEAR)
    assert this["closed"] is False
    assert _amounts(this["resultat"])["arsresultat"] == Decimal("2000.00")
    balance = _amounts(this["balanse"])
    # Not closed yet: this year's result is in equity as undisposed.
    assert balance["udisponert_resultat"] == Decimal("2000.00")
    assert balance["egenkapital"] == Decimal("37020.00")
    assert balance["eiendeler"] == balance["egenkapital_og_gjeld"]

    # The years line up row for row.
    assert [li["key"] for li in this["resultat"]] == [li["key"] for li in last["resultat"]]
    assert [li["key"] for li in this["balanse"]] == [li["key"] for li in last["balanse"]]


def test_a_group_lists_its_accounts_and_a_subtotal_lists_none(client: TestClient) -> None:
    company = _books(client)
    _post_opening(
        client, company["id"], [_line("1920", debit="30000.00"), _line("2000", credit="30000.00")]
    )
    _a_profitable_year(client, company["id"])
    last = _year(_report(client, company["id"]), LAST_YEAR)
    lines = {line["key"]: line for line in last["resultat"]}
    assert lines["salgsinntekter"]["kind"] == "gruppe"
    assert [a["account_number"] for a in lines["salgsinntekter"]["accounts"]] == ["3000"]
    assert lines["driftsinntekter"]["kind"] == "sum"
    assert lines["driftsinntekter"]["accounts"] == []
    # A class nothing was posted to in any year is not shown.
    assert "lonnskostnad" not in lines
    # Open: the result is undisposed, and the balance sheet still balances.
    balance = _amounts(last["balanse"])
    assert balance["udisponert_resultat"] == Decimal("9000.00")
    assert balance["eiendeler"] == balance["egenkapital_og_gjeld"]


def test_a_year_with_nothing_in_it_says_so(client: TestClient) -> None:
    company = _books(client)
    _post_opening(
        client, company["id"], [_line("1920", debit="30000.00"), _line("2000", credit="30000.00")]
    )
    _a_profitable_year(client, company["id"])
    assert _close(client, company["id"], skattekostnad="1980.00").status_code == 201
    this = _year(_report(client, company["id"]), THIS_YEAR)
    assert this["voucher_count"] == 0
    assert this["last_entry"] is None
    assert _amounts(this["resultat"])["arsresultat"] == 0
    # The balance carries over all the same.
    assert _amounts(this["balanse"])["bankinnskudd"] == Decimal("41250.00")
    last = _year(_report(client, company["id"]), LAST_YEAR)
    assert last["last_entry"] == f"{LAST_YEAR}-12-31"


def test_another_programs_numbering_falls_back_on_the_account_type() -> None:
    assert group_of("3000", AccountType.INNTEKT).key == "salgsinntekter"
    assert group_of("1920", AccountType.EIENDEL).key == "bankinnskudd"
    assert group_of("8220", AccountType.INNTEKT).key == "finansinntekter"
    assert group_of("8220", AccountType.KOSTNAD).key == "finanskostnader"
    assert group_of("K-100", AccountType.EIENDEL).key == "andre_eiendeler"
    assert group_of("K-100", AccountType.KOSTNAD).key == "andre_driftskostnader"


def test_the_overview_says_where_the_books_stop(client: TestClient) -> None:
    company = _books(client)
    _post_opening(
        client, company["id"], [_line("1920", debit="30000.00"), _line("2000", credit="30000.00")]
    )
    _a_profitable_year(client, company["id"])
    summary = client.get(
        f"/api/v1/companies/{company['id']}/cashflow/summary",
        params={"financial_year": THIS_YEAR},
    ).json()
    # Nothing this year, and the books end on last year's last entry.
    assert summary["entry_count"] == 0
    assert summary["books_through"] == _last_year()


def test_a_year_brought_over_from_saf_t_balances_and_the_overview_says_so(
    client: TestClient,
) -> None:
    """The case the page was built for: last year imported, nothing this year."""
    company = _books(client)
    uploaded = _upload(client, company["id"], (f"{EARLIER}.xml", _bank_on_2390_file()))
    assert _post(client, company["id"], uploaded["id"]).status_code == 202

    imported = _year(_report(client, company["id"]), EARLIER)
    assert imported["voucher_count"] > 0
    result = _amounts(imported["resultat"])
    # Milk sold for 1 000 net, feed bought for 400.
    assert result["driftsinntekter"] == Decimal("1000.00")
    assert result["arsresultat"] == Decimal("600.00")
    balance = _amounts(imported["balanse"])
    assert balance["eiendeler"] == balance["egenkapital_og_gjeld"]
    assert balance["udisponert_resultat"] == Decimal("600.00")

    summary = client.get(
        f"/api/v1/companies/{company['id']}/cashflow/summary",
        params={"financial_year": THIS_YEAR},
    ).json()
    assert summary["books_through"] is not None
    assert summary["books_through"] < f"{THIS_YEAR}-01-01"
    # The milk's VAT was never settled in the file: still on 2700, owed then.
    assert summary["vat_position"] == "250.00"
    assert summary["vat_settled"] == "0.00"


# --- When the overview calls the books behind -----------------------------------------


def _company(period_type: PeriodType, category: Meldingskategori) -> Company:
    return Company(vat_period_type=period_type.value, vat_category=category.value)


def test_new_year_alone_does_not_put_the_books_behind() -> None:
    """Last year's final term is open and real until its deadline, 10 February."""
    company = _company(PeriodType.TO_MAANEDER, Meldingskategori.ALMINNELIG)
    december = date(2026, 12, 31)
    assert books_behind(company, december, date(2027, 1, 1)) is False
    assert books_behind(company, december, date(2027, 2, 10)) is False
    assert books_behind(company, december, date(2027, 2, 11)) is True


def test_the_deadline_of_the_term_the_books_stop_in_decides() -> None:
    company = _company(PeriodType.TO_MAANEDER, Meldingskategori.ALMINNELIG)
    # Mid-March is 2. termin, due 10 June.
    assert books_behind(company, date(2026, 3, 14), date(2026, 6, 10)) is False
    assert books_behind(company, date(2026, 3, 14), date(2026, 6, 11)) is True
    assert books_behind(company, None, date(2026, 6, 11)) is False


def test_a_yearly_farm_melding_waits_until_april() -> None:
    company = _company(PeriodType.AAR, Meldingskategori.PRIMAERNAERING)
    assert books_behind(company, date(2025, 12, 31), date(2026, 3, 31)) is False
    assert books_behind(company, date(2025, 12, 31), date(2026, 10, 15)) is True


def test_the_overview_marks_its_positions_as_of_the_day_the_books_stop(
    client: TestClient,
) -> None:
    company = _books(client)
    _post_opening(
        client, company["id"], [_line("1920", debit="30000.00"), _line("2000", credit="30000.00")]
    )
    _a_profitable_year(client, company["id"])
    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    # Last year's March voucher is in a term whose deadline passed long ago.
    assert summary["positions_as_of"] == _last_year()
