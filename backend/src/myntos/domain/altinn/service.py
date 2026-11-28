"""Database persistence for Altinn 3 systembruker records.

All lookups are scoped by ``company.id`` (docs/company-isolation.md). Network
calls to Altinn and Maskinporten stay in the API/integration layer so this
service remains pure database state.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.core.clock import utc_now
from myntos.domain.altinn.models import CompanySystemUser
from myntos.domain.ledger.models import Company


def new_external_ref(company: Company) -> str:
    """Stable, unique ``externalRef`` for a new Altinn system-user request.

    Includes the company id and a short random suffix so re-requesting (for
    example after an earlier request expired or when adding a resource) never
    collides with an old request in Altinn.
    """
    return f"myntos-{company.id}-{uuid.uuid4().hex[:12]}"


async def latest_for_company(db: AsyncSession, company: Company) -> CompanySystemUser | None:
    """Most recent system-user request for ``company``, approved or pending."""
    row: CompanySystemUser | None = await db.scalar(
        select(CompanySystemUser)
        .where(CompanySystemUser.company_id == company.id)
        .order_by(CompanySystemUser.id.desc())
        .limit(1)
    )
    return row


async def approved_for_resource(
    db: AsyncSession, company: Company, resource: str
) -> CompanySystemUser | None:
    """Newest approved system user for ``company`` that includes ``resource``."""
    rows = await db.scalars(
        select(CompanySystemUser)
        .where(
            CompanySystemUser.company_id == company.id,
            CompanySystemUser.status == "accepted",
        )
        .order_by(CompanySystemUser.id.desc())
    )
    for row in rows:
        if row.covers_resource(resource):
            return row
    return None


async def record_request(
    db: AsyncSession,
    *,
    company: Company,
    system_id: str,
    external_ref: str,
    altinn_request_id: str,
    status: str,
    resources: tuple[str, ...],
    access_packages: tuple[str, ...],
    confirm_url: str | None,
    requested_by_user_id: int | None,
) -> CompanySystemUser:
    """Store a newly created Altinn system-user request."""
    now = utc_now()
    normalised = status.strip().lower() or "new"
    row = CompanySystemUser(
        company_id=company.id,
        system_id=system_id,
        external_ref=external_ref,
        altinn_request_id=altinn_request_id,
        status=normalised,
        resources=list(resources),
        access_packages=list(access_packages),
        confirm_url=confirm_url,
        requested_by_user_id=requested_by_user_id,
        requested_at=now,
        checked_at=now,
        approved_at=now if normalised == "accepted" else None,
    )
    db.add(row)
    await db.flush()
    return row


async def update_status(
    db: AsyncSession,
    row: CompanySystemUser,
    *,
    status: str,
    confirm_url: str | None,
) -> CompanySystemUser:
    """Update a stored system-user row after polling Altinn."""
    now = utc_now()
    normalised = status.strip().lower() or row.status
    row.status = normalised
    if confirm_url is not None:
        row.confirm_url = confirm_url
    row.checked_at = now
    if normalised == "accepted" and row.approved_at is None:
        row.approved_at = now
    await db.flush()
    return row
