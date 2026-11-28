"""Which level a question gets, weekly NOK value quotas, monthly subscriptions, and one-off months.

The rule a person can hold in their head, and the one ``/ki/about/`` states:

- **«Gratis» is always unlimited** while KI is turned on for the company.
- **«Medium» and «Smart»** require an active KI plan (either a monthly
  invoiced subscription or a paid one-off month) with quota left in this
  week's allowance (resets every Monday at 00:00 Norwegian time).
- Internally each plan has a weekly value budget in NOK (ex. VAT), and each
  paid call deducts its live NOK cost from Løynd's ``/v1/models`` pricing.
  Users and API clients see only the remaining percentage (0 to 100 %).
- Asking for a paid level without an active plan or after hitting the weekly
  limit is never an error: the question is answered at the free level instead,
  and the answer says why (:attr:`Resolution.notice`).

Every paid request is counted once after it finishes against the week (and
period, if any) it started in. One request can overshoot what is left and still
completes; the next request is the one that falls back to «Gratis».
"""

from __future__ import annotations

import calendar
import secrets
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, cast
from zoneinfo import ZoneInfo

from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.core.clock import as_utc, utc_now
from myntos.domain.ki import plans as ki_plans
from myntos.domain.ki.errors import KiSubscriptionNotFoundError
from myntos.domain.ki.models import (
    KiPeriod,
    KiPeriodStatus,
    KiSubscription,
    KiSubscriptionStatus,
    KiWeeklyUsage,
)
from myntos.domain.ki.plans import CURRENCY, Plan
from myntos.integrations.loynd import Level
from myntos.integrations.mollie import MolliePayment

_OSLO = ZoneInfo("Europe/Oslo")
_ZERO_NOK = Decimal("0.000000")
_MICRO_NOK = Decimal("0.000001")

_MONTHS_NB = (
    "januar",
    "februar",
    "mars",
    "april",
    "mai",
    "juni",
    "juli",
    "august",
    "september",
    "oktober",
    "november",
    "desember",
)

#: Said when a paid level was asked for and the free one answered. Bokmål,
#: because the pages show it as it is.
NO_PLAN = (
    "«{level}» krever en KI-plan. Svaret kommer fra den gratis modellen. "
    "Du kan velge en plan under Om KI."
)
SPENT = (
    "Kvoten i KI-planen er brukt opp for denne måneden, så svaret kommer fra den "
    "gratis modellen. Du kan velge en ny plan under Om KI."
)
WEEKLY_SPENT = (
    "Ukekvoten i KI-planen er brukt opp, så svaret kommer fra den gratis "
    "modellen. Kvoten nullstilles mandag {reset}, eller du kan bytte til en større "
    "plan under Om KI."
)
#: Not «ingenting ble trukket»: a bilag asks several questions, and those the
#: paid model answered before it stopped answering are counted.
REFUSED = (
    "Den betalte modellen svarte ikke akkurat nå, så svaret kommer fra den gratis "
    "modellen, som ikke teller mot kvoten."
)

_LEVEL_NAMES = {Level.FREE: "Gratis", Level.MEDIUM: "Medium", Level.THINKING: "Smart"}


def level_name(level: Level) -> str:
    """The level as the page names it."""
    return _LEVEL_NAMES[level]


def format_norwegian_day(day: date) -> str:
    """``date(2026, 5, 4)`` -> ``'4. mai'``."""
    return f"{day.day}. {_MONTHS_NB[day.month - 1]}"


def _to_micro_nok(value: Decimal | int | float | str | None) -> Decimal:
    """Normalise a DB numeric value to 6-decimal ``Decimal`` (guards SQLite float noise)."""
    if value is None:
        return _ZERO_NOK
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    return dec.quantize(_MICRO_NOK, rounding=ROUND_HALF_UP)


