"""Bank import and reconciliation: the account's own record beside the register.

Upload a camt.053 file, see each movement of money it holds next to the voucher
that books it, and for the ones nothing books yet, either confirm the voucher
Myntos suggests or post a new one from the line.

Nothing here edits a voucher. Confirming a match writes a link on the bank
line; posting from a line goes through the register's own
:func:`~myntos.domain.ledger.service.record_cash_flow`. A posted voucher is
history, and the bank agreeing with it is not a reason to touch it.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status
from pydantic import BaseModel, Field

from myntos.api.deps import (
    KEY_READ,
    CurrentCompany,
    CurrentUser,
    DbSession,
    Storage,
    key_post,
    key_write,
)
from myntos.domain.bank import matching
from myntos.domain.bank import service as bank_service
from myntos.domain.bank.models import BankStatement, BankStatementLine
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.ledger.cashflow import CASH_FLOW_TYPES, CashFlowDirection

router = APIRouter(tags=["bank"])


class StatementResponse(BaseModel):
    id: int
    # Sent so lib/api.ts can check the answer belongs to the company on screen,
    # as it does for every company-scoped answer that names one.
    company_id: int
    bank_account_id: int
    account: str
    account_label: str | None
    statement_ref: str
    from_date: date
    to_date: date
    opening_balance: Decimal
    closing_balance: Decimal
    lines_new: int
    lines_known: int
    filename: str
    camt_version: str
    sha256: str
    imported_at: datetime


class ImportResponse(BaseModel):
    statements: list[StatementResponse]
    # True when the same file had been imported before. Nothing was stored, and
    # the statements are the ones the first import made.
    already_imported: bool
    skipped_unbooked: int


class SuggestionResponse(BaseModel):
    voucher_id: int
    voucher_no: str
    occurred_on: date
    description: str
    party_name: str | None
    type_label: str | None
    score: int
    # "sterk" or "mulig". Both can be confirmed; the word is what the page
    # shows instead of a number nobody can check.
    confidence: Literal["sterk", "mulig"]
    reasons: list[str]


class InvoiceHintResponse(BaseModel):
    document_id: int
    document_number: str | None
    buyer_name: str


class LineResponse(BaseModel):
    id: int
    company_id: int
    statement_id: int
    bank_account_id: int
    booking_date: date
    value_date: date | None
    amount: Decimal
    direction: CashFlowDirection
    counterparty_name: str | None
    remittance: str | None
    kid: str | None
    bank_reference: str | None
    additional_info: str | None
    voucher_id: int | None
    voucher_no: str | None
    # "bekreftet" or "opprettet": whether somebody agreed an existing voucher
    # explains the line, or the voucher was posted from it.
    match_kind: str | None
    matched_at: datetime | None
    # Set when the voucher the line was matched to has been reversed since. The
    # link stays; the page says the line is no longer explained.
    voucher_reversed: bool
    suggestions: list[SuggestionResponse]
    invoice: InvoiceHintResponse | None


class ConfirmMatchRequest(BaseModel):
    voucher_id: int


class CreateFromLineRequest(BaseModel):
    """What the person adds to a bank line to make it a voucher.

    No amount, no date and no direction: the bank states those, and a form that
    let them be changed would let the register disagree with the account it is
    being reconciled against, from the very screen meant to stop that.
    """

    type_code: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=255)
    tax_code: str | None = Field(default=None, max_length=32)
    party_id: int | None = None


def _statement_response(statement: BankStatement) -> StatementResponse:
    return StatementResponse(
        id=statement.id,
        company_id=statement.company_id,
        bank_account_id=statement.bank_account_id,
        account=statement.bank_account.account,
        account_label=statement.bank_account.label,
        statement_ref=statement.statement_ref,
        from_date=statement.from_date,
        to_date=statement.to_date,
        opening_balance=statement.opening_balance,
        closing_balance=statement.closing_balance,
        lines_new=statement.lines_new,
        lines_known=statement.lines_known,
        filename=statement.file.filename,
        camt_version=statement.file.camt_version,
        sha256=statement.file.sha256,
        imported_at=statement.imported_at,
    )


def _suggestion_response(suggestion: matching.Suggestion) -> SuggestionResponse:
    candidate = suggestion.candidate
    entry_type = CASH_FLOW_TYPES.get(candidate.type_code) if candidate.type_code else None
    return SuggestionResponse(
        voucher_id=candidate.voucher_id,
        voucher_no=candidate.voucher_no,
        occurred_on=candidate.occurred_on,
        description=candidate.description,
        party_name=candidate.party_name,
        type_label=entry_type.label if entry_type is not None else None,
        score=suggestion.score,
        confidence="sterk" if suggestion.strong else "mulig",
        reasons=list(suggestion.reasons),
    )


def _line_response(
    line: BankStatementLine,
    *,
    suggestions: list[matching.Suggestion] | None = None,
    reversed_vouchers: set[int] | None = None,
    invoices: dict[str, bank_service.InvoiceHint] | None = None,
) -> LineResponse:
    hint = (invoices or {}).get(line.kid) if line.kid else None
    return LineResponse(
        id=line.id,
        company_id=line.company_id,
        statement_id=line.statement_id,
        bank_account_id=line.bank_account_id,
        booking_date=line.booking_date,
        value_date=line.value_date,
        amount=line.amount,
        direction=line.direction,
        counterparty_name=line.counterparty_name,
        remittance=line.remittance,
        kid=line.kid,
        bank_reference=line.bank_reference,
        additional_info=line.additional_info,
        voucher_id=line.transaction_pk,
        voucher_no=line.transaction.transaction_id if line.transaction is not None else None,
        match_kind=line.match_kind,
        matched_at=line.matched_at,
        voucher_reversed=(
            line.transaction_pk is not None and line.transaction_pk in (reversed_vouchers or set())
        ),
        suggestions=[_suggestion_response(s) for s in suggestions or []],
        invoice=(
            InvoiceHintResponse(
                document_id=hint.document_id,
                document_number=hint.document_number,
                buyer_name=hint.buyer_name,
            )
            if hint is not None
            else None
        ),
    )


@router.post(
    "/companies/{company_id}/bank/kontoutskrifter",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_post("importere en kontoutskrift"),
)
async def import_statement(
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    storage: Storage,
    file: Annotated[UploadFile, File()],
    filename: Annotated[str | None, Form()] = None,
) -> ImportResponse:
    """Import a camt.053 file.

    Idempotent. The same file twice is answered with the first import; a file
    whose period overlaps one already imported adds only the lines that are new.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    content = await file.read()
    result = await bank_service.import_statement_file(
        db,
        storage,
        company=context.company,
        content=content,
        filename=filename or file.filename,
        uploaded_by_user_id=user.id,
    )
    return ImportResponse(
        statements=[_statement_response(s) for s in result.statements],
        already_imported=result.already_imported,
        skipped_unbooked=result.skipped_unbooked,
    )


