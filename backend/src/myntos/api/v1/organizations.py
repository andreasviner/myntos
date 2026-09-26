"""The account: whose companies these are, and who may add another.

An **organisasjon** is the account a person signs up for. It owns the companies
set up under it and nothing else that anybody looks at: who may open one
company's books, who is invited to it and what it costs all live on the company
now (:mod:`myntos.api.v1.members`, and ``plan`` on the company itself).

That is a narrower job than this module used to have, and the reason is worth
keeping. An account named after its first company (which Myntos used to do) and
displaying its own members beside one company's figures was indistinguishable
from one company's data appearing under another's name. Access is per company;
the account is the container, and it is not shown to anybody.

What is left is therefore small and mostly for the machinery: the id that
``POST /companies`` needs in order to know where new books belong, and the
permission that decides whether the caller may add them.
"""

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from myntos.api.deps import CurrentMembership, CurrentUser, DbSession
from myntos.domain.identity import service
from myntos.domain.identity.access import Permission, permissions_for, require_permission
from myntos.domain.identity.models import OrganizationMembership

router = APIRouter(prefix="/organizations", tags=["organizations"])


class OrganizationResponse(BaseModel):
    id: int
    name: str
    slug: str
    # See MembershipResponse in myntos.api.v1.auth — same flag, same purpose.
    name_is_placeholder: bool
    role: str
    permissions: list[str]


class CreateOrganizationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class UpdateOrganizationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


def _organization_response(membership: OrganizationMembership) -> OrganizationResponse:
    organization = membership.organization
    return OrganizationResponse(
        id=organization.id,
        name=organization.name,
        slug=organization.slug,
        name_is_placeholder=organization.name_is_placeholder,
        role=membership.role.value,
        permissions=sorted(permission.value for permission in permissions_for(membership.role)),
    )


@router.get("")
async def list_organizations(user: CurrentUser, db: DbSession) -> list[OrganizationResponse]:
    """Every organization the caller belongs to."""
    memberships = await service.list_memberships(db, user.id)
    return [_organization_response(membership) for membership in memberships]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_organization(
    payload: CreateOrganizationRequest, user: CurrentUser, db: DbSession
) -> OrganizationResponse:
    """Create another organization, with the caller as its owner.

    A regnskapsfører who takes on a client with separate access needs makes a
    second organization; anyone else never touches this.
    """
    organization = await service.create_organization(db, name=payload.name, owner=user)
    membership = await service.get_membership(db, organization_id=organization.id, user_id=user.id)
    return _organization_response(membership)


@router.get("/{organization_id}")
async def get_organization(membership: CurrentMembership) -> OrganizationResponse:
    require_permission(membership.role, Permission.ORGANIZATION_READ)
    return _organization_response(membership)


@router.patch("/{organization_id}")
async def update_organization(
    payload: UpdateOrganizationRequest, membership: CurrentMembership
) -> OrganizationResponse:
    require_permission(membership.role, Permission.ORGANIZATION_MANAGE)
    service.rename_organization(membership.organization, payload.name)
    return _organization_response(membership)
