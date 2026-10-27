"""Application entry point.

Run locally with:

    uv run uvicorn myntos.main:app --reload
"""

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from myntos import __version__
from myntos.api.deps import get_document_storage, get_working_files
from myntos.api.errors import (
    approval_held_handler,
    domain_error_handler,
    unhandled_error_middleware,
)
from myntos.api.v1.feedback import keep_out_of_the_access_log
from myntos.api.v1.router import api_router
from myntos.api.v1.saft_innlesing import get_import_sessions
from myntos.auth.approvals import ApprovalHeld
from myntos.core.config import get_settings
from myntos.core.security import BodySizeLimitMiddleware, security_headers_middleware
from myntos.db.session import get_engine
from myntos.domain.faktura.errors import FakturaError
from myntos.domain.feedback import service as feedback_service
from myntos.domain.feedback.errors import FeedbackError
from myntos.domain.identity import closure
from myntos.domain.identity.errors import IdentityError
from myntos.domain.ledger.errors import LedgerError
from myntos.integrations.brreg import BrregError
from myntos.saft import intake, jobs, reader
from myntos.storage.base import DocumentStorage
from myntos.storage.working import WorkingStore

logger = logging.getLogger(__name__)

_PURGE_FIRST_DELAY = 5 * 60
_PURGE_INTERVAL = 24 * 60 * 60

_MIB = 1024 * 1024
# The import of the previous program's books, which takes Fiken's complete
# export as it downloads (saft/intake.py), and reads its body only once it
# knows who is sending it (api/v1/saft_innlesing.py). First, so the wider
# pattern below does not claim it.
_IMPORT_UPLOAD_PATH = re.compile(r"^/api/v1/companies/\d+/saft/innlesing$")
# The other routes that read the previous program's files, the one upload
# bigger than a few bilag: a SAF-T file read in the request, and Tripletex's
# printout of the vouchers with their attachments (saft/vedlegg), which is
# about as large for a year. FastAPI reads these bodies before it knows who
# sent them, so their ceiling stays at what Altinn takes in one attachment.
_SAFT_UPLOAD_PATHS = re.compile(r"^/api/v1/companies/\d+/(saft/|inngaende-balanse/saft$)")

# Where production answers, named in the published document so that a client
# generated from it, or an agent reading it cold, calls the right host.
PUBLIC_API_URL = "https://api.myntos.no"

# The first thing a developer or an agent reads in /api/v1/openapi.json, so it
# says what the rest of the document assumes: the key extensions on every
# operation, where an agent starts, and what v1 promises
# (docs/agents-and-api.md rules 5, 11, 12 and 13).
_DESCRIPTION = """\
Myntos is a Norwegian accounting program for small companies: payments and \
receipts, vouchers, invoices, bank statements, the VAT return and the year end. \
This is its whole API: the web app at https://myntos.no is one client of it \
and calls nothing another client cannot.

Every operation says what a key made for an agent or a program may do with it. \
`x-myntos-key-scope` is `read`, `write` (can be undone), `post` (cannot be \
undone) or `none` (no credential needed); an operation without it is closed to \
keys (403 `not_for_agent_keys`). A writing operation carries \
`x-myntos-approval-label`, the words a person approves when the key may only \
ask (the request then answers 202 `approval_pending`), and \
`x-myntos-key-preview` marks a write that stores nothing. Agents start at \
https://myntos.no/llms.txt, which says how to connect and links a skill for \
every feature.

Errors are `{"detail", "code"}`: branch on `code`; `detail` is Norwegian for \
a person. Amounts are decimal strings (`"1250.00"`), never floats. Within v1, \
changes are additive: new operations, fields and optional parameters. Anything \
renamed or removed means v2, announced in llms.txt with a deprecation period.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open one database connection before anyone asks for one.

    The first connection to Neon costs about 600 ms from this machine — TLS,
    then SCRAM, then the compute waking if it had gone to sleep — and without
    this it is paid by whoever loads a page first after a deploy. The unit
    restarts on every ``./update.sh``, so that was reliably a real person.

    Deliberately not fatal. A backend that refuses to start because the database
    is briefly unreachable is worse than one that starts and says so on the
    first request: ``/api/v1/health`` is what update.sh waits for, and failing
    it here would turn a blip in Frankfurt into a failed deployment.
    """
    try:
        async with get_engine().connect() as connection:
            await connection.execute(text("select 1"))
    except Exception:  # startup must survive a database that is briefly unreachable
        logger.warning("Could not reach the database at startup", exc_info=True)
    # SAF-T imports a restart stopped halfway start again (myntos.saft.jobs).
    # Through the dependencies, so a test's database is the one looked in and
    # its store the one the old program's documents are put in.
    sessions = app.dependency_overrides.get(get_import_sessions, get_import_sessions)
    storage = app.dependency_overrides.get(get_document_storage, get_document_storage)
    working = app.dependency_overrides.get(get_working_files, get_working_files)
    jobs.resume_in_background(sessions, storage, working)
    purging = asyncio.get_running_loop().create_task(_purge_daily(sessions, storage, working))
    yield
    purging.cancel()


