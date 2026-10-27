"""SAF-T files read, compared and posted on the server, while the person does something else.

A farm's year is 600 vouchers and reads in seconds; a shop's is a hundred
thousand lines and a 50 MB upload. Done in the browser's requests, as it was,
that is a tab that must stay open through an upload, a read, a hundred small
requests adding accounts and parties, a second upload, a second read and a
third for the post. The report that this was slow is why this module exists.

So a file is uploaded **once**, into a :class:`~myntos.saft.models.SaftImport`
whose files are kept in the working store (:mod:`myntos.storage.working`, put
there by :mod:`myntos.saft.intake`), and everything after happens here, in
the background of the request that started it (FastAPI's ``BackgroundTasks``,
which run after the answer has been sent):

1. :func:`read` parses the files and runs the whole move
   (:func:`myntos.saft.move.apply`) in a transaction that is **rolled back**.
   What it did is the review, and because it is the posting itself, undone, the
   review and the post cannot disagree. A comparison
   (:func:`myntos.saft.compare.compare`) is read the same way and is then done.
2. :func:`post`, when the person says yes, runs the move again and commits it,
   all of it or nothing. The books may have moved since the review (a payment
   registered in the meantime): then nothing is posted, the import is back to
   ``ready`` with the review as it now stands, and the person reads it again.

The page polls the import and may be closed at any point after the upload: the
work does not belong to the page.

**The documents behind the vouchers go the same way** (``purpose`` ``vedlegg``,
since 2026-10-22): the old program's printout is uploaded once,
:func:`read_documents` reads it and stores the plan, and
:func:`post_documents` puts the documents in the bilag archive on the person's
yes. One difference, and it is the archive's: the review is not the post
rolled back, because a file written to the document store stays written. Both
run :func:`myntos.saft.documents.plan`, the review writes nothing, and the
post goes ahead only while the plan is still the one the person saw.

**The documents in the old program's export come after the books**
(since 2026-10-27). An upload of Fiken's complete export keeps the ZIP whole
beside its SAF-T files (``FileKind.EXPORT``); :func:`read` adds what is in it to
the review (``archives``), and :func:`post`, once the books are committed,
copies every file into the document store (:mod:`myntos.saft.archive`). The
books are one transaction and the documents are hundreds of files, so the post
marks itself as done with the books (``posted_at``, ``result``) **in the books'
own transaction**: a restart after it keeps the documents and does not post
again. An export whose books are already in Myntos (brought over before the
documents were kept) may be posted for its documents alone. When the store
fails halfway, the import is ``posted`` with ``documents_not_kept``, keeps its
files, and posting it again keeps the rest.

**One process.** Which imports are being worked on is kept in this process
(:data:`_running`), as the agent keys' counters are, which is exact with the one
uvicorn process this deployment runs. A restart (every deploy) stops the work
in the middle; :func:`resume` runs at startup and starts it again. Nothing is
lost by that: a read stores nothing until it is done, a post is one
transaction that either committed or did not, and the documents after it are
kept once each however often they are started.
"""

from __future__ import annotations

import asyncio
import gzip
import logging
import re
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from typing import Any

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from myntos.auth import agent_keys
from myntos.core.clock import utc_now
from myntos.domain.arsoppgjor.findings import Finding
from myntos.domain.identity.access import Permission, has_permission
from myntos.domain.identity.models import CompanyMembership
from myntos.domain.ledger.errors import LedgerError
from myntos.domain.ledger.models import Company
from myntos.saft import archive, compare, documents, intake, move, printout, reader
from myntos.saft.errors import InvalidPrintoutError, InvalidSaftFileError, SaftImportError
from myntos.saft.models import (
    SAFT_PURPOSES,
    FileKind,
    ImportPurpose,
    ImportStatus,
    SaftImport,
    SaftImportFile,
)
from myntos.saft.printout import Printout
from myntos.saft.reader import SaftEntries
from myntos.storage.base import DocumentStorage, ObjectNotFoundError, StorageError
from myntos.storage.working import WorkingStore

logger = logging.getLogger(__name__)

Sessions = async_sessionmaker[AsyncSession]

#: How long uploaded files are kept when nothing is done with them. Long enough
#: for an accountant to be asked and to answer; short enough that a file
#: somebody forgot does not stay with us. Counted from the upload, as the
#: working store's own lifecycle rule counts (myntos.storage.working), so the
#: two can never disagree about whether a file should still be there.
FILE_LIFETIME = timedelta(days=14)

#: How old an object no row points at must be before the sweep deletes it.
#: A file is stored before the row that names it is committed, so a younger
#: one may be an upload still under way.
_ORPHAN_GRACE = timedelta(hours=1)

#: The imports this process is working on now. See the module docstring.
_running: set[int] = set()

#: The tasks :func:`resume` started, held so they are not collected mid-run.
_resumed: set[asyncio.Task[None]] = set()

_ACTIVE = (ImportStatus.READING.value, ImportStatus.POSTING.value)

#: The imports whose files are let go after FILE_LIFETIME: not being worked
#: on, and either never posted or posted with documents still to keep.
_SETTLED = (ImportStatus.READY.value, ImportStatus.FAILED.value, ImportStatus.POSTED.value)

#: ``error_code`` of a posted import whose export's documents were not all
#: kept. Posting it again keeps the rest.
DOCUMENTS_NOT_KEPT = "documents_not_kept"


class ImportNotFoundError(LedgerError):
    """No such import in these books (another company's is not found either)."""


class ImportedDocumentNotFoundError(LedgerError):
    """No document kept from an export in these books by that id (another company's neither)."""


class ImportBusyError(LedgerError):
    """The import is being read or posted, or another one is being posted."""


class ImportNotPostableError(LedgerError):
    """The review has a finding that stops it, or there is nothing to post."""


# --- Creating, finding, throwing away ------------------------------------------------


