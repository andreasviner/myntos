"""The saldo per group per year: what the skattemelding's saldo specification is built from.

**Rolled forward, never stored.** A saldo at the end of a year is the one at
its start, plus what was delivered, less the grants that come off the cost,
less what was sold, less the depreciation taken, plus a negative saldo taken to
income. Every one of those is either in the register (assets, openings) or in
a posted voucher (:class:`~myntos.domain.anlegg.models.FixedAssetDepreciation`
rows of a voucher that stands), so the saldo is derived from them each time,
like a closing balance is derived from postings. A stored saldo would be a
second source of truth that drifts the first time an asset is corrected.

**Which choices are the company's and which are the law's.** How much to
depreciate is the company's choice up to the maximum (Skatte-ABC D-2-9.1), and
so is how much more than the minimum of a negative saldo to take to income;
those are read from the posted voucher. A year without a posted voucher took
nothing: the saldo carries forward whole, and the page says the year was never
posted. What the law decides is applied whether or not anything was posted:
an asset in groups e to i that is sold is settled against its saldo in the
year it leaves, and the difference goes to the gevinst- og tapskonto
(§ 14-44 tredje ledd); a negative saldo in group b goes there too.

**The gevinst- og tapskonto is a tax balance, not an account.** Gains and
losses in groups e to i, and on assets outside the saldo system (§ 14-53), go
there and are taken to income or deducted with 20 % a year (§ 14-45), the
whole of it under 30 000 (§ 14-47). Nothing is posted for it: the books show
the gain when the asset leaves, and the difference is the næringsspesifikasjon's
to reconcile (docs/anlegg.md). Myntos uses the law's default every year,
exactly the minimum income and the maximum deduction.

**One saldo, several accounts.** A collective group may sit on more than one
balance-sheet account (a farm's tractor on 1200, the office furniture on 1250,
both group d). The rules apply to the group's total, and the depreciation is
spread over the accounts in proportion to what each carries, so each account's
book value follows the saldo it is part of.

:func:`specification` is the function the næringsspesifikasjon calls.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_FLOOR, Decimal

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from myntos.domain.anlegg import rules
from myntos.domain.anlegg.models import FixedAsset, FixedAssetDepreciation, FixedAssetOpening
from myntos.domain.anlegg.rules import (
    COLLECTIVE,
    NEGATIVE_INCOME,
    PER_BUILDING,
    SALE_REDUCES_SALDO,
    ZERO,
    Saldogruppe,
    Satsvariant,
)
from myntos.domain.ledger.models import Company, FinancialYear, Transaction

_CENT = Decimal("0.01")

#: The opening row of the gevinst- og tapskonto.
GTK = "gtk"

#: Kinds of :class:`FixedAssetDepreciation` row.
DEPRECIATION = "avskrivning"
NEGATIVE_SALDO = "negativ_saldo"
GAIN = "gevinst"
LOSS = "tap"
#: The basis a voucher was posted on, per saldo and account; signed, and read
#: only to tell whether the register has moved since (never a posting).
BASIS = "grunnlag"


# --- What a saldo is ---------------------------------------------------------------------


def saldo_key(asset: FixedAsset) -> str:
    """Which saldo an asset belongs to: the group, the asset, or the building (§ 14-41)."""
    group = Saldogruppe(asset.saldogruppe)
    if group in COLLECTIVE:
        return group.value
    if group in PER_BUILDING:
        return f"j:{asset.building_id}" if asset.building_id is not None else "j"
    return f"{group.value}:{asset.id}"


def opening_key(row: FixedAssetOpening) -> str:
    if row.saldogruppe == Saldogruppe.J.value and row.building_id is not None:
        return f"j:{row.building_id}"
    return row.saldogruppe


def group_of(key: str) -> Saldogruppe:
    return Saldogruppe(key.split(":", 1)[0])


def grant_deducted(asset: FixedAsset) -> Decimal:
    """The grant that comes off the cost (§ 14-42 annet ledd a), or nothing."""
    return asset.grant if asset.grant_reduces_cost else ZERO


def proceeds_for_tax(asset: FixedAsset) -> Decimal:
    """What the saldo is reduced by when the asset leaves.

    The sale price, plus a tax-free grant when the asset leaves within five
    years of being acquired: the grant was given for an investment the company
    did not keep, and § 14-44 første ledd annet punktum counts it as part of
    the price (Skatte-ABC T-11-4.3).
    """
    proceeds = asset.disposal_proceeds or ZERO
    if (
        not asset.grant_reduces_cost
        and asset.grant > ZERO
        and asset.disposed_on is not None
        and asset.disposed_on < _years_after(asset.acquired_on, 5)
    ):
        proceeds += asset.grant
    return proceeds


def _years_after(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


# --- What comes out ----------------------------------------------------------------------


@dataclass(frozen=True)
class AccountShare:
    """The part of a saldo that sits on one balance-sheet account."""

    account_number: str
    basis: Decimal
    depreciation: Decimal = ZERO
    income: Decimal = ZERO
    # The value cleared off the account when the asset left: positive is a
    # gain (more was received than the saldo held), negative a loss.
    settled_gain: Decimal = ZERO


@dataclass(frozen=True)
class SaldoYear:
    """One saldo in one year: one line of the skattemelding's saldo specification.

    The amounts read left to right, the way the specification prints them:
    ``opening + additions - grants - disposals = basis``, then
    ``basis - depreciation + income + to_gain_loss_account = closing``.
    """

    key: str
    saldogruppe: Saldogruppe
    #: The group's name, or the asset's own description for a saldo per asset.
    label: str
    #: The asset the saldo belongs to (e to i, linear, not depreciable), or the
    #: building a j saldo hangs off; None for a collective group.
    asset_id: int | None
    rate_variant: Satsvariant | None
    #: The maximum rate for the year, or None outside the saldo system.
    rate: Decimal | None
    opening: Decimal
    #: Cost of what was delivered in the year (inngangsverdi, påkostning included).
    additions: Decimal
    #: Public grants deducted from that cost (§ 14-42 annet ledd a).
    grants: Decimal
    #: What was received for what left (realisasjon, uttak), with a tax-free
    #: grant added back when the asset left within five years.
    disposals: Decimal
    #: Grunnlag for avskrivning/inntektsføring (Skatte-ABC D-2-7.1).
    basis: Decimal
    #: The most the year allows: the rate, or all of a rest under 30 000.
    max_depreciation: Decimal
    #: The least the law demands of a negative saldo (§§ 14-46, 14-47).
    min_income: Decimal
    depreciation: Decimal
    income: Decimal
    #: Moved to the gevinst- og tapskonto: positive a gain, negative a loss.
    to_gain_loss_account: Decimal
    closing: Decimal
    #: True when a rest under the limit may be deducted whole (§ 14-47).
    small_rest: bool
    accounts: tuple[AccountShare, ...]


@dataclass(frozen=True)
class GainLossYear:
    """The gevinst- og tapskonto for one year (§ 14-45). Positive is a gevinstsaldo."""

    opening: Decimal
    gains: Decimal
    losses: Decimal
    basis: Decimal
    #: Taken to income: at least 20 %, all of it under 30 000.
    income: Decimal
    #: Deducted: at most 20 %, all of it under 30 000.
    deduction: Decimal
    closing: Decimal


@dataclass(frozen=True)
class SaldoSpecification:
    """Every saldo the company has in one year, and the gevinst- og tapskonto.

    ``posted`` says whether the depreciation in it is what a standing voucher
    posted (True) or Myntos's proposal, the maximum, for a year not yet posted.
    """

    company_id: int
    year: int
    #: The first year the register covers.
    start_year: int
    rules_from_year: int | None
    rates_confirmed: bool
    posted: bool
    saldi: tuple[SaldoYear, ...]
    gain_loss_account: GainLossYear
    #: Years from the start up to this one with saldi but no posted depreciation.
    unposted_years: tuple[int, ...] = field(default=())


# --- Loading -------------------------------------------------------------------------------


@dataclass
class _Register:
    start_year: int
    assets: list[FixedAsset]
    openings: list[FixedAssetOpening]
    # (year, saldo key) -> account -> kind -> amount, from vouchers that stand.
    posted: dict[tuple[int, str], dict[str, dict[str, Decimal]]]
    posted_years: set[int]


async def start_year(db: AsyncSession, company: Company) -> int | None:
    """The first year the register covers: the opening saldi's year, else the first in Myntos."""
    opened = await db.scalar(
        select(func.min(FixedAssetOpening.year)).where(FixedAssetOpening.company_id == company.id)
    )
    if opened is not None:
        return int(opened)
    first = await db.scalar(
        select(func.min(FinancialYear.year)).where(FinancialYear.company_id == company.id)
    )
    return int(first) if first is not None else None


