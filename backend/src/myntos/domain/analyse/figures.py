"""Income and costs read off the books, for a period, split the ways a person asks.

docs/analyse.md, «The figures: what counts», is the rule; this is it in SQL.

**Accounting basis, the same lines as ``/regnskap/``.** Posted lines on
accounts of type ``inntekt`` and ``kostnad``, dated by the voucher date, in
every journal except the opening balance (``IB``) and the year-end closing
(``AVS``). Income is credit less debit on an income account, costs debit less
credit on a cost account. A reversal stays in and nets its original to
nothing, so a corrected entry counts once, as its replacement. An invoice
counts on its issue date; its payment moves 1500 against 1920, neither of
which is an income or cost account, so nothing is counted twice.

**Before tax, so 83-89 are out by account class too.** The tax cost (8300)
and the disposition (8800, 8960, 8990) are ``kostnad`` accounts, and Myntos's
own closing posts them in ``AVS``, which is out already. A previous program's
closing does not: brought over by SAF-T it sits in the ``IMPORT`` journal,
and without this rule its 8800 would turn a profitable year into a zero one.
Leaving out every account that :func:`myntos.domain.ledger.report.group_of`
puts in ``skattekostnad`` or ``disponering`` is the same line ``/regnskap/``
draws for «Resultat før skattekostnad», which is why the whole-year invariant
holds to the øre whatever program closed the year.

**Months with no books are not zero.** A month has books when it lies between
the company's first and last voucher outside the opening balance (the
closing included, since a closed year is booked to its end). Before the
first, the company was not in Myntos yet; after the last, nobody has booked
it. A quiet month in between is a real zero. :class:`Books` says which is
which, and the charts send ``null`` for the first kind.

**Scoped by company through the journal**, the way every ledger query here
is: a voucher has no ``company_id`` of its own, its journal has
(docs/company-isolation.md). The accounts are the company's own as well, so
a line could not reach another company's account even if one were wrong.

Computed on every request: a few grouped sums on indexed columns. A cache
would be a second truth to keep in step with every posting.

**KI's tags group lines; they never make a number** (WP5b). :func:`per_hva`
and :func:`per_counterparty` sum the same lines as every other split and only
choose which slice a line goes in by the tag KI stored on it, so the slices
still add up to the side's total to the øre. A line without a tag is «Ikke
lest ennå». With KI off no tag is read at all, even one already stored
(docs/analyse.md rule 10): the split is then every line unread.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.analyse import vocabulary
from myntos.domain.analyse.errors import InvalidAnalysisPeriodError
from myntos.domain.analyse.models import Facet, KiLineTag, KiTag, KiVoucherAnalysis
from myntos.domain.ledger.cashflow import CASH_FLOW_TYPES
from myntos.domain.ledger.chart import CLOSING_JOURNAL_ID, OPENING_JOURNAL_ID
from myntos.domain.ledger.models import (
    Account,
    AccountType,
    Company,
    Journal,
    Party,
    Transaction,
    TransactionLine,
)
from myntos.domain.ledger.report import group_of

ZERO = Decimal("0.00")
_ORE = Decimal("0.01")

#: The groups of the oppstilling that come after «Resultat før skattekostnad».
_AFTER_THE_RESULT = frozenset({"skattekostnad", "disponering"})

#: Five years, the time bokføringsloven keeps the books. A longer period is a
#: chart of more months than anybody can read, and a query over everything.
MAX_MONTHS = 60

NO_PARTY_KEY = "uten_motpart"
NO_PARTY_LABEL = "Uten motpart"
NO_CATEGORY_KEY = "uten_kategori"
NO_CATEGORY_LABEL = "Uten kategori"
#: Lines KI has not given a ``hva`` yet: their own slice, never folded into
#: «Andre» (D5), because «not read» and «many small things» are different news.
UNREAD_KEY = "ikke_lest"
UNREAD_LABEL = "Ikke lest ennå"
#: Prefixed so a counterparty KI read can never share a key with a party's id.
COUNTERPARTY_TAG_PREFIX = "motpart:"

#: A side is split by ``hva`` on the default page from this share read (D5).
READ_ENOUGH = Decimal("0.90")

#: Recurring cost detection (WP6, ``faste_kostnader``): same counterparty,
#: similar monthly amount within +/-20 % of the streak's median, for at least
#: three consecutive calendar months.
RECURRING_MIN_MONTHS = 3
RECURRING_TOLERANCE = Decimal("0.20")

#: ``(year, month)``.
Month = tuple[int, int]


class Side(StrEnum):
    """The two halves of the result, named as the API and the datasets name them."""

    INNTEKTER = "inntekter"
    KOSTNADER = "kostnader"


_ACCOUNT_TYPE: dict[Side, AccountType] = {
    Side.INNTEKTER: AccountType.INNTEKT,
    Side.KOSTNADER: AccountType.KOSTNAD,
}


def month_label(month: Month) -> str:
    """``(2026, 3)`` as the chart spec writes a month: ``2026-03``."""
    return f"{month[0]:04d}-{month[1]:02d}"


def year_earlier(day: date) -> date:
    """The same date a year before; 29 February becomes 28 February."""
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


@dataclass(frozen=True)
class Period:
    """From ``fra`` to ``til``, both days included."""

    fra: date
    til: date

    def months(self) -> list[Month]:
        """Every calendar month the period touches, first to last."""
        months: list[Month] = []
        year, month = self.fra.year, self.fra.month
        while (year, month) <= (self.til.year, self.til.month):
            months.append((year, month))
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return months

    def year_before(self) -> Period:
        """«Samme tid i fjor»: the same dates one year earlier."""
        return Period(year_earlier(self.fra), year_earlier(self.til))

    def is_year_to_date(self, today: date) -> bool:
        return self.fra == date(today.year, 1, 1) and self.til == today

    def is_calendar_year(self) -> bool:
        return self.fra == date(self.fra.year, 1, 1) and self.til == date(self.fra.year, 12, 31)


def period(fra: date, til: date) -> Period:
    """A period to read, or :class:`InvalidAnalysisPeriodError` saying what is wrong."""
    if fra > til:
        raise InvalidAnalysisPeriodError(
            f"perioden starter ({fra.isoformat()}) etter at den slutter ({til.isoformat()})"
        )
    chosen = Period(fra, til)
    if len(chosen.months()) > MAX_MONTHS:
        raise InvalidAnalysisPeriodError(
            f"perioden kan være høyst {MAX_MONTHS} måneder; del den opp i flere"
        )
    return chosen


@dataclass(frozen=True)
class Totals:
    inntekter: Decimal = ZERO
    kostnader: Decimal = ZERO

    @property
    def resultat(self) -> Decimal:
        """Resultat før skatt: 83-89 are not in either side (module docstring)."""
        return self.inntekter - self.kostnader

    def of(self, side: Side) -> Decimal:
        return self.inntekter if side is Side.INNTEKTER else self.kostnader

    def __add__(self, other: Totals) -> Totals:
        return Totals(self.inntekter + other.inntekter, self.kostnader + other.kostnader)


@dataclass(frozen=True)
class Part:
    """One side's amount for one account, group, party or category."""

    key: str
    label: str
    amount: Decimal


