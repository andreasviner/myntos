"""SAF-T files read, compared and posted on the server, while the person does something else.

A farm's year is 600 vouchers and reads in seconds; a shop's is a hundred
thousand lines and a 50 MB upload. Done in the browser's requests, as it was,
that is a tab that must stay open through an upload, a read, a hundred small
requests adding accounts and parties, a second upload, a second read and a
third for the post. The report that this was slow is why this module exists.

So a file is uploaded **once**, into a :class:`~myntos.saft.models.SaftImport`,
and everything after happens here, in the background of the request that
started it (FastAPI's ``BackgroundTasks``, which run after the answer has been
sent):

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

**One process.** Which imports are being worked on is kept in this process
(:data:`_running`), as the agent keys' counters are, which is exact with the one
uvicorn process this deployment runs. A restart (every deploy) stops the work
in the middle; :func:`resume` runs at startup and starts it again. Nothing is
lost by that: a read stores nothing until it is done, and a post is one
transaction that either committed or did not.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
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
from myntos.saft import compare, move, reader
from myntos.saft.errors import InvalidSaftFileError, SaftImportError
from myntos.saft.models import ImportPurpose, ImportStatus, SaftImport, SaftImportFile
from myntos.saft.reader import SaftEntries

logger = logging.getLogger(__name__)

Sessions = async_sessionmaker[AsyncSession]

#: How long uploaded files are kept when nothing is done with them. Long enough
#: for an accountant to be asked and to answer; short enough that a file
#: somebody forgot does not stay with us.
FILE_LIFETIME = timedelta(days=14)

#: The imports this process is working on now. See the module docstring.
_running: set[int] = set()

#: The tasks :func:`resume` started, held so they are not collected mid-run.
_resumed: set[asyncio.Task[None]] = set()

_ACTIVE = (ImportStatus.READING.value, ImportStatus.POSTING.value)


@dataclass(frozen=True)
class Upload:
    filename: str
    content: bytes


class ImportNotFoundError(LedgerError):
    """No such import in these books (another company's is not found either)."""


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
    uploads: Sequence[Upload],
    til: date | None,
    user_id: int | None,
    key_id: int | None,
) -> SaftImport:
    """Store the uploaded files and mark the import as being read. Reads nothing yet."""
    if not uploads:
        raise InvalidSaftFileError("velg minst én SAF-T-fil")
    if purpose is ImportPurpose.SAMMENLIGNING and len(uploads) != 1:
        raise InvalidSaftFileError(
            "sammenligningen leser én fil: den for året det gamle programmet fører nå"
        )
    if len(uploads) > move.MAX_FILES:
        raise InvalidSaftFileError(f"Myntos leser høyst {move.MAX_FILES} filer, én per år")
    for upload in uploads:
        if len(upload.content) > reader.MAX_UPLOAD_BYTES:
            raise InvalidSaftFileError(
                f"«{upload.filename}» er større enn 200 MB, som er det største Altinn tar imot "
                "av en SAF-T-fil"
            )
        if not upload.content:
            raise InvalidSaftFileError(f"«{upload.filename}» er tom")
    job = SaftImport(
        company_id=company.id,
        purpose=purpose.value,
        status=ImportStatus.READING.value,
        til=til,
        created_by_user_id=user_id,
        created_via_agent_key_id=key_id,
        step="Venter på å lese filene",
        done=0,
        total=len(uploads),
        files=[
            SaftImportFile(
                position=index,
                filename=(upload.filename or f"saft-{index + 1}.xml")[:255],
                size=len(upload.content),
                content=upload.content,
            )
            for index, upload in enumerate(uploads)
        ],
    )
    db.add(job)
    await db.flush()
    _running.add(job.id)
    return job


async def get(db: AsyncSession, *, company: Company, import_pk: int) -> SaftImport:
    """One of ``company``'s imports, looked up with the company, never by id alone."""
    job = await db.scalar(
        select(SaftImport).where(SaftImport.id == import_pk, SaftImport.company_id == company.id)
    )
    if job is None:
        raise ImportNotFoundError("finner ikke innlesingen")
    await _expire(db, [job])
    return job


async def list_for(db: AsyncSession, *, company: Company, limit: int = 20) -> list[SaftImport]:
    """The company's imports, newest first."""
    jobs = list(
        await db.scalars(
            select(SaftImport)
            .where(SaftImport.company_id == company.id)
            .order_by(SaftImport.id.desc())
            .limit(limit)
        )
    )
    await _expire(db, jobs)
    return jobs


