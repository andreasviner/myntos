"""End-to-end tests for companies, the cash-flow register and the bilag archive.

The whole stack runs: HTTP, the service layer, the posting engine and SQLite.
That is the point — the arithmetic is already covered by
``test_cashflow_domain``, so what these check is that a payment survives the
round trip as a balanced, immutable, correctly-scoped voucher.
"""

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from myntos.core.clock import utc_now
from myntos.domain.bilag import classification
from myntos.integrations import loynd
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
    menu = classification.build_menu()
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