@dataclass(frozen=True)
class Books:
    """The dates the company has books for: its first and last voucher outside ``IB``."""

    first: date | None
    last: date | None

    def has(self, month: Month) -> bool:
        if self.first is None or self.last is None:
            return False
        return (self.first.year, self.first.month) <= month <= (self.last.year, self.last.month)

    def cover(self, chosen: Period) -> bool:
        """Every month of ``chosen`` has books, so a comparison with it is a fair one.

        A comparison year the company was only in for part of is left out
        rather than shown, because «+300 % fra samme tid i fjor» against two
        months of books would be true arithmetic and a false statement.
        """
        return all(self.has(month) for month in chosen.months())


async def books(db: AsyncSession, *, company: Company) -> Books:
    """When the company's books start and end, for telling a quiet month from none.

    The opening balance is left out: a company that moved from another
    program at New Year has an ``IB`` voucher dated 1 January whether or not
    January's own vouchers were brought along, and its months before the
    first real voucher are not known to be zero. The closing is kept: a
    closed year is booked to its last day.
    """
    first, last = (
        await db.execute(
            select(func.min(Transaction.transaction_date), func.max(Transaction.transaction_date))
            .join(Journal, Journal.id == Transaction.journal_id)
            .where(
                Journal.company_id == company.id,
                Journal.journal_id != OPENING_JOURNAL_ID,
                Transaction.posted_at.is_not(None),
            )
        )
    ).one()
    return Books(first=first, last=last)


async def _accounts(db: AsyncSession, company: Company) -> dict[int, Account]:
    """The company's income and cost accounts that count toward the result before tax."""
    found = await db.scalars(
        select(Account).where(
            Account.company_id == company.id,
            Account.account_type.in_(tuple(_ACCOUNT_TYPE.values())),
        )
    )
    return {
        account.id: account
        for account in found
        if group_of(account.account_number, account.account_type).key not in _AFTER_THE_RESULT
    }


def _money(value: object) -> Decimal:
    if value is None:
        return ZERO
    return Decimal(str(value)).quantize(_ORE)


