"""Monthly Norwegian section 5-1 invoicing and delivery for KI subscriptions.

Each month of an active KI subscription is billed by issuing a real
:class:`~myntos.domain.faktura.models.SalesDocument` from the configured seller
company (``KI_BILLING_COMPANY_ID``, e.g. Lindeman Technologies AS):

1. Look up or register the subscribing company as a customer (``Party``) in the
   seller company's books, matched by ``orgnr``.
2. Draft and issue a compliant section 5-1 invoice (gapless number series,
   double-entry voucher in the seller's ``SALG`` journal, automatic KID,
   frozen EHF XML, archived PDF).
3. Record a :class:`~myntos.domain.ki.models.KiBillingInvoice` linked to the
   subscribing company and the seller's document.
4. Deliver the invoice:
   - First over Peppol as EHF (via Ademico) whenever Ademico is configured, the
     seller is registered on Peppol, and the buyer can receive EHF in SMP/ELMA.
   - And send a PDF copy by email when a mailer and billing email are configured.
   - Always downloadable as PDF directly from ``/ki/about/#planer``.

Delivery failures never roll back an issued section 5-1 invoice: the invoice
stands in the seller's books and the buyer can always download the PDF in Myntos.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from myntos.core.clock import as_utc, utc_now
from myntos.core.config import get_settings
from myntos.core.mail import Mailer
from myntos.domain.faktura import pdf as faktura_pdf
from myntos.domain.faktura import service as faktura_service
from myntos.domain.faktura.models import SalesDocument
from myntos.domain.faktura.service import LineInput
from myntos.domain.faktura.totals import totals as faktura_totals
from myntos.domain.identity.models import CompanyMembership, OrganizationRole, User
from myntos.domain.ki import invoice_mail
from myntos.domain.ki import plans as ki_plans
from myntos.domain.ki import service as ki_service
from myntos.domain.ki.errors import KiBillingNotConfiguredError, KiInvoiceNotFoundError
from myntos.domain.ki.models import (
    KiBillingInvoice,
    KiInvoiceDeliveryMethod,
    KiInvoiceDeliveryStatus,
    KiSubscription,
    KiSubscriptionStatus,
)
from myntos.domain.ledger import parties as party_service
from myntos.domain.ledger.models import Company, Party, PartyType
from myntos.domain.peppol import service as peppol_service
from myntos.domain.peppol.errors import PeppolError
from myntos.domain.peppol.models import TransmissionStatus
from myntos.integrations import ademico, peppol_smp
from myntos.storage.base import DocumentStorage

logger = logging.getLogger(__name__)

_OSLO = ZoneInfo("Europe/Oslo")
_FIRST_SWEEP_DELAY = 120.0
_DUE_DAYS = 14

Sessions = async_sessionmaker[AsyncSession]


def is_billing_configured() -> bool:
    """Whether monthly KI subscription invoicing is configured on this deployment."""
    return get_settings().ki_billing_company_id is not None


async def load_seller_company(db: AsyncSession) -> Company:
    """Load the configured seller company with its bank accounts, or raise 503."""
    seller_id = get_settings().ki_billing_company_id
    if seller_id is None:
        raise KiBillingNotConfiguredError(
            "Månedlig fakturering av KI-abonnement er ikke aktivert i denne installasjonen."
        )
    seller = await db.scalar(
        select(Company)
        .options(selectinload(Company.bank_accounts))
        .where(Company.id == seller_id, Company.closed_at.is_(None))
    )
    if seller is None:
        raise KiBillingNotConfiguredError(
            "Faktureringsselskapet for KI-abonnement ble ikke funnet."
        )
    return seller


async def _find_or_create_customer(db: AsyncSession, *, seller: Company, buyer: Company) -> Party:
    """Find ``buyer`` as a customer in ``seller``'s books, or register it."""
    existing: Party | None = None
    if buyer.orgnr:
        existing = await db.scalar(
            select(Party).where(
                Party.company_id == seller.id,
                Party.party_type == PartyType.KUNDE,
                Party.orgnr == buyer.orgnr,
            )
        )
    if existing is not None:
        # Keep buyer name and address up to date before snapshotting onto the invoice.
        existing.name = buyer.name.strip()
        existing.address_line = buyer.address_line
        existing.postal_code = buyer.postal_code
        existing.city = buyer.city
        existing.country = buyer.country
        await db.flush()
        return existing

    return await party_service.create_party(
        db,
        company=seller,
        party_type=PartyType.KUNDE,
        name=buyer.name,
        orgnr=buyer.orgnr,
        address_line=buyer.address_line,
        postal_code=buyer.postal_code,
        city=buyer.city,
        country=buyer.country,
    )


