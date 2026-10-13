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
    # Filed from another program: the previous one, during a parallel run or
    # before a move in the middle of a year. A person's statement, recorded so
    # Myntos stops offering to file a term somebody else has filed, because a
    # second melding for the term would *replace* the first at Skatteetaten.
    # Not a filing (nothing was sent from here), and undoable: details and the
    # undo are on :class:`MvaFilingElsewhere`.
    ANNET_PROGRAM = "annet_program"
    # An ANNET_PROGRAM statement taken back. Kept rather than deleted, so the
    # term's history still says who said it was filed elsewhere and who
    # changed their mind.
    OPPHEVET = "opphevet"

    @property
    def is_filed(self) -> bool:
        """Filed *from Myntos*: there is an Altinn instance and a receipt to fetch."""
        return self in {FilingStatus.SENDT, FilingStatus.MOTTATT}

    @property
    def closes_term(self) -> bool:
        """Whether the term needs nothing more from this installation.

        True for a melding Myntos filed and for one somebody says was filed
        elsewhere: neither is overdue, and neither may be filed again by
        accident.
        """
        return self.is_filed or self is FilingStatus.ANNET_PROGRAM


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
    # Set on a row whose status is ``annet_program`` or ``opphevet``: when the
    # other program filed it, what it said, and whether the statement stands.
    # Eager, because every listing of a term's filings shows it.
    elsewhere: Mapped[MvaFilingElsewhere | None] = relationship(
        back_populates="filing", cascade="all, delete-orphan", uselist=False, lazy="selectin"
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


class MvaFilingElsewhere(Base):
    """A term filed from another program, as a person recorded it.

    The row it belongs to is an :class:`MvaFiling` with status
    ``annet_program``, so every place that asks "what happened to this term"
    (the period list, the overdue count, the refusal to file it again, an
    agent's request to file it) finds it where it finds Myntos's own filings.
    That row keeps Myntos's own figures for the term as they stood when the
    statement was made (``fastsatt_merverdiavgift``, ``lines``,
    ``content_hash``); nothing in it was sent from here, so it has no XML and no
    validation.

    This table is what the other program said: the day it filed and, when the
    person knows it, the amount it filed (``amount``, positive owed, negative a
    refund). ``voucher_pk`` is the old program's settlement voucher the import
    brought over, when the statement was proposed from one.

    **Undoable**, because it is a statement and not a filing: a person who
    marked the wrong term, or who wants to file a correction for it from Myntos
    (a newer melding replaces the old program's at Skatteetaten), takes it back.
    The row stays, with who took it back and when, and its filing's status
    becomes ``opphevet``.
    """

    __tablename__ = "mva_filing_elsewhere"
    __table_args__ = (UniqueConstraint("filing_id", name="uq_mva_filing_elsewhere_filing_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    filing_id: Mapped[int] = mapped_column(ForeignKey("mva_filing.id", ondelete="CASCADE"))
    filed_on: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal | None] = mapped_column(MONEY)
    # SET NULL: the statement is the person's, and outlives the voucher it was
    # proposed from (an import reversed, a voucher removed with its company).
    voucher_pk: Mapped[int | None] = mapped_column(
        ForeignKey("gl_transaction.id", ondelete="SET NULL")
    )
    # The key a statement was recorded or taken back through, beside the person
    # (docs/agents-and-api.md rule 6). The person is on the filing row.
    created_via_agent_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_key.id", ondelete="SET NULL")
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # SET NULL, and kept as a tombstone by erasure, like every actor column
    # on a filing (docs/gdpr.md).
    withdrawn_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    withdrawn_via_agent_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_key.id", ondelete="SET NULL")
    )

    filing: Mapped[MvaFiling] = relationship(back_populates="elsewhere")


class MvaSecondNaering(Base):
    """The company's second VAT return, when it files two.

    A farm, a forest or a fisherman files the primærnæring melding; other
    business beside it goes in the same melding only while its taxable sales
    stay at or under 30 000 kroner a year (merverdiavgiftsloven § 15-1 (1)).
    Above that the company files an alminnelig melding as well
    (Merverdiavgiftshåndboken 15-1.3), each on its own terms: the primærnæring
    one yearly unless Skatteetaten agreed to two months
    (skatteforvaltningsforskriften §§ 8-3-7, 8-3-8 (3)), the alminnelig one
    every two months unless it was granted a year (§ 8-3-3).

    The **main** melding is the company's own columns, ``vat_category`` and
    ``vat_period_type``, untouched, so a company with one melding is exactly
    what it was. This row is the second one, at most one per company, and its
    category is always the other of the two Myntos files: there is no such
    thing as two alminnelige meldinger for one orgnr.

    ``valid_from`` is the day the second melding starts: lines before it belong
    to the main melding whatever their account, because until then the other
    business was reported there, as § 15-1 (1) allows.
    """

    __tablename__ = "mva_second_naering"
    __table_args__ = (UniqueConstraint("company_id", name="uq_mva_second_naering_company_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    period_type: Mapped[str] = mapped_column(String(32))
    valid_from: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MvaSecondNaeringAccount(Base):
    """An account whose VAT goes in the second melding.

    By account, and not by register category or by a choice on every line,
    because every way a line reaches the ledger already chooses an account (the
    register, invoices, bank lines, imported vouchers, manual vouchers), so the
    rule reaches all of them without any of them knowing about it, and a farm
    keeps its other business on accounts of its own anyway for the
    næringsspesifikasjon. See docs/integrations/mva.md, «Two returns».

    Unique on the account: an account belongs to one company and to one melding.
    """

    __tablename__ = "mva_second_naering_account"
    __table_args__ = (
        UniqueConstraint("account_id", name="uq_mva_second_naering_account_account_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"))
