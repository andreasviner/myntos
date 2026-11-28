"""The payment reference, and the check digit that makes it worth having.

A KID is only useful if a mistyped one is rejected rather than reconciled
against the wrong invoice, so most of what is worth testing here is the check
digit — both schemes, because which one a seller's bank agreed is not something
this system is told.
"""

from datetime import date

import pytest

from myntos.domain.faktura import kid

ISSUE_DATE = date(2026, 11, 25)

# --- Check digits -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("base", "check"),
    [
        ("000001", "8"),
        ("000002", "6"),
        ("000010", "9"),
        ("7992739871", "3"),  # the textbook Luhn example
    ],
)
def test_the_mod10_check_digit_is_luhn(base: str, check: str) -> None:
    assert kid.mod10_check_digit(base) == check


def test_a_mod11_base_that_cannot_carry_a_check_digit_says_so() -> None:
    """A remainder of one would need a check digit of ten, which is not a digit."""
    impossible = next(
        base
        for base in (str(n).rjust(6, "0") for n in range(1, 2000))
        if _mod11_remainder(base) == 1
    )
    assert kid.mod11_check_digit(impossible) is None


def _mod11_remainder(base: str) -> int:
    return sum(int(c) * (i % 6 + 2) for i, c in enumerate(reversed(base))) % 11


def test_every_generated_kid_validates() -> None:
    for number in [*range(1, 500), 9999, 100000, 999999, 1234567]:
        generated = kid.preview_for_next(number, issue_date=ISSUE_DATE)
        assert kid.is_valid(generated), generated


def test_a_wrong_check_digit_is_caught() -> None:
    """The point of the whole exercise: 0000018 is right, so 0000011 is not."""
    assert kid.is_valid("0000018")
    assert not kid.is_valid("0000011")


def test_a_kid_from_a_mod11_bank_is_accepted_too() -> None:
    """We generate MOD10, but refusing MOD11 would reject half of Norway's banks."""
    base = "123456"
    check = kid.mod11_check_digit(base)
    assert check is not None
    assert kid.is_valid(base + check)
    # And it really is the other scheme, or this test proves nothing.
    assert check != kid.mod10_check_digit(base)


@pytest.mark.parametrize("bad", ["", "1", "abcdefg", "00000 18", "12,34", "1" * 26])
def test_what_is_not_a_kid_at_all(bad: str) -> None:
    assert not kid.is_valid(bad)


# --- Building -----------------------------------------------------------------------


def test_the_kid_is_the_issue_date_invoice_number_and_check_digit() -> None:
    built = kid.for_document_number("137", issue_date=ISSUE_DATE)

    assert built == "202611251374"
    assert built[: kid.DATE_LENGTH] == "20261125"
    assert built[kid.DATE_LENGTH : -1] == "137"
    assert built[-1] == kid.mod10_check_digit("20261125137")


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        ("1", "2026112512"),
        ("18", "20261125189"),
        ("42", "20261125429"),
        ("1042", "2026112510428"),
        ("100000", "202611251000006"),
    ],
)
def test_the_kid_starts_with_the_issue_date_and_has_no_leading_zeros(
    number: str, expected: str
) -> None:
    """At least ten digits, never starting with zeros that are hard to count and type."""
    built = kid.for_document_number(number, issue_date=ISSUE_DATE)

    assert built == expected
    assert len(built) >= kid.MIN_LENGTH
    assert not built.startswith("0")
    assert kid.is_valid(built)


def test_the_buyer_does_not_change_the_kid() -> None:
    """The invoice number already names the buyer, so the preview is the same for anyone."""
    assert kid.preview_for_next(7, issue_date=ISSUE_DATE) == kid.for_document_number(
        "7", issue_date=ISSUE_DATE
    )


def test_a_number_from_another_system_keeps_its_digits() -> None:
    """A series brought over may read "F-01042"; the digits are still the invoice."""
    built = kid.for_document_number("F-01042", issue_date=ISSUE_DATE)

    assert built == kid.for_document_number("1042", issue_date=ISSUE_DATE)
    assert kid.is_valid(built)


def test_the_shortest_generated_kid_is_ten_digits_and_banks_still_accept_shorter_ones() -> None:
    """Generated KIDs start at ten digits; validation still accepts 2-25 digits for older KIDs."""
    generated = kid.for_document_number("1", issue_date=ISSUE_DATE)
    assert len(generated) == kid.MIN_LENGTH
    assert kid.is_valid(generated)
    assert kid.is_valid("18")
    assert kid.is_valid("000000000018")


def test_a_kid_never_outgrows_the_field() -> None:
    built = kid.for_document_number("9" * 40, issue_date=ISSUE_DATE)
    assert len(built) == kid.MAX_LENGTH
    assert built.startswith("20261125")
    assert kid.is_valid(built)


def test_consecutive_invoices_on_the_same_day_stay_distinct() -> None:
    """High-volume companies issuing many invoices in a single day never collide."""
    built = {kid.for_document_number(str(n), issue_date=ISSUE_DATE) for n in range(1, 20000)}
    assert len(built) == 19999


def test_different_issue_dates_change_the_prefix_without_colliding() -> None:
    first = kid.for_document_number("12", issue_date=date(2026, 1, 2))
    second = kid.for_document_number("2", issue_date=date(2026, 1, 21))
    assert first != second
    assert first.startswith("20260102")
    assert second.startswith("20260121")
