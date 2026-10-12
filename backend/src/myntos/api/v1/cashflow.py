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
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import partial
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from myntos.api.deps import (
    ATTACH_TO_VOUCHER,
    KEY_PREVIEW,
    KEY_READ,
    CompanyContext,
    CurrentCompany,
    CurrentUser,
    DbSession,
    Storage,
    key_post,
)
from myntos.core.clock import utc_now
from myntos.domain.bilag import (
    classification,
    derived,
    extraction,
    interpretation,
    questions,
    reading_cache,
    vendor,
)
from myntos.domain.bilag import context as bilag_context
from myntos.domain.bilag import service as bilag_service
from myntos.domain.bilag.errors import UnsupportedDocumentError
from myntos.domain.bilag.models import (
    MAX_ATTACHMENT_BYTES,
    PDF_CONTENT_TYPE,
    READ_AS,
    Attachment,
)
from myntos.domain.bilag.text import extract_text
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.ledger import kontoplan, landbruk, vatstatus
from myntos.domain.ledger import parties as party_service
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.cashflow import (
    CASH_FLOW_TYPES,
    CashFlowDirection,
    types_for,
)
from myntos.domain.ledger.chart import LANDBRUK_ACCOUNTS, STANDARD_TAX_CODES
from myntos.domain.ledger.models import Account, AccountType, Company, PartyType
from myntos.domain.ledger.service import (
    COMPOSITE_LABEL,
    COMPOSITE_TYPE,
    MAX_COMPOSITE_PARTS,
    CashFlowPart,
    CashFlowView,
    CompositePart,
    PaymentMethod,
)
from myntos.integrations import loynd, norgesbank

logger = logging.getLogger(__name__)

router = APIRouter(tags=["kontantstrøm"])


class CashFlowTypeResponse(BaseModel):
    code: str
    label: str
    direction: CashFlowDirection
    account_number: str
    default_tax_code: str
    note: str
    #: The category this one reverses, when it is a refund. Sent so the form can
    #: group the sixteen refunds apart from the eight ways of earning money.
    reverses: str | None = None
    #: The type of the category's account. Only in the company's own catalogue,
    #: where the form offers other accounts of the same type (see
    #: myntos.domain.ledger.service._basis_account).
    account_type: AccountType | None = None
    #: The add-on the category belongs to (``landbruk``), or null for the
    #: categories every business has. Only an add-on the company has switched
    #: on ever appears here.
    segment: str | None = None


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


class CatalogueAccountResponse(BaseModel):
    number: str
    description: str
    account_type: AccountType


class CompanyCatalogueResponse(CatalogueResponse):
    """The catalogue as one company's books see it today.

    Different from the shared one in three ways, each of them a decision the
    form must not make on its own: the default VAT code follows the company's
    registration status, the codes on offer are only the ones it may use, and
    a category whose account has been deactivated is not offered at all.
    """

    vat_registered: bool
    vat_registered_from: date | None
    # Active accounts a category may be moved to, beside its own: the ones a
    # person added, and the seeded ones not locked to a purpose.
    accounts: list[CatalogueAccountResponse]
    #: The add-ons these books have (``landbruk``), whose categories are in
    #: ``types``. Empty for most companies.
    segments: list[str] = []


class AttachmentResponse(BaseModel):
    id: int
    filename: str
    #: What the file actually is, sniffed on upload rather than trusted from the
    #: browser. The register labels each document with it, and every one of them
    #: said "PDF" until it was sent: nine screenshots of an AliExpress order came
    #: back as nine PDFs, which is the archive describing the bilag wrongly.
    content_type: str
    byte_size: int
    sha256: str


class CashFlowPartResponse(BaseModel):
    """One line of a composite entry (sammensatt bilag)."""

    type_code: str | None
    type_label: str | None
    #: Which way this line moved money. On a settlement the sale is ``inn``
    #: and each deduction ``ut``, whatever the bank movement as a whole did.
    direction: CashFlowDirection
    gross_amount: Decimal
    net_amount: Decimal
    vat_amount: Decimal
    account_number: str
    account_description: str
    tax_code: str | None
    description: str


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
    # Who entered it. NULL for a voucher posted before the column existed, or
    # whose author has since been erased; the register shows «ukjent» rather
    # than dropping the line. The id comes along so the interface can tell two
    # colleagues with the same name apart without asking the name to be unique.
    created_by_id: int | None
    created_by_name: str | None
    # The key the author worked through, by the name its person gave it, when
    # the entry came from an agent or an integration rather than this page.
    created_via: str | None = None
    attachments: list[AttachmentResponse]
    #: The lines of a composite entry, whose ``type_code`` is ``sammensatt``.
    #: Empty for an ordinary entry. On a composite entry ``tax_code`` is null
    #: (each line has its own) and ``vat_amount`` is the VAT that moved with
    #: the money, netted across the lines.
    parts: list[CashFlowPartResponse] = []


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


class QuestionResponse(BaseModel):
    """One thing the register would like answered before this is posted.

    A question rather than a warning, because the answer has somewhere to go:
    the explanation box feeds the model, and re-reading with it produces a
    better proposal. `field` says which form field the answer would settle, so
    the interface can put the question where the gap is.
    """

    text: str
    field: str | None
    # True for the one question the model composed, false for the ones the
    # register derived from its own reading. The interface marks them apart for
    # the reason it marks every other model output apart.
    asked_by_model: bool


class DocumentReadResponse(BaseModel):
    """One uploaded file, and how much we got out of it.

    Returned per file so the interface can mark a screenshot that produced
    nothing next to the five that did, instead of showing one proposal and
    leaving the user to guess which of their uploads it was built from.
    """

    filename: str
    content_type: str
    chars: int


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
    # What is still unclear, phrased so the user can answer it in the
    # explanation box and read the bilag again. Empty when the documents settled
    # everything that matters.
    questions: list[QuestionResponse]
    # One row per uploaded file, in upload order.
    documents_read: list[DocumentReadResponse]
    # Whether anything was put in front of the model besides the documents: the
    # company's formål and aktivitet, and the sentence the user typed. The /ki
    # page has to be able to say what was sent, and "the document" stopped being
    # the whole answer.
    context_used: bool
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
    # Another account of the same type than the category's own, from the
    # company's kontoplan. Left out, the category's account is used.
    account_number: str | None = Field(default=None, max_length=32)


class CompositeLineRequest(BaseModel):
    """One line of a composite entry: a category and the gross amount it moved."""

    type_code: str = Field(min_length=1, max_length=64)
    # Gross, like an ordinary entry: the amount with VAT, as the settlement
    # prints it. Always positive; the category says which way it went.
    gross_amount: Decimal = Field(gt=0)
    tax_code: str | None = Field(default=None, max_length=32)
    account_number: str | None = Field(default=None, max_length=32)
    # The line's own text, when it needs one ("Omsetningsavgift 0,10 kr/l").
    # Left out, the line carries the voucher's description.
    description: str | None = Field(default=None, max_length=255)


class CreateCompositeRequest(BaseModel):
    occurred_on: date
    description: str = Field(min_length=1, max_length=255)
    lines: list[CompositeLineRequest] = Field(min_length=2, max_length=MAX_COMPOSITE_PARTS)
    payment_method: PaymentMethod = PaymentMethod.BANK
    party_id: int | None = None


