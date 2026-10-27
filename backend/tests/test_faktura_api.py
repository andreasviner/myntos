"""End-to-end tests for invoicing.

The whole stack runs: HTTP, the service layer, the posting engine, the bilag
archive and SQLite. The rules themselves are covered by ``test_faktura_domain``
and the two output formats by ``test_faktura_ehf`` and ``test_faktura_pdf``, so
what these check is what only the full round trip can show — that issuing an
invoice numbers it gaplessly, posts a balanced voucher, archives a readable copy,
and then refuses to let anyone change any of it.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from myntos.core.clock import utc_now
from myntos.domain.faktura import kid
from myntos.integrations import loynd
from tests.conftest import join_company, register, write
from tests.test_faktura_drafting import CUSTOMER_EMAIL, CUSTOMER_EMAIL_REPLY

ORGNR = "910000004"
CUSTOMER_ORGNR = "910000012"

THIS_YEAR = utc_now().year
DELIVERED = date(THIS_YEAR, 3, 14)
DUE = DELIVERED + timedelta(days=14)


def make_company(client: TestClient, organization_id: int, **overrides: Any) -> dict[str, Any]:
    payload = {
        "organization_id": organization_id,
        "orgnr": ORGNR,
        "name": "Nordmann Consulting AS",
        "first_financial_year": THIS_YEAR,
        "address_line": "Kirkegata 2",
        "postal_code": "0153",
        "city": "Oslo",
        "vat_registered": True,
        "bank_account": "15062033158",
        **overrides,
    }
    response = write(client, "POST", "/api/v1/companies", json=payload)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def signed_in_company(client: TestClient, **kwargs: Any) -> dict[str, Any]:
    session = register(client, **kwargs)
    return make_company(client, session["memberships"][0]["organization_id"])


def draft_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "buyer_name": "Kunde AS",
        "buyer_orgnr": CUSTOMER_ORGNR,
        "buyer_address_line": "Storgata 1",
        "buyer_postal_code": "0155",
        "buyer_city": "Oslo",
        "delivery_date": DELIVERED.isoformat(),
        "delivery_place": "Oslo",
        "due_date": DUE.isoformat(),
        "lines": [
            {
                "description": "Rådgivning mars",
                "quantity": "10",
                "unit_price": "1500.00",
                "tax_code": "3",
            }
        ],
    }
    payload.update(overrides)
    return payload


def create_draft(client: TestClient, company_id: int, **overrides: Any) -> dict[str, Any]:
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/faktura",
        json=draft_payload(**overrides),
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def issue(client: TestClient, company_id: int, document_id: int) -> Any:
    return write(client, "POST", f"/api/v1/companies/{company_id}/faktura/{document_id}/utsted")


# --- Drafts ---------------------------------------------------------------------------


def test_a_draft_has_no_number_and_is_not_in_the_books(client: TestClient) -> None:
    """§ 5-1-3: numbering drafts is how a series acquires gaps."""
    company = signed_in_company(client)
    draft = create_draft(client, company["id"])

    assert draft["status"] == "utkast"
    assert draft["document_number"] is None
    assert draft["voucher_id"] is None
    assert draft["net_total"] == "15000.00"
    assert draft["vat_total"] == "3750.00"
    assert draft["gross_total"] == "18750.00"


def test_a_complete_draft_reports_nothing_blocking(client: TestClient) -> None:
    company = signed_in_company(client)
    draft = create_draft(client, company["id"])

    response = client.get(f"/api/v1/companies/{company['id']}/faktura/{draft['id']}/kontroll")
    assert response.status_code == 200
    assert [v for v in response.json() if v["severity"] == "blokkerende"] == []


def test_kontroll_names_the_rule_and_the_field_for_each_gap(client: TestClient) -> None:
    company = signed_in_company(client)
    draft = create_draft(client, company["id"], delivery_date=None, delivery_place=None)

    found = client.get(f"/api/v1/companies/{company['id']}/faktura/{draft['id']}/kontroll").json()
    blocking = {v["field"]: v for v in found if v["severity"] == "blokkerende"}

    assert set(blocking) == {"delivery_date", "delivery_place"}
    assert blocking["delivery_date"]["rule"] == "bokføringsforskriften § 5-1-1 nr. 4"


def test_an_incomplete_draft_cannot_be_issued(client: TestClient) -> None:
    company = signed_in_company(client)
    draft = create_draft(client, company["id"], delivery_place=None)

    response = issue(client, company["id"], draft["id"])
    assert response.status_code == 422
    assert response.json()["code"] == "invoice_not_compliant"

    # And nothing was consumed: the next invoice still gets number 1.
    fixed = create_draft(client, company["id"])
    assert issue(client, company["id"], fixed["id"]).json()["document_number"] == "1"


def test_a_draft_can_be_changed_and_discarded(client: TestClient) -> None:
    company = signed_in_company(client)
    draft = create_draft(client, company["id"])

    changed = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/faktura/{draft['id']}",
        json={
            "lines": [
                {
                    "description": "Rådgivning april",
                    "quantity": "2",
                    "unit_price": "1000.00",
                    "tax_code": "33",
                }
            ]
        },
    )
    assert changed.status_code == 200
    assert changed.json()["vat_total"] == "240.00"

    discarded = write(client, "DELETE", f"/api/v1/companies/{company['id']}/faktura/{draft['id']}")
    assert discarded.status_code == 204
    assert client.get(f"/api/v1/companies/{company['id']}/faktura/{draft['id']}").status_code == 404


def test_a_line_may_carry_a_discount(client: TestClient) -> None:
    company = signed_in_company(client)
    draft = create_draft(
        client,
        company["id"],
        lines=[
            {
                "description": "Rådgivning",
                "quantity": "10",
                "unit_price": "1000.00",
                "tax_code": "3",
                "discount_percent": "15",
            }
        ],
    )
    assert draft["net_total"] == "8500.00"
    assert Decimal(draft["lines"][0]["discount_percent"]) == Decimal(15)


def test_an_input_vat_code_is_refused_on_a_sales_line(client: TestClient) -> None:
    company = signed_in_company(client)
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/faktura",
        json=draft_payload(
            lines=[
                {
                    "description": "Noe",
                    "quantity": "1",
                    "unit_price": "100.00",
                    "tax_code": "1",
                }
            ]
        ),
    )
    assert response.status_code == 422


# --- Issuing ---------------------------------------------------------------------------


def test_issuing_numbers_posts_and_freezes_the_document(client: TestClient) -> None:
    company = signed_in_company(client)
    draft = create_draft(client, company["id"])

    response = issue(client, company["id"], draft["id"])
    assert response.status_code == 201, response.text
    issued = response.json()

    assert issued["status"] == "utstedt"
    assert issued["document_number"] == "1"
    assert issued["issue_date"] is not None
    assert issued["voucher_id"] is not None
    assert issued["has_ehf"] is True
    assert issued["violations"] == []


def test_numbering_is_gapless_and_per_company(client: TestClient) -> None:
    """§ 5-1-3 wants a series complete enough to check by counting."""
    company = signed_in_company(client)
    numbers = []
    for _ in range(3):
        draft = create_draft(client, company["id"])
        numbers.append(issue(client, company["id"], draft["id"]).json()["document_number"])
    assert numbers == ["1", "2", "3"]

    # A discarded draft leaves no hole, because it never held a number.
    discarded = create_draft(client, company["id"])
    write(client, "DELETE", f"/api/v1/companies/{company['id']}/faktura/{discarded['id']}")
    draft = create_draft(client, company["id"])
    assert issue(client, company["id"], draft["id"]).json()["document_number"] == "4"


def test_issuing_posts_a_balanced_voucher_to_the_sales_journal(client: TestClient) -> None:
    company = signed_in_company(client)
    draft = create_draft(client, company["id"])
    issued = issue(client, company["id"], draft["id"]).json()

    # The receivable and the VAT are visible in the year's figures. The cash-flow
    # summary reads the ledger, so this is the posting seen from outside.
    summary = client.get(
        f"/api/v1/companies/{company['id']}/cashflow/summary?financial_year={THIS_YEAR}"
    ).json()
    assert Decimal(summary["output_vat"]) == Decimal(issued["vat_total"])
    # No money has moved: an invoice is revenue and a receivable, not a payment.
    assert Decimal(summary["cash_position"]) == Decimal("0.00")


def test_an_issued_invoice_cannot_be_changed_or_discarded(client: TestClient) -> None:
    """§ 5-2-7: an issued sales document is corrected by kreditnota, never edited."""
    company = signed_in_company(client)
    draft = create_draft(client, company["id"])
    issued = issue(client, company["id"], draft["id"]).json()
    url = f"/api/v1/companies/{company['id']}/faktura/{issued['id']}"

    changed = write(client, "PATCH", url, json={"note": "noe annet"})
    assert changed.status_code == 409
    assert changed.json()["code"] == "invoice_not_draft"
    assert write(client, "DELETE", url).status_code == 409
    assert issue(client, company["id"], issued["id"]).status_code == 409


def test_the_seller_snapshot_survives_a_later_company_change(client: TestClient) -> None:
    """An issued document has to keep saying what it said when it was issued."""
    company = signed_in_company(client)
    draft = create_draft(client, company["id"])
    issued = issue(client, company["id"], draft["id"]).json()

    renamed = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}",
        json={"name": "Nordmann Rådgivning AS"},
    )
    assert renamed.status_code == 200

    unchanged = client.get(f"/api/v1/companies/{company['id']}/faktura/{issued['id']}").json()
    assert unchanged["seller_name"] == "Nordmann Consulting AS"


def test_an_invoice_cannot_be_issued_without_somewhere_to_pay_it(client: TestClient) -> None:
    """The gap that shipped once: an invoice sent with no account number on it."""
    session = register(client)
    company = make_company(client, session["memberships"][0]["organization_id"], bank_account=None)
    draft = create_draft(client, company["id"])

    response = issue(client, company["id"], draft["id"])
    assert response.status_code == 422
    assert "kontonummer" in response.json()["detail"]
    # Still a draft, still without a number: the refusal happens before anything
    # irreversible.
    after = client.get(f"/api/v1/companies/{company['id']}/faktura/{draft['id']}").json()
    assert after["status"] == "utkast"
    assert after["document_number"] is None


def test_adding_the_account_number_clears_the_finding_on_an_existing_draft(
    client: TestClient,
) -> None:
    """Otherwise the fix would be unreachable from the draft it was needed for.

    The draft carries a snapshot of the seller, so a company corrected afterwards
    has to reach the draft — which is why saving it re-snapshots.
    """
    session = register(client)
    company = make_company(client, session["memberships"][0]["organization_id"], bank_account=None)
    draft = create_draft(client, company["id"])
    fields = [v["field"] for v in draft["violations"]]
    assert "seller_bank_account" in fields

    assert (
        write(
            client,
            "POST",
            f"/api/v1/companies/{company['id']}/bankkontoer",
            json={"account": "1506.20.33158"},
        ).status_code
        == 201
    )

    # Saving the draft is what the form does before it shows the review, and it
    # is what has to pick the corrected company up.
    saved = patch(client, company["id"], draft["id"], note="uendret").json()
    assert saved["seller_bank_account"] == "15062033158"
    assert [v["field"] for v in saved["violations"] if v["severity"] == "blokkerende"] == []
    assert issue(client, company["id"], draft["id"]).status_code == 201


def test_an_account_number_with_a_bad_check_digit_is_refused(client: TestClient) -> None:
    session = register(client)
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": session["memberships"][0]["organization_id"],
            "orgnr": ORGNR,
            "name": "Nordmann Consulting AS",
            "first_financial_year": THIS_YEAR,
            # A transposed pair — exactly what the check digit exists to catch.
            "bank_account": "15062033518",
        },
    )
    assert response.status_code == 422
    assert "feil kontrollsiffer" in response.json()["detail"]


def _add_account(client: TestClient, company_id: int, account: str) -> int:
    """A second kontonummer, and its id. Companies here are made with one already."""
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/bankkontoer",
        json={"account": account, "label": "Skattetrekk"},
    )
    assert response.status_code == 201, response.text
    added = next(a for a in response.json()["bank_accounts"] if a["account"] == account)
    return int(added["id"])


def test_an_invoice_is_paid_into_the_primary_account_unless_it_says_otherwise(
    client: TestClient,
) -> None:
    """The answer for every company with one account, and the default for the rest."""
    company = signed_in_company(client)
    _add_account(client, company["id"], "98765432103")
    draft = create_draft(client, company["id"])

    assert draft["bank_account_id"] is None
    assert draft["seller_bank_account"] == "15062033158"


def test_an_invoice_can_name_which_account_it_is_paid_into(client: TestClient) -> None:
    """What multiple accounts actually asks of an invoice: a choice."""
    company = signed_in_company(client)
    skattetrekk = _add_account(client, company["id"], "98765432103")
    draft = create_draft(client, company["id"], bank_account_id=skattetrekk)

    assert draft["bank_account_id"] == skattetrekk
    assert draft["seller_bank_account"] == "98765432103"

    issued = issue(client, company["id"], draft["id"])
    assert issued.status_code == 201, issued.text
    assert issued.json()["seller_bank_account"] == "98765432103"


def test_closing_an_account_does_not_change_an_issued_invoice(client: TestClient) -> None:
    """A posted document is history: it keeps saying what it said when it was issued."""
    company = signed_in_company(client)
    skattetrekk = _add_account(client, company["id"], "98765432103")
    draft = create_draft(client, company["id"], bank_account_id=skattetrekk)
    issued = issue(client, company["id"], draft["id"]).json()

    closed = write(client, "DELETE", f"/api/v1/companies/{company['id']}/bankkontoer/{skattetrekk}")
    assert closed.status_code == 200, closed.text

    after = client.get(f"/api/v1/companies/{company['id']}/faktura/{issued['id']}").json()
    assert after["seller_bank_account"] == "98765432103"
    # The key is gone, because the account is; the number the customer was told
    # to pay into is not, because it was told.
    assert after["bank_account_id"] is None


def test_a_draft_falls_back_to_the_primary_when_its_account_is_closed(
    client: TestClient,
) -> None:
    """Closing a bank account must not be blocked by a draft from March."""
    company = signed_in_company(client)
    skattetrekk = _add_account(client, company["id"], "98765432103")
    draft = create_draft(client, company["id"], bank_account_id=skattetrekk)

    write(client, "DELETE", f"/api/v1/companies/{company['id']}/bankkontoer/{skattetrekk}")

    saved = patch(client, company["id"], draft["id"], note="uendret").json()
    assert saved["seller_bank_account"] == "15062033158"
    # Back to naming none, which is the same state as a draft that never chose:
    # it follows the primary from here on rather than holding a dead id.
    assert saved["bank_account_id"] is None
    assert [v["field"] for v in saved["violations"] if v["severity"] == "blokkerende"] == []


def test_a_draft_cannot_be_pointed_at_another_companys_account(client: TestClient) -> None:
    """The company filter, on a field in the body rather than in the path."""
    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    mine = make_company(client, organization_id)
    theirs = make_company(client, organization_id, orgnr=CUSTOMER_ORGNR, name="Naboen AS")
    smuggled = theirs["bank_accounts"][0]["id"]

    response = write(
        client,
        "POST",
        f"/api/v1/companies/{mine['id']}/faktura",
        json=draft_payload(bank_account_id=smuggled),
    )
    assert response.status_code == 404


def test_a_company_that_is_not_vat_registered_cannot_invoice_vat(client: TestClient) -> None:
    session = register(client)
    company = make_company(
        client, session["memberships"][0]["organization_id"], vat_registered=False
    )
    draft = create_draft(client, company["id"])

    response = issue(client, company["id"], draft["id"])
    assert response.status_code == 422
    assert "Merverdiavgiftsregisteret" in response.json()["detail"]


def test_an_unregistered_company_can_invoice_without_vat(client: TestClient) -> None:
    session = register(client)
    company = make_company(
        client, session["memberships"][0]["organization_id"], vat_registered=False
    )
    draft = create_draft(
        client,
        company["id"],
        lines=[
            {
                "description": "Rådgivning",
                "quantity": "1",
                "unit_price": "5000.00",
                "tax_code": "0",
            }
        ],
    )
    issued = issue(client, company["id"], draft["id"]).json()

    assert issued["vat_total"] == "0.00"
    assert issued["gross_total"] == "5000.00"
    # And the document says why there is no VAT.
    assert issued["subtotals"][0]["reason"] is not None


# --- Kreditnota -------------------------------------------------------------------------


def test_a_kreditnota_reverses_the_invoice(client: TestClient) -> None:
    company = signed_in_company(client)
    draft = create_draft(client, company["id"])
    issued = issue(client, company["id"], draft["id"]).json()

    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/faktura/{issued['id']}/kreditnota",
        json={},
    )
    assert response.status_code == 201, response.text
    credit = response.json()

    assert credit["document_type"] == "kreditnota"
    # Shares the series with the invoices, so completeness stays countable.
    assert credit["document_number"] == "2"
    assert credit["credits_document_id"] == issued["id"]
    assert credit["gross_total"] == issued["gross_total"]

    # The output VAT nets back to nothing.
    summary = client.get(
        f"/api/v1/companies/{company['id']}/cashflow/summary?financial_year={THIS_YEAR}"
    ).json()
    assert Decimal(summary["output_vat"]) == Decimal("0.00")

    # And the invoice now points at the note that reversed it.
    original = client.get(f"/api/v1/companies/{company['id']}/faktura/{issued['id']}").json()
    assert original["credited_by_document_id"] == credit["id"]


def test_an_invoice_can_only_be_credited_once(client: TestClient) -> None:
    company = signed_in_company(client)
    draft = create_draft(client, company["id"])
    issued = issue(client, company["id"], draft["id"]).json()
    url = f"/api/v1/companies/{company['id']}/faktura/{issued['id']}/kreditnota"

    assert write(client, "POST", url, json={}).status_code == 201
    second = write(client, "POST", url, json={})
    assert second.status_code == 409
    assert second.json()["code"] == "already_credited"


def test_a_draft_cannot_be_credited(client: TestClient) -> None:
    company = signed_in_company(client)
    draft = create_draft(client, company["id"])
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/faktura/{draft['id']}/kreditnota",
        json={},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "not_creditable"


# --- The two documents --------------------------------------------------------------------


def test_the_ehf_is_served_exactly_as_it_was_frozen(client: TestClient) -> None:
    company = signed_in_company(client)
    draft = create_draft(client, company["id"])
    issued = issue(client, company["id"], draft["id"]).json()

    response = client.get(f"/api/v1/companies/{company['id']}/faktura/{issued['id']}/ehf")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "faktura-1.xml" in response.headers["content-disposition"]
    body = response.text
    assert "urn:cen.eu:en16931:2017#compliant" in body
    assert "NO910000004MVA" in body
    # Two reads give the same bytes: it is stored, not regenerated.
    assert client.get(f"/api/v1/companies/{company['id']}/faktura/{issued['id']}/ehf").text == body


def test_a_draft_has_no_ehf_yet(client: TestClient) -> None:
    company = signed_in_company(client)
    draft = create_draft(client, company["id"])
    response = client.get(f"/api/v1/companies/{company['id']}/faktura/{draft['id']}/ehf")
    assert response.status_code == 404
    assert response.json()["code"] == "ehf_not_available"


def test_the_pdf_is_downloadable_for_drafts_and_issued_documents(client: TestClient) -> None:
    company = signed_in_company(client)
    draft = create_draft(client, company["id"])

    preview = client.get(f"/api/v1/companies/{company['id']}/faktura/{draft['id']}/pdf")
    assert preview.status_code == 200
    assert preview.content.startswith(b"%PDF-")

    issued = issue(client, company["id"], draft["id"]).json()
    final = client.get(f"/api/v1/companies/{company['id']}/faktura/{issued['id']}/pdf")
    assert final.content.startswith(b"%PDF-")
    assert "faktura-1.pdf" in final.headers["content-disposition"]


def test_issuing_files_the_pdf_in_the_append_only_bilag_archive(client: TestClient) -> None:
    """Bokføringsloven § 13: the document has to stay readable for five years."""
    company = signed_in_company(client)
    draft = create_draft(client, company["id"])
    issue(client, company["id"], draft["id"])

    entries = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    # The invoice voucher has no payment line, so it is not a cash-flow entry —
    # but its attachment is in the same archive, reachable by id.
    assert entries == []

    listed = client.get(f"/api/v1/companies/{company['id']}/faktura").json()
    voucher_id = listed[0]["voucher_id"]
    assert voucher_id is not None


# --- Access control -------------------------------------------------------------------------


def test_another_organizations_invoice_is_not_found(client: TestClient) -> None:
    first = signed_in_company(client)
    draft = create_draft(client, first["id"])
    write(client, "POST", "/api/v1/auth/logout")

    session = register(client, "ola@example.no", organization_name="Ola AS")
    other = make_company(client, session["memberships"][0]["organization_id"], orgnr="910000012")
    # Reached through the intruder's own company id, so the 404 is about the
    # document rather than about the company.
    response = client.get(f"/api/v1/companies/{other['id']}/faktura/{draft['id']}")
    assert response.status_code == 404


@pytest.mark.parametrize("role", ["viewer"])
def test_a_viewer_may_read_but_not_issue(client: TestClient, role: str) -> None:
    owner = register(client)
    organization_id = owner["memberships"][0]["organization_id"]
    company = make_company(client, organization_id)
    draft = create_draft(client, company["id"])

    join_company(client, company["id"], email="revisor@example.no", role=role)

    assert client.get(f"/api/v1/companies/{company['id']}/faktura/{draft['id']}").status_code == 200
    assert issue(client, company["id"], draft["id"]).status_code == 403


def test_the_catalogue_lists_the_sellable_codes_and_the_cutover_date(
    client: TestClient,
) -> None:
    register(client)
    catalogue = client.get("/api/v1/faktura/katalog").json()

    codes = {option["tax_code"] for option in catalogue["tax_options"]}
    assert codes == {"3", "31", "33", "5", "6", "52", "0"}
    assert "1" not in codes  # inngående mva is not sellable
    assert catalogue["e_invoice_mandatory_from"] == "2027-01-01"
    assert {unit["code"] for unit in catalogue["units"]} >= {"EA", "HUR", "KGM"}
    # No LOYND_KEY in the test settings, so the form is told not to offer the box.
    assert catalogue["ai_available"] is False


# --- KID ------------------------------------------------------------------------------


def patch(client: TestClient, company_id: int, document_id: int, **fields: Any) -> Any:
    return write(
        client,
        "PATCH",
        f"/api/v1/companies/{company_id}/faktura/{document_id}",
        json=fields,
    )


def test_a_draft_has_no_kid_because_it_has_no_number_yet(client: TestClient) -> None:
    """The KID carries the invoice number, and numbering waits for issue."""
    company = signed_in_company(client)
    draft = create_draft(client, company["id"])

    assert draft["payment_reference"] is None
    assert draft["payment_reference_auto"] is True


def test_issuing_assigns_the_kid_from_the_invoice_number(client: TestClient) -> None:
    company = signed_in_company(client)
    draft = create_draft(client, company["id"])

    issued = issue(client, company["id"], draft["id"]).json()

    assert issued["document_number"] == "1"
    assert issued["payment_reference"] == kid.for_document_number("1")
    assert kid.is_valid(issued["payment_reference"])


def test_the_kid_is_on_the_printed_invoice_and_in_the_e_invoice(client: TestClient) -> None:
    """A payment reference the documents do not show is one nobody can pay by."""
    company = signed_in_company(client)
    issued = issue(client, company["id"], create_draft(client, company["id"])["id"]).json()
    reference = issued["payment_reference"]

    xml = client.get(f"/api/v1/companies/{company['id']}/faktura/{issued['id']}/ehf").text
    assert f"<cbc:PaymentID>{reference}</cbc:PaymentID>" in xml

    pdf = client.get(f"/api/v1/companies/{company['id']}/faktura/{issued['id']}/pdf").content
    assert pdf.startswith(b"%PDF")


def test_the_preview_says_what_the_next_invoice_would_get(client: TestClient) -> None:
    company = signed_in_company(client)

    upcoming = client.get(f"/api/v1/companies/{company['id']}/faktura/neste-nummer").json()
    assert upcoming == {"document_number": "1", "payment_reference": "18"}

    # And it is a preview, not a reservation: nothing was consumed.
    issued = issue(client, company["id"], create_draft(client, company["id"])["id"]).json()
    assert issued["document_number"] == "1"
    assert issued["payment_reference"] == upcoming["payment_reference"]

    # It moves with the series.
    after = client.get(f"/api/v1/companies/{company['id']}/faktura/neste-nummer").json()
    assert after["document_number"] == "2"


def make_customer(client: TestClient, company_id: int, **overrides: Any) -> dict[str, Any]:
    payload = {
        "party_type": "kunde",
        "name": "Kunde AS",
        "orgnr": CUSTOMER_ORGNR,
        "address_line": "Storgata 1",
        "postal_code": "0155",
        "city": "Oslo",
        **overrides,
    }
    response = write(client, "POST", f"/api/v1/companies/{company_id}/parties", json=payload)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def test_a_registered_customer_gets_the_same_short_kid(client: TestClient) -> None:
    """Invoice 1 is «18» whoever it is to: the invoice number already names the buyer."""
    company = signed_in_company(client)
    customer = make_customer(client, company["id"])

    draft = create_draft(client, company["id"], party_id=customer["id"], buyer_name=None)
    issued = issue(client, company["id"], draft["id"]).json()

    assert issued["payment_reference"] == "18"


def test_a_buyer_typed_onto_the_document_gets_no_zeros(client: TestClient) -> None:
    """It used to be 000000000018, which nobody copies off a paper invoice correctly."""
    company = signed_in_company(client)
    issued = issue(client, company["id"], create_draft(client, company["id"])["id"]).json()

    assert issued["payment_reference"] == "18"


def test_the_preview_ignores_a_customer_it_is_asked_about(client: TestClient) -> None:
    """The route used to take party_id, and an old caller sending one still gets an answer."""
    company = signed_in_company(client)
    customer = make_customer(client, company["id"])

    anonymous = client.get(f"/api/v1/companies/{company['id']}/faktura/neste-nummer").json()
    named = client.get(
        f"/api/v1/companies/{company['id']}/faktura/neste-nummer?party_id={customer['id']}"
    ).json()

    assert named == anonymous


def test_a_seller_may_turn_the_kid_off(client: TestClient) -> None:
    company = signed_in_company(client)
    draft = create_draft(client, company["id"], payment_reference_auto=False)

    issued = issue(client, company["id"], draft["id"]).json()
    assert issued["payment_reference"] is None


def test_a_seller_may_supply_their_own_kid(client: TestClient) -> None:
    """From an OCR agreement whose format we do not model. It survives issue."""
    own = "1234567897"
    company = signed_in_company(client)
    draft = create_draft(client, company["id"], payment_reference=own, payment_reference_auto=False)
    assert draft["payment_reference"] == own

    issued = issue(client, company["id"], draft["id"]).json()
    assert issued["payment_reference"] == own


def test_switching_back_to_automatic_clears_the_typed_number(client: TestClient) -> None:
    """Otherwise the form shows a KID the invoice will not carry."""
    company = signed_in_company(client)
    draft = create_draft(
        client, company["id"], payment_reference="1234567897", payment_reference_auto=False
    )

    back = patch(client, company["id"], draft["id"], payment_reference_auto=True).json()
    assert back["payment_reference"] is None
    assert back["payment_reference_auto"] is True


def test_an_empty_string_is_how_a_kid_is_removed(client: TestClient) -> None:
    """``None`` means "leave it alone" everywhere else in the payload."""
    company = signed_in_company(client)
    draft = create_draft(
        client, company["id"], payment_reference="1234567897", payment_reference_auto=False
    )

    cleared = patch(client, company["id"], draft["id"], payment_reference="").json()
    assert cleared["payment_reference"] is None
    assert cleared["payment_reference_auto"] is False


def test_a_hand_typed_kid_with_a_bad_check_digit_is_a_warning_not_a_refusal(
    client: TestClient,
) -> None:
    """A KID is a banking convenience; § 5-1 does not ask for one at all."""
    company = signed_in_company(client)
    draft = create_draft(
        client, company["id"], payment_reference="0000011", payment_reference_auto=False
    )

    found = client.get(f"/api/v1/companies/{company['id']}/faktura/{draft['id']}/kontroll").json()
    warning = next(v for v in found if v["field"] == "payment_reference")
    assert warning["severity"] == "advarsel"

    # And it still issues, carrying the number the seller insisted on.
    issued = issue(client, company["id"], draft["id"])
    assert issued.status_code == 201
    assert issued.json()["payment_reference"] == "0000011"


def test_an_automatic_kid_never_warns(client: TestClient) -> None:
    company = signed_in_company(client)
    draft = create_draft(client, company["id"])

    found = client.get(f"/api/v1/companies/{company['id']}/faktura/{draft['id']}/kontroll").json()
    assert [v for v in found if v["field"] == "payment_reference"] == []


def test_a_kreditnota_carries_no_kid(client: TestClient) -> None:
    """It reverses a payment rather than asking for one."""
    company = signed_in_company(client)
    issued = issue(client, company["id"], create_draft(client, company["id"])["id"]).json()
    assert issued["payment_reference"] is not None

    note = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/faktura/{issued['id']}/kreditnota",
        json={},
    ).json()
    assert note["payment_reference"] is None
    assert note["payment_reference_auto"] is False


# --- Proposed lines -------------------------------------------------------------------


def draft_lines(client: TestClient, company_id: int, text: str) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/faktura/linjeforslag",
        json={"text": text},
    )


def answering(reply: str) -> Any:
    async def complete(*, system: str, prompt: str, max_tokens: int = 16) -> str:
        del system, prompt, max_tokens
        return reply

    return complete


def test_a_described_sale_comes_back_as_lines_the_form_can_take(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "complete", answering("Rådgivning mars | 10 | timer | 1500 | 1"))

    company = signed_in_company(client)
    proposed = draft_lines(client, company["id"], "10 timer rådgivning à 1500").json()

    assert proposed["lines"] == [
        {
            "description": "Rådgivning mars",
            "quantity": "10",
            "unit_code": "HUR",
            "unit_price": "1500",
            "tax_code": "3",
        }
    ]
    # And the shape is the one a draft takes back, unedited.
    draft = create_draft(client, company["id"], lines=proposed["lines"])
    assert draft["lines"][0]["net_amount"] == "15000.00"


def test_a_company_outside_the_vat_register_is_proposed_no_vat(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The taxable rates are never on the menu, so the answer cannot carry one."""
    seen: list[str] = []

    async def complete(*, system: str, prompt: str, max_tokens: int = 16) -> str:
        del system, max_tokens
        seen.append(prompt)
        return "Klipp | 1 | stk | 500 | 1"

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "complete", complete)

    session = register(client)
    company = make_company(
        client, session["memberships"][0]["organization_id"], vat_registered=False
    )
    proposed = draft_lines(client, company["id"], "Klippet håret til en kunde, 500 kr").json()

    assert "25 %" not in seen[0]
    assert proposed["lines"][0]["tax_code"] == "0"


