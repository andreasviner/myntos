"""End-to-end tests for companies, the cash-flow register and the bilag archive.

The whole stack runs: HTTP, the service layer, the posting engine and SQLite.
That is the point — the arithmetic is already covered by
``test_cashflow_domain``, so what these check is that a payment survives the
round trip as a balanced, immutable, correctly-scoped voucher.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from myntos.core.clock import utc_now
from myntos.domain.bilag import classification
from myntos.domain.ledger.cashflow import CashFlowDirection
from myntos.integrations import loynd, norgesbank
from tests.conftest import register, write

ORGNR = "910000004"
OTHER_ORGNR = "910000012"
INVALID_ORGNR = "910000005"

# The smallest thing that is unambiguously a PDF. Validation reads the magic
# number, not the extension or the declared content type.
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"

THIS_YEAR = utc_now().year


def create_company(
    client: TestClient,
    organization_id: int,
    *,
    orgnr: str = ORGNR,
    name: str = "Nordmann Consulting AS",
) -> dict[str, Any]:
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": organization_id,
            "orgnr": orgnr,
            "name": name,
            "first_financial_year": THIS_YEAR,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def signed_in_company(client: TestClient, **kwargs: Any) -> dict[str, Any]:
    """Register an account and give it a company to book into."""
    session = register(client, **kwargs)
    organization_id = session["memberships"][0]["organization_id"]
    return create_company(client, organization_id)


def record(
    client: TestClient,
    company_id: int,
    *,
    type_code: str,
    gross_amount: str,
    description: str = "Test",
    occurred_on: str | None = None,
    tax_code: str | None = None,
    payment_method: str = "bank",
    party_id: int | None = None,
) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/cashflow",
        json={
            "type_code": type_code,
            "occurred_on": occurred_on or date(THIS_YEAR, 3, 14).isoformat(),
            "gross_amount": gross_amount,
            "description": description,
            "tax_code": tax_code,
            "payment_method": payment_method,
            "party_id": party_id,
        },
    )


# --- Companies ------------------------------------------------------------------


def test_creating_a_company_seeds_it_ready_to_book_into(client: TestClient) -> None:
    company = signed_in_company(client)
    assert company["orgnr"] == ORGNR
    # A payment posts immediately, which is only possible if the kontoplan, the
    # VAT codes and the financial year were all created with the company.
    assert (
        record(client, company["id"], type_code="varekjop", gross_amount="100.00").status_code
        == 201
    )


def test_orgnr_check_digit_is_verified(client: TestClient) -> None:
    session = register(client)
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": session["memberships"][0]["organization_id"],
            "orgnr": INVALID_ORGNR,
            "name": "Feil AS",
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_orgnr"


def test_the_same_orgnr_twice_in_one_organization_is_rejected(client: TestClient) -> None:
    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    create_company(client, organization_id)
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={"organization_id": organization_id, "orgnr": ORGNR, "name": "Igjen AS"},
    )
    assert response.status_code == 409


def test_two_organizations_may_keep_books_for_the_same_orgnr(client: TestClient) -> None:
    """A client can change accountant without the old one blocking the new one."""
    first = register(client, "kari@example.no")
    create_company(client, first["memberships"][0]["organization_id"])
    write(client, "POST", "/api/v1/auth/logout")

    second = register(client, "ola@example.no", full_name="Ola Nordmann")
    company = create_company(client, second["memberships"][0]["organization_id"])
    assert company["orgnr"] == ORGNR


def test_another_organizations_company_is_not_found(client: TestClient) -> None:
    owner = register(client, "kari@example.no")
    company = create_company(client, owner["memberships"][0]["organization_id"])
    write(client, "POST", "/api/v1/auth/logout")

    register(client, "fremmed@example.no", full_name="Fremmed Person")
    assert client.get(f"/api/v1/companies/{company['id']}").status_code == 404
    assert client.get(f"/api/v1/companies/{company['id']}/cashflow").status_code == 404


def test_one_account_lists_every_company_it_keeps_books_for(client: TestClient) -> None:
    """The regnskapsfører case, and what the company picker in the UI is drawn from."""
    session = register(client, "kari@example.no", organization_name="Fjord Regnskap AS")
    organization_id = session["memberships"][0]["organization_id"]
    first = create_company(client, organization_id, orgnr=ORGNR, name="Klient En AS")
    second = create_company(client, organization_id, orgnr=OTHER_ORGNR, name="Klient To AS")

    listed = client.get("/api/v1/companies").json()
    assert {company["orgnr"] for company in listed} == {ORGNR, OTHER_ORGNR}
    # Both reachable on their own, and each with its own set of books: an entry
    # booked in one must not turn up in the other.
    for company in (first, second):
        assert client.get(f"/api/v1/companies/{company['id']}").status_code == 200

    record(client, first["id"], type_code="salg_hoy_sats", gross_amount="1250.00")
    assert client.get(f"/api/v1/companies/{first['id']}/cashflow").json() != []
    assert client.get(f"/api/v1/companies/{second['id']}/cashflow").json() == []


def test_listing_companies_only_shows_your_own(client: TestClient) -> None:
    owner = register(client, "kari@example.no")
    create_company(client, owner["memberships"][0]["organization_id"])
    write(client, "POST", "/api/v1/auth/logout")

    other = register(client, "ola@example.no", full_name="Ola Nordmann")
    create_company(client, other["memberships"][0]["organization_id"], orgnr=OTHER_ORGNR)

    listed = client.get("/api/v1/companies").json()
    assert [company["orgnr"] for company in listed] == [OTHER_ORGNR]


# --- Recording payments ---------------------------------------------------------


def test_money_in_splits_the_gross_amount_into_net_and_vat(client: TestClient) -> None:
    company = signed_in_company(client)
    response = record(
        client,
        company["id"],
        type_code="salg_hoy_sats",
        gross_amount="1250.00",
        description="Faktura 1",
    )
    assert response.status_code == 201, response.text
    entry = response.json()

    assert entry["direction"] == "inn"
    assert entry["gross_amount"] == "1250.00"
    assert entry["net_amount"] == "1000.00"
    assert entry["vat_amount"] == "250.00"
    assert entry["account_number"] == "3000"
    assert entry["tax_code"] == "3"
    assert entry["voucher_no"] == f"{THIS_YEAR}-0001"


def test_money_out_books_to_the_cost_account(client: TestClient) -> None:
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="kontorrekvisita", gross_amount="625.00").json()

    assert entry["direction"] == "ut"
    assert entry["net_amount"] == "500.00"
    assert entry["vat_amount"] == "125.00"
    assert entry["account_number"] == "6800"
    assert entry["tax_code"] == "1"


def test_a_type_without_vat_books_the_whole_amount(client: TestClient) -> None:
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="lonn", gross_amount="30000.00").json()

    assert entry["net_amount"] == "30000.00"
    assert entry["vat_amount"] == "0.00"
    assert entry["tax_code"] == "0"


def test_the_default_vat_code_can_be_overridden(client: TestClient) -> None:
    """Rented premises without VAT — the note on the type says to expect this."""
    company = signed_in_company(client)
    entry = record(
        client, company["id"], type_code="husleie", gross_amount="10000.00", tax_code="0"
    ).json()

    assert entry["tax_code"] == "0"
    assert entry["vat_amount"] == "0.00"


def test_cash_payments_use_the_cash_account(client: TestClient) -> None:
    company = signed_in_company(client)
    entry = record(
        client,
        company["id"],
        type_code="reise",
        gross_amount="450.00",
        payment_method="kontant",
    ).json()
    assert entry["payment_account"] == "1900"


def test_voucher_numbers_are_sequential(client: TestClient) -> None:
    company = signed_in_company(client)
    numbers = [
        record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()[
            "voucher_no"
        ]
        for _ in range(3)
    ]
    assert numbers == [f"{THIS_YEAR}-000{n}" for n in (1, 2, 3)]


def test_an_unknown_type_is_refused(client: TestClient) -> None:
    company = signed_in_company(client)
    response = record(client, company["id"], type_code="pengevask", gross_amount="100.00")
    assert response.status_code == 422
    assert response.json()["code"] == "unknown_cash_flow_type"


def test_an_unknown_vat_code_is_refused(client: TestClient) -> None:
    company = signed_in_company(client)
    response = record(
        client, company["id"], type_code="varekjop", gross_amount="100.00", tax_code="99"
    )
    assert response.status_code == 422
    assert response.json()["code"] == "unknown_tax_code"


def test_a_date_before_the_first_financial_year_is_refused(client: TestClient) -> None:
    """The seeded VAT rates are not claimed to hold before the books start."""
    company = signed_in_company(client)
    response = record(
        client,
        company["id"],
        type_code="varekjop",
        gross_amount="100.00",
        occurred_on=date(THIS_YEAR - 3, 6, 1).isoformat(),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "period_outside_books"


def test_a_later_year_opens_itself(client: TestClient) -> None:
    company = signed_in_company(client)
    response = record(
        client,
        company["id"],
        type_code="varekjop",
        gross_amount="100.00",
        occurred_on=date(THIS_YEAR + 1, 2, 1).isoformat(),
    )
    assert response.status_code == 201
    assert response.json()["voucher_no"] == f"{THIS_YEAR + 1}-0001"


def test_a_zero_amount_is_refused(client: TestClient) -> None:
    company = signed_in_company(client)
    assert (
        record(client, company["id"], type_code="varekjop", gross_amount="0.00").status_code == 422
    )


# --- Listing and summary --------------------------------------------------------


def test_the_register_lists_what_was_recorded(client: TestClient) -> None:
    company = signed_in_company(client)
    record(client, company["id"], type_code="salg_hoy_sats", gross_amount="1250.00")
    record(client, company["id"], type_code="varekjop", gross_amount="500.00")

    entries = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    assert len(entries) == 2
    assert {entry["direction"] for entry in entries} == {"inn", "ut"}
    assert all(entry["type_label"] for entry in entries)


def test_the_summary_adds_up(client: TestClient) -> None:
    company = signed_in_company(client)
    record(client, company["id"], type_code="salg_hoy_sats", gross_amount="1250.00")
    record(client, company["id"], type_code="salg_hoy_sats", gross_amount="2500.00")
    record(client, company["id"], type_code="kontorrekvisita", gross_amount="625.00")

    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    assert summary["total_in"] == "3750.00"
    assert summary["total_out"] == "625.00"
    assert summary["net"] == "3125.00"
    assert summary["entry_count"] == 3
    assert summary["output_vat"] == "750.00"
    assert summary["input_vat"] == "125.00"
    # What would be owed to Skatteetaten for the period.
    assert summary["vat_position"] == "625.00"
    assert summary["cash_position"] == "3125.00"


def test_the_summary_breaks_down_by_type(client: TestClient) -> None:
    company = signed_in_company(client)
    record(client, company["id"], type_code="salg_hoy_sats", gross_amount="1000.00")
    record(client, company["id"], type_code="lonn", gross_amount="4000.00")

    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    by_type = {item["type_code"]: item for item in summary["by_type"]}
    assert by_type["lonn"]["total"] == "4000.00"
    assert by_type["lonn"]["direction"] == "ut"
    assert by_type["salg_hoy_sats"]["type_label"] == "Salg med 25 % mva"


def test_the_summary_is_scoped_to_the_financial_year(client: TestClient) -> None:
    company = signed_in_company(client)
    record(client, company["id"], type_code="salg_hoy_sats", gross_amount="1000.00")
    record(
        client,
        company["id"],
        type_code="salg_hoy_sats",
        gross_amount="8000.00",
        occurred_on=date(THIS_YEAR + 1, 1, 5).isoformat(),
    )

    this_year = client.get(
        f"/api/v1/companies/{company['id']}/cashflow/summary?financial_year={THIS_YEAR}"
    ).json()
    assert this_year["total_in"] == "1000.00"
    # The bank balance is a position, not a movement: it spans both years.
    assert this_year["cash_position"] == "9000.00"


# --- Corrections ----------------------------------------------------------------


def test_a_reversal_nets_the_entry_out(client: TestClient) -> None:
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="salg_hoy_sats", gross_amount="1250.00").json()

    response = write(
        client, "POST", f"/api/v1/companies/{company['id']}/cashflow/{entry['id']}/reversal"
    )
    assert response.status_code == 201, response.text
    reversal = response.json()
    assert reversal["is_reversal"] is True
    assert reversal["direction"] == "ut"
    assert reversal["gross_amount"] == "1250.00"

    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    assert summary["net"] == "0.00"
    assert summary["output_vat"] == "0.00"


def test_an_entry_cannot_be_reversed_twice(client: TestClient) -> None:
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()
    url = f"/api/v1/companies/{company['id']}/cashflow/{entry['id']}/reversal"

    assert write(client, "POST", url).status_code == 201
    second = write(client, "POST", url)
    assert second.status_code == 409
    assert second.json()["code"] == "already_reversed"


def test_a_reversed_entry_says_so_in_the_listing(client: TestClient) -> None:
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()
    write(client, "POST", f"/api/v1/companies/{company['id']}/cashflow/{entry['id']}/reversal")

    entries = {e["id"]: e for e in client.get(f"/api/v1/companies/{company['id']}/cashflow").json()}
    assert entries[entry["id"]]["reversed_by_id"] is not None


def test_reversing_a_voucher_from_another_company_is_not_found(client: TestClient) -> None:
    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    first = create_company(client, organization_id)
    second = create_company(client, organization_id, orgnr=OTHER_ORGNR, name="Andre AS")
    entry = record(client, first["id"], type_code="varekjop", gross_amount="100.00").json()

    response = write(
        client, "POST", f"/api/v1/companies/{second['id']}/cashflow/{entry['id']}/reversal"
    )
    assert response.status_code == 404


# --- Bilag documents ------------------------------------------------------------


def upload(client: TestClient, company_id: int, voucher_id: int, content: bytes, name: str) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/cashflow/{voucher_id}/dokument",
        files={"file": (name, content, "application/pdf")},
    )


def test_a_pdf_can_be_attached_and_read_back(client: TestClient) -> None:
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()

    response = upload(client, company["id"], entry["id"], PDF_BYTES, "kvittering.pdf")
    assert response.status_code == 201, response.text
    attachment = response.json()
    assert attachment["filename"] == "kvittering.pdf"
    assert attachment["byte_size"] == len(PDF_BYTES)

    download = client.get(f"/api/v1/companies/{company['id']}/dokument/{attachment['id']}")
    assert download.status_code == 200
    assert download.content == PDF_BYTES
    # Never inline: the bytes came from a user.
    assert download.headers["content-disposition"].startswith("attachment;")


def test_an_attached_document_shows_up_on_the_entry(client: TestClient) -> None:
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()
    upload(client, company["id"], entry["id"], PDF_BYTES, "bilag.pdf")

    listed = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    assert len(listed[0]["attachments"]) == 1


def test_a_file_that_is_not_a_pdf_is_refused(client: TestClient) -> None:
    """The magic number decides, not the declared content type."""
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()

    response = upload(client, company["id"], entry["id"], b"MZ\x90\x00 not a pdf", "faktura.pdf")
    assert response.status_code == 415
    assert response.json()["code"] == "unsupported_document"


def test_a_dangerous_filename_is_neutralised(client: TestClient) -> None:
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()

    attachment = upload(
        client, company["id"], entry["id"], PDF_BYTES, '../../etc/pas"swd.pdf'
    ).json()
    assert "/" not in attachment["filename"]
    assert '"' not in attachment["filename"]


def test_another_organizations_document_is_not_reachable(client: TestClient) -> None:
    owner = register(client, "kari@example.no")
    company = create_company(client, owner["memberships"][0]["organization_id"])
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()
    attachment = upload(client, company["id"], entry["id"], PDF_BYTES, "bilag.pdf").json()
    write(client, "POST", "/api/v1/auth/logout")

    stranger = register(client, "fremmed@example.no", full_name="Fremmed Person")
    own = create_company(client, stranger["memberships"][0]["organization_id"], orgnr=OTHER_ORGNR)
    # Both the stranger's own company id and the real one must fail.
    assert (
        client.get(f"/api/v1/companies/{own['id']}/dokument/{attachment['id']}").status_code == 404
    )
    assert (
        client.get(f"/api/v1/companies/{company['id']}/dokument/{attachment['id']}").status_code
        == 404
    )


# --- Reading a faktura ----------------------------------------------------------


def text_pdf(lines: list[str]) -> bytes:
    """A one-page PDF whose text layer holds ``lines``.

    Written out by hand rather than with a PDF library: the point of these tests
    is that an ordinary invoice survives the round trip through pypdf, and a
    generator from the same ecosystem would prove less than it appears to.
    WinAnsi encoding, so ``Å`` and ``ø`` come back as themselves.
    """
    drawn = ["BT", "/F1 11 Tf", "14 TL", "40 800 Td"]
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        drawn.append(f"({escaped}) Tj T*")
    drawn.append("ET")
    stream = "\n".join(drawn).encode("cp1252")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    start_xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start_xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


# The supplier carries OTHER_ORGNR: ORGNR belongs to the company being booked
# for, and an invoice printing our own number is one we issued ourselves.
INVOICE_LINES = [
    "Telenor Norge AS",
    "Snarøyveien 30",
    "1360 Fornebu",
    f"Org.nr. {OTHER_ORGNR} MVA",
    "FAKTURA",
    "Fakturanummer: 104588",
    f"Fakturadato: 14.03.{THIS_YEAR}",
    f"Forfallsdato: 28.03.{THIS_YEAR}",
    "Kunde: Nordmann Consulting AS",
    "Mobilabonnement mars              798,00",
    "Sum eks. mva                   1 447,00",
    "Mva 25 %                         361,75",
    "Å betale                       1 808,75",
]


def interpret(
    client: TestClient, company_id: int, content: bytes, name: str = "faktura.pdf"
) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/cashflow/forslag",
        files={"file": (name, content, "application/pdf")},
    )


def test_an_uploaded_invoice_proposes_the_whole_entry(client: TestClient) -> None:
    company = signed_in_company(client)

    response = interpret(client, company["id"], text_pdf(INVOICE_LINES))
    assert response.status_code == 200, response.text
    suggestion = response.json()

    assert suggestion["direction"] == "ut"
    assert suggestion["occurred_on"] == date(THIS_YEAR, 3, 14).isoformat()
    assert suggestion["gross_amount"] == "1808.75"
    assert suggestion["vat_amount"] == "361.75"
    assert suggestion["type_code"] == "telefon_internett"
    assert suggestion["type_label"] == "Telefon og internett"
    assert suggestion["tax_code"] == "1"
    assert suggestion["counterparty_orgnr"] == OTHER_ORGNR
    assert suggestion["description"] == "Faktura 104588, Telenor Norge AS"
    assert "gross_amount" in suggestion["filled"]


def test_a_proposal_can_be_posted_unchanged(client: TestClient) -> None:
    """The whole point: what comes back fits the form it is going into."""
    company = signed_in_company(client)
    suggestion = interpret(client, company["id"], text_pdf(INVOICE_LINES)).json()

    entry = record(
        client,
        company["id"],
        type_code=suggestion["type_code"],
        occurred_on=suggestion["occurred_on"],
        gross_amount=suggestion["gross_amount"],
        description=suggestion["description"],
        tax_code=suggestion["tax_code"],
    ).json()

    assert entry["gross_amount"] == "1808.75"
    assert entry["vat_amount"] == "361.75"
    assert entry["account_number"] == "6900"


def test_a_registered_supplier_is_recognised_and_proposed(client: TestClient) -> None:
    company = signed_in_company(client)
    party = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/parties",
        json={"party_type": "leverandor", "name": "Telenor Norge AS", "orgnr": OTHER_ORGNR},
    ).json()

    suggestion = interpret(client, company["id"], text_pdf(INVOICE_LINES)).json()
    assert suggestion["party_id"] == party["id"]


def test_reading_a_document_stores_nothing(client: TestClient) -> None:
    """A user who uploads the wrong file leaves nothing in an append-only archive."""
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()

    interpret(client, company["id"], text_pdf(INVOICE_LINES))

    listed = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    assert [e["id"] for e in listed] == [entry["id"]]
    assert listed[0]["attachments"] == []


def test_a_pdf_with_no_text_layer_proposes_nothing_and_explains(client: TestClient) -> None:
    """A photographed receipt. Not an error — the user fills the form in by hand."""
    company = signed_in_company(client)

    suggestion = interpret(client, company["id"], PDF_BYTES).json()
    assert suggestion["gross_amount"] is None
    assert suggestion["filled"] == []
    assert suggestion["warnings"] != []


def test_a_file_that_is_not_a_pdf_is_refused_before_it_is_read(client: TestClient) -> None:
    company = signed_in_company(client)

    response = interpret(client, company["id"], b"MZ\x90\x00 not a pdf")
    assert response.status_code == 415
    assert response.json()["code"] == "unsupported_document"


def test_the_model_picks_the_category_and_the_answer_says_so(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Løynd chooses from the catalogue; the entry form gets the whole consequence.

    The invoice says "Mobilabonnement", which our own keyword list reads as
    telephony. The model is stubbed into answering "kontorrekvisita" instead, so
    the category in the response can only have come from it — along with the
    account that follows from it and the label saying where it came from.
    """
    # The menu the endpoint builds, not the whole catalogue: a purchase is
    # offered only the expense half, so the numbers differ and a stub that
    # answered by position on the full list would name a different category.
    menu = classification.build_menu(CashFlowDirection.UT)
    rekvisita = next(item for item in menu if item.type_code == "kontorrekvisita")

    async def fake_complete(*, system: str, prompt: str, max_tokens: int = 16) -> str:
        del system, prompt, max_tokens
        return str(rekvisita.number)

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "complete", fake_complete)

    company = signed_in_company(client)
    suggestion = interpret(client, company["id"], text_pdf(INVOICE_LINES)).json()

    assert suggestion["type_code"] == "kontorrekvisita"
    assert suggestion["type_source"] == "ki"
    # The rest of the reading is untouched: amounts and dates are printed facts.
    assert suggestion["gross_amount"] == "1808.75"
    assert suggestion["occurred_on"] == date(THIS_YEAR, 3, 14).isoformat()


