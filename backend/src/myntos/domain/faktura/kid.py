"""KID — the payment reference that reconciles a payment to its invoice.

A KID (kundeidentifikasjon) is the number a payer types into their bank
alongside the amount. It travels with the payment, comes back to the seller in
the bank's OCR file, and is what lets a received krone be matched to the invoice
it settles instead of to a name and a date. That is its whole job, and it is why
the number is not arbitrary.

**The structure is utstedelsesdato + fakturanummer + kontrollsiffer**, and
nothing else:

- The **issue date** (``YYYYMMDD``) keeps every KID at least ten digits long
  without leading zeros or long runs of zeros to miscount, and tells a reader
  when the invoice was issued.
- The **invoice number** identifies the document being settled, and through it
  the customer. A company has one unbroken series (bokføringsforskriften
  § 5-1-3), so the number is unique within the books and never reused — even if
  a company issues thousands of invoices on the same day.
- The **check digit** is what makes a mistyped KID get rejected at the keyboard
  rather than reconciled against somebody else's invoice.

**No customer number, and no leading zeros.** Until 2026-10-23 the KID was a
five-digit customer number and a six-digit invoice number, both zero-padded
(``000000000018``), which forced payers to count long strings of zeros. From
2026-10-23 to 2026-11-25 it was just the invoice number plus check digit
(``18``), which was shorter than expected on invoices. Prefixing the eight-digit
issue date (``YYYYMMDD``) before the unpadded invoice number keeps every KID at
least ten digits with no leading zeros to miscount, while leaving sixteen digits
for the invoice number inside Nets' 25-digit limit so high-volume companies
issuing many invoices in a single day never collide or run out of room. The
customer number still buys nothing the invoice number does not already give: a
partial payment, a number typed off an old reminder and one transfer for two
invoices all still carry *an* invoice's KID, and that invoice names its buyer. A
seller with a KID-avtale that fixes a different format types their own KID,
which is checked but never overridden.

So invoice 1 issued on 2026-11-25 has the KID ``2026112512``, invoice 42 has
``20261125429``, and invoice 1042 has ``2026112510428``. Every bank takes two to
twenty-five digits on the payer's side and validates the check digit there.

The invoice number is assigned by :func:`~myntos.domain.faktura.service.issue`
and by nothing else — a draft has none, deliberately, because numbering drafts is
how a series acquires gaps. So a KID cannot exist before issue either, and
:func:`for_document_number` is called from the same function that hands out the
number. What the form shows before that is a preview (:func:`preview_for_next`),
which is honest about being one.

**Reading one back is a lookup, not a parse.** The KID is stored on the issued
document (``payment_reference``), so an OCR-file importer finds the invoice by
matching the reference exactly. Nothing has to read numbers back out of it by
position, which is what the old fixed widths were for.

**Two check-digit schemes, and we validate against both.** Norwegian banks use
MOD10 (Luhn) or MOD11, agreed per customer in the KID-avtale. We *generate*
MOD10, which is the more common of the two, but :func:`is_valid` accepts either —
because the alternative is telling a user whose bank agreed MOD11 that the KID
they typed is wrong. A typo still fails both about four times in five, which is
what the check digit is for.
"""

from __future__ import annotations

import re
from datetime import date

# Eight-digit issue date (YYYYMMDD) plus at least one invoice-number digit and
# one MOD10 check digit: every generated KID is at least ten digits and never
# starts with leading zeros.
DATE_LENGTH = 8
MIN_LENGTH = DATE_LENGTH + 2

# Nets' limit for the field. Leaves sixteen digits for the invoice number beside
# the eight-digit date and check digit — unreachable with sequential numbering,
# but truncation in build() is cheaper than a rejected payment file.
MAX_LENGTH = 25

_DIGITS = re.compile(r"\d+")


def _digits_of(value: str) -> str:
    """Only the digits in ``value``. A KID is digits and nothing else."""
    return "".join(_DIGITS.findall(value))


def mod10_check_digit(base: str) -> str:
    """The Luhn check digit for ``base``.

    Weights alternate 2, 1, 2, 1 … from the **right**, and a product above nine
    has its digits added rather than being used as it is — that is what makes the
    scheme catch a transposed pair as well as a single mistyped digit.
    """
    total = 0
    for position, character in enumerate(reversed(base)):
        product = int(character) * (2 if position % 2 == 0 else 1)
        total += product - 9 if product > 9 else product
    return str((10 - total % 10) % 10)


def mod11_check_digit(base: str) -> str | None:
    """The MOD11 check digit for ``base``, or ``None`` where none exists.

    Weights run 2, 3, 4, 5, 6, 7 from the right and repeat. A remainder of one
    would require a check digit of ten, which does not fit in a digit; the
    convention is that such a base cannot carry a MOD11 KID at all, so the answer
    is ``None`` rather than a wrong digit.
    """
    total = 0
    for position, character in enumerate(reversed(base)):
        total += int(character) * (position % 6 + 2)
    remainder = total % 11
    if remainder == 0:
        return "0"
    if remainder == 1:
        return None
    return str(11 - remainder)


def build(document_number: str, *, issue_date: date) -> str:
    """A complete KID: issue date (``YYYYMMDD``), invoice number, MOD10 check digit.

    Non-digits are dropped rather than refused: a series brought from another
    system may read ``F-1042``, and the digits in it are still the invoice.
    Leading zeros on the invoice number are dropped so there are no runs of
    zeros to miscount; the eight-digit issue date keeps every generated KID at
    least :data:`MIN_LENGTH` (10) digits while leaving sixteen digits for the
    invoice number up to :data:`MAX_LENGTH` (25).
    """
    date_prefix = issue_date.strftime("%Y%m%d")
    number = _digits_of(document_number).lstrip("0") or "0"
    # Only ever bites an invoice number wider than sixteen digits. The rightmost
    # digits are kept because they tell consecutive invoices apart.
    max_number_digits = MAX_LENGTH - 1 - len(date_prefix)
    base = date_prefix + number[-max_number_digits:]
    return base + mod10_check_digit(base)


def is_valid(kid: str) -> bool:
    """Whether ``kid`` carries a check digit that works under MOD10 **or** MOD11.

    Both, because which one applies is a matter of the seller's agreement with
    their bank and not something this system is told. A number that satisfies
    neither is a typo with about four-in-five confidence — enough to say
    something about, not enough to refuse an invoice over.
    """
    cleaned = kid.strip()
    if not cleaned.isdigit() or not 2 <= len(cleaned) <= MAX_LENGTH:
        return False
    base, check = cleaned[:-1], cleaned[-1]
    return check in (mod10_check_digit(base), mod11_check_digit(base))


def for_document_number(document_number: str, *, issue_date: date) -> str:
    """The KID an invoice numbered ``document_number`` issued on ``issue_date`` gets.

    Called at issue, from the same place the number and issue date are assigned.
    The buyer does not come into it: the invoice number already names them.
    """
    return build(document_number, issue_date=issue_date)


def preview_for_next(next_number: int, *, issue_date: date) -> str:
    """The KID the next invoice would get if issued on ``issue_date``.

    A preview and not a reservation: nothing is taken from the series here, so an
    invoice issued in between — or waiting until another day to issue — moves
    this number on. That is why the interface labels it rather than presenting
    it as settled.
    """
    return build(str(next_number), issue_date=issue_date)
