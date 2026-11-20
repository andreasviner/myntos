"""A company's registration with the access point, and every invoice it sent.

Two tables, and neither has a column that points at a person. Who pressed
«Send» is in the approval log when a key asked, and in nobody's row when a
person did: a column naming them would be one more thing for docs/gdpr.md to
tombstone, for an answer the invoice itself does not need.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from myntos.core.clock import utc_now
from myntos.db.base import Base


class PeppolRegistration(Base):
    """The company as the access point knows it.

    Made when an admin registers the company for e-invoicing, which requires
    the company to have been confirmed through Altinn first
    (myntos.domain.peppol.service.require_registrable): the access point
    answers to anybody holding the deployment's key, so Myntos is the only
    thing standing between an orgnr typed into a new account and invoices sent
    in that business's name.
    """

    __tablename__ = "peppol_registration"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("company.id", ondelete="CASCADE"), unique=True
    )
    # Recommand's id for the company. Shared by every Myntos company with the
    # same orgnr: see myntos.integrations.recommand.find_company.
    remote_company_id: Mapped[str] = mapped_column(String(64))
    # When the access point first said the identity check was done. Kept so the
    # page stops asking once it is; NULL until then.
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    @property
    def is_verified(self) -> bool:
        return self.verified_at is not None


class TransmissionStatus(enum.StrEnum):
    """Where a sent invoice stands, in the access point's own three words."""

    # Handed to the network; the customer's access point has not acknowledged it.
    PENDING = "pending"
    # The customer's access point acknowledged it. Delivered, in the sense the
    # law means: it is in the customer's system.
    DELIVERED = "delivered"
    # It did not arrive. The only state that allows sending it again.
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
    # Recommand's id for what it stored, which is what a later status check asks
    # about.
    remote_document_id: Mapped[str] = mapped_column(String(64))
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
    # Recommand's fixed vocabulary (``recipient_not_found``, ``validation``…)
    # and the access point's own words, set only when ``status`` is failed.
    failure_category: Mapped[str | None] = mapped_column(String(32))
    failure_message: Mapped[str | None] = mapped_column(String(1000))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    # When Myntos last asked how it stood. NULL until the first check after the
    # send.
    status_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
