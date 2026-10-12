"""Inngående balanse and årsavslutning, end to end.

Real postings through the API, real SQLite, and the SAF-T file as the reader
that matters most: the opening balance has to arrive there as an *opening*
balance, and a closed year's result has to arrive on equity. The file is
validated against Skatteetaten's XSDs wherever it is produced.

The books start the year before this one, so that year is over and can be
closed; this year is the one that is still running.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import join_company, register, write
from tests.test_cashflow_api import OTHER_ORGNR, THIS_YEAR, record
from tests.test_saft_api import NS, _accounts, _parse, _validate

LAST_YEAR = THIS_YEAR - 1

pytestmark = pytest.mark.skipif(
    LAST_YEAR < 2025, reason="SAF-T is only exported from 2025, and the tests read the file"
)


# --- Setting up a set of books -------------------------------------------------------


def _company(
    client: TestClient,
    organization_id: int,
    *,
    form: str | None = "AS",
    orgnr: str = "910000004",
    name: str = "Overgang AS",
) -> dict[str, Any]:
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": organization_id,
            "orgnr": orgnr,
            "name": name,
            "first_financial_year": LAST_YEAR,
            "organisation_form": form,
            # Registered, because the scenarios are written net of VAT: an
            # unregistered company books the gross amount to the cost.
            "vat_registered": True,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _books(client: TestClient, form: str | None = "AS") -> dict[str, Any]:
    session = register(client)
    return _company(client, session["memberships"][0]["organization_id"], form=form)


def _party(client: TestClient, company_id: int, party_type: str, name: str) -> int:
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/parties",
        json={"party_type": party_type, "name": name},
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def _line(account: str, debit: str = "0", credit: str = "0", **extra: Any) -> dict[str, Any]:
    return {"account_number": account, "debit": debit, "credit": credit, **extra}


def _as_opening(customer: int) -> list[dict[str, Any]]:
    """An AS's balance sheet on 1 January: bank, one open invoice, share capital, equity."""
    return [
        _line("1920", debit="50000.00"),
        _line("1500", debit="10000.00", party_id=customer, description="Faktura 1043"),
        _line("2000", credit="30000.00"),
        _line("2050", credit="25000.00"),
        _line("2400", credit="5000.00"),
    ]


def _post_opening(
    client: TestClient, company_id: int, lines: list[dict[str, Any]], **kw: Any
) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/inngaende-balanse",
        json={"lines": lines, **kw},
    )


def _last_year(day: int = 14, month: int = 3) -> str:
    return date(LAST_YEAR, month, day).isoformat()


def _close(client: TestClient, company_id: int, year: int = LAST_YEAR, **kw: Any) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/arsavslutning",
        json={"aar": year, "bekreftet": True, **kw},
    )


def _saft(client: TestClient, company_id: int, year: int, tmp_path: Path) -> ET.Element:
    checked = client.get(f"/api/v1/companies/{company_id}/saft/kontroll?aar={year}")
    assert checked.status_code == 200, checked.text
    assert checked.json()["exportable"] is True, checked.json()["findings"]
    response = client.get(f"/api/v1/companies/{company_id}/saft/fil?aar={year}")
    assert response.status_code == 200, response.text
    _validate(response.content, "1.30", tmp_path)
    _validate(response.content, "1.40", tmp_path)
    return _parse(response.content)


def _side(account: ET.Element, prefix: str) -> Decimal:
    """Net balance, debit positive, as the file states it."""
    debit = account.findtext(f"s:{prefix}DebitBalance", namespaces=NS)
    credit = account.findtext(f"s:{prefix}CreditBalance", namespaces=NS)
    return Decimal(debit) if debit is not None else -Decimal(str(credit))


# --- Inngående balanse: the review ---------------------------------------------------


