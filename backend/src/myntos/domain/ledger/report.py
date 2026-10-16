"""Resultat og balanse: every financial year in the books, side by side.

The overview answers for one year, the current one, and a company that has
just brought last year over from its previous program reads «Inn i år 0,00»
there with nowhere to see the year it brought. This is that somewhere: the
profit and loss account (resultatregnskap) and the balance sheet (balanse)
for each year, laid out the way an årsregnskap is, so the years can be read
against each other.

**Read off the ledger, nothing stored.** A year's result is its posted lines on
the income and cost accounts; its balance is every posted line up to and
including the year on the balance accounts. Nothing in Myntos writes
:class:`~myntos.domain.ledger.models.AccountOpeningBalance` (myntos.saft.service
says why), so the cumulative ledger is the balance, the ``IB`` voucher
included. Reversals need no special case: a reversed pair nets to nothing on
every account.

**Grouped by account class, not by the næringsspesifikasjon.** The groups are
NS 4102's classes (the standard kontoplan's first two digits), which is what
regnskapsloven's oppstilling follows for a small company and what a person
who has seen an årsregnskap recognises. The næringsspesifikasjon's posts are a
per-year mapping a person may have changed (AccountGrouping), and an overview
that moved when somebody remapped an account would be a worse overview.
An account whose number does not start with a class digit (a previous
program's own numbering) falls back on its account type, so every account
lands somewhere and the balance sheet still balances.

**An open year's result sits in equity as «Udisponert resultat».** Until a
year is closed its result is on the income and cost accounts, not on 2050, so
the balance sheet would be out by exactly the result. Showing it as its own
line under equity is what the SAF-T export does too, and it says the true
thing: the result exists, nobody has disposed of it yet. Once the year is
closed the disposition voucher moves it, and the line goes to zero.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.ledger.models import (
    Account,
    AccountType,
    Company,
    FinancialYear,
    Journal,
    Transaction,
    TransactionLine,
)

ZERO = Decimal("0.00")


class Side(StrEnum):
    """Which way a group's amount is read, and which statement it is on."""

    INCOME = "income"  # credit less debit, added to the result
    COST = "cost"  # debit less credit, taken from the result
    DISPOSITION = "disposition"  # 88-89: the result disposed of, not part of it
    ASSET = "asset"  # debit less credit
    CLAIM = "claim"  # equity and liabilities: credit less debit


@dataclass(frozen=True)
class _Group:
    key: str
    label: str
    side: Side


# Resultatregnskap, in the order of the oppstilling.
_SALES = _Group("salgsinntekter", "Salgsinntekter", Side.INCOME)
_OTHER_INCOME = _Group("andre_driftsinntekter", "Andre driftsinntekter", Side.INCOME)
_GOODS = _Group("varekostnad", "Varekostnad", Side.COST)
_PAYROLL = _Group("lonnskostnad", "Lønnskostnad", Side.COST)
_DEPRECIATION = _Group("avskrivninger", "Avskrivninger", Side.COST)
_OTHER_COSTS = _Group("andre_driftskostnader", "Andre driftskostnader", Side.COST)
_FIN_INCOME = _Group("finansinntekter", "Finansinntekter", Side.INCOME)
_FIN_COSTS = _Group("finanskostnader", "Finanskostnader", Side.COST)
_TAX = _Group("skattekostnad", "Skattekostnad", Side.COST)
_DISPOSITION = _Group("disponering", "Disponering", Side.DISPOSITION)

