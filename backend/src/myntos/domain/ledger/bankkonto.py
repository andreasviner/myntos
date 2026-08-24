"""Bank account number validation.

Its own module beside :mod:`myntos.domain.ledger.orgnr`, and for the same
reason: more than one thing needs it, and none of them should have to import a
service to get at it.

A Norwegian kontonummer is eleven digits whose last one is a mod-11 check digit
over the first ten, weighted 2, 3, 4, 5, 6, 7 from the right and repeating —
the same arithmetic :func:`myntos.domain.faktura.kid.mod11_check_digit` already
does, which is why it is borrowed rather than written twice.

Checking the digit matters more here than it looks. The number is printed on
every invoice and put in the ``PayeeFinancialAccount`` of every EHF file, and a
transposed pair is not discovered by the seller — it is discovered by a customer
whose payment bounced, or worse did not, weeks later. An issued salgsdokument
cannot be corrected either; the only fix is a kreditnota.

Only Norwegian account numbers are accepted. A Norwegian IBAN is written
``NO`` + two check digits + the same eleven digits, so one that is pasted in is
reduced to the account number it contains; the IBAN check digits carry no
information the mod-11 digit does not, and are dropped rather than stored. A
foreign IBAN is refused outright — we would be storing something the PDF and
the EHF cannot present correctly, and pretending otherwise is worse than saying
it is not supported yet.
"""

import re

from myntos.domain.faktura.kid import mod11_check_digit
from myntos.domain.ledger.errors import InvalidBankAccountError

# What people type between the groups: 1506.20.33158, 1506 20 33158.
_SEPARATORS = re.compile(r"[\s.\-]")
# A Norwegian IBAN prefix, which is followed by the account number itself.
_NORWEGIAN_IBAN = re.compile(r"^NO\d{2}(?=\d{11}$)", re.IGNORECASE)


def validate_bank_account(value: str) -> str:
    """Normalise and check a Norwegian kontonummer, or raise.

    Returns the eleven digits without separators — the form the database column
    and the renderers expect. :func:`format_bank_account` puts the grouping back
    for display.
    """
    cleaned = _NORWEGIAN_IBAN.sub("", _SEPARATORS.sub("", value.strip()))
    if not cleaned:
        raise InvalidBankAccountError("kontonummeret er tomt")
    if len(cleaned) != 11 or not cleaned.isdigit():
        raise InvalidBankAccountError(
            "kontonummeret må være elleve siffer. Utenlandske kontonumre og IBAN "
            "utenfor Norge støttes ikke ennå"
        )
    if mod11_check_digit(cleaned[:10]) != cleaned[10]:
        raise InvalidBankAccountError(
            f"kontonummeret {format_bank_account(cleaned)} har feil kontrollsiffer. "
            f"Kontroller det mot nettbanken"
        )
    return cleaned


def format_bank_account(account: str) -> str:
    """``1506.20.33158`` — the grouping a Norwegian reads an account number in.

    Anything that is not eleven digits is handed back untouched: this is called
    on stored values, and a stored value from before validation existed should be
    shown as it is rather than mangled into a shape it does not have.
    """
    digits = _SEPARATORS.sub("", account)
    if len(digits) != 11 or not digits.isdigit():
        return account
    return f"{digits[:4]}.{digits[4:6]}.{digits[6:]}"
