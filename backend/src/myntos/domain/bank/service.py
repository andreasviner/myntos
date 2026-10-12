"""Importing bank statements and reconciling the register against them.

The register used to be typed twice: once by the bank, once by the person
reading the bank's screen into Myntos. This turns the bank's half into data. A
camt.053 file is read (:mod:`.camt`), checked to add up, archived whole, and its
lines are set beside the vouchers already posted, so that each movement of
money is either explained by an entry in the books or visibly not.

Like every service here, nothing commits: the request owns the transaction, so
an import is all of its statements or none of them, and posting a voucher from
a line and linking the line to it cannot half-happen.

Every lookup filters on ``company.id``, for the reason
docs/company-isolation.md gives: two companies in the same account both pass
the membership check, and an id in a path is only ever as safe as the WHERE
clause that reads it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from myntos.core.clock import utc_now
from myntos.domain.bank import camt, matching
from myntos.domain.bank.errors import (
    BankLineAlreadyMatchedError,
    BankLineNotFoundError,
    BankMatchMismatchError,
    BankStatementConflictError,
    BankStatementNotFoundError,
    InvalidBankStatementError,
    UnknownStatementAccountError,
    VoucherAlreadyMatchedError,
)
from myntos.domain.bank.models import BankStatement, BankStatementFile, BankStatementLine
from myntos.domain.bilag.errors import DocumentTooLargeError
from myntos.domain.bilag.models import MAX_ATTACHMENT_BYTES
from myntos.domain.bilag.service import sanitise_filename
from myntos.domain.faktura.models import SalesDocument
from myntos.domain.ledger import cashflow
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.bankkonto import format_bank_account
from myntos.domain.ledger.cashflow import CashFlowDirection
from myntos.domain.ledger.chart import BANK_ACCOUNT
from myntos.domain.ledger.models import Company, Journal, Transaction, TransactionLine
from myntos.domain.ledger.service import PaymentMethod
from myntos.storage.base import DocumentStorage

XML_CONTENT_TYPE = "application/xml"
_FALLBACK_FILENAME = "kontoutskrift.xml"

MATCH_CONFIRMED = "bekreftet"
MATCH_CREATED = "opprettet"


# --- Import ------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportResult:
    file: BankStatementFile
    statements: list[BankStatement]
    # True when these exact bytes had been imported before, in which case
    # nothing was stored and ``statements`` are the ones the first import made.
    already_imported: bool
    # Entries the file holds that are not booked yet, and were left out.
    skipped_unbooked: int = 0


def _display_name(filename: str | None) -> str:
    """The name the file is shown and downloaded under.

    Through the bilag archive's own sanitiser, so a statement's name obeys the
    same rules as every other document's. That sanitiser falls back to
    ``bilag.pdf`` for a name with nothing left in it, which on an XML file
    would be a wrong extension on a document an auditor downloads by name, so
    the fallback is replaced here.
    """
    raw = (filename or "").strip()
    cleaned = sanitise_filename(raw, content_type=XML_CONTENT_TYPE) if raw else ""
    if not cleaned or (cleaned == "bilag.pdf" and not raw.lower().endswith(".pdf")):
        return _FALLBACK_FILENAME
    return cleaned


async def _existing_file(
    db: AsyncSession, company: Company, digest: str
) -> BankStatementFile | None:
    found: BankStatementFile | None = await db.scalar(
        select(BankStatementFile)
        .where(BankStatementFile.company_id == company.id, BankStatementFile.sha256 == digest)
        .options(
            selectinload(BankStatementFile.statements).selectinload(BankStatement.bank_account),
            selectinload(BankStatementFile.statements).selectinload(BankStatement.file),
        )
    )
    return found


async def import_statement_file(
    db: AsyncSession,
    storage: DocumentStorage,
    *,
    company: Company,
    content: bytes,
    filename: str | None,
    uploaded_by_user_id: int | None,
) -> ImportResult:
    """Read, check, archive and store one camt.053 file.

    Everything that can refuse the file runs before anything is written: the
    parse, the arithmetic, the currency, the accounts, and the comparison with
    lines already held. Only then is the file put in storage and the rows added.
    The file is written before its row, as in the bilag archive, so a failure
    can leave an orphaned file and never a row pointing at nothing.
    """
    if not content:
        raise InvalidBankStatementError("filen er tom")
    if len(content) > MAX_ATTACHMENT_BYTES:
        megabytes = MAX_ATTACHMENT_BYTES // (1024 * 1024)
        raise DocumentTooLargeError(f"filen er større enn {megabytes} MB")

    digest = hashlib.sha256(content).hexdigest()
    existing = await _existing_file(db, company, digest)
    if existing is not None:
        # The same bytes again. Answered with the first import rather than
        # refused: pressing the button twice is not a mistake worth an error,
        # and archiving a second copy of an identical document adds nothing.
        return ImportResult(file=existing, statements=existing.statements, already_imported=True)

    parsed = camt.parse(content)

    accounts = {held.account: held for held in company.bank_accounts}
    unknown = sorted(
        {s.account_number for s in parsed.statements if s.account_number not in accounts}
    )
    if unknown:
        listed = ", ".join(format_bank_account(number) for number in unknown)
        raise UnknownStatementAccountError(
            f"kontoutskriften gjelder {listed}, som ikke er registrert på dette selskapet. "
            "Legg kontoen til under Selskapet først, eller sjekk at du har valgt riktig selskap"
        )

    # Keyed per account, across every statement in the file, so a file holding
    # two overlapping days of the same account dedups against itself as well as
    # against what was imported before.
    planned: list[tuple[camt.ParsedStatement, list[tuple[str, camt.ParsedEntry, bool]]]] = []
    claimed: dict[tuple[int, str], camt.ParsedEntry] = {}
    for statement in parsed.statements:
        account = accounts[statement.account_number]
        keys = camt.dedup_keys(statement.entries)
        held = await _held_lines(db, account.id, keys)
        rows: list[tuple[str, camt.ParsedEntry, bool]] = []
        for key, entry in zip(keys, statement.entries, strict=True):
            previous = held.get(key)
            earlier = claimed.get((account.id, key))
            if previous is not None:
                _assert_same(key, entry, previous.booking_date, previous.amount, previous.direction)
            elif earlier is not None:
                _assert_same(key, entry, earlier.booking_date, earlier.amount, earlier.direction)
            known = previous is not None or earlier is not None
            if not known:
                claimed[(account.id, key)] = entry
            rows.append((key, entry, known))
        planned.append((statement, rows))

    display_name = _display_name(filename)
    key = await storage.save(content=content, filename=display_name, content_type=XML_CONTENT_TYPE)
    archived = BankStatementFile(
        company_id=company.id,
        filename=display_name,
        content_type=XML_CONTENT_TYPE,
        byte_size=len(content),
        sha256=digest,
        storage_key=key,
        camt_version=parsed.version,
        message_id=_cut(parsed.message_id, 128),
        uploaded_by_user_id=uploaded_by_user_id,
    )
    db.add(archived)
    await db.flush()

    stored: list[BankStatement] = []
    for statement, rows in planned:
        account = accounts[statement.account_number]
        row = BankStatement(
            company_id=company.id,
            file_id=archived.id,
            bank_account_id=account.id,
            statement_ref=statement.statement_id,
            from_date=statement.from_date,
            to_date=statement.to_date,
            opening_balance=statement.opening_balance,
            closing_balance=statement.closing_balance,
            lines_new=sum(1 for _, _, known in rows if not known),
            lines_known=sum(1 for _, _, known in rows if known),
        )
        # Both set through the relationship, so the response can read them off
        # the new row without a lazy load inside the request.
        row.bank_account = account
        row.file = archived
        db.add(row)
        await db.flush()
        for dedup_key, entry, known in rows:
            if known:
                continue
            db.add(
                BankStatementLine(
                    company_id=company.id,
                    statement_id=row.id,
                    bank_account_id=account.id,
                    dedup_key=dedup_key,
                    booking_date=entry.booking_date,
                    value_date=entry.value_date,
                    amount=entry.amount,
                    direction=entry.direction,
                    currency=entry.currency,
                    counterparty_name=_cut(entry.counterparty_name, 255),
                    remittance=_cut(entry.remittance, 1000),
                    kid=_cut(entry.kid, 64),
                    end_to_end_id=_cut(entry.end_to_end_id, 64),
                    bank_reference=_cut(entry.bank_reference, 128),
                    additional_info=_cut(entry.additional_info, 500),
                )
            )
        stored.append(row)
    try:
        await db.flush()
    except IntegrityError as error:
        # Two imports of overlapping files racing each other: the unique key is
        # what makes the loser lose, and the answer is to try again, which will
        # then find the lines the winner stored.
        raise BankStatementConflictError(
            "en annen import av den samme kontoen ble lagret samtidig. Prøv igjen"
        ) from error

    return ImportResult(
        file=archived,
        statements=stored,
        already_imported=False,
        skipped_unbooked=sum(s.skipped_unbooked for s in parsed.statements),
    )


def _cut(value: str | None, length: int) -> str | None:
    return value[:length] if value else None


async def _held_lines(
    db: AsyncSession, bank_account_pk: int, keys: Sequence[str]
) -> dict[str, BankStatementLine]:
    if not keys:
        return {}
    rows = await db.scalars(
        select(BankStatementLine).where(
            BankStatementLine.bank_account_id == bank_account_pk,
            BankStatementLine.dedup_key.in_(list(set(keys))),
        )
    )
    return {line.dedup_key: line for line in rows}


def _assert_same(
    key: str,
    entry: camt.ParsedEntry,
    booking_date: date,
    amount: Decimal,
    direction: CashFlowDirection,
) -> None:
    """A line we already hold must say what it said the first time.

    Only reachable for a bank reference: a fingerprint *is* those fields, so a
    changed one is a different fingerprint and simply a new line.
    """
    if (entry.booking_date, entry.amount, entry.direction) == (booking_date, amount, direction):
        return
    reference = key.removeprefix("ref:")
    raise BankStatementConflictError(
        f"bankreferansen {reference} er allerede importert med {direction.value} {amount} "
        f"kroner bokført {booking_date.isoformat()}, men denne filen sier "
        f"{entry.direction.value} {entry.amount} kroner bokført {entry.booking_date.isoformat()}. "
        "Filen er ikke importert. Kontakt banken om utskriften er rettet"
    )


# --- Reading -----------------------------------------------------------------------


async def list_statements(
    db: AsyncSession, *, company: Company, limit: int = 100
) -> list[BankStatement]:
    """Imported statements, the most recent period first."""
    rows = await db.scalars(
        select(BankStatement)
        .where(BankStatement.company_id == company.id)
        .options(selectinload(BankStatement.file), selectinload(BankStatement.bank_account))
        .order_by(BankStatement.to_date.desc(), BankStatement.id.desc())
        .limit(limit)
    )
    return list(rows)


async def get_statement(db: AsyncSession, *, company: Company, statement_pk: int) -> BankStatement:
    statement = await db.scalar(
        select(BankStatement)
        .where(BankStatement.id == statement_pk, BankStatement.company_id == company.id)
        .options(selectinload(BankStatement.file), selectinload(BankStatement.bank_account))
    )
    if statement is None:
        raise BankStatementNotFoundError("finner ikke kontoutskriften")
    return statement


async def get_line(db: AsyncSession, *, company: Company, line_pk: int) -> BankStatementLine:
    """One of this company's bank lines, or :class:`BankLineNotFoundError`."""
    line = await db.scalar(
        select(BankStatementLine)
        .where(BankStatementLine.id == line_pk, BankStatementLine.company_id == company.id)
        .options(selectinload(BankStatementLine.transaction))
    )
    if line is None:
        raise BankLineNotFoundError("finner ikke banklinjen")
    return line


