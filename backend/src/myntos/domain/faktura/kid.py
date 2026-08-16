"""KID — the payment reference that reconciles a payment to its invoice.

A KID (kundeidentifikasjon) is the number a payer types into their bank
alongside the amount. It travels with the payment, comes back to the seller in
the bank's OCR file, and is what lets a received krone be matched to the invoice
it settles instead of to a name and a date. That is its whole job, and it is why
the number is not arbitrary.

**The structure is kundenummer + fakturanummer + kontrollsiffer**, which is the
ordinary Norwegian one, and each of the three parts earns its place:

- The **customer number** is what makes the OCR file postable to the right
  kundereskontro. An invoice number alone identifies the invoice, but a payment
  that arrives for an amount that does not match — a partial payment, two
  invoices settled in one transfer, a number typed from an old reminder — still
  has to land on a customer, and this is what lands it.
- The **invoice number** identifies the document being settled.
- The **check digit** is what makes a mistyped KID get rejected at the keyboard
  rather than reconciled against somebody else's invoice.

Both leading parts are **fixed width**, zero-padded. That is not cosmetic: a
KID-avtale fixes the length, and a reference that is eleven digits for customer
10000 and twelve for customer 100000 is one the bank's OCR service rejects. It
is also what lets the two fields be read back out of a KID by position.

An invoice to a buyer typed in rather than picked from the customer register has
no customer number, and gets zeros in that field. Zeros rather than a shorter
KID, for the same reason: the width is the agreement.

The invoice number is assigned by :func:`~myntos.domain.faktura.service.issue`
and by nothing else — a draft has none, deliberately, because numbering drafts is
how a series acquires gaps. So a KID cannot exist before issue either, and
:func:`for_document_number` is called from the same function that hands out the
number. What the form shows before that is a preview (:func:`preview_for_next`),
which is honest about being one.

**Two check-digit schemes, and we validate against both.** Norwegian banks use
MOD10 (Luhn) or MOD11, agreed per customer in the KID-avtale. We *generate*
MOD10, which is the more common of the two, but :func:`is_valid` accepts either —
because the alternative is telling a user whose bank agreed MOD11 that the KID
they typed is wrong. A typo still fails both about four times in five, which is
what the check digit is for.

**The format must match the seller's KID-avtale.** Widths and scheme are agreed
with the bank, and a KID the bank does not recognise comes back unreconciled.
Myntos does not yet model that agreement, so it generates the ordinary shape and
lets the user type their own; :data:`CUSTOMER_LENGTH` and :data:`INVOICE_LENGTH`
are the two numbers to make configurable when it does get modelled.
"""

from __future__ import annotations

import re

# Five digits for the customer, because that is what this system's customer
# numbers are: myntos.domain.ledger.parties starts them at 10000, so every
# generated one fits exactly and a migrated register has room for its own.
CUSTOMER_LENGTH = 5

# Six for the invoice — a million documents before the width has to change, which
# for the businesses this is built for is never.
INVOICE_LENGTH = 6

# Twelve digits in total, then: 5 + 6 + 1. Comfortably inside every KID-avtale's
# range, and inside what a person will retype off a paper invoice.
LENGTH = CUSTOMER_LENGTH + INVOICE_LENGTH + 1

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


def _field(value: str | None, width: int) -> str:
    """One fixed-width numeric field of a KID.

    Non-digits are dropped rather than refused: a customer number an accountant
    brought from another system may read ``K-1042``, and the digits in it are
    still the customer. One with no digits at all — and a missing one — become
    zeros, which keeps the width the agreement fixed.

    A value too wide for its field keeps its **rightmost** digits. That loses the
    distinction between customer 1000042 and 2000042 in a five-digit field, which
    is bad; cutting the other end would lose the distinction between 4200000 and
    4200001, which is every consecutive number and therefore worse.
    """
    digits = _digits_of(value or "").lstrip("0")
    return digits[-width:].rjust(width, "0")


def build(*, customer_number: str | None, document_number: str) -> str:
    """A complete KID: kundenummer, fakturanummer, MOD10 check digit.

    Both leading fields are fixed width, so the KID is always :data:`LENGTH`
    digits and the two numbers can be read back out of it by position.
    """
    base = _field(customer_number, CUSTOMER_LENGTH) + _field(document_number, INVOICE_LENGTH)
    # Only ever bites a base that is already absurd, and never for the widths
    # above — LENGTH is well inside MAX_LENGTH.
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


def for_document_number(document_number: str, *, customer_number: str | None = None) -> str:
    """The KID an invoice numbered ``document_number`` gets.

    Called at issue, from the same place the number itself is assigned.
    ``customer_number`` is the buyer's ``party_id`` where the invoice went to a
    registered customer, and ``None`` for a buyer typed straight onto the
    document — a one-off sale that was never in the register.
    """
    return build(customer_number=customer_number, document_number=document_number)


def preview_for_next(next_number: int, *, customer_number: str | None = None) -> str:
    """The KID the next invoice would get, for the form to show before issue.

    A preview and not a reservation: nothing is taken from the series here, so an
    invoice issued in between moves the number on and this one changes with it.
    That is why the interface labels it rather than presenting it as settled. It
    also moves when the customer does, which is why the form asks again whenever
    a different one is picked.
    """
    return build(customer_number=customer_number, document_number=str(next_number))


def split(reference: str) -> tuple[str, str] | None:
    """Read the customer and invoice numbers back out of one of our own KIDs.

    The reason the fields are fixed width. Nothing calls this yet — it is what an
    OCR-file importer will need, and it is here so the layout is written down
    once rather than re-derived by whoever builds that.

    ``None`` for anything that is not a KID this module would have produced,
    including one from a seller's own bank agreement.
    """
    if len(reference) != LENGTH or not reference.isdigit():
        return None
    if reference[-1] != mod10_check_digit(reference[:-1]):
        return None
    return reference[:CUSTOMER_LENGTH], reference[CUSTOMER_LENGTH : LENGTH - 1]
