"""Application entry point.

Run locally with:

    uv run uvicorn myntos.main:app --reload
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from myntos import __version__
from myntos.api.v1.router import api_router
from myntos.core.config import get_settings
from myntos.core.security import security_headers_middleware


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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
    )
    app.middleware("http")(security_headers_middleware)

    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
