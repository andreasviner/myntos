"""Service layer for company email inbox addresses, sender security, and booking."""

from __future__ import annotations

import enum
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.core.clock import utc_now
from myntos.domain.bilag import service as bilag_service
from myntos.domain.identity.models import CompanyMembership, User
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.models import Company, Transaction
from myntos.domain.ledger.service import PaymentMethod
from myntos.domain.mottak import address as address_rules
from myntos.domain.mottak import parser as email_parser
from myntos.domain.mottak.allocation import ensure_inbox_address as ensure_inbox_address
from myntos.domain.mottak.errors import (
    InboundEmailRateLimitedError,
    InvalidAllowedSenderError,
    MailDocumentAlreadyBookedError,
    MailDocumentDismissedError,
    MailDocumentNotFoundError,
)
from myntos.domain.mottak.models import (
    CompanyInboxAddress,
    IncomingMailDocument,
    MailDocumentStatus,
    SenderPolicy,
)
from myntos.storage.base import DocumentStorage

logger = logging.getLogger(__name__)

MAX_ALLOWED_SENDERS = 200


class SenderTrust(enum.StrEnum):
    """Security classification displayed alongside every received email invoice."""

    # Sender is a verified member of this company and passed auth checks.
    EGEN_BRUKER = "egen_bruker"
    # Sender matches the company's approved-sender allowlist and passed auth checks.
    GODKJENT = "godkjent"
    # Sender is not recognised as a member or approved sender (no spoof signal).
    UKJENT = "ukjent"
    # Auth failure (SPF/DKIM/DMARC fail) or spoof/redirect signal (From vs
    # Envelope/Reply-To mismatch). Always escalates to warning even if the
    # claimed From address looks familiar.
    ADVARSEL = "advarsel"


@dataclass(frozen=True)
class SenderAssessment:
    trust: SenderTrust
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class IngestResult:
    outcome: str  # "accepted" | "ignored" | "rejected"
    stored: int
    skipped_attachments: int = 0
    reason: str | None = None


def parse_allowed_senders(raw_json: str) -> list[str]:
    try:
        loaded = json.loads(raw_json)
    except ValueError:
        return []
    if not isinstance(loaded, list):
        return []
    return [str(item).strip().lower() for item in loaded if isinstance(item, str) and item.strip()]


async def update_inbox_settings(
    db: AsyncSession,
    *,
    company: Company,
    enabled: bool | None = None,
    sender_policy: SenderPolicy | None = None,
    allowed_senders: Sequence[str] | None = None,
) -> CompanyInboxAddress:
    """Update the company's email intake switch, sender policy, and allowlist."""
    inbox = await ensure_inbox_address(db, company=company)
    if enabled is not None:
        inbox.enabled = enabled
    if sender_policy is not None:
        inbox.sender_policy = sender_policy
    if allowed_senders is not None:
        normalised: list[str] = []
        seen: set[str] = set()
        for raw in allowed_senders:
            cleaned = address_rules.normalise_allowed_sender(raw)
            if cleaned not in seen:
                seen.add(cleaned)
                normalised.append(cleaned)
        if len(normalised) > MAX_ALLOWED_SENDERS:
            raise InvalidAllowedSenderError(
                f"du kan lagre maks {MAX_ALLOWED_SENDERS} godkjente avsendere"
            )
        inbox.allowed_senders_json = json.dumps(normalised)
    await db.flush()
    return inbox


async def verified_member_emails(db: AsyncSession, *, company_id: int) -> frozenset[str]:
    """Lowercased verified email addresses of active members on ``company_id``."""
    rows = await db.scalars(
        select(User.email)
        .join(CompanyMembership, CompanyMembership.user_id == User.id)
        .where(
            CompanyMembership.company_id == company_id,
            User.email.is_not(None),
            User.email_verified_at.is_not(None),
        )
    )
    return frozenset(str(addr).strip().lower() for addr in rows if addr)


