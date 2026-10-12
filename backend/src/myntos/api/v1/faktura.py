"""Fakturering — writing, issuing and crediting a salgsdokument.

The shape of this API follows the document's lifecycle rather than CRUD, because
the document's lifecycle is what the law describes. A draft can be written,
changed and thrown away freely. Issuing is one irreversible call. After it there
is no PATCH and no DELETE, only ``/kreditnota`` — which is not an API design
preference but bokføringsforskriften § 5-2-7.

``GET /faktura/{id}/kontroll`` exists so the interface can show what still stands
between a draft and a lawful invoice *before* the user commits. Issuing checks the
same rules and refuses; the difference is that this one is safe to call on every
keystroke and answers with the list rather than an error.

``POST /faktura/linjeforslag`` is the one route here that talks to a model. It
stores nothing and posts nothing: it turns a sentence about a sale into proposed
lines the user then edits, saves and issues by hand like any other. It answers
200 with an empty list when no model is configured or none answers, because a
form that cannot be filled in by hand when the model is down would be a worse
trade than never asking.

The two download routes serve the two documents this produces. ``/ehf`` is the
e-invoice — the legally required format for a sale to another bokføringspliktig
from 1 January 2027 — served exactly as it was frozen at issue. ``/pdf`` is the
readable copy, which is also filed in the bilag archive so it survives
independently of this endpoint.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, Field

from myntos.api.deps import (
    KEY_PREVIEW,
    KEY_READ,
    CurrentCompany,
    CurrentUser,
    DbSession,
    Storage,
    key_post,
    key_write,
)
from myntos.core.clock import utc_now
from myntos.domain.faktura import compliance, drafting, kid
from myntos.domain.faktura import pdf as faktura_pdf
from myntos.domain.faktura import service as faktura_service
from myntos.domain.faktura.catalogue import SALES_TAX_OPTIONS, UNITS
from myntos.domain.faktura.errors import EhfNotAvailableError
from myntos.domain.faktura.models import (
    DEFAULT_PAYMENT_TERM_DAYS,
    DEFAULT_UNIT_CODE,
    DocumentType,
    InvoiceStatus,
    SalesDocument,
    SalesDocumentLine,
)
from myntos.domain.faktura.service import LineInput
from myntos.domain.faktura.totals import totals
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.ledger import parties as party_service
from myntos.domain.ledger.chart import TAX_CODES_BY_CODE
from myntos.integrations import loynd

router = APIRouter(tags=["faktura"])


# --- Catalogue ---------------------------------------------------------------------


class SalesTaxOptionResponse(BaseModel):
    tax_code: str
    label: str
    income_account: str
    note: str
    # The rate as a percentage, for display. Resolved from the company's own
    # date-effective table rather than from a constant.
    rate_percent: Decimal


class UnitResponse(BaseModel):
    code: str
    label: str


class FakturaCatalogueResponse(BaseModel):
    """Everything the invoice form needs to render itself."""

    tax_options: list[SalesTaxOptionResponse]
    units: list[UnitResponse]
    default_unit: str
    default_payment_term_days: int
    # When e-invoicing to a business customer stops being advisory. The interface
    # shows the date rather than hardcoding it in a sentence.
    e_invoice_mandatory_from: date
    # Whether this deployment has a model configured at all. Answered here so the
    # form can leave the box out entirely rather than offer a button that always
    # fails — a deployment without LOYND_KEY has opted out, not broken.
    ai_available: bool
    # The longest description /linjeforslag accepts, so the textarea and the
    # endpoint cannot disagree about it.
    ai_max_chars: int


# --- Documents ---------------------------------------------------------------------


class LinePayload(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal
    unit_price: Decimal = Field(ge=0)
    tax_code: str = Field(min_length=1, max_length=32)
    unit_code: str = Field(default=DEFAULT_UNIT_CODE, max_length=8)
    # A percentage on the wire, a fraction in the ledger — converted here so the
    # form can speak the language the user types in.
    discount_percent: Decimal = Field(default=Decimal(0), ge=0, lt=100)

    def to_input(self) -> LineInput:
        return LineInput(
            description=self.description,
            quantity=self.quantity,
            unit_price=self.unit_price,
            tax_code=self.tax_code,
            unit_code=self.unit_code,
            discount_rate=self.discount_percent / 100,
        )


class LineResponse(BaseModel):
    line_no: int
    description: str
    quantity: Decimal
    unit_code: str
    unit_price: Decimal
    discount_percent: Decimal
    tax_code: str
    tax_rate_percent: Decimal
    net_amount: Decimal
    vat_amount: Decimal


class TaxSubtotalResponse(BaseModel):
    """One § 5-1-5 group: a category, its rate, and what falls under it."""

    category: str
    rate_percent: Decimal
    reason: str | None
    taxable_amount: Decimal
    tax_amount: Decimal


class ViolationResponse(BaseModel):
    rule: str
    field: str
    message: str
    severity: compliance.Severity


class FakturaResponse(BaseModel):
    id: int
    document_type: DocumentType
    status: InvoiceStatus
    document_number: str | None

    party_id: int | None
    buyer_name: str
    buyer_orgnr: str | None
    buyer_address_line: str | None
    buyer_postal_code: str | None
    buyer_city: str | None
    buyer_is_business: bool

    seller_name: str
    seller_orgnr: str
    seller_vat_registered: bool
    seller_in_foretaksregisteret: bool
    seller_bank_account: str | None
    # Which of the company's accounts this document is paid into. Null means
    # "whichever is primary" — the answer for every company with one account,
    # and for a draft nobody has picked for. The number above is what the
    # document says; this is the choice it says it from.
    bank_account_id: int | None

    issue_date: date | None
    delivery_date: date | None
    delivery_place: str | None
    due_date: date | None
    currency_code: str
    buyer_reference: str | None
    order_reference: str | None
    payment_reference: str | None
    # Whether issue assigns the KID. On a draft that is the whole answer, since
    # payment_reference is still null; on an issued document the reference above
    # is the fact and this only records where it came from.
    payment_reference_auto: bool
    note: str | None
    reverse_charge: bool

    lines: list[LineResponse]
    subtotals: list[TaxSubtotalResponse]
    net_total: Decimal
    vat_total: Decimal
    gross_total: Decimal

    # The markings the document itself carries — "Foretaksregisteret", the
    # omvendt-avgiftsplikt sentence. Returned so the interface shows the same
    # text the PDF and the XML do.
    markings: list[str]
    # Everything still unmet. Empty on an issued document by construction.
    violations: list[ViolationResponse]

    voucher_id: int | None
    credits_document_id: int | None
    credited_by_document_id: int | None
    has_ehf: bool


class CreateFakturaRequest(BaseModel):
    # Either a registered customer, or a name and address typed in. A one-off
    # consumer sale has no business in the party register.
    party_id: int | None = None
    buyer_name: str | None = Field(default=None, max_length=255)
    buyer_orgnr: str | None = Field(default=None, max_length=11)
    buyer_address_line: str | None = Field(default=None, max_length=255)
    buyer_postal_code: str | None = Field(default=None, max_length=16)
    buyer_city: str | None = Field(default=None, max_length=128)
    # Asked, not inferred: an orgnr proves a business, but its absence does not
    # prove a consumer — and the answer decides whether the e-invoice
    # requirement applies to this document at all.
    buyer_is_business: bool = True

    delivery_date: date | None = None
    delivery_place: str | None = Field(default=None, max_length=255)
    due_date: date | None = None
    buyer_reference: str | None = Field(default=None, max_length=128)
    order_reference: str | None = Field(default=None, max_length=128)
    payment_reference: str | None = Field(default=None, max_length=32)
    # Default on: almost nobody wants to invent a payment reference, and one
    # derived from the invoice number is what makes a payment reconcilable.
    payment_reference_auto: bool = True
    # Which account this invoice is paid into. Omitted means the company's
    # primary, which is what a company with one account always wants and what
    # the form leaves alone until there is a second one to choose between.
    bank_account_id: int | None = None
    note: str | None = Field(default=None, max_length=1000)
    reverse_charge: bool = False
    lines: list[LinePayload] = Field(default_factory=list)


class UpdateFakturaRequest(BaseModel):
    """The same fields as on create, but every one is optional.

    Written out rather than inherited from :class:`CreateFakturaRequest`: three
    fields have to change type from ``bool``/``list`` to their nullable form, and
    overriding a parent's field type in Pydantic is the kind of subtlety that
    reads as a mistake even when it works.

    ``None`` means "leave it alone" throughout — including ``lines``, where
    sending ``[]`` deliberately clears them and omitting the key does not.
    """

    party_id: int | None = None
    buyer_name: str | None = Field(default=None, max_length=255)
    buyer_orgnr: str | None = Field(default=None, max_length=11)
    buyer_address_line: str | None = Field(default=None, max_length=255)
    buyer_postal_code: str | None = Field(default=None, max_length=16)
    buyer_city: str | None = Field(default=None, max_length=128)
    buyer_is_business: bool | None = None
    delivery_date: date | None = None
    delivery_place: str | None = Field(default=None, max_length=255)
    due_date: date | None = None
    buyer_reference: str | None = Field(default=None, max_length=128)
    order_reference: str | None = Field(default=None, max_length=128)
    # An empty string is the way to clear a KID: ``None`` means "leave it
    # alone" everywhere in this model, which would otherwise make a payment
    # reference impossible to remove once set.
    payment_reference: str | None = Field(default=None, max_length=32)
    payment_reference_auto: bool | None = None
    bank_account_id: int | None = None
    note: str | None = Field(default=None, max_length=1000)
    reverse_charge: bool | None = None
    lines: list[LinePayload] | None = None


class CreditNoteRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class NextNumberResponse(BaseModel):
    """What the next issued invoice would be numbered, and its KID.

    A preview, not a reservation — see :func:`next_number`.
    """

    document_number: str
    payment_reference: str
    # The customer number that went into it, so the form can explain the KID's
    # first five digits rather than leave them looking like padding.
    customer_number: str | None


class LineDraftRequest(BaseModel):
    """A sale described in the seller's own words."""

    text: str = Field(min_length=1, max_length=drafting.MAX_REQUEST_CHARS)


