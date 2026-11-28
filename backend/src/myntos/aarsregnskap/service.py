"""Database persistence and computation loader for årsregnskap (RR-0002).

Every query is scoped to ``company.id`` (docs/company-isolation.md).
Newly persisted or fetched :class:`AarsregnskapFiling` rows eagerly load
``documents`` via ``selectinload`` so serialization never hits lazy-loading
``MissingGreenlet`` errors in async sessions.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from myntos.aarsregnskap.compute import (
    ALL_OVERRIDE_KEYS,
    ComputedAarsregnskap,
    DraftInput,
    FixedAssetNoteSummary,
    LedgerAccountBalance,
    ShareholderSummary,
    compute_aarsregnskap,
    round_kr,
)
from myntos.aarsregnskap.errors import (
    AarsregnskapDocumentNotFoundError,
    AarsregnskapFilingNotFoundError,
    InvalidAarsregnskapDataError,
)
from myntos.aarsregnskap.models import (
    AarsregnskapDraft,
    AarsregnskapFiling,
    AarsregnskapFilingDocument,
)
from myntos.aksjonaer import compute as aksjonaer_compute
from myntos.aksjonaer import service as aksjonaer_service
from myntos.core.clock import utc_now
from myntos.domain.anlegg.models import FixedAsset, FixedAssetDepreciation, FixedAssetOpening
from myntos.domain.anlegg.saldo import standing_transaction_filter
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.models import (
    Account,
    Company,
    FinancialYear,
    Journal,
    Transaction,
    TransactionLine,
)
from myntos.storage.base import DocumentStorage


async def get_draft(db: AsyncSession, company: Company, year: int) -> AarsregnskapDraft | None:
    row: AarsregnskapDraft | None = await db.scalar(
        select(AarsregnskapDraft).where(
            AarsregnskapDraft.company_id == company.id,
            AarsregnskapDraft.year == year,
        )
    )
    return row


async def upsert_draft(
    db: AsyncSession,
    company: Company,
    year: int,
    *,
    user_id: int | None,
    payload: dict[str, Any],
) -> AarsregnskapDraft:
    """Create or update the company's årsregnskap draft for ``year``."""
    if year < 1980 or year > 2100:
        raise InvalidAarsregnskapDataError("ugyldig regnskapsår")

    row = await get_draft(db, company, year)
    if row is None:
        row = AarsregnskapDraft(company_id=company.id, year=year)
        db.add(row)

    bool_fields = (
        "small_company",
        "is_parent",
        "consolidated",
        "winding_up_accounts",
        "auditor_elected_out",
        "prepared_by_external_accountant",
        "external_accountant_assistance",
    )
    for field in bool_fields:
        if field in payload and payload[field] is not None:
            setattr(row, field, bool(payload[field]))

    str_fields = (
        ("confirming_representative", 70),
        ("contact_email", 70),
        ("board_place", 120),
        ("general_meeting_place", 120),
        ("general_meeting_chair", 120),
        ("general_meeting_cosigner", 120),
    )
    for field, limit in str_fields:
        if field in payload:
            raw = payload[field]
            cleaned = str(raw).strip()[:limit] if raw is not None else None
            setattr(row, field, cleaned or None)

    date_fields = ("approval_date", "board_meeting_date", "general_meeting_date")
    for field in date_fields:
        if field in payload:
            val = payload[field]
            if val is None or val == "":
                setattr(row, field, None)
            elif isinstance(val, date):
                setattr(row, field, val)
            else:
                try:
                    setattr(row, field, date.fromisoformat(str(val)))
                except ValueError as err:
                    raise InvalidAarsregnskapDataError(f"ugyldig dato for {field}") from err

    if "dividend_proposed" in payload and payload["dividend_proposed"] is not None:
        div = Decimal(str(payload["dividend_proposed"]))
        if div < Decimal("0"):
            raise InvalidAarsregnskapDataError("foreslått utbytte kan ikke være negativt")
        row.dividend_proposed = div.quantize(Decimal("0.01"))

    if "board_members" in payload and payload["board_members"] is not None:
        members: list[dict[str, Any]] = []
        for entry in payload["board_members"][:20]:
            if isinstance(entry, dict):
                name = str(entry.get("name") or "").strip()[:120]
                role = str(entry.get("role") or "Styremedlem").strip()[:60]
                if name:
                    members.append({"name": name, "role": role})
        row.board_members = members

    if "prior_year_overrides" in payload and payload["prior_year_overrides"] is not None:
        cleaned_overrides: dict[str, int] = {}
        for key, raw_val in payload["prior_year_overrides"].items():
            if key not in ALL_OVERRIDE_KEYS:
                raise InvalidAarsregnskapDataError(f"ukjent linjekode i sammenligningstall: {key}")
            if raw_val is None or raw_val == "":
                continue
            try:
                cleaned_overrides[key] = int(raw_val)
            except (TypeError, ValueError) as err:
                raise InvalidAarsregnskapDataError(
                    f"ugyldig heltall for sammenligningstall {key}"
                ) from err
        row.prior_year_overrides = cleaned_overrides

    if "notes" in payload and payload["notes"] is not None:
        if not isinstance(payload["notes"], dict):
            raise InvalidAarsregnskapDataError("noter må være et objekt")
        merged_notes = dict(row.notes or {})
        merged_notes.update(payload["notes"])
        row.notes = merged_notes

    row.updated_by_user_id = user_id
    row.updated_at = utc_now()
    await db.flush()
    return row


