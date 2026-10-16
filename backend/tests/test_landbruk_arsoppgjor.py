"""The farm's year-end count and its private uttak, end to end through the API.

Synthetic farms only (regnaskaps_tests holds real names and is never copied).
The year is 2025 throughout, because it is the year whose takseringsregler are
in Myntos; a later year has no enhetspriser until Skattedirektoratet publishes
them in December, which one test pins down.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.arsoppgjor import buskap
from myntos.domain.arsoppgjor import takseringsregler as regler
from myntos.domain.ledger.models import Company
from tests.conftest import join_company, register, write
from tests.test_kontoplan import in_app

YEAR = 2025

pytestmark = pytest.mark.skipif(
    regler.for_year(YEAR) is None, reason="the 2025 takseringsregler are the ones these test"
)


def _farm(
    client: TestClient,
    *,
    form: str = "ENK",
    vat_registered: bool = True,
    landbruk: bool = True,
    email: str = "bonde@example.no",
) -> dict[str, Any]:
    session = register(client, email)
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": session["memberships"][0]["organization_id"],
            "orgnr": "910000004",
            "name": "Prøvegard",
            "first_financial_year": YEAR,
            "organisation_form": form,
            "vat_registered": vat_registered,
        },
    )
    assert response.status_code == 201, response.text
    company: dict[str, Any] = response.json()
    if landbruk:
        switched = write(
            client,
            "PUT",
            f"/api/v1/companies/{company['id']}/landbruk",
            json={"enabled": True},
        )
        assert switched.status_code == 200, switched.text
    return company


def _vat(client: TestClient, company_id: int, *, category: str, period_type: str) -> None:
    async def work(session: AsyncSession) -> None:
        await session.execute(
            update(Company)
            .where(Company.id == company_id)
            .values(vat_category=category, vat_period_type=period_type)
        )

    in_app(client, work)


def _opening(client: TestClient, company_id: int, lines: list[tuple[str, str]]) -> None:
    """An opening balance carrying the farm's inventory, against equity."""
    total = sum((Decimal(amount) for _, amount in lines), Decimal("0"))
    body = [
        {"account_number": account, "debit": amount, "credit": "0"} for account, amount in lines
    ]
    body.append({"account_number": "2050", "debit": "0", "credit": str(total)})
    response = write(
        client, "POST", f"/api/v1/companies/{company_id}/inngaende-balanse", json={"lines": body}
    )
    assert response.status_code == 201, response.text


def _balances(client: TestClient, company_id: int, year: int = YEAR) -> dict[str, Decimal]:
    async def work(session: AsyncSession) -> dict[str, Decimal]:
        company = await session.scalar(select(Company).where(Company.id == company_id))
        assert company is not None
        return await buskap.booked_balances(session, company, up_to=year)

    return in_app(client, work)


def _line(side: str, category: str, quantity: str, **extra: Any) -> dict[str, Any]:
    return {"side": side, "category": category, "quantity": quantity, **extra}


def _herd() -> list[dict[str, Any]]:
    """Ten cows and four heifers in, a herd that grew, grain and bought feed out."""
    return [
        _line("inngaende", "storfe_kyr_ib", "10", unit_value="9000"),
        _line("inngaende", "storfe_kviger_over_12", "4", unit_value="2500"),
        _line("utgaende", "storfe_kyr_ib", "8"),
        _line("utgaende", "storfe_kyr_tilfort", "3"),
        _line("utgaende", "storfe_kviger_under_12", "5"),
        _line("utgaende", "korn", "10000"),
        _line("utgaende", "innkjopt_kraftfor", "2000", unit_value="4.50"),
    ]


def _count(client: TestClient, company_id: int, path: str = "", **body: Any) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/landbruk/telling{path}",
        json={"aar": YEAR, **body},
    )


def _codes(response: Any) -> set[str]:
    return {f["code"] for f in response.json()["findings"]}


# --- The count ----------------------------------------------------------------------


def test_a_company_without_the_add_on_has_no_count(client: TestClient) -> None:
    company = _farm(client, landbruk=False)
    response = client.get(f"/api/v1/companies/{company['id']}/landbruk/telling?aar={YEAR}")
    assert response.status_code == 409
    assert response.json()["code"] == "segment_not_enabled"