def _signed(account_type: AccountType, debit: object, credit: object) -> Decimal:
    """Income as credit less debit, costs as debit less credit: both positive normally."""
    if account_type is AccountType.INNTEKT:
        return _money(credit) - _money(debit)
    return _money(debit) - _money(credit)


async def _net(
    db: AsyncSession,
    company: Company,
    chosen: Period,
    accounts: Iterable[int],
    *keys: Any,
    outer: Sequence[tuple[Any, Any]] = (),
) -> list[tuple[tuple[Any, ...], AccountType, Decimal]]:
    """``(keys, account type, signed amount)`` per value of ``keys``, in the period.

    The one query every figure is a grouping of, so that what counts is
    decided in one place and the splits cannot drift from the totals.
    ``outer`` are ``(target, on)`` outer joins a key may come from (a line's
    tag): outer, so a line without one is still counted, under ``None``.
    """
    ids = list(accounts)
    if not ids:
        return []
    query = (
        select(
            *keys,
            Account.account_type,
            func.sum(TransactionLine.debit),
            func.sum(TransactionLine.credit),
        )
        .select_from(TransactionLine)
        .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
        .join(Journal, Journal.id == Transaction.journal_id)
        .join(Account, Account.id == TransactionLine.account_id)
    )
    for target, on in outer:
        query = query.outerjoin(target, on)
    rows = await db.execute(
        query.where(
            Journal.company_id == company.id,
            Account.company_id == company.id,
            Journal.journal_id.not_in((OPENING_JOURNAL_ID, CLOSING_JOURNAL_ID)),
            Transaction.posted_at.is_not(None),
            Transaction.transaction_date >= chosen.fra,
            Transaction.transaction_date <= chosen.til,
            TransactionLine.account_id.in_(ids),
        ).group_by(*keys, Account.account_type)
    )
    found: list[tuple[tuple[Any, ...], AccountType, Decimal]] = []
    for row in rows:
        *values, account_type, debit, credit = row
        account_type = AccountType(account_type)
        found.append((tuple(values), account_type, _signed(account_type, debit, credit)))
    return found


async def totals(db: AsyncSession, *, company: Company, chosen: Period) -> Totals:
    """Inntekter, kostnader and resultat før skatt for the period."""
    accounts = await _accounts(db, company)
    found = Totals()
    for _, account_type, amount in await _net(db, company, chosen, accounts):
        found = found + _totals(account_type, amount)
    return found


def _totals(account_type: AccountType, amount: Decimal) -> Totals:
    if account_type is AccountType.INNTEKT:
        return Totals(inntekter=amount)
    return Totals(kostnader=amount)


async def per_month(db: AsyncSession, *, company: Company, chosen: Period) -> dict[Month, Totals]:
    """Each month of the period, zero where nothing was posted.

    By the month of the voucher date, not ``Transaction.period``: a file from
    a previous program may number its periods some other way, and the date
    is what a person means by «mars».

    Whether a zero month is a quiet month or one with no books is
    :class:`Books`' to say, not this function's.
    """
    accounts = await _accounts(db, company)
    months: dict[Month, Totals] = {month: Totals() for month in chosen.months()}
    year = extract("year", Transaction.transaction_date)
    month = extract("month", Transaction.transaction_date)
    for (y, m), account_type, amount in await _net(db, company, chosen, accounts, year, month):
        key = (int(y), int(m))
        months[key] = months.get(key, Totals()) + _totals(account_type, amount)
    return months


def _ranked(parts: dict[str, Part]) -> list[Part]:
    """Largest first, the label breaking ties so the order never wobbles between requests."""
    return sorted(parts.values(), key=lambda part: (-part.amount, part.label, part.key))


def _add(parts: dict[str, Part], key: str, label: str, amount: Decimal) -> None:
    held = parts.get(key)
    parts[key] = Part(key, label, amount if held is None else held.amount + amount)


async def per_account(
    db: AsyncSession, *, company: Company, chosen: Period, side: Side
) -> list[Part]:
    """One side by account, largest first. The key is the account number."""
    accounts = await _accounts(db, company)
    wanted = {pk for pk, a in accounts.items() if a.account_type is _ACCOUNT_TYPE[side]}
    parts: dict[str, Part] = {}
    for (account_pk,), _, amount in await _net(
        db, company, chosen, wanted, TransactionLine.account_id
    ):
        account = accounts[account_pk]
        label = f"{account.account_number} {account.description}".strip()
        _add(parts, account.account_number, label, amount)
    return _ranked(parts)


