"""SAF-T Financial: check a financial year, then download it.

SAF-T is not a periodic filing. The file is produced when Skatteetaten asks for
it at a bokettersyn and uploaded by hand through Altinn form RF-1363, so there is
nothing here to submit, only a file to hand over:

``GET /companies/{id}/saft/kontroll?aar=&versjon=``
    What the cross-reference check finds in the year, without producing a file.
    This is what the page shows before offering the download.

``GET /companies/{id}/saft/fil?aar=&versjon=``
    The file itself, named the way RF-1363 wants it. Refused with the findings
    when any of them would fail at the auditor: a file that is wrong in ways the
    XSD does not see looks like compliance and is not.

Reading the books is all either route needs (``LEDGER_READ``), deliberately: the
person most likely to want this file is a revisor who was given read access and
nothing else.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from myntos import __version__
from myntos.api.deps import KEY_READ, CurrentCompany, CurrentUser, DbSession
from myntos.core.clock import utc_now
from myntos.domain.identity.access import Permission, require_permission
from myntos.saft import export, service
from myntos.saft.check import Finding, Severity
from myntos.saft.errors import UnsupportedSaftVersionError

router = APIRouter(tags=["saft"])

_Year = Annotated[int, Query(ge=2000, le=2100)]
_Version = Annotated[export.SaftVersion | None, Query()]


class FindingResponse(BaseModel):
    severity: str
    code: str
    message: str


class CheckResponse(BaseModel):
    # Named, like every company-scoped answer, so the frontend's company wall
    # can refuse it if it ever arrives for the wrong set of books.
    company_id: int
    year: int
    version: str
    versions: list[str]
    exportable: bool
    number_of_entries: int
    total_debit: Decimal
    total_credit: Decimal
    findings: list[FindingResponse]


def _version(year: int, asked: export.SaftVersion | None) -> export.SaftVersion:
    if year < export.FIRST_EXPORTABLE_YEAR:
        raise UnsupportedSaftVersionError(
            f"SAF-T for {year} skal leveres i et eldre format enn Myntos lager; "
            f"Myntos eksporterer fra {export.FIRST_EXPORTABLE_YEAR}"
        )
    version = asked or export.version_for_year(year)
    if version not in export.allowed_versions(year):
        raise UnsupportedSaftVersionError(
            f"SAF-T for {year} må leveres i versjon "
            + " eller ".join(v.value for v in export.allowed_versions(year))
        )
    return version


def _findings(findings: list[Finding]) -> list[FindingResponse]:
    return [
        FindingResponse(severity=f.severity.value, code=f.code, message=f.message) for f in findings
    ]


@router.get("/companies/{company_id}/saft/kontroll", openapi_extra=KEY_READ)
async def check_year(
    aar: _Year,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    versjon: _Version = None,
) -> CheckResponse:
    """Check one year's SAF-T file before making it: the findings and totals, no file.

    SAF-T Financial is the standard XML export of a company's whole general
    ledger for one financial year, in a format set by Skatteetaten (the
    Norwegian Tax Administration). It is not filed on a schedule: a company
    produces it when Skatteetaten asks for it, usually at a tax audit
    (bokettersyn), and the person uploads it themselves in Altinn on form
    RF-1363. It is also how books move to another accounting program.

    Altinn checks only the XML schema. This runs the cross-reference check an
    auditor would: every account, customer, supplier, VAT code and dimension a
    line points to is in the file's master data, every account has a reporting
    code (næringsspesifikasjon) from the year's published list, vouchers and
    the ledger balance, the opening balance sums to zero, and no identifier is
    longer than the schema allows. ``exportable`` is false while any finding
    has severity ``feil``, and ``GET /saft/fil`` then refuses. A ``merknad`` is
    a note, such as ``udisponert_resultat`` (an earlier year was not closed in
    Myntos) or ``inngaende_balanse_fra_bilag`` (the opening voucher appears as
    opening balances rather than as an entry). Messages are Norwegian and may
    quote names from the books; decide from ``code``.

    ``aar`` is the year, 2025 or later: earlier years belong to an older format
    Myntos never had data for. ``versjon`` is optional; ``1.30`` is the default
    up to 2026 with ``1.40`` allowed beside it, and from 2027 only ``1.40`` is
    accepted. ``versions`` lists what the year allows. Refusals: 404
    ``saft_year_not_found`` (no books for the year) and 422
    ``unsupported_saft_version``. Needs read access only, on purpose: the
    person who most often needs the file is an auditor with read access.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    version = _version(aar, versjon)
    snapshot = await service.load(
        db,
        company=context.company,
        year=aar,
        exported_by=user,
        software_version=__version__,
        created_at=utc_now(),
    )
    findings = service.findings(snapshot)
    return CheckResponse(
        company_id=context.company.id,
        year=aar,
        version=version.value,
        versions=[v.value for v in export.allowed_versions(aar)],
        exportable=not any(f.severity is Severity.FEIL for f in findings),
        number_of_entries=len(snapshot.audit.entries),
        total_debit=snapshot.audit.total_debit,
        total_credit=snapshot.audit.total_credit,
        findings=_findings(findings),
    )


@router.get("/companies/{company_id}/saft/fil", response_model=None, openapi_extra=KEY_READ)
async def download_year(
    aar: _Year,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    versjon: _Version = None,
) -> Response:
    """The SAF-T Financial XML file for one financial year, as a download.

    Same ``aar`` and ``versjon`` as ``GET /saft/kontroll``, which should be
    called first and shown. The body is ``application/xml``, named in
    ``Content-Disposition`` the way RF-1363 requires,
    ``SAF-T Financial_<orgnr>_<yyyymmddhhmmss>_1_1.xml`` in Oslo time, so keep
    the name. It holds the company's whole ledger for the year, with names and
    addresses of customers and suppliers and the name of the person who made
    it: personal data, to be handed only to whoever asked for it.

    Nothing is submitted anywhere; uploading to Altinn is the person's job.
    The check runs again here, and the file is refused with 422
    ``saft_not_exportable`` and the blocking ``findings`` when it finds an
    error: a file that passes the schema and fails at the auditor looks like
    compliance and is not. Also 404 ``saft_year_not_found`` and 422
    ``unsupported_saft_version``. Needs read access only.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    version = _version(aar, versjon)
    created_at = utc_now()
    snapshot = await service.load(
        db,
        company=context.company,
        year=aar,
        exported_by=user,
        software_version=__version__,
        created_at=created_at,
    )
    findings = service.findings(snapshot)
    blocking = [f for f in findings if f.severity is Severity.FEIL]
    if blocking:
        # Answered here rather than raised: the generic handler renders a
        # message and a code, and what the person needs is the list.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "detail": "SAF-T-filen ville ikke holdt ved et bokettersyn. Se funnene.",
                "code": "saft_not_exportable",
                "findings": [f.model_dump() for f in _findings(blocking)],
            },
        )
    content = export.build_xml(snapshot.audit, version)
    name = export.filename(context.company.orgnr, created_at)
    return Response(
        content=content,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