def test_the_count_prices_by_the_rules_and_posts_one_voucher(client: TestClient) -> None:
    company = _farm(client)
    _opening(client, company["id"], [("1450", "100000.00")])

    review = _count(client, company["id"], "/kontroll", lines=_herd())
    assert review.status_code == 200, review.text
    body = review.json()
    assert body["postable"], body["findings"]
    assert body["company_id"] == company["id"]
    closing = {li["category"]: li for li in body["closing"]}
    # Cows from the opening balance at the average of the adults in it (§ 5-2),
    # new cows and calves at the year's enhetspriser, grain per kilo, feed at cost.
    assert closing["storfe_kyr_ib"]["valuation"] == "snitt"
    assert Decimal(closing["storfe_kyr_ib"]["unit_value"]) == Decimal("9000")
    assert Decimal(closing["storfe_kyr_tilfort"]["value"]) == Decimal("27600.00")
    assert Decimal(closing["storfe_kviger_under_12"]["value"]) == Decimal("14000.00")
    assert Decimal(closing["korn"]["value"]) == Decimal("17000.00")
    assert closing["korn"]["account_number"] == "1490"
    assert Decimal(closing["innkjopt_kraftfor"]["value"]) == Decimal("9000.00")
    voucher = {
        (v["account_number"], Decimal(v["debit"]), Decimal(v["credit"]))
        for v in body["voucher_lines"]
    }
    assert voucher == {
        ("1450", Decimal("13600.00"), Decimal("0.00")),
        ("4690", Decimal("0.00"), Decimal("13600.00")),
        ("1490", Decimal("17000.00"), Decimal("0.00")),
        ("4290", Decimal("0.00"), Decimal("17000.00")),
        ("1400", Decimal("9000.00"), Decimal("0.00")),
        ("4090", Decimal("0.00"), Decimal("9000.00")),
    }
    totals = {t["naeringsspesifikasjon"]: Decimal(t["closing"]) for t in body["totals"]}
    assert totals["buskap"] == Decimal("113600.00")

    posted = _count(client, company["id"], lines=_herd())
    assert posted.status_code == 201, posted.text
    assert posted.json()["voucher_no"]
    balances = _balances(client, company["id"])
    assert balances["1450"] == Decimal("113600.00")
    assert balances["1490"] == Decimal("17000.00")
    assert balances["4690"] == Decimal("-13600.00")

    state = client.get(f"/api/v1/companies/{company['id']}/landbruk/telling?aar={YEAR}").json()
    assert state["standing"]["id"] == posted.json()["id"]
    assert state["opening_source"] == "oppgitt"


def test_the_average_counts_every_adult_and_rounds_to_ten_kroner(client: TestClient) -> None:
    company = _farm(client)
    lines = [
        _line("inngaende", "storfe_kyr_ib", "2", unit_value="9010"),
        # Last year's new cows are adults in this year's opening balance.
        _line("inngaende", "storfe_kyr_tilfort", "1", unit_value="9210"),
        _line("utgaende", "storfe_kyr_ib", "3"),
    ]
    body = _count(client, company["id"], "/kontroll", lines=lines).json()
    # 27 230 / 3 = 9 076.67, to the nearest 10 kroner.
    assert Decimal(body["closing"][0]["unit_value"]) == Decimal("9080")
    # Nothing on 1450 in the books, so the merknad about the opening is there.
    assert "inngaende_avvik" in _codes_of(body)


def _codes_of(body: dict[str, Any]) -> set[str]:
    return {f["code"] for f in body["findings"]}


def test_a_balance_with_no_line_is_refused(client: TestClient) -> None:
    company = _farm(client)
    _opening(client, company["id"], [("1450", "50000.00"), ("1400", "8000.00")])
    lines = [
        _line("inngaende", "storfe_kyr_ib", "5", unit_value="10000"),
        _line("utgaende", "storfe_kyr_ib", "5"),
    ]
    review = _count(client, company["id"], "/kontroll", lines=lines)
    assert not review.json()["postable"]
    assert "konto_uten_telling" in _codes(review)
    posted = _count(client, company["id"], lines=lines)
    assert posted.status_code == 422
    assert posted.json()["code"] == "invalid_count"
    # Counting zero writes it down.
    lines.append(_line("utgaende", "innkjopt_kraftfor", "0", unit_value="5"))
    assert _count(client, company["id"], lines=lines).status_code == 201
    assert _balances(client, company["id"])["1400"] == Decimal("0.00")