def _composite_parts(payload: CreateCompositeRequest) -> list[CompositePart]:
    return [
        CompositePart(
            type_code=line.type_code,
            gross_amount=line.gross_amount,
            tax_code=line.tax_code,
            account_number=line.account_number,
            description=line.description,
        )
        for line in payload.lines
    ]


async def _ask_for_category(
    document_text: str,
    *,
    allowed: CashFlowDirection | None,
    title: str | None,
    context: "bilag_context.BilagContext | None" = None,
    returning: bool = False,
    segments: frozenset[str] = frozenset(),
) -> classification.Classification:
    """Which category Løynd puts the document in, and which it nearly chose.

    An unavailable model is not an error for the user: the reading we did
    ourselves stands on its own, and a register that stops working because a
    model is down would be a worse trade than never asking.
    """
    try:
        return await classification.classify(
            document_text,
            ask=loynd.complete,
            allowed=allowed,
            title=title,
            context=context,
            returning=returning,
            segments=segments,
        )
    except loynd.LoyndError:
        return classification.Classification()


async def _ask_for_fields(
    document_text: str,
    *,
    today: date,
    title: str | None,
    on_description: Callable[[str], Awaitable[None]],
    context: "bilag_context.BilagContext | None" = None,
) -> extraction.Extraction:
    """What Løynd reads in the places our own parser came up empty.

    Always the streaming call, whether or not the caller is streaming to a
    browser. One path to the model rather than two: a second way of asking the
    same question is a second thing to keep in step, and the day they drift the
    difference shows up as a proposal that is subtly worse on whichever route
    nobody watched. A caller that does not care passes an ``on_description``
    that discards.
    """
    try:
        return await extraction.extract_streaming(
            document_text,
            ask=loynd.stream,
            today=today,
            title=title,
            on_description=on_description,
            context=context,
        )
    except loynd.LoyndError:
        return extraction.Extraction()


@dataclass(frozen=True)
class _Document:
    """An uploaded file as text, however we managed to get the text."""

    text: str
    title: str | None = None
    encrypted: bool = False
    unreadable: bool = False
    #: What the bytes turned out to be. Decides whether the file went to Løynd's
    #: vision model or to a PDF parser, and whether an empty result is worth a
    #: sentence to the user.
    content_type: str = PDF_CONTENT_TYPE
    #: The name the file was uploaded under, sanitised. Shown as the heading
    #: above this document's text when there is more than one.
    label: str = ""


#: How many documents are read from Løynd at once.
#:
#: Not a limit on how many may be uploaded — the user may attach as many as the
#: bilag needs, and a FINN purchase documented by an advert, a chat, a receipt
#: and a bank line is four. It is a limit on how many are *in flight*, because
#: transcribing an image occupies a GPU slot and the deployment has about three.
#: Asking for more would not go faster; it would queue on Løynd's side instead of
#: ours, where the timeout is less forgiving.
_READ_CONCURRENCY = 3


async def _read_documents(
    uploads: list[tuple[bytes, str, str]], *, use_loynd: bool
) -> list[_Document]:
    """Read every uploaded file, concurrently but not unboundedly.

    Order is preserved regardless of which finishes first: the documents are
    concatenated into one reading and a bilag whose pages arrive shuffled reads
    like a different bilag.
    """
    limiter = asyncio.Semaphore(_READ_CONCURRENCY)

    async def one(item: tuple[bytes, str, str]) -> _Document:
        content, filename, content_type = item
        label = bilag_service.sanitise_filename(filename, content_type=content_type)
        # The text is remembered by the bytes it came from, so editing the
        # description and having the register read the bilag again costs a
        # prompt rather than another ninety seconds of transcription. A miss is
        # not an error and not a special case: it costs the reading, which is
        # what would have happened anyway.
        cache_key = reading_cache.key(
            content, filename=filename, content_type=content_type, use_loynd=use_loynd
        )
        remembered = reading_cache.CACHE.get(cache_key)
        if remembered is not None:
            return _Document(
                text=remembered.text,
                title=remembered.title,
                encrypted=remembered.encrypted,
                unreadable=remembered.unreadable,
                content_type=content_type,
                label=label,
            )
        async with limiter:
            document = await _read_document(
                content,
                filename=filename,
                content_type=content_type,
                use_loynd=use_loynd,
            )
        reading_cache.CACHE.put(
            cache_key,
            reading_cache.Reading(
                text=document.text,
                title=document.title,
                encrypted=document.encrypted,
                unreadable=document.unreadable,
            ),
        )
        return document

    return list(await asyncio.gather(*(one(item) for item in uploads)))


#: What each document's heading starts with. A constant because two places need
#: it: the one that writes the headings, and the one that strips them back out
#: before a model's quote is checked against the text.
# Named in the reader, which splits the combined text back into documents to
# work out the total of each. One constant, or the two drift apart silently.
_HEADING_PREFIX = interpretation.DOCUMENT_HEADING_PREFIX


def _joined(documents: list[_Document]) -> str:
    """The documents as one text, each under a heading naming its file.

    Headings rather than a bare concatenation, and they earn their cost twice
    over. They tell the model that a screenshot of a FINN advert and a screenshot
    of a bank statement are two documents and not one confused page — without
    them the advert's asking price and the bank's actual charge become two
    numbers on the same receipt, and there is no reason to prefer either. And
    they survive into the text that every model answer is checked against, so a
    quote is still verified against the document it came from.

    A single document keeps the plain text it always had, so the one-file case
    sends exactly the prompt it sent before and nothing about it has to be
    re-measured.
    """
    readable = [document for document in documents if document.text.strip()]
    if len(readable) <= 1:
        return readable[0].text if readable else ""
    return "\n\n".join(
        f"{_HEADING_PREFIX}{index}: {document.label or 'uten navn'} ---\n{document.text}"
        for index, document in enumerate(readable, start=1)
    )


async def _read_document(
    content: bytes, *, filename: str, content_type: str, use_loynd: bool
) -> _Document:
    """The text of one uploaded file, from Løynd when possible and locally when not.

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

    **An image has no second reader.** pypdf cannot open a PNG, so a photograph
    is either transcribed by Løynd or it is not read at all — there is no local
    fallback to drop to, and pretending otherwise by running the PDF parser over
    pixels would turn a clear "we could not read this" into a corrupt-file
    error. A company with KI switched off can still *file* a photographed
    receipt, which is what the archive is for; what it cannot do is have the
    form fill itself in from one, and that is said plainly.
    """
    label = bilag_service.sanitise_filename(filename, content_type=content_type)
    # Anything that is not a PDF is pixels, including a HEIC that could not be
    # turned into something Løynd reads: pypdf has nothing to offer either way.
    is_image = content_type != PDF_CONTENT_TYPE
    sendable = content_type == PDF_CONTENT_TYPE or content_type in loynd.IMAGE_CONTENT_TYPES

    if use_loynd and sendable:
        try:
            document = await loynd.extract_file(
                content=content, filename=filename, content_type=content_type
            )
        except loynd.LoyndError:
            pass
        else:
            if not document.empty:
                return _Document(
                    text=document.text,
                    title=document.title or _from_filename(filename),
                    content_type=content_type,
                    label=label,
                )
            # An empty answer is a real answer about a scanned document, but it
            # is also what a extractor that dislikes this particular file
            # returns, so pypdf still gets its turn before we conclude anything.

    if is_image:
        # Nothing local to try. `unreadable` rather than a silent empty string,
        # so the user is told why the form did not fill itself in.
        return _Document(
            text="",
            title=_from_filename(filename),
            unreadable=True,
            content_type=content_type,
            label=label,
        )

    local = await asyncio.to_thread(extract_text, content)
    return _Document(
        text=local.text,
        title=local.title or _from_filename(filename),
        encrypted=local.encrypted,
        unreadable=local.unreadable,
        content_type=content_type,
        label=label,
    )


