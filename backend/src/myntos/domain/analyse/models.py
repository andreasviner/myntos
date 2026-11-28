"""The six ``ki_*`` tables: what KI read in the books, and the runs that read it.

docs/analyse.md «The tables» is the contract, and this module follows it
column for column (one deviation, ``ki_voucher_analysis.model``, said where
it is). Three rules hold for every table:

- **``company_id`` on every row**, a foreign key to ``company`` with
  ``CASCADE``, so the tenancy check is one predicate (like
  ``bilag_attachment``) and a model-made tag, which is a name from one
  company's books, can never be read as another's (docs/analyse.md rule 5).
- **Anything about a voucher or a line cascades from it.**
  ``closure._delete_books`` deletes the vouchers when a company's retention
  ends, and a ``RESTRICT`` here would stop the purge (docs/gdpr.md).
- **A person is ``SET NULL``.** The two columns that point at ``app_user``
  are in gdpr.md's erasure table: erasing the person must not take a run or a
  dismissal with it, the same reason a voucher keeps its row when its author
  goes.

Nothing here is shown to anybody as such (rule 7): the tables are a cache of
what a model said, stamped so it is never paid for twice (rule 6).
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from myntos.core.clock import utc_now
from myntos.db.base import Base

#: JSON on SQLite (the test suite), JSONB on PostgreSQL, as everywhere else.
JSON_DOCUMENT = JSON().with_variant(JSONB, "postgresql")


class Facet(enum.StrEnum):
    """The kind of tag. Fixed in code: a model can never add one."""

    #: What the money was for, finer than the account. Exactly one per line.
    HVA = "hva"
    #: The counterparty, only where the books have none on the voucher.
    MOTPART = "motpart"
    #: What a reviewer would want to look at. Seeds only.
    FLAGG = "flagg"


class TagOrigin(enum.StrEnum):
    """Who made a tag."""

    #: A seed, written by Myntos in code (domain/analyse/vocabulary.py).
    MYNTOS = "myntos"
    #: Proposed by a model for this company, within the caps.
    KI = "ki"


class AnalysisStatus(enum.StrEnum):
    OK = "ok"
    #: Asked twice (in its batch and alone) without an answer that validated.
    FAILED = "failed"


class AnalysisSource(enum.StrEnum):
    #: A model answered.
    KI = "ki"
    #: The software knew without asking: a reversal, or depreciation (rule 3).
    REGNSKAP = "regnskap"


class RunKind(enum.StrEnum):
    MERKING = "merking"
    DYP = "dyp"
    REVISOR = "revisor"
    RYDDING = "rydding"


class RunStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: The statuses the partial unique index counts as active. A tuple and a
#: literal SQL list, because the index's ``WHERE`` must be the same text on
#: both databases and the migration repeats it.
ACTIVE_RUN_STATUSES = (RunStatus.QUEUED.value, RunStatus.RUNNING.value)
ACTIVE_RUN_WHERE = "status IN ('queued', 'running')"


class KiRun(Base):
    """One piece of background KI work for a company: reading vouchers, a review.

    Modelled on ``saft_import``: a row with ``status``, ``step``, ``done`` and
    ``total`` that the page polls, written by work that outlives the request
    which started it (domain/analyse/jobs.py).
    """

    __tablename__ = "ki_run"
    __table_args__ = (
        # One active run per company and kind: pressing the button twice, or
        # two people opening the page at once, finds the run already going
        # rather than starting a second one that would ask Løynd the same
        # questions. An index rather than a check in code, because two requests
        # can both look and both find nothing.
        Index(
            "uq_ki_run_active",
            "company_id",
            "kind",
            unique=True,
            sqlite_where=text(ACTIVE_RUN_WHERE),
            postgresql_where=text(ACTIVE_RUN_WHERE),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("company.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.QUEUED.value)
    requested_level: Mapped[str] = mapped_column(String(10))
    period_from: Mapped[date | None] = mapped_column(Date)
    period_to: Mapped[date | None] = mapped_column(Date)
    step: Mapped[str | None] = mapped_column(String(32))
    done: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    # Counted against the plan: the paid levels only, as every KI box counts.
    tokens_charged: Mapped[int] = mapped_column(BigInteger, default=0)
    # Everything Løynd reported, the free level included: the free level costs
    # Myntos GPU time rather than money, and the operator still wants to see it.
    tokens_total: Mapped[int] = mapped_column(BigInteger, default=0)
    ki_period_id: Mapped[int | None] = mapped_column(
        ForeignKey("ki_period.id", ondelete="SET NULL")
    )
    # The bokmål notices a KI box would show (NO_PLAN, SPENT, REFUSED from
    # myntos.domain.ki.service), so the page can say why a run answered lower.
    notices: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list)
    input_hash: Mapped[str | None] = mapped_column(String(64))
    requested_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    via_agent_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_key.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A code, never text with data in it: the operator's page reads these
    # without the books they came from (docs/operator.md).
    error_code: Mapped[str | None] = mapped_column(String(48))


class KiTag(Base):
    """A tag in one company's vocabulary: a seed copied in, or one a model added."""

    __tablename__ = "ki_tag"
    __table_args__ = (UniqueConstraint("company_id", "facet", "slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("company.id", ondelete="CASCADE"), index=True
    )
    facet: Mapped[str] = mapped_column(String(16))
    slug: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(12))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_by_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ki_run.id", ondelete="SET NULL")
    )
    # Kept rather than deleted when a consolidation run (WP8) merges two tags,
    # so the merge can be undone.
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("ki_tag.id", ondelete="SET NULL"))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KiVoucherAnalysis(Base):
    """The current summary and stamp for one voucher. One row per voucher.

    The stamp says when, at which level (asked for and answered at), by which
    model and revision, where it ran, from which input and which prompt and
    seed version: docs/analyse.md «The stamp», read by
    :func:`myntos.domain.analyse.freshness.verdict`.
    """

    __tablename__ = "ki_voucher_analysis"
    __table_args__ = (
        Index("ix_ki_voucher_analysis_company_status", "company_id", "status"),
        Index("ix_ki_voucher_analysis_company_prompt", "company_id", "prompt_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    transaction_pk: Mapped[int] = mapped_column(
        ForeignKey("gl_transaction.id", ondelete="CASCADE"), unique=True
    )
    status: Mapped[str] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(10))
    summary: Mapped[str | None] = mapped_column(String(200))
    input_hash: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[int] = mapped_column(SmallInteger)
    seed_version: Mapped[int] = mapped_column(SmallInteger)
    requested_level: Mapped[str] = mapped_column(String(10))
    level: Mapped[str] = mapped_column(String(10))
    # NULL where no model was involved (``source = 'regnskap'``): depreciation
    # and a reversal are the software's own answer, and naming a model there
    # would make the stamp claim something that did not happen. The contract
    # had it NOT NULL; docs/analyse.md says so now.
    model: Mapped[str | None] = mapped_column(String(80))
    model_revision: Mapped[str | None] = mapped_column(String(80))
    location: Mapped[str | None] = mapped_column(String(12))
    analysed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # How many times in a row this voucher failed, for the back-off.
    failures: Mapped[int] = mapped_column(SmallInteger, default=0)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("ki_run.id", ondelete="SET NULL"))