async def per_group(
    db: AsyncSession, *, company: Company, chosen: Period, side: Side
) -> list[Part]:
    """One side by NS 4102 group (``report.group_of``), the groups ``/regnskap/`` shows."""
    accounts = await _accounts(db, company)
    wanted = {pk for pk, a in accounts.items() if a.account_type is _ACCOUNT_TYPE[side]}
    parts: dict[str, Part] = {}
    for (account_pk,), _, amount in await _net(
        db, company, chosen, wanted, TransactionLine.account_id
    ):
        account = accounts[account_pk]
        group = group_of(account.account_number, account.account_type)
        _add(parts, group.key, group.label, amount)
    return _ranked(parts)


async def per_party(
    db: AsyncSession, *, company: Company, chosen: Period, side: Side
) -> list[Part]:
    """One side by the party on the line: customers for income, suppliers for costs.

    The party is the one on the income or cost line itself, which is where
    the register and an invoice put it. A line without one (a bank fee, a
    voucher from a program that kept no customer ledger) is «Uten motpart»,
    so the parts still add up to the side's total. The key is the party's id
    in Myntos (``party_pk`` elsewhere in the API).
    """
    accounts = await _accounts(db, company)
    wanted = {pk for pk, a in accounts.items() if a.account_type is _ACCOUNT_TYPE[side]}
    rows = await _net(db, company, chosen, wanted, TransactionLine.party_id)
    ids = {party_pk for (party_pk,), _, _ in rows if party_pk is not None}
    names: dict[int, str] = {}
    if ids:
        # Read with the company's id as well: the line's party is the
        # company's own by construction, and this keeps it so by query.
        names = {
            party.id: party.name
            for party in await db.scalars(
                select(Party).where(Party.company_id == company.id, Party.id.in_(ids))
            )
        }
    parts: dict[str, Part] = {}
    for (party_pk,), _, amount in rows:
        if party_pk is None or party_pk not in names:
            _add(parts, NO_PARTY_KEY, NO_PARTY_LABEL, amount)
        else:
            _add(parts, str(party_pk), names[party_pk], amount)
    return _ranked(parts)


def category_of(entry_type: str | None, voucher_type: str | None) -> tuple[str, str]:
    """The register category a line was booked under: ``(code, label)``.

    The line's own ``entry_type`` first, which is how a composite voucher's
    lines keep their categories apart. Lines from before that column have
    none, and a single-category register entry carries its code as the
    voucher type, so that is read next. A refund or a kreditnota counts under
    the category it reverses: it is that cost going back, and as a slice of
    its own it would be a negative piece of a pie. Anything else (an
    invoice, a manual voucher, a previous program's voucher) is «Uten
    kategori».
    """
    for code in (entry_type, voucher_type):
        found = CASH_FLOW_TYPES.get(code or "")
        if found is None:
            continue
        if found.reverses is not None and found.reverses in CASH_FLOW_TYPES:
            found = CASH_FLOW_TYPES[found.reverses]
        return found.code, found.label
    return NO_CATEGORY_KEY, NO_CATEGORY_LABEL


async def per_category(
    db: AsyncSession, *, company: Company, chosen: Period, side: Side = Side.KOSTNADER
) -> list[Part]:
    """One side by the register category it was booked under (:func:`category_of`)."""
    accounts = await _accounts(db, company)
    wanted = {pk for pk, a in accounts.items() if a.account_type is _ACCOUNT_TYPE[side]}
    parts: dict[str, Part] = {}
    for (entry_type, voucher_type), _, amount in await _net(
        db, company, chosen, wanted, TransactionLine.entry_type, Transaction.voucher_type
    ):
        key, label = category_of(entry_type, voucher_type)
        _add(parts, key, label, amount)
    return _ranked(parts)


# --- What KI read the lines to be about (WP5b) -------------------------------------------


def _line_tag(company: Company, facet: Facet) -> Any:
    """One tag of ``facet`` per line of the company, as a subquery to outer-join on.

    The lowest tag id when a line somehow has two: a line has exactly one
    ``hva`` by construction (tagging.py), and taking one here keeps a broken
    row from counting a line twice and the slices from drifting from the
    total. Both tables are read with the company's id, so another company's
    tags cannot reach a split even through a wrong ``line_pk``.
    """
    return (
        select(KiLineTag.line_pk.label("line_pk"), func.min(KiLineTag.tag_id).label("tag_id"))
        .join(KiTag, KiTag.id == KiLineTag.tag_id)
        .where(
            KiLineTag.company_id == company.id,
            KiTag.company_id == company.id,
            KiTag.facet == facet.value,
        )
        .group_by(KiLineTag.line_pk)
        .subquery()
    )


async def _tags(db: AsyncSession, company: Company) -> dict[int, vocabulary.Tag]:
    """The company's tags by id, each standing for the tag it was merged into."""
    return vocabulary.resolved(await vocabulary.load(db, company.id))