def _combined(documents: list[_Document]) -> _Document:
    """Every uploaded file as one reading.

    The flags are folded rather than picked: if any file was password-protected
    the user needs to be told, and if *every* file came back unreadable that is a
    different sentence from one of six being a blank screenshot. The title is
    the first one offered, because that is the document the rest hang off — an
    eBay order confirmation followed by three bank screenshots is still an eBay
    bilag.
    """
    return _Document(
        text=_joined(documents),
        title=next((document.title for document in documents if document.title), None),
        encrypted=any(document.encrypted for document in documents),
        unreadable=bool(documents)
        and all(document.unreadable or not document.text.strip() for document in documents),
        content_type=documents[0].content_type if documents else PDF_CONTENT_TYPE,
    )


def _has_letterhead(documents: list[_Document]) -> bool:
    """Whether the top of the combined text is where a name would be printed.

    The first readable file decides, because that is the one whose top the
    positional rules read: `_joined` concatenates in upload order and
    `interpretation.read` looks at the first eight lines and at the first
    title on offer. A PDF has a letterhead there. A screenshot has a clock, a
    back arrow and a search box.
    """
    readable = [document for document in documents if document.text.strip()]
    if not readable:
        return True
    return readable[0].content_type not in loynd.IMAGE_CONTENT_TYPES


