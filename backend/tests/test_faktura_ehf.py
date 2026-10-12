"""The EHF / Peppol BIS Billing 3.0 output.

These check the two things a generator most easily gets wrong and a recipient's
validator will certainly catch: **element order**, which UBL fixes by
``xsd:sequence``, and the **VAT breakdown**, which EN 16931 cross-checks against
the document total.

Parsing the result back rather than matching strings: what matters is the tree
the recipient sees, and a test that asserts on formatting breaks every time the
indentation changes without ever catching a real fault.
"""

from datetime import date
from decimal import Decimal
from typing import Any
from xml.etree import ElementTree as ET

import pytest

from myntos.domain.faktura import ehf
from myntos.domain.faktura.models import DocumentType, InvoiceStatus, SalesDocument
from tests.test_faktura_domain import make_document, make_line

CBC = ehf.CBC
CAC = ehf.CAC


def parse(xml: str) -> ET.Element:
    return ET.fromstring(xml)  # noqa: S314 - our own output, not untrusted input


def local_names(element: ET.Element) -> list[str]:
    """Child tags without their namespace, in document order."""
    return [child.tag.rsplit("}", maxsplit=1)[-1] for child in element]


def find(element: ET.Element, path: str) -> ET.Element:
    """``cac:Foo/cbc:Bar`` with the prefixes resolved."""
    found = element.find(path, {"cac": CAC, "cbc": CBC})
    assert found is not None, f"{path} missing"
    return found


def issued(**overrides: Any) -> SalesDocument:
    document = make_document(**overrides)
    document.status = InvoiceStatus.UTSTEDT
    document.document_number = "1"
    document.issue_date = date(2026, 8, 16)
    return document


def test_the_document_is_well_formed_and_names_the_peppol_profile() -> None:
    root = parse(ehf.render(issued()))

    assert root.tag == f"{{{ehf.INVOICE_NS}}}Invoice"
    assert find(root, "cbc:CustomizationID").text == ehf.CUSTOMIZATION_ID
    assert find(root, "cbc:ProfileID").text == ehf.PROFILE_ID
    assert find(root, "cbc:InvoiceTypeCode").text == "380"
    assert find(root, "cbc:DocumentCurrencyCode").text == "NOK"


def test_invoice_head_follows_the_ubl_sequence() -> None:
    """UBL declares these in xsd:sequence; the wrong order is an invalid document."""
    root = parse(ehf.render(issued(buyer_reference="Avd. 4", order_reference="PO-99")))
    names = local_names(root)

    expected = [
        "CustomizationID",
        "ProfileID",
        "ID",
        "IssueDate",
        "DueDate",
        "InvoiceTypeCode",
        "DocumentCurrencyCode",
        "BuyerReference",
        "OrderReference",
        "AccountingSupplierParty",
        "AccountingCustomerParty",
        "Delivery",
        "TaxTotal",
        "LegalMonetaryTotal",
        "InvoiceLine",
    ]
    positions = [names.index(name) for name in expected]
    assert positions == sorted(positions), names


def test_credit_note_uses_its_own_root_type_code_and_element_order() -> None:
    """CreditNote runs TypeCode before Note; Invoice runs DueDate before TypeCode."""
    document = issued()
    document.document_type = DocumentType.KREDITNOTA
    original = issued()

    root = parse(ehf.render(document, original))
    names = local_names(root)

    assert root.tag == f"{{{ehf.CREDIT_NOTE_NS}}}CreditNote"
    assert find(root, "cbc:CreditNoteTypeCode").text == "381"
    assert "DueDate" not in names
    assert "CreditNoteLine" in names
    assert "InvoiceLine" not in names
    # § 5-2-7: the credit note points back at the document it reverses.
    reference = find(root, "cac:BillingReference/cac:InvoiceDocumentReference")
    assert find(reference, "cbc:ID").text == "1"


def test_the_seller_vat_identifier_carries_the_no_mva_form() -> None:
    root = parse(ehf.render(issued()))
    party = find(root, "cac:AccountingSupplierParty/cac:Party")

    assert find(party, "cac:PartyTaxScheme/cbc:CompanyID").text == "NO910000004MVA"
    endpoint = find(party, "cbc:EndpointID")
    assert endpoint.attrib["schemeID"] == ehf.NORWEGIAN_ORGNR_SCHEME
    assert endpoint.text == "910000004"