def standing_transaction_filter() -> tuple[ColumnElement[bool], ...]:
    """A depreciation voucher counts while it is posted and nothing reverses it."""
    reversal = aliased(Transaction, name="depreciation_reversal")
    return (
        Transaction.posted_at.is_not(None),
        ~select(reversal.id).where(reversal.reverses_transaction_pk == Transaction.id).exists(),
    )


async def _load(db: AsyncSession, company: Company) -> _Register | None:
    first = await start_year(db, company)
    if first is None:
        return None
    assets = list(
        await db.scalars(
            select(FixedAsset).where(FixedAsset.company_id == company.id).order_by(FixedAsset.id)
        )
    )
    openings = list(
        await db.scalars(
            select(FixedAssetOpening)
            .where(FixedAssetOpening.company_id == company.id)
            .order_by(FixedAssetOpening.id)
        )
    )
    rows = await db.execute(
        select(FixedAssetDepreciation)
        .join(Transaction, Transaction.id == FixedAssetDepreciation.transaction_pk)
        .where(FixedAssetDepreciation.company_id == company.id, *standing_transaction_filter())
    )
    posted: dict[tuple[int, str], dict[str, dict[str, Decimal]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(lambda: ZERO))
    )
    years: set[int] = set()
    for (row,) in rows:
        posted[(row.year, row.saldo_key)][row.account_number][row.kind] += row.amount
        years.add(row.year)
    return _Register(
        start_year=first, assets=assets, openings=openings, posted=posted, posted_years=years
    )