# Balanse.
_INTANGIBLE = _Group("immaterielle_eiendeler", "Immaterielle eiendeler", Side.ASSET)
_TANGIBLE = _Group("varige_driftsmidler", "Varige driftsmidler", Side.ASSET)
_FINANCIAL_FIXED = _Group("finansielle_anleggsmidler", "Finansielle anleggsmidler", Side.ASSET)
_STOCK = _Group("varer", "Varer", Side.ASSET)
_RECEIVABLES = _Group("fordringer", "Fordringer", Side.ASSET)
_INVESTMENTS = _Group("investeringer", "Investeringer", Side.ASSET)
_BANK = _Group("bankinnskudd", "Bankinnskudd og kontanter", Side.ASSET)
_OTHER_ASSETS = _Group("andre_eiendeler", "Andre eiendeler", Side.ASSET)
_EQUITY = _Group("egenkapital", "Egenkapital", Side.CLAIM)
_PROVISIONS = _Group("avsetninger", "Avsetning for forpliktelser", Side.CLAIM)
_LONG_TERM = _Group("annen_langsiktig_gjeld", "Annen langsiktig gjeld", Side.CLAIM)
_SHORT_TERM = _Group("kortsiktig_gjeld", "Kortsiktig gjeld", Side.CLAIM)

#: NS 4102's classes, by the account number's first two digits.
_BY_CLASS: dict[str, _Group] = {
    **{f"{n}": _SALES for n in (30, 31, 32)},
    **{f"{n}": _OTHER_INCOME for n in range(33, 40)},
    **{f"{n}": _GOODS for n in range(40, 50)},
    **{f"{n}": _PAYROLL for n in range(50, 60)},
    "60": _DEPRECIATION,
    **{f"{n}": _OTHER_COSTS for n in range(61, 80)},
    "80": _FIN_INCOME,
    "81": _FIN_COSTS,
    # 82 has no fixed meaning left in the standard; read by the account's type
    # below rather than guessed at here.
    **{f"{n}": _TAX for n in range(83, 88)},
    "88": _DISPOSITION,
    "89": _DISPOSITION,
    "10": _INTANGIBLE,
    "11": _TANGIBLE,
    "12": _TANGIBLE,
    "13": _FINANCIAL_FIXED,
    "14": _STOCK,
    "15": _RECEIVABLES,
    "16": _RECEIVABLES,
    "17": _RECEIVABLES,
    "18": _INVESTMENTS,
    "19": _BANK,
    "20": _EQUITY,
    "21": _PROVISIONS,
    "22": _LONG_TERM,
    **{f"{n}": _SHORT_TERM for n in range(23, 30)},
}

#: For a number that names no class: what the account says it is.
_BY_TYPE: dict[AccountType, _Group] = {
    AccountType.INNTEKT: _OTHER_INCOME,
    AccountType.KOSTNAD: _OTHER_COSTS,
    AccountType.EIENDEL: _OTHER_ASSETS,
    AccountType.GJELD: _SHORT_TERM,
    AccountType.EGENKAPITAL: _EQUITY,
}


def group_of(account_number: str, account_type: AccountType) -> _Group:
    """The line of the oppstilling an account's amount belongs on."""
    group = _BY_CLASS.get(account_number[:2]) if account_number[:2].isdigit() else None
    if group is not None:
        return group
    if account_number.startswith("82"):
        return _FIN_INCOME if account_type is AccountType.INNTEKT else _FIN_COSTS
    return _BY_TYPE[account_type]


def _is_result(group: _Group) -> bool:
    return group.side in (Side.INCOME, Side.COST, Side.DISPOSITION)


class LineKind(StrEnum):
    GROUP = "gruppe"  # accounts summed, with the accounts behind it
    SUM = "sum"  # a subtotal of the lines above


@dataclass(frozen=True)
class AccountFigure:
    account_number: str
    description: str
    amount: Decimal


@dataclass(frozen=True)
class Line:
    key: str
    label: str
    kind: LineKind
    amount: Decimal
    accounts: tuple[AccountFigure, ...] = ()


@dataclass(frozen=True)
class YearFigures:
    year: int
    start_date: date
    end_date: date
    closed: bool
    voucher_count: int
    last_entry: date | None
    result_lines: list[Line]
    balance_lines: list[Line]


#: The oppstilling, top to bottom. A tuple is a subtotal: its key, its label
#: and the keys it adds up, each with its sign.
_Row = _Group | tuple[str, str, tuple[tuple[str, int], ...]]

