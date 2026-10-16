"""Anleggsregister and saldoavskrivning, end to end through the API.

The books start the year before this one, so that year is over and its
depreciation can be posted; this year is still running. Every figure below is
worked out by hand from skatteloven §§ 14-40 to 14-47 as they read from 2024:
group d 20 %, husdyrbygg 6 %, the 30 000 limits, the gevinst- og tapskonto at
20 %.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import register, write
from tests.test_cashflow_api import THIS_YEAR, record

LAST_YEAR = THIS_YEAR - 1

pytestmark = pytest.mark.skipif(
    LAST_YEAR < 2024, reason="Myntos has the depreciation rates from 2024"
)


def _books(client: TestClient, first_year: int = LAST_YEAR, form: str = "ENK") -> dict[str, Any]:
    session = register(client)
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": session["memberships"][0]["organization_id"],
            "orgnr": "910000004",
            "name": "Gard og Maskin",
            "first_financial_year": first_year,
            "organisation_form": form,
            "vat_registered": True,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _account(client: TestClient, company_id: int, number: str, description: str) -> None:
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/kontoplan",
        json={"number": number, "description": description},
        params={"aar": LAST_YEAR},
    )
    assert response.status_code == 201, response.text


def _asset(client: TestClient, company_id: int, **fields: Any) -> Any:
    body = {
        "description": "Traktor",
        "saldogruppe": "d",
        "account_number": "1250",
        "acquired_on": date(LAST_YEAR, 3, 1).isoformat(),
        "cost": "100000.00",
        **fields,
    }
    return write(client, "POST", f"/api/v1/companies/{company_id}/anlegg", json=body)


def _plan(client: TestClient, company_id: int, year: int = LAST_YEAR) -> dict[str, Any]:
    response = client.get(
        f"/api/v1/companies/{company_id}/anlegg/avskrivning", params={"aar": year}
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _saldo(plan: dict[str, Any], key: str) -> dict[str, Any]:
    found: dict[str, Any] = next(s for s in plan["specification"]["saldi"] if s["key"] == key)
    return found


def _codes(plan: dict[str, Any]) -> set[str]:
    return {f["code"] for f in plan["findings"]}


def _post(client: TestClient, company_id: int, year: int = LAST_YEAR, **kw: Any) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/anlegg/avskrivning",
        json={"aar": year, **kw},
    )


# --- The register ----------------------------------------------------------------------


def test_an_asset_is_read_off_its_purchase_voucher_and_depreciated_at_20_percent(
    client: TestClient,
) -> None:
    company = _books(client)
    bought = record(
        client,
        company["id"],
        type_code="driftsmiddel",
        gross_amount="125000.00",
        occurred_on=date(LAST_YEAR, 5, 10).isoformat(),
    )
    assert bought.status_code == 201, bought.text
    made = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/anlegg",
        json={"description": "Hjullaster", "saldogruppe": "d", "voucher_id": bought.json()["id"]},
    )
    assert made.status_code == 201, made.text
    asset = made.json()
    # Net of the deductible VAT, on the account the purchase went to, on its date.
    assert (asset["account_number"], asset["cost"], asset["acquired_on"]) == (
        "1250",
        "100000.00",
        date(LAST_YEAR, 5, 10).isoformat(),
    )
    assert asset["company_id"] == company["id"]
    assert asset["saldo_key"] == "d"

    plan = _plan(client, company["id"])
    d = _saldo(plan, "d")
    assert Decimal(d["basis"]) == Decimal("100000.00")
    assert Decimal(d["rate"]) == Decimal("0.20")
    # A full year's depreciation, whatever the month of delivery (Skatte-ABC D-2-9.1).
    assert Decimal(d["max_depreciation"]) == Decimal("20000.00")
    assert plan["postable"] is True, plan["findings"]
    assert not _codes(plan) & {"konto_avviker", "kjop_ikke_i_registeret"}
    assert {(line["account_number"], line["debit"], line["credit"]) for line in plan["lines"]} == {
        ("6015", "20000.00", "0.00"),
        ("1250", "0.00", "20000.00"),
    }

    posted = _post(client, company["id"])
    assert posted.status_code == 201, posted.text
    assert Decimal(posted.json()["total"]) == Decimal("20000.00")
    assert posted.json()["transaction_date"] == date(LAST_YEAR, 12, 31).isoformat()

    spec = client.get(
        f"/api/v1/companies/{company['id']}/anlegg/saldo", params={"aar": LAST_YEAR}
    ).json()
    assert spec["posted"] is True
    assert Decimal(spec["saldi"][0]["closing"]) == Decimal("80000.00")

    # The next year opens with what the voucher left, and is not over yet.
    following = _plan(client, company["id"], THIS_YEAR)
    d = _saldo(following, "d")
    assert Decimal(d["opening"]) == Decimal("80000.00")
    assert Decimal(d["max_depreciation"]) == Decimal("16000.00")
    # Its financial year opens on demand, like any voucher's.
    assert "aaret_er_ikke_over" in _codes(following)
    assert following["postable"] is False


def test_a_rest_under_30_000_may_be_deducted_whole(client: TestClient) -> None:
    company = _books(client)
    assert _asset(client, company["id"], cost="25000.00").status_code == 201
    plan = _plan(client, company["id"])
    d = _saldo(plan, "d")
    assert d["small_rest"] is True
    assert Decimal(d["max_depreciation"]) == Decimal("25000.00")
    # Under the limit it could have been expensed at once, and typed in by hand
    # the books do not carry it yet: both are said, neither blocks.
    assert {"under_belopsgrensen", "konto_avviker"} <= _codes(plan)
    assert plan["postable"] is True


def test_a_livestock_building_is_depreciated_at_6_percent(client: TestClient) -> None:
    company = _books(client)
    _account(client, company["id"], "1100", "Bygninger")
    made = _asset(
        client,
        company["id"],
        description="Fjøs",
        saldogruppe="h",
        rate_variant="husdyrbygg",
        account_number="1100",
        cost="2000000.00",
    )
    assert made.status_code == 201, made.text
    plan = _plan(client, company["id"])
    building = _saldo(plan, f"h:{made.json()['id']}")
    assert Decimal(building["rate"]) == Decimal("0.06")
    assert Decimal(building["max_depreciation"]) == Decimal("120000.00")
    assert plan["lines"][0]["account_number"] == "6000"


def test_a_raised_rate_belongs_to_group_h_only(client: TestClient) -> None:
    company = _books(client)
    refused = _asset(client, company["id"], rate_variant="husdyrbygg")
    assert refused.status_code == 422
    assert refused.json()["code"] == "invalid_fixed_asset"


def test_an_investment_grant_reduces_the_cost_unless_it_is_tax_free(client: TestClient) -> None:
    company = _books(client)
    _account(client, company["id"], "1100", "Bygninger")
    reduced = _asset(
        client,
        company["id"],
        description="Lager",
        saldogruppe="h",
        account_number="1100",
        cost="1000000.00",
        grant="300000.00",
    ).json()
    tax_free = _asset(
        client,
        company["id"],
        description="Fjøs, Innovasjon Norge",
        saldogruppe="h",
        account_number="1100",
        cost="1000000.00",
        grant="300000.00",
        grant_reduces_cost=False,
    ).json()
    plan = _plan(client, company["id"])
    first = _saldo(plan, f"h:{reduced['id']}")
    assert Decimal(first["grants"]) == Decimal("300000.00")
    assert Decimal(first["basis"]) == Decimal("700000.00")
    assert Decimal(first["max_depreciation"]) == Decimal("28000.00")
    second = _saldo(plan, f"h:{tax_free['id']}")
    assert Decimal(second["grants"]) == 0
    assert Decimal(second["max_depreciation"]) == Decimal("40000.00")

    # Sold within five years, the tax-free grant counts as part of the price
    # (§ 14-44 første ledd).
    sold = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/anlegg/{tax_free['id']}/avgang",
        json={"disposed_on": date(LAST_YEAR, 11, 1).isoformat(), "proceeds": "900000.00"},
    )
    assert sold.status_code == 200, sold.text
    second = _saldo(_plan(client, company["id"]), f"h:{tax_free['id']}")
    assert Decimal(second["disposals"]) == Decimal("1200000.00")


def test_a_grant_larger_than_the_cost_is_refused(client: TestClient) -> None:
    company = _books(client)
    refused = _asset(client, company["id"], grant="100000.01")
    assert refused.status_code == 422


# --- Disposals ---------------------------------------------------------------------------


def test_a_sale_in_group_d_reduces_the_saldo_and_a_negative_saldo_is_income(
    client: TestClient,
) -> None:
    company = _books(client)
    openings = write(
        client,
        "PUT",
        f"/api/v1/companies/{company['id']}/anlegg/inngaende-saldo",
        json={
            "aar": LAST_YEAR,
            "rows": [{"saldogruppe": "d", "account_number": "1250", "amount": "50000.00"}],
        },
    )
    assert openings.status_code == 200, openings.text
    old = _asset(
        client,
        company["id"],
        description="Gammel treskeverk",
        acquired_on=date(LAST_YEAR - 3, 6, 1).isoformat(),
        cost="400000.00",
    )
    assert old.status_code == 201, old.text
    sold = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/anlegg/{old.json()['id']}/avgang",
        json={"disposed_on": date(LAST_YEAR, 9, 1).isoformat(), "proceeds": "200000.00"},
    )
    assert sold.status_code == 200, sold.text

    plan = _plan(client, company["id"])
    d = _saldo(plan, "d")
    assert Decimal(d["opening"]) == Decimal("50000.00")
    assert Decimal(d["disposals"]) == Decimal("200000.00")
    assert Decimal(d["basis"]) == Decimal("-150000.00")
    # At least 20 % of a negative saldo is income (§ 14-46), rounded up.
    assert Decimal(d["min_income"]) == Decimal("30000.00")
    assert {(line["account_number"], line["debit"], line["credit"]) for line in plan["lines"]} == {
        ("1250", "30000.00", "0.00"),
        ("3895", "0.00", "30000.00"),
    }

    too_little = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/anlegg/avskrivning/kontroll",
        json={"aar": LAST_YEAR, "valg": {"d": "29999.00"}},
    ).json()
    assert "under_minstebelop" in _codes(too_little)
    assert too_little["postable"] is False
    more = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/anlegg/avskrivning/kontroll",
        json={"aar": LAST_YEAR, "valg": {"d": "50000.00"}},
    ).json()
    assert Decimal(_saldo(more, "d")["income"]) == Decimal("50000.00")


def test_a_building_sold_at_a_gain_goes_to_the_gain_and_loss_account(client: TestClient) -> None:
    company = _books(client)
    _account(client, company["id"], "1100", "Bygninger")
    building = _asset(
        client,
        company["id"],
        description="Verksted",
        saldogruppe="h",
        account_number="1100",
        cost="1000000.00",
    ).json()
    write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/anlegg/{building['id']}/avgang",
        json={"disposed_on": date(LAST_YEAR, 10, 1).isoformat(), "proceeds": "1200000.00"},
    )
    plan = _plan(client, company["id"])
    saldo = _saldo(plan, f"h:{building['id']}")
    # No depreciation in the year it was realised (Skatte-ABC D-2-11).
    assert Decimal(saldo["max_depreciation"]) == 0
    assert Decimal(saldo["to_gain_loss_account"]) == Decimal("200000.00")
    assert Decimal(saldo["closing"]) == 0
    gtk = plan["specification"]["gain_loss_account"]
    assert Decimal(gtk["gains"]) == Decimal("200000.00")
    # At least 20 % of a gevinstsaldo is income each year (§ 14-45).
    assert Decimal(gtk["income"]) == Decimal("40000.00")
    assert Decimal(gtk["closing"]) == Decimal("160000.00")
    # The books clear the asset account against the gain; the GTK is tax only.
    assert {(line["account_number"], line["debit"], line["credit"]) for line in plan["lines"]} == {
        ("1100", "200000.00", "0.00"),
        ("3880", "0.00", "200000.00"),
    }


def test_an_asset_from_before_the_register_brings_its_value(client: TestClient) -> None:
    company = _books(client)
    _account(client, company["id"], "1100", "Bygninger")
    before = date(LAST_YEAR - 2, 4, 1).isoformat()
    refused = _asset(
        client, company["id"], saldogruppe="h", account_number="1100", acquired_on=before
    )
    assert refused.status_code == 422
    brought = _asset(
        client,
        company["id"],
        saldogruppe="h",
        account_number="1100",
        acquired_on=before,
        opening_value="900000.00",
    )
    assert brought.status_code == 201, brought.text
    assert brought.json()["opening_year"] == LAST_YEAR
    saldo = _saldo(_plan(client, company["id"]), f"h:{brought.json()['id']}")
    assert Decimal(saldo["opening"]) == Decimal("900000.00")
    assert Decimal(saldo["additions"]) == 0
    # And a collective group's asset never carries a value of its own.
    collective = _asset(client, company["id"], acquired_on=before, opening_value="1.00")
    assert collective.status_code == 422


def test_opening_saldi_are_for_collective_groups_and_the_gain_and_loss_account(
    client: TestClient,
) -> None:
    company = _books(client)
    url = f"/api/v1/companies/{company['id']}/anlegg/inngaende-saldo"
    for rows in (
        [{"saldogruppe": "e", "account_number": "1250", "amount": "1.00"}],
        [{"saldogruppe": "gtk", "account_number": "1250", "amount": "1.00"}],
        [{"saldogruppe": "d", "amount": "1.00"}],
        [{"saldogruppe": "d", "account_number": "3000", "amount": "1.00"}],
    ):
        refused = write(client, "PUT", url, json={"aar": LAST_YEAR, "rows": rows})
        assert refused.status_code == 422, rows
        assert refused.json()["code"] == "invalid_opening_saldo"
    stored = write(
        client,
        "PUT",
        url,
        json={
            "aar": LAST_YEAR,
            "rows": [
                {"saldogruppe": "d", "account_number": "1250", "amount": "60000.00"},
                {"saldogruppe": "gtk", "amount": "-40000.00"},
            ],
        },
    )
    assert stored.status_code == 200, stored.text
    plan = _plan(client, company["id"])
    assert Decimal(_saldo(plan, "d")["max_depreciation"]) == Decimal("12000.00")
    # A tapssaldo of 40 000: at most 20 % is deducted.
    assert Decimal(plan["specification"]["gain_loss_account"]["deduction"]) == Decimal("8000.00")


# --- Posting, correcting, reversing ------------------------------------------------------


def test_the_voucher_is_corrected_and_reversed_only_from_its_own_route(
    client: TestClient,
) -> None:
    company = _books(client)
    first = _asset(client, company["id"])
    assert first.status_code == 201
    chosen = _post(client, company["id"], valg={"d": "10000.00"})
    assert chosen.status_code == 201, chosen.text
    assert Decimal(chosen.json()["total"]) == Decimal("10000.00")

    plan = _plan(client, company["id"])
    assert plan["standing"]["id"] == chosen.json()["id"]
    # Shown with the choice its voucher made, so nothing is out of date.
    assert Decimal(_saldo(plan, "d")["depreciation"]) == Decimal("10000.00")
    assert "avskrivning_utdatert" not in _codes(plan)

    again = _post(client, company["id"])
    assert again.status_code == 409
    assert again.json()["code"] == "depreciation_exists"

    ledger = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/cashflow/{chosen.json()['id']}/reversal",
    )
    assert ledger.status_code == 409
    assert ledger.json()["code"] == "cannot_reverse_here"

    # The register changes after posting: the review says the voucher is stale.
    assert _asset(client, company["id"], description="Plog", cost="50000.00").status_code == 201
    assert "avskrivning_utdatert" in _codes(_plan(client, company["id"]))

    # Without valg a correction keeps the choice the voucher made for the
    # group; the person now takes the new maximum, 20 % of 150 000.
    corrected = _post(client, company["id"], korriger=True, valg={"d": "30000.00"})
    assert corrected.status_code == 201, corrected.text
    assert Decimal(corrected.json()["total"]) == Decimal("30000.00")
    plan = _plan(client, company["id"])
    assert plan["standing"]["corrects_id"] == chosen.json()["id"]
    assert [v["is_reversal"] for v in plan["history"]] == [False, True, False]
    assert "avskrivning_utdatert" not in _codes(plan)

    url = f"/api/v1/companies/{company['id']}/anlegg/avskrivning/reversering"
    reversed_ = write(client, "POST", url, json={"aar": LAST_YEAR})
    assert reversed_.status_code == 201, reversed_.text
    again = write(client, "POST", url, json={"aar": LAST_YEAR})
    assert again.status_code == 409
    assert again.json()["code"] == "no_depreciation"
    assert _plan(client, company["id"])["standing"] is None


@pytest.mark.skipif(THIS_YEAR - 2 < 2024, reason="needs two finished years from 2024")
def test_an_earlier_year_is_not_reversed_under_a_later_one(client: TestClient) -> None:
    company = _books(client, first_year=THIS_YEAR - 2)
    assert (
        _asset(client, company["id"], acquired_on=date(THIS_YEAR - 2, 2, 1).isoformat()).status_code
        == 201
    )
    assert _post(client, company["id"], THIS_YEAR - 2).status_code == 201
    later = _post(client, company["id"], LAST_YEAR)
    assert later.status_code == 201, later.text
    # 100 000 less 20 000, then 20 % of 80 000.
    assert Decimal(later.json()["total"]) == Decimal("16000.00")
    refused = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/anlegg/avskrivning/reversering",
        json={"aar": THIS_YEAR - 2},
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "later_year_depreciated"


def test_the_year_end_review_says_when_depreciation_is_missing(client: TestClient) -> None:
    company = _books(client)
    url = f"/api/v1/companies/{company['id']}/arsavslutning"
    before = client.get(url, params={"aar": LAST_YEAR}).json()
    assert "avskrivning_ikke_fort" not in {f["code"] for f in before["findings"]}

    _asset(client, company["id"])
    missing = client.get(url, params={"aar": LAST_YEAR}).json()
    found = next(f for f in missing["findings"] if f["code"] == "avskrivning_ikke_fort")
    assert found["severity"] == "merknad"

    assert _post(client, company["id"]).status_code == 201
    posted = client.get(url, params={"aar": LAST_YEAR}).json()
    assert not {"avskrivning_ikke_fort", "avskrivning_utdatert"} & {
        f["code"] for f in posted["findings"]
    }


def test_a_closed_year_freezes_the_register(client: TestClient) -> None:
    company = _books(client)
    asset = _asset(client, company["id"]).json()
    assert _post(client, company["id"]).status_code == 201
    closed = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/arsavslutning",
        json={"aar": LAST_YEAR, "bekreftet": True},
    )
    assert closed.status_code == 201, closed.text
    changed = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/anlegg/{asset['id']}",
        json={"cost": "90000.00"},
    )
    assert changed.status_code == 409
    assert changed.json()["code"] == "register_year_closed"
    # Renaming touches no saldo, but the year it entered is closed all the same.
    assert (
        write(
            client,
            "DELETE",
            f"/api/v1/companies/{company['id']}/anlegg/{asset['id']}",
        ).status_code
        == 409
    )
    reversed_ = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/anlegg/avskrivning/reversering",
        json={"aar": LAST_YEAR},
    )
    assert reversed_.status_code == 409


def test_a_patch_changes_only_what_is_sent(client: TestClient) -> None:
    company = _books(client)
    asset = _asset(client, company["id"], note="Kjøpt brukt").json()
    changed = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/anlegg/{asset['id']}",
        json={"description": "Traktor, Valtra"},
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["description"] == "Traktor, Valtra"
    assert (body["cost"], body["note"]) == ("100000.00", "Kjøpt brukt")
