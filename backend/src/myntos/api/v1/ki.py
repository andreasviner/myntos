"""KI plans, monthly subscriptions, invoices, file reading, and KI chat.

The books are free and stay free. What a KI plan buys is the two larger levels
of Løynd («Medium» and «Smart») with weekly quotas that reset every Monday at
00:00 Norwegian time; «Gratis» stays unlimited for every company with KI turned
on. Internally each plan carries a weekly value budget in NOK (ex. VAT) spent
at Løynd's live ``/v1/models`` prices, and every user-facing surface and API
response reports remaining quota purely in percent (0 to 100 %).

Plans are billed monthly by Norwegian section 5-1 invoice (sent as EHF over
Peppol when the company can receive EHF, and as PDF by email and downloadable
in Myntos). Legacy one-off Mollie checkout routes stay intact for backwards
compatibility.

Reading plans, KI status, KI invoices, and saved chat threads is ``KEY_READ``.
Sending chat messages and creating/deleting threads are ``key_write`` routes.
Changing or canceling a subscription (and starting a payment) is owner-only
(``billing:manage``) and never open to agent keys (docs/agents-and-api.md rule 5).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.api.deps import (
    KEY_PREVIEW,
    KEY_READ,
    CurrentCompany,
    CurrentUser,
    DbSession,
    OutgoingMail,
    Storage,
    key_write,
)
from myntos.core.clock import as_utc
from myntos.core.config import get_settings
from myntos.domain.bilag import derived
from myntos.domain.bilag import service as bilag_service
from myntos.domain.bilag.models import READ_AS
from myntos.domain.identity.access import Permission, has_permission, require_permission
from myntos.domain.ki import chat as ki_chat
from myntos.domain.ki import chat_context
from myntos.domain.ki import invoicing as ki_invoicing
from myntos.domain.ki import plans as ki_plans
from myntos.domain.ki import service as ki_service
from myntos.domain.ki.errors import (
    KiBillingNotConfiguredError,
    KiModelUnavailableError,
    KiPaymentNotFoundError,
    KiPaymentProviderError,
    KiPlanNotFoundError,
    KiPurchaseNotAvailableError,
    KiTurnedOffError,
)
from myntos.domain.ki.models import (
    KiBillingInvoice,
    KiChatMessage,
    KiChatThread,
    KiPeriod,
    KiPeriodStatus,
    KiSubscription,
    KiSubscriptionStatus,
)
from myntos.integrations import loynd, mollie

logger = logging.getLogger(__name__)

_CHAT_MAX_TOKENS = 1800
_SSE_START = "start"
_SSE_DELTA = "delta"
_SSE_DONE = "ferdig"
_SSE_ERROR = "feil"


@dataclass
class KiRequest:
    """The model one request asks, at the level the company's plan allows.

    Built by :func:`ki_request` before the first question and finished by
    :meth:`finish` after the last, by every route with a level beside its text
    box (the bilag on ``/kontantstrom/``, both boxes on ``/faktura/``, and KI
    chat on ``/ki/``), so the rule for which level a question gets and the
    counting of what it used live in one place.
    """

    model: loynd.Model
    resolution: ki_service.Resolution
    cost_nok: Decimal = Decimal("0.000000")

    @property
    def level(self) -> loynd.Level:
        """The level that answered: lower than asked when it fell back."""
        return self.model.level

    async def finish(self, db: AsyncSession) -> list[str]:
        """Count paid NOK spend against the week and month, and return any notices."""
        notices = [self.resolution.notice] if self.resolution.notice else []
        if self.model.fell_back:
            notices.append(ki_service.REFUSED)
        cost = await self.model.cost_nok()
        self.cost_nok = cost
        await ki_service.record_usage(
            db,
            self.resolution.period_id,
            cost,
            tokens=self.model.usage.total,
            company_id=self.resolution.company_id,
            week_start=self.resolution.week_start,
        )
        return notices


async def ki_request(db: AsyncSession, company_id: int, wanted: loynd.Level) -> KiRequest:
    resolution = await ki_service.resolve(db, company_id, wanted)
    return KiRequest(model=loynd.Model(resolution.level), resolution=resolution)


router = APIRouter(tags=["ki"])


# --- Plans & status -----------------------------------------------------------------


class PlanResponse(BaseModel):
    code: str = Field(
        description="Stable name of the plan, sent back to choose it: `start`, `pluss`, `pro`."
    )
    name: str = Field(description="The plan's name as the page shows it, in Norwegian.")
    price_excl_vat: Decimal = Field(description="Kroner per month, excluding 25 % VAT.")
    vat: Decimal = Field(description="The VAT on one month, in kroner.")
    price_incl_vat: Decimal = Field(description="What one month costs with VAT, in kroner.")
    quota_label: str = Field(
        description=(
            "Relative weekly quota tier label as printed on the plan card, in Norwegian "
            "(for example «Standard ukekvote», «3× ukekvote», «8× ukekvote»). Paid usage is "  # noqa: RUF001
            "tracked internally by live model cost and shown as remaining percent."
        )
    )
    summary: str = Field(description="One line about who the plan suits, in Norwegian.")


def _plan(plan: ki_plans.Plan) -> PlanResponse:
    return PlanResponse(
        code=plan.code,
        name=plan.name,
        price_excl_vat=plan.price_excl_vat,
        vat=plan.vat,
        price_incl_vat=plan.price_incl_vat,
        quota_label=plan.quota_label,
        summary=plan.summary,
    )


class PlansResponse(BaseModel):
    plans: list[PlanResponse]
    currency: str = Field(description="Always `NOK`.")
    levels: list[str] = Field(
        description=(
            "The levels a KI request can ask for, cheapest first: `free` (unlimited for every "
            "company with KI on), `medium` and `thinking` (while the company has an active KI "
            "plan with weekly quota remaining)."
        )
    )


@router.get("/ki/planer", openapi_extra=KEY_READ)
async def list_plans(user: CurrentUser) -> PlansResponse:
    """The KI plans available, their relative weekly quota tiers, and the KI levels.

    Paid plans give weekly quotas on «Medium» and «Smart» (resetting every
    Monday at 00:00 Norwegian time, reported as remaining percent) and are
    billed monthly by invoice. «Gratis» is unlimited for every company with KI
    enabled. Prices are in NOK; 25 % VAT applies on top.
    """
    del user
    return PlansResponse(
        plans=[_plan(plan) for plan in ki_plans.PLANS],
        currency=ki_plans.CURRENCY,
        levels=[level.value for level in loynd.Level],
    )


class PeriodResponse(BaseModel):
    reference: str = Field(description="Myntos's reference for the one-off purchase.")
    plan: str = Field(description="The plan's code, as in `GET /api/v1/ki/planer`.")
    plan_name: str
    status: KiPeriodStatus = Field(
        description=(
            "`open` (checkout started, not finished), `paid`, or how it ended without "
            "payment: `failed`, `canceled`, `expired`."
        )
    )
    remaining_percent: int = Field(
        ge=0,
        le=100,
        description="Percentage of this month's paid KI quota remaining (0 to 100).",
    )
    used_percent: int = Field(
        ge=0,
        le=100,
        description="Percentage of this month's paid KI quota used (100 minus remaining_percent).",
    )
    amount: Decimal = Field(description="What was charged, including VAT, in kroner.")
    starts_at: datetime | None = Field(description="Set once paid.")
    ends_at: datetime | None = Field(
        description="Set once paid. One calendar month after the start."
    )
    test: bool = Field(description="Paid with Mollie's test mode: no money moved.")


def _period(period: KiPeriod) -> PeriodResponse:
    plan = ki_plans.by_code(period.plan)
    rem = ki_service.period_remaining_percent(period)
    return PeriodResponse(
        reference=period.reference,
        plan=period.plan,
        plan_name=plan.name if plan else period.plan,
        status=KiPeriodStatus(period.status),
        remaining_percent=rem,
        used_percent=100 - rem,
        amount=period.amount,
        starts_at=as_utc(period.starts_at) if period.starts_at else None,
        ends_at=as_utc(period.ends_at) if period.ends_at else None,
        test=period.mollie_mode != "live",
    )


class SubscriptionResponse(BaseModel):
    plan: str = Field(description="Active plan code: `start`, `pluss`, or `pro`.")
    plan_name: str
    status: KiSubscriptionStatus = Field(
        description="`active` (renews monthly), `canceling` (runs until period end), or `canceled`."
    )
    quota_label: str = Field(
        description="Relative weekly quota tier label for this plan, in Norwegian."
    )
    price_excl_vat: Decimal
    vat_amount: Decimal
    price_incl_vat: Decimal
    currency: str
    billing_email: str | None
    started_at: datetime
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    canceled_at: datetime | None


def _subscription(sub: KiSubscription) -> SubscriptionResponse:
    plan = ki_plans.by_code(sub.plan)
    return SubscriptionResponse(
        plan=sub.plan,
        plan_name=plan.name if plan else sub.plan,
        status=KiSubscriptionStatus(sub.status),
        quota_label=plan.quota_label if plan else sub.plan,
        price_excl_vat=sub.price_excl_vat,
        vat_amount=sub.vat_amount,
        price_incl_vat=sub.price_incl_vat,
        currency=sub.currency,
        billing_email=sub.billing_email,
        started_at=as_utc(sub.started_at),
        current_period_start=as_utc(sub.current_period_start),
        current_period_end=as_utc(sub.current_period_end),
        cancel_at_period_end=sub.cancel_at_period_end,
        canceled_at=as_utc(sub.canceled_at) if sub.canceled_at else None,
    )


class WeeklyUsageResponse(BaseModel):
    week_start: date = Field(description="Monday date (Europe/Oslo) of the current usage week.")
    resets_at: datetime = Field(
        description="UTC instant when next week's quota begins (Monday 00:00 Oslo)."
    )
    remaining_percent: int = Field(
        ge=0,
        le=100,
        description=(
            "Percentage of this week's paid KI quota remaining (0 to 100). "
            "0 means this week's quota is exhausted and «Gratis» answers until Monday reset."
        ),
    )
    used_percent: int = Field(
        ge=0,
        le=100,
        description=(
            "Percentage of this week's paid KI quota used so far (100 minus remaining_percent)."
        ),
    )


def _weekly(quota: ki_service.WeeklyQuota) -> WeeklyUsageResponse:
    return WeeklyUsageResponse(
        week_start=quota.week_start,
        resets_at=as_utc(quota.resets_at),
        remaining_percent=quota.remaining_percent,
        used_percent=quota.used_percent,
    )


class KiInvoiceResponse(BaseModel):
    id: int
    plan: str
    plan_name: str
    period_start: date
    period_end: date
    invoice_number: str | None
    amount_excl_vat: Decimal
    vat_amount: Decimal
    amount_incl_vat: Decimal
    currency: str
    issued_on: date
    due_date: date
    delivery_method: str = Field(description="`ehf`, `email`, or `portal`.")
    delivery_status: str = Field(description="`pending`, `sent`, `delivered`, or `failed`.")
    delivered_at: datetime | None
    has_pdf: bool


def _invoice(row: KiBillingInvoice) -> KiInvoiceResponse:
    plan = ki_plans.by_code(row.plan)
    return KiInvoiceResponse(
        id=row.id,
        plan=row.plan,
        plan_name=plan.name if plan else row.plan,
        period_start=row.period_start,
        period_end=row.period_end,
        invoice_number=row.invoice_number,
        amount_excl_vat=row.amount_excl_vat,
        vat_amount=row.vat_amount,
        amount_incl_vat=row.amount_incl_vat,
        currency=row.currency,
        issued_on=row.issued_on,
        due_date=row.due_date,
        delivery_method=row.delivery_method,
        delivery_status=row.delivery_status,
        delivered_at=as_utc(row.delivered_at) if row.delivered_at else None,
        has_pdf=row.seller_document_id is not None,
    )


class LevelResponse(BaseModel):
    level: loynd.Level
    name: str = Field(description="As the page shows it: «Gratis», «Medium», «Smart».")
    available: bool = Field(
        description=(
            "Whether a request asking for this level gets it now. A request asking for one that "
            "is not available is still answered, at `free`, with a warning saying why."
        )
    )


class KiStatusResponse(BaseModel):
    ki_enabled: bool = Field(
        description="The company's own switch. Off means no model sees anything, at any level."
    )
    model_available: bool = Field(description="Whether this deployment has a model at all.")
    plans_on_sale: bool = Field(
        description=(
            "Whether this deployment offers KI plans (either monthly invoiced subscriptions or "
            "one-off Mollie checkout)."
        )
    )
    can_buy: bool = Field(
        description=(
            "Whether this deployment offers plans and the caller may activate or buy one for this "
            "company (only the company's owners can). Never open to an agent key."
        )
    )
    subscription_available: bool = Field(
        description="Whether monthly invoiced KI subscriptions are configured on this deployment."
    )
    can_manage_subscription: bool = Field(
        description="Whether the caller may activate, change, or cancel a monthly KI subscription."
    )
    levels: list[LevelResponse]
    subscription: SubscriptionResponse | None = Field(
        description="The active or canceling monthly KI subscription, if one is running."
    )
    weekly: WeeklyUsageResponse | None = Field(
        description="This week's paid KI quota remaining in percent, when a plan is active."
    )
    current: PeriodResponse | None = Field(
        description="The one-off Mollie month running now, if one is."
    )
    upcoming: list[PeriodResponse] = Field(
        description="One-off months already paid for that start when the current one ends."
    )


@router.get("/companies/{company_id}/ki", openapi_extra=KEY_READ)
async def ki_status(context: CurrentCompany, db: DbSession) -> KiStatusResponse:
    """The company's KI status: switch, available levels, active subscription, and weekly quota.

    Read this before asking for `medium` or `thinking` on any KI route, or read
    the `ki_level` the answer comes back with: a paid level without an active
    plan or after the weekly quota is spent answers at `free` with a notice,
    never an error.
    """
    company = context.company
    model_ready = loynd.is_configured()
    model = company.ki_enabled and model_ready
    sub = await ki_service.active_subscription(db, company.id)
    current = await ki_service.current(db, company.id)
    quota = await ki_service.weekly_quota(db, company.id, subscription=sub, period=current)
    has_period_value = current is not None and ki_service.has_period_quota(current)
    paid_ok = (
        model
        and quota is not None
        and not quota.exhausted
        and (sub is not None or has_period_value)
    )
    sub_available = ki_invoicing.is_billing_configured() and model_ready
    mollie_available = mollie.is_configured() and model_ready
    on_sale = sub_available or mollie_available
    is_owner = has_permission(context.membership.role, Permission.BILLING_MANAGE)
    return KiStatusResponse(
        ki_enabled=company.ki_enabled,
        model_available=model_ready,
        plans_on_sale=on_sale,
        can_buy=on_sale and is_owner,
        subscription_available=sub_available,
        can_manage_subscription=sub_available and is_owner,
        levels=[
            LevelResponse(
                level=level,
                name=ki_service.level_name(level),
                available=model and (paid_ok if level.is_paid else True),
            )
            for level in loynd.Level
        ],
        subscription=_subscription(sub) if sub else None,
        weekly=_weekly(quota) if quota else None,
        current=_period(current) if current else None,
        upcoming=[_period(period) for period in await ki_service.upcoming(db, company.id)],
    )


# --- Monthly subscriptions & invoicing ----------------------------------------------


class SubscriptionRequest(BaseModel):
    plan: str = Field(
        description="Plan code from `GET /api/v1/ki/planer`: `start`, `pluss`, `pro`."
    )
    billing_email: EmailStr | None = Field(
        default=None,
        description=(
            "Optional email address for PDF copies of monthly KI invoices. "
            "Defaults to the activating owner's account email."
        ),
    )


class SubscriptionActivationResponse(BaseModel):
    subscription: SubscriptionResponse
    invoice: KiInvoiceResponse | None = Field(
        description=(
            "The section 5-1 invoice issued for the new billing month, or null when switching "
            "plans inside an already-invoiced month (or when the company is the billing company)."
        )
    )
    weekly: WeeklyUsageResponse


@router.put("/companies/{company_id}/ki/abonnement")
async def set_subscription(
    payload: SubscriptionRequest,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    storage: Storage,
    mailer: OutgoingMail,
) -> SubscriptionActivationResponse:
    """Activate or switch the company's monthly invoiced KI subscription.

    Owner-only (``billing:manage``), never open to agent keys. Starting a new
    subscription immediately activates the chosen plan's weekly quota and
    issues the first month's Norwegian section 5-1 invoice from the configured
    billing company (delivered via EHF over Peppol when the company can receive
    EHF, plus PDF by email and downloadable in Myntos). Switching plans inside
    an active month updates the weekly quota immediately without double-billing;
    the next monthly invoice reflects the new plan.
    """
    require_permission(context.membership.role, Permission.BILLING_MANAGE)
    plan = ki_plans.by_code(payload.plan)
    if plan is None:
        raise KiPlanNotFoundError("ukjent KI-plan")
    if not loynd.is_configured() or not ki_invoicing.is_billing_configured():
        raise KiBillingNotConfiguredError(
            "Månedlig KI-abonnement kan ikke aktiveres i denne installasjonen."
        )
    if not context.company.ki_enabled:
        raise KiTurnedOffError("Slå på KI for selskapet før du velger en KI-plan.")

    billing_email = str(payload.billing_email) if payload.billing_email else user.email
    sub, is_new_period = await ki_service.activate_subscription(
        db,
        company_id=context.company.id,
        plan=plan,
        user_id=user.id,
        billing_email=billing_email,
    )

    invoice_row: KiBillingInvoice | None = None
    if is_new_period:
        invoice_row = await ki_invoicing.issue_subscription_invoice(
            db,
            storage,
            mailer,
            buyer=context.company,
            subscription=sub,
        )

    quota = await ki_service.weekly_quota(db, context.company.id, subscription=sub)
    assert quota is not None  # noqa: S101 - subscription was just activated
    return SubscriptionActivationResponse(
        subscription=_subscription(sub),
        invoice=_invoice(invoice_row) if invoice_row is not None else None,
        weekly=_weekly(quota),
    )


@router.delete("/companies/{company_id}/ki/abonnement")
async def cancel_subscription(context: CurrentCompany, db: DbSession) -> SubscriptionResponse:
    """Cancel the company's monthly KI subscription at the end of the running month.

    Owner-only (``billing:manage``), never open to agent keys. Paid levels stay
    available with the plan's weekly quota until ``current_period_end``, and no
    further monthly invoice is issued after that.
    """
    require_permission(context.membership.role, Permission.BILLING_MANAGE)
    sub = await ki_service.cancel_subscription(db, company_id=context.company.id)
    return _subscription(sub)


@router.get("/companies/{company_id}/ki/fakturaer", openapi_extra=KEY_READ)
async def list_ki_invoices(context: CurrentCompany, db: DbSession) -> list[KiInvoiceResponse]:
    """List monthly invoices issued for this company's KI subscription, newest first.

    Each row gives the invoice number, billing period, amounts in NOK (excl. VAT,
    VAT, incl. VAT), due date, delivery method (`ehf`, `email`, or `portal`),
    and whether the PDF can be downloaded via ``GET …/ki/fakturaer/{invoice_id}/pdf``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    rows = await ki_invoicing.list_invoices(db, context.company.id)
    return [_invoice(row) for row in rows]