def evaluate_sender_trust(
    *,
    sender_email: str,
    envelope_from: str,
    reply_to: str | None,
    spf_result: str | None,
    dkim_result: str | None,
    dmarc_result: str | None,
    member_emails: frozenset[str],
    allowed_senders: Sequence[str],
) -> SenderAssessment:
    """Classify sender trustworthiness and list concrete Norwegian security warnings."""
    normalised_from = sender_email.strip().lower()
    normalised_env = envelope_from.strip().lower()
    normalised_reply = reply_to.strip().lower() if reply_to else None

    warnings: list[str] = []
    auth_failed = False

    if dmarc_result == "fail":
        auth_failed = True
        warnings.append(
            "DMARC feilet — e-posten kan være sendt av noen som utgir seg for å være avsenderen"
        )
    if spf_result in ("fail", "softfail"):
        if spf_result == "fail":
            auth_failed = True
        warnings.append(
            f"SPF-sjekk ga «{spf_result}» — avsenderserveren er ikke godkjent for avsenderdomenet"
        )
    if dkim_result == "fail":
        auth_failed = True
        warnings.append("DKIM-signatur feilet — innholdet eller avsenderen kan være endret")

    from_domain = address_rules.email_domain(normalised_from)
    env_domain = address_rules.email_domain(normalised_env)
    reply_domain = address_rules.email_domain(normalised_reply)

    # A different SMTP envelope domain is normal for billing providers when DMARC
    # passes via aligned DKIM, so only raise a spoofing warning when DMARC did
    # not pass.
    if env_domain and from_domain and env_domain != from_domain and dmarc_result != "pass":
        warnings.append(
            f"Teknisk avsender ({normalised_env}) tilhører et annet domene enn From-adressen"
        )

    if reply_domain and from_domain and reply_domain != from_domain:
        warnings.append(
            f"Svaradresse (Reply-To: {normalised_reply}) går til et annet domene enn avsenderen"
        )

    is_member = normalised_from in member_emails
    is_allowed = address_rules.sender_matches_allowlist(normalised_from, allowed_senders)

    if auth_failed or warnings:
        return SenderAssessment(trust=SenderTrust.ADVARSEL, warnings=tuple(warnings))
    if is_member:
        return SenderAssessment(trust=SenderTrust.EGEN_BRUKER, warnings=())
    if is_allowed:
        return SenderAssessment(trust=SenderTrust.GODKJENT, warnings=())
    return SenderAssessment(
        trust=SenderTrust.UKJENT,
        warnings=("Ukjent avsender — kontroller faktura og kontonummer nøye før du betaler",),
    )


