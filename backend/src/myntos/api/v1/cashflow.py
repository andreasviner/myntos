"""The cash-flow register: money in, money out, and the bilag behind it.

Three things a user supplies per payment — the amount that moved, the date, and
**what it was for** — and one they may supply, the PDF. Everything else is
derived: the account, the VAT code and rate, the double entry, the bilagsnummer.

The type is not decoration. It decides which account the amount lands on, and
through that account which næringsspesifikasjon post it reaches in the
skattemelding; and it decides the VAT code the MVA-melding aggregates over.
That is why :func:`types` returns the catalogue with its notes attached — the
user has to be able to see the assumption before accepting it.

The PDF can also come first. :func:`suggest_from_document` reads an uploaded
faktura and proposes the three supplied values, so the ordinary case becomes
"upload, check, register" rather than "retype what the invoice already says". It
proposes only what the document states and stores nothing — see
:mod:`myntos.domain.bilag.interpretation`.

Entries are posted immediately and are therefore immutable. A mistake is undone
with :func:`reverse`, which is what bokføringsloven expects and what leaves an
auditor able to see both the error and the correction.
"""

import asyncio
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status
from pydantic import BaseModel, Field

from myntos.api.deps import CurrentCompany, CurrentUser, DbSession, Storage
from myntos.core.clock import utc_now
from myntos.domain.bilag import classification, interpretation
from myntos.domain.bilag import service as bilag_service
from myntos.domain.bilag.models import MAX_ATTACHMENT_BYTES, Attachment
from myntos.domain.bilag.text import extract_text
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.ledger import parties as party_service
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.cashflow import CASH_FLOW_TYPES, CashFlowDirection, types_for
from myntos.domain.ledger.chart import STANDARD_TAX_CODES
from myntos.domain.ledger.models import PartyType
from myntos.domain.ledger.service import CashFlowView, PaymentMethod
from myntos.integrations import loynd

router = APIRouter(tags=["kontantstrøm"])


class CashFlowTypeResponse(BaseModel):
    code: str
    label: str
    direction: CashFlowDirection
    account_number: str
    default_tax_code: str
    note: str


class TaxCodeResponse(BaseModel):
    code: str
    description: str
    # A percentage for display; the ledger keeps the fraction.
    rate_percent: Decimal


class CatalogueResponse(BaseModel):
    """Everything the entry form needs to render itself."""

    types: list[CashFlowTypeResponse]
    tax_codes: list[TaxCodeResponse]
    max_attachment_bytes: int


class AttachmentResponse(BaseModel):
    id: int
    filename: str
    byte_size: int
    sha256: str


class CashFlowResponse(BaseModel):
    id: int
    voucher_no: str
    occurred_on: date
    direction: CashFlowDirection
    type_code: str | None
    type_label: str | None
    gross_amount: Decimal
    net_amount: Decimal
    vat_amount: Decimal
    account_number: str
    account_description: str
    tax_code: str | None
    party_id: int | None
    party_name: str | None
    payment_account: str
    description: str
    is_reversal: bool
    # Set when this entry has been reversed; the UI greys it out rather than
    # hiding it, because the reversed entry is still part of the record.
    reversed_by_id: int | None
    attachments: list[AttachmentResponse]


class TypeTotalResponse(BaseModel):
    type_code: str | None
    type_label: str | None
    direction: CashFlowDirection
    total: Decimal
    count: int


class SummaryResponse(BaseModel):
    financial_year: int
    total_in: Decimal
    total_out: Decimal
    net: Decimal
    entry_count: int
    output_vat: Decimal
    input_vat: Decimal
    # Positive means owed to Skatteetaten, negative means owed to you. An
    # estimate from the register alone — it is not an MVA-melding.
    vat_position: Decimal
    cash_position: Decimal
    by_type: list[TypeTotalResponse]


