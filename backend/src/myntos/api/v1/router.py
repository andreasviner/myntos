"""Aggregated router for API v1. New endpoint modules register here."""

from fastapi import APIRouter

from myntos.api.v1 import health

api_router = APIRouter()
api_router.include_router(health.router)