async def ingest_inbound_email(
    db: AsyncSession,
    storage: DocumentStorage,
    *,
    raw_mime: bytes,
    envelope_from: str,
    envelope_to: str,
    domain: str,
    max_per_hour: int,
) -> IngestResult:
    """Validate, authorise, parse and store attachments from one inbound email."""
    local_part = address_rules.extract_local_part(envelope_to, domain)
    if local_part is None:
        return IngestResult(outcome="ignored", stored=0, reason="unknown_domain_or_recipient")

    row = (
        await db.execute(
            select(CompanyInboxAddress, Company)
            .join(Company, Company.id == CompanyInboxAddress.company_id)
            .where(CompanyInboxAddress.local_part == local_part)
        )
    ).first()
    if row is None:
        return IngestResult(outcome="ignored", stored=0, reason="unknown_recipient")

    inbox, company = row
    if company.closed_at is not None or not inbox.enabled:
        return IngestResult(outcome="rejected", stored=0, reason="inbox_disabled")

    # Enforce per-company hourly rate limit (counted by distinct received emails).
    if max_per_hour > 0:
        since = utc_now() - timedelta(hours=1)
        recent_messages = await db.scalar(
            select(func.count(func.distinct(IncomingMailDocument.message_id))).where(
                IncomingMailDocument.company_id == company.id,
                IncomingMailDocument.received_at >= since,
            )
        )
        if int(recent_messages or 0) >= max_per_hour:
            raise InboundEmailRateLimitedError(
                "innboksen har mottatt for mange e-poster den siste timen. Prøv igjen senere"
            )

    parsed = email_parser.parse_inbound_email(raw_mime, envelope_from=envelope_from)

    # Strict allowlist policy: reject unknown or spoofed senders BEFORE writing
    # anything to storage or database.
    if inbox.sender_policy is SenderPolicy.KUN_GODKJENTE:
        members = await verified_member_emails(db, company_id=company.id)
        allowed = parse_allowed_senders(inbox.allowed_senders_json)
        sender = parsed.sender_email.lower()
        recognised = sender in members or address_rules.sender_matches_allowlist(sender, allowed)
        auth_bad = parsed.dmarc_result == "fail" or (
            parsed.spf_result == "fail" and parsed.dkim_result != "pass"
        )
        if not recognised or auth_bad:
            logger.info(
                "Rejected inbound email for company %s under strict sender policy",
                company.id,
            )
            return IngestResult(outcome="rejected", stored=0, reason="sender_not_allowed")

    if not parsed.attachments:
        return IngestResult(
            outcome="ignored",
            stored=0,
            skipped_attachments=parsed.skipped_attachments,
            reason="no_supported_attachments",
        )

    existing_indices = set(
        await db.scalars(
            select(IncomingMailDocument.attachment_index).where(
                IncomingMailDocument.company_id == company.id,
                IncomingMailDocument.message_id == parsed.message_id,
            )
        )
    )

    now = utc_now()
    canonical_recipient = address_rules.format_inbox_address(inbox.local_part, domain)
    stored = 0
    for attachment in parsed.attachments:
        if attachment.index in existing_indices:
            continue
        key = await storage.save(
            content=attachment.content,
            filename=attachment.filename,
            content_type=attachment.content_type,
        )
        doc = IncomingMailDocument(
            company_id=company.id,
            message_id=parsed.message_id,
            attachment_index=attachment.index,
            sender_email=parsed.sender_email,
            sender_name=parsed.sender_name,
            envelope_from=parsed.envelope_from,
            reply_to=parsed.reply_to,
            recipient_address=canonical_recipient,
            subject=parsed.subject,
            body_excerpt=parsed.body_excerpt,
            spf_result=parsed.spf_result,
            dkim_result=parsed.dkim_result,
            dmarc_result=parsed.dmarc_result,
            filename=attachment.filename,
            content_type=attachment.content_type,
            byte_size=len(attachment.content),
            sha256=attachment.sha256,
            storage_key=key,
            status=MailDocumentStatus.NY,
            received_at=now,
        )
        db.add(doc)
        stored += 1

    await db.flush()
    return IngestResult(
        outcome="accepted",
        stored=stored,
        skipped_attachments=parsed.skipped_attachments,
    )


