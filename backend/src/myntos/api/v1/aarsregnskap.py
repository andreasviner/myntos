"""Årsregnskap til Regnskapsregisteret (Brønnøysundregistrene, RR-0002) API routes.

Supports both submission paths allowed by Brønnøysundregistrene's Altinn 3 app
``brg/aarsregnskap-vanlig-202406``:

1. **Systembruker + ID-porten signing (two-step):**
   - ``POST /companies/{company_id}/aarsregnskap/klargjor`` prepares, uploads
     Hovedskjema + Underskjema + optional PDF attachment, validates, and locks
     the instance into ``Signering`` using the company's approved Altinn 3
     systembruker.
   - ``POST /companies/{company_id}/aarsregnskap/innsendinger/{aarsregnskap_filing_id}/signer``
     signs and auto-submits the locked instance using a board member or
     representative's own ID-porten login (legally attaching a fødselsnummer),
     then downloads and archives ``ref-data-as-pdf`` for five-year retention.
2. **Direct ID-porten flow (no systembruker required):**
   - ``POST /companies/{company_id}/aarsregnskap/send`` prepares, validates, and
     locks the instance with the person's ID-porten login (and optionally signs
     and submits immediately when ``signer_direkte`` is true).

Every external mutation uses ``key_send`` so agent keys always hold for
explicit human approval (``docs/agents-and-api.md`` rule 5). Partial remote
failures return 502 via ``JSONResponse`` instead of raising, so the ``feilet``
row carrying the live ``altinn_instance_id`` is committed rather than rolled
back.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from myntos.aarsregnskap import pdf as aarsregnskap_pdf
from myntos.aarsregnskap import service as aarsregnskap_service
from myntos.aarsregnskap import xmlwriter
from myntos.aarsregnskap.compute import ComputedAarsregnskap, StatementLine
from myntos.aarsregnskap.errors import (
    AarsregnskapChangedError,
    AarsregnskapConfirmationRequiredError,
    AarsregnskapNotReadyError,
    AarsregnskapNotSubmittableError,
    AarsregnskapSubmissionNotAvailableError,
    InvalidAarsregnskapDataError,
)
from myntos.aarsregnskap.models import (
    AarsregnskapFiling,
    AarsregnskapFilingDocument,
    AarsregnskapFilingStatus,
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
from myntos.api.v1.skatt import government_login_for, has_usable_login
from myntos.api.v1.systembruker import is_systemuser_available
from myntos.auth.idporten.client import is_configured as idporten_is_configured
from myntos.auth.idporten.vault import is_configured as vault_is_configured
from myntos.auth.maskinporten.client import MaskinportenError
from myntos.core.clock import utc_now
from myntos.core.config import get_settings
from myntos.domain.altinn import service as systemuser_service
from myntos.domain.altinn.errors import SystemUserNotApprovedError
from myntos.domain.arsoppgjor.findings import Finding
from myntos.domain.identity.access import Permission, require_permission
from myntos.integrations.aarsregnskap import (
    AarsregnskapClient,
    AarsregnskapClientError,
    has_blocking_validation_errors,
)
from myntos.integrations.altinn import systemregister
from myntos.integrations.altinn.instances import AltinnInstanceError, InstanceRef
from myntos.integrations.altinn.tokens import (
    AltinnError,
    exchange_id_porten_token,
    machine_tokens,
)

router = APIRouter(tags=["aarsregnskap"])
logger = logging.getLogger(__name__)

_OSLO = ZoneInfo("Europe/Oslo")


def _today_norway() -> date:
    return utc_now().astimezone(_OSLO).date()


def _default_year() -> int:
    return _today_norway().year - 1


# --- Response schemas -------------------------------------------------------


class FindingResponse(BaseModel):
    severity: str
    code: str
    message: str


class StatementLineResponse(BaseModel):
    key: str
    label: str
    kind: str
    section: str
    orid_current: int | None
    orid_prior: int | None
    current: int
    prior: int | None
    note_ref: str | None
    editable_prior: bool


class ShareholderSummaryResponse(BaseModel):
    name: str
    shares: int
    ownership_percent: Decimal
    share_class: str


class FixedAssetNoteResponse(BaseModel):
    tangible_opening_cost: int
    tangible_additions: int
    tangible_disposals: int
    tangible_closing_cost: int
    tangible_year_depreciation: int
    tangible_accumulated_depreciation: int
    tangible_carrying_value: int
    intangible_opening_cost: int
    intangible_additions: int
    intangible_disposals: int
    intangible_closing_cost: int
    intangible_year_depreciation: int
    intangible_accumulated_depreciation: int
    intangible_carrying_value: int


class ResolvedNotesResponse(BaseModel):
    regnskapsprinsipper: str
    usikkerhet_fortsatt_drift: bool
    usikkerhet_fortsatt_drift_tekst: str
    antall_aarsverk: Decimal
    har_otp_plikt: bool
    otp_tekst: str
    lonn_aarets: int
    lonn_fjoraarets: int | None
    arbeidsgiveravgift_aarets: int
    arbeidsgiveravgift_fjoraarets: int | None
    pensjon_aarets: int
    pensjon_fjoraarets: int | None
    andre_ytelser_aarets: int
    andre_ytelser_fjoraarets: int | None
    sum_lonnskostnad_aarets: int
    sum_lonnskostnad_fjoraarets: int | None
    lonnskostnad_fritekst: str
    aksjekapital_tekst: str
    antall_aksjer: int | None
    paalydende: Decimal | None
    aksjonaerer: list[ShareholderSummaryResponse]
    egne_aksjer_antall: int
    anleggsmidler: FixedAssetNoteResponse
    anleggsmidler_fritekst: str
    bundne_bankinnskudd: int
    gjeld_over_fem_aar: int
    pantstillelser_gjeld: int
    pantsatte_eiendeler_verdi: int
    garantiforpliktelser: int
    gjeld_fritekst: str
    laan_til_styret: int
    laan_til_daglig_leder: int
    sikkerhet_styret: int
    sikkerhet_daglig_leder: int
    laan_rentesats_styret: Decimal | None
    laan_rentesats_daglig_leder: Decimal | None
    laan_vilkaar_styret: str
    laan_vilkaar_daglig_leder: str
    laan_fritekst: str
    egenkapital_ib: int
    egenkapital_aarsresultat: int
    egenkapital_utbytte: int
    egenkapital_andre_endringer: int
    egenkapital_ub: int
    skatt_tekst: str
    ytterligere_opplysninger: str
    egendefinerte_noter: list[dict[str, str]]


class DraftSettingsResponse(BaseModel):
    small_company: bool
    is_parent: bool
    consolidated: bool
    winding_up_accounts: bool
    auditor_elected_out: bool
    prepared_by_external_accountant: bool
    external_accountant_assistance: bool
    approval_date: date | None
    confirming_representative: str | None
    contact_email: str | None
    board_meeting_date: date | None
    board_place: str | None
    board_members: list[dict[str, Any]]
    general_meeting_date: date | None
    general_meeting_place: str | None
    general_meeting_chair: str | None
    general_meeting_cosigner: str | None
    dividend_proposed: Decimal
    prior_year_overrides: dict[str, int]


class FilingDocumentResponse(BaseModel):
    id: int
    document_type: str
    filename: str
    content_type: str
    byte_size: int
    sha256: str
    fetched_at: datetime


class AarsregnskapFilingResponse(BaseModel):
    id: int
    year: int
    status: str
    auth_method: str
    altinn_instance_id: str | None
    hovedskjema_data_id: str | None
    underskjema_data_id: str | None
    altinn_portal_url: str | None
    content_hash: str
    stale: bool
    aarsresultat: Decimal
    sum_eiendeler: Decimal
    sum_egenkapital: Decimal
    fastsettelsedato: date | None
    bekreftende_representant: str | None
    validation_issues: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    signed_by_name: str | None
    created_at: datetime
    locked_at: datetime | None
    signed_at: datetime | None
    receipt_received_at: datetime | None
    error: str | None
    documents: list[FilingDocumentResponse]


class AarsregnskapOverviewResponse(BaseModel):
    company_id: int
    orgnr: str
    company_name: str
    organisation_form: str | None
    year: int
    start_date: date
    end_date: date
    year_closed: bool
    has_prior_year_in_books: bool
    uses_prior_year_overrides: bool
    driftsinntekter: int
    driftsresultat: int
    resultat_for_skatt: int
    skattekostnad: int
    aarsresultat: int
    sum_anleggsmidler: int
    sum_omlopsmidler: int
    sum_eiendeler: int
    sum_innskutt_egenkapital: int
    sum_opptjent_egenkapital: int
    sum_egenkapital: int
    sum_langsiktig_gjeld: int
    sum_kortsiktig_gjeld: int
    sum_gjeld: int
    sum_egenkapital_og_gjeld: int
    balance_diff: int
    rounding_adjustment: int
    content_hash: str
    result_lines: list[StatementLineResponse]
    disposition_lines: list[StatementLineResponse]
    balance_asset_lines: list[StatementLineResponse]
    balance_equity_liability_lines: list[StatementLineResponse]
    off_balance_lines: list[StatementLineResponse]
    draft: DraftSettingsResponse
    notes: ResolvedNotesResponse
    findings: list[FindingResponse]
    blocked: bool
    submission_available: bool
    systembruker_available: bool
    systembruker_approved: bool
    id_porten_available: bool
    id_porten_logged_in: bool
    filings: list[AarsregnskapFilingResponse]


# --- Request payloads -------------------------------------------------------


class BoardMemberInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(default="Styremedlem", max_length=60)


class SaveDraftPayload(BaseModel):
    aar: int = Field(ge=1980, le=2100)
    small_company: bool | None = None
    is_parent: bool | None = None
    consolidated: bool | None = None
    winding_up_accounts: bool | None = None
    auditor_elected_out: bool | None = None
    prepared_by_external_accountant: bool | None = None
    external_accountant_assistance: bool | None = None
    approval_date: date | None = None
    confirming_representative: str | None = Field(default=None, max_length=70)
    contact_email: str | None = Field(default=None, max_length=70)
    board_meeting_date: date | None = None
    board_place: str | None = Field(default=None, max_length=120)
    board_members: list[BoardMemberInput] | None = None
    general_meeting_date: date | None = None
    general_meeting_place: str | None = Field(default=None, max_length=120)
    general_meeting_chair: str | None = Field(default=None, max_length=120)
    general_meeting_cosigner: str | None = Field(default=None, max_length=120)
    dividend_proposed: Decimal | None = Field(default=None, ge=Decimal("0"))
    prior_year_overrides: dict[str, int | None] | None = None
    notes: dict[str, Any] | None = None


class PreparePayload(BaseModel):
    aar: int = Field(ge=1980, le=2100)
    aarsresultat: int
    sum_eiendeler: int
    bekreftet: bool
    laas: bool = True
    legg_ved_pdf: bool = True


class SendWithIdPortenPayload(BaseModel):
    aar: int = Field(ge=1980, le=2100)
    aarsresultat: int
    sum_eiendeler: int
    bekreftet: bool
    laas: bool = True
    signer_direkte: bool = False
    legg_ved_pdf: bool = True


class SignFilingPayload(BaseModel):
    bekreftet: bool
    sjekk_endringer: bool = True


# --- Serializers ------------------------------------------------------------


def _line_resp(line: StatementLine) -> StatementLineResponse:
    return StatementLineResponse(
        key=line.key,
        label=line.label,
        kind=line.kind,
        section=line.section,
        orid_current=line.orid_current,
        orid_prior=line.orid_prior,
        current=line.current,
        prior=line.prior,
        note_ref=line.note_ref,
        editable_prior=line.editable_prior,
    )


def _finding_dict(finding: Finding) -> dict[str, Any]:
    return {
        "severity": finding.severity.value,
        "code": finding.code,
        "message": finding.message,
    }


def _doc_resp(doc: AarsregnskapFilingDocument) -> FilingDocumentResponse:
    return FilingDocumentResponse(
        id=doc.id,
        document_type=doc.document_type,
        filename=doc.filename,
        content_type=doc.content_type,
        byte_size=doc.byte_size,
        sha256=doc.sha256,
        fetched_at=doc.fetched_at,
    )


def _filing_resp(
    filing: AarsregnskapFiling, *, current_hash: str | None = None
) -> AarsregnskapFilingResponse:
    return AarsregnskapFilingResponse(
        id=filing.id,
        year=filing.year,
        status=filing.status,
        auth_method=filing.auth_method,
        altinn_instance_id=filing.altinn_instance_id,
        hovedskjema_data_id=filing.hovedskjema_data_id,
        underskjema_data_id=filing.underskjema_data_id,
        altinn_portal_url=filing.altinn_portal_url,
        content_hash=filing.content_hash,
        stale=current_hash is not None and filing.content_hash != current_hash,
        aarsresultat=filing.aarsresultat,
        sum_eiendeler=filing.sum_eiendeler,
        sum_egenkapital=filing.sum_egenkapital,
        fastsettelsedato=filing.fastsettelsedato,
        bekreftende_representant=filing.bekreftende_representant,
        validation_issues=list(filing.validation_issues or []),
        findings=list(filing.findings or []),
        signed_by_name=filing.signed_by_name,
        created_at=filing.created_at,
        locked_at=filing.locked_at,
        signed_at=filing.signed_at,
        receipt_received_at=filing.receipt_received_at,
        error=filing.error,
        documents=[_doc_resp(d) for d in filing.documents],
    )


async def _overview_response(
    db: DbSession,
    context: CurrentCompany,
    user: CurrentUser,
    computed: ComputedAarsregnskap,
) -> AarsregnskapOverviewResponse:
    settings = get_settings()
    app_url = (settings.altinn_aarsregnskap_app_url or "").strip()
    sys_available = is_systemuser_available(settings)
    sys_approved = (
        await systemuser_service.approved_for_resource(
            db, context.company, systemregister.AARSREGNSKAP_RESOURCE
        )
        is not None
    )
    idp_available = idporten_is_configured() and vault_is_configured()
    idp_logged_in = await has_usable_login(db, user) if idp_available else False
    filings = await aarsregnskap_service.list_filings(db, context.company, year=computed.year)

    n = computed.notes
    d = computed.draft
    fa = n.anleggsmidler
    return AarsregnskapOverviewResponse(
        company_id=context.company.id,
        orgnr=context.company.orgnr,
        company_name=context.company.name,
        organisation_form=computed.organisation_form,
        year=computed.year,
        start_date=computed.start_date,
        end_date=computed.end_date,
        year_closed=computed.year_closed,
        has_prior_year_in_books=computed.has_prior_year_in_books,
        uses_prior_year_overrides=computed.uses_prior_year_overrides,
        driftsinntekter=computed.driftsinntekter,
        driftsresultat=computed.driftsresultat,
        resultat_for_skatt=computed.resultat_for_skatt,
        skattekostnad=computed.skattekostnad,
        aarsresultat=computed.aarsresultat,
        sum_anleggsmidler=computed.sum_anleggsmidler,
        sum_omlopsmidler=computed.sum_omlopsmidler,
        sum_eiendeler=computed.sum_eiendeler,
        sum_innskutt_egenkapital=computed.sum_innskutt_egenkapital,
        sum_opptjent_egenkapital=computed.sum_opptjent_egenkapital,
        sum_egenkapital=computed.sum_egenkapital,
        sum_langsiktig_gjeld=computed.sum_langsiktig_gjeld,
        sum_kortsiktig_gjeld=computed.sum_kortsiktig_gjeld,
        sum_gjeld=computed.sum_gjeld,
        sum_egenkapital_og_gjeld=computed.sum_egenkapital_og_gjeld,
        balance_diff=computed.balance_diff,
        rounding_adjustment=computed.rounding_adjustment,
        content_hash=computed.content_hash,
        result_lines=[_line_resp(r) for r in computed.result_lines],
        disposition_lines=[_line_resp(r) for r in computed.disposition_lines],
        balance_asset_lines=[_line_resp(r) for r in computed.balance_asset_lines],
        balance_equity_liability_lines=[
            _line_resp(r) for r in computed.balance_equity_liability_lines
        ],
        off_balance_lines=[_line_resp(r) for r in computed.off_balance_lines],
        draft=DraftSettingsResponse(
            small_company=d.small_company,
            is_parent=d.is_parent,
            consolidated=d.consolidated,
            winding_up_accounts=d.winding_up_accounts,
            auditor_elected_out=d.auditor_elected_out,
            prepared_by_external_accountant=d.prepared_by_external_accountant,
            external_accountant_assistance=d.external_accountant_assistance,
            approval_date=d.approval_date,
            confirming_representative=d.confirming_representative,
            contact_email=d.contact_email,
            board_meeting_date=d.board_meeting_date,
            board_place=d.board_place,
            board_members=list(d.board_members),
            general_meeting_date=d.general_meeting_date,
            general_meeting_place=d.general_meeting_place,
            general_meeting_chair=d.general_meeting_chair,
            general_meeting_cosigner=d.general_meeting_cosigner,
            dividend_proposed=d.dividend_proposed,
            prior_year_overrides=dict(d.prior_year_overrides),
        ),
        notes=ResolvedNotesResponse(
            regnskapsprinsipper=n.regnskapsprinsipper,
            usikkerhet_fortsatt_drift=n.usikkerhet_fortsatt_drift,
            usikkerhet_fortsatt_drift_tekst=n.usikkerhet_fortsatt_drift_tekst,
            antall_aarsverk=n.antall_aarsverk,
            har_otp_plikt=n.har_otp_plikt,
            otp_tekst=n.otp_tekst,
            lonn_aarets=n.lonn_aarets,
            lonn_fjoraarets=n.lonn_fjoraarets,
            arbeidsgiveravgift_aarets=n.arbeidsgiveravgift_aarets,
            arbeidsgiveravgift_fjoraarets=n.arbeidsgiveravgift_fjoraarets,
            pensjon_aarets=n.pensjon_aarets,
            pensjon_fjoraarets=n.pensjon_fjoraarets,
            andre_ytelser_aarets=n.andre_ytelser_aarets,
            andre_ytelser_fjoraarets=n.andre_ytelser_fjoraarets,
            sum_lonnskostnad_aarets=n.sum_lonnskostnad_aarets,
            sum_lonnskostnad_fjoraarets=n.sum_lonnskostnad_fjoraarets,
            lonnskostnad_fritekst=n.lonnskostnad_fritekst,
            aksjekapital_tekst=n.aksjekapital_tekst,
            antall_aksjer=n.antall_aksjer,
            paalydende=n.paalydende,
            aksjonaerer=[
                ShareholderSummaryResponse(
                    name=s.name,
                    shares=s.shares,
                    ownership_percent=s.ownership_percent,
                    share_class=s.share_class,
                )
                for s in n.aksjonaerer
            ],
            egne_aksjer_antall=n.egne_aksjer_antall,
            anleggsmidler=FixedAssetNoteResponse(
                tangible_opening_cost=fa.tangible_opening_cost,
                tangible_additions=fa.tangible_additions,
                tangible_disposals=fa.tangible_disposals,
                tangible_closing_cost=fa.tangible_closing_cost,
                tangible_year_depreciation=fa.tangible_year_depreciation,
                tangible_accumulated_depreciation=fa.tangible_accumulated_depreciation,
                tangible_carrying_value=fa.tangible_carrying_value,
                intangible_opening_cost=fa.intangible_opening_cost,
                intangible_additions=fa.intangible_additions,
                intangible_disposals=fa.intangible_disposals,
                intangible_closing_cost=fa.intangible_closing_cost,
                intangible_year_depreciation=fa.intangible_year_depreciation,
                intangible_accumulated_depreciation=fa.intangible_accumulated_depreciation,
                intangible_carrying_value=fa.intangible_carrying_value,
            ),
            anleggsmidler_fritekst=n.anleggsmidler_fritekst,
            bundne_bankinnskudd=n.bundne_bankinnskudd,
            gjeld_over_fem_aar=n.gjeld_over_fem_aar,
            pantstillelser_gjeld=n.pantstillelser_gjeld,
            pantsatte_eiendeler_verdi=n.pantsatte_eiendeler_verdi,
            garantiforpliktelser=n.garantiforpliktelser,
            gjeld_fritekst=n.gjeld_fritekst,
            laan_til_styret=n.laan_til_styret,
            laan_til_daglig_leder=n.laan_til_daglig_leder,
            sikkerhet_styret=n.sikkerhet_styret,
            sikkerhet_daglig_leder=n.sikkerhet_daglig_leder,
            laan_rentesats_styret=n.laan_rentesats_styret,
            laan_rentesats_daglig_leder=n.laan_rentesats_daglig_leder,
            laan_vilkaar_styret=n.laan_vilkaar_styret,
            laan_vilkaar_daglig_leder=n.laan_vilkaar_daglig_leder,
            laan_fritekst=n.laan_fritekst,
            egenkapital_ib=n.egenkapital_ib,
            egenkapital_aarsresultat=n.egenkapital_aarsresultat,
            egenkapital_utbytte=n.egenkapital_utbytte,
            egenkapital_andre_endringer=n.egenkapital_andre_endringer,
            egenkapital_ub=n.egenkapital_ub,
            skatt_tekst=n.skatt_tekst,
            ytterligere_opplysninger=n.ytterligere_opplysninger,
            egendefinerte_noter=list(n.egendefinerte_noter),
        ),
        findings=[
            FindingResponse(severity=f.severity.value, code=f.code, message=f.message)
            for f in computed.findings
        ],
        blocked=computed.blocked,
        submission_available=bool(app_url),
        systembruker_available=sys_available,
        systembruker_approved=sys_approved,
        id_porten_available=idp_available,
        id_porten_logged_in=idp_logged_in,
        filings=[_filing_resp(f, current_hash=computed.content_hash) for f in filings],
    )


def _require_app_url() -> str:
    app_url = (get_settings().altinn_aarsregnskap_app_url or "").strip()
    if not app_url:
        raise AarsregnskapSubmissionNotAvailableError(
            "Innsending av årsregnskap til Altinn er ikke satt opp i denne installasjonen "
            "(ALTINN_AARSREGNSKAP_APP_URL mangler)."
        )
    return app_url


def _require_ready_and_confirmed(
    computed: ComputedAarsregnskap,
    *,
    bekreftet: bool,
    aarsresultat: int,
    sum_eiendeler: int,
) -> None:
    if not bekreftet:
        raise AarsregnskapConfirmationRequiredError(
            "Du må bekrefte at årsregnskapet og notene er kontrollert før innsending."
        )
    if computed.blocked:
        first_err = next((f.message for f in computed.findings if f.severity.value == "feil"), "")
        raise AarsregnskapNotReadyError(
            f"Årsregnskapet har feil som må rettes før innsending: {first_err}"
        )
    if computed.aarsresultat != aarsresultat or computed.sum_eiendeler != sum_eiendeler:
        raise AarsregnskapChangedError(
            "Regnskapet eller utkastet har endret seg siden tallene ble vist. Last siden på "
            "nytt og kontroller tallene før innsending."
        )


def _client_for(app_url: str) -> AarsregnskapClient:
    """Seam for tests to inject a stub Altinn 3 client."""
    return AarsregnskapClient(app_url)


# --- Routes -----------------------------------------------------------------


@router.get(
    "/companies/{company_id}/aarsregnskap",
    openapi_extra=KEY_READ,
)
async def get_aarsregnskap(
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    aar: int | None = Query(default=None, ge=1980, le=2100),
) -> AarsregnskapOverviewResponse:
    """Read the computed annual accounts (årsregnskap, RR-0002) for a financial year.

    Computes resultatregnskap, disponeringer, and balanse (with comparative
    prior-year figures from the books or manual overrides), resolves mandatory
    small-company notes (regnskapsloven § 7-35 ff.) using linked shareholder
    and fixed-asset registers, runs pre-flight compliance checks, and lists
    past Altinn 3 filings for the year.

    Needs ``ledger:read``. Read-only; does not contact Altinn.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    year = aar if aar is not None else _default_year()
    computed = await aarsregnskap_service.build_computed(
        db, context.company, year, today=_today_norway()
    )
    return await _overview_response(db, context, user, computed)


