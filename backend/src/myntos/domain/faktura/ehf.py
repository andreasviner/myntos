"""EHF / Peppol BIS Billing 3.0 — the invoice as UBL 2.1 XML.

This is the format lov 19. juni 2026 nr. 39 requires for sales to another
bokføringspliktig. EHF is Norway's profile of Peppol BIS Billing 3.0, which is
itself a CEN EN 16931 implementation on OASIS UBL 2.1 — so the document below
answers to three rule sets at once, and the constraints that look arbitrary are
usually one of them.

Written against the standard rather than through a library, for the same reason
:mod:`myntos.storage.sigv4` signs its own requests: the whole format is a few
hundred lines of element construction, the alternative pulls in a dependency
that must be trusted with the one artefact an auditor will read back, and the
element **order** — the part a generator most easily gets wrong — is fixed by the
XSD sequence either way. Doing it here means the ordering is visible and
testable instead of implicit.

Three things worth knowing before changing anything:

**Order is not style.** UBL declares every element in ``xsd:sequence``, so a
document with the right elements in the wrong order is invalid. ``Invoice`` and
``CreditNote`` do not share an order — ``Invoice`` runs *DueDate,
InvoiceTypeCode, Note*, while ``CreditNote`` runs *DueDate, TaxPointDate,
CreditNoteTypeCode, Note* — which is why the two heads are written out
separately below instead of being parameterised into one.

**VAT is reported per breakdown group, not per line.** ``TaxSubtotal`` mirrors
§ 5-1-5 exactly (:mod:`myntos.domain.faktura.totals`), and EN 16931's
BR-CO-14 checks the document's tax total against the sum of those groups.

**A zero-rate group needs a reason.** BR-E-10, BR-G-10, BR-AE-10 and BR-Z-09 all
require ``TaxExemptionReason`` where no VAT is charged. The reasons come from
:mod:`myntos.domain.faktura.vat` so the printed document and the XML give the
customer the same explanation.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from decimal import Decimal

from myntos.domain.faktura.models import DocumentType, SalesDocument, SalesDocumentLine
from myntos.domain.faktura.totals import DocumentTotals, TaxSubtotal, totals
from myntos.domain.faktura.vat import VatCategory, category_for, percent
from myntos.domain.ledger.posting import ZERO, quantize_money

# Unit prices are carried at four decimals — see myntos.domain.faktura.models.
# Rounding BT-146 to øre would turn an hourly rate of 1 234.5625 into a
# different rate, and the line total is computed from the unrounded value.
PRICE_EXPONENT = Decimal("0.0001")

INVOICE_NS = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
CREDIT_NOTE_NS = "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"

# What makes this an EN 16931-compliant Peppol BIS Billing 3.0 document. The
# receiving access point routes on these two strings, so they are exact.
CUSTOMIZATION_ID = "urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0"
PROFILE_ID = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"

# Peppol's identifier scheme for a Norwegian organisasjonsnummer. This is what
# turns an orgnr into a routable address.
NORWEGIAN_ORGNR_SCHEME = "0192"

# UNCL 4461. 31 is credit transfer — a Norwegian bank payment.
PAYMENT_MEANS_CREDIT_TRANSFER = "31"

# UNCL 5153. The only tax scheme this system deals in.
VAT_SCHEME = "VAT"

# Registered once, at import. ElementTree keeps the prefix table globally, and
# the alternative is prefixes named ns0/ns1 in a document a human has to read.
ET.register_namespace("cac", CAC)
ET.register_namespace("cbc", CBC)


def _plain(value: Decimal) -> str:
    """A decimal without exponent notation.

    ``str(Decimal("1E+2"))`` is ``"1E+2"``, which is a valid decimal to Python and
    not to an XSD validator. Quantities and rates reach this after ``normalize()``
    and would otherwise arrive in exponent form.
    """
    formatted = format(value, "f")
    # format() renders -0 as "-0"; a negative zero quantity or rate is noise
    # that a strict recipient may well reject.
    return "0" if formatted in {"-0", "-0.00"} else formatted


def _money(value: Decimal) -> str:
    """An amount at exactly two decimals, as every EN 16931 amount must be."""
    return _plain(quantize_money(value))


def _sub(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    """Append a child element, skipping it entirely when its text is empty.

    Returning the element even when it was skipped would invite callers to hang
    children off something that is not in the tree, so an empty text is only
    allowed for the container elements that pass ``None`` deliberately.
    """
    element = ET.SubElement(parent, tag, {k: v for k, v in attrs.items() if v})
    if text is not None:
        element.text = text
    return element


def _optional(parent: ET.Element, tag: str, text: str | None, **attrs: str) -> None:
    """Append a child only when there is something to put in it."""
    if text:
        _sub(parent, tag, text, **attrs)


def _cbc(name: str) -> str:
    return f"{{{CBC}}}{name}"


def _cac(name: str) -> str:
    return f"{{{CAC}}}{name}"


# --- Parties ----------------------------------------------------------------------


def _address(
    parent: ET.Element,
    *,
    street: str | None,
    postal_code: str | None,
    city: str | None,
    country: str,
) -> None:
    """A ``cac:PostalAddress``.

    ``cac:Country`` is emitted unconditionally: BR-09 and BR-11 make the country
    code mandatory on both parties, and it is the one field of an address that
    has a defensible default.
    """
    address = _sub(parent, _cac("PostalAddress"))
    _optional(address, _cbc("StreetName"), street)
    _optional(address, _cbc("CityName"), city)
    _optional(address, _cbc("PostalZone"), postal_code)
    country_element = _sub(address, _cac("Country"))
    _sub(country_element, _cbc("IdentificationCode"), country)


def _supplier(parent: ET.Element, document: SalesDocument) -> None:
    """``cac:AccountingSupplierParty`` — us."""
    party = _sub(_sub(parent, _cac("AccountingSupplierParty")), _cac("Party"))
    _sub(
        party,
        _cbc("EndpointID"),
        document.seller_orgnr,
        schemeID=NORWEGIAN_ORGNR_SCHEME,
    )
    identification = _sub(party, _cac("PartyIdentification"))
    _sub(identification, _cbc("ID"), document.seller_orgnr, schemeID=NORWEGIAN_ORGNR_SCHEME)

    name = _sub(party, _cac("PartyName"))
    _sub(name, _cbc("Name"), document.seller_name)

    _address(
        party,
        street=document.seller_address_line,
        postal_code=document.seller_postal_code,
        city=document.seller_city,
        country=document.seller_country,
    )

    # Only when actually registered. A PartyTaxScheme on a business that is not
    # in Merverdiavgiftsregisteret asserts a VAT identifier it does not hold.
    if document.seller_vat_registered:
        tax_scheme_party = _sub(party, _cac("PartyTaxScheme"))
        _sub(tax_scheme_party, _cbc("CompanyID"), f"NO{document.seller_orgnr}MVA")
        _sub(_sub(tax_scheme_party, _cac("TaxScheme")), _cbc("ID"), VAT_SCHEME)

    legal = _sub(party, _cac("PartyLegalEntity"))
    _sub(legal, _cbc("RegistrationName"), document.seller_name)
    _sub(legal, _cbc("CompanyID"), document.seller_orgnr, schemeID=NORWEGIAN_ORGNR_SCHEME)


def _customer(parent: ET.Element, document: SalesDocument) -> None:
    """``cac:AccountingCustomerParty`` — the buyer.

    The endpoint is what a Peppol access point routes on. It is omitted when the
    buyer has no orgnr, which is the consumer case; the compliance check is what
    stops that document being sent to a business.
    """
    party = _sub(_sub(parent, _cac("AccountingCustomerParty")), _cac("Party"))
    if document.buyer_orgnr:
        _sub(party, _cbc("EndpointID"), document.buyer_orgnr, schemeID=NORWEGIAN_ORGNR_SCHEME)
        identification = _sub(party, _cac("PartyIdentification"))
        _sub(identification, _cbc("ID"), document.buyer_orgnr, schemeID=NORWEGIAN_ORGNR_SCHEME)

    name = _sub(party, _cac("PartyName"))
    _sub(name, _cbc("Name"), document.buyer_name)

    _address(
        party,
        street=document.buyer_address_line,
        postal_code=document.buyer_postal_code,
        city=document.buyer_city,
        country=document.buyer_country,
    )

    legal = _sub(party, _cac("PartyLegalEntity"))
    _sub(legal, _cbc("RegistrationName"), document.buyer_name)
    if document.buyer_orgnr:
        _sub(legal, _cbc("CompanyID"), document.buyer_orgnr, schemeID=NORWEGIAN_ORGNR_SCHEME)


# --- Money ------------------------------------------------------------------------


def _tax_category(parent: ET.Element, subtotal: TaxSubtotal) -> None:
    """``cac:TaxCategory`` — the category, its rate, and why it is zero."""
    category = _sub(parent, _cac("TaxCategory"))
    _sub(category, _cbc("ID"), subtotal.category.value)
    _sub(category, _cbc("Percent"), _plain(subtotal.percent))
    if subtotal.category is not VatCategory.STANDARD and subtotal.reason:
        _sub(category, _cbc("TaxExemptionReason"), subtotal.reason)
    _sub(_sub(category, _cac("TaxScheme")), _cbc("ID"), VAT_SCHEME)


def _tax_total(parent: ET.Element, computed: DocumentTotals, currency: str) -> None:
    """``cac:TaxTotal`` with one ``TaxSubtotal`` per § 5-1-5 group."""
    tax_total = _sub(parent, _cac("TaxTotal"))
    _sub(tax_total, _cbc("TaxAmount"), _money(computed.tax_total), currencyID=currency)
    for subtotal in computed.subtotals:
        element = _sub(tax_total, _cac("TaxSubtotal"))
        _sub(
            element,
            _cbc("TaxableAmount"),
            _money(subtotal.taxable_amount),
            currencyID=currency,
        )
        _sub(element, _cbc("TaxAmount"), _money(subtotal.tax_amount), currencyID=currency)
        _tax_category(element, subtotal)


def _monetary_total(parent: ET.Element, computed: DocumentTotals, currency: str) -> None:
    """``cac:LegalMonetaryTotal`` — the foot of the document."""
    total = _sub(parent, _cac("LegalMonetaryTotal"))
    _sub(total, _cbc("LineExtensionAmount"), _money(computed.line_total), currencyID=currency)
    _sub(total, _cbc("TaxExclusiveAmount"), _money(computed.tax_exclusive), currencyID=currency)
    _sub(total, _cbc("TaxInclusiveAmount"), _money(computed.tax_inclusive), currencyID=currency)
    _sub(total, _cbc("PayableAmount"), _money(computed.payable), currencyID=currency)


def _line(
    parent: ET.Element, document: SalesDocument, line: SalesDocumentLine, currency: str
) -> None:
    """One ``cac:InvoiceLine`` / ``cac:CreditNoteLine``.

    A discount is expressed as a line ``AllowanceCharge`` against the undiscounted
    unit price, not by quietly lowering the price: BT-146 is the *item net price*,
    and EN 16931 derives the line amount as ``quantity * price - allowances``. A
    discounted price with no allowance would still add up, but the customer would
    lose the discount they were promised from the face of the document.
    """
    quantity_tag = "InvoicedQuantity" if not document.is_credit_note else "CreditedQuantity"
    element = _sub(parent, _cac("InvoiceLine" if not document.is_credit_note else "CreditNoteLine"))
    _sub(element, _cbc("ID"), str(line.line_no))
    _sub(element, _cbc(quantity_tag), _plain(line.quantity.normalize()), unitCode=line.unit_code)
    _sub(element, _cbc("LineExtensionAmount"), _money(line.net_amount), currencyID=currency)

    extended = quantize_money(line.quantity * line.unit_price)
    discount = extended - line.net_amount
    if discount > ZERO:
        allowance = _sub(element, _cac("AllowanceCharge"))
        # false = allowance (a deduction); true would be a charge.
        _sub(allowance, _cbc("ChargeIndicator"), "false")
        # BR-42: an allowance needs a reason or a reason code.
        _sub(allowance, _cbc("AllowanceChargeReason"), "Rabatt")
        _sub(allowance, _cbc("Amount"), _money(discount), currencyID=currency)

    item = _sub(element, _cac("Item"))
    _sub(item, _cbc("Name"), line.description)
    treatment = category_for(line.tax_code, reverse_charge=document.reverse_charge)
    rate = line.tax_rate if treatment.category is VatCategory.STANDARD else ZERO
    classified = _sub(item, _cac("ClassifiedTaxCategory"))
    _sub(classified, _cbc("ID"), treatment.category.value)
    _sub(classified, _cbc("Percent"), _plain(percent(rate)))
    _sub(_sub(classified, _cac("TaxScheme")), _cbc("ID"), VAT_SCHEME)

    price = _sub(element, _cac("Price"))
    _sub(
        price,
        _cbc("PriceAmount"),
        _plain(line.unit_price.quantize(PRICE_EXPONENT)),
        currencyID=currency,
    )


# --- The document ------------------------------------------------------------------


def _payment(parent: ET.Element, document: SalesDocument) -> None:
    """``cac:PaymentMeans`` and ``cac:PaymentTerms``.

    Emitted only when there is somewhere to pay. An empty ``PaymentMeans`` fails
    BR-49, which wants a means code on any that is present — so the choice is
    between a complete block and none, never a placeholder.
    """
    if document.seller_bank_account:
        means = _sub(parent, _cac("PaymentMeans"))
        _sub(means, _cbc("PaymentMeansCode"), PAYMENT_MEANS_CREDIT_TRANSFER)
        # The KID. Carried here so the payment reconciles at the buyer's end
        # without anyone retyping it.
        _optional(means, _cbc("PaymentID"), document.payment_reference)
        account = _sub(means, _cac("PayeeFinancialAccount"))
        _sub(account, _cbc("ID"), document.seller_bank_account)

    if document.due_date is not None and not document.is_credit_note:
        terms = _sub(parent, _cac("PaymentTerms"))
        _sub(terms, _cbc("Note"), f"Forfall {document.due_date.isoformat()}")


def _delivery(parent: ET.Element, document: SalesDocument) -> None:
    """``cac:Delivery`` — § 5-1-1 nr. 4's leveringstidspunkt and leveringssted."""
    if document.delivery_date is None and not document.delivery_place:
        return
    delivery = _sub(parent, _cac("Delivery"))
    if document.delivery_date is not None:
        _sub(delivery, _cbc("ActualDeliveryDate"), document.delivery_date.isoformat())
    if document.delivery_place:
        location = _sub(delivery, _cac("DeliveryLocation"))
        _address(
            location,
            street=document.delivery_place,
            postal_code=None,
            city=None,
            country=document.seller_country,
        )
        # _address writes cac:PostalAddress; cac:DeliveryLocation wants
        # cac:Address for the same content, so retag it in place rather than
        # duplicating the address builder for one differing element name.
        for child in location:
            if child.tag == _cac("PostalAddress"):
                child.tag = _cac("Address")


