"""Bank import and reconciliation, end to end through the API.

The real application on a real SQLite file, so the import exercises the parser,
the archive, the dedup constraint and the register's own posting path together.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import join_company, register, write

FIXTURES = Path(__file__).parent / "fixtures" / "bank"
MARCH = (FIXTURES / "camt053_v02_mars.xml").read_bytes()
APRIL = (FIXTURES / "camt053_v08_april.xml").read_bytes()

ACCOUNT = "15062033158"
# The fixtures are dated 2026, so the books must start then whatever year the
# suite runs in.
YEAR = 2026


def _company(
    client: TestClient,
    *,
    orgnr: str = "910000004",
    bank_account: str = ACCOUNT,
    session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session = session or register(client)
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": session["memberships"][0]["organization_id"],
            "orgnr": orgnr,
            "name": "Klient En AS",
            "first_financial_year": YEAR,
            "vat_registered": True,
            "bank_account": bank_account,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _import(client: TestClient, company_id: int, content: bytes, name: str = "mars.xml") -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/bank/kontoutskrifter",
        files={"file": (name, content, "application/xml")},
    )


def _lines(client: TestClient, company_id: int, status: str = "alle") -> list[dict[str, Any]]:
    response = client.get(f"/api/v1/companies/{company_id}/bank/linjer", params={"status": status})
    assert response.status_code == 200, response.text
    body: list[dict[str, Any]] = response.json()
    return body


def _line(lines: list[dict[str, Any]], amount: str) -> dict[str, Any]:
    return next(line for line in lines if line["amount"] == amount)


def _voucher(client: TestClient, company_id: int, **overrides: Any) -> dict[str, Any]:
    payload = {
        "type_code": "kontorrekvisita",
        "occurred_on": f"{YEAR}-03-14",
        "gross_amount": "1250.00",
        "description": "Kontorland, ordre 4711",
        **overrides,
    }
    response = write(client, "POST", f"/api/v1/companies/{company_id}/cashflow", json=payload)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# --- Import ---------------------------------------------------------------------------


def test_a_statement_is_imported_and_archived(client: TestClient) -> None:
    company = _company(client)
    response = _import(client, company["id"], MARCH)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["already_imported"] is False
    (statement,) = body["statements"]
    assert statement["company_id"] == company["id"]
    assert statement["account"] == ACCOUNT
    assert statement["lines_new"] == 3
    assert statement["opening_balance"] == "10000.00"
    assert statement["closing_balance"] == "21201.00"
    assert statement["camt_version"] == "001.02"

    # The file is kept byte for byte: it is the bank's own record.
    stored = client.get(
        f"/api/v1/companies/{company['id']}/bank/kontoutskrifter/{statement['id']}/fil"
    )
    assert stored.status_code == 200
    assert stored.content == MARCH
    assert "attachment" in stored.headers["content-disposition"]

    lines = _lines(client, company["id"])
    assert len(lines) == 3
    incoming = _line(lines, "12500.00")
    assert incoming["direction"] == "inn"
    assert incoming["kid"] == "000100000100234"
    assert incoming["voucher_id"] is None


def test_the_same_file_twice_is_one_import(client: TestClient) -> None:
    company = _company(client)
    first = _import(client, company["id"], MARCH).json()
    again = _import(client, company["id"], MARCH)
    assert again.status_code == 201, again.text
    assert again.json()["already_imported"] is True
    assert [s["id"] for s in again.json()["statements"]] == [s["id"] for s in first["statements"]]
    assert len(_lines(client, company["id"])) == 3
    listed = client.get(f"/api/v1/companies/{company['id']}/bank/kontoutskrifter").json()
    assert len(listed) == 1


def test_an_overlapping_statement_adds_only_what_is_new(client: TestClient) -> None:
    company = _company(client)
    _import(client, company["id"], MARCH)
    response = _import(client, company["id"], APRIL, "april.xml")
    assert response.status_code == 201, response.text
    body = response.json()
    (statement,) = body["statements"]
    # Two repeated under the same reference; two identical fees and a deposit
    # are new; one pending entry is left out.
    assert statement["lines_known"] == 2
    assert statement["lines_new"] == 3
    assert body["skipped_unbooked"] == 1
    lines = _lines(client, company["id"])
    assert len(lines) == 6
    assert sum(1 for line in lines if line["amount"] == "25.00") == 2

    # And the overlap the other way round is just as idempotent.
    other = _company(client, orgnr="910000012", session=register(client, "per@example.no"))
    _import(client, other["id"], APRIL, "april.xml")
    _import(client, other["id"], MARCH)
    assert len(_lines(client, other["id"])) == 6


def test_a_statement_that_does_not_add_up_stores_nothing(client: TestClient) -> None:
    company = _company(client)
    broken = MARCH.replace(b">21201.00<", b">21200.00<")
    response = _import(client, company["id"], broken)
    assert response.status_code == 422
    assert response.json()["code"] == "unbalanced_bank_statement"
    assert _lines(client, company["id"]) == []
    assert client.get(f"/api/v1/companies/{company['id']}/bank/kontoutskrifter").json() == []


def test_a_foreign_currency_statement_is_refused(client: TestClient) -> None:
    company = _company(client)
    euro = MARCH.replace(b'<Amt Ccy="NOK">49.00</Amt>', b'<Amt Ccy="EUR">49.00</Amt>')
    response = _import(client, company["id"], euro)
    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_statement_currency"


def test_an_account_the_company_does_not_have_is_refused(client: TestClient) -> None:
    company = _company(client, bank_account="98765432103")
    response = _import(client, company["id"], MARCH)
    assert response.status_code == 422
    assert response.json()["code"] == "unknown_statement_account"
    assert "1506.20.33158" in response.json()["detail"]
    assert _lines(client, company["id"]) == []


def test_a_known_reference_with_new_contents_is_refused(client: TestClient) -> None:
    company = _company(client)
    _import(client, company["id"], MARCH)
    changed = APRIL.replace(b">1250.00<", b">1350.00<").replace(b">21951.00<", b">21851.00<")
    response = _import(client, company["id"], changed, "april.xml")
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "bank_statement_conflict"
    assert len(_lines(client, company["id"])) == 3


def test_a_viewer_cannot_import(client: TestClient) -> None:
    company = _company(client)
    join_company(client, company["id"], email="revisor@example.no", role="viewer")
    response = _import(client, company["id"], MARCH)
    assert response.status_code == 403
    assert _lines(client, company["id"]) == []


# --- Reconciliation -------------------------------------------------------------------


def test_a_posted_voucher_is_suggested_and_confirmed_without_being_touched(
    client: TestClient,
) -> None:
    company = _company(client)
    voucher = _voucher(client, company["id"])
    _import(client, company["id"], MARCH)

    line = _line(_lines(client, company["id"], "uavstemt"), "1250.00")
    (suggestion,) = line["suggestions"]
    assert suggestion["voucher_id"] == voucher["id"]
    assert suggestion["confidence"] == "sterk"
    assert "Navnet i banken stemmer med bilaget" in suggestion["reasons"]
    # Nothing else in the window has the same amount and direction.
    assert _line(_lines(client, company["id"]), "49.00")["suggestions"] == []

    before = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    confirmed = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/bank/linjer/{line['id']}/avstemming",
        json={"voucher_id": voucher["id"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["voucher_id"] == voucher["id"]
    assert confirmed.json()["voucher_no"] == voucher["voucher_no"]
    assert confirmed.json()["match_kind"] == "bekreftet"

    # The register is exactly as it was: matching is a link, never a posting.
    assert client.get(f"/api/v1/companies/{company['id']}/cashflow").json() == before
    assert [entry["id"] for entry in _lines(client, company["id"], "avstemt")] == [line["id"]]

    again = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/bank/linjer/{line['id']}/avstemming",
        json={"voucher_id": voucher["id"]},
    )
    assert again.status_code == 409
    assert again.json()["code"] == "bank_line_already_matched"

    # A matched voucher is no longer offered for another line.
    for open_line in _lines(client, company["id"], "uavstemt"):
        assert all(s["voucher_id"] != voucher["id"] for s in open_line["suggestions"])


def test_a_voucher_that_does_not_explain_the_line_is_refused(client: TestClient) -> None:
    company = _company(client)
    voucher = _voucher(client, company["id"])
    _import(client, company["id"], MARCH)
    fee = _line(_lines(client, company["id"]), "49.00")
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/bank/linjer/{fee['id']}/avstemming",
        json={"voucher_id": voucher["id"]},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "bank_match_mismatch"


def test_a_cash_payment_is_not_the_booking_of_a_bank_line(client: TestClient) -> None:
    company = _company(client)
    cash = _voucher(client, company["id"], payment_method="kontant")
    _import(client, company["id"], MARCH)
    line = _line(_lines(client, company["id"]), "1250.00")
    assert line["suggestions"] == []
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/bank/linjer/{line['id']}/avstemming",
        json={"voucher_id": cash["id"]},
    )
    assert response.status_code == 422


def test_a_reversed_voucher_is_neither_suggested_nor_left_looking_matched(
    client: TestClient,
) -> None:
    company = _company(client)
    voucher = _voucher(client, company["id"])
    _import(client, company["id"], MARCH)
    line = _line(_lines(client, company["id"]), "1250.00")
    write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/bank/linjer/{line['id']}/avstemming",
        json={"voucher_id": voucher["id"]},
    )
    reversal = write(
        client, "POST", f"/api/v1/companies/{company['id']}/cashflow/{voucher['id']}/reversal"
    )
    assert reversal.status_code == 201
    assert _line(_lines(client, company["id"]), "1250.00")["voucher_reversed"] is True

    undone = write(
        client,
        "DELETE",
        f"/api/v1/companies/{company['id']}/bank/linjer/{line['id']}/avstemming",
    )
    assert undone.status_code == 200, undone.text
    assert undone.json()["voucher_id"] is None
    # Neither the reversed voucher nor its reversal is the booking now.
    assert undone.json()["suggestions"] == []


def test_a_new_voucher_is_posted_from_an_unexplained_line(client: TestClient) -> None:
    company = _company(client)
    _import(client, company["id"], MARCH)
    fee = _line(_lines(client, company["id"]), "49.00")

    wrong_way = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/bank/linjer/{fee['id']}/bilag",
        json={"type_code": "salg_hoy_sats", "description": "Gebyr"},
    )
    assert wrong_way.status_code == 422
    assert wrong_way.json()["code"] == "bank_match_mismatch"

    created = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/bank/linjer/{fee['id']}/bilag",
        json={"type_code": "bankgebyr", "description": "Kontohold mars"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["match_kind"] == "opprettet"
    voucher_id = created.json()["voucher_id"]

    (entry,) = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    assert entry["id"] == voucher_id
    # Amount, date and direction are the bank's; the type decides the account
    # and the VAT, exactly as on the register's own form.
    assert entry["gross_amount"] == "49.00"
    assert entry["occurred_on"] == f"{YEAR}-03-31"
    assert entry["direction"] == "ut"
    assert entry["account_number"] == "7770"
    assert entry["payment_account"] == "1920"

    again = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/bank/linjer/{fee['id']}/bilag",
        json={"type_code": "bankgebyr", "description": "Kontohold mars"},
    )
    assert again.status_code == 409


def test_vat_on_a_line_follows_the_type_catalogue(client: TestClient) -> None:
    company = _company(client)
    _import(client, company["id"], MARCH)
    purchase = _line(_lines(client, company["id"]), "1250.00")
    created = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/bank/linjer/{purchase['id']}/bilag",
        json={"type_code": "kontorrekvisita", "description": "Kontorland ordre 4711"},
    )
    assert created.status_code == 201, created.text
    (entry,) = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    assert entry["net_amount"] == "1000.00"
    assert entry["vat_amount"] == "250.00"
    assert entry["tax_code"] == "1"


# --- The company wall -----------------------------------------------------------------


def test_another_companys_statement_and_lines_are_not_found(client: TestClient) -> None:
    mine = _company(client)
    _import(client, mine["id"], MARCH)
    statement = client.get(f"/api/v1/companies/{mine['id']}/bank/kontoutskrifter").json()[0]
    line = _lines(client, mine["id"])[0]

    # Somebody else, signed in on the same client: the ids are real, the books
    # are not theirs.
    register(client, "per@example.no", full_name="Per Hansen")
    assert client.get(f"/api/v1/companies/{mine['id']}/bank/linjer").status_code == 404
    assert (
        client.get(
            f"/api/v1/companies/{mine['id']}/bank/kontoutskrifter/{statement['id']}/fil"
        ).status_code
        == 404
    )
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{mine['id']}/bank/linjer/{line['id']}/bilag",
        json={"type_code": "bankgebyr", "description": "x"},
    )
    assert response.status_code == 404


def test_a_sibling_companys_voucher_cannot_be_linked(client: TestClient) -> None:
    """The voucher id travels in the body, where no routing layer looks at it."""
    session = register(client)
    mine = _company(client, session=session)
    theirs = _company(client, orgnr="910000012", session=session)
    voucher = _voucher(client, theirs["id"])
    _import(client, mine["id"], MARCH)
    line = _line(_lines(client, mine["id"]), "1250.00")
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{mine['id']}/bank/linjer/{line['id']}/avstemming",
        json={"voucher_id": voucher["id"]},
    )
    assert response.status_code == 404
    # Nor is it offered: the sibling's voucher is not in this company's books.
    assert line["suggestions"] == []


def test_the_same_file_in_two_companies_is_two_imports(client: TestClient) -> None:
    session = register(client)
    first = _company(client, session=session)
    second = _company(client, orgnr="910000012", session=session)
    assert _import(client, first["id"], MARCH).json()["already_imported"] is False
    assert _import(client, second["id"], MARCH).json()["already_imported"] is False
    assert len(_lines(client, first["id"])) == 3
    assert len(_lines(client, second["id"])) == 3