def test_a_document_the_model_cannot_place_falls_back_to_our_own_reading(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refusing(**kwargs: Any) -> str:
        raise loynd.LoyndUnavailableError("nede")

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "complete", refusing)

    company = signed_in_company(client)
    suggestion = interpret(client, company["id"], text_pdf(INVOICE_LINES)).json()

    assert suggestion["type_code"] == "telefon_internett"
    assert suggestion["type_source"] == "dokument"


# What a receipt from a foreign webshop looks like once pypdf is done with it.
# Not one of the words our own labels look for, a date written month first, and a
# total in pounds — see tests/test_bilag_extraction.py for the reading itself.
RECEIPT_LINES = [
    "Order information",
    "Buyer andreali_7",
    "Seller daisyaus-0",
    f"Placed on Aug 23, {THIS_YEAR}",
    "Payment method Credit Card",
    "Order total",
    "1 item GBP 2,137.00",
    "Order total GBP 2,137.00",
    "Items bought from daisyaus-0",
    "Order number: 10-15068-44333",
    "NEW Apple Mac Studio M3 Ultra 28-Core 96GB RAM 1TB SSD 60-Core GPU",
]


# The model's two answers, as it would give them for the receipt above.
RECEIPT_ANSWER = f"""DATO: Aug 23, {THIS_YEAR}
VALUTA: GBP
MOTPART: daisyaus-0
BESKRIVELSE: Apple Mac Studio til kontoret"""