async def create(
    db: AsyncSession,
    *,
    company: Company,
    purpose: ImportPurpose,
    files: Sequence[intake.KeptFile],
    til: date | None,
    user_id: int | None,
    key_id: int | None,
) -> SaftImport:
    """Record the files ``intake`` stored and mark the import as being read. Reads nothing yet.

    The files were checked as they were stored (:mod:`myntos.saft.intake`):
    how many, how large, and that a ZIP had SAF-T files in it.
    """
    job = SaftImport(
        company_id=company.id,
        purpose=purpose.value,
        status=ImportStatus.READING.value,
        til=til,
        created_by_user_id=user_id,
        created_via_agent_key_id=key_id,
        step="Venter på å lese filene",
        done=0,
        total=len(files),
        files=[
            SaftImportFile(
                position=index,
                filename=file.filename[:255],
                size=file.size,
                storage_key=file.key,
                kind=file.kind.value,
            )
            for index, file in enumerate(files)
        ],
    )
    db.add(job)
    await db.flush()
    _running.add(job.id)
    return job


async def get(
    db: AsyncSession,
    *,
    company: Company,
    import_pk: int,
    purposes: Sequence[ImportPurpose] = SAFT_PURPOSES,
) -> SaftImport:
    """One of ``company``'s imports, looked up with the company, never by id alone.

    Only of the kinds ``purposes`` names: the SAF-T routes and the documents'
    each see their own, so one cannot read, post or throw away the other's.
    """
    job = await db.scalar(
        select(SaftImport).where(
            SaftImport.id == import_pk,
            SaftImport.company_id == company.id,
            SaftImport.purpose.in_([purpose.value for purpose in purposes]),
        )
    )
    if job is None:
        raise ImportNotFoundError("finner ikke innlesingen")
    await _expire(db, [job])
    return job


async def list_for(
    db: AsyncSession,
    *,
    company: Company,
    purposes: Sequence[ImportPurpose] = SAFT_PURPOSES,
    limit: int = 20,
) -> list[SaftImport]:
    """The company's imports of the kinds ``purposes`` names, newest first."""
    jobs = list(
        await db.scalars(
            select(SaftImport)
            .where(
                SaftImport.company_id == company.id,
                SaftImport.purpose.in_([purpose.value for purpose in purposes]),
            )
            .order_by(SaftImport.id.desc())
            .limit(limit)
        )
    )
    await _expire(db, jobs)
    return jobs


async def _expire(db: AsyncSession, jobs: Sequence[SaftImport]) -> None:
    """Let go of the files of imports uploaded more than :data:`FILE_LIFETIME` ago.

    Done when the imports are read, as a waiting approval's files are: the next
    person to look is the first one it matters to. The objects themselves are
    deleted by :func:`sweep`, which also does this for imports nobody looks at.
    """
    cutoff = utc_now() - FILE_LIFETIME
    stale = [job.id for job in jobs if job.status in _SETTLED and _aware(job.created_at) < cutoff]
    if stale:
        await db.execute(_let_go(SaftImportFile.import_id.in_(stale)))


def _let_go(*where: Any) -> Any:
    """The update that empties files: neither the stored copy nor an old in-database one."""
    return (
        update(SaftImportFile)
        .where(*where, _kept())
        .values(content=None, storage_key=None)
        .execution_options(synchronize_session=False)
    )


def _kept() -> Any:
    return SaftImportFile.storage_key.is_not(None) | SaftImportFile.content.is_not(None)


def _aware(value: datetime) -> datetime:
    # SQLite hands back naive datetimes for timezone-aware columns.
    return value if value.tzinfo is not None else value.replace(tzinfo=utc_now().tzinfo)


async def files_kept(db: AsyncSession, job: SaftImport) -> bool:
    """Whether the uploaded files are still here to be read or posted from."""
    found = await db.scalar(
        select(SaftImportFile.id).where(SaftImportFile.import_id == job.id, _kept()).limit(1)
    )
    return found is not None


def is_running(job: SaftImport) -> bool:
    return job.id in _running


def documents_in(review: dict[str, Any] | None) -> int:
    """How many files the review says the export's documents will keep."""
    return sum(int(item.get("documents") or 0) for item in (review or {}).get("archives") or [])


def postable(review: dict[str, Any] | None) -> bool:
    """Whether the review may be posted: books to post, or only the export's documents to keep.

    Documents alone when the books are already in Myntos (brought over before
    the documents were kept) and nothing in the review stops it. A ``feil``
    stops both: the documents are kept with the books they came with.
    """
    review = review or {}
    if review.get("postable"):
        return True
    findings = review.get("findings") or []
    return documents_in(review) > 0 and not any(item.get("severity") == "feil" for item in findings)


def documents_left(job: SaftImport) -> bool:
    """Whether a posted import still has documents to keep, posting it again."""
    return job.status == ImportStatus.POSTED.value and job.error_code == DOCUMENTS_NOT_KEPT


async def discard(
    db: AsyncSession,
    *,
    company: Company,
    import_pk: int,
    purposes: Sequence[ImportPurpose] = SAFT_PURPOSES,
) -> list[str]:
    """Throw an import away, files and all. Not one being worked on, and not a posted one.

    A posted import is the record of what was brought over and when; it stays,
    without its files. Returns the stored files' keys, for the caller to delete
    once this is committed (:func:`forget`): deleted first, a failed commit
    would leave an import whose files are gone.
    """
    job = await get(db, company=company, import_pk=import_pk, purposes=purposes)
    if job.status in _ACTIVE:
        raise ImportBusyError("innlesingen er i gang og kan ikke forkastes nå")
    if job.status == ImportStatus.POSTED.value:
        raise ImportBusyError("innlesingen er ført, og står som et spor av hva som ble hentet inn")
    keys = await _keys(db, SaftImportFile.import_id == job.id)
    await db.delete(job)
    await db.flush()
    return keys


