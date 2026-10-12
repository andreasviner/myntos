"""Aggregated router for API v1. New endpoint modules register here."""

from fastapi import APIRouter

from myntos.api.v1 import (
    account,
    agent,
    approvals,
    arsoppgjor,
    auth,
    bank,
    cashflow,
    companies,
    enhetsregisteret,
    faktura,
    health,
    kontoplan,
    members,
    mva,
    mva_justering,
    organizations,
    parties,
    saft,
    skatt,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(organizations.router)
api_router.include_router(companies.router)
api_router.include_router(members.router)
api_router.include_router(parties.router)
api_router.include_router(cashflow.router)
api_router.include_router(faktura.router)
api_router.include_router(enhetsregisteret.router)
api_router.include_router(skatt.router)
api_router.include_router(mva.router)
api_router.include_router(saft.router)
api_router.include_router(account.router)
api_router.include_router(bank.router)
api_router.include_router(arsoppgjor.router)
api_router.include_router(kontoplan.router)
api_router.include_router(mva_justering.router)
api_router.include_router(agent.router)
api_router.include_router(approvals.router)