async def list_lines(
    db: AsyncSession,
    *,
    company: Company,
    matched: bool | None = None,
    statement_pk: int | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[BankStatementLine]:
    """Bank lines, newest booking first. ``matched`` narrows to one side."""
    query = (
        select(BankStatementLine)
        .where(BankStatementLine.company_id == company.id)
        .options(selectinload(BankStatementLine.transaction))
    )
    if matched is True:
        query = query.where(BankStatementLine.transaction_pk.is_not(None))
    elif matched is False:
        query = query.where(BankStatementLine.transaction_pk.is_(None))
    if statement_pk is not None:
        query = query.where(BankStatementLine.statement_id == statement_pk)
    rows = await db.scalars(
        query.order_by(BankStatementLine.booking_date.desc(), BankStatementLine.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(rows)


async def reversed_vouchers(db: AsyncSession, voucher_pks: Sequence[int]) -> set[int]:
    """Which of ``voucher_pks`` have been reversed since.

    A line matched to a voucher that was later reversed is no longer explained
    by anything that stands, and the page has to be able to say so. The link is
    left in place rather than cleared behind the person's back: it was true when
    it was made, and the reversal is the event worth showing.
    """
    if not voucher_pks:
        return set()
    rows = await db.scalars(
        select(Transaction.reverses_transaction_pk).where(
            Transaction.reverses_transaction_pk.in_(list(voucher_pks))
        )
    )
    return {pk for pk in rows if pk is not None}


@dataclass(frozen=True)
class InvoiceHint:
    """An issued invoice whose KID a bank line carries."""

    document_id: int
    document_number: str | None
    buyer_name: str


async def invoices_by_kid(
    db: AsyncSession, *, company: Company, kids: Sequence[str]
) -> dict[str, InvoiceHint]:
    """Issued invoices this company holds, keyed by the KID they were issued with.

    A KID is the one piece of a bank line that identifies a document rather
    than resembling one, so when an incoming payment carries an invoice's KID
    the page says which invoice. Settling the receivable from here is not built
    yet (there is no voucher type for a customer paying 1500), so this is a
    pointer, not a posting.
    """
    wanted = sorted({kid for kid in kids if kid})
    if not wanted:
        return {}
    rows = await db.scalars(
        select(SalesDocument).where(
            SalesDocument.company_id == company.id,
            SalesDocument.payment_reference.in_(wanted),
            SalesDocument.document_number.is_not(None),
        )
    )
    return {
        doc.payment_reference: InvoiceHint(
            document_id=doc.id, document_number=doc.document_number, buyer_name=doc.buyer_name
        )
        for doc in rows
        if doc.payment_reference is not None
    }


# --- Suggestions -------------------------------------------------------------------


def facts(line: BankStatementLine) -> matching.LineFacts:
    return matching.LineFacts(
        booking_date=line.booking_date,
        amount=line.amount,
        direction=line.direction,
        counterparty_name=line.counterparty_name,
        remittance=line.remittance,
        kid=line.kid,
        additional_info=line.additional_info,
    )


async def _candidates(
    db: AsyncSession, company: Company, *, earliest: date, latest: date
) -> list[matching.Candidate]:
    """Posted bank vouchers in the window that nothing has claimed yet.

    Three things take a voucher out of the running, each for the reason the
    register's own summary gives for leaving it out of the figures: it is a
    reversal, it has been reversed, or it already explains another bank line.
    What is left is a voucher that still stands and is still unexplained.
    """
    reversal = aliased(Transaction, name="cancelling_voucher")
    claimed = (
        select(BankStatementLine.transaction_pk)
        .where(BankStatementLine.transaction_pk.is_not(None))
        .scalar_subquery()
    )
    rows = await db.scalars(
        select(Transaction)
        .join(Journal, Journal.id == Transaction.journal_id)
        .where(
            Journal.company_id == company.id,
            Transaction.posted_at.is_not(None),
            Transaction.transaction_date >= earliest,
            Transaction.transaction_date <= latest,
            Transaction.reverses_transaction_pk.is_(None),
            ~select(reversal.id).where(reversal.reverses_transaction_pk == Transaction.id).exists(),
            Transaction.id.not_in(claimed),
        )
        .options(
            selectinload(Transaction.lines).selectinload(TransactionLine.account),
            selectinload(Transaction.lines).selectinload(TransactionLine.party),
            selectinload(Transaction.lines).selectinload(TransactionLine.tax_code),
        )
    )
    found: list[matching.Candidate] = []
    for transaction in rows:
        view = ledger_service.describe(transaction)
        # Only money that went through the bank can be the booking of a bank
        # line. A cash receipt for the same amount on the same day is a
        # coincidence the register must not offer.
        if view is None or view.payment_account != BANK_ACCOUNT:
            continue
        found.append(
            matching.Candidate(
                voucher_id=view.id,
                voucher_no=view.voucher_no,
                occurred_on=view.occurred_on,
                gross_amount=view.gross_amount,
                direction=view.direction,
                description=view.description,
                party_name=view.party_name,
                type_code=view.type_code,
            )
        )
    return found


async def suggest(
    db: AsyncSession, *, company: Company, lines: Sequence[BankStatementLine]
) -> dict[int, list[matching.Suggestion]]:
    """Ranked candidate vouchers for each unmatched line, in one query.

    One fetch over the union of the lines' windows rather than one per line:
    a page of two hundred lines must not be two hundred queries.
    """
    open_lines = [line for line in lines if line.transaction_pk is None]
    if not open_lines:
        return {}
    earliest = min(line.booking_date for line in open_lines) - matching.WINDOW_BEFORE
    latest = max(line.booking_date for line in open_lines) + matching.WINDOW_AFTER
    candidates = await _candidates(db, company, earliest=earliest, latest=latest)
    return {line.id: matching.rank(facts(line), candidates) for line in open_lines}


# --- Reconciling -------------------------------------------------------------------


async def _assert_explains(db: AsyncSession, line: BankStatementLine, voucher: Transaction) -> None:
    """Refuse a voucher that cannot be the booking of ``line``.

    The same requirements :func:`matching.score` puts on a suggestion, checked
    again on confirm because the voucher id arrives from the browser and a
    suggestion is not a promise.
    """
    view = ledger_service.describe(voucher)
    if view is None or view.payment_account != BANK_ACCOUNT:
        raise BankMatchMismatchError(
            "bilaget er ikke ført mot bankkontoen (1920), så det kan ikke være banklinjens føring"
        )
    if view.is_reversal or await reversed_vouchers(db, [voucher.id]):
        raise BankMatchMismatchError(
            "bilaget er reversert, eller er selv en reversering, og forklarer ikke lenger "
            "noen bevegelse på kontoen"
        )
    if view.direction is not line.direction or view.gross_amount != line.amount:
        raise BankMatchMismatchError(
            f"bilaget gjelder {view.direction.value} {view.gross_amount} kroner, mens "
            f"banklinjen er {line.direction.value} {line.amount} kroner. Beløpet må være "
            "det samme"
        )


async def _link(
    db: AsyncSession,
    line: BankStatementLine,
    voucher: Transaction,
    *,
    kind: str,
    user_id: int | None,
) -> BankStatementLine:
    owner = await db.scalar(
        select(BankStatementLine.id).where(BankStatementLine.transaction_pk == voucher.id)
    )
    if owner is not None and owner != line.id:
        raise VoucherAlreadyMatchedError(
            f"bilag {voucher.transaction_id} er allerede avstemt mot en annen banklinje"
        )
    line.transaction_pk = voucher.id
    line.transaction = voucher
    line.match_kind = kind
    line.matched_at = utc_now()
    line.matched_by_user_id = user_id
    try:
        await db.flush()
    except IntegrityError as error:
        # Two people confirming the same voucher against two lines at once.
        raise VoucherAlreadyMatchedError(
            f"bilag {voucher.transaction_id} er allerede avstemt mot en annen banklinje"
        ) from error
    return line


async def confirm_match(
    db: AsyncSession,
    *,
    company: Company,
    line_pk: int,
    voucher_pk: int,
    user_id: int | None,
) -> BankStatementLine:
    """Record that ``voucher_pk`` is the booking of the bank line.

    A link and nothing else: the voucher is read, never written. The voucher is
    looked up through the company like every other sub-resource, so a voucher id
    from the company next door is not found rather than linked.
    """
    line = await get_line(db, company=company, line_pk=line_pk)
    if line.transaction_pk is not None:
        raise BankLineAlreadyMatchedError("banklinjen er allerede avstemt")
    voucher = await ledger_service.get_voucher(db, company=company, voucher_pk=voucher_pk)
    await _assert_explains(db, line, voucher)
    return await _link(db, line, voucher, kind=MATCH_CONFIRMED, user_id=user_id)


async def remove_match(db: AsyncSession, *, company: Company, line_pk: int) -> BankStatementLine:
    """Undo a reconciliation. The voucher stays exactly as it was.

    Allowed because the link is our claim and not the ledger's: a match
    confirmed against the wrong one of two identical payments is a mistake in
    the reconciliation, and it is corrected by moving the link. A voucher that
    was *posted* from the line stays posted; if that is what was wrong, the
    register's reversal is the way to undo it.
    """
    line = await get_line(db, company=company, line_pk=line_pk)
    line.transaction_pk = None
    line.transaction = None
    line.match_kind = None
    line.matched_at = None
    line.matched_by_user_id = None
    await db.flush()
    return line


async def create_entry_from_line(
    db: AsyncSession,
    *,
    company: Company,
    line_pk: int,
    type_code: str,
    description: str,
    tax_code: str | None,
    party_pk: int | None,
    created_by_user_id: int | None,
) -> tuple[BankStatementLine, Transaction]:
    """Post a voucher for an unexplained bank line, and link the two.

    Through :func:`myntos.domain.ledger.service.record_cash_flow`, the same
    function the register's own form uses, so the type catalogue decides the
    account and the VAT treatment exactly as it does there. A second posting
    path for bank lines would be a second place for the VAT rules to drift.

    What the bank states is not the person's to change: the amount, the date
    and the direction come from the line. The type has to agree with the
    direction, because a money-in category on a payment out would post the
    double entry backwards.
    """
    line = await get_line(db, company=company, line_pk=line_pk)
    if line.transaction_pk is not None:
        raise BankLineAlreadyMatchedError("banklinjen er allerede avstemt")
    entry_type = cashflow.get_type(type_code)
    if entry_type.direction is not line.direction:
        wanted = "en innbetaling" if line.direction is CashFlowDirection.INN else "en utbetaling"
        raise BankMatchMismatchError(
            f"«{entry_type.label}» går feil vei: banklinjen er {wanted}. Velg en type for "
            f"{'penger inn' if line.direction is CashFlowDirection.INN else 'penger ut'}"
        )
    transaction = await ledger_service.record_cash_flow(
        db,
        company=company,
        type_code=type_code,
        occurred_on=line.booking_date,
        gross_amount=line.amount,
        description=description,
        tax_code=tax_code,
        payment_method=PaymentMethod.BANK,
        party_pk=party_pk,
        created_by_user_id=created_by_user_id,
    )
    await _link(db, line, transaction, kind=MATCH_CREATED, user_id=created_by_user_id)
    return line, transaction