def _billing_reference(parent: ET.Element, credited: SalesDocument) -> None:
    """The kreditnota's pointer back to the faktura it reverses (§ 5-2-7)."""
    reference = _sub(_sub(parent, _cac("BillingReference")), _cac("InvoiceDocumentReference"))
    _sub(reference, _cbc("ID"), credited.document_number or "")
    if credited.issue_date is not None:
        _sub(reference, _cbc("IssueDate"), credited.issue_date.isoformat())


def _notes(document: SalesDocument) -> list[str]:
    """Free text plus the markings the document is required to carry.

    Imported lazily: :mod:`myntos.domain.faktura.compliance` reads the totals,
    which read the VAT tables, and importing it at module scope would make the
    exporter depend on the checker for a string.
    """
    from myntos.domain.faktura.compliance import describe_markings

    notes = list(describe_markings(document))
    if document.note:
        notes.append(document.note)
    return notes


def build(document: SalesDocument, credited: SalesDocument | None = None) -> ET.Element:
    """The UBL tree for ``document``.

    ``credited`` is the faktura a kreditnota reverses; it is required for one and
    ignored for the other.

    The root tag is **unqualified** and carries a literal ``xmlns`` attribute
    rather than being written as ``{namespace}Invoice``. That is what makes the
    serialiser emit the canonical EHF shape — a default namespace on the root,
    ``cac``/``cbc`` prefixes below it. ElementTree's own ``default_namespace``
    option would be the obvious way to get there and cannot be used: it rejects
    any unqualified *attribute* name, and UBL's ``currencyID``, ``schemeID`` and
    ``unitCode`` are all unqualified. The tree this returns therefore only has
    the right namespace once serialised; read it back with ``fromstring`` if you
    need to inspect it.
    """
    is_credit_note = document.document_type is DocumentType.KREDITNOTA
    root_ns = CREDIT_NOTE_NS if is_credit_note else INVOICE_NS
    root = ET.Element("CreditNote" if is_credit_note else "Invoice", {"xmlns": root_ns})
    currency = document.currency_code

    _sub(root, _cbc("CustomizationID"), CUSTOMIZATION_ID)
    _sub(root, _cbc("ProfileID"), PROFILE_ID)
    _sub(root, _cbc("ID"), document.document_number or "")
    if document.issue_date is not None:
        _sub(root, _cbc("IssueDate"), document.issue_date.isoformat())

    # The two document types order their head differently — see the module
    # docstring. Invoice: DueDate, TypeCode, Note. CreditNote: TypeCode, Note.
    if is_credit_note:
        _sub(root, _cbc("CreditNoteTypeCode"), document.document_type.ubl_type_code)
    else:
        if document.due_date is not None:
            _sub(root, _cbc("DueDate"), document.due_date.isoformat())
        _sub(root, _cbc("InvoiceTypeCode"), document.document_type.ubl_type_code)
    for note in _notes(document):
        _sub(root, _cbc("Note"), note)

    _sub(root, _cbc("DocumentCurrencyCode"), currency)
    _optional(root, _cbc("BuyerReference"), document.buyer_reference)

    if document.order_reference:
        order = _sub(root, _cac("OrderReference"))
        _sub(order, _cbc("ID"), document.order_reference)
    if credited is not None:
        _billing_reference(root, credited)

    _supplier(root, document)
    _customer(root, document)
    _delivery(root, document)
    _payment(root, document)

    computed = totals(document)
    _tax_total(root, computed, currency)
    _monetary_total(root, computed, currency)
    for line in document.lines:
        _line(root, document, line, currency)
    return root


def render(document: SalesDocument, credited: SalesDocument | None = None) -> str:
    """``document`` as an EHF XML string, indented and declared UTF-8.

    Indented on purpose: this is the artefact an auditor, a support case and a
    failing-validation ticket all end up reading, and a single-line 8 kB document
    helps none of them. Whitespace between elements is insignificant to every
    UBL consumer.
    """
    root = build(document, credited)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'