@router.put(
    "/companies/{company_id}/aarsregnskap",
    openapi_extra=key_write("oppdatere utkast og noter for årsregnskapet"),
)
async def save_aarsregnskap_draft(
    payload: SaveDraftPayload,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
) -> AarsregnskapOverviewResponse:
    """Save editable annual accounts settings, prior-year figures, notes, and minutes data.

    Persists RR-0002 Hovedskjema metadata (``small_company``, ``is_parent``,
    ``auditor_elected_out``, ``approval_date``, ``confirming_representative``,
    ``contact_email``), board and general-meeting minutes details, optional
    manual prior-year comparative figures (``prior_year_overrides``), and
    structured small-company notes plus custom notes (``notes``).

    Does not touch the general ledger or contact Altinn. Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    raw_payload = payload.model_dump(exclude_unset=True)
    year = int(raw_payload.pop("aar"))
    if "board_members" in raw_payload and raw_payload["board_members"] is not None:
        raw_payload["board_members"] = [
            {"name": m.name, "role": m.role} for m in (payload.board_members or [])
        ]
    await aarsregnskap_service.upsert_draft(
        db,
        context.company,
        year,
        user_id=user.id,
        payload=raw_payload,
    )
    computed = await aarsregnskap_service.build_computed(
        db, context.company, year, today=_today_norway()
    )
    return await _overview_response(db, context, user, computed)


@router.get(
    "/companies/{company_id}/aarsregnskap/xml",
    openapi_extra=KEY_READ,
)
async def preview_aarsregnskap_xml(
    context: CurrentCompany,
    db: DbSession,
    aar: int | None = Query(default=None, ge=1980, le=2100),
    del_skjema: Literal["hovedskjema", "underskjema"] = Query(default="underskjema", alias="del"),
) -> Response:
    """Preview or download the generated RR-0002 Hovedskjema or RR-0002-U Underskjema XML.

    Returns ``application/xml`` matching Brønnøysundregistrene's official
    ``brg/aarsregnskap-vanlig-202406`` schemas. Needs ``ledger:read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    year = aar if aar is not None else _default_year()
    computed = await aarsregnskap_service.build_computed(
        db, context.company, year, today=_today_norway()
    )
    if del_skjema == "hovedskjema":
        xml_str = xmlwriter.render_hovedskjema(context.company, computed)
        filename = f"aarsregnskap-{year}-hovedskjema.xml"
    else:
        xml_str = xmlwriter.render_underskjema(computed)
        filename = f"aarsregnskap-{year}-selskapsregnskap.xml"
    return Response(
        content=xml_str.encode("utf-8"),
        media_type="application/xml",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get(
    "/companies/{company_id}/aarsregnskap/pdf",
    openapi_extra=KEY_READ,
)
async def download_aarsregnskap_pdf(
    context: CurrentCompany,
    db: DbSession,
    aar: int | None = Query(default=None, ge=1980, le=2100),
    dokument: Literal["aarsregnskap", "styreprotokoll", "generalforsamling"] = Query(
        default="aarsregnskap"
    ),
) -> Response:
    """Download a printable PDF for annual accounts, board minutes, or general meeting minutes.

    ``dokument=aarsregnskap`` renders the full årsregnskap (resultatregnskap,
    balanse with comparative prior year, all mandatory and custom notes, and
    board signature lines). ``styreprotokoll`` and ``generalforsamling`` render
    pre-filled minutes templates under aksjeloven § 6-29 and § 5-16.

    Needs ``ledger:read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    year = aar if aar is not None else _default_year()
    computed = await aarsregnskap_service.build_computed(
        db, context.company, year, today=_today_norway()
    )
    if dokument == "styreprotokoll":
        content = aarsregnskap_pdf.render_styreprotokoll_pdf(context.company, computed)
        filename = f"styreprotokoll-{year}.pdf"
    elif dokument == "generalforsamling":
        content = aarsregnskap_pdf.render_generalforsamling_pdf(context.company, computed)
        filename = f"generalforsamlingsprotokoll-{year}.pdf"
    else:
        content = aarsregnskap_pdf.render_aarsregnskap_pdf(context.company, computed)
        filename = f"aarsregnskap-{year}.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post(
    "/companies/{company_id}/aarsregnskap/klargjor",
    openapi_extra=key_send("klargjøre årsregnskapet i Altinn med systembruker"),
    response_model=AarsregnskapFilingResponse,
)
async def prepare_with_systembruker(
    payload: PreparePayload,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    storage: Storage,
) -> AarsregnskapFilingResponse | JSONResponse:
    """Create, fill, validate, and lock the årsregnskap in Altinn 3 via systembruker.

    **A key's request always waits for its person** (``key_send``): 202
    ``approval_pending``. Requires an approved Altinn 3 systembruker covering
    ``app_brg_aarsregnskap-vanlig-202406``.

    Uploads RR-0002 Hovedskjema XML, RR-0002-U Underskjema XML, and (when
    ``legg_ved_pdf`` is true) the rendered annual accounts PDF as ``Vedlegg``,
    runs Brønnøysundregistrene's ``/validate``, and moves the instance to
    ``Signering`` via ``{"action": "confirm"}`` when ``laas`` is true and no
    blocking validation errors were returned. A board member or representative
    then signs and submits using ID-porten (either via ``/signer`` or directly
    in Altinn).

    On remote failure after contacting Altinn, persists a ``feilet`` filing row
    carrying the Altinn instance id and returns 502 instead of rolling back.
    Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    app_url = _require_app_url()
    settings = get_settings()
    if not is_systemuser_available(settings):
        raise AarsregnskapSubmissionNotAvailableError(
            "Altinn-systembruker er ikke satt opp i denne installasjonen."
        )

    computed = await aarsregnskap_service.build_computed(
        db, context.company, payload.aar, today=_today_norway()
    )
    _require_ready_and_confirmed(
        computed,
        bekreftet=payload.bekreftet,
        aarsresultat=payload.aarsresultat,
        sum_eiendeler=payload.sum_eiendeler,
    )

    sys_user = await systemuser_service.approved_for_resource(
        db, context.company, systemregister.AARSREGNSKAP_RESOURCE
    )
    if sys_user is None:
        raise SystemUserNotApprovedError(
            "Selskapet har ingen godkjent Altinn-systembruker med tilgang til årsregnskap "
            "(app_brg_aarsregnskap-vanlig-202406). Godkjenn systembruker under Innstillinger "
            "eller bruk ID-porten-innsending i stedet."
        )

    hoved_xml = xmlwriter.render_hovedskjema(context.company, computed)
    under_xml = xmlwriter.render_underskjema(computed)
    pdf_bytes = (
        aarsregnskap_pdf.render_aarsregnskap_pdf(context.company, computed)
        if payload.legg_ved_pdf
        else None
    )
    attachments = (
        [(f"aarsregnskap-{payload.aar}.pdf", pdf_bytes, "application/pdf")]
        if pdf_bytes is not None
        else None
    )
    findings_json = [_finding_dict(f) for f in computed.findings]

    try:
        altinn_token = await machine_tokens().get_token(
            systemuser_org=context.company.orgnr,
            external_ref=sys_user.external_ref,
        )
        prepared = await _client_for(app_url).prepare_and_lock(
            organisation_number=context.company.orgnr,
            hovedskjema_xml=hoved_xml,
            underskjema_xml=under_xml,
            token=altinn_token,
            attachments=attachments,
            lock=payload.laas,
        )
    except (AarsregnskapClientError, AltinnError, MaskinportenError) as err:
        logger.error("Årsregnskap systembruker prepare failed for company %s", context.company.id)
        instance_id = err.instance_id if isinstance(err, AarsregnskapClientError) else None
        hoved_id = err.hovedskjema_data_id if isinstance(err, AarsregnskapClientError) else None
        under_id = err.underskjema_data_id if isinstance(err, AarsregnskapClientError) else None
        portal_url = err.portal_url if isinstance(err, AarsregnskapClientError) else None
        val_issues = err.validation_issues if isinstance(err, AarsregnskapClientError) else []
        failed = await aarsregnskap_service.create_filing(
            db,
            company=context.company,
            year=payload.aar,
            status=AarsregnskapFilingStatus.FEILET.value,
            auth_method="systembruker",
            altinn_instance_id=instance_id,
            hovedskjema_data_id=hoved_id,
            underskjema_data_id=under_id,
            altinn_portal_url=portal_url,
            content_hash=computed.content_hash,
            aarsresultat=Decimal(computed.aarsresultat),
            sum_eiendeler=Decimal(computed.sum_eiendeler),
            sum_egenkapital=Decimal(computed.sum_egenkapital),
            fastsettelsedato=computed.draft.approval_date,
            bekreftende_representant=computed.draft.confirming_representative,
            hovedskjema_xml=hoved_xml,
            underskjema_xml=under_xml,
            validation_issues=val_issues,
            findings=findings_json,
            created_by_user_id=user.id,
            error=str(err),
        )
        return JSONResponse(
            status_code=502,
            content={
                "detail": (
                    "Klarte ikke å klargjøre årsregnskapet i Altinn. "
                    "Kontroller feilen og prøv igjen."
                ),
                "code": "aarsregnskap_remote",
                "filing_id": failed.id,
                "altinn_instance_id": failed.altinn_instance_id,
            },
        )

    now = utc_now()
    filing_status = (
        AarsregnskapFilingStatus.LAAST.value
        if prepared.locked
        else AarsregnskapFilingStatus.KLARGJORT.value
    )
    filing = await aarsregnskap_service.create_filing(
        db,
        company=context.company,
        year=payload.aar,
        status=filing_status,
        auth_method="systembruker",
        altinn_instance_id=prepared.ref.path,
        hovedskjema_data_id=prepared.hovedskjema_data_id,
        underskjema_data_id=prepared.underskjema_data_id,
        altinn_portal_url=prepared.portal_url,
        content_hash=computed.content_hash,
        aarsresultat=Decimal(computed.aarsresultat),
        sum_eiendeler=Decimal(computed.sum_eiendeler),
        sum_egenkapital=Decimal(computed.sum_egenkapital),
        fastsettelsedato=computed.draft.approval_date,
        bekreftende_representant=computed.draft.confirming_representative,
        hovedskjema_xml=hoved_xml,
        underskjema_xml=under_xml,
        validation_issues=prepared.validation_issues,
        findings=findings_json,
        created_by_user_id=user.id,
        locked_at=now if prepared.locked else None,
    )
    await aarsregnskap_service.archive_document(
        db,
        storage,
        company=context.company,
        filing=filing,
        document_type="hovedskjema",
        filename=f"aarsregnskap-{payload.aar}-hovedskjema.xml",
        content_type="application/xml",
        content=hoved_xml.encode("utf-8"),
        external_document_id=prepared.hovedskjema_data_id,
    )
    await aarsregnskap_service.archive_document(
        db,
        storage,
        company=context.company,
        filing=filing,
        document_type="underskjema",
        filename=f"aarsregnskap-{payload.aar}-selskapsregnskap.xml",
        content_type="application/xml",
        content=under_xml.encode("utf-8"),
        external_document_id=prepared.underskjema_data_id,
    )
    if pdf_bytes is not None:
        await aarsregnskap_service.archive_document(
            db,
            storage,
            company=context.company,
            filing=filing,
            document_type="vedlegg_pdf",
            filename=f"aarsregnskap-{payload.aar}.pdf",
            content_type="application/pdf",
            content=pdf_bytes,
        )
    filing = await aarsregnskap_service.get_filing(db, context.company, filing.id)
    return _filing_resp(filing, current_hash=computed.content_hash)


@router.post(
    "/companies/{company_id}/aarsregnskap/send",
    openapi_extra=key_send(
        "klargjøre og sende årsregnskapet i Altinn med ID-porten", id_porten=True
    ),
    response_model=AarsregnskapFilingResponse,
)
async def prepare_or_submit_with_idporten(
    payload: SendWithIdPortenPayload,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    storage: Storage,
) -> AarsregnskapFilingResponse | JSONResponse:
    """Create, fill, validate, lock, and optionally sign the årsregnskap via ID-porten.

    **A key's request always waits for its person** (``key_send``,
    ``id_porten=True``): 202 ``approval_pending``. Use this route when the
    company does not have an approved Altinn 3 systembruker, or when a board
    member wants to prepare and sign in a single ID-porten session
    (``signer_direkte=true``).

    On remote failure, persists a ``feilet`` filing row with any created
    ``altinn_instance_id`` and returns 502 instead of rolling back. Needs
    ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    app_url = _require_app_url()
    computed = await aarsregnskap_service.build_computed(
        db, context.company, payload.aar, today=_today_norway()
    )
    _require_ready_and_confirmed(
        computed,
        bekreftet=payload.bekreftet,
        aarsresultat=payload.aarsresultat,
        sum_eiendeler=payload.sum_eiendeler,
    )

    _, id_porten_token = await government_login_for(
        db,
        user,
        missing="Logg inn med ID-porten for å sende årsregnskapet til Altinn.",
    )

    hoved_xml = xmlwriter.render_hovedskjema(context.company, computed)
    under_xml = xmlwriter.render_underskjema(computed)
    pdf_bytes = (
        aarsregnskap_pdf.render_aarsregnskap_pdf(context.company, computed)
        if payload.legg_ved_pdf
        else None
    )
    attachments = (
        [(f"aarsregnskap-{payload.aar}.pdf", pdf_bytes, "application/pdf")]
        if pdf_bytes is not None
        else None
    )
    findings_json = [_finding_dict(f) for f in computed.findings]
    client = _client_for(app_url)

    try:
        altinn_token = (await exchange_id_porten_token(id_porten_token)).value
        prepared = await client.prepare_and_lock(
            organisation_number=context.company.orgnr,
            hovedskjema_xml=hoved_xml,
            underskjema_xml=under_xml,
            token=altinn_token,
            attachments=attachments,
            lock=payload.laas or payload.signer_direkte,
        )
        signed_result = None
        if (
            payload.signer_direkte
            and prepared.locked
            and not has_blocking_validation_errors(prepared.validation_issues)
        ):
            signed_result = await client.sign_and_submit(
                prepared.ref, id_porten_altinn_token=altinn_token
            )
    except (AarsregnskapClientError, AltinnError) as err:
        logger.error("Årsregnskap ID-porten send failed for company %s", context.company.id)
        instance_id = err.instance_id if isinstance(err, AarsregnskapClientError) else None
        hoved_id = err.hovedskjema_data_id if isinstance(err, AarsregnskapClientError) else None
        under_id = err.underskjema_data_id if isinstance(err, AarsregnskapClientError) else None
        portal_url = err.portal_url if isinstance(err, AarsregnskapClientError) else None
        val_issues = err.validation_issues if isinstance(err, AarsregnskapClientError) else []
        failed = await aarsregnskap_service.create_filing(
            db,
            company=context.company,
            year=payload.aar,
            status=AarsregnskapFilingStatus.FEILET.value,
            auth_method="id_porten",
            altinn_instance_id=instance_id,
            hovedskjema_data_id=hoved_id,
            underskjema_data_id=under_id,
            altinn_portal_url=portal_url,
            content_hash=computed.content_hash,
            aarsresultat=Decimal(computed.aarsresultat),
            sum_eiendeler=Decimal(computed.sum_eiendeler),
            sum_egenkapital=Decimal(computed.sum_egenkapital),
            fastsettelsedato=computed.draft.approval_date,
            bekreftende_representant=computed.draft.confirming_representative,
            hovedskjema_xml=hoved_xml,
            underskjema_xml=under_xml,
            validation_issues=val_issues,
            findings=findings_json,
            created_by_user_id=user.id,
            error=str(err),
        )
        return JSONResponse(
            status_code=502,
            content={
                "detail": (
                    "Klarte ikke å sende årsregnskapet til Altinn. Kontroller feilen og prøv igjen."
                ),
                "code": "aarsregnskap_remote",
                "filing_id": failed.id,
                "altinn_instance_id": failed.altinn_instance_id,
            },
        )

    now = utc_now()
    if signed_result is not None:
        has_receipt = any(a.document_type == "kvittering_pdf" for a in signed_result.artifacts)
        filing_status = (
            AarsregnskapFilingStatus.MOTTATT.value
            if has_receipt
            else AarsregnskapFilingStatus.SIGNERT.value
        )
    elif prepared.locked:
        filing_status = AarsregnskapFilingStatus.LAAST.value
    else:
        filing_status = AarsregnskapFilingStatus.KLARGJORT.value

    filing = await aarsregnskap_service.create_filing(
        db,
        company=context.company,
        year=payload.aar,
        status=filing_status,
        auth_method="id_porten",
        altinn_instance_id=prepared.ref.path,
        hovedskjema_data_id=prepared.hovedskjema_data_id,
        underskjema_data_id=prepared.underskjema_data_id,
        altinn_portal_url=prepared.portal_url,
        content_hash=computed.content_hash,
        aarsresultat=Decimal(computed.aarsresultat),
        sum_eiendeler=Decimal(computed.sum_eiendeler),
        sum_egenkapital=Decimal(computed.sum_egenkapital),
        fastsettelsedato=computed.draft.approval_date,
        bekreftende_representant=computed.draft.confirming_representative,
        hovedskjema_xml=hoved_xml,
        underskjema_xml=under_xml,
        validation_issues=prepared.validation_issues,
        findings=findings_json,
        created_by_user_id=user.id,
        locked_at=now if prepared.locked else None,
        signed_by_user_id=user.id if signed_result is not None else None,
        signed_by_name=user.full_name if signed_result is not None else None,
        signed_at=now if signed_result is not None else None,
        receipt_received_at=(
            now
            if signed_result is not None
            and any(a.document_type == "kvittering_pdf" for a in signed_result.artifacts)
            else None
        ),
    )

    await aarsregnskap_service.archive_document(
        db,
        storage,
        company=context.company,
        filing=filing,
        document_type="hovedskjema",
        filename=f"aarsregnskap-{payload.aar}-hovedskjema.xml",
        content_type="application/xml",
        content=hoved_xml.encode("utf-8"),
        external_document_id=prepared.hovedskjema_data_id,
    )
    await aarsregnskap_service.archive_document(
        db,
        storage,
        company=context.company,
        filing=filing,
        document_type="underskjema",
        filename=f"aarsregnskap-{payload.aar}-selskapsregnskap.xml",
        content_type="application/xml",
        content=under_xml.encode("utf-8"),
        external_document_id=prepared.underskjema_data_id,
    )
    if pdf_bytes is not None:
        await aarsregnskap_service.archive_document(
            db,
            storage,
            company=context.company,
            filing=filing,
            document_type="vedlegg_pdf",
            filename=f"aarsregnskap-{payload.aar}.pdf",
            content_type="application/pdf",
            content=pdf_bytes,
        )
    if signed_result is not None:
        for artifact in signed_result.artifacts:
            await aarsregnskap_service.archive_document(
                db,
                storage,
                company=context.company,
                filing=filing,
                document_type=artifact.document_type,
                filename=artifact.filename,
                content_type=artifact.content_type,
                content=artifact.content,
                external_document_id=artifact.external_document_id,
            )

    filing = await aarsregnskap_service.get_filing(db, context.company, filing.id)
    return _filing_resp(filing, current_hash=computed.content_hash)


@router.post(
    "/companies/{company_id}/aarsregnskap/innsendinger/{aarsregnskap_filing_id}/signer",
    openapi_extra=key_send(
        "signere og sende inn årsregnskapet til Regnskapsregisteret",
        id_porten=True,
    ),
    response_model=AarsregnskapFilingResponse,
)
async def sign_aarsregnskap_filing(
    aarsregnskap_filing_id: int,
    payload: SignFilingPayload,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    storage: Storage,
) -> AarsregnskapFilingResponse | JSONResponse:
    """Sign and auto-submit a prepared årsregnskap in Altinn 3 using ID-porten.

    **A key's request always waits for its person** (``key_send``,
    ``id_porten=True``): 202 ``approval_pending``. Signing in
    ``brg/aarsregnskap-vanlig-202406`` (``{"action": "sign"}``) legally
    attaches the signer's fødselsnummer and immediately submits the annual
    accounts to Regnskapsregisteret.

    Refuses with 404 if the filing does not belong to the company, 409
    ``aarsregnskap_not_submittable`` if the filing is already signed or has no
    Altinn instance id, 409 ``aarsregnskap_changed`` if the books changed since
    preparation (while ``sjekk_endringer`` is true), 422
    ``aarsregnskap_confirmation_required`` if ``bekreftet`` is false, and 502
    (persisting the error on the row) if Altinn rejects the signature. Needs
    ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    filing = await aarsregnskap_service.get_filing(db, context.company, aarsregnskap_filing_id)
    if not payload.bekreftet:
        raise AarsregnskapConfirmationRequiredError(
            "Du må bekrefte signeringen før årsregnskapet sendes til Regnskapsregisteret."
        )
    app_url = _require_app_url()
    if filing.status not in (
        AarsregnskapFilingStatus.LAAST.value,
        AarsregnskapFilingStatus.KLARGJORT.value,
    ):
        raise AarsregnskapNotSubmittableError(
            f"Innsendingen har status «{filing.status}» og kan ikke signeres på nytt."
        )
    if not filing.altinn_instance_id:
        raise AarsregnskapNotSubmittableError(
            "Innsendingen mangler Altinn-instans-ID og kan ikke signeres."
        )

    computed = await aarsregnskap_service.build_computed(
        db, context.company, filing.year, today=_today_norway()
    )
    if payload.sjekk_endringer and filing.content_hash != computed.content_hash:
        raise AarsregnskapChangedError(
            "Regnskapet eller notene er endret etter at denne innsendingen ble klargjort. "
            "Klargjør en ny innsending med oppdaterte tall før signering."
        )

    _, id_porten_token = await government_login_for(
        db,
        user,
        missing="Logg inn med ID-porten for å signere og sende inn årsregnskapet.",
    )

    try:
        ref = InstanceRef.parse(filing.altinn_instance_id)
        altinn_token = (await exchange_id_porten_token(id_porten_token)).value
        client = _client_for(app_url)
        if filing.status == AarsregnskapFilingStatus.KLARGJORT.value:
            # Lock first if instance was left in Utfylling.
            await client._apps.process_next(ref, token=altinn_token, action="confirm")
            filing.locked_at = utc_now()
        result = await client.sign_and_submit(ref, id_porten_altinn_token=altinn_token)
    except (AarsregnskapClientError, AltinnInstanceError, AltinnError) as err:
        logger.error("Årsregnskap signing failed for filing %s", filing.id)
        filing.status = AarsregnskapFilingStatus.FEILET.value
        filing.error = str(err)[:1000]
        await db.flush()
        return JSONResponse(
            status_code=502,
            content={
                "detail": (
                    "Signeringen i Altinn feilet. Kontroller rettigheter i Altinn og prøv igjen."
                ),
                "code": "aarsregnskap_remote",
                "filing_id": filing.id,
                "altinn_instance_id": filing.altinn_instance_id,
            },
        )

    now = utc_now()
    has_receipt = any(a.document_type == "kvittering_pdf" for a in result.artifacts)
    filing.status = (
        AarsregnskapFilingStatus.MOTTATT.value
        if has_receipt
        else AarsregnskapFilingStatus.SIGNERT.value
    )
    filing.signed_by_user_id = user.id
    filing.signed_by_name = user.full_name
    filing.signed_at = now
    if has_receipt:
        filing.receipt_received_at = now
    filing.error = None
    await db.flush()

    for artifact in result.artifacts:
        await aarsregnskap_service.archive_document(
            db,
            storage,
            company=context.company,
            filing=filing,
            document_type=artifact.document_type,
            filename=artifact.filename,
            content_type=artifact.content_type,
            content=artifact.content,
            external_document_id=artifact.external_document_id,
        )
    filing = await aarsregnskap_service.get_filing(db, context.company, filing.id)
    return _filing_resp(filing, current_hash=computed.content_hash)


