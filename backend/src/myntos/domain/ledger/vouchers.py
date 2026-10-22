"""The bilagsjournal: every posted voucher of a year, from every journal.

The register (``…/cashflow``) lists what was registered in Myntos as a payment,
read back as its category, and leaves out on purpose what was booked by other
rules: the opening balance, the previous program's vouchers, the manual ones.
That was right for the register and left those vouchers nowhere to be seen.
The farm pilot's farmer brought a year over from Tripletex, found the accounts
in the kontoplan and the figures on the overview, and asked where the payments
were. They were posted; there was no page that listed them.

This is that page's question: which vouchers stand in the books, with every
line, whatever wrote them. Read-only, and it says nothing about categories:
an account, an amount, a text, the party and the VAT code, as the ledger has
them. It is also the first of the bokføringsspesifikasjoner bokføringsloven § 5
asks a program to produce (docs/FEATURES.md, item 5): the bilagsjournal, and,
narrowed to one account with its balance carried, the hovedbok for that account,
which for 1920 is the company's ins and outs of the bank.

**With the documents in the archive.** Each voucher lists the files the bilag
archive holds for it. The register shows a payment's receipts, but a voucher
brought over from the old program is not in the register, and when its
documents came over too (Tripletex's printout, :mod:`myntos.saft.documents`)
this was the only page that could show them.

**A balance carried, only for one account.** Narrowed to an account, each
voucher shows its movement on that account and the balance after it, opening
with what the account held before the period: for a balance-sheet account
everything before, for an income or cost account only this year's, since those
open every year at nothing (the SAF-T export reads them the same way). Without
an account the vouchers are only listed: a sum of every account is zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.bilag import service as bilag_service
from myntos.domain.bilag.models import Attachment
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.errors import AccountNotFoundError
from myntos.domain.ledger.models import (
    Account,
    Company,
    Journal,
    Transaction,
    TransactionLine,
)

ZERO = Decimal("0.00")

#: The most vouchers one page carries.
PAGE_SIZE = 100


@dataclass(frozen=True)
class VoucherLine:
    account_number: str
    account_description: str
    description: str | None
    debit: Decimal
    credit: Decimal
    party_name: str | None
    tax_code: str | None
    dimensions: tuple[str, ...]


@dataclass(frozen=True)
class VoucherDocument:
    """A file in the bilag archive on the voucher, downloaded by its id."""

    id: int
    filename: str
    content_type: str
    byte_size: int


@dataclass(frozen=True)
class VoucherRow:
    id: int
    journal: str
    journal_description: str
    voucher_no: str
    on: date
    description: str
    voucher_type: str | None
    #: Where the documentation is when it is not in Myntos's archive: the old
    #: program's voucher, «Tripletex bilag 240-2026».
    source_document: str | None
    total: Decimal
    reverses_id: int | None
    reversed_by_id: int | None
    created_by_name: str | None
    lines: tuple[VoucherLine, ...]
    #: The voucher's movement on the account asked about, and the balance after
    #: it. None when no account was asked about.
    movement: Decimal | None = None
    balance: Decimal | None = None
    documents: tuple[VoucherDocument, ...] = ()


@dataclass(frozen=True)
class JournalCount:
    code: str
    description: str
    vouchers: int


@dataclass(frozen=True)
class VoucherPage:
    year: int
    month: int | None
    total_count: int
    offset: int
    vouchers: list[VoucherRow]
    #: Every journal with vouchers in the year, whatever the other filters.
    journals: list[JournalCount]
    account_number: str | None = None
    account_description: str | None = None
    #: What the account held before the period, and at its end.
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None


def _period(year: int, month: int | None) -> tuple[date, date]:
    if month is None:
        return date(year, 1, 1), date(year, 12, 31)
    following = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return date(year, month, 1), date.fromordinal(following.toordinal() - 1)


def _filtered(
    company: Company,
    start: date,
    end: date,
    journal: str | None,
    account: Account | None,
    search: str | None,
    with_documents: bool,
) -> Select[tuple[int]]:
    query = (
        select(Transaction.id)
        .join(Journal, Journal.id == Transaction.journal_id)
        .where(
            Journal.company_id == company.id,
            Transaction.posted_at.is_not(None),
            Transaction.transaction_date >= start,
            Transaction.transaction_date <= end,
        )
    )
    if journal is not None:
        query = query.where(Journal.journal_id == journal)
    if account is not None:
        query = query.where(
            select(TransactionLine.id)
            .where(
                TransactionLine.transaction_pk == Transaction.id,
                TransactionLine.account_id == account.id,
            )
            .exists()
        )
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(
            or_(
                Transaction.description.ilike(pattern),
                Transaction.transaction_id.ilike(pattern),
                select(TransactionLine.id)
                .where(
                    TransactionLine.transaction_pk == Transaction.id,
                    TransactionLine.description.ilike(pattern),
                )
                .exists(),
            )
        )
    if with_documents:
        query = query.where(
            select(Attachment.id)
            .where(Attachment.transaction_pk == Transaction.id, Attachment.company_id == company.id)
            .exists()
        )
    return query


async def _opening(db: AsyncSession, company: Company, account: Account, start: date) -> Decimal:
    query = (
        select(func.sum(TransactionLine.debit), func.sum(TransactionLine.credit))
        .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
        .join(Journal, Journal.id == Transaction.journal_id)
        .where(
            Journal.company_id == company.id,
            Transaction.posted_at.is_not(None),
            TransactionLine.account_id == account.id,
            Transaction.transaction_date < start,
        )
    )
    if not account.account_type.is_balance_account:
        query = query.where(Transaction.transaction_date >= date(start.year, 1, 1))
    debit, credit = (await db.execute(query)).one()
    return (debit or ZERO) - (credit or ZERO)


async def list_vouchers(
    db: AsyncSession,
    *,
    company: Company,
    year: int,
    month: int | None = None,
    journal: str | None = None,
    account_number: str | None = None,
    search: str | None = None,
    with_documents: bool = False,
    newest_first: bool = False,
    offset: int = 0,
    limit: int = PAGE_SIZE,
) -> VoucherPage:
    """One page of the year's posted vouchers, with their lines, oldest first by default.

    ``newest_first`` turns the list around, which is how /bilag/ shows it: what
    was just posted is what a person looks for, and a year brought over from the
    old program put it hundreds of vouchers down. The balance beside each
    voucher is the account's after that voucher in date order either way, so a
    page read from the top down shows the balance going back in time, as a bank
    statement does.

    ``with_documents`` keeps the vouchers the bilag archive has a file for. A
    printout from Tripletex put 48 documents on a farm's September, and they
    were there, but on the fifth page of 596 vouchers listed from January: the
    pilot looked at the first two and reported them missing (2026-10-22).
    """
    start, end = _period(year, month)
    account: Account | None = None
    if account_number:
        account = await db.scalar(
            select(Account).where(
                Account.company_id == company.id, Account.account_number == account_number.strip()
            )
        )
        if account is None:
            raise AccountNotFoundError(f"kontoplanen har ingen konto {account_number.strip()}")

    ordered = _filtered(company, start, end, journal, account, search, with_documents).order_by(
        Transaction.transaction_date, Transaction.id
    )
    ids = list(await db.scalars(ordered))
    listed = ids[::-1] if newest_first else ids
    page_ids = listed[offset : offset + limit]

    movements: dict[int, Decimal] = {}
    opening: Decimal | None = None
    if account is not None:
        opening = await _opening(db, company, account, start)
    if account is not None and ids:
        for pk, debit, credit in await db.execute(
            select(
                TransactionLine.transaction_pk,
                func.sum(TransactionLine.debit),
                func.sum(TransactionLine.credit),
            )
            .where(
                TransactionLine.account_id == account.id,
                TransactionLine.transaction_pk.in_(ids),
            )
            .group_by(TransactionLine.transaction_pk)
        ):
            movements[int(pk)] = (debit or ZERO) - (credit or ZERO)

    transactions = (
        {
            transaction.id: transaction
            for transaction in await db.scalars(
                ledger_service._voucher_query().where(
                    Journal.company_id == company.id, Transaction.id.in_(page_ids)
                )
            )
        }
        if page_ids
        else {}
    )
    journals = {
        journal_row.id: journal_row
        for journal_row in await db.scalars(select(Journal).where(Journal.company_id == company.id))
    }
    reversals: dict[int, int] = {}
    if page_ids:
        for original, reversal in await db.execute(
            select(Transaction.reverses_transaction_pk, Transaction.id).where(
                Transaction.reverses_transaction_pk.in_(page_ids)
            )
        ):
            if original is not None:
                reversals[int(original)] = int(reversal)
    authors = await ledger_service._author_names(
        db, [t.created_by_user_id for t in transactions.values()]
    )
    attached = await bilag_service.list_attachments(db, company=company, voucher_pks=page_ids)

    # The balance after each voucher, carried in date order whatever order the
    # page is in, so the same voucher shows the same balance read either way.
    after: dict[int, Decimal] = {}
    if opening is not None:
        running = opening
        for pk in ids:
            running += movements.get(pk, ZERO)
            after[pk] = running
    rows: list[VoucherRow] = []
    for pk in page_ids:
        transaction = transactions[pk]
        journal_row = journals[transaction.journal_id]
        movement = movements.get(pk) if account is not None else None
        rows.append(
            VoucherRow(
                id=transaction.id,
                journal=journal_row.journal_id,
                journal_description=journal_row.description,
                voucher_no=transaction.transaction_id,
                on=transaction.transaction_date,
                description=transaction.description,
                voucher_type=transaction.voucher_type,
                source_document=transaction.source_document_id,
                total=sum((line.debit for line in transaction.lines), ZERO),
                reverses_id=transaction.reverses_transaction_pk,
                reversed_by_id=reversals.get(transaction.id),
                created_by_name=(
                    authors.get(transaction.created_by_user_id)
                    if transaction.created_by_user_id is not None
                    else None
                ),
                lines=tuple(
                    VoucherLine(
                        account_number=line.account.account_number,
                        account_description=line.account.description,
                        description=line.description,
                        debit=line.debit,
                        credit=line.credit,
                        party_name=line.party.name if line.party is not None else None,
                        tax_code=line.tax_code.code if line.tax_code is not None else None,
                        dimensions=tuple(
                            f"{allocation.dimension_value.value} "
                            f"{allocation.dimension_value.description}"
                            for allocation in line.analysis
                        ),
                    )
                    for line in sorted(
                        transaction.lines, key=lambda li: (li.record_id or "").zfill(18)
                    )
                ),
                movement=movement,
                balance=after.get(pk) if account is not None else None,
                documents=tuple(
                    VoucherDocument(
                        id=attachment.id,
                        filename=attachment.filename,
                        content_type=attachment.content_type,
                        byte_size=attachment.byte_size,
                    )
                    for attachment in attached.get(transaction.id, [])
                ),
            )
        )

    counts = [
        JournalCount(code=code, description=description, vouchers=int(count))
        for code, description, count in await db.execute(
            select(Journal.journal_id, Journal.description, func.count(Transaction.id))
            .join(Transaction, Transaction.journal_id == Journal.id)
            .where(
                Journal.company_id == company.id,
                Transaction.posted_at.is_not(None),
                Transaction.transaction_date >= date(year, 1, 1),
                Transaction.transaction_date <= date(year, 12, 31),
            )
            .group_by(Journal.journal_id, Journal.description)
            .order_by(Journal.journal_id)
        )
    ]
    closing = (
        opening + sum(movements.values(), ZERO)
        if opening is not None and account is not None
        else None
    )
    return VoucherPage(
        year=year,
        month=month,
        total_count=len(ids),
        offset=offset,
        vouchers=rows,
        journals=counts,
        account_number=account.account_number if account is not None else None,
        account_description=account.description if account is not None else None,
        opening_balance=opening,
        closing_balance=closing,
    )
