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

Entries are posted immediately and are therefore immutable, which is what
bokføringsloven expects and what leaves an auditor able to see both an error and
its correction. Two endpoints act on that: :func:`reverse` for a payment that
should not have been recorded at all, and :func:`edit_entry` for one whose
details were wrong, which reverses and re-posts in a single transaction so the
user gets an edit and the ledger keeps its history.
"""

import asyncio
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status
from pydantic import BaseModel, Field

from myntos.api.deps import CurrentCompany, CurrentUser, DbSession, Storage
from myntos.core.clock import utc_now
from myntos.domain.bilag import classification, extraction, interpretation
from myntos.domain.bilag import service as bilag_service
from myntos.domain.bilag.models import MAX_ATTACHMENT_BYTES, Attachment
from myntos.domain.bilag.text import extract_text
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.ledger import parties as party_service
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.cashflow import (
    CASH_FLOW_TYPES,
    CashFlowDirection,
    CashFlowType,
    types_for,
)
from myntos.domain.ledger.chart import STANDARD_TAX_CODES
from myntos.domain.ledger.models import PartyType
from myntos.domain.ledger.service import CashFlowView, PaymentMethod
from myntos.integrations import loynd, norgesbank

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
    # Set on the voucher a correction posted, naming the entry it replaced. The
    # register folds that entry and its reversal into one line underneath this
    # one, so an edit reads as one event instead of three payments.
    corrects_id: int | None
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
    # deserve the same amount of trust. Kept next to `sources`, which says the
    # same thing for every other field.
    type_source: interpretation.FieldSource | None
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
    # The total as printed, when the document is not in kroner. `gross_amount` is
    # left empty in that case: the ledger is in kroner, and only the bank knows
    # what the account was actually charged.
    foreign_amount: Decimal | None
    # Norges Bank's rate for `currency_code` and the day it is quoted for, set
    # when `gross_amount` was worked out from `foreign_amount` rather than read.
    # Both are shown so the user can redo the multiplication.
    exchange_rate: Decimal | None
    rate_quoted_on: date | None
    filled: list[str]
    # Field name to "dokument", "ki" or "beregnet", for every field in `filled`.
    # The form marks each one with where it came from.
    sources: dict[str, interpretation.FieldSource]
    warnings: list[str]
    # Whether a model was actually consulted for this document. False covers
    # three different situations that used to be indistinguishable from the
    # outside: the company has turned KI off, the deployment has no key, and
    # Løynd could not be reached. The user is entitled to know that the empty
    # category field is not the model's considered opinion.
    ki_used: bool


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


async def _ask_for_category(
    document_text: str, *, allowed: CashFlowDirection | None, title: str | None
) -> CashFlowType | None:
    """Which category Løynd puts the document in, or nothing if it did not say.

    An unavailable model is not an error for the user: the reading we did
    ourselves stands on its own, and a register that stops working because a
    model is down would be a worse trade than never asking.
    """
    try:
        return await classification.classify(
            document_text, ask=loynd.complete, allowed=allowed, title=title
        )
    except loynd.LoyndError:
        return None


async def _ask_for_fields(
    document_text: str, *, today: date, title: str | None
) -> extraction.Extraction:
    """What Løynd reads in the places our own parser came up empty."""
    try:
        return await extraction.extract(document_text, ask=loynd.complete, today=today, title=title)
    except loynd.LoyndError:
        return extraction.Extraction()


@dataclass(frozen=True)
class _Document:
    """An uploaded file as text, however we managed to get the text."""

    text: str
    title: str | None = None
    encrypted: bool = False
    unreadable: bool = False


async def _read_document(content: bytes, *, filename: str, use_loynd: bool) -> _Document:
    """The text of an uploaded PDF, from Løynd when possible and locally when not.

    **Løynd first, on purpose.** Handing the file over rather than a string we
    extracted ourselves is what makes this pipeline improvable without touching
    Myntos again: the extractor on the other side can learn new formats, or
    learn to look at a page instead of reading its text layer, and the only
    change here is that better text starts coming back. Extracting locally and
    sending the result throws the document away before the request is made, and
    no upgrade on the far side can undo that.

    **pypdf second, and it is a real fallback.** A deployment with no key, a
    company that has turned KI off, and a Løynd that is down all land here, and
    all of them get the register they had before: the local text layer, read the
    way it has always been read.

    Neither side runs OCR, so a scanned receipt comes back empty either way.
    That is said plainly to the user rather than dressed up as a failure.
    """
    if use_loynd:
        try:
            document = await loynd.extract_file(content=content, filename=filename)
        except loynd.LoyndError:
            pass
        else:
            if not document.empty:
                return _Document(
                    text=document.text, title=document.title or _from_filename(filename)
                )
            # An empty answer is a real answer about a scanned document, but it
            # is also what a extractor that dislikes this particular file
            # returns, so pypdf still gets its turn before we conclude anything.

    local = await asyncio.to_thread(extract_text, content)
    return _Document(
        text=local.text,
        title=local.title or _from_filename(filename),
        encrypted=local.encrypted,
        unreadable=local.unreadable,
    )


def _from_filename(filename: str) -> str | None:
    """The filename as a title, when the file itself offers none.

    Browsers name a downloaded receipt after the page it came from, so
    "Order details _ eBay.pdf" is not decoration: on that document it is the
    only occurrence of the word "eBay" anywhere, metadata included, once the
    logo at the top turns out to be an image. It is treated exactly like a PDF
    title, which is to say as a hint about who this is from and never as a
    source of an amount or a date.
    """
    stem = bilag_service.sanitise_filename(filename).rsplit(".", maxsplit=1)[0]
    cleaned = " ".join(stem.replace("_", " ").split())
    return cleaned if len(cleaned) >= 3 else None


async def _to_kroner(suggestion: interpretation.InvoiceSuggestion, *, on: date) -> None:
    """Fill the kroner amount from Norges Bank, when the bilag is not in kroner.

    Best-effort by design. A rate that does not arrive leaves the suggestion
    exactly as it was, which is the behaviour every foreign bilag had until now:
    an empty amount field and a sentence telling the user to look it up.
    """
    currency = suggestion.currency_code
    if currency is None or suggestion.foreign_amount is None or suggestion.gross_amount is not None:
        return
    try:
        rate = await norgesbank.rate_for(currency, on=on)
    except norgesbank.RateUnavailableError:
        return
    interpretation.apply_conversion(
        suggestion,
        kroner=rate.to_kroner(suggestion.foreign_amount),
        rate=rate.kroner_per_unit,
        quoted_on=rate.quoted_on,
    )


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
        corrects_id=view.corrects_id,
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

    # Whether a model may see this document at all, decided once and honoured by
    # everything below. A company that has switched KI off gets the parser and
    # nothing else, including no upload to Løynd's extractor: "off" has to mean
    # the document does not leave the building, or it means nothing.
    use_loynd = context.company.ki_enabled and loynd.is_configured()

    extracted = await _read_document(content, filename=file.filename or "", use_loynd=use_loynd)
    parties = await party_service.list_parties(db, company=context.company)
    today = utc_now().date()
    hints = [
        interpretation.PartyHint(
            id=party.id,
            name=party.name,
            orgnr=party.orgnr,
            is_customer=party.party_type is PartyType.KUNDE,
        )
        for party in parties
    ]

    def propose(
        found: interpretation.InvoiceReading, model_fields: frozenset[str] = frozenset()
    ) -> interpretation.InvoiceSuggestion:
        return interpretation.suggest(
            extracted.text,
            today=today,
            own_orgnr=context.company.orgnr,
            own_name=context.company.name,
            reading=found,
            parties=hints,
            model_fields=model_fields,
        )

    # The heuristics are CPU-bound and operate on text from outside; off the
    # event loop they cannot stall other requests.
    reading = await asyncio.to_thread(
        interpretation.read, extracted.text, today=today, title=extracted.title
    )
    suggestion = await asyncio.to_thread(propose, reading)

    # Then the two questions for the model, asked at the same time because the
    # user is watching a spinner: which category this belongs to, and what the
    # document says where our own reading came up empty. The category menu is
    # restricted to the direction when a registered party settled that question,
    # so a recognised customer cannot come back as an expense.
    if use_loynd:
        chosen, model_reading = await asyncio.gather(
            _ask_for_category(
                extracted.text,
                # Always narrowed, not only when a registered party settled it.
                #
                # The direction is a *reading*: interpretation.suggest decides
                # it from whether the document names us as its sender, and
                # everything else is a purchase. The category is the question.
                # Handing the model the full catalogue lets a category answer
                # silently redecide the direction, because apply_type takes the
                # direction from whichever category comes back — and that is not
                # a small error. Løynd files this eBay receipt under "Salg til
                # utlandet" given all 26 choices, which would book a Mac Studio
                # the company bought as export revenue it earned.
                #
                # If the reading is wrong, the user flips the radio and picks
                # again, which is a visible correction. A direction quietly
                # reversed by a category is not.
                allowed=suggestion.direction,
                title=extracted.title,
            ),
            _ask_for_fields(extracted.text, today=today, title=extracted.title),
        )
    else:
        chosen, model_reading = None, extraction.Extraction()

    # What the model read changes what the document is taken to say, so the
    # proposal is built again from the merged reading rather than patched in
    # place: a currency it found decides whether the amount may be proposed at
    # all and whether a VAT code follows, and working that out in a second place
    # is how the two places come to disagree.
    merged, model_fields = extraction.merge(reading, model_reading)
    if model_fields:
        reading = merged
        suggestion = await asyncio.to_thread(propose, reading, model_fields)
    if chosen is not None:
        interpretation.apply_type(
            suggestion,
            reading=reading,
            type_code=chosen.code,
            source=interpretation.FieldSource.MODEL,
        )
    extraction.apply_description(suggestion, model_reading, item_text=reading.item_text)

    # Last, because it needs the final currency: a code the model supplied is
    # what turns a total that looked like kroner into 2 137,00 GBP, and there is
    # nothing to convert until that is settled. The driftsmiddel threshold is
    # then re-tested against whichever amount and category came out of all of
    # the above, since both halves of that test have had a chance to move.
    await _to_kroner(suggestion, on=suggestion.occurred_on or today)
    interpretation.note_capitalisation(suggestion)

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
        foreign_amount=suggestion.foreign_amount,
        exchange_rate=suggestion.exchange_rate,
        rate_quoted_on=suggestion.rate_quoted_on,
        filled=suggestion.filled,
        sources=suggestion.sources,
        warnings=warnings,
        ki_used=use_loynd,
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


@router.put(
    "/companies/{company_id}/cashflow/{voucher_id}",
    status_code=status.HTTP_201_CREATED,
)
async def edit_entry(
    voucher_id: int,
    payload: CreateCashFlowRequest,
    context: CurrentCompany,
    db: DbSession,
) -> CashFlowResponse:
    """Change a registered payment.

    A posted voucher cannot be altered, so this does the only thing that can
    stand in for it: reverses the original and posts the corrected version,
    both inside the request's single transaction. Either all three vouchers
    exist afterwards or none of the new ones do. That atomicity is the reason
    this is an endpoint rather than two calls from the browser, where a dropped
    connection between them would leave a reversed entry and no replacement,
    which is a worse state than the mistake being corrected.

    **Why not simply update the rows.** "25 % mva" is not a value stored on the
    entry; it decides how many lines the entry has. At 27 108,49 kroner the
    deductible version debits 6500 with 21 686,79 and 2710 with 5 421,70, and
    the non-deductible one debits 6500 with the whole sum and does not touch
    2710 at all. Account 2710 is what the MVA-melding aggregates, so that line
    is a claim to Skatteetaten. Editing it away in place would erase a claim
    that may already have been reported, leaving the books disagreeing with the
    filing and nothing in the record to say why. The reversal is what keeps the
    two reconcilable.

    What the user sees is still an edit: they open the entry, change what was
    wrong and save. What the ledger keeps is the original, its reversal and the
    corrected entry, which is what bokføringsloven asks for and what lets an
    auditor follow a number in a report back to a document.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    await ledger_service.reverse_cash_flow(db, company=context.company, voucher_pk=voucher_id)
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
        # What makes the three vouchers readable as one correction rather than
        # as three unrelated payments. Set here and nowhere else: an ordinary
        # entry replaces nothing.
        corrects_pk=voucher_id,
    )
    view = ledger_service.describe(transaction)
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