def test_nothing_is_stored_by_asking(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "complete", answering("Rådgivning | 10 | timer | 1500 | 1"))

    company = signed_in_company(client)
    draft_lines(client, company["id"], "10 timer rådgivning")

    assert client.get(f"/api/v1/companies/{company['id']}/faktura").json() == []


def test_a_model_that_does_not_answer_is_not_an_error_for_the_user(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refusing(**kwargs: Any) -> str:
        raise loynd.LoyndUnavailableError("nede")

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "complete", refusing)

    company = signed_in_company(client)
    response = draft_lines(client, company["id"], "10 timer rådgivning")

    assert response.status_code == 200
    assert response.json()["lines"] == []
    assert response.json()["warnings"] != []


def test_a_deployment_without_a_key_says_so_rather_than_failing(client: TestClient) -> None:
    company = signed_in_company(client)
    response = draft_lines(client, company["id"], "10 timer rådgivning")

    assert response.status_code == 200
    assert response.json()["lines"] == []
    assert "Løynd" in response.json()["warnings"][0]


def test_a_description_longer_than_we_accept_is_refused_before_it_is_sent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = False

    async def complete(**kwargs: Any) -> str:
        nonlocal asked
        asked = True
        return ""

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "complete", complete)

    company = signed_in_company(client)
    assert draft_lines(client, company["id"], "x" * 5000).status_code == 422
    assert not asked


