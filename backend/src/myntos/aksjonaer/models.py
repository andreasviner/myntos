"""ORM models for the shareholder register (aksjeeierbok) and RF-1086 filings.

Norwegian aksjeloven § 4-5 requires every aksjeselskap (AS) to keep an
aksjeeierbok listing share classes, shareholders, share counts, and changes.
Skatteetaten requires every AS to file Aksjonærregisteroppgaven (RF-1086) by
31 January each year through an end-user system.

All tables carry ``company_id`` with ``ON DELETE CASCADE`` so company isolation
is a single predicate and deleting a company past its five-year retention purges
every row cleanly (docs/company-isolation.md, docs/gdpr.md).
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from myntos.db.base import Base
from myntos.domain.ledger.models import MONEY

# Par values and per-share amounts in RF-1086 XSD allow up to 6 decimals.
PER_SHARE = Numeric(18, 6)


class AksjonaerFilingStatus(enum.StrEnum):
    # Hovedskjema and/or underskjemaer started; not yet confirmed.
    SENDT = "sendt"
    # Confirmed via POST /{year}/{hovedskjemaId}/bekreft with leveranseReferanse.
    BEKREFTET = "bekreftet"
    # Stopped partway; hovedskjema_id and error record where it stopped.
    FEILET = "feilet"

    @property
    def is_filed(self) -> bool:
        return self is AksjonaerFilingStatus.BEKREFTET


class ShareClass(Base):
    """One share class (aksjeklasse, e.g. ``01`` Ordinære aksjer) for a company."""

    __tablename__ = "share_class"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_share_class_company_code"),
        Index("ix_share_class_company_id", "company_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    # Skatteetaten KodelisteAksjeklasse (01..13).
    code: Mapped[str] = mapped_column(String(2), default="01")
    name: Mapped[str] = mapped_column(String(80), default="Ordinære aksjer")
    isin: Mapped[str | None] = mapped_column(String(35))
    # Opening state as of 31.12 of ``opening_year`` (01.01 of opening_year + 1).
    # Null ``opening_year`` means the class starts from 0 before its first event.
    opening_year: Mapped[int | None] = mapped_column(Integer)
    opening_shares: Mapped[int] = mapped_column(BigInteger, default=0)
    opening_par_value: Mapped[Decimal] = mapped_column(PER_SHARE, default=Decimal("0"))
    opening_share_capital: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"))
    opening_paid_in_capital: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"))
    opening_paid_in_premium: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    opening_holdings: Mapped[list[ShareOpeningHolding]] = relationship(
        back_populates="share_class",
        cascade="all, delete-orphan",
        order_by="ShareOpeningHolding.id",
    )
    events: Mapped[list[ShareEvent]] = relationship(
        back_populates="share_class",
        cascade="all, delete-orphan",
        order_by="ShareEvent.event_date, ShareEvent.id",
    )


class Shareholder(Base):
    """One shareholder recorded in the company's aksjeeierbok."""

    __tablename__ = "shareholder"
    __table_args__ = (Index("ix_shareholder_company_id", "company_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    # ``person``, ``selskap``, or ``utenlandsk``.
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(150))
    # Norwegian fødselsnummer or D-nummer (11 digits), stored so RF-1086-U can
    # identify the shareholder to Skatteetaten (personopplysningsloven § 12 /
    # skatteforvaltningsloven § 7-7). Masked in JSON listings; never logged.
    national_id: Mapped[str | None] = mapped_column(String(11))
    # Norwegian organisasjonsnummer (9 digits) for corporate shareholders.
    orgnr: Mapped[str | None] = mapped_column(String(9))
    # Foreign shareholder id issued by Skatteetaten (``UTL`` + 9 digits).
    foreign_id: Mapped[str | None] = mapped_column(String(12))
    address: Mapped[str | None] = mapped_column(String(105))
    postal_code: Mapped[str | None] = mapped_column(String(15))
    city: Mapped[str | None] = mapped_column(String(35))
    country_code: Mapped[str] = mapped_column(String(2), default="NO")
    email: Mapped[str | None] = mapped_column(String(150))
    is_own_shares: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ShareOpeningHolding(Base):
    """Opening share count for one shareholder in one share class."""

    __tablename__ = "share_opening_holding"
    __table_args__ = (
        UniqueConstraint("share_class_id", "shareholder_id", name="uq_share_opening_class_holder"),
        Index("ix_share_opening_holding_company_id", "company_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    share_class_id: Mapped[int] = mapped_column(ForeignKey("share_class.id", ondelete="CASCADE"))
    shareholder_id: Mapped[int] = mapped_column(ForeignKey("shareholder.id", ondelete="CASCADE"))
    shares: Mapped[int] = mapped_column(BigInteger, default=0)

    share_class: Mapped[ShareClass] = relationship(back_populates="opening_holdings")
    shareholder: Mapped[Shareholder] = relationship()


class ShareEvent(Base):
    """One corporate or shareholder event in a share class's history."""

    __tablename__ = "share_event"
    __table_args__ = (
        Index("ix_share_event_company_date", "company_id", "event_date"),
        Index("ix_share_event_share_class_id", "share_class_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    share_class_id: Mapped[int] = mapped_column(ForeignKey("share_class.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(32))
    subtype: Mapped[str | None] = mapped_column(String(32))
    event_date: Mapped[date] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(String(255))
    # Event-level aggregates (meaning depends on event_type; all exact Decimal / int).
    total_shares: Mapped[int | None] = mapped_column(BigInteger)
    par_value_after: Mapped[Decimal | None] = mapped_column(PER_SHARE)
    total_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    premium_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    amount_per_share: Mapped[Decimal | None] = mapped_column(PER_SHARE)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    share_class: Mapped[ShareClass] = relationship(back_populates="events")
    lines: Mapped[list[ShareEventLine]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="ShareEventLine.id",
    )


class ShareEventLine(Base):
    """Per-shareholder allocation belonging to a :class:`ShareEvent`."""

    __tablename__ = "share_event_line"
    __table_args__ = (
        Index("ix_share_event_line_event_id", "event_id"),
        Index("ix_share_event_line_company_id", "company_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    event_id: Mapped[int] = mapped_column(ForeignKey("share_event.id", ondelete="CASCADE"))
    shareholder_id: Mapped[int] = mapped_column(ForeignKey("shareholder.id", ondelete="CASCADE"))
    # ``mottaker`` (acquirer / dividend recipient / affected holder) or ``avgiver`` (seller).
    role: Mapped[str] = mapped_column(String(16), default="mottaker")
    shares: Mapped[int] = mapped_column(BigInteger, default=0)
    amount: Mapped[Decimal | None] = mapped_column(MONEY)
    paid_in_premium: Mapped[Decimal | None] = mapped_column(MONEY)
    withholding_tax: Mapped[Decimal | None] = mapped_column(MONEY)
    withholding_tax_rate: Mapped[int | None] = mapped_column(Integer)
    withholding_tax_country: Mapped[str | None] = mapped_column(String(2))
    counterparty_national_id: Mapped[str | None] = mapped_column(String(11))
    counterparty_orgnr: Mapped[str | None] = mapped_column(String(9))

    event: Mapped[ShareEvent] = relationship(back_populates="lines")
    shareholder: Mapped[Shareholder] = relationship()


class AksjonaerFiling(Base):
    """One RF-1086 submission attempt for one share class and income year."""

    __tablename__ = "aksjonaer_filing"
    __table_args__ = (Index("ix_aksjonaer_filing_company_year", "company_id", "year"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    share_class_id: Mapped[int | None] = mapped_column(
        ForeignKey("share_class.id", ondelete="SET NULL")
    )
    share_class_code: Mapped[str] = mapped_column(String(2))
    year: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    hovedskjema_xml: Mapped[str] = mapped_column(Text)
    underskjema_count: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    hovedskjema_id: Mapped[str | None] = mapped_column(String(80))
    leveranse_referanse: Mapped[str | None] = mapped_column(String(120))
    dialog_id: Mapped[str | None] = mapped_column(String(80))
    forsendelse_id: Mapped[str | None] = mapped_column(String(80))
    contact_email: Mapped[str | None] = mapped_column(String(150))
    contact_name: Mapped[str | None] = mapped_column(String(150))
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    submitted_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    error: Mapped[str | None] = mapped_column(String(1000))

    documents: Mapped[list[AksjonaerFilingDocument]] = relationship(
        back_populates="filing",
        cascade="all, delete-orphan",
        order_by="AksjonaerFilingDocument.id",
    )


class AksjonaerFilingDocument(Base):
    """Archived XML or receipt document belonging to an :class:`AksjonaerFiling`."""

    __tablename__ = "aksjonaer_filing_document"
    __table_args__ = (Index("ix_aksjonaer_filing_document_filing_id", "filing_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    filing_id: Mapped[int] = mapped_column(ForeignKey("aksjonaer_filing.id", ondelete="CASCADE"))
    document_type: Mapped[str] = mapped_column(String(64))
    external_document_id: Mapped[str | None] = mapped_column(String(80))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    filing: Mapped[AksjonaerFiling] = relationship(back_populates="documents")
