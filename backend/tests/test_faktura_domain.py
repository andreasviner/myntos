"""The invoice rules, without a database.

Everything here runs on plain ORM objects. What is being tested is
bokføringsforskriften § 5-1 and the arithmetic under it — accounting rules, not
storage behaviour — so a database would only slow the suite down and add a way
for these to fail for reasons that have nothing to do with the rules.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from myntos.domain.faktura.compliance import (
    E_INVOICE_MANDATORY_FROM,
    FORETAKSREGISTERET_FORMS,
    Severity,
    blocking,
    check,
    describe_markings,
)
from myntos.domain.faktura.models import (
    DocumentType,
    InvoiceStatus,
    SalesDocument,
    SalesDocumentLine,
)
from myntos.domain.faktura.totals import breakdown, line_net, totals
from myntos.domain.faktura.vat import (
    UnsupportedSalesTaxCodeError,
    VatCategory,
    category_for,
    percent,
)
from myntos.domain.ledger.posting import compute_tax

TODAY = date(2026, 8, 16)

RATES = {
    "3": Decimal("0.250000"),
    "31": Decimal("0.150000"),
    "32": Decimal("0.120000"),
    "5": Decimal("0.000000"),
    "6": Decimal("0.000000"),
    "52": Decimal("0.000000"),
    "0": Decimal("0.000000"),
}

ACCOUNTS = {
    "3": "3000",
    "31": "3010",
    "32": "3020",
    "5": "3100",
    "6": "3100",
    "52": "3120",
    "0": "3100",
}


def make_line(
    line_no: int = 1,
    *,
    description: str = "Rådgivning",
    quantity: str = "1",
    unit_price: str = "1000.00",
    tax_code: str = "3",
    discount_rate: str = "0",
) -> SalesDocumentLine:
    rate = RATES[tax_code]
    net = line_net(
        quantity=Decimal(quantity),
        unit_price=Decimal(unit_price),
        discount_rate=Decimal(discount_rate),
    )
    return SalesDocumentLine(
        line_no=line_no,
        description=description,
        quantity=Decimal(quantity),
        unit_code="EA",
        unit_price=Decimal(unit_price),
        discount_rate=Decimal(discount_rate),
        tax_code=tax_code,
        tax_rate=rate,
        income_account_number=ACCOUNTS[tax_code],
        net_amount=net,
        # compute_tax, not quantize(): the ledger rounds half *away from zero*,
        # and Decimal's default is half-to-even. Building the fixture the other
        # way would make the rounding tests agree with themselves rather than
        # with the service.
        vat_amount=compute_tax(net, rate),
    )


def make_document(
    *,
    lines: list[SalesDocumentLine] | None = None,
    vat_registered: bool = True,
    **overrides: Any,
) -> SalesDocument:
    """A document that passes every check, so a test can break exactly one thing."""
    document = SalesDocument(
        company_id=1,
        document_type=DocumentType.FAKTURA,
        status=InvoiceStatus.UTKAST,
        buyer_name="Kunde AS",
        buyer_orgnr="910000012",
        buyer_address_line="Storgata 1",
        buyer_postal_code="0155",
        buyer_city="Oslo",
        buyer_country="NO",
        buyer_is_business=True,
        seller_name="Nordmann Consulting AS",
        seller_orgnr="910000004",
        seller_address_line="Kirkegata 2",
        seller_postal_code="0153",
        seller_city="Oslo",
        seller_country="NO",
        seller_vat_registered=vat_registered,
        seller_in_foretaksregisteret=False,
        currency_code="NOK",
        delivery_date=date(2026, 8, 1),
        delivery_place="Oslo",
        due_date=date(2026, 8, 30),
        reverse_charge=False,
    )
    for field, value in overrides.items():
        setattr(document, field, value)
    document.lines = lines if lines is not None else [make_line()]
    return document


# --- Arithmetic ---------------------------------------------------------------------


def test_line_net_applies_discount_to_the_extended_amount() -> None:
    # 3 * 333.33 = 999.99, less 10 % = 899.991 -> 899.99. Discounting the unit
    # price first would round twice and land on 900.00.
    net = line_net(
        quantity=Decimal("3"), unit_price=Decimal("333.33"), discount_rate=Decimal("0.10")
    )
    assert net == Decimal("899.99")


def test_vat_is_computed_from_the_summed_basis_not_per_line() -> None:
    """Three lines that each round up would over-report VAT by an øre.

    0.10 at 25 % is 0.025, which rounds to 0.03 per line — 0.09 in total. The
    group basis is 0.30, whose VAT is 0.075 → 0.08. The document reports the
    group, because that is what the recipient's validator checks.
    """
    lines = [make_line(index, unit_price="0.10", quantity="1") for index in range(1, 4)]
    document = make_document(lines=lines)
    computed = totals(document)

    assert sum(line.vat_amount for line in lines) == Decimal("0.09")
    assert computed.tax_total == Decimal("0.08")
    assert computed.payable == Decimal("0.38")


def test_totals_add_up() -> None:
    document = make_document(
        lines=[
            make_line(1, unit_price="1000.00", tax_code="3"),
            make_line(2, unit_price="500.00", tax_code="32"),
        ]
    )
    computed = totals(document)
    assert computed.line_total == Decimal("1500.00")
    assert computed.tax_total == Decimal("310.00")  # 250 + 60
    assert computed.tax_inclusive == Decimal("1810.00")
    assert computed.payable == computed.tax_inclusive


# --- § 5-1-5: separate presentation and summing -----------------------------------------


def test_breakdown_separates_rates_and_categories() -> None:
    """§ 5-1-5: each rate, and taxable versus exempt, shown and summed for itself."""
    document = make_document(
        lines=[
            make_line(1, unit_price="1000.00", tax_code="3"),
            make_line(2, unit_price="400.00", tax_code="3"),
            make_line(3, unit_price="200.00", tax_code="32"),
            make_line(4, unit_price="100.00", tax_code="52"),
        ]
    )
    groups = breakdown(document, document.lines)
    by_key = {(group.category, group.rate): group for group in groups}

    assert len(groups) == 3
    # The two 25 % lines merged into one group and were summed together.
    assert by_key[(VatCategory.STANDARD, Decimal("0.250000"))].taxable_amount == Decimal("1400.00")
    assert by_key[(VatCategory.STANDARD, Decimal("0.250000"))].tax_amount == Decimal("350.00")
    assert by_key[(VatCategory.STANDARD, Decimal("0.120000"))].tax_amount == Decimal("24.00")
    export = by_key[(VatCategory.EXPORT, Decimal("0.000000"))]
    assert export.tax_amount == Decimal("0.00")
    # Every zero-rated group states why, which EN 16931 requires and a customer
    # looking at a 0 % line deserves.
    assert export.reason is not None


def test_reverse_charge_overrides_every_line_category() -> None:
    document = make_document(reverse_charge=True)
    groups = breakdown(document, document.lines)
    assert [group.category for group in groups] == [VatCategory.REVERSE_CHARGE]
    assert groups[0].tax_amount == Decimal("0.00")
    assert totals(document).tax_total == Decimal("0.00")


def test_percent_renders_without_trailing_zeroes() -> None:
    assert percent(Decimal("0.250000")) == Decimal("25")
    assert percent(Decimal("0.000000")) == Decimal("0")


def test_purchase_codes_are_refused_on_a_sales_document() -> None:
    # 1 is inngående mva. Putting it on a sales line would book input VAT on our
    # own revenue.
    with pytest.raises(UnsupportedSalesTaxCodeError):
        category_for("1")


# --- § 5-1: what the document must contain ----------------------------------------------


def test_a_complete_invoice_has_nothing_blocking() -> None:
    assert blocking(check(make_document(), today=TODAY)) == []


@pytest.mark.parametrize(
    ("field", "value", "expected_rule"),
    [
        ("buyer_name", "", "bokføringsforskriften § 5-1-2"),
        ("seller_orgnr", "", "bokføringsforskriften § 5-1-2"),
        ("delivery_date", None, "bokføringsforskriften § 5-1-1 nr. 4"),
        ("delivery_place", None, "bokføringsforskriften § 5-1-1 nr. 4"),
        ("due_date", None, "bokføringsforskriften § 5-1-1 nr. 5"),
    ],
)
def test_missing_mandatory_content_blocks_issue(field: str, value: Any, expected_rule: str) -> None:
    document = make_document(**{field: value})
    found = blocking(check(document, today=TODAY))
    assert [v.rule for v in found] == [expected_rule]
    assert found[0].field == field


def test_a_buyer_with_neither_address_nor_orgnr_is_not_identified() -> None:
    document = make_document(
        buyer_orgnr=None,
        buyer_address_line=None,
        buyer_postal_code=None,
        buyer_is_business=False,
    )
    messages = [v.message for v in blocking(check(document, today=TODAY))]
    assert any("adresse eller organisasjonsnummer" in message for message in messages)


def test_a_buyer_identified_by_orgnr_alone_is_enough() -> None:
    document = make_document(buyer_address_line=None, buyer_postal_code=None, buyer_city=None)
    assert blocking(check(document, today=TODAY)) == []


def test_an_invoice_without_lines_describes_no_ytelse() -> None:
    document = make_document(lines=[])
    rules = {v.rule for v in blocking(check(document, today=TODAY))}
    assert "bokføringsforskriften § 5-1-1 nr. 3" in rules


def test_vat_cannot_be_charged_without_being_registered() -> None:
    document = make_document(vat_registered=False)
    found = blocking(check(document, today=TODAY))
    assert [v.field for v in found] == ["seller_vat_registered"]


def test_an_unregistered_seller_may_invoice_without_vat() -> None:
    document = make_document(vat_registered=False, lines=[make_line(tax_code="0")])
    assert blocking(check(document, today=TODAY)) == []
    # And the document says why there is no VAT rather than leaving a bare 0 %.
    assert totals(document).subtotals[0].reason is not None


def test_reverse_charge_warns_when_the_lines_still_carry_a_taxable_rate() -> None:
    """Ticking the flag silently drops the VAT; the user is told rather than surprised."""
    document = make_document(reverse_charge=True, lines=[make_line(tax_code="3")])
    found = check(document, today=TODAY)
    warnings = [v for v in found if v.field == "reverse_charge"]

    assert blocking(found) == []
    assert len(warnings) == 1
    assert warnings[0].severity is Severity.ADVARSEL
    assert totals(document).tax_total == Decimal("0.00")
    assert any("Omvendt avgiftsplikt" in marking for marking in describe_markings(document))


def test_reverse_charge_on_zero_rated_lines_is_quiet() -> None:
    document = make_document(reverse_charge=True, lines=[make_line(tax_code="52")])
    assert [v for v in check(document, today=TODAY) if v.field == "reverse_charge"] == []


def test_unntatt_omsetning_may_share_an_invoice_with_taxable_sales() -> None:
    """Code 6 maps to category E, which EN 16931 allows alongside S.

    The category that may not be mixed is O (BR-O-11), and 6 is deliberately not
    mapped to it — see :mod:`myntos.domain.faktura.vat`.
    """
    document = make_document(lines=[make_line(1, tax_code="3"), make_line(2, tax_code="6")])
    assert blocking(check(document, today=TODAY)) == []
    assert {group.category for group in breakdown(document, document.lines)} == {
        VatCategory.STANDARD,
        VatCategory.EXEMPT,
    }


def test_late_issue_warns_but_does_not_block() -> None:
    """§ 5-2-2. The document is late either way; refusing would leave the sale
    undocumented, which is the worse breach."""
    document = make_document(delivery_date=date(2026, 5, 1))
    found = check(document, today=TODAY)
    late = [v for v in found if v.rule == "bokføringsforskriften § 5-2-2"]
    assert len(late) == 1
    assert late[0].severity is Severity.ADVARSEL
    assert blocking(found) == []


def test_foretaksregisteret_marking_needs_the_head_office_address() -> None:
    document = make_document(seller_in_foretaksregisteret=True, seller_address_line=None)
    assert [v.field for v in blocking(check(document, today=TODAY))] == ["seller_address_line"]


def test_foretaksregisteret_marking_appears_on_the_document() -> None:
    document = make_document(seller_in_foretaksregisteret=True)
    assert "Foretaksregisteret" in describe_markings(document)


@pytest.mark.parametrize("form", sorted(FORETAKSREGISTERET_FORMS))
def test_an_as_that_forgot_the_foretaksregisteret_flag_is_caught(form: str) -> None:
    """The organisasjonsform is the evidence; the flag alone is only a checkbox.

    Without this the document would go out silently missing a word § 5-1-2
    requires, and nothing on the document itself would show that anything was
    wrong.
    """
    document = make_document(seller_organisation_form=form, seller_in_foretaksregisteret=False)
    found = blocking(check(document, today=TODAY))
    assert [v.field for v in found] == ["seller_in_foretaksregisteret"]


def test_an_enk_needs_no_foretaksregisteret_marking() -> None:
    document = make_document(seller_organisation_form="ENK", seller_in_foretaksregisteret=False)
    assert blocking(check(document, today=TODAY)) == []
    assert describe_markings(document) == []


def test_a_due_date_in_the_past_warns_but_is_lawful() -> None:
    """§ 5-1-1 nr. 5 asks for a forfallsdato, not for a future one."""
    document = make_document(due_date=date(2026, 7, 1))
    found = check(document, today=TODAY)
    warnings = [v for v in found if v.field == "due_date"]

    assert blocking(found) == []
    assert len(warnings) == 1
    assert warnings[0].severity is Severity.ADVARSEL


# --- The e-invoice requirement ------------------------------------------------------------


def test_e_invoice_gap_is_a_warning_before_the_cutover() -> None:
    document = make_document(buyer_orgnr=None)
    found = [v for v in check(document, today=date(2026, 12, 31)) if v.field == "buyer_orgnr"]
    assert len(found) == 1
    assert found[0].severity is Severity.ADVARSEL
    assert "1. januar 2027" in found[0].message


def test_e_invoice_gap_blocks_from_the_cutover() -> None:
    document = make_document(buyer_orgnr=None)
    found = [v for v in check(document, today=E_INVOICE_MANDATORY_FROM) if v.field == "buyer_orgnr"]
    assert len(found) == 1
    assert found[0].severity is Severity.BLOKKERENDE


def test_a_consumer_sale_is_outside_the_e_invoice_requirement() -> None:
    """Asked, not inferred: a missing orgnr does not make someone a consumer."""
    document = make_document(buyer_orgnr=None, buyer_is_business=False)
    found = [v for v in check(document, today=E_INVOICE_MANDATORY_FROM) if v.field == "buyer_orgnr"]
    assert found == []


def test_foreign_currency_is_refused_while_vat_cannot_be_restated_in_nok() -> None:
    """§ 5-1-1 nr. 6 wants the merverdiavgift in NOK; we do not compute that yet."""
    document = make_document(currency_code="EUR")
    assert [v.field for v in blocking(check(document, today=TODAY))] == ["currency_code"]