def test_a_year_without_published_rates_refuses_normalverdi_only(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = _farm(client)
    published = regler.for_year
    monkeypatch.setattr(regler, "for_year", lambda y: None if y == YEAR else published(y))
    lines = [
        _line("inngaende", "storfe_kyr_ib", "4", unit_value="9000"),
        _line("utgaende", "storfe_kyr_ib", "4"),
        _line("utgaende", "storfe_kyr_tilfort", "1"),
    ]
    body = _count(client, company["id"], "/kontroll", lines=lines).json()
    assert {f["code"] for f in body["findings"] if f["severity"] == "feil"} == {"ingen_satser"}
    # The average needs no rates: all but the new cow can be prepared.
    assert _count(client, company["id"], "/kontroll", lines=lines[:2]).json()["postable"]


def test_a_second_count_is_a_correction_and_the_register_cannot_reverse_it(
    client: TestClient,
) -> None:
    company = _farm(client)
    _opening(client, company["id"], [("1450", "100000.00")])
    first = _count(client, company["id"], lines=_herd())
    assert first.status_code == 201

    again = _count(client, company["id"], lines=_herd())
    assert again.status_code == 409
    assert again.json()["code"] == "count_exists"

    reversal = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/cashflow/{first.json()['voucher_id']}/reversal",
    )
    assert reversal.status_code == 409
    assert reversal.json()["code"] == "cannot_reverse_here"

    fewer = [li for li in _herd() if li["category"] != "korn"]
    corrected = _count(client, company["id"], lines=fewer, korriger=True)
    assert corrected.status_code == 201, corrected.text
    assert corrected.json()["corrects_id"] == first.json()["id"]
    assert _balances(client, company["id"]).get("1490", Decimal("0")) == Decimal("0.00")

    withdrawn = _count(client, company["id"], "/reversering")
    assert withdrawn.status_code == 201, withdrawn.text
    assert withdrawn.json()["withdrawn"]
    balances = _balances(client, company["id"])
    assert balances["1450"] == Decimal("100000.00")
    state = client.get(f"/api/v1/companies/{company['id']}/landbruk/telling?aar={YEAR}").json()
    assert state["standing"] is None
    assert len(state["history"]) == 3
    assert _count(client, company["id"], "/reversering").json()["code"] == "no_count"


def test_next_year_opens_with_this_years_closing_rows(client: TestClient) -> None:
    company = _farm(client)
    # No opening rows in the first year, so nothing for the average to read:
    # the cows are counted as new ones.
    lines = [li for li in _herd() if li["side"] == "utgaende" and li["category"] != "storfe_kyr_ib"]
    assert _count(client, company["id"], lines=lines).status_code == 201
    write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/cashflow",
        json={
            "type_code": "landbruk_melk",
            "occurred_on": f"{YEAR + 1}-02-01",
            "gross_amount": "1250.00",
            "description": "Melk",
            "payment_method": "bank",
        },
    )
    state = client.get(f"/api/v1/companies/{company['id']}/landbruk/telling?aar={YEAR + 1}")
    assert state.status_code == 200, state.text
    body = state.json()
    assert body["opening_source"] == "forrige_telling"
    assert {li["category"] for li in body["opening"]} == {li["category"] for li in lines}
    typed = _count(
        client,
        company["id"],
        "/kontroll",
        aar=YEAR + 1,
        lines=[_line("inngaende", "korn", "1", unit_value="1")],
    )
    assert "inngaende_fra_forrige_telling" in _codes(typed)


def test_a_viewer_reads_the_count_and_cannot_post_it(client: TestClient) -> None:
    company = _farm(client)
    join_company(client, company["id"], email="revisor@example.no", role="viewer")
    url = f"/api/v1/companies/{company['id']}/landbruk/telling"
    assert client.get(f"{url}?aar={YEAR}").status_code == 200
    assert _count(client, company["id"], lines=_herd()).status_code == 403


