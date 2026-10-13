"""The anleggsregister's tables.

Three, and each holds something no voucher can:

- :class:`FixedAsset`, one row per driftsmiddel: what it is, which saldogruppe
  it is in, when it was delivered, what it cost, the public grant against it,
  and whether and for how much it left. A voucher says that 1200 went up by
  640 000; only the register says it was a tractor in group d delivered in
  March, which is what the tax rules turn on.
- :class:`FixedAssetOpening`, the saldi a company brings with it: the tax value
  of each collective group (and of each building's technical installations,
  and the gevinst- og tapskonto) at the start of the first year the register
  covers. The saldo is a tax figure the old program carried and the books do
  not: Tripletex's balance on 1200 is not the saldo of group d once the old
  accountant has written anything down differently.
- :class:`FixedAssetDepreciation`, what each depreciation voucher posted per
  saldo. The law lets a company take less than the maximum, so a year's
  depreciation is a choice, and the next year's saldo is built on the choice
  made rather than on what the maximum would have been. The voucher carries the
  amounts and these rows say which saldo each belonged to. A row counts while
  its voucher stands: reversing the voucher takes the year's depreciation out of
  every saldo without a second write that could be forgotten.

Register rows are not vouchers: they may be changed and deleted while every
year they touch is open, and are frozen once one of those years is closed
(:mod:`myntos.domain.anlegg.register`).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from myntos.db.base import Base
from myntos.domain.ledger.models import MONEY


class FixedAsset(Base):
    """One driftsmiddel in the register."""

    __tablename__ = "fixed_asset"
    __table_args__ = (Index("ix_fixed_asset_company", "company_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    description: Mapped[str] = mapped_column(String(255))
    # myntos.domain.anlegg.rules.Saldogruppe, as a plain string like
    # MvaFiling.status: a kind added later is a code change, not an ALTER TYPE.
    saldogruppe: Mapped[str] = mapped_column(String(16))
    # A raised rate inside group h (husdyrbygg 6 %, kort levetid 10 %, fruktfelt,
    # bærfelt). NULL for the group's ordinary rate.
    rate_variant: Mapped[str | None] = mapped_column(String(32))
    # The balance-sheet account the asset's value sits on, by number like the
    # rest of the ledger's references (Account.account_number), so the register
    # survives an account being deactivated.
    account_number: Mapped[str] = mapped_column(String(32))
    # Ervervet ved levering, not when it was ordered or paid (Skatte-ABC
    # D-2-8.2.1): this is the date the saldo takes it in.
    acquired_on: Mapped[date] = mapped_column(Date)
    # Inngangsverdi: kostpris including VAT the company could not deduct.
    cost: Mapped[Decimal] = mapped_column(MONEY)
    # A public grant towards it (Innovasjon Norge, Enova, the kommune).
    grant: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"))
    # Whether the grant comes off the cost (§ 14-42 annet ledd a, the rule) or
    # not (the same paragraph's exception: Innovasjon Norge's tilskudd to faste
    # anlegg i landbruket, and distriktsrettet investeringsstøtte, inside the
    # geographic area of forskrift 2021-12-14-3661 § 3, which is tax free and
    # leaves the cost whole, § 5-31 a).
    grant_reduces_cost: Mapped[bool] = mapped_column(Boolean, default=True)
    # The purchase voucher, when it was booked in Myntos. SET NULL: the register
    # row is documentation of the asset, and outlives anything that could take
    # the voucher with it.
    transaction_pk: Mapped[int | None] = mapped_column(
        ForeignKey("gl_transaction.id", ondelete="SET NULL")
    )
    # For a group j installation: the building (an h or i asset) whose j saldo
    # it belongs to. NULL puts it on the company's one j saldo without a
    # building.
    building_id: Mapped[int | None] = mapped_column(
        ForeignKey("fixed_asset.id", ondelete="SET NULL")
    )
    # For an asset brought from the old program: its tax value (saldoverdi) at
    # the start of ``opening_year``. Then ``cost`` is history and adds nothing
    # to a saldo; the opening value is where the register picks it up.
    opening_year: Mapped[int | None] = mapped_column(Integer)
    opening_value: Mapped[Decimal | None] = mapped_column(MONEY)
    # Years the right lasts, for linear depreciation (§ 14-50). For one brought
    # from the old program, the years left from ``opening_year``.
    useful_life_years: Mapped[int | None] = mapped_column(Integer)
    # Realisasjon or uttak: the day ownership passed (Skatte-ABC D-2-8.6.1) and
    # what was received. Zero proceeds is an asset scrapped.
    disposed_on: Mapped[date | None] = mapped_column(Date)
    disposal_proceeds: Mapped[Decimal | None] = mapped_column(MONEY)
    note: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # SET NULL for the reason Transaction.created_by_user_id gives.
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )


class FixedAssetOpening(Base):
    """A saldo brought from the old program, at the start of the register's first year.

    One row per collective group and account (a, b, c, d), per building and
    account for group j, and one for the gevinst- og tapskonto
    (``saldogruppe = "gtk"``, no account: it is a tax balance the books do not
    carry). Signed: a negative saldo in group d is a negative amount.
    """

    __tablename__ = "fixed_asset_opening"
    __table_args__ = (Index("ix_fixed_asset_opening_company", "company_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    year: Mapped[int] = mapped_column(Integer)
    saldogruppe: Mapped[str] = mapped_column(String(16))
    building_id: Mapped[int | None] = mapped_column(
        ForeignKey("fixed_asset.id", ondelete="CASCADE")
    )
    account_number: Mapped[str | None] = mapped_column(String(32))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )


class FixedAssetDepreciation(Base):
    """What one depreciation voucher did to one saldo on one account.

    Written with the voucher and never changed. ``kind`` is one of
    ``avskrivning`` (the depreciation taken), ``negativ_saldo`` (a negative
    saldo taken to income), ``gevinst`` and ``tap`` (an asset that left, its
    remaining value cleared off the account). ``amount`` is always positive.
    """

    __tablename__ = "fixed_asset_depreciation"
    __table_args__ = (Index("ix_fixed_asset_depreciation_company", "company_id", "year"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    year: Mapped[int] = mapped_column(Integer)
    transaction_pk: Mapped[int] = mapped_column(
        ForeignKey("gl_transaction.id", ondelete="CASCADE")
    )
    saldo_key: Mapped[str] = mapped_column(String(64))
    saldogruppe: Mapped[str] = mapped_column(String(16))
    account_number: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32))
    amount: Mapped[Decimal] = mapped_column(MONEY)
