"""Customers and suppliers — SAF-T ``Customers`` / ``Suppliers``.

A payment records what it was for; a party records who it was with. Both are
needed, for different reasons: the type drives the skattemelding, while the
party is what makes a bilag traceable to a counterparty at an audit, and what
SAF-T's ``CustomerID``/``SupplierID`` on a ledger line refer to.

Two rules from SAF-T shape the model:

- Every party settles against a **balance account** — kundefordringer for a
  customer, leverandørgjeld for a supplier (``BalanceAccountStructure``). It is
  derived from the type rather than chosen, because there is exactly one right
  answer per type in the seeded chart.
- ``party_id`` is the number the *user* knows the party by, unique per company
  and type. It is generated when not supplied, so nobody has to invent a
  numbering scheme before recording their first invoice, but an accountant
  migrating from another system can bring theirs along.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.ledger.chart import PAYABLES_ACCOUNT, RECEIVABLES_ACCOUNT
from myntos.domain.ledger.errors import (
    ChartIncompleteError,
    DuplicatePartyError,
    PartyNotFoundError,
)
from myntos.domain.ledger.models import Account, Company, Party, PartyType
from myntos.domain.ledger.orgnr import validate_orgnr

# Where auto-generated numbers start, kept apart so a customer and a supplier
# are never confusable by number alone.
_FIRST_NUMBER = {PartyType.KUNDE: 10000, PartyType.LEVERANDOR: 20000}

BALANCE_ACCOUNT = {
    PartyType.KUNDE: RECEIVABLES_ACCOUNT,
    PartyType.LEVERANDOR: PAYABLES_ACCOUNT,
}


async def _balance_account(db: AsyncSession, company: Company, party_type: PartyType) -> Account:
    number = BALANCE_ACCOUNT[party_type]
    account = await db.scalar(
        select(Account).where(Account.company_id == company.id, Account.account_number == number)
    )
    if account is None:
        raise ChartIncompleteError(
            f"kontoplanen mangler konto {number}, som kreves for {party_type.value}er"
        )
    return account


async def _next_party_id(db: AsyncSession, company: Company, party_type: PartyType) -> str:
    """The next free number for this type.

    Counts from the highest one in use rather than from the row count, so
    supplying your own numbers does not make the generator start colliding with
    them.
    """
    highest = await db.scalar(
        select(func.max(Party.party_id)).where(
            Party.company_id == company.id, Party.party_type == party_type
        )
    )
    start = _FIRST_NUMBER[party_type]
    if highest is None or not str(highest).isdigit():
        return str(start)
    return str(max(int(highest) + 1, start))


async def list_parties(
    db: AsyncSession, *, company: Company, party_type: PartyType | None = None
) -> list[Party]:
    query = select(Party).where(Party.company_id == company.id)
    if party_type is not None:
        query = query.where(Party.party_type == party_type)
    result = await db.scalars(query.order_by(Party.party_type, Party.name))
    return list(result)


async def get_party(db: AsyncSession, *, company: Company, party_pk: int) -> Party:
    """One party belonging to ``company``, or :class:`PartyNotFoundError`.

    Scoped to the company, so a party id from another tenant is simply not
    found — the same answer as one that never existed.
    """
    party = await db.scalar(
        select(Party).where(Party.id == party_pk, Party.company_id == company.id)
    )
    if party is None:
        raise PartyNotFoundError("finner ikke kunden eller leverandøren")
    return party


async def create_party(
    db: AsyncSession,
    *,
    company: Company,
    party_type: PartyType,
    name: str,
    orgnr: str | None = None,
    party_id: str | None = None,
    address_line: str | None = None,
    postal_code: str | None = None,
    city: str | None = None,
    country: str = "NO",
) -> Party:
    """Register a customer or a supplier."""
    balance_account = await _balance_account(db, company, party_type)
    party = Party(
        company_id=company.id,
        party_type=party_type,
        party_id=party_id.strip() if party_id else await _next_party_id(db, company, party_type),
        name=name.strip(),
        # Optional, but checked when given: a wrong orgnr on a supplier is
        # wrong on every bilag that ever references them.
        orgnr=validate_orgnr(orgnr) if orgnr else None,
        address_line=address_line,
        postal_code=postal_code,
        city=city,
        country=country,
        balance_account_id=balance_account.id,
    )
    db.add(party)
    try:
        await db.flush()
    except IntegrityError as error:
        raise DuplicatePartyError(f"nummeret {party.party_id} er allerede i bruk") from error
    return party


async def update_party(
    db: AsyncSession,
    *,
    company: Company,
    party_pk: int,
    name: str,
    orgnr: str | None = None,
    address_line: str | None = None,
    postal_code: str | None = None,
    city: str | None = None,
) -> Party:
    """Correct a party's details.

    The number and the type are not editable. Both are referenced by posted
    vouchers, and SAF-T exports them per financial year — changing either would
    silently rewrite what past filings said.
    """
    party = await get_party(db, company=company, party_pk=party_pk)
    party.name = name.strip()
    party.orgnr = validate_orgnr(orgnr) if orgnr else None
    party.address_line = address_line
    party.postal_code = postal_code
    party.city = city
    await db.flush()
    return party