async def _keys(db: AsyncSession, *where: Any) -> list[str]:
    return [
        key
        for key in await db.scalars(select(SaftImportFile.storage_key).where(*where))
        if key is not None
    ]


async def forget(working: WorkingStore, keys: Sequence[str]) -> None:
    """Delete stored files no row points at any more; what fails here, :func:`sweep` deletes."""
    await intake.forget(working, keys)


async def start_posting(
    db: AsyncSession,
    *,
    company: Company,
    import_pk: int,
    user_id: int,
    key_id: int | None,
    may_chart: bool,
    purposes: Sequence[ImportPurpose] = SAFT_PURPOSES,
) -> SaftImport:
    """Mark a reviewed import as being posted. :func:`post` does the posting.

    For documents (``vedlegg``) "posting" is putting them in the bilag archive,
    which :func:`post_documents` does; the same states and the same guards.
    """
    job = await get(db, company=company, import_pk=import_pk, purposes=purposes)
    if job.purpose == ImportPurpose.SAMMENLIGNING.value:
        raise ImportNotPostableError("en sammenligning fører ingenting")
    if documents_left(job):
        # The books are posted; this keeps the documents the first try did not.
        if not await files_kept(db, job):
            raise ImportNotPostableError(
                f"eksporten ble slettet etter {FILE_LIFETIME.days} dager. Last den opp på nytt "
                "for å ta vare på resten av dokumentene"
            )
        return await _mark_posting(db, job, user_id=user_id, key_id=key_id)
    if job.status != ImportStatus.READY.value or job.review is None:
        raise ImportBusyError(
            "innlesingen er ikke klar til å føres"
            if job.status in _ACTIVE
            else "innlesingen kan ikke føres slik den står"
        )
    reviewed = (
        postable(job.review)
        if job.purpose == ImportPurpose.IMPORT.value
        else bool(job.review.get("postable"))
    )
    if not reviewed:
        raise ImportNotPostableError(
            "gjennomgangen har funn som stopper føringen, eller ingenting nytt å føre"
        )
    if job.review.get("accounts_to_add") and not may_chart:
        raise ImportNotPostableError(
            "filene fører på kontoer som ikke er i kontoplanen, og bare en administrator "
            "eller eier kan legge dem til. Be en av dem om å føre regnskapet fra filene"
        )
    # The same for avdelinger: setting them up is ledger:chart too, and the
    # post would add them (move.apply refuses it again, as the last word).
    if job.review.get("dimensions_to_add") and not may_chart:
        raise ImportNotPostableError(
            "filene fører på avdelinger eller produkter som ikke er satt opp, og bare en "
            "administrator eller eier kan legge dem til. Be en av dem om å føre regnskapet fra "
            "filene"
        )
    if not await files_kept(db, job):
        raise ImportNotPostableError(
            f"filene ble slettet etter {FILE_LIFETIME.days} dager uten at noe ble ført. Last "
            "dem opp på nytt"
        )
    return await _mark_posting(db, job, user_id=user_id, key_id=key_id)


async def _mark_posting(
    db: AsyncSession, job: SaftImport, *, user_id: int, key_id: int | None
) -> SaftImport:
    other = await db.scalar(
        select(SaftImport.id).where(
            SaftImport.company_id == job.company_id,
            SaftImport.status == ImportStatus.POSTING.value,
            SaftImport.id != job.id,
        )
    )
    if other is not None:
        raise ImportBusyError("en annen innlesing føres akkurat nå. Vent til den er ferdig")
    review = job.review or {}
    if job.posted_at is None:
        # Who posted the books. Keeping the rest of the documents afterwards
        # does not make somebody else that person.
        job.posted_by_user_id = user_id
        job.posted_via_agent_key_id = key_id
    job.status = ImportStatus.POSTING.value
    job.error = None
    job.error_code = None
    job.step = "Venter på å føre"
    job.done = 0
    job.total = (
        len(job.files)
        if job.purpose == ImportPurpose.VEDLEGG.value
        else len(review.get("years", [])) + 1
    )
    job.updated_at = utc_now()
    await db.flush()
    _running.add(job.id)
    return job


# --- The work ------------------------------------------------------------------------


async def _progress(
    sessions: Sessions, import_pk: int, step: str, done: int, total: int | None = None
) -> None:
    """Say how far the work has come, in a transaction of its own so the page sees it now."""
    values: dict[str, Any] = {"step": step, "done": done, "updated_at": utc_now()}
    if total is not None:
        values["total"] = total
    async with sessions() as db:
        await db.execute(update(SaftImport).where(SaftImport.id == import_pk).values(**values))
        await db.commit()


async def _finish(sessions: Sessions, import_pk: int, **values: Any) -> None:
    async with sessions() as db:
        await db.execute(
            update(SaftImport)
            .where(SaftImport.id == import_pk)
            .values(updated_at=utc_now(), **values)
        )
        await db.commit()


async def _empty_files(sessions: Sessions, working: WorkingStore, import_pk: int) -> None:
    async with sessions() as db:
        keys = await _keys(db, SaftImportFile.import_id == import_pk)
        await db.execute(_let_go(SaftImportFile.import_id == import_pk))
        await db.commit()
    await forget(working, keys)


async def _company(db: AsyncSession, job: SaftImport) -> Company:
    # By the id on the import's own row, which a company-checked request wrote;
    # nothing a caller sends reaches this lookup (docs/company-isolation.md).
    company = await db.scalar(select(Company).where(Company.id == job.company_id))
    if company is None:
        raise ImportNotFoundError("finner ikke selskapet innlesingen gjelder")
    return company