@router.get("/companies/{company_id}/bank/kontoutskrifter", openapi_extra=KEY_READ)
async def list_statements(
    context: CurrentCompany,
    db: DbSession,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[StatementResponse]:
    """The bank statements (kontoutskrifter) imported into these books, latest period first.

    A bank statement is the bank's own record of what moved on one account over
    a period. Each camt.053 file imported with ``POST …/bank/kontoutskrifter``
    holds one or more of them, and each is listed here: which of the company's
    registered accounts it covers (``account``, eleven digits; ``account_label``,
    the company's own name for it), the period (``from_date`` to ``to_date``),
    the opening and closing balance the bank stated (decimal strings, kroner),
    and how many of its movements were new (``lines_new``) against how many
    were already held from an earlier statement that overlapped it
    (``lines_known``). ``filename``, ``camt_version`` and ``sha256`` describe
    the archived file, which ``…/kontoutskrifter/{statement_id}/fil`` returns
    byte for byte.

    Use it to say which periods the reconciliation (bankavstemming) covers and
    to spot a gap between two statements. The movements themselves, and
    whether each is explained by a voucher, are ``GET …/bank/linjer``.

    ``limit`` caps the list (1 to 500, default 100). Needs ``ledger:read``,
    which every role has. Nothing here can be deleted: a statement is primary
    documentation under bokføringsloven, kept like a receipt.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    statements = await bank_service.list_statements(db, company=context.company, limit=limit)
    return [_statement_response(s) for s in statements]


@router.get(
    "/companies/{company_id}/bank/kontoutskrifter/{statement_id}/fil", openapi_extra=KEY_READ
)
async def download_statement(
    statement_id: int, context: CurrentCompany, db: DbSession, storage: Storage
) -> Response:
    """The file as it was uploaded, byte for byte.

    ``attachment`` rather than ``inline`` for the reason the bilag download
    gives: the bytes came from a user's computer, and the site's own origin is
    no place to render them.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    statement = await bank_service.get_statement(
        db, company=context.company, statement_pk=statement_id
    )
    content = await storage.load(statement.file.storage_key)
    return Response(
        content=content,
        media_type=statement.file.content_type,
        headers={"Content-Disposition": f'attachment; filename="{statement.file.filename}"'},
    )


@router.get("/companies/{company_id}/bank/linjer", openapi_extra=KEY_READ)
async def list_lines(
    context: CurrentCompany,
    db: DbSession,
    status_filter: Annotated[
        Literal["alle", "uavstemt", "avstemt"], Query(alias="status")
    ] = "alle",
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[LineResponse]:
    """Bank lines, newest first, with suggestions for the ones not yet matched."""
    require_permission(context.membership.role, Permission.LEDGER_READ)
    matched = {"alle": None, "uavstemt": False, "avstemt": True}[status_filter]
    lines = await bank_service.list_lines(
        db, company=context.company, matched=matched, limit=limit, offset=offset
    )
    suggestions = await bank_service.suggest(db, company=context.company, lines=lines)
    reversed_ = await bank_service.reversed_vouchers(
        db, [line.transaction_pk for line in lines if line.transaction_pk is not None]
    )
    invoices = await bank_service.invoices_by_kid(
        db, company=context.company, kids=[line.kid for line in lines if line.kid]
    )
    return [
        _line_response(
            line,
            suggestions=suggestions.get(line.id),
            reversed_vouchers=reversed_,
            invoices=invoices,
        )
        for line in lines
    ]


@router.post(
    "/companies/{company_id}/bank/linjer/{line_id}/avstemming",
    openapi_extra=key_write("koble en banklinje til et bilag"),
)
async def confirm_match(
    line_id: int,
    payload: ConfirmMatchRequest,
    context: CurrentCompany,
    db: DbSession,
    user: CurrentUser,
) -> LineResponse:
    """Confirm that a posted voucher is the booking of this bank line."""
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    line = await bank_service.confirm_match(
        db,
        company=context.company,
        line_pk=line_id,
        voucher_pk=payload.voucher_id,
        user_id=user.id,
    )
    return _line_response(line)


@router.delete(
    "/companies/{company_id}/bank/linjer/{line_id}/avstemming",
    openapi_extra=key_write("fjerne koblingen mellom en banklinje og et bilag"),
)
async def remove_match(line_id: int, context: CurrentCompany, db: DbSession) -> LineResponse:
    """Take the link off again. The voucher is not touched."""
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    line = await bank_service.remove_match(db, company=context.company, line_pk=line_id)
    suggestions = await bank_service.suggest(db, company=context.company, lines=[line])
    return _line_response(line, suggestions=suggestions.get(line.id))


@router.post(
    "/companies/{company_id}/bank/linjer/{line_id}/bilag",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_post("føre et bilag for en banklinje"),
)
async def create_from_line(
    line_id: int,
    payload: CreateFromLineRequest,
    context: CurrentCompany,
    db: DbSession,
    user: CurrentUser,
) -> LineResponse:
    """Post a voucher for a bank line nothing explains, and link the two."""
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    line, _ = await bank_service.create_entry_from_line(
        db,
        company=context.company,
        line_pk=line_id,
        type_code=payload.type_code,
        description=payload.description,
        tax_code=payload.tax_code,
        party_pk=payload.party_id,
        # From the session, never the payload, for the reason record_entry gives.
        created_by_user_id=user.id,
    )
    return _line_response(line)