async def list_mail_documents(
    db: AsyncSession,
    *,
    company: Company,
    status: MailDocumentStatus | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[IncomingMailDocument]:
    """List received email attachments for ``company``, newest first."""
    stmt = select(IncomingMailDocument).where(IncomingMailDocument.company_id == company.id)
    if status is not None:
        stmt = stmt.where(IncomingMailDocument.status == status)
    stmt = (
        stmt.order_by(IncomingMailDocument.received_at.desc(), IncomingMailDocument.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(await db.scalars(stmt))


async def get_mail_document(
    db: AsyncSession, *, company: Company, mail_id: int
) -> IncomingMailDocument:
    """Return one received email attachment belonging to ``company``, or 404."""
    doc = await db.scalar(
        select(IncomingMailDocument).where(
            IncomingMailDocument.id == mail_id,
            IncomingMailDocument.company_id == company.id,
        )
    )
    if doc is None:
        raise MailDocumentNotFoundError("finner ikke e-postfakturaen")
    return doc


async def dismiss_mail_document(
    db: AsyncSession, *, company: Company, mail_id: int
) -> IncomingMailDocument:
    """Move an unbooked email attachment to ``avvist`` without deleting it."""
    doc = await db.scalar(
        select(IncomingMailDocument)
        .where(
            IncomingMailDocument.id == mail_id,
            IncomingMailDocument.company_id == company.id,
        )
        .with_for_update()
    )
    if doc is None:
        raise MailDocumentNotFoundError("finner ikke e-postfakturaen")
    if doc.status is MailDocumentStatus.BOKFORT:
        raise MailDocumentAlreadyBookedError("e-postfakturaen er allerede bokført")
    if doc.status is not MailDocumentStatus.AVVIST:
        doc.status = MailDocumentStatus.AVVIST
        doc.dismissed_at = utc_now()
        await db.flush()
    return doc


async def reopen_mail_document(
    db: AsyncSession, *, company: Company, mail_id: int
) -> IncomingMailDocument:
    """Move a dismissed email attachment back to ``ny`` so it can be booked."""
    doc = await db.scalar(
        select(IncomingMailDocument)
        .where(
            IncomingMailDocument.id == mail_id,
            IncomingMailDocument.company_id == company.id,
        )
        .with_for_update()
    )
    if doc is None:
        raise MailDocumentNotFoundError("finner ikke e-postfakturaen")
    if doc.status is MailDocumentStatus.BOKFORT:
        raise MailDocumentAlreadyBookedError("e-postfakturaen er allerede bokført")
    if doc.status is MailDocumentStatus.AVVIST:
        doc.status = MailDocumentStatus.NY
        doc.dismissed_at = None
        await db.flush()
    return doc


async def book_mail_document(
    db: AsyncSession,
    storage: DocumentStorage,
    *,
    company: Company,
    mail_id: int,
    type_code: str,
    occurred_on: date,
    gross_amount: Decimal,
    description: str | None = None,
    tax_code: str | None = None,
    payment_method: PaymentMethod = PaymentMethod.BANK,
    party_id: int | None = None,
    account_number: str | None = None,
    dimension_value_ids: Sequence[int] = (),
    created_by_user_id: int | None = None,
    created_via_agent_key_id: int | None = None,
) -> tuple[IncomingMailDocument, Transaction]:
    """Book a received email attachment into the ledger and archive it on the voucher."""
    doc = await db.scalar(
        select(IncomingMailDocument)
        .where(
            IncomingMailDocument.id == mail_id,
            IncomingMailDocument.company_id == company.id,
        )
        .with_for_update()
    )
    if doc is None:
        raise MailDocumentNotFoundError("finner ikke e-postfakturaen")
    if doc.status is MailDocumentStatus.BOKFORT:
        raise MailDocumentAlreadyBookedError("e-postfakturaen er allerede bokført")
    if doc.status is MailDocumentStatus.AVVIST:
        raise MailDocumentDismissedError(
            "e-postfakturaen er avvist. Åpne den på nytt før du bokfører den"
        )

    fallback_text = doc.subject or doc.filename or doc.sender_email
    text = (description or fallback_text).strip()[:255]

    voucher = await ledger_service.record_cash_flow(
        db,
        company=company,
        type_code=type_code,
        occurred_on=occurred_on,
        gross_amount=gross_amount,
        description=text,
        tax_code=tax_code,
        payment_method=payment_method,
        party_pk=party_id,
        created_by_user_id=created_by_user_id,
        account_number=account_number,
        dimension_value_ids=dimension_value_ids,
    )
    if created_via_agent_key_id is not None:
        voucher.created_via_agent_key_id = created_via_agent_key_id

    raw_bytes = await storage.load(doc.storage_key)
    await bilag_service.attach_document(
        db,
        storage,
        company=company,
        transaction=voucher,
        content=raw_bytes,
        filename=doc.filename,
        uploaded_by_user_id=created_by_user_id,
    )

    doc.status = MailDocumentStatus.BOKFORT
    doc.voucher_id = voucher.id
    doc.booked_at = utc_now()
    await db.flush()
    return doc, voucher