async def _company_context(
    db: DbSession,
    *,
    company: Company,
    note: str | None,
    use_loynd: bool,
    direction: CashFlowDirection | None = None,
) -> bilag_context.BilagContext:
    """What the model is told about this company, assembled from what we hold.

    The formål and the aktivitet come from the stored Enhetsregisteret answer,
    which Myntos already fetches for every confirmed company — so this costs a
    single indexed row and never a round trip to Brønnøysund. A company with no
    snapshot yet contributes nothing and the model is asked the question it was
    asked before.

    Built only when a model is actually going to be consulted. Assembling it
    otherwise would be work whose result is discarded, and, more to the point,
    the user's note would be read out of the request for no purpose at all.
    """
    if not use_loynd:
        return bilag_context.NO_CONTEXT

    snapshot = await ledger_service.get_register_snapshot(db, company=company)
    unit = snapshot.unit if snapshot is not None else {}
    unit = unit if isinstance(unit, dict) else {}

    def lines(key: str) -> list[str]:
        value = unit.get(key)
        return [str(item) for item in value] if isinstance(value, list) else []

    return bilag_context.BilagContext.build(
        purpose=lines("purpose"),
        activity=lines("activity"),
        industry_code=company.industry_code,
        industry_description=company.industry_description,
        # The company row, not the register snapshot: this is the flag the rest
        # of Myntos posts VAT by, and telling the model something the ledger
        # disagrees with would be worse than telling it nothing.
        vat_registered=company.vat_registered,
        note=note,
        direction=direction.value if direction is not None else None,
        farm=landbruk.is_enabled(company),
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
    # "IMG 9461" is not a hint about anything, and it is not harmless either:
    # it is the title the model is shown above the whole bilag, and on a bank
    # screenshot in front of nine AliExpress orders it was the difference
    # between the category question being answered and an empty reply. The
    # parser already refuses these as names (interpretation._clean_title); the
    # model is now spared them too.
    if interpretation.is_camera_roll(cleaned):
        return None
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
    if type_code == COMPOSITE_TYPE:
        return COMPOSITE_LABEL
    entry_type = CASH_FLOW_TYPES.get(type_code) if type_code is not None else None
    return entry_type.label if entry_type is not None else None


def _part_response(part: CashFlowPart) -> CashFlowPartResponse:
    return CashFlowPartResponse(
        type_code=part.type_code,
        type_label=_label_for(part.type_code),
        direction=part.direction,
        gross_amount=part.gross_amount,
        net_amount=part.net_amount,
        vat_amount=part.vat_amount,
        account_number=part.account_number,
        account_description=part.account_description,
        tax_code=part.tax_code,
        description=part.description,
    )


def _attachment_response(attachment: Attachment) -> AttachmentResponse:
    return AttachmentResponse(
        id=attachment.id,
        filename=attachment.filename,
        content_type=attachment.content_type,
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
        created_by_id=view.created_by_id,
        created_by_name=view.created_by_name,
        created_via=view.created_via,
        attachments=[_attachment_response(a) for a in attachments or []],
        parts=[_part_response(part) for part in view.parts],
    )


def _via(context: CompanyContext) -> str | None:
    """The name of the key this request was made with, for an entry it just posted."""
    return None if context.agent_key is None else context.agent_key.name


@router.get("/cashflow/types", openapi_extra=KEY_READ)
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
                reverses=entry.reverses,
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


@router.get("/companies/{company_id}/cashflow/types", openapi_extra=KEY_READ)
async def company_catalogue(context: CurrentCompany, db: DbSession) -> CompanyCatalogueResponse:
    """The catalogue for this company's books, as of today.

    What the entry form should load. The shared ``/cashflow/types`` stays for
    the signup flow, which needs it before a company exists, and it describes
    a registered business, which is what its defaults assume.

    ``types`` are the categories a payment can be registered under, each with
    its direction (``inn`` money in, ``ut`` money out), the account it books
    to, the VAT code it assumes and a ``note`` in bokmål stating that
    assumption, which is worth showing the person before they accept it. A
    category with ``reverses`` set is a refund (refusjon, money back on a
    purchase) or a kreditnota (money back to a customer). ``tax_codes`` are
    the VAT codes this company may use: only code 0 and a few reverse-charge
    codes when it is not VAT-registered (``vat_registered``). ``accounts`` are
    the other accounts a category may be moved to, same type only.

    ``segments`` lists the add-ons the company has. With ``landbruk`` (the
    farming add-on, ``GET /api/v1/companies/{company_id}/landbruk``) the
    catalogue also carries the farm's categories, marked ``segment:
    "landbruk"``: settlements from the dairy or slaughterhouse, production
    subsidies (produksjonstilskudd), feed, fertiliser and the deductions on a
    settlement. Their accounts are added to the kontoplan the first time they
    are used if they are not there yet, and are offered here either way.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    company = context.company
    registered = vatstatus.registered_on(company, utc_now().date())
    allowed = vatstatus.allowed_tax_codes(registered)
    segments = landbruk.segments_for(company)
    accounts = {
        account.account_number: CatalogueAccountResponse(
            number=account.account_number,
            description=account.description,
            account_type=account.account_type,
        )
        for account in await db.scalars(
            select(Account).where(Account.company_id == company.id, Account.is_active)
        )
    }
    known = {
        account.account_number
        for account in await db.scalars(select(Account).where(Account.company_id == company.id))
    }
    if landbruk.SEGMENT in segments:
        # The add-on's accounts a farm has not got yet, because its næringskode
        # switched the add-on on and nobody has posted to them. Offered as if
        # they were there, since posting adds them (ledger.service._accounts_for);
        # one the company has switched off is not offered, since it is there.
        for seed in LANDBRUK_ACCOUNTS:
            if seed.number not in known:
                accounts[seed.number] = CatalogueAccountResponse(
                    number=seed.number,
                    description=seed.description,
                    account_type=seed.account_type,
                )
    types: list[CashFlowTypeResponse] = []
    for entry in types_for(segments=segments):
        own = accounts.get(entry.account_number)
        if own is None:
            continue
        if not registered and entry.code in vatstatus.REGISTERED_ONLY_TYPES:
            continue
        types.append(
            CashFlowTypeResponse(
                code=entry.code,
                label=entry.label,
                direction=entry.direction,
                account_number=entry.account_number,
                default_tax_code=vatstatus.default_tax_code(entry.default_tax_code, registered),
                note=entry.note,
                reverses=entry.reverses,
                account_type=own.account_type,
                segment=entry.segment,
            )
        )
    return CompanyCatalogueResponse(
        types=types,
        tax_codes=[
            TaxCodeResponse(
                code=seed.code, description=seed.description, rate_percent=seed.rate * 100
            )
            for seed in STANDARD_TAX_CODES
            if allowed is None or seed.code in allowed
        ],
        max_attachment_bytes=MAX_ATTACHMENT_BYTES,
        vat_registered=registered,
        vat_registered_from=company.vat_registered_from,
        accounts=[
            account
            for number, account in sorted(accounts.items())
            if number not in kontoplan.LOCKED_ACCOUNTS
        ],
        segments=sorted(segments),
    )


#: The names the pipeline reports progress under. Norwegian, like every other
#: name on the wire in this API, and stable: a browser matches on them.
_EVENT_DOCUMENTS = "dokumenter"
_EVENT_DESCRIPTION = "beskrivelse"
_EVENT_NOTICE = "advarsel"
_EVENT_SUGGESTION = "forslag"
_EVENT_ERROR = "feil"


async def _uploaded(files: list[UploadFile]) -> list[tuple[bytes, str, str]]:
    """The uploaded bytes, with each file's real type established.

    Separate from the pipeline, and called before a stream is opened, because
    everything it can refuse deserves a status code. Once the first byte of a
    streamed response has gone out the status line is spent, and "that is not a
    document we can read" would arrive as a frame inside a 200 — which is a
    worse way to say it and a harder one to handle.
    """
    uploads: list[tuple[bytes, str, str]] = []
    for upload in files:
        content = await upload.read()
        # The same validation the archive applies, so a file that could never be
        # attached is refused here rather than after the user has confirmed a
        # form. It also answers what the bytes actually are, which decides
        # whether this one goes to a PDF parser or to the vision model.
        content_type = bilag_service.validate_document(content)
        uploads.append((content, upload.filename or "", content_type))
    return uploads


async def _as_loynd_reads(uploads: list[tuple[bytes, str, str]]) -> list[tuple[bytes, str, str]]:
    """The uploads as Løynd can read them: a HEIC photo becomes a JPEG copy.

    Only the copy that is read changes. What gets archived is whatever the
    browser sends to ``/dokument`` afterwards, which is the original, untouched.
    The copy is stripped of its EXIF on the way (see
    :mod:`myntos.domain.bilag.derived`), so the position the photo was taken at
    does not travel to the model with it.

    Renamed to ``.jpg`` as well as retyped, because Løynd picks its extractor
    by the extension before the content type, and a JPEG still called
    ``IMG_1234.HEIC`` would be refused as a format it does not open. A photo
    that will not convert is passed on as it came, and is reported unread.
    """

    async def one(item: tuple[bytes, str, str]) -> tuple[bytes, str, str]:
        content, filename, content_type = item
        target = READ_AS.get(content_type)
        if target is None:
            return item
        try:
            copy = await derived.reading_copy(content)
        except derived.DerivationError:
            return item
        stem = filename.rsplit(".", maxsplit=1)[0] if "." in filename else filename
        return copy, f"{stem or 'bilag'}.jpg", target

    return list(await asyncio.gather(*(one(item) for item in uploads)))


async def _propose(
    context: CurrentCompany,
    db: DbSession,
    *,
    uploads: list[tuple[bytes, str, str]],
    forklaring: str | None,
    retning: CashFlowDirection | None = None,
) -> AsyncIterator[tuple[str, object]]:
    """Read the bilag and build the proposal, reporting progress as it goes.

    One pipeline, two endpoints. The JSON route below drains this and keeps the
    last event; the streaming route forwards every event as it arrives. They
    cannot drift, because there is nothing to keep in step — a difference between
    "what the form gets" and "what the user watched being written" would be the
    kind of bug that only shows up on the route nobody instrumented.

    The events, in the order they can occur:

    ``dokumenter``
        Which files were read, and how much text came out of each. First,
        because a photograph that came back blank is worth knowing about before
        the rest of the proposal exists.
    ``advarsel``
        ``{"melding": ...}``, at most once, right after the files: the bilag is
        long enough that the questions will take a while. Said here rather than
        only with the proposal, because "this will take a while" is worth
        nothing once the wait is over.
    ``beskrivelse``
        The next piece of the description, as the model writes it. Zero or more,
        and none at all when there is no model.
    ``forslag``
        The finished :class:`SuggestionResponse`. Exactly one, last.
    """
    # Whether a model may see these documents at all, decided once and honoured
    # by everything below. A company that has switched KI off gets the parser and
    # nothing else, including no upload to Løynd's extractor: "off" has to mean
    # the documents do not leave the building, or it means nothing.
    use_loynd = context.company.ki_enabled and loynd.is_configured()
    # Converted only when a model is going to look: with KI off there is no
    # reader for a photo at all, and decoding one would be work for nothing.
    if use_loynd:
        uploads = await _as_loynd_reads(uploads)

    documents = await _read_documents(uploads, use_loynd=use_loynd)
    read_report = [
        DocumentReadResponse(
            filename=document.label,
            content_type=document.content_type,
            chars=len(document.text),
        )
        for document in documents
    ]
    # Said as soon as it is known rather than with everything else at the end.
    # Which files were readable is the one thing a user may need to act on
    # immediately — a photograph that came back blank is worth re-taking now,
    # not after the rest of the proposal has finished arriving.
    yield _EVENT_DOCUMENTS, read_report
    extracted = _combined(documents)

    # A long bilag is said out loud twice: now, while the user is waiting for
    # it, and with the proposal, where the part that matters afterwards — that a
    # model given this much text reads each part of it less closely — sits
    # next to the fields it decided. Only when a model is asked at all: the
    # parser reads a long text exactly as well as a short one.
    long_bilag: str | None = None
    if use_loynd:
        shown, cut = classification.measure(extracted.text, title=extracted.title)
        if cut:
            # Written with a space between the thousands, as the rest of the
            # interface writes numbers: "28 000", not "28,000".
            ceiling = f"{classification.MAX_BILAG_CHARS:,}".replace(",", " ")
            long_bilag = (
                f"Bilaget er så langt at KI-en bare fikk lese de første {ceiling} "
                "tegnene av det. Kategori, motpart og beskrivelse bygger på det den "
                "fikk se, så kontroller dem ekstra nøye."
            )
        elif shown > classification.LONG_BILAG_CHARS:
            long_bilag = (
                "Bilaget er langt, og KI-en leser lange tekster mindre nøye. "
                "Kontroller kategori, motpart og beskrivelse ekstra nøye."
            )
        if long_bilag is not None:
            yield _EVENT_NOTICE, {"melding": "Bilaget er langt, så dette tar litt lengre tid."}
    bilag_ctx = await _company_context(
        db, company=context.company, note=forklaring, direction=retning, use_loynd=use_loynd
    )
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
            direction=retning,
            farm=landbruk.is_enabled(context.company),
        )

    # The heuristics are CPU-bound and operate on text from outside; off the
    # event loop they cannot stall other requests.
    reading = await asyncio.to_thread(
        partial(
            interpretation.read,
            extracted.text,
            today=today,
            title=extracted.title,
            # Which organisasjonsnummer on the page is the *other* party's is
            # not answerable without knowing who we are, and reading the sender
            # off our own reference block is how a purchase came back as a sale.
            own_orgnr=context.company.orgnr,
            # Whether the top of this text is a letterhead or a status bar. The
            # positional rules for a sender's name are sound on a document and
            # wrong on a phone, and the first file decides, because it is the
            # one whose top they read. See interpretation.read.
            has_letterhead=_has_letterhead(documents),
        )
    )
    suggestion = await asyncio.to_thread(propose, reading)

    # Then the two questions for the model, asked at the same time because the
    # user is watching a spinner: which category this belongs to, and what the
    # document says where our own reading came up empty. The category menu is
    # restricted to the direction when a registered party settled that question,
    # so a recognised customer cannot come back as an expense.
    if use_loynd:
        # The description is handed over while it is still being written, and
        # that is why this is a queue rather than a straight await: the sentence
        # arrives inside a callback, and a generator cannot yield from one. The
        # two questions are still asked at the same time — the category answer
        # comes back while the sentence is being typed on screen.
        pieces: asyncio.Queue[str | None] = asyncio.Queue()

        async def on_description(piece: str) -> None:
            await pieces.put(piece)

        async def ask_the_model() -> tuple[classification.Classification, extraction.Extraction]:
            try:
                return await asyncio.gather(
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
                        context=bilag_ctx,
                        # Both legs are printed on the page, so the question is
                        # not whether this is a refund but a refund of what.
                        returning=reading.shows_a_return,
                        segments=landbruk.segments_for(context.company),
                    ),
                    _ask_for_fields(
                        extracted.text,
                        today=today,
                        title=extracted.title,
                        on_description=on_description,
                        context=bilag_ctx,
                    ),
                )
            finally:
                # Whatever happened, the loop below has to stop waiting.
                await pieces.put(None)

        asking = asyncio.create_task(ask_the_model())
        try:
            while (piece := await pieces.get()) is not None:
                yield _EVENT_DESCRIPTION, piece
            category, model_reading = await asking
        finally:
            # A caller that walks away — a closed browser tab, most often —
            # closes this generator at the yield above, and the model would
            # otherwise keep generating into nothing.
            if not asking.done():
                asking.cancel()
    else:
        category, model_reading = classification.Classification(), extraction.Extraction()

    # What the model read changes what the document is taken to say, so the
    # proposal is built again from the merged reading rather than patched in
    # place: a currency it found decides whether the amount may be proposed at
    # all and whether a VAT code follows, and working that out in a second place
    # is how the two places come to disagree.
    merged, model_fields = extraction.merge(reading, model_reading)
    if model_fields:
        reading = merged
        suggestion = await asyncio.to_thread(propose, reading, model_fields)
    if category.chosen is not None:
        interpretation.apply_type(
            suggestion,
            reading=reading,
            type_code=category.chosen.code,
            source=interpretation.FieldSource.MODEL,
        )
    extraction.apply_description(suggestion, model_reading, item_text=reading.item_text)

    # Nobody named the counterparty: not the party register, not an
    # organisasjonsnummer, not a "Selger" line, and not the model reading the
    # transcription. On a bilag made of screenshots that is the normal outcome
    # and not a failure, because an app prints its name as a logo and a logo
    # does not survive being turned into text.
    #
    # So the last question goes back to the pixels, which are still here. It
    # costs one request per image and about three seconds, and it is asked only
    # in the case where the alternative is an empty field the user has to fill
    # in themselves. See myntos.domain.bilag.vendor.
    if use_loynd and suggestion.party_id is None and suggestion.counterparty_name is None:
        pictures = [
            (content, content_type)
            for content, _, content_type in uploads
            if content_type in loynd.IMAGE_CONTENT_TYPES
        ]
        if pictures:
            named = await vendor.named_by(
                pictures, look=loynd.ask_about_image, at_a_time=_READ_CONCURRENCY
            )
            if named is not None and interpretation.offerable(named):
                suggestion.counterparty_name = named
                suggestion.mark("party_id", interpretation.FieldSource.MODEL)

    # Last, because it needs the final currency: a code the model supplied is
    # what turns a total that looked like kroner into 2 137,00 GBP, and there is
    # nothing to convert until that is settled. The driftsmiddel threshold is
    # then re-tested against whichever amount and category came out of all of
    # the above, since both halves of that test have had a chance to move.
    await _to_kroner(suggestion, on=suggestion.occurred_on or today)
    interpretation.note_capitalisation(suggestion)

    # The document says what VAT the seller charged; whether *this* company may
    # deduct it is a different question, and the answer for an unregistered one
    # is no. Proposing the invoice's 25 % would fill the form with a code the
    # register then refuses, so the proposal says 0 and says why.
    registered = vatstatus.registered_on(context.company, suggestion.occurred_on or today)
    allowed = vatstatus.allowed_tax_codes(registered)
    if (
        allowed is not None
        and suggestion.tax_code is not None
        and suggestion.tax_code not in allowed
    ):
        suggestion.tax_code = vatstatus.default_tax_code(suggestion.tax_code, registered)
        suggestion.warnings.append(
            "Selskapet er ikke mva-registrert, så mvaen på bilaget kan ikke trekkes fra. "
            "Hele beløpet føres som kostnad, med mva-kode 0."
        )
    if not registered and suggestion.type_code in vatstatus.REGISTERED_ONLY_TYPES:
        suggestion.type_code = None

    warnings = list(suggestion.warnings)
    if long_bilag is not None:
        warnings.append(long_bilag)
    if extracted.encrypted:
        warnings.insert(0, "PDF-en er passordbeskyttet, så vi fikk ikke lest innholdet.")
    elif extracted.unreadable:
        # Which sentence depends on what was uploaded, because the two failures
        # have different fixes. A damaged PDF is worth re-exporting; an image
        # that came back blank means the model could not read it, and telling
        # somebody their photograph "may be damaged" sends them looking for a
        # problem with a file that is fine.
        images = sum(1 for document in documents if document.content_type != PDF_CONTENT_TYPE)
        if images == len(documents):
            warnings.insert(
                0,
                "Vi fikk ikke lest tekst ut av bildene. Prøv et skarpere bilde, "
                "eller fyll inn feltene selv.",
            )
        else:
            warnings.insert(0, "Vi klarte ikke å lese dokumentet. Det kan være skadet.")
    else:
        unread = [
            document.label or "et av dokumentene"
            for document in documents
            if not document.text.strip()
        ]
        if unread:
            # Named, not counted. "Vi fikk ikke lest 2 av 6" leaves the user
            # checking all six against a form to work out which two are missing.
            warnings.insert(
                0,
                "Vi fikk ikke lest: " + ", ".join(unread) + ". Resten er med i forslaget.",
            )

    asked = questions.compose(
        suggestion,
        reading,
        model_question=model_reading.question,
        # Only when the category it was torn about is the one on the form. A
        # runner-up to a choice that lost to our own keyword reading is a doubt
        # about a proposal nobody is looking at, and asking about it would be
        # asking the user to arbitrate between two things we did not propose.
        alternative=category.alternative if category.in_doubt else None,
        has_note=bilag_ctx.note is not None,
    )

    yield (
        _EVENT_SUGGESTION,
        SuggestionResponse(
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
            questions=[
                QuestionResponse(
                    text=question.text,
                    field=question.field,
                    asked_by_model=question.asked_by_model,
                )
                for question in asked
            ],
            ki_used=use_loynd,
            documents_read=read_report,
            context_used=not bilag_ctx.is_empty,
        ),
    )


@router.post("/companies/{company_id}/cashflow/forslag", openapi_extra=KEY_PREVIEW)
async def suggest_from_document(
    context: CurrentCompany,
    db: DbSession,
    files: Annotated[list[UploadFile], File()],
    forklaring: Annotated[str | None, Form()] = None,
    # Which way the money went, when the user clicked it before or during the
    # read. Their word, so it decides the direction and narrows the category
    # menu, and the model is told it. See interpretation.suggest.
    retning: Annotated[CashFlowDirection | None, Form()] = None,
) -> SuggestionResponse:
    """Read the uploaded bilag and propose what the entry form should hold.

    **A bilag is not always one file.** A purchase from a private seller on FINN
    is documented by the advert, the chat where the price was agreed, the
    payment receipt and the line on the bank statement, and no one of those four
    is the bilag — together they are. So this takes a list, the list has no
    length limit, and the documents are read into one text under one heading
    each. Uploading a single invoice still sends exactly the prompt it always
    did; see :func:`_joined`.

    **``forklaring`` is what the user typed.** Some bilag cannot be decided from
    the paper at all: three thousand kroner arriving from a private account is a
    deposit from the owner, a repaid loan or a sale, and the documents say the
    same thing in all three cases. A sentence from the person who made the
    payment settles it. This route only shows it to the model, and it cannot
    fill a field, because every quoted answer is still checked against the
    *documents* and the note is not one of them. It is the form's description
    field, though, so the same sentence is stored and posted as the bilagstekst
    when the user goes on to register the payment. See
    :mod:`myntos.domain.bilag.context`.

    Nothing is posted and nothing is stored on disk: the bytes are read,
    interpreted and dropped. What survives the request is the *text*, held in
    memory for a few minutes and keyed by the bytes it came from
    (:mod:`myntos.domain.bilag.reading_cache`), so that editing the description
    and having the bilag read again costs a prompt rather than another ninety
    seconds of transcribing screenshots.

    The documents themselves are archived only when the user goes on to register
    the payment, which is the point at which there is a voucher for them to
    belong to — so a user who uploads the wrong file, or changes their mind,
    leaves nothing behind in an archive that has no delete path.

    It still needs ``LEDGER_WRITE``. Reading a document is not a change to the
    books, but it is a step in making one, and a viewer has no business feeding
    files to the register.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    if not files:
        raise UnsupportedDocumentError("last opp minst én fil")

    proposal: SuggestionResponse | None = None
    async for name, payload in _propose(
        context, db, uploads=await _uploaded(files), forklaring=forklaring, retning=retning
    ):
        if name == _EVENT_SUGGESTION and isinstance(payload, SuggestionResponse):
            proposal = payload
    if proposal is None:  # pragma: no cover - the pipeline always ends with one
        raise UnsupportedDocumentError("fikk ikke lest bilaget")
    return proposal


@router.post("/companies/{company_id}/cashflow/forslag/strom", openapi_extra=KEY_PREVIEW)
async def stream_suggestion_from_document(
    context: CurrentCompany,
    db: DbSession,
    files: Annotated[list[UploadFile], File()],
    forklaring: Annotated[str | None, Form()] = None,
    # Which way the money went, when the user clicked it before or during the
    # read. Their word, so it decides the direction and narrows the category
    # menu, and the model is told it. See interpretation.suggest.
    retning: Annotated[CashFlowDirection | None, Form()] = None,
) -> Response:
    """The same proposal as ``/forslag``, reported as it is worked out.

    Server-sent events, because the wait is real and unevenly distributed. The
    parser finishes in milliseconds; a photographed receipt is a vision model
    looking at a page, and nine screenshots take a minute and a half. What used
    to happen in that minute was nothing at all — a spinner, and then a form
    that filled itself in all at once. Now the file list arrives as soon as the
    text does and the description is typed out as the model writes it, which is
    the same wait spent watching something true.

    The frames are ``event:`` / ``data:`` pairs with a JSON object in the data,
    and the last one is the whole proposal — so a client that ignores every
    frame but ``forslag`` gets exactly what the JSON route returns.

    Everything that can be refused is refused before the stream opens (see
    :func:`_uploaded`). A failure after that arrives as a ``feil`` frame, because
    by then the status line has already said 200.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    if not files:
        raise UnsupportedDocumentError("last opp minst én fil")
    uploads = await _uploaded(files)

    async def frames() -> AsyncIterator[str]:
        try:
            async for name, payload in _propose(
                context, db, uploads=uploads, forklaring=forklaring, retning=retning
            ):
                yield _frame(name, payload)
        except Exception:
            # Logged without the document: a traceback from this pipeline can
            # quote the bilag, and the bilag is the user's business.
            logger.exception("the proposal pipeline failed mid-stream")
            yield _frame(
                _EVENT_ERROR,
                {"melding": "Vi fikk ikke lest bilaget. Fyll ut skjemaet selv."},
            )

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={
            # Both of these are about intermediaries. Caddy streams as it is,
            # but a proxy that buffers would collect the whole response and
            # deliver it at once, which is precisely the behaviour this route
            # exists to avoid — and it would look like a bug here.
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


def _frame(name: str, payload: object) -> str:
    """One server-sent event.

    The payload is always JSON, never a bare string: a description piece is
    arbitrary text from a model, and text that goes out raw is text that can
    contain a newline and end the frame early.
    """
    body: object
    if isinstance(payload, BaseModel):
        body = payload.model_dump(mode="json")
    elif isinstance(payload, list):
        body = [
            item.model_dump(mode="json") if isinstance(item, BaseModel) else item
            for item in payload
        ]
    elif isinstance(payload, str):
        body = {"tekst": payload}
    else:
        body = payload
    return f"event: {name}\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"


@router.get("/companies/{company_id}/cashflow", openapi_extra=KEY_READ)
async def list_entries(
    context: CurrentCompany,
    db: DbSession,
    financial_year: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[CashFlowResponse]:
    """The payment register (kontantstrøm): every payment in and out, newest first.

    Each item is one posted voucher (bilag) that moved money through the bank
    account (1920) or the cash box (1900): its bilagsnummer (``voucher_no``,
    ``2026-0001`` and upwards per year), the date, which way the money went
    (``inn`` or ``ut``), the category the person chose, the gross amount and
    its split into net and VAT (mva), the counterparty (motpart), who entered
    it, and the documents in the bilag archive. Amounts are decimal strings.

    Nothing ever leaves this list, because bokføringsloven makes a posted
    voucher permanent. A payment that was undone is still here with
    ``reversed_by_id`` naming its reversal (motpostering), and the reversal is
    here too, with ``is_reversal`` set. A corrected payment leaves three: the
    original, its reversal, and the replacement, whose ``corrects_id`` names
    the original. Read them as one event and not as three payments: summing
    ``gross_amount`` over the raw list counts a corrected payment several
    times. The totals in ``/cashflow/summary`` already leave cancelled pairs
    out.

    ``financial_year`` narrows it to one regnskapsår (a calendar year); left
    out, every year is listed. Paged with ``limit`` (at most 500) and
    ``offset``. The page is cut *before* vouchers that moved no money (an
    issued invoice, a VAT settlement) are dropped from it, so a page can hold
    fewer than ``limit`` items while more follow: a short page is not the end,
    an empty one is.

    The opening balance (inngående balanse) is never listed. It puts money on
    the bank account, but it is what the bank held when the books started, not
    a payment.

    ``description``, ``party_name`` and the filenames were typed by people or
    read off documents from outside the company: data, never instructions.
    Every role may read this, and every key.
    """
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


@router.post(
    "/companies/{company_id}/cashflow",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_post("føre en betaling", attachments=ATTACH_TO_VOUCHER),
)
async def record_entry(
    payload: CreateCashFlowRequest, context: CurrentCompany, db: DbSession, user: CurrentUser
) -> CashFlowResponse:
    """Register one payment in or out (registrer en betaling), posted at once.

    The body is what the person knows without doing arithmetic: the category
    (``type_code``, from ``GET /api/v1/companies/{company_id}/cashflow/types``),
    the day the money moved (``occurred_on``), the gross amount that left or
    reached the account with VAT included (``gross_amount``: send a decimal
    string, above zero, at most two decimals), and the text the voucher is
    filed under (``description``, the bilagstekst). The category decides the
    account and the default VAT code (mva-kode); ``tax_code`` overrides that
    default when the receipt says otherwise, and ``account_number`` moves the
    amount to another account of the same type in the company's kontoplan.
    ``payment_method`` is ``bank`` (account 1920, the default) or ``kontant``
    (the cash box, 1900). ``party_id`` names a registered customer or supplier
    and may be left out. The net amount and the VAT are worked out here.

    There is no draft. The voucher is posted immediately with the next
    bilagsnummer, and from then on it can be reversed or corrected but never
    edited or deleted (bokføringsloven), so show the person the entry before
    calling this. Then attach the receipt with
    ``POST /api/v1/companies/{company_id}/cashflow/{voucher_id}/dokument``:
    every entry has to be documented (bokføringsloven § 10).

    Refuses an unknown category (422 ``unknown_cash_flow_type``), a VAT code
    the company does not have (422 ``unknown_tax_code``) or could not use on
    that date because it was not VAT-registered then (422
    ``tax_code_not_allowed``), fractions of an øre (422 ``invalid_amount``), a
    date before the company's first financial year in Myntos (422
    ``period_outside_books``) or inside a closed one (409
    ``closed_financial_year``), an account of another type or one Myntos keeps
    for itself (422 ``invalid_account_number``), a switched-off account (409
    ``inactive_account``), and a party that is not this company's (404
    ``party_not_found``). Needs the role Medlem or above.
    """
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
        account_number=payload.account_number,
        # Who is entering it. Taken from the session and never from the payload:
        # a client-supplied author is a client-supplied answer to "who booked
        # this", which is the one question the field exists to answer.
        created_by_user_id=user.id,
    )
    view = ledger_service.describe(
        transaction, created_by_name=user.full_name, created_via=_via(context)
    )
    # describe() only returns None for a voucher with no payment line, which
    # the function above cannot produce.
    assert view is not None  # noqa: S101
    return _cash_flow_response(view)


@router.put(
    "/companies/{company_id}/cashflow/{voucher_id}",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_post("rette en betaling", attachments=ATTACH_TO_VOUCHER),
)
async def edit_entry(
    voucher_id: int,
    payload: CreateCashFlowRequest,
    context: CurrentCompany,
    db: DbSession,
    user: CurrentUser,
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

    The body is the whole entry again, as for ``POST …/cashflow``, not the
    fields that changed: a field left out takes its default, not the old
    value. Returns the replacement, whose ``corrects_id`` names the original.
    The documents stay on the original voucher, because the archive cannot
    move them; the register counts the three as documented together.

    Refuses an entry already reversed or corrected, so correct the newest
    replacement instead (409 ``already_reversed``), an original in a closed
    financial year (409 ``closed_financial_year``), the opening balance and
    the year-end closing (409 ``cannot_reverse_here``), an id that is not
    this company's (404 ``voucher_not_found``), and everything ``POST
    …/cashflow`` refuses about the new values. Either all of it happens or
    none of it does.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    # Both new vouchers are this person's work, the reversal included: they are
    # the one correcting the entry, whoever made it.
    await ledger_service.reverse_cash_flow(
        db, company=context.company, voucher_pk=voucher_id, created_by_user_id=user.id
    )
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
        created_by_user_id=user.id,
        account_number=payload.account_number,
    )
    view = ledger_service.describe(
        transaction, created_by_name=user.full_name, created_via=_via(context)
    )
    assert view is not None  # noqa: S101
    return _cash_flow_response(view)


@router.post(
    "/companies/{company_id}/cashflow/sammensatt",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_post("føre et sammensatt bilag", attachments=ATTACH_TO_VOUCHER),
)
async def record_composite(
    payload: CreateCompositeRequest, context: CurrentCompany, db: DbSession, user: CurrentUser
) -> CashFlowResponse:
    """Register one payment that is several things at once (et sammensatt bilag).

    For a bank movement that several trades netted into: a settlement from the
    dairy (melkeoppgjør) is milk sold, less the levy, the share in the
    cooperative and the feed the dairy sold you; a payout from Vipps or Stripe
    is the day's sales less the fee; one shop receipt has food at 15 % and other
    goods at 25 %; a loan instalment is interest and repayment. Booked under one
    category each of these is wrong somewhere, so each part is a ``line`` of
    its own: a category (``type_code`` from ``GET
    /api/v1/companies/{company_id}/cashflow/types``), its gross amount with VAT
    (``gross_amount``, a decimal string above zero), and optionally its own VAT
    code, account and text, exactly as for ``POST
    /api/v1/companies/{company_id}/cashflow``.

    Every amount is positive; the category says which way it went. A money-in
    category adds to the payment and a money-out category takes from it, so a
    settlement of 12 500 of milk with 600 deducted for the share in Tine is the
    milk line (money in, 12 500) and the share line (money out, 600), and the
    bank line becomes 11 900 in. The lines must not net to zero. ``occurred_on``,
    ``description``, ``payment_method`` and ``party_id`` belong to the whole
    voucher. Between 2 and 40 lines; one line is an ordinary payment.

    Posted at once, one bilagsnummer, immutable like any other entry: show the
    person every line and the net amount before calling this, and attach the
    settlement with ``POST
    /api/v1/companies/{company_id}/cashflow/{voucher_id}/dokument``. The answer
    is the entry with ``type_code`` ``sammensatt`` and its ``parts``.

    Refuses fewer than two or more than forty lines, or lines that net to
    nothing (422 ``invalid_composite_entry``), a category from an add-on the
    company has not switched on (409 ``segment_not_enabled``), and everything
    ``POST /api/v1/companies/{company_id}/cashflow`` refuses about any line.
    Nothing is posted unless every line is accepted. Needs the role Medlem or
    above.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    transaction = await ledger_service.record_composite(
        db,
        company=context.company,
        occurred_on=payload.occurred_on,
        description=payload.description,
        parts=_composite_parts(payload),
        payment_method=payload.payment_method,
        party_pk=payload.party_id,
        created_by_user_id=user.id,
    )
    view = ledger_service.describe(
        transaction, created_by_name=user.full_name, created_via=_via(context)
    )
    assert view is not None  # noqa: S101
    return _cash_flow_response(view)


