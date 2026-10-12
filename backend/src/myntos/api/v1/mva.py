"""The MVA-melding: see a period, check it with Skatteetaten, file it, keep the receipt.

The routes follow the filing's lifecycle, like ``/faktura``, because the law
describes the lifecycle and not a resource:

``GET /companies/{id}/mva``
    The year's periods, each with what the ledger says it comes to now, its
    deadline, and what has been filed for it.

``GET /companies/{id}/mva/termin?aar=&termin=``
    One period's melding in full, every line. This is what the person reads
    before anything is sent, and what is shown again when they confirm. The
    period is a query, not a path segment: it is not a record the company
    owns, and every company has a period called 2026/3.

``POST /companies/{id}/mva/termin/kontroll``
    Sends the melding to Skatteetaten's validation and keeps the exact bytes
    with the answer. Nothing is filed. Needs the ID-porten login.

``POST /companies/{id}/mva/innsendinger/{filing}/send``
    Files the validated melding through Altinn. Refuses when the ledger has
    moved since the check, and when the confirmation does not name the figure
    that was checked: the person confirms one melding and exactly that one is
    filed.

``POST /companies/{id}/mva/innsendinger/{filing}/tilbakemelding``
    Fetches the receipt, the payment information and the final validation
    result, and stores them. Skatteetaten produces them after the submission,
    so the send route tries once and this one is for later.

The government login is **not spent** by any of this. Fetching the receipt
after the submission needs the same login, and spending it on submit would mean
a second BankID for a document the first one already paid for. What limits it is
ID-porten's own expiry, which is short.

A failure in the middle of a submission is recorded, not rolled back. The
request's transaction would otherwise throw away the one row that says an
instance may now exist in Altinn, so the send route answers 502 by *returning* a
response rather than raising, and the row is committed with it.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from myntos import __version__
from myntos.api.deps import KEY_READ, CurrentCompany, CurrentUser, DbSession, Storage, key_write
from myntos.api.v1.skatt import government_login_for
from myntos.core.clock import utc_now
from myntos.domain.identity import service as identity_service
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.identity.models import User
from myntos.domain.ledger.errors import LedgerError
from myntos.domain.ledger.models import Company
from myntos.domain.ledger.posting import PostingError
from myntos.domain.mva import melding as melding_module
from myntos.domain.mva import service
from myntos.domain.mva.codes import SUPPORTED_CATEGORIES, Meldingskategori
from myntos.domain.mva.errors import (
    ConfirmationRequiredError,
    FilingNotSubmittableError,
    MeldingChangedError,
    MeldingNotBuildableError,
    NotVatRegisteredError,
    PeriodNotOverError,
)
from myntos.domain.mva.melding import Melding, Spesifikasjonslinje, Valideringsresultat
from myntos.domain.mva.models import FilingStatus, MvaFiling
from myntos.domain.mva.periods import PeriodType, Termin
from myntos.integrations.altinn.instances import AltinnInstanceError, InstanceRef
from myntos.integrations.altinn.tokens import AltinnError, exchange_id_porten_token
from myntos.integrations.skatteetaten import mva as mva_integration

router = APIRouter(tags=["mva"])
logger = logging.getLogger(__name__)

# Periods end at midnight in Norway, not in UTC. A melding for January may be
# checked on the first of February from 00:00 Oslo time, which is still
# 31 January for a UTC clock.
_OSLO = ZoneInfo("Europe/Oslo")

_NO_LOGIN = "logg inn med ID-porten for å kontrollere og sende mva-meldingen"


def _today() -> date:
    return utc_now().astimezone(_OSLO).date()


# --- Responses --------------------------------------------------------------------


class LineResponse(BaseModel):
    mva_kode: str
    description: str
    # "utgaaende" or "inngaaende": which side of the melding the line is on,
    # since a reverse-charge code appears on both.
    side: str
    grunnlag: Decimal | None
    sats_percent: Decimal | None
    merverdiavgift: Decimal
    spesifikasjon: str | None


class BlockerResponse(BaseModel):
    """Something that stops the period being checked or filed, in words."""

    code: str
    message: str


class FindingResponse(BaseModel):
    sti: str
    melding: str
    alvorlighet: str | None
    kode: str | None
    mva_kode: str | None
    detaljer: str | None


class DocumentResponse(BaseModel):
    data_type: str
    filename: str
    content_type: str
    byte_size: int


class FilingResponse(BaseModel):
    id: int
    company_id: int
    year: int
    termin: int
    period_label: str
    status: str
    fastsatt_merverdiavgift: Decimal
    lines: list[LineResponse]
    merknad: str | None
    validation_status: str
    # True when the validation said Skatteetaten would refuse this melding, or
    # when its answer could not be read.
    validation_blocks: bool
    findings: list[FindingResponse]
    validated_at: str
    submitted_at: str | None
    submitted_by_name: str | None
    altinn_instance_id: str | None
    feedback_received_at: str | None
    error: str | None
    payment_kid: str | None
    payment_amount: Decimal | None
    payment_due: date | None
    payment_account: str | None
    settlement_voucher_id: int | None
    documents: list[DocumentResponse]
    # Whether the ledger still says exactly what this filing says. Answered on
    # the period page, where the ledger has just been read; null elsewhere.
    # A checked melding that no longer matches cannot be sent, and the page
    # says so before the button is pressed rather than after.
    matches_ledger: bool | None = None


class PeriodRowResponse(BaseModel):
    year: int
    termin: int
    label: str
    name: str
    start: date
    end: date
    deadline: date
    is_over: bool
    fastsatt_merverdiavgift: Decimal
    line_count: int
    status: str | None
    filed_fastsatt: Decimal | None
    changed_since_filed: bool


class SettingsResponse(BaseModel):
    period_type: str
    category: str
    period_types: list[dict[str, str]]
    categories: list[dict[str, str]]


class OverviewResponse(BaseModel):
    company_id: int
    year: int
    vat_registered: bool
    settings: SettingsResponse
    periods: list[PeriodRowResponse]


class ReconciliationResponse(BaseModel):
    """The melding against the VAT accounts it should agree with.

    ``difference`` is non-zero only when something was booked to 2700 or 2710
    without a tax code, or with one on the wrong line. The register cannot do
    that; an import or a manual voucher one day might, and this is where it
    would show before it reached Skatteetaten.
    """

    output_account: Decimal
    input_account: Decimal
    difference: Decimal


class PeriodResponse(BaseModel):
    company_id: int
    year: int
    termin: int
    label: str
    name: str
    start: date
    end: date
    deadline: date
    category: str
    orgnr: str
    lines: list[LineResponse]
    output_vat: Decimal
    input_vat: Decimal
    fastsatt_merverdiavgift: Decimal
    reconciliation: ReconciliationResponse
    blockers: list[BlockerResponse]
    # Whether this installation can check and file at all. False when the MVA
    # app or the validation URL is not configured: the page then offers the XML
    # for upload by hand instead of a button that answers 503.
    submission_available: bool
    changed_since_filed: bool
    filings: list[FilingResponse]


_CODE_DESCRIPTIONS: dict[str, str] = {}


def _code_description(code: str) -> str:
    if not _CODE_DESCRIPTIONS:
        from myntos.domain.ledger.chart import STANDARD_TAX_CODES

        _CODE_DESCRIPTIONS.update({seed.code: seed.description for seed in STANDARD_TAX_CODES})
    return _CODE_DESCRIPTIONS.get(code, f"Mva-kode {code}")


def _line_response(line: Spesifikasjonslinje) -> LineResponse:
    return LineResponse(
        mva_kode=line.mva_kode,
        description=_code_description(line.mva_kode),
        side="inngaaende" if line.is_deduction else "utgaaende",
        grunnlag=line.grunnlag,
        sats_percent=None if line.sats is None else (line.sats * 100).quantize(Decimal("0.01")),
        merverdiavgift=line.merverdiavgift,
        spesifikasjon=line.spesifikasjon,
    )


def _snapshot_line(raw: dict[str, object]) -> LineResponse:
    def dec(value: object) -> Decimal | None:
        return None if value is None else Decimal(str(value))

    sats = dec(raw.get("sats"))
    grunnlag = dec(raw.get("grunnlag"))
    code = str(raw.get("mva_kode"))
    return LineResponse(
        mva_kode=code,
        description=_code_description(code),
        side="inngaaende" if grunnlag is None else "utgaaende",
        grunnlag=grunnlag,
        sats_percent=None if sats is None else (sats * 100).quantize(Decimal("0.01")),
        merverdiavgift=dec(raw.get("merverdiavgift")) or Decimal("0.00"),
        spesifikasjon=None if raw.get("spesifikasjon") is None else str(raw["spesifikasjon"]),
    )


def _findings(result: Valideringsresultat) -> list[FindingResponse]:
    return [
        FindingResponse(
            sti=avvik.sti,
            melding=avvik.melding,
            alvorlighet=avvik.alvorlighet,
            kode=avvik.kode,
            mva_kode=avvik.mva_kode,
            detaljer=avvik.detaljer,
        )
        for avvik in result.avvik
    ]


def _filing_response(filing: MvaFiling, *, current_hash: str | None = None) -> FilingResponse:
    termin = service.termin_of_filing(filing)
    result = melding_module.parse_validation(filing.validation_xml.encode("utf-8"))
    return FilingResponse(
        id=filing.id,
        company_id=filing.company_id,
        year=filing.year,
        termin=filing.period_index,
        period_label=termin.label,
        status=filing.status,
        fastsatt_merverdiavgift=filing.fastsatt_merverdiavgift,
        lines=[_snapshot_line(raw) for raw in filing.lines],
        merknad=filing.merknad,
        validation_status=filing.validation_status,
        validation_blocks=result.blocks_submission,
        findings=_findings(result),
        validated_at=filing.validated_at.isoformat(),
        submitted_at=filing.submitted_at.isoformat() if filing.submitted_at else None,
        submitted_by_name=filing.submitted_by_name,
        altinn_instance_id=filing.altinn_instance_id,
        feedback_received_at=(
            filing.feedback_received_at.isoformat() if filing.feedback_received_at else None
        ),
        error=filing.error,
        payment_kid=filing.payment_kid,
        payment_amount=filing.payment_amount,
        payment_due=filing.payment_due,
        payment_account=filing.payment_account,
        settlement_voucher_id=filing.settlement_transaction_pk,
        documents=[
            DocumentResponse(
                data_type=document.data_type,
                filename=document.filename,
                content_type=document.content_type,
                byte_size=document.byte_size,
            )
            for document in filing.documents
        ],
        matches_ledger=None if current_hash is None else filing.content_hash == current_hash,
    )


def _settings_response(company: Company) -> SettingsResponse:
    return SettingsResponse(
        period_type=service.period_type_of(company).value,
        category=service.category_of(company).value,
        period_types=[{"value": p.value, "label": p.label} for p in PeriodType],
        categories=[
            {"value": c.value, "label": _CATEGORY_LABELS[c]}
            for c in Meldingskategori
            if c in SUPPORTED_CATEGORIES
        ],
    )


_CATEGORY_LABELS = {
    Meldingskategori.ALMINNELIG: "Alminnelig næring",
    Meldingskategori.PRIMAERNAERING: "Primærnæring (jord, skog, fiske)",
    Meldingskategori.KOMPENSASJON: "Kompensasjon",
    Meldingskategori.OMVENDT_AVGIFTSPLIKT: "Omvendt avgiftsplikt",
    Meldingskategori.EHANDEL: "E-handel (VOEC)",
}


def _blockers(company: Company, termin: Termin, melding: Melding | None) -> list[BlockerResponse]:
    """What stands between this period and a filing, as the backend sees it.

    The same checks the actions make, offered up front so the page can disable
    the button and say why instead of letting someone press it to find out.
    """
    blockers: list[BlockerResponse] = []
    if not company.vat_registered:
        blockers.append(
            BlockerResponse(
                code="not_vat_registered",
                message=(
                    "Selskapet er ikke merket som registrert i Merverdiavgiftsregisteret. "
                    "Et selskap som ikke er registrert, sender ikke mva-melding."
                ),
            )
        )
    if not service.is_past(termin, _today()):
        blockers.append(
            BlockerResponse(
                code="period_not_over",
                message=(
                    f"Terminen slutter {termin.end.strftime('%d.%m.%Y')}, "
                    "og meldingen kan sendes fra dagen etter."
                ),
            )
        )
    if melding is None:
        blockers.append(
            BlockerResponse(
                code="not_buildable",
                message="Regnskapet har en mva-kode som ikke kan rapporteres ennå.",
            )
        )
    return blockers


def _require_fileable(company: Company, termin: Termin) -> None:
    if not company.vat_registered:
        raise NotVatRegisteredError(
            "selskapet er ikke registrert i Merverdiavgiftsregisteret, så det sendes ingen "
            "mva-melding"
        )
    if not service.is_past(termin, _today()):
        raise PeriodNotOverError(
            f"terminen slutter {termin.end.strftime('%d.%m.%Y')}; meldingen kan sendes fra "
            "dagen etter"
        )


def _submission_configured() -> bool:
    try:
        mva_integration.build_client()
    except mva_integration.MvaNotConfiguredError:
        return False
    return True


def _client() -> mva_integration.MvaMeldingClient:
    try:
        return mva_integration.build_client()
    except mva_integration.MvaNotConfiguredError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="innsending av mva-melding er ikke satt opp på denne installasjonen",
        ) from None


async def _filer_name(db: DbSession, user: User) -> str:
    """Who the envelope says created the filing: ID-porten's name if we have it."""
    identity = await identity_service.government_identity_for(db, user.id)
    if identity is not None and identity.full_name:
        return identity.full_name
    return user.full_name


