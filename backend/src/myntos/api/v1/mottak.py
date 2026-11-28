"""Mottatt — email invoice intake, sender security settings, and booking routes.

Every company in Myntos receives a dedicated ``<slug>@myntos.no`` address on
creation (for example ``Test AS`` -> ``test@myntos.no``, falling back to
``testas@myntos.no`` and numbered suffixes when taken or reserved). A Cloudflare
Email Worker forwards inbound messages to ``POST /api/v1/webhooks/inbound-email``
signed with HMAC-SHA256.

Changing the company's email intake switch, sender policy or approved-sender
allowlist is admin-only (``ORGANIZATION_MANAGE``) and closed to agent keys:
deciding which external senders may feed invoices into the books is the
company's own security decision. Reading received email invoices and their
attachments is ``KEY_READ``; booking one into the ledger is ``key_post``
(immutable once posted); dismissing or reopening an unbooked email attachment
is ``key_write``.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status
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
from myntos.core.config import get_settings
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.ledger.service import PaymentMethod
from myntos.domain.mottak import address as address_rules
from myntos.domain.mottak import service as mottak_service
from myntos.domain.mottak.errors import (
    InboxNotAvailableError,
    InvalidInboundEmailError,
    InvalidWebhookSignatureError,
)
from myntos.domain.mottak.models import (
    CompanyInboxAddress,
    IncomingMailDocument,
    MailDocumentStatus,
    SenderPolicy,
)
from myntos.domain.mottak.service import SenderTrust

router = APIRouter(tags=["mottak"])

_WEBHOOK_MAX_SKEW_SECONDS = 300
_SAFE_DOWNLOAD_NAME = re.compile(r"[^A-Za-z0-9æøåÆØÅ._-]+")


class InboundEmailWebhookResponse(BaseModel):
    status: str
    stored: int
    skipped_attachments: int = 0
    reason: str | None = None


class InboundEmailSettingsResponse(BaseModel):
    # Whether this Myntos deployment has inbound email intake configured.
    available: bool
    # Full email address assigned to this company (e.g. ``test@myntos.no``).
    address: str
    local_part: str
    domain: str
    # Whether the company's email inbox currently accepts incoming mail.
    enabled: bool
    # ``alle_med_varsel`` (default) or ``kun_godkjente`` (strict allowlist).
    sender_policy: SenderPolicy
    # Approved external sender emails (``faktura@leverandor.no``) and domain
    # rules (``@leverandor.no``).
    allowed_senders: list[str]
    # Verified email addresses of active company members, always recognised as
    # trusted senders in both policies.
    member_emails: list[str]


class UpdateInboundEmailSettingsRequest(BaseModel):
    enabled: bool | None = None
    sender_policy: SenderPolicy | None = None
    allowed_senders: list[str] | None = Field(default=None, max_length=250)


class IncomingMailSummaryResponse(BaseModel):
    id: int
    sender_email: str
    sender_name: str | None
    envelope_from: str
    reply_to: str | None
    recipient_address: str
    subject: str
    spf_result: str | None
    dkim_result: str | None
    dmarc_result: str | None
    sender_trust: SenderTrust
    security_warnings: list[str]
    filename: str
    content_type: str
    byte_size: int
    status: MailDocumentStatus
    voucher_id: int | None
    received_at: datetime
    booked_at: datetime | None
    dismissed_at: datetime | None


class IncomingMailDetailResponse(IncomingMailSummaryResponse):
    message_id: str
    attachment_index: int
    body_excerpt: str | None
    sha256: str


class BookIncomingMailRequest(BaseModel):
    # Category from GET /api/v1/companies/{company_id}/cashflow/types
    # (for example ``varekjop`` or ``datakostnad``).
    type_code: str = Field(min_length=1, max_length=64)
    # Payment or invoice date (YYYY-MM-DD).
    occurred_on: date
    # Gross amount in NOK (VAT included), as a decimal string.
    gross_amount: Decimal = Field(gt=0)
    # Voucher text. Defaults to the email subject or attachment filename.
    description: str | None = Field(default=None, min_length=1, max_length=255)
    # Input VAT code override (e.g. "1", "11", "13", "0"). Defaults to the
    # category's default.
    tax_code: str | None = Field(default=None, max_length=32)
    payment_method: PaymentMethod = PaymentMethod.BANK
    party_id: int | None = None
    account_number: str | None = Field(default=None, max_length=32)
    dimension_value_ids: list[int] = Field(default_factory=list, max_length=9)


def _settings_response(
    inbox: CompanyInboxAddress, *, member_emails: frozenset[str]
) -> InboundEmailSettingsResponse:
    settings = get_settings()
    domain = settings.inbound_email_domain.strip().lower()
    return InboundEmailSettingsResponse(
        available=settings.inbound_email_configured,
        address=address_rules.format_inbox_address(inbox.local_part, domain),
        local_part=inbox.local_part,
        domain=domain,
        enabled=inbox.enabled,
        sender_policy=inbox.sender_policy,
        allowed_senders=mottak_service.parse_allowed_senders(inbox.allowed_senders_json),
        member_emails=sorted(member_emails),
    )


def _mail_summary(
    doc: IncomingMailDocument,
    *,
    member_emails: frozenset[str],
    allowed_senders: list[str],
) -> IncomingMailSummaryResponse:
    assessment = mottak_service.evaluate_sender_trust(
        sender_email=doc.sender_email,
        envelope_from=doc.envelope_from,
        reply_to=doc.reply_to,
        spf_result=doc.spf_result,
        dkim_result=doc.dkim_result,
        dmarc_result=doc.dmarc_result,
        member_emails=member_emails,
        allowed_senders=allowed_senders,
    )
    return IncomingMailSummaryResponse(
        id=doc.id,
        sender_email=doc.sender_email,
        sender_name=doc.sender_name,
        envelope_from=doc.envelope_from,
        reply_to=doc.reply_to,
        recipient_address=doc.recipient_address,
        subject=doc.subject,
        spf_result=doc.spf_result,
        dkim_result=doc.dkim_result,
        dmarc_result=doc.dmarc_result,
        sender_trust=assessment.trust,
        security_warnings=list(assessment.warnings),
        filename=doc.filename,
        content_type=doc.content_type,
        byte_size=doc.byte_size,
        status=doc.status,
        voucher_id=doc.voucher_id,
        received_at=doc.received_at,
        booked_at=doc.booked_at,
        dismissed_at=doc.dismissed_at,
    )


def _mail_detail(
    doc: IncomingMailDocument,
    *,
    member_emails: frozenset[str],
    allowed_senders: list[str],
) -> IncomingMailDetailResponse:
    summary = _mail_summary(doc, member_emails=member_emails, allowed_senders=allowed_senders)
    return IncomingMailDetailResponse(
        **summary.model_dump(),
        message_id=doc.message_id,
        attachment_index=doc.attachment_index,
        body_excerpt=doc.body_excerpt,
        sha256=doc.sha256,
    )


def _verify_webhook_signature(
    *,
    secret: str,
    timestamp_header: str | None,
    signature_header: str | None,
    raw_body: bytes,
) -> None:
    if not timestamp_header or not signature_header:
        raise InvalidWebhookSignatureError("manglende signatur eller tidsstempel")
    try:
        sent_at = int(timestamp_header.strip())
    except ValueError as error:
        raise InvalidWebhookSignatureError("ugyldig tidsstempel-header") from error
    if abs(int(time.time()) - sent_at) > _WEBHOOK_MAX_SKEW_SECONDS:
        raise InvalidWebhookSignatureError("webhook-tidsstempelet er utløpt")

    prefix, sep, digest_hex = signature_header.strip().partition("=")
    if prefix.lower() != "sha256" or not sep or len(digest_hex) != 64:
        raise InvalidWebhookSignatureError("ugyldig signaturformat")

    expected = hmac.new(
        secret.encode("utf-8"),
        f"{sent_at}.".encode("ascii") + raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, digest_hex.lower()):
        raise InvalidWebhookSignatureError("ugyldig webhook-signatur")


@router.post("/webhooks/inbound-email")
async def inbound_email_webhook(
    request: Request, db: DbSession, storage: Storage
) -> InboundEmailWebhookResponse:
    """Ingest a raw RFC 5322 email forwarded by the Cloudflare Email Worker.

    Authenticated with HMAC-SHA256 (``X-Myntos-Timestamp`` + ``X-Myntos-Signature``)
    over ``<timestamp>.<raw_body>`` using ``INBOUND_EMAIL_WEBHOOK_SECRET``. Not
    callable by user sessions or agent keys. Fails closed with 503
    ``inbox_not_available`` when inbound email intake is not configured, 401
    ``invalid_webhook_signature`` on bad or expired signatures, 422
    ``invalid_inbound_email`` when envelope headers or body are malformed, and
    429 ``inbound_email_rate_limited`` if the recipient company exceeded its
    hourly limit.
    """
    settings = get_settings()
    if not settings.inbound_email_configured or settings.inbound_email_webhook_secret is None:
        raise InboxNotAvailableError(
            "e-postmottak er ikke konfigurert i denne installasjonen av Myntos"
        )

    raw_body = await request.body()
    _verify_webhook_signature(
        secret=settings.inbound_email_webhook_secret.get_secret_value().strip(),
        timestamp_header=request.headers.get("X-Myntos-Timestamp"),
        signature_header=request.headers.get("X-Myntos-Signature"),
        raw_body=raw_body,
    )

    envelope_to = (request.headers.get("X-Myntos-Envelope-To") or "").strip()
    envelope_from = (request.headers.get("X-Myntos-Envelope-From") or "").strip()
    if not envelope_to:
        raise InvalidInboundEmailError("manglende mottakeradresse (X-Myntos-Envelope-To)")

    result = await mottak_service.ingest_inbound_email(
        db,
        storage,
        raw_mime=raw_body,
        envelope_from=envelope_from,
        envelope_to=envelope_to,
        domain=settings.inbound_email_domain,
        max_per_hour=settings.inbound_email_max_per_hour,
    )
    return InboundEmailWebhookResponse(
        status=result.outcome,
        stored=result.stored,
        skipped_attachments=result.skipped_attachments,
        reason=result.reason,
    )


@router.get("/companies/{company_id}/mottak/epost-innstillinger", openapi_extra=KEY_READ)
async def get_email_inbox_settings(
    context: CurrentCompany, db: DbSession
) -> InboundEmailSettingsResponse:
    """Read this company's ``@myntos.no`` email inbox address and sender security settings.

    Returns the company's unique ``<slug>@myntos.no`` address, whether email
    intake is enabled, the active ``sender_policy`` (``alle_med_varsel`` or
    ``kun_godkjente``), the approved-sender allowlist, and the verified email
    addresses of active company members (which are always recognised as trusted
    senders).
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    inbox = await mottak_service.ensure_inbox_address(db, company=context.company)
    members = await mottak_service.verified_member_emails(db, company_id=context.company.id)
    return _settings_response(inbox, member_emails=members)


