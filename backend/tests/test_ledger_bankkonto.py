"""The kontonummer check, which is the last line before an uncorrectable invoice.

Once a salgsdokument is issued it can only be corrected with a kreditnota, and
the account number on it is printed and put in the EHF. So a wrong one is not a
field to fix later — it is a document to write again.
"""

import re

import pytest

from myntos.domain.ledger.bankkonto import format_bank_account, validate_bank_account
from myntos.domain.ledger.errors import InvalidBankAccountError

# A number whose mod-11 check digit works out. 1506.20.3315 + 8.
VALID = "15062033158"


@pytest.mark.parametrize(
    "typed",
    [
        "15062033158",
        "1506.20.33158",
        "1506 20 33158",
        "1506-20-33158",
        # A Norwegian IBAN is the same eleven digits behind a prefix.
        "NO9315062033158",
        "no93 1506 2033 158",
    ],
)
def test_the_separators_people_type_are_accepted(typed: str) -> None:
    assert validate_bank_account(typed) == VALID


@pytest.mark.parametrize(
    "typed",
    [
        # A transposed pair — the case the check digit exists to catch.
        "15062033518",
        "15062033142",
        "1506203315",
        "150620331580",
        "1506203315x",
        "",
        "   ",
        # A foreign IBAN. Refused rather than stored as something the PDF and
        # the EHF cannot present.
        "DE89370400440532013000",
    ],
)
def test_a_number_that_cannot_be_an_account_is_refused(typed: str) -> None:
    with pytest.raises(InvalidBankAccountError):
        validate_bank_account(typed)


def test_the_refusal_says_which_number_it_read() -> None:
    with pytest.raises(InvalidBankAccountError, match=re.escape("1506.20.33518")):
        validate_bank_account("15062033518")


def test_display_grouping_is_the_one_a_norwegian_reads() -> None:
    assert format_bank_account(VALID) == "1506.20.33158"


def test_a_stored_value_of_another_shape_is_shown_as_it_is() -> None:
    # Rows written before this module existed. Mangling them into a grouping
    # they do not have would be worse than showing what is actually stored.
    assert format_bank_account("GB33BUKB20201555555555") == "GB33BUKB20201555555555"
