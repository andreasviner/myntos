"""Database service for the shareholder register (aksjeeierbok) and RF-1086 filings.

Every lookup filters on ``company.id`` (docs/company-isolation.md). Nothing here
commits: the API route owns the transaction boundary, except where partial
remote failure records a ``feilet`` filing row before returning 502.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from myntos.aksjonaer.codes import (
    EventLineRole,
    EventType,
    ShareholderKind,
    validate_country_code,
    validate_foreign_id,
    validate_isin,
    validate_national_id,
    validate_share_class_code,
    validate_shareholder_orgnr,
)
from myntos.aksjonaer.errors import (
    AksjonaerDocumentNotFoundError,
    AksjonaerFilingNotFoundError,
    DuplicateShareClassError,
    InvalidAksjonaerDataError,
    ShareClassInUseError,
    ShareClassNotFoundError,
    ShareEventNotFoundError,
    ShareholderInUseError,
    ShareholderNotFoundError,
)
from myntos.aksjonaer.models import (
    AksjonaerFiling,
    AksjonaerFilingDocument,
    ShareClass,
    ShareEvent,
    ShareEventLine,
    Shareholder,
    ShareOpeningHolding,
)
from myntos.aksjonaer.xmlwriter import ParsedPrefill
from myntos.core.clock import utc_now
from myntos.domain.ledger.models import Account, Company, Journal, Transaction, TransactionLine
from myntos.storage.base import DocumentStorage

# Account 2000 (Aksjekapital) in Norwegian NS 4102 charts.
_SHARE_CAPITAL_ACCOUNT = "2000"


@dataclass(frozen=True)
class EventLineInput:
    shareholder_id: int
    role: str = EventLineRole.MOTTAKER.value
    shares: int = 0
    amount: Decimal | None = None
    paid_in_premium: Decimal | None = None
    withholding_tax: Decimal | None = None
    withholding_tax_rate: int | None = None
    withholding_tax_country: str | None = None
    counterparty_national_id: str | None = None
    counterparty_orgnr: str | None = None


async def list_share_classes(db: AsyncSession, company: Company) -> list[ShareClass]:
    rows = await db.scalars(
        select(ShareClass)
        .where(ShareClass.company_id == company.id)
        .options(
            selectinload(ShareClass.opening_holdings),
            selectinload(ShareClass.events).selectinload(ShareEvent.lines),
        )
        .order_by(ShareClass.code, ShareClass.id)
    )
    return list(rows)


async def get_share_class(db: AsyncSession, company: Company, share_class_id: int) -> ShareClass:
    row = await db.scalar(
        select(ShareClass)
        .where(ShareClass.id == share_class_id, ShareClass.company_id == company.id)
        .options(
            selectinload(ShareClass.opening_holdings),
            selectinload(ShareClass.events).selectinload(ShareEvent.lines),
        )
    )
    if row is None:
        raise ShareClassNotFoundError("finner ikke aksjeklassen")
    return row


async def list_shareholders(db: AsyncSession, company: Company) -> list[Shareholder]:
    rows = await db.scalars(
        select(Shareholder)
        .where(Shareholder.company_id == company.id)
        .order_by(Shareholder.name, Shareholder.id)
    )
    return list(rows)


async def get_shareholder(db: AsyncSession, company: Company, shareholder_id: int) -> Shareholder:
    row = await db.scalar(
        select(Shareholder).where(
            Shareholder.id == shareholder_id, Shareholder.company_id == company.id
        )
    )
    if row is None:
        raise ShareholderNotFoundError("finner ikke aksjonæren")
    return row


async def get_event(db: AsyncSession, company: Company, event_id: int) -> ShareEvent:
    row = await db.scalar(
        select(ShareEvent)
        .where(ShareEvent.id == event_id, ShareEvent.company_id == company.id)
        .options(selectinload(ShareEvent.lines))
    )
    if row is None:
        raise ShareEventNotFoundError("finner ikke aksjehendelsen")
    return row


async def create_share_class(
    db: AsyncSession,
    *,
    company: Company,
    code: str,
    name: str,
    isin: str | None = None,
    opening_year: int | None = None,
    opening_shares: int = 0,
    opening_par_value: Decimal = Decimal("0"),
    opening_share_capital: Decimal = Decimal("0.00"),
    opening_paid_in_capital: Decimal = Decimal("0.00"),
    opening_paid_in_premium: Decimal = Decimal("0.00"),
) -> ShareClass:
    valid_code = validate_share_class_code(code)
    valid_isin = validate_isin(isin)
    existing = await db.scalar(
        select(ShareClass.id).where(
            ShareClass.company_id == company.id, ShareClass.code == valid_code
        )
    )
    if existing is not None:
        raise DuplicateShareClassError(f"aksjeklasse {valid_code} finnes allerede i selskapet")
    if opening_shares < 0 or opening_par_value < 0 or opening_share_capital < 0:
        raise InvalidAksjonaerDataError(
            "inngående aksjer, pålydende og aksjekapital kan ikke være negative"
        )
    sc = ShareClass(
        company_id=company.id,
        code=valid_code,
        name=name.strip() or f"Aksjeklasse {valid_code}",
        isin=valid_isin,
        opening_year=opening_year,
        opening_shares=opening_shares,
        opening_par_value=opening_par_value,
        opening_share_capital=opening_share_capital,
        opening_paid_in_capital=opening_paid_in_capital,
        opening_paid_in_premium=opening_paid_in_premium,
    )
    db.add(sc)
    await db.flush()
    return sc


async def update_share_class(
    db: AsyncSession,
    *,
    company: Company,
    share_class_id: int,
    name: str | None = None,
    isin: str | None = None,
    clear_isin: bool = False,
    opening_year: int | None = None,
    clear_opening_year: bool = False,
    opening_shares: int | None = None,
    opening_par_value: Decimal | None = None,
    opening_share_capital: Decimal | None = None,
    opening_paid_in_capital: Decimal | None = None,
    opening_paid_in_premium: Decimal | None = None,
) -> ShareClass:
    sc = await get_share_class(db, company, share_class_id)
    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            raise InvalidAksjonaerDataError("navnet på aksjeklassen kan ikke være tomt")
        sc.name = cleaned
    if clear_isin:
        sc.isin = None
    elif isin is not None:
        sc.isin = validate_isin(isin)
    if clear_opening_year:
        sc.opening_year = None
    elif opening_year is not None:
        sc.opening_year = opening_year
    if opening_shares is not None:
        if opening_shares < 0:
            raise InvalidAksjonaerDataError("inngående antall aksjer kan ikke være negativt")
        sc.opening_shares = opening_shares
    if opening_par_value is not None:
        if opening_par_value < 0:
            raise InvalidAksjonaerDataError("pålydende kan ikke være negativt")
        sc.opening_par_value = opening_par_value
    if opening_share_capital is not None:
        if opening_share_capital < 0:
            raise InvalidAksjonaerDataError("inngående aksjekapital kan ikke være negativ")
        sc.opening_share_capital = opening_share_capital
    if opening_paid_in_capital is not None:
        sc.opening_paid_in_capital = opening_paid_in_capital
    if opening_paid_in_premium is not None:
        if opening_paid_in_premium < 0:
            raise InvalidAksjonaerDataError("inngående overkurs kan ikke være negativ")
        sc.opening_paid_in_premium = opening_paid_in_premium
    await db.flush()
    return sc


async def delete_share_class(db: AsyncSession, *, company: Company, share_class_id: int) -> None:
    sc = await get_share_class(db, company, share_class_id)
    if sc.events:
        raise ShareClassInUseError(
            "aksjeklassen har registrerte hendelser og kan ikke slettes før disse er fjernet"
        )
    has_filing = await db.scalar(
        select(AksjonaerFiling.id).where(
            AksjonaerFiling.company_id == company.id,
            AksjonaerFiling.share_class_id == sc.id,
        )
    )
    if has_filing is not None:
        raise ShareClassInUseError(
            "det er allerede sendt aksjonærregisteroppgave for denne aksjeklassen"
        )
    await db.delete(sc)
    await db.flush()


def _validate_shareholder_fields(
    *,
    kind: str,
    name: str,
    national_id: str | None,
    orgnr: str | None,
    foreign_id: str | None,
    country_code: str,
) -> tuple[str, str, str | None, str | None, str | None, str]:
    try:
        valid_kind = ShareholderKind(kind).value
    except ValueError as err:
        raise InvalidAksjonaerDataError(
            "aksjonærtype må være «person», «selskap» eller «utenlandsk»"
        ) from err
    cleaned_name = name.strip()
    if not cleaned_name:
        raise InvalidAksjonaerDataError("navn på aksjonæren må fylles ut")
    valid_country = validate_country_code(country_code or "NO")

    clean_fnr: str | None = None
    clean_org: str | None = None
    clean_utl: str | None = None
    if valid_kind == ShareholderKind.PERSON.value:
        if national_id and national_id.strip():
            clean_fnr = validate_national_id(national_id)
    elif valid_kind == ShareholderKind.SELSKAP.value:
        if orgnr and orgnr.strip():
            clean_org = validate_shareholder_orgnr(orgnr)
    else:
        if foreign_id and foreign_id.strip():
            clean_utl = validate_foreign_id(foreign_id)
    return valid_kind, cleaned_name, clean_fnr, clean_org, clean_utl, valid_country


async def set_opening_holding(
    db: AsyncSession,
    *,
    company: Company,
    share_class_id: int,
    shareholder_id: int,
    shares: int,
) -> None:
    if shares < 0:
        raise InvalidAksjonaerDataError("inngående antall aksjer kan ikke være negativt")
    # Verify both belong to this company.
    await get_share_class(db, company, share_class_id)
    await get_shareholder(db, company, shareholder_id)
    existing = await db.scalar(
        select(ShareOpeningHolding).where(
            ShareOpeningHolding.company_id == company.id,
            ShareOpeningHolding.share_class_id == share_class_id,
            ShareOpeningHolding.shareholder_id == shareholder_id,
        )
    )
    if shares == 0:
        if existing is not None:
            await db.delete(existing)
    elif existing is None:
        db.add(
            ShareOpeningHolding(
                company_id=company.id,
                share_class_id=share_class_id,
                shareholder_id=shareholder_id,
                shares=shares,
            )
        )
    else:
        existing.shares = shares
    await db.flush()


async def create_shareholder(
    db: AsyncSession,
    *,
    company: Company,
    kind: str,
    name: str,
    national_id: str | None = None,
    orgnr: str | None = None,
    foreign_id: str | None = None,
    address: str | None = None,
    postal_code: str | None = None,
    city: str | None = None,
    country_code: str = "NO",
    email: str | None = None,
    is_own_shares: bool = False,
    opening_holdings: dict[int, int] | None = None,
) -> Shareholder:
    valid_kind, cleaned_name, clean_fnr, clean_org, clean_utl, valid_country = (
        _validate_shareholder_fields(
            kind=kind,
            name=name,
            national_id=national_id,
            orgnr=orgnr,
            foreign_id=foreign_id,
            country_code=country_code,
        )
    )
    holder = Shareholder(
        company_id=company.id,
        kind=valid_kind,
        name=cleaned_name,
        national_id=clean_fnr,
        orgnr=clean_org,
        foreign_id=clean_utl,
        address=(address or "").strip() or None,
        postal_code=(postal_code or "").strip() or None,
        city=(city or "").strip() or None,
        country_code=valid_country,
        email=(email or "").strip() or None,
        is_own_shares=is_own_shares,
    )
    db.add(holder)
    await db.flush()

    if opening_holdings:
        for sc_id, qty in opening_holdings.items():
            await set_opening_holding(
                db, company=company, share_class_id=sc_id, shareholder_id=holder.id, shares=qty
            )
    return holder


async def update_shareholder(
    db: AsyncSession,
    *,
    company: Company,
    shareholder_id: int,
    kind: str | None = None,
    name: str | None = None,
    national_id: str | None = None,
    orgnr: str | None = None,
    foreign_id: str | None = None,
    address: str | None = None,
    postal_code: str | None = None,
    city: str | None = None,
    country_code: str | None = None,
    email: str | None = None,
    is_own_shares: bool | None = None,
    opening_holdings: dict[int, int] | None = None,
) -> Shareholder:
    holder = await get_shareholder(db, company, shareholder_id)
    target_kind = kind if kind is not None else holder.kind
    target_name = name if name is not None else holder.name
    target_fnr = national_id if national_id is not None else holder.national_id
    target_org = orgnr if orgnr is not None else holder.orgnr
    target_utl = foreign_id if foreign_id is not None else holder.foreign_id
    target_country = country_code if country_code is not None else holder.country_code

    valid_kind, cleaned_name, clean_fnr, clean_org, clean_utl, valid_country = (
        _validate_shareholder_fields(
            kind=target_kind,
            name=target_name,
            national_id=target_fnr,
            orgnr=target_org,
            foreign_id=target_utl,
            country_code=target_country,
        )
    )
    holder.kind = valid_kind
    holder.name = cleaned_name
    holder.national_id = clean_fnr
    holder.orgnr = clean_org
    holder.foreign_id = clean_utl
    holder.country_code = valid_country
    if address is not None:
        holder.address = address.strip() or None
    if postal_code is not None:
        holder.postal_code = postal_code.strip() or None
    if city is not None:
        holder.city = city.strip() or None
    if email is not None:
        holder.email = email.strip() or None
    if is_own_shares is not None:
        holder.is_own_shares = is_own_shares
    await db.flush()

    if opening_holdings:
        for sc_id, qty in opening_holdings.items():
            await set_opening_holding(
                db, company=company, share_class_id=sc_id, shareholder_id=holder.id, shares=qty
            )
    return holder


async def delete_shareholder(db: AsyncSession, *, company: Company, shareholder_id: int) -> None:
    holder = await get_shareholder(db, company, shareholder_id)
    has_lines = await db.scalar(
        select(ShareEventLine.id).where(
            ShareEventLine.company_id == company.id,
            ShareEventLine.shareholder_id == holder.id,
        )
    )
    if has_lines is not None:
        raise ShareholderInUseError(
            "aksjonæren er knyttet til hendelser og kan ikke slettes før hendelsene er fjernet"
        )
    await db.execute(
        delete(ShareOpeningHolding).where(
            ShareOpeningHolding.company_id == company.id,
            ShareOpeningHolding.shareholder_id == holder.id,
        )
    )
    await db.delete(holder)
    await db.flush()


async def create_event(
    db: AsyncSession,
    *,
    company: Company,
    share_class_id: int,
    event_type: str,
    event_date: date,
    subtype: str | None = None,
    description: str | None = None,
    total_shares: int | None = None,
    par_value_after: Decimal | None = None,
    total_amount: Decimal | None = None,
    premium_amount: Decimal | None = None,
    amount_per_share: Decimal | None = None,
    lines: list[EventLineInput],
    created_by_user_id: int | None = None,
) -> ShareEvent:
    try:
        valid_type = EventType(event_type).value
    except ValueError as err:
        raise InvalidAksjonaerDataError("ukjent hendelsestype for aksjeeierboken") from err
    sc = await get_share_class(db, company, share_class_id)

    # Verify every referenced shareholder belongs to this company.
    for item in lines:
        await get_shareholder(db, company, item.shareholder_id)
        try:
            EventLineRole(item.role)
        except ValueError as err:
            raise InvalidAksjonaerDataError(
                "rolle på hendelseslinje må være mottaker eller avgiver"
            ) from err
        if item.shares < 0:
            raise InvalidAksjonaerDataError("antall aksjer på en linje kan ikke være negativt")
        if item.withholding_tax_country:
            validate_country_code(item.withholding_tax_country)
        if item.withholding_tax_rate is not None and not (0 <= item.withholding_tax_rate <= 100):
            raise InvalidAksjonaerDataError("kildeskatt-prosent må være mellom 0 og 100")
        if item.counterparty_national_id:
            validate_national_id(item.counterparty_national_id)
        if item.counterparty_orgnr:
            validate_shareholder_orgnr(item.counterparty_orgnr)

    event = ShareEvent(
        company_id=company.id,
        share_class_id=sc.id,
        event_type=valid_type,
        subtype=(subtype or "").strip() or None,
        event_date=event_date,
        description=(description or "").strip() or None,
        total_shares=total_shares,
        par_value_after=par_value_after,
        total_amount=total_amount,
        premium_amount=premium_amount,
        amount_per_share=amount_per_share,
        created_by_user_id=created_by_user_id,
    )
    db.add(event)
    await db.flush()

    for item in lines:
        db.add(
            ShareEventLine(
                company_id=company.id,
                event_id=event.id,
                shareholder_id=item.shareholder_id,
                role=item.role,
                shares=item.shares,
                amount=item.amount,
                paid_in_premium=item.paid_in_premium,
                withholding_tax=item.withholding_tax,
                withholding_tax_rate=item.withholding_tax_rate,
                withholding_tax_country=(item.withholding_tax_country or "").strip().upper()
                or None,
                counterparty_national_id=(
                    validate_national_id(item.counterparty_national_id)
                    if item.counterparty_national_id
                    else None
                ),
                counterparty_orgnr=(
                    validate_shareholder_orgnr(item.counterparty_orgnr)
                    if item.counterparty_orgnr
                    else None
                ),
            )
        )
    await db.flush()
    return await get_event(db, company, event.id)


async def delete_event(db: AsyncSession, *, company: Company, event_id: int) -> None:
    event = await get_event(db, company, event_id)
    await db.delete(event)
    await db.flush()


async def import_prefill(
    db: AsyncSession, *, company: Company, parsed: ParsedPrefill
) -> tuple[int, int]:
    """Upsert share classes, shareholders, and opening holdings from Skatteetaten prefill.

    Returns ``(classes_imported, shareholders_imported)``.
    """
    existing_classes = {sc.code: sc for sc in await list_share_classes(db, company)}
    class_count = 0
    for psc in parsed.share_classes:
        sc = existing_classes.get(psc.code)
        if sc is None:
            sc = ShareClass(
                company_id=company.id,
                code=psc.code,
                name=psc.name,
                isin=psc.isin,
                opening_year=psc.opening_year,
                opening_shares=psc.opening_shares,
                opening_par_value=psc.opening_par_value,
                opening_share_capital=psc.opening_share_capital,
                opening_paid_in_capital=psc.opening_paid_in_capital,
                opening_paid_in_premium=psc.opening_paid_in_premium,
            )
            db.add(sc)
            await db.flush()
            existing_classes[psc.code] = sc
            class_count += 1
        else:
            sc.opening_year = psc.opening_year
            sc.opening_shares = psc.opening_shares
            sc.opening_par_value = psc.opening_par_value
            sc.opening_share_capital = psc.opening_share_capital
            sc.opening_paid_in_capital = psc.opening_paid_in_capital
            sc.opening_paid_in_premium = psc.opening_paid_in_premium
            if psc.isin:
                sc.isin = psc.isin
            class_count += 1

    existing_holders = await list_shareholders(db, company)
    by_fnr = {h.national_id: h for h in existing_holders if h.national_id}
    by_org = {h.orgnr: h for h in existing_holders if h.orgnr}
    by_utl = {h.foreign_id: h for h in existing_holders if h.foreign_id}

    holder_count = 0
    for ph in parsed.shareholders:
        holder: Shareholder | None = None
        if ph.national_id and ph.national_id in by_fnr:
            holder = by_fnr[ph.national_id]
        elif ph.orgnr and ph.orgnr in by_org:
            holder = by_org[ph.orgnr]
        elif ph.foreign_id and ph.foreign_id in by_utl:
            holder = by_utl[ph.foreign_id]

        if holder is None:
            holder = Shareholder(
                company_id=company.id,
                kind=ph.kind,
                name=ph.name,
                national_id=ph.national_id,
                orgnr=ph.orgnr,
                foreign_id=ph.foreign_id,
                address=ph.address,
                postal_code=ph.postal_code,
                city=ph.city,
                country_code=ph.country_code,
            )
            db.add(holder)
            await db.flush()
            if holder.national_id:
                by_fnr[holder.national_id] = holder
            if holder.orgnr:
                by_org[holder.orgnr] = holder
            if holder.foreign_id:
                by_utl[holder.foreign_id] = holder
        holder_count += 1

        sc = existing_classes.get(ph.share_class_code)
        if sc is not None:
            await set_opening_holding(
                db,
                company=company,
                share_class_id=sc.id,
                shareholder_id=holder.id,
                shares=ph.opening_shares,
            )

    await db.flush()
    return class_count, holder_count


async def ledger_share_capital_for_year(
    db: AsyncSession, company: Company, year: int
) -> Decimal | None:
    """Closing credit balance of account 2000 (Aksjekapital) in ``year``, or None if empty."""
    row = (
        await db.execute(
            select(
                func.count(TransactionLine.id),
                func.coalesce(func.sum(TransactionLine.credit - TransactionLine.debit), 0),
            )
            .join(Account, Account.id == TransactionLine.account_id)
            .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
            .join(Journal, Journal.id == Transaction.journal_id)
            .where(
                Journal.company_id == company.id,
                Account.company_id == company.id,
                Account.account_number == _SHARE_CAPITAL_ACCOUNT,
                Transaction.posted_at.is_not(None),
                Transaction.transaction_date <= date(year, 12, 31),
            )
        )
    ).one()
    count, balance = int(row[0] or 0), Decimal(str(row[1] or 0))
    if count == 0:
        return None
    return balance.quantize(Decimal("0.01"))


async def list_filings(
    db: AsyncSession, company: Company, *, year: int | None = None
) -> list[AksjonaerFiling]:
    stmt = (
        select(AksjonaerFiling)
        .where(AksjonaerFiling.company_id == company.id)
        .options(selectinload(AksjonaerFiling.documents))
        .order_by(AksjonaerFiling.submitted_at.desc(), AksjonaerFiling.id.desc())
    )
    if year is not None:
        stmt = stmt.where(AksjonaerFiling.year == year)
    return list(await db.scalars(stmt))


async def get_filing(db: AsyncSession, company: Company, filing_id: int) -> AksjonaerFiling:
    row = await db.scalar(
        select(AksjonaerFiling)
        .where(AksjonaerFiling.id == filing_id, AksjonaerFiling.company_id == company.id)
        .options(selectinload(AksjonaerFiling.documents))
    )
    if row is None:
        raise AksjonaerFilingNotFoundError("finner ikke innsendingen av aksjonærregisteroppgaven")
    return row


async def create_filing(
    db: AsyncSession,
    *,
    company: Company,
    share_class_id: int,
    share_class_code: str,
    year: int,
    status: str,
    hovedskjema_xml: str,
    underskjema_count: int,
    content_hash: str,
    idempotency_key: str,
    contact_email: str | None,
    contact_name: str | None,
    findings: list[dict[str, Any]],
    submitted_by_user_id: int | None,
) -> AksjonaerFiling:
    filing = AksjonaerFiling(
        company_id=company.id,
        share_class_id=share_class_id,
        share_class_code=share_class_code,
        year=year,
        status=status,
        hovedskjema_xml=hovedskjema_xml,
        underskjema_count=underskjema_count,
        content_hash=content_hash,
        idempotency_key=idempotency_key,
        contact_email=contact_email,
        contact_name=contact_name,
        findings=findings,
        submitted_by_user_id=submitted_by_user_id,
        submitted_at=utc_now(),
    )
    db.add(filing)
    await db.flush()
    return await get_filing(db, company, filing.id)


async def store_filing_document(
    db: AsyncSession,
    *,
    company: Company,
    filing: AksjonaerFiling,
    storage: DocumentStorage,
    document_type: str,
    filename: str,
    content_type: str,
    content: bytes,
    external_document_id: str | None = None,
) -> AksjonaerFilingDocument:
    """Archive one submitted XML or Skatteetaten document into append-only storage."""
    key = await storage.save(content=content, filename=filename, content_type=content_type)
    doc = AksjonaerFilingDocument(
        filing=filing,
        document_type=document_type,
        external_document_id=external_document_id,
        filename=filename,
        content_type=content_type,
        byte_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        storage_key=key,
        fetched_at=utc_now(),
    )
    db.add(doc)
    await db.flush()
    return doc


async def get_filing_document(
    db: AsyncSession,
    *,
    company: Company,
    filing_id: int,
    document_type: str,
) -> AksjonaerFilingDocument:
    filing = await get_filing(db, company, filing_id)
    for doc in filing.documents:
        if doc.document_type == document_type:
            return doc
    raise AksjonaerDocumentNotFoundError("finner ikke dokumentet på denne innsendingen")