async def _expire(db: AsyncSession, jobs: Sequence[SaftImport]) -> None:
    """Empty the files of imports left untouched past :data:`FILE_LIFETIME`.

    Done when the imports are read rather than by a timer, as a waiting
    approval's files are: the next person to look is the first one it matters to.
    """
    cutoff = utc_now() - FILE_LIFETIME
    stale = [
        job.id
        for job in jobs
        if job.status in (ImportStatus.READY.value, ImportStatus.FAILED.value)
        and _aware(job.updated_at) < cutoff
    ]
    if stale:
        await db.execute(
            update(SaftImportFile)
            .where(SaftImportFile.import_id.in_(stale), SaftImportFile.content.is_not(None))
            .values(content=None)
        )


def _aware(value: datetime) -> datetime:
    # SQLite hands back naive datetimes for timezone-aware columns.
    return value if value.tzinfo is not None else value.replace(tzinfo=utc_now().tzinfo)


async def files_kept(db: AsyncSession, job: SaftImport) -> bool:
    """Whether the uploaded files are still here to be read or posted from."""
    found = await db.scalar(
        select(SaftImportFile.id).where(
            SaftImportFile.import_id == job.id, SaftImportFile.content.is_not(None)
        )
    )
    return found is not None


def is_running(job: SaftImport) -> bool:
    return job.id in _running


async def discard(db: AsyncSession, *, company: Company, import_pk: int) -> None:
    """Throw an import away, files and all. Not one being worked on, and not a posted one.

    A posted import is the record of what was brought over and when; it stays,
    without its files.
    """
    job = await get(db, company=company, import_pk=import_pk)
    if job.status in _ACTIVE:
        raise ImportBusyError("innlesingen er i gang og kan ikke forkastes nå")
    if job.status == ImportStatus.POSTED.value:
        raise ImportBusyError("innlesingen er ført, og står som et spor av hva som ble hentet inn")
    await db.delete(job)
    await db.flush()


async def start_posting(
    db: AsyncSession,
    *,
    company: Company,
    import_pk: int,
    user_id: int,
    key_id: int | None,
    may_chart: bool,
) -> SaftImport:
    """Mark a reviewed import as being posted. :func:`post` does the posting."""
    job = await get(db, company=company, import_pk=import_pk)
    if job.purpose != ImportPurpose.IMPORT.value:
        raise ImportNotPostableError("en sammenligning fører ingenting")
    if job.status != ImportStatus.READY.value or job.review is None:
        raise ImportBusyError(
            "innlesingen er ikke klar til å føres"
            if job.status in _ACTIVE
            else "innlesingen kan ikke føres slik den står"
        )
    if not job.review.get("postable"):
        raise ImportNotPostableError(
            "gjennomgangen har funn som stopper føringen, eller ingenting nytt å føre"
        )
    if job.review.get("accounts_to_add") and not may_chart:
        raise ImportNotPostableError(
            "filene fører på kontoer som ikke er i kontoplanen, og bare en administrator "
            "eller eier kan legge dem til. Be en av dem om å føre regnskapet fra filene"
        )
    if not await files_kept(db, job):
        raise ImportNotPostableError(
            f"filene ble slettet etter {FILE_LIFETIME.days} dager uten at noe ble ført. Last "
            "dem opp på nytt"
        )
    other = await db.scalar(
        select(SaftImport.id).where(
            SaftImport.company_id == company.id,
            SaftImport.status == ImportStatus.POSTING.value,
            SaftImport.id != job.id,
        )
    )
    if other is not None:
        raise ImportBusyError("en annen innlesing føres akkurat nå. Vent til den er ferdig")
    job.status = ImportStatus.POSTING.value
    job.posted_by_user_id = user_id
    job.posted_via_agent_key_id = key_id
    job.error = None
    job.error_code = None
    job.step = "Venter på å føre"
    job.done = 0
    job.total = len(job.review.get("years", [])) + 1
    job.updated_at = utc_now()
    await db.flush()
    _running.add(job.id)
    return job


# --- The work ------------------------------------------------------------------------


async def _progress(sessions: Sessions, import_pk: int, step: str, done: int) -> None:
    """Say how far the work has come, in a transaction of its own so the page sees it now."""
    async with sessions() as db:
        await db.execute(
            update(SaftImport)
            .where(SaftImport.id == import_pk)
            .values(step=step, done=done, updated_at=utc_now())
        )
        await db.commit()


async def _finish(sessions: Sessions, import_pk: int, **values: Any) -> None:
    async with sessions() as db:
        await db.execute(
            update(SaftImport)
            .where(SaftImport.id == import_pk)
            .values(updated_at=utc_now(), **values)
        )
        await db.commit()


