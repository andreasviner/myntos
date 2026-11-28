"""E-faktura over Peppol — registering, sending and receiving EHF documents.

From 1 January 2027 a sale to another Norwegian business is documented by an
e-invoice (EHF), delivered over Peppol, the European network for business
documents. Myntos writes outgoing EHF XML at issue and freezes it unchanged;
these routes hand it to Ademico (the certified Peppol access point), follow
whether it arrived, opt companies into receiving supplier EHF invoices in
ELMA/Peppol, and let users or agents review and book received EHFs into the
ledger with a readable PDF archived beside each voucher.

Registration and receiving opt-in are admin-only (``ORGANIZATION_MANAGE``) and
closed to agent keys: deciding which business invoices are sent or received
for is the company's own act, and requires the company to have been confirmed
through Altinn first.

Sending an outgoing invoice is ``key_send`` (always waits for the person's
approval on ``/godkjenning/``); booking an incoming invoice is ``key_post``
(cannot be undone); dismissing or reopening an unbooked incoming invoice is
``key_write``.

The access point (:mod:`myntos.integrations.ademico`) and public Peppol SMP
lookup (:mod:`myntos.integrations.peppol_smp`) are called here, never from
:mod:`myntos.domain.peppol` (docs/ARCHITECTURE.md, «Backend layout»).
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from myntos.api.deps import (
    FAKTURA_INTEGRATION,
    KEY_READ,
    CurrentCompany,
    CurrentUser,
    DbSession,
    Storage,
    key_post,
    key_send,
    key_write,
)
from myntos.api.v1.saft_innlesing import get_import_sessions
from myntos.core.config import get_settings
from myntos.domain.faktura.models import DocumentType
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.identity.errors import NotForIntegrationKeysError
from myntos.domain.ledger.service import PaymentMethod
from myntos.domain.peppol import incoming_pdf, ubl_reader
from myntos.domain.peppol import service as peppol_service
from myntos.domain.peppol.errors import (
    AccessPointRefusedError,
    AccessPointUnavailableError,
    PeppolNotAvailableError,
    ReceivingConflictError,
    RecipientNotOnPeppolError,
    SendUncertainError,
)
from myntos.domain.peppol.models import (
    IncomingDocumentType,
    IncomingStatus,
    PeppolIncomingDocument,
    PeppolRegistration,
    PeppolTransmission,
    TransmissionStatus,
)
from myntos.integrations import ademico, peppol_smp, peppol_sync

router = APIRouter(tags=["e-faktura"])

_UNAVAILABLE = "fikk ikke kontakt med tilgangspunktet for e-faktura. Prøv igjen om litt"
_SAFE_XML_FILENAME = re.compile(r"[^A-Za-z0-9æøåÆØÅ._-]+")

SyncSessions = Annotated[async_sessionmaker[AsyncSession], Depends(get_import_sessions)]

logger = logging.getLogger(__name__)


class PeppolStandingResponse(BaseModel):
    # The deployment has Ademico configured at all. False means nothing can be
    # sent or received from this Myntos, whatever the company does.
    available: bool
    # The company has been confirmed through Altinn, which registering needs.
    company_confirmed: bool
    # Registered as a legal entity with the access point.
    registered: bool
    # Ready to send. With Ademico, Altinn ownership confirmation is the check,
    # so this becomes true immediately upon registration.
    verified: bool
    # All of the above: outgoing invoices can be sent as EHF.
    can_send: bool
    # Published in SML/SMP (ELMA) through Ademico to receive supplier EHFs.
    can_receive: bool


class PeppolRegistrationResponse(PeppolStandingResponse):
    # Kept for v1 API compatibility; always null with Ademico, which has no
    # external identity-check link for Norwegian companies.
    verification_url: str | None = None


class SetReceivingRequest(BaseModel):
    enabled: bool


class RecipientResponse(BaseModel):
    orgnr: str
    # Registered on Peppol to receive this kind of document (an invoice, or a
    # credit note when asked about one).
    can_receive: bool


class TransmissionResponse(BaseModel):
    status: TransmissionStatus
    # The Peppol address it went to: ``0192:`` and the customer's orgnr.
    recipient: str
    sent_at: datetime
    status_checked_at: datetime | None
    # Set only when ``status`` is ``failed``.
    failure_category: str | None
    failure_message: str | None


class SyncResponse(BaseModel):
    received: int
    deliveries_updated: int


class IncomingLineResponse(BaseModel):
    line_id: str
    description: str
    quantity: str
    unit_code: str
    unit_price: str | None
    net_amount: str
    vat_category: str | None
    vat_percent: str | None


class IncomingTaxSubtotalResponse(BaseModel):
    taxable_amount: str
    tax_amount: str
    category: str | None
    percent: str | None


class IncomingInvoiceSummaryResponse(BaseModel):
    id: int
    document_type: IncomingDocumentType
    invoice_number: str
    issue_date: date | None
    due_date: date | None
    currency: str
    net_amount: Decimal
    vat_amount: Decimal
    gross_amount: Decimal
    seller_name: str
    seller_orgnr: str | None
    seller_peppol_id: str
    payment_reference: str | None
    seller_bank_account: str | None
    status: IncomingStatus
    voucher_id: int | None
    has_embedded_pdf: bool
    received_at: datetime
    booked_at: datetime | None
    dismissed_at: datetime | None
    # Proposed Norwegian SAF-T input VAT code read from the EHF's VAT breakdown.
    suggested_tax_code: str
    # Existing supplier Party.id in this company matching seller_orgnr, if any.
    suggested_party_id: int | None


class IncomingInvoiceDetailResponse(IncomingInvoiceSummaryResponse):
    buyer_reference: str | None
    order_reference: str | None
    note: str | None
    ehf_sha256: str
    lines: list[IncomingLineResponse]
    tax_subtotals: list[IncomingTaxSubtotalResponse]


class BookIncomingRequest(BaseModel):
    # Category from GET /api/v1/companies/{company_id}/cashflow/types
    # (for example ``varekjop`` or ``datakostnad``; a refund category for a
    # credit note).
    type_code: str = Field(min_length=1, max_length=64)
    # Payment or booking date (YYYY-MM-DD). Defaults to the invoice's issue_date.
    occurred_on: date | None = None
    # Gross amount in NOK (VAT included), as a decimal string. Optional for NOK
    # invoices (defaults to the invoice's gross_amount); required for foreign
    # currency invoices.
    gross_amount: Decimal | None = Field(default=None, gt=0)
    # Voucher text. Defaults to "<Leverandør>, faktura <nr>".
    description: str | None = Field(default=None, min_length=1, max_length=255)
    # Input VAT code override (e.g. "1", "11", "13", "0"). Defaults to the
    # category's default.
    tax_code: str | None = Field(default=None, max_length=32)
    payment_method: PaymentMethod = PaymentMethod.BANK
    # Existing supplier Party.id. Leave null to match or auto-create from the
    # EHF when create_supplier is true.
    party_id: int | None = None
    create_supplier: bool = True
    account_number: str | None = Field(default=None, max_length=32)
    dimension_value_ids: list[int] = Field(default_factory=list, max_length=9)


def transmission_response(transmission: PeppolTransmission) -> TransmissionResponse:
    return TransmissionResponse(
        status=transmission.status,
        recipient=transmission.recipient,
        sent_at=transmission.sent_at,
        status_checked_at=transmission.status_checked_at,
        failure_category=transmission.failure_category,
        failure_message=transmission.failure_message,
    )


def _require_access_point() -> None:
    if not ademico.is_configured():
        raise PeppolNotAvailableError(
            "e-faktura over Peppol er ikke satt opp i denne installasjonen av Myntos"
        )


def _refused(
    what: str, error: ademico.AdemicoRefusedError | ademico.ParticipantConflictError
) -> AccessPointRefusedError:
    found = "; ".join(error.problems)
    detail = f"tilgangspunktet avviste {what}" + (f": {found}" if found else "")
    return AccessPointRefusedError(detail, error.problems)


def _standing_fields(standing: peppol_service.Standing) -> dict[str, bool]:
    return {
        "available": standing.available,
        "company_confirmed": standing.company_confirmed,
        "registered": standing.registered,
        "verified": standing.verified,
        "can_send": standing.can_send,
        "can_receive": standing.can_receive,
    }


def _incoming_summary(
    doc: PeppolIncomingDocument,
    *,
    vat_registered: bool,
    supplier_by_orgnr: dict[str, int],
) -> IncomingInvoiceSummaryResponse:
    return IncomingInvoiceSummaryResponse(
        id=doc.id,
        document_type=doc.document_type,
        invoice_number=doc.invoice_number,
        issue_date=doc.issue_date,
        due_date=doc.due_date,
        currency=doc.currency,
        net_amount=doc.net_amount,
        vat_amount=doc.vat_amount,
        gross_amount=doc.gross_amount,
        seller_name=doc.seller_name,
        seller_orgnr=doc.seller_orgnr,
        seller_peppol_id=doc.seller_peppol_id,
        payment_reference=doc.payment_reference,
        seller_bank_account=doc.seller_bank_account,
        status=doc.status,
        voucher_id=doc.voucher_id,
        has_embedded_pdf=doc.has_embedded_pdf,
        received_at=doc.received_at,
        booked_at=doc.booked_at,
        dismissed_at=doc.dismissed_at,
        suggested_tax_code=ubl_reader.suggest_input_tax_code(
            doc.tax_subtotals_json, vat_registered=vat_registered
        ),
        suggested_party_id=(supplier_by_orgnr.get(doc.seller_orgnr) if doc.seller_orgnr else None),
    )


def _incoming_detail(
    doc: PeppolIncomingDocument,
    *,
    vat_registered: bool,
    supplier_by_orgnr: dict[str, int],
) -> IncomingInvoiceDetailResponse:
    summary = _incoming_summary(
        doc, vat_registered=vat_registered, supplier_by_orgnr=supplier_by_orgnr
    )
    try:
        raw_lines = json.loads(doc.lines_json)
    except ValueError:
        raw_lines = []
    try:
        raw_subtotals = json.loads(doc.tax_subtotals_json)
    except ValueError:
        raw_subtotals = []
    lines = [
        IncomingLineResponse(
            line_id=str(item.get("line_id") or ""),
            description=str(item.get("description") or ""),
            quantity=str(item.get("quantity") or "0"),
            unit_code=str(item.get("unit_code") or "EA"),
            unit_price=str(item["unit_price"]) if item.get("unit_price") is not None else None,
            net_amount=str(item.get("net_amount") or "0.00"),
            vat_category=(
                str(item["vat_category"]) if item.get("vat_category") is not None else None
            ),
            vat_percent=str(item["vat_percent"]) if item.get("vat_percent") is not None else None,
        )
        for item in raw_lines
        if isinstance(item, dict)
    ]
    subtotals = [
        IncomingTaxSubtotalResponse(
            taxable_amount=str(item.get("taxable_amount") or "0.00"),
            tax_amount=str(item.get("tax_amount") or "0.00"),
            category=str(item["category"]) if item.get("category") is not None else None,
            percent=str(item["percent"]) if item.get("percent") is not None else None,
        )
        for item in raw_subtotals
        if isinstance(item, dict)
    ]
    return IncomingInvoiceDetailResponse(
        **summary.model_dump(),
        buyer_reference=doc.buyer_reference,
        order_reference=doc.order_reference,
        note=doc.note,
        ehf_sha256=doc.ehf_sha256,
        lines=lines,
        tax_subtotals=subtotals,
    )


async def _ensure_remote_entity(
    context: CurrentCompany, db: DbSession, *, initial_receiving: bool = False
) -> tuple[ademico.RemoteEntity, PeppolRegistration]:
    """Adopt or create the company's legal entity at Ademico and record it locally."""
    company = context.company
    address, postal_code, city = peppol_service.registered_address(company)
    registration = await peppol_service.get_registration(db, company=company)
    remote = await ademico.find_legal_entity(company.orgnr)
    if remote is None:
        remote = await ademico.create_legal_entity(
            orgnr=company.orgnr,
            name=company.name,
            address=address,
            postal_code=postal_code,
            city=city,
            receiving=initial_receiving,
        )
    # Send registration never silently flips receiving on locally: receiving
    # is always an explicit choice on PUT …/peppol/mottak so two sets of books
    # for the same orgnr never hijack each other's inbox.
    recorded = await peppol_service.record_registration(
        db,
        company=company,
        registration=registration,
        remote_company_id=remote.legal_entity_id,
        remote_registration_id=remote.peppol_registration_id,
        verified=True,
    )
    return remote, recorded


