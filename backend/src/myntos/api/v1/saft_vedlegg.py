"""The documents behind the previous program's vouchers, put in the bilag archive.

``POST /companies/{id}/saft/vedlegg``
    Upload Tripletex's printout of the vouchers with their attachments, one or
    more PDFs. Answers 202 at once; read in the background.
``GET  /companies/{id}/saft/vedlegg``
    The company's uploads of printouts, newest first.
``GET  /companies/{id}/saft/vedlegg/{import_id}``
    One upload: how far it has come, and once read, which document goes on
    which voucher.
``POST /companies/{id}/saft/vedlegg/{import_id}/arkivering``
    Put the documents in the bilag archive. Answers 202; done in the background.
``DELETE /companies/{id}/saft/vedlegg/{import_id}``
    Throw an upload away before anything is put in the archive.

Under ``/saft/`` beside the import because the documents belong to the vouchers
the SAF-T import brought over, and are found by the numbers it kept. What the
printout is and how it is cut is :mod:`myntos.saft.printout`; how a document
finds its voucher is :mod:`myntos.saft.documents`; the background work is
:mod:`myntos.saft.jobs`, shared with the SAF-T import.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, File, Response, UploadFile, status
from pydantic import BaseModel

from myntos.api.deps import (
    KEY_PREVIEW,
    KEY_READ,
    CurrentCompany,
    CurrentUser,
    DbSession,
    Storage,
    Working,
    key_post,
    key_write,
)
from myntos.api.v1.saft_innlesing import ImportFindingResponse, ImportSessions
from myntos.domain.identity.access import Permission, has_permission, require_permission
from myntos.saft import intake, jobs
from myntos.saft.documents import Action
from myntos.saft.models import ImportPurpose, ImportStatus, SaftImport

router = APIRouter(tags=["saft"])

_PURPOSES = (ImportPurpose.VEDLEGG,)


# --- Shapes --------------------------------------------------------------------------


class PrintoutFileResponse(BaseModel):
    file_name: str
    pages: int
    #: Vouchers found in it, each starting at one of Tripletex's own pages.
    vouchers: int
    #: Pages before the first voucher, which belong to none.
    loose_pages: int


class DocumentTargetResponse(BaseModel):
    #: The voucher in Myntos (``id`` in ``GET …/bilag``) and its number there.
    voucher_id: int
    voucher_no: str
    #: Whether it already has this exact document.
    already: bool


class PrintedVoucherResponse(BaseModel):
    #: Tripletex's number, «240-2026».
    number: str
    file_name: str
    #: Where it starts in the printout (from 1), and how many pages it has,
    #: Tripletex's own page first.
    first_page: int
    pages: int
    #: The pages after Tripletex's own: the receipts and invoices.
    attachment_pages: int
    printed_on: date | None
    #: ``attach``: goes on its vouchers. ``already_attached``: they have it.
    #: ``no_attachments``: printed alone, nothing to put on. ``not_in_books``:
    #: Myntos has no voucher with this number from Tripletex. ``too_large``:
    #: over 25 MB. ``repeated``: the same pages earlier in the upload.
    action: Action
    #: The document's SHA-256 and size, for one with attachments.
    sha256: str | None
    byte_size: int | None
    #: The debit total on Tripletex's page and on the vouchers in Myntos.
    printed_debit: Decimal | None
    booked_debit: Decimal | None
    #: The vouchers in Myntos it belongs on: more than one when the import split
    #: it by date (``2026-246`` and ``2026-246-1010``).
    targets: list[DocumentTargetResponse]


class DocumentReviewResponse(BaseModel):
    """What putting the printouts' documents in the archive does, or did."""

    kind: Literal["vedlegg"]
    postable: bool
    #: The program whose printout this is: ``Tripletex``.
    software: str
    files: list[PrintoutFileResponse]
    vouchers_in_files: int
    #: Printed vouchers whose document goes in, and how many documents that is
    #: (one per voucher in Myntos, so more when a voucher was split by date).
    to_attach: int
    documents: int
    already_attached: int
    no_attachments: int
    not_in_books: int
    too_large: int
    repeated: int
    vouchers: list[PrintedVoucherResponse]
    findings: list[ImportFindingResponse]


class UploadedPrintoutResponse(BaseModel):
    filename: str
    size: int