@router.get("/companies/{company_id}/ki/fakturaer/{invoice_id}/pdf", openapi_extra=KEY_READ)
async def download_ki_invoice_pdf(
    invoice_id: int, context: CurrentCompany, db: DbSession
) -> Response:
    """Download the PDF of one monthly KI subscription invoice for this company.

    404 ``ki_invoice_not_found`` when the id does not belong to this company.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    pdf_bytes, filename = await ki_invoicing.get_invoice_pdf(
        db, company_id=context.company.id, invoice_id=invoice_id
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# --- Legacy one-off Mollie checkout -------------------------------------------------


class PurchaseRequest(BaseModel):
    plan: str = Field(description="The plan's code from `GET /api/v1/ki/planer`.")


class PurchaseResponse(BaseModel):
    reference: str
    checkout_url: str = Field(description="Mollie's payment page. Send the browser there.")


@router.post("/companies/{company_id}/ki/betaling", status_code=status.HTTP_201_CREATED)
async def start_purchase(
    payload: PurchaseRequest, context: CurrentCompany, db: DbSession
) -> PurchaseResponse:
    """Start paying for one month of a plan, and get Mollie's checkout page.

    Only the company's owners may buy (``billing:manage``), and never with an
    agent key. The month starts when the payment is confirmed, or when the month
    already paid for ends, whichever is later. Nothing is charged again later.
    """
    require_permission(context.membership.role, Permission.BILLING_MANAGE)
    plan = ki_plans.by_code(payload.plan)
    if plan is None:
        raise KiPlanNotFoundError("ukjent KI-plan")
    mode = mollie.active_mode()
    if mode is None or not loynd.is_configured():
        raise KiPurchaseNotAvailableError("KI-planer kan ikke kjøpes i denne installasjonen")
    if not context.company.ki_enabled:
        raise KiTurnedOffError("Slå på KI for selskapet før du kjøper en plan.")

    period = await ki_service.start_purchase(
        db,
        company_id=context.company.id,
        plan=plan,
        mode=mode,
        user_id=context.membership.user_id,
    )
    base = get_settings().frontend_base_url.rstrip("/")
    try:
        payment = await mollie.create_payment(
            amount=plan.price_incl_vat,
            description=f"Myntos {plan.name}, en måned",
            redirect_url=f"{base}/ki/about/?betaling={quote(period.reference)}",
            metadata={"reference": period.reference, "plan": plan.code},
        )
    except mollie.MollieError as error:
        raise KiPaymentProviderError(
            "Betalingen kunne ikke startes. Prøv igjen om litt."
        ) from error
    if payment.checkout_url is None:
        raise KiPaymentProviderError("Betalingen kunne ikke startes. Prøv igjen om litt.")
    period.mollie_payment_id = payment.id
    await db.flush()
    return PurchaseResponse(reference=period.reference, checkout_url=payment.checkout_url)


async def _settle(db: DbSession, period: KiPeriod) -> KiPeriod:
    """Ask Mollie about the period's payment and bring the row in line."""
    if period.status != KiPeriodStatus.OPEN.value or period.mollie_payment_id is None:
        return period
    try:
        payment = await mollie.get_payment(period.mollie_payment_id)
    except mollie.MollieError as error:
        raise KiPaymentProviderError("Fikk ikke svar fra Mollie. Prøv igjen om litt.") from error
    try:
        return await ki_service.settle(db, period, payment)
    except ki_service.PaymentMismatchError as error:
        logger.error("Mollie's payment does not match KI purchase %s", period.reference)
        raise KiPaymentProviderError("Betalingen stemmer ikke med kjøpet.") from error