# The same four lines, every one of them about a document other than this one.
INVENTED_ANSWER = f"""DATO: 01.01.{THIS_YEAR}
VALUTA: USD
MOTPART: eBay Inc
BESKRIVELSE: UKJENT"""


def answering(category: str, fields: str) -> Any:
    """A Løynd that answers both questions, told apart by what was asked."""

    async def complete(*, system: str, prompt: str, max_tokens: int = 16) -> str:
        del system, max_tokens
        return fields if "BESKRIVELSE" in prompt else category

    return complete


def test_a_receipt_with_none_of_our_words_still_fills_the_form(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case the reading alone could not carry: no labels, no kroner, no orgnr.

    The date and the seller are on the page, so the parser gets them once it
    knows the shapes. What it cannot get is what the payment was for, and that is
    both of Løynd's answers here: the category, and the sentence that goes in the
    ledger next to the order number.
    """
    menu = classification.build_menu(CashFlowDirection.UT)
    inventar = next(item for item in menu if item.type_code == "inventar")
    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(
        loynd,
        "complete",
        answering(str(inventar.number), RECEIPT_ANSWER),
    )

    company = signed_in_company(client)
    suggestion = interpret(client, company["id"], text_pdf(RECEIPT_LINES)).json()

    assert suggestion["occurred_on"] == date(THIS_YEAR, 8, 23).isoformat()
    assert suggestion["counterparty_name"] == "daisyaus-0"
    assert suggestion["type_code"] == "inventar"
    assert suggestion["description"] == "Ordre 10-15068-44333, Apple Mac Studio til kontoret"
    assert suggestion["sources"]["description"] == "ki"
    assert suggestion["sources"]["occurred_on"] == "dokument"
    # In pounds, so the kroner field stays empty and the total is shown instead.
    assert suggestion["currency_code"] == "GBP"
    assert suggestion["foreign_amount"] == "2137.00"
    assert suggestion["gross_amount"] is None
    assert suggestion["tax_code"] is None
    # The separator is a non-breaking space; the part worth asserting on is that
    # the sentence names the total in the currency the document actually used.
    assert any("137,00 GBP" in warning for warning in suggestion["warnings"])


def test_a_model_answer_the_document_does_not_support_changes_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invented is not the same as unavailable, but it ends the same way."""
    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(
        loynd,
        "complete",
        answering("0", INVENTED_ANSWER),
    )

    company = signed_in_company(client)
    suggestion = interpret(client, company["id"], text_pdf(RECEIPT_LINES)).json()

    assert suggestion["occurred_on"] == date(THIS_YEAR, 8, 23).isoformat()
    assert suggestion["currency_code"] == "GBP"
    assert suggestion["counterparty_name"] == "daisyaus-0"
    assert suggestion["description"] == "Ordre 10-15068-44333, daisyaus-0"
    assert suggestion["sources"]["description"] == "dokument"


# --- Permissions ----------------------------------------------------------------


def test_a_viewer_may_read_the_register_but_not_add_to_it(client: TestClient) -> None:
    owner = register(client, "kari@example.no")
    organization_id = owner["memberships"][0]["organization_id"]
    company = create_company(client, organization_id)
    invitation = write(
        client,
        "POST",
        f"/api/v1/organizations/{organization_id}/invitations",
        json={"email": "viewer@example.no", "role": "viewer"},
    ).json()
    token = invitation["invite_url"].split("token=")[1]
    write(client, "POST", "/api/v1/auth/logout")

    register(client, "viewer@example.no", full_name="Kikker Person")
    write(client, "POST", "/api/v1/auth/invitations/accept", json={"token": token})

    assert client.get(f"/api/v1/companies/{company['id']}/cashflow").status_code == 200
    denied = record(client, company["id"], type_code="varekjop", gross_amount="100.00")
    assert denied.status_code == 403
    assert denied.json()["code"] == "permission_denied"


def test_a_member_may_not_add_a_company(client: TestClient) -> None:
    """Taking on a new set of books is an administrative act."""
    owner = register(client, "kari@example.no")
    organization_id = owner["memberships"][0]["organization_id"]
    invitation = write(
        client,
        "POST",
        f"/api/v1/organizations/{organization_id}/invitations",
        json={"email": "medlem@example.no", "role": "member"},
    ).json()
    token = invitation["invite_url"].split("token=")[1]
    write(client, "POST", "/api/v1/auth/logout")

    register(client, "medlem@example.no", full_name="Medlem Person")
    write(client, "POST", "/api/v1/auth/invitations/accept", json={"token": token})

    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={"organization_id": organization_id, "orgnr": ORGNR, "name": "Snik AS"},
    )
    assert response.status_code == 403


