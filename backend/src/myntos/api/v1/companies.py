"""Companies — the bookkeeping-obligated entities inside an organization.

An organization is the account and the tenant; a company is a set of books with
an orgnr. A sole trader has one of each, an accounting firm has one
organization and many companies. Nothing here is reachable without a membership
in the owning organization, and a company belonging to someone else's
organization answers 404 rather than 403.

Creating a company also seeds its kontoplan, VAT codes and first financial year
— see :func:`myntos.domain.ledger.service.create_company` for why that happens
here rather than lazily.
"""

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from myntos.api.deps import CurrentCompany, CurrentUser, DbSession
from myntos.core.clock import utc_now
from myntos.domain.identity import service as identity_service
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.models import Company

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
    )
    return _company_response(company)