class KiLineTag(Base):
    """One tag on one income or cost line. Replaced with its analysis, in one transaction."""

    __tablename__ = "ki_line_tag"
    __table_args__ = (
        PrimaryKeyConstraint("line_pk", "tag_id"),
        Index("ix_ki_line_tag_company_tag", "company_id", "tag_id"),
    )

    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    line_pk: Mapped[int] = mapped_column(ForeignKey("gl_transaction_line.id", ondelete="CASCADE"))
    transaction_pk: Mapped[int] = mapped_column(
        ForeignKey("gl_transaction.id", ondelete="CASCADE"), index=True
    )
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("ki_voucher_analysis.id", ondelete="CASCADE"), index=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("ki_tag.id", ondelete="CASCADE"))


class KiReport(Base):
    """What a deep analysis or a review produced (WP6, WP7). Created here, written there."""

    __tablename__ = "ki_report"
    __table_args__ = (
        Index("ix_ki_report_company_kind_created", "company_id", "kind", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    run_id: Mapped[int] = mapped_column(ForeignKey("ki_run.id", ondelete="CASCADE"), unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    period_from: Mapped[date] = mapped_column(Date)
    period_to: Mapped[date] = mapped_column(Date)
    input_hash: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[int] = mapped_column(SmallInteger)
    schema_version: Mapped[int] = mapped_column(SmallInteger)
    requested_level: Mapped[str] = mapped_column(String(10))
    level: Mapped[str] = mapped_column(String(10))
    model: Mapped[str | None] = mapped_column(String(80))
    model_revision: Mapped[str | None] = mapped_column(String(80))
    location: Mapped[str | None] = mapped_column(String(12))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    body: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)


class KiFindingDismissal(Base):
    """«Dette er riktig» on a review's finding (WP7). Created here, written there."""

    __tablename__ = "ki_finding_dismissal"
    __table_args__ = (UniqueConstraint("company_id", "code", "subject"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(48))
    subject: Mapped[str] = mapped_column(String(128))
    transaction_pk: Mapped[int | None] = mapped_column(
        ForeignKey("gl_transaction.id", ondelete="CASCADE"), index=True
    )
    note: Mapped[str | None] = mapped_column(Text)
    dismissed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    dismissed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
