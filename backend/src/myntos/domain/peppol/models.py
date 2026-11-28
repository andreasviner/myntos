"""A company's registration with the Peppol access point, sent and received EHFs.

Three tables, and none of them has a column that points at a person. Who pressed
«Send» is in the approval log when a key asked, and in nobody's row when a
person did; who booked a received EHF is on the posted voucher's
``created_by_user_id``. Keeping user foreign keys out of these tables avoids
adding one more thing for docs/gdpr.md to tombstone, for answers the ledger
already holds.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from myntos.core.clock import utc_now
from myntos.db.base import Base


class PeppolRegistration(Base):
    """The company as Ademico knows it.

    Made when an admin registers the company for e-invoicing, which requires
    the company to have been confirmed through Altinn first
    (myntos.domain.peppol.service.require_registrable): the access point
    answers to anybody holding the deployment's credentials, so Myntos is the
    only thing standing between an orgnr typed into a new account and invoices
    sent or received in that business's name.

    Sending is local at Ademico (``peppolRegistration=false``) and may be shared
    when two sets of books in Myntos are both confirmed for the same orgnr.
    Receiving (``receiving_enabled=True``, ``peppolRegistration=true``) publishes
    the orgnr in SML/SMP (ELMA) and routes incoming supplier invoices to one
    specific set of books, so ``uq_peppol_registration_receiving_orgnr`` allows
    at most one active receiving company per orgnr across the whole database.
    """

    __tablename__ = "peppol_registration"
    __table_args__ = (
        Index(
            "uq_peppol_registration_receiving_orgnr",
            "orgnr",
            unique=True,
            sqlite_where=text("receiving_enabled = 1 AND orgnr IS NOT NULL"),
            postgresql_where=text("receiving_enabled IS TRUE AND orgnr IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("company.id", ondelete="CASCADE"), unique=True
    )
    # Ademico's legalEntityId (stringified integer). Shared by Myntos companies
    # with the same orgnr when sending.
    remote_company_id: Mapped[str] = mapped_column(String(64))
    # Ademico's peppolRegistrationId under remote_company_id, needed to toggle
    # SMP/ELMA receiving on or off without recreating the legal entity.
    remote_registration_id: Mapped[str | None] = mapped_column(String(64))
    # Denormalised from company.orgnr so receiving can be routed by receiver
    # orgnr in one indexed query and kept globally unique while receiving is on.
    orgnr: Mapped[str | None] = mapped_column(String(9))
    # When the company became allowed to send. With Ademico, Altinn ownership
    # confirmation is the check, so this is stamped at registration time.
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Whether this company's orgnr is published in SML/SMP through Ademico to
    # receive supplier invoices into Myntos.
    receiving_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    receiving_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    @property
    def is_verified(self) -> bool:
        return self.verified_at is not None


class TransmissionStatus(enum.StrEnum):
    """Where a sent invoice stands, in Myntos's own three words."""

    # Queued at Ademico; the customer's access point has not acknowledged it yet.
    PENDING = "pending"
    # Handed over AS4 and acknowledged by the customer's access point.
    DELIVERED = "delivered"
    # Delivery failed or was rejected. The only state that allows sending again.
    FAILED = "failed"


