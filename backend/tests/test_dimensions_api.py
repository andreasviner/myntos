"""Dimensions (avdelinger, prosjekter, driftsgrener), end to end.

Real postings through the API on SQLite: a dimension and its values set up the
way the page does it, a register entry and a composite entry put on them, the
result per value adding up to the year's result with «Uten …» for the rest, a
correction and a reversal keeping the allocation, and the SAF-T file carrying
the lines' ``Analysis`` in a form the schema accepts. What a value posted to
may no longer do (be deleted, be chosen while switched off) is pinned here too,
because a posted voucher is history and its lines keep their dimension for good.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from myntos.saft.export import NAMESPACE
from tests.conftest import join_company, write
from tests.test_cashflow_api import OTHER_ORGNR, THIS_YEAR, create_company, signed_in_company
from tests.test_composite_entries import MILK_SETTLEMENT, composite
from tests.test_landbruk import farm_company
from tests.test_saft_api import NS, _parse, _validate


def _path(company_id: int, tail: str = "") -> str:
    return f"/api/v1/companies/{company_id}/dimensjoner{tail}"


def _dimension(
    client: TestClient, company_id: int, code: str = "AVDELING", description: str = "Avdeling"
) -> dict[str, Any]:
    response = write(
        client, "POST", _path(company_id), json={"code": code, "description": description}
    )
    assert response.status_code == 201, response.text
    return next(item for item in response.json() if item["code"] == code.upper())


def _value(
    client: TestClient, company_id: int, dimension_id: int, value: str, description: str
) -> int:
    response = write(
        client,
        "POST",
        _path(company_id, f"/{dimension_id}/verdier"),
        json={"value": value, "description": description},
    )
    assert response.status_code == 201, response.text
    dimension = next(item for item in response.json() if item["id"] == dimension_id)
    return int(next(item["id"] for item in dimension["values"] if item["value"] == value))


def _entry(
    client: TestClient,
    company_id: int,
    type_code: str,
    amount: str,
    values: list[int],
    *,
    on: str = f"{THIS_YEAR}-03-14",
) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/cashflow",
        json={
            "type_code": type_code,
            "occurred_on": on,
            "gross_amount": amount,
            "description": "Test",
            "payment_method": "bank",
            "dimension_value_ids": values,
        },
    )


def _result(client: TestClient, company_id: int, dimension_id: int) -> dict[str, Any]:
    response = client.get(_path(company_id, f"/{dimension_id}/resultat?aar={THIS_YEAR}"))
    assert response.status_code == 200, response.text
    found: dict[str, Any] = response.json()
    return found


def _rows(result: dict[str, Any]) -> dict[str, tuple[str, str, str]]:
    return {
        row["value"]: (row["total"]["income"], row["total"]["costs"], row["total"]["result"])
        for row in result["rows"]
    }


def _two_departments(client: TestClient, company_id: int) -> tuple[int, int, int]:
    dimension = _dimension(client, company_id)
    shop = _value(client, company_id, dimension["id"], "10", "Butikk")
    workshop = _value(client, company_id, dimension["id"], "20", "Verksted")
    return dimension["id"], shop, workshop


# --- Setting them up ---------------------------------------------------------------------


def test_a_dimension_and_its_values_are_set_up_and_listed(client: TestClient) -> None:
    company = signed_in_company(client)
    assert client.get(_path(company["id"])).json() == []

    dimension_id, shop, _ = _two_departments(client, company["id"])
    listed = client.get(_path(company["id"])).json()
    assert [(d["code"], d["description"], d["kind"], d["without_label"]) for d in listed] == [
        ("AVDELING", "Avdeling", "AVDELING", "Uten avdeling")
    ]
    assert [(v["value"], v["description"], v["lines"]) for v in listed[0]["values"]] == [
        ("10", "Butikk", 0),
        ("20", "Verksted", 0),
    ]

    renamed = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/dimensjonsverdier/{shop}",
        json={"description": "Butikken"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()[0]["values"][0]["description"] == "Butikken"
    # The code never changes, and a body cannot change it.
    assert renamed.json()[0]["values"][0]["value"] == "10"
    assert dimension_id == renamed.json()[0]["id"]


def test_a_dimension_named_like_one_myntos_knows_is_recognised(client: TestClient) -> None:
    company = signed_in_company(client)
    made = _dimension(client, company["id"], code="a", description="Avdeling")
    # Upper-cased, and known as an avdeling by its name though its code is «A».
    assert (made["code"], made["kind"]) == ("A", "AVDELING")
    own = _dimension(client, company["id"], code="KUNDETYPE", description="Kundetype")
    assert own["kind"] is None


def test_duplicates_and_codes_saf_t_cannot_carry_are_refused(client: TestClient) -> None:
    company = signed_in_company(client)
    dimension_id, _, _ = _two_departments(client, company["id"])

    again = write(
        client, "POST", _path(company["id"]), json={"code": "AVDELING", "description": "Annet"}
    )
    assert again.status_code == 409
    assert again.json()["code"] == "duplicate_dimension"
    same_value = write(
        client,
        "POST",
        _path(company["id"], f"/{dimension_id}/verdier"),
        json={"value": "10", "description": "Igjen"},
    )
    assert same_value.status_code == 409
    assert same_value.json()["code"] == "duplicate_dimension"
    bad = write(client, "POST", _path(company["id"]), json={"code": "AV DEL", "description": "X"})
    assert bad.status_code == 422
    assert bad.json()["code"] == "invalid_dimension"


def test_a_value_nothing_is_posted_to_can_be_deleted(client: TestClient) -> None:
    company = signed_in_company(client)
    dimension_id, shop, workshop = _two_departments(client, company["id"])

    gone = write(
        client, "DELETE", f"/api/v1/companies/{company['id']}/dimensjonsverdier/{workshop}"
    )
    assert gone.status_code == 204, gone.text
    values = client.get(_path(company["id"])).json()[0]["values"]
    assert [v["id"] for v in values] == [shop]

    whole = write(client, "DELETE", _path(company["id"], f"/{dimension_id}"))
    assert whole.status_code == 204, whole.text
    assert client.get(_path(company["id"])).json() == []


def test_a_value_posted_to_is_switched_off_never_deleted(client: TestClient) -> None:
    company = signed_in_company(client)
    dimension_id, shop, _ = _two_departments(client, company["id"])
    assert _entry(client, company["id"], "kontorrekvisita", "125.00", [shop]).status_code == 201
    assert client.get(_path(company["id"])).json()[0]["values"][0]["lines"] == 1

    for url in (
        f"/api/v1/companies/{company['id']}/dimensjonsverdier/{shop}",
        _path(company["id"], f"/{dimension_id}"),
    ):
        refused = write(client, "DELETE", url)
        assert refused.status_code == 409, url
        assert refused.json()["code"] == "dimension_in_use"

    off = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/dimensjonsverdier/{shop}",
        json={"is_active": False},
    )
    assert off.status_code == 200, off.text
    refused = _entry(client, company["id"], "kontorrekvisita", "50.00", [shop])
    assert refused.status_code == 409
    assert refused.json()["code"] == "inactive_dimension"

    # The whole dimension switched off refuses every value in it.
    write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/dimensjonsverdier/{shop}",
        json={"is_active": True},
    )
    write(client, "PATCH", _path(company["id"], f"/{dimension_id}"), json={"is_active": False})
    refused = _entry(client, company["id"], "kontorrekvisita", "50.00", [shop])
    assert refused.status_code == 409
    assert refused.json()["code"] == "inactive_dimension"


def test_only_administrators_set_them_up_and_everyone_reads_them(client: TestClient) -> None:
    company = signed_in_company(client)
    dimension_id, shop, _ = _two_departments(client, company["id"])
    join_company(client, company["id"], email="medlem@example.no", role="member")

    assert client.get(_path(company["id"])).status_code == 200
    assert client.get(_path(company["id"], f"/{dimension_id}/resultat")).status_code == 200
    made = write(client, "POST", _path(company["id"]), json={"code": "P", "description": "P"})
    assert made.status_code == 403
    # Choosing a value on an entry is ordinary bookkeeping, which a member does.
    assert _entry(client, company["id"], "kontorrekvisita", "80.00", [shop]).status_code == 201


# --- On an entry ------------------------------------------------------------------------


def test_an_entry_carries_its_avdeling_through_correction_and_reversal(
    client: TestClient,
) -> None:
    company = signed_in_company(client)
    dimension_id, shop, workshop = _two_departments(client, company["id"])

    entry = _entry(client, company["id"], "salg_hoy_sats", "1250.00", [shop])
    assert entry.status_code == 201, entry.text
    assert entry.json()["dimension_value_ids"] == [shop]
    listed = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    assert listed[0]["dimension_value_ids"] == [shop]

    # Corrected to the workshop: the original is reversed with its avdeling, so
    # the shop nets to nothing and the workshop has the sale.
    corrected = write(
        client,
        "PUT",
        f"/api/v1/companies/{company['id']}/cashflow/{entry.json()['id']}",
        json={
            "type_code": "salg_hoy_sats",
            "occurred_on": f"{THIS_YEAR}-03-14",
            "gross_amount": "1250.00",
            "description": "Test",
            "payment_method": "bank",
            "dimension_value_ids": [workshop],
        },
    )
    assert corrected.status_code == 201, corrected.text
    assert corrected.json()["dimension_value_ids"] == [workshop]
    rows = _rows(_result(client, company["id"], dimension_id))
    assert rows["10"] == ("0.00", "0.00", "0.00")
    assert rows["20"] == ("1000.00", "0.00", "1000.00")

    reversed_ = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/cashflow/{corrected.json()['id']}/reversal",
    )
    assert reversed_.status_code == 201, reversed_.text
    assert reversed_.json()["dimension_value_ids"] == [workshop]
    result = _result(client, company["id"], dimension_id)
    assert _rows(result)["20"] == ("0.00", "0.00", "0.00")
    assert result["total"]["total"]["result"] == "0.00"


def test_another_companys_value_is_not_found(client: TestClient) -> None:
    company = signed_in_company(client)
    _, shop, _ = _two_departments(client, company["id"])
    sibling = create_company(client, company["organization_id"], orgnr=OTHER_ORGNR)

    refused = _entry(client, sibling["id"], "kontorrekvisita", "100.00", [shop])
    assert refused.status_code == 404
    assert refused.json()["code"] == "dimension_not_found"
    # The value's id gives nothing away under the other company's routes either.
    patched = write(
        client,
        "PATCH",
        f"/api/v1/companies/{sibling['id']}/dimensjonsverdier/{shop}",
        json={"description": "Overtatt"},
    )
    assert patched.status_code == 404


def test_one_value_per_dimension_on_a_line(client: TestClient) -> None:
    company = signed_in_company(client)
    dimension_id, shop, workshop = _two_departments(client, company["id"])
    project = _dimension(client, company["id"], code="PROSJEKT", description="Prosjekt")
    roof = _value(client, company["id"], project["id"], "P-1", "Nytt tak")

    two = _entry(client, company["id"], "kontorrekvisita", "100.00", [shop, workshop])
    assert two.status_code == 422
    assert two.json()["code"] == "invalid_dimension"
    # An avdeling and a prosjekt are two dimensions, and both stand.
    both = _entry(client, company["id"], "kontorrekvisita", "100.00", [shop, roof])
    assert both.status_code == 201, both.text
    assert sorted(both.json()["dimension_value_ids"]) == sorted([shop, roof])
    assert _rows(_result(client, company["id"], project["id"]))["P-1"][1] == "80.00"
    assert _rows(_result(client, company["id"], dimension_id))["10"][1] == "80.00"


def test_the_result_per_value_adds_up_to_the_year(client: TestClient) -> None:
    company = signed_in_company(client)
    dimension_id, shop, workshop = _two_departments(client, company["id"])
    for type_code, amount, values, on in (
        ("salg_hoy_sats", "2500.00", [shop], f"{THIS_YEAR}-01-10"),
        ("kontorrekvisita", "125.00", [shop], f"{THIS_YEAR}-02-10"),
        ("salg_hoy_sats", "1250.00", [workshop], f"{THIS_YEAR}-02-11"),
        # Nobody marked this one: it is «Uten avdeling».
        ("kontorrekvisita", "250.00", [], f"{THIS_YEAR}-03-01"),
    ):
        response = _entry(client, company["id"], type_code, amount, values, on=on)
        assert response.status_code == 201, response.text

    result = _result(client, company["id"], dimension_id)
    assert result["year"] == THIS_YEAR
    # Net of VAT: 2 500 is 2 000 of sales, 125 is 100 of office supplies.
    assert _rows(result) == {
        "10": ("2000.00", "100.00", "1900.00"),
        "20": ("1000.00", "0.00", "1000.00"),
    }
    assert result["unallocated"]["description"] == "Uten avdeling"
    assert result["unallocated"]["total"]["result"] == "-200.00"
    assert result["total"]["total"]["result"] == "2700.00"
    shop_row = next(row for row in result["rows"] if row["value"] == "10")
    assert [m["result"] for m in shop_row["months"][:3]] == ["2000.00", "-100.00", "0.00"]
    assert len(shop_row["months"]) == 12
    assert result["partly_allocated"] == 0

    total = sum(
        (Decimal(row["total"]["result"]) for row in result["rows"]),
        Decimal(result["unallocated"]["total"]["result"]),
    )
    assert total == Decimal(result["total"]["total"]["result"])


def test_a_settlement_puts_each_line_on_its_own_driftsgren(client: TestClient) -> None:
    company = farm_company(client)
    switched = write(
        client, "PUT", f"/api/v1/companies/{company['id']}/landbruk", json={"enabled": True}
    )
    assert switched.status_code == 200, switched.text
    added = write(
        client,
        "POST",
        _path(company["id"], "/forslag"),
        json={"kind": "DRIFTSGR", "codes": ["6110", "6300"]},
    )
    assert added.status_code == 200, added.text
    dimension = next(item for item in added.json() if item["code"] == "DRIFTSGR")
    ids = {value["value"]: value["id"] for value in dimension["values"]}

    lines = [dict(line) for line in MILK_SETTLEMENT]
    lines[0]["dimension_value_ids"] = [ids["6110"]]
    lines[3]["dimension_value_ids"] = [ids["6300"]]
    response = composite(client, company["id"], lines, description="Melk oktober")
    assert response.status_code == 201, response.text
    parts = response.json()["parts"]
    assert [part["dimension_value_ids"] for part in parts] == [[ids["6110"]], [], [], [ids["6300"]]]
    # The headline of a composite entry is no single line, and says so.
    assert response.json()["dimension_value_ids"] == []

    result = _result(client, company["id"], dimension["id"])
    rows = _rows(result)
    assert rows["6110"][0] == "10000.00"
    assert rows["6300"][1] == "4000.00"
    assert result["unallocated"]["description"] == "Uten driftsgren"


# --- NIBIO's lists, for a farm only -----------------------------------------------------


def test_a_company_that_is_not_a_farm_is_offered_nothing(client: TestClient) -> None:
    company = signed_in_company(client)
    offered = client.get(_path(company["id"], "/forslag")).json()
    assert offered == {"landbruk": False, "source": None, "sets": []}
    refused = write(
        client,
        "POST",
        _path(company["id"], "/forslag"),
        json={"kind": "DRIFTSGR", "codes": ["6110"]},
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "segment_not_enabled"


def test_a_farm_takes_nibios_codes_and_asking_twice_adds_nothing(client: TestClient) -> None:
    company = farm_company(client)
    write(client, "PUT", f"/api/v1/companies/{company['id']}/landbruk", json={"enabled": True})
    offered = client.get(_path(company["id"], "/forslag")).json()
    assert offered["landbruk"] is True
    assert offered["source"]
    sets = {item["kind"]: item for item in offered["sets"]}
    assert {"DRIFTSGR", "AVDELING"} <= set(sets)
    assert "6110" in {value["code"] for value in sets["DRIFTSGR"]["values"]}
    assert {value["code"] for value in sets["AVDELING"]["values"]} >= {"10", "20", "30"}

    body = {"kind": "AVDELING", "codes": ["20", "30"]}
    first = write(client, "POST", _path(company["id"], "/forslag"), json=body).json()
    second = write(client, "POST", _path(company["id"], "/forslag"), json=body).json()
    assert first == second
    made = next(item for item in second if item["code"] == "AVDELING")
    assert [(v["value"], v["description"]) for v in made["values"]] == [
        ("20", "Jordbruk"),
        ("30", "Skogbruk"),
    ]
    again = client.get(_path(company["id"], "/forslag")).json()
    added = {v["code"] for s in again["sets"] for v in s["values"] if v["added"]}
    assert added == {"20", "30"}

    unknown = write(
        client,
        "POST",
        _path(company["id"], "/forslag"),
        json={"kind": "DRIFTSGR", "codes": ["9999"]},
    )
    assert unknown.status_code == 422
    assert unknown.json()["code"] == "invalid_dimension"


# --- The SAF-T file ---------------------------------------------------------------------


def test_the_saf_t_file_carries_each_lines_avdeling(client: TestClient, tmp_path: Path) -> None:
    company = signed_in_company(client)
    _, shop, workshop = _two_departments(client, company["id"])
    assert _entry(client, company["id"], "salg_hoy_sats", "2500.00", [shop]).status_code == 201
    assert _entry(client, company["id"], "kontorrekvisita", "125.00", []).status_code == 201
    write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/dimensjonsverdier/{workshop}",
        json={"is_active": False},
    )

    checked = client.get(f"/api/v1/companies/{company['id']}/saft/kontroll?aar={THIS_YEAR}")
    assert checked.json()["exportable"] is True, checked.json()["findings"]
    response = client.get(f"/api/v1/companies/{company['id']}/saft/fil?aar={THIS_YEAR}")
    assert response.status_code == 200, response.text
    _validate(response.content, checked.json()["version"], tmp_path)

    root = _parse(response.content)
    table = {
        entry.findtext("s:AnalysisID", namespaces=NS): (
            entry.findtext("s:AnalysisType", namespaces=NS),
            entry.findtext("s:AnalysisIDDescription", namespaces=NS),
            entry.findtext("s:Status", namespaces=NS),
        )
        for entry in root.findall(".//s:AnalysisTypeTable/s:AnalysisTypeTableEntry", NS)
    }
    assert table == {
        "10": ("AVDELING", "Butikk", "Active"),
        "20": ("AVDELING", "Verksted", "Closed"),
    }
    # The sale's revenue line carries the shop for its whole amount; the bank
    # and VAT lines carry nothing, and neither does the unmarked purchase.
    marked = [
        (
            line.findtext("s:AccountID", namespaces=NS),
            line.findtext("s:Analysis/s:AnalysisID", namespaces=NS),
            line.findtext("s:Analysis/s:CreditAnalysisAmount/s:Amount", namespaces=NS),
        )
        for line in root.iter(f"{{{NAMESPACE}}}Line")
        if line.find("s:Analysis", NS) is not None
    ]
    assert marked == [("3000", "10", "2000.00")]