# --- Overview and one period ------------------------------------------------------


@router.get("/companies/{company_id}/mva", openapi_extra=KEY_READ)
async def overview(
    context: CurrentCompany,
    db: DbSession,
    aar: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> OverviewResponse:
    """Every VAT period (termin) of one year: what each comes to, its deadline, what was filed.

    A Norwegian business registered for VAT (merverdiavgift, «mva») reports it
    to Skatteetaten once per period in the MVA-melding, the VAT return. The
    period is a «termin»: two months unless Skatteetaten has granted the
    company one month or a whole year. ``settings`` says which this company
    has; ``settings.period_types`` and ``settings.categories`` list the values
    ``PATCH /companies/{company_id}/mva/innstillinger`` accepts.

    ``aar`` is the year, this year in Norway by default. Each row of
    ``periods`` is one termin:

    - ``termin``: its number within the year (1 to 6 when two-monthly), which
      is what every other MVA route takes; ``label`` is how a person reads it
      («5. termin 2026 (september-oktober)»).
    - ``deadline``: the last day to file and to pay. Moved off weekends, not
      off public holidays, so it is never later than the real one.
    - ``is_over``: whether the period has ended. A melding can only be filed
      from the day after.
    - ``fastsatt_merverdiavgift``: what the books say the period comes to now,
      as a decimal string. Positive is owed to Skatteetaten, negative is a
      refund.
    - ``status``: the latest filing's state, ``null`` when nothing has been
      checked or sent; otherwise ``validert``, ``avvist``, ``sendt``,
      ``mottatt`` or ``feilet`` (see
      ``GET /companies/{company_id}/mva/innsendinger/{filing_id}``).
    - ``filed_fastsatt`` and ``changed_since_filed``: the figure last actually
      filed, and whether the books for the period have changed since. True
      means a corrected melding is due; a new one replaces the old.

    ``vat_registered`` false means the company is not in
    Merverdiavgiftsregisteret and files no MVA-melding at all.

    Open one period with ``GET /companies/{company_id}/mva/termin``. Needs
    ``ledger:read``, which every role has; a key needs ``read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    company = context.company
    year = aar or _today().year
    today = _today()
    rows: list[PeriodRowResponse] = []
    for summary in await service.year_overview(db, company, year):
        latest = summary.latest
        rows.append(
            PeriodRowResponse(
                year=summary.termin.year,
                termin=summary.termin.index,
                label=summary.termin.label,
                name=summary.termin.name,
                start=summary.termin.start,
                end=summary.termin.end,
                deadline=summary.deadline,
                is_over=service.is_past(summary.termin, today),
                fastsatt_merverdiavgift=summary.melding.fastsatt_merverdiavgift,
                line_count=len(summary.melding.lines),
                status=latest.status if latest is not None else None,
                filed_fastsatt=(
                    summary.filed.fastsatt_merverdiavgift if summary.filed is not None else None
                ),
                changed_since_filed=summary.changed_since_filed,
            )
        )
    return OverviewResponse(
        company_id=company.id,
        year=year,
        vat_registered=company.vat_registered,
        settings=_settings_response(company),
        periods=rows,
    )


class SettingsPayload(BaseModel):
    period_type: str | None = Field(default=None, max_length=32)
    category: str | None = Field(default=None, max_length=32)


@router.patch(
    "/companies/{company_id}/mva/innstillinger", openapi_extra=key_write("endre mva-innstillingene")
)
async def update_settings(
    payload: SettingsPayload, context: CurrentCompany, db: DbSession
) -> SettingsResponse:
    """Change the period length or the category.

    ``organization:manage``, like VAT registration on ``/organisasjon``: how
    often a company files is something Skatteetaten decided about the company,
    not a bookkeeping entry.
    """
    require_permission(context.membership.role, Permission.ORGANIZATION_MANAGE)
    service.update_settings(
        context.company, period_type=payload.period_type, category=payload.category
    )
    await db.flush()
    return _settings_response(context.company)


_Year = Annotated[int, Query(ge=2000, le=2100)]
_Index = Annotated[int, Query(ge=1, le=12)]


@router.get("/companies/{company_id}/mva/termin", openapi_extra=KEY_READ)
async def period(
    aar: _Year, termin: _Index, context: CurrentCompany, db: DbSession
) -> PeriodResponse:
    """One period's MVA-melding in full, as the books have it now, and what stops it being filed.

    This is what the person has to see before a melding is sent, so it is what
    an agent shows them. ``aar`` is the year and ``termin`` the period's number
    within it: 1 to 6 when two-monthly, 1 to 12 monthly, 1 yearly
    (``GET /companies/{company_id}/mva`` lists them).

    The melding is every posted entry with a VAT code whose posting date falls
    in the period, summed per VAT code. Each of ``lines`` has ``mva_kode`` (the
    SAF-T VAT code Skatteetaten uses), ``description``, ``side``
    (``utgaaende``: VAT charged on sales, owed; ``inngaaende``: VAT paid on
    purchases and deducted, reported as a negative amount with no basis or
    rate), ``grunnlag`` (the amount the VAT was calculated on),
    ``sats_percent``, ``merverdiavgift``, and ``spesifikasjon`` on the lines
    that come from VAT adjustments. A purchase from abroad with reverse charge
    appears on both sides. ``output_vat`` and ``input_vat`` are the two totals
    and ``fastsatt_merverdiavgift`` the result: positive is owed («Å betale»),
    negative a refund («Til gode»). Amounts are decimal strings. A period with
    no lines is still filed, with zero.

    ``blockers`` is what stops a filing, each a ``code`` and the Norwegian
    ``message`` the page shows:

    - ``not_vat_registered``: the company is not marked as registered in
      Merverdiavgiftsregisteret. If that is wrong, the person corrects it in
      the company settings.
    - ``period_not_over``: filing opens the day after the period ends.
    - ``not_buildable``: the books hold a VAT code the melding cannot report.
      A voucher has to be corrected; ``lines`` is then empty.

    ``reconciliation.difference`` other than zero means something was booked
    to the VAT accounts 2700 or 2710 without a VAT code, so the melding and the
    accounts disagree. Find that voucher before anything is filed.

    ``filings`` is every check and submission for the period, newest first;
    ``matches_ledger`` on each says whether the books still say what it says.
    ``changed_since_filed`` is true when a filed period has changed since. A
    new melding for the period then replaces the earlier one at Skatteetaten.
    ``submission_available`` false means this installation cannot file; the
    melding can be downloaded with ``GET /companies/{company_id}/mva/termin/xml``
    and uploaded in Skatteetaten's own portal instead.

    Refusals: 422 ``invalid_period`` when the termin does not exist for the
    company's period length. Needs ``ledger:read``; a key needs ``read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    company = context.company
    period_ = service.termin_of(company, aar, termin)
    try:
        melding: Melding | None = await service.build_melding(db, company, period_)
    except MeldingNotBuildableError:
        melding = None
    balances = await service.vat_balances(db, company, period_, include_settlement=False)
    filings = await service.filings_for(db, company, period_)
    filed = service.latest_filed(filings)
    fastsatt = melding.fastsatt_merverdiavgift if melding is not None else Decimal("0.00")
    return PeriodResponse(
        company_id=company.id,
        year=period_.year,
        termin=period_.index,
        label=period_.label,
        name=period_.name,
        start=period_.start,
        end=period_.end,
        deadline=period_.deadline(service.category_of(company)),
        category=service.category_of(company).value,
        orgnr=company.orgnr,
        lines=[_line_response(line) for line in melding.lines] if melding is not None else [],
        output_vat=melding.output_vat if melding is not None else Decimal("0.00"),
        input_vat=melding.input_vat if melding is not None else Decimal("0.00"),
        fastsatt_merverdiavgift=fastsatt,
        reconciliation=ReconciliationResponse(
            output_account=balances.output,
            input_account=balances.input,
            difference=balances.net - fastsatt,
        ),
        blockers=_blockers(company, period_, melding),
        submission_available=_submission_configured(),
        changed_since_filed=(
            filed is not None
            and melding is not None
            and filed.content_hash != service.content_hash(melding)
        ),
        filings=[
            _filing_response(
                filing,
                current_hash=None if melding is None else service.content_hash(melding),
            )
            for filing in filings
        ],
    )


def _xml_download(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/companies/{company_id}/mva/termin/xml", openapi_extra=KEY_READ)
async def period_xml(
    aar: _Year, termin: _Index, context: CurrentCompany, db: DbSession
) -> Response:
    """The melding as XML, as the ledger has it now, for reading or uploading by hand.

    Skatteetaten's own portal accepts a melding file from an accounting system,
    so this is also the way to file while this installation cannot submit.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    company = context.company
    period_ = service.termin_of(company, aar, termin)
    melding = await service.build_melding(db, company, period_)
    content = melding_module.melding_xml(
        melding, reference=str(uuid.uuid4()), system_version=__version__
    )
    return _xml_download(content, f"mva-melding-{company.orgnr}-{aar}-{termin}.xml")


# --- Validation -------------------------------------------------------------------


class ValidationPayload(BaseModel):
    aar: int = Field(ge=2000, le=2100)
    termin: int = Field(ge=1, le=12)
    # Free text sent with the melding. Skatteetaten asks for one when a figure
    # needs explaining, and the validation says so when it does.
    merknad: str | None = Field(default=None, max_length=4000)


@router.post("/companies/{company_id}/mva/termin/kontroll")
async def validate_period(
    payload: ValidationPayload,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
) -> FilingResponse:
    """Have Skatteetaten check a period's melding («Kontroller hos Skatteetaten»). Files nothing.

    **No key can call this** (403 ``not_for_agent_keys``). It runs on the
    person's own ID-porten login, and what Skatteetaten is told is the
    person's to say, by Skatteetaten's policy. An agent prepares and reviews
    the period with the read routes and asks the person to check and file on
    the MVA page.

    It builds the melding as the books have it now, sends it to Skatteetaten's
    validation with ``merknad`` (optional free text, which the check asks for
    when a figure needs explaining), and stores the exact XML with the answer
    as a new filing. The filing is ``validert`` when it may be sent (verdict
    ``ingen avvik``, or ``avvikende skattemelding``: may be sent, may be
    followed up) and ``avvist`` when Skatteetaten would refuse it, with
    ``findings`` saying why; then the books are corrected and the period
    checked again. A submission later sends exactly these bytes.

    Refusals: 409 ``not_vat_registered``; 409 ``period_not_over``; 422
    ``melding_not_buildable`` (a VAT code the melding cannot carry); 422
    ``invalid_period``; 409 without a code when the person has no valid
    ID-porten login (log in again on the page); 502 when Skatteetaten does not
    answer (try again later); 503 when this installation cannot file. Needs
    ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    company = context.company
    period_ = service.termin_of(company, payload.aar, payload.termin)
    _require_fileable(company, period_)
    melding = await service.build_melding(db, company, period_)
    client = _client()

    reference = str(uuid.uuid4())
    merknad = (payload.merknad or "").strip() or None
    xml = melding_module.melding_xml(
        melding, reference=reference, system_version=__version__, merknad=merknad
    )
    _, government_token = await government_login_for(db, user, missing=_NO_LOGIN)
    try:
        answer = await client.validate(xml, id_porten_token=government_token)
    except mva_integration.MvaError as error:
        logger.warning("MVA validation failed for company %s: %s", company.id, error)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Skatteetatens kontroll av mva-meldingen svarte ikke. Prøv igjen om litt.",
        ) from error

    result = melding_module.parse_validation(answer.content)
    filing = await service.record_validation(
        db,
        company=company,
        melding=melding,
        reference=reference,
        xml=xml,
        validation_xml=answer.content,
        result=result,
        merknad=merknad,
        user_id=user.id,
    )
    return _filing_response(filing)


# --- Submission -------------------------------------------------------------------


class SubmissionPayload(BaseModel):
    # The figure the person was shown and agreed to. Compared exactly with the
    # validated one: a confirmation of a different number confirms nothing.
    fastsatt_merverdiavgift: Decimal
    bekreftet: bool


async def _store_feedback(
    db: DbSession,
    storage: Storage,
    filing: MvaFiling,
    feedback: mva_integration.MvaFeedback,
) -> None:
    have = {document.data_type for document in filing.documents}
    for data_type, content in feedback.documents.items():
        if data_type in have:
            continue
        element = feedback.instance.element_of_type(data_type)
        content_type = (element.content_type if element else None) or (
            "application/pdf" if data_type == "kvittering" else "application/xml"
        )
        media = content_type.split(";")[0].strip()
        extension = "pdf" if media == "application/pdf" else "xml"
        filename = (element.filename if element else None) or f"{data_type}.{extension}"
        key = await storage.save(content=content, filename=filename, content_type=media)
        await service.record_document(
            db,
            filing,
            data_type=data_type,
            filename=filename,
            content_type=media,
            content=content,
            storage_key=key,
        )
        if data_type == "betalingsinformasjon":
            try:
                service.record_payment(filing, melding_module.parse_payment(content))
            except melding_module.MeldingError:
                logger.warning("betalingsinformasjon for filing %s was unreadable", filing.id)
    if feedback.is_complete and filing.feedback_received_at is None:
        filing.feedback_received_at = utc_now()
        filing.status = FilingStatus.MOTTATT.value
    await db.flush()


def _failed(filing: MvaFiling, message: str) -> JSONResponse:
    """A 502 that keeps the row: see the module docstring."""
    filing.status = FilingStatus.FEILET.value
    filing.error = message[:1000]
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={
            "detail": (
                "Innsendingen stoppet underveis. Ingenting er bekreftet sendt. "
                "Sjekk Altinn før du prøver igjen, og kontroller meldingen på nytt."
            ),
            "code": "submission_failed",
            "filing": _filing_response(filing).model_dump(mode="json"),
        },
    )