async def _owner_email_for(db: AsyncSession, company_id: int) -> str | None:
    """Fallback recipient email: an owner of ``company_id``."""
    email: str | None = await db.scalar(
        select(User.email)
        .join(CompanyMembership, CompanyMembership.user_id == User.id)
        .where(
            CompanyMembership.company_id == company_id,
            CompanyMembership.role == OrganizationRole.OWNER,
            User.is_active.is_(True),
        )
        .order_by(CompanyMembership.created_at, User.id)
        .limit(1)
    )
    return email


def _dato(value: date) -> str:
    return value.strftime("%d.%m.%Y")


async def issue_subscription_invoice(
    db: AsyncSession,
    storage: DocumentStorage,
    mailer: Mailer,
    *,
    buyer: Company,
    subscription: KiSubscription,
    now: datetime | None = None,
) -> KiBillingInvoice | None:
    """Issue and deliver one monthly section 5-1 invoice for ``subscription``.

    Idempotent for ``(buyer.id, period_start, period_end)``: returns the
    existing invoice row if this billing cycle was already invoiced.
    Returns ``None`` when the subscribing company is the seller company itself
    (a company never invoices itself in Norwegian accounting).
    """
    moment = as_utc(now or utc_now())
    seller = await load_seller_company(db)
    if seller.id == buyer.id:
        return None

    period_start = as_utc(subscription.current_period_start).astimezone(_OSLO).date()
    period_end = as_utc(subscription.current_period_end).astimezone(_OSLO).date()

    existing: KiBillingInvoice | None = await db.scalar(
        select(KiBillingInvoice).where(
            KiBillingInvoice.company_id == buyer.id,
            KiBillingInvoice.period_start == period_start,
            KiBillingInvoice.period_end == period_end,
        )
    )
    if existing is not None:
        return existing

    plan = ki_plans.by_code(subscription.plan)
    plan_name = plan.name if plan is not None else subscription.plan
    quota_note = f" ({plan.quota_label})" if plan is not None else ""
    issue_date = moment.astimezone(_OSLO).date()
    due_date = issue_date + timedelta(days=_DUE_DAYS)
    tax_code = "3" if seller.vat_registered else "0"

    customer = await _find_or_create_customer(db, seller=seller, buyer=buyer)
    period_text = f"{_dato(period_start)} til {_dato(period_end)}"
    draft = await faktura_service.create_draft(
        db,
        company=seller,
        party_pk=customer.id,
        buyer_is_business=True,
        delivery_date=issue_date,
        delivery_place="Digital tjeneste",
        due_date=due_date,
        order_reference=f"KI-{subscription.plan.upper()}-{period_start.isoformat()}",
        note=f"Månedlig abonnement på Myntos {plan_name}{quota_note} for perioden {period_text}.",
        lines=[
            LineInput(
                description=f"Myntos {plan_name} ({period_text})",
                quantity=Decimal("1"),
                unit_code="MON",
                unit_price=subscription.price_excl_vat,
                tax_code=tax_code,
            )
        ],
        today=issue_date,
    )
    issued = await faktura_service.issue(
        db,
        storage,
        company=seller,
        document_pk=draft.id,
        user_id=None,
        today=issue_date,
        now=moment,
        peppol_setup_required=False,
    )
    computed = faktura_totals(issued)
    recipient_email = subscription.billing_email or await _owner_email_for(db, buyer.id)

    record = KiBillingInvoice(
        company_id=buyer.id,
        subscription_id=subscription.id,
        plan=subscription.plan,
        period_start=period_start,
        period_end=period_end,
        seller_company_id=seller.id,
        seller_document_id=issued.id,
        invoice_number=issued.document_number,
        amount_excl_vat=computed.tax_exclusive,
        vat_amount=computed.tax_total,
        amount_incl_vat=computed.payable,
        currency=subscription.currency,
        issued_on=issue_date,
        due_date=issued.due_date or due_date,
        delivery_method=KiInvoiceDeliveryMethod.PORTAL.value,
        delivery_status=KiInvoiceDeliveryStatus.PENDING.value,
        recipient_email=recipient_email,
    )
    db.add(record)
    await db.flush()

    await _deliver_invoice(
        db,
        mailer=mailer,
        seller=seller,
        buyer=buyer,
        issued=issued,
        record=record,
        plan_name=plan_name,
    )
    await db.flush()
    return record