class SuggestionResponse(BaseModel):
    """What an uploaded document appears to say, as form values.

    Every field except ``direction`` may be ``None``: a proposal is only made
    where the document actually says something. ``filled`` names the fields that
    came from the document so the UI can mark them, and ``warnings`` carries the
    sentences the user needs in order to check them.
    """

    direction: CashFlowDirection
    type_code: str | None
    type_label: str | None
    # "dokument" when the words in the file gave it away, "ki" when Løynd chose
    # it from the catalogue. The interface says which, because the two do not
    # deserve the same amount of trust.
    type_source: interpretation.TypeSource | None
    tax_code: str | None
    occurred_on: date | None
    gross_amount: Decimal | None
    # For display only. The ledger computes the split itself when posting.
    vat_amount: Decimal | None
    description: str | None
    party_id: int | None
    counterparty_name: str | None
    counterparty_orgnr: str | None
    invoice_number: str | None
    currency_code: str | None
    filled: list[str]
    warnings: list[str]


class CreateCashFlowRequest(BaseModel):
    type_code: str = Field(min_length=1, max_length=64)
    occurred_on: date
    # Gross: what left or entered the account, VAT included.
    gross_amount: Decimal = Field(gt=0)
    description: str = Field(min_length=1, max_length=255)
    # Overrides the type's default. The defaults are assumptions, not verdicts
    # — see the notes in myntos.domain.ledger.cashflow.
    tax_code: str | None = Field(default=None, max_length=32)
    payment_method: PaymentMethod = PaymentMethod.BANK
    # Optional: plenty of payments have no counterparty worth registering, and
    # demanding one makes people invent placeholder suppliers.
    party_id: int | None = None


def _label_for(type_code: str | None) -> str | None:
    entry_type = CASH_FLOW_TYPES.get(type_code) if type_code is not None else None
    return entry_type.label if entry_type is not None else None


def _attachment_response(attachment: Attachment) -> AttachmentResponse:
    return AttachmentResponse(
        id=attachment.id,
        filename=attachment.filename,
        byte_size=attachment.byte_size,
        sha256=attachment.sha256,
    )


def _cash_flow_response(
    view: CashFlowView, attachments: list[Attachment] | None = None
) -> CashFlowResponse:
    return CashFlowResponse(
        id=view.id,
        voucher_no=view.voucher_no,
        occurred_on=view.occurred_on,
        direction=view.direction,
        type_code=view.type_code,
        type_label=_label_for(view.type_code),
        gross_amount=view.gross_amount,
        net_amount=view.net_amount,
        vat_amount=view.vat_amount,
        account_number=view.account_number,
        account_description=view.account_description,
        tax_code=view.tax_code,
        party_id=view.party_id,
        party_name=view.party_name,
        payment_account=view.payment_account,
        description=view.description,
        is_reversal=view.is_reversal,
        reversed_by_id=view.reversed_by_id,
        attachments=[_attachment_response(a) for a in attachments or []],
    )


@router.get("/cashflow/types")
async def catalogue(user: CurrentUser) -> CatalogueResponse:
    """The selectable categories and VAT codes.

    Requires a session but no company: the catalogue is the same for everyone,
    and the signup flow needs it before the first company exists.
    """
    del user
    return CatalogueResponse(
        types=[
            CashFlowTypeResponse(
                code=entry.code,
                label=entry.label,
                direction=entry.direction,
                account_number=entry.account_number,
                default_tax_code=entry.default_tax_code,
                note=entry.note,
            )
            for entry in types_for()
        ],
        tax_codes=[
            TaxCodeResponse(
                code=seed.code,
                description=seed.description,
                rate_percent=seed.rate * 100,
            )
            for seed in STANDARD_TAX_CODES
        ],
        max_attachment_bytes=MAX_ATTACHMENT_BYTES,
    )