async def _files(
    sessions: Sessions, import_pk: int, kind: FileKind = FileKind.READ
) -> list[tuple[int, str, str | None]]:
    """The import's files of ``kind`` in upload order: id, name and where the stored copy is."""
    async with sessions() as db:
        return list(
            (
                await db.execute(
                    select(SaftImportFile.id, SaftImportFile.filename, SaftImportFile.storage_key)
                    .where(SaftImportFile.import_id == import_pk, SaftImportFile.kind == kind.value)
                    .order_by(SaftImportFile.position)
                )
            ).tuples()
        )


def _gone() -> str:
    return f"filene ble slettet etter {FILE_LIFETIME.days} dager. Last dem opp på nytt"


async def _legacy_content(sessions: Sessions, file_pk: int) -> bytes | None:
    """A file kept in the database, as uploads were before the working store."""
    async with sessions() as db:
        content: bytes | None = await db.scalar(
            select(SaftImportFile.content).where(SaftImportFile.id == file_pk)
        )
        return content


def _read_stored(working: WorkingStore, key: str) -> SaftEntries:
    """Parse a stored SAF-T file while it downloads. Blocking: runs in a worker thread."""
    with working.open(key) as raw, gzip.GzipFile(fileobj=raw) as xml:
        try:
            return reader.read_entries_xml(xml)
        except (OSError, EOFError) as error:
            # A copy we compressed ourselves that does not decompress was
            # damaged on the way; the person can only upload it again.
            raise InvalidSaftFileError(
                "kopien av filen hos Myntos kan ikke leses. Last den opp på nytt"
            ) from error


async def _parse(
    sessions: Sessions, working: WorkingStore, import_pk: int
) -> list[move.SourceFile]:
    """Every file of the import, read one at a time, each as it streams from the store."""
    files = await _files(sessions, import_pk)
    parsed: list[move.SourceFile] = []
    for index, (file_pk, filename, key) in enumerate(files):
        await _progress(
            sessions, import_pk, f"Leser «{filename}» ({index + 1} av {len(files)})", index
        )
        try:
            if key is not None:
                entries: SaftEntries = await run_in_threadpool(_read_stored, working, key)
            else:
                content = await _legacy_content(sessions, file_pk)
                if content is None:
                    raise InvalidSaftFileError(_gone())
                entries = await run_in_threadpool(reader.read_entries, BytesIO(content))
                del content
        except ObjectNotFoundError as error:
            raise InvalidSaftFileError(_gone()) from error
        except InvalidSaftFileError as error:
            raise InvalidSaftFileError(f"«{filename}»: {error}") from error
        parsed.append(move.SourceFile(name=filename, entries=entries))
        async with sessions() as db:
            await db.execute(
                update(SaftImportFile)
                .where(SaftImportFile.id == file_pk)
                .values(year=entries.balances.start.year)
            )
            await db.commit()
    return parsed


async def _load(sessions: Sessions, import_pk: int) -> SaftImport | None:
    async with sessions() as db:
        job: SaftImport | None = await db.scalar(
            select(SaftImport).where(SaftImport.id == import_pk)
        )
        return job


async def read(sessions: Sessions, working: WorkingStore, import_pk: int) -> None:
    """Read an import's files and store what they would do. Commits nothing to the books."""
    _running.add(import_pk)
    try:
        job = await _load(sessions, import_pk)
        if job is None:
            return
        try:
            files = await _parse(sessions, working, import_pk)
            await _progress(sessions, import_pk, "Går gjennom det som blir ført", len(files))
            async with sessions() as db:
                company = await _company(db, job)
                if job.purpose == ImportPurpose.SAMMENLIGNING.value:
                    result = await compare.compare(
                        db, company=company, found=files[0].entries, til=job.til
                    )
                    review = comparison_review(result)
                else:
                    report = await move.apply(
                        db,
                        company=company,
                        files=files,
                        til=job.til,
                        created_by_user_id=job.created_by_user_id,
                    )
                    review = move_review(report)
                await db.rollback()
            if job.purpose == ImportPurpose.IMPORT.value:
                # After the move, which refuses another company's SAF-T files:
                # an export's documents are kept only with books that are
                # this company's.
                review["archives"] = await _archives(sessions, working, import_pk)
        except (SaftImportError, LedgerError) as error:
            await _finish(
                sessions,
                import_pk,
                status=ImportStatus.FAILED.value,
                step=None,
                error=_sentence(str(error)),
                error_code=_code(error),
            )
            return
        except Exception:
            logger.exception("SAF-T-innlesing %s feilet", import_pk)
            await _finish(
                sessions,
                import_pk,
                status=ImportStatus.FAILED.value,
                step=None,
                error="Noe gikk galt hos oss mens filene ble lest. Prøv igjen, eller ta kontakt.",
                error_code="internal_error",
            )
            return
        await _finish(
            sessions,
            import_pk,
            status=ImportStatus.READY.value,
            step=None,
            done=len(files),
            review=review,
            error=None,
            error_code=None,
        )
        if job.purpose == ImportPurpose.SAMMENLIGNING.value:
            # Compared is done: there is nothing more to read the file for.
            await _empty_files(sessions, working, import_pk)
    finally:
        _running.discard(import_pk)


async def _archives(
    sessions: Sessions, working: WorkingStore, import_pk: int
) -> list[dict[str, Any]]:
    """What keeping each export's documents will do, read from the ZIP directories alone."""
    found: list[dict[str, Any]] = []
    for _, filename, key in await _files(sessions, import_pk, FileKind.EXPORT):
        if key is None:
            raise InvalidSaftFileError(_gone())
        try:
            found.append(await run_in_threadpool(archive.read_review, working, key, filename))
        except ObjectNotFoundError as error:
            raise InvalidSaftFileError(_gone()) from error
    return found


