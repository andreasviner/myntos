"""Liveness endpoint used by deploy targets and uptime checks."""

from fastapi import APIRouter
from pydantic import BaseModel

from myntos import __version__
from myntos.api.deps import NO_KEY_NEEDED
from myntos.core.config import get_settings

router = APIRouter(tags=["meta"])


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str


@router.get("/health", openapi_extra=NO_KEY_NEEDED)
async def health() -> HealthResponse:
    """Whether the API is up. No credentials, and nothing about anybody's books.

    For deploy scripts and uptime checks, and for an agent that got no answer
    from anything else and needs to know whether the problem is the service or
    its own request. Says only that the process is serving: it does not touch
    the database, so a slow database is not reported as a dead API.
    """
    return HealthResponse(
        status="ok",
        version=__version__,
        environment=get_settings().environment,
    )
