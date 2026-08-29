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


class CapitalResponse(BaseModel):
    amount: str
    currency: str
    shares: int | None


class RegisteredUnitResponse(BaseModel):
    """Field-for-field :class:`~myntos.integrations.brreg.RegisteredUnit`.

    Everything the register holds that is of any use, rather than the handful
    the signup form fills in. The extra fields cost nothing — the lookup is one
    request either way — and each one is a field somebody would otherwise type,
    which is where a set of books ends up describing a subtly different entity.
    """

    orgnr: str
    name: str
    organisation_form: str | None
    organisation_form_description: str | None
    address_line: str | None
    postal_code: str | None
    city: str | None
    registered_in_vat_register: bool
    registered_in_foretaksregisteret: bool
    bankrupt: bool
    under_liquidation: bool
    under_forced_liquidation: bool
    municipality: str | None
    municipality_number: str | None
    country: str | None
    postal_address_line: str | None
    postal_address_postal_code: str | None
    postal_address_city: str | None
    industry_code: str | None
    industry_description: str | None
    secondary_industries: list[tuple[str, str]]
    website: str | None
    phone: str | None
    mobile: str | None
    email: str | None
    established_on: str | None
    registered_on: str | None
    registered_in_foretaksregisteret_on: str | None
    registered_in_vat_register_on: str | None
    last_submitted_annual_accounts: str | None
    parent_orgnr: str | None
    in_group: bool
    employees: int | None
    language: str | None
    sector_code: str | None
    sector_description: str | None
    capital: CapitalResponse | None
    purpose: list[str]
    activity: list[str]
    former_names: list[str]
    registered_in_frivillighetsregisteret: bool
    registered_in_stiftelsesregisteret: bool
    is_subunit: bool


class RoleResponse(BaseModel):
    """One registered role: a board member, a daglig leder, a revisor.

    Names only, never the birth date the register pairs them with. A board list
    is what an accountant needs; a date of birth is personal data with no
    bookkeeping use, and the place to keep it out of the browser is here — the
    same rule that keeps fødselsnummer out of the Altinn party list.
    """

    group: str
    group_description: str
    code: str
    description: str
    name: str
    # Set when the holder is a company, which is the ordinary case for revisor
    # and regnskapsfører.
    orgnr: str | None
    elected_by: str | None


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


@router.get("/enhetsregisteret/{orgnr}/roller")
async def lookup_roles(orgnr: str, _user: CurrentUser) -> list[RoleResponse]:
    """Who holds a registered role in the unit: styre, daglig leder, revisor…

    A separate call rather than part of the lookup above, because it is a second
    request to Brønnøysund and most callers do not want it: the signup form
    needs a name and an address, and the roles are for the page that shows who
    the company is.

    A **subunit** has no roles of its own — the register answers 404 — so the
    unit is looked up first and its ``overordnetEnhet`` followed. That costs a
    round trip and saves the caller from having to know the rule.

    An empty list is a legitimate answer, not a 404: plenty of units have
    nothing registered, and a missing role list is not a missing company.
    """
    number = validate_orgnr(orgnr)
    unit = await brreg.lookup_unit(number)
    roles = await brreg.fetch_roles(number, parent_orgnr=unit.parent_orgnr)
    # Field by field, unlike the unit above, because the shapes deliberately
    # differ: ``Role.resigned`` is a filter the integration has already applied,
    # and echoing it would invite a client to filter on it a second time.
    return [
        RoleResponse(
            group=role.group,
            group_description=role.group_description,
            code=role.code,
            description=role.description,
            name=role.name,
            orgnr=role.orgnr,
            elected_by=role.elected_by,
        )
        for role in roles
    ]