# --- A proposed invoice ---------------------------------------------------------------
#
# What the box at the top of the form does: a pasted text in, the buyer, the
# terms and the lines out, then a draft made from exactly that and issued. The
# model is a stand-in; what is tested is that its answer becomes a correct
# invoice, and that nothing reaches the invoice the text did not say.

# The fields of a proposal that are also fields of a draft. The rest
# (party_match, invoice_email, warnings) are for the person reading it.
DRAFT_FIELDS = (
    "party_id",
    "buyer_name",
    "buyer_orgnr",
    "buyer_address_line",
    "buyer_postal_code",
    "buyer_city",
    "buyer_is_business",
    "delivery_date",
    "delivery_place",
    "due_date",
    "buyer_reference",
    "lines",
)


def draft_invoice(client: TestClient, company_id: int, text: str) -> Any:
    return write(
        client, "POST", f"/api/v1/companies/{company_id}/faktura/forslag", json={"text": text}
    )


def as_draft(proposal: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """The proposal as a draft takes it: what it said, and nothing it left open."""
    body = {name: proposal[name] for name in DRAFT_FIELDS if proposal[name] is not None}
    body.update(extra)
    return body


def model_answering(**replies: str) -> Any:
    """A stand-in that answers by which text it was shown: {word in the text: reply}."""

    async def complete(*, system: str, prompt: str, max_tokens: int = 16) -> str:
        del system, max_tokens
        return next(reply for word, reply in replies.items() if word in prompt)

    return complete


def oslo_today() -> date:
    return utc_now().astimezone(ZoneInfo("Europe/Oslo")).date()


def test_a_customers_email_and_a_described_sale_become_an_issued_invoice(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case from a real customer: their details in one box, the sale in the other."""
    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(
        loynd,
        "complete",
        model_answering(
            Fakturaadresse=CUSTOMER_EMAIL_REPLY,
            kursmapper=(
                "kunde: -\nadresse: -\nreferanse: -\nleveringsdato: -\nleveringssted: -\n"
                "forfall: -\nRådgivning | 10 | timer | 1500 | 1\nKursmappe | 2 | stk | 450 | 1"
            ),
        ),
    )
    company = signed_in_company(client)

    proposed = draft_invoice(client, company["id"], CUSTOMER_EMAIL)
    assert proposed.status_code == 200, proposed.text
    buyer = proposed.json()
    assert buyer["party_id"] is None
    assert buyer["party_match"] is None
    assert buyer["buyer_name"] == "Fjord Aluminium AS"
    assert buyer["buyer_orgnr"] == "910000039"
    assert buyer["buyer_address_line"] == "Postboks 53"
    assert buyer["buyer_postal_code"] == "6601"
    assert buyer["buyer_city"] == "Sunndalsøra"
    assert buyer["buyer_is_business"] is True
    assert buyer["buyer_reference"] == "GS-HR00003-02-05, Att: Kari Nordmann"
    assert buyer["invoice_email"] == "faktura@fjordaluminium.no"
    # The e-mail named no day, place or term, and none was made up for it.
    assert (buyer["delivery_date"], buyer["delivery_place"], buyer["due_date"]) == (None,) * 3
    assert buyer["lines"] == []
    assert any("faktura@fjordaluminium.no" in w for w in buyer["warnings"])
    assert any("ingen linjer" in w for w in buyer["warnings"])

    lines = draft_lines(
        client, company["id"], "10 timer rådgivning à 1500 og to kursmapper à 450"
    ).json()["lines"]
    assert len(lines) == 2

    body = as_draft(
        buyer, lines=lines, delivery_date=DELIVERED.isoformat(), delivery_place="Sunndalsøra"
    )
    created = write(client, "POST", f"/api/v1/companies/{company['id']}/faktura", json=body)
    assert created.status_code == 201, created.text
    draft = created.json()
    assert [v for v in draft["violations"] if v["severity"] == "blokkerende"] == []

    issued = issue(client, company["id"], draft["id"])
    assert issued.status_code == 201, issued.text
    invoice = issued.json()
    assert invoice["document_number"] == "1"
    assert invoice["buyer_name"] == "Fjord Aluminium AS"
    assert invoice["buyer_orgnr"] == "910000039"
    assert invoice["buyer_address_line"] == "Postboks 53"
    assert (invoice["buyer_postal_code"], invoice["buyer_city"]) == ("6601", "Sunndalsøra")
    assert invoice["buyer_reference"] == "GS-HR00003-02-05, Att: Kari Nordmann"
    assert invoice["net_total"] == "15900.00"
    assert invoice["vat_total"] == "3975.00"
    assert invoice["gross_total"] == "19875.00"
    assert invoice["payment_reference"] == "18"
    assert invoice["voucher_id"] is not None

    # The e-invoice carries what the customer asked it to be marked with, and is
    # addressed by their number.
    xml = client.get(f"/api/v1/companies/{company['id']}/faktura/{invoice['id']}/ehf").text
    assert "<cbc:BuyerReference>GS-HR00003-02-05, Att: Kari Nordmann</cbc:BuyerReference>" in xml
    assert "910000039" in xml


def test_a_described_sale_to_a_registered_customer_issues_as_proposed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = (
        f"Faktura til Bygg og Anlegg AS for 10 timer rådgivning levert 14. mars {THIS_YEAR} "
        "i Bergen, 1 500 kroner timen. Forfall 30 dager. Deres ref: Ola Nordmann"
    )
    reply = (
        f"kunde: Bygg og Anlegg AS\nadresse: -\nreferanse: Ola Nordmann\n"
        f"leveringsdato: {THIS_YEAR}-03-14\nleveringssted: Bergen\nforfall: 30 dager\n"
        "Rådgivning | 10 | timer | 1500 | 1"
    )
    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "complete", answering(reply))
    company = signed_in_company(client)
    customer = make_customer(client, company["id"], name="Bygg og Anlegg AS")

    proposal = draft_invoice(client, company["id"], text).json()
    assert proposal["party_id"] == customer["id"]
    assert proposal["party_match"] == "navn"
    assert proposal["buyer_is_business"] is True
    assert proposal["delivery_date"] == DELIVERED.isoformat()
    assert proposal["delivery_place"] == "Bergen"
    assert proposal["due_date"] == (oslo_today() + timedelta(days=30)).isoformat()
    assert proposal["buyer_reference"] == "Ola Nordmann"
    assert proposal["warnings"] == []

    created = write(
        client, "POST", f"/api/v1/companies/{company['id']}/faktura", json=as_draft(proposal)
    )
    assert created.status_code == 201, created.text
    invoice = issue(client, company["id"], created.json()["id"]).json()

    assert invoice["status"] == "utstedt"
    assert invoice["party_id"] == customer["id"]
    # The register's details, not the sentence's.
    assert invoice["buyer_name"] == "Bygg og Anlegg AS"
    assert invoice["buyer_orgnr"] == CUSTOMER_ORGNR
    assert invoice["buyer_address_line"] == "Storgata 1"
    assert invoice["delivery_date"] == DELIVERED.isoformat()
    assert invoice["delivery_place"] == "Bergen"
    assert invoice["due_date"] == proposal["due_date"]
    assert [line["net_amount"] for line in invoice["lines"]] == ["15000.00"]
    assert invoice["gross_total"] == "18750.00"
    assert invoice["payment_reference"] == "18"

    summary = client.get(
        f"/api/v1/companies/{company['id']}/cashflow/summary?financial_year={THIS_YEAR}"
    ).json()
    assert Decimal(summary["output_vat"]) == Decimal("3750.00")


def test_a_new_address_in_the_text_for_a_registered_customer_is_pointed_out(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The draft prints the register's address, so the e-mail's new one must be read."""
    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "complete", answering(CUSTOMER_EMAIL_REPLY))
    company = signed_in_company(client)
    customer = make_customer(client, company["id"], name="Fjord Aluminium AS", orgnr="910000039")

    proposal = draft_invoice(client, company["id"], CUSTOMER_EMAIL).json()

    assert proposal["party_id"] == customer["id"]
    assert proposal["party_match"] == "orgnr"
    assert any("annen fakturaadresse" in w for w in proposal["warnings"])


def test_another_companys_customer_is_never_proposed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One account, two sets of books: a name that fits next door is not a customer here."""
    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(
        loynd, "complete", answering("kunde: Bygg og Anlegg AS\nRådgivning | 1 | stk | 900 | 1")
    )
    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    ours = make_company(client, organization_id)
    theirs = make_company(client, organization_id, orgnr="910000020", name="Annen Drift AS")
    make_customer(client, theirs["id"], name="Bygg og Anlegg AS")

    proposal = draft_invoice(client, ours["id"], "Rådgivning for Bygg og Anlegg AS, 900").json()

    assert proposal["party_id"] is None
    assert proposal["party_match"] is None
    assert proposal["buyer_name"] == "Bygg og Anlegg AS"


def test_a_buyer_the_text_does_not_name_never_reaches_the_proposal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(
        loynd,
        "complete",
        answering(
            "kunde: Hansen Bygg AS\nleveringsdato: 2026-03-31\nRådgivning | 10 | timer | 1500 | 1"
        ),
    )
    company = signed_in_company(client)
    make_customer(client, company["id"], name="Hansen Bygg AS")

    proposal = draft_invoice(client, company["id"], "10 timer rådgivning i mars à 1500").json()

    assert proposal["buyer_name"] is None
    assert proposal["party_id"] is None
    assert proposal["delivery_date"] is None
    assert len(proposal["lines"]) == 1


def test_a_text_with_nothing_in_it_says_so(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "complete", answering("kunde: -\nadresse: -"))
    company = signed_in_company(client)

    proposal = draft_invoice(client, company["id"], "Takk for sist!").json()

    assert proposal["lines"] == []
    assert proposal["buyer_name"] is None
    assert proposal["warnings"][0].startswith("Vi fant verken kjøper eller fakturalinjer")


def test_a_company_that_switched_ki_off_is_not_asked_about(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = False

    async def complete(**kwargs: Any) -> str:
        nonlocal asked
        asked = True
        return ""

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "complete", complete)
    company = signed_in_company(client)
    write(client, "PATCH", f"/api/v1/companies/{company['id']}", json={"ki_enabled": False})

    response = draft_invoice(client, company["id"], CUSTOMER_EMAIL)

    assert response.status_code == 200
    assert response.json()["buyer_name"] is None
    assert "KI er slått av" in response.json()["warnings"][0]
    assert not asked


def test_a_proposal_without_a_model_is_an_empty_form_not_an_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = signed_in_company(client)
    unconfigured = draft_invoice(client, company["id"], CUSTOMER_EMAIL)
    assert unconfigured.status_code == 200
    assert "Løynd" in unconfigured.json()["warnings"][0]

    async def refusing(**kwargs: Any) -> str:
        raise loynd.LoyndUnavailableError("nede")

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "complete", refusing)
    down = draft_invoice(client, company["id"], CUSTOMER_EMAIL)
    assert down.status_code == 200
    assert down.json()["lines"] == []
    assert "svar fra Løynd" in down.json()["warnings"][0]


def test_a_viewer_has_nothing_to_propose(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "complete", answering(CUSTOMER_EMAIL_REPLY))
    owner = register(client)
    company = make_company(client, owner["memberships"][0]["organization_id"])
    join_company(client, company["id"], email="revisor@example.no", role="viewer")

    assert draft_invoice(client, company["id"], CUSTOMER_EMAIL).status_code == 403


def test_a_text_longer_than_we_accept_is_refused_before_it_is_sent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = False

    async def complete(**kwargs: Any) -> str:
        nonlocal asked
        asked = True
        return ""

    monkeypatch.setattr(loynd, "is_configured", lambda: True)
    monkeypatch.setattr(loynd, "complete", complete)
    company = signed_in_company(client)

    assert draft_invoice(client, company["id"], "x" * 2001).status_code == 422
    assert not asked
