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

from myntos.api.deps import KEY_READ, CurrentCompany, DbSession, key_write
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


@router.get("/companies/{company_id}/parties", openapi_extra=KEY_READ)
async def list_parties(
    context: CurrentCompany, db: DbSession, party_type: PartyType | None = None
) -> list[PartyResponse]:
    """The company's customers and suppliers, by type and then by name.

    A party is who the company trades with: a customer (``kunde``) an invoice is
    addressed to, or a supplier (``leverandor``) a payment went to. It is master
    data rather than bookkeeping, and SAF-T exports it as the customer and
    supplier ledgers (kunde- og leverandørreskontro). ``party_type=kunde`` or
    ``party_type=leverandor`` narrows the list to one kind.

    Two numbers, and they are easy to mix up. ``id`` is the one the API takes
    back: it is what ``party_id`` means on an invoice and in
    ``…/faktura/neste-nummer``. ``party_id`` here is the number the person knows
    the party by, generated from 10000 for customers and 20000 for suppliers.

    A party carries no bank account number. Any role that may read the books
    may call it.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    found = await parties.list_parties(db, company=context.company, party_type=party_type)
    return [_party_response(party) for party in found]


@router.post(
    "/companies/{company_id}/parties",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_write("legge til en kunde eller leverandør"),
)
async def create_party(
    payload: CreatePartyRequest, context: CurrentCompany, db: DbSession
) -> PartyResponse:
    """Register a customer (``kunde``) or a supplier (``leverandor``).

    Changes nothing in the ledger. ``name`` is required. ``orgnr``, the
    nine-digit Norwegian organisasjonsnummer, is optional, since private persons
    and foreign companies have none, but its check digit is verified when given.
    ``party_id``, the number the person will know the party by, is generated
    when omitted: from 10000 for customers and from 20000 for suppliers.

    The type and that number can never be changed afterwards, because posted
    vouchers and SAF-T exports refer to them, and there is no delete. Look in
    ``GET …/parties`` first so the same customer is not registered twice. A
    customer an invoice will be addressed to needs an orgnr or an address
    (``address_line`` or ``postal_code``), or the invoice cannot be issued
    (bokføringsforskriften § 5-1-2).

    Refusals: 409 ``duplicate_party`` when that ``party_id`` is already taken for
    this type (leave it out and one is generated); 422 ``invalid_orgnr`` when
    the number's check digit is wrong (look it up in Enhetsregisteret, or leave
    it out).
    """
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


@router.patch(
    "/companies/{company_id}/parties/{party_pk}",
    openapi_extra=key_write("endre en kunde eller leverandør"),
)
async def update_party(
    party_pk: int, payload: UpdatePartyRequest, context: CurrentCompany, db: DbSession
) -> PartyResponse:
    """Correct a customer's or supplier's name, orgnr and address.

    Replaces all four: a field left out is cleared, not kept. Read the party
    from ``GET …/parties`` first and send back the values that are not changing.
    The type and the number (``party_id``) cannot be changed, because posted
    vouchers and SAF-T exports refer to them.

    An issued invoice keeps the buyer details it was issued with, for good. A
    draft copies the customer's details when it is saved with that customer, so
    a draft saved before this change shows the old ones until it is saved again
    with ``party_id``.

    Refusals: 404 ``party_not_found`` when the id is not one of this company's
    parties; 422 ``invalid_orgnr`` when the number's check digit is wrong.
    """
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
