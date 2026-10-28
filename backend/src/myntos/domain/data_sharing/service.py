"""Reading and changing what a company shares.

Whatever collects anything later asks :func:`allows` first, every time, and
not once at startup: a company that lowers its level has stopped sharing from
that moment, and docs/data-sharing.md promises that what was kept under the
higher level is deleted with it.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.data_sharing.models import ORDER, DataSharingChoice, DataSharingLevel
from myntos.domain.ledger.models import Company


def level_of(company: Company) -> DataSharingLevel:
    """The company's level. One the code no longer knows reads as nothing.

    A level withdrawn from the list in a later release must not be read as
    the one above it: a company's yes covers what it was shown, and nothing
    it was not.
    """
    try:
        return DataSharingLevel(company.data_sharing)
    except ValueError:
        return DataSharingLevel.NONE


def allows(company: Company, needed: DataSharingLevel) -> bool:
    """Whether the company's level covers ``needed``, which is never true of nothing."""
    if needed is DataSharingLevel.NONE:
        return False
    return ORDER.index(level_of(company)) >= ORDER.index(needed)


async def choose(
    db: AsyncSession, company: Company, level: DataSharingLevel, *, user_id: int | None
) -> Company:
    """Set the company's level, and log who did it.

    The same level again changes nothing and logs nothing, so a form saved
    twice does not read as two decisions.
    """
    if company.data_sharing == level.value:
        return company
    company.data_sharing = level.value
    db.add(DataSharingChoice(company_id=company.id, level=level.value, chosen_by_user_id=user_id))
    await db.flush()
    return company
