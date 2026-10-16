"""Manual vouchers (manuelt bilag), end to end.

Real postings through the API on SQLite, with synthetic books: a review that
stores nothing, a post that runs the same review again, VAT that Myntos splits
so the MVA-melding and 2700/2710 agree, a written reason on every voucher and
on every undoing of one, and corrections as a reversal plus a linked
replacement, from this journal's own routes only.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import agent_header, join_company, register, write
from tests.test_arsoppgjor_api import LAST_YEAR, _books, _close, _saft
from tests.test_cashflow_api import (
    OTHER_ORGNR,
    PDF_BYTES,
    THIS_YEAR,
    create_company,
    record,
    signed_in_company,
)
from tests.test_saft_api import NS

REASON = "Påløpt kostnad for mars, fakturert i april. Beløpet er fra leverandørens tilbud."


def _path(company_id: int, tail: str = "") -> str:
    return f"/api/v1/companies/{company_id}/manuelt-bilag{tail}"


def _line(account: str, debit: str = "0", credit: str = "0", **extra: Any) -> dict[str, Any]:
    return {"account_number": account, "debit": debit, "credit": credit, **extra}


def _voucher(
    lines: list[dict[str, Any]],
    *,
    on: date | None = None,
    description: str = "Periodisering",
    explanation: str = REASON,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "occurred_on": (on or date(THIS_YEAR, 3, 31)).isoformat(),
        "description": description,
        "explanation": explanation,
        "lines": lines,
        **extra,
    }


def _review(client: TestClient, company_id: int, body: dict[str, Any]) -> dict[str, Any]:
    response = write(client, "POST", _path(company_id, "/kontroll"), json=body)
    assert response.status_code == 200, response.text
    found: dict[str, Any] = response.json()
    return found


def _post(client: TestClient, company_id: int, body: dict[str, Any]) -> Any:
    return write(client, "POST", _path(company_id), json=body)


def _codes(review: dict[str, Any], severity: str | None = None) -> set[str]:
    return {f["code"] for f in review["findings"] if severity is None or f["severity"] == severity}


def _party(client: TestClient, company_id: int, party_type: str, name: str) -> int:
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/parties",
        json={"party_type": party_type, "name": name},
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


_ACCRUAL = [_line("6800", debit="4000.00"), _line("2400", credit="4000.00")]


# --- The review --------------------------------------------------------------------------


def test_the_review_shows_the_voucher_and_stores_nothing(client: TestClient) -> None:
    company = signed_in_company(client)
    supplier = _party(client, company["id"], "leverandor", "Kontorleverandøren AS")
    lines = [
        _line("6800", debit="4000.00", description="Rekvisita mars"),
        _line("2400", credit="4000.00", party_id=supplier),
    ]
    review = _review(client, company["id"], _voucher(lines))

    assert review["postable"] is True, review["findings"]
    assert review["year"] == THIS_YEAR
    assert Decimal(review["total_debit"]) == Decimal(review["total_credit"]) == Decimal("4000.00")
    assert [(li["account_number"], li["debit"], li["credit"]) for li in review["lines"]] == [
        ("6800", "4000.00", "0.00"),
        ("2400", "0.00", "4000.00"),
    ]
    assert review["lines"][1]["party_name"] == "Kontorleverandøren AS"
    # A line without its own text is filed under the voucher's.
    assert review["lines"][1]["description"] == "Periodisering"
    listed = client.get(_path(company["id"])).json()
    assert listed["entries"] == []


def test_the_review_refuses_what_cannot_be_posted(client: TestClient) -> None:
    company = signed_in_company(client)
    review = _review(
        client,
        company["id"],
        _voucher(
            [
                _line("6800", debit="100.00"),
                _line("2700", credit="25.00"),
                _line("9999", credit="50.00"),
                _line("8800", credit="10.00"),
                _line("2050", debit="1.005"),
            ],
            description="  ",
            explanation="kort",
        ),
    )
    assert review["postable"] is False
    assert {
        "mangler_tekst",
        "mangler_begrunnelse",
        "mva_konto",
        "ukjent_konto",
        "aarsavslutningskonto",
        "ugyldig_belop",
        "ubalanse",
    } <= _codes(review, "feil")


def test_one_line_is_not_a_voucher(client: TestClient) -> None:
    company = signed_in_company(client)
    review = _review(client, company["id"], _voucher([_line("6800", debit="1.00")]))
    assert "for_faa_linjer" in _codes(review, "feil")


def test_a_deactivated_account_is_refused_as_the_register_refuses_it(client: TestClient) -> None:
    company = signed_in_company(client)
    chart = client.get(f"/api/v1/companies/{company['id']}/kontoplan?aar={THIS_YEAR}").json()
    phone = next(a for a in chart["accounts"] if a["number"] == "6900")
    off = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/kontoplan/{phone['id']}",
        json={"is_active": False},
    )
    assert off.status_code == 200, off.text
    review = _review(
        client,
        company["id"],
        _voucher([_line("6900", debit="10.00"), _line("2400", credit="10.00")]),
    )
    assert "deaktivert_konto" in _codes(review, "feil")


def test_a_party_belongs_on_its_own_balance_account(client: TestClient) -> None:
    company = signed_in_company(client)
    supplier = _party(client, company["id"], "leverandor", "Leverandør AS")
    wrong = _review(
        client,
        company["id"],
        _voucher([_line("1500", debit="10.00", party_id=supplier), _line("3000", credit="10.00")]),
    )
    assert "part_paa_feil_konto" in _codes(wrong, "feil")

    unsplit = _review(
        client,
        company["id"],
        _voucher([_line("1500", debit="10.00"), _line("3000", credit="10.00")]),
    )
    assert unsplit["postable"] is True
    assert "uten_part" in _codes(unsplit, "merknad")


def test_the_bank_account_is_allowed_and_noted(client: TestClient) -> None:
    """Moving a balance onto 1920 is a reclassification, and the review says where payments go."""
    company = signed_in_company(client)
    review = _review(
        client,
        company["id"],
        _voucher(
            [_line("1920", debit="15000.00"), _line("2220", credit="15000.00")],
            description="Omklassifisering driftskreditt",
        ),
    )
    assert review["postable"] is True
    assert "betalingskonto" in _codes(review, "merknad")


def test_a_year_before_the_books_and_a_closed_year_are_refused(client: TestClient) -> None:
    company = _books(client)
    before = _review(
        client,
        company["id"],
        _voucher(_ACCRUAL, on=date(LAST_YEAR - 1, 12, 31)),
    )
    assert "foer_forste_aar" in _codes(before, "feil")

    assert _close(client, company["id"]).status_code == 201
    closed = _review(client, company["id"], _voucher(_ACCRUAL, on=date(LAST_YEAR, 12, 31)))
    assert "aar_avsluttet" in _codes(closed, "feil")
    response = _post(client, company["id"], _voucher(_ACCRUAL, on=date(LAST_YEAR, 12, 31)))
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_manual_voucher"


# --- Posting -----------------------------------------------------------------------------


def test_a_posted_voucher_is_numbered_in_its_own_journal_with_its_reason(
    client: TestClient,
) -> None:
    company = signed_in_company(client)
    first = _post(client, company["id"], _voucher(_ACCRUAL))
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["journal"] == "MANUELT"
    assert body["voucher_no"] == f"{THIS_YEAR}-0001"
    assert body["explanation"] == REASON
    assert body["created_by_name"] == "Kari Nordmann"
    assert body["total"] == "4000.00"
    second = _post(client, company["id"], _voucher(_ACCRUAL))
    assert second.json()["voucher_no"] == f"{THIS_YEAR}-0002"

    one = client.get(_path(company["id"], f"/{body['id']}"))
    assert one.status_code == 200
    assert one.json()["explanation"] == REASON
    listed = client.get(_path(company["id"])).json()
    assert [e["voucher_no"] for e in listed["entries"]] == [
        f"{THIS_YEAR}-0002",
        f"{THIS_YEAR}-0001",
    ]
    assert listed["closed"] is False


def test_the_post_runs_the_review_again(client: TestClient) -> None:
    company = signed_in_company(client)
    response = _post(
        client,
        company["id"],
        _voucher([_line("6800", debit="10.00"), _line("2400", credit="9.00")], explanation=""),
    )
    assert response.status_code == 422
    codes = {f["code"] for f in response.json()["findings"]}
    assert {"ubalanse", "mangler_begrunnelse"} <= codes
    assert client.get(_path(company["id"])).json()["entries"] == []


def test_a_manual_voucher_stays_out_of_the_register_and_the_register_cannot_reverse_it(
    client: TestClient,
) -> None:
    company = signed_in_company(client)
    posted = _post(
        client,
        company["id"],
        _voucher(
            [_line("1920", debit="500.00"), _line("2050", credit="500.00")],
            description="Omklassifisering",
        ),
    ).json()

    register_list = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    assert posted["id"] not in {entry["id"] for entry in register_list}
    # It is on the bank account all the same, so the position counts it.
    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    assert summary["cash_position"] == "500.00"
    by_type = {item["type_code"]: item for item in summary["by_type"]}
    assert by_type["manuelt"]["type_label"] == "Manuelt bilag"

    refused = write(
        client, "POST", f"/api/v1/companies/{company['id']}/cashflow/{posted['id']}/reversal"
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "cannot_reverse_here"


# --- VAT ---------------------------------------------------------------------------------


def test_vat_is_split_from_the_amount_and_the_melding_agrees_with_the_accounts(
    client: TestClient,
) -> None:
    company = signed_in_company(client)
    supplier = _party(client, company["id"], "leverandor", "Rekvisita AS")
    customer = _party(client, company["id"], "kunde", "Kunde AS")
    response = _post(
        client,
        company["id"],
        _voucher(
            [
                _line("6800", debit="1250.00", tax_code="1"),
                _line("2400", credit="1250.00", party_id=supplier),
                _line("1500", debit="625.00", party_id=customer),
                _line("3000", credit="625.00", tax_code="3"),
            ],
            on=date(THIS_YEAR, 3, 14),
            description="Rettet mva",
        ),
    )
    assert response.status_code == 201, response.text
    lines = response.json()["lines"]
    assert [
        (li["account_number"], li["debit"], li["credit"], li["tax_code"], li["generated"])
        for li in lines
    ] == [
        ("6800", "1000.00", "0.00", "1", False),
        ("2710", "250.00", "0.00", None, True),
        ("2400", "0.00", "1250.00", None, False),
        ("1500", "625.00", "0.00", None, False),
        ("3000", "0.00", "500.00", "3", False),
        ("2700", "0.00", "125.00", None, True),
    ]
    assert lines[0]["vat_amount"] == "250.00"
    # Read back as typed, for a correction to start from.
    typed = response.json()["input_lines"]
    assert [(li["account_number"], li["debit"], li["credit"], li["tax_code"]) for li in typed] == [
        ("6800", "1250.00", "0.00", "1"),
        ("2400", "0.00", "1250.00", None),
        ("1500", "625.00", "0.00", None),
        ("3000", "0.00", "625.00", "3"),
    ]

    termin = client.get(
        f"/api/v1/companies/{company['id']}/mva/termin", params={"aar": THIS_YEAR, "termin": 2}
    ).json()
    assert Decimal(termin["output_vat"]) == Decimal("125.00")
    assert Decimal(termin["input_vat"]) == Decimal("250.00")
    assert Decimal(termin["reconciliation"]["difference"]) == Decimal("0.00")


def test_reverse_charge_posts_both_sides_and_reads_back_as_typed(client: TestClient) -> None:
    company = signed_in_company(client)
    response = _post(
        client,
        company["id"],
        _voucher(
            [_line("6800", debit="1000.00", tax_code="86"), _line("2400", credit="1000.00")],
            on=date(THIS_YEAR, 3, 14),
        ),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert [(li["account_number"], li["debit"], li["credit"]) for li in body["lines"]] == [
        ("6800", "1000.00", "0.00"),
        ("2700", "0.00", "250.00"),
        ("2710", "250.00", "0.00"),
        ("2400", "0.00", "1000.00"),
    ]
    assert body["input_lines"][0]["debit"] == "1000.00"
    termin = client.get(
        f"/api/v1/companies/{company['id']}/mva/termin", params={"aar": THIS_YEAR, "termin": 2}
    ).json()
    assert Decimal(termin["reconciliation"]["difference"]) == Decimal("0.00")
    assert Decimal(termin["fastsatt_merverdiavgift"]) == Decimal("0.00")


def test_codes_that_need_the_register_are_refused(client: TestClient) -> None:
    company = signed_in_company(client)
    for code in ("14", "87"):
        review = _review(
            client,
            company["id"],
            _voucher(
                [_line("6800", debit="100.00", tax_code=code), _line("2400", credit="100.00")]
            ),
        )
        assert "ugyldig_mva_kode" in _codes(review, "feil"), code
    listed = client.get(_path(company["id"])).json()
    offered = {item["code"] for item in listed["tax_codes"]}
    assert {"0", "1", "3", "86"} <= offered
    assert not {"14", "15", "87"} & offered


def test_an_unregistered_company_gets_no_deduction(client: TestClient) -> None:
    session = register(client)
    company = create_company(
        client, session["memberships"][0]["organization_id"], vat_registered=False
    )
    review = _review(
        client,
        company["id"],
        _voucher([_line("6800", debit="125.00", tax_code="1"), _line("2400", credit="125.00")]),
    )
    assert "ugyldig_mva_kode" in _codes(review, "feil")
    offered = {item["code"] for item in client.get(_path(company["id"])).json()["tax_codes"]}
    assert "1" not in offered
    assert "0" in offered


# --- Reversal and correction -------------------------------------------------------------


def test_a_reversal_needs_its_own_reason_and_happens_once(client: TestClient) -> None:
    company = signed_in_company(client)
    posted = _post(client, company["id"], _voucher(_ACCRUAL)).json()
    url = _path(company["id"], f"/{posted['id']}/reversering")

    no_reason = write(client, "POST", url, json={"explanation": ""})
    assert no_reason.status_code == 422
    assert no_reason.json()["findings"][0]["code"] == "mangler_begrunnelse"

    reversed_ = write(client, "POST", url, json={"explanation": "Ført to ganger ved en feil."})
    assert reversed_.status_code == 201, reversed_.text
    reversal = reversed_.json()
    assert reversal["is_reversal"] is True
    assert reversal["reverses_id"] == posted["id"]
    assert reversal["explanation"] == "Ført to ganger ved en feil."
    assert [(li["account_number"], li["credit"]) for li in reversal["lines"]] == [
        ("6800", "4000.00"),
        ("2400", "0.00"),
    ]

    again = write(client, "POST", url, json={"explanation": "Ført to ganger ved en feil."})
    assert again.status_code == 409
    assert again.json()["code"] == "already_reversed"
    of_reversal = write(
        client,
        "POST",
        _path(company["id"], f"/{reversal['id']}/reversering"),
        json={"explanation": "Reverserer reverseringen."},
    )
    assert of_reversal.status_code == 409
    assert of_reversal.json()["code"] == "cannot_reverse_here"

    original = client.get(_path(company["id"], f"/{posted['id']}")).json()
    assert original["reversed_by_id"] == reversal["id"]
    # The original's reason is untouched: documentation is never edited.
    assert original["explanation"] == REASON


def test_a_correction_is_a_reversal_and_a_linked_replacement(client: TestClient) -> None:
    company = signed_in_company(client)
    posted = _post(client, company["id"], _voucher(_ACCRUAL)).json()
    corrected_lines = [_line("6800", debit="4500.00"), _line("2400", credit="4500.00")]
    reason = "Beløpet var feil; fakturaen viste 4 500 kroner."

    review = _review(
        client,
        company["id"],
        _voucher(corrected_lines, explanation=reason, corrects_id=posted["id"]),
    )
    assert review["postable"] is True, review["findings"]
    assert review["corrects_id"] == posted["id"]

    response = write(
        client,
        "PUT",
        _path(company["id"], f"/{posted['id']}"),
        json=_voucher(corrected_lines, explanation=reason),
    )
    assert response.status_code == 201, response.text
    replacement = response.json()
    assert replacement["corrects_id"] == posted["id"]
    assert replacement["explanation"] == reason

    entries = {e["id"]: e for e in client.get(_path(company["id"])).json()["entries"]}
    assert len(entries) == 3
    original = entries[posted["id"]]
    assert original["corrected_by_id"] == replacement["id"]
    reversal = entries[original["reversed_by_id"]]
    # The two pieces of documentation name each other (bokføringsforskriften § 5-12).
    assert reversal["explanation"].startswith(f"Rettet med bilag {replacement['voucher_no']}")

    twice = write(
        client,
        "PUT",
        _path(company["id"], f"/{posted['id']}"),
        json=_voucher(corrected_lines, explanation=reason),
    )
    assert twice.status_code == 422
    assert "allerede_reversert" in {f["code"] for f in twice.json()["findings"]}


def test_a_voucher_in_a_closed_year_is_not_reversed_into_it(client: TestClient) -> None:
    company = _books(client)
    posted = _post(client, company["id"], _voucher(_ACCRUAL, on=date(LAST_YEAR, 12, 31))).json()
    assert _close(client, company["id"]).status_code == 201
    refused = write(
        client,
        "POST",
        _path(company["id"], f"/{posted['id']}/reversering"),
        json={"explanation": "Skulle vært ført i år."},
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "closed_financial_year"
    review = _review(
        client,
        company["id"],
        _voucher(_ACCRUAL, on=date(THIS_YEAR, 1, 1), corrects_id=posted["id"]),
    )
    assert "originalens_aar_avsluttet" in _codes(review, "feil")


# --- Documents, roles, keys and the company wall -------------------------------------------


def test_a_manual_voucher_carries_documents_in_the_archive(client: TestClient) -> None:
    company = signed_in_company(client)
    posted = _post(client, company["id"], _voucher(_ACCRUAL)).json()
    uploaded = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/cashflow/{posted['id']}/dokument",
        files={"file": ("varetelling.pdf", PDF_BYTES, "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    read = client.get(_path(company["id"], f"/{posted['id']}")).json()
    assert [a["filename"] for a in read["attachments"]] == ["varetelling.pdf"]


def test_a_viewer_reads_but_does_not_post(client: TestClient) -> None:
    company = signed_in_company(client)
    _post(client, company["id"], _voucher(_ACCRUAL))
    join_company(client, company["id"], email="revisor@example.no", role="viewer")
    assert len(client.get(_path(company["id"])).json()["entries"]) == 1
    assert _post(client, company["id"], _voucher(_ACCRUAL)).status_code == 403
    review = write(client, "POST", _path(company["id"], "/kontroll"), json=_voucher(_ACCRUAL))
    assert review.status_code == 403


def test_a_key_reviews_at_once_and_waits_to_post(client: TestClient) -> None:
    company = signed_in_company(client)
    header = agent_header(
        client, company["id"], scopes=("read", "write"), approval_scopes=("post",)
    )
    review = client.post(_path(company["id"], "/kontroll"), json=_voucher(_ACCRUAL), headers=header)
    assert review.status_code == 200, review.text
    held = client.post(_path(company["id"]), json=_voucher(_ACCRUAL), headers=header)
    assert held.status_code == 202, held.text
    assert held.json()["code"] == "approval_pending"
    assert client.get(_path(company["id"])).json()["entries"] == []


def test_another_companys_manual_voucher_is_not_found(client: TestClient) -> None:
    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    mine = create_company(client, organization_id)
    theirs = create_company(client, organization_id, orgnr=OTHER_ORGNR, name="Søster AS")
    their_voucher = _post(client, theirs["id"], _voucher(_ACCRUAL)).json()
    assert client.get(_path(theirs["id"], f"/{their_voucher['id']}")).status_code == 200

    assert client.get(_path(mine["id"], f"/{their_voucher['id']}")).status_code == 404
    assert (
        write(
            client,
            "POST",
            _path(mine["id"], f"/{their_voucher['id']}/reversering"),
            json={"explanation": "Prøver søsterselskapet."},
        ).status_code
        == 404
    )
    assert (
        write(
            client, "PUT", _path(mine["id"], f"/{their_voucher['id']}"), json=_voucher(_ACCRUAL)
        ).status_code
        == 404
    )
    smuggled = write(
        client,
        "POST",
        _path(mine["id"], "/kontroll"),
        json=_voucher(_ACCRUAL, corrects_id=their_voucher["id"]),
    )
    assert smuggled.status_code == 404
    assert client.get(_path(mine["id"])).json()["entries"] == []

    # A payment of the company's own is not a manual voucher either.
    payment = record(client, mine["id"], type_code="varekjop", gross_amount="100.00").json()
    assert client.get(_path(mine["id"], f"/{payment['id']}")).status_code == 404


def test_the_journal_reaches_the_saft_file(client: TestClient, tmp_path: Path) -> None:
    company = _books(client)
    posted = _post(
        client,
        company["id"],
        _voucher(
            [_line("6800", debit="1250.00", tax_code="1"), _line("2400", credit="1250.00")],
            on=date(LAST_YEAR, 12, 31),
            description="Påløpt kostnad desember",
        ),
    )
    assert posted.status_code == 201, posted.text
    assert _close(client, company["id"]).status_code == 201
    root = _saft(client, company["id"], LAST_YEAR, tmp_path)
    journals = {
        j.findtext("s:JournalID", namespaces=NS): j
        for j in root.iterfind(".//s:GeneralLedgerEntries/s:Journal", NS)
    }
    assert "MANUELT" in journals
    journal = journals["MANUELT"]
    assert journal.findtext("s:Type", namespaces=NS) == "manuelt"
    transaction = journal.find("s:Transaction", NS)
    assert transaction is not None
    assert transaction.findtext("s:Description", namespaces=NS) == "Påløpt kostnad desember"


# --- Dimensions --------------------------------------------------------------------------


def _department(client: TestClient, company_id: int, *codes: str) -> tuple[int, list[int]]:
    base = f"/api/v1/companies/{company_id}/dimensjoner"
    created = write(
        client, "POST", base, json={"code": "AVDELING", "description": "Avdeling"}
    ).json()
    dimension_id = int(created[0]["id"])
    for code in codes:
        response = write(
            client,
            "POST",
            f"{base}/{dimension_id}/verdier",
            json={"value": code, "description": f"Avdeling {code}"},
        )
        assert response.status_code == 201, response.text
    values = client.get(base).json()[0]["values"]
    return dimension_id, [int(value["id"]) for value in values]


def test_a_dimension_goes_on_the_net_line_and_survives_a_correction(client: TestClient) -> None:
    company = signed_in_company(client)
    dimension_id, (first, second) = _department(client, company["id"], "10", "20")
    supplier = _party(client, company["id"], "leverandor", "Rekvisita AS")
    lines = [
        _line("6800", debit="1250.00", tax_code="1", dimension_value_ids=[first]),
        _line("2400", credit="1250.00", party_id=supplier),
    ]
    response = _post(client, company["id"], _voucher(lines))
    assert response.status_code == 201, response.text
    voucher = response.json()
    # On the net 1000, which is what posting checks the allocation against, and
    # not on the VAT line Myntos added.
    assert [
        (li["account_number"], li["debit"], li["dimension_value_ids"]) for li in voucher["lines"]
    ] == [
        ("6800", "1000.00", [first]),
        ("2710", "250.00", []),
        ("2400", "0.00", []),
    ]
    assert voucher["input_lines"][0]["dimension_value_ids"] == [first]

    def costs_on(value_id: int) -> Decimal:
        result = client.get(
            f"/api/v1/companies/{company['id']}/dimensjoner/{dimension_id}/resultat",
            params={"aar": THIS_YEAR},
        ).json()
        row = next(row for row in result["rows"] if row["value_id"] == value_id)
        return Decimal(row["total"]["costs"])

    assert costs_on(first) == Decimal("1000.00")

    # Corrected onto the other department: the reversal takes the cost off the
    # first, the replacement puts it on the second.
    moved = [dict(line) for line in voucher["input_lines"]]
    moved[0]["dimension_value_ids"] = [second]
    corrected = write(
        client,
        "PUT",
        _path(company["id"], f"/{voucher['id']}"),
        json=_voucher(moved, explanation="Feil avdeling, kostnaden hører til avdeling 20."),
    )
    assert corrected.status_code == 201, corrected.text
    assert costs_on(first) == Decimal("0.00")
    assert costs_on(second) == Decimal("1000.00")


def test_a_dimension_that_cannot_be_used_is_a_finding(client: TestClient) -> None:
    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    mine = create_company(client, organization_id)
    theirs = create_company(client, organization_id, orgnr=OTHER_ORGNR, name="Søster AS")
    _, (first, second) = _department(client, mine["id"], "10", "20")
    _, (foreign,) = _department(client, theirs["id"], "10")

    for chosen in ([first, second], [foreign]):
        body = _voucher(
            [
                _line("6800", debit="400.00", dimension_value_ids=chosen),
                _line("1700", credit="400.00"),
            ]
        )
        review = _review(client, mine["id"], body)
        assert "ugyldig_dimensjon" in _codes(review, "feil")
        assert not review["postable"]
        refused = _post(client, mine["id"], body)
        assert refused.status_code == 422
        assert refused.json()["code"] == "invalid_manual_voucher"
