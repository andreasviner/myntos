"""The farm's year-end lists: the count of livestock and stock, and private uttak.

Both are the documentation behind a voucher, not a ledger of their own. The
voucher is what the books say (the change in the value of the buskap on 1450,
the VAT on milk the household drank); the list is *why* it says it: how many
cows, at which normalverdi, how many litres at which rate. Bokføringsforskriften
asks for exactly that list (§ 3-1 første ledd nr. 5 for uttak; the count is the
vareopptelling behind the balance), and the næringsspesifikasjon's ``buskap``
post and a livestock card need the opening and closing value per kind and the
heads behind them, which no voucher carries.

**Written once, never changed.** A list is stored when it is posted, with the
voucher it posted, and is never updated or deleted afterwards. A correction is a
new list that names the one it replaces (``corrects_id``) and a new voucher,
after the old voucher has been reversed, which is how every other correction in
Myntos works. So the documentation of a reversed voucher stays beside it,
exactly as it was, for the five years bokføringsloven § 13 asks.

**The unit values are copied onto the line.** Skattedirektoratet publishes new
values every year and a published year can be corrected; a count posted with
last December's normalverdi has to keep saying what it was posted with, the
way a voucher line keeps the VAT rate of its date (``TransactionLine.tax_rate``).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from myntos.db.base import Base
from myntos.domain.ledger.models import MONEY, RATE

#: Quantities: heads are whole, but feed is counted in kilos, tonnes and FEm,
#: and a liter of milk in an uttak list may have a decimal.
QUANTITY = Numeric(18, 3)
#: Unit values: a normalverdi per head is whole kroner, a rate per liter or per
#: kilo is not always whole øre. The line's *value* is money and rounded to øre.
UNIT_VALUE = Numeric(18, 4)


class FarmCount(Base):
    """One posted count of livestock and stock at the end of one financial year."""

    __tablename__ = "farm_count"
    __table_args__ = (Index("ix_farm_count_company", "company_id", "financial_year"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    financial_year: Mapped[int] = mapped_column(Integer)
    # 31 December of the year, as the takseringsregler value the herd.
    counted_on: Mapped[date] = mapped_column(Date)
    # The voucher that brought 1450, 1400 and 1490 to the counted values. NULL
    # when the count changed nothing in value (the same herd at the same
    # values), which is a count all the same and the card still needs it.
    # SET NULL for the reason ``corrects_transaction_pk`` gives on a voucher:
    # the list outlives anything that could take its voucher with it.
    transaction_pk: Mapped[int | None] = mapped_column(
        ForeignKey("gl_transaction.id", ondelete="SET NULL")
    )
    # The count this one replaced. Its voucher was reversed in the same
    # request this one was posted in.
    corrects_id: Mapped[int | None] = mapped_column(
        ForeignKey("farm_count.id", ondelete="SET NULL")
    )
    # Where the opening rows came from: ``forrige_telling`` (copied from the
    # previous year's count) or ``oppgitt`` (typed, the first year in Myntos).
    # ``trukket`` marks a withdrawal: a list with no lines, stored in place of
    # the one withdrawn, so withdrawing writes a row instead of changing one.
    opening_source: Mapped[str] = mapped_column(String(16))
    # SET NULL, as for a voucher: the list outlives the account of its author.
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lines: Mapped[list[FarmCountLine]] = relationship(
        back_populates="count", cascade="all, delete-orphan", order_by="FarmCountLine.position"
    )


class FarmCountLine(Base):
    """One row of a count: a kind of animal or a kind of stock, at the opening or the close."""

    __tablename__ = "farm_count_line"

    id: Mapped[int] = mapped_column(primary_key=True)
    count_id: Mapped[int] = mapped_column(ForeignKey("farm_count.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    # ``inngaende`` (1 January) or ``utgaende`` (31 December).
    side: Mapped[str] = mapped_column(String(16))
    # ``buskap`` (1450), ``innkjopt`` (bought stock, 1400) or ``egenprodusert``
    # (the farm's own feed and seed for its own use, 1490).
    kind: Mapped[str] = mapped_column(String(16))
    # A category from the year's takseringsregler (``storfe_melkeku``) or
    # ``annet``. Plain strings, so a category added in a later year is a data
    # change and not an ALTER TYPE.
    category: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[Decimal] = mapped_column(QUANTITY)
    unit: Mapped[str] = mapped_column(String(16))
    # ``normalverdi`` (the takseringsregler's value per head), ``kostpris``
    # (what it cost), or ``verdsatt`` (a value the person states, with the
    # reason in the description).
    valuation: Mapped[str] = mapped_column(String(16))
    unit_value: Mapped[Decimal] = mapped_column(UNIT_VALUE)
    value: Mapped[Decimal] = mapped_column(MONEY)
    account_number: Mapped[str] = mapped_column(String(32))

    count: Mapped[FarmCount] = relationship(back_populates="lines")


class FarmWithdrawal(Base):
    """One posted list of private uttak of the farm's own produce for one year."""

    __tablename__ = "farm_withdrawal"
    __table_args__ = (Index("ix_farm_withdrawal_company", "company_id", "financial_year"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    financial_year: Mapped[int] = mapped_column(Integer)
    # The voucher date, which is also the VAT period the uttak is reported in.
    occurred_on: Mapped[date] = mapped_column(Date)
    transaction_pk: Mapped[int | None] = mapped_column(
        ForeignKey("gl_transaction.id", ondelete="SET NULL")
    )
    corrects_id: Mapped[int | None] = mapped_column(
        ForeignKey("farm_withdrawal.id", ondelete="SET NULL")
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # A list with no lines is a withdrawal, stored in place of the one withdrawn
    # (myntos.domain.arsoppgjor.uttak).
    lines: Mapped[list[FarmWithdrawalLine]] = relationship(
        back_populates="withdrawal",
        cascade="all, delete-orphan",
        order_by="FarmWithdrawalLine.position",
    )


class FarmWithdrawalLine(Base):
    """One product taken out: what, how much, at which rate, and the VAT on it."""

    __tablename__ = "farm_withdrawal_line"

    id: Mapped[int] = mapped_column(primary_key=True)
    withdrawal_id: Mapped[int] = mapped_column(ForeignKey("farm_withdrawal.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    # A product from the year's takseringsregler (``melk``) or ``annet``.
    product: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[Decimal] = mapped_column(QUANTITY)
    unit: Mapped[str] = mapped_column(String(16))
    unit_value: Mapped[Decimal] = mapped_column(UNIT_VALUE)
    # The value without VAT: the uttak's basis, and the income.
    value: Mapped[Decimal] = mapped_column(MONEY)
    # NULL when the company was not VAT-registered on the date: the uttak is
    # income all the same, but there is no melding to report it in.
    tax_code: Mapped[str | None] = mapped_column(String(8))
    vat_rate: Mapped[Decimal | None] = mapped_column(RATE)
    vat: Mapped[Decimal] = mapped_column(MONEY)
    account_number: Mapped[str] = mapped_column(String(32))

    withdrawal: Mapped[FarmWithdrawal] = relationship(back_populates="lines")