@router.get("/companies/{company_id}/peppol", openapi_extra=KEY_READ)
async def peppol_standing(context: CurrentCompany, db: DbSession) -> PeppolStandingResponse:
    """Whether this company can send and receive invoices as EHF over Peppol.

    The steps in order: ``available`` (this Myntos has a Peppol access point),
    ``company_confirmed`` (someone proved through Altinn that they may act for
    the company), ``registered`` (an admin registered the company with the
    access point) and ``verified`` (ready to send). ``can_send`` is all of
    them. ``can_receive`` is true when an admin has also switched on receiving
    supplier EHF invoices in ELMA/Peppol for this company.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    registration = await peppol_service.get_registration(db, company=context.company)
    standing = peppol_service.Standing(
        available=ademico.is_configured(),
        company_confirmed=context.company.is_verified,
        registration=registration,
    )
    return PeppolStandingResponse(**_standing_fields(standing))


@router.post("/companies/{company_id}/peppol")
async def register_peppol(context: CurrentCompany, db: DbSession) -> PeppolRegistrationResponse:
    """Register the company with Ademico for sending EHF over Peppol.

    Admins only (``ORGANIZATION_MANAGE``), closed to agent keys, and only for a
    company confirmed through Altinn (409 ``company_not_confirmed`` otherwise).
    Registers the company for **sending only** (``peppolRegistration: false``),
    so it never disturbs an existing Peppol/ELMA inbox in another program. Use
    ``PUT …/peppol/mottak`` to opt into receiving incoming EHF invoices.

    409 ``company_address_missing`` when the company has no business address;
    502 when Ademico refused or could not be reached; 503
    ``peppol_not_available`` when this Myntos has no access point.
    """
    require_permission(context.membership.role, Permission.ORGANIZATION_MANAGE)
    _require_access_point()
    peppol_service.require_registrable(context.company)
    try:
        _, registration = await _ensure_remote_entity(context, db, initial_receiving=False)
    except (ademico.AdemicoRefusedError, ademico.ParticipantConflictError) as error:
        raise _refused("registreringen", error) from error
    except ademico.AdemicoError as error:
        raise AccessPointUnavailableError(_UNAVAILABLE) from error
    standing = peppol_service.Standing(
        available=True, company_confirmed=True, registration=registration
    )
    return PeppolRegistrationResponse(**_standing_fields(standing), verification_url=None)


@router.put("/companies/{company_id}/peppol/mottak")
async def set_peppol_receiving(
    payload: SetReceivingRequest, context: CurrentCompany, db: DbSession
) -> PeppolStandingResponse:
    """Enable or disable receiving incoming EHF invoices over Peppol (ELMA/SMP).

    Admins only (``ORGANIZATION_MANAGE``) and closed to agent keys. Publishing
    a Norwegian organisasjonsnummer to receive in Peppol/ELMA is exclusive: a
    business can only receive through one access point at a time.

    - ``enabled: true`` checks that no other active Myntos company receives for
      this orgnr (409 ``receiving_already_claimed``), registers the legal entity
      at Ademico if needed, and publishes it to SML/SMP. If the orgnr is already
      registered to receive at another provider (e.g. Fiken or Tripletex),
      returns 409 ``receiving_conflict`` telling the admin to switch off
      receiving there first.
    - ``enabled: false`` unpublishes the participant from SML/SMP while keeping
      sending active, freeing the orgnr for another program.
    """
    require_permission(context.membership.role, Permission.ORGANIZATION_MANAGE)
    _require_access_point()
    company = context.company
    peppol_service.require_registrable(company)
    address, postal_code, city = peppol_service.registered_address(company)

    if payload.enabled:
        await peppol_service.require_receiving_claimable(db, company=company)

    try:
        remote, registration = await _ensure_remote_entity(context, db, initial_receiving=False)
        if remote.peppol_registration_id is None:
            raise AccessPointUnavailableError(_UNAVAILABLE)
        if (
            remote.receiving_enabled != payload.enabled
            or remote.publish_in_directory != payload.enabled
        ):
            await ademico.set_receiving(
                remote.legal_entity_id,
                remote.peppol_registration_id,
                orgnr=company.orgnr,
                name=company.name,
                address=address,
                postal_code=postal_code,
                city=city,
                enabled=payload.enabled,
                registration_date=remote.registration_date,
                update_registration=remote.receiving_enabled != payload.enabled,
            )
    except ademico.ParticipantConflictError as error:
        if payload.enabled:
            raise ReceivingConflictError(
                "organisasjonsnummeret er allerede registrert for mottak av e-faktura hos et "
                "annet tilgangspunkt (for eksempel i et annet regnskapsprogram). Slå av mottak "
                "av EHF der først, og prøv igjen"
            ) from error
        raise _refused("oppdateringen av mottak", error) from error
    except ademico.AdemicoRefusedError as error:
        raise _refused("oppdateringen av mottak", error) from error
    except ademico.AdemicoError as error:
        raise AccessPointUnavailableError(_UNAVAILABLE) from error

    peppol_service.set_receiving_state(registration, enabled=payload.enabled)
    await db.flush()
    standing = peppol_service.Standing(
        available=True, company_confirmed=True, registration=registration
    )
    return PeppolStandingResponse(**_standing_fields(standing))


@router.get(
    "/companies/{company_id}/peppol/mottaker",
    openapi_extra=KEY_READ | FAKTURA_INTEGRATION,
)
async def peppol_recipient(
    context: CurrentCompany,
    orgnr: Annotated[str, Query(pattern=r"^\d{9}$")],
    kreditnota: bool = False,
) -> RecipientResponse:
    """Whether a customer can receive an invoice (or credit note) as EHF over Peppol.

    ``orgnr`` is the customer's organisasjonsnummer, nine digits, which is
    their address on Peppol. Ask before issuing an invoice to a business: when
    ``can_receive`` is true, sending it as EHF right after issuing is the
    normal way it goes, and from 1 January 2027 the required one for a sale to
    another business that keeps accounts. When false, the customer is not
    registered to receive this kind of document and gets the PDF.

    Queries the public Peppol SML/SMP (including ELMA in Norway) directly;
    sends and stores no accounting data. 503 ``peppol_not_available`` when this
    Myntos has no access point, 502 ``access_point_unavailable`` when the
    public lookup could not be reached.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    _require_access_point()
    try:
        receives = await peppol_smp.can_receive(orgnr, credit_note=kreditnota)
    except peppol_smp.SmpLookupError as error:
        raise AccessPointUnavailableError(_UNAVAILABLE) from error
    return RecipientResponse(orgnr=orgnr, can_receive=receives)