class PeppolTransmission(Base):
    """One send of one issued invoice or credit note.

    Kept for as long as the invoice is: that the EHF went, when and to which
    address is part of what the seller can show about the sale.
    """

    __tablename__ = "peppol_transmission"
    __table_args__ = (
        # One transmission per document that has not failed. A second send of
        # an invoice that went is a second invoice in the customer's books,
        # which they may well pay twice. A failed one may be tried again, which
        # is why this is a partial index rather than a unique column.
        Index(
            "uq_peppol_transmission_live",
            "document_pk",
            unique=True,
            sqlite_where=text("status != 'failed'"),
            postgresql_where=text("status != 'failed'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    # CASCADE like the document's lines: the only documents ever deleted are
    # drafts, which are never sent, and a company's books at the end of their
    # retention (docs/gdpr.md), when everything about them goes.
    document_pk: Mapped[int] = mapped_column(ForeignKey("sales_document.id", ondelete="CASCADE"))
    # Ademico's transmissionId, used to follow delivery status via documents
    # lookup or incoming sending notifications.
    remote_document_id: Mapped[str] = mapped_column(String(128))
    # The Peppol address it went to, ``0192:<orgnr>``. Stored rather than read
    # off the document because it is the fact of the send, not of the invoice.
    recipient: Mapped[str] = mapped_column(String(64))
    status: Mapped[TransmissionStatus] = mapped_column(
        Enum(
            TransmissionStatus,
            name="peppol_transmission_status",
            native_enum=False,
            length=16,
            values_callable=lambda e: [m.value for m in e],
        )
    )
    failure_category: Mapped[str | None] = mapped_column(String(32))
    failure_message: Mapped[str | None] = mapped_column(String(1000))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    # When Myntos last updated delivery status from Ademico.
    status_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IncomingDocumentType(enum.StrEnum):
    """Kind of incoming EHF received over Peppol."""

    FAKTURA = "faktura"
    KREDITNOTA = "kreditnota"


class IncomingStatus(enum.StrEnum):
    """Lifecycle of a received EHF in the company's inbox."""

    # Arrived from Peppol and waiting to be reviewed and booked.
    NY = "ny"
    # Booked as a voucher in the ledger, with its PDF filed in the bilag archive.
    BOKFORT = "bokfort"
    # Dismissed (already booked manually or sent in error). Kept, never deleted:
    # bokføringsloven § 13 keeps incoming primary documentation.
    AVVIST = "avvist"


class PeppolIncomingDocument(Base):
    """An EHF invoice or credit note received over Peppol for a company.

    Append-only in the sense that matters for bokføringsloven § 13: the raw
    received UBL XML and its SHA-256 digest are frozen as they arrived and never
    deleted while the company's books exist. Booking links it to a posted
    voucher (``voucher_id``) and archives a human-readable PDF bilag beside it.
    """

    __tablename__ = "peppol_incoming_document"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "transmission_id", name="uq_peppol_incoming_company_transmission"
        ),
        Index("ix_peppol_incoming_company_status", "company_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    # Ademico's transmissionId for this incoming delivery. Combined with
    # company_id, makes polling crash-safe and idempotent.
    transmission_id: Mapped[str] = mapped_column(String(128))
    sbdh_transmission_id: Mapped[str | None] = mapped_column(String(128))
    document_type: Mapped[IncomingDocumentType] = mapped_column(
        Enum(
            IncomingDocumentType,
            name="peppol_incoming_document_type",
            native_enum=False,
            length=16,
            values_callable=lambda e: [m.value for m in e],
        )
    )
    invoice_number: Mapped[str] = mapped_column(String(128))
    issue_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(8), default="NOK")
    net_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    vat_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    seller_name: Mapped[str] = mapped_column(String(255))
    seller_orgnr: Mapped[str | None] = mapped_column(String(32))
    seller_peppol_id: Mapped[str] = mapped_column(String(128))
    buyer_reference: Mapped[str | None] = mapped_column(String(255))
    order_reference: Mapped[str | None] = mapped_column(String(255))
    payment_reference: Mapped[str | None] = mapped_column(String(128))
    seller_bank_account: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    # Parsed lines and VAT breakdown groups from the UBL XML (JSON arrays), kept
    # so listings and detail views never re-parse raw XML per row.
    lines_json: Mapped[str] = mapped_column(Text, default="[]")
    tax_subtotals_json: Mapped[str] = mapped_column(Text, default="[]")
    # The exact UBL XML received from Peppol, verbatim, and its SHA-256 digest.
    ehf_xml: Mapped[str] = mapped_column(Text)
    ehf_sha256: Mapped[str] = mapped_column(String(64))
    has_embedded_pdf: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[IncomingStatus] = mapped_column(
        Enum(
            IncomingStatus,
            name="peppol_incoming_status",
            native_enum=False,
            length=16,
            values_callable=lambda e: [m.value for m in e],
        ),
        default=IncomingStatus.NY,
    )
    voucher_id: Mapped[int | None] = mapped_column(
        ForeignKey("gl_transaction.id", ondelete="SET NULL")
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    booked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