def test_the_register_needs_a_session(client: TestClient) -> None:
    assert client.get("/api/v1/companies").status_code == 401
    assert client.get("/api/v1/cashflow/types").status_code == 401


def test_the_catalogue_lists_types_with_their_notes(client: TestClient) -> None:
    register(client)
    catalogue = client.get("/api/v1/cashflow/types").json()

    assert catalogue["max_attachment_bytes"] == 25 * 1024 * 1024
    assert {entry["direction"] for entry in catalogue["types"]} == {"inn", "ut"}
    assert all(entry["note"] for entry in catalogue["types"])
    assert any(code["code"] == "3" for code in catalogue["tax_codes"])


# --- Kroner, the file's own name, and the switch that turns the model off ---------


def quoting(rate: str, quoted_on: date) -> Any:
    """A Norges Bank that answers with one fixed rate.

    ``quoted_on`` is the day the rate is *published* for, which is not the day
    asked about: the receipt is dated a Sunday and the last quote before it is
    the Friday's. The real lookup does the same walk backwards, and the stub
    ignores its ``on`` argument for exactly that reason.
    """

    async def rate_for(currency_code: str, *, on: date) -> norgesbank.Rate:
        del on
        return norgesbank.Rate(
            currency_code=currency_code,
            quoted_on=quoted_on,
            kroner_per_unit=Decimal(rate),
        )

    return rate_for