@router.get("/companies/{company_id}/ki/betaling/{reference}")
async def purchase_status(reference: str, context: CurrentCompany, db: DbSession) -> PeriodResponse:
    """Where a Mollie purchase stands, asking Mollie first if it is still open."""
    period = await ki_service.by_reference(db, company_id=context.company.id, reference=reference)
    if period is None:
        raise KiPaymentNotFoundError("fant ikke kjøpet")
    return _period(await _settle(db, period))


@router.post("/webhooks/mollie")
async def mollie_webhook(request: Request, db: DbSession) -> Response:
    """Mollie saying a payment changed. Re-fetched; never believed."""
    form = await request.form()
    payment_id = form.get("id")
    if not isinstance(payment_id, str) or not mollie.is_valid_payment_id(payment_id):
        return Response(status_code=status.HTTP_200_OK)
    period = await ki_service.by_payment(db, payment_id)
    if period is None or not mollie.is_configured():
        return Response(status_code=status.HTTP_200_OK)
    try:
        await _settle(db, period)
    except KiPaymentProviderError as error:
        if isinstance(error.__cause__, ki_service.PaymentMismatchError):
            return Response(status_code=status.HTTP_200_OK)
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response(status_code=status.HTTP_200_OK)


# --- Reading a picture or PDF into a KI box -----------------------------------------