def test_closing_the_year_asks_for_the_count(client: TestClient) -> None:
    company = _farm(client)
    plan = client.get(f"/api/v1/companies/{company['id']}/arsavslutning?aar={YEAR}")
    assert plan.status_code == 200, plan.text
    assert "ingen_telling_buskap" in _codes(plan)


# --- Private uttak --------------------------------------------------------------------


def _uttak(client: TestClient, company_id: int, path: str = "", **body: Any) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/landbruk/uttak{path}",
        json={"aar": YEAR, **body},
    )


HOUSEHOLD = [
    # Two adults and a grandparent over 70: 2.5 milk norms.
    {"product": "melk", "quantity": "2.5"},
    {"product": "ved", "quantity": "10"},
    {"product": "kjott", "quantity": "40", "unit_value": "120"},
]


def test_a_primary_producer_on_the_yearly_melding_takes_out_vat_free(client: TestClient) -> None:
    company = _farm(client)
    _vat(client, company["id"], category="primaernaering", period_type="aar")
    review = _uttak(client, company["id"], "/kontroll", lines=HOUSEHOLD)
    assert review.status_code == 200, review.text
    body = review.json()
    assert body["postable"], body["findings"]
    assert {li["tax_code"] for li in body["lines"]} == {"5"}
    assert {li["vat_reason"] for li in body["lines"]} == {"fritatt"}
    assert Decimal(body["total_value"]) == Decimal("1025.00") + Decimal("7500.00") + Decimal(
        "4800.00"
    )
    assert Decimal(body["total_vat"]) == Decimal("0.00")

    posted = _uttak(client, company["id"], lines=HOUSEHOLD)
    assert posted.status_code == 201, posted.text
    balances = _balances(client, company["id"])
    assert balances["2060"] == Decimal("13325.00")
    assert balances["3163"] == Decimal("-1025.00")
    assert balances["3164"] == Decimal("-7500.00")
    assert balances["3162"] == Decimal("-4800.00")
    assert "2700" not in balances or balances["2700"] == Decimal("0.00")

    again = _uttak(client, company["id"], lines=HOUSEHOLD)
    assert again.json()["code"] == "uttak_exists"
    withdrawn = _uttak(client, company["id"], "/reversering")
    assert withdrawn.status_code == 201
    assert _balances(client, company["id"])["2060"] == Decimal("0.00")


def test_an_alminnelig_melding_taxes_the_uttak_as_a_sale(client: TestClient) -> None:
    company = _farm(client)
    body = _uttak(client, company["id"], "/kontroll", lines=HOUSEHOLD).json()
    by_product = {li["product"]: li for li in body["lines"]}
    # Raw milk and firewood are not food; meat is, at 15 %.
    assert by_product["melk"]["tax_code"] == "3"
    assert Decimal(by_product["melk"]["vat"]) == Decimal("256.25")
    assert by_product["kjott"]["tax_code"] == "31"
    assert Decimal(by_product["kjott"]["vat"]) == Decimal("720.00")
    assert "norm_som_mva_grunnlag" in _codes_of(body)
    assert _uttak(client, company["id"], lines=HOUSEHOLD).status_code == 201
    assert _balances(client, company["id"])["2700"] == -(
        Decimal("256.25") + Decimal("1875.00") + Decimal("720.00")
    )


def test_an_unregistered_farm_has_no_vat_on_its_uttak(client: TestClient) -> None:
    company = _farm(client, vat_registered=False)
    body = _uttak(client, company["id"], "/kontroll", lines=HOUSEHOLD).json()
    assert {li["tax_code"] for li in body["lines"]} == {None}
    assert {li["vat_reason"] for li in body["lines"]} == {"uregistrert"}


def test_an_as_has_no_private_uttak(client: TestClient) -> None:
    company = _farm(client, form="AS")
    response = _uttak(client, company["id"], lines=HOUSEHOLD)
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_uttak"
    assert "ikke_for_aksjeselskap" in _codes(response)


def test_a_product_without_a_norm_needs_its_value(client: TestClient) -> None:
    company = _farm(client)
    body = _uttak(
        client, company["id"], "/kontroll", lines=[{"product": "egg", "quantity": "20"}]
    ).json()
    assert "mangler_verdi" in _codes_of(body)
