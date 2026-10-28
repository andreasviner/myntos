"""The levels, and the log of who chose which for a set of books.

The level in force is ``Company.data_sharing``, because every page that shows
the company shows it. The log is here because the level alone cannot answer the
question that matters once something is collected: whether the company had said
yes on the day it was taken, and who said it. A row per change, never edited.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from myntos.db.base import Base

LEVEL_LENGTH = 32


class DataSharingLevel(enum.StrEnum):
    """How much of a company's books Myntos may use to improve Myntos.

    Declared in order, and the order is the point: each level shares everything
    the one before it does. Bokmål on the page, from ``lib/datasharing.ts``:
    «Ingenting», «Bruksstatistikk», «Anonymiserte rettelser», «Alt, også bilag».
    """

    # The default, and what every company had before the choice existed.
    NONE = "none"
    # Which pages and features are used, how often, how long they take and what
    # fails. Nothing from the books.
    USAGE = "usage"
    # Usage, and how a proposal was corrected: the category, account and VAT
    # code Myntos proposed against the ones the person chose. No names, orgnr,
    # amounts, free text or documents.
    ANONYMISED = "anonymised"
    # The bilag files and everything entered about them, as they are. Names,
    # amounts and whatever personal data is on a receipt included. The page
    # shows it in red.
    EVERYTHING = "everything"


ORDER: tuple[DataSharingLevel, ...] = tuple(DataSharingLevel)


class DataSharingChoice(Base):
    """One change of a company's level, by whom and when."""

    __tablename__ = "data_sharing_choice"
    __table_args__ = (Index("ix_data_sharing_choice_company_id", "company_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # CASCADE: the log is about these books, and goes when they are purged.
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    # A plain string rather than a database enum, like FinancialYearEvent.action:
    # a level added later should be a code change, not an ALTER TYPE.
    level: Mapped[str] = mapped_column(String(LEVEL_LENGTH))
    chosen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # SET NULL like every actor column (docs/gdpr.md), but a person who chose
    # to share leaves a tombstone instead (myntos.domain.identity.erasure):
    # Myntos uses what it was given on the strength of that choice, and must be
    # able to say who made it.
    chosen_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