async def _purge_daily(
    sessions: Callable[[], jobs.Sessions],
    storage: Callable[[], DocumentStorage],
    working: Callable[[], WorkingStore],
) -> None:
    """Delete, once a day, deleted companies whose retention has run out.

    And feedback that was closed longer ago than ``feedback_retention_days``
    (docs/feedback.md), and the uploaded SAF-T files and printouts nothing
    needs any more (``jobs.sweep``), for the same reason: what decides it ships
    with it.

    In the process rather than on a systemd timer, so that it is deployed with
    the code that decides what it deletes and cannot be forgotten on a rebuilt
    machine. Nothing is due before the first books deleted from Myntos reach
    the end of their five years, so on most days it asks one question and
    goes back to sleep. The storage is only built when something is due,
    because a deployment without R2 credentials refuses to build it, and a
    process should not fail daily over a deletion with nothing to delete.

    Five minutes after startup rather than at once: a deploy is busy enough,
    and a day's delay in deleting something kept for five years is nothing.
    """
    await asyncio.sleep(_PURGE_FIRST_DELAY)
    while True:
        try:
            async with sessions()() as db:
                if await closure.due_for_purge(db):
                    await closure.purge_expired(db, storage())
        except Exception:  # a failed run is retried tomorrow, and must not end the loop
            logger.exception("Could not purge companies past their retention")
        try:
            async with sessions()() as db:
                days = get_settings().feedback_retention_days
                if purged := await feedback_service.purge_closed(db, retention_days=days):
                    logger.info("Deleted %d closed feedback items past their retention", purged)
                await db.commit()
        except Exception:  # as above: tomorrow again, and the loop goes on
            logger.exception("Could not purge closed feedback")
        try:
            if swept := await jobs.sweep(sessions(), working()):
                logger.info("Deleted %d working copies of uploads nothing needs", swept)
        except Exception:  # as above; the bucket's lifecycle rule is the backstop
            logger.exception("Could not sweep the working copies of uploads")
        await asyncio.sleep(_PURGE_INTERVAL)


def _log_our_own(level: str) -> None:
    """Hand the ``myntos`` loggers' records to stderr, at ``LOG_LEVEL``.

    uvicorn configures its own loggers and nobody else's, so everything below
    WARNING that Myntos itself logged went nowhere: Python's last-resort handler
    prints warnings and drops the rest. Among what it dropped was the line
    saying an invitation had been handed to Cloudflare, with the id Cloudflare's
    analytics are searched by, which is the line 2026-10-23 needed and did not
    have. systemd puts stderr in the journal.

    Once, however many times ``create_app`` runs, and still propagating, so a
    test's ``caplog`` sees the same records. Every INFO line under ``myntos``
    was written to leave the user's content out (a prompt, a filename, an
    address), which is what makes turning them on safe; keep it so.
    """
    package = logging.getLogger("myntos")
    package.setLevel(level.upper())
    if not any(getattr(handler, "_myntos_own", False) for handler in package.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        handler._myntos_own = True  # type: ignore[attr-defined]
        package.addHandler(handler)


def create_app() -> FastAPI:
    settings = get_settings()
    production = settings.environment == "production"
    _log_our_own(settings.log_level)

    app = FastAPI(
        title="Myntos API",
        version=__version__,
        description=_DESCRIPTION,
        # The schema is published everywhere, production included: it is the
        # reference developers and agents build against (docs/agents-and-api.md
        # rule 11). Nothing in it is a secret. The source it is generated from
        # is public, and every route already states in it what a key may do.
        # Under /api/v1/ because it describes v1, and a v2 gets its own.
        openapi_url="/api/v1/openapi.json",
        # The interactive page is a development tool, not the reference: it
        # loads its script from a CDN and sends requests from a form. Never in
        # production. It reads the schema from openapi_url above.
        docs_url=None if production else "/docs",
        redoc_url=None,
        # Production's address in production only. A development server whose
        # document named production would aim /docs' «Try it out» there; with
        # no servers listed, a client uses the host the document came from.
        servers=[{"url": PUBLIC_API_URL}] if production else None,
        lifespan=lifespan,
    )

    # Middleware, innermost first — each call wraps the ones before it, so the
    # order below is the reverse of the order a response passes through them.
    #
    # unhandled_error_middleware goes first, and so ends up *inside*
    # CORSMiddleware, which is the whole point: an exception that escaped it
    # would be answered by Starlette's ServerErrorMiddleware, outside every
    # middleware here, and the resulting 500 would reach the browser with no CORS
    # headers — reported as an allow-list problem instead of the server error it
    # is. See the docstring there before reordering these.
    app.middleware("http")(unhandled_error_middleware)
    # Outside the error middleware, so nothing it catches can stand in for the
    # 413, and inside CORS, so a browser is shown the 413 rather than a CORS
    # failure. The numbers are in BodySizeLimitMiddleware's docstring.
    app.add_middleware(
        BodySizeLimitMiddleware,
        body=2 * _MIB,
        upload=64 * _MIB,
        large_uploads=(
            (_IMPORT_UPLOAD_PATH, intake.MAX_UPLOAD_BYTES),
            (_SAFT_UPLOAD_PATHS, reader.MAX_UPLOAD_BYTES),
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
    )
    app.middleware("http")(security_headers_middleware)

    # One handler for every error hierarchy we render — the status code for each
    # error lives in myntos.api.errors, not in the endpoints. A hierarchy that is
    # in that table but *not* registered here does not become a 400: it escapes
    # the handler entirely and surfaces as a 500, so the two lists have to be
    # kept in step. (Bilag errors need no line of their own; they subclass
    # LedgerError.)
    app.add_exception_handler(IdentityError, domain_error_handler)
    app.add_exception_handler(LedgerError, domain_error_handler)
    app.add_exception_handler(FakturaError, domain_error_handler)
    app.add_exception_handler(BrregError, domain_error_handler)
    app.add_exception_handler(FeedbackError, domain_error_handler)
    # Not an error: a key's request stored to wait for its person (rule 13).
    app.add_exception_handler(ApprovalHeld, approval_held_handler)

    app.include_router(api_router, prefix="/api/v1")
    # Anonymous feedback stays anonymous in the journal too (docs/feedback.md).
    keep_out_of_the_access_log()
    return app


app = create_app()
