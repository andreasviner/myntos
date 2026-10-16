"""Application entry point.

Run locally with:

    uv run uvicorn myntos.main:app --reload
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from myntos import __version__
from myntos.api.deps import get_document_storage
from myntos.api.errors import (
    approval_held_handler,
    domain_error_handler,
    unhandled_error_middleware,
)
from myntos.api.v1.router import api_router
from myntos.api.v1.saft_innlesing import get_import_sessions
from myntos.auth.approvals import ApprovalHeld
from myntos.core.config import get_settings
from myntos.core.security import security_headers_middleware
from myntos.db.session import get_engine
from myntos.domain.faktura.errors import FakturaError
from myntos.domain.identity import closure
from myntos.domain.identity.errors import IdentityError
from myntos.domain.ledger.errors import LedgerError
from myntos.integrations.brreg import BrregError
from myntos.saft import jobs
from myntos.storage.base import DocumentStorage

logger = logging.getLogger(__name__)

_PURGE_FIRST_DELAY = 5 * 60
_PURGE_INTERVAL = 24 * 60 * 60


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
    # Through the dependency, so a test's database is the one looked in.
    sessions = app.dependency_overrides.get(get_import_sessions, get_import_sessions)
    jobs.resume_in_background(sessions)
    storage = app.dependency_overrides.get(get_document_storage, get_document_storage)
    purging = asyncio.get_running_loop().create_task(_purge_daily(sessions, storage))
    yield
    purging.cancel()


async def _purge_daily(
    sessions: Callable[[], jobs.Sessions], storage: Callable[[], DocumentStorage]
) -> None:
    """Delete, once a day, deleted companies whose retention has run out.

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
        await asyncio.sleep(_PURGE_INTERVAL)


def create_app() -> FastAPI:
    settings = get_settings()
    # Interactive API docs are a development tool; never expose them in production.
    show_docs = settings.environment != "production"

    app = FastAPI(
        title="Myntos API",
        version=__version__,
        docs_url="/docs" if show_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if show_docs else None,
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
    # Not an error: a key's request stored to wait for its person (rule 13).
    app.add_exception_handler(ApprovalHeld, approval_held_handler)

    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