@router.post(
    "/companies/{company_id}/faktura/{document_id}/send",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_send("sende fakturaen som EHF til kunden") | FAKTURA_INTEGRATION,
)
async def send_faktura(
    document_id: int, context: CurrentCompany, db: DbSession
) -> TransmissionResponse:
    """Send an issued invoice or credit note to the customer as EHF over Peppol.

    What is sent is the EHF (the Norwegian e-invoice, an XML file) that was
    made when the document was issued, unchanged. It goes to the customer's
    organisasjonsnummer (orgnr, the Norwegian business register number), which
    is their address on Peppol. Nothing is e-mailed.

    An AI agent's key always waits for its person's approval, whatever the key
    may do on its own, because an invoice that has gone cannot be called back.
    A server's integration key (``myntos_ik_``), which an owner made for
    invoicing and bound to the server's IP addresses, sends at once: the
    owner's decision of 2026-11-26. It sends invoices only; a credit note is
    403 ``not_for_integration_keys``.

    ``status`` in the answer is ``pending`` (queued for AS4 delivery),
    ``delivered`` or ``failed``. Follow it with ``GET …/forsendelse``.
    Refusals: 409 ``not_registered``, 409 ``not_sendable``, 409
    ``already_sent``, 409 ``recipient_not_on_peppol``, 502 ``send_uncertain``,
    502 ``access_point_refused`` or ``access_point_unavailable``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    _require_access_point()
    sendable = await peppol_service.prepare_send(
        db, company=context.company, document_pk=document_id
    )
    peppol_service.require_verified(sendable.registration)
    # The owner's line for integrations was invoices, not credit notes: a
    # server that may make and send invoices has no business sending a credit
    # note somebody else made, which cannot be called back either.
    if (
        context.agent_key is not None
        and context.agent_key.is_integration
        and sendable.document.document_type is DocumentType.KREDITNOTA
    ):
        raise NotForIntegrationKeysError(
            "en integrasjonsnøkkel sender bare fakturaer, ikke kreditnotaer. En person må sende "
            "kreditnotaen selv, innlogget i Myntos."
        )

    kind = "kreditnota" if sendable.document.document_type is DocumentType.KREDITNOTA else "faktura"
    filename = f"{kind}-{sendable.document.document_number or sendable.document.id}.xml"

    try:
        sent = await ademico.send(xml=sendable.xml, filename=filename)
    except ademico.RecipientUnreachableError as error:
        raise RecipientNotOnPeppolError(
            "kunden kan ikke motta EHF over Peppol, så ingenting ble sendt. "
            "Send PDF-en til kunden selv"
        ) from error
    except (ademico.AdemicoRefusedError, ademico.ParticipantConflictError) as error:
        raise _refused("fakturaen", error) from error
    except ademico.SendTimedOutError as error:
        raise SendUncertainError(
            "tilgangspunktet svarte ikke i tide, så vi vet ikke om fakturaen gikk. "
            "Spør kunden om de har fått den før du sender den igjen"
        ) from error
    except ademico.AdemicoError as error:
        raise AccessPointUnavailableError(_UNAVAILABLE) from error

    transmission = await peppol_service.record_transmission(
        db,
        company=context.company,
        document=sendable.document,
        remote_document_id=sent.id,
        recipient=ademico.peppol_address(sendable.buyer_orgnr),
        status=TransmissionStatus(sent.delivery.status),
        failure_category=sent.delivery.failure_category,
        failure_message=sent.delivery.failure_message,
    )
    return transmission_response(transmission)


@router.get(
    "/companies/{company_id}/faktura/{document_id}/forsendelse",
    openapi_extra=KEY_READ | FAKTURA_INTEGRATION,
)
async def faktura_transmission(
    document_id: int, context: CurrentCompany, db: DbSession
) -> TransmissionResponse:
    """Whether the invoice sent as EHF has reached the customer.

    The latest send of the document. While it is ``pending`` each call asks
    Ademico again and stores the answer; ``status_checked_at`` says when that
    last succeeded, and an access point that cannot be reached leaves the
    stored answer. ``delivered`` means the customer's access point acknowledged
    it. ``failed`` comes with ``failure_category`` and ``failure_message``, and
    a failed send may be tried again with ``POST …/send``.

    404 ``transmission_not_found`` when the document has never been sent;
    404 ``invoice_not_found`` when the id is not one of this company's
    documents.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    latest = await peppol_service.latest_transmission(
        db, company=context.company, document_pk=document_id
    )
    if latest.status == TransmissionStatus.PENDING and ademico.is_configured():
        try:
            delivery = await ademico.delivery(latest.remote_document_id)
        except ademico.AdemicoError:
            return transmission_response(latest)
        await peppol_service.record_delivery(
            db,
            latest,
            status=TransmissionStatus(delivery.status),
            failure_category=delivery.failure_category,
            failure_message=delivery.failure_message,
        )
    return transmission_response(latest)