@router.post("/companies/{company_id}/cashflow/forslag")
async def suggest_from_document(
    context: CurrentCompany,
    db: DbSession,
    file: Annotated[UploadFile, File()],
) -> SuggestionResponse:
    """Read an uploaded faktura and propose what the entry form should hold.

    Nothing is posted and nothing is stored: the bytes are read, interpreted and
    dropped. The document is archived only when the user goes on to register the
    payment, which is the point at which there is a voucher for it to belong to
    — so a user who uploads the wrong file, or changes their mind, leaves nothing
    behind in an archive that has no delete path.

    It still needs ``LEDGER_WRITE``. Reading a document is not a change to the
    books, but it is a step in making one, and a viewer has no business feeding
    files to the register.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    content = await file.read()
    # The same validation the archive applies, so a file that could never be
    # attached is refused here rather than after the user has confirmed a form.
    bilag_service.validate_pdf(content)

    # Both the parsing and the heuristics are CPU-bound and operate on bytes
    # from outside; off the event loop they cannot stall other requests.
    extracted = await asyncio.to_thread(extract_text, content)
    parties = await party_service.list_parties(db, company=context.company)
    reading = await asyncio.to_thread(interpretation.read, extracted.text, today=utc_now().date())
    suggestion = await asyncio.to_thread(
        interpretation.suggest,
        extracted.text,
        today=utc_now().date(),
        own_orgnr=context.company.orgnr,
        own_name=context.company.name,
        reading=reading,
        parties=[
            interpretation.PartyHint(
                id=party.id,
                name=party.name,
                orgnr=party.orgnr,
                is_customer=party.party_type is PartyType.KUNDE,
            )
            for party in parties
        ],
    )

    # Then the category, which is the one thing the document does not state and
    # the one thing a model is better at than a keyword list. Restricted to the
    # direction when a registered party settled that question, so a recognised
    # customer cannot come back as an expense.
    #
    # No key or no answer is not an error for the user: the reading above already
    # stands on its own, and a register that stops working because a model is
    # down would be a worse trade than never asking.
    chosen = None
    if loynd.is_configured():
        try:
            chosen = await classification.classify(
                extracted.text,
                ask=loynd.complete,
                allowed=suggestion.direction if suggestion.party_id is not None else None,
            )
        except loynd.LoyndError:
            chosen = None
    if chosen is not None:
        interpretation.apply_type(
            suggestion,
            reading=reading,
            type_code=chosen.code,
            source=interpretation.TypeSource.MODEL,
        )

    warnings = list(suggestion.warnings)
    if extracted.encrypted:
        warnings.insert(0, "PDF-en er passordbeskyttet, så vi fikk ikke lest innholdet.")
    elif extracted.unreadable:
        warnings.insert(0, "Vi klarte ikke å lese PDF-en. Den kan være skadet.")

    return SuggestionResponse(
        direction=suggestion.direction,
        type_code=suggestion.type_code,
        type_label=_label_for(suggestion.type_code),
        type_source=suggestion.type_source,
        tax_code=suggestion.tax_code,
        occurred_on=suggestion.occurred_on,
        gross_amount=suggestion.gross_amount,
        vat_amount=suggestion.vat_amount,
        description=suggestion.description,
        party_id=suggestion.party_id,
        counterparty_name=suggestion.counterparty_name,
        counterparty_orgnr=suggestion.counterparty_orgnr,
        invoice_number=suggestion.invoice_number,
        currency_code=suggestion.currency_code,
        filled=suggestion.filled,
        warnings=warnings,
    )


@router.get("/companies/{company_id}/cashflow")
async def list_entries(
    context: CurrentCompany,
    db: DbSession,
    financial_year: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[CashFlowResponse]:
    require_permission(context.membership.role, Permission.LEDGER_READ)
    views = await ledger_service.list_cash_flows(
        db,
        company=context.company,
        financial_year=financial_year,
        limit=limit,
        offset=offset,
    )
    attachments = await bilag_service.list_attachments(
        db, company=context.company, voucher_pks=[view.id for view in views]
    )
    return [_cash_flow_response(view, attachments.get(view.id)) for view in views]


@router.post("/companies/{company_id}/cashflow", status_code=status.HTTP_201_CREATED)
async def record_entry(
    payload: CreateCashFlowRequest, context: CurrentCompany, db: DbSession
) -> CashFlowResponse:
    """Register one payment in or out."""
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    transaction = await ledger_service.record_cash_flow(
        db,
        company=context.company,
        type_code=payload.type_code,
        occurred_on=payload.occurred_on,
        gross_amount=payload.gross_amount,
        description=payload.description,
        tax_code=payload.tax_code,
        payment_method=payload.payment_method,
        party_pk=payload.party_id,
    )
    view = ledger_service.describe(transaction)
    # describe() only returns None for a voucher with no payment line, which
    # the function above cannot produce.
    assert view is not None  # noqa: S101
    return _cash_flow_response(view)


@router.get("/companies/{company_id}/cashflow/summary")
async def summary(
    context: CurrentCompany, db: DbSession, financial_year: int | None = None
) -> SummaryResponse:
    """Totals for one financial year, for the overview on the front page."""
    require_permission(context.membership.role, Permission.LEDGER_READ)
    year = financial_year if financial_year is not None else utc_now().year
    totals = await ledger_service.summarise(db, company=context.company, financial_year=year)
    return SummaryResponse(
        financial_year=totals.financial_year,
        total_in=totals.total_in,
        total_out=totals.total_out,
        net=totals.net,
        entry_count=totals.entry_count,
        output_vat=totals.output_vat,
        input_vat=totals.input_vat,
        vat_position=totals.vat_position,
        cash_position=totals.cash_position,
        by_type=[
            TypeTotalResponse(
                type_code=item.type_code,
                type_label=_label_for(item.type_code),
                direction=item.direction,
                total=item.total,
                count=item.count,
            )
            for item in totals.by_type
        ],
    )


@router.post(
    "/companies/{company_id}/cashflow/{voucher_id}/reversal",
    status_code=status.HTTP_201_CREATED,
)
async def reverse(voucher_id: int, context: CurrentCompany, db: DbSession) -> CashFlowResponse:
    """Undo an entry by posting its mirror image.

    There is no edit and no delete. A posted entry is history, and the record
    has to show that a correction happened rather than look as though the
    mistake never did.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    reversal = await ledger_service.reverse_cash_flow(
        db, company=context.company, voucher_pk=voucher_id
    )
    view = ledger_service.describe(reversal)
    assert view is not None  # noqa: S101
    return _cash_flow_response(view)