@router.post("/companies/{company_id}/mva/innsendinger/{filing_id}/send", response_model=None)
async def submit_filing(
    filing_id: int,
    payload: SubmissionPayload,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    storage: Storage,
) -> FilingResponse | JSONResponse:
    """File a checked melding with Skatteetaten through Altinn («Send inn mva-meldingen»).

    Cannot be withdrawn: a filed melding is only ever replaced, by a newer one
    for the same period.

    **No key can call this** (403 ``not_for_agent_keys``). Filing is the
    person's, on their own ID-porten login, by Skatteetaten's policy. An agent
    asks the person to file (``POST /companies/{company_id}/godkjenninger/innsending``),
    and the person checks and sends it from the MVA page.

    The body confirms the figure the person was shown: ``fastsatt_merverdiavgift``
    must equal the checked filing's exactly, and ``bekreftet`` must be true.
    The XML stored by the check is what is sent, and only while the books still
    say the same. On success the filing is ``sendt``; the period's VAT accounts
    2700 and 2710 are moved onto the settlement account 2740 in a voucher of its
    own (``settlement_voucher_id``; ``error`` says so if that voucher could not
    be posted, while the melding stays filed); and Skatteetaten's receipt and
    payment information are fetched if they are ready, which makes it
    ``mottatt``. Otherwise ``…/tilbakemelding`` fetches them later.

    Refusals: 409 ``filing_not_submittable`` (refused in the check, already
    sent, or stopped partway: check again); 422 ``confirmation_required``; 409
    ``melding_changed`` (the books moved after the check: check again and
    look); 409 ``not_vat_registered`` or ``period_not_over``; 409 without a
    code when the ID-porten login is missing or expired; 502
    ``submission_failed``, with the filing in the body, when Altinn stopped
    partway (the filing is kept as ``feilet``; check in Altinn whether the
    melding arrived before trying again); 503 when this installation cannot
    file. Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    company = context.company
    filing = await service.get_filing(db, company, filing_id, lock=True)
    if filing.status != FilingStatus.VALIDERT.value:
        raise FilingNotSubmittableError(
            "denne meldingen kan ikke sendes: "
            + (
                "Skatteetaten avviste den i kontrollen"
                if filing.status == FilingStatus.AVVIST.value
                else "den er allerede sendt eller stoppet underveis; kontroller på nytt"
            )
        )
    if not payload.bekreftet or payload.fastsatt_merverdiavgift != filing.fastsatt_merverdiavgift:
        raise ConfirmationRequiredError(
            "bekreft beløpet i meldingen du kontrollerte før den sendes"
        )
    termin = service.termin_of_filing(filing)
    _require_fileable(company, termin)
    current = await service.build_melding(db, company, termin)
    if service.content_hash(current) != filing.content_hash:
        raise MeldingChangedError(
            "regnskapet for terminen er endret etter kontrollen; kontroller meldingen på nytt"
        )
    client = _client()
    _, government_token = await government_login_for(db, user, missing=_NO_LOGIN)
    filer = await _filer_name(db, user)

    try:
        altinn_token = (await exchange_id_porten_token(government_token)).value
    except AltinnError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error

    envelope = melding_module.envelope_xml(current, created_by=filer, created_at=utc_now())
    try:
        instance = await client.create_instance(
            altinn_token=altinn_token, organisation_number=company.orgnr
        )
    except AltinnInstanceError as error:
        logger.warning("MVA instance creation failed for company %s: %s", company.id, error)
        return _failed(filing, f"opprettelse av instans: {error}")
    filing.altinn_instance_id = instance.ref.path
    try:
        await client.upload_melding(
            instance,
            altinn_token=altinn_token,
            metadata=envelope,
            melding=filing.melding_xml.encode("utf-8"),
        )
        await client.submit(instance.ref, id_porten_altinn_token=altinn_token)
    except AltinnInstanceError as error:
        logger.warning("MVA submission failed for company %s: %s", company.id, error)
        return _failed(filing, str(error))

    filing.status = FilingStatus.SENDT.value
    filing.submitted_at = utc_now()
    filing.submitted_by_user_id = user.id
    filing.submitted_by_name = filer
    await db.flush()

    # From here on the melding is filed, and nothing may undo the row that says
    # so. An exception would roll the request back, and a filed melding that
    # Myntos believes unfiled is one it offers to file again. So both steps
    # below are allowed to fail, and say so on the filing instead.
    try:
        async with db.begin_nested():
            settlement = await service.post_settlement(
                db, company=company, termin=termin, created_by_user_id=user.id
            )
        filing.settlement_transaction_pk = settlement.id if settlement is not None else None
    except (LedgerError, PostingError) as error:
        logger.warning("MVA settlement failed for filing %s: %s", filing.id, error)
        filing.error = f"Meldingen er sendt, men oppgjøret mot 2740 ble ikke ført: {error}"[:1000]

    # The payment information is produced while the submission completes, so
    # it is usually there already. The receipt may not be; a miss here is the
    # ordinary case and /tilbakemelding fetches the rest.
    try:
        async with db.begin_nested():
            feedback = await client.feedback(instance.ref, altinn_token=altinn_token)
            await _store_feedback(db, storage, filing, feedback)
    except Exception:  # see above: nothing may undo the filing
        logger.exception("MVA feedback not stored yet for filing %s", filing.id)
    await db.flush()
    return _filing_response(filing)


@router.post("/companies/{company_id}/mva/innsendinger/{filing_id}/tilbakemelding")
async def fetch_feedback(
    filing_id: int,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    storage: Storage,
) -> FilingResponse:
    """Fetch Skatteetaten's receipt and payment information for a filed melding («Hent kvittering»).

    **No key can call this** (403 ``not_for_agent_keys``). It reads from Altinn
    on the person's own ID-porten login. An agent reads what is already stored
    with ``GET /companies/{company_id}/mva/innsendinger/{filing_id}`` and
    ``…/dokument``, and asks the person to press «Hent kvittering» on the MVA
    page when the receipt is missing.

    Skatteetaten produces three documents after a submission: the receipt
    (``kvittering``, a PDF), the payment information (``betalingsinformasjon``)
    and the final validation result (``valideringsresultat``). The submission
    tries to fetch them once; this is for later. Each is stored, and the KID,
    amount, due date and account to pay to are read onto the filing. When all
    three are in, the filing is ``mottatt``, and calling this again returns it
    unchanged.

    Refusals: 404 ``filing_not_found``; 409 ``filing_not_submittable`` when the
    filing was never sent; 409 without a code when the ID-porten login is
    missing or expired; 502 when Altinn does not answer. Needs
    ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    filing = await service.get_filing(db, context.company, filing_id)
    if not FilingStatus(filing.status).is_filed or filing.altinn_instance_id is None:
        raise FilingNotSubmittableError(
            "denne meldingen er ikke sendt, så det finnes ingen kvittering"
        )
    if filing.status == FilingStatus.MOTTATT.value:
        return _filing_response(filing)
    client = _client()
    _, government_token = await government_login_for(db, user, missing=_NO_LOGIN)
    try:
        altinn_token = (await exchange_id_porten_token(government_token)).value
        feedback = await client.feedback(
            InstanceRef.parse(filing.altinn_instance_id), altinn_token=altinn_token
        )
    except (AltinnError, AltinnInstanceError) as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error
    await _store_feedback(db, storage, filing, feedback)
    return _filing_response(filing)