# --- Ademico's webhook --------------------------------------------------------------


def _is_ademico(authorization: str | None, secret: str) -> bool:
    """Whether a request carries the Basic Auth credentials Myntos gave Ademico."""
    if not authorization:
        return False
    scheme, _, encoded = authorization.strip().partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return False
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    user, sep, password = decoded.partition(":")
    if not sep:
        return False
    # Both halves compared in full whatever the first says, so the time taken
    # tells a caller nothing about which half was wrong.
    user_ok = hmac.compare_digest(user.encode(), ademico.WEBHOOK_USER.encode())
    password_ok = hmac.compare_digest(password.encode(), secret.encode())
    return user_ok and password_ok


@router.post("/webhooks/ademico")
async def ademico_webhook(request: Request, sessions: SyncSessions) -> Response:
    """Ademico saying a Peppol event happened. Read back; never believed.

    Server to server: a delivery, a buyer's answer, or a received EHF for any
    company under this deployment, pushed with the Basic Auth credentials Myntos
    gave Ademico (user ``ademico``, password ``ADEMICO_WEBHOOK_SECRET``). The
    body only names documents; each is read back from Ademico before anything
    is stored (myntos.integrations.peppol_sync).

    503 when no secret or no access point is configured, and when Ademico could
    not confirm an event yet, so it pushes again. 401 on wrong credentials. 200
    otherwise, including for an event that is not ours and for a body with
    nothing readable in it: no retry changes either, and the sweep behind the
    webhook finds whatever was in it.
    """
    settings = get_settings()
    if (
        not settings.ademico_webhook_configured
        or settings.ademico_webhook_secret is None
        or not ademico.is_configured()
    ):
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    secret = settings.ademico_webhook_secret.get_secret_value().strip()
    if not _is_ademico(request.headers.get("authorization"), secret):
        # WARNING, not ERROR: anyone on the internet can knock. A wrong secret
        # at Ademico shows up as the sweep's ERROR instead.
        logger.warning("Refused a call to the Ademico webhook without its credentials")
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    try:
        body: object = json.loads(await request.body())
    except ValueError:
        body = None
    notifications = ademico.notifications_from_push(body)
    if not notifications:
        # Ademico's push format is undocumented, so this is the line that says
        # it is not what Myntos expects. Key names only: values may name
        # companies or documents (docs/operator.md).
        keys = sorted(body)[:20] if isinstance(body, dict) else []
        logger.error(
            "Ademico webhook body had no notification Myntos could read (type=%s keys=%s)",
            type(body).__name__,
            ",".join(str(key) for key in keys),
        )
        return Response(status_code=status.HTTP_200_OK)

    result = await peppol_sync.handle_push(sessions, notifications)
    if result.retry:
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response(status_code=status.HTTP_200_OK)