async def post(
    sessions: Sessions, working: WorkingStore, import_pk: int, storage: DocumentStorage
) -> None:
    """Post a reviewed import, all of it or nothing, then keep the documents of its export."""
    _running.add(import_pk)
    try:
        job = await _load(sessions, import_pk)
        if job is None or job.status != ImportStatus.POSTING.value:
            return
        # Set in the books' own transaction (_post_books): a restart after it
        # carries on with the documents and does not post again.
        if job.posted_at is None and not await _post_books(sessions, working, job):
            return
        await _keep_documents(sessions, working, storage, import_pk)
    finally:
        _running.discard(import_pk)


async def _post_books(sessions: Sessions, working: WorkingStore, job: SaftImport) -> bool:
    """The books, in one transaction that also marks the import as posted. False when stopped."""
    import_pk = job.id
    reviewed = job.review or {}
    try:
        files = await _parse(sessions, working, import_pk)
        await _progress(sessions, import_pk, "Fører regnskapet", len(files))
        async with sessions() as db:
            company = await _company(db, job)
            may_chart = await _may(db, job, Permission.LEDGER_CHART)
            if not await _may(db, job, Permission.LEDGER_WRITE):
                raise ImportNotPostableError(
                    "den som ba om føringen, har ikke lenger tilgang til å føre i dette regnskapet"
                )
            if job.posted_via_agent_key_id is not None:
                # No request is running, so the stamp a key's voucher carries
                # (docs/agents-and-api.md rule 6) is put on from here.
                db.info[agent_keys.AGENT_KEY_INFO] = job.posted_via_agent_key_id
            report = await move.apply(
                db,
                company=company,
                files=files,
                til=job.til,
                created_by_user_id=job.posted_by_user_id,
                may_chart=may_chart,
            )
            review = move_review(report)
            review["archives"] = reviewed.get("archives") or []
            # What the person said yes to: the books (and the documents with
            # them), or the documents alone. Anything else is not what they saw.
            if report.blocking or report.postable != bool(reviewed.get("postable")):
                await db.rollback()
                await _finish(
                    sessions,
                    import_pk,
                    status=ImportStatus.READY.value,
                    step=None,
                    review=review,
                    error="Ingenting ble ført: regnskapet har endret seg siden filene ble "
                    "lest. Se over gjennomgangen på nytt.",
                    error_code="review_changed",
                )
                return False
            if not report.postable:
                # The documents alone: the move wrote nothing worth keeping.
                await db.rollback()
            await db.execute(
                update(SaftImport)
                .where(SaftImport.id == import_pk)
                .values(result=review, posted_at=utc_now(), updated_at=utc_now())
            )
            await db.commit()
    except (SaftImportError, LedgerError) as error:
        await _finish(
            sessions,
            import_pk,
            status=ImportStatus.READY.value,
            step=None,
            error="Ingenting ble ført: " + _sentence(str(error)),
            error_code=_code(error),
        )
        return False
    except Exception:
        logger.exception("Føring av SAF-T-innlesing %s feilet", import_pk)
        await _finish(
            sessions,
            import_pk,
            status=ImportStatus.READY.value,
            step=None,
            error="Noe gikk galt hos oss, og ingenting ble ført. Prøv igjen, eller ta kontakt.",
            error_code="internal_error",
        )
        return False
    return True


async def _keep_documents(
    sessions: Sessions, working: WorkingStore, storage: DocumentStorage, import_pk: int
) -> None:
    """Copy the documents of the import's exports into the document store, then let the files go.

    Called once the books are posted. Each document is kept once
    (:func:`myntos.saft.archive.keep`), so a restart or a second try after a
    failure carries on where it stopped.
    """
    job = await _load(sessions, import_pk)
    if job is None:
        return
    result = dict(job.result or {})
    years = result.get("years") or []
    software = next((year["software"] for year in years if year.get("software")), None)
    exports = await _files(sessions, import_pk, FileKind.EXPORT)
    kept: list[dict[str, Any]] = []

    try:
        for _, filename, key in exports:
            if key is None:
                raise InvalidSaftFileError(_gone())

            async def progress(done: int, total: int, name: str = filename) -> None:
                await _progress(
                    sessions,
                    import_pk,
                    f"Tar vare på dokumentene i «{name}» ({done} av {total})",
                    done,
                    total,
                )

            kept.append(
                await archive.keep(
                    sessions,
                    storage,
                    working,
                    key=key,
                    file_name=filename,
                    company_id=job.company_id,
                    import_pk=import_pk,
                    software=software,
                    user_id=job.posted_by_user_id,
                    progress=progress,
                )
            )
    except (StorageError, SaftImportError) as error:
        logger.warning(
            "Dokumentene i eksporten til innlesing %s ble ikke tatt vare på",
            import_pk,
            exc_info=True,
        )
        await _documents_stopped(sessions, import_pk, result, _documents_reason(error))
        return
    except Exception:
        logger.exception(
            "Dokumentene i eksporten til innlesing %s ble ikke tatt vare på", import_pk
        )
        await _documents_stopped(
            sessions, import_pk, result, "Noe gikk galt hos oss. Prøv igjen, eller ta kontakt."
        )
        return
    if exports:
        result["archives"] = kept
    await _finish(
        sessions,
        import_pk,
        status=ImportStatus.POSTED.value,
        step=None,
        result=result,
        error=None,
        error_code=None,
    )
    await _empty_files(sessions, working, import_pk)


def _documents_reason(error: Exception) -> str:
    if isinstance(error, ObjectNotFoundError):
        return _sentence(_gone())
    if isinstance(error, StorageError):
        return "Lageret for dokumenter svarte ikke. Prøv igjen om litt."
    return _sentence(str(error))


