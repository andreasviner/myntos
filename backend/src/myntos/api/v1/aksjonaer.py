"""Aksjeeierbok (shareholder register) and Aksjonærregisteroppgaven (RF-1086) endpoints.

Every Norwegian aksjeselskap (AS / ASA) must keep an aksjeeierbok (aksjeloven
§ 4-5) and file Aksjonærregisteroppgaven (RF-1086) to Skatteetaten by 31 January
each year through an end-user system (Skatteetaten retired the altinn.no web
form in June 2026).

Submission uses Maskinporten + Altinn 3 systembruker (no ID-porten session
required at send time, because the company approves Myntos once in Altinn).
Writes to the register are ``key_write``; calls that talk to Skatteetaten
(``/prefill`` and ``/send``) are ``key_send`` so an agent key always waits for
a person's confirmation on ``/godkjenning/`` before anything leaves Myntos
(docs/agents-and-api.md rule 5).
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from myntos.aksjonaer import compute, service, xmlwriter
from myntos.aksjonaer.codes import (
    EMISJON_SUBTYPES,
    FONDSEMISJON_SUBTYPES,
    KAPITALNEDSETTELSE_SUBTYPES,
    OVERDRAGELSE_SUBTYPES,
    SHARE_CLASS_CODES,
    UTBYTTE_SUBTYPES,
    EventLineRole,
    mask_national_id,
)
from myntos.aksjonaer.errors import (
    AksjonaerConfirmationRequiredError,
    AksjonaerNotReadyError,
    AksjonaerPrefillNotFoundError,
    AksjonaerRemoteError,
    AksjonaerSubmissionNotAvailableError,
    InvalidAksjonaerDataError,
    ShareClassNotFoundError,
    ShareholderNotFoundError,
)
from myntos.aksjonaer.models import (
    AksjonaerFiling,
    AksjonaerFilingStatus,
    ShareClass,
    ShareEvent,
    Shareholder,
)
from myntos.api.deps import (
    KEY_READ,
    CurrentCompany,
    CurrentUser,
    DbSession,
    Storage,
    key_send,
    key_write,
)
from myntos.api.v1.systembruker import is_systemuser_available
from myntos.auth.maskinporten.client import MaskinportenError, get_token_provider
from myntos.core.clock import utc_now
from myntos.core.config import Settings, get_settings
from myntos.domain.altinn import service as systemuser_service
from myntos.domain.altinn.errors import SystemUserNotApprovedError
from myntos.domain.arsoppgjor.findings import Finding, Severity
from myntos.domain.identity.access import Permission, require_permission
from myntos.integrations.altinn import systemregister
from myntos.integrations.skatteetaten import aksjonaer as ske_aksjonaer

router = APIRouter(tags=["aksjonaer"])
logger = logging.getLogger(__name__)

_OSLO = ZoneInfo("Europe/Oslo")
_YearQuery = Annotated[int | None, Query(ge=2000, le=2100)]


def _today() -> date:
    return utc_now().astimezone(_OSLO).date()


def _default_year(today: date) -> int:
    """In Jan-Jun file last year (due 31 Jan); in Jul-Dec prepare the current year."""
    return today.year - 1 if today.month <= 6 else today.year


def is_submission_available(settings: Settings | None = None) -> bool:
    """Whether Maskinporten systembruker and Skatteetaten RF-1086 API are configured."""
    cfg = settings or get_settings()
    return is_systemuser_available(cfg) and bool((cfg.skatteetaten_api_base_url or "").strip())


# --- Response models --------------------------------------------------------------


class FindingResponse(BaseModel):
    severity: str
    code: str
    message: str


class ClassSnapshotResponse(BaseModel):
    shares: int
    par_value: Decimal
    share_capital: Decimal
    paid_in_capital: Decimal
    paid_in_premium: Decimal


class ShareEventLineResponse(BaseModel):
    id: int
    shareholder_id: int
    shareholder_name: str
    role: str
    shares: int
    amount: Decimal | None
    paid_in_premium: Decimal | None
    withholding_tax: Decimal | None
    withholding_tax_rate: int | None
    withholding_tax_country: str | None
    counterparty_national_id_masked: str | None
    counterparty_orgnr: str | None


class ShareEventResponse(BaseModel):
    id: int
    company_id: int
    share_class_id: int
    share_class_code: str
    event_type: str
    subtype: str | None
    event_date: date
    description: str | None
    total_shares: int | None
    par_value_after: Decimal | None
    total_amount: Decimal | None
    premium_amount: Decimal | None
    amount_per_share: Decimal | None
    lines: list[ShareEventLineResponse]


class ShareholderHoldingResponse(BaseModel):
    shareholder_id: int
    kind: str
    name: str
    national_id_masked: str | None
    has_national_id: bool
    orgnr: str | None
    foreign_id: str | None
    country_code: str
    is_own_shares: bool
    opening_shares: int
    closing_shares: int
    ownership_percent: Decimal
    dividends_total: Decimal


class ShareClassYearResponse(BaseModel):
    id: int
    company_id: int
    code: str
    name: str
    isin: str | None
    opening_year: int | None
    opening_shares: int
    opening_par_value: Decimal
    opening_share_capital: Decimal
    opening_paid_in_capital: Decimal
    opening_paid_in_premium: Decimal
    opening: ClassSnapshotResponse
    closing: ClassSnapshotResponse
    liquidated: bool
    blocked: bool
    findings: list[FindingResponse]
    shareholders: list[ShareholderHoldingResponse]


class ShareClassResponse(BaseModel):
    id: int
    company_id: int
    code: str
    name: str
    isin: str | None
    opening_year: int | None
    opening_shares: int
    opening_par_value: Decimal
    opening_share_capital: Decimal
    opening_paid_in_capital: Decimal
    opening_paid_in_premium: Decimal


class ShareholderResponse(BaseModel):
    id: int
    company_id: int
    kind: str
    name: str
    national_id_masked: str | None
    has_national_id: bool
    orgnr: str | None
    foreign_id: str | None
    address: str | None
    postal_code: str | None
    city: str | None
    country_code: str
    email: str | None
    is_own_shares: bool
    opening_holdings: dict[str, int]


class AksjonaerFilingDocumentResponse(BaseModel):
    document_type: str
    filename: str
    content_type: str
    byte_size: int
    fetched_at: datetime


class AksjonaerFilingResponse(BaseModel):
    id: int
    company_id: int
    share_class_id: int | None
    share_class_code: str
    year: int
    status: str
    underskjema_count: int
    hovedskjema_id: str | None
    leveranse_referanse: str | None
    dialog_id: str | None
    forsendelse_id: str | None
    contact_email: str | None
    contact_name: str | None
    findings: list[FindingResponse]
    submitted_at: datetime
    error: str | None
    documents: list[AksjonaerFilingDocumentResponse]


class AksjonaerCodesResponse(BaseModel):
    share_classes: dict[str, str]
    utbytte_subtypes: dict[str, str]
    emisjon_subtypes: dict[str, str]
    overdragelse_subtypes: dict[str, str]
    fondsemisjon_subtypes: dict[str, str]
    kapitalnedsettelse_subtypes: dict[str, str]


class AksjonaerYearResponse(BaseModel):
    company_id: int
    company_name: str
    orgnr: str
    organisation_form: str | None
    year: int
    submission_available: bool
    systembruker_approved: bool
    contact_email_default: str | None
    contact_name_default: str | None
    company_opening_capital: Decimal
    company_closing_capital: Decimal
    ledger_share_capital: Decimal | None
    classes: list[ShareClassYearResponse]
    shareholders: list[ShareholderResponse]
    events: list[ShareEventResponse]
    findings: list[FindingResponse]
    blocked: bool
    filings: list[AksjonaerFilingResponse]
    codes: AksjonaerCodesResponse


class PrefillResultResponse(BaseModel):
    company_id: int
    year: int
    oppgave_status: str | None
    share_classes_imported: int
    shareholders_imported: int


# --- Request models ---------------------------------------------------------------


class CreateShareClassBody(BaseModel):
    code: str = Field(default="01", min_length=2, max_length=2)
    name: str = Field(default="Ordinære aksjer", min_length=1, max_length=80)
    isin: str | None = Field(default=None, max_length=35)
    opening_year: int | None = Field(default=None, ge=1900, le=2100)
    opening_shares: int = Field(default=0, ge=0)
    opening_par_value: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    opening_share_capital: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0"))
    opening_paid_in_capital: Decimal = Field(default=Decimal("0.00"))
    opening_paid_in_premium: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0"))


class UpdateShareClassBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    isin: str | None = Field(default=None, max_length=35)
    clear_isin: bool = False
    opening_year: int | None = Field(default=None, ge=1900, le=2100)
    clear_opening_year: bool = False
    opening_shares: int | None = Field(default=None, ge=0)
    opening_par_value: Decimal | None = Field(default=None, ge=Decimal("0"))
    opening_share_capital: Decimal | None = Field(default=None, ge=Decimal("0"))
    opening_paid_in_capital: Decimal | None = None
    opening_paid_in_premium: Decimal | None = Field(default=None, ge=Decimal("0"))


class CreateShareholderBody(BaseModel):
    kind: Literal["person", "selskap", "utenlandsk"]
    name: str = Field(min_length=1, max_length=150)
    national_id: str | None = Field(default=None, max_length=20)
    orgnr: str | None = Field(default=None, max_length=20)
    foreign_id: str | None = Field(default=None, max_length=20)
    address: str | None = Field(default=None, max_length=105)
    postal_code: str | None = Field(default=None, max_length=15)
    city: str | None = Field(default=None, max_length=35)
    country_code: str = Field(default="NO", min_length=2, max_length=2)
    email: str | None = Field(default=None, max_length=150)
    is_own_shares: bool = False
    # Map of share_class_id (as integer) -> opening share count.
    opening_holdings: dict[int, int] | None = None


class UpdateShareholderBody(BaseModel):
    kind: Literal["person", "selskap", "utenlandsk"] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=150)
    national_id: str | None = Field(default=None, max_length=20)
    orgnr: str | None = Field(default=None, max_length=20)
    foreign_id: str | None = Field(default=None, max_length=20)
    address: str | None = Field(default=None, max_length=105)
    postal_code: str | None = Field(default=None, max_length=15)
    city: str | None = Field(default=None, max_length=35)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    email: str | None = Field(default=None, max_length=150)
    is_own_shares: bool | None = None
    opening_holdings: dict[int, int] | None = None


class CreateEventLineBody(BaseModel):
    shareholder_id: int
    role: Literal["mottaker", "avgiver"] = EventLineRole.MOTTAKER.value
    shares: int = Field(default=0, ge=0)
    amount: Decimal | None = None
    paid_in_premium: Decimal | None = None
    withholding_tax: Decimal | None = None
    withholding_tax_rate: int | None = Field(default=None, ge=0, le=100)
    withholding_tax_country: str | None = Field(default=None, max_length=2)
    counterparty_national_id: str | None = Field(default=None, max_length=20)
    counterparty_orgnr: str | None = Field(default=None, max_length=20)


class CreateShareEventBody(BaseModel):
    share_class_id: int
    event_type: str = Field(min_length=1, max_length=32)
    subtype: str | None = Field(default=None, max_length=32)
    event_date: date
    description: str | None = Field(default=None, max_length=255)
    total_shares: int | None = Field(default=None, ge=0)
    par_value_after: Decimal | None = Field(default=None, ge=Decimal("0"))
    total_amount: Decimal | None = None
    premium_amount: Decimal | None = None
    amount_per_share: Decimal | None = None
    lines: list[CreateEventLineBody] = Field(default_factory=list)


class PrefillBody(BaseModel):
    aar: int = Field(ge=2000, le=2100)


class SendAksjonaerBody(BaseModel):
    aar: int = Field(ge=2000, le=2100)
    share_class_id: int | None = None
    contact_email: str | None = Field(default=None, max_length=150)
    contact_name: str | None = Field(default=None, max_length=150)
    contact_role: str | None = Field(default=None, max_length=40)
    contact_phone: str | None = Field(default=None, max_length=30)
    bekreftet: bool


# --- Serialisation helpers --------------------------------------------------------


def _finding(f: Finding) -> FindingResponse:
    return FindingResponse(severity=f.severity.value, code=f.code, message=f.message)


def _snapshot(snap: compute.ClassSnapshot) -> ClassSnapshotResponse:
    return ClassSnapshotResponse(
        shares=snap.shares,
        par_value=snap.par_value,
        share_capital=snap.share_capital,
        paid_in_capital=snap.paid_in_capital,
        paid_in_premium=snap.paid_in_premium,
    )


def _share_class_response(sc: ShareClass) -> ShareClassResponse:
    return ShareClassResponse(
        id=sc.id,
        company_id=sc.company_id,
        code=sc.code,
        name=sc.name,
        isin=sc.isin,
        opening_year=sc.opening_year,
        opening_shares=sc.opening_shares,
        opening_par_value=sc.opening_par_value,
        opening_share_capital=sc.opening_share_capital,
        opening_paid_in_capital=sc.opening_paid_in_capital,
        opening_paid_in_premium=sc.opening_paid_in_premium,
    )


def _shareholder_response(
    holder: Shareholder, holdings_map: dict[int, dict[int, int]]
) -> ShareholderResponse:
    per_class = holdings_map.get(holder.id, {})
    return ShareholderResponse(
        id=holder.id,
        company_id=holder.company_id,
        kind=holder.kind,
        name=holder.name,
        national_id_masked=mask_national_id(holder.national_id),
        has_national_id=bool(holder.national_id),
        orgnr=holder.orgnr,
        foreign_id=holder.foreign_id,
        address=holder.address,
        postal_code=holder.postal_code,
        city=holder.city,
        country_code=holder.country_code,
        email=holder.email,
        is_own_shares=holder.is_own_shares,
        opening_holdings={str(sc_id): qty for sc_id, qty in per_class.items()},
    )


def _event_response(
    event: ShareEvent,
    *,
    share_class_code: str,
    holder_names: dict[int, str],
) -> ShareEventResponse:
    return ShareEventResponse(
        id=event.id,
        company_id=event.company_id,
        share_class_id=event.share_class_id,
        share_class_code=share_class_code,
        event_type=event.event_type,
        subtype=event.subtype,
        event_date=event.event_date,
        description=event.description,
        total_shares=event.total_shares,
        par_value_after=event.par_value_after,
        total_amount=event.total_amount,
        premium_amount=event.premium_amount,
        amount_per_share=event.amount_per_share,
        lines=[
            ShareEventLineResponse(
                id=line.id,
                shareholder_id=line.shareholder_id,
                shareholder_name=holder_names.get(line.shareholder_id, ""),
                role=line.role,
                shares=line.shares,
                amount=line.amount,
                paid_in_premium=line.paid_in_premium,
                withholding_tax=line.withholding_tax,
                withholding_tax_rate=line.withholding_tax_rate,
                withholding_tax_country=line.withholding_tax_country,
                counterparty_national_id_masked=mask_national_id(line.counterparty_national_id),
                counterparty_orgnr=line.counterparty_orgnr,
            )
            for line in event.lines
        ],
    )


def _filing_response(filing: AksjonaerFiling) -> AksjonaerFilingResponse:
    return AksjonaerFilingResponse(
        id=filing.id,
        company_id=filing.company_id,
        share_class_id=filing.share_class_id,
        share_class_code=filing.share_class_code,
        year=filing.year,
        status=filing.status,
        underskjema_count=filing.underskjema_count,
        hovedskjema_id=filing.hovedskjema_id,
        leveranse_referanse=filing.leveranse_referanse,
        dialog_id=filing.dialog_id,
        forsendelse_id=filing.forsendelse_id,
        contact_email=filing.contact_email,
        contact_name=filing.contact_name,
        findings=[FindingResponse(**item) for item in filing.findings],
        submitted_at=filing.submitted_at,
        error=filing.error,
        documents=[
            AksjonaerFilingDocumentResponse(
                document_type=doc.document_type,
                filename=doc.filename,
                content_type=doc.content_type,
                byte_size=doc.byte_size,
                fetched_at=doc.fetched_at,
            )
            for doc in filing.documents
        ],
    )


def _codes_response() -> AksjonaerCodesResponse:
    return AksjonaerCodesResponse(
        share_classes=dict(SHARE_CLASS_CODES),
        utbytte_subtypes=dict(UTBYTTE_SUBTYPES),
        emisjon_subtypes=dict(EMISJON_SUBTYPES),
        overdragelse_subtypes={key: val[2] for key, val in OVERDRAGELSE_SUBTYPES.items()},
        fondsemisjon_subtypes=dict(FONDSEMISJON_SUBTYPES),
        kapitalnedsettelse_subtypes=dict(KAPITALNEDSETTELSE_SUBTYPES),
    )


def _holdings_by_holder(classes: list[ShareClass]) -> dict[int, dict[int, int]]:
    out: dict[int, dict[int, int]] = {}
    for sc in classes:
        for oh in sc.opening_holdings:
            out.setdefault(oh.shareholder_id, {})[sc.id] = int(oh.shares)
    return out


# --- Year overview ----------------------------------------------------------------


@router.get("/companies/{company_id}/aksjonaer", openapi_extra=KEY_READ)
async def get_aksjonaer_year(
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    aar: _YearQuery = None,
) -> AksjonaerYearResponse:
    """Read the company's aksjeeierbok and computed RF-1086 statement for one income year.

    Returns every share class with its opening (01.01) and closing (31.12)
    share count, par value, share capital, paid-in capital and paid-in premium,
    every shareholder with opening/closing share count, ownership percentage
    and dividends (national IDs are masked as ``DDMMYY *****``), all corporate
    and transfer events in ``aar``, a cross-check against ledger account 2000
    (Aksjekapital), all pre-flight findings (``feil`` blocks submission;
    ``merknad`` is advisory), Altinn 3 systembruker readiness, and past RF-1086
    filings for the year.

    ``aar`` defaults to last year in January through June (for the 31 January
    deadline) and the current year from July through December. Needs
    ``ledger:read``; a key needs ``read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    company = context.company
    year = aar if aar is not None else _default_year(_today())

    classes = await service.list_share_classes(db, company)
    shareholders = await service.list_shareholders(db, company)
    ledger_cap = await service.ledger_share_capital_for_year(db, company, year)
    filings = await service.list_filings(db, company, year=year)
    sysuser = await systemuser_service.approved_for_resource(
        db, company, systemregister.AKSJONAERREGISTER_RESOURCE
    )

    contact_email_default = (company.email or user.email or "").strip() or None
    computed = compute.compute_year(
        company=company,
        year=year,
        share_classes=classes,
        shareholders=shareholders,
        contact_email=contact_email_default,
        ledger_share_capital=ledger_cap,
    )

    holdings_map = _holdings_by_holder(classes)
    holder_names = {h.id: h.name for h in shareholders}

    year_events: list[ShareEventResponse] = []
    class_responses: list[ShareClassYearResponse] = []
    for csc in computed.classes:
        sc = csc.share_class
        for ev, _, _ in csc.year_events:
            year_events.append(
                _event_response(ev, share_class_code=sc.code, holder_names=holder_names)
            )
        class_responses.append(
            ShareClassYearResponse(
                id=sc.id,
                company_id=sc.company_id,
                code=sc.code,
                name=sc.name,
                isin=sc.isin,
                opening_year=sc.opening_year,
                opening_shares=sc.opening_shares,
                opening_par_value=sc.opening_par_value,
                opening_share_capital=sc.opening_share_capital,
                opening_paid_in_capital=sc.opening_paid_in_capital,
                opening_paid_in_premium=sc.opening_paid_in_premium,
                opening=_snapshot(csc.opening),
                closing=_snapshot(csc.closing),
                liquidated=csc.liquidated,
                blocked=csc.blocked,
                findings=[_finding(f) for f in csc.findings],
                shareholders=[
                    ShareholderHoldingResponse(
                        shareholder_id=hs.shareholder.id,
                        kind=hs.shareholder.kind,
                        name=hs.shareholder.name,
                        national_id_masked=mask_national_id(hs.shareholder.national_id),
                        has_national_id=bool(hs.shareholder.national_id),
                        orgnr=hs.shareholder.orgnr,
                        foreign_id=hs.shareholder.foreign_id,
                        country_code=hs.shareholder.country_code,
                        is_own_shares=hs.shareholder.is_own_shares,
                        opening_shares=hs.opening_shares,
                        closing_shares=hs.closing_shares,
                        ownership_percent=hs.ownership_percent,
                        dividends_total=hs.dividends_total,
                    )
                    for hs in csc.shareholders
                ],
            )
        )

    year_events.sort(key=lambda e: (e.event_date, e.id))

    return AksjonaerYearResponse(
        company_id=company.id,
        company_name=company.name,
        orgnr=company.orgnr,
        organisation_form=company.organisation_form,
        year=year,
        submission_available=is_submission_available(),
        systembruker_approved=sysuser is not None,
        contact_email_default=contact_email_default,
        contact_name_default=user.full_name,
        company_opening_capital=computed.company_opening_capital,
        company_closing_capital=computed.company_closing_capital,
        ledger_share_capital=computed.ledger_share_capital,
        classes=class_responses,
        shareholders=[_shareholder_response(h, holdings_map) for h in shareholders],
        events=year_events,
        findings=[_finding(f) for f in computed.findings],
        blocked=computed.blocked,
        filings=[_filing_response(f) for f in filings],
        codes=_codes_response(),
    )