@dataclass(frozen=True)
class Reading:
    """One side split by what KI read each line to be about, and what it has not read."""

    #: One part per ``hva``, keyed by its slug, largest first.
    parts: list[Part]
    #: The amount on lines without a ``hva``: not read yet, or read and failed.
    unread: Decimal

    @property
    def total(self) -> Decimal:
        """The side's total: the same figure :func:`totals` gives, to the øre."""
        return sum((part.amount for part in self.parts), self.unread)

    @property
    def unread_part(self) -> Part:
        return Part(UNREAD_KEY, UNREAD_LABEL, self.unread)

    @property
    def dekning(self) -> Decimal | None:
        """The share of the side's amount on lines with a ``hva``, rounded down to 0.01.

        Down, so that 0.899 reads 0.89 and is not shown as read enough (D5).
        Kept between 0 and 1: a refund KI has not read yet can make the
        unread part negative and the read part more than the whole. None
        when the side comes to zero or less, since a share of nothing is no
        share; the page then splits by account, which needs no share.
        """
        whole = self.total
        if whole <= 0:
            return None
        share = min(Decimal(1), max(Decimal(0), (whole - self.unread) / whole))
        return share.quantize(_ORE, rounding=ROUND_DOWN)


async def per_hva(db: AsyncSession, *, company: Company, chosen: Period, side: Side) -> Reading:
    """One side by the ``hva`` KI gave each line (``vocabulary``), and the rest unread.

    The key is the tag's slug, the label the company's own label for it,
    capped (rule 8). With KI off every line is unread, whatever is stored
    (rule 10): the tags are not even joined.
    """
    accounts = await _accounts(db, company)
    wanted = {pk for pk, a in accounts.items() if a.account_type is _ACCOUNT_TYPE[side]}
    if not company.ki_enabled:
        unread = sum((amount for _, _, amount in await _net(db, company, chosen, wanted)), ZERO)
        return Reading(parts=[], unread=unread)
    tagged = _line_tag(company, Facet.HVA)
    rows = await _net(
        db,
        company,
        chosen,
        wanted,
        tagged.c.tag_id,
        outer=((tagged, tagged.c.line_pk == TransactionLine.id),),
    )
    tags = await _tags(db, company) if any(tag_id is not None for (tag_id,), _, _ in rows) else {}
    parts: dict[str, Part] = {}
    unread = ZERO
    for (tag_id,), _, amount in rows:
        tag = tags.get(tag_id) if tag_id is not None else None
        if tag is None:
            unread += amount
            continue
        _add(parts, tag.slug, vocabulary.clean_label(tag.label) or tag.slug, amount)
    return Reading(parts=_ranked(parts), unread=unread)


async def per_counterparty(
    db: AsyncSession, *, company: Company, chosen: Period, side: Side = Side.KOSTNADER
) -> list[Part]:
    """One side by who the money went to or came from: the line's party, else KI's ``motpart``.

    The books' own party first, as in :func:`per_party`; where the line has
    none, the counterparty KI read off the voucher (a ``motpart`` tag, which
    can be a private person's name, so this is only shown to people who may
    read the books). A tag with the same name as a party found here is put
    with that party rather than drawn twice. Neither is «Uten motpart». With
    KI off it is :func:`per_party`, tags unread (rule 10).
    """
    if not company.ki_enabled:
        return await per_party(db, company=company, chosen=chosen, side=side)
    accounts = await _accounts(db, company)
    wanted = {pk for pk, a in accounts.items() if a.account_type is _ACCOUNT_TYPE[side]}
    tagged = _line_tag(company, Facet.MOTPART)
    rows = await _net(
        db,
        company,
        chosen,
        wanted,
        TransactionLine.party_id,
        tagged.c.tag_id,
        outer=((tagged, tagged.c.line_pk == TransactionLine.id),),
    )
    ids = {party_pk for (party_pk, _), _, _ in rows if party_pk is not None}
    names: dict[int, str] = {}
    if ids:
        names = {
            party.id: party.name
            for party in await db.scalars(
                select(Party).where(Party.company_id == company.id, Party.id.in_(ids))
            )
        }
    by_name = {name.casefold(): pk for pk, name in names.items()}
    tags = await _tags(db, company) if any(t is not None for (_, t), _, _ in rows) else {}
    parts: dict[str, Part] = {}
    for (party_pk, tag_id), _, amount in rows:
        if party_pk is not None and party_pk in names:
            _add(parts, str(party_pk), names[party_pk], amount)
            continue
        tag = tags.get(tag_id) if tag_id is not None else None
        if tag is None:
            _add(parts, NO_PARTY_KEY, NO_PARTY_LABEL, amount)
            continue
        label = vocabulary.clean_label(tag.label) or tag.slug
        same = by_name.get(label.casefold())
        if same is not None:
            _add(parts, str(same), names[same], amount)
        else:
            _add(parts, f"{COUNTERPARTY_TAG_PREFIX}{tag.slug}", label, amount)
    return _ranked(parts)