async def _empty_files(sessions: Sessions, import_pk: int) -> None:
    async with sessions() as db:
        await db.execute(
            update(SaftImportFile).where(SaftImportFile.import_id == import_pk).values(content=None)
        )
        await db.commit()


async def _company(db: AsyncSession, job: SaftImport) -> Company:
    # By the id on the import's own row, which a company-checked request wrote;
    # nothing a caller sends reaches this lookup (docs/company-isolation.md).
    company = await db.scalar(select(Company).where(Company.id == job.company_id))
    if company is None:
        raise ImportNotFoundError("finner ikke selskapet innlesingen gjelder")
    return company


async def _parse(sessions: Sessions, import_pk: int) -> list[move.SourceFile]:
    """Every file of the import, read, one at a time so only one is in memory as bytes."""
    async with sessions() as db:
        files = list(
            (
                await db.execute(
                    select(SaftImportFile.id, SaftImportFile.filename)
                    .where(SaftImportFile.import_id == import_pk)
                    .order_by(SaftImportFile.position)
                )
            ).tuples()
        )
    parsed: list[move.SourceFile] = []
    for index, (file_pk, filename) in enumerate(files):
        await _progress(
            sessions, import_pk, f"Leser «{filename}» ({index + 1} av {len(files)})", index
        )
        async with sessions() as db:
            content = await db.scalar(
                select(SaftImportFile.content).where(SaftImportFile.id == file_pk)
            )
        if content is None:
            raise InvalidSaftFileError(
                f"filene ble slettet etter {FILE_LIFETIME.days} dager. Last dem opp på nytt"
            )
        try:
            entries: SaftEntries = await run_in_threadpool(reader.read_entries, BytesIO(content))
        except InvalidSaftFileError as error:
            raise InvalidSaftFileError(f"«{filename}»: {error}") from error
        del content
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


async def read(sessions: Sessions, import_pk: int) -> None:
    """Read an import's files and store what they would do. Commits nothing to the books."""
    _running.add(import_pk)
    try:
        job = await _load(sessions, import_pk)
        if job is None:
            return
        try:
            files = await _parse(sessions, import_pk)
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
            await _empty_files(sessions, import_pk)
    finally:
        _running.discard(import_pk)


async def post(sessions: Sessions, import_pk: int) -> None:
    """Post a reviewed import, all of it or nothing."""
    _running.add(import_pk)
    try:
        job = await _load(sessions, import_pk)
        if job is None or job.status != ImportStatus.POSTING.value:
            return
        try:
            files = await _parse(sessions, import_pk)
            await _progress(sessions, import_pk, "Fører regnskapet", len(files))
            async with sessions() as db:
                company = await _company(db, job)
                may_chart = await _may(db, job, Permission.LEDGER_CHART)
                if not await _may(db, job, Permission.LEDGER_WRITE):
                    raise ImportNotPostableError(
                        "den som ba om føringen, har ikke lenger tilgang til å føre i dette "
                        "regnskapet"
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
                if not report.postable:
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
                    return
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
            return
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
        await _empty_files(sessions, import_pk)
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


async def resume(sessions: Sessions) -> None:
    """Start again whatever a restart stopped halfway. Called once, at startup."""
    async with sessions() as db:
        stopped = list(
            (
                await db.execute(
                    select(SaftImport.id, SaftImport.status).where(SaftImport.status.in_(_ACTIVE))
                )
            ).tuples()
        )
    for import_pk, status in stopped:
        if import_pk in _running:
            continue
        if status == ImportStatus.READING.value:
            await read(sessions, import_pk)
        else:
            await post(sessions, import_pk)


def resume_in_background(factory: Callable[[], Sessions]) -> None:
    """Schedule :func:`resume` without holding up startup."""

    async def run() -> None:
        try:
            await resume(factory())
        except Exception:
            logger.exception("Kunne ikke gjenoppta SAF-T-innlesinger etter omstart")

    task = asyncio.get_running_loop().create_task(run())
    _resumed.add(task)
    task.add_done_callback(_resumed.discard)


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
    "FILE_LIFETIME",
    "ImportBusyError",
    "ImportNotFoundError",
    "ImportNotPostableError",
    "Sessions",
    "Upload",
    "comparison_review",
    "create",
    "discard",
    "get",
    "is_running",
    "list_for",
    "move_review",
    "post",
    "read",
    "resume",
    "resume_in_background",
    "start_posting",
]