# --- The arithmetic ------------------------------------------------------------------------


def allocate(amount: Decimal, weights: dict[str, Decimal]) -> dict[str, Decimal]:
    """Spread ``amount`` over accounts in proportion to their (positive) weights, to the øre.

    Each share is rounded down and the øre left over go to the largest, so the
    shares always add up to exactly ``amount``.
    """
    positive = {k: w for k, w in weights.items() if w > ZERO}
    if amount == ZERO or not positive:
        return {}
    total = sum(positive.values(), ZERO)
    shares = {
        k: (amount * w / total).quantize(_CENT, rounding=ROUND_FLOOR) for k, w in positive.items()
    }
    largest = max(positive, key=lambda k: (positive[k], k))
    shares[largest] += amount - sum(shares.values(), ZERO)
    return {k: v for k, v in shares.items() if v != ZERO}


@dataclass
class _Running:
    """One saldo as it is rolled forward: the balance per account."""

    key: str
    group: Saldogruppe
    label: str
    asset: FixedAsset | None
    by_account: dict[str, Decimal] = field(default_factory=lambda: defaultdict(lambda: ZERO))
    closed: bool = False


def _label(group: Saldogruppe, asset: FixedAsset | None, buildings: dict[int, FixedAsset]) -> str:
    if group in COLLECTIVE or asset is None:
        return rules.GROUP_LABELS[group]
    if group in PER_BUILDING:
        building = buildings.get(asset.building_id) if asset.building_id is not None else None
        return (
            f"Saldogruppe j: fast teknisk installasjon i {building.description}"
            if building is not None
            else rules.GROUP_LABELS[group]
        )
    return asset.description


def _linear_share(asset: FixedAsset, year: int, basis: Decimal) -> Decimal:
    """The year's equal share of a time-limited right (§ 14-50), in twelfths the first year."""
    life = asset.useful_life_years or 0
    if life <= 0 or basis <= ZERO:
        return ZERO
    if asset.opening_value is not None and asset.opening_year is not None:
        base, start = asset.opening_value, date(asset.opening_year, 1, 1)
    else:
        base, start = asset.cost - grant_deducted(asset), asset.acquired_on
    annual = base / Decimal(life)
    months = rules.started_months(start, year)
    share = (annual * Decimal(months) / Decimal(12)).quantize(Decimal("1"), rounding=ROUND_FLOOR)
    # The last year takes what is left, so a right that has run out is at zero
    # and not at the øre the rounding left behind.
    ends = start.year + life if start.month > 1 or start.day > 1 else start.year + life - 1
    if year >= ends or share >= basis:
        return basis
    return share.quantize(_CENT)


