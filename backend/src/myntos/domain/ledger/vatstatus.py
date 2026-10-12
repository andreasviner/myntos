"""Whether a company was VAT-registered on a given day, and what follows from it.

Pure functions over a :class:`~myntos.domain.ledger.models.Company`, so the rule
is in one place and testable without a database.

**Registration is a property of a date.** ``company.vat_registered`` says what
is true now; ``vat_registered_from`` says since when. A purchase dated before
registration took effect carried no right to deduct, whatever the flag says on
the day it is typed in, so every caller asks :func:`registered_on` with the
entry's own date.

**What it decides, and what it never touches.** It decides the VAT code the
cash-flow register *proposes* for a new entry and the codes it *accepts* for
one. It never rewrites a posted voucher: a voucher is history (bokføringsloven),
it already carries the code and the rate it was posted with, and switching the
company's status is a statement about entries yet to come. A business that
registers in May does not thereby acquire deductions on April's purchases
through Myntos; that is tilbakegående avgiftsoppgjør (merverdiavgiftsloven
§ 8-6), which is a claim made in a melding, not an edit.

What an unregistered business gets, and why:

- **No VAT split.** It has no right to deduct input VAT
  (merverdiavgiftsloven § 8-1 gives it to registered businesses), so the whole
  amount it paid is the cost. Splitting 25 % off onto 2710 would put a
  receivable from Skatteetaten on the balance sheet that no melding will ever
  claim.
- **No output VAT on its sales.** It may not charge VAT, and it files no
  ordinary melding. Its sales are booked gross with code 0.
- **The reverse-charge codes without deduction stay open** (82, 84, 87, 89, 92).
  An unregistered business that buys remote services from abroad does owe the
  VAT on them (merverdiavgiftsloven § 11-3) and reports it in a melding of the
  category ``omvendtAvgiftsplikt``, which Myntos does not file yet; the books
  can still say the debt is there.
"""

from __future__ import annotations

from datetime import date

from myntos.domain.ledger.chart import NO_VAT_CODE, STANDARD_TAX_CODES
from myntos.domain.ledger.models import Company

#: Codes an unregistered business may post with. Everything else either claims
#: a deduction or charges output VAT, and both belong to registered businesses.
UNREGISTERED_TAX_CODES: frozenset[str] = frozenset(
    {NO_VAT_CODE}
    | {
        seed.code
        for seed in STANDARD_TAX_CODES
        if seed.reverse_charge and not seed.deductible and seed.rate > 0
    }
)

#: Cash-flow categories that only make sense for a registered business, because
#: their account *is* a VAT account. Border VAT on a tollpass booked to 2710 by
#: an unregistered importer would be a deduction it has no right to; for it the
#: amount is part of what the goods cost, and belongs on the purchase.
REGISTERED_ONLY_TYPES: frozenset[str] = frozenset({"mva_innforsel", "refusjon_mva_innforsel"})


def registered_on(company: Company, day: date) -> bool:
    """Whether ``company`` was in Merverdiavgiftsregisteret on ``day``."""
    if not company.vat_registered:
        return False
    since = company.vat_registered_from
    return since is None or day >= since


def allowed_tax_codes(registered: bool) -> frozenset[str] | None:
    """The codes a new entry may carry, or ``None`` for "any the company has"."""
    return None if registered else UNREGISTERED_TAX_CODES


def default_tax_code(type_default: str, registered: bool) -> str:
    """The code to propose for a category, given the registration status.

    A registered business gets the catalogue's default, which states its own
    assumption next to it. An unregistered one gets code 0 for everything:
    gross to the category's account, nothing to 2700 or 2710.
    """
    return type_default if registered else NO_VAT_CODE