def test_a_receipt_in_pounds_comes_back_in_kroner(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The field that used to be left empty, with the arithmetic next to it.

    Marked "beregnet" rather than "dokument": the document says 2 137,00 GBP and
    says nothing at all about kroner. The user still has to check it against the
    statement, because the card issuer's spread is not on the invoice, but they
    start from a number that is within a percent instead of from nothing.
    """
    monkeypatch.setattr(norgesbank, "rate_for", quoting("12.6853", date(THIS_YEAR, 8, 21)))

    company = signed_in_company(client)
    suggestion = interpret(client, company["id"], text_pdf(RECEIPT_LINES)).json()

    assert suggestion["currency_code"] == "GBP"
    assert suggestion["foreign_amount"] == "2137.00"
    assert suggestion["gross_amount"] == "27108.49"
    assert suggestion["sources"]["gross_amount"] == "beregnet"
    assert suggestion["exchange_rate"] == "12.6853"
    assert suggestion["rate_quoted_on"] == date(THIS_YEAR, 8, 21).isoformat()
    assert any("12,6853" in warning for warning in suggestion["warnings"])
    # Over the threshold once it is kroner, and not before.
    assert any("driftsmiddel" in warning for warning in suggestion["warnings"])


def test_a_rate_that_does_not_arrive_leaves_the_amount_to_the_user(
    client: TestClient,
) -> None:
    """Best-effort. Norges Bank being down is not an error for the user.

    The autouse fixture refuses every lookup, so this is the unconfigured and
    unreachable case at once, and the answer is the one the register gave before
    conversion existed: an empty field and a sentence saying why.
    """
    company = signed_in_company(client)
    suggestion = interpret(client, company["id"], text_pdf(RECEIPT_LINES)).json()

    assert suggestion["gross_amount"] is None
    assert suggestion["exchange_rate"] is None
    assert any("står tomt med vilje" in warning for warning in suggestion["warnings"])


def test_the_filename_names_the_shop_when_the_page_does_not(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A browser names a downloaded receipt after the page it came from.

    On this document that is the only occurrence of the word eBay anywhere the
    text layer can reach, so it is put in front of the model with the excerpt.
    Asserted on the prompt rather than on the answer, because what is being
    tested is that the model was not asked with the shop's name withheld.

    The model gets the title whole. The trimming that turns "Order details eBay"
    into "eBay" is for the seller field, where a name is wanted and "Order
    details" is furniture; here the furniture is a fact about the document and
    worth knowing.
    """
    seen: list[str] = []

    async def complete(*, system: str, prompt: str, max_tokens: int = 16) -> str:
        del system, max_tokens
        seen.append(prompt)
        return "UKJENT"

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "complete", complete)

    company = signed_in_company(client)
    interpret(client, company["id"], text_pdf(RECEIPT_LINES), name="Order details _ eBay.pdf")

    assert seen
    assert all("Dokumentets tittel: Order details eBay" in prompt for prompt in seen)


