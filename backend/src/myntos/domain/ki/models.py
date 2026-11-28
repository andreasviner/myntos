"""KI plans, monthly subscriptions, weekly NOK value usage, invoicing, and chat threads.

Four groups of tables live here, all scoped to ``company_id`` with ``CASCADE``
so they belong strictly to one company's books and leave with it on purge
(docs/gdpr.md):

- :class:`KiPeriod` - one calendar month paid through Mollie (one-off checkout),
  with a NOK value budget (``value_allowance_nok`` / ``value_used_nok``).
- :class:`KiSubscription` - monthly KI plan billed by invoice (active,
  canceling at period end, or canceled), carrying its ``weekly_value_nok``
  allowance.
- :class:`KiWeeklyUsage` - paid-level NOK value spent by a company in one ISO
  week (Monday 00:00 Europe/Oslo), enforced across all paid KI surfaces and
  exposed to users strictly as a remaining percentage.
- :class:`KiBillingInvoice` - record of each monthly section 5-1 invoice issued
  from the configured seller company for a company's KI subscription, linking
  to the seller's frozen ``SalesDocument`` and delivery state (EHF / email).
- :class:`KiChatThread` and :class:`KiChatMessage` - saved KI chat conversations
  about a company's books.
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
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from myntos.db.base import Base
from myntos.domain.ledger.models import MONEY

JSON_DOCUMENT = JSON().with_variant(JSONB, "postgresql")

#: Internal NOK cost/value tracking with micro-krone precision (6 decimals) so
#: even a small «Medium» completion (~0.002 kr) is counted accurately without
#: rounding down to 0.00 kr.
VALUE_NOK = Numeric(38, 6)


class KiPeriodStatus(enum.StrEnum):
    """Where the payment for a one-off Mollie period stands."""

    OPEN = "open"
    PAID = "paid"
    FAILED = "failed"
    CANCELED = "canceled"
    EXPIRED = "expired"


class KiSubscriptionStatus(enum.StrEnum):
    """Lifecycle of a monthly invoiced KI subscription."""

    # Active and renewing monthly until canceled.
    ACTIVE = "active"
    # Canceled by an owner, still usable until current_period_end, no next invoice.
    CANCELING = "canceling"
    # Ended; no paid levels available unless reactivated.
    CANCELED = "canceled"


class KiInvoiceDeliveryMethod(enum.StrEnum):
    """How a monthly KI invoice was delivered to the subscribing company."""

    EHF = "ehf"
    EMAIL = "email"
    PORTAL = "portal"


class KiInvoiceDeliveryStatus(enum.StrEnum):
    """Delivery state of a monthly KI invoice."""

    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"


class KiPeriod(Base):
    __tablename__ = "ki_period"
    __table_args__ = (Index("ix_ki_period_company_id", "company_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # CASCADE: the period is about these books and goes when they are purged.
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    # The plan's code at the time (myntos.domain.ki.plans). Kept as a string so a
    # plan retired from the table still names what was bought.
    plan: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default=KiPeriodStatus.OPEN.value)
    # Myntos's own reference, in the return URL and Mollie's metadata. Random
    # rather than the row id, because it is in a URL a browser history keeps.
    reference: Mapped[str] = mapped_column(String(32), unique=True)
    # Unique: one Mollie payment opens one period, however many times the
    # webhook and the browser's return both report it paid.
    mollie_payment_id: Mapped[str | None] = mapped_column(String(80), unique=True)
    # "test" or "live". A test payment opens a period too, which is what makes
    # the whole flow testable, and the column is how a test period is told apart
    # from a real one afterwards.
    mollie_mode: Mapped[str] = mapped_column(String(8))
    # Copied from the plan when the checkout starts. Gross, as charged.
    amount: Mapped[Decimal] = mapped_column(MONEY)
    vat_amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3), default="NOK")
    # Backend-only NOK value budget for the month (ex. VAT) and live NOK cost
    # spent so far. Added to with one atomic UPDATE per request.
    value_allowance_nok: Mapped[Decimal] = mapped_column(MONEY)
    value_used_nok: Mapped[Decimal] = mapped_column(
        VALUE_NOK, default=Decimal("0.000000"), server_default=text("0.000000")
    )
    # Internal telemetry counter only; never governs quotas or reaches the API.
    tokens_used: Mapped[int] = mapped_column(BigInteger, default=0)
    # Set when paid. A month bought while another is running starts when that
    # one ends, so buying early never throws away days already paid for.
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # SET NULL like every actor column (docs/gdpr.md): who paid is the person's
    # to erase; that the company had the plan is not.
    bought_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )


class KiSubscription(Base):
    """A company's monthly KI plan, billed by invoice each month."""

    __tablename__ = "ki_subscription"
    __table_args__ = (
        UniqueConstraint("company_id", name="uq_ki_subscription_company_id"),
        Index("ix_ki_subscription_status_end", "status", "current_period_end"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    plan: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default=KiSubscriptionStatus.ACTIVE.value)
    # Copied from the plan when activated or changed so price/value-budget
    # promises for the running month stay explicit on the row. Backend-only:
    # the API exposes only the remaining percentage.
    weekly_value_nok: Mapped[Decimal] = mapped_column(MONEY)
    price_excl_vat: Mapped[Decimal] = mapped_column(MONEY)
    vat_amount: Mapped[Decimal] = mapped_column(MONEY)
    price_incl_vat: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3), default="NOK")
    billing_email: Mapped[str | None] = mapped_column(String(320))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KiWeeklyUsage(Base):
    """Paid-level NOK value spent by one company in one ISO week (Monday in Europe/Oslo)."""

    __tablename__ = "ki_weekly_usage"
    __table_args__ = (
        UniqueConstraint("company_id", "week_start", name="uq_ki_weekly_usage_company_week"),
        Index("ix_ki_weekly_usage_company_id", "company_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    # The Monday date in Europe/Oslo for this usage week.
    week_start: Mapped[date] = mapped_column(Date)
    # Authoritative quota spend in NOK (ex. VAT, micro-krone precision).
    value_used_nok: Mapped[Decimal] = mapped_column(
        VALUE_NOK, default=Decimal("0.000000"), server_default=text("0.000000")
    )
    # Internal telemetry counter only; never governs quotas or reaches the API.
    tokens_used: Mapped[int] = mapped_column(BigInteger, default=0)


class KiBillingInvoice(Base):
    """A monthly invoice issued for a company's KI subscription.

    Links the subscribing company (``company_id``) to the issued section 5-1
    ``SalesDocument`` in the configured seller company's books
    (``seller_document_id``). The seller's invoice remains in the seller's own
    ledger for five years even if the subscribing company is later purged.
    """

    __tablename__ = "ki_billing_invoice"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "period_start", "period_end", name="uq_ki_billing_invoice_period"
        ),
        Index("ix_ki_billing_invoice_company_id", "company_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("ki_subscription.id", ondelete="SET NULL")
    )
    plan: Mapped[str] = mapped_column(String(32))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    seller_company_id: Mapped[int | None] = mapped_column(
        ForeignKey("company.id", ondelete="SET NULL")
    )
    seller_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales_document.id", ondelete="SET NULL")
    )
    invoice_number: Mapped[str | None] = mapped_column(String(64))
    amount_excl_vat: Mapped[Decimal] = mapped_column(MONEY)
    vat_amount: Mapped[Decimal] = mapped_column(MONEY)
    amount_incl_vat: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3), default="NOK")
    issued_on: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    delivery_method: Mapped[str] = mapped_column(
        String(16), default=KiInvoiceDeliveryMethod.PORTAL.value
    )
    delivery_status: Mapped[str] = mapped_column(
        String(16), default=KiInvoiceDeliveryStatus.PENDING.value
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recipient_email: Mapped[str | None] = mapped_column(String(320))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KiChatThread(Base):
    """One saved KI chat conversation about a company's books."""

    __tablename__ = "ki_chat_thread"
    __table_args__ = (Index("ix_ki_chat_thread_company_updated", "company_id", "updated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(160), default="Ny samtale")
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KiChatMessage(Base):
    """One user question or assistant answer inside a :class:`KiChatThread`."""

    __tablename__ = "ki_chat_message"
    __table_args__ = (
        Index("ix_ki_chat_message_thread_id", "thread_id"),
        Index("ix_ki_chat_message_company_id", "company_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("ki_chat_thread.id", ondelete="CASCADE"))
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    level: Mapped[str | None] = mapped_column(String(16))
    requested_level: Mapped[str | None] = mapped_column(String(16))
    # Internal telemetry / cost auditing only; never returned to clients.
    tokens_used: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_nok: Mapped[Decimal] = mapped_column(
        VALUE_NOK, default=Decimal("0.000000"), server_default=text("0.000000")
    )
    notices: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
