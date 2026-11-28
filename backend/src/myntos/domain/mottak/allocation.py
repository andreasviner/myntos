"""Inbox address allocation kept separate from ledger.service to avoid circular imports."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.ledger.models import Company
from myntos.domain.mottak import address as address_rules
from myntos.domain.mottak.models import CompanyInboxAddress, SenderPolicy


async def ensure_inbox_address(db: AsyncSession, *, company: Company) -> CompanyInboxAddress:
    """Return the company's inbox address row, allocating a unique local-part if absent."""
    existing = await db.scalar(
        select(CompanyInboxAddress).where(CompanyInboxAddress.company_id == company.id)
    )
    if existing is not None:
        return existing

    # Pre-load taken addresses so normal allocation succeeds on the first insert.
    taken_rows = await db.scalars(select(CompanyInboxAddress.local_part))
    taken = frozenset(str(part).lower() for part in taken_rows)

    for candidate in address_rules.candidate_local_parts(
        company.name, company.orgnr, extra_reserved=taken
    ):
        row = CompanyInboxAddress(
            company_id=company.id,
            local_part=candidate,
            enabled=company.closed_at is None,
            sender_policy=SenderPolicy.ALLE_MED_VARSEL,
            allowed_senders_json="[]",
        )
        try:
            async with db.begin_nested():
                db.add(row)
                await db.flush()
            return row
        except IntegrityError:
            raced = await db.scalar(
                select(CompanyInboxAddress).where(CompanyInboxAddress.company_id == company.id)
            )
            if raced is not None:
                return raced
            continue

    raise RuntimeError(f"could not allocate inbox address for company {company.id}")
