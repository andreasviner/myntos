"""ORM models for company email inbox addresses and received email invoice attachments.

Neither table holds a foreign key to ``app_user`` (following the same GDPR
tombstone principle as :mod:`myntos.domain.peppol.models`): who booked an
incoming email invoice is recorded on the resulting posted voucher's
``created_by_user_id``.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from myntos.core.clock import utc_now
from myntos.db.base import Base


class SenderPolicy(enum.StrEnum):
    """How strictly a company's email inbox filters incoming senders."""

    # Accept mail from any sender, displaying sender identity, auth checks
    # (SPF/DKIM/DMARC), and warnings for unknown or mismatched senders.
    ALLE_MED_VARSEL = "alle_med_varsel"
    # Accept mail only from verified company members or explicitly approved
    # sender addresses/domains; silently reject everything else before storage.
    KUN_GODKJENTE = "kun_godkjente"


class MailDocumentStatus(enum.StrEnum):
    """Lifecycle of an email attachment in the company's Mottatt inbox."""

    # Received and waiting to be reviewed and booked.
    NY = "ny"
    # Booked as an immutable voucher in the ledger, with the file copied into
    # the append-only bilag archive.
    BOKFORT = "bokfort"
    # Dismissed (spam, duplicate, or not a bilag). Retained for auditability
    # until the company's books are purged after statutory retention.
    AVVIST = "avvist"


class CompanyInboxAddress(Base):
    """A company's dedicated ``<local_part>@myntos.no`` email address for bilag intake.

    Allocated automatically when a company is created (for example ``Test AS``
    -> ``test@myntos.no``, falling back to ``testas@myntos.no`` and numbered
    suffixes if taken or reserved). ``enabled`` is switched off when a company
    is closed so mail cannot keep arriving in locked books.
    """

    __tablename__ = "company_inbox_address"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("company.id", ondelete="CASCADE"), unique=True
    )
    local_part: Mapped[str] = mapped_column(String(64), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sender_policy: Mapped[SenderPolicy] = mapped_column(
        Enum(
            SenderPolicy,
            name="mottak_sender_policy",
            native_enum=False,
            length=32,
            values_callable=lambda e: [m.value for m in e],
        ),
        default=SenderPolicy.ALLE_MED_VARSEL,
    )
    # JSON array of normalised lowercase entries: either full addresses
    # ("faktura@leverandor.no") or domain rules ("@leverandor.no").
    allowed_senders_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class IncomingMailDocument(Base):
    """One validated invoice or receipt attachment received by email.

    An email carrying two PDFs produces two rows sharing ``message_id`` with
    distinct ``attachment_index`` values, so each invoice can be booked or
    dismissed independently. The raw file bytes live in ``DocumentStorage``
    (EU R2 in production) under ``storage_key``.
    """

    __tablename__ = "incoming_mail_document"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "message_id",
            "attachment_index",
            name="uq_incoming_mail_company_message_att",
        ),
        Index("ix_incoming_mail_company_status", "company_id", "status"),
        Index("ix_incoming_mail_company_received", "company_id", "received_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    message_id: Mapped[str] = mapped_column(String(255))
    attachment_index: Mapped[int] = mapped_column(Integer)
    sender_email: Mapped[str] = mapped_column(String(320))
    sender_name: Mapped[str | None] = mapped_column(String(255))
    envelope_from: Mapped[str] = mapped_column(String(320))
    reply_to: Mapped[str | None] = mapped_column(String(320))
    recipient_address: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(500), default="")
    # Plain-text excerpt only (never HTML), capped at 1000 characters.
    body_excerpt: Mapped[str | None] = mapped_column(Text)
    # Authentication results parsed from the outermost Authentication-Results header.
    spf_result: Mapped[str | None] = mapped_column(String(32))
    dkim_result: Mapped[str | None] = mapped_column(String(32))
    dmarc_result: Mapped[str | None] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    status: Mapped[MailDocumentStatus] = mapped_column(
        Enum(
            MailDocumentStatus,
            name="mottak_mail_status",
            native_enum=False,
            length=16,
            values_callable=lambda e: [m.value for m in e],
        ),
        default=MailDocumentStatus.NY,
    )
    voucher_id: Mapped[int | None] = mapped_column(
        ForeignKey("gl_transaction.id", ondelete="SET NULL")
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    booked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
