"""Companies — the bookkeeping-obligated entities inside an organization.

An organization is the account and the tenant; a company is a set of books with
an orgnr. A sole trader has one of each, an accounting firm has one
organization and many companies. Nothing here is reachable without a membership
in the owning organization, and a company belonging to someone else's
organization answers 404 rather than 403.

Creating a company also seeds its kontoplan, VAT codes and first financial year
— see :func:`myntos.domain.ledger.service.create_company` for why that happens
here rather than lazily.

Creating one says nothing about *whose* company it is. ``POST
/companies/{id}/bekreft`` is the step that settles that: the caller's ID-porten
login is spent on Altinn's list of parties they may act for, and only an orgnr
on that list may be confirmed. One confirmed claim exists per orgnr across the
installation, so the second organization to try gets
``company_already_claimed`` and the choice of asking for access or keeping
unconfirmed books.
"""

import contextlib
from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from myntos.api.deps import CurrentCompany, CurrentUser, DbSession
from myntos.api.v1.enhetsregisteret import RegisteredUnitResponse, RoleResponse
from myntos.api.v1.skatt import authorized_parties_for
from myntos.core.clock import as_utc, utc_now
from myntos.domain.identity import service as identity_service
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.models import Company
from myntos.integrations import brreg
from myntos.integrations.altinn.authorized_parties import flatten
from myntos.integrations.brreg import BrregError

router = APIRouter(tags=["companies"])


class CompanyResponse(BaseModel):
    id: int
    organization_id: int
    orgnr: str
    name: str
    address_line: str | None
    postal_code: str | None
    city: str | None
    currency_code: str
    # What a salgsdokument has to be able to state about the seller — see
    # bokføringsforskriften § 5-1-2 and myntos.domain.faktura.
    vat_registered: bool
    in_foretaksregisteret: bool
    organisation_form: str | None
    bank_account: str | None
    # Whether this company's bilag may be shown to a model. See
    # myntos.domain.ledger.models.Company.
    ki_enabled: bool
    # --- Copied from Enhetsregisteret ---
    # Refreshed by POST /companies/{id}/hent and by a confirmation. The register
    # is the authority for all of them, so these are a cache with a timestamp
    # rather than a second opinion — register_synced_at is null until it has
    # been read for this company at least once.
    industry_code: str | None
    industry_description: str | None
    website: str | None
    phone: str | None
    email: str | None
    parent_orgnr: str | None
    register_synced_at: datetime | None
    # Whether somebody has proved through Altinn that they may act for this
    # orgnr. False is the ordinary state for books somebody just typed an orgnr
    # into, and the interface says "ubekreftet" rather than hiding anything:
    # bookkeeping is not gated on it, ownership is.
    verified: bool
    verified_at: datetime | None


class CreateCompanyRequest(BaseModel):
    organization_id: int
    # Nine digits; the mod-11 check digit is verified in the domain, so the
    # bound here is only the shape.
    orgnr: str = Field(min_length=9, max_length=11)
    name: str = Field(min_length=1, max_length=255)
    address_line: str | None = Field(default=None, max_length=255)
    postal_code: str | None = Field(default=None, max_length=16)
    city: str | None = Field(default=None, max_length=128)
    # Which year the books start in. Everything before it is refused, because
    # the seeded VAT rates are only asserted from this year onwards.
    first_financial_year: int | None = Field(default=None, ge=2000, le=2100)
    # Normally taken from Enhetsregisteret by the signup flow rather than typed.
    vat_registered: bool = False
    in_foretaksregisteret: bool = False
    organisation_form: str | None = Field(default=None, max_length=16)
    bank_account: str | None = Field(default=None, max_length=34)


