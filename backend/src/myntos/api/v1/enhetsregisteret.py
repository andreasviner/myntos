"""Looking a company or a party up by organisasjonsnummer.

Behind authentication even though Enhetsregisteret itself is open: an
unauthenticated endpoint here would make Myntos a free anonymous proxy for
Brønnøysundregistrene, and ours is the traffic their rate limiting would see.

Read-only and free of side effects. Nothing is stored and nothing is created —
the register's answer is a suggestion to whoever is filling in the form, who
stays the one deciding what actually gets saved.
"""

from dataclasses import asdict

from fastapi import APIRouter
from pydantic import BaseModel

from myntos.api.deps import CurrentUser
from myntos.domain.ledger.orgnr import validate_orgnr
from myntos.integrations import brreg

router = APIRouter(tags=["enhetsregisteret"])


class RegisteredUnitResponse(BaseModel):
    """Field-for-field :class:`~myntos.integrations.brreg.RegisteredUnit`."""

    orgnr: str
    name: str
    organisation_form: str | None
    organisation_form_description: str | None
    address_line: str | None
    postal_code: str | None
    city: str | None
    registered_in_vat_register: bool
    bankrupt: bool
    under_liquidation: bool


@router.get("/enhetsregisteret/{orgnr}")
async def lookup_unit(orgnr: str, _user: CurrentUser) -> RegisteredUnitResponse:
    """Look up a registered unit by its organisasjonsnummer.

    The check digit is verified here rather than left to the register, so a
    mistyped number comes back as "that is not a valid number" (422) instead of
    the far more confusing "no such company".
    """
    unit = await brreg.lookup_unit(validate_orgnr(orgnr))
    # Spelled out as a mapping rather than field by field because the response
    # model is deliberately the same shape: any divergence should be a decision,
    # and this fails loudly the moment one is introduced by accident.
    return RegisteredUnitResponse(**asdict(unit))