async def _deliver_invoice(
    db: AsyncSession,
    *,
    mailer: Mailer,
    seller: Company,
    buyer: Company,
    issued: SalesDocument,
    record: KiBillingInvoice,
    plan_name: str,
) -> None:
    """Deliver ``issued`` via EHF over Peppol when possible, and email the PDF."""
    ehf_sent = False
    delivery_error: str | None = None

    # 1. Try EHF over Peppol when Ademico is configured and buyer receives EHF.
    if ademico.is_configured() and buyer.orgnr:
        try:
            if await peppol_smp.can_receive(buyer.orgnr, credit_note=False):
                sendable = await peppol_service.prepare_send(
                    db, company=seller, document_pk=issued.id
                )
                peppol_service.require_verified(sendable.registration)
                filename = f"faktura-{issued.document_number or issued.id}.xml"
                sent = await ademico.send(xml=sendable.xml, filename=filename)
                await peppol_service.record_transmission(
                    db,
                    company=seller,
                    document=sendable.document,
                    remote_document_id=sent.id,
                    recipient=ademico.peppol_address(sendable.buyer_orgnr),
                    status=TransmissionStatus(sent.delivery.status),
                    failure_category=sent.delivery.failure_category,
                    failure_message=sent.delivery.failure_message,
                )
                ehf_sent = True
                record.delivery_method = KiInvoiceDeliveryMethod.EHF.value
                record.delivery_status = (
                    KiInvoiceDeliveryStatus.DELIVERED.value
                    if sent.delivery.status == TransmissionStatus.DELIVERED.value
                    else KiInvoiceDeliveryStatus.SENT.value
                )
                record.delivered_at = utc_now()
        except (peppol_smp.SmpLookupError, PeppolError, ademico.AdemicoError) as error:
            delivery_error = type(error).__name__
            logger.warning(
                "Could not send KI invoice %s via EHF (%s); falling back to email/portal",
                record.invoice_number,
                delivery_error,
            )

    # 2. Send PDF by email when mailer and address are configured.
    if mailer.configured and record.recipient_email:
        base_url = get_settings().frontend_base_url.rstrip("/")
        pdf_bytes = faktura_pdf.render(issued)
        mail = invoice_mail.compose_invoice_mail(
            to=record.recipient_email,
            buyer_name=buyer.name,
            seller_name=seller.name,
            plan_name=plan_name,
            invoice_number=issued.document_number or str(issued.id),
            period_start=record.period_start,
            period_end=record.period_end,
            amount_incl_vat=record.amount_incl_vat,
            due_date=record.due_date,
            payment_reference=issued.payment_reference,
            bank_account=issued.seller_bank_account,
            ehf_sent=ehf_sent,
            pdf_bytes=pdf_bytes,
            pdf_filename=faktura_pdf.filename_for(issued),
            portal_url=f"{base_url}/ki/about/#planer",
        )
        result = await mailer.send(mail)
        if result.sent:
            if not ehf_sent:
                record.delivery_method = KiInvoiceDeliveryMethod.EMAIL.value
                record.delivery_status = KiInvoiceDeliveryStatus.SENT.value
                record.delivered_at = utc_now()
        elif not ehf_sent:
            delivery_error = result.error or "email_delivery_failed"

    if (
        not ehf_sent
        and record.delivery_status == KiInvoiceDeliveryStatus.PENDING.value
        and delivery_error is not None
    ):
        record.delivery_status = KiInvoiceDeliveryStatus.FAILED.value
        record.error_code = delivery_error[:64]