async def _documents_stopped(
    sessions: Sessions, import_pk: int, result: dict[str, Any], reason: str
) -> None:
    """Posted, with documents left to keep: the files stay, and posting again keeps the rest."""
    first = (
        "Regnskapet er ført, men ikke alle dokumentene i eksporten ble tatt vare på"
        if result.get("postable")
        else "Ikke alle dokumentene i eksporten ble tatt vare på"
    )
    await _finish(
        sessions,
        import_pk,
        status=ImportStatus.POSTED.value,
        step=None,
        result=result,
        error=f"{first}. {reason} Det som er tatt vare på, blir ikke lagt inn to ganger.",
        error_code=DOCUMENTS_NOT_KEPT,
    )


# --- The documents behind the vouchers ---------------------------------------------------


async def _printout_content(
    sessions: Sessions, working: WorkingStore, file_pk: int, key: str | None
) -> bytes:
    """A printout's bytes, whole: pypdf reads a PDF from its end."""
    try:
        content = (
            await working.load(key) if key is not None else await _legacy_content(sessions, file_pk)
        )
    except ObjectNotFoundError as error:
        raise InvalidPrintoutError(_gone()) from error
    if content is None:
        raise InvalidPrintoutError(_gone())
    return content


async def _printouts(
    sessions: Sessions, working: WorkingStore, import_pk: int
) -> list[tuple[int, str, str | None, Printout]]:
    """Every printout of the upload, read, one at a time so only one is in memory as bytes.

    What is kept of each is the vouchers and their documents' digests, never the
    documents: :func:`post_documents` cuts them again file by file.
    """
    files = await _files(sessions, import_pk)
    parsed: list[tuple[int, str, str | None, Printout]] = []
    for index, (file_pk, filename, key) in enumerate(files):
        await _progress(
            sessions, import_pk, f"Leser «{filename}» ({index + 1} av {len(files)})", index
        )
        content = await _printout_content(sessions, working, file_pk, key)
        found = await run_in_threadpool(printout.read, content, name=filename)
        del content
        parsed.append((file_pk, filename, key, found))
    return parsed


def _reviewed(review: dict[str, Any] | None) -> frozenset[tuple[int, str]]:
    """What the review the person saw said would go into the archive."""
    return frozenset(
        (int(target["voucher_id"]), voucher.get("sha256") or "")
        for voucher in (review or {}).get("vouchers", [])
        if voucher.get("action") == documents.Action.ATTACH.value
        for target in voucher.get("targets", [])
        if not target.get("already")
    )


async def read_documents(sessions: Sessions, working: WorkingStore, import_pk: int) -> None:
    """Read an upload's printouts and store what putting them in the archive would do."""
    _running.add(import_pk)
    try:
        job = await _load(sessions, import_pk)
        if job is None:
            return
        try:
            files = await _printouts(sessions, working, import_pk)
            await _progress(sessions, import_pk, "Finner bilagene i Myntos", len(files))
            async with sessions() as db:
                company = await _company(db, job)
                planned = await documents.plan(
                    db, company=company, printouts=[(name, read) for _, name, _, read in files]
                )
                review = documents_review(planned)
        except (SaftImportError, LedgerError) as error:
            await _finish(
                sessions,
                import_pk,
                status=ImportStatus.FAILED.value,
                step=None,
                error=_sentence(str(error)),
                error_code=_code(error),
            )
            return
        except Exception:
            logger.exception("Utskrift av bilag %s kunne ikke leses", import_pk)
            await _finish(
                sessions,
                import_pk,
                status=ImportStatus.FAILED.value,
                step=None,
                error="Noe gikk galt hos oss mens utskriftene ble lest. Prøv igjen, eller ta "
                "kontakt.",
                error_code="internal_error",
            )
            return
        await _finish(
            sessions,
            import_pk,
            status=ImportStatus.READY.value,
            step=None,
            done=len(files),
            review=review,
            error=None,
            error_code=None,
        )
    finally:
        _running.discard(import_pk)


async def post_documents(
    sessions: Sessions, working: WorkingStore, import_pk: int, storage: DocumentStorage
) -> None:
    """Put a reviewed upload's documents in the bilag archive, all of them or none.

    The plan is made again from the files and the books as they are now, and
    must be the one the person saw: a voucher brought over since, or a document
    put on one by hand, changes what goes where, and then nothing is put on and
    the upload is ``ready`` again with the new review. The rows go in one
    transaction; a file written to the store before a failure is left there
    unreferenced, the harmless direction (``bilag_service.attach_document``).
    """
    _running.add(import_pk)
    try:
        job = await _load(sessions, import_pk)
        if job is None or job.status != ImportStatus.POSTING.value:
            return
        try:
            files = await _printouts(sessions, working, import_pk)
            await _progress(sessions, import_pk, "Legger dokumentene i bilagsarkivet", len(files))
            async with sessions() as db:
                company = await _company(db, job)
                if not await _may(db, job, Permission.LEDGER_WRITE):
                    raise ImportNotPostableError(
                        "den som ba om det, har ikke lenger tilgang til å føre i dette regnskapet"
                    )
                planned = await documents.plan(
                    db, company=company, printouts=[(name, read) for _, name, _, read in files]
                )
                review = documents_review(planned)
                if not planned.postable or planned.attachments() != _reviewed(job.review):
                    await db.rollback()
                    await _finish(
                        sessions,
                        import_pk,
                        status=ImportStatus.READY.value,
                        step=None,
                        review=review,
                        error="Ingenting ble lagt i bilagsarkivet: bilagene i Myntos eller "
                        "arkivet har endret seg siden utskriftene ble lest. Se over "
                        "gjennomgangen på nytt.",
                        error_code="review_changed",
                    )
                    return
                for index, (file_pk, _, key, _) in enumerate(files):
                    content = await _printout_content(sessions, working, file_pk, key)
                    await documents.attach(
                        db,
                        storage,
                        company=company,
                        plan=planned,
                        file_index=index,
                        content=content,
                        user_id=job.posted_by_user_id,
                    )
                    del content
                await db.commit()
        except (SaftImportError, LedgerError) as error:
            await _finish(
                sessions,
                import_pk,
                status=ImportStatus.READY.value,
                step=None,
                error="Ingenting ble lagt i bilagsarkivet: " + _sentence(str(error)),
                error_code=_code(error),
            )
            return
        except Exception:
            logger.exception("Dokumentene fra utskrift %s kunne ikke legges på", import_pk)
            await _finish(
                sessions,
                import_pk,
                status=ImportStatus.READY.value,
                step=None,
                error="Noe gikk galt hos oss, og ingenting ble lagt i bilagsarkivet. Prøv igjen, "
                "eller ta kontakt.",
                error_code="internal_error",
            )
            return
        await _finish(
            sessions,
            import_pk,
            status=ImportStatus.POSTED.value,
            step=None,
            done=len(files),
            result=review,
            posted_at=utc_now(),
            error=None,
            error_code=None,
        )
        await _empty_files(sessions, working, import_pk)
    finally:
        _running.discard(import_pk)


