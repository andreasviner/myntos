"""The log of closing and reopening financial years.

``financial_year.closed_at`` says whether a year is locked *now*, and nothing
else. That is not enough once a year can be reopened: the column is cleared
again, and with it the only trace that the year was ever closed, by whom, and
why somebody decided to undo it. An auditor asking "was 2026 changed after it
was closed" would get no answer from the data.

So every close and every reopen writes a row here, and nothing updates or
deletes one. The vouchers say *what* changed in the books (the disposition, and
its reversal on reopening); this says who did it and the reason they gave.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from myntos.db.base import Base
from myntos.domain.ledger.models import MONEY


class YearAction(enum.StrEnum):
    AVSLUTTET = "avsluttet"
    GJENAPNET = "gjenapnet"
    # The books ended early because the company was wound up, and that undone.
    # Logged like a closing: the year's end date moved, and who moved it is
    # part of the trail bokføringsloven § 4 asks for.
    OPPLOST = "opplost"
    OPPLOST_ANGRET = "opplost_angret"


class FinancialYearEvent(Base):
    __tablename__ = "financial_year_event"
    __table_args__ = (Index("ix_financial_year_event_company", "company_id", "financial_year"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    financial_year: Mapped[int] = mapped_column(Integer)
    # A plain string rather than a database enum, like MvaFiling.status: an
    # action added later should be a code change, not an ALTER TYPE.
    action: Mapped[str] = mapped_column(String(16))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # SET NULL for the reason Transaction.created_by_user_id gives: the record of
    # a year being closed outlives the account of the person who closed it.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    # Required for a reopening, empty for a close. Reopening is the act that
    # needs explaining: it changes books somebody may already have relied on.
    reason: Mapped[str | None] = mapped_column(String(1000))
    # The figures the close was made on, as they stood. Kept so the log reads
    # on its own after a reopening has reversed the vouchers that carried them.
    result: Mapped[Decimal | None] = mapped_column(MONEY)
    tax: Mapped[Decimal | None] = mapped_column(MONEY)
