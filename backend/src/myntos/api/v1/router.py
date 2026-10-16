"""Aggregated router for API v1. New endpoint modules register here."""

from fastapi import APIRouter

from myntos.api.v1 import (
    account,
    agent,
    anlegg,
    approvals,
    arsoppgjor,
    auth,
    bank,
    bilag,
    cashflow,
    companies,
    dimensjoner,
    enhetsregisteret,
    faktura,
    health,
    kontoplan,
    landbruk,
    landbruk_arsoppgjor,
    manuelt_bilag,
    members,
    mva,
    mva_justering,
    organizations,
    parties,
    regnskap,
    saft,
    saft_import,
    saft_innlesing,
    skatt,
    sletting,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(organizations.router)
# Before companies: /companies/slettede must not be read as /companies/{company_id}.
api_router.include_router(sletting.router)
api_router.include_router(companies.router)
api_router.include_router(members.router)
api_router.include_router(parties.router)
api_router.include_router(cashflow.router)
api_router.include_router(faktura.router)
api_router.include_router(enhetsregisteret.router)
api_router.include_router(skatt.router)
api_router.include_router(mva.router)
api_router.include_router(saft.router)
api_router.include_router(saft_import.router)
api_router.include_router(saft_innlesing.router)
api_router.include_router(bilag.router)
api_router.include_router(regnskap.router)
api_router.include_router(account.router)
api_router.include_router(bank.router)
api_router.include_router(arsoppgjor.router)
api_router.include_router(manuelt_bilag.router)
api_router.include_router(anlegg.router)
api_router.include_router(kontoplan.router)
api_router.include_router(dimensjoner.router)
api_router.include_router(landbruk.router)
api_router.include_router(landbruk_arsoppgjor.router)
api_router.include_router(mva_justering.router)
api_router.include_router(agent.router)
api_router.include_router(approvals.router)