def test_an_unregistered_seller_asserts_no_vat_identifier() -> None:
    root = parse(ehf.render(issued(vat_registered=False, lines=[make_line(tax_code="0")])))
    party = find(root, "cac:AccountingSupplierParty/cac:Party")

    schemes = {
        find(scheme, "cac:TaxScheme/cbc:ID").text: find(scheme, "cbc:CompanyID").text
        for scheme in party.findall("cac:PartyTaxScheme", {"cac": CAC})
    }
    # No VAT scheme, and so no NO...MVA. What it does carry is its orgnr as the
    # tax registration (BT-32), because BR-E-02 refuses its category-E lines
    # without one.
    assert schemes == {ehf.TAX_REGISTRATION_SCHEME: "910000004"}
    # And the legal entity is still identified, which BR-CO-26 requires.
    assert find(party, "cac:PartyLegalEntity/cbc:CompanyID").text == "910000004"


def test_foretaksregisteret_is_the_tax_registration_peppol_looks_for() -> None:
    """NO-R-002 reads the marking from PartyTaxScheme under scheme TAX, not from a note."""
    root = parse(ehf.render(issued(seller_in_foretaksregisteret=True)))
    party = find(root, "cac:AccountingSupplierParty/cac:Party")
    schemes = {
        find(scheme, "cac:TaxScheme/cbc:ID").text: find(scheme, "cbc:CompanyID").text
        for scheme in party.findall("cac:PartyTaxScheme", {"cac": CAC})
    }
    assert schemes == {"VAT": "NO910000004MVA", "TAX": "Foretaksregisteret"}


def test_the_markings_share_one_note() -> None:
    """PEPPOL-EN16931-R002: a single document-level note outside Germany."""
    document = issued(seller_in_foretaksregisteret=True, note="Takk for handelen")
    document.document_type = DocumentType.KREDITNOTA
    root = parse(ehf.render(document, issued()))

    notes = root.findall(f"{{{CBC}}}Note")
    assert len(notes) == 1
    assert (notes[0].text or "").split("\n") == [
        "Foretaksregisteret",
        "Kreditnota",
        "Takk for handelen",
    ]


def test_a_document_without_any_reference_says_so() -> None:
    """PEPPOL-EN16931-R003 wants BT-10 or BT-13; neither is the seller's to invent."""
    root = parse(ehf.render(issued(buyer_reference=None, order_reference=None)))
    assert find(root, "cbc:BuyerReference").text == ehf.NO_BUYER_REFERENCE

    with_order = parse(ehf.render(issued(buyer_reference=None, order_reference="PO-1")))
    assert with_order.find(f"{{{CBC}}}BuyerReference") is None

    with_own = parse(ehf.render(issued(buyer_reference="Avd. 4")))
    assert find(with_own, "cbc:BuyerReference").text == "Avd. 4"


def test_the_buyer_endpoint_is_what_peppol_routes_on() -> None:
    root = parse(ehf.render(issued()))
    party = find(root, "cac:AccountingCustomerParty/cac:Party")
    assert find(party, "cbc:EndpointID").text == "910000012"


def test_both_parties_always_carry_a_country_code() -> None:
    """BR-09 and BR-11: mandatory even when the rest of the address is unknown."""
    root = parse(ehf.render(issued(buyer_address_line=None, buyer_postal_code=None)))
    for side in ("AccountingSupplierParty", "AccountingCustomerParty"):
        country = find(
            root, f"cac:{side}/cac:Party/cac:PostalAddress/cac:Country/cbc:IdentificationCode"
        )
        assert country.text == "NO"


def test_tax_subtotals_mirror_the_paragraph_5_1_5_breakdown() -> None:
    document = issued(
        lines=[
            make_line(1, unit_price="1000.00", tax_code="3"),
            make_line(2, unit_price="200.00", tax_code="33"),
            make_line(3, unit_price="100.00", tax_code="52"),
        ]
    )
    root = parse(ehf.render(document))
    tax_total = find(root, "cac:TaxTotal")
    subtotals = tax_total.findall("cac:TaxSubtotal", {"cac": CAC})

    assert find(tax_total, "cbc:TaxAmount").text == "274.00"  # 250 + 24 + 0
    assert len(subtotals) == 3

    # BR-CO-14: the document tax total is the sum of the breakdown groups.
    assert sum(
        Decimal(find(subtotal, "cbc:TaxAmount").text or "0") for subtotal in subtotals
    ) == Decimal("274.00")

    categories = {find(subtotal, "cac:TaxCategory/cbc:ID").text: subtotal for subtotal in subtotals}
    assert set(categories) == {"S", "G"}
    # BR-G-10: a zero-rated group states its reason.
    assert find(categories["G"], "cac:TaxCategory/cbc:TaxExemptionReason").text