def test_a_company_with_ki_off_is_never_asked_and_says_so(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off means the document does not leave the building, not a hidden button.

    Neither of the two questions is asked and no file is uploaded to Løynd's
    extractor either — a switch that still sent the bilag and merely ignored the
    answer would be worse than no switch, because it would be a promise that is
    not kept.
    """

    async def refuse(**_: Any) -> str:
        raise AssertionError("the model was asked despite KI being off")

    async def refuse_upload(**_: Any) -> Any:
        raise AssertionError("the document was uploaded despite KI being off")

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "complete", refuse)
    monkeypatch.setattr(loynd, "extract_file", refuse_upload)

    company = signed_in_company(client)
    write(client, "PATCH", f"/api/v1/companies/{company['id']}", json={"ki_enabled": False})

    suggestion = interpret(client, company["id"], text_pdf(RECEIPT_LINES)).json()

    assert suggestion["ki_used"] is False
    # The parser still did its work; only the two questions went unasked.
    assert suggestion["occurred_on"] == date(THIS_YEAR, 8, 23).isoformat()
    assert suggestion["type_code"] is None


def test_the_switch_is_on_by_default_and_survives_a_round_trip(client: TestClient) -> None:
    company = signed_in_company(client)
    assert company["ki_enabled"] is True

    off = write(
        client, "PATCH", f"/api/v1/companies/{company['id']}", json={"ki_enabled": False}
    ).json()
    assert off["ki_enabled"] is False

    # An unrelated edit leaves it alone, which is the whole contract of PATCH
    # here: a field nobody mentioned is a field nobody changed.
    renamed = write(
        client, "PATCH", f"/api/v1/companies/{company['id']}", json={"name": "Nytt navn AS"}
    ).json()
    assert renamed["ki_enabled"] is False


def test_a_purchase_is_never_offered_a_sales_category(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The menu matches the direction the parser read, so it cannot reverse it.

    Given all 26 categories, Løynd files this eBay receipt under "Salg til
    utlandet": a Mac Studio the company bought, booked as export revenue it
    earned. `apply_type` takes the direction from whichever category comes back,
    so the wrong answer does not merely mislabel the entry, it turns money out
    into money in and lands it in næringsspesifikasjonen as income.

    The direction is a reading and the category is the question, so the menu is
    narrowed to what the reading concluded. A reading that is wrong is corrected
    by the user flipping a radio button, which is visible; a direction reversed
    by a category is not.
    """
    seen: list[str] = []

    async def complete(*, system: str, prompt: str, max_tokens: int = 16) -> str:
        del system, max_tokens
        seen.append(prompt)
        return "UKJENT"

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "complete", complete)

    company = signed_in_company(client)
    suggestion = interpret(client, company["id"], text_pdf(RECEIPT_LINES)).json()

    assert suggestion["direction"] == "ut"
    menu_prompt = next(prompt for prompt in seen if "Svar med kun tallet" in prompt)
    assert "Salg til utlandet" not in menu_prompt
    assert "Verktøy og inventar" in menu_prompt
    # And the direction is stated once rather than repeated on every line, which
    # is what moved loynd-lite off "Uttak til eier" for this document.
    assert "Dette er et kjøp selskapet har gjort" in menu_prompt
    assert "(penger ut)" not in menu_prompt