def _compute_year(
    year: int,
    register: _Register,
    running: dict[str, _Running],
    gain_loss: Decimal,
    year_rules: rules.YearRules,
    choices: dict[str, Decimal] | None,
    use_posted: bool,
) -> tuple[list[SaldoYear], GainLossYear, Decimal]:
    """One year: from the running balances at its start to the ones at its end.

    ``use_posted`` reads depreciation and income from the posted voucher (an
    earlier year, or this one when it is posted); otherwise the year's figures
    are the proposal, the maximum, or ``choices`` where the person chose less.
    """
    buildings = {a.id: a for a in register.assets}
    opening: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    additions: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    grants: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    disposals: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    leaving: set[str] = set()
    gain_loss_opening = gain_loss

    if year == register.start_year:
        for row in register.openings:
            if row.saldogruppe == GTK:
                gain_loss_opening += row.amount
                continue
            key = opening_key(row)
            group = Saldogruppe(row.saldogruppe)
            building = buildings.get(row.building_id) if row.building_id is not None else None
            if key not in running:
                running[key] = _Running(
                    key=key,
                    group=group,
                    label=(
                        f"Saldogruppe j: fast teknisk installasjon i {building.description}"
                        if building is not None
                        else rules.GROUP_LABELS[group]
                    ),
                    # A j saldo has no asset of its own: the building's rate
                    # variant (husdyrbygg) is not the installation's.
                    asset=None,
                )
            opening[key][row.account_number or ""] += row.amount

    for asset in register.assets:
        key = saldo_key(asset)
        group = Saldogruppe(asset.saldogruppe)
        brought = asset.opening_value is not None and asset.opening_year is not None
        # An asset delivered before the register starts is already in the
        # opening saldo of its group, or carries its own opening value; only
        # what is delivered from the start onwards adds to a saldo.
        enters = (brought and asset.opening_year == year) or (
            not brought and asset.acquired_on.year == year and year >= register.start_year
        )
        leaves = asset.disposed_on is not None and asset.disposed_on.year == year
        # Selling something from before the register reduces its group's saldo
        # even when the group had no opening saldo: the sale is what makes the
        # saldo negative, and § 14-46 then wants part of it as income.
        if (enters or (leaves and group in SALE_REDUCES_SALDO)) and key not in running:
            running[key] = _Running(
                key=key,
                group=group,
                label=_label(group, asset, buildings),
                asset=asset if group not in COLLECTIVE | PER_BUILDING else None,
            )
        if enters and brought:
            opening[key][asset.account_number] += asset.opening_value or ZERO
        elif enters:
            additions[key][asset.account_number] += asset.cost
            grants[key][asset.account_number] += grant_deducted(asset)
        if leaves and key in running and not running[key].closed:
            disposals[key][asset.account_number] += proceeds_for_tax(asset)
            if group not in SALE_REDUCES_SALDO:
                leaving.add(key)

    results: list[SaldoYear] = []
    gains = ZERO
    losses = ZERO
    for key in sorted(running, key=_sort_key):
        state = running[key]
        if state.closed:
            continue
        group = state.group
        variant = (
            Satsvariant(state.asset.rate_variant)
            if state.asset is not None and state.asset.rate_variant
            else None
        )
        rate = rules.rate_for(year_rules, group, variant)
        accounts = (
            set(state.by_account)
            | set(opening[key])
            | set(additions[key])
            | set(grants[key])
            | set(disposals[key])
        )
        start_by_account = {a: state.by_account[a] + opening[key][a] for a in accounts}
        basis_by_account = {
            a: start_by_account[a] + additions[key][a] - grants[key][a] - disposals[key][a]
            for a in accounts
        }
        basis = sum(basis_by_account.values(), ZERO)
        opening_total = sum(start_by_account.values(), ZERO)
        add_total = sum(additions[key].values(), ZERO)
        grant_total = sum(grants[key].values(), ZERO)
        disposal_total = sum(disposals[key].values(), ZERO)

        max_dep = ZERO
        min_income = ZERO
        small = False
        settle = key in leaving or (group is Saldogruppe.B and basis < ZERO)
        if not settle and basis > ZERO and group in rules.SALDO_GROUPS and rate is not None:
            small = basis < year_rules.rest_limit
            max_dep = basis if small else rules.max_share(basis, rate)
        elif not settle and basis > ZERO and group is Saldogruppe.LINEAER and state.asset:
            max_dep = _linear_share(state.asset, year, basis)
        elif not settle and basis < ZERO and group in NEGATIVE_INCOME and rate is not None:
            small = -basis < year_rules.rest_limit
            min_income = -basis if small else rules.min_share(-basis, rate)

        posted = register.posted.get((year, key), {})
        if use_posted:
            dep_by_account = {a: kinds.get(DEPRECIATION, ZERO) for a, kinds in posted.items()}
            income_by_account = {a: kinds.get(NEGATIVE_SALDO, ZERO) for a, kinds in posted.items()}
        else:
            chosen = None if choices is None else choices.get(key)
            if max_dep > ZERO:
                dep = max_dep if chosen is None else min(max(chosen, ZERO), max_dep)
                dep_by_account = allocate(dep, basis_by_account)
                income_by_account = {}
            elif min_income > ZERO:
                income = min_income if chosen is None else min(max(chosen, min_income), -basis)
                income_by_account = allocate(income, {a: -v for a, v in basis_by_account.items()})
                dep_by_account = {}
            else:
                dep_by_account, income_by_account = {}, {}

        depreciation = sum(dep_by_account.values(), ZERO)
        income = sum(income_by_account.values(), ZERO)
        settled_by_account: dict[str, Decimal] = {}
        to_gain_loss = ZERO
        if settle:
            # The saldo is closed: what is left on each account is the gain
            # (negative basis) or loss (positive) of the asset that left.
            settled_by_account = {a: -v for a, v in basis_by_account.items() if v != ZERO}
            to_gain_loss = -basis
            if to_gain_loss > ZERO:
                gains += to_gain_loss
            else:
                losses += -to_gain_loss
        shares = tuple(
            AccountShare(
                account_number=a,
                basis=basis_by_account[a],
                depreciation=dep_by_account.get(a, ZERO),
                income=income_by_account.get(a, ZERO),
                settled_gain=settled_by_account.get(a, ZERO),
            )
            for a in sorted(accounts)
        )
        for share in shares:
            state.by_account[share.account_number] = (
                share.basis - share.depreciation + share.income + share.settled_gain
            )
        closing = basis - depreciation + income + to_gain_loss
        if settle:
            state.closed = True
        results.append(
            SaldoYear(
                key=key,
                saldogruppe=group,
                label=state.label,
                asset_id=int(key.split(":")[1]) if ":" in key else None,
                rate_variant=variant,
                rate=rate,
                opening=opening_total,
                additions=add_total,
                grants=grant_total,
                disposals=disposal_total,
                basis=basis,
                max_depreciation=max_dep,
                min_income=min_income,
                depreciation=depreciation,
                income=income,
                to_gain_loss_account=to_gain_loss,
                closing=closing,
                small_rest=small,
                accounts=shares,
            )
        )

    gl_basis = gain_loss_opening + gains - losses
    gl_income = ZERO
    gl_deduction = ZERO
    if gl_basis > ZERO:
        gl_income = (
            gl_basis
            if gl_basis < year_rules.rest_limit
            else rules.min_share(gl_basis, year_rules.gain_loss_rate)
        )
    elif gl_basis < ZERO:
        gl_deduction = (
            -gl_basis
            if -gl_basis < year_rules.rest_limit
            else rules.max_share(-gl_basis, year_rules.gain_loss_rate)
        )
    gl_closing = gl_basis - gl_income + gl_deduction
    gain_loss_year = GainLossYear(
        opening=gain_loss_opening,
        gains=gains,
        losses=losses,
        basis=gl_basis,
        income=gl_income,
        deduction=gl_deduction,
        closing=gl_closing,
    )
    return results, gain_loss_year, gl_closing