class UpdateCompanyRequest(BaseModel):
    """Every field omitted is left alone; the orgnr cannot be changed at all."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    address_line: str | None = Field(default=None, max_length=255)
    postal_code: str | None = Field(default=None, max_length=16)
    city: str | None = Field(default=None, max_length=128)
    vat_registered: bool | None = None
    in_foretaksregisteret: bool | None = None
    organisation_form: str | None = Field(default=None, max_length=16)
    bank_account: str | None = Field(default=None, max_length=34)
    ki_enabled: bool | None = None


def _company_response(company: Company) -> CompanyResponse:
    return CompanyResponse(
        id=company.id,
        organization_id=company.organization_id,
        orgnr=company.orgnr,
        name=company.name,
        address_line=company.address_line,
        postal_code=company.postal_code,
        city=company.city,
        currency_code=company.currency_code,
        vat_registered=company.vat_registered,
        in_foretaksregisteret=company.in_foretaksregisteret,
        organisation_form=company.organisation_form,
        bank_account=company.bank_account,
        ki_enabled=company.ki_enabled,
        industry_code=company.industry_code,
        industry_description=company.industry_description,
        website=company.website,
        phone=company.phone,
        email=company.email,
        parent_orgnr=company.parent_orgnr,
        register_synced_at=(
            None if company.register_synced_at is None else as_utc(company.register_synced_at)
        ),
        verified=company.is_verified,
        # Through as_utc, because SQLite hands back a naive datetime while
        # PostgreSQL hands back an aware one, and the same row would otherwise
        # serialise with a trailing "Z" on the response that wrote it and
        # without one on every response that read it back.
        verified_at=None if company.verified_at is None else as_utc(company.verified_at),
    )


@router.get("/companies")
async def list_companies(user: CurrentUser, db: DbSession) -> list[CompanyResponse]:
    """Every company the caller can reach, across all their organizations."""
    companies = await ledger_service.list_companies(db, user_id=user.id)
    return [_company_response(company) for company in companies]


@router.post("/companies", status_code=status.HTTP_201_CREATED)
async def create_company(
    payload: CreateCompanyRequest, user: CurrentUser, db: DbSession
) -> CompanyResponse:
    """Add a company to one of the caller's organizations."""
    membership = await identity_service.get_membership(
        db, organization_id=payload.organization_id, user_id=user.id
    )
    # Adding a set of books is an administrative act, not a bookkeeping one:
    # it changes what the organization is responsible for reporting.
    require_permission(membership.role, Permission.ORGANIZATION_MANAGE)

    company = await ledger_service.create_company(
        db,
        organization=membership.organization,
        orgnr=payload.orgnr,
        name=payload.name,
        first_financial_year=payload.first_financial_year or utc_now().year,
        address_line=payload.address_line,
        postal_code=payload.postal_code,
        city=payload.city,
        vat_registered=payload.vat_registered,
        in_foretaksregisteret=payload.in_foretaksregisteret,
        organisation_form=payload.organisation_form,
        bank_account=payload.bank_account,
    )
    # A signup that never named its organization is still carrying "Ola Nordmann
    # (privat)". The company's name came from Enhetsregisteret, so it is a better
    # answer than the one we made up — but only against a placeholder, and only
    # until someone has chosen a name of their own.
    identity_service.adopt_name_if_placeholder(membership.organization, company.name)
    # Fill in everything else the register knows — næringskode, contact details,
    # the main unit a subunit belongs to — without the client having had to ask
    # for them. The signup form only ever looked up a name and an address, and
    # every field left empty here is one somebody types later, or does not.
    #
    # Best effort, like the confirmation: the books are set up either way, and a
    # slow Brønnøysund must not turn a completed signup into an error page.
    with contextlib.suppress(BrregError):
        await _refresh(db, company)
    return _company_response(company)


class RefreshResponse(BaseModel):
    """The company after a refresh, and what the register moved."""

    company: CompanyResponse
    # Field names, in the response model's own vocabulary, so the interface can
    # name what changed rather than saying "something did". Empty when the
    # register agreed with everything already stored, which is the ordinary case
    # and worth saying plainly.
    changed: list[str]
    # What the register holds beyond what is stored — board, revisor, kapital,
    # formål — for the page that shows who the company is. Fetched in the same
    # round trip because the caller is already waiting on Brønnøysund.
    unit: RegisteredUnitResponse
    roles: list[RoleResponse]


async def _refresh(db: DbSession, company: Company) -> RefreshResponse:
    """Read the register and copy what it says onto ``company``.

    Every failure here is :mod:`myntos.integrations.brreg`'s own error, which
    the error handler renders — 404 for a number the register does not know,
    410 for a deleted unit, 502 for a register that would not answer. None of
    them is dressed up as success: a refresh that quietly did nothing would
    leave the interface claiming the company was up to date.
    """
    unit = await brreg.lookup_unit(company.orgnr)
    changed = await ledger_service.refresh_company_from_register(db, company=company, unit=unit)
    # Roles are decoration on this endpoint: nothing is stored from them, and
    # the refresh they ride along with has already succeeded. Letting a second
    # failed request throw the first one's result away would mean an entry with
    # an odd role list could never be refreshed at all.
    roles: list[brreg.Role] = []
    with contextlib.suppress(BrregError):
        roles = await brreg.fetch_roles(company.orgnr, parent_orgnr=unit.parent_orgnr)
    return RefreshResponse(
        company=_company_response(company),
        changed=changed,
        unit=RegisteredUnitResponse(**asdict(unit)),
        roles=[
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
        ],
    )


