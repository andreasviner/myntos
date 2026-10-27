"""KID — the payment reference that reconciles a payment to its invoice.

A KID (kundeidentifikasjon) is the number a payer types into their bank
alongside the amount. It travels with the payment, comes back to the seller in
the bank's OCR file, and is what lets a received krone be matched to the invoice
it settles instead of to a name and a date. That is its whole job, and it is why
the number is not arbitrary.

**The structure is fakturanummer + kontrollsiffer**, and nothing else:

- The **invoice number** identifies the document being settled, and through it
  the customer. A company has one unbroken series (bokføringsforskriften
  § 5-1-3), so the number is unique within the books and never reused.
- The **check digit** is what makes a mistyped KID get rejected at the keyboard
  rather than reconciled against somebody else's invoice.

**No customer number, and no zeros.** Until 2026-10-23 the KID was a five-digit
customer number and a six-digit invoice number, both zero-padded, so the first
invoice to a buyer typed onto the document read ``000000000018``. That is a
number a person copies off a paper invoice, and nobody counts nine zeros right.
The customer number bought nothing the invoice number does not already give: a
partial payment, a number typed off an old reminder and one transfer for two
invoices all still carry *an* invoice's KID, and that invoice names its buyer.
The fixed width was there for a KID-avtale, the OCR agreement with the bank that
fixes the length. Myntos does not model that agreement, and a seller who has one
types their own KID, which is checked but never overridden.

So the invoice numbered 1 has the KID ``18``, and the one numbered 1042 has
``10421``. Every bank takes two to twenty-five digits on the payer's side and
checks only the check digit there.

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

# Nets' limit for the field. Unreachable with sequential numbering, but the
# truncation in build() is cheaper than finding out from a rejected payment file.
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


def build(document_number: str) -> str:
    """A complete KID: the invoice number, then its MOD10 check digit.

    Non-digits are dropped rather than refused: a series brought from another
    system may read ``F-1042``, and the digits in it are still the invoice.
    Leading zeros are dropped too, because they are the part people mistype and
    carry nothing. The shortest KID is two digits, which is also the shortest a
    bank takes.
    """
    base = _digits_of(document_number).lstrip("0") or "0"
    # Only ever bites a number that is already absurd. The rightmost digits are
    # kept because they are the ones that tell consecutive invoices apart.
    base = base[-(MAX_LENGTH - 1) :]
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


def for_document_number(document_number: str) -> str:
    """The KID an invoice numbered ``document_number`` gets.

    Called at issue, from the same place the number itself is assigned. The
    buyer does not come into it: the invoice number already names them.
    """
    return build(document_number)


def preview_for_next(next_number: int) -> str:
    """The KID the next invoice would get, for the form to show before issue.

    A preview and not a reservation: nothing is taken from the series here, so an
    invoice issued in between moves the number on and this one changes with it.
    That is why the interface labels it rather than presenting it as settled.
    """
    return build(str(next_number))