# --- Editing a posted entry ------------------------------------------------------


def test_editing_an_entry_reverses_it_and_posts_the_correction(client: TestClient) -> None:
    """What the user sees as an edit, and what the ledger keeps.

    The case this exists for: a purchase booked with 25 % inngående mva that
    carried none, because it came from a foreign seller in a foreign currency.
    That is not a payment to undo, it is a payment to record correctly, and
    "25 %" is not a field to overwrite: it decides whether the entry has a line
    on 2710 at all, and 2710 is what the MVA-melding aggregates.
    """
    company = signed_in_company(client)
    original = record(
        client,
        company["id"],
        type_code="inventar",
        gross_amount="27108.49",
        description="Ordre 10-15068-44333, Apple Mac Studio",
    ).json()
    assert original["vat_amount"] == "5421.70"
    assert original["net_amount"] == "21686.79"

    corrected = write(
        client,
        "PUT",
        f"/api/v1/companies/{company['id']}/cashflow/{original['id']}",
        json={
            "type_code": "inventar",
            "occurred_on": original["occurred_on"],
            "gross_amount": "27108.49",
            "description": "Ordre 10-15068-44333, Apple Mac Studio",
            # "0: Ingen merverdiavgiftsbehandling". Not None, which means
            # "use the type default" and would put the 25 % straight back.
            "tax_code": "0",
            "payment_method": "bank",
            "party_id": None,
        },
    )
    assert corrected.status_code == 201, corrected.text
    fixed = corrected.json()
    assert fixed["vat_amount"] == "0.00"
    assert fixed["net_amount"] == "27108.49"

    entries = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    by_voucher = {entry["voucher_no"]: entry for entry in entries}
    # All three, which is the point: the original, the proof it was undone, and
    # the right one. An auditor can follow the correction rather than find a
    # number that quietly changed.
    assert len(by_voucher) == 3
    assert by_voucher[original["voucher_no"]]["reversed_by_id"] is not None
    assert sum(entry["is_reversal"] for entry in entries) == 1
    # And the third link, which is what lets the three be read back as one
    # correction. Without it the replacement is indistinguishable from an
    # unrelated payment posted the same day, and the register can only pair
    # them by guessing from dates and amounts.
    assert fixed["corrects_id"] == original["id"]
    assert by_voucher[original["voucher_no"]]["corrects_id"] is None
    assert [e["corrects_id"] for e in entries if e["is_reversal"]] == [None]
    # The VAT the books claim is now zero: the reversal cancels the 5 421,70 the
    # first posting put on 2710, and the correction adds none back.
    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    assert summary["input_vat"] == "0.00"


def test_an_entry_cannot_be_edited_twice(client: TestClient) -> None:
    """The second attempt finds an entry that is already reversed.

    Worth pinning because the form offers "Rediger" per row and a stale page is
    one back-button away from sending the same edit again. The guard is the
    unique index on the reversal link, not the interface.
    """
    company = signed_in_company(client)
    original = record(client, company["id"], type_code="varekjop", gross_amount="500.00").json()
    payload = {
        "type_code": "varekjop",
        "occurred_on": original["occurred_on"],
        "gross_amount": "600.00",
        "description": "Rettet",
        "tax_code": None,
        "payment_method": "bank",
        "party_id": None,
    }
    path = f"/api/v1/companies/{company['id']}/cashflow/{original['id']}"
    assert write(client, "PUT", path, json=payload).status_code == 201
    assert write(client, "PUT", path, json=payload).status_code == 409


def test_correcting_a_correction_chains_back_to_the_first_entry(client: TestClient) -> None:
    """Rettings can be rettet, and the trail has to survive it.

    The entry that is already reversed cannot be edited again, but the
    replacement can — it is an ordinary posted voucher. Each correction points
    at the one before it, so the register can walk the chain back and show five
    vouchers as one line rather than as three separate payments plus a pair.

    No unique index on the correction link, unlike the reversal one, and this is
    why: the same chain would violate it at the second step if there were.
    """
    company = signed_in_company(client)
    first = record(client, company["id"], type_code="varekjop", gross_amount="500.00").json()

    def correct(voucher_id: int, amount: str) -> dict[str, Any]:
        response = write(
            client,
            "PUT",
            f"/api/v1/companies/{company['id']}/cashflow/{voucher_id}",
            json={
                "type_code": "varekjop",
                "occurred_on": first["occurred_on"],
                "gross_amount": amount,
                "description": f"Rettet til {amount}",
                "tax_code": None,
                "payment_method": "bank",
                "party_id": None,
            },
        )
        assert response.status_code == 201, response.text
        return dict(response.json())

    second = correct(int(first["id"]), "600.00")
    third = correct(int(second["id"]), "700.00")

    assert second["corrects_id"] == first["id"]
    assert third["corrects_id"] == second["id"]

    entries = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    # Five vouchers: three postings and the two reversals between them.
    assert len(entries) == 5
    assert sum(entry["is_reversal"] for entry in entries) == 2
    # Only the last one is still standing, and it is the only one the register
    # shows without being asked.
    live = [
        entry for entry in entries if not entry["is_reversal"] and entry["reversed_by_id"] is None
    ]
    assert [entry["id"] for entry in live] == [third["id"]]