# --- Share classes ----------------------------------------------------------------


@router.post(
    "/companies/{company_id}/aksjonaer/aksjeklasser",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_write("opprette en aksjeklasse"),
)
async def create_share_class(
    body: CreateShareClassBody,
    context: CurrentCompany,
    db: DbSession,
) -> ShareClassResponse:
    """Create a share class (aksjeklasse) in the company's aksjeeierbok.

    ``code`` follows Skatteetaten's ``KodelisteAksjeklasse`` (``01`` Ordinære
    aksjer through ``13`` J-aksjer). Most Norwegian AS companies have a single
    class ``01``. If the company existed before the first year recorded in
    Myntos, supply ``opening_year`` (e.g. ``2025`` for opening balances on
    01.01.2026) together with ``opening_shares``, ``opening_par_value``,
    ``opening_share_capital``, ``opening_paid_in_capital``, and
    ``opening_paid_in_premium``.

    Refusals: 409 ``duplicate_share_class`` if ``code`` already exists; 422
    ``invalid_aksjonaer_data``. Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    sc = await service.create_share_class(
        db,
        company=context.company,
        code=body.code,
        name=body.name,
        isin=body.isin,
        opening_year=body.opening_year,
        opening_shares=body.opening_shares,
        opening_par_value=body.opening_par_value,
        opening_share_capital=body.opening_share_capital,
        opening_paid_in_capital=body.opening_paid_in_capital,
        opening_paid_in_premium=body.opening_paid_in_premium,
    )
    return _share_class_response(sc)


@router.patch(
    "/companies/{company_id}/aksjonaer/aksjeklasser/{share_class_id}",
    openapi_extra=key_write("oppdatere en aksjeklasse"),
)
async def update_share_class(
    share_class_id: int,
    body: UpdateShareClassBody,
    context: CurrentCompany,
    db: DbSession,
) -> ShareClassResponse:
    """Update a share class's name, ISIN, or opening capital figures.

    Refusals: 404 ``share_class_not_found``; 422 ``invalid_aksjonaer_data``.
    Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    sc = await service.update_share_class(
        db,
        company=context.company,
        share_class_id=share_class_id,
        name=body.name,
        isin=body.isin,
        clear_isin=body.clear_isin,
        opening_year=body.opening_year,
        clear_opening_year=body.clear_opening_year,
        opening_shares=body.opening_shares,
        opening_par_value=body.opening_par_value,
        opening_share_capital=body.opening_share_capital,
        opening_paid_in_capital=body.opening_paid_in_capital,
        opening_paid_in_premium=body.opening_paid_in_premium,
    )
    return _share_class_response(sc)


