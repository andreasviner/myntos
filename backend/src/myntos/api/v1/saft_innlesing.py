"""The previous program's SAF-T files, read and posted on the server.

``POST /companies/{id}/saft/innlesing``
    Upload one or more files (one per year), or one file to compare. Answers
    202 at once; the files are read in the background.
``GET  /companies/{id}/saft/innlesing``
    The company's imports, newest first.
``GET  /companies/{id}/saft/innlesing/{import_id}``
    One import: where it has come, and once read, the review.
``POST /companies/{id}/saft/innlesing/{import_id}/foring``
    Post what the review showed. Answers 202; posted in the background.
``DELETE /companies/{id}/saft/innlesing/{import_id}``
    Throw an import away, files and all.

Why on the server, and how it holds together, is in :mod:`myntos.saft.jobs`;
what an import does to the books is in :mod:`myntos.saft.move`. The routes in
:mod:`myntos.api.v1.saft_import` and ``…/inngaende-balanse/saft`` still read
one file per request and keep nothing, for an agent that wants an answer in the
same call; this is what the page uses.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Response, UploadFile, status
from pydantic import BaseModel, Field

from myntos.api.deps import (
    KEY_PREVIEW,
    KEY_READ,
    CurrentCompany,
    CurrentUser,
    DbSession,
    key_post,
    key_write,
)
from myntos.api.v1.saft_import import (
    AccountDifferenceResponse,
    ImportSourceResponse,
    MonthResponse,
    PartyDifferenceResponse,
    VatDifferenceResponse,
)
from myntos.db.session import get_session_factory
from myntos.domain.identity.access import Permission, has_permission, require_permission
from myntos.domain.ledger.models import AccountType, PartyType
from myntos.saft import jobs, reader
from myntos.saft.errors import InvalidSaftFileError
from myntos.saft.models import ImportPurpose, ImportStatus, SaftImport

router = APIRouter(tags=["saft"])


def get_import_sessions() -> jobs.Sessions:
    """Where the background work opens its sessions.

    A dependency rather than a direct call, so the tests point it at their own
    database: the work runs after the request's session is gone, and opens its
    own.
    """
    return get_session_factory()


ImportSessions = Annotated[jobs.Sessions, Depends(get_import_sessions)]


# --- Shapes --------------------------------------------------------------------------


class ImportFindingResponse(BaseModel):
    #: The year the finding is about, or null for the files as a whole.
    year: int | None = None
    severity: str
    code: str
    message: str


class YearResponse(BaseModel):
    year: int
    file_name: str
    software: str | None
    start_date: date
    end_date: date
    #: Whether the file runs to 31 December. Only the last year may not.
    complete: bool
    #: The last day brought over from this year.
    through: date
    #: Whether the year is new in Myntos, opened by the import.
    opened: bool
    vouchers: int
    lines: int
    first_date: date | None
    last_date: date | None
    already_imported: int
    later: int
    months: list[MonthResponse]


class OpeningResponse(BaseModel):
    year: int
    #: ``post`` a new opening balance; ``replace`` the standing one (posted for
    #: a later year) with one for this year; ``keep`` the one already posted;
    #: ``none``: the earliest file opens at zero (the books start in its year),
    #: so none is posted, and one standing for a later year (``replaces``) is
    #: reversed.
    action: Literal["post", "replace", "keep", "none"]
    replaces: str | None
    replaces_year: int | None
    lines: int
    total: Decimal


class AccountToAddResponse(BaseModel):
    number: str
    description: str
    account_type: AccountType
    grouping_category: str
    grouping_code: str
    source_accounts: list[str]


class PartyToAddResponse(BaseModel):
    party_type: PartyType
    name: str
    orgnr: str | None
    party_id: str | None
    address_line: str | None
    postal_code: str | None
    city: str | None
    source_id: str


class DimensionToAddResponse(BaseModel):
    code: str
    dimension_description: str
    dimension_id: int | None
    value: str
    description: str


class MoveReviewResponse(BaseModel):
    """What posting the import does, or did."""

    kind: Literal["import"]
    postable: bool
    vouchers: int
    first_year_before: int | None
    first_year_after: int
    years_opened: list[int]
    years: list[YearResponse]
    opening: OpeningResponse | None
    accounts_to_add: list[AccountToAddResponse]
    parties_to_add: list[PartyToAddResponse]
    dimensions_to_add: list[DimensionToAddResponse]
    findings: list[ImportFindingResponse]


class ComparisonReviewResponse(BaseModel):
    """The old program's year against Myntos's books."""

    kind: Literal["sammenligning"]
    source: ImportSourceResponse
    start_date: date
    through: date
    matches: bool
    accounts_compared: int
    parties_compared: int
    terms_compared: int
    accounts: list[AccountDifferenceResponse]
    parties: list[PartyDifferenceResponse]
    vat: list[VatDifferenceResponse]
    findings: list[ImportFindingResponse]


Review = Annotated[MoveReviewResponse | ComparisonReviewResponse, Field(discriminator="kind")]


class ImportFileResponse(BaseModel):
    filename: str
    size: int
    #: The year the file is for, once read.
    year: int | None


class ImportResponse(BaseModel):
    company_id: int
    id: int
    purpose: ImportPurpose
    status: ImportStatus
    til: date | None
    created_at: datetime
    updated_at: datetime
    posted_at: datetime | None
    #: What the server is doing now (``reading``/``posting``), in Norwegian.
    step: str | None
    done: int
    total: int
    #: Whether the server is working on it in this moment. False for one in
    #: ``reading`` or ``posting`` means it was stopped by a restart and is
    #: being started again.
    running: bool
    #: Whether the uploaded files are still kept (they go once it is posted,
    #: thrown away, compared, or left for 14 days).
    files_kept: bool
    files: list[ImportFileResponse]
    review: Review | None
    #: What the post did, once ``posted``.
    result: MoveReviewResponse | None
    error: str | None
    error_code: str | None
    #: Whether the caller may post it now: ready, nothing in the review stops
    #: it, the files are kept, and the caller holds the permissions it needs.
    may_post: bool


async def _response(db: DbSession, context: CurrentCompany, job: SaftImport) -> ImportResponse:
    kept = await jobs.files_kept(db, job)
    role = context.membership.role
    review = job.review or {}
    may_post = (
        job.purpose == ImportPurpose.IMPORT.value
        and job.status == ImportStatus.READY.value
        and bool(review.get("postable"))
        and kept
        and has_permission(role, Permission.LEDGER_WRITE)
        and (
            not (review.get("accounts_to_add") or review.get("dimensions_to_add"))
            or has_permission(role, Permission.LEDGER_CHART)
        )
    )
    return ImportResponse.model_validate(
        {
            "company_id": job.company_id,
            "id": job.id,
            "purpose": job.purpose,
            "status": job.status,
            "til": job.til,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "posted_at": job.posted_at,
            "step": job.step,
            "done": job.done,
            "total": job.total,
            "running": jobs.is_running(job),
            "files_kept": kept,
            "files": [{"filename": f.filename, "size": f.size, "year": f.year} for f in job.files],
            "review": job.review,
            "result": job.result,
            "error": job.error,
            "error_code": job.error_code,
            "may_post": may_post,
        }
    )


# --- Routes ---------------------------------------------------------------------------


@router.post(
    "/companies/{company_id}/saft/innlesing",
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra=KEY_PREVIEW,
)
async def upload_files(
    context: CurrentCompany,
    db: DbSession,
    user: CurrentUser,
    sessions: ImportSessions,
    background: BackgroundTasks,
    files: Annotated[list[UploadFile], File()],
    til: Annotated[date | None, Form()] = None,
    sammenlign: Annotated[bool, Form()] = False,
) -> ImportResponse:
    """Upload the previous program's SAF-T files, to be read on the server. Posts nothing.

    SAF-T Financial is the export every Norwegian accounting program writes
    (Tripletex, Fiken, PowerOffice, Visma …): the chart of accounts with every
    balance, the customers and suppliers, and every voucher, for one financial
    year per file. Send them as ``multipart/form-data``, each in a ``files``
    field: the XML, or the ZIP it downloads as, at most 200 MB each and ten in
    all. Answers **202** with the import at once; reading a year of vouchers
    takes seconds to minutes, so poll ``GET …/saft/innlesing/{import_id}`` until
    ``status`` is ``ready`` or ``failed``. Nothing has to stay connected.

    **To bring the books over** (the default): one file per year, from
    1 January, the years in a row, all but the last one whole. The earliest
    file's opening balances become the opening balance (inngående balanse), and
    every year's vouchers come over in the ``IMPORT`` journal under the old
    program's numbers. Years before the company's first year in Myntos are
    added; an opening balance already posted for a later year is replaced, so
    nothing is counted twice. The review (``review``, ``kind: import``) says
    what will be posted per year, the opening balance, the accounts, customers,
    suppliers and avdelinger the posting will add, and the findings, each with
    its ``year``. Nothing is in the books until ``POST …/foring``.

    **To compare** (``sammenlign=true``): one fresh file for the year the old
    program is keeping now, during a parallel run. The review (``kind:
    sammenligning``) is the same as ``POST …/saft/sammenligning`` answers, and
    the file is gone once compared.

    ``til`` (YYYY-MM-DD, optional) stops the last year early. A file that is
    not a SAF-T file, another company's file (``saft_wrong_company``) or files
    that are not one year each in a row (``saft_wrong_year``) end as ``failed``
    with the reason in ``error`` and the code in ``error_code``.

    For a key this is a preview: it changes nothing in the books, so it runs at
    once even when the key's writes wait for approval. The files are kept only
    until the import is posted or thrown away, or for 14 days. Bringing books
    over needs ``ledger:write``; comparing needs ``ledger:read``.
    """
    purpose = ImportPurpose.SAMMENLIGNING if sammenlign else ImportPurpose.IMPORT
    require_permission(
        context.membership.role,
        Permission.LEDGER_READ if sammenlign else Permission.LEDGER_WRITE,
    )
    uploads: list[jobs.Upload] = []
    for upload in files:
        if upload.size is not None and upload.size > reader.MAX_UPLOAD_BYTES:
            raise InvalidSaftFileError(
                f"«{upload.filename}» er større enn 200 MB, som er det største Altinn tar imot "
                "av en SAF-T-fil"
            )
        content = await upload.read(reader.MAX_UPLOAD_BYTES + 1)
        uploads.append(jobs.Upload(filename=upload.filename or "", content=content))
    job = await jobs.create(
        db,
        company=context.company,
        purpose=purpose,
        uploads=uploads,
        til=til,
        user_id=user.id,
        key_id=context.agent_key.id if context.agent_key is not None else None,
    )
    # Committed before the answer, so the work below finds the row it reads.
    await db.commit()
    background.add_task(jobs.read, sessions, job.id)
    return await _response(db, context, job)


@router.get("/companies/{company_id}/saft/innlesing", openapi_extra=KEY_READ)
async def list_imports(context: CurrentCompany, db: DbSession) -> list[ImportResponse]:
    """The company's SAF-T imports and comparisons, newest first (the last 20).

    The record of what was brought over from the previous program and when: a
    ``posted`` import keeps its review and ``result`` after its files are gone.
    One in ``reading`` or ``posting`` is being worked on. Needs ``ledger:read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    return [
        await _response(db, context, job)
        for job in await jobs.list_for(db, company=context.company)
    ]


