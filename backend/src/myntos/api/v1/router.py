"""Aggregated router for API v1. New endpoint modules register here."""

from fastapi import APIRouter

from myntos.api.v1 import (
    auth,
    cashflow,
    companies,
    enhetsregisteret,
    faktura,
    health,
    organizations,
    parties,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(organizations.router)
api_router.include_router(companies.router)
api_router.include_router(parties.router)
api_router.include_router(cashflow.router)
api_router.include_router(faktura.router)
api_router.include_router(enhetsregisteret.router)