_RESULT_LAYOUT: tuple[_Row, ...] = (
    _SALES,
    _OTHER_INCOME,
    (
        "driftsinntekter",
        "Sum driftsinntekter",
        (("salgsinntekter", 1), ("andre_driftsinntekter", 1)),
    ),
    _GOODS,
    _PAYROLL,
    _DEPRECIATION,
    _OTHER_COSTS,
    (
        "driftskostnader",
        "Sum driftskostnader",
        (
            ("varekostnad", 1),
            ("lonnskostnad", 1),
            ("avskrivninger", 1),
            ("andre_driftskostnader", 1),
        ),
    ),
    ("driftsresultat", "Driftsresultat", (("driftsinntekter", 1), ("driftskostnader", -1))),
    _FIN_INCOME,
    _FIN_COSTS,
    ("netto_finans", "Netto finansposter", (("finansinntekter", 1), ("finanskostnader", -1))),
    (
        "resultat_for_skatt",
        "Resultat før skattekostnad",
        (("driftsresultat", 1), ("netto_finans", 1)),
    ),
    _TAX,
    ("arsresultat", "Årsresultat", (("resultat_for_skatt", 1), ("skattekostnad", -1))),
)

_UNDISPOSED = _Group("udisponert_resultat", "Udisponert resultat", Side.CLAIM)

_BALANCE_LAYOUT: tuple[_Row, ...] = (
    _INTANGIBLE,
    _TANGIBLE,
    _FINANCIAL_FIXED,
    (
        "anleggsmidler",
        "Sum anleggsmidler",
        (
            ("immaterielle_eiendeler", 1),
            ("varige_driftsmidler", 1),
            ("finansielle_anleggsmidler", 1),
        ),
    ),
    _STOCK,
    _RECEIVABLES,
    _INVESTMENTS,
    _BANK,
    _OTHER_ASSETS,
    (
        "omlopsmidler",
        "Sum omløpsmidler",
        (
            ("varer", 1),
            ("fordringer", 1),
            ("investeringer", 1),
            ("bankinnskudd", 1),
            ("andre_eiendeler", 1),
        ),
    ),
    ("eiendeler", "Sum eiendeler", (("anleggsmidler", 1), ("omlopsmidler", 1))),
    _EQUITY,
    _UNDISPOSED,
    ("sum_egenkapital", "Sum egenkapital", (("egenkapital", 1), ("udisponert_resultat", 1))),
    _PROVISIONS,
    _LONG_TERM,
    (
        "langsiktig_gjeld",
        "Sum langsiktig gjeld",
        (("avsetninger", 1), ("annen_langsiktig_gjeld", 1)),
    ),
    _SHORT_TERM,
    ("gjeld", "Sum gjeld", (("langsiktig_gjeld", 1), ("kortsiktig_gjeld", 1))),
    (
        "egenkapital_og_gjeld",
        "Sum egenkapital og gjeld",
        (("sum_egenkapital", 1), ("gjeld", 1)),
    ),
)


def _signed(group: _Group, net: Decimal) -> Decimal:
    """Debit less credit, turned the way the line is read."""
    return net if group.side in (Side.COST, Side.ASSET) else -net


def _lay_out(
    layout: tuple[_Row, ...],
    groups: dict[str, list[AccountFigure]],
    shown: set[str],
    derived: dict[str, Decimal] | None = None,
) -> list[Line]:
    """The lines of one statement for one year, subtotals worked out in order.

    ``derived`` is a group line with an amount and no account behind it.
    """
    amounts: dict[str, Decimal] = {}
    lines: list[Line] = []
    for row in layout:
        if isinstance(row, _Group):
            accounts = sorted(groups.get(row.key, []), key=lambda a: a.account_number)
            amount = sum((a.amount for a in accounts), ZERO)
            if derived is not None and row.key in derived:
                amount = derived[row.key]
            amounts[row.key] = amount
            if row.key in shown:
                lines.append(Line(row.key, row.label, LineKind.GROUP, amount, tuple(accounts)))
        else:
            key, label, parts = row
            amount = sum((amounts.get(part, ZERO) * sign for part, sign in parts), ZERO)
            amounts[key] = amount
            lines.append(Line(key, label, LineKind.SUM, amount))
    return lines


