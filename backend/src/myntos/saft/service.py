"""Read one company's financial year out of the ledger into an :class:`AuditFile`.

Every query here is filtered by the company's own id, journals included:
:class:`~myntos.domain.ledger.models.Transaction` has no ``company_id`` of its
own and reaches the company through its journal, so the join is the wall. A
SAF-T file is the whole of a company's books in one download, which makes it
the single most damaging thing to get wrong in docs/company-isolation.md.

**Opening balances come from the ledger.** Nothing in Myntos writes
:class:`AccountOpeningBalance`; a company that brought its books from another
system posts its opening balance as a voucher in the ``IB`` journal
(:mod:`myntos.domain.arsoppgjor.opening`), and a closed year has its result
disposed onto equity by vouchers in ``AVS``. So:

- **The ``IB`` journal is the year's opening, not its movements.** Its vouchers
  for the exported year are added to ``OpeningDebitBalance`` /
  ``OpeningCreditBalance`` (per account and per customer and supplier) and left
  out of ``GeneralLedgerEntries``. Both at once would count the same balance
  twice, and the file's closing balance would no longer be its opening plus its
  transactions, which is the first thing a reader of the file checks. Reversals
  and replacements of the opening voucher live in the same journal and net
  there. For every later year the voucher is simply part of the earlier years'
  postings, like everything else.
- **A closed year's result is already on equity.** The disposition voucher
  brings the income and cost accounts to zero, so the derivation below finds
  nothing undisposed and says nothing.

The rest of the rule, for what is still derived:

- If the company has stored opening rows for some year at or before the one
  exported, the latest such year is the base, and the postings from that year up
  to the one before the export are added on top of it.
- With no stored rows at all, the base is zero, which is true: every company in
  Myntos started its books here.
- Profit-and-loss accounts always open at zero, because at year end they are
  closed to equity. The result they carried in the years between the base and
  the export has not been disposed of by anybody, so it is placed on equity
  (2050) as udisponert resultat, and the check says that it was.

That keeps the opening balance sheet summing to zero, which SAF-T requires and
the check verifies, without pretending an årsoppgjør was made.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from myntos.domain.identity.models import User
from myntos.domain.ledger.chart import OPENING_JOURNAL_ID
from myntos.domain.ledger.models import (
    Account,
    AccountGrouping,
    AccountOpeningBalance,
    Company,
    DimensionType,
    FinancialYear,
    Journal,
    Party,
    PartyOpeningBalance,
    PartyType,
    TaxCode,
    Transaction,
    TransactionLine,
    TransactionLineAnalysis,
)
from myntos.saft import check as check_module
from myntos.saft.errors import SaftYearNotFoundError
from myntos.saft.export import (
    ZERO,
    Address,
    AnalysisValue,
    AuditFile,
    Balance,
    CompanyHeader,
    Entry,
    GLAccount,
    JournalEntries,
    Line,
    LineAnalysis,
    LineTax,
    PartyEntry,
    PartyKind,
    TaxRatePeriod,
)

# Where a prior year's undisposed result lands in a derived opening balance.
EQUITY_ACCOUNT = "2050"


@dataclass(frozen=True)
class Snapshot:
    audit: AuditFile
    # Notes about how the snapshot was put together, which are true of the file
    # and not of the ledger, so the check cannot find them by reading it.
    notes: tuple[check_module.Finding, ...] = ()


def _posted_in(company: Company) -> list[ColumnElement[bool]]:
    """The filter every ledger query here starts from: this company, posted."""
    return [Journal.company_id == company.id, Transaction.posted_at.is_not(None)]


def _journal_filter(opening: bool | None) -> list[ColumnElement[bool]]:
    """Only the ``IB`` journal, everything but it, or (None) everything."""
    if opening is None:
        return []
    if opening:
        return [Journal.journal_id == OPENING_JOURNAL_ID]
    return [Journal.journal_id != OPENING_JOURNAL_ID]


async def _movements(
    db: AsyncSession,
    company: Company,
    *,
    from_year: int | None,
    to_year: int,
    opening: bool | None = None,
) -> dict[int, Balance]:
    """Debit and credit per account over ``[from_year, to_year]``, inclusive."""
    query = (
        select(
            TransactionLine.account_id,
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .join(Transaction, TransactionLine.transaction_pk == Transaction.id)
        .join(Journal, Transaction.journal_id == Journal.id)
        .where(
            *_posted_in(company), *_journal_filter(opening), Transaction.financial_year <= to_year
        )
        .group_by(TransactionLine.account_id)
    )
    if from_year is not None:
        query = query.where(Transaction.financial_year >= from_year)
    rows = await db.execute(query)
    return {
        account_id: Balance(Decimal(str(debit)), Decimal(str(credit)))
        for account_id, debit, credit in rows
    }


async def _party_movements(
    db: AsyncSession,
    company: Company,
    *,
    from_year: int | None,
    to_year: int,
    opening: bool | None = None,
) -> dict[tuple[int, int], Balance]:
    """Debit and credit per (party, account) over ``[from_year, to_year]``."""
    query = (
        select(
            TransactionLine.party_id,
            TransactionLine.account_id,
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .join(Transaction, TransactionLine.transaction_pk == Transaction.id)
        .join(Journal, Transaction.journal_id == Journal.id)
        .where(
            *_posted_in(company),
            *_journal_filter(opening),
            Transaction.financial_year <= to_year,
            TransactionLine.party_id.is_not(None),
        )
        .group_by(TransactionLine.party_id, TransactionLine.account_id)
    )
    if from_year is not None:
        query = query.where(Transaction.financial_year >= from_year)
    rows = await db.execute(query)
    return {
        (party_id, account_id): Balance(Decimal(str(debit)), Decimal(str(credit)))
        for party_id, account_id, debit, credit in rows
    }


async def _opening_base_year(db: AsyncSession, company: Company, year: int) -> int | None:
    """The latest year at or before ``year`` for which opening rows were stored."""
    found = await db.scalar(
        select(func.max(AccountOpeningBalance.financial_year))
        .join(Account, AccountOpeningBalance.account_id == Account.id)
        .where(Account.company_id == company.id, AccountOpeningBalance.financial_year <= year)
    )
    return None if found is None else int(found)


async def load(
    db: AsyncSession,
    *,
    company: Company,
    year: int,
    exported_by: User,
    software_version: str,
    created_at: datetime,
) -> Snapshot:
    financial_year = await db.scalar(
        select(FinancialYear).where(
            FinancialYear.company_id == company.id, FinancialYear.year == year
        )
    )
    if financial_year is None:
        raise SaftYearNotFoundError(f"selskapet har ikke noe regnskap for {year} i Myntos")

    notes: list[check_module.Finding] = []

    # --- Accounts and their balances -----------------------------------------
    accounts = list(
        await db.scalars(
            select(Account).where(Account.company_id == company.id).order_by(Account.account_number)
        )
    )
    by_pk = {account.id: account for account in accounts}
    groupings = {
        grouping.account_id: grouping
        for grouping in await db.scalars(
            select(AccountGrouping)
            .join(Account, AccountGrouping.account_id == Account.id)
            .where(Account.company_id == company.id, AccountGrouping.financial_year == year)
        )
    }

    base_year = await _opening_base_year(db, company, year)
    stored: dict[int, Balance] = {}
    party_stored: dict[int, Balance] = {}
    if base_year is not None:
        for row in await db.scalars(
            select(AccountOpeningBalance)
            .join(Account, AccountOpeningBalance.account_id == Account.id)
            .where(
                Account.company_id == company.id,
                AccountOpeningBalance.financial_year == base_year,
            )
        ):
            stored[row.account_id] = Balance(row.opening_debit, row.opening_credit)
        for party_row in await db.scalars(
            select(PartyOpeningBalance)
            .join(Party, PartyOpeningBalance.party_id == Party.id)
            .where(
                Party.company_id == company.id,
                PartyOpeningBalance.financial_year == base_year,
            )
        ):
            party_stored[party_row.party_id] = Balance(
                party_row.opening_debit, party_row.opening_credit
            )

    earlier: dict[int, Balance] = {}
    party_earlier: dict[tuple[int, int], Balance] = {}
    # Stored rows for the exported year itself are the opening balance as is;
    # anything older needs the postings since.
    if base_year is None or base_year < year:
        earlier = await _movements(db, company, from_year=base_year, to_year=year - 1)
        party_earlier = await _party_movements(db, company, from_year=base_year, to_year=year - 1)
    # The opening voucher of this very year is opening balance, not movement.
    brought_in = await _movements(db, company, from_year=year, to_year=year, opening=True)
    party_brought_in = await _party_movements(
        db, company, from_year=year, to_year=year, opening=True
    )
    for account_id, balance in brought_in.items():
        earlier[account_id] = earlier.get(account_id, Balance()).plus(balance.debit, balance.credit)
    for key, balance in party_brought_in.items():
        party_earlier[key] = party_earlier.get(key, Balance()).plus(balance.debit, balance.credit)
    if any(b.debit or b.credit for b in brought_in.values()):
        notes.append(
            check_module.Finding(
                check_module.Severity.MERKNAD,
                "inngaende_balanse_fra_bilag",
                f"Inngående balanse for {year} kommer fra åpningsbilaget i journalen "
                f"{OPENING_JOURNAL_ID}. Den står som inngående saldo på kontoene og er ikke "
                "med blant årets transaksjoner.",
            )
        )
    this_year = await _movements(db, company, from_year=year, to_year=year, opening=False)
    party_this_year = await _party_movements(
        db, company, from_year=year, to_year=year, opening=False
    )

    undisposed = ZERO
    openings: dict[int, Balance] = {}
    for account in accounts:
        moved = earlier.get(account.id, Balance())
        if account.account_type.is_balance_account:
            base = stored.get(account.id, Balance())
            openings[account.id] = base.plus(moved.debit, moved.credit)
        else:
            # Closed to equity at year end. What it carried is the result.
            openings[account.id] = Balance()
            undisposed += moved.net
    if undisposed != ZERO:
        equity = next((a for a in accounts if a.account_number == EQUITY_ACCOUNT), None)
        if equity is not None:
            openings[equity.id] = openings[equity.id].plus(
                undisposed if undisposed > ZERO else ZERO,
                -undisposed if undisposed < ZERO else ZERO,
            )
            notes.append(
                check_module.Finding(
                    check_module.Severity.MERKNAD,
                    "udisponert_resultat",
                    "Resultatet for tidligere år er ikke disponert i Myntos, fordi året ikke "
                    "er avsluttet der. Den inngående balansen fører det på konto "
                    f"{EQUITY_ACCOUNT} slik at den går i null. Avslutt året under "
                    "Årsavslutning, så disponeres det med riktige kontoer.",
                )
            )

    gl_accounts = []
    for account in accounts:
        opening = openings[account.id]
        moved = this_year.get(account.id, Balance())
        grouping = groupings.get(account.id)
        if (
            grouping is None
            and account.created_date is not None
            and account.created_date > financial_year.end_date
            and opening == Balance()
            and moved == Balance()
        ):
            # Added to the kontoplan after this year was closed, so it was
            # never grouped for it (myntos.domain.ledger.kontoplan writes no
            # grouping into a closed year) and nothing in the year touches it.
            # It was not part of these books then; listing it would only make
            # the check refuse a year whose filing has already been made.
            continue
        gl_accounts.append(
            GLAccount(
                account_id=account.account_number,
                description=account.description,
                grouping_category=grouping.grouping_category if grouping else None,
                grouping_code=grouping.grouping_code if grouping else None,
                created=account.created_date,
                opening=opening,
                closing=opening.plus(moved.debit, moved.credit),
            )
        )

    # --- Customers and suppliers ----------------------------------------------
    parties = list(
        await db.scalars(
            select(Party).where(Party.company_id == company.id).order_by(Party.party_id)
        )
    )
    party_entries = []
    for party in parties:
        key = (party.id, party.balance_account_id)
        opening = party_stored.get(party.id, Balance())
        before = party_earlier.get(key, Balance())
        opening = opening.plus(before.debit, before.credit)
        moved = party_this_year.get(key, Balance())
        balance_account = by_pk.get(party.balance_account_id)
        party_entries.append(
            PartyEntry(
                kind=PartyKind.CUSTOMER
                if party.party_type is PartyType.KUNDE
                else PartyKind.SUPPLIER,
                party_id=party.party_id,
                name=party.name,
                orgnr=party.orgnr,
                address=Address(party.address_line, party.postal_code, party.city, party.country),
                balance_account_id=balance_account.account_number if balance_account else "",
                opening=opening,
                closing=opening.plus(moved.debit, moved.credit),
            )
        )
    party_by_pk = {party.id: party for party in parties}

    # --- VAT table ----------------------------------------------------------------
    tax_codes = list(
        await db.scalars(
            select(TaxCode)
            .where(TaxCode.company_id == company.id)
            .options(selectinload(TaxCode.rates))
        )
    )
    tax_by_pk = {code.id: code for code in tax_codes}
    rates = sorted(
        (
            TaxRatePeriod(
                code=code.code,
                description=code.description,
                standard_tax_code=code.standard_tax_code,
                rate=rate.rate,
                valid_from=rate.valid_from,
                valid_to=rate.valid_to,
            )
            for code in tax_codes
            for rate in code.rates
        ),
        key=lambda r: (int(r.code) if r.code.isdigit() else 10_000, r.code, r.valid_from),
    )

    # --- Dimensions ---------------------------------------------------------------
    dimension_types = list(
        await db.scalars(
            select(DimensionType)
            .where(DimensionType.company_id == company.id)
            .options(selectinload(DimensionType.values))
            .order_by(DimensionType.code)
        )
    )
    analysis_values = tuple(
        AnalysisValue(
            analysis_type=dimension.code,
            type_description=dimension.description,
            analysis_id=value.value,
            id_description=value.description,
            status="Active" if dimension.is_active and value.is_active else "Closed",
        )
        for dimension in dimension_types
        for value in sorted(dimension.values, key=lambda v: v.value)
    )

    # --- The year's vouchers ------------------------------------------------------
    transactions = list(
        await db.scalars(
            select(Transaction)
            .join(Journal, Transaction.journal_id == Journal.id)
            .where(
                *_posted_in(company),
                *_journal_filter(False),
                Transaction.financial_year == year,
            )
            .options(
                selectinload(Transaction.journal),
                selectinload(Transaction.lines)
                .selectinload(TransactionLine.analysis)
                .selectinload(TransactionLineAnalysis.dimension_value),
            )
            .order_by(Journal.journal_id, Transaction.transaction_date, Transaction.id)
        )
    )
    dimension_code = {dimension.id: dimension.code for dimension in dimension_types}

    def _line(line: TransactionLine, transaction: Transaction) -> Line:
        party = party_by_pk.get(line.party_id) if line.party_id is not None else None
        tax_code = tax_by_pk.get(line.tax_code_id) if line.tax_code_id is not None else None
        account = by_pk[line.account_id]
        return Line(
            record_id=line.record_id,
            account_id=account.account_number,
            description=line.description,
            debit=line.debit,
            credit=line.credit,
            customer_id=party.party_id
            if party is not None and party.party_type is PartyType.KUNDE
            else None,
            supplier_id=party.party_id
            if party is not None and party.party_type is PartyType.LEVERANDOR
            else None,
            source_document_id=transaction.source_document_id,
            currency_code=line.currency_code,
            currency_amount=line.currency_amount,
            exchange_rate=line.exchange_rate,
            tax=LineTax(
                code=tax_code.code,
                rate=line.tax_rate,
                base=line.tax_base,
                debit_tax=line.debit_tax_amount or ZERO,
                credit_tax=line.credit_tax_amount or ZERO,
            )
            if tax_code is not None
            else None,
            analysis=tuple(
                LineAnalysis(
                    analysis_type=dimension_code.get(a.dimension_value.dimension_type_id, ""),
                    analysis_id=a.dimension_value.value,
                    amount=a.amount,
                )
                for a in line.analysis
            ),
        )

    grouped: dict[int, list[Entry]] = defaultdict(list)
    journals: dict[int, Journal] = {}
    for transaction in transactions:
        journals[transaction.journal.id] = transaction.journal
        grouped[transaction.journal.id].append(
            Entry(
                transaction_id=transaction.transaction_id,
                period=transaction.period,
                year=transaction.financial_year,
                transaction_date=transaction.transaction_date,
                gl_posting_date=transaction.gl_posting_date,
                system_entry=transaction.system_entry_date,
                description=transaction.description,
                voucher_type=transaction.voucher_type,
                source_id=str(transaction.created_by_user_id)
                if transaction.created_by_user_id is not None
                else None,
                lines=tuple(
                    _line(line, transaction)
                    for line in sorted(
                        transaction.lines,
                        key=lambda li: (
                            int(li.record_id) if li.record_id.isdigit() else 0,
                            li.record_id,
                        ),
                    )
                ),
            )
        )
    journal_entries = tuple(
        JournalEntries(
            journal_id=journal.journal_id,
            description=journal.description,
            journal_type=journal.journal_type,
            entries=tuple(grouped[pk]),
        )
        for pk, journal in sorted(journals.items(), key=lambda item: item[1].journal_id)
    )

    primary_first = sorted(company.bank_accounts, key=lambda b: (not b.is_primary, b.id))
    audit = AuditFile(
        year=year,
        start=financial_year.start_date,
        end=financial_year.end_date,
        created_at=created_at,
        software_version=software_version,
        company=CompanyHeader(
            orgnr=company.orgnr,
            name=company.name,
            address=Address(
                company.address_line, company.postal_code, company.city, company.country
            ),
            contact_name=exported_by.full_name,
            telephone=company.phone,
            email=company.email,
            vat_registered=company.vat_registered,
            bank_accounts=tuple((b.account, b.label) for b in primary_first),
        ),
        accounts=tuple(gl_accounts),
        parties=tuple(party_entries),
        tax_rates=tuple(rates),
        analysis_values=analysis_values,
        journals=journal_entries,
    )
    return Snapshot(audit=audit, notes=tuple(notes))


def findings(snapshot: Snapshot) -> list[check_module.Finding]:
    """The check's findings and the snapshot's own notes, blocking ones first."""
    combined = [*check_module.check(snapshot.audit), *snapshot.notes]
    return sorted(combined, key=lambda f: f.severity is not check_module.Severity.FEIL)
