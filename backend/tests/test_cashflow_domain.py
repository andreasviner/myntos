"""Pure checks on the cash-flow catalogue, the orgnr rule and the gross split.

No database. These are the parts that have to be right before any endpoint can
be, and they are cheap enough to assert exhaustively — every catalogue entry,
every rate, a decade of amounts.
"""

from decimal import Decimal

import pytest

from myntos.domain.ledger import posting
from myntos.domain.ledger.cashflow import (
    CASH_FLOW_TYPES,
    CashFlowDirection,
    get_type,
    types_for,
)
from myntos.domain.ledger.chart import (
    ACCOUNTS_BY_NUMBER,
    STANDARD_ACCOUNTS,
    STANDARD_TAX_CODES,
    TAX_CODES_BY_CODE,
)
from myntos.domain.ledger.errors import InvalidOrgnrError, UnknownCashFlowTypeError
from myntos.domain.ledger.models import NON_REPORTABLE_STANDARD_TAX_CODES
from myntos.domain.ledger.orgnr import validate_orgnr

# The rates the catalogue can produce, plus the boundary ones.
RATES = [Decimal("0.00"), Decimal("0.12"), Decimal("0.15"), Decimal("0.25")]


def decimal_places(value: Decimal) -> int:
    """How many digits after the point — ``Decimal.as_tuple`` also types NaN."""
    exponent = value.as_tuple().exponent
    return -exponent if isinstance(exponent, int) else 0


# --- The catalogue --------------------------------------------------------------


def test_every_type_points_at_a_seeded_account_and_tax_code() -> None:
    """A category the seeded chart cannot serve is a 500 waiting for a user."""
    for entry in types_for():
        assert entry.account_number in ACCOUNTS_BY_NUMBER, entry.code
        assert entry.default_tax_code in TAX_CODES_BY_CODE, entry.code


def test_account_numbers_are_unique() -> None:
    numbers = [account.number for account in STANDARD_ACCOUNTS]
    assert len(numbers) == len(set(numbers))


def test_both_directions_are_offered() -> None:
    assert types_for(CashFlowDirection.INN)
    assert types_for(CashFlowDirection.UT)
    assert len(types_for()) == len(CASH_FLOW_TYPES)


def test_income_types_book_to_income_or_equity_accounts() -> None:
    """Money in must not land on a cost account, nor money out on a revenue one."""
    for entry in types_for(CashFlowDirection.INN):
        account = ACCOUNTS_BY_NUMBER[entry.account_number]
        assert account.account_type.value in {"inntekt", "egenkapital"}, entry.code
    for entry in types_for(CashFlowDirection.UT):
        account = ACCOUNTS_BY_NUMBER[entry.account_number]
        # "eiendel" belongs here too, and only for money out: buying a
        # driftsmiddel is cash leaving for something that is not consumed, so
        # the value lands on the balance sheet and reaches the income statement
        # later as avskrivning. The reverse has no meaning — money coming in
        # cannot land on an asset account through this register — which is why
        # the two directions still allow different sets.
        assert account.account_type.value in {"kostnad", "egenkapital", "eiendel"}, entry.code


def test_every_type_carries_a_note() -> None:
    """The note is where the default VAT assumption is stated; an empty one lies."""
    for entry in types_for():
        assert entry.note.strip()


def test_owner_movements_stay_out_of_the_income_statement() -> None:
    """A withdrawal is not a cost and a deposit is not income."""
    assert not get_type("privatuttak").is_income_statement
    assert not get_type("egenkapitalinnskudd").is_income_statement
    assert get_type("varekjop").is_income_statement


def test_unknown_type_is_rejected() -> None:
    with pytest.raises(UnknownCashFlowTypeError):
        get_type("kjop_av_enhjorning")


def test_excluded_tax_codes_are_marked_non_reportable() -> None:
    """Codes 0, 7, 20, 21 and 22 must never reach the MVA-melding."""
    for seed in STANDARD_TAX_CODES:
        expected = seed.standard_tax_code not in NON_REPORTABLE_STANDARD_TAX_CODES
        assert seed.is_reportable is expected, seed.code


def test_zero_rated_sales_codes_are_still_reportable() -> None:
    """0 % is not the same as "outside the system" — the melding wants the basis."""
    for code in ("5", "6", "52"):
        seed = TAX_CODES_BY_CODE[code]
        assert seed.rate == Decimal("0.000000")
        assert seed.is_reportable


# --- Organisasjonsnummer --------------------------------------------------------


@pytest.mark.parametrize("orgnr", ["910000004", "910000012", "910000020", "910 000 039"])
def test_valid_orgnr_is_accepted_and_normalised(orgnr: str) -> None:
    assert validate_orgnr(orgnr) == orgnr.replace(" ", "")


@pytest.mark.parametrize(
    "orgnr",
    [
        "910000005",  # right length, wrong check digit
        "91000000",  # eight digits
        "9100000045",  # ten digits
        "91000000a",
        "",
    ],
)
def test_invalid_orgnr_is_rejected(orgnr: str) -> None:
    with pytest.raises(InvalidOrgnrError):
        validate_orgnr(orgnr)


# --- Splitting a gross amount ---------------------------------------------------


@pytest.mark.parametrize("rate", RATES)
@pytest.mark.parametrize(
    "gross",
    ["0.01", "0.99", "1.00", "12.34", "99.99", "1250.00", "1000.01", "123456.78"],
)
def test_split_gross_always_adds_back_to_the_gross(gross: str, rate: Decimal) -> None:
    """The invariant the whole register rests on: the voucher must balance."""
    amount = Decimal(gross)
    net, vat = posting.split_gross(amount, rate)
    assert net + vat == amount
    assert net >= Decimal("0.00")
    assert vat >= Decimal("0.00")
    assert decimal_places(net) <= 2
    assert decimal_places(vat) <= 2


def test_split_gross_on_a_round_amount() -> None:
    assert posting.split_gross(Decimal("1250.00"), Decimal("0.25")) == (
        Decimal("1000.00"),
        Decimal("250.00"),
    )


def test_split_gross_at_zero_rate_is_all_net() -> None:
    assert posting.split_gross(Decimal("500.00"), Decimal("0.00")) == (
        Decimal("500.00"),
        Decimal("0.00"),
    )


def test_split_gross_rejects_a_negative_rate() -> None:
    with pytest.raises(posting.PostingError):
        posting.split_gross(Decimal("100.00"), Decimal("-0.25"))


def test_split_gross_rejects_sub_ore_input() -> None:
    with pytest.raises(posting.PostingError):
        posting.split_gross(Decimal("100.001"), Decimal("0.25"))