# --- Incoming EHF invoices & credit notes -----------------------------------------


@router.post("/companies/{company_id}/peppol/synkroniser", openapi_extra=KEY_READ)
async def sync_peppol(context: CurrentCompany, sessions: SyncSessions) -> SyncResponse:
    """Check the access point now for delivery updates and incoming EHF invoices.

    Ademico tells Myntos about each received invoice and each delivery as it
    happens, and Myntos also checks by itself every 15 minutes, so this is
    rarely needed. Call it when a user or agent expects a supplier invoice that
    has not appeared, or wants to be sure the inbox is complete before booking.
    Stores nothing that was not already delivered to Ademico, and never posts
    to the ledger. Answers with counts for this company only. 503
    ``peppol_not_available`` when no access point is configured; 502
    ``access_point_unavailable`` when Ademico cannot be reached.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    _require_access_point()
    try:
        summary = await peppol_sync.sync_once(sessions)
    except ademico.AdemicoError as error:
        raise AccessPointUnavailableError(_UNAVAILABLE) from error
    return SyncResponse(
        received=summary.received_for(context.company.id),
        deliveries_updated=summary.deliveries_updated_for(context.company.id),
    )


@router.get("/companies/{company_id}/peppol/innkommende", openapi_extra=KEY_READ)
async def list_incoming_ehf(
    context: CurrentCompany,
    db: DbSession,
    status_filter: Annotated[IncomingStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[IncomingInvoiceSummaryResponse]:
    """List EHF supplier invoices and credit notes received over Peppol for this company.

    Ordered newest first. Pass ``status=ny`` for unbooked invoices waiting in
    the inbox, ``status=bokfort`` for booked ones, or ``status=avvist`` for
    dismissed ones. Each row includes ``suggested_tax_code`` (read from the
    EHF's VAT breakdown) and ``suggested_party_id`` (matching an existing
    supplier by orgnr). Amounts are decimal strings, never floats.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    items = await peppol_service.list_incoming(
        db, company=context.company, status=status_filter, limit=limit, offset=offset
    )
    suppliers = await peppol_service.match_supplier_parties(
        db,
        company=context.company,
        orgnrs=[doc.seller_orgnr for doc in items if doc.seller_orgnr],
    )
    return [
        _incoming_summary(
            doc,
            vat_registered=context.company.vat_registered,
            supplier_by_orgnr=suppliers,
        )
        for doc in items
    ]