class DraftedLineResponse(BaseModel):
    """One proposed line, in exactly the shape :class:`LinePayload` takes back."""

    description: str
    quantity: Decimal
    unit_code: str
    unit_price: Decimal
    tax_code: str


class LineDraftResponse(BaseModel):
    """Proposed lines, and what the user needs to know before trusting them.

    ``lines`` is empty whenever nothing usable came back — no key, no answer, or
    an answer we could not read. That is a 200 and not an error: the form works
    without it, and ``warnings`` says which of the three happened.
    """

    lines: list[DraftedLineResponse]
    warnings: list[str]


# --- Mapping ------------------------------------------------------------------------


def _line_response(line: SalesDocumentLine) -> LineResponse:
    return LineResponse(
        line_no=line.line_no,
        description=line.description,
        quantity=line.quantity,
        unit_code=line.unit_code,
        unit_price=line.unit_price,
        discount_percent=line.discount_rate * 100,
        tax_code=line.tax_code,
        tax_rate_percent=line.tax_rate * 100,
        net_amount=line.net_amount,
        vat_amount=line.vat_amount,
    )


def _faktura_response(
    document: SalesDocument,
    *,
    credited_by: int | None = None,
    today: date | None = None,
) -> FakturaResponse:
    computed = totals(document)
    violations = (
        [] if document.is_issued else compliance.check(document, today=today or utc_now().date())
    )
    return FakturaResponse(
        id=document.id,
        document_type=document.document_type,
        status=document.status,
        document_number=document.document_number,
        party_id=document.party_id,
        buyer_name=document.buyer_name,
        buyer_orgnr=document.buyer_orgnr,
        buyer_address_line=document.buyer_address_line,
        buyer_postal_code=document.buyer_postal_code,
        buyer_city=document.buyer_city,
        buyer_is_business=document.buyer_is_business,
        seller_name=document.seller_name,
        seller_orgnr=document.seller_orgnr,
        seller_vat_registered=document.seller_vat_registered,
        seller_in_foretaksregisteret=document.seller_in_foretaksregisteret,
        seller_bank_account=document.seller_bank_account,
        bank_account_id=document.bank_account_id,
        issue_date=document.issue_date,
        delivery_date=document.delivery_date,
        delivery_place=document.delivery_place,
        due_date=document.due_date,
        currency_code=document.currency_code,
        buyer_reference=document.buyer_reference,
        order_reference=document.order_reference,
        payment_reference=document.payment_reference,
        payment_reference_auto=document.payment_reference_auto,
        note=document.note,
        reverse_charge=document.reverse_charge,
        lines=[_line_response(line) for line in document.lines],
        subtotals=[
            TaxSubtotalResponse(
                category=subtotal.category.value,
                rate_percent=subtotal.percent,
                reason=subtotal.reason,
                taxable_amount=subtotal.taxable_amount,
                tax_amount=subtotal.tax_amount,
            )
            for subtotal in computed.subtotals
        ],
        net_total=computed.tax_exclusive,
        vat_total=computed.tax_total,
        gross_total=computed.payable,
        markings=compliance.describe_markings(document),
        violations=[
            ViolationResponse(rule=v.rule, field=v.field, message=v.message, severity=v.severity)
            for v in violations
        ],
        voucher_id=document.transaction_pk,
        credits_document_id=document.credits_document_pk,
        credited_by_document_id=credited_by,
        has_ehf=document.ehf_xml is not None,
    )