@router.get("/companies/{company_id}/saft/innlesing/{import_id}", openapi_extra=KEY_READ)
async def get_import(import_id: int, context: CurrentCompany, db: DbSession) -> ImportResponse:
    """One SAF-T import: how far the server has come, and once read, what it found.

    Poll this every second or two while ``status`` is ``reading`` or
    ``posting``; ``step``, ``done`` and ``total`` say what is happening
    («Leser «2025.zip» (1 av 2)»). Then:

    - ``ready``: ``review`` says what posting will do (or, for a comparison,
      where the two programs part). Show it to the person. ``may_post`` is
      whether the caller may post it now. ``error`` is set when a post was
      tried and stopped (the books changed since the files were read:
      ``review_changed``); nothing was posted then, and ``review`` is new.
    - ``posted``: ``result`` is what was posted.
    - ``failed``: ``error`` says why the files could not be read.

    Another company's import is 404, like one that does not exist. Needs
    ``ledger:read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    job = await jobs.get(db, company=context.company, import_pk=import_id)
    return await _response(db, context, job)


@router.post(
    "/companies/{company_id}/saft/innlesing/{import_id}/foring",
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra=key_post("føre regnskapet fra det gamle regnskapsprogrammet"),
)
async def post_import(
    import_id: int,
    context: CurrentCompany,
    db: DbSession,
    user: CurrentUser,
    sessions: ImportSessions,
    background: BackgroundTasks,
) -> ImportResponse:
    """Post what the import's review showed. Cannot be undone. Answers 202 and posts on the server.

    Adds the accounts, customers, suppliers and avdelinger the review listed,
    posts the opening balance (or replaces a later one), and every year's
    vouchers, in **one transaction**: all of it, or none of it. The move is run
    again on the books as they are now, so what is posted is what the rules say
    at that moment; if anything has changed so that it no longer can be posted
    (a payment registered in the same weeks, say), nothing is posted and the
    import goes back to ``ready`` with the new review and ``error_code``
    ``review_changed``. Poll ``GET …/saft/innlesing/{import_id}`` until it is
    ``posted``.

    The vouchers are posted under bokføringsloven like any other: never edited
    or deleted afterwards. Show the person the review and wait for a yes before
    calling this.

    Refusals: 409 ``import_busy`` (it is being read or posted, or another import
    is being posted); 422 ``import_not_postable`` (a ``feil`` in the review,
    nothing new to post, the files gone after 14 days, or accounts to add and
    the caller is not «Administrator» or «Eier», who alone may change the
    kontoplan). Needs ``ledger:write``. For a key it needs ``post``, and may
    answer 202 ``approval_pending`` when the person approves the key's posts
    one by one.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    job = await jobs.start_posting(
        db,
        company=context.company,
        import_pk=import_id,
        user_id=user.id,
        key_id=context.agent_key.id if context.agent_key is not None else None,
        may_chart=has_permission(context.membership.role, Permission.LEDGER_CHART),
    )
    await db.commit()
    background.add_task(jobs.post, sessions, job.id)
    return await _response(db, context, job)


@router.delete(
    "/companies/{company_id}/saft/innlesing/{import_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=key_write("forkaste en innlesing av SAF-T-filer"),
)
async def discard_import(import_id: int, context: CurrentCompany, db: DbSession) -> Response:
    """Throw away an import that has not been posted, with its files. Nothing in the books changes.

    For an import read by mistake, or one whose review showed the wrong files.
    Not one being read or posted (409 ``import_busy``), and not a posted one,
    which stays as the record of what was brought over. Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    await jobs.discard(db, company=context.company, import_pk=import_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