@router.get("/companies/{company_id}/peppol/innkommende/{incoming_id}", openapi_extra=KEY_READ)
async def get_incoming_ehf(
    incoming_id: int, context: CurrentCompany, db: DbSession
) -> IncomingInvoiceDetailResponse:
    """Read one received EHF invoice or credit note in full, with lines and VAT breakdown.

    Includes the supplier's orgnr, bank account, KID (``payment_reference``),
    every line item, each VAT subtotal, and ``ehf_sha256`` (the digest of the
    frozen received XML). 404 ``incoming_invoice_not_found`` if the id does not
    belong to this company.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    doc = await peppol_service.get_incoming(db, company=context.company, incoming_id=incoming_id)
    suppliers = await peppol_service.match_supplier_parties(
        db,
        company=context.company,
        orgnrs=[doc.seller_orgnr] if doc.seller_orgnr else [],
    )
    return _incoming_detail(
        doc,
        vat_registered=context.company.vat_registered,
        supplier_by_orgnr=suppliers,
    )


@router.get("/companies/{company_id}/peppol/innkommende/{incoming_id}/ehf", openapi_extra=KEY_READ)
async def download_incoming_ehf_xml(
    incoming_id: int, context: CurrentCompany, db: DbSession
) -> Response:
    """Download the original received EHF UBL XML file exactly as it arrived over Peppol."""
    require_permission(context.membership.role, Permission.LEDGER_READ)
    doc = await peppol_service.get_incoming(db, company=context.company, incoming_id=incoming_id)
    kind = (
        "ehf-kreditnota" if doc.document_type is IncomingDocumentType.KREDITNOTA else "ehf-faktura"
    )
    stem = _SAFE_XML_FILENAME.sub("-", doc.invoice_number.strip()).strip("-")[:60] or str(doc.id)
    return Response(
        content=doc.ehf_xml.encode("utf-8"),
        media_type="application/xml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{kind}-{stem}.xml"'},
    )


@router.get("/companies/{company_id}/peppol/innkommende/{incoming_id}/pdf", openapi_extra=KEY_READ)
async def download_incoming_ehf_pdf(
    incoming_id: int, context: CurrentCompany, db: DbSession
) -> Response:
    """Download a readable PDF of a received EHF invoice or credit note.

    Returns the supplier's own embedded PDF when the EHF carried one
    (``has_embedded_pdf: true``), or a clean A4 summary rendered by Myntos
    directly from the frozen UBL XML.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    doc = await peppol_service.get_incoming(db, company=context.company, incoming_id=incoming_id)
    pdf_bytes, filename = incoming_pdf.pdf_for(doc)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.post(
    "/companies/{company_id}/peppol/innkommende/{incoming_id}/bokfor",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_post("bokføre en innkommende e-faktura"),
)
async def book_incoming_ehf(
    incoming_id: int,
    payload: BookIncomingRequest,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    storage: Storage,
) -> IncomingInvoiceDetailResponse:
    """Book a received EHF invoice or credit note into the ledger as a posted voucher.

    Posts an immutable double-entry voucher (same rules as ``POST …/cashflow``),
    automatically files the readable PDF (the supplier's embedded PDF or Myntos's
    rendered summary) into the append-only bilag archive attached to that voucher,
    and marks the incoming EHF ``bokfort`` with its ``voucher_id``.

    Irreversible once posted (needs ``post`` scope for keys; may answer 202
    ``approval_pending``). For NOK invoices, ``gross_amount`` may be omitted to
    use the invoice's own total; for foreign-currency invoices, supply the NOK
    amount paid. Refuses 409 ``incoming_invoice_already_booked``, 409
    ``incoming_invoice_dismissed``, 422 ``incoming_invoice_invalid``, or any
    ledger/VAT refusal from booking (e.g. ``closed_financial_year``,
    ``tax_code_not_allowed``).
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    doc, _ = await peppol_service.book_incoming(
        db,
        storage,
        company=context.company,
        incoming_id=incoming_id,
        type_code=payload.type_code,
        occurred_on=payload.occurred_on,
        gross_amount=payload.gross_amount,
        description=payload.description,
        tax_code=payload.tax_code,
        payment_method=payload.payment_method,
        party_id=payload.party_id,
        create_supplier=payload.create_supplier,
        account_number=payload.account_number,
        dimension_value_ids=payload.dimension_value_ids,
        created_by_user_id=user.id,
        created_via_agent_key_id=context.agent_key.id if context.agent_key is not None else None,
    )
    suppliers = await peppol_service.match_supplier_parties(
        db,
        company=context.company,
        orgnrs=[doc.seller_orgnr] if doc.seller_orgnr else [],
    )
    return _incoming_detail(
        doc,
        vat_registered=context.company.vat_registered,
        supplier_by_orgnr=suppliers,
    )


@router.post(
    "/companies/{company_id}/peppol/innkommende/{incoming_id}/avvis",
    openapi_extra=key_write("avvise en innkommende e-faktura"),
)
async def dismiss_incoming_ehf(
    incoming_id: int, context: CurrentCompany, db: DbSession
) -> IncomingInvoiceDetailResponse:
    """Dismiss an unbooked incoming EHF (for example already booked manually or sent in error).

    Moves the document out of the active ``ny`` inbox to ``avvist``. Nothing is
    deleted (bokføringsloven § 13 keeps received documentation), and a dismissed
    EHF can be returned to ``ny`` with ``POST …/gjenapne``. Refuses 409
    ``incoming_invoice_already_booked`` when already booked.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    doc = await peppol_service.dismiss_incoming(
        db, company=context.company, incoming_id=incoming_id
    )
    suppliers = await peppol_service.match_supplier_parties(
        db,
        company=context.company,
        orgnrs=[doc.seller_orgnr] if doc.seller_orgnr else [],
    )
    return _incoming_detail(
        doc,
        vat_registered=context.company.vat_registered,
        supplier_by_orgnr=suppliers,
    )


@router.post(
    "/companies/{company_id}/peppol/innkommende/{incoming_id}/gjenapne",
    openapi_extra=key_write("åpne en avvist e-faktura på nytt"),
)
async def reopen_incoming_ehf(
    incoming_id: int, context: CurrentCompany, db: DbSession
) -> IncomingInvoiceDetailResponse:
    """Move a dismissed incoming EHF back to ``ny`` so it can be booked.

    Refuses 409 ``incoming_invoice_already_booked`` when the document is
    already booked.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    doc = await peppol_service.reopen_incoming(db, company=context.company, incoming_id=incoming_id)
    suppliers = await peppol_service.match_supplier_parties(
        db,
        company=context.company,
        orgnrs=[doc.seller_orgnr] if doc.seller_orgnr else [],
    )
    return _incoming_detail(
        doc,
        vat_registered=context.company.vat_registered,
        supplier_by_orgnr=suppliers,
    )