@router.put(
    "/companies/{company_id}/cashflow/sammensatt/{voucher_id}",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_post("rette et sammensatt bilag", attachments=ATTACH_TO_VOUCHER),
)
async def edit_composite(
    voucher_id: int,
    payload: CreateCompositeRequest,
    context: CurrentCompany,
    db: DbSession,
    user: CurrentUser,
) -> CashFlowResponse:
    """Change a registered payment into a composite entry, or correct one.

    The composite twin of ``PUT /api/v1/companies/{company_id}/cashflow/{voucher_id}``,
    and atomic for the same reason: it reverses the original and posts the
    replacement in one transaction, so the ledger keeps the original, its
    reversal and the corrected entry, and the person sees an edit. The original
    may be an ordinary entry, which is how a payment booked as one category is
    split up after the fact. The body is the whole composite entry again.

    Refuses what ``PUT …/cashflow/{voucher_id}`` refuses about the original
    (409 ``already_reversed``, 409 ``closed_financial_year``, 409
    ``cannot_reverse_here``, 404 ``voucher_not_found``) and what ``POST
    /api/v1/companies/{company_id}/cashflow/sammensatt`` refuses about the new
    lines. Either all of it happens or none of it does.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    await ledger_service.reverse_cash_flow(
        db, company=context.company, voucher_pk=voucher_id, created_by_user_id=user.id
    )
    transaction = await ledger_service.record_composite(
        db,
        company=context.company,
        occurred_on=payload.occurred_on,
        description=payload.description,
        parts=_composite_parts(payload),
        payment_method=payload.payment_method,
        party_pk=payload.party_id,
        corrects_pk=voucher_id,
        created_by_user_id=user.id,
    )
    view = ledger_service.describe(
        transaction, created_by_name=user.full_name, created_via=_via(context)
    )
    assert view is not None  # noqa: S101
    return _cash_flow_response(view)


@router.get("/companies/{company_id}/cashflow/summary", openapi_extra=KEY_READ)
async def summary(
    context: CurrentCompany, db: DbSession, financial_year: int | None = None
) -> SummaryResponse:
    """Totals for one financial year: the figures on the overview (oversikten).

    For one ``financial_year`` (a calendar year; the current one when left
    out), ``total_in`` and ``total_out`` are what moved through the bank
    account and the cash box, ``net`` is the difference, ``entry_count`` the
    number of vouchers behind them, and ``by_type`` the same money per
    category, largest first. A payment that was reversed, and its reversal,
    are left out of all of these, so a corrected payment counts once.

    ``output_vat`` is the VAT charged on sales (utgående mva, account 2700),
    ``input_vat`` the VAT on purchases that may be deducted (inngående mva,
    2710), and ``vat_position`` the first minus the second: positive is owed
    to Skatteetaten, negative is owed to the company. It is an estimate read
    off the register, not the MVA-melding (the VAT return), which is worked
    out per period (termin) on its own page.

    ``cash_position`` is what the bank account and the cash box hold according
    to the books, across every year and including the opening balance: a
    balance, not this year's movement. Amounts are decimal strings. Every role
    may read this, and every key.
    """
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
    openapi_extra=key_post("reversere en betaling"),
)
async def reverse(
    voucher_id: int, context: CurrentCompany, db: DbSession, user: CurrentUser
) -> CashFlowResponse:
    """Undo an entry by posting its mirror image (reversere, motpostere).

    There is no edit and no delete. A posted entry is history, and the record
    has to show that a correction happened rather than look as though the
    mistake never did.

    For a payment that should never have been recorded: one that did not
    happen, or was entered twice. A payment that happened with the wrong
    details is corrected with ``PUT /api/v1/companies/{company_id}/cashflow/{voucher_id}``
    instead. The reversal lands in the original's period and gets its own
    bilagsnummer; it is what this returns. No body.

    Refuses an entry already reversed or corrected (409 ``already_reversed``),
    one in a closed financial year (409 ``closed_financial_year``), the opening
    balance and the year-end closing, which have pages of their own (409
    ``cannot_reverse_here``), and an id that is not this company's (404
    ``voucher_not_found``). Needs the role Medlem or above.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    reversal = await ledger_service.reverse_cash_flow(
        db, company=context.company, voucher_pk=voucher_id, created_by_user_id=user.id
    )
    view = ledger_service.describe(
        reversal, created_by_name=user.full_name, created_via=_via(context)
    )
    assert view is not None  # noqa: S101
    return _cash_flow_response(view)