def remaining_percent(allowance: Decimal, used: Decimal) -> int:
    """Whole percentage remaining (0..100). Returns 0 if and only if exhausted.

    While any positive NOK value remains, clamps to at least 1 % so the UI
    never reports «0 % igjen» while paid levels are still enabled.
    """
    if allowance <= 0 or used >= allowance:
        return 0
    if used <= 0:
        return 100
    left = allowance - used
    pct = int(((left * Decimal(100)) / allowance).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return max(1, min(100, pct))


def period_remaining_percent(period: KiPeriod) -> int:
    """Whole percentage remaining on a legacy one-off :class:`KiPeriod`."""
    return remaining_percent(
        _to_micro_nok(period.value_allowance_nok),
        _to_micro_nok(period.value_used_nok),
    )


def has_period_quota(period: KiPeriod) -> bool:
    """Whether a legacy one-off :class:`KiPeriod` still has NOK value remaining."""
    return _to_micro_nok(period.value_used_nok) < _to_micro_nok(period.value_allowance_nok)


def week_bounds(now: datetime | None = None) -> tuple[date, datetime]:
    """Current ISO week's Monday date in Norway and the UTC instant it resets."""
    moment = as_utc(now or utc_now()).astimezone(_OSLO)
    monday = moment.date() - timedelta(days=moment.weekday())
    next_monday = monday + timedelta(days=7)
    resets_at = datetime.combine(next_monday, time.min, tzinfo=_OSLO).astimezone(UTC)
    return monday, resets_at


@dataclass(frozen=True)
class WeeklyQuota:
    """How much of this week's paid NOK value allowance a company has used."""

    week_start: date
    resets_at: datetime
    value_allowance_nok: Decimal
    value_used_nok: Decimal

    @property
    def value_left_nok(self) -> Decimal:
        return max(self.value_allowance_nok - self.value_used_nok, _ZERO_NOK)

    @property
    def exhausted(self) -> bool:
        return self.value_allowance_nok <= 0 or self.value_used_nok >= self.value_allowance_nok

    @property
    def remaining_percent(self) -> int:
        return remaining_percent(self.value_allowance_nok, self.value_used_nok)

    @property
    def used_percent(self) -> int:
        return 100 - self.remaining_percent


@dataclass(frozen=True)
class Resolution:
    """What a request for a level came to."""

    #: The level the question is asked at.
    level: Level
    #: The one-off Mollie period to count NOK spend on, if one is running.
    period_id: int | None = None
    #: The company whose weekly counter receives paid NOK spend.
    company_id: int | None = None
    #: The Monday date (Europe/Oslo) of the week this request started in.
    week_start: date | None = None
    #: The active subscription id, if this request ran under a monthly plan.
    subscription_id: int | None = None
    #: Why the level is lower than the one asked for, in bokmål, or nothing.
    notice: str | None = None


def _is_running(period: KiPeriod, now: datetime) -> bool:
    if period.status != KiPeriodStatus.PAID.value or period.starts_at is None:
        return False
    if period.ends_at is None:
        return False
    return as_utc(period.starts_at) <= now < as_utc(period.ends_at)


def is_subscription_active(subscription: KiSubscription, now: datetime) -> bool:
    """Whether ``subscription`` grants paid levels at ``now``."""
    if subscription.status not in (
        KiSubscriptionStatus.ACTIVE.value,
        KiSubscriptionStatus.CANCELING.value,
    ):
        return False
    return (
        as_utc(subscription.current_period_start) <= now < as_utc(subscription.current_period_end)
    )


async def _paid(db: AsyncSession, company_id: int) -> list[KiPeriod]:
    # Filtered in Python rather than in SQL: a company has a handful of rows, and
    # comparing timezone-aware instants in Python is identical on Postgres and SQLite.
    rows = await db.scalars(
        select(KiPeriod)
        .where(KiPeriod.company_id == company_id, KiPeriod.status == KiPeriodStatus.PAID.value)
        .order_by(KiPeriod.starts_at, KiPeriod.id)
    )
    return list(rows)


async def current(
    db: AsyncSession, company_id: int, *, now: datetime | None = None
) -> KiPeriod | None:
    """The paid one-off month running now, if one is."""
    moment = now or utc_now()
    for period in await _paid(db, company_id):
        if _is_running(period, moment):
            return period
    return None


async def upcoming(
    db: AsyncSession, company_id: int, *, now: datetime | None = None
) -> list[KiPeriod]:
    """One-off months paid for that have not started yet, soonest first."""
    moment = now or utc_now()
    return [
        period
        for period in await _paid(db, company_id)
        if period.starts_at is not None and as_utc(period.starts_at) > moment
    ]


async def get_subscription(db: AsyncSession, company_id: int) -> KiSubscription | None:
    """The company's monthly KI subscription row, if any."""
    sub: KiSubscription | None = await db.scalar(
        select(KiSubscription).where(KiSubscription.company_id == company_id)
    )
    return sub


async def active_subscription(
    db: AsyncSession, company_id: int, *, now: datetime | None = None
) -> KiSubscription | None:
    """The company's currently active (or canceling-at-end) monthly subscription."""
    moment = now or utc_now()
    sub = await get_subscription(db, company_id)
    if sub is not None and is_subscription_active(sub, moment):
        return sub
    return None


async def weekly_value_used_nok(
    db: AsyncSession, company_id: int, *, week_start: date | None = None
) -> Decimal:
    """Paid-level NOK value spent by ``company_id`` in ``week_start``."""
    target = week_start or week_bounds()[0]
    used: Decimal | None = await db.scalar(
        select(KiWeeklyUsage.value_used_nok).where(
            KiWeeklyUsage.company_id == company_id,
            KiWeeklyUsage.week_start == target,
        )
    )
    return _to_micro_nok(used)


async def weekly_quota(
    db: AsyncSession,
    company_id: int,
    *,
    subscription: KiSubscription | None = None,
    period: KiPeriod | None = None,
    now: datetime | None = None,
) -> WeeklyQuota | None:
    """This week's paid NOK value allowance and spend, or ``None`` without a plan."""
    moment = now or utc_now()
    sub = (
        subscription
        if subscription is not None
        else await active_subscription(db, company_id, now=moment)
    )
    allowance: Decimal | None = None
    if sub is not None and is_subscription_active(sub, moment):
        allowance = _to_micro_nok(sub.weekly_value_nok)
    else:
        running = period if period is not None else await current(db, company_id, now=moment)
        if running is not None and _is_running(running, moment):
            plan = ki_plans.by_code(running.plan)
            period_budget = _to_micro_nok(running.value_allowance_nok)
            weekly_cap = plan.weekly_value_nok if plan is not None else period_budget
            allowance = min(_to_micro_nok(weekly_cap), period_budget)
    if allowance is None:
        return None
    start, resets_at = week_bounds(moment)
    used = await weekly_value_used_nok(db, company_id, week_start=start)
    return WeeklyQuota(
        week_start=start,
        resets_at=resets_at,
        value_allowance_nok=allowance,
        value_used_nok=used,
    )


async def resolve(
    db: AsyncSession, company_id: int, wanted: Level, *, now: datetime | None = None
) -> Resolution:
    """The level a question asked for at ``wanted`` gets."""
    if not wanted.is_paid:
        return Resolution(Level.FREE)
    moment = now or utc_now()
    start, _ = week_bounds(moment)
    next_monday = start + timedelta(days=7)

    sub = await active_subscription(db, company_id, now=moment)
    period = await current(db, company_id, now=moment)

    if sub is not None:
        used = await weekly_value_used_nok(db, company_id, week_start=start)
        if used >= _to_micro_nok(sub.weekly_value_nok):
            return Resolution(
                Level.FREE,
                notice=WEEKLY_SPENT.format(reset=format_norwegian_day(next_monday)),
            )
        return Resolution(
            wanted,
            period_id=period.id if period is not None else None,
            company_id=company_id,
            week_start=start,
            subscription_id=sub.id,
        )

    if period is None:
        return Resolution(Level.FREE, notice=NO_PLAN.format(level=level_name(wanted)))
    period_allowance = _to_micro_nok(period.value_allowance_nok)
    period_used = _to_micro_nok(period.value_used_nok)
    if period_used >= period_allowance:
        return Resolution(Level.FREE, notice=SPENT)
    plan = ki_plans.by_code(period.plan)
    weekly_cap = (
        min(_to_micro_nok(plan.weekly_value_nok), period_allowance) if plan else period_allowance
    )
    used = await weekly_value_used_nok(db, company_id, week_start=start)
    if used >= weekly_cap:
        return Resolution(
            Level.FREE,
            notice=WEEKLY_SPENT.format(reset=format_norwegian_day(next_monday)),
        )
    return Resolution(
        wanted,
        period_id=period.id,
        company_id=company_id,
        week_start=start,
    )


async def record_usage(
    db: AsyncSession,
    period_id: int | None,
    cost_nok: Decimal,
    *,
    tokens: int = 0,
    company_id: int | None = None,
    week_start: date | None = None,
) -> None:
    """Add ``cost_nok`` (and internal ``tokens`` telemetry) atomically to usage buckets.

    Uses atomic SQL ``UPDATE ... SET value_used_nok = value_used_nok + :c`` so
    concurrent requests finishing together are both counted accurately.
    """
    cost = _to_micro_nok(cost_nok)
    tok = max(tokens, 0)
    if cost <= 0 and tok <= 0:
        return
    if period_id is not None:
        await db.execute(
            update(KiPeriod)
            .where(KiPeriod.id == period_id)
            .values(
                value_used_nok=KiPeriod.value_used_nok + cost,
                tokens_used=KiPeriod.tokens_used + tok,
            )
        )
    if company_id is not None:
        target_week = week_start or week_bounds()[0]
        updated = await db.execute(
            update(KiWeeklyUsage)
            .where(
                KiWeeklyUsage.company_id == company_id,
                KiWeeklyUsage.week_start == target_week,
            )
            .values(
                value_used_nok=KiWeeklyUsage.value_used_nok + cost,
                tokens_used=KiWeeklyUsage.tokens_used + tok,
            )
        )
        if (cast(CursorResult[Any], updated).rowcount or 0) == 0:
            try:
                async with db.begin_nested():
                    db.add(
                        KiWeeklyUsage(
                            company_id=company_id,
                            week_start=target_week,
                            value_used_nok=cost,
                            tokens_used=tok,
                        )
                    )
                    await db.flush()
            except IntegrityError:
                await db.execute(
                    update(KiWeeklyUsage)
                    .where(
                        KiWeeklyUsage.company_id == company_id,
                        KiWeeklyUsage.week_start == target_week,
                    )
                    .values(
                        value_used_nok=KiWeeklyUsage.value_used_nok + cost,
                        tokens_used=KiWeeklyUsage.tokens_used + tok,
                    )
                )
    await db.flush()


def add_month(moment: datetime) -> datetime:
    """The same time one calendar month later, on the month's last day if need be.

    A calendar month and not thirty days, because the page says «per måned» and
    a month started on the 31st of January should not end on the 2nd of March.
    """
    year = moment.year + (moment.month // 12)
    month = moment.month % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


async def activate_subscription(
    db: AsyncSession,
    *,
    company_id: int,
    plan: Plan,
    user_id: int | None,
    billing_email: str | None = None,
    now: datetime | None = None,
) -> tuple[KiSubscription, bool]:
    """Start or change a company's monthly KI subscription.

    Returns ``(subscription, new_billing_period)`` where ``new_billing_period``
    is True when a fresh billing month just began and needs a monthly invoice,
    and False when switching plans inside an already-invoiced month.
    """
    moment = as_utc(now or utc_now())
    existing = await get_subscription(db, company_id)

    if existing is not None and is_subscription_active(existing, moment):
        # Switching or reactivating inside a running billing month: new weekly
        # value allowance takes effect immediately, and the next renewal invoice
        # bills the newly selected plan.
        existing.plan = plan.code
        existing.status = KiSubscriptionStatus.ACTIVE.value
        existing.weekly_value_nok = plan.weekly_value_nok
        existing.price_excl_vat = plan.price_excl_vat
        existing.vat_amount = plan.vat
        existing.price_incl_vat = plan.price_incl_vat
        existing.currency = CURRENCY
        if billing_email is not None:
            existing.billing_email = billing_email.strip() or None
        existing.cancel_at_period_end = False
        existing.canceled_at = None
        existing.activated_by_user_id = user_id
        existing.updated_at = moment
        await db.flush()
        return existing, False

    period_end = add_month(moment)
    if existing is not None:
        existing.plan = plan.code
        existing.status = KiSubscriptionStatus.ACTIVE.value
        existing.weekly_value_nok = plan.weekly_value_nok
        existing.price_excl_vat = plan.price_excl_vat
        existing.vat_amount = plan.vat
        existing.price_incl_vat = plan.price_incl_vat
        existing.currency = CURRENCY
        existing.billing_email = (billing_email or "").strip() or existing.billing_email
        existing.started_at = moment
        existing.current_period_start = moment
        existing.current_period_end = period_end
        existing.cancel_at_period_end = False
        existing.canceled_at = None
        existing.activated_by_user_id = user_id
        existing.updated_at = moment
        await db.flush()
        return existing, True

    created = KiSubscription(
        company_id=company_id,
        plan=plan.code,
        status=KiSubscriptionStatus.ACTIVE.value,
        weekly_value_nok=plan.weekly_value_nok,
        price_excl_vat=plan.price_excl_vat,
        vat_amount=plan.vat,
        price_incl_vat=plan.price_incl_vat,
        currency=CURRENCY,
        billing_email=(billing_email or "").strip() or None,
        started_at=moment,
        current_period_start=moment,
        current_period_end=period_end,
        cancel_at_period_end=False,
        canceled_at=None,
        activated_by_user_id=user_id,
        updated_at=moment,
    )
    db.add(created)
    await db.flush()
    return created, True


async def cancel_subscription(
    db: AsyncSession, *, company_id: int, now: datetime | None = None
) -> KiSubscription:
    """Cancel the company's monthly KI subscription at the end of the current period."""
    moment = as_utc(now or utc_now())
    sub = await get_subscription(db, company_id)
    if sub is None or not is_subscription_active(sub, moment):
        raise KiSubscriptionNotFoundError("selskapet har ikke et aktivt KI-abonnement")
    sub.cancel_at_period_end = True
    sub.status = KiSubscriptionStatus.CANCELING.value
    sub.canceled_at = moment
    sub.updated_at = moment
    await db.flush()
    return sub


async def start_purchase(
    db: AsyncSession, *, company_id: int, plan: Plan, mode: str, user_id: int | None
) -> KiPeriod:
    """The row a Mollie checkout points at, before anybody has paid."""
    period = KiPeriod(
        company_id=company_id,
        plan=plan.code,
        status=KiPeriodStatus.OPEN.value,
        reference=secrets.token_urlsafe(18),
        mollie_mode=mode,
        amount=plan.price_incl_vat,
        vat_amount=plan.vat,
        currency=CURRENCY,
        value_allowance_nok=plan.monthly_value_nok,
        value_used_nok=_ZERO_NOK,
        tokens_used=0,
        bought_by_user_id=user_id,
    )
    db.add(period)
    await db.flush()
    return period


async def by_reference(db: AsyncSession, *, company_id: int, reference: str) -> KiPeriod | None:
    period: KiPeriod | None = await db.scalar(
        select(KiPeriod).where(KiPeriod.company_id == company_id, KiPeriod.reference == reference)
    )
    return period


async def by_payment(db: AsyncSession, payment_id: str) -> KiPeriod | None:
    period: KiPeriod | None = await db.scalar(
        select(KiPeriod).where(KiPeriod.mollie_payment_id == payment_id)
    )
    return period


async def open_periods(db: AsyncSession, company_id: int) -> list[KiPeriod]:
    """Checkouts started and not yet settled either way."""
    rows = await db.scalars(
        select(KiPeriod).where(
            KiPeriod.company_id == company_id,
            KiPeriod.status == KiPeriodStatus.OPEN.value,
            KiPeriod.mollie_payment_id.is_not(None),
        )
    )
    return list(rows)


_FINAL_WITHOUT_MONEY = {
    "failed": KiPeriodStatus.FAILED,
    "canceled": KiPeriodStatus.CANCELED,
    "expired": KiPeriodStatus.EXPIRED,
}


class PaymentMismatchError(Exception):
    """Mollie's payment is not the one this row started: never opens a period."""


async def settle(
    db: AsyncSession, period: KiPeriod, payment: MolliePayment, *, now: datetime | None = None
) -> KiPeriod:
    """Bring the row in line with what Mollie says about its payment.

    Safe to call any number of times, from the webhook and from the browser's
    return in either order: a row already paid is left as it is, so a month is
    opened once however many times it is reported.
    """
    if period.status == KiPeriodStatus.PAID.value:
        return period
    if (
        payment.id != period.mollie_payment_id
        or payment.amount != period.amount
        or payment.currency != period.currency
        or payment.metadata.get("reference") != period.reference
    ):
        raise PaymentMismatchError(period.reference)

    if payment.status == "paid":
        moment = now or utc_now()
        later = [
            as_utc(other.ends_at)
            for other in await _paid(db, period.company_id)
            if other.ends_at is not None and as_utc(other.ends_at) > moment
        ]
        start = max([moment, *later])
        period.status = KiPeriodStatus.PAID.value
        period.paid_at = moment
        period.starts_at = start
        period.ends_at = add_month(start)
    elif payment.status in _FINAL_WITHOUT_MONEY:
        period.status = _FINAL_WITHOUT_MONEY[payment.status].value
    await db.flush()
    return period
