"""ORM models for årsregnskap (RR-0002) drafts, filings, and archived documents.

Norwegian regnskapsloven § 3-1 and § 8-2 require every aksjeselskap (AS/ASA)
to prepare annual accounts (resultatregnskap, balanse, and mandatory notes)
and file them with Regnskapsregisteret (Brønnøysundregistrene) via Altinn 3
(``brg/aarsregnskap-vanlig-202406``) within one month after adoption by the
general meeting and no later than 31 July.

All tables carry ``company_id`` with ``ON DELETE CASCADE`` so company isolation
is a single predicate and deleting a company past its five-year retention
purges every row cleanly (docs/company-isolation.md, docs/gdpr.md).
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from myntos.db.base import Base
from myntos.domain.ledger.models import MONEY


class AarsregnskapFilingStatus(enum.StrEnum):
    # Instance created and XML/attachments uploaded, still in Utfylling (not locked).
    KLARGJORT = "klargjort"
    # Locked via PUT process/next {"action": "confirm"}, waiting in Signering.
    LAAST = "laast"
    # Signed via ID-porten PUT process/next {"action": "sign"}; auto-submitted to Brreg.
    SIGNERT = "signert"
    # Receipt (ref-data-as-pdf) fetched and archived from Altinn after process ended.
    MOTTATT = "mottatt"
    # Stopped partway; altinn_instance_id and error record where it stopped.
    FEILET = "feilet"

    @property
    def is_filed(self) -> bool:
        return self in (AarsregnskapFilingStatus.SIGNERT, AarsregnskapFilingStatus.MOTTATT)


class AarsregnskapDraft(Base):
    """Per-year editable settings, prior-year overrides, and notes for årsregnskapet."""

    __tablename__ = "aarsregnskap_draft"
    __table_args__ = (
        UniqueConstraint("company_id", "year", name="uq_aarsregnskap_draft_co_year"),
        Index("ix_aarsregnskap_draft_co_id", "company_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    year: Mapped[int] = mapped_column(Integer)

    # Regnskapsprinsipper og selskapsopplysninger (RR-0002 Hovedskjema).
    small_company: Mapped[bool] = mapped_column(Boolean, default=True)
    is_parent: Mapped[bool] = mapped_column(Boolean, default=False)
    consolidated: Mapped[bool] = mapped_column(Boolean, default=False)
    winding_up_accounts: Mapped[bool] = mapped_column(Boolean, default=False)
    auditor_elected_out: Mapped[bool] = mapped_column(Boolean, default=True)
    prepared_by_external_accountant: Mapped[bool] = mapped_column(Boolean, default=False)
    external_accountant_assistance: Mapped[bool] = mapped_column(Boolean, default=False)

    # Fastsettelse (generalforsamling) og kontaktinformasjon.
    approval_date: Mapped[date | None] = mapped_column(Date)
    confirming_representative: Mapped[str | None] = mapped_column(String(70))
    contact_email: Mapped[str | None] = mapped_column(String(70))

    # Styre- og generalforsamlingsprotokoll (for utskrift og arkiv).
    board_meeting_date: Mapped[date | None] = mapped_column(Date)
    board_place: Mapped[str | None] = mapped_column(String(120))
    board_members: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        default=list,
    )
    general_meeting_date: Mapped[date | None] = mapped_column(Date)
    general_meeting_place: Mapped[str | None] = mapped_column(String(120))
    general_meeting_chair: Mapped[str | None] = mapped_column(String(120))
    general_meeting_cosigner: Mapped[str | None] = mapped_column(String(120))

    # Foreslått utbytte (kr) for disponeringsforslag i protokoll/noter dersom ikke ført ennå.
    dividend_proposed: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"))

    # Optional manual prior-year figures (line_key -> whole NOK integer) for companies
    # whose prior financial year was not kept in Myntos.
    prior_year_overrides: Mapped[dict[str, int]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        default=dict,
    )

    # Structured small-company notes (regnskapsloven § 7-35 ff.) and custom notes.
    notes: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        default=dict,
    )

    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AarsregnskapFiling(Base):
    """One RR-0002 submission attempt to Regnskapsregisteret via Altinn 3."""

    __tablename__ = "aarsregnskap_filing"
    __table_args__ = (Index("ix_aarsregnskap_filing_co_year", "company_id", "year"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    year: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    # ``systembruker`` or ``id_porten``: how the instance was created and filled.
    auth_method: Mapped[str] = mapped_column(String(24))

    altinn_instance_id: Mapped[str | None] = mapped_column(String(128))
    hovedskjema_data_id: Mapped[str | None] = mapped_column(String(80))
    underskjema_data_id: Mapped[str | None] = mapped_column(String(80))
    altinn_portal_url: Mapped[str | None] = mapped_column(String(512))

    content_hash: Mapped[str] = mapped_column(String(64))
    aarsresultat: Mapped[Decimal] = mapped_column(MONEY)
    sum_eiendeler: Mapped[Decimal] = mapped_column(MONEY)
    sum_egenkapital: Mapped[Decimal] = mapped_column(MONEY)
    fastsettelsedato: Mapped[date | None] = mapped_column(Date)
    bekreftende_representant: Mapped[str | None] = mapped_column(String(70))

    hovedskjema_xml: Mapped[str] = mapped_column(Text)
    underskjema_xml: Mapped[str] = mapped_column(Text)

    validation_issues: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        default=list,
    )
    findings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        default=list,
    )

    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    signed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    signed_by_name: Mapped[str | None] = mapped_column(String(150))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(1000))

    documents: Mapped[list[AarsregnskapFilingDocument]] = relationship(
        back_populates="filing",
        cascade="all, delete-orphan",
        order_by="AarsregnskapFilingDocument.id",
    )


class AarsregnskapFilingDocument(Base):
    """Archived XML, PDF, or receipt belonging to an :class:`AarsregnskapFiling`."""

    __tablename__ = "aarsregnskap_filing_document"
    __table_args__ = (Index("ix_aarsregnskap_doc_filing_id", "filing_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    filing_id: Mapped[int] = mapped_column(ForeignKey("aarsregnskap_filing.id", ondelete="CASCADE"))
    # ``hovedskjema``, ``underskjema``, ``vedlegg_pdf``, ``kvittering_pdf``, ``signatur_json``.
    document_type: Mapped[str] = mapped_column(String(64))
    external_document_id: Mapped[str | None] = mapped_column(String(80))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    filing: Mapped[AarsregnskapFiling] = relationship(back_populates="documents")