def _sort_key(key: str) -> tuple[str, int]:
    group, _, rest = key.partition(":")
    return group, int(rest) if rest.isdigit() else 0


@dataclass(frozen=True)
class _Rolled:
    register: _Register
    saldi: list[SaldoYear]
    gain_loss: GainLossYear
    posted: bool
    unposted_years: tuple[int, ...]
    year_rules: rules.YearRules


async def roll_forward(
    db: AsyncSession,
    company: Company,
    year: int,
    *,
    choices: dict[str, Decimal] | None = None,
    proposal: bool = False,
) -> _Rolled | None:
    """Every year from the register's start up to ``year``, and ``year`` itself.

    ``proposal`` computes ``year`` as Myntos would post it now, even if a
    voucher for it stands (the review of a correction); otherwise a posted
    year reads what was posted. None when the year is before the register, or
    before the first year whose rules Myntos has.
    """
    register = await _load(db, company)
    if register is None or year < register.start_year:
        return None
    running: dict[str, _Running] = {}
    gain_loss = ZERO
    unposted: list[int] = []
    saldi: list[SaldoYear] = []
    gain_loss_year: GainLossYear | None = None
    year_rules: rules.YearRules | None = None
    for current in range(register.start_year, year + 1):
        year_rules = rules.rules_for(current)
        if year_rules is None:
            return None
        this_year = current == year
        # An earlier year reads what was posted, and a year never posted took
        # nothing: its saldo carries forward whole. This year reads the posted
        # voucher too, unless the caller wants the proposal.
        use_posted = not this_year or (current in register.posted_years and not proposal)
        saldi, gain_loss_year, gain_loss = _compute_year(
            current,
            register,
            running,
            gain_loss,
            year_rules,
            choices if this_year else None,
            use_posted=use_posted,
        )
        if not this_year and current not in register.posted_years and _needs_posting(saldi):
            unposted.append(current)
    # The loop runs at least once (``year >= start_year``); this is for the
    # type checker, and returns rather than asserts so it holds under -O.
    if gain_loss_year is None or year_rules is None:
        return None
    return _Rolled(
        register=register,
        saldi=saldi,
        gain_loss=gain_loss_year,
        posted=year in register.posted_years and not proposal,
        unposted_years=tuple(unposted),
        year_rules=year_rules,
    )