class ReadFileResponse(BaseModel):
    text: str = Field(description="What the file says, as plain text. Empty when it says nothing.")
    empty: bool = Field(description="True when nothing readable came back: a blank page, a wall.")


@router.post("/companies/{company_id}/ki/les", openapi_extra=KEY_PREVIEW)
async def read_file(
    context: CurrentCompany, file: Annotated[UploadFile, File()]
) -> ReadFileResponse:
    """Read a picture or a PDF into text, for a KI text box.

    The reading itself is always Løynd's own vision model on its own machines,
    whatever the level, and counts against no plan. Stores nothing.

    409 ``ki_turned_off`` when the company has KI off; 502
    ``ki_model_unavailable`` when this deployment has no model or Løynd did not
    answer.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    if not context.company.ki_enabled:
        raise KiTurnedOffError("KI er slått av for dette selskapet.")
    content = await file.read()
    content_type = bilag_service.validate_document(content)
    filename = file.filename or ""
    if content_type in READ_AS:
        try:
            content = await derived.reading_copy(content)
        except derived.DerivationError as error:
            raise KiModelUnavailableError(
                "Bildet kunne ikke leses. Prøv et annet format."
            ) from error
        stem = filename.rsplit(".", maxsplit=1)[0] if "." in filename else filename
        filename, content_type = f"{stem or 'bilde'}.jpg", READ_AS[content_type]
    try:
        document = await loynd.extract_file(
            content=content, filename=filename, content_type=content_type
        )
    except loynd.LoyndError as error:
        raise KiModelUnavailableError(
            "Vi fikk ikke lest filen nå. Skriv det inn selv, eller prøv igjen."
        ) from error
    return ReadFileResponse(text=document.text.strip(), empty=document.empty)


# --- KI Chat over the company's books -----------------------------------------------


class ChatThreadSummaryResponse(BaseModel):
    id: int
    title: str
    message_count: int
    preview: str | None
    created_at: datetime
    updated_at: datetime


class ChatMessageResponse(BaseModel):
    id: int
    role: str = Field(description="`user` or `assistant`.")
    content: str
    level: loynd.Level | None = Field(
        description=(
            "The KI level that answered (`free`, `medium`, `thinking`), on assistant messages."
        )
    )
    requested_level: loynd.Level | None
    notices: list[str]
    created_at: datetime


class ChatThreadDetailResponse(BaseModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[ChatMessageResponse]


class CreateChatThreadRequest(BaseModel):
    title: str | None = Field(default=None, max_length=160)


class SendChatMessageRequest(BaseModel):
    content: str = Field(
        min_length=1,
        max_length=4000,
        description="The question to ask about this company's books.",
    )
    level: loynd.Level = Field(
        default=loynd.Level.FREE,
        description="Requested KI level: `free`, `medium`, or `thinking`.",
    )


class ChatTurnResponse(BaseModel):
    thread: ChatThreadSummaryResponse
    user_message: ChatMessageResponse
    assistant_message: ChatMessageResponse
    ki_level: loynd.Level
    notices: list[str]


def _message_response(msg: KiChatMessage) -> ChatMessageResponse:
    return ChatMessageResponse(
        id=msg.id,
        role=msg.role,
        content=msg.content,
        level=loynd.Level(msg.level) if msg.level else None,
        requested_level=loynd.Level(msg.requested_level) if msg.requested_level else None,
        notices=[str(item) for item in (msg.notices or [])],
        created_at=as_utc(msg.created_at),
    )


def _thread_summary(
    thread: KiChatThread, *, message_count: int, preview: str | None
) -> ChatThreadSummaryResponse:
    return ChatThreadSummaryResponse(
        id=thread.id,
        title=thread.title,
        message_count=message_count,
        preview=preview,
        created_at=as_utc(thread.created_at),
        updated_at=as_utc(thread.updated_at),
    )


def _sse_frame(name: str, payload: object) -> str:
    body: object = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    return f"event: {name}\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"


@router.get("/companies/{company_id}/ki/samtaler", openapi_extra=KEY_READ)
async def list_chat_threads(
    context: CurrentCompany, db: DbSession
) -> list[ChatThreadSummaryResponse]:
    """List saved KI chat threads for this company, newest activity first."""
    require_permission(context.membership.role, Permission.LEDGER_READ)
    items = await ki_chat.list_threads(db, company_id=context.company.id)
    return [
        _thread_summary(item.thread, message_count=item.message_count, preview=item.preview)
        for item in items
    ]


@router.post(
    "/companies/{company_id}/ki/samtaler",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_write("opprette en KI-samtale"),
)
async def create_chat_thread(
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    payload: CreateChatThreadRequest | None = None,
) -> ChatThreadDetailResponse:
    """Create a new saved KI chat thread for this company.

    Refuses 409 ``ki_turned_off`` when KI is switched off for the company.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    if not context.company.ki_enabled:
        raise KiTurnedOffError("KI er slått av for dette selskapet.")
    thread = await ki_chat.create_thread(
        db,
        company_id=context.company.id,
        user_id=user.id,
        title=payload.title if payload is not None else None,
    )
    return ChatThreadDetailResponse(
        id=thread.id,
        title=thread.title,
        created_at=as_utc(thread.created_at),
        updated_at=as_utc(thread.updated_at),
        messages=[],
    )