# --- WP6 queries: monthly hva, recurring costs, voucher counts, largest vouchers --------


@dataclass(frozen=True)
class MonthlyHva:
    """Costs (or income) per ``hva`` and per calendar month, plus unread lines per month."""

    #: Ranked by total across the period, largest first.
    parts: list[Part]
    #: ``slug -> {month: amount}`` for every tagged part.
    by_month: dict[str, dict[Month, Decimal]]
    #: Unread amount per month.
    unread_by_month: dict[Month, Decimal]

    @property
    def unread_total(self) -> Decimal:
        return sum(self.unread_by_month.values(), ZERO)


async def per_hva_per_month(
    db: AsyncSession, *, company: Company, chosen: Period, side: Side = Side.KOSTNADER
) -> MonthlyHva:
    """One side by ``hva`` and calendar month, for stacked monthly charts (WP6).

    With KI off no tag is read (rule 10), so every booked line lands in
    ``unread_by_month``.
    """
    accounts = await _accounts(db, company)
    wanted = {pk for pk, a in accounts.items() if a.account_type is _ACCOUNT_TYPE[side]}
    year = extract("year", Transaction.transaction_date)
    month = extract("month", Transaction.transaction_date)
    unread_by_month: dict[Month, Decimal] = {}
    if not company.ki_enabled:
        for (y, m), _, amount in await _net(db, company, chosen, wanted, year, month):
            key = (int(y), int(m))
            unread_by_month[key] = unread_by_month.get(key, ZERO) + amount
        return MonthlyHva(parts=[], by_month={}, unread_by_month=unread_by_month)
    tagged = _line_tag(company, Facet.HVA)
    rows = await _net(
        db,
        company,
        chosen,
        wanted,
        year,
        month,
        tagged.c.tag_id,
        outer=((tagged, tagged.c.line_pk == TransactionLine.id),),
    )
    tags = (
        await _tags(db, company) if any(tag_id is not None for (_, _, tag_id), _, _ in rows) else {}
    )
    parts: dict[str, Part] = {}
    by_month: dict[str, dict[Month, Decimal]] = {}
    for (y, m, tag_id), _, amount in rows:
        month_key = (int(y), int(m))
        tag = tags.get(tag_id) if tag_id is not None else None
        if tag is None:
            unread_by_month[month_key] = unread_by_month.get(month_key, ZERO) + amount
            continue
        label = vocabulary.clean_label(tag.label) or tag.slug
        _add(parts, tag.slug, label, amount)
        series = by_month.setdefault(tag.slug, {})
        series[month_key] = series.get(month_key, ZERO) + amount
    return MonthlyHva(parts=_ranked(parts), by_month=by_month, unread_by_month=unread_by_month)


async def per_hva_counts(
    db: AsyncSession, *, company: Company, chosen: Period, side: Side = Side.KOSTNADER
) -> dict[str, int]:
    """Distinct posted voucher count per resolved ``hva`` slug in the period."""
    if not company.ki_enabled:
        return {}
    accounts = await _accounts(db, company)
    wanted = {pk for pk, a in accounts.items() if a.account_type is _ACCOUNT_TYPE[side]}
    if not wanted:
        return {}
    tagged = _line_tag(company, Facet.HVA)
    rows = await db.execute(
        select(tagged.c.tag_id, Transaction.id)
        .select_from(TransactionLine)
        .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
        .join(Journal, Journal.id == Transaction.journal_id)
        .join(tagged, tagged.c.line_pk == TransactionLine.id)
        .where(
            Journal.company_id == company.id,
            Journal.journal_id.not_in((OPENING_JOURNAL_ID, CLOSING_JOURNAL_ID)),
            Transaction.posted_at.is_not(None),
            Transaction.transaction_date >= chosen.fra,
            Transaction.transaction_date <= chosen.til,
            TransactionLine.account_id.in_(wanted),
        )
        .distinct()
    )
    pairs = list(rows.all())
    if not pairs:
        return {}
    tags = await _tags(db, company)
    vouchers_by_slug: dict[str, set[int]] = {}
    for tag_id, tx_id in pairs:
        tag = tags.get(tag_id)
        if tag is not None:
            vouchers_by_slug.setdefault(tag.slug, set()).add(tx_id)
    return {slug: len(txs) for slug, txs in vouchers_by_slug.items()}


@dataclass(frozen=True)
class RecurringCost:
    """A counterparty billed roughly the same amount for at least three consecutive months."""

    key: str
    label: str
    months: int
    average: Decimal
    total: Decimal
    first_month: Month
    last_month: Month
    #: Whole-percent change from the first half of the streak to the second, or None if flat/short.
    trend_percent: int | None


