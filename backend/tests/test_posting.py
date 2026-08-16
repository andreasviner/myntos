"""Posting engine invariants.

Everything here runs on plain ORM objects with no database — the rules being
tested are accounting rules, not storage behaviour.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from myntos.domain.ledger.models import (
    TaxCode,
    TaxRate,
    Transaction,
    TransactionLine,
    TransactionLineAnalysis,
)
from myntos.domain.ledger.posting import (
    AmbiguousTaxRateError,
    ImmutablePostingError,
    PostingError,
    TaxRateNotFoundError,
    UnbalancedEntryError,
    apply_tax,
    assert_mutable,
    build_reversal,
    compute_tax,
    derive_closing_balance,
    post,
    rate_on,
    totals,
    validate,
)


def kr(amount: str) -> Decimal:
    return Decimal(amount)


def make_transaction(*lines: TransactionLine, transaction_id: str = "B-1") -> Transaction:
    transaction = Transaction(
        transaction_id=transaction_id,
        financial_year=2026,
        period=8,
        transaction_date=date(2026, 8, 1),
        gl_posting_date=date(2026, 8, 1),
        description="Testbilag",
    )
    transaction.lines = list(lines)
    return transaction


def line(
    record_id: str,
    *,
    debit: str = "0.00",
    credit: str = "0.00",
    account_id: int = 1,
) -> TransactionLine:
    return TransactionLine(
        record_id=record_id,
        account_id=account_id,
        description="linje",
        debit=kr(debit),
        credit=kr(credit),
    )


def mva_code(code: str = "3", *, rates: list[TaxRate] | None = None) -> TaxCode:
    tax_code = TaxCode(
        code=code,
        description="Utgående MVA, høy sats",
        standard_tax_code=code,
        is_reportable=True,
    )
    tax_code.rates = rates or [TaxRate(valid_from=date(2026, 1, 1), rate=Decimal("0.250000"))]
    return tax_code


# --- Balance -------------------------------------------------------------


def test_balanced_voucher_posts() -> None:
    transaction = make_transaction(
        line("1", debit="1250.00"),
        line("2", credit="1250.00"),
    )
    posted = post(transaction, now=datetime(2026, 8, 12, tzinfo=UTC))
    assert posted.is_posted
    assert totals(transaction) == (kr("1250.00"), kr("1250.00"))


def test_unbalanced_voucher_is_refused_and_names_the_difference() -> None:
    transaction = make_transaction(
        line("1", debit="1250.00"),
        line("2", credit="1249.99"),
    )
    with pytest.raises(UnbalancedEntryError) as excinfo:
        validate(transaction)
    assert "0.01" in str(excinfo.value)


def test_balance_has_no_tolerance() -> None:
    """One øre is a failure, not a rounding artefact."""
    transaction = make_transaction(
        line("1", debit="0.02"),
        line("2", credit="0.01"),
    )
    with pytest.raises(UnbalancedEntryError):
        validate(transaction)


def test_voucher_needs_at_least_two_lines() -> None:
    transaction = make_transaction(line("1", debit="100.00"))
    with pytest.raises(PostingError, match="at least two lines"):
        validate(transaction)


def test_voucher_of_only_zero_amounts_is_refused() -> None:
    transaction = make_transaction(line("1"), line("2"))
    with pytest.raises(PostingError, match="no amount"):
        validate(transaction)


def test_duplicate_record_ids_are_refused() -> None:
    transaction = make_transaction(
        line("1", debit="100.00"),
        line("1", credit="100.00"),
    )
    with pytest.raises(PostingError, match="Duplicate record_id"):
        validate(transaction)


def test_period_must_be_a_month() -> None:
    transaction = make_transaction(line("1", debit="1.00"), line("2", credit="1.00"))
    transaction.period = 13
    with pytest.raises(PostingError, match="period must be 1-12"):
        validate(transaction)


# --- Exact decimals ------------------------------------------------------


def test_floats_are_rejected() -> None:
    bad = line("1")
    bad.debit = 1250.00  # type: ignore[assignment]
    transaction = make_transaction(bad, line("2", credit="1250.00"))
    with pytest.raises(PostingError, match="must be a Decimal"):
        validate(transaction)


def test_sub_ore_precision_is_rejected() -> None:
    transaction = make_transaction(
        line("1", debit="1250.005"),
        line("2", credit="1250.005"),
    )
    with pytest.raises(PostingError, match="sub-øre precision"):
        validate(transaction)


def test_negative_amounts_are_rejected() -> None:
    """Direction is expressed by the debit/credit pair, never by a sign."""
    transaction = make_transaction(
        line("1", debit="-100.00"),
        line("2", credit="-100.00"),
    )
    with pytest.raises(PostingError, match="must not be negative"):
        validate(transaction)


def test_a_line_cannot_carry_both_sides() -> None:
    transaction = make_transaction(
        line("1", debit="100.00", credit="100.00"),
        line("2", credit="100.00"),
    )
    with pytest.raises(PostingError, match="both a debit and a credit"):
        validate(transaction)


def test_unset_amounts_count_as_zero() -> None:
    """A freshly built line has None until flush; that must mean 0.00."""
    unset = TransactionLine(record_id="1", account_id=1, description="linje", debit=kr("100.00"))
    transaction = make_transaction(unset, line("2", credit="100.00"))
    validate(transaction)


# --- Immutability --------------------------------------------------------


def test_posted_voucher_cannot_be_posted_again() -> None:
    transaction = make_transaction(line("1", debit="10.00"), line("2", credit="10.00"))
    post(transaction)
    with pytest.raises(ImmutablePostingError):
        post(transaction)


def test_assert_mutable_points_at_the_reversal_path() -> None:
    transaction = make_transaction(line("1", debit="10.00"), line("2", credit="10.00"))
    post(transaction)
    with pytest.raises(ImmutablePostingError, match="reversing voucher"):
        assert_mutable(transaction)


def test_draft_is_mutable() -> None:
    transaction = make_transaction(line("1", debit="10.00"), line("2", credit="10.00"))
    assert_mutable(transaction)


# --- Date-effective tax rates -------------------------------------------


def water_and_wastewater() -> TaxCode:
    """SAF-T code 31: 25 % until 2025-07-01, 15 % from then on.

    The real mid-year change that a single rate column on the tax code would
    get wrong for every 2025 period straddling it.
    """
    return mva_code(
        "31",
        rates=[
            TaxRate(
                valid_from=date(2025, 1, 1),
                valid_to=date(2025, 7, 1),
                rate=Decimal("0.250000"),
            ),
            TaxRate(valid_from=date(2025, 7, 1), rate=Decimal("0.150000")),
        ],
    )


def test_rate_before_the_change() -> None:
    assert rate_on(water_and_wastewater(), date(2025, 6, 30)) == Decimal("0.250000")


def test_valid_to_is_exclusive() -> None:
    """On the day of the change the new rate applies, not the old one."""
    assert rate_on(water_and_wastewater(), date(2025, 7, 1)) == Decimal("0.150000")


def test_open_ended_rate_still_applies_later() -> None:
    assert rate_on(water_and_wastewater(), date(2026, 8, 12)) == Decimal("0.150000")


def test_missing_rate_is_an_error_not_a_zero() -> None:
    with pytest.raises(TaxRateNotFoundError):
        rate_on(water_and_wastewater(), date(2024, 12, 31))


def test_overlapping_rates_are_refused() -> None:
    broken = mva_code(
        "31",
        rates=[
            TaxRate(valid_from=date(2025, 1, 1), rate=Decimal("0.250000")),
            TaxRate(valid_from=date(2025, 7, 1), rate=Decimal("0.150000")),
        ],
    )
    with pytest.raises(AmbiguousTaxRateError):
        rate_on(broken, date(2025, 8, 1))


# --- VAT calculation -----------------------------------------------------


def test_compute_tax_rounds_half_up_to_ore() -> None:
    assert compute_tax(kr("100.01"), Decimal("0.250000")) == kr("25.00")
    assert compute_tax(kr("100.02"), Decimal("0.250000")) == kr("25.01")
    assert compute_tax(kr("0.10"), Decimal("0.111100")) == kr("0.01")


def test_apply_tax_puts_the_amount_on_the_lines_own_side() -> None:
    sale = line("2", credit="10000.00")
    amount = apply_tax(sale, mva_code(), date(2026, 8, 1))
    assert amount == kr("2500.00")
    assert sale.credit_tax_amount == kr("2500.00")
    # 0.00 rather than NULL: SAF-T requires both elements once TaxInformation
    # is present, and an empty element fails validation.
    assert sale.debit_tax_amount == kr("0.00")
    assert sale.tax_base == kr("10000.00")
    assert sale.tax_rate == Decimal("0.250000")


def test_apply_tax_snapshots_the_rate() -> None:
    """Changing the rate table later must not rewrite a posted line."""
    code = mva_code()
    sale = line("2", credit="10000.00")
    apply_tax(sale, code, date(2026, 8, 1))

    code.rates.append(TaxRate(valid_from=date(2026, 9, 1), rate=Decimal("0.300000")))

    assert sale.tax_rate == Decimal("0.250000")
    assert sale.credit_tax_amount == kr("2500.00")


def test_realistic_sales_voucher_with_vat_balances() -> None:
    receivable = line("1", debit="12500.00", account_id=1500)
    revenue = line("2", credit="10000.00", account_id=3000)
    output_vat = line("3", credit="2500.00", account_id=2700)

    apply_tax(revenue, mva_code(), date(2026, 8, 1))

    transaction = make_transaction(receivable, revenue, output_vat)
    post(transaction)

    assert totals(transaction) == (kr("12500.00"), kr("12500.00"))
    # TaxInformation sits on the basis line, not on the VAT account line.
    assert revenue.tax_code is not None
    assert output_vat.tax_code is None


def test_tax_information_is_all_or_nothing() -> None:
    sale = line("2", credit="10000.00")
    sale.tax_code = mva_code()
    sale.tax_base = kr("10000.00")
    # tax_rate and the two amounts deliberately left unset.
    transaction = make_transaction(line("1", debit="10000.00"), sale)
    with pytest.raises(PostingError, match="incomplete tax information"):
        validate(transaction)


def test_tax_amounts_without_a_code_are_refused() -> None:
    stray = line("2", credit="10000.00")
    stray.debit_tax_amount = kr("2500.00")
    transaction = make_transaction(line("1", debit="10000.00"), stray)
    with pytest.raises(PostingError, match="no tax code"):
        validate(transaction)


# --- Dimensions ----------------------------------------------------------


def test_dimension_allocation_may_be_partial() -> None:
    cost = line("1", debit="1000.00")
    cost.analysis = [TransactionLineAnalysis(dimension_value_id=1, amount=kr("400.00"))]
    validate(make_transaction(cost, line("2", credit="1000.00")))


def test_dimension_allocation_cannot_exceed_the_line() -> None:
    cost = line("1", debit="1000.00")
    cost.analysis = [
        TransactionLineAnalysis(dimension_value_id=1, amount=kr("600.00")),
        TransactionLineAnalysis(dimension_value_id=2, amount=kr("600.00")),
    ]
    with pytest.raises(PostingError, match="allocates"):
        validate(make_transaction(cost, line("2", credit="1000.00")))


# --- Reversal ------------------------------------------------------------


def test_reversal_swaps_every_side() -> None:
    revenue = line("2", credit="10000.00", account_id=3000)
    apply_tax(revenue, mva_code(), date(2026, 8, 1))
    original = make_transaction(line("1", debit="10000.00", account_id=1500), revenue)
    post(original)

    reversal = build_reversal(
        original,
        transaction_id="B-2",
        gl_posting_date=date(2026, 9, 1),
        period=9,
        financial_year=2026,
    )

    assert not reversal.is_posted
    assert reversal.description == "Reversering av B-1"
    assert [(line_.debit, line_.credit) for line_ in reversal.lines] == [
        (kr("0.00"), kr("10000.00")),
        (kr("10000.00"), kr("0.00")),
    ]
    # Tax amounts swap sides too, or the MVA-melding double-counts.
    assert reversal.lines[1].debit_tax_amount == kr("2500.00")
    assert reversal.lines[1].credit_tax_amount == kr("0.00")

    validate(reversal)


def test_reversal_carries_the_dimension_allocations() -> None:
    """Otherwise the reversed cost stays on the department's books forever."""
    cost = line("1", debit="1000.00", account_id=6000)
    cost.analysis = [TransactionLineAnalysis(dimension_value_id=7, amount=kr("1000.00"))]
    original = make_transaction(cost, line("2", credit="1000.00", account_id=2400))
    post(original)

    reversal = build_reversal(
        original,
        transaction_id="B-2",
        gl_posting_date=date(2026, 9, 1),
        period=9,
        financial_year=2026,
    )

    assert [(a.dimension_value_id, a.amount) for a in reversal.lines[0].analysis] == [
        (7, kr("1000.00"))
    ]


def test_a_draft_is_edited_not_reversed() -> None:
    draft = make_transaction(line("1", debit="10.00"), line("2", credit="10.00"))
    with pytest.raises(PostingError, match="not posted"):
        build_reversal(
            draft,
            transaction_id="B-2",
            gl_posting_date=date(2026, 9, 1),
            period=9,
            financial_year=2026,
        )


# --- Closing balances ----------------------------------------------------


def test_closing_balance_nets_to_one_side() -> None:
    assert derive_closing_balance(
        opening_debit=kr("1000.00"),
        opening_credit=kr("0.00"),
        debit_total=kr("500.00"),
        credit_total=kr("200.00"),
    ) == (kr("1300.00"), kr("0.00"))


def test_closing_balance_flips_when_credits_dominate() -> None:
    assert derive_closing_balance(
        opening_debit=kr("0.00"),
        opening_credit=kr("100.00"),
        debit_total=kr("50.00"),
        credit_total=kr("500.00"),
    ) == (kr("0.00"), kr("550.00"))
