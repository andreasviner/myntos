"""A customer paying an issued invoice: the receivable settled, in full or in part.

An issued invoice is revenue and a debit on 1500 (kundefordringer) the day it
is issued (:func:`myntos.domain.faktura.service.issue`). The money arriving is
a second event, often weeks later, and it is booked as a voucher of its own:
the bank or cash account debited, 1500 credited with the same customer. Until
2026-11-19 nothing in Myntos could book that from the invoice. A payment was
booked as a sale in the register, which counted the revenue twice, or as a
manual voucher, or not at all, which left 1500 growing for ever and the
overview's «Inn i år» short of the year's income with no way to see why.

**Never automatic on the due date.** The due date is when the money is owed,
not when it came. A payment booked on a date nobody saw it arrive is an entry
without documentation (bokføringsloven § 10, bokføringsforskriften § 5-1-1),
and a customer who pays late, pays part, or never pays would be booked as
having paid, with 1500 understated by exactly the money the company is still
waiting for. The bank statement is the documentation, so the payment is
registered from it: by hand, with the date and amount read off the statement,
or from the bank line itself when it carries the invoice's KID
(:func:`myntos.domain.bank.service.settle_invoice_from_line`).

**The link lives on the ledger line.** The payment's 1500 line names the
invoice (``TransactionLine.settles_document_pk``), and what an invoice has been
paid is the credits on the lines that name it less the debits. Nothing else is
stored, so nothing can disagree with the postings. A reversal copies the line,
link included, which is why a payment undone in the register counts as unpaid
again here without the register knowing invoices exist.

Partial payments are payments: an invoice may be paid in as many instalments
as the customer likes, and each is its own voucher. Paying more than is owed is
refused (:class:`~myntos.domain.faktura.errors.PaymentExceedsOutstandingError`):
the excess is the customer's money, or another invoice's, and which is the
person's to say.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.faktura.errors import (
    NotPayableError,
    PaymentExceedsOutstandingError,
    PaymentInFutureError,
)
from myntos.domain.faktura.models import SalesDocument
from myntos.domain.faktura.service import credited_by, get_document
from myntos.domain.faktura.totals import totals
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.chart import RECEIVABLES_ACCOUNT
from myntos.domain.ledger.models import (
    Account,
    Company,
    Journal,
    Transaction,
    TransactionLine,
)
from myntos.domain.ledger.posting import ZERO
from myntos.domain.ledger.service import PaymentMethod

# The day a payment may be dated up to is the day in Norway. In UTC a payment
# typed in just after midnight on the day it came would be a day in the future.
_OSLO = ZoneInfo("Europe/Oslo")

# A voucher's description column is 255 characters; a long buyer name must not
# turn a payment into a database error.
_DESCRIPTION_LENGTH = 255


class PaymentState(enum.StrEnum):
    """Where an issued invoice stands, read off its payments."""

    UBETALT = "ubetalt"
    DELVIS_BETALT = "delvis_betalt"
    BETALT = "betalt"
    # Cancelled by a kreditnota. Nothing is owed on it whatever was paid; what
    # was paid before the credit is owed back to the customer.
    KREDITERT = "kreditert"


@dataclass(frozen=True)
class InvoicePayment:
    """One payment of an invoice that still stands (neither reversed nor a reversal)."""

    voucher_id: int
    voucher_no: str
    paid_on: date
    amount: Decimal
    # 1920 for the bank, 1900 for cash.
    payment_account: str


@dataclass(frozen=True)
class Settlement:
    """What an issued invoice has been paid, and what is left.

    ``outstanding`` is what the customer still owes. On a credited invoice it
    is minus what was paid before the credit, which the company owes the
    customer back until it repays it; on every other invoice it is never below
    zero, because an overpayment is refused. A repayment is not linked to the
    invoice (there is no route for it yet: it is a manual voucher crediting
    the bank and debiting 1500), so a credited invoice keeps showing what was
    paid on it after the money has gone back.
    """

    paid: Decimal
    outstanding: Decimal
    state: PaymentState
    payments: tuple[InvoicePayment, ...]


def today_in_norway() -> date:
    return datetime.now(_OSLO).date()


def _is_payable_kind(document: SalesDocument) -> bool:
    return document.is_issued and not document.is_credit_note


async def settlements(
    db: AsyncSession,
    *,
    company: Company,
    documents: Sequence[SalesDocument],
    credited: Mapping[int, int] | None = None,
) -> dict[int, Settlement]:
    """``{invoice id: what it has been paid}`` for the issued fakturaer given.

    Drafts and kreditnotaer get no entry: neither is owed anything. Two
    queries for the whole page whatever its length. ``credited`` is
    :func:`~myntos.domain.faktura.service.credited_by` for the same documents,
    passed by a caller that already has it; fetched here otherwise.

    Every voucher that names an invoice counts towards what is paid, the
    reversals included, so a payment and its reversal net to nothing. The
    payments listed are only the ones that still stand: a payment reversed,
    and the reversal itself, are history the register shows, not money the
    invoice has had.
    """
    payable = [document for document in documents if _is_payable_kind(document)]
    if not payable:
        return {}
    ids = [document.id for document in payable]
    if credited is None:
        credited = await credited_by(db, ids)
    rows = (
        await db.execute(
            select(
                TransactionLine.settles_document_pk,
                Transaction.id,
                Transaction.transaction_id,
                Transaction.transaction_date,
                Transaction.reverses_transaction_pk,
                TransactionLine.debit,
                TransactionLine.credit,
            )
            .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
            # Through the company's journals as well as through the invoice:
            # the invoices are this company's already, and a line in another
            # company's books naming one of them would be a bug this keeps out
            # of the figures rather than a payment.
            .join(Journal, Journal.id == Transaction.journal_id)
            .where(
                Journal.company_id == company.id,
                Transaction.posted_at.is_not(None),
                TransactionLine.settles_document_pk.in_(ids),
            )
            .order_by(Transaction.transaction_date, Transaction.id)
        )
    ).all()
    reversed_ = {row.reverses_transaction_pk for row in rows if row.reverses_transaction_pk}
    standing = [
        row for row in rows if row.reverses_transaction_pk is None and row.id not in reversed_
    ]
    accounts = await _payment_accounts(db, [row.id for row in standing])

    paid: dict[int, Decimal] = {}
    for row in rows:
        moved = (row.credit or ZERO) - (row.debit or ZERO)
        paid[row.settles_document_pk] = paid.get(row.settles_document_pk, ZERO) + moved
    listed: dict[int, list[InvoicePayment]] = {}
    for row in standing:
        listed.setdefault(row.settles_document_pk, []).append(
            InvoicePayment(
                voucher_id=row.id,
                voucher_no=row.transaction_id,
                paid_on=row.transaction_date,
                amount=(row.credit or ZERO) - (row.debit or ZERO),
                payment_account=accounts.get(row.id, ""),
            )
        )

    found: dict[int, Settlement] = {}
    for document in payable:
        total = totals(document).payable
        received = paid.get(document.id, ZERO)
        if document.id in credited:
            state = PaymentState.KREDITERT
            outstanding = -received
        else:
            outstanding = total - received
            if received == ZERO:
                state = PaymentState.UBETALT
            elif outstanding <= ZERO:
                state = PaymentState.BETALT
            else:
                state = PaymentState.DELVIS_BETALT
        found[document.id] = Settlement(
            paid=received,
            outstanding=outstanding,
            state=state,
            payments=tuple(listed.get(document.id, ())),
        )
    return found


async def _payment_accounts(db: AsyncSession, voucher_pks: Sequence[int]) -> dict[int, str]:
    """``{voucher id: the account the money came into}`` for payment vouchers."""
    if not voucher_pks:
        return {}
    rows = await db.execute(
        select(TransactionLine.transaction_pk, Account.account_number)
        .join(Account, Account.id == TransactionLine.account_id)
        .where(
            TransactionLine.transaction_pk.in_(list(voucher_pks)),
            TransactionLine.debit > ZERO,
            Account.account_number != RECEIVABLES_ACCOUNT,
        )
    )
    return dict(rows.tuples().all())


async def settlement_of(
    db: AsyncSession, *, company: Company, document: SalesDocument
) -> Settlement | None:
    """:func:`settlements` for one document; ``None`` for a draft or a kreditnota."""
    return (await settlements(db, company=company, documents=[document])).get(document.id)


def _kroner(value: Decimal) -> str:
    """``1250`` → ``1 250,00``, as the person reads it on the invoice."""
    return f"{value.quantize(Decimal('0.01')):,.2f}".replace(",", " ").replace(".", ",")


async def register_payment(
    db: AsyncSession,
    *,
    company: Company,
    document_pk: int,
    paid_on: date,
    amount: Decimal,
    payment_method: PaymentMethod = PaymentMethod.BANK,
    created_by_user_id: int | None = None,
    today: date | None = None,
) -> tuple[SalesDocument, Transaction]:
    """Book a payment the customer made on an issued invoice.

    ``paid_on`` and ``amount`` are what the bank statement (or the cash
    receipt) says: the day the money came and how much. Less than what is
    left is a part payment, and the invoice stays open for the rest.

    Refuses a draft, a kreditnota and a credited invoice
    (:class:`NotPayableError`), a date after today in Norway
    (:class:`PaymentInFutureError`), and more than is left
    (:class:`PaymentExceedsOutstandingError`). The year has to be open, as for
    any voucher (:func:`myntos.domain.ledger.service.ensure_financial_year`).
    """
    # Locked first, so two payments of the same invoice sent at once are
    # checked against each other rather than both against the same balance.
    # Only PostgreSQL takes the lock; SQLite has one writer anyway.
    await db.execute(
        select(SalesDocument.id)
        .where(SalesDocument.id == document_pk, SalesDocument.company_id == company.id)
        .with_for_update()
    )
    document = await get_document(db, company=company, document_pk=document_pk)
    if document.is_credit_note:
        raise NotPayableError(
            "en kreditnota betales ikke inn: det er selskapet som skylder kunden pengene"
        )
    if not document.is_issued:
        raise NotPayableError("fakturaen er ikke utstedt ennå, så kunden skylder ingenting på den")
    if paid_on > (today or today_in_norway()):
        raise PaymentInFutureError(
            f"datoen {paid_on:%d.%m.%Y} er fram i tid. Registrer innbetalingen på dagen "
            "pengene kom, slik kontoutskriften viser det"
        )
    settlement = await settlement_of(db, company=company, document=document)
    assert settlement is not None  # noqa: S101 - an issued faktura always has one
    if settlement.state is PaymentState.KREDITERT:
        raise NotPayableError(
            f"faktura {document.document_number} er kreditert, så kunden skylder ingenting på den"
        )
    if amount > settlement.outstanding:
        if settlement.outstanding <= ZERO:
            raise PaymentExceedsOutstandingError(
                f"faktura {document.document_number} er allerede betalt"
            )
        raise PaymentExceedsOutstandingError(
            f"det står {_kroner(settlement.outstanding)} kr igjen på faktura "
            f"{document.document_number}, og innbetalingen er på {_kroner(amount)} kr. Det "
            "som er betalt for mye, er ikke en betaling av denne fakturaen"
        )
    description = f"Innbetaling faktura {document.document_number}, {document.buyer_name}"
    transaction = await ledger_service.record_invoice_payment(
        db,
        company=company,
        document_pk=document.id,
        party_pk=document.party_id,
        occurred_on=paid_on,
        amount=amount,
        description=description[:_DESCRIPTION_LENGTH],
        payment_method=payment_method,
        created_by_user_id=created_by_user_id,
    )
    return document, transaction