def _next_month(m: Month) -> Month:
    return (m[0] + 1, 1) if m[1] == 12 else (m[0], m[1] + 1)


def _median(values: Sequence[Decimal]) -> Decimal:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal(2)


def _best_recurring_streak(
    monthly: dict[Month, Decimal],
) -> tuple[list[Month], list[Decimal]] | None:
    """Longest run of >= RECURRING_MIN_MONTHS positive months within RECURRING_TOLERANCE."""
    positive = sorted(m for m, val in monthly.items() if val > 0)
    if len(positive) < RECURRING_MIN_MONTHS:
        return None
    # Split into runs of consecutive calendar months first.
    runs: list[list[Month]] = []
    for m in positive:
        if not runs or _next_month(runs[-1][-1]) != m:
            runs.append([m])
        else:
            runs[-1].append(m)
    best: tuple[list[Month], list[Decimal]] | None = None
    for run in runs:
        if len(run) < RECURRING_MIN_MONTHS:
            continue
        # Find the longest contiguous slice inside the consecutive run where every
        # month lies within +/- RECURRING_TOLERANCE of the slice's median.
        n = len(run)
        for start in range(n):
            if best is not None and n - start <= len(best[0]):
                break
            for end in range(n, start + RECURRING_MIN_MONTHS - 1, -1):
                if best is not None and end - start <= len(best[0]):
                    break
                sub_months = run[start:end]
                sub_vals = [monthly[m] for m in sub_months]
                med = _median(sub_vals)
                if med <= 0:
                    continue
                if all(abs(v - med) <= med * RECURRING_TOLERANCE for v in sub_vals):
                    best = (sub_months, sub_vals)
                    break
    return best


async def recurring_costs(
    db: AsyncSession, *, company: Company, chosen: Period
) -> list[RecurringCost]:
    """Same counterparty, similar amount, three or more consecutive months (rule 3, WP6).

    Uses the line's party, and when KI is on also KI's ``motpart`` tag (same
    merging rule as :func:`per_counterparty`). Works with KI off over parties
    alone, so ``faste_kostnader`` has ``needs_tags = False``.
    """
    accounts = await _accounts(db, company)
    wanted = {pk for pk, a in accounts.items() if a.account_type is AccountType.KOSTNAD}
    if not wanted:
        return []
    year = extract("year", Transaction.transaction_date)
    month = extract("month", Transaction.transaction_date)
    if company.ki_enabled:
        tagged = _line_tag(company, Facet.MOTPART)
        raw_rows = await _net(
            db,
            company,
            chosen,
            wanted,
            year,
            month,
            TransactionLine.party_id,
            tagged.c.tag_id,
            outer=((tagged, tagged.c.line_pk == TransactionLine.id),),
        )
        rows = [
            (int(y), int(m), party_pk, tag_id, amt) for (y, m, party_pk, tag_id), _, amt in raw_rows
        ]
    else:
        raw_rows = await _net(db, company, chosen, wanted, year, month, TransactionLine.party_id)
        rows = [(int(y), int(m), party_pk, None, amt) for (y, m, party_pk), _, amt in raw_rows]

    party_ids = {party_pk for _, _, party_pk, _, _ in rows if party_pk is not None}
    names: dict[int, str] = {}
    if party_ids:
        names = {
            party.id: party.name
            for party in await db.scalars(
                select(Party).where(Party.company_id == company.id, Party.id.in_(party_ids))
            )
        }
    by_name = {name.casefold(): pk for pk, name in names.items()}
    tags = (
        await _tags(db, company)
        if company.ki_enabled and any(t is not None for _, _, _, t, _ in rows)
        else {}
    )

    labels: dict[str, str] = {}
    monthly_by_key: dict[str, dict[Month, Decimal]] = {}
    totals_by_key: dict[str, Decimal] = {}
    for y, m, party_pk, tag_id, amount in rows:
        key: str | None = None
        label: str | None = None
        if party_pk is not None and party_pk in names:
            key, label = str(party_pk), names[party_pk]
        elif tag_id is not None:
            tag = tags.get(tag_id)
            if tag is not None:
                clean = vocabulary.clean_label(tag.label) or tag.slug
                same = by_name.get(clean.casefold())
                if same is not None:
                    key, label = str(same), names[same]
                else:
                    key, label = f"{COUNTERPARTY_TAG_PREFIX}{tag.slug}", clean
        if key is None or label is None:
            continue
        labels[key] = label
        mkey = (y, m)
        per_m = monthly_by_key.setdefault(key, {})
        per_m[mkey] = per_m.get(mkey, ZERO) + amount
        totals_by_key[key] = totals_by_key.get(key, ZERO) + amount

    found: list[RecurringCost] = []
    for key, monthly in monthly_by_key.items():
        streak = _best_recurring_streak(monthly)
        if streak is None:
            continue
        months_list, values = streak
        avg = (sum(values, ZERO) / Decimal(len(values))).quantize(_ORE, rounding=ROUND_HALF_UP)
        half = len(values) // 2
        trend: int | None = None
        if half >= 1:
            first_avg = sum(values[:half], ZERO) / Decimal(half)
            second_avg = sum(values[-half:], ZERO) / Decimal(half)
            if first_avg > 0:
                pct = int(
                    (((second_avg - first_avg) / first_avg) * 100).quantize(
                        Decimal(1), rounding=ROUND_HALF_UP
                    )
                )
                trend = pct
        found.append(
            RecurringCost(
                key=key,
                label=labels[key],
                months=len(months_list),
                average=avg,
                total=totals_by_key[key],
                first_month=months_list[0],
                last_month=months_list[-1],
                trend_percent=trend,
            )
        )
    return sorted(found, key=lambda item: (-item.total, item.label, item.key))