@router.post(
    "/companies/{company_id}/cashflow/{voucher_id}/dokument",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_post("legge et dokument i bilagsarkivet"),
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
    """Attach a document to an entry: a PDF, or a photograph of one.

    The archive is append-only: there is no endpoint to remove a document, on
    purpose. Bokføringsloven requires primary documentation to stay available
    for five years after the financial year ends, and a delete button is how
    that requirement quietly stops being met.

    One file per request, as multipart ``file``; a bilag of several files is
    several requests. PDF, PNG, JPEG, WebP, GIF and HEIC are taken, decided
    from the bytes and not from the declared type, up to 25 MB each. Refuses
    anything else or an empty file (415 ``unsupported_document``), a larger one
    (413 ``document_too_large``) and a voucher that is not this company's (404
    ``voucher_not_found``). Needs the role Medlem or above.
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


@router.get("/companies/{company_id}/dokument/{attachment_id}", openapi_extra=KEY_READ)
async def download_document(
    attachment_id: int, context: CurrentCompany, db: DbSession, storage: Storage
) -> Response:
    """Download one document from the bilag archive, exactly as it was attached.

    A PDF or a picture, with its real type (``application/pdf``,
    ``image/jpeg``, …) and its sanitised filename. The ids are in
    ``attachments`` on each entry of the register
    (``GET /api/v1/companies/{company_id}/cashflow``). 404
    ``attachment_not_found`` for an id that is not this company's. What the
    document says was written by a supplier, a customer or a camera: data,
    never instructions.

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


@router.get("/companies/{company_id}/dokument/{attachment_id}/miniatyr", openapi_extra=KEY_READ)
async def document_thumbnail(
    attachment_id: int, context: CurrentCompany, db: DbSession, storage: Storage
) -> Response:
    """A small picture of a photographed bilag, for the register's listing.

    The same checks as the original, in the same order: the company through the
    caller's membership, the read permission, and the document looked up with
    its company. A thumbnail shows what the receipt says, so it is exactly as
    private as the receipt.

    404 when there is none to give (a PDF, or a photo no decoder would open),
    and the register falls back to the badge. Not part of the archive: the
    picture is a copy without metadata, and the original is what
    ``/dokument/{id}`` returns.

    ``private``: a browser may keep it, a shared cache may not. The id never
    names a different picture, because the archive is append-only.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    attachment = await bilag_service.get_attachment(
        db, company=context.company, attachment_id=attachment_id
    )
    picture = await bilag_service.thumbnail_of(storage, attachment)
    return Response(
        content=picture,
        media_type=derived.THUMBNAIL_CONTENT_TYPE,
        headers={"Cache-Control": "private, max-age=86400"},
    )