class DocumentImportResponse(BaseModel):
    company_id: int
    id: int
    status: ImportStatus
    created_at: datetime
    updated_at: datetime
    #: When the documents were put in the archive.
    posted_at: datetime | None
    #: What the server is doing now, in Norwegian.
    step: str | None
    done: int
    total: int
    #: Whether the server is working on it in this moment. False for one in
    #: ``reading`` or ``posting`` means a restart stopped it and it is being
    #: started again.
    running: bool
    #: Whether the uploaded printouts are still kept (they go once the documents
    #: are in the archive, the upload is thrown away, or after 14 days).
    files_kept: bool
    files: list[UploadedPrintoutResponse]
    review: DocumentReviewResponse | None
    #: What was put in the archive, once ``posted``.
    result: DocumentReviewResponse | None
    error: str | None
    error_code: str | None
    #: Whether the caller may put the documents in the archive now: read,
    #: something to put on, the printouts kept, and the role to do it.
    may_post: bool


async def _response(
    db: DbSession, context: CurrentCompany, job: SaftImport
) -> DocumentImportResponse:
    kept = await jobs.files_kept(db, job)
    review = job.review or {}
    return DocumentImportResponse.model_validate(
        {
            "company_id": job.company_id,
            "id": job.id,
            "status": job.status,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "posted_at": job.posted_at,
            "step": job.step,
            "done": job.done,
            "total": job.total,
            "running": jobs.is_running(job),
            "files_kept": kept,
            "files": [{"filename": f.filename, "size": f.size} for f in job.files],
            "review": job.review,
            "result": job.result,
            "error": job.error,
            "error_code": job.error_code,
            "may_post": (
                job.status == ImportStatus.READY.value
                and bool(review.get("postable"))
                and kept
                and has_permission(context.membership.role, Permission.LEDGER_WRITE)
            ),
        }
    )


# --- Routes ---------------------------------------------------------------------------


@router.post(
    "/companies/{company_id}/saft/vedlegg",
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra=KEY_PREVIEW,
)
async def upload_printouts(
    context: CurrentCompany,
    db: DbSession,
    user: CurrentUser,
    sessions: ImportSessions,
    working: Working,
    background: BackgroundTasks,
    files: Annotated[list[UploadFile], File()],
) -> DocumentImportResponse:
    """Upload the old program's printout of its vouchers with attachments. Posts nothing.

    The SAF-T file carries the books but not the documents behind them (the
    receipts and invoices, «bilagene»). Tripletex has no export of those; it
    prints them instead: «Bilag → Bilagsoversikt», choose the period, tick the
    box in the «Dato» column, «Skriv ut valgte bilag», and download the PDF.
    Every voucher in it starts with a page Tripletex draws itself («Bilag
    nummer 240-2026», the company, the posting lines), followed by its
    attachments. Myntos cuts it at those pages and finds each voucher among the
    ones the SAF-T import brought over, **by Tripletex's number**, which the
    import kept («Tripletex bilag 240-2026» in ``source_document``). Only
    Tripletex's printout is read so far.

    Send one or more PDFs as ``multipart/form-data``, each in a ``files``
    field: at most 24, and 200 MB for the whole request (a larger year goes up
    in more than one upload; Tripletex advises a period at a time when there is
    much anyway). Answers **202** with the upload at once; poll
    ``GET …/saft/vedlegg/{import_id}`` until ``status`` is ``ready`` or
    ``failed``. The review then says, per printed voucher, which vouchers in
    Myntos its document goes on, and what is left out and why. Nothing is in the
    archive until ``POST …/arkivering``.

    Ends ``failed`` with the reason in ``error`` and the code in ``error_code``
    for a file that is not such a printout or cannot be read
    (``invalid_printout``) and for another company's vouchers
    (``printout_wrong_company``, by the organisasjonsnummer on every page
    Tripletex draws). 422 ``invalid_printout`` at once for no file, an empty
    one, one over 200 MB or more than 24; 413 ``request_too_large`` for a request
    over 200 MB in all.

    For a key this is a preview: it changes nothing in the books or the archive,
    so it runs at once even when the key's writes wait for approval. The files
    are kept only until the documents are archived or the upload is thrown
    away, or for 14 days. Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    kept = await intake.keep_printouts(working, files)
    try:
        job = await jobs.create(
            db,
            company=context.company,
            purpose=ImportPurpose.VEDLEGG,
            files=kept,
            til=None,
            user_id=user.id,
            key_id=context.agent_key.id if context.agent_key is not None else None,
        )
        # Committed before the answer, so the work below finds the row it reads.
        await db.commit()
    except Exception:
        await jobs.forget(working, [file.key for file in kept])
        raise
    background.add_task(jobs.read_documents, sessions, working, job.id)
    return await _response(db, context, job)


@router.get("/companies/{company_id}/saft/vedlegg", openapi_extra=KEY_READ)
async def list_printouts(context: CurrentCompany, db: DbSession) -> list[DocumentImportResponse]:
    """The company's uploads of the previous program's printouts, newest first (the last 20).

    The record of which documents were brought into the archive from the old
    program and when: a ``posted`` upload keeps its ``result`` after its files
    are gone. Needs ``ledger:read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    return [
        await _response(db, context, job)
        for job in await jobs.list_for(db, company=context.company, purposes=_PURPOSES)
    ]


