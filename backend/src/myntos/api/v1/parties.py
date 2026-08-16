"""Customers and suppliers.

Master data, not bookkeeping: creating a customer changes nothing in the
ledger, which is why it needs only :attr:`Permission.LEDGER_WRITE` and why
there is an editing route at all. Posted vouchers reference a party, so the
number and the type stay fixed once created — see
:func:`myntos.domain.ledger.parties.update_party`.

There is no delete. A party referenced by a posted voucher cannot be removed
without either breaking the reference or rewriting history, and the party who
has never been used is harmless.
"""

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from myntos.api.deps import CurrentCompany, DbSession
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.ledger import parties
from myntos.domain.ledger.models import Party, PartyType

router = APIRouter(tags=["parter"])


class PartyResponse(BaseModel):
    id: int
    party_type: PartyType
    party_id: str
    name: str
    orgnr: str | None
    address_line: str | None
    postal_code: str | None
    city: str | None
    balance_account_id: int


class CreatePartyRequest(BaseModel):
    party_type: PartyType
    name: str = Field(min_length=1, max_length=255)
    orgnr: str | None = Field(default=None, max_length=11)
    # Generated when omitted, so nobody has to invent a numbering scheme before
    # registering their first supplier.
    party_id: str | None = Field(default=None, max_length=64)
    address_line: str | None = Field(default=None, max_length=255)
    postal_code: str | None = Field(default=None, max_length=16)
    city: str | None = Field(default=None, max_length=128)


class UpdatePartyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    orgnr: str | None = Field(default=None, max_length=11)
    address_line: str | None = Field(default=None, max_length=255)
    postal_code: str | None = Field(default=None, max_length=16)
    city: str | None = Field(default=None, max_length=128)


def _party_response(party: Party) -> PartyResponse:
    return PartyResponse(
        id=party.id,
        party_type=party.party_type,
        party_id=party.party_id,
        name=party.name,
        orgnr=party.orgnr,
        address_line=party.address_line,
        postal_code=party.postal_code,
        city=party.city,
        balance_account_id=party.balance_account_id,
    )


@router.get("/companies/{company_id}/parties")
async def list_parties(
    context: CurrentCompany, db: DbSession, party_type: PartyType | None = None
) -> list[PartyResponse]:
    require_permission(context.membership.role, Permission.LEDGER_READ)
    found = await parties.list_parties(db, company=context.company, party_type=party_type)
    return [_party_response(party) for party in found]


@router.post("/companies/{company_id}/parties", status_code=status.HTTP_201_CREATED)
async def create_party(
    payload: CreatePartyRequest, context: CurrentCompany, db: DbSession
) -> PartyResponse:
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    party = await parties.create_party(
        db,
        company=context.company,
        party_type=payload.party_type,
        name=payload.name,
        orgnr=payload.orgnr,
        party_id=payload.party_id,
        address_line=payload.address_line,
        postal_code=payload.postal_code,
        city=payload.city,
    )
    return _party_response(party)


@router.patch("/companies/{company_id}/parties/{party_pk}")
async def update_party(
    party_pk: int, payload: UpdatePartyRequest, context: CurrentCompany, db: DbSession
) -> PartyResponse:
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    party = await parties.update_party(
        db,
        company=context.company,
        party_pk=party_pk,
        name=payload.name,
        orgnr=payload.orgnr,
        address_line=payload.address_line,
        postal_code=payload.postal_code,
        city=payload.city,
    )
    return _party_response(party)