async def _ledger_balances(
    db: AsyncSession,
    company: Company,
    *,
    year: int | None = None,
    up_to: int | None = None,
) -> list[LedgerAccountBalance]:
    query = (
        select(
            Account.account_number,
            Account.description,
            Account.account_type,
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .join(TransactionLine, TransactionLine.account_id == Account.id)
        .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
        .join(Journal, Journal.id == Transaction.journal_id)
        .where(Journal.company_id == company.id, Transaction.posted_at.is_not(None))
        .group_by(Account.id, Account.account_number, Account.description, Account.account_type)
    )
    if year is not None:
        query = query.where(Transaction.financial_year == year)
    if up_to is not None:
        query = query.where(Transaction.financial_year <= up_to)

    rows = await db.execute(query)
    out: list[LedgerAccountBalance] = []
    for number, desc, acc_type, debit, credit in rows:
        net = Decimal(str(debit)) - Decimal(str(credit))
        if net != Decimal("0"):
            out.append(
                LedgerAccountBalance(
                    account_number=number,
                    description=desc,
                    account_type=acc_type,
                    net=net,
                )
            )
    return out


async def _load_shareholder_summary(
    db: AsyncSession, company: Company, year: int
) -> tuple[list[ShareholderSummary], int | None, Decimal | None]:
    classes = await aksjonaer_service.list_share_classes(db, company)
    if not classes:
        return [], None, None
    holders = await aksjonaer_service.list_shareholders(db, company)
    computed = aksjonaer_compute.compute_year(
        company=company,
        year=year,
        share_classes=classes,
        shareholders=holders,
        ledger_share_capital=None,
    )
    summaries: list[ShareholderSummary] = []
    total_shares = 0
    par_value: Decimal | None = None
    for cls in computed.classes:
        total_shares += cls.closing.shares
        if par_value is None and cls.closing.par_value > Decimal("0"):
            par_value = cls.closing.par_value
        for sh in cls.shareholders:
            if sh.closing_shares > 0:
                summaries.append(
                    ShareholderSummary(
                        name=sh.shareholder.name,
                        shares=sh.closing_shares,
                        ownership_percent=sh.ownership_percent,
                        share_class=cls.share_class.name,
                    )
                )
    summaries.sort(key=lambda s: (-s.shares, s.name))
    return summaries, (total_shares if total_shares > 0 else None), par_value


async def _load_fixed_asset_summary(
    db: AsyncSession, company: Company, year: int
) -> FixedAssetNoteSummary:
    assets = list(await db.scalars(select(FixedAsset).where(FixedAsset.company_id == company.id)))
    if not assets:
        return FixedAssetNoteSummary()

    openings = list(
        await db.scalars(
            select(FixedAssetOpening).where(
                FixedAssetOpening.company_id == company.id,
                FixedAssetOpening.year <= year,
            )
        )
    )
    depreciations = list(
        await db.scalars(
            select(FixedAssetDepreciation)
            .join(Transaction, Transaction.id == FixedAssetDepreciation.transaction_pk)
            .where(
                FixedAssetDepreciation.company_id == company.id,
                FixedAssetDepreciation.year <= year,
                FixedAssetDepreciation.kind == "avskrivning",
                *standing_transaction_filter(),
            )
        )
    )

    def _is_intangible(acc_no: str) -> bool:
        return acc_no.startswith("10")

    t_open = Decimal("0")
    t_add = Decimal("0")
    t_disp = Decimal("0")
    i_open = Decimal("0")
    i_add = Decimal("0")
    i_disp = Decimal("0")

    for op in openings:
        if op.account_number is None:
            continue
        if _is_intangible(op.account_number):
            i_open += op.amount
        else:
            t_open += op.amount

    for asset in assets:
        cost = asset.cost - (asset.grant if asset.grant_reduces_cost else Decimal("0"))
        if asset.opening_year is not None and asset.opening_value is not None:
            cost = asset.opening_value
        intangible = _is_intangible(asset.account_number)
        if asset.acquired_on.year < year:
            if asset.disposed_on is None or asset.disposed_on.year >= year:
                if intangible:
                    i_open += cost
                else:
                    t_open += cost
        elif asset.acquired_on.year == year:
            if intangible:
                i_add += cost
            else:
                t_add += cost
        if asset.disposed_on is not None and asset.disposed_on.year == year:
            if intangible:
                i_disp += cost
            else:
                t_disp += cost

    t_dep_year = Decimal("0")
    t_dep_acc = Decimal("0")
    i_dep_year = Decimal("0")
    i_dep_acc = Decimal("0")
    for dep in depreciations:
        if _is_intangible(dep.account_number):
            i_dep_acc += dep.amount
            if dep.year == year:
                i_dep_year += dep.amount
        else:
            t_dep_acc += dep.amount
            if dep.year == year:
                t_dep_year += dep.amount

    t_close = max(Decimal("0"), t_open + t_add - t_disp)
    i_close = max(Decimal("0"), i_open + i_add - i_disp)
    return FixedAssetNoteSummary(
        tangible_opening_cost=round_kr(t_open),
        tangible_additions=round_kr(t_add),
        tangible_disposals=round_kr(t_disp),
        tangible_closing_cost=round_kr(t_close),
        tangible_year_depreciation=round_kr(t_dep_year),
        tangible_accumulated_depreciation=round_kr(t_dep_acc),
        tangible_carrying_value=max(0, round_kr(t_close - t_dep_acc)),
        intangible_opening_cost=round_kr(i_open),
        intangible_additions=round_kr(i_add),
        intangible_disposals=round_kr(i_disp),
        intangible_closing_cost=round_kr(i_close),
        intangible_year_depreciation=round_kr(i_dep_year),
        intangible_accumulated_depreciation=round_kr(i_dep_acc),
        intangible_carrying_value=max(0, round_kr(i_close - i_dep_acc)),
    )


def _board_members_from_snapshot(unit: dict[str, Any] | None) -> tuple[dict[str, Any], ...]:
    """Extract board / daglig leder defaults from the stored Enhetsregisteret snapshot."""
    if not isinstance(unit, dict):
        return ()
    roles = unit.get("roles")
    if not isinstance(roles, list):
        return ()
    members: list[dict[str, Any]] = []
    for entry in roles:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        role = str(entry.get("role") or entry.get("description") or "").strip()
        if name:
            members.append({"name": name, "role": role or "Styremedlem"})
    return tuple(members[:20])


async def build_computed(
    db: AsyncSession,
    company: Company,
    year: int,
    *,
    today: date | None = None,
) -> ComputedAarsregnskap:
    """Load ledger balances, draft settings, and linked registers, and compute årsregnskap."""
    fy = await db.scalar(
        select(FinancialYear).where(
            FinancialYear.company_id == company.id,
            FinancialYear.year == year,
        )
    )
    prior_fy = await db.scalar(
        select(FinancialYear).where(
            FinancialYear.company_id == company.id,
            FinancialYear.year == year - 1,
        )
    )
    start_date = fy.start_date if fy is not None else date(year, 1, 1)
    end_date = fy.end_date if fy is not None else date(year, 12, 31)
    year_closed = fy is not None and fy.closed_at is not None

    curr_movements = await _ledger_balances(db, company, year=year)
    curr_cumulative = await _ledger_balances(db, company, up_to=year)

    prior_movements: list[LedgerAccountBalance] | None = None
    prior_cumulative: list[LedgerAccountBalance] | None = None
    if prior_fy is not None:
        pm = await _ledger_balances(db, company, year=year - 1)
        pc = await _ledger_balances(db, company, up_to=year - 1)
        if pm or pc:
            prior_movements = pm
            prior_cumulative = pc

    draft_row = await get_draft(db, company, year)
    snapshot = await ledger_service.get_register_snapshot(db, company=company)
    unit = snapshot.unit if snapshot is not None and isinstance(snapshot.unit, dict) else {}

    if draft_row is not None:
        draft_input = DraftInput(
            small_company=draft_row.small_company,
            is_parent=draft_row.is_parent,
            consolidated=draft_row.consolidated,
            winding_up_accounts=draft_row.winding_up_accounts,
            auditor_elected_out=draft_row.auditor_elected_out,
            prepared_by_external_accountant=draft_row.prepared_by_external_accountant,
            external_accountant_assistance=draft_row.external_accountant_assistance,
            approval_date=draft_row.approval_date,
            confirming_representative=draft_row.confirming_representative,
            contact_email=draft_row.contact_email,
            board_meeting_date=draft_row.board_meeting_date,
            board_place=draft_row.board_place,
            board_members=tuple(draft_row.board_members or ()),
            general_meeting_date=draft_row.general_meeting_date,
            general_meeting_place=draft_row.general_meeting_place,
            general_meeting_chair=draft_row.general_meeting_chair,
            general_meeting_cosigner=draft_row.general_meeting_cosigner,
            dividend_proposed=draft_row.dividend_proposed,
            prior_year_overrides=dict(draft_row.prior_year_overrides or {}),
            notes=dict(draft_row.notes or {}),
        )
    else:
        # Sensible defaults from Enhetsregisteret snapshot when no draft saved yet.
        default_board = _board_members_from_snapshot(unit)
        default_place = (
            str(unit.get("business_city") or unit.get("postal_city") or "").strip() or None
        )
        draft_input = DraftInput(
            winding_up_accounts=company.wound_up_on is not None
            and company.wound_up_on.year == year,
            board_place=default_place,
            board_members=default_board,
            general_meeting_place=default_place,
        )

    shareholders, share_count, par_value = await _load_shareholder_summary(db, company, year)
    fixed_assets = await _load_fixed_asset_summary(db, company, year)

    return compute_aarsregnskap(
        company=company,
        year=year,
        start_date=start_date,
        end_date=end_date,
        year_closed=year_closed,
        current_movements=curr_movements,
        current_cumulative=curr_cumulative,
        prior_movements=prior_movements,
        prior_cumulative=prior_cumulative,
        draft=draft_input,
        shareholders=shareholders,
        share_count=share_count,
        par_value=par_value,
        fixed_assets=fixed_assets,
        today=today,
    )


# --- Filings and archived documents -----------------------------------------


async def list_filings(
    db: AsyncSession, company: Company, *, year: int | None = None
) -> list[AarsregnskapFiling]:
    query = (
        select(AarsregnskapFiling)
        .options(selectinload(AarsregnskapFiling.documents))
        .where(AarsregnskapFiling.company_id == company.id)
        .order_by(AarsregnskapFiling.created_at.desc(), AarsregnskapFiling.id.desc())
    )
    if year is not None:
        query = query.where(AarsregnskapFiling.year == year)
    return list(await db.scalars(query))


async def get_filing(db: AsyncSession, company: Company, filing_id: int) -> AarsregnskapFiling:
    row: AarsregnskapFiling | None = await db.scalar(
        select(AarsregnskapFiling)
        .options(selectinload(AarsregnskapFiling.documents))
        .where(
            AarsregnskapFiling.id == filing_id,
            AarsregnskapFiling.company_id == company.id,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise AarsregnskapFilingNotFoundError("finner ikke årsregnskapsinnsendingen")
    return row


async def create_filing(
    db: AsyncSession,
    *,
    company: Company,
    year: int,
    status: str,
    auth_method: str,
    altinn_instance_id: str | None,
    hovedskjema_data_id: str | None,
    underskjema_data_id: str | None,
    altinn_portal_url: str | None,
    content_hash: str,
    aarsresultat: Decimal,
    sum_eiendeler: Decimal,
    sum_egenkapital: Decimal,
    fastsettelsedato: date | None,
    bekreftende_representant: str | None,
    hovedskjema_xml: str,
    underskjema_xml: str,
    validation_issues: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    created_by_user_id: int | None,
    locked_at: datetime | None = None,
    signed_by_user_id: int | None = None,
    signed_by_name: str | None = None,
    signed_at: datetime | None = None,
    receipt_received_at: datetime | None = None,
    error: str | None = None,
) -> AarsregnskapFiling:
    filing = AarsregnskapFiling(
        company_id=company.id,
        year=year,
        status=status,
        auth_method=auth_method,
        altinn_instance_id=altinn_instance_id,
        hovedskjema_data_id=hovedskjema_data_id,
        underskjema_data_id=underskjema_data_id,
        altinn_portal_url=altinn_portal_url,
        content_hash=content_hash,
        aarsresultat=aarsresultat,
        sum_eiendeler=sum_eiendeler,
        sum_egenkapital=sum_egenkapital,
        fastsettelsedato=fastsettelsedato,
        bekreftende_representant=bekreftende_representant,
        hovedskjema_xml=hovedskjema_xml,
        underskjema_xml=underskjema_xml,
        validation_issues=validation_issues,
        findings=findings,
        created_by_user_id=created_by_user_id,
        locked_at=locked_at,
        signed_by_user_id=signed_by_user_id,
        signed_by_name=signed_by_name,
        signed_at=signed_at,
        receipt_received_at=receipt_received_at,
        error=error[:1000] if error else None,
    )
    db.add(filing)
    await db.flush()
    return await get_filing(db, company, filing.id)


async def archive_document(
    db: AsyncSession,
    storage: DocumentStorage,
    *,
    company: Company,
    filing: AarsregnskapFiling,
    document_type: str,
    filename: str,
    content_type: str,
    content: bytes,
    external_document_id: str | None = None,
) -> AarsregnskapFilingDocument:
    """Persist bytes to ``DocumentStorage`` and attach an archive row to ``filing``."""
    # Avoid duplicating an identical document_type + sha256 if status is polled twice.
    digest = hashlib.sha256(content).hexdigest()
    existing = await db.scalar(
        select(AarsregnskapFilingDocument).where(
            AarsregnskapFilingDocument.filing_id == filing.id,
            AarsregnskapFilingDocument.document_type == document_type,
            AarsregnskapFilingDocument.sha256 == digest,
        )
    )
    if existing is not None:
        return existing

    storage_key = await storage.save(
        content=content,
        filename=filename,
        content_type=content_type,
    )
    doc = AarsregnskapFilingDocument(
        filing_id=filing.id,
        document_type=document_type,
        external_document_id=external_document_id,
        filename=filename,
        content_type=content_type,
        byte_size=len(content),
        sha256=digest,
        storage_key=storage_key,
    )
    db.add(doc)
    await db.flush()
    return doc


async def get_filing_document(
    db: AsyncSession, company: Company, filing_id: int, document_type: str
) -> AarsregnskapFilingDocument:
    filing = await get_filing(db, company, filing_id)
    for doc in reversed(filing.documents):
        if doc.document_type == document_type:
            return doc
    raise AarsregnskapDocumentNotFoundError(
        f"finner ikke dokument av typen «{document_type}» for denne innsendingen"
    )