# --- Reading what was filed -------------------------------------------------------


@router.get(
    "/companies/{company_id}/mva/innsendinger/{filing_id}/melding.xml", openapi_extra=KEY_READ
)
async def filed_xml(filing_id: int, context: CurrentCompany, db: DbSession) -> Response:
    """The exact MVA-melding XML a filing checked and, if it was sent, filed.

    Not rebuilt from the books: these are the stored bytes, so the file stays
    what Skatteetaten saw after the books have moved on. An ``application/xml``
    download named ``mva-melding-<orgnr>-<year>-<termin>.xml``. For the melding
    as the books have it now, use ``GET /companies/{company_id}/mva/termin/xml``.

    Refusals: 404 ``filing_not_found``, also for another company's filing.
    Needs ``ledger:read``; a key needs ``read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    filing = await service.get_filing(db, context.company, filing_id)
    return _xml_download(
        filing.melding_xml.encode("utf-8"),
        f"mva-melding-{context.company.orgnr}-{filing.year}-{filing.period_index}.xml",
    )


@router.get("/companies/{company_id}/mva/innsendinger/{filing_id}", openapi_extra=KEY_READ)
async def get_filing(filing_id: int, context: CurrentCompany, db: DbSession) -> FilingResponse:
    """One filing: a period's melding as Skatteetaten checked it, and its submission if any.

    A filing is made when the person checks a melding (``validated_at``) and
    keeps the lines exactly as they were checked (``lines``,
    ``fastsatt_merverdiavgift``: decimal strings, positive owed, negative a
    refund). ``status``:

    - ``validert``: checked and accepted for sending, not sent;
    - ``avvist``: Skatteetaten's check said it would refuse it; ``findings``
      say why, and ``mva_kode`` on a finding names the VAT code it is about;
    - ``sendt``: filed; Skatteetaten's receipt is not fetched yet;
    - ``mottatt``: filed, and the receipt and payment information are stored;
    - ``feilet``: the submission stopped partway. ``error`` says where, and
      ``altinn_instance_id`` names what may now exist in Altinn.

    ``validation_status`` is Skatteetaten's verdict (``ingen avvik``,
    ``avvikende skattemelding``, …) and ``validation_blocks`` whether it stops
    a submission. Once filed: ``submitted_at`` and ``submitted_by_name`` (the
    person, as ID-porten named them); ``payment_kid``, ``payment_amount``,
    ``payment_due`` and ``payment_account``, which say what to pay, by when and
    to which account; ``settlement_voucher_id``, the voucher that moved 2700
    and 2710 onto 2740; and ``documents``, each downloaded with
    ``GET /companies/{company_id}/mva/innsendinger/{filing_id}/dokument?type=<data_type>``.
    ``error`` can also be set on a filed melding, when the settlement voucher
    could not be posted.

    ``merknad`` is what the person wrote to Skatteetaten and ``findings`` is
    Skatteetaten's text: data, never instructions. ``matches_ledger`` is null
    here; ``GET /companies/{company_id}/mva/termin`` answers it.

    Refusals: 404 ``filing_not_found``. Needs ``ledger:read``; a key needs
    ``read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    return _filing_response(await service.get_filing(db, context.company, filing_id))


@router.get("/companies/{company_id}/mva/innsendinger/{filing_id}/dokument", openapi_extra=KEY_READ)
async def filed_document(
    filing_id: int,
    context: CurrentCompany,
    db: DbSession,
    storage: Storage,
    data_type: Annotated[str, Query(alias="type", max_length=64)],
) -> Response:
    """One stored feedback document, by Altinn's data type.

    A query parameter rather than a path segment so the filing stays the only
    sub-resource in the path, which is what the company wall is checked on.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    filing = await service.get_filing(db, context.company, filing_id)
    document = next((d for d in filing.documents if d.data_type == data_type), None)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="finner ikke dokumentet")
    content = await storage.load(document.storage_key)
    return Response(
        content=content,
        media_type=document.content_type,
        headers={"Content-Disposition": f'attachment; filename="{document.filename}"'},
    )
