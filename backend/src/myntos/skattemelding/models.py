"""A skattemelding filing: one attempt at telling Skatteetaten what one income year comes to.

The same shape as :class:`myntos.domain.mva.models.MvaFiling`, for the same
reasons. A row is made when the pair is **checked**, because the check already
sends it to Skatteetaten and the answer (what it computed, what it found) is
what the person confirms against; so it is kept with the exact envelope it
answered. Submission uploads *those* bytes to Altinn, and refuses when the books
have moved since: confirming one næringsspesifikasjon and filing another is the
failure the row exists to make impossible.

Rows are never deleted or rewritten once sent. A correction is a new filing for
the same year (a later egenfastsetting replaces the earlier one at
Skatteetaten), so the history of what was filed, when and by whom, is the list
of rows.

**The auditor's step.** A company with a revisor files a næringsspesifikasjon
the revisor confirms (``skalBekreftesAvRevisor``), and the Altinn instance is
made with the flag so the process has a step for it. The person's own ``next``
then leaves the instance waiting for the revisor (``hos_revisor``), who
confirms in Skatteetaten's own viewer, not in Myntos. Only after that does
Skatteetaten process the melding and leave its answer (``tilbakemelding``) on
the instance.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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


class SkattemeldingStatus(enum.StrEnum):
    # Checked, and Skatteetaten would take it. Not sent.
    VALIDERT = "validert"
    # Checked, and Skatteetaten said it would refuse it.
    AVVIST = "avvist"
    # Sent through Altinn; Skatteetaten's answer has not arrived yet.
    SENDT = "sendt"
    # Sent by the person, and waiting for the revisor to confirm it in
    # Skatteetaten's viewer before Skatteetaten processes it.
    HOS_REVISOR = "hos_revisor"
    # Sent, and Skatteetaten's answer is stored.
    MOTTATT = "mottatt"
    # The submission started and stopped partway. The instance may exist in
    # Altinn; ``error`` says where it stopped.
    FEILET = "feilet"

    @property
    def is_filed(self) -> bool:
        """Sent *from Myntos*: there is an Altinn instance, and an answer to fetch."""
        return self in {
            SkattemeldingStatus.SENDT,
            SkattemeldingStatus.HOS_REVISOR,
            SkattemeldingStatus.MOTTATT,
        }


class SkattemeldingFiling(Base):
    __tablename__ = "skattemelding_filing"
    __table_args__ = (Index("ix_skattemelding_filing_company_id_year", "company_id", "year"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    year: Mapped[int] = mapped_column(Integer)
    # A plain string, as on mva_filing: a status added later is a code change.
    status: Mapped[str] = mapped_column(String(16))

    # The skattemelding the envelope answers, as Skatteetaten named it in the
    # fetch (``dokumentidentifikator``, ``SKI:755:32858``), and its taxpayer
    # number, which the næringsspesifikasjon repeats.
    melding_id: Mapped[str] = mapped_column(String(64))
    # Ten digits, past a 32-bit integer (3001570199 in TT02).
    partsnummer: Mapped[int] = mapped_column(BigInteger)
    # The envelope as checked, which is what is uploaded: the skattemelding and
    # the næringsspesifikasjon inside it, base64, byte for byte.
    request_xml: Mapped[str] = mapped_column(Text)
    # SHA-256 of the næringsspesifikasjon alone. It has no timestamp or
    # reference in it, so the same books build the same bytes, and a fresh
    # build that hashes differently is books that moved.
    content_hash: Mapped[str] = mapped_column(String(64))
    # What the person answered before the check, which the skattemelding
    # carries (``upersonlig.Answers``), so the send rebuilds the same thing.
    answers: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    # Read off Enhetsregisteret at the check. Decides the Altinn instance's
    # revisor step, which cannot be added after the instance exists.
    has_auditor: Mapped[bool] = mapped_column(Boolean)
    # The figures as Myntos computed them at the check, for showing a filing
    # without rebuilding it from books that may have moved on since.
    aarsresultat: Mapped[Decimal] = mapped_column(MONEY)
    skattemessig_resultat: Mapped[Decimal] = mapped_column(MONEY)
    # What Skatteetaten computed, in whole kroner, when it computed any.
    beregnet_skatt: Mapped[Decimal | None] = mapped_column(MONEY)
    # Myntos's own merknader at the check (code, message), which go with the
    # filing because they explain what was sent: real estate left out of the
    # formue, an equity change named «annen».
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSON().with_variant(JSONB, "postgresql"))

    # ``validertOK`` or ``validertMedFeil``, and the whole answer.
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
    # The name ID-porten vouched for, as on mva_filing, and never the number.
    submitted_by_name: Mapped[str | None] = mapped_column(String(255))
    # Altinn's substatus once Skatteetaten has processed it: its label and its
    # description, Skatteetaten's own words for whether it was taken.
    outcome: Mapped[str | None] = mapped_column(String(255))
    outcome_text: Mapped[str | None] = mapped_column(Text)
    feedback_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(1000))

    documents: Mapped[list[SkattemeldingFilingDocument]] = relationship(
        back_populates="filing",
        cascade="all, delete-orphan",
        order_by="SkattemeldingFilingDocument.id",
    )


class SkattemeldingFilingDocument(Base):
    """A document Skatteetaten left on the Altinn instance: its answer, ``tilbakemelding``.

    Stored through :mod:`myntos.storage`, as the MVA receipts are: the year's
    evidence that the filing happened, and what Skatteetaten said about it.
    """

    __tablename__ = "skattemelding_filing_document"
    __table_args__ = (
        UniqueConstraint(
            "filing_id", "data_type", name="uq_skattemelding_filing_document_filing_id"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    filing_id: Mapped[int] = mapped_column(
        ForeignKey("skattemelding_filing.id", ondelete="CASCADE")
    )
    data_type: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    filing: Mapped[SkattemeldingFiling] = relationship(back_populates="documents")