@router.delete(
    "/companies/{company_id}/aksjonaer/aksjeklasser/{share_class_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=key_write("slette en aksjeklasse"),
)
async def delete_share_class(
    share_class_id: int,
    context: CurrentCompany,
    db: DbSession,
) -> Response:
    """Delete a share class that has no events or RF-1086 filings.

    Refusals: 404 ``share_class_not_found``; 409 ``share_class_in_use``.
    Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    await service.delete_share_class(db, company=context.company, share_class_id=share_class_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Shareholders -----------------------------------------------------------------


@router.post(
    "/companies/{company_id}/aksjonaer/aksjonaerer",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_write("legge til en aksjonær i aksjeeierboken"),
)
async def create_shareholder(
    body: CreateShareholderBody,
    context: CurrentCompany,
    db: DbSession,
) -> ShareholderResponse:
    """Add a shareholder (aksjonær) to the company's aksjeeierbok.

    ``kind`` is ``person`` (Norwegian individual identified by 11-digit
    fødselsnummer or D-nummer, mod-11 validated), ``selskap`` (Norwegian
    company identified by 9-digit organisasjonsnummer), or ``utenlandsk``
    (foreign shareholder identified by Skatteetaten ``UTL`` + 9 digits, or
    name and ISO-2 ``country_code``). Optional ``opening_holdings`` maps
    ``share_class_id`` to the shareholder's opening share count.

    Refusals: 404 ``share_class_not_found``; 422 ``invalid_aksjonaer_data``.
    Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    holder = await service.create_shareholder(
        db,
        company=context.company,
        kind=body.kind,
        name=body.name,
        national_id=body.national_id,
        orgnr=body.orgnr,
        foreign_id=body.foreign_id,
        address=body.address,
        postal_code=body.postal_code,
        city=body.city,
        country_code=body.country_code,
        email=body.email,
        is_own_shares=body.is_own_shares,
        opening_holdings=body.opening_holdings,
    )
    classes = await service.list_share_classes(db, context.company)
    return _shareholder_response(holder, _holdings_by_holder(classes))