@dataclass(frozen=True)
class LargestVoucher:
    """One of the period's largest vouchers, for the deep analysis packet (rule 8)."""

    bilag: str
    dato: date
    retning: Side
    belop: Decimal
    konto: str
    motpart: str | None
    oppsummering: str


async def largest_vouchers(
    db: AsyncSession, *, company: Company, chosen: Period, limit: int = 15
) -> list[LargestVoucher]:
    """The ``limit`` largest income or cost vouchers in ``chosen``, newest-tie-broken."""
    accounts = await _accounts(db, company)
    if not accounts:
        return []
    rows = await db.execute(
        select(
            Transaction.id,
            Transaction.transaction_id,
            Transaction.transaction_date,
            Transaction.description,
            TransactionLine.account_id,
            TransactionLine.party_id,
            TransactionLine.debit,
            TransactionLine.credit,
            KiVoucherAnalysis.summary,
        )
        .select_from(TransactionLine)
        .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
        .join(Journal, Journal.id == Transaction.journal_id)
        .outerjoin(
            KiVoucherAnalysis,
            (KiVoucherAnalysis.transaction_pk == Transaction.id)
            & (KiVoucherAnalysis.company_id == company.id),
        )
        .where(
            Journal.company_id == company.id,
            Journal.journal_id.not_in((OPENING_JOURNAL_ID, CLOSING_JOURNAL_ID)),
            Transaction.posted_at.is_not(None),
            Transaction.transaction_date >= chosen.fra,
            Transaction.transaction_date <= chosen.til,
            TransactionLine.account_id.in_(accounts.keys()),
        )
    )
    by_tx: dict[int, list[Any]] = {}
    party_ids: set[int] = set()
    for row in rows:
        by_tx.setdefault(row[0], []).append(row)
        if row[5] is not None:
            party_ids.add(row[5])
    party_names: dict[int, str] = {}
    if party_ids:
        party_names = {
            party.id: party.name
            for party in await db.scalars(
                select(Party).where(Party.company_id == company.id, Party.id.in_(party_ids))
            )
        }

    candidates: list[LargestVoucher] = []
    for tx_rows in by_tx.values():
        income_sum = ZERO
        cost_sum = ZERO
        dominant_account = ""
        dominant_abs = Decimal(-1)
        motpart: str | None = None
        for _, _, _, _, account_id, party_id, debit, credit, _ in tx_rows:
            acct = accounts[account_id]
            signed = _signed(acct.account_type, debit, credit)
            if acct.account_type is AccountType.INNTEKT:
                income_sum += signed
            else:
                cost_sum += signed
            if abs(signed) > dominant_abs:
                dominant_abs = abs(signed)
                dominant_account = f"{acct.account_number} {acct.description}".strip()
            if motpart is None and party_id is not None and party_id in party_names:
                motpart = party_names[party_id]
        if abs(cost_sum) >= abs(income_sum):
            side, total = Side.KOSTNADER, cost_sum
        else:
            side, total = Side.INNTEKTER, income_sum
        if total == 0:
            continue
        _, bilag, dato, desc, _, _, _, _, summary = tx_rows[0]
        text = (summary if company.ki_enabled and summary else None) or desc or dominant_account
        candidates.append(
            LargestVoucher(
                bilag=str(bilag),
                dato=dato,
                retning=side,
                belop=total,
                konto=dominant_account,
                motpart=motpart,
                oppsummering=str(text)[:200],
            )
        )
    candidates.sort(key=lambda item: (-abs(item.belop), item.dato, item.bilag))
    return candidates[:limit]
