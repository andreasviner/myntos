"""Liveness endpoint used by deploy targets and uptime checks."""

from fastapi import APIRouter
from pydantic import BaseModel

from myntos import __version__
from myntos.core.config import get_settings

router = APIRouter(tags=["meta"])


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str


@router.get("/health")
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=__version__,
        environment=get_settings().environment,
    )