@router.put("/companies/{company_id}/mottak/epost-innstillinger")
async def update_email_inbox_settings(
    payload: UpdateInboundEmailSettingsRequest,
    context: CurrentCompany,
    db: DbSession,
) -> InboundEmailSettingsResponse:
    """Update this company's email intake switch, sender security policy and allowlist.

    Admins only (``ORGANIZATION_MANAGE``) and closed to agent keys. Each entry
    in ``allowed_senders`` must be either a full email address
    (``faktura@leverandor.no``) or a domain rule (``@leverandor.no``). Returns
    422 ``invalid_allowed_sender`` if any entry is malformed.
    """
    require_permission(context.membership.role, Permission.ORGANIZATION_MANAGE)
    inbox = await mottak_service.update_inbox_settings(
        db,
        company=context.company,
        enabled=payload.enabled,
        sender_policy=payload.sender_policy,
        allowed_senders=payload.allowed_senders,
    )
    members = await mottak_service.verified_member_emails(db, company_id=context.company.id)
    return _settings_response(inbox, member_emails=members)


@router.get("/companies/{company_id}/mottak/epost", openapi_extra=KEY_READ)
async def list_incoming_mail(
    context: CurrentCompany,
    db: DbSession,
    status_filter: Annotated[MailDocumentStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[IncomingMailSummaryResponse]:
    """List invoice and receipt attachments received at the company's ``@myntos.no`` address.

    Ordered newest first. Pass ``status=ny`` for unbooked email invoices,
    ``status=bokfort`` for booked ones, or ``status=avvist`` for dismissed ones.
    Every item includes full sender transparency: ``sender_email``,
    ``sender_name``, ``envelope_from``, ``reply_to``, ``spf_result``,
    ``dkim_result``, ``dmarc_result``, ``sender_trust`` (``egen_bruker``,
    ``godkjent``, ``ukjent``, or ``advarsel``), and concrete Norwegian
    ``security_warnings`` when anything looks suspicious or unrecognised.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    inbox = await mottak_service.ensure_inbox_address(db, company=context.company)
    allowed = mottak_service.parse_allowed_senders(inbox.allowed_senders_json)
    members = await mottak_service.verified_member_emails(db, company_id=context.company.id)
    items = await mottak_service.list_mail_documents(
        db, company=context.company, status=status_filter, limit=limit, offset=offset
    )
    return [_mail_summary(doc, member_emails=members, allowed_senders=allowed) for doc in items]


@router.get("/companies/{company_id}/mottak/epost/{mail_id}", openapi_extra=KEY_READ)
async def get_incoming_mail(
    mail_id: int, context: CurrentCompany, db: DbSession
) -> IncomingMailDetailResponse:
    """Read one received email invoice attachment in full, including sender security details.

    Includes the plain-text email excerpt (never HTML), attachment SHA-256
    digest, SMTP envelope/reply-to metadata, SPF/DKIM/DMARC results, and
    computed ``sender_trust`` + ``security_warnings``. Returns 404
    ``mail_document_not_found`` when the id does not belong to this company.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    doc = await mottak_service.get_mail_document(db, company=context.company, mail_id=mail_id)
    inbox = await mottak_service.ensure_inbox_address(db, company=context.company)
    allowed = mottak_service.parse_allowed_senders(inbox.allowed_senders_json)
    members = await mottak_service.verified_member_emails(db, company_id=context.company.id)
    return _mail_detail(doc, member_emails=members, allowed_senders=allowed)


@router.get("/companies/{company_id}/mottak/epost/{mail_id}/fil", openapi_extra=KEY_READ)
async def download_incoming_mail_file(
    mail_id: int, context: CurrentCompany, db: DbSession, storage: Storage
) -> Response:
    """Download the validated PDF or image attachment from a received email invoice.

    Served inline with ``X-Content-Type-Options: nosniff`` and the magic-byte
    verified MIME type (PDF, PNG, JPEG, WebP, GIF, or HEIC). Returns 404
    ``mail_document_not_found`` when the id does not belong to this company.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    doc = await mottak_service.get_mail_document(db, company=context.company, mail_id=mail_id)
    raw = await storage.load(doc.storage_key)
    safe_name = _SAFE_DOWNLOAD_NAME.sub("-", doc.filename).strip("-")[:120] or f"vedlegg-{doc.id}"
    return Response(
        content=raw,
        media_type=doc.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{safe_name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/companies/{company_id}/mottak/epost/{mail_id}/bokfor",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_post("bokføre en e-postfaktura"),
)
async def book_incoming_mail(
    mail_id: int,
    payload: BookIncomingMailRequest,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    storage: Storage,
) -> IncomingMailDetailResponse:
    """Book a received email invoice attachment into the ledger as a posted voucher.

    Posts an immutable double-entry voucher (same rules as ``POST …/cashflow``),
    files the received PDF or image into the append-only bilag archive attached
    to that voucher, and marks the email item ``bokfort`` with its ``voucher_id``.

    Irreversible once posted (needs ``post`` scope for keys; may answer 202
    ``approval_pending``). Refuses 409 ``mail_document_already_booked``, 409
    ``mail_document_dismissed``, 404 ``mail_document_not_found``, or any
    ledger/VAT refusal from booking.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    doc, _ = await mottak_service.book_mail_document(
        db,
        storage,
        company=context.company,
        mail_id=mail_id,
        type_code=payload.type_code,
        occurred_on=payload.occurred_on,
        gross_amount=payload.gross_amount,
        description=payload.description,
        tax_code=payload.tax_code,
        payment_method=payload.payment_method,
        party_id=payload.party_id,
        account_number=payload.account_number,
        dimension_value_ids=payload.dimension_value_ids,
        created_by_user_id=user.id,
        created_via_agent_key_id=context.agent_key.id if context.agent_key is not None else None,
    )
    inbox = await mottak_service.ensure_inbox_address(db, company=context.company)
    allowed = mottak_service.parse_allowed_senders(inbox.allowed_senders_json)
    members = await mottak_service.verified_member_emails(db, company_id=context.company.id)
    return _mail_detail(doc, member_emails=members, allowed_senders=allowed)


@router.post(
    "/companies/{company_id}/mottak/epost/{mail_id}/avvis",
    openapi_extra=key_write("avvise en e-postfaktura"),
)
async def dismiss_incoming_mail(
    mail_id: int, context: CurrentCompany, db: DbSession
) -> IncomingMailDetailResponse:
    """Dismiss an unbooked email attachment (for example spam, duplicate, or not a bilag).

    Moves the item from ``ny`` to ``avvist`` without deleting the stored file,
    and can be reversed with ``POST …/gjenapne``. Refuses 409
    ``mail_document_already_booked`` if already booked.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    doc = await mottak_service.dismiss_mail_document(db, company=context.company, mail_id=mail_id)
    inbox = await mottak_service.ensure_inbox_address(db, company=context.company)
    allowed = mottak_service.parse_allowed_senders(inbox.allowed_senders_json)
    members = await mottak_service.verified_member_emails(db, company_id=context.company.id)
    return _mail_detail(doc, member_emails=members, allowed_senders=allowed)


@router.post(
    "/companies/{company_id}/mottak/epost/{mail_id}/gjenapne",
    openapi_extra=key_write("åpne en avvist e-postfaktura på nytt"),
)
async def reopen_incoming_mail(
    mail_id: int, context: CurrentCompany, db: DbSession
) -> IncomingMailDetailResponse:
    """Move a dismissed email attachment back to ``ny`` so it can be reviewed and booked.

    Refuses 409 ``mail_document_already_booked`` when already booked.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    doc = await mottak_service.reopen_mail_document(db, company=context.company, mail_id=mail_id)
    inbox = await mottak_service.ensure_inbox_address(db, company=context.company)
    allowed = mottak_service.parse_allowed_senders(inbox.allowed_senders_json)
    members = await mottak_service.verified_member_emails(db, company_id=context.company.id)
    return _mail_detail(doc, member_emails=members, allowed_senders=allowed)