@router.post(
    "/companies/{company_id}/cashflow/{voucher_id}/dokument",
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    voucher_id: int,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    storage: Storage,
    file: Annotated[UploadFile, File()],
    # Multipart, so the filename normally arrives in the part header. Accepted
    # as a field too because some clients send an empty one.
    filename: Annotated[str | None, Form()] = None,
) -> AttachmentResponse:
    """Attach a PDF to an entry.

    The archive is append-only: there is no endpoint to remove a document, on
    purpose. Bokføringsloven requires primary documentation to stay available
    for five years after the financial year ends, and a delete button is how
    that requirement quietly stops being met.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    transaction = await ledger_service.get_voucher(
        db, company=context.company, voucher_pk=voucher_id
    )
    content = await file.read()
    attachment = await bilag_service.attach_document(
        db,
        storage,
        company=context.company,
        transaction=transaction,
        content=content,
        filename=filename or file.filename or "bilag.pdf",
        uploaded_by_user_id=user.id,
    )
    return _attachment_response(attachment)


@router.get("/companies/{company_id}/dokument/{attachment_id}")
async def download_document(
    attachment_id: int, context: CurrentCompany, db: DbSession, storage: Storage
) -> Response:
    """Return the stored PDF.

    ``Content-Disposition: attachment`` rather than ``inline``: the bytes came
    from a user, and a PDF rendered in the site's own origin is a scripting
    surface we have no reason to open. The filename was sanitised on the way in
    (:func:`myntos.domain.bilag.service.sanitise_filename`), so it cannot break
    out of the header.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    attachment = await bilag_service.get_attachment(
        db, company=context.company, attachment_id=attachment_id
    )
    content = await storage.load(attachment.storage_key)
    return Response(
        content=content,
        media_type=attachment.content_type,
        headers={"Content-Disposition": f'attachment; filename="{attachment.filename}"'},
    )