async def list_invoices(db: AsyncSession, company_id: int) -> list[KiBillingInvoice]:
    """Monthly KI invoices for ``company_id``, newest first."""
    rows = await db.scalars(
        select(KiBillingInvoice)
        .where(KiBillingInvoice.company_id == company_id)
        .order_by(KiBillingInvoice.issued_on.desc(), KiBillingInvoice.id.desc())
    )
    return list(rows)


async def get_invoice_pdf(
    db: AsyncSession, *, company_id: int, invoice_id: int
) -> tuple[bytes, str]:
    """Render the PDF for one KI invoice belonging to ``company_id``."""
    record: KiBillingInvoice | None = await db.scalar(
        select(KiBillingInvoice).where(
            KiBillingInvoice.id == invoice_id,
            KiBillingInvoice.company_id == company_id,
        )
    )
    if record is None or record.seller_document_id is None:
        raise KiInvoiceNotFoundError("finner ikke fakturaen")

    document = await db.scalar(
        select(SalesDocument)
        .options(selectinload(SalesDocument.lines), selectinload(SalesDocument.party))
        .where(SalesDocument.id == record.seller_document_id)
    )
    if document is None:
        raise KiInvoiceNotFoundError("finner ikke fakturaen")

    return faktura_pdf.render(document), faktura_pdf.filename_for(document)


async def process_due_subscriptions(
    sessions: Sessions,
    storage: DocumentStorage,
    mailer: Mailer,
    *,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Advance subscriptions whose billing month has ended and issue monthly invoices.

    Returns ``(renewed_count, ended_count)``. Each subscription is processed in
    its own transaction so one failure never stops other renewals.
    """
    moment = as_utc(now or utc_now())
    async with sessions() as db:
        candidates = list(
            (
                await db.scalars(
                    select(KiSubscription.id).where(
                        KiSubscription.status.in_(
                            (
                                KiSubscriptionStatus.ACTIVE.value,
                                KiSubscriptionStatus.CANCELING.value,
                            )
                        )
                    )
                )
            ).all()
        )

    renewed = 0
    ended = 0
    for sub_id in candidates:
        async with sessions() as db:
            sub = await db.get(KiSubscription, sub_id)
            if sub is None:
                continue
            if as_utc(sub.current_period_end) > moment:
                continue

            buyer = await db.get(Company, sub.company_id)
            if (
                buyer is None
                or buyer.closed_at is not None
                or not buyer.ki_enabled
                or sub.cancel_at_period_end
                or sub.status == KiSubscriptionStatus.CANCELING.value
            ):
                sub.status = KiSubscriptionStatus.CANCELED.value
                sub.canceled_at = sub.canceled_at or moment
                sub.updated_at = moment
                await db.commit()
                ended += 1
                continue

            if not is_billing_configured():
                continue

            try:
                plan = ki_plans.by_code(sub.plan)
                if plan is not None:
                    sub.weekly_value_nok = plan.weekly_value_nok
                    sub.price_excl_vat = plan.price_excl_vat
                    sub.vat_amount = plan.vat
                    sub.price_incl_vat = plan.price_incl_vat
                new_start = as_utc(sub.current_period_end)
                new_end = ki_service.add_month(new_start)
                sub.current_period_start = new_start
                sub.current_period_end = new_end
                sub.updated_at = moment
                await db.flush()

                await issue_subscription_invoice(
                    db,
                    storage,
                    mailer,
                    buyer=buyer,
                    subscription=sub,
                    now=moment,
                )
                await db.commit()
                renewed += 1
            except Exception:
                await db.rollback()
                logger.exception("Could not renew KI subscription %d", sub_id)

    return renewed, ended


async def sweep_regularly(
    sessions: Callable[[], Sessions],
    storage: Callable[[], DocumentStorage],
    mailer: Mailer,
) -> None:
    """Background loop checking for KI subscription renewals due for invoicing."""
    interval = get_settings().ki_billing_interval_seconds
    if interval <= 0:
        return
    await asyncio.sleep(_FIRST_SWEEP_DELAY)
    while True:
        try:
            if is_billing_configured():
                renewed, ended = await process_due_subscriptions(sessions(), storage(), mailer)
                if renewed or ended:
                    logger.info(
                        "KI billing sweep processed %d renewed and %d ended subscriptions",
                        renewed,
                        ended,
                    )
        except Exception:
            logger.exception("KI billing sweep failed")
        await asyncio.sleep(interval)
