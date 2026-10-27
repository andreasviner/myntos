"""The payment reference, and the check digit that makes it worth having.

A KID is only useful if a mistyped one is rejected rather than reconciled
against the wrong invoice, so most of what is worth testing here is the check
digit — both schemes, because which one a seller's bank agreed is not something
this system is told.
"""

import pytest

from myntos.domain.faktura import kid

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
        generated = kid.preview_for_next(number)
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


def test_the_kid_is_the_invoice_number_then_its_check_digit() -> None:
    built = kid.for_document_number("137")

    assert built == "1370"
    assert built[:-1] == "137"
    assert built[-1] == kid.mod10_check_digit("137")


@pytest.mark.parametrize(
    ("number", "expected"),
    [("1", "18"), ("18", "182"), ("1042", "10421"), ("100000", "1000009")],
)
def test_the_kid_has_no_zeros_in_front(number: str, expected: str) -> None:
    """Read off a paper invoice and typed into a bank: nine zeros were miscounted."""
    built = kid.for_document_number(number)

    assert built == expected
    assert not built.startswith("0")


def test_the_buyer_does_not_change_the_kid() -> None:
    """The invoice number already names the buyer, so the preview is the same for anyone."""
    assert kid.preview_for_next(7) == kid.for_document_number("7")


def test_a_number_from_another_system_keeps_its_digits() -> None:
    """A series brought over may read "F-01042"; the digits are still the invoice."""
    built = kid.for_document_number("F-01042")

    assert built == kid.for_document_number("1042")
    assert kid.is_valid(built)


def test_the_shortest_kid_is_still_one_a_bank_takes() -> None:
    """Two digits is the floor on the payer's side, and invoice 1 is exactly that."""
    assert len(kid.for_document_number("1")) == 2
    assert kid.is_valid(kid.for_document_number("1"))


def test_a_kid_never_outgrows_the_field() -> None:
    built = kid.for_document_number("9" * 40)
    assert len(built) <= kid.MAX_LENGTH


def test_consecutive_invoices_stay_distinct() -> None:
    """The property that matters: no two invoices may share a KID."""
    built = {kid.for_document_number(str(n)) for n in range(1, 20000)}
    assert len(built) == 19999