async def _may(db: AsyncSession, job: SaftImport, permission: Permission) -> bool:
    """Whether the person who asked for the post still holds ``permission`` in these books."""
    if job.posted_by_user_id is None:
        return False
    role = await db.scalar(
        select(CompanyMembership.role).where(
            CompanyMembership.company_id == job.company_id,
            CompanyMembership.user_id == job.posted_by_user_id,
        )
    )
    return role is not None and has_permission(role, permission)


async def resume(sessions: Sessions, storage: DocumentStorage, working: WorkingStore) -> None:
    """Start again whatever a restart stopped halfway. Called once, at startup."""
    async with sessions() as db:
        stopped = list(
            (
                await db.execute(
                    select(SaftImport.id, SaftImport.status, SaftImport.purpose).where(
                        SaftImport.status.in_(_ACTIVE)
                    )
                )
            ).tuples()
        )
    for import_pk, status, purpose in stopped:
        if import_pk in _running:
            continue
        reading = status == ImportStatus.READING.value
        if purpose == ImportPurpose.VEDLEGG.value:
            if reading:
                await read_documents(sessions, working, import_pk)
            else:
                await post_documents(sessions, working, import_pk, storage)
        elif reading:
            await read(sessions, working, import_pk)
        else:
            await post(sessions, working, import_pk, storage)


def resume_in_background(
    factory: Callable[[], Sessions],
    storage: Callable[[], DocumentStorage],
    working: Callable[[], WorkingStore],
) -> None:
    """Schedule :func:`resume` without holding up startup."""

    async def run() -> None:
        try:
            await resume(factory(), storage(), working())
        except Exception:
            logger.exception("Kunne ikke gjenoppta SAF-T-innlesinger etter omstart")

    task = asyncio.get_running_loop().create_task(run())
    _resumed.add(task)
    task.add_done_callback(_resumed.discard)


async def sweep(sessions: Sessions, working: WorkingStore) -> int:
    """Delete the stored files nothing needs any more. Once a day, from ``main._purge_daily``.

    First the files of every import uploaded more than :data:`FILE_LIFETIME`
    ago and not being worked on are let go, as :func:`_expire` does for the
    imports somebody looks at; the rest nobody may look at again. Then every
    object in the store that no row points at is deleted: those just let go, an
    import thrown away whose delete failed, the files of books deleted from
    Myntos (their rows went with the company, or were emptied by
    ``closure.close_company``), and an upload whose client left halfway. One
    younger than :data:`_ORPHAN_GRACE` is left alone: a file is stored before
    the row naming it is committed. Returns how many objects were deleted.
    """
    cutoff = utc_now() - FILE_LIFETIME
    async with sessions() as db:
        settled = (
            await db.execute(
                select(SaftImport.id, SaftImport.created_at).where(
                    SaftImport.status.in_(_SETTLED),
                    SaftImport.id.in_(select(SaftImportFile.import_id).where(_kept())),
                )
            )
        ).tuples()
        stale = [import_pk for import_pk, created in settled if _aware(created) < cutoff]
        if stale:
            await db.execute(_let_go(SaftImportFile.import_id.in_(stale)))
            await db.commit()
        referenced = set(await _keys(db))
    young = utc_now() - _ORPHAN_GRACE
    orphans = [
        item.key
        for item in await working.listing()
        if item.key not in referenced and item.modified < young
    ]
    await forget(working, orphans)
    return len(orphans)


# --- What is stored as the review ------------------------------------------------------


def _sentence(message: str) -> str:
    text = message.strip()
    if not text:
        return "Filene kunne ikke leses."
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def _code(error: Exception) -> str:
    """``SaftWrongYearError`` → ``saft_wrong_year``, the code the API gives the same refusal."""
    name = type(error).__name__.removesuffix("Error")
    return _CAMEL_BOUNDARY.sub("_", name).lower()


def _money(value: Decimal) -> str:
    return str(value)


def _finding(finding: Finding, year: int | None = None) -> dict[str, Any]:
    return {
        "year": year,
        "severity": finding.severity.value,
        "code": finding.code,
        "message": finding.message,
    }