async def every_year(db: AsyncSession, *, company: Company) -> list[YearFigures]:
    """Resultat og balanse for each of the company's financial years, newest first.

    A group line is listed in every year when any year has an amount on it, and
    in none when no year has, so the years line up row for row and a small
    company is not shown a dozen lines of zeros.
    """
    years = {
        fy.year: fy
        for fy in await db.scalars(
            select(FinancialYear).where(FinancialYear.company_id == company.id)
        )
    }
    accounts = {
        account.id: account
        for account in await db.scalars(select(Account).where(Account.company_id == company.id))
    }
    movements: dict[int, dict[int, Decimal]] = defaultdict(dict)
    for year, account_id, debit, credit in await db.execute(
        select(
            Transaction.financial_year,
            TransactionLine.account_id,
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
        .join(Journal, Journal.id == Transaction.journal_id)
        .where(Journal.company_id == company.id, Transaction.posted_at.is_not(None))
        .group_by(Transaction.financial_year, TransactionLine.account_id)
    ):
        movements[int(year)][account_id] = Decimal(str(debit)) - Decimal(str(credit))
    counts: dict[int, tuple[int, date | None]] = {
        int(year): (int(count), last)
        for year, count, last in await db.execute(
            select(
                Transaction.financial_year,
                func.count(Transaction.id),
                func.max(Transaction.transaction_date),
            )
            .join(Journal, Journal.id == Transaction.journal_id)
            .where(Journal.company_id == company.id, Transaction.posted_at.is_not(None))
            .group_by(Transaction.financial_year)
        )
    }

    all_years = sorted(set(years) | set(movements))
    cumulative: dict[int, Decimal] = defaultdict(lambda: ZERO)
    per_year: dict[int, tuple[dict[str, list[AccountFigure]], dict[str, list[AccountFigure]]]] = {}
    undisposed_by_year: dict[int, Decimal] = {}
    shown_balance: set[str] = set()
    for year in all_years:
        result_groups: dict[str, list[AccountFigure]] = defaultdict(list)
        for account_id, net in movements.get(year, {}).items():
            cumulative[account_id] += net
            account = accounts[account_id]
            group = group_of(account.account_number, account.account_type)
            if _is_result(group) and net != ZERO:
                result_groups[group.key].append(
                    AccountFigure(account.account_number, account.description, _signed(group, net))
                )
        balance_groups: dict[str, list[AccountFigure]] = defaultdict(list)
        undisposed = ZERO
        for account_id, net in cumulative.items():
            account = accounts[account_id]
            group = group_of(account.account_number, account.account_type)
            if _is_result(group):
                # Everything the income, cost and disposition accounts hold up
                # to this year's end is result nobody has moved to equity yet.
                undisposed -= net
            elif net != ZERO:
                balance_groups[group.key].append(
                    AccountFigure(account.account_number, account.description, _signed(group, net))
                )
        undisposed_by_year[year] = undisposed
        if undisposed != ZERO:
            shown_balance.add(_UNDISPOSED.key)
        per_year[year] = (result_groups, balance_groups)

    shown_result = {key for groups, _ in per_year.values() for key in groups}
    shown_balance |= {key for _, groups in per_year.values() for key in groups}
    figures: list[YearFigures] = []
    for year in reversed(all_years):
        result_groups, balance_groups = per_year[year]
        fy = years.get(year)
        count, last = counts.get(year, (0, None))
        figures.append(
            YearFigures(
                year=year,
                start_date=fy.start_date if fy is not None else date(year, 1, 1),
                end_date=fy.end_date if fy is not None else date(year, 12, 31),
                closed=fy is not None and fy.closed_at is not None,
                voucher_count=count,
                last_entry=last,
                result_lines=_lay_out(_RESULT_LAYOUT, result_groups, shown_result),
                balance_lines=_lay_out(
                    _BALANCE_LAYOUT,
                    balance_groups,
                    shown_balance,
                    {_UNDISPOSED.key: undisposed_by_year[year]},
                ),
            )
        )
    return figures
