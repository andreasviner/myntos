"""Organisasjonsnummer validation.

Its own module because two things need it — the company being kept books for,
and the customers and suppliers it trades with — and neither should have to
import the other's service to get at it.
"""

from myntos.domain.ledger.errors import InvalidOrgnrError

# Mod-11 weights applied to the first eight digits (Brønnøysundregistrene).
_WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)


def validate_orgnr(orgnr: str) -> str:
    """Normalise and check an organisasjonsnummer, or raise.

    The check digit is verified, not just the length. An orgnr identifies a
    party on every filing this system will ever produce, and a transposed pair
    of digits is both easy to type and expensive to discover later — the mod-11
    check catches almost all of them for free.

    A remainder of 1 would demand a check digit of 10, which cannot be written
    as one digit; Brønnøysundregistrene simply never issues those numbers, so
    such an input is invalid rather than a special case.
    """
    digits = orgnr.replace(" ", "")
    if len(digits) != 9 or not digits.isdigit():
        raise InvalidOrgnrError("organisasjonsnummer må være ni siffer")
    total = sum(int(digit) * weight for digit, weight in zip(digits[:8], _WEIGHTS, strict=True))
    remainder = total % 11
    check_digit = 0 if remainder == 0 else 11 - remainder
    if check_digit == 10 or check_digit != int(digits[8]):
        raise InvalidOrgnrError("organisasjonsnummeret har feil kontrollsiffer")
    return digits
