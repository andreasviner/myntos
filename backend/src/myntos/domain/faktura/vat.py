"""VAT categories: from our tax codes to what an e-invoice has to say.

The ledger reasons in Norwegian VAT codes (:mod:`myntos.domain.ledger.chart`),
which map to SAF-T standard tax codes and drive the MVA-melding. An e-invoice
reasons in **EN 16931 category codes** (UNCL 5305) — a much smaller vocabulary
that the receiving system in another country also understands. The two have to
be reconciled somewhere, and one table beats the alternative of every exporter
inventing its own.

Two rules from EN 16931 are enforced here rather than discovered at the
recipient's validator:

**A zero-rate category needs a stated reason.** ``E``, ``G``, ``AE``, ``K`` and
``O`` all mean "no VAT, and here is why". The business rules (BR-E-10, BR-G-10,
BR-AE-10 …) require a ``TaxExemptionReason`` on each, so a reason is part of the
category here and not an optional extra the exporter may forget.

**Category ``O`` cannot be mixed.** BR-O-11 forbids an invoice that carries a
"not subject to VAT" breakdown from carrying any other breakdown. Norwegian
practice reaches for ``O`` to mean *unntatt fra merverdiavgiftsloven*, which is
easy to combine with an ordinary taxable line by accident — so ``6`` maps to
``E`` instead, which is mixable and which Norwegian EHF guidance accepts for
unntatt omsetning. ``O`` stays available for the case it is actually meant for.

Purchase codes are absent on purpose. Codes ``1``, ``11`` and ``13`` are
inngående merverdiavgift; a sales document that carried one would book input VAT
on our own revenue. :func:`category_for` refuses them rather than guessing.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal

from myntos.domain.faktura.errors import FakturaError


class VatCategory(enum.StrEnum):
    """EN 16931 / UNCL 5305 tax category code, as it appears in the XML."""

    STANDARD = "S"
    ZERO_RATED = "Z"
    EXEMPT = "E"
    REVERSE_CHARGE = "AE"
    EEA_SUPPLY = "K"
    EXPORT = "G"
    OUT_OF_SCOPE = "O"

    @property
    def requires_reason(self) -> bool:
        """True for every category EN 16931 wants an exemption reason on."""
        return self is not VatCategory.STANDARD

    @property
    def is_exclusive(self) -> bool:
        """True for the category that may not share an invoice (BR-O-11)."""
        return self is VatCategory.OUT_OF_SCOPE


class UnsupportedSalesTaxCodeError(FakturaError):
    """The VAT code cannot appear on a sales document."""


@dataclass(frozen=True)
class VatTreatment:
    """How one tax code appears on an e-invoice."""

    category: VatCategory
    # Norwegian prose. Shown on the PDF and carried as TaxExemptionReason in the
    # XML, so the recipient — and an auditor — can see why no VAT was charged.
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.category.requires_reason and not self.reason:
            raise AssertionError(f"category {self.category} needs an exemption reason")


# Keyed by the internal VAT code, which the seeded chart keeps equal to the
# SAF-T standard code. Only output (sales) codes appear.
_SALES_TREATMENTS: dict[str, VatTreatment] = {
    "3": VatTreatment(VatCategory.STANDARD),
    "31": VatTreatment(VatCategory.STANDARD),
    "32": VatTreatment(VatCategory.STANDARD),
    "5": VatTreatment(
        VatCategory.ZERO_RATED,
        "Avgiftsfritt salg, jf. merverdiavgiftsloven kapittel 6.",
    ),
    "6": VatTreatment(
        VatCategory.EXEMPT,
        "Omsetningen er unntatt fra merverdiavgiftsloven, jf. lovens kapittel 3.",
    ),
    "52": VatTreatment(
        VatCategory.EXPORT,
        "Utførsel av varer og tjenester, jf. merverdiavgiftsloven §§ 6-21 og 6-22.",
    ),
    # Not "no VAT treatment" here but "the seller charges none": this is the code
    # a business below the registration threshold uses, and the reason has to say
    # so — a customer who sees 0 % without an explanation will ask whether the
    # invoice is wrong.
    "0": VatTreatment(
        VatCategory.EXEMPT,
        "Selger er ikke registrert i Merverdiavgiftsregisteret.",
    ),
}

# The § 5-1-1 nr. 7 marking, quoted rather than paraphrased: the wording is what
# the regulation prescribes, and a recipient's system may well look for it. The
# separator is an en dash because that is what the regulation prints.
REVERSE_CHARGE_MARKING = "Omvendt avgiftsplikt – Merverdiavgift ikke beregnet"  # noqa: RUF001

REVERSE_CHARGE_TREATMENT = VatTreatment(VatCategory.REVERSE_CHARGE, REVERSE_CHARGE_MARKING)


def category_for(tax_code: str, *, reverse_charge: bool = False) -> VatTreatment:
    """How ``tax_code`` should be presented on an invoice.

    ``reverse_charge`` overrides the code's own category: when the buyer accounts
    for the VAT, no rate is charged whatever the underlying supply would attract.
    """
    if reverse_charge:
        return REVERSE_CHARGE_TREATMENT
    treatment = _SALES_TREATMENTS.get(tax_code)
    if treatment is None:
        raise UnsupportedSalesTaxCodeError(
            f"mva-koden {tax_code!r} kan ikke brukes på en salgsfaktura; "
            f"gyldige koder er {', '.join(sorted(_SALES_TREATMENTS))}"
        )
    return treatment


def is_sales_code(tax_code: str) -> bool:
    """Whether ``tax_code`` may appear on a sales document at all."""
    return tax_code in _SALES_TREATMENTS


def percent(rate: Decimal) -> Decimal:
    """A stored fraction (``0.250000``) as the percentage the XML wants (``25``).

    Normalised so 25 renders as ``25`` rather than ``25.0000``: EN 16931 compares
    percentages numerically, but the document is also read by people, and trailing
    zeroes on a rate look like a precision that is not there.
    """
    scaled = (rate * 100).normalize()
    # normalize() turns 0 into 0E+2 and 100 into 1E+2; quantizing back to an
    # integer exponent undoes the exponent form without reintroducing decimals.
    if scaled == scaled.to_integral_value():
        return scaled.quantize(Decimal(1))
    return scaled