@router.get("/companies/{company_id}/saft/vedlegg/{import_id}", openapi_extra=KEY_READ)
async def get_printout(
    import_id: int, context: CurrentCompany, db: DbSession
) -> DocumentImportResponse:
    """One upload of printouts: how far the server has come, and once read, what goes where.

    Poll this every second or two while ``status`` is ``reading`` or
    ``posting``. Then:

    - ``ready``: ``review`` lists every printed voucher with its ``action`` and
      the ``targets``, the vouchers in Myntos its document goes on, with the
      counts beside (``to_attach``, ``documents``, ``no_attachments``,
      ``not_in_books`` …) and ``findings``. Show it to the person.
      ``may_post`` says whether the caller may archive it now. ``error`` is
      set when an archiving was tried and stopped (``review_changed``: the
      vouchers or the archive changed since the read); nothing was archived
      then, and ``review`` is new.
    - ``posted``: ``result`` is what was put in the archive.
    - ``failed``: ``error`` says why the printouts could not be read.

    Another company's upload is 404, like one that does not exist, and so is a
    SAF-T import's id. Needs ``ledger:read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    job = await jobs.get(db, company=context.company, import_pk=import_id, purposes=_PURPOSES)
    return await _response(db, context, job)


@router.post(
    "/companies/{company_id}/saft/vedlegg/{import_id}/arkivering",
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra=key_post("legge dokumentene fra det gamle regnskapsprogrammet i bilagsarkivet"),
)
async def archive_printouts(
    import_id: int,
    context: CurrentCompany,
    db: DbSession,
    user: CurrentUser,
    sessions: ImportSessions,
    storage: Storage,
    working: Working,
    background: BackgroundTasks,
) -> DocumentImportResponse:
    """Put the documents the review showed in the bilag archive. Cannot be undone. Answers 202.

    Each printed voucher with attachments becomes one PDF, its pages as
    Tripletex printed them with Tripletex's own page first, named «Tripletex
    bilag 240-2026.pdf», and is attached to every voucher in ``targets`` that
    does not have it yet. The bilag archive is append-only, because
    bokføringsloven § 13 requires the documentation to be kept for five years
    after the year's end: a document put on cannot be taken off again. Show the
    person the review and wait for a yes.

    The plan is made again from the printouts and the books as they are now. If
    it is no longer what the review said (a voucher brought over since, a
    document attached meanwhile), nothing is archived and the upload is
    ``ready`` again with ``error_code`` ``review_changed`` and the new review.
    Otherwise all of it goes in, in one transaction. Poll
    ``GET …/saft/vedlegg/{import_id}`` until ``posted``.

    Refusals: 409 ``import_busy`` (it is being read or archived, or an import is
    being posted); 422 ``import_not_postable`` (nothing to put in the archive,
    or the files gone after 14 days); 404 ``import_not_found``. Needs
    ``ledger:write``. For a key it needs ``post``, and may answer 202
    ``approval_pending`` when the person approves the key's posts one by one.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    job = await jobs.start_posting(
        db,
        company=context.company,
        import_pk=import_id,
        user_id=user.id,
        key_id=context.agent_key.id if context.agent_key is not None else None,
        may_chart=has_permission(context.membership.role, Permission.LEDGER_CHART),
        purposes=_PURPOSES,
    )
    await db.commit()
    background.add_task(jobs.post_documents, sessions, working, job.id, storage)
    return await _response(db, context, job)


@router.delete(
    "/companies/{company_id}/saft/vedlegg/{import_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=key_write(
        "forkaste en opplasting av utskrifter fra det gamle regnskapsprogrammet"
    ),
)
async def discard_printouts(
    import_id: int, context: CurrentCompany, db: DbSession, working: Working
) -> Response:
    """Throw away an upload of printouts whose documents are not archived, with its files.

    For a printout uploaded by mistake, or one whose review showed the wrong
    month. Nothing in the books or the archive changes. Not one being read or
    archived (409 ``import_busy``), and not an archived one, which stays as the
    record of what was brought in. Needs ``ledger:write``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    keys = await jobs.discard(db, company=context.company, import_pk=import_id, purposes=_PURPOSES)
    await db.commit()
    await jobs.forget(working, keys)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