@router.post(
    "/companies/{company_id}/aarsregnskap/innsendinger/{aarsregnskap_filing_id}/status",
    openapi_extra=key_send(
        "hente status og kvittering for årsregnskapet fra Altinn",
        id_porten=True,
    ),
    response_model=AarsregnskapFilingResponse,
)
async def refresh_aarsregnskap_status(
    aarsregnskap_filing_id: int,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    storage: Storage,
) -> AarsregnskapFilingResponse | JSONResponse:
    """Poll Altinn 3 for instance status and archive the receipt PDF if submitted.

    Useful when a board member signed directly in Altinn's web portal or when
    the receipt PDF (``ref-data-as-pdf``) was still being generated right after
    signing. Needs ``ledger:write`` and an active ID-porten login.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    filing = await aarsregnskap_service.get_filing(db, context.company, aarsregnskap_filing_id)
    app_url = _require_app_url()
    if not filing.altinn_instance_id:
        raise AarsregnskapNotSubmittableError(
            "Innsendingen har ingen Altinn-instans-ID å hente status for."
        )

    _, id_porten_token = await government_login_for(
        db,
        user,
        missing="Logg inn med ID-porten for å hente status og kvittering fra Altinn.",
    )

    try:
        ref = InstanceRef.parse(filing.altinn_instance_id)
        altinn_token = (await exchange_id_porten_token(id_porten_token)).value
        result = await _client_for(app_url).fetch_status(ref, token=altinn_token)
    except (AarsregnskapClientError, AltinnInstanceError, AltinnError):
        logger.error("Årsregnskap status poll failed for filing %s", filing.id)
        return JSONResponse(
            status_code=502,
            content={
                "detail": "Klarte ikke å hente status fra Altinn. Prøv igjen senere.",
                "code": "aarsregnskap_remote",
                "filing_id": filing.id,
            },
        )

    now = utc_now()
    if result.process_ended:
        has_receipt = any(a.document_type == "kvittering_pdf" for a in result.artifacts)
        if has_receipt:
            filing.status = AarsregnskapFilingStatus.MOTTATT.value
            filing.receipt_received_at = filing.receipt_received_at or now
        elif filing.status != AarsregnskapFilingStatus.MOTTATT.value:
            filing.status = AarsregnskapFilingStatus.SIGNERT.value
        filing.signed_at = filing.signed_at or now
        filing.error = None
        await db.flush()
        for artifact in result.artifacts:
            await aarsregnskap_service.archive_document(
                db,
                storage,
                company=context.company,
                filing=filing,
                document_type=artifact.document_type,
                filename=artifact.filename,
                content_type=artifact.content_type,
                content=artifact.content,
                external_document_id=artifact.external_document_id,
            )

    computed = await aarsregnskap_service.build_computed(
        db, context.company, filing.year, today=_today_norway()
    )
    filing = await aarsregnskap_service.get_filing(db, context.company, filing.id)
    return _filing_resp(filing, current_hash=computed.content_hash)


@router.get(
    "/companies/{company_id}/aarsregnskap/innsendinger/{aarsregnskap_filing_id}/dokument",
    openapi_extra=KEY_READ,
)
async def download_archived_document(
    aarsregnskap_filing_id: int,
    context: CurrentCompany,
    db: DbSession,
    storage: Storage,
    doc_type: str = Query(alias="type"),
) -> Response:
    """Download an archived XML, PDF attachment, or Altinn receipt from an årsregnskap filing.

    ``type`` is one of ``hovedskjema``, ``underskjema``, ``vedlegg_pdf``,
    ``kvittering_pdf``, or ``signatur_json``. Needs ``ledger:read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    allowed_types = {
        "hovedskjema",
        "underskjema",
        "vedlegg_pdf",
        "kvittering_pdf",
        "signatur_json",
    }
    if doc_type not in allowed_types:
        raise InvalidAarsregnskapDataError(f"ukjent dokumenttype: {doc_type}")
    doc = await aarsregnskap_service.get_filing_document(
        db, context.company, aarsregnskap_filing_id, doc_type
    )
    content = await storage.load(doc.storage_key)
    return Response(
        content=content,
        media_type=doc.content_type,
        headers={"Content-Disposition": f"attachment; filename={doc.filename}"},
    )
