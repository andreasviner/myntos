"""Line arithmetic and the VAT breakdown § 5-1-5 asks for.

Pure functions over the models — no session, no I/O — so the arithmetic that
decides what a customer is billed is testable on its own and cannot differ
between the PDF, the XML and the ledger posting. All three call this.

The breakdown is the point. § 5-1-5 requires avgiftspliktig, avgiftsfritt and
unntatt omsetning to appear **each for itself and summed separately**, and the
same where different rates are used. That is one grouping — by VAT category and
rate — and it happens to be exactly the ``TaxSubtotal`` structure EN 16931 wants,
so the Norwegian regulation and the European standard are satisfied by the same
computation rather than by two that could disagree.

Rounding follows the ledger's rule (:mod:`myntos.domain.ledger.posting`): half
away from zero, at øre. VAT is computed **per breakdown group, from the summed
basis**, not per line and then added up. Summing individually-rounded lines
drifts by an øre against ``basis * rate`` often enough to matter, and the
recipient's validator checks the group, not the lines.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from myntos.domain.faktura.models import SalesDocument, SalesDocumentLine
from myntos.domain.faktura.vat import VatCategory, VatTreatment, category_for, percent
from myntos.domain.ledger.posting import ZERO, quantize_money


@dataclass(frozen=True)
class TaxSubtotal:
    """One row of the VAT breakdown: a category, a rate, and what falls under it."""

    category: VatCategory
    rate: Decimal
    reason: str | None
    taxable_amount: Decimal
    tax_amount: Decimal

    @property
    def percent(self) -> Decimal:
        """The rate as the percentage a document shows."""
        return percent(self.rate)


@dataclass(frozen=True)
class DocumentTotals:
    """What the foot of the document says."""

    # Sum of the line net amounts (EN 16931 BT-106).
    line_total: Decimal
    # Net of VAT (BT-109). Equal to line_total until document-level discounts
    # exist; kept distinct because the two are different fields in the standard
    # and conflating them now would make adding a discount a schema change.
    tax_exclusive: Decimal
    tax_total: Decimal
    tax_inclusive: Decimal
    payable: Decimal
    subtotals: list[TaxSubtotal]


def line_net(*, quantity: Decimal, unit_price: Decimal, discount_rate: Decimal = ZERO) -> Decimal:
    """The net amount for one line, rounded to øre.

    The discount is applied to the extended amount rather than to the unit price:
    discounting the price first and then multiplying rounds twice, and the second
    rounding is applied to a number the customer never sees.
    """
    gross_of_discount = quantity * unit_price
    return quantize_money(gross_of_discount * (Decimal(1) - discount_rate))


def _treatment(document: SalesDocument, line: SalesDocumentLine) -> VatTreatment:
    return category_for(line.tax_code, reverse_charge=document.reverse_charge)


def breakdown(document: SalesDocument, lines: Iterable[SalesDocumentLine]) -> list[TaxSubtotal]:
    """Group the lines by VAT category and rate, and sum each group.

    Ordered by category then rate so the same document always produces the same
    breakdown in the same order — the XML is stored and compared, and a set-order
    that varied per process would make two identical invoices look different.
    """
    groups: dict[tuple[str, Decimal], list[SalesDocumentLine]] = {}
    reasons: dict[tuple[str, Decimal], str | None] = {}
    for line in lines:
        treatment = _treatment(document, line)
        # A reverse-charged or exempt line carries no rate on the document even
        # if the underlying supply has one; the rate that is *charged* is what a
        # breakdown row reports.
        rate = line.tax_rate if treatment.category is VatCategory.STANDARD else ZERO
        key = (treatment.category.value, rate)
        groups.setdefault(key, []).append(line)
        reasons[key] = treatment.reason

    subtotals = []
    for (category, rate), grouped in groups.items():
        basis = sum((line.net_amount for line in grouped), start=ZERO)
        subtotals.append(
            TaxSubtotal(
                category=VatCategory(category),
                rate=rate,
                reason=reasons[(category, rate)],
                taxable_amount=basis,
                # From the summed basis, not from the lines' own vat_amount —
                # see the module docstring.
                tax_amount=quantize_money(basis * rate),
            )
        )
    return sorted(subtotals, key=lambda s: (s.category.value, s.rate))


def totals(
    document: SalesDocument, lines: Iterable[SalesDocumentLine] | None = None
) -> DocumentTotals:
    """Everything the foot of the document and the ledger posting need."""
    rows = list(lines if lines is not None else document.lines)
    line_total = sum((line.net_amount for line in rows), start=ZERO)
    subtotals = breakdown(document, rows)
    tax_total = sum((subtotal.tax_amount for subtotal in subtotals), start=ZERO)
    return DocumentTotals(
        line_total=line_total,
        tax_exclusive=line_total,
        tax_total=tax_total,
        tax_inclusive=line_total + tax_total,
        payable=line_total + tax_total,
        subtotals=subtotals,
    )
