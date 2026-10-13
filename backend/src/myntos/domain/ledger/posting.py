"""Double-entry posting engine.

Everything here is pure Python over the ORM objects in
:mod:`myntos.domain.ledger.models` — no session, no I/O. That keeps the rules
that matter legally (a posted entry balances, and never changes afterwards)
testable without a database, and keeps them in one place instead of scattered
across request handlers.

Two rules carry the weight:

**Exact decimals.** Every amount is a :class:`~decimal.Decimal` with at most
two decimal places. Floats are rejected outright rather than silently
converted — a ledger that is off by 0.01 because of binary rounding is a
ledger that fails an audit, and the failure surfaces years later.

**Balance.** ``sum(debit) == sum(credit)``, compared exactly. There is no
tolerance, because there is no legitimate way for a voucher to be off by a
little.

Corrections never edit history: :func:`build_reversal` produces the reversing
voucher that bokføringsloven expects.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from myntos.domain.ledger.errors import LedgerError
from myntos.domain.ledger.models import (
    TaxCode,
    Transaction,
    TransactionLine,
    TransactionLineAnalysis,
)

# One øre. All monetary values quantize to this.
ORE = Decimal("0.01")
ZERO = Decimal("0.00")


class PostingError(Exception):
    """Base class for every refusal to post."""


class UnbalancedEntryError(PostingError):
    """Debits and credits do not match."""


class ImmutablePostingError(PostingError):
    """An attempt to change a transaction that has already been posted."""


class TaxRateNotFoundError(PostingError, LedgerError):
    """No tax rate is in force for the requested code and date.

    Also a :class:`LedgerError`, so the API answers it as a refusal and not as
    a crash: since the previous program's earlier years can be brought over
    (:func:`myntos.domain.ledger.service.open_earlier_years`), a year Myntos
    holds may have no rate for a code nobody proved, and an entry there with
    that code is refused rather than given an invented rate.
    """


class AmbiguousTaxRateError(PostingError):
    """Overlapping validity intervals — the rate table itself is wrong."""


def quantize_money(value: Decimal) -> Decimal:
    """Round a computed amount to øre, half away from zero.

    Used for derived amounts (VAT from a basis), never to paper over an input
    that arrived with more precision than it should have — those are rejected
    by :func:`validate`.
    """
    return value.quantize(ORE, rounding=ROUND_HALF_UP)


def _check_amount(value: object, field: str) -> Decimal:
    """Reject anything that is not a non-negative Decimal in whole øre."""
    if not isinstance(value, Decimal):
        raise PostingError(f"{field} must be a Decimal, got {type(value).__name__}")
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        raise PostingError(f"{field} is not a finite amount: {value}")
    if exponent < -2:
        raise PostingError(f"{field} has sub-øre precision: {value}")
    if value < 0:
        raise PostingError(f"{field} must not be negative: {value}")
    return value


def _amount_or_zero(value: Decimal | None, field: str) -> Decimal:
    """Treat an unset amount as zero, exactly as the database would.

    ``debit`` and ``credit`` carry a column default of ``0.00``, which
    SQLAlchemy only applies on INSERT — on a freshly constructed line the
    attribute is still ``None``. Validating such a line must reach the same
    verdict as validating it after a flush, so ``None`` is normalised here
    rather than rejected.
    """
    if value is None:
        return ZERO
    return _check_amount(value, field)


def rate_on(tax_code: TaxCode, on: date) -> Decimal:
    """Return the rate in force for ``tax_code`` on ``on``.

    ``valid_to`` is exclusive, so a rate ending 2025-07-01 does not apply on
    2025-07-01 itself — which is exactly how the water/wastewater cut was
    worded. Overlapping intervals raise rather than silently picking one.
    """
    matches = [
        r for r in tax_code.rates if r.valid_from <= on and (r.valid_to is None or on < r.valid_to)
    ]
    if not matches:
        raise TaxRateNotFoundError(
            f"Myntos har ingen sats for mva-kode {tax_code.code} {on:%d.%m.%Y}. Satsene for årene "
            "som ble hentet fra det gamle programmet, gjelder bare kodene filene brukte"
        )
    if len(matches) > 1:
        raise AmbiguousTaxRateError(
            f"{len(matches)} overlapping rates for tax code {tax_code.code!r} on {on.isoformat()}"
        )
    return matches[0].rate


def compute_tax(basis: Decimal, rate: Decimal) -> Decimal:
    """VAT amount for a basis and rate, rounded to øre.

    Skatteetaten's validation compares ``grunnlag * sats`` against the reported
    ``merverdiavgift`` with a tolerance, so rounding here is expected — what is
    not tolerated is rounding the basis itself to whole kroner first.
    """
    _check_amount(basis, "basis")
    if rate < 0:
        raise PostingError(f"rate must not be negative: {rate}")
    return quantize_money(basis * rate)


def split_gross(gross: Decimal, rate: Decimal) -> tuple[Decimal, Decimal]:
    """Split a gross amount into ``(net, vat)`` that add back to it exactly.

    Cash-flow registration starts from the amount that actually left or entered
    the bank account — gross, VAT included — while the ledger needs the net
    basis and the VAT as separate lines. The VAT is therefore derived as
    ``gross - net`` rather than as ``net * rate``: rounding the two halves
    independently disagrees by an øre often enough to matter, and a voucher that
    is off by an øre does not post at all.

    The consequence is that ``vat`` here can differ from
    :func:`compute_tax` on the same basis by one øre. That is the correct
    trade: the cash amount is the fact, and the split has to honour it.
    """
    _check_amount(gross, "gross")
    if rate < 0:
        raise PostingError(f"rate must not be negative: {rate}")
    net = quantize_money(gross / (Decimal(1) + rate))
    return net, gross - net


def set_tax_information(
    line: TransactionLine,
    *,
    tax_code: TaxCode,
    rate: Decimal,
    basis: Decimal,
    amount: Decimal,
) -> None:
    """Write the four SAF-T tax fields onto a **basis** line.

    The single place that populates ``TaxInformation``, so the two callers —
    :func:`apply_tax`, which derives the VAT from a net basis, and the
    cash-flow registration, which derives it from a gross amount — cannot drift
    apart on which side the amounts land or on filling both elements.

    The VAT amount goes on the same side as the line's own amount, and the
    opposite side becomes ``0.00`` rather than staying ``NULL``: SAF-T requires
    both tax-amount elements once ``TaxInformation`` is present, and an empty
    element fails validation.
    """
    debit = _amount_or_zero(line.debit, "line debit")
    credit = _amount_or_zero(line.credit, "line credit")
    if debit == ZERO and credit == ZERO:
        raise PostingError(f"line {line.record_id!r} has no amount to attach tax to")
    _check_amount(basis, "tax basis")
    _check_amount(amount, "tax amount")

    line.tax_code = tax_code
    line.tax_rate = rate
    line.tax_base = basis
    if debit > ZERO:
        line.debit_tax_amount = amount
        line.credit_tax_amount = ZERO
    else:
        line.debit_tax_amount = ZERO
        line.credit_tax_amount = amount


def apply_tax(line: TransactionLine, tax_code: TaxCode, on: date) -> Decimal:
    """Attach tax information to a **basis** line and return the VAT amount.

    Call this on the line carrying the net amount (the sales or cost account),
    never on the VAT account line itself — putting ``TaxInformation`` on the
    VAT line is the classic SAF-T exporter bug. The rate is resolved once and
    snapshotted onto the line, so later rate changes cannot rewrite history.
    """
    debit = _amount_or_zero(line.debit, "line debit")
    credit = _amount_or_zero(line.credit, "line credit")
    basis = debit if debit > ZERO else credit
    if basis == ZERO:
        raise PostingError(f"line {line.record_id!r} has no amount to compute tax from")

    rate = rate_on(tax_code, on)
    amount = compute_tax(basis, rate)
    set_tax_information(line, tax_code=tax_code, rate=rate, basis=basis, amount=amount)
    return amount


def totals(transaction: Transaction) -> tuple[Decimal, Decimal]:
    """Return ``(debit_total, credit_total)`` for a transaction."""
    debit = sum((_amount_or_zero(line.debit, "debit") for line in transaction.lines), start=ZERO)
    credit = sum((_amount_or_zero(line.credit, "credit") for line in transaction.lines), start=ZERO)
    return debit, credit


def validate(transaction: Transaction) -> None:
    """Raise if ``transaction`` may not be posted.

    Checks every invariant the database cannot express on its own: the
    cross-line balance, uniqueness of record IDs within the voucher, exact
    decimals, and the all-or-nothing shape of SAF-T tax information.
    """
    lines = transaction.lines
    if len(lines) < 2:
        raise PostingError(
            f"A voucher needs at least two lines, got {len(lines)} ({transaction.transaction_id!r})"
        )

    if not 1 <= transaction.period <= 12:
        raise PostingError(f"period must be 1-12, got {transaction.period}")

    seen: set[str] = set()
    for line in lines:
        if line.record_id in seen:
            raise PostingError(f"Duplicate record_id {line.record_id!r} in voucher")
        seen.add(line.record_id)
        _validate_line(line)

    debit, credit = totals(transaction)
    if debit != credit:
        raise UnbalancedEntryError(
            f"Voucher {transaction.transaction_id!r} is out of balance: "
            f"debit {debit} vs credit {credit} (difference {debit - credit})"
        )
    if debit == ZERO:
        raise PostingError(f"Voucher {transaction.transaction_id!r} has no amounts")


def _validate_line(line: TransactionLine) -> None:
    label = f"line {line.record_id!r}"
    debit = _amount_or_zero(line.debit, f"{label} debit")
    credit = _amount_or_zero(line.credit, f"{label} credit")

    if debit > ZERO and credit > ZERO:
        raise PostingError(f"{label} carries both a debit and a credit amount")
    if debit == ZERO and credit == ZERO:
        raise PostingError(f"{label} has no amount")

    _validate_line_tax(line, label)
    _validate_line_analysis(line, label, debit + credit)


def _validate_line_tax(line: TransactionLine, label: str) -> None:
    """Tax information is all-or-nothing, per SAF-T."""
    tax_fields = (line.tax_rate, line.tax_base, line.debit_tax_amount, line.credit_tax_amount)
    has_code = line.tax_code is not None or line.tax_code_id is not None

    if not has_code:
        if any(field is not None for field in tax_fields):
            raise PostingError(f"{label} has tax amounts but no tax code")
        return

    if any(field is None for field in tax_fields):
        raise PostingError(
            f"{label} has a tax code but incomplete tax information; "
            f"rate, basis and both tax amounts are all mandatory"
        )
    # mypy: the None case is excluded above.
    assert line.tax_base is not None  # noqa: S101
    assert line.debit_tax_amount is not None  # noqa: S101
    assert line.credit_tax_amount is not None  # noqa: S101
    _check_amount(line.tax_base, f"{label} tax_base")
    _check_amount(line.debit_tax_amount, f"{label} debit_tax_amount")
    _check_amount(line.credit_tax_amount, f"{label} credit_tax_amount")


def _dimension_of(allocation: TransactionLineAnalysis) -> object:
    """Which dimension an allocation is in, read without loading anything.

    The value's dimension when the value object is on the allocation (every new
    allocation has it: myntos.domain.ledger.dimensions.allocate). A reversal's
    copy of a loaded allocation carries only the value's id, and is keyed by
    that: its original was checked when it was posted, and a mirror of it
    cannot break the rule.
    """
    value = allocation.__dict__.get("dimension_value")
    if value is None:
        return ("value", allocation.dimension_value_id)
    if value.dimension_type_id is not None:
        return ("dimension", value.dimension_type_id)
    return ("dimension-object", id(value.__dict__.get("dimension_type")))


def _validate_line_analysis(line: TransactionLine, label: str, line_amount: Decimal) -> None:
    """Dimension allocations may be partial, but never exceed the line, per dimension.

    Per dimension and not summed across them: a line on the milk production in
    the farm department is allocated in full twice, once to its driftsgren and
    once to its avdeling, and that is one amount sorted two ways, not twice the
    amount. Summed, it was refused the first time a line had two dimensions.
    """
    if not line.analysis:
        return
    allocated: dict[object, Decimal] = {}
    for allocation in line.analysis:
        amount = _check_amount(allocation.amount, f"{label} analysis amount")
        key = _dimension_of(allocation)
        allocated[key] = allocated.get(key, ZERO) + amount
    for total in allocated.values():
        if total > line_amount:
            raise PostingError(
                f"{label} allocates {total} to one dimension but the line is only {line_amount}"
            )


def assert_mutable(transaction: Transaction) -> None:
    """Guard for any code path that intends to change a transaction."""
    if transaction.is_posted:
        raise ImmutablePostingError(
            f"Voucher {transaction.transaction_id!r} was posted at "
            f"{transaction.posted_at} and cannot be changed; post a reversing "
            f"voucher instead"
        )


def post(transaction: Transaction, *, now: datetime | None = None) -> Transaction:
    """Validate and post a draft voucher.

    After this returns, the voucher is history: :func:`assert_mutable` will
    refuse further edits, and corrections go through :func:`build_reversal`.
    """
    assert_mutable(transaction)
    validate(transaction)
    transaction.posted_at = now or datetime.now(UTC)
    return transaction


def build_reversal(
    transaction: Transaction,
    *,
    transaction_id: str,
    gl_posting_date: date,
    period: int,
    financial_year: int,
    description: str | None = None,
) -> Transaction:
    """Build the reversing voucher for a posted transaction.

    Debits become credits and vice versa, tax amounts included. The result is
    an unposted draft — the caller decides when it enters the ledger, and
    :func:`post` validates it like any other voucher.
    """
    if not transaction.is_posted:
        raise PostingError(
            f"Voucher {transaction.transaction_id!r} is not posted; edit the draft instead "
            f"of reversing it"
        )

    reversal = Transaction(
        journal_id=transaction.journal_id,
        transaction_id=transaction_id,
        financial_year=financial_year,
        period=period,
        transaction_date=transaction.transaction_date,
        gl_posting_date=gl_posting_date,
        description=description or f"Reversering av {transaction.transaction_id}",
        voucher_type=transaction.voucher_type,
        source_document_id=transaction.source_document_id,
    )
    reversal.lines = [_reverse_line(line) for line in transaction.lines]
    return reversal


def _reverse_line(line: TransactionLine) -> TransactionLine:
    reversed_line = TransactionLine(
        record_id=line.record_id,
        account_id=line.account_id,
        party_id=line.party_id,
        description=line.description,
        debit=line.credit,
        credit=line.debit,
        currency_code=line.currency_code,
        currency_amount=line.currency_amount,
        exchange_rate=line.exchange_rate,
        tax_code_id=line.tax_code_id,
        tax_rate=line.tax_rate,
        tax_base=line.tax_base,
        # Tax amounts swap sides with everything else, or the MVA-melding
        # counts the original twice instead of netting it out.
        debit_tax_amount=line.credit_tax_amount,
        credit_tax_amount=line.debit_tax_amount,
        # The melding groups by spesifikasjon as well as by code, so a reversed
        # tap på krav has to land in the tap på krav line to cancel it. Dropped,
        # it would net against the period's ordinary deductions instead and
        # leave both lines wrong by the same amount.
        vat_specification=line.vat_specification,
        # A composite entry reads back line by line, and so must its reversal,
        # or the register shows the cancelling voucher as lines of nothing.
        entry_type=line.entry_type,
    )
    # Copy the relationships too, and only when they are actually set: on a
    # transaction that has not been flushed the foreign key columns are still
    # None, and copying only the ids would silently drop the account and tax
    # code while keeping the amounts. Assigning None unconditionally would be
    # just as wrong the other way — it would null out a loaded FK.
    if line.account is not None:
        reversed_line.account = line.account
    if line.tax_code is not None:
        reversed_line.tax_code = line.tax_code
    # The counterparty too: a reversal that dropped it would leave the
    # customer's or supplier's sub-ledger permanently overstated, and reading
    # the reversal back would try to lazy-load the relationship on an async
    # session, which raises.
    if line.party is not None:
        reversed_line.party = line.party
    # Dimension allocations are part of the entry; a reversal that omits them
    # leaves the department or project reporting permanently overstated.
    reversed_line.analysis = [_copy_allocation(a) for a in line.analysis]
    return reversed_line


def _copy_allocation(allocation: TransactionLineAnalysis) -> TransactionLineAnalysis:
    copy = TransactionLineAnalysis(
        dimension_value_id=allocation.dimension_value_id,
        amount=allocation.amount,
    )
    # The object too, but only when it is already on the allocation: a new one,
    # or one whose query loaded it. Reading the relationship otherwise is a lazy
    # load, which an async session refuses (MissingGreenlet), and every
    # reversal path that did not ask for the values would have raised on the
    # first allocated line it met. Without the object the copy is checked per
    # value rather than per dimension (_dimension_of), which its original
    # already passed.
    value = allocation.__dict__.get("dimension_value")
    if value is not None:
        copy.dimension_value = value
    return copy


def derive_closing_balance(
    *,
    opening_debit: Decimal,
    opening_credit: Decimal,
    debit_total: Decimal,
    credit_total: Decimal,
) -> tuple[Decimal, Decimal]:
    """Net opening balance and period movement into a closing debit/credit pair.

    SAF-T wants a closing balance on every account, but storing one alongside
    the postings creates a second source of truth that drifts. The exporter
    calls this instead. The result is one-sided: a net debit position reports
    ``(x, 0.00)``, a net credit position ``(0.00, x)``.
    """
    net = (opening_debit + debit_total) - (opening_credit + credit_total)
    if net >= ZERO:
        return quantize_money(net), ZERO
    return ZERO, quantize_money(-net)