def _needs_posting(saldi: Iterable[SaldoYear]) -> bool:
    return any(
        s.max_depreciation > ZERO or s.min_income > ZERO or s.to_gain_loss_account != ZERO
        for s in saldi
    )


# --- The function the næringsspesifikasjon calls --------------------------------------------


async def specification(db: AsyncSession, *, company: Company, year: int) -> SaldoSpecification:
    """The saldo specification for ``year``: per saldo, opening, additions, grants, disposals,
    basis, depreciation, income from a negative saldo, what went to the gevinst- og tapskonto,
    and closing; and the gevinst- og tapskonto itself.

    For a year whose depreciation voucher stands, the figures are what was
    posted. For a year not yet posted they are Myntos's proposal, the maximum
    the law allows, and ``posted`` is False: a skattemelding built from them
    describes depreciation the books do not have yet. Empty when the register
    has nothing, or the year is before the register or before 2024.
    """
    rolled = await roll_forward(db, company, year)
    first = await start_year(db, company)
    if rolled is None:
        year_rules = rules.rules_for(year)
        return SaldoSpecification(
            company_id=company.id,
            year=year,
            start_year=first if first is not None else year,
            rules_from_year=year_rules.from_year if year_rules is not None else None,
            rates_confirmed=year <= rules.CONFIRMED_THROUGH,
            posted=False,
            saldi=(),
            gain_loss_account=GainLossYear(ZERO, ZERO, ZERO, ZERO, ZERO, ZERO, ZERO),
        )
    return SaldoSpecification(
        company_id=company.id,
        year=year,
        start_year=rolled.register.start_year,
        rules_from_year=rolled.year_rules.from_year,
        rates_confirmed=year <= rules.CONFIRMED_THROUGH,
        posted=rolled.posted,
        saldi=tuple(rolled.saldi),
        gain_loss_account=rolled.gain_loss,
        unposted_years=rolled.unposted_years,
    )