def test_a_correction_is_not_counted_three_times_in_the_years_figures(
    client: TestClient,
) -> None:
    """The overview after an edit, which is where this went wrong in the wild.

    Reproduces the reported case exactly: a 27 108,49 kroner import booked with
    25 % inngående mva it never carried, corrected to innførsel uten
    fradragsrett, plus one small unrelated purchase.

    Before the fix the front page read *inn i år 27 108,49* for a company that
    had been paid nothing all year. Reversing a purchase debits the bank, so
    the reversal looked exactly like income, and the same purchase appeared in
    *ut i år* twice. Only the net was right, because the pair does genuinely
    cancel — which is precisely what makes the error easy to miss.
    """
    company = signed_in_company(client)
    original = record(
        client,
        company["id"],
        type_code="inventar",
        gross_amount="27108.49",
        description="Apple Mac Studio",
    ).json()
    record(client, company["id"], type_code="varekjop", gross_amount="48.86").json()

    corrected = write(
        client,
        "PUT",
        f"/api/v1/companies/{company['id']}/cashflow/{original['id']}",
        json={
            "type_code": "driftsmiddel",
            "occurred_on": original["occurred_on"],
            "gross_amount": "27108.49",
            "description": "Apple Mac Studio",
            "tax_code": "82",
            "payment_method": "bank",
            "party_id": None,
        },
    )
    assert corrected.status_code == 201, corrected.text

    # Four vouchers in the ledger; two payments actually happened.
    assert len(client.get(f"/api/v1/companies/{company['id']}/cashflow").json()) == 4

    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    assert summary["total_in"] == "0.00"
    assert summary["total_out"] == "27157.35"
    assert summary["net"] == "-27157.35"
    assert summary["entry_count"] == 2
    assert summary["cash_position"] == "-27157.35"
    # The purchase is one line under one type, not two lines that disagree.
    assert [(t["type_code"], t["total"]) for t in summary["by_type"]] == [
        ("driftsmiddel", "27108.49"),
        ("varekjop", "48.86"),
    ]
    # 6 777,12 owed on the import with no deduction against it, less the 9,77
    # inngående mva on the small purchase. The reversal cancelled the 5 421,70
    # the first posting had wrongly claimed on 2710.
    assert summary["output_vat"] == "6777.12"
    assert summary["input_vat"] == "9.77"
    assert summary["vat_position"] == "6767.35"


def test_a_failed_correction_leaves_the_original_alone(client: TestClient) -> None:
    """The reason the reversal and the replacement are one request.

    Two calls from the browser can be interrupted between them, and the state
    that leaves behind — reversed, with no replacement — is worse than the
    mistake being corrected. Here the replacement is rejected, and the original
    has to come back untouched rather than reversed.
    """
    company = signed_in_company(client)
    original = record(client, company["id"], type_code="varekjop", gross_amount="500.00").json()

    refused = write(
        client,
        "PUT",
        f"/api/v1/companies/{company['id']}/cashflow/{original['id']}",
        json={
            "type_code": "finnes_ikke",
            "occurred_on": original["occurred_on"],
            "gross_amount": "600.00",
            "description": "Rettet",
            "tax_code": None,
            "payment_method": "bank",
            "party_id": None,
        },
    )
    assert refused.status_code >= 400

    entries = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    assert len(entries) == 1
    assert entries[0]["reversed_by_id"] is None


# --- Innførselsmva and capitalised purchases -------------------------------------


def test_an_import_computes_vat_on_top_and_posts_both_sides(client: TestClient) -> None:
    """The eBay Mac Studio, booked the way merverdiavgiftsloven wants it.

    A foreign seller charged no Norwegian VAT, so nothing is carved out of the
    27 108,49: the whole sum is the cost of the machine. The import VAT is
    computed on top, and it goes on *both* sides — owed on 2700, reclaimed on
    2710 — because the MVA-melding reports it twice and a single net-zero line
    would report it nowhere.
    """
    company = signed_in_company(client)
    entry = record(
        client,
        company["id"],
        type_code="driftsmiddel",
        gross_amount="27108.49",
        tax_code="81",
        description="Ordre 10-15068-44333, Apple Mac Studio",
    ).json()

    # Not 21 686,79: the cost is the whole amount that left the account.
    assert entry["net_amount"] == "27108.49"
    assert entry["gross_amount"] == "27108.49"
    assert entry["vat_amount"] == "6777.12"
    # A balance-sheet account, because nothing was consumed.
    assert entry["account_number"] == "1250"

    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    # Both sides reported, and they cancel: the import costs the business
    # nothing in VAT but is still on the return twice.
    assert summary["input_vat"] == "6777.12"
    assert summary["output_vat"] == "6777.12"
    assert summary["vat_position"] == "0.00"


def test_an_ordinary_purchase_still_carves_the_vat_out(client: TestClient) -> None:
    """The contrast that makes the case above worth a flag rather than a rate."""
    company = signed_in_company(client)
    entry = record(
        client,
        company["id"],
        type_code="kontorrekvisita",
        gross_amount="27108.49",
        tax_code="1",
    ).json()

    assert entry["net_amount"] == "21686.79"
    assert entry["vat_amount"] == "5421.70"


def test_a_non_deductible_import_is_a_real_cost(client: TestClient) -> None:
    """Code 82: owed and not reclaimed, so it lands on the purchase's account."""
    company = signed_in_company(client)
    entry = record(
        client,
        company["id"],
        type_code="driftsmiddel",
        gross_amount="10000.00",
        tax_code="82",
    ).json()

    summary = client.get(f"/api/v1/companies/{company['id']}/cashflow/summary").json()
    assert entry["vat_amount"] == "2500.00"
    assert summary["output_vat"] == "2500.00"
    # Nothing to reclaim, so it is owed rather than a wash.
    assert summary["input_vat"] == "0.00"
    assert summary["vat_position"] == "2500.00"


def test_the_catalogue_offers_the_import_codes_and_the_asset_category(
    client: TestClient,
) -> None:
    """The options exist on the website, not only in the posting engine."""
    signed_in_company(client)
    catalogue = client.get("/api/v1/cashflow/types").json()

    codes = {code["code"] for code in catalogue["tax_codes"]}
    assert {"81", "82", "83", "86", "87"} <= codes
    types = {entry["code"]: entry for entry in catalogue["types"]}
    assert types["driftsmiddel"]["account_number"] == "1250"
    assert types["driftsmiddel"]["direction"] == "ut"