def test_amounts_carry_the_currency_and_exactly_two_decimals() -> None:
    root = parse(ehf.render(issued(lines=[make_line(unit_price="1000.00")])))
    total = find(root, "cac:LegalMonetaryTotal")

    for tag, expected in (
        ("LineExtensionAmount", "1000.00"),
        ("TaxExclusiveAmount", "1000.00"),
        ("TaxInclusiveAmount", "1250.00"),
        ("PayableAmount", "1250.00"),
    ):
        element = find(total, f"cbc:{tag}")
        assert element.text == expected
        assert element.attrib["currencyID"] == "NOK"


def test_a_discount_becomes_a_line_allowance_against_the_full_price() -> None:
    """BT-146 is the item net price; the discount has to be visible, not baked in."""
    document = issued(lines=[make_line(unit_price="1000.00", quantity="2", discount_rate="0.10")])
    root = parse(ehf.render(document))
    line = find(root, "cac:InvoiceLine")

    assert find(line, "cbc:LineExtensionAmount").text == "1800.00"
    assert find(line, "cac:Price/cbc:PriceAmount").text == "1000.0000"
    allowance = find(line, "cac:AllowanceCharge")
    assert find(allowance, "cbc:ChargeIndicator").text == "false"
    assert find(allowance, "cbc:Amount").text == "200.00"
    # BR-42: an allowance needs a reason.
    assert find(allowance, "cbc:AllowanceChargeReason").text


def test_reverse_charge_marks_every_line_and_charges_nothing() -> None:
    root = parse(ehf.render(issued(reverse_charge=True)))

    assert find(root, "cac:TaxTotal/cbc:TaxAmount").text == "0.00"
    category = find(root, "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory")
    assert find(category, "cbc:ID").text == "AE"
    assert find(category, "cbc:Percent").text == "0"
    # § 5-1-1 nr. 7: the prescribed marking travels with the document.
    notes = [note.text or "" for note in root.findall(f"{{{CBC}}}Note")]
    assert any("Omvendt avgiftsplikt" in note for note in notes)


def test_delivery_carries_the_time_and_place_paragraph_5_1_1_nr_4_requires() -> None:
    root = parse(ehf.render(issued()))
    delivery = find(root, "cac:Delivery")

    assert find(delivery, "cbc:ActualDeliveryDate").text == "2026-08-01"
    location = find(delivery, "cac:DeliveryLocation")
    # cac:Address inside DeliveryLocation, not cac:PostalAddress.
    assert find(location, "cac:Address/cbc:StreetName").text == "Oslo"


def test_payment_means_appears_only_when_there_is_somewhere_to_pay() -> None:
    """BR-49 wants a means code on any PaymentMeans present, so it is all or nothing."""
    # A complete document now carries an account — compliance blocks an invoice
    # without one — so this case has to be asked for explicitly. It still has to
    # render: a kreditnota is exempt, and so is a document from before the check.
    without = parse(ehf.render(issued(seller_bank_account=None)))
    assert without.find("cac:PaymentMeans", {"cac": CAC}) is None

    with_account = parse(
        ehf.render(issued(seller_bank_account="15062033158", payment_reference="123456785"))
    )
    means = find(with_account, "cac:PaymentMeans")
    assert find(means, "cbc:PaymentMeansCode").text == ehf.PAYMENT_MEANS_CREDIT_TRANSFER
    assert find(means, "cbc:PaymentID").text == "123456785"
    assert find(means, "cac:PayeeFinancialAccount/cbc:ID").text == "15062033158"


def test_norwegian_characters_survive_the_round_trip() -> None:
    root = parse(ehf.render(issued(buyer_name="Grønn Ørn Ærlig AS")))
    assert find(root, "cac:AccountingCustomerParty/cac:Party/cac:PartyName/cbc:Name").text == (
        "Grønn Ørn Ærlig AS"
    )


def test_rendering_is_deterministic() -> None:
    """The XML is stored at issue and compared later; two runs must not differ."""
    document = issued(
        lines=[make_line(1, tax_code="3"), make_line(2, tax_code="33"), make_line(3, tax_code="5")]
    )
    assert ehf.render(document) == ehf.render(document)


@pytest.mark.parametrize("tax_code", ["3", "31", "33", "5", "6", "52", "0"])
def test_every_sellable_code_produces_a_valid_category(tax_code: str) -> None:
    root = parse(ehf.render(issued(lines=[make_line(tax_code=tax_code)], vat_registered=True)))
    category = find(root, "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory")
    code = find(category, "cbc:ID").text
    assert code in {"S", "Z", "E", "G", "AE", "K", "O"}
    reason = category.find(f"{{{CBC}}}TaxExemptionReason")
    if code in {"S", "Z"}:
        # S charges VAT; Z is a rate of 0 %, and BR-Z-10 forbids a reason on it.
        assert reason is None
    else:
        # Every other zero category must say why no VAT was charged.
        assert reason is not None and reason.text