# --- Endpoints -----------------------------------------------------------------------


@router.get("/faktura/katalog", openapi_extra=KEY_READ)
async def catalogue(user: CurrentUser) -> FakturaCatalogueResponse:
    """The VAT treatments and units an invoice line may use.

    Requires a session but no company: the catalogue is the same for everyone,
    and the rates shown here are the seeded ones. The rate that ends up on a line
    is resolved per company and per delivery date when the line is saved.
    """
    del user
    return FakturaCatalogueResponse(
        tax_options=[
            SalesTaxOptionResponse(
                tax_code=option.tax_code,
                label=option.label,
                income_account=option.income_account,
                note=option.note,
                rate_percent=TAX_CODES_BY_CODE[option.tax_code].rate * 100,
            )
            for option in SALES_TAX_OPTIONS
        ],
        units=[UnitResponse(code=unit.code, label=unit.label) for unit in UNITS],
        default_unit=DEFAULT_UNIT_CODE,
        default_payment_term_days=DEFAULT_PAYMENT_TERM_DAYS,
        e_invoice_mandatory_from=compliance.E_INVOICE_MANDATORY_FROM,
        # A deployment-level fact only: this endpoint has no company, and the
        # company's own KI switch is checked where it matters, in draft_lines
        # below. The page consults both before showing the box.
        ai_available=loynd.is_configured(),
        ai_max_chars=drafting.MAX_REQUEST_CHARS,
    )


