"""A filing: one attempt at telling Skatteetaten what one period comes to.

A row is created when a melding is **validated**, not when it is submitted.
Validation already sends the melding to Skatteetaten, and the answer is the
thing the user confirms against, so it has to be kept with the exact bytes it
was an answer to. Submission then sends *those* bytes, and refuses if the
ledger has moved since: confirming one melding and filing another is the
failure this table exists to make impossible.

Rows are never deleted or rewritten once sent. A correction is a new filing for
the same period (Skatteetaten replaces the earlier melding with the later one),
so the history of what was filed, when and by whom, is the list of rows.

The three documents Skatteetaten sends back are stored through
:mod:`myntos.storage` and listed in :class:`MvaFilingDocument`. They are the
period's bilag in bokføringsloven's sense: the receipt is the evidence that the
filing happened, and the payment information is where the money went.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
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


class FilingStatus(enum.StrEnum):
    # Validated and accepted for submission, not yet sent.
    VALIDERT = "validert"
    # Validated, and Skatteetaten said it would refuse it.
    AVVIST = "avvist"
    # Sent through Altinn; the receipt has not arrived yet.
    SENDT = "sendt"
    # Sent, and all three feedback documents are stored.
    MOTTATT = "mottatt"
    # The submission started and stopped partway. The instance may exist in
    # Altinn; ``error`` says where it stopped.
    FEILET = "feilet"

    @property
    def is_filed(self) -> bool:
        return self in {FilingStatus.SENDT, FilingStatus.MOTTATT}


class MvaFiling(Base):
    __tablename__ = "mva_filing"
    __table_args__ = (
        Index("ix_mva_filing_period", "company_id", "year", "period_type", "period_index"),
        UniqueConstraint("reference", name="uq_mva_filing_reference"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    year: Mapped[int] = mapped_column(Integer)
    period_type: Mapped[str] = mapped_column(String(32))
    period_index: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(32))
    # A plain string rather than a database enum: a status added later should be
    # a code change, not an ALTER TYPE on a table that holds filings.
    status: Mapped[str] = mapped_column(String(16))

    # ``regnskapssystemsreferanse`` in the melding. A UUID, so it is unique
    # across every installation that might ever file for the same orgnr.
    reference: Mapped[str] = mapped_column(String(36))
    melding_xml: Mapped[str] = mapped_column(Text)
    # The hash the submission compares against a freshly built melding. Over
    # the *lines*, not the XML: the XML carries the reference and a timestamp,
    # which differ on every build of an identical melding.
    content_hash: Mapped[str] = mapped_column(String(64))
    fastsatt_merverdiavgift: Mapped[Decimal] = mapped_column(MONEY)
    # The lines as they were validated, for showing a filed melding without
    # rebuilding it from a ledger that may have moved on since.
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    merknad: Mapped[str | None] = mapped_column(Text)

    validation_status: Mapped[str] = mapped_column(String(64))
    validation_xml: Mapped[str] = mapped_column(Text)
    validated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # SET NULL for the reason Transaction.created_by_user_id gives: the filing
    # outlives the account of the person who made it.
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )

    altinn_instance_id: Mapped[str | None] = mapped_column(String(80))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    # The name ID-porten vouched for, kept as text. Not the fødselsnummer: the
    # receipt from Skatteetaten already names the person, and a copy of the
    # number here would be one more place it can leak from.
    submitted_by_name: Mapped[str | None] = mapped_column(String(255))
    feedback_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(1000))

    # Read out of betalingsinformasjon.xml, so the period's page can say what
    # to pay, where to, and by when, without opening a file.
    payment_kid: Mapped[str | None] = mapped_column(String(64))
    payment_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    payment_due: Mapped[date | None] = mapped_column(Date)
    payment_account: Mapped[str | None] = mapped_column(String(32))

    # The voucher that moved the period's 2700 and 2710 onto 2740. NULL for a
    # period with nothing to move, and SET NULL because the filing is the
    # older fact.
    settlement_transaction_pk: Mapped[int | None] = mapped_column(
        ForeignKey("gl_transaction.id", ondelete="SET NULL")
    )

    documents: Mapped[list[MvaFilingDocument]] = relationship(
        back_populates="filing", cascade="all, delete-orphan", order_by="MvaFilingDocument.id"
    )


class MvaFilingDocument(Base):
    """One of the documents Skatteetaten returned for a filing."""

    __tablename__ = "mva_filing_document"
    __table_args__ = (
        UniqueConstraint("filing_id", "data_type", name="uq_mva_filing_document_filing_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    filing_id: Mapped[int] = mapped_column(ForeignKey("mva_filing.id", ondelete="CASCADE"))
    # Altinn's dataType: valideringsresultat, betalingsinformasjon, kvittering.
    data_type: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    filing: Mapped[MvaFiling] = relationship(back_populates="documents")
