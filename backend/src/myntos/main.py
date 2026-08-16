"""Application entry point.

Run locally with:

    uv run uvicorn myntos.main:app --reload
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from myntos import __version__
from myntos.api.errors import domain_error_handler, unhandled_error_middleware
from myntos.api.v1.router import api_router
from myntos.core.config import get_settings
from myntos.core.security import security_headers_middleware
from myntos.domain.faktura.errors import FakturaError
from myntos.domain.identity.errors import IdentityError
from myntos.domain.ledger.errors import LedgerError
from myntos.integrations.brreg import BrregError


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

    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