@router.get("/companies/{company_id}/ki/samtaler/{thread_id}", openapi_extra=KEY_READ)
async def get_chat_thread(
    thread_id: int, context: CurrentCompany, db: DbSession
) -> ChatThreadDetailResponse:
    """Read one saved KI chat thread and every message in it.

    404 ``ki_chat_thread_not_found`` if the thread does not belong to this company.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    thread = await ki_chat.get_thread(db, company_id=context.company.id, thread_id=thread_id)
    messages = await ki_chat.list_messages(db, company_id=context.company.id, thread_id=thread.id)
    return ChatThreadDetailResponse(
        id=thread.id,
        title=thread.title,
        created_at=as_utc(thread.created_at),
        updated_at=as_utc(thread.updated_at),
        messages=[_message_response(msg) for msg in messages],
    )


@router.delete(
    "/companies/{company_id}/ki/samtaler/{thread_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=key_write("slette en KI-samtale"),
)
async def delete_chat_thread(thread_id: int, context: CurrentCompany, db: DbSession) -> Response:
    """Delete one saved KI chat thread and its messages.

    404 ``ki_chat_thread_not_found`` if the thread does not belong to this company.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    await ki_chat.delete_thread(db, company_id=context.company.id, thread_id=thread_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _prepare_chat_turn(
    *,
    db: AsyncSession,
    context: CurrentCompany,
    thread_id: int,
    payload: SendChatMessageRequest,
) -> tuple[KiChatThread, list[KiChatMessage], KiRequest, str, list[tuple[str, str]]]:
    """Validate KI state, load thread + history, resolve level, and assemble books prompt."""
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    if not context.company.ki_enabled:
        raise KiTurnedOffError("KI er slått av for dette selskapet.")
    # Look up the thread scoped to this company before checking model availability
    # so cross-company thread ids always answer 404 rather than leaking 502.
    thread = await ki_chat.get_thread(db, company_id=context.company.id, thread_id=thread_id)
    if not loynd.is_configured():
        raise KiModelUnavailableError("KI-modellen er ikke tilgjengelig i denne installasjonen.")
    prior = await ki_chat.list_messages(db, company_id=context.company.id, thread_id=thread.id)
    req = await ki_request(db, context.company.id, payload.level)
    recent_questions = [m.content for m in prior if m.role == "user"]
    system_prompt = await chat_context.build_system_prompt(
        db,
        company=context.company,
        question=payload.content,
        recent_questions=recent_questions,
    )
    history = ki_chat.history_pairs(prior)
    return thread, prior, req, system_prompt, history


@router.post(
    "/companies/{company_id}/ki/samtaler/{thread_id}/meldinger",
    openapi_extra=key_write("sende en melding i KI-chat"),
)
async def send_chat_message(
    thread_id: int,
    payload: SendChatMessageRequest,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
) -> ChatTurnResponse:
    """Ask a question about this company's books in a saved KI chat thread.

    Reads a fresh structured snapshot of the company's ledger, invoices,
    deadlines, and relevant vouchers, then answers at the requested KI level
    (falling back to ``free`` with a notice when the company has no active plan
    or has reached its weekly quota). Paid answers count against the company's
    weekly quota.

    Refuses 404 ``ki_chat_thread_not_found``, 409 ``ki_turned_off``, or 502
    ``ki_model_unavailable``.
    """
    thread, prior, req, system_prompt, history = await _prepare_chat_turn(
        db=db, context=context, thread_id=thread_id, payload=payload
    )
    try:
        answer = await req.model.complete(
            system=system_prompt,
            prompt=payload.content.strip(),
            max_tokens=_CHAT_MAX_TOKENS,
            history=history,
        )
    except loynd.LoyndError as error:
        raise KiModelUnavailableError("KI svarte ikke akkurat nå. Prøv igjen om litt.") from error

    user_msg = await ki_chat.add_user_message(
        db,
        thread=thread,
        content=payload.content,
        requested_level=payload.level.value,
        user_id=user.id,
    )
    notices = await req.finish(db)
    assistant_msg = await ki_chat.add_assistant_message(
        db,
        thread=thread,
        content=answer,
        level=req.level.value,
        requested_level=payload.level.value,
        tokens_used=req.model.usage.total,
        cost_nok=req.cost_nok,
        notices=notices,
    )
    preview = " ".join(assistant_msg.content.split()).strip()[:120]
    return ChatTurnResponse(
        thread=_thread_summary(thread, message_count=len(prior) + 2, preview=preview),
        user_message=_message_response(user_msg),
        assistant_message=_message_response(assistant_msg),
        ki_level=req.level,
        notices=notices,
    )


@router.post(
    "/companies/{company_id}/ki/samtaler/{thread_id}/meldinger/strom",
    openapi_extra=key_write("sende en melding i KI-chat"),
)
async def stream_chat_message(
    thread_id: int,
    payload: SendChatMessageRequest,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
) -> StreamingResponse:
    """Ask a question about this company's books and stream the reply as SSE.

    Same grounding, level resolution, and weekly quota counting as
    ``POST …/ki/samtaler/{thread_id}/meldinger``, delivered as Server-Sent
    Events: ``start`` (saved user message and resolved level), repeated
    ``delta`` frames (``{"tekst": ...}``), and ``ferdig`` (saved turn, final
    level, and notices), or ``feil`` if the model stops answering mid-stream.

    Refusals (404 ``ki_chat_thread_not_found``, 409 ``ki_turned_off``, 502
    ``ki_model_unavailable``) happen before the stream opens.
    """
    thread, prior, req, system_prompt, history = await _prepare_chat_turn(
        db=db, context=context, thread_id=thread_id, payload=payload
    )
    user_msg = await ki_chat.add_user_message(
        db,
        thread=thread,
        content=payload.content,
        requested_level=payload.level.value,
        user_id=user.id,
    )
    await db.commit()
    prior_count = len(prior)
    prompt_text = payload.content.strip()

    async def frames() -> AsyncIterator[str]:
        yield _sse_frame(
            _SSE_START,
            {
                "thread_id": thread.id,
                "thread_title": thread.title,
                "user_message": _message_response(user_msg).model_dump(mode="json"),
                "ki_level": req.level.value,
            },
        )
        chunks: list[str] = []
        try:
            async for piece in req.model.stream(
                system=system_prompt,
                prompt=prompt_text,
                max_tokens=_CHAT_MAX_TOKENS,
                history=history,
            ):
                chunks.append(piece)
                yield _sse_frame(_SSE_DELTA, {"tekst": piece})
            answer = "".join(chunks).strip()
            if not answer:
                yield _sse_frame(
                    _SSE_ERROR,
                    {"melding": "KI ga et tomt svar. Prøv å stille spørsmålet på nytt."},
                )
                return
            notices = await req.finish(db)
            assistant_msg = await ki_chat.add_assistant_message(
                db,
                thread=thread,
                content=answer,
                level=req.level.value,
                requested_level=payload.level.value,
                tokens_used=req.model.usage.total,
                cost_nok=req.cost_nok,
                notices=notices,
            )
            await db.commit()
            preview = " ".join(assistant_msg.content.split()).strip()[:120]
            done = ChatTurnResponse(
                thread=_thread_summary(thread, message_count=prior_count + 2, preview=preview),
                user_message=_message_response(user_msg),
                assistant_message=_message_response(assistant_msg),
                ki_level=req.level,
                notices=notices,
            )
            yield _sse_frame(_SSE_DONE, done)
        except loynd.LoyndError:
            yield _sse_frame(
                _SSE_ERROR,
                {"melding": "KI svarte ikke akkurat nå. Prøv igjen om litt."},
            )
        except Exception:
            logger.exception("KI chat stream failed mid-response")
            yield _sse_frame(
                _SSE_ERROR,
                {"melding": "Noe gikk galt under svaret. Prøv igjen om litt."},
            )

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )
