"""The skattemelding: see the year, check it with Skatteetaten, file it, keep the answer.

An aksjeselskap (AS) or enkeltpersonforetak (ENK) files two documents together each
spring: the **næringsspesifikasjon**, which is the books (the result and balance per
post, the saldo specification, equity reconciliation, and for ENK beregnet
personinntekt and omløpsmiddel specifications), and the **skattemelding**, which
Skatteetaten serves as an utkast (``skattemeldingUpersonlig`` for AS on orgnr,
``skattemeldingPersonlig`` for ENK on the owner's fødselsnummer). For an ENK, every
personal item in the fetched skattemelding (salary, bank accounts, loans, deductions)
is preserved untouched, and submissions default to ``ikkeKomplett`` so the owner
reviews and completes personal items on skatteetaten.no.

The routes follow the lifecycle, as ``/mva`` does:

``GET /companies/{id}/skattemelding?aar=``
    The year's næringsspesifikasjon as the books have it now, what stops it,
    and the filings made for the year. What the person reads first.

``POST /companies/{id}/skattemelding/kontroll``
    Fetches the current skattemelding from Skatteetaten (on orgnr for AS, on
    the logged-in owner's fødselsnummer for ENK), prepares the pair, sends it
    to Skatteetaten's validation, and keeps the exact envelope with the answer.
    Nothing is filed. Needs the ID-porten login.

``POST /companies/{id}/skattemelding/innsendinger/{filing}/send``
    Files the checked envelope through Altinn, byte for byte, and only while
    the books still build the same næringsspesifikasjon.

``POST /companies/{id}/skattemelding/innsendinger/{filing}/tilbakemelding``
    Fetches Skatteetaten's answer once it has processed the melding.

As with the MVA-melding, the government login is not spent, and a failure in
the middle of a submission is recorded rather than rolled back: the send route
answers 502 by *returning* a response, so the row that says an instance may
now exist in Altinn is committed with it.

**A wound-up AS's last year** (``company.wound_up_on``, set under
Årsavslutning) is checked and filed as soon as its last day is past, as a
forhåndsfastsetting (skatteforvaltningsloven § 8-2): Myntos asks Skatteetaten
to prepare the melding when it has none (``klargjoerforhaandsfastsetting``),
and sends it marked as fully wound up, with what the shareholders were paid.
Proven end to end in TT02 on 2026-10-22 (docs/integrations/skatteetaten.md).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from myntos.api.deps import (
    KEY_READ,
    CurrentCompany,
    CurrentUser,
    DbSession,
    Storage,
    key_send,
)
from myntos.api.v1.skatt import government_login_for
from myntos.core.clock import utc_now
from myntos.core.config import get_settings
from myntos.domain.arsoppgjor.findings import Finding, Severity
from myntos.domain.identity import service as identity_service
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.identity.models import GovernmentLoginAttempt, User
from myntos.domain.ledger.models import Company
from myntos.integrations.altinn.instances import AltinnInstanceError, InstanceRef
from myntos.integrations.altinn.tokens import AltinnError, exchange_id_porten_token
from myntos.integrations.skatteetaten import client as skattemelding_integration
from myntos.skattemelding import (
    codes,
    envelope,
    forhaandsfastsetting,
    naeringsspesifikasjon,
    personlig,
    service,
    upersonlig,
    xmlwriter,
)
from myntos.skattemelding.errors import (
    SkattemeldingAnswerRequiredError,
    SkattemeldingChangedError,
    SkattemeldingConfirmationRequiredError,
    SkattemeldingNotReadyError,
    SkattemeldingNotSubmittableError,
    SkattemeldingYearNotSupportedError,
)
from myntos.skattemelding.models import SkattemeldingFiling, SkattemeldingStatus

router = APIRouter(tags=["skattemelding"])
logger = logging.getLogger(__name__)

# The income year ends at midnight in Norway, as an MVA termin does.
_OSLO = ZoneInfo("Europe/Oslo")

_NO_LOGIN = "logg inn med ID-porten for å kontrollere og sende skattemeldingen"

# Skatteetaten's viewer for an instance, where the revisor confirms and where
# anyone with access sees the melding as Skatteetaten reads it. Two hosts, as
# everything of Skatteetaten's has; which one follows from the Altinn app the
# deployment files to, so a test filing never links to production.
_VIEWER = {
    "test": "https://skatt-test.sits.no/web/skattemelding-visning/altinn",
    "prod": "https://skatt.skatteetaten.no/web/skattemelding-visning/altinn",
}
_PORTAL = {
    "test": "https://skatt-test.sits.no/web/skattemeldingen",
    "prod": "https://skatt.skatteetaten.no/web/skattemeldingen",
}
_APP_ID = "skd/formueinntekt-skattemelding-v2"

_Year = Annotated[int | None, Query(ge=2000, le=2100)]


def _today() -> date:
    return utc_now().astimezone(_OSLO).date()


# --- Responses --------------------------------------------------------------------


class FindingResponse(BaseModel):
    # "feil" stops the check; "merknad" is for reading.
    severity: str
    code: str
    message: str


class PostResponse(BaseModel):
    category: str
    code: str
    name: str
    # As sent: the books' own side, turned round where Skatteetaten subtracts.
    amount: Decimal
    subtracted: bool
    accounts: list[str]


class SaldoResponse(BaseModel):
    key: str
    saldogruppe: str
    label: str
    opening: Decimal
    additions: Decimal
    grants: Decimal
    disposals: Decimal
    basis: Decimal
    depreciation: Decimal
    income: Decimal
    closing: Decimal
    # What is sent: the depreciation as a percentage of the basis.
    rate_percent: Decimal


class DifferenceResponse(BaseModel):
    type: str
    name: str
    amount: Decimal
    # Added to the result (tillegg) or taken from it (fradrag).
    adds: bool


class FormueResponse(BaseModel):
    with_discount: int
    without_discount: int
    debt: int


class CodeOptionResponse(BaseModel):
    code: str
    name: str


class SkjermingsgrunnlagResponse(BaseModel):
    type: str
    name: str
    opening: Decimal
    closing: Decimal
    average: Decimal
    is_debt: bool


class PersoninntektResponse(BaseModel):
    skjermingsgrunnlag: list[SkjermingsgrunnlagResponse]
    sum_foer_gjeld: Decimal
    sum_etter_gjeld: Decimal
    skjermingsrente: Decimal
    skjermingsfradrag: Decimal
    kapitalinntekt: Decimal
    kapitalkostnad: Decimal
    rentekostnad_foretaksgjeld: Decimal
    antall_maaneder: int
    andel_innehaver: Decimal
    beregnet_personinntekt: Decimal
    tilordnet_innehaver: Decimal


class AvvikResponse(BaseModel):
    stage: str
    type: str
    path: str | None
    received: str | None
    computed: str | None


class VeiledningResponse(BaseModel):
    type: str
    text: str | None
    strategy: str | None
    path: str | None
    is_error: bool


class DocumentResponse(BaseModel):
    data_type: str
    filename: str
    content_type: str
    byte_size: int
    fetched_at: datetime


class FilingResponse(BaseModel):
    id: int
    # Every company-scoped answer names its company, which the frontend
    # checks against the one on screen (docs/company-isolation.md).
    company_id: int
    year: int
    status: str
    validation_status: str
    validated_at: datetime
    aarsresultat: Decimal
    skattemessig_resultat: Decimal
    beregnet_skatt: Decimal | None
    has_auditor: bool
    ytelser_til_aksjonaerer: bool
    # Filed early because the company was wound up in the year, and what the
    # person stated the shareholders were paid (whole kroner) when it was.
    forhaandsfastsetting: bool = False
    likvidasjonsvederlag: int | None = None
    # "komplett" or (ENK hand-off default) "ikkeKomplett".
    innsendingstype: str = "komplett"
    # Direct link to skatteetaten.no where an ENK owner finishes personal items.
    skatteetaten_portal_url: str | None = None
    # ENK check answers stored alongside the filing (never fødselsnummer).
    naeringstype: str | None = None
    kommunenummer: str | None = None
    naeringsbeskrivelse: str | None = None
    antall_maaneder: int | None = None
    andel_innehaver: Decimal | None = None
    # Myntos's merknader at the check, and Skatteetaten's answer.
    findings: list[FindingResponse]
    avvik: list[AvvikResponse]
    veiledninger: list[VeiledningResponse]
    reasons: list[str]
    # Whether the books still build what was checked; null where not asked.
    matches_books: bool | None = None
    altinn_instance_id: str | None
    viewer_url: str | None
    submitted_at: datetime | None
    submitted_by_name: str | None
    outcome: str | None
    outcome_text: str | None
    feedback_received_at: datetime | None
    error: str | None
    documents: list[DocumentResponse]


class YearResponse(BaseModel):
    company_id: int
    year: int
    supported_years: list[int]
    organisation_form: str | None
    year_closed: bool
    # The year's last day: 31 December, or a wound-up company's last day.
    last_day: date
    is_over: bool
    # The company's last year, filed early as a forhåndsfastsetting; with the
    # payout to the shareholders the books suggest, which the person confirms.
    forhaandsfastsetting: bool
    likvidasjonsvederlag_forslag: Decimal | None
    # The register has deleted the company and no longer lists its roles: every
    # check asks whether it had a revisor (``revisor`` in its body), and the
    # newest filing's ``has_auditor`` is what was answered last.
    revisor_unknown: bool
    # Whether this installation can check and file at all.
    submission_available: bool
    regnskapsplikt: str
    rules: str | None
    has_auditor: bool | None
    auditor_name: str | None
    posts: list[PostResponse]
    saldi: list[SaldoResponse]
    permanent_differences: list[DifferenceResponse]
    equity_opening: Decimal
    equity_changes: list[DifferenceResponse]
    equity_closing: Decimal
    aarsresultat: Decimal
    tillegg: Decimal
    fradrag: Decimal
    skattemessig_resultat: Decimal
    formue: FormueResponse
    findings: list[FindingResponse]
    blocked: bool
    filings: list[FilingResponse]
    # ENK-specific preview and suggested check inputs (null/empty for AS).
    er_enk: bool = False
    naeringstype_forslag: str | None = None
    naeringstyper: list[CodeOptionResponse] = Field(default_factory=list)
    kommunenummer_forslag: str | None = None
    naeringsbeskrivelse_forslag: str | None = None
    antall_maaneder_forslag: int | None = None
    personinntekt: PersoninntektResponse | None = None


def _finding(finding: Finding) -> FindingResponse:
    return FindingResponse(
        severity=finding.severity.value, code=finding.code, message=finding.message
    )


def _viewer_url(instance_id: str | None) -> str | None:
    if instance_id is None:
        return None
    app_url = get_settings().altinn_skattemelding_app_url or ""
    host = _VIEWER["test" if "tt02" in app_url else "prod"]
    return f"{host}?appId={_APP_ID}&instansId={instance_id}"


def _portal_url(year: int) -> str:
    app_url = get_settings().altinn_skattemelding_app_url or ""
    host = _PORTAL["test" if "tt02" in app_url else "prod"]
    return f"{host}/{year}"


def _personinntekt_response(pi: personlig.Personinntekt | None) -> PersoninntektResponse | None:
    if pi is None:
        return None
    return PersoninntektResponse(
        skjermingsgrunnlag=[
            SkjermingsgrunnlagResponse(
                type=line.type,
                name=line.name,
                opening=line.opening,
                closing=line.closing,
                average=line.average,
                is_debt=line.is_debt,
            )
            for line in pi.skjermingsgrunnlag
        ],
        sum_foer_gjeld=pi.sum_foer_gjeld,
        sum_etter_gjeld=pi.sum_etter_gjeld,
        skjermingsrente=pi.skjermingsrente,
        skjermingsfradrag=pi.skjermingsfradrag,
        kapitalinntekt=pi.kapitalinntekt,
        kapitalkostnad=pi.kapitalkostnad,
        rentekostnad_foretaksgjeld=pi.rentekostnad_foretaksgjeld,
        antall_maaneder=pi.antall_maaneder,
        andel_innehaver=pi.andel_innehaver,
        beregnet_personinntekt=pi.beregnet_personinntekt,
        tilordnet_innehaver=pi.tilordnet_innehaver,
    )


def _filing_response(
    filing: SkattemeldingFiling, *, current_hash: str | None = None, is_enk: bool = False
) -> FilingResponse:
    try:
        answer = envelope.parse_validation(filing.validation_xml.encode("utf-8"))
        avvik = [
            AvvikResponse(
                stage=item.stage,
                type=item.type,
                path=item.path,
                received=item.received,
                computed=item.computed,
            )
            for item in answer.avvik
        ]
        veiledninger = [
            VeiledningResponse(
                type=item.type,
                text=item.text,
                strategy=item.strategy,
                path=item.path,
                is_error=item.is_error,
            )
            for item in answer.veiledninger
        ]
        reasons = list(answer.reasons)
    except envelope.EnvelopeError:
        avvik, veiledninger, reasons = [], [], []
    matches = None
    if current_hash is not None and filing.status == SkattemeldingStatus.VALIDERT.value:
        matches = current_hash == filing.content_hash
    stored = filing.answers or {}
    enk_filing = is_enk or "naeringstype" in stored
    enk_answers = service.enk_answers_from(stored) if enk_filing else None
    innsendingstype = (
        enk_answers.innsendingstype.value
        if enk_answers is not None
        else str(stored.get("innsendingstype") or envelope.Innsendingstype.KOMPLETT.value)
    )
    return FilingResponse(
        id=filing.id,
        company_id=filing.company_id,
        year=filing.year,
        status=filing.status,
        validation_status=filing.validation_status,
        validated_at=filing.validated_at,
        aarsresultat=filing.aarsresultat,
        skattemessig_resultat=filing.skattemessig_resultat,
        beregnet_skatt=filing.beregnet_skatt,
        has_auditor=filing.has_auditor,
        ytelser_til_aksjonaerer=bool(stored.get("ytelser_til_aksjonaerer")),
        forhaandsfastsetting=bool(stored.get("forhaandsfastsetting")),
        likvidasjonsvederlag=service.payout_from(stored),
        innsendingstype=innsendingstype,
        skatteetaten_portal_url=_portal_url(filing.year) if enk_filing else None,
        naeringstype=enk_answers.naeringstype if enk_answers is not None else None,
        kommunenummer=enk_answers.kommunenummer if enk_answers is not None else None,
        naeringsbeskrivelse=enk_answers.naeringsbeskrivelse if enk_answers is not None else None,
        antall_maaneder=enk_answers.antall_maaneder if enk_answers is not None else None,
        andel_innehaver=enk_answers.andel_innehaver if enk_answers is not None else None,
        findings=[FindingResponse(**item) for item in filing.findings],
        avvik=avvik,
        veiledninger=veiledninger,
        reasons=reasons,
        matches_books=matches,
        altinn_instance_id=filing.altinn_instance_id,
        viewer_url=_viewer_url(filing.altinn_instance_id),
        submitted_at=filing.submitted_at,
        submitted_by_name=filing.submitted_by_name,
        outcome=filing.outcome,
        outcome_text=filing.outcome_text,
        feedback_received_at=filing.feedback_received_at,
        error=filing.error,
        documents=[
            DocumentResponse(
                data_type=d.data_type,
                filename=d.filename,
                content_type=d.content_type,
                byte_size=d.byte_size,
                fetched_at=d.fetched_at,
            )
            for d in filing.documents
        ],
    )


def _submission_configured() -> bool:
    try:
        skattemelding_integration.build_client()
    except skattemelding_integration.SkatteetatenNotConfiguredError:
        return False
    return True


def _client() -> skattemelding_integration.SkattemeldingClient:
    try:
        return skattemelding_integration.build_client()
    except skattemelding_integration.SkatteetatenNotConfiguredError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="innsending av skattemelding er ikke satt opp på denne installasjonen",
        ) from None


async def _filer_name(db: DbSession, user: User) -> str:
    """Who sent it, as ID-porten named them when it did; the MVA-melding's rule."""
    identity = await identity_service.government_identity_for(db, user.id)
    if identity is not None and identity.full_name:
        return identity.full_name
    return user.full_name


def _default_year(company: Company, today: date) -> int:
    """A wound-up company's last year; else last year when Myntos can file it, else the newest."""
    years = service.supported_years()
    if company.wound_up_on is not None and company.wound_up_on.year in years:
        return company.wound_up_on.year
    return today.year - 1 if today.year - 1 in years else years[-1]


def _enk_details_for(
    prepared: service.Prepared, answers: personlig.EnkAnswers
) -> naeringsspesifikasjon.EnkDocumentDetails:
    pi, _ = personlig.compute_personinntekt(
        prepared.nsp, accounts=prepared.accounts, answers=answers
    )
    return personlig.document_details(
        prepared.nsp, answers=answers, personinntekt=pi, formue_values=prepared.formue
    )


def _current_hash_for(prepared: service.Prepared, filing: SkattemeldingFiling) -> str | None:
    """The hash the books give now for ``filing``'s partsnummer and ENK options."""
    try:
        enk_details = (
            _enk_details_for(prepared, service.enk_answers_from(filing.answers or {}))
            if prepared.nsp.is_enk
            else None
        )
        return service.content_hash(
            service.naeringsspesifikasjon_xml(
                prepared.nsp, partsnummer=filing.partsnummer, enk_details=enk_details
            )
        )
    except xmlwriter.DocumentError:
        return None


def _current_hash(prepared: service.Prepared, filings: list[SkattemeldingFiling]) -> str | None:
    """The hash the books give now, for the newest checked filing's partsnummer."""
    if not filings:
        return None
    return _current_hash_for(prepared, filings[0])


def _require_pid(attempt: GovernmentLoginAttempt | None) -> str:
    """The 11-digit fødselsnummer of the logged-in ID-porten user, for ENK filings."""
    pid = (attempt.pid or "").strip() if attempt is not None else ""
    if len(pid) != 11 or not pid.isdigit():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "ID-porten-innloggingen mangler fødselsnummer. Logg inn med ID-porten på nytt "
                "for å kontrollere og sende skattemeldingen for et enkeltpersonforetak."
            ),
        )
    return pid


# --- The year ---------------------------------------------------------------------


@router.get("/companies/{company_id}/skattemelding", openapi_extra=KEY_READ)
async def year_overview(
    context: CurrentCompany, user: CurrentUser, db: DbSession, aar: _Year = None
) -> YearResponse:
    """An aksjeselskap's or enkeltpersonforetak's næringsspesifikasjon for one income year.

    The næringsspesifikasjon is the part of the yearly tax return (the
    skattemelding) that comes from the books: the result and the balance summed
    per post of Skatteetaten's list (the post each account is grouped on in
    the chart of accounts), the saldo specification from the fixed-asset
    register, the permanent differences between book and taxable result (for
    AS), the equity reconciliation, and (for ENK) beregnet personinntekt with
    skjermingsgrunnlag and omløpsmiddel specifications. Read it before the
    person checks it with Skatteetaten.

    ``aar`` is the income year, last year when left out. Amounts are decimal
    strings in kroner. ``aarsresultat``, ``tillegg``, ``fradrag`` and
    ``skattemessig_resultat`` are computed the way Skatteetaten computes them,
    and are shown, never sent: Skatteetaten derives them itself.
    ``formue`` is what the skattemelding will state (for AS) or transfer (for
    ENK) as assets (``with_discount`` the ones that get the valuation discount)
    and debt, in whole kroner; real estate and shares are left out and named in
    ``findings``.

    For an enkeltpersonforetak (``er_enk`` true), ``personinntekt`` holds the
    computed skjermingsgrunnlag lines, skjermingsrente, skjermingsfradrag,
    finance adjustments and beregnet personinntekt (skatteloven § 12-10 ff.),
    while ``naeringstyper``, ``naeringstype_forslag``, ``kommunenummer_forslag``,
    ``naeringsbeskrivelse_forslag`` and ``antall_maaneder_forslag`` suggest the
    check inputs.

    ``findings`` say what stops the check (``severity`` ``feil``, with
    ``blocked`` true) and what to read (``merknad``). Each ``code`` is stable;
    the ``message`` says what to do, in Norwegian. The common ones:
    ``aret_er_apent`` (close the year first), ``konto_uten_post`` (an account
    with a balance and no post), ``revisor_ukjent`` (fetch the company from
    Enhetsregisteret), ``post_ikke_tillatt`` (a post not allowed for the
    company's organisation form and regnskapsplikt).

    **A wound-up AS's last year** (the company was dissolved, oppløst,
    and its books end on ``last_day`` instead of 31 December; set with ``POST
    /companies/{company_id}/arsavslutning/opplosning``) has
    ``forhaandsfastsetting`` true: its skattemelding is filed as soon as
    ``last_day`` is past, before the ordinary assessment, as
    skatteforvaltningsloven § 8-2 asks. ``likvidasjonsvederlag_forslag`` is
    what the books say the shareholders were paid when it was dissolved (the
    equity that left other than through the result), for the person to
    confirm; the merknad ``utbetaling_ved_opplosning`` says the same.
    ``revisor_unknown`` is true when Enhetsregisteret has deleted the company
    and no longer lists its roles (merknad ``revisor_ukjent_slettet``): the
    check then asks whether it had a revisor. ``filings`` are the year's checks and
    submissions, newest first, as ``GET
    /companies/{company_id}/skattemelding/innsendinger/{filing_id}`` describes
    them; ``matches_books`` on a checked one says whether the books still
    build what was checked.

    Refusals: 404 ``financial_year_not_found``; 422
    ``skattemelding_year_not_supported`` for a year Myntos has no schema for
    (``supported_years`` in a successful answer lists them). Needs
    ``ledger:read``; a key needs ``read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    company = context.company
    today = _today()
    year = aar if aar is not None else _default_year(company, today)
    filings = await service.filings_for(db, company, year)
    # A deleted company's revisor is the person's answer at the last check; the
    # register no longer says.
    prepared = await service.prepare(
        db,
        company=company,
        year=year,
        user=user,
        auditor_answer=filings[0].has_auditor if filings else None,
    )
    nsp = prepared.nsp
    payout = naeringsspesifikasjon.payout(nsp)
    current_hash = _current_hash(prepared, filings)
    naeringstyper = (
        [
            CodeOptionResponse(code=item.code, name=item.name)
            for item in codes.codes(year, codes.NAERINGSTYPE)
        ]
        if nsp.is_enk
        else []
    )
    return YearResponse(
        company_id=company.id,
        year=year,
        supported_years=service.supported_years(),
        organisation_form=company.organisation_form,
        year_closed=not any(f.code == "aret_er_apent" for f in nsp.findings),
        last_day=service.last_day(company, year),
        is_over=service.is_over(company, year, today),
        forhaandsfastsetting=service.is_final(company, year),
        likvidasjonsvederlag_forslag=payout,
        revisor_unknown=prepared.auditor.deleted,
        submission_available=_submission_configured(),
        regnskapsplikt=nsp.regnskapsplikt.value,
        rules=nsp.rules,
        has_auditor=prepared.auditor.has_auditor,
        auditor_name=prepared.auditor.name,
        posts=[
            PostResponse(
                category=p.category,
                code=p.code,
                name=p.name,
                amount=p.amount,
                subtracted=p.subtracted,
                accounts=list(p.accounts),
            )
            for p in nsp.posts
        ],
        saldi=[
            SaldoResponse(
                key=s.key,
                saldogruppe=s.saldogruppe,
                label=s.label,
                opening=s.opening,
                additions=s.additions,
                grants=s.grants,
                disposals=s.disposals,
                basis=s.basis,
                depreciation=s.depreciation,
                income=s.income,
                closing=s.closing,
                rate_percent=s.rate_percent,
            )
            for s in nsp.saldi
        ],
        permanent_differences=[
            DifferenceResponse(type=d.type, name=d.name, amount=d.amount, adds=d.adds)
            for d in nsp.permanent
        ],
        equity_opening=nsp.equity_opening,
        equity_changes=[
            DifferenceResponse(type=c.type, name=c.name, amount=c.amount, adds=c.adds)
            for c in nsp.equity_changes
        ],
        equity_closing=nsp.equity_closing,
        aarsresultat=nsp.result,
        tillegg=nsp.additions,
        fradrag=nsp.deductions,
        skattemessig_resultat=nsp.taxable_result,
        formue=FormueResponse(
            with_discount=prepared.formue.with_discount,
            without_discount=prepared.formue.without_discount,
            debt=prepared.formue.debt,
        ),
        findings=[_finding(f) for f in prepared.findings],
        blocked=prepared.blocked,
        filings=[
            _filing_response(f, current_hash=current_hash, is_enk=nsp.is_enk) for f in filings
        ],
        er_enk=nsp.is_enk,
        naeringstype_forslag=prepared.naeringstype_forslag,
        naeringstyper=naeringstyper,
        kommunenummer_forslag=prepared.kommunenummer_forslag,
        naeringsbeskrivelse_forslag=prepared.naeringsbeskrivelse_forslag,
        antall_maaneder_forslag=prepared.antall_maaneder_forslag,
        personinntekt=_personinntekt_response(prepared.personinntekt),
    )


# --- Validation -------------------------------------------------------------------


class ValidationPayload(BaseModel):
    aar: int = Field(ge=2000, le=2100)
    # AS only: «Skal selskapet rapportere opplysninger om ytelser mellom selskap og
    # personlig aksjonær, deltaker eller nærstående til denne», as the
    # skattemelding asks it. Required for AS, ignored for ENK.
    ytelser_til_aksjonaerer: bool | None = None
    # A wound-up AS's last year only: what the shareholders were paid in
    # all when it was dissolved, in whole kroner (likvidasjonsvederlag).
    likvidasjonsvederlag: int | None = Field(default=None, ge=0)
    # A company the register has deleted only, when it no longer lists the
    # roles: whether the company had a revisor.
    revisor: bool | None = None
    # ENK only: industry type from Skatteetaten's naeringstype code list.
    naeringstype: str | None = Field(default=None, max_length=64)
    # ENK only: 4-digit municipality number for the business.
    kommunenummer: str | None = Field(default=None, pattern=r"^\d{4}$")
    # ENK only: short description of the business activity.
    naeringsbeskrivelse: str | None = Field(default=None, max_length=255)
    # ENK only: "ikkeKomplett" (default hand-off to skatteetaten.no) or "komplett".
    innsendingstype: Literal["ikkeKomplett", "komplett"] | None = None
    # ENK only: months operated in the year (1-12, default 12).
    antall_maaneder: int | None = Field(default=None, ge=1, le=12)
    # ENK only: owner's percentage share of profit and personinntekt (0-100, default 100).
    andel_innehaver: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("100"))


@router.post(
    "/companies/{company_id}/skattemelding/kontroll",
    openapi_extra=key_send("sende skattemeldingen til kontroll hos Skatteetaten", id_porten=True),
)
async def validate_year(
    payload: ValidationPayload,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
) -> FilingResponse:
    """Have Skatteetaten check the skattemelding («Kontroller hos Skatteetaten»). Files nothing.

    **A key's request always waits for its person** (``key_send``): 202
    ``approval_pending``, whatever the key may do on its own. The key needs
    ``post``, on its own or by asking. Approved on /godkjenning/, it runs with
    the person's own ID-porten login, which they must hold when they approve,
    as if they had pressed the button themselves: what Skatteetaten is told is
    the person's to say, by Skatteetaten's policy, and nothing leaves Myntos
    without a person's yes (docs/agents-and-api.md rule 5).

    For an **aksjeselskap (AS)**, it builds the næringsspesifikasjon from the
    books, fetches the company's current ``skattemeldingUpersonlig`` on orgnr,
    adds formue and ``ytelser_til_aksjonaerer`` (required for AS), sends the
    pair to Skatteetaten's validation, and stores the exact envelope as a new
    filing (``validert`` or ``avvist``).

    For an **enkeltpersonforetak (ENK)**, it fetches the logged-in owner's
    personal ``skattemeldingPersonlig`` on their fødselsnummer from ID-porten
    (never orgnr), preserves every personal item untouched (salary, bank
    accounts, loans, deductions), builds the ENK næringsspesifikasjon
    (including beregnet personinntekt, skjermingsgrunnlag, and omløpsmidler),
    and validates with ``innsendingstype`` (default ``ikkeKomplett`` so the
    owner finishes personal items on skatteetaten.no, or ``komplett``). Optional
    ENK fields ``naeringstype``, ``kommunenummer``, ``naeringsbeskrivelse``,
    ``antall_maaneder``, and ``andel_innehaver`` default to the suggestions
    returned by ``GET /companies/{company_id}/skattemelding``.

    **A wound-up AS's last year** (``forhaandsfastsetting`` in ``GET
    /companies/{company_id}/skattemelding``) can be checked the day after its
    last day and needs ``likvidasjonsvederlag`` in whole kroner (0 when
    nothing). For a company Enhetsregisteret has deleted, the body also needs
    ``revisor`` when ``revisor_unknown`` is true.

    Refusals: 409 ``skattemelding_not_ready`` while the year's review has a
    ``feil`` (the message lists them); 422 ``skattemelding_year_not_supported``
    for a year Myntos cannot file or one not over yet; 422
    ``skattemelding_answer_required`` when a required answer is missing or
    invalid; 409 ``skattemelding_not_available`` when Skatteetaten is still
    preparing a forhåndsfastsetting melding or refuses to prepare one; 404
    ``financial_year_not_found``; 409 without a code when the person has no
    valid ID-porten login (or no fødselsnummer on an ENK login); 502 when
    Skatteetaten does not answer or answers something unreadable; 503 when this
    installation cannot file. Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    company = context.company
    year = payload.aar
    service.require_supported(year)
    today = _today()
    if not service.is_over(company, year, today):
        first = service.last_day(company, year) + timedelta(days=1)
        raise SkattemeldingYearNotSupportedError(
            f"skattemeldingen for {year} kan kontrolleres fra {first:%d.%m.%Y}"
        )
    er_enk = service.is_enk(company)
    final = service.is_final(company, year)
    prepared = await service.prepare(
        db, company=company, year=year, user=user, auditor_answer=payload.revisor
    )
    if prepared.auditor.deleted and prepared.auditor.has_auditor is None:
        raise SkattemeldingAnswerRequiredError(
            "svar på om selskapet hadde revisor: Enhetsregisteret viser ikke lenger rollene "
            "i et slettet selskap"
        )
    if prepared.blocked:
        raise SkattemeldingNotReadyError(
            " ".join(f.message for f in prepared.findings if f.severity is Severity.FEIL)
        )
    if er_enk:
        valid_naeringstyper = codes.by_code(year, codes.NAERINGSTYPE)
        chosen_naeringstype = (
            payload.naeringstype or prepared.naeringstype_forslag or personlig.DEFAULT_NAERINGSTYPE
        )
        if chosen_naeringstype not in valid_naeringstyper:
            raise SkattemeldingAnswerRequiredError(
                f"ugyldig næringstype «{chosen_naeringstype}» for {year}"
            )
        chosen_innsendingstype = (
            envelope.Innsendingstype(payload.innsendingstype)
            if payload.innsendingstype is not None
            else envelope.Innsendingstype.IKKE_KOMPLETT
        )
        enk_answers = personlig.EnkAnswers(
            naeringstype=chosen_naeringstype,
            kommunenummer=payload.kommunenummer or prepared.kommunenummer_forslag,
            naeringsbeskrivelse=(
                (payload.naeringsbeskrivelse or prepared.naeringsbeskrivelse_forslag or "").strip()
                or None
            ),
            innsendingstype=chosen_innsendingstype,
            antall_maaneder=(
                payload.antall_maaneder
                if payload.antall_maaneder is not None
                else (prepared.antall_maaneder_forslag or personlig.DEFAULT_MONTHS)
            ),
            andel_innehaver=(
                payload.andel_innehaver
                if payload.andel_innehaver is not None
                else personlig.DEFAULT_OWNER_SHARE
            ),
        )
        client = _client()
        attempt, government_token = await government_login_for(db, user, missing=_NO_LOGIN)
        pid = _require_pid(attempt)
        try:
            fetched_raw = await client.fetch(
                inntektsaar=year, identifier=pid, id_porten_token=government_token
            )
            fetched = envelope.parse_fetch(fetched_raw)
        except (skattemelding_integration.SkatteetatenError, envelope.EnvelopeError) as error:
            logger.error("skattemelding fetch failed for company %s: %s", company.id, error)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Skatteetaten svarte ikke med skattemeldingen for året. Prøv igjen om litt.",
            ) from error
        partsnummer = fetched.partsnummer
        if partsnummer is None or not fetched.personal:
            logger.error(
                "company %s: Skatteetaten served %s without a personal partsreferanse",
                company.id,
                fetched.type,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="skattemeldingen Skatteetaten sendte er ikke en personlig skattemelding",
            )
        pi, pi_findings = personlig.compute_personinntekt(
            prepared.nsp, accounts=prepared.accounts, answers=enk_answers
        )
        enk_details = personlig.document_details(
            prepared.nsp, answers=enk_answers, personinntekt=pi, formue_values=prepared.formue
        )
        try:
            nsp_xml = service.naeringsspesifikasjon_xml(
                prepared.nsp, partsnummer=partsnummer, enk_details=enk_details
            )
            completed, added_findings = personlig.complete(fetched, prepared.nsp)
        except xmlwriter.DocumentError as error:
            logger.exception("company %s: the ENK skattemelding could not be written", company.id)
            raise SkattemeldingNotReadyError(
                "Myntos klarte ikke å lage skattemeldingen fra regnskapet. Det er en feil i "
                "Myntos; meld den gjerne under Meld feil eller ønsker."
            ) from error
        request = envelope.request_xml(
            year=year,
            melding=replace(fetched, content=completed),
            naeringsspesifikasjon=nsp_xml,
            innsendingstype=enk_answers.innsendingstype,
            tin=None,
            forhaandsfastsetting=False,
        )
        try:
            answer = await client.validate(
                inntektsaar=year,
                identifier=pid,
                payload=request,
                id_porten_token=government_token,
            )
            result = envelope.parse_validation(answer.content)
        except (skattemelding_integration.SkatteetatenError, envelope.EnvelopeError) as error:
            logger.error("skattemelding validation failed for company %s: %s", company.id, error)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Skatteetatens kontroll av skattemeldingen svarte ikke. Prøv igjen om litt.",
            ) from error
        prepared = replace(
            prepared,
            findings=(
                *prepared.findings,
                *(f for f in (*pi_findings, *added_findings) if f not in prepared.findings),
            ),
        )
        filing = await service.record_validation(
            db,
            company=company,
            prepared=prepared,
            melding=fetched,
            partsnummer=partsnummer,
            answers=enk_answers,
            nsp_xml=nsp_xml,
            request_xml=request,
            validation_xml=answer.content,
            result=result,
            user_id=user.id,
            forhaandsfastsetting=False,
        )
        return _filing_response(filing, is_enk=True)

    if payload.ytelser_til_aksjonaerer is None:
        raise SkattemeldingAnswerRequiredError(
            "svar på om selskapet skal rapportere opplysninger om ytelser mellom selskap og "
            "aksjonær"
        )
    if payload.innsendingstype == envelope.Innsendingstype.IKKE_KOMPLETT.value:
        raise SkattemeldingAnswerRequiredError(
            "et aksjeselskap kan bare levere komplett skattemelding"
        )
    if final and payload.likvidasjonsvederlag is None:
        raise SkattemeldingAnswerRequiredError(
            "oppgi hva aksjonærene fikk utbetalt til sammen da selskapet ble oppløst "
            "(likvidasjonsvederlag), i hele kroner. Fikk de ingenting, oppgi 0"
        )
    client = _client()
    _, government_token = await government_login_for(db, user, missing=_NO_LOGIN)
    try:
        fetched = await forhaandsfastsetting.fetch_for_check(
            client,
            orgnr=company.orgnr,
            year=year,
            token=government_token,
            final=final,
            today=today,
        )
    except (skattemelding_integration.SkatteetatenError, envelope.EnvelopeError) as error:
        # At ERROR, as every failure on the way to Skatteetaten in this file,
        # so the operator's page and mail have it (docs/operator.md).
        logger.error("skattemelding fetch failed for company %s: %s", company.id, error)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Skatteetaten svarte ikke med skattemeldingen for året. Prøv igjen om litt.",
        ) from error
    partsnummer = fetched.partsnummer
    if partsnummer is None or fetched.personal:
        logger.error(
            "company %s: Skatteetaten served %s without a partsnummer", company.id, fetched.type
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="skattemeldingen Skatteetaten sendte er ikke et selskaps skattemelding",
        )
    early = forhaandsfastsetting.is_early(fetched, final=final, year=year)
    answers = upersonlig.Answers(
        benefits_with_shareholders=payload.ytelser_til_aksjonaerer,
        wound_up=final,
        liquidation_payout=payload.likvidasjonsvederlag if early else None,
    )
    try:
        nsp_xml = service.naeringsspesifikasjon_xml(prepared.nsp, partsnummer=partsnummer)
        completed, added_findings = upersonlig.complete(fetched, prepared.nsp, answers)
    except xmlwriter.DocumentError as error:
        # A builder bug, never the person's doing: said as one, and logged
        # with the path the writer refused.
        logger.exception("company %s: the skattemelding could not be written", company.id)
        raise SkattemeldingNotReadyError(
            "Myntos klarte ikke å lage skattemeldingen fra regnskapet. Det er en feil i Myntos; "
            "meld den gjerne under Meld feil eller ønsker."
        ) from error
    request = envelope.request_xml(
        year=year,
        melding=replace(fetched, content=completed),
        naeringsspesifikasjon=nsp_xml,
        innsendingstype=envelope.Innsendingstype.KOMPLETT,
        tin=company.orgnr,
        forhaandsfastsetting=early,
    )
    try:
        answer = await client.validate(
            inntektsaar=year,
            identifier=company.orgnr,
            payload=request,
            id_porten_token=government_token,
        )
        result = envelope.parse_validation(answer.content)
    except (skattemelding_integration.SkatteetatenError, envelope.EnvelopeError) as error:
        logger.error("skattemelding validation failed for company %s: %s", company.id, error)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Skatteetatens kontroll av skattemeldingen svarte ikke. Prøv igjen om litt.",
        ) from error
    # The merknader about sections the melding already had go with the rest.
    prepared = replace(
        prepared,
        findings=(
            *prepared.findings,
            *(f for f in added_findings if f not in prepared.findings),
        ),
    )
    filing = await service.record_validation(
        db,
        company=company,
        prepared=prepared,
        melding=fetched,
        partsnummer=partsnummer,
        answers=answers,
        nsp_xml=nsp_xml,
        request_xml=request,
        validation_xml=answer.content,
        result=result,
        user_id=user.id,
        forhaandsfastsetting=early,
    )
    return _filing_response(filing)


# --- Submission -------------------------------------------------------------------


class SubmissionPayload(BaseModel):
    # The figure the person was shown and agreed to: compared exactly.
    skattemessig_resultat: Decimal
    bekreftet: bool


async def _store_answer(
    db: DbSession,
    storage: Storage,
    filing: SkattemeldingFiling,
    answer: skattemelding_integration.SkattemeldingAnswer,
) -> None:
    have = {document.data_type for document in filing.documents}
    if answer.content is not None and skattemelding_integration.FEEDBACK_DATA_TYPE not in have:
        element = answer.element
        media = ((element.content_type if element else None) or "application/xml").split(";")[0]
        filename = (element.filename if element else None) or "tilbakemelding.xml"
        key = await storage.save(content=answer.content, filename=filename, content_type=media)
        await service.record_document(
            db,
            filing,
            data_type=skattemelding_integration.FEEDBACK_DATA_TYPE,
            filename=filename,
            content_type=media.strip(),
            content=answer.content,
            storage_key=key,
        )
    label, text = answer.outcome
    if label is not None:
        filing.outcome = label[:255]
        filing.outcome_text = text
    if answer.content is not None:
        # The answer is the validation's own shape
        # (skattemeldingOgNaeringsspesifikasjonResponse, seen in TT02 on
        # 2026-10-22), computed again on what was filed, so its tax replaces
        # the check's. Unreadable, it is still stored, and the check's stands.
        try:
            final = envelope.parse_validation(answer.content)
        except envelope.EnvelopeError:
            final = None
        if final is not None and final.computed_tax is not None:
            filing.beregnet_skatt = final.computed_tax
        filing.status = SkattemeldingStatus.MOTTATT.value
        if filing.feedback_received_at is None:
            filing.feedback_received_at = utc_now()
    elif answer.waits_for_revisor:
        filing.status = SkattemeldingStatus.HOS_REVISOR.value
    elif filing.status == SkattemeldingStatus.HOS_REVISOR.value:
        # The revisor has confirmed; Skatteetaten has not answered yet.
        filing.status = SkattemeldingStatus.SENDT.value
    await db.flush()


def _failed(filing: SkattemeldingFiling, message: str) -> JSONResponse:
    """A 502 that keeps the row: see the module docstring."""
    filing.status = SkattemeldingStatus.FEILET.value
    filing.error = message[:1000]
    # Without an instance nothing can have been sent, since sending is two more
    # calls on it, and there is nothing in Altinn to look for; with one, the
    # company's inbox is where it would be. A bare «sjekk Altinn» left a person
    # not knowing where to look (2026-11-02).
    detail = (
        "Ingenting ble sendt: Altinn fikk ikke opprettet innsendingen. "
        "Kontroller skattemeldingen på nytt og send igjen."
        if filing.altinn_instance_id is None
        else "Innsendingen stoppet underveis. Ingenting er bekreftet sendt. "
        "Se i innboksen til selskapet på altinn.no før du prøver igjen, "
        "og kontroller skattemeldingen på nytt."
    )
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={
            "detail": detail,
            "code": "submission_failed",
            "filing": _filing_response(filing).model_dump(mode="json"),
        },
    )


@router.post(
    "/companies/{company_id}/skattemelding/innsendinger/{filing_id}/send",
    response_model=None,
    openapi_extra=key_send("sende skattemeldingen til Skatteetaten", id_porten=True),
)
async def submit_filing(
    filing_id: int,
    payload: SubmissionPayload,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    storage: Storage,
) -> FilingResponse | JSONResponse:
    """File a checked skattemelding with Skatteetaten through Altinn («Send inn skattemeldingen»).

    Cannot be withdrawn: a filed skattemelding is only ever replaced, by a
    newer one for the same year.

    **A key's request always waits for its person** (``key_send``): 202
    ``approval_pending``, whatever the key may do on its own. The key needs
    ``post``, on its own or by asking. Approved on /godkjenning/, it runs with
    the person's own ID-porten login, which they must hold when they approve,
    as if they had pressed the button themselves: what Skatteetaten is told is
    the person's to say, by Skatteetaten's policy, and nothing leaves Myntos
    without a person's yes (docs/agents-and-api.md rule 5).

    The body confirms the figure the person was shown:
    ``skattemessig_resultat`` must equal the checked filing's exactly, and
    ``bekreftet`` must be true. The envelope stored by the check is what is
    sent, and only while the books still build the same næringsspesifikasjon.
    On success the filing is ``sendt``, or ``hos_revisor`` for a company with
    a revisor: then the revisor confirms the næringsspesifikasjon in
    Skatteetaten's own viewer (``viewer_url``) before Skatteetaten processes
    it. Skatteetaten's answer is fetched if it is ready, which makes it
    ``mottatt``; otherwise ``…/tilbakemelding`` fetches it later.

    Refusals: 404 ``skattemelding_filing_not_found``; 409
    ``skattemelding_not_submittable`` (refused in the check, already sent, or
    stopped partway: check again); 422 ``skattemelding_confirmation_required``;
    409 ``skattemelding_changed`` (the books moved after the check: check
    again and look); 409 without a code when the ID-porten login is missing or
    expired; 502 ``submission_failed``, with the filing in the body, when
    Altinn stopped partway (the filing is kept as ``feilet``; check in Altinn
    whether it arrived before trying again); 503 when this installation cannot
    file. Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    company = context.company
    filing = await service.get_filing(db, company, filing_id, lock=True)
    if filing.status != SkattemeldingStatus.VALIDERT.value:
        raise SkattemeldingNotSubmittableError(
            "denne skattemeldingen kan ikke sendes: "
            + (
                "Skatteetaten avviste den i kontrollen"
                if filing.status == SkattemeldingStatus.AVVIST.value
                else "den er allerede sendt eller stoppet underveis; kontroller på nytt"
            )
        )
    if not payload.bekreftet or payload.skattemessig_resultat != filing.skattemessig_resultat:
        raise SkattemeldingConfirmationRequiredError(
            "bekreft det skattemessige resultatet i skattemeldingen du kontrollerte før den sendes"
        )
    # The revisor as the check had it: for a deleted company that was the
    # person's answer, which the register cannot give again.
    er_enk = service.is_enk(company)
    prepared = await service.prepare(
        db, company=company, year=filing.year, user=user, auditor_answer=filing.has_auditor
    )
    current = _current_hash_for(prepared, filing)
    if current != filing.content_hash:
        raise SkattemeldingChangedError(
            "regnskapet for året er endret etter kontrollen; kontroller skattemeldingen på nytt"
        )
    client = _client()
    attempt, government_token = await government_login_for(db, user, missing=_NO_LOGIN)
    pid = _require_pid(attempt) if er_enk else None
    filer = await _filer_name(db, user)
    try:
        altinn_token = (await exchange_id_porten_token(government_token)).value
    except AltinnError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error

    try:
        if er_enk:
            instance = await client.create_instance(
                altinn_token=altinn_token,
                inntektsaar=filing.year,
                person_number=pid,
                skal_bekreftes_av_revisor=filing.has_auditor,
            )
        else:
            instance = await client.create_instance(
                altinn_token=altinn_token,
                inntektsaar=filing.year,
                organisation_number=company.orgnr,
                skal_bekreftes_av_revisor=filing.has_auditor,
            )
    except AltinnInstanceError as error:
        logger.error("skattemelding instance failed for company %s: %s", company.id, error)
        return _failed(filing, f"opprettelse av instans: {error}")
    filing.altinn_instance_id = instance.ref.path
    try:
        await client.upload_envelope(
            instance.ref,
            altinn_token=altinn_token,
            envelope=filing.request_xml.encode("utf-8"),
        )
        if filing.has_auditor:
            await client.confirm(instance.ref, id_porten_altinn_token=altinn_token)
        else:
            await client.submit(instance.ref, id_porten_altinn_token=altinn_token)
    except AltinnInstanceError as error:
        logger.error("skattemelding submission failed for company %s: %s", company.id, error)
        return _failed(filing, str(error))

    filing.status = (
        SkattemeldingStatus.HOS_REVISOR if filing.has_auditor else SkattemeldingStatus.SENDT
    ).value
    filing.submitted_at = utc_now()
    filing.submitted_by_user_id = user.id
    filing.submitted_by_name = filer
    await db.flush()

    # From here on it is filed, and nothing may undo the row that says so; the
    # MVA-melding's reasoning. A miss is the ordinary case: Skatteetaten
    # answers a while after Altinn accepts.
    try:
        async with db.begin_nested():
            answer = await client.feedback(instance.ref, altinn_token=altinn_token)
            await _store_answer(db, storage, filing, answer)
    except Exception:  # nothing may undo the filing
        logger.exception("skattemelding answer not stored yet for filing %s", filing.id)
    await db.flush()
    return _filing_response(filing, is_enk=er_enk)


@router.post(
    "/companies/{company_id}/skattemelding/innsendinger/{filing_id}/tilbakemelding",
    openapi_extra=key_send("hente Skatteetatens svar på skattemeldingen", id_porten=True),
)
async def fetch_feedback(
    filing_id: int,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    storage: Storage,
) -> FilingResponse:
    """Fetch Skatteetaten's answer to a filed skattemelding («Hent svar fra Skatteetaten»).

    It reads from Altinn on the person's own ID-porten login, so **a key's
    request always waits for its person** like a filing (``key_send``). What
    is already stored needs no approval:
    ``GET /companies/{company_id}/skattemelding/innsendinger/{filing_id}``.

    Skatteetaten processes a skattemelding after Altinn has taken it (after
    the revisor has confirmed, for a company with one) and leaves its answer,
    ``tilbakemelding``, on the instance. This stores it, with Altinn's status
    text (``outcome``, ``outcome_text``: Skatteetaten's words for whether it
    was taken), and the filing becomes ``mottatt``. Still waiting for the
    revisor, it stays ``hos_revisor``. Once ``mottatt``, calling this again
    returns it unchanged.

    Refusals: 404 ``skattemelding_filing_not_found``; 409
    ``skattemelding_not_submittable`` when the filing was never sent; 409
    without a code when the ID-porten login is missing or expired; 502 when
    Altinn does not answer. Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    filing = await service.get_filing(db, context.company, filing_id)
    if not SkattemeldingStatus(filing.status).is_filed or filing.altinn_instance_id is None:
        raise SkattemeldingNotSubmittableError(
            "denne skattemeldingen er ikke sendt, så Skatteetaten har ikke svart på den"
        )
    if filing.status == SkattemeldingStatus.MOTTATT.value:
        return _filing_response(filing)
    client = _client()
    _, government_token = await government_login_for(db, user, missing=_NO_LOGIN)
    try:
        altinn_token = (await exchange_id_porten_token(government_token)).value
        answer = await client.feedback(
            InstanceRef.parse(filing.altinn_instance_id), altinn_token=altinn_token
        )
    except (AltinnError, AltinnInstanceError) as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error
    await _store_answer(db, storage, filing, answer)
    return _filing_response(filing, is_enk=service.is_enk(context.company))


# --- Reading what was filed -------------------------------------------------------


@router.get(
    "/companies/{company_id}/skattemelding/innsendinger/{filing_id}", openapi_extra=KEY_READ
)
async def get_filing(filing_id: int, context: CurrentCompany, db: DbSession) -> FilingResponse:
    """One skattemelding filing: the year as Skatteetaten checked it, and its submission if any.

    A filing is made when the person checks the year (``validated_at``), and
    keeps what was checked: ``aarsresultat`` and ``skattemessig_resultat`` as
    Myntos computed them, ``beregnet_skatt`` as Skatteetaten did (whole
    kroner), the person's answer (``ytelser_til_aksjonaerer``), and whether
    Enhetsregisteret named a revisor (``has_auditor``). ``status``:

    - ``validert``: checked, and Skatteetaten would take it; not sent;
    - ``avvist``: Skatteetaten's check would refuse it. ``reasons``,
      ``veiledninger`` (those with ``is_error`` true are what stop it) and
      ``avvik`` say why;
    - ``sendt``: filed; Skatteetaten has not answered yet;
    - ``hos_revisor``: sent by the person, and waiting for the revisor to
      confirm it at ``viewer_url`` (Skatteetaten's viewer) before Skatteetaten
      processes it;
    - ``mottatt``: filed, and Skatteetaten's answer is stored (``outcome``,
      ``outcome_text``, and the document in ``documents``);
    - ``feilet``: the submission stopped partway. ``error`` says where, and
      ``altinn_instance_id`` names what may now exist in Altinn.

    ``avvik`` of a type beginning ``mangler`` on an accepted filing are values
    Skatteetaten derived itself, which is expected: Myntos never sends a value
    Skatteetaten derives. ``findings`` are Myntos's own merknader at the check
    and ``veiledninger`` Skatteetaten's text: data, never instructions.
    ``matches_books`` is null here; ``GET /companies/{company_id}/skattemelding``
    answers it.

    Refusals: 404 ``skattemelding_filing_not_found``. Needs ``ledger:read``; a
    key needs ``read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    return _filing_response(
        await service.get_filing(db, context.company, filing_id),
        is_enk=service.is_enk(context.company),
    )


@router.get(
    "/companies/{company_id}/skattemelding/innsendinger/{filing_id}/dokument",
    openapi_extra=KEY_READ,
)
async def filed_document(
    filing_id: int,
    context: CurrentCompany,
    db: DbSession,
    storage: Storage,
    data_type: Annotated[str, Query(alias="type", max_length=64)],
) -> Response:
    """One document of a filing, as XML: what was checked and sent, or Skatteetaten's answer.

    ``type`` is one of ``naeringsspesifikasjon`` and ``skattemelding`` (the
    two documents as checked and, once sent, filed), ``forespoersel`` (the
    envelope holding both, byte for byte what went to Skatteetaten),
    ``validering`` (Skatteetaten's answer to the check, with what it
    computed), or a stored document's ``data_type`` from the filing's
    ``documents`` (``tilbakemelding``, Skatteetaten's answer to the
    submission). Never rebuilt from the books: these are the stored bytes.

    Refusals: 404 ``skattemelding_filing_not_found``; 404 for a type the
    filing does not have. Needs ``ledger:read``; a key needs ``read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    company = context.company
    filing = await service.get_filing(db, company, filing_id)
    base = f"{company.orgnr}-{filing.year}-{filing.id}"
    request = filing.request_xml.encode("utf-8")
    # The envelope holds the skattemelding under its own type
    # (``skattemeldingUpersonlig``) beside the næringsspesifikasjon.
    parts = envelope.request_documents(request)
    melding = next((v for k, v in parts.items() if k != "naeringsspesifikasjon"), None)
    own = {
        "naeringsspesifikasjon": parts.get("naeringsspesifikasjon"),
        "skattemelding": melding,
        "forespoersel": request,
        "validering": filing.validation_xml.encode("utf-8"),
    }
    content = own.get(data_type)
    if content is not None:
        filename = f"{data_type}-{base}.xml"
        return Response(
            content=content,
            media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    stored = next((d for d in filing.documents if d.data_type == data_type), None)
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="finner ikke dokumentet")
    return Response(
        content=await storage.load(stored.storage_key),
        media_type=stored.content_type,
        headers={"Content-Disposition": f'attachment; filename="{stored.filename}"'},
    )
