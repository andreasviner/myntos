"""The Analyse figures: income and costs read off the books, and the charts made of them.

docs/analyse.md, «The figures: what counts», is the rule under test. The one
that matters most is the invariant: for a whole year, «Inntekter» less
«Kostnader» is the result before tax on ``/regnskap/``, to the øre, whatever
the books hold. Two figures for the same thing that disagree by a few kroner
is how a person stops trusting both, so it is asserted over books with every
kind of voucher Myntos posts: an invoice and its payment, a composite
voucher, a reversal and a correction, a previous program's year with its own
closing, and a year Myntos closed.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from myntos.domain.analyse import datasets, sentences
from myntos.domain.analyse.charts import Chart
from myntos.domain.analyse.figures import Books, Part, Period, period, year_earlier
from tests.conftest import agent_header, join_company, register, write
from tests.test_arsoppgjor_api import (
    LAST_YEAR,
    _a_profitable_year,
    _books,
    _close,
    _company,
    _line,
    _party,
    _post_opening,
)
from tests.test_cashflow_api import THIS_YEAR, record
from tests.test_composite_entries import composite
from tests.test_faktura_payments import _issued, _pay
from tests.test_saft_innlesing import EARLIER, _post, _upload, earlier_file

NBSP = "\N{NO-BREAK SPACE}"


def _analyse(client: TestClient, company_id: int, **params: Any) -> dict[str, Any]:
    response = client.get(f"/api/v1/companies/{company_id}/analyse", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _whole_year(client: TestClient, company_id: int, year: int) -> dict[str, Any]:
    return _analyse(
        client, company_id, fra=f"{year}-01-01", til=f"{year}-12-31", sammenlign="false"
    )


def _chart(client: TestClient, company_id: int, name: str, **params: Any) -> dict[str, Any]:
    response = client.get(f"/api/v1/companies/{company_id}/analyse/datasett/{name}", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _regnskap(client: TestClient, company_id: int) -> dict[int, dict[str, Decimal]]:
    response = client.get(f"/api/v1/companies/{company_id}/regnskap")
    assert response.status_code == 200, response.text
    return {
        year["year"]: {line["key"]: Decimal(line["amount"]) for line in year["resultat"]}
        for year in response.json()["years"]
    }


def _assert_the_invariant(client: TestClient, company_id: int) -> dict[int, dict[str, Any]]:
    """Every year on /regnskap/ against the whole year here, to the øre."""
    found: dict[int, dict[str, Any]] = {}
    years = _regnskap(client, company_id)
    assert years, "no years to compare"
    for year, result in years.items():
        figures = _whole_year(client, company_id, year)["nokkeltall"]
        inntekter, kostnader = Decimal(figures["inntekter"]), Decimal(figures["kostnader"])
        assert Decimal(figures["resultat"]) == inntekter - kostnader
        assert Decimal(figures["resultat"]) == result["resultat_for_skatt"], (year, figures)
        assert inntekter == result["driftsinntekter"] + result.get("finansinntekter", 0)
        assert kostnader == result["driftskostnader"] + result.get("finanskostnader", 0)
        found[year] = figures
    return found


# --- The invariant ------------------------------------------------------------------


def _invoicing_books(client: TestClient) -> int:
    """An AS from last year that can also issue an invoice: an address, a kontonummer."""
    session = register(client)
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": session["memberships"][0]["organization_id"],
            "orgnr": "910000004",
            "name": "Overgang AS",
            "first_financial_year": LAST_YEAR,
            "organisation_form": "AS",
            "vat_registered": True,
            "in_foretaksregisteret": True,
            "address_line": "Kirkegata 2",
            "postal_code": "0153",
            "city": "Oslo",
            "bank_account": "15062033158",
        },
    )
    assert response.status_code == 201, response.text
    company_id: int = response.json()["id"]
    return company_id


def _customer(client: TestClient, company_id: int) -> int:
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/parties",
        json={
            "party_type": "kunde",
            "name": "Kunde AS",
            "orgnr": "910000012",
            "address_line": "Storgata 1",
            "postal_code": "0155",
            "city": "Oslo",
        },
    )
    assert response.status_code == 201, response.text
    party_id: int = response.json()["id"]
    return party_id


def test_a_whole_year_is_the_result_before_tax_on_regnskap(client: TestClient) -> None:
    cid = _invoicing_books(client)
    # The opening balance (IB) on the year's first day: balance accounts only.
    opened = _post_opening(
        client, cid, [_line("1920", debit="30000.00"), _line("2000", credit="30000.00")]
    )
    assert opened.status_code == 201, opened.text
    # Last year: 10 000 sold and 1 000 spent, net of VAT.
    _a_profitable_year(client, cid)
    # A composite voucher, a sale less a bank fee, split by its lines.
    mixed = composite(
        client,
        cid,
        [
            {"type_code": "salg_hoy_sats", "gross_amount": "1250.00"},
            {"type_code": "bankgebyr", "gross_amount": "25.00"},
        ],
        occurred_on=date(LAST_YEAR, 5, 10),
    )
    assert mixed.status_code == 201, mixed.text
    # A purchase corrected: a reversal and a replacement, linked.
    bought = record(
        client, cid, type_code="varekjop", gross_amount="500.00", occurred_on=f"{LAST_YEAR}-06-01"
    )
    assert bought.status_code == 201, bought.text
    corrected = write(
        client,
        "PUT",
        f"/api/v1/companies/{cid}/cashflow/{bought.json()['id']}",
        json={
            "type_code": "varekjop",
            "occurred_on": f"{LAST_YEAR}-06-01",
            "gross_amount": "800.00",
            "description": "Rettet",
            "tax_code": None,
            "payment_method": "bank",
            "party_id": None,
        },
    )
    assert corrected.status_code == 201, corrected.text
    # And another reversed outright.
    gone = record(
        client,
        cid,
        type_code="kontorrekvisita",
        gross_amount="300.00",
        occurred_on=f"{LAST_YEAR}-07-01",
    )
    reversed_ = write(
        client, "POST", f"/api/v1/companies/{cid}/cashflow/{gone.json()['id']}/reversal"
    )
    assert reversed_.status_code == 201, reversed_.text
    # Closed, with a tax cost: the AVS journal posts 8300 and the disposition.
    closed = _close(client, cid, skattekostnad="1980.00")
    assert closed.status_code == 201, closed.text
    # This year: an invoice to a registered customer, and its payment.
    invoice = _issued(client, cid, party_id=_customer(client, cid))
    paid = _pay(client, cid, invoice["id"], invoice["gross_total"])
    assert paid.status_code == 201, paid.text

    found = _assert_the_invariant(client, cid)

    # The tax cost and the disposition are not costs here, as on /regnskap/.
    last = found[LAST_YEAR]
    assert Decimal(last["inntekter"]) == Decimal("11000.00")
    split = _chart(
        client,
        cid,
        "kostnader_per_konto",
        fra=f"{LAST_YEAR}-01-01",
        til=f"{LAST_YEAR}-12-31",
        topp=9,
        visning="stolper",
    )
    assert not {"8300", "8960", "8990", "8800"} & {s["key"] for s in split["slices"]}
    # The invoice counts once, net of VAT, on its issue date; its payment not at all.
    this = found[THIS_YEAR]
    assert Decimal(this["inntekter"]) == Decimal(invoice["net_total"])


def test_a_previous_programs_closing_is_not_a_cost(client: TestClient) -> None:
    """A year brought by SAF-T carries its own 8300 and 8960 in ``IMPORT``, not ``AVS``."""
    company = _books(client)
    uploaded = _upload(client, company["id"], (f"{EARLIER}.xml", earlier_file()))
    assert _post(client, company["id"], uploaded["id"]).status_code == 202

    found = _assert_the_invariant(client, company["id"])

    # Milk sold for 1 000 net; the file's closing (100 tax, 900 to equity) is out.
    assert Decimal(found[EARLIER]["inntekter"]) == Decimal("1000.00")
    assert Decimal(found[EARLIER]["kostnader"]) == Decimal("0.00")


# --- What counts ------------------------------------------------------------------


def test_an_opening_balance_alone_is_no_books(client: TestClient) -> None:
    """The IB voucher is a position, not a month of business: nothing counts, no month has books."""
    company = _books(client)
    opened = _post_opening(
        client, company["id"], [_line("1920", debit="30000.00"), _line("2000", credit="30000.00")]
    )
    assert opened.status_code == 201, opened.text

    body = _whole_year(client, company["id"], LAST_YEAR)

    assert body["bokfort_til"] is None
    assert body["nokkeltall"]["inntekter"] == "0.00"
    assert body["tekst"] == []
    months = body["diagrammer"][0]["series"][0]["values"]
    assert months == [None] * 12
    # Nothing to split: the pies have no slices rather than a ring of nothing.
    assert body["diagrammer"][2]["slices"] == []
    assert body["diagrammer"][3]["slices"] == []


def test_a_reversal_nets_its_original_to_nothing(client: TestClient) -> None:
    company = _books(client)
    cid = company["id"]
    bought = record(client, cid, type_code="kontorrekvisita", gross_amount="1250.00")
    assert bought.status_code == 201, bought.text
    before = _whole_year(client, cid, THIS_YEAR)["nokkeltall"]
    assert before["kostnader"] == "1000.00"

    reversed_ = write(
        client, "POST", f"/api/v1/companies/{cid}/cashflow/{bought.json()['id']}/reversal"
    )
    assert reversed_.status_code == 201, reversed_.text

    after = _whole_year(client, cid, THIS_YEAR)
    assert after["nokkeltall"]["kostnader"] == "0.00"
    # The account came to zero, so it is no slice at all.
    assert after["diagrammer"][3]["slices"] == []


def test_a_composite_voucher_is_split_by_its_lines(client: TestClient) -> None:
    company = _books(client)
    cid = company["id"]
    mixed = composite(
        client,
        cid,
        [
            {"type_code": "salg_hoy_sats", "gross_amount": "1250.00"},
            {"type_code": "bankgebyr", "gross_amount": "25.00"},
            {"type_code": "kontorrekvisita", "gross_amount": "125.00"},
        ],
    )
    assert mixed.status_code == 201, mixed.text
    year = {"fra": f"{THIS_YEAR}-01-01", "til": f"{THIS_YEAR}-12-31"}

    categories = _chart(client, cid, "kostnader_per_kategori", **year)
    by_key = {s["key"]: Decimal(s["value"]) for s in categories["slices"]}
    assert by_key == {"kontorrekvisita": Decimal("100.00"), "bankgebyr": Decimal("25.00")}

    income = _chart(client, cid, "inntekter_per_konto", **year)
    assert [(s["key"], s["value"]) for s in income["slices"]] == [("3000", "1000.00")]


def test_a_refund_counts_against_the_category_it_refunds(client: TestClient) -> None:
    company = _books(client)
    cid = company["id"]
    assert (
        record(client, cid, type_code="kontorrekvisita", gross_amount="1250.00").status_code == 201
    )
    refund = record(client, cid, type_code="refusjon_kontorrekvisita", gross_amount="250.00")
    assert refund.status_code == 201, refund.text

    categories = _chart(
        client, cid, "kostnader_per_kategori", fra=f"{THIS_YEAR}-01-01", til=f"{THIS_YEAR}-12-31"
    )

    assert [(s["key"], s["value"]) for s in categories["slices"]] == [("kontorrekvisita", "800.00")]


def test_income_per_customer_names_the_party_and_the_rest_is_without_one(
    client: TestClient,
) -> None:
    company = _books(client)
    cid = company["id"]
    customer = _party(client, cid, "kunde", "Kunde AS")
    sold = record(client, cid, type_code="salg_hoy_sats", gross_amount="2500.00", party_id=customer)
    assert sold.status_code == 201, sold.text
    assert record(client, cid, type_code="salg_hoy_sats", gross_amount="1250.00").status_code == 201

    chart = _chart(
        client, cid, "inntekter_per_kunde", fra=f"{THIS_YEAR}-01-01", til=f"{THIS_YEAR}-12-31"
    )

    assert chart["view"] == "stolper"
    assert [(s["key"], s["label"], s["value"]) for s in chart["slices"]] == [
        (str(customer), "Kunde AS", "2000.00"),
        ("uten_motpart", "Uten motpart", "1000.00"),
    ]


# --- Months ----------------------------------------------------------------------


def test_months_outside_the_books_are_null_and_a_quiet_month_is_zero(client: TestClient) -> None:
    company = _books(client)
    cid = company["id"]
    for on in (f"{THIS_YEAR}-03-14", f"{THIS_YEAR}-05-20"):
        entry = record(
            client, cid, type_code="salg_hoy_sats", gross_amount="1250.00", occurred_on=on
        )
        assert entry.status_code == 201, entry.text

    body = _whole_year(client, cid, THIS_YEAR)

    assert body["bokfort_til"] == f"{THIS_YEAR}-05-20"
    income = body["diagrammer"][0]
    assert income["x"]["labels"][0] == f"{THIS_YEAR}-01"
    assert income["series"][0]["values"] == [
        None,
        None,
        "1000.00",
        "0.00",
        "1000.00",
        *([None] * 7),
    ]
    # March and May tie, so no month is «the best»; there is no cost to name,
    # and no year before to compare with: one sentence, without a percentage.
    assert body["tekst"] == [f"Inntektene i {THIS_YEAR} er 2{NBSP}000{NBSP}kr."]


def test_the_comparison_is_the_same_dates_a_year_earlier_when_the_books_cover_them(
    client: TestClient,
) -> None:
    company = _books(client)
    cid = company["id"]
    for on, amount in ((f"{LAST_YEAR}-01-05", "1250.00"), (f"{LAST_YEAR}-03-14", "2500.00")):
        entry = record(client, cid, type_code="salg_hoy_sats", gross_amount=amount, occurred_on=on)
        assert entry.status_code == 201, entry.text
    entry = record(
        client,
        cid,
        type_code="salg_hoy_sats",
        gross_amount="5000.00",
        occurred_on=f"{THIS_YEAR}-03-14",
    )
    assert entry.status_code == 201, entry.text

    body = _analyse(client, cid, fra=f"{THIS_YEAR}-01-01", til=f"{THIS_YEAR}-03-31")

    assert body["sammenligning"] == {"fra": f"{LAST_YEAR}-01-01", "til": f"{LAST_YEAR}-03-31"}
    assert body["nokkeltall"]["forrige"] == {
        "inntekter": "3000.00",
        "kostnader": "0.00",
        "resultat": "3000.00",
    }
    series = body["diagrammer"][0]["series"]
    assert [(s["role"], s["label"]) for s in series] == [
        ("hoved", str(THIS_YEAR)),
        ("sammenligning", str(LAST_YEAR)),
    ]
    assert series[1]["values"] == ["1000.00", "0.00", "2000.00"]

    # Asked not to compare, there is nothing to compare.
    alone = _analyse(
        client, cid, fra=f"{THIS_YEAR}-01-01", til=f"{THIS_YEAR}-03-31", sammenlign="false"
    )
    assert alone["sammenligning"] is None
    assert alone["nokkeltall"]["forrige"] is None
    assert len(alone["diagrammer"][0]["series"]) == 1


def test_no_comparison_with_a_year_the_books_only_partly_cover(client: TestClient) -> None:
    """Books from March last year: January to March this year has no fair comparison."""
    company = _books(client)
    cid = company["id"]
    for on in (f"{LAST_YEAR}-03-14", f"{THIS_YEAR}-03-14"):
        entry = record(
            client, cid, type_code="salg_hoy_sats", gross_amount="1250.00", occurred_on=on
        )
        assert entry.status_code == 201, entry.text

    body = _analyse(client, cid, fra=f"{THIS_YEAR}-01-01", til=f"{THIS_YEAR}-03-31")

    assert body["sammenligning"] is None
    assert body["nokkeltall"]["forrige"] is None
    assert not any("fjor" in sentence for sentence in body["tekst"])


def test_29_february_compares_with_28_february() -> None:
    assert year_earlier(date(2028, 2, 29)) == date(2027, 2, 28)
    assert year_earlier(date(2026, 11, 26)) == date(2025, 11, 26)
    leap = Period(date(2028, 2, 1), date(2028, 2, 29)).year_before()
    assert (leap.fra, leap.til) == (date(2027, 2, 1), date(2027, 2, 28))
    assert len(leap.months()) == 1


def test_a_period_the_wrong_way_round_or_too_long_is_refused(client: TestClient) -> None:
    company = _books(client)
    url = f"/api/v1/companies/{company['id']}/analyse"
    backwards = client.get(url, params={"fra": f"{THIS_YEAR}-05-01", "til": f"{THIS_YEAR}-04-01"})
    assert backwards.status_code == 422
    assert backwards.json()["code"] == "invalid_analysis_period"
    too_long = client.get(url, params={"fra": "2000-01-01", "til": f"{THIS_YEAR}-01-01"})
    assert too_long.status_code == 422
    assert period(date(2021, 1, 1), date(2025, 12, 31)).months()[-1] == (2025, 12)


def test_books_tell_a_quiet_month_from_none() -> None:
    books = Books(first=date(2026, 3, 14), last=date(2026, 5, 2))
    assert [books.has((2026, m)) for m in (2, 3, 4, 5, 6)] == [False, True, True, True, False]
    assert books.cover(Period(date(2026, 3, 1), date(2026, 5, 31)))
    assert not books.cover(Period(date(2026, 2, 1), date(2026, 5, 31)))
    assert not Books(None, None).has((2026, 3))


# --- The charts --------------------------------------------------------------------


def test_every_dataset_builds_a_valid_chart_in_every_view(client: TestClient) -> None:
    company = _books(client)
    cid = company["id"]
    for type_code, gross in (
        ("salg_hoy_sats", "12500.00"),
        ("kontorrekvisita", "1250.00"),
        ("bankgebyr", "25.00"),
        ("varekjop", "5000.00"),
    ):
        assert record(client, cid, type_code=type_code, gross_amount=gross).status_code == 201

    listed = client.get(f"/api/v1/companies/{cid}/analyse/datasett")
    assert listed.status_code == 200, listed.text
    names = [d["name"] for d in listed.json()["datasets"]]
    assert names == [d.name for d in datasets.DATASETS]
    # WP5b's group by KI's tags; the company has KI on, as a new one does.
    assert [d["needs_tags"] for d in listed.json()["datasets"]] == [
        d.needs_tags for d in datasets.DATASETS
    ]
    assert {d.name for d in datasets.DATASETS if d.needs_tags} == {
        "inntekter_per_hva",
        "kostnader_per_hva",
        "kostnader_per_motpart",
        "hva_per_maned",
    }

    for dataset in datasets.DATASETS:
        for view in dataset.views:
            for fra in (f"{THIS_YEAR}-01-01", f"{THIS_YEAR - 4}-01-01"):
                body = _chart(
                    client, cid, dataset.name, visning=view, fra=fra, til=f"{THIS_YEAR}-12-31"
                )
                chart = Chart.model_validate(body)
                assert chart.dataset.name == dataset.name
                assert chart.view == view
                assert chart.id == dataset.name


def test_a_view_a_dataset_does_not_have_or_an_unknown_name_is_refused(client: TestClient) -> None:
    company = _books(client)
    url = f"/api/v1/companies/{company['id']}/analyse/datasett"

    wrong_view = client.get(f"{url}/inntekter_per_kunde", params={"visning": "smultring"})
    assert wrong_view.status_code == 422
    assert wrong_view.json()["code"] == "chart_request"
    too_many = client.get(f"{url}/kostnader_per_konto", params={"visning": "smultring", "topp": 6})
    assert too_many.status_code == 422
    assert client.get(f"{url}/kostnader_per_konto", params={"topp": 11}).status_code == 422
    unknown = client.get(f"{url}/kostnader_per_hjerte")
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "unknown_dataset"


def test_a_donut_with_a_negative_slice_is_sent_as_bars(client: TestClient) -> None:
    """A refund booked with nothing to refund leaves a cost account in credit."""
    company = _books(client)
    cid = company["id"]
    assert (
        record(client, cid, type_code="kontorrekvisita", gross_amount="1250.00").status_code == 201
    )
    refund = record(client, cid, type_code="refusjon_varekjop", gross_amount="250.00")
    assert refund.status_code == 201, refund.text

    chart = _chart(
        client, cid, "kostnader_per_konto", fra=f"{THIS_YEAR}-01-01", til=f"{THIS_YEAR}-12-31"
    )

    assert chart["view"] == "stolper"
    assert any(Decimal(s["value"]) < 0 for s in chart["slices"])


def _base(**fields: Any) -> dict[str, Any]:
    return {
        "id": "x",
        "title": "Tittel",
        "unit": "kr",
        "dataset": {"name": "x", "params": {}},
        **fields,
    }


_MONTHS = {"kind": "maned", "labels": ["2026-01", "2026-02"]}
_SERIES = [{"key": "a", "label": "2026", "role": "hoved", "values": ["1.00", None]}]


def _slices(*shares: str) -> list[dict[str, str]]:
    return [
        {"key": f"s{i}", "label": f"S{i}", "value": "1.00", "share": share}
        for i, share in enumerate(shares)
    ]


@pytest.mark.parametrize(
    "spec",
    [
        pytest.param(_base(view="kolonner", series=_SERIES), id="kolonner without x"),
        pytest.param(_base(view="kolonner", x=_MONTHS), id="kolonner without series"),
        pytest.param(
            _base(
                view="linje", x=_MONTHS, series=[{**_SERIES[0], "key": f"k{i}"} for i in range(5)]
            ),
            id="five series",
        ),
        pytest.param(
            _base(view="kolonner", x=_MONTHS, series=[{**_SERIES[0], "values": ["1.00"]}]),
            id="a value short",
        ),
        pytest.param(
            _base(
                view="kolonner", x={"kind": "maned", "labels": ["mars", "april"]}, series=_SERIES
            ),
            id="a month not YYYY-MM",
        ),
        pytest.param(_base(view="smultring", slices=_slices(*["0.1"] * 7)), id="seven in a donut"),
        pytest.param(
            _base(view="smultring", slices=_slices("0.5", "0.4")), id="a donut short of one"
        ),
        pytest.param(
            _base(
                view="smultring",
                slices=[
                    {"key": "a", "label": "A", "value": "2.00", "share": "2"},
                    {"key": "b", "label": "B", "value": "-1.00", "share": "-1"},
                ],
            ),
            id="a negative slice in a donut",
        ),
        pytest.param(_base(view="stolper", slices=_slices(*["0.0625"] * 11)), id="eleven bars"),
        pytest.param(_base(view="stolper", x=_MONTHS, series=_SERIES), id="bars with series"),
        pytest.param(
            _base(view="tabell", x=_MONTHS, series=_SERIES, slices=_slices("1")),
            id="a table of both",
        ),
        pytest.param(_base(view="tabell"), id="a table of nothing"),
        pytest.param(_base(view="tall"), id="a number without total"),
        pytest.param(
            _base(view="tall", total="1.00", slices=_slices("1")), id="a number with slices"
        ),
        pytest.param(_base(view="sektor", slices=_slices("1")), id="an unknown view"),
        pytest.param(
            _base(view="smultring", slices=_slices("1"), title="T" * 61), id="a title too long"
        ),
        pytest.param(
            _base(view="smultring", slices=[*_slices("0.5"), *_slices("0.5")]),
            id="two slices one key",
        ),
        pytest.param(
            _base(view="smultring", slices=_slices("1"), farge="rød"), id="an unknown field"
        ),
    ],
)
def test_a_wrong_shape_is_refused_before_it_is_sent(spec: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Chart.model_validate(spec)


def test_the_right_shapes_pass() -> None:
    Chart.model_validate(_base(view="kolonner", x=_MONTHS, series=_SERIES))
    Chart.model_validate(_base(view="smultring", slices=_slices("0.5", "0.5")))
    Chart.model_validate(_base(view="smultring", slices=[]))
    Chart.model_validate(_base(view="stolper", slices=_slices(*["0.1"] * 10)))
    Chart.model_validate(_base(view="tabell", slices=_slices("1")))
    Chart.model_validate(_base(view="tabell", x=_MONTHS, series=_SERIES))
    Chart.model_validate(_base(view="tall", total="412000.00"))


def _parts(*amounts: str) -> list[Part]:
    return [Part(f"k{i}", f"Konto {i}", Decimal(a)) for i, a in enumerate(amounts)]


def test_andre_absorbs_the_rounding() -> None:
    # Five named at 100/700 each round up to 0.1429; «Andre» (200/700 = 0.2857)
    # takes the 0.0002 they gained, so the whole is exactly one.
    slices = datasets.top_slices(_parts(*["100.00"] * 7), topp=5)

    assert [s.key for s in slices] == ["k0", "k1", "k2", "k3", "k4", "andre"]
    assert [s.share for s in slices[:5]] == [Decimal("0.1429")] * 5
    assert slices[-1].value == Decimal("200.00")
    assert slices[-1].share == Decimal("0.2855")
    assert sum(s.share for s in slices) == 1
    Chart.model_validate(_base(view="smultring", slices=[s.model_dump() for s in slices]))


def test_without_andre_the_largest_takes_the_rounding() -> None:
    slices = datasets.top_slices(_parts("100.00", "100.00", "100.00"), topp=5)

    assert [s.share for s in slices] == [Decimal("0.3334"), Decimal("0.3333"), Decimal("0.3333")]


def test_a_single_part_past_the_top_is_named_rather_than_called_andre() -> None:
    slices = datasets.top_slices(_parts("600", "500", "400", "300", "200", "100"), topp=5)
    assert "andre" not in {s.key for s in slices}
    assert len(slices) == 6


def test_parts_that_came_to_zero_are_no_slices() -> None:
    assert datasets.top_slices(_parts("100.00", "0.00"), topp=5)[-1].key == "k0"
    assert datasets.top_slices(_parts("0.00"), topp=5) == []


# --- The sentences -----------------------------------------------------------------


def test_amounts_and_shares_are_written_the_norwegian_way() -> None:
    assert sentences.kroner(Decimal("412000.40")) == f"412{NBSP}000{NBSP}kr"
    assert sentences.kroner(Decimal("999.50")) == f"1{NBSP}000{NBSP}kr"
    assert sentences.kroner(Decimal("-1500")) == f"-1{NBSP}500{NBSP}kr"
    assert sentences.percent(Decimal("0.125")) == f"13{NBSP}%"


def test_the_income_sentence_and_its_comparison() -> None:
    today = date(2026, 11, 26)
    ytd = Period(date(2026, 1, 1), today)
    assert sentences.income(ytd, today, Decimal("412000"), Decimal("368000")) == (
        f"Inntektene hittil i år er 412{NBSP}000{NBSP}kr, 12{NBSP}% mer enn samme tid i fjor."
    )
    assert sentences.income(ytd, today, Decimal("300000"), Decimal("400000")) == (
        f"Inntektene hittil i år er 300{NBSP}000{NBSP}kr, 25{NBSP}% mindre enn samme tid i fjor."
    )
    assert sentences.income(ytd, today, Decimal("400100"), Decimal("400000")) == (
        f"Inntektene hittil i år er 400{NBSP}100{NBSP}kr, omtrent det samme som samme tid i fjor."
    )
    # No comparison, or nothing in it: the sentence without a percentage, never «0 %».
    assert sentences.income(ytd, today, Decimal("1000"), None) == (
        f"Inntektene hittil i år er 1{NBSP}000{NBSP}kr."
    )
    assert sentences.income(ytd, today, Decimal("1000"), Decimal("0")) == (
        f"Inntektene hittil i år er 1{NBSP}000{NBSP}kr."
    )
    assert sentences.income(ytd, today, Decimal("0"), Decimal("1000")) is None
    last_year = Period(date(2025, 1, 1), date(2025, 12, 31))
    assert sentences.income(last_year, today, Decimal("2000"), Decimal("1000")) == (
        f"Inntektene i 2025 var 2{NBSP}000{NBSP}kr, 100{NBSP}% mer enn i 2024."
    )


def test_the_largest_cost_and_when_it_is_left_out() -> None:
    parts = [
        sentences.Named("4000 Innkjøp av varer", Decimal("38000")),
        sentences.Named("6300 Leie lokale", Decimal("12000")),
    ]
    assert sentences.largest_cost(parts, Decimal("100000")) == (
        f"Den største kostnaden er 4000 Innkjøp av varer, 38{NBSP}% av kostnadene."
    )
    assert sentences.largest_cost(parts[:1], Decimal("38000")) is None
    assert sentences.largest_cost([], Decimal("0")) is None


def test_the_best_month_and_when_it_is_left_out() -> None:
    today = date(2026, 11, 26)
    months = [((2026, 2), Decimal("100")), ((2026, 3), Decimal("900")), ((2026, 4), Decimal("50"))]
    assert sentences.best_month(months, today) == "Mars var den beste måneden."
    assert sentences.best_month(months[:1], today) is None
    tie = [((2026, 2), Decimal("900")), ((2026, 3), Decimal("900"))]
    assert sentences.best_month(tie, today) is None
    losses = [((2026, 2), Decimal("-100")), ((2026, 3), Decimal("-50"))]
    assert sentences.best_month(losses, today) is None
    across = [((2025, 12), Decimal("900")), ((2026, 1), Decimal("100"))]
    assert sentences.best_month(across, today) == "Desember 2025 var den beste måneden."
    now = [((2026, 10), Decimal("100")), ((2026, 11), Decimal("900"))]
    assert sentences.best_month(now, today) == "November er så langt den beste måneden."


def test_the_page_says_it_in_sentences(client: TestClient) -> None:
    company = _books(client)
    cid = company["id"]
    for type_code, gross, on in (
        ("salg_hoy_sats", "12500.00", f"{THIS_YEAR}-02-10"),
        ("salg_hoy_sats", "2500.00", f"{THIS_YEAR}-03-10"),
        ("varekjop", "5000.00", f"{THIS_YEAR}-02-11"),
        ("kontorrekvisita", "1250.00", f"{THIS_YEAR}-03-11"),
    ):
        entry = record(client, cid, type_code=type_code, gross_amount=gross, occurred_on=on)
        assert entry.status_code == 201, entry.text

    body = _analyse(client, cid, fra=f"{THIS_YEAR}-01-01", til=f"{THIS_YEAR}-12-31")

    assert body["grunnlag"] == "regnskap"
    # KI is on for a new company, but nothing has read the books here (no
    # Løynd in the tests): nothing read, so the pies are by account.
    assert body["ki"] == {
        "lesing": None,
        "dekning": {"inntekter": "0.00", "kostnader": "0.00"},
    }
    assert body["nokkeltall"]["inntekter"] == "12000.00"
    assert [c["dataset"]["name"] for c in body["diagrammer"]] == [
        "inntekter_per_maned",
        "kostnader_per_maned",
        "inntekter_per_konto",
        "kostnader_per_konto",
    ]
    assert body["tekst"][0].startswith(f"Inntektene i {THIS_YEAR}")
    assert any(s.startswith("Den største kostnaden er 4") for s in body["tekst"])
    assert "Februar var den beste måneden." in body["tekst"]
    assert not any("\N{EM DASH}" in s for s in body["tekst"])


# --- Who may read it ------------------------------------------------------------------


def test_another_companys_figures_are_a_404_with_a_session_and_with_a_key(
    client: TestClient,
) -> None:
    first = register(client)
    theirs = _company(client, first["memberships"][0]["organization_id"], orgnr="910000004")
    assert (
        record(client, theirs["id"], type_code="salg_hoy_sats", gross_amount="1250.00").status_code
        == 201
    )

    second = register(client, "per@example.no", full_name="Per Hansen")
    own = _company(
        client, second["memberships"][0]["organization_id"], orgnr="910000012", name="Per AS"
    )
    header = agent_header(client, own["id"])
    paths = (
        "analyse",
        "analyse/datasett",
        "analyse/datasett/inntekter_per_maned",
    )
    for path in paths:
        url = f"/api/v1/companies/{theirs['id']}/{path}"
        assert client.get(url).status_code == 404, path
        assert client.get(url, headers=header).status_code == 404, path
        # Their own company answers, with the session and with the key.
        mine = f"/api/v1/companies/{own['id']}/{path}"
        assert client.get(mine).status_code == 200, path
        assert client.get(mine, headers=header).status_code == 200, path


def test_an_unknown_dataset_name_under_your_own_company_is_a_404(client: TestClient) -> None:
    """The one path parameter here names a dataset, not a record: the list is the same for all."""
    company = _books(client)
    response = client.get(f"/api/v1/companies/{company['id']}/analyse/datasett/1")
    assert response.status_code == 404
    assert response.json()["code"] == "unknown_dataset"


def test_a_viewer_may_read_the_figures(client: TestClient) -> None:
    """Read-only, like /regnskap/: a revisor invited to look sees the same page."""
    company = _books(client)
    cid = company["id"]
    assert record(client, cid, type_code="salg_hoy_sats", gross_amount="1250.00").status_code == 201
    join_company(client, cid, email="revisor@example.no", role="viewer")

    body = _whole_year(client, cid, THIS_YEAR)

    assert body["nokkeltall"]["inntekter"] == "1000.00"
    assert client.get(f"/api/v1/companies/{cid}/analyse/datasett").status_code == 200
    _chart(client, cid, "inntekter_per_konto")