def test_the_review_balances_and_names_every_line(client: TestClient) -> None:
    company = _books(client)
    customer = _party(client, company["id"], "kunde", "Gammel Kunde AS")
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/inngaende-balanse/kontroll",
        json={"lines": _as_opening(customer)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["year"] == LAST_YEAR
    assert body["postable"] is True, body["findings"]
    assert Decimal(body["total_debit"]) == Decimal("60000.00") == Decimal(body["total_credit"])
    assert Decimal(body["difference"]) == 0
    assert body["lines"][1]["party_name"] == "Gammel Kunde AS"
    assert body["lines"][2]["account_description"] == "Aksjekapital"
    # 2400 is not split on suppliers: allowed, and said.
    assert [f["code"] for f in body["findings"]] == ["uten_aapne_poster"]


@pytest.mark.parametrize(
    ("lines", "code"),
    [
        ([_line("1920", debit="100.00"), _line("2050", credit="99.00")], "ubalanse"),
        ([_line("1920", debit="100.00"), _line("3000", credit="100.00")], "resultatkonto"),
        ([_line("1920", debit="100.00"), _line("2700", credit="100.00")], "mva_konto"),
        ([_line("1920", debit="100.00"), _line("2999", credit="100.00")], "ukjent_konto"),
        (
            [_line("1920", debit="100.00", credit="1.00"), _line("2050", credit="99.00")],
            "ugyldig_belop",
        ),
        ([_line("1920", debit="100.001"), _line("2050", credit="100.001")], "ugyldig_belop"),
        ([], "ingen_linjer"),
    ],
)
def test_the_review_refuses_what_is_not_an_opening_balance(
    client: TestClient, lines: list[dict[str, Any]], code: str
) -> None:
    company = _books(client)
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/inngaende-balanse/kontroll",
        json={"lines": lines},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["postable"] is False
    assert code in {f["code"] for f in body["findings"] if f["severity"] == "feil"}
    # And the post refuses the same thing, with the list.
    posted = _post_opening(client, company["id"], lines)
    assert posted.status_code == 422
    assert posted.json()["code"] == "invalid_opening_balance"
    assert code in {f["code"] for f in posted.json()["findings"]}


def test_a_reskontro_is_all_or_nothing_and_on_the_partys_own_account(client: TestClient) -> None:
    company = _books(client)
    customer = _party(client, company["id"], "kunde", "Kunde AS")
    supplier = _party(client, company["id"], "leverandor", "Leverandør AS")
    half = [
        _line("1500", debit="600.00", party_id=customer),
        _line("1500", debit="400.00"),
        _line("2050", credit="1000.00"),
    ]
    wrong = [_line("1500", debit="1000.00", party_id=supplier), _line("2050", credit="1000.00")]
    for lines, code in ((half, "reskontro_ufullstendig"), (wrong, "part_paa_feil_konto")):
        body = write(
            client,
            "POST",
            f"/api/v1/companies/{company['id']}/inngaende-balanse/kontroll",
            json={"lines": lines},
        ).json()
        assert code in {f["code"] for f in body["findings"]}


def test_a_saldobalanse_upload_becomes_reviewed_lines_and_nothing_is_posted(
    client: TestClient,
) -> None:
    company = _books(client)
    csv = (
        "Konto;Navn;Utgående saldo\n"
        "1920;Bank;50 000,00\n"
        "2000;Aksjekapital;-30 000,00\n"
        "2050;Annen egenkapital;-20 000,00\n"
        "3000;Salg;-1 000,00\n"
    ).encode()
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/inngaende-balanse/saldobalanse",
        files={"file": ("saldobalanse.csv", csv, "text/csv")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [line["account_number"] for line in body["lines"]] == ["1920", "2000", "2050", "3000"]
    # A pre-closing saldobalanse: the result line is refused with the reason.
    assert "resultatkonto" in {f["code"] for f in body["findings"]}
    assert body["postable"] is False
    state = client.get(f"/api/v1/companies/{company['id']}/inngaende-balanse").json()
    assert state["standing"] is None and state["history"] == []


def test_an_unreadable_saldobalanse_says_why(client: TestClient) -> None:
    company = _books(client)
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/inngaende-balanse/saldobalanse",
        files={"file": ("x.csv", b"Navn;Saldo\nBank;1\n", "text/csv")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_saldobalanse"


# --- Inngående balanse: posting and correcting ------------------------------------------


def test_the_opening_balance_is_one_balanced_voucher_on_the_first_day(client: TestClient) -> None:
    company = _books(client)
    customer = _party(client, company["id"], "kunde", "Gammel Kunde AS")
    response = _post_opening(client, company["id"], _as_opening(customer))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["transaction_date"] == f"{LAST_YEAR}-01-01"
    assert Decimal(body["total"]) == Decimal("60000.00")

    state = client.get(f"/api/v1/companies/{company['id']}/inngaende-balanse").json()
    assert state["allowed"] is True
    standing = state["standing"]
    assert standing["id"] == body["id"]
    debit = sum(Decimal(line["debit"]) for line in standing["lines"])
    credit = sum(Decimal(line["credit"]) for line in standing["lines"])
    assert debit == credit == Decimal("60000.00")


def test_the_opening_bank_balance_is_a_position_not_a_receipt(client: TestClient) -> None:
    """The overview counts it in the bank and not in the year's money in."""
    company = _books(client)
    customer = _party(client, company["id"], "kunde", "K AS")
    _post_opening(client, company["id"], _as_opening(customer))
    summary = client.get(
        f"/api/v1/companies/{company['id']}/cashflow/summary?financial_year={LAST_YEAR}"
    ).json()
    assert Decimal(summary["total_in"]) == 0
    assert Decimal(summary["cash_position"]) == Decimal("50000.00")
    listed = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    assert listed == []


def test_a_second_opening_balance_is_refused_and_a_correction_is_reversal_plus_replacement(
    client: TestClient,
) -> None:
    company = _books(client)
    first = _post_opening(
        client, company["id"], [_line("1920", debit="1000.00"), _line("2050", credit="1000.00")]
    )
    assert first.status_code == 201
    again = _post_opening(
        client, company["id"], [_line("1920", debit="2000.00"), _line("2050", credit="2000.00")]
    )
    assert again.status_code == 409
    assert again.json()["code"] == "opening_balance_exists"

    corrected = _post_opening(
        client,
        company["id"],
        [_line("1920", debit="2000.00"), _line("2050", credit="2000.00")],
        korriger=True,
    )
    assert corrected.status_code == 201, corrected.text
    state = client.get(f"/api/v1/companies/{company['id']}/inngaende-balanse").json()
    assert len(state["history"]) == 3
    original, reversal, replacement = state["history"]
    assert original["reversed"] is True
    assert reversal["is_reversal"] is True
    assert replacement["corrects_id"] == original["id"]
    assert state["standing"]["id"] == replacement["id"]
    summary = client.get(
        f"/api/v1/companies/{company['id']}/cashflow/summary?financial_year={LAST_YEAR}"
    ).json()
    assert Decimal(summary["cash_position"]) == Decimal("2000.00")


def test_the_opening_voucher_is_not_reversed_from_the_register(client: TestClient) -> None:
    company = _books(client)
    posted = _post_opening(
        client, company["id"], [_line("1920", debit="1000.00"), _line("2050", credit="1000.00")]
    ).json()
    response = write(
        client, "POST", f"/api/v1/companies/{company['id']}/cashflow/{posted['id']}/reversal"
    )
    assert response.status_code == 409
    assert response.json()["code"] == "cannot_reverse_here"


def test_withdrawing_the_opening_balance_reverses_it(client: TestClient) -> None:
    company = _books(client)
    nothing = write(
        client, "POST", f"/api/v1/companies/{company['id']}/inngaende-balanse/reversering"
    )
    assert nothing.status_code == 409
    _post_opening(
        client, company["id"], [_line("1920", debit="10.00"), _line("2050", credit="10.00")]
    )
    undone = write(
        client, "POST", f"/api/v1/companies/{company['id']}/inngaende-balanse/reversering"
    )
    assert undone.status_code == 201, undone.text
    state = client.get(f"/api/v1/companies/{company['id']}/inngaende-balanse").json()
    assert state["standing"] is None


def test_a_viewer_may_read_but_not_post_the_opening_balance(client: TestClient) -> None:
    company = _books(client)
    join_company(client, company["id"], email="revisor@example.no", role="viewer")
    assert client.get(f"/api/v1/companies/{company['id']}/inngaende-balanse").status_code == 200
    response = _post_opening(
        client, company["id"], [_line("1920", debit="1.00"), _line("2050", credit="1.00")]
    )
    assert response.status_code == 403


# --- Inngående balanse in SAF-T --------------------------------------------------------


def test_saft_opens_the_year_with_the_opening_voucher_and_leaves_it_out_of_the_entries(
    client: TestClient, tmp_path: Path
) -> None:
    company = _books(client)
    customer = _party(client, company["id"], "kunde", "Gammel Kunde AS")
    _post_opening(client, company["id"], _as_opening(customer))
    # The customer pays the open invoice from the old system.
    paid = record(
        client,
        company["id"],
        type_code="salg_hoy_sats",
        gross_amount="1250.00",
        occurred_on=_last_year(),
    )
    assert paid.status_code == 201, paid.text

    root = _saft(client, company["id"], LAST_YEAR, tmp_path)
    accounts = _accounts(root)
    assert _side(accounts["1920"], "Opening") == Decimal("50000.00")
    assert _side(accounts["1920"], "Closing") == Decimal("51250.00")
    assert _side(accounts["2000"], "Opening") == Decimal("-30000.00")
    assert sum(_side(a, "Opening") for a in accounts.values()) == 0
    journals = {j.findtext("s:JournalID", namespaces=NS) for j in root.findall(".//s:Journal", NS)}
    assert "IB" not in journals
    assert root.findtext(".//s:GeneralLedgerEntries/s:NumberOfEntries", namespaces=NS) == "1"
    customers = root.findall(".//s:Customers/s:Customer", NS)
    assert [c.findtext("s:Name", namespaces=NS) for c in customers] == ["Gammel Kunde AS"]
    # The open item from the old system is the customer's opening balance.
    structure = customers[0].find("s:BalanceAccount", NS)
    assert structure is not None
    assert _side(structure, "Opening") == Decimal("10000.00")

    findings = client.get(
        f"/api/v1/companies/{company['id']}/saft/kontroll?aar={LAST_YEAR}"
    ).json()["findings"]
    assert "inngaende_balanse_fra_bilag" in {f["code"] for f in findings}


# --- Årsavslutning ----------------------------------------------------------------------


def _a_profitable_year(client: TestClient, company_id: int) -> None:
    """Sales of 10 000 net, costs of 1 000 net: a result of 9 000 before tax."""
    entries: tuple[dict[str, Any], ...] = (
        {"type_code": "salg_hoy_sats", "gross_amount": "12500.00"},
        {"type_code": "kontorrekvisita", "gross_amount": "1250.00"},
    )
    for kwargs in entries:
        response = record(client, company_id, occurred_on=_last_year(), **kwargs)
        assert response.status_code == 201, response.text


def test_the_plan_for_an_as_shows_the_result_tax_and_disposition(client: TestClient) -> None:
    company = _books(client)
    _post_opening(
        client, company["id"], [_line("1920", debit="30000.00"), _line("2000", credit="30000.00")]
    )
    _a_profitable_year(client, company["id"])
    plan = client.get(
        f"/api/v1/companies/{company['id']}/arsavslutning?aar={LAST_YEAR}&skattekostnad=1980.00"
    ).json()
    assert plan["closable"] is True, plan["findings"]
    assert Decimal(plan["result_before_tax"]) == Decimal("9000.00")
    assert Decimal(plan["result"]) == Decimal("7020.00")
    assert [(li["account_number"], li["debit"], li["credit"]) for li in plan["tax_lines"]] == [
        ("8300", "1980.00", "0.00"),
        ("2500", "0.00", "1980.00"),
    ]
    assert [
        (li["account_number"], li["debit"], li["credit"]) for li in plan["disposition_lines"]
    ] == [
        ("8960", "7020.00", "0.00"),
        ("2050", "0.00", "7020.00"),
    ]
    # Nothing was posted by asking.
    years = client.get(f"/api/v1/companies/{company['id']}/regnskapsaar").json()
    assert years[0]["closed"] is False


def test_closing_an_as_disposes_the_result_locks_the_year_and_opens_the_next(
    client: TestClient, tmp_path: Path
) -> None:
    company = _books(client)
    _post_opening(
        client, company["id"], [_line("1920", debit="30000.00"), _line("2000", credit="30000.00")]
    )
    _a_profitable_year(client, company["id"])
    closed = _close(client, company["id"], skattekostnad="1980.00")
    assert closed.status_code == 201, closed.text
    assert len(closed.json()["vouchers"]) == 2

    # Locked: nothing more goes into the year, from any route.
    late = record(
        client,
        company["id"],
        type_code="kontorrekvisita",
        gross_amount="10.00",
        occurred_on=_last_year(),
    )
    assert late.status_code == 409
    assert late.json()["code"] == "closed_financial_year"
    assert _close(client, company["id"]).status_code == 422

    years = client.get(f"/api/v1/companies/{company['id']}/regnskapsaar").json()
    assert [(y["year"], y["closed"]) for y in years] == [(LAST_YEAR, True), (THIS_YEAR, False)]
    assert years[0]["events"][0]["action"] == "avsluttet"
    assert Decimal(years[0]["events"][0]["result"]) == Decimal("7020.00")

    # The closed year's file: every income and cost account ends at zero, and
    # the result sits on 2050.
    closed_year = _accounts(_saft(client, company["id"], LAST_YEAR, tmp_path))
    pnl = [a for number, a in closed_year.items() if number[0] in "345678"]
    assert sum(_side(a, "Closing") for a in pnl) == 0
    assert _side(closed_year["2050"], "Closing") == Decimal("-7020.00")
    assert _side(closed_year["2500"], "Closing") == Decimal("-1980.00")

    # And the next year opens with it, with nothing undisposed to explain.
    findings = client.get(
        f"/api/v1/companies/{company['id']}/saft/kontroll?aar={THIS_YEAR}"
    ).json()["findings"]
    assert "udisponert_resultat" not in {f["code"] for f in findings}
    following = _accounts(_saft(client, company["id"], THIS_YEAR, tmp_path))
    assert _side(following["2050"], "Opening") == Decimal("-7020.00")
    assert _side(following["3000"], "Opening") == 0


def test_an_as_loss_beyond_free_equity_becomes_udekket_tap_and_profit_covers_it_first(
    client: TestClient,
) -> None:
    company = _books(client)
    _post_opening(
        client,
        company["id"],
        [
            _line("1920", debit="31000.00"),
            _line("2000", credit="30000.00"),
            _line("2050", credit="1000.00"),
        ],
    )
    loss = record(
        client,
        company["id"],
        type_code="kontorrekvisita",
        gross_amount="5000.00",
        tax_code="0",
        occurred_on=_last_year(),
    )
    assert loss.status_code == 201, loss.text
    closed = _close(client, company["id"])
    assert closed.status_code == 201, closed.text
    lines = [
        (li["account_number"], li["debit"], li["credit"])
        for li in closed.json()["disposition_lines"]
    ]
    assert lines == [
        ("2050", "1000.00", "0.00"),
        ("8960", "0.00", "1000.00"),
        ("2080", "4000.00", "0.00"),
        ("8990", "0.00", "4000.00"),
    ]

    # This year's profit covers the udekket tap before anything reaches 2050.
    sale = record(
        client, company["id"], type_code="salg_hoy_sats", gross_amount="3000.00", tax_code="0"
    )
    assert sale.status_code == 201, sale.text
    plan = client.get(f"/api/v1/companies/{company['id']}/arsavslutning?aar={THIS_YEAR}").json()
    assert plan["closable"] is False  # the year is still running
    assert "aaret_er_ikke_over" in {f["code"] for f in plan["findings"]}
    assert [
        (li["account_number"], li["debit"], li["credit"]) for li in plan["disposition_lines"]
    ] == [
        ("8990", "3000.00", "0.00"),
        ("2080", "0.00", "3000.00"),
    ]


def test_an_enk_takes_the_result_and_the_drawings_onto_equity_and_pays_no_tax(
    client: TestClient,
) -> None:
    company = _books(client, form="ENK")
    entries: tuple[dict[str, Any], ...] = (
        {"type_code": "salg_hoy_sats", "gross_amount": "5000.00", "tax_code": "0"},
        {"type_code": "privatuttak", "gross_amount": "2000.00"},
    )
    for kwargs in entries:
        response = record(client, company["id"], occurred_on=_last_year(), **kwargs)
        assert response.status_code == 201, response.text

    taxed = client.get(
        f"/api/v1/companies/{company['id']}/arsavslutning?aar={LAST_YEAR}&skattekostnad=100.00"
    ).json()
    assert "skatt_for_enk" in {f["code"] for f in taxed["findings"]}
    assert taxed["closable"] is False

    closed = _close(client, company["id"])
    assert closed.status_code == 201, closed.text
    body = closed.json()
    assert body["tax_lines"] == []
    assert [
        (li["account_number"], li["debit"], li["credit"]) for li in body["disposition_lines"]
    ] == [
        ("8800", "5000.00", "0.00"),
        ("2050", "0.00", "5000.00"),
        ("2050", "2000.00", "0.00"),
        ("2060", "0.00", "2000.00"),
    ]


@pytest.mark.parametrize(
    ("form", "code"),
    [("ANS", "organisasjonsform_ikke_stottet"), (None, "ukjent_organisasjonsform")],
)
def test_other_organisation_forms_are_refused_by_name(
    client: TestClient, form: str | None, code: str
) -> None:
    company = _books(client, form=form)
    _a_profitable_year(client, company["id"])
    response = _close(client, company["id"])
    assert response.status_code == 422
    assert response.json()["code"] == "year_not_closable"
    assert code in {f["code"] for f in response.json()["findings"]}


def test_a_running_year_and_one_after_an_open_year_are_not_closed(client: TestClient) -> None:
    company = _books(client)
    record(client, company["id"], type_code="kontorrekvisita", gross_amount="10.00")
    response = _close(client, company["id"], year=THIS_YEAR)
    assert response.status_code == 422
    codes = {f["code"] for f in response.json()["findings"]}
    assert {"aaret_er_ikke_over", "tidligere_aar_aapent"} <= codes


def test_closing_needs_the_confirmation_and_the_close_permission(client: TestClient) -> None:
    company = _books(client)
    unconfirmed = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/arsavslutning",
        json={"aar": LAST_YEAR},
    )
    assert unconfirmed.status_code == 422
    join_company(client, company["id"], email="kollega@example.no", role="member")
    assert _close(client, company["id"]).status_code == 403


def test_the_first_year_without_an_opening_balance_is_noted(client: TestClient) -> None:
    company = _books(client)
    plan = client.get(f"/api/v1/companies/{company['id']}/arsavslutning?aar={LAST_YEAR}").json()
    assert "ingen_inngaende_balanse" in {f["code"] for f in plan["findings"]}


# --- Reopening ------------------------------------------------------------------------


def _reopen(client: TestClient, company_id: int, **kw: Any) -> Any:
    body = {
        "aar": LAST_YEAR,
        "begrunnelse": "Manglende leverandørfaktura fra desember",
        "bekreftet": True,
    }
    body.update(kw)
    return write(
        client, "POST", f"/api/v1/companies/{company_id}/arsavslutning/gjenapning", json=body
    )


def test_reopening_needs_a_reason_and_reverses_rather_than_erases(client: TestClient) -> None:
    company = _books(client)
    _a_profitable_year(client, company["id"])
    assert _close(client, company["id"]).status_code == 201

    assert _reopen(client, company["id"], begrunnelse="").status_code == 422
    assert _reopen(client, company["id"], bekreftet=False).status_code == 422

    reopened = _reopen(client, company["id"])
    assert reopened.status_code == 200, reopened.text
    assert len(reopened.json()["reversals"]) == 1

    years = client.get(f"/api/v1/companies/{company['id']}/regnskapsaar").json()
    first = years[0]
    assert first["closed"] is False
    assert [e["action"] for e in first["events"]] == ["avsluttet", "gjenapnet"]
    assert first["events"][1]["reason"] == "Manglende leverandørfaktura fra desember"

    # The year takes postings again, and closes again on the new figures.
    late = record(
        client,
        company["id"],
        type_code="kontorrekvisita",
        gross_amount="1250.00",
        occurred_on=_last_year(31, 12),
    )
    assert late.status_code == 201, late.text
    again = _close(client, company["id"])
    assert again.status_code == 201, again.text
    assert Decimal(again.json()["result"]) == Decimal("8000.00")
    assert _reopen(client, company["id"], aar=THIS_YEAR).status_code == 409


def test_the_closing_voucher_is_not_reversed_from_the_register(client: TestClient) -> None:
    company = _books(client)
    _a_profitable_year(client, company["id"])
    voucher = _close(client, company["id"]).json()["vouchers"][0]
    _reopen(client, company["id"])
    response = write(
        client, "POST", f"/api/v1/companies/{company['id']}/cashflow/{voucher['id']}/reversal"
    )
    assert response.status_code == 409


# --- The company wall --------------------------------------------------------------------


def test_another_companys_party_and_books_stay_on_their_side(client: TestClient) -> None:
    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    mine = _company(client, organization_id)
    theirs = _company(client, organization_id, orgnr=OTHER_ORGNR, name="Naboen AS")
    their_customer = _party(client, theirs["id"], "kunde", "Hemmelig Kunde AS")

    smuggled = write(
        client,
        "POST",
        f"/api/v1/companies/{mine['id']}/inngaende-balanse/kontroll",
        json={
            "lines": [
                _line("1500", debit="100.00", party_id=their_customer),
                _line("2050", credit="100.00"),
            ]
        },
    ).json()
    assert "ukjent_part" in {f["code"] for f in smuggled["findings"]}
    assert "Hemmelig" not in str(smuggled)

    _post_opening(
        client, theirs["id"], [_line("1920", debit="777.00"), _line("2050", credit="777.00")]
    )
    _a_profitable_year(client, theirs["id"])
    assert _close(client, theirs["id"]).status_code == 201

    state = client.get(f"/api/v1/companies/{mine['id']}/inngaende-balanse").json()
    assert state["standing"] is None and state["history"] == []
    years = client.get(f"/api/v1/companies/{mine['id']}/regnskapsaar").json()
    assert all(not y["closed"] and y["events"] == [] for y in years)
    plan = client.get(f"/api/v1/companies/{mine['id']}/arsavslutning?aar={LAST_YEAR}").json()
    assert Decimal(plan["result"]) == 0