def move_review(report: move.MoveReport) -> dict[str, Any]:
    """The move's report as the import stores it and the API answers it."""
    return {
        "kind": ImportPurpose.IMPORT.value,
        "postable": report.postable,
        "vouchers": report.vouchers,
        "first_year_before": report.first_year_before,
        "first_year_after": report.first_year_after,
        "years_opened": report.years_opened,
        "years": [
            {
                "year": year.year,
                "file_name": year.file_name,
                "software": year.software,
                "start_date": year.start.isoformat(),
                "end_date": year.end.isoformat(),
                "complete": year.complete,
                "through": year.through.isoformat(),
                "opened": year.opened,
                "vouchers": year.vouchers,
                "lines": year.lines,
                "first_date": year.first_date.isoformat() if year.first_date else None,
                "last_date": year.last_date.isoformat() if year.last_date else None,
                "already_imported": year.already_imported,
                "later": year.later,
                "months": [
                    {"month": m.month, "vouchers": m.vouchers, "total": _money(m.total)}
                    for m in year.months
                ],
            }
            for year in report.years
        ],
        "opening": (
            None
            if report.opening is None
            else {
                "year": report.opening.year,
                "action": report.opening.action,
                "replaces": report.opening.replaces,
                "replaces_year": report.opening.replaces_year,
                "lines": report.opening.lines,
                "total": _money(report.opening.total),
            }
        ),
        "accounts_to_add": [
            {
                "number": add.number,
                "description": add.description,
                "account_type": add.account_type.value,
                "grouping_category": add.grouping_category,
                "grouping_code": add.grouping_code,
                "source_accounts": list(add.source_accounts),
            }
            for add in report.accounts_to_add
        ],
        "parties_to_add": [
            {
                "party_type": add.party_type.value,
                "name": add.name,
                "orgnr": add.orgnr,
                "party_id": add.party_id,
                "address_line": add.address_line,
                "postal_code": add.postal_code,
                "city": add.city,
                "source_id": add.source_id,
            }
            for add in report.parties_to_add
        ],
        "dimensions_to_add": [
            {
                "code": add.code,
                "dimension_description": add.dimension_description,
                "dimension_id": add.dimension_id,
                "value": add.value,
                "description": add.description,
            }
            for add in report.dimensions_to_add
        ],
        "findings": [_finding(item.finding, item.year) for item in report.findings],
    }


def documents_review(plan: documents.DocumentPlan) -> dict[str, Any]:
    """The documents' plan as the upload stores it and the API answers it."""
    action = documents.Action
    return {
        "kind": ImportPurpose.VEDLEGG.value,
        "postable": plan.postable,
        "software": documents.SOFTWARE,
        "files": [
            {
                "file_name": file.name,
                "pages": file.pages,
                "vouchers": file.vouchers,
                "loose_pages": file.loose_pages,
            }
            for file in plan.files
        ],
        "vouchers_in_files": len(plan.vouchers),
        "to_attach": plan.count(action.ATTACH),
        "documents": plan.documents,
        "already_attached": plan.count(action.ALREADY_ATTACHED),
        "no_attachments": plan.count(action.NO_ATTACHMENTS),
        "not_in_books": plan.count(action.NOT_IN_BOOKS),
        "too_large": plan.count(action.TOO_LARGE),
        "repeated": plan.count(action.REPEATED),
        "vouchers": [
            {
                "number": voucher.printed.label,
                "file_name": voucher.file_name,
                "first_page": voucher.printed.first_page + 1,
                "pages": voucher.printed.pages,
                "attachment_pages": voucher.printed.attachment_pages,
                "printed_on": voucher.printed.on.isoformat() if voucher.printed.on else None,
                "action": voucher.action.value,
                "sha256": voucher.printed.sha256,
                "byte_size": voucher.printed.size,
                "printed_debit": (
                    _money(voucher.printed.debit) if voucher.printed.debit is not None else None
                ),
                "booked_debit": (
                    _money(voucher.booked_debit) if voucher.booked_debit is not None else None
                ),
                "targets": [
                    {
                        "voucher_id": target.voucher_pk,
                        "voucher_no": target.voucher_no,
                        "already": target.already,
                    }
                    for target in voucher.targets
                ],
            }
            for voucher in plan.vouchers
        ],
        "findings": [_finding(finding) for finding in plan.findings],
    }


def comparison_review(result: compare.Comparison) -> dict[str, Any]:
    """The comparison as the import stores it and the API answers it."""
    source = result.source
    return {
        "kind": ImportPurpose.SAMMENLIGNING.value,
        "source": {
            "software": source.software,
            "audit_file_version": source.audit_file_version,
            "orgnr": source.orgnr,
            "company_name": source.company_name,
            "start_date": source.start.isoformat(),
            "end_date": source.end.isoformat(),
        },
        "start_date": result.start.isoformat(),
        "through": result.through.isoformat(),
        "matches": result.matches,
        "accounts_compared": result.accounts_compared,
        "parties_compared": result.parties_compared,
        "terms_compared": result.terms_compared,
        "accounts": [
            {
                "account_number": a.account_number,
                "description": a.description,
                "in_file": _money(a.in_file),
                "in_myntos": _money(a.in_myntos),
                "difference": _money(a.difference),
                "months": list(a.months),
            }
            for a in result.accounts
        ],
        "parties": [
            {
                "party_type": p.party_type.value,
                "name": p.name,
                "source_id": p.source_id,
                "party_id": p.party_pk,
                "in_file": _money(p.in_file),
                "in_myntos": _money(p.in_myntos),
                "difference": _money(p.difference),
            }
            for p in result.parties
        ],
        "vat": [
            {
                "termin": v.termin,
                "code": v.code,
                "spesifikasjon": v.spesifikasjon,
                "base_in_file": _money(v.base_in_file),
                "base_in_myntos": _money(v.base_in_myntos),
                "vat_in_file": _money(v.vat_in_file),
                "vat_in_myntos": _money(v.vat_in_myntos),
            }
            for v in result.vat
        ],
        "findings": [_finding(f) for f in result.findings],
    }


__all__ = [
    "DOCUMENTS_NOT_KEPT",
    "FILE_LIFETIME",
    "ImportBusyError",
    "ImportNotFoundError",
    "ImportNotPostableError",
    "ImportedDocumentNotFoundError",
    "Sessions",
    "comparison_review",
    "create",
    "discard",
    "documents_in",
    "documents_left",
    "documents_review",
    "forget",
    "get",
    "is_running",
    "list_for",
    "move_review",
    "post",
    "post_documents",
    "postable",
    "read",
    "read_documents",
    "resume",
    "resume_in_background",
    "start_posting",
    "sweep",
]