@router.get("/companies/{company_id}/faktura", openapi_extra=KEY_READ)
async def list_faktura(
    context: CurrentCompany,
    db: DbSession,
    # Aliased because ``status`` is taken by fastapi.status, imported here for
    # the response codes. Annotated rather than a plain default so the Query()
    # call is metadata rather than an argument default.
    status_filter: Annotated[InvoiceStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[FakturaResponse]:
    """The company's invoice book: drafts, issued invoices and credit notes, newest first.

    A *faktura* is a sales invoice and a *kreditnota* the credit note that
    reverses one; both are in this one list and share one number series. Each
    entry is the whole document: buyer, lines, VAT subtotals and totals (amounts
    as decimal strings), and for a draft also ``violations``, what still stands
    between it and a lawful invoice — the same findings as ``…/kontroll``. An
    issued invoice that has been credited names its credit note in
    ``credited_by_document_id``.

    ``status=utkast`` lists only drafts: no number, not in the books, free to
    change or discard. ``status=utstedt`` lists only issued documents: numbered,
    posted, and final. ``limit`` (1 to 500, default 100) and ``offset`` page
    through the rest.

    Any role that may read the books may call it. Only this company's documents
    are ever listed.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    documents = await faktura_service.list_documents(
        db, company=context.company, status=status_filter, limit=limit, offset=offset
    )
    credited = await faktura_service.credited_by(db, [d.id for d in documents])
    return [
        _faktura_response(document, credited_by=credited.get(document.id)) for document in documents
    ]


@router.post(
    "/companies/{company_id}/faktura",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_write("lage et utkast til faktura"),
)
async def create_faktura(
    payload: CreateFakturaRequest, context: CurrentCompany, db: DbSession
) -> FakturaResponse:
    """Start a draft. Nothing is numbered, posted or sent until it is issued."""
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    document = await faktura_service.create_draft(
        db,
        company=context.company,
        party_pk=payload.party_id,
        buyer_name=payload.buyer_name,
        buyer_orgnr=payload.buyer_orgnr,
        buyer_address_line=payload.buyer_address_line,
        buyer_postal_code=payload.buyer_postal_code,
        buyer_city=payload.buyer_city,
        buyer_is_business=payload.buyer_is_business,
        delivery_date=payload.delivery_date,
        delivery_place=payload.delivery_place,
        due_date=payload.due_date,
        buyer_reference=payload.buyer_reference,
        order_reference=payload.order_reference,
        payment_reference=payload.payment_reference,
        payment_reference_auto=payload.payment_reference_auto,
        bank_account_pk=payload.bank_account_id,
        note=payload.note,
        reverse_charge=payload.reverse_charge,
        lines=[line.to_input() for line in payload.lines],
    )
    return _faktura_response(document)


@router.get("/companies/{company_id}/faktura/neste-nummer", openapi_extra=KEY_READ)
async def next_number(
    context: CurrentCompany,
    db: DbSession,
    party_id: Annotated[int | None, Query()] = None,
) -> NextNumberResponse:
    """The number and KID the next issued invoice would get.

    Registered **before** ``/faktura/{document_id}``, and that is load-bearing
    rather than tidy: the path parameter there matches any segment and is only
    then validated as an ``int``, so a route declared after it would answer 422
    for this URL instead of running.

    ``party_id`` is the customer the invoice is going to, because the KID carries
    their customer number as well as the invoice number — so the preview changes
    when the customer does. Omitted for a buyer typed straight onto the document,
    which is the case that gets zeros in that field.

    It reserves nothing. Nothing is taken from the number series and nothing is
    written, so an invoice issued between this call and the next issue moves the
    number on — which is why the form labels what it shows as a preview rather
    than printing it as settled. Reserving instead would mean a draft that was
    never issued had taken a number out of the series, and a gap in the series is
    what an auditor reads as a deleted invoice.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    customer_number: str | None = None
    if party_id is not None:
        # Scoped to the company by get_party, so another tenant's id is simply
        # not found rather than quietly ending up in someone's payment reference.
        party = await party_service.get_party(db, company=context.company, party_pk=party_id)
        customer_number = party.party_id
    upcoming = await faktura_service.peek_next_number(db, company=context.company)
    return NextNumberResponse(
        document_number=str(upcoming),
        payment_reference=kid.preview_for_next(upcoming, customer_number=customer_number),
        customer_number=customer_number,
    )


@router.post("/companies/{company_id}/faktura/linjeforslag", openapi_extra=KEY_PREVIEW)
async def draft_faktura_lines(
    payload: LineDraftRequest, context: CurrentCompany
) -> LineDraftResponse:
    """Turn a description of a sale into proposed invoice lines.

    Registered above the ``/{document_id}`` routes so the literal segment is the
    obvious reading of the path. Nothing depends on the order — those are GET and
    PATCH and this is a POST — but a route that only works because of a framework
    detail is one that stops working when the detail changes.

    Stores nothing and posts nothing. The proposal goes into the form, where the
    user edits it, saves it as a draft and issues it exactly as they would a line
    they typed — the model's answer never reaches the ledger without passing
    through a person and through § 5-1.

    ``LEDGER_WRITE`` rather than read: nothing changes here, but this is a step
    in writing an invoice, and a viewer has nothing to draft.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    # The company's own switch first, then the deployment's key. Turning KI off
    # for a set of books has to cover both places it is used, or it is a setting
    # that means one thing on one page and something else on another.
    if not context.company.ki_enabled:
        return LineDraftResponse(
            lines=[],
            warnings=["KI er slått av for dette selskapet, så linjene må skrives for hånd."],
        )
    if not loynd.is_configured():
        return LineDraftResponse(
            lines=[],
            warnings=[
                "Denne installasjonen har ikke satt opp Løynd, så linjene må skrives for hånd."
            ],
        )

    # A company outside Merverdiavgiftsregisteret may not charge output VAT
    # (merverdiavgiftsloven § 15-14), so the taxable options are not on the menu
    # at all. Making the wrong answer unavailable beats catching it afterwards.
    allowed = None if context.company.vat_registered else ("0",)
    try:
        proposal = await drafting.draft_lines(
            payload.text, ask=loynd.complete, allowed_tax_codes=allowed
        )
    except loynd.LoyndError:
        # Not an error for the user: the form is untouched and still theirs to
        # fill in. See myntos.integrations.loynd — every call is optional.
        return LineDraftResponse(
            lines=[],
            warnings=["Vi fikk ikke svar fra Løynd nå. Skriv linjene selv, eller prøv igjen."],
        )

    return LineDraftResponse(
        lines=[
            DraftedLineResponse(
                description=line.description,
                quantity=line.quantity,
                unit_code=line.unit_code,
                unit_price=line.unit_price,
                tax_code=line.tax_code,
            )
            for line in proposal.lines
        ],
        warnings=proposal.warnings,
    )


@router.get("/companies/{company_id}/faktura/{document_id}", openapi_extra=KEY_READ)
async def get_faktura(document_id: int, context: CurrentCompany, db: DbSession) -> FakturaResponse:
    """One invoice or credit note, with its lines, VAT subtotals and totals.

    ``status`` says where the document is in its life. ``utkast`` is a draft: it
    has no number, is not in the books, and may be changed with PATCH or
    discarded with DELETE. ``utstedt`` is issued: numbered, posted to the
    ledger, and never changed again, because Norwegian bookkeeping rules
    (bokføringsforskriften § 5-2-7) allow an issued invoice to be corrected only
    by a kreditnota, ``POST …/kreditnota``.

    For a draft, ``violations`` lists what the rules for a sales document
    (bokføringsforskriften § 5-1) still find missing, each with a ``severity``:
    ``blokkerende`` stops it being issued, ``advarsel`` is only worth knowing.
    ``seller_bank_account`` is the kontonummer (bank account number) the
    invoice tells the customer to pay into, taken from the company's own
    accounts; null means the company has none, and then nothing can be issued.
    ``payment_reference`` is the KID, the Norwegian payment reference, which
    issuing sets when ``payment_reference_auto`` is true. Amounts are decimal
    strings.

    404 ``invoice_not_found`` when the id is not one of this company's
    documents: list them with ``GET …/faktura`` to find the right one.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    document = await faktura_service.get_document(
        db, company=context.company, document_pk=document_id
    )
    credited = await faktura_service.credited_by(db, [document.id])
    return _faktura_response(document, credited_by=credited.get(document.id))


@router.patch(
    "/companies/{company_id}/faktura/{document_id}",
    openapi_extra=key_write("endre et fakturautkast"),
)
async def update_faktura(
    document_id: int,
    payload: UpdateFakturaRequest,
    context: CurrentCompany,
    db: DbSession,
) -> FakturaResponse:
    """Change a draft. Refused once the document has been issued."""
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    document = await faktura_service.update_draft(
        db,
        company=context.company,
        document_pk=document_id,
        party_pk=payload.party_id,
        buyer_name=payload.buyer_name,
        buyer_orgnr=payload.buyer_orgnr,
        buyer_address_line=payload.buyer_address_line,
        buyer_postal_code=payload.buyer_postal_code,
        buyer_city=payload.buyer_city,
        buyer_is_business=payload.buyer_is_business,
        delivery_date=payload.delivery_date,
        delivery_place=payload.delivery_place,
        due_date=payload.due_date,
        buyer_reference=payload.buyer_reference,
        order_reference=payload.order_reference,
        payment_reference=payload.payment_reference,
        payment_reference_auto=payload.payment_reference_auto,
        bank_account_pk=payload.bank_account_id,
        note=payload.note,
        reverse_charge=payload.reverse_charge,
        lines=None if payload.lines is None else [line.to_input() for line in payload.lines],
    )
    return _faktura_response(document)


@router.delete(
    "/companies/{company_id}/faktura/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=key_write("slette et fakturautkast"),
)
async def delete_faktura(document_id: int, context: CurrentCompany, db: DbSession) -> None:
    """Discard a draft.

    Only a draft. It has no number, so discarding it leaves no gap in the series
    — which is the whole reason numbering waits until issue.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    await faktura_service.delete_draft(db, company=context.company, document_pk=document_id)


@router.get("/companies/{company_id}/faktura/{document_id}/kontroll", openapi_extra=KEY_READ)
async def check_faktura(
    document_id: int, context: CurrentCompany, db: DbSession
) -> list[ViolationResponse]:
    """What still stands between this draft and a lawful invoice.

    Safe to call as often as the form likes: it reads, it does not write, and it
    runs exactly the checks issuing will run.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    document = await faktura_service.get_document(
        db, company=context.company, document_pk=document_id
    )
    return [
        ViolationResponse(rule=v.rule, field=v.field, message=v.message, severity=v.severity)
        for v in faktura_service.check_now(document)
    ]


@router.post(
    "/companies/{company_id}/faktura/{document_id}/utsted",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_post("utstede en faktura"),
)
async def issue_faktura(
    document_id: int,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    storage: Storage,
) -> FakturaResponse:
    """Issue the draft: number it, post it, freeze its EHF, file its PDF.

    Irreversible. After this the document is history and is corrected only by a
    kreditnota — see :func:`credit_faktura`.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    document = await faktura_service.issue(
        db, storage, company=context.company, document_pk=document_id, user_id=user.id
    )
    return _faktura_response(document)


@router.post(
    "/companies/{company_id}/faktura/{document_id}/kreditnota",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_post("lage en kreditnota"),
)
async def credit_faktura(
    document_id: int,
    payload: CreditNoteRequest,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    storage: Storage,
) -> FakturaResponse:
    """Reverse an issued faktura with a kreditnota (§ 5-2-7).

    Issued immediately rather than left as a draft: a correction the seller has
    already agreed to, sitting unissued, leaves the books showing revenue that
    is not owed.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    document = await faktura_service.credit_note(
        db,
        storage,
        company=context.company,
        document_pk=document_id,
        note=payload.note,
        user_id=user.id,
    )
    return _faktura_response(document)


@router.get("/companies/{company_id}/faktura/{document_id}/ehf", openapi_extra=KEY_READ)
async def download_ehf(document_id: int, context: CurrentCompany, db: DbSession) -> Response:
    """The e-invoice, exactly as it was frozen at issue.

    Served from the stored column rather than regenerated. From 2027 this XML is
    the sales document for a business customer, and a document rebuilt on every
    request is one that can quietly differ from what was sent.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    document = await faktura_service.get_document(
        db, company=context.company, document_pk=document_id
    )
    if document.ehf_xml is None:
        raise EhfNotAvailableError(
            "fakturaen er ikke utstedt ennå, så det finnes ingen elektronisk faktura"
        )
    kind = "kreditnota" if document.is_credit_note else "faktura"
    return Response(
        content=document.ehf_xml,
        media_type="application/xml",
        headers={
            "Content-Disposition": (f'attachment; filename="{kind}-{document.document_number}.xml"')
        },
    )


@router.get("/companies/{company_id}/faktura/{document_id}/pdf", openapi_extra=KEY_READ)
async def download_pdf(document_id: int, context: CurrentCompany, db: DbSession) -> Response:
    """The readable copy.

    Rendered on demand rather than served from the archive: for an issued
    document the inputs are frozen, so the output is the same bytes the archive
    holds, and for a draft this is the preview that lets someone see the invoice
    before committing to it.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    document = await faktura_service.get_document(
        db, company=context.company, document_pk=document_id
    )
    return Response(
        content=faktura_pdf.render(document),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{faktura_pdf.filename_for(document)}"'
        },
    )