@router.post("/companies/{company_id}/hent")
async def refresh_company(context: CurrentCompany, db: DbSession) -> RefreshResponse:
    """Re-read everything Enhetsregisteret says about this company.

    The register is the authority for the address, the organisasjonsform, the
    næringskode, the contact details and — the two that decide what an invoice
    may claim — the VAT and Foretaksregisteret markings. Businesses move, cross
    the VAT threshold and change their næring, and none of those events tells
    Myntos anything. This is how the copy catches up.

    ``ORGANIZATION_MANAGE`` because it can change what every future
    salgsdokument claims about the seller, which is the same reason editing
    those fields by hand needs it.

    The name is deliberately not touched — see
    :func:`myntos.domain.ledger.service.refresh_company_from_register`.
    """
    require_permission(context.membership.role, Permission.ORGANIZATION_MANAGE)
    return await _refresh(db, context.company)


@router.post("/companies/{company_id}/bekreft")
async def verify_company(
    context: CurrentCompany, user: CurrentUser, db: DbSession
) -> CompanyResponse:
    """Confirm, through Altinn, that these books belong to the caller.

    Creating a company proves nothing: anyone can type nine digits. This is the
    step that turns it into a claim, and the evidence is the same one a
    submission will later rest on — Altinn's own list of parties the person may
    act for, read from their ID-porten login.

    Three refusals, and each is a different thing for the interface to say:

    - **409 without a code** — no usable government login. The way out is the
      ID-porten button, not an error message.
    - **403 ``not_authorized_for_party``** — the login worked and Altinn does not
      list this orgnr. Rights are granted in Altinn, by the company, not here.
    - **409 ``company_already_claimed``** — somebody else confirmed this orgnr
      first. The frontend branches on that code to offer the two ways forward:
      ask an administrator there for access, or keep these books unconfirmed.

    ``ORGANIZATION_MANAGE``, the same permission as creating a set of books:
    settling whose company this is is an administrative act, and the personal
    ID-porten login it rests on belongs to the person making it.
    """
    require_permission(context.membership.role, Permission.ORGANIZATION_MANAGE)
    parties = await authorized_parties_for(db, user)
    # Subunits included, and flattened the same way the picker shows them: a
    # regnskapsfører engaged for one department holds rights on the subunit's
    # own orgnr, not on the main unit's.
    by_orgnr = {
        party.orgnr: party.party_id
        for party in flatten(parties)
        if party.is_usable and party.orgnr is not None
    }
    company = await ledger_service.confirm_company_ownership(
        db, company=context.company, user_id=user.id, authorized_parties=by_orgnr
    )
    # And bring the register's picture along with it. Confirming is the moment
    # somebody has just told us this company is theirs, which is exactly when a
    # stale address or a missing næringskode is worth fixing — and doing it here
    # means the ordinary path never leaves a company half-filled waiting for
    # somebody to press a second button.
    #
    # Best effort: the claim is established either way, and Brønnøysund being
    # slow is not a reason to refuse a confirmation that already succeeded.
    with contextlib.suppress(BrregError):
        await _refresh(db, company)
    return _company_response(company)


@router.get("/companies/{company_id}")
async def get_company(context: CurrentCompany) -> CompanyResponse:
    return _company_response(context.company)


@router.patch("/companies/{company_id}")
async def update_company(
    payload: UpdateCompanyRequest, context: CurrentCompany, db: DbSession
) -> CompanyResponse:
    """Correct the company's details, including what its invoices may claim.

    ``ORGANIZATION_MANAGE`` rather than ``LEDGER_WRITE``: whether the business is
    registered for VAT is not a bookkeeping entry, it is a statement about the
    entity that every future salgsdokument will repeat, and an ordinary member
    posting payments has no business changing it.
    """
    require_permission(context.membership.role, Permission.ORGANIZATION_MANAGE)
    company = await ledger_service.update_company_profile(
        db,
        company=context.company,
        name=payload.name,
        address_line=payload.address_line,
        postal_code=payload.postal_code,
        city=payload.city,
        vat_registered=payload.vat_registered,
        in_foretaksregisteret=payload.in_foretaksregisteret,
        organisation_form=payload.organisation_form,
        bank_account=payload.bank_account,
        ki_enabled=payload.ki_enabled,
    )
    return _company_response(company)