@router.patch(
    "/companies/{company_id}/aksjonaer/aksjonaerer/{shareholder_id}",
    openapi_extra=key_write("oppdatere en aksjonær i aksjeeierboken"),
)
async def update_shareholder(
    shareholder_id: int,
    body: UpdateShareholderBody,
    context: CurrentCompany,
    db: DbSession,
) -> ShareholderResponse:
    """Update a shareholder's details, identifier, or opening share holdings.

    Omit ``national_id`` when editing other fields to preserve the stored
    fødselsnummer without re-entering it.

    Refusals: 404 ``shareholder_not_found`` or ``share_class_not_found``; 422
    ``invalid_aksjonaer_data``. Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    holder = await service.update_shareholder(
        db,
        company=context.company,
        shareholder_id=shareholder_id,
        kind=body.kind,
        name=body.name,
        national_id=body.national_id,
        orgnr=body.orgnr,
        foreign_id=body.foreign_id,
        address=body.address,
        postal_code=body.postal_code,
        city=body.city,
        country_code=body.country_code,
        email=body.email,
        is_own_shares=body.is_own_shares,
        opening_holdings=body.opening_holdings,
    )
    classes = await service.list_share_classes(db, context.company)
    return _shareholder_response(holder, _holdings_by_holder(classes))


@router.delete(
    "/companies/{company_id}/aksjonaer/aksjonaerer/{shareholder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=key_write("slette en aksjonær fra aksjeeierboken"),
)
async def delete_shareholder(
    shareholder_id: int,
    context: CurrentCompany,
    db: DbSession,
) -> Response:
    """Delete a shareholder who does not appear on any recorded event.

    Refusals: 404 ``shareholder_not_found``; 409 ``shareholder_in_use``.
    Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    await service.delete_shareholder(db, company=context.company, shareholder_id=shareholder_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Corporate & shareholder events ----------------------------------------------


@router.post(
    "/companies/{company_id}/aksjonaer/hendelser",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_write("registrere en aksjehendelse"),
)
async def create_share_event(
    body: CreateShareEventBody,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
) -> ShareEventResponse:
    """Record a corporate or shareholder event in the aksjeeierbok.

    Supported ``event_type`` values: ``stiftelse``, ``nyemisjon``,
    ``overdragelse``, ``utbytte``, ``splitt``, ``spleis``, ``fondsemisjon``,
    ``kapitalforhoyelse_palydende``, ``kapitalnedsettelse``, ``likvidasjon``.
    Each per-shareholder entry in ``lines`` names a ``shareholder_id`` in the
    same company and its ``role`` (``mottaker`` or ``avgiver``), ``shares``,
    and optional ``amount``, ``paid_in_premium``, withholding tax, or
    continuity counterparty ID.

    Refusals: 404 ``share_class_not_found`` or ``shareholder_not_found``; 422
    ``invalid_aksjonaer_data``. Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    sc = await service.get_share_class(db, context.company, body.share_class_id)
    event = await service.create_event(
        db,
        company=context.company,
        share_class_id=body.share_class_id,
        event_type=body.event_type,
        subtype=body.subtype,
        event_date=body.event_date,
        description=body.description,
        total_shares=body.total_shares,
        par_value_after=body.par_value_after,
        total_amount=body.total_amount,
        premium_amount=body.premium_amount,
        amount_per_share=body.amount_per_share,
        lines=[
            service.EventLineInput(
                shareholder_id=line.shareholder_id,
                role=line.role,
                shares=line.shares,
                amount=line.amount,
                paid_in_premium=line.paid_in_premium,
                withholding_tax=line.withholding_tax,
                withholding_tax_rate=line.withholding_tax_rate,
                withholding_tax_country=line.withholding_tax_country,
                counterparty_national_id=line.counterparty_national_id,
                counterparty_orgnr=line.counterparty_orgnr,
            )
            for line in body.lines
        ],
        created_by_user_id=user.id,
    )
    holders = await service.list_shareholders(db, context.company)
    holder_names = {h.id: h.name for h in holders}
    return _event_response(event, share_class_code=sc.code, holder_names=holder_names)


@router.delete(
    "/companies/{company_id}/aksjonaer/hendelser/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=key_write("slette en aksjehendelse"),
)
async def delete_share_event(
    event_id: int,
    context: CurrentCompany,
    db: DbSession,
) -> Response:
    """Delete a corporate or shareholder event from the aksjeeierbok.

    Refusals: 404 ``share_event_not_found``. Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    await service.delete_event(db, company=context.company, event_id=event_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- XML preview / download -------------------------------------------------------


@router.get("/companies/{company_id}/aksjonaer/xml", openapi_extra=KEY_READ)
async def preview_aksjonaer_xml(
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    aar: Annotated[int, Query(ge=2000, le=2100)],
    klasse: Annotated[str, Query(min_length=2, max_length=2)] = "01",
    skjema: Literal["hovedskjema", "underskjema"] = "hovedskjema",
    aksjonaer_id: int | None = None,
) -> Response:
    """Download the generated RF-1086 Hovedskjema or RF-1086-U Underskjema XML.

    Builds the exact Skatteetaten XSD-compliant XML from the company's current
    aksjeeierbok for income year ``aar`` and share class code ``klasse``. Pass
    ``skjema=underskjema&aksjonaer_id={id}`` to inspect a single shareholder's
    Underskjema. Read-only; files nothing.

    Refusals: 404 ``share_class_not_found`` or ``shareholder_not_found``; 422
    ``invalid_aksjonaer_data``. Needs ``ledger:read``; a key needs ``read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    company = context.company
    classes = await service.list_share_classes(db, company)
    shareholders = await service.list_shareholders(db, company)
    computed = compute.compute_year(
        company=company,
        year=aar,
        share_classes=classes,
        shareholders=shareholders,
        contact_email=company.email or user.email,
    )
    target = next((c for c in computed.classes if c.share_class.code == klasse), None)
    if target is None:
        raise ShareClassNotFoundError(f"finner ikke aksjeklasse {klasse}")

    if skjema == "hovedskjema":
        contact = xmlwriter.ContactInfo(
            email=(company.email or user.email or "").strip() or None,
            name=user.full_name,
            phone=company.phone,
        )
        xml_text = xmlwriter.build_hovedskjema(target, company, contact)
        filename = f"RF-1086-hovedskjema-{company.orgnr}-{aar}-{klasse}.xml"
    else:
        if aksjonaer_id is None:
            raise InvalidAksjonaerDataError("oppgi aksjonaer_id for å laste ned underskjema")
        holder_state = next(
            (hs for hs in target.shareholders if hs.shareholder.id == aksjonaer_id), None
        )
        if holder_state is None:
            raise ShareholderNotFoundError(
                "finner ikke aksjonæren med beholdning eller hendelser i denne aksjeklassen"
            )
        xml_text = xmlwriter.build_underskjema(target, holder_state, company)
        filename = f"RF-1086-U-{company.orgnr}-{aar}-{klasse}-{aksjonaer_id}.xml"

    return Response(
        content=xml_text.encode("utf-8"),
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Prefill from Skatteetaten ----------------------------------------------------


@router.post(
    "/companies/{company_id}/aksjonaer/prefill",
    openapi_extra=key_send("hente fjorårets aksjonærregisteroppgave fra Skatteetaten"),
)
async def fetch_aksjonaer_prefill(
    body: PrefillBody,
    context: CurrentCompany,
    db: DbSession,
) -> PrefillResultResponse:
    """Import opening share classes and shareholders from Skatteetaten's RF-1086 prefill.

    Calls ``GET /api/aksjonaerregister/v1/prefill/{aar}/{orgnr}`` using the
    company's approved Altinn 3 systembruker, parses the returned RF-1086 and
    RF-1086-U XML documents, and upserts opening capital figures and opening
    shareholder holdings for ``aar - 1`` (01.01 of ``aar``).

    **A key's request always waits for its person** (``key_send``): 202
    ``approval_pending``.

    Refusals: 503 ``aksjonaer_submission_not_available``; 409
    ``system_user_not_approved``; 409 ``aksjonaer_prefill_not_found`` when
    Skatteetaten has no prior filing for this company; 502 ``aksjonaer_remote``.
    Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    company = context.company
    settings = get_settings()
    if not is_submission_available(settings):
        raise AksjonaerSubmissionNotAvailableError(
            "Innsending og prefill for aksjonærregisteroppgaven er ikke satt opp i denne "
            "installasjonen."
        )

    sysuser = await systemuser_service.approved_for_resource(
        db, company, systemregister.AKSJONAERREGISTER_RESOURCE
    )
    if sysuser is None:
        raise SystemUserNotApprovedError(
            "Selskapet har ikke en godkjent Altinn-systembruker for aksjonærregisteroppgaven "
            "ennå. Opprett og godkjenn systembrukeren under Innstillinger for selskapet."
        )

    try:
        token = await get_token_provider().get_access_token(
            [ske_aksjonaer.SCOPE],
            systemuser_org=company.orgnr,
            external_ref=sysuser.external_ref,
        )
        oppgave_status, docs = await ske_aksjonaer.fetch_all_prefill(
            token, year=body.aar, orgnr=company.orgnr, settings=settings
        )
    except (MaskinportenError, ske_aksjonaer.SkatteetatenAksjonaerError) as err:
        logger.error("RF-1086 prefill failed for company %s year %s", company.id, body.aar)
        detail = (
            err.user_message()
            if isinstance(err, ske_aksjonaer.SkatteetatenAksjonaerError)
            else "Klarte ikke å hente tilgangstoken fra Maskinporten for systembrukeren."
        )
        raise AksjonaerRemoteError(detail) from err

    if not docs:
        raise AksjonaerPrefillNotFoundError(
            f"Skatteetaten har ingen tidligere aksjonærregisteroppgave å hente for {body.aar}. "
            "For nystiftede selskaper legger du inn aksjeklassen og stiftelsen direkte."
        )

    parsed = xmlwriter.parse_prefill_documents(
        [(doc.aksjeklasse, doc.dokument) for doc in docs],
        for_year=body.aar,
    )
    class_count, holder_count = await service.import_prefill(db, company=company, parsed=parsed)
    return PrefillResultResponse(
        company_id=company.id,
        year=body.aar,
        oppgave_status=oppgave_status,
        share_classes_imported=class_count,
        shareholders_imported=holder_count,
    )


# --- RF-1086 submission -----------------------------------------------------------


def _failed_submission(filing: AksjonaerFiling, detail: str) -> JSONResponse:
    """Return HTTP 502 while letting DbSession commit the ``feilet`` filing row."""
    filing.status = AksjonaerFilingStatus.FEILET.value
    filing.error = detail[:1000]
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={
            "detail": detail,
            "code": "aksjonaer_remote",
            "filing": _filing_response(filing).model_dump(mode="json"),
        },
    )


@router.post(
    "/companies/{company_id}/aksjonaer/send",
    response_model=None,
    openapi_extra=key_send("sende inn aksjonærregisteroppgaven til Skatteetaten"),
)
async def send_aksjonaer(
    body: SendAksjonaerBody,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    storage: Storage,
) -> AksjonaerFilingResponse | JSONResponse:
    """File Aksjonærregisteroppgaven (RF-1086) for one share class and income year.

    Executes Skatteetaten's three-step RF-1086 API protocol with the company's
    approved Altinn 3 systembruker:

    1. ``POST /{aar}/1086H`` with the generated Hovedskjema XML and a fresh
       UUID ``idempotencyKey``.
    2. ``POST /{aar}/{hovedskjemaId}/1086U`` once per shareholder with opening
       holdings, closing holdings, or events in ``aar``.
    3. ``POST /{aar}/{hovedskjemaId}/bekreft?antall_underskjema={n}`` to finalise
       the submission and receive Skatteetaten's ``oppgavegiversLeveranseReferanse``,
       ``dialogId``, and ``forsendelseId``.

    Archives the exact submitted Hovedskjema and Underskjema XMLs in append-only
    document storage. A failure partway through is persisted as status
    ``feilet`` (with ``hovedskjema_id`` if created) and answered with 502.

    **A key's request always waits for its person** (``key_send``): 202
    ``approval_pending``, whatever the key may do on its own.

    Refusals: 422 ``aksjonaer_confirmation_required`` if ``bekreftet`` is false;
    422 ``invalid_aksjonaer_data`` if contact email is missing or multiple share
    classes exist without ``share_class_id``; 404 ``share_class_not_found``;
    409 ``aksjonaer_not_ready`` while blocking ``feil`` findings exist; 409
    ``system_user_not_approved``; 503 ``aksjonaer_submission_not_available``;
    502 ``aksjonaer_remote``. Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    company = context.company
    if not body.bekreftet:
        raise AksjonaerConfirmationRequiredError(
            "Bekreft at opplysningene i aksjonærregisteroppgaven er kontrollert før innsending."
        )

    settings = get_settings()
    if not is_submission_available(settings):
        raise AksjonaerSubmissionNotAvailableError(
            "Innsending av aksjonærregisteroppgaven er ikke satt opp i denne installasjonen."
        )

    sysuser = await systemuser_service.approved_for_resource(
        db, company, systemregister.AKSJONAERREGISTER_RESOURCE
    )
    if sysuser is None:
        raise SystemUserNotApprovedError(
            "Selskapet har ikke en godkjent Altinn-systembruker for aksjonærregisteroppgaven "
            "ennå. Opprett og godkjenn systembrukeren under Innstillinger for selskapet."
        )

    classes = await service.list_share_classes(db, company)
    shareholders = await service.list_shareholders(db, company)

    if body.share_class_id is not None:
        target_sc = next((sc for sc in classes if sc.id == body.share_class_id), None)
        if target_sc is None:
            raise ShareClassNotFoundError("finner ikke aksjeklassen")
    elif len(classes) == 1:
        target_sc = classes[0]
    elif not classes:
        raise AksjonaerNotReadyError("Selskapet har ingen aksjeklasse registrert ennå.")
    else:
        raise InvalidAksjonaerDataError(
            "Selskapet har flere aksjeklasser. Oppgi hvilken aksjeklasse som skal sendes inn."
        )

    contact_email = (body.contact_email or company.email or user.email or "").strip()
    if not contact_email:
        raise InvalidAksjonaerDataError(
            "Oppgi en kontakt-e-post før innsending av aksjonærregisteroppgaven."
        )

    ledger_cap = await service.ledger_share_capital_for_year(db, company, body.aar)
    computed = compute.compute_year(
        company=company,
        year=body.aar,
        share_classes=classes,
        shareholders=shareholders,
        contact_email=contact_email,
        ledger_share_capital=ledger_cap,
    )
    target_computed = next(c for c in computed.classes if c.share_class.id == target_sc.id)
    blocking = [
        f.message
        for f in (*computed.findings, *target_computed.findings)
        if f.severity is Severity.FEIL
    ]
    if blocking:
        raise AksjonaerNotReadyError(" ".join(blocking))

    contact = xmlwriter.ContactInfo(
        email=contact_email,
        name=(body.contact_name or user.full_name or "").strip() or None,
        role=(body.contact_role or "").strip() or None,
        phone=(body.contact_phone or company.phone or "").strip() or None,
    )
    built = xmlwriter.build_filing_xml(target_computed, company, contact)

    try:
        token = await get_token_provider().get_access_token(
            [ske_aksjonaer.SCOPE],
            systemuser_org=company.orgnr,
            external_ref=sysuser.external_ref,
        )
    except MaskinportenError as err:
        logger.error("Maskinporten system-user token failed for company %s", company.id)
        raise AksjonaerRemoteError(
            "Klarte ikke å hente systembrukertoken fra Maskinporten. Kontroller at "
            "systembrukeren fortsatt er aktiv i Altinn."
        ) from err

    hoved_key = str(uuid.uuid4())
    all_findings = [
        {"severity": f.severity.value, "code": f.code, "message": f.message}
        for f in (*computed.findings, *target_computed.findings)
    ]
    filing = await service.create_filing(
        db,
        company=company,
        share_class_id=target_sc.id,
        share_class_code=target_sc.code,
        year=body.aar,
        status=AksjonaerFilingStatus.SENDT.value,
        hovedskjema_xml=built.hovedskjema_xml,
        underskjema_count=len(built.underskjemaer),
        content_hash=built.content_hash,
        idempotency_key=hoved_key,
        contact_email=contact.email,
        contact_name=contact.name,
        findings=all_findings,
        submitted_by_user_id=user.id,
    )

    try:
        hoved_id = await ske_aksjonaer.submit_hovedskjema(
            token,
            year=body.aar,
            xml=built.hovedskjema_xml,
            idempotency_key=hoved_key,
            settings=settings,
        )
        filing.hovedskjema_id = hoved_id
        await db.flush()

        for _, _, under_xml in built.underskjemaer:
            await ske_aksjonaer.submit_underskjema(
                token,
                year=body.aar,
                hovedskjema_id=hoved_id,
                xml=under_xml,
                idempotency_key=str(uuid.uuid4()),
                settings=settings,
            )

        receipt = await ske_aksjonaer.confirm_submission(
            token,
            year=body.aar,
            hovedskjema_id=hoved_id,
            antall_underskjema=len(built.underskjemaer),
            settings=settings,
        )
    except ske_aksjonaer.SkatteetatenAksjonaerError as err:
        logger.error(
            "RF-1086 submission failed for company %s filing %s (code=%s)",
            company.id,
            filing.id,
            err.code or err.status_code,
        )
        await db.flush()
        return _failed_submission(filing, err.user_message())

    filing.status = AksjonaerFilingStatus.BEKREFTET.value
    filing.leveranse_referanse = receipt.leveranse_referanse
    filing.dialog_id = receipt.dialog_id
    filing.forsendelse_id = receipt.forsendelse_id
    await db.flush()

    # Archive the exact XMLs sent; never undo a confirmed filing if storage fails.
    try:
        async with db.begin_nested():
            await service.store_filing_document(
                db,
                company=company,
                filing=filing,
                storage=storage,
                document_type="hovedskjema",
                filename=f"RF-1086-hovedskjema-{company.orgnr}-{body.aar}-{target_sc.code}.xml",
                content_type="application/xml",
                content=built.hovedskjema_xml.encode("utf-8"),
            )
            for idx, (holder_id, _, under_xml) in enumerate(built.underskjemaer, start=1):
                await service.store_filing_document(
                    db,
                    company=company,
                    filing=filing,
                    storage=storage,
                    document_type=f"underskjema_{holder_id}",
                    filename=(f"RF-1086-U-{company.orgnr}-{body.aar}-{target_sc.code}-{idx}.xml"),
                    content_type="application/xml",
                    content=under_xml.encode("utf-8"),
                )
    except Exception:
        logger.exception("RF-1086 archive failed for confirmed filing %s", filing.id)

    reloaded = await service.get_filing(db, company, filing.id)
    return _filing_response(reloaded)


# --- Reading archived filings -----------------------------------------------------


@router.get(
    "/companies/{company_id}/aksjonaer/innsendinger/{aksjonaer_filing_id}",
    openapi_extra=KEY_READ,
)
async def get_aksjonaer_filing(
    aksjonaer_filing_id: int,
    context: CurrentCompany,
    db: DbSession,
) -> AksjonaerFilingResponse:
    """Read one RF-1086 filing attempt, its Skatteetaten receipt IDs, and archived documents.

    Refusals: 404 ``aksjonaer_filing_not_found``. Needs ``ledger:read``; a key
    needs ``read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    filing = await service.get_filing(db, context.company, aksjonaer_filing_id)
    return _filing_response(filing)


@router.get(
    "/companies/{company_id}/aksjonaer/innsendinger/{aksjonaer_filing_id}/dokument",
    openapi_extra=KEY_READ,
)
async def get_aksjonaer_filing_document(
    aksjonaer_filing_id: int,
    context: CurrentCompany,
    db: DbSession,
    storage: Storage,
    document_type: Annotated[str, Query(alias="type", max_length=64)] = "hovedskjema",
) -> Response:
    """Download an archived XML or receipt document from a submitted RF-1086 filing.

    ``type`` is ``hovedskjema`` or any ``document_type`` listed on the filing's
    ``documents`` (such as ``underskjema_{shareholder_id}``).

    Refusals: 404 ``aksjonaer_filing_not_found`` or
    ``aksjonaer_document_not_found``. Needs ``ledger:read``; a key needs ``read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    filing = await service.get_filing(db, context.company, aksjonaer_filing_id)
    stored = next((d for d in filing.documents if d.document_type == document_type), None)
    if stored is not None:
        return Response(
            content=await storage.load(stored.storage_key),
            media_type=stored.content_type,
            headers={"Content-Disposition": f'attachment; filename="{stored.filename}"'},
        )
    if document_type == "hovedskjema":
        filename = (
            f"RF-1086-hovedskjema-{context.company.orgnr}-{filing.year}"
            f"-{filing.share_class_code}-{filing.id}.xml"
        )
        return Response(
            content=filing.hovedskjema_xml.encode("utf-8"),
            media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    # Trigger domain 404 when neither archived nor built-in hovedskjema.
    await service.get_filing_document(
        db, company=context.company, filing_id=aksjonaer_filing_id, document_type=document_type
    )
    return Response(status_code=status.HTTP_404_NOT_FOUND)
