"""Myntos against the old program's latest SAF-T file: the parallel run's check.

A company trying Myntos beside the program it keeps its books in books the same
bilag in both for a while, and the question it is asking is whether the two
agree. This answers it from the old program's own SAF-T export, account by
account, customer and supplier by customer and supplier, and VAT term by VAT
term, and says in which months an account parts.

**Movements, not balances.** Each side's movement over the year so far is
compared, the opening balance left out of both. The opening balance in Myntos
is deliberately not the file's to the letter (last year's VAT on 2740, last
year's result on 2050, perhaps the driftskreditt on 1920), and comparing
closing balances would report those decisions as differences forever. The
movements are what the parallel run is testing.

**The same window on both sides.** From 1 January to the day of the last
voucher in the file, or an earlier ``til``: a bilag booked in Myntos this
morning and not yet in an export from last week is not a disagreement.

**Accounts as the import puts them.** The first four digits, unless the
opening balance moved one (:func:`myntos.saft.voucher_import.account_map`).
The VAT accounts (2700, 2710, 2740 and anything grouped as «Skyldig
merverdiavgift») are compared as one: each program settles a term by moving
2700 and 2710 to 2740 when its own return is filed, and only one of them files
it. What the VAT should be is compared per term and code instead, which is the
comparison that matters.

**Customers and suppliers on 1500 and 2400 only.** The sub-ledger is the lines
on the receivables and payables accounts that name a party. Myntos's register
names the party on the cost line too, and counting that would make every
purchase a difference.

**What it cannot see.** Which voucher is the other's: the two programs number
differently and may split a bilag differently. The months narrow a difference
down to where to look.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.arsoppgjor import opening
from myntos.domain.arsoppgjor.findings import Finding, merknad
from myntos.domain.ledger.chart import (
    CLOSING_JOURNAL_ID,
    INPUT_VAT_ACCOUNT,
    OPENING_JOURNAL_ID,
    OUTPUT_VAT_ACCOUNT,
    PAYABLES_ACCOUNT,
    RECEIVABLES_ACCOUNT,
)
from myntos.domain.ledger.models import (
    NON_REPORTABLE_STANDARD_TAX_CODES,
    Account,
    AccountGrouping,
    Company,
    Journal,
    Party,
    PartyType,
    Transaction,
    TransactionLine,
)
from myntos.domain.mva import service as mva_service
from myntos.domain.mva.periods import Termin, termin_for
from myntos.saft.errors import SaftWrongYearError
from myntos.saft.opening_import import check_company, match_party, target_account
from myntos.saft.reader import PartyKind, SaftEntries
from myntos.saft.voucher_import import (
    ImportSource,
    account_map,
    parts_by_date,
    split_code,
    vat_as_kept,
)

ZERO = Decimal("0.00")
VAT_ACCOUNT = "2740"
VAT_DESCRIPTION = "Merverdiavgift, alle mva-kontoer samlet"
_VAT_GROUPING = ("kortsiktigGjeld", "2740")
_SUB_LEDGER = frozenset({RECEIVABLES_ACCOUNT, PAYABLES_ACCOUNT})


@dataclass(frozen=True)
class AccountDifference:
    account_number: str
    description: str
    in_file: Decimal
    in_myntos: Decimal
    #: The months in which the two moved the account differently.
    months: tuple[int, ...]

    @property
    def difference(self) -> Decimal:
        return self.in_myntos - self.in_file


@dataclass(frozen=True)
class PartyDifference:
    party_type: PartyType
    name: str
    #: The old program's number for the party, when the file names it.
    source_id: str | None
    #: This company's party, when Myntos has it.
    party_pk: int | None
    in_file: Decimal
    in_myntos: Decimal

    @property
    def difference(self) -> Decimal:
        return self.in_myntos - self.in_file


@dataclass(frozen=True)
class VatDifference:
    termin: str
    code: str
    spesifikasjon: str | None
    base_in_file: Decimal
    base_in_myntos: Decimal
    vat_in_file: Decimal
    vat_in_myntos: Decimal


@dataclass(frozen=True)
class Comparison:
    source: ImportSource
    start: date
    through: date
    accounts_compared: int
    parties_compared: int
    terms_compared: int
    accounts: list[AccountDifference]
    parties: list[PartyDifference]
    vat: list[VatDifference]
    findings: list[Finding]

    @property
    def matches(self) -> bool:
        return not (self.accounts or self.parties or self.vat)


def _day(value: date) -> str:
    return f"{value.day}.{value.month}.{value.year}"


async def _vat_accounts(db: AsyncSession, company: Company, year: int) -> set[str]:
    numbers = set(
        await db.scalars(
            select(Account.account_number)
            .join(AccountGrouping, AccountGrouping.account_id == Account.id)
            .where(
                Account.company_id == company.id,
                AccountGrouping.financial_year == year,
                AccountGrouping.grouping_category == _VAT_GROUPING[0],
                AccountGrouping.grouping_code == _VAT_GROUPING[1],
            )
        )
    )
    return numbers | {OUTPUT_VAT_ACCOUNT, INPUT_VAT_ACCOUNT, VAT_ACCOUNT}


def _differences(
    left: dict[str, dict[int, Decimal]], right: dict[str, dict[int, Decimal]]
) -> Iterable[tuple[str, Decimal, Decimal, tuple[int, ...]]]:
    for key in sorted(set(left) | set(right)):
        months_left = left.get(key, {})
        months_right = right.get(key, {})
        total_left = sum(months_left.values(), ZERO)
        total_right = sum(months_right.values(), ZERO)
        months = tuple(
            month
            for month in sorted(set(months_left) | set(months_right))
            if months_left.get(month, ZERO) != months_right.get(month, ZERO)
        )
        if months:
            yield key, total_left, total_right, months


async def compare(
    db: AsyncSession, *, company: Company, found: SaftEntries, til: date | None = None
) -> Comparison:
    """Compare ``found``'s year so far with this company's books. Stores nothing."""
    balances = found.balances
    check_company(company, balances.orgnr, balances.company_name)
    year = balances.start.year
    start = date(year, 1, 1)
    if balances.start != start:
        raise SaftWrongYearError(
            f"filen gjelder {balances.start.isoformat()} til {balances.end.isoformat()}. "
            "Sammenligningen trenger en fil fra 1. januar: ta ut SAF-T-filen for hele året"
        )
    dated = [
        (on, lines)
        for transaction in found.transactions
        for on, lines in parts_by_date(transaction).items()
        if start <= on <= balances.end
    ]
    last = max((on for on, _ in dated), default=start)
    through = min(til, last) if til is not None else last

    standing = (await opening.state(db, company=company)).standing
    moved = account_map(standing, balances.accounts)
    vat_accounts = await _vat_accounts(db, company, year)
    vat_accounts |= {
        number
        for account in balances.accounts
        if (account.grouping_category, account.grouping_code) == _VAT_GROUPING
        and (number := target_account(account.account_id)) is not None
    }

    def key_of(account_id: str) -> str:
        number = moved.get(account_id) or target_account(account_id) or account_id
        return VAT_ACCOUNT if number in vat_accounts else number

    accounts = {
        account.account_number: account
        for account in await db.scalars(select(Account).where(Account.company_id == company.id))
    }
    parties = list(await db.scalars(select(Party).where(Party.company_id == company.id)))
    register = {(record.kind, record.party_id): record for record in balances.register}
    file_codes = balances.tax_codes or {}
    termin = mva_service.period_type_of(company)

    # --- The old program's side --------------------------------------------------
    file_accounts: dict[str, dict[int, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    file_parties: dict[tuple[PartyKind, str], Decimal] = defaultdict(lambda: ZERO)
    file_vat: dict[tuple[int, str, str | None], list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    for on, lines in dated:
        if on > through:
            continue
        for line in lines:
            key = key_of(line.account_id)
            file_accounts[key][on.month] += line.amount
            if (
                line.party_kind is not None
                and line.party_id
                and (moved.get(line.account_id) or target_account(line.account_id)) in _SUB_LEDGER
            ):
                file_parties[(line.party_kind, line.party_id)] += line.amount
            # Fiken's VAT read as the import posts it: read raw, a sale's VAT is a
            # deduction, every base is 0 and a reverse charge nets to nothing.
            for tax in vat_as_kept(line, file_codes).taxes:
                stripped, spesifikasjon = split_code(tax.code)
                code = file_codes.get(tax.code) or stripped
                if code in NON_REPORTABLE_STANDARD_TAX_CODES:
                    continue
                totals = file_vat[(termin_for(termin, on).index, code, spesifikasjon)]
                totals[0] += abs(tax.base) if line.amount >= ZERO else -abs(tax.base)
                totals[1] += tax.debit_tax - tax.credit_tax

    # --- Myntos's side -------------------------------------------------------------
    window = (
        Journal.company_id == company.id,
        # The opening balance is not a movement, and a year closed in Myntos
        # moves its result to equity in a way the old program's file does not.
        Journal.journal_id.not_in((OPENING_JOURNAL_ID, CLOSING_JOURNAL_ID)),
        Transaction.posted_at.is_not(None),
        Transaction.gl_posting_date >= start,
        Transaction.gl_posting_date <= through,
    )
    our_accounts: dict[str, dict[int, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    rows = await db.execute(
        select(
            Account.account_number,
            Transaction.gl_posting_date,
            func.sum(TransactionLine.debit),
            func.sum(TransactionLine.credit),
        )
        .join(TransactionLine, TransactionLine.account_id == Account.id)
        .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
        .join(Journal, Journal.id == Transaction.journal_id)
        .where(*window)
        .group_by(Account.account_number, Transaction.gl_posting_date)
    )
    for number, on, debit, credit in rows:
        key = VAT_ACCOUNT if number in vat_accounts else number
        our_accounts[key][on.month] += (debit or ZERO) - (credit or ZERO)
    our_parties: dict[int, Decimal] = defaultdict(lambda: ZERO)
    party_rows = await db.execute(
        select(
            TransactionLine.party_id,
            func.sum(TransactionLine.debit),
            func.sum(TransactionLine.credit),
        )
        .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
        .join(Journal, Journal.id == Transaction.journal_id)
        .join(Account, Account.id == TransactionLine.account_id)
        .where(
            *window,
            TransactionLine.party_id.is_not(None),
            Account.account_number.in_(_SUB_LEDGER),
        )
        .group_by(TransactionLine.party_id)
    )
    for party_pk, debit, credit in party_rows:
        our_parties[party_pk] += (debit or ZERO) - (credit or ZERO)
    our_vat: dict[tuple[int, str, str | None], list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    for on, taxed in await mva_service.taxed_lines(db, company, start, through):
        totals = our_vat[(termin_for(termin, on).index, taxed.code, taxed.spesifikasjon)]
        totals[0] += taxed.base if taxed.is_debit else -taxed.base
        totals[1] += taxed.debit_tax - taxed.credit_tax

    # --- Where they part -------------------------------------------------------------
    descriptions = {a.account_id: a.description for a in balances.accounts}
    account_differences = [
        AccountDifference(
            account_number=number,
            description=(
                VAT_DESCRIPTION
                if number == VAT_ACCOUNT
                else accounts[number].description
                if number in accounts
                else next(
                    (text for source, text in descriptions.items() if key_of(source) == number),
                    "",
                )
            ),
            in_file=in_file,
            in_myntos=in_myntos,
            months=months,
        )
        for number, in_file, in_myntos, months in _differences(file_accounts, our_accounts)
    ]

    # A party is this company's party when Myntos has it, and the old program's
    # number when it does not. Two numbers there for one party here (a supplier
    # registered twice) are one row.
    by_pk = {party.id: party for party in parties}
    resolved: dict[tuple[PartyKind, str], Party | None] = {}
    for kind, party_id in file_parties:
        record = register.get((kind, party_id))
        resolved[(kind, party_id)] = match_party(
            parties,
            PartyType(kind),
            record.orgnr if record is not None else None,
            record.name if record is not None else party_id,
        )
    theirs_by_party: dict[int, Decimal] = defaultdict(lambda: ZERO)
    unmatched: dict[tuple[PartyKind, str], Decimal] = {}
    for party_key, amount in file_parties.items():
        matched = resolved[party_key]
        if matched is None:
            unmatched[party_key] = amount
        else:
            theirs_by_party[matched.id] += amount
    party_differences: list[PartyDifference] = []
    for party_pk in set(theirs_by_party) | set(our_parties):
        in_file = theirs_by_party.get(party_pk, ZERO)
        in_myntos = our_parties.get(party_pk, ZERO)
        if in_file == in_myntos or party_pk not in by_pk:
            continue
        party = by_pk[party_pk]
        party_differences.append(
            PartyDifference(
                party_type=PartyType(party.party_type),
                name=party.name,
                source_id=next(
                    (pid for (_, pid), found in resolved.items() if found is party), None
                ),
                party_pk=party.id,
                in_file=in_file,
                in_myntos=in_myntos,
            )
        )
    for (kind, party_id), in_file in unmatched.items():
        if in_file == ZERO:
            continue
        record = register.get((kind, party_id))
        party_differences.append(
            PartyDifference(
                party_type=PartyType(kind),
                name=record.name if record is not None else party_id,
                source_id=party_id,
                party_pk=None,
                in_file=in_file,
                in_myntos=ZERO,
            )
        )
    party_differences.sort(key=lambda p: (p.party_type, p.name.lower()))

    vat_differences: list[VatDifference] = []
    for vat_key in sorted(set(file_vat) | set(our_vat), key=lambda k: (k[0], k[1], k[2] or "")):
        ours = our_vat.get(vat_key, [ZERO, ZERO])
        theirs = file_vat.get(vat_key, [ZERO, ZERO])
        if ours == theirs:
            continue
        index, code, spesifikasjon = vat_key
        label = Termin(termin, year, index).label
        vat_differences.append(
            VatDifference(
                termin=label,
                code=code,
                spesifikasjon=spesifikasjon,
                base_in_file=theirs[0],
                base_in_myntos=ours[0],
                vat_in_file=theirs[1],
                vat_in_myntos=ours[1],
            )
        )

    findings = [
        merknad(
            "sammenlignet_periode",
            f"Sammenlignet fra {_day(start)} til {_day(through)}"
            + (", dagen for det siste bilaget i filen." if til is None or til >= last else ".")
            + " Inngående balanse er holdt utenfor på begge sider.",
        ),
        merknad(
            "mva_kontoer_samlet",
            "Mva-kontoene (2700, 2710, 2740 og andre gruppert som skyldig merverdiavgift) er "
            "sammenlignet som én, fordi hvert program flytter dem til 2740 når det selv sender "
            "mva-meldingen. Mva-en sammenlignes per termin og kode i stedet.",
        ),
    ]
    terms_compared = len({vat_key[0] for vat_key in set(file_vat) | set(our_vat)})
    return Comparison(
        source=ImportSource(
            software=balances.software,
            audit_file_version=balances.audit_file_version,
            orgnr=balances.orgnr,
            company_name=balances.company_name,
            start=balances.start,
            end=balances.end,
        ),
        start=start,
        through=through,
        accounts_compared=len(set(file_accounts) | set(our_accounts)),
        parties_compared=len(set(theirs_by_party) | set(our_parties)) + len(unmatched),
        terms_compared=terms_compared,
        accounts=account_differences,
        parties=party_differences,
        vat=vat_differences,
        findings=findings,
    )


__all__ = [
    "AccountDifference",
    "Comparison",
    "PartyDifference",
    "VatDifference",
    "compare",
]
