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
        for customer in (None, "10000", "10042", "99999"):
            generated = kid.preview_for_next(number, customer_number=customer)
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


def test_the_kid_is_customer_then_invoice_then_check_digit() -> None:
    """The ordinary Norwegian structure, and the reason both fields are fixed."""
    built = kid.for_document_number("137", customer_number="10042")

    assert built == "100420001370"
    assert built[:5] == "10042"  # kundenummer
    assert built[5:11] == "000137"  # fakturanummer
    assert built[-1] == kid.mod10_check_digit(built[:-1])


def test_the_two_numbers_can_be_read_back_out() -> None:
    """Which is what an OCR-file importer will do with it."""
    built = kid.for_document_number("137", customer_number="10042")
    assert kid.split(built) == ("10042", "000137")


@pytest.mark.parametrize("reference", ["0000018", "100420001371", "abcdefghijkl"])
def test_what_cannot_be_read_back(reference: str) -> None:
    """Wrong length, wrong check digit, not digits — a KID from another system."""
    assert kid.split(reference) is None


def test_a_buyer_who_is_not_a_registered_customer_gets_zeros() -> None:
    """Zeros rather than a shorter KID: the width is what the bank agreed."""
    built = kid.for_document_number("1")

    assert built[: kid.CUSTOMER_LENGTH] == "00000"
    assert len(built) == kid.LENGTH


def test_a_customer_number_from_another_system_keeps_its_digits() -> None:
    """An accountant migrating in may number customers "K-1042"."""
    built = kid.for_document_number("7", customer_number="K-1042")

    assert built[: kid.CUSTOMER_LENGTH] == "01042"
    assert kid.is_valid(built)


def test_the_width_never_changes() -> None:
    """A reference that grows between invoice 9 and 10 is one the bank rejects."""
    widths = {
        len(kid.preview_for_next(n, customer_number=c))
        for n in (1, 9, 10, 999, 1000, 999999)
        for c in (None, "1", "10000", "99999")
    }
    assert widths == {kid.LENGTH}


def test_a_kid_never_outgrows_the_field() -> None:
    built = kid.for_document_number("9" * 40, customer_number="9" * 40)
    assert len(built) <= kid.MAX_LENGTH


def test_consecutive_invoices_to_one_customer_stay_distinct() -> None:
    """The property that matters: no two invoices may share a KID."""
    built = {kid.for_document_number(str(n), customer_number="10042") for n in range(1, 2000)}
    assert len(built) == 1999
