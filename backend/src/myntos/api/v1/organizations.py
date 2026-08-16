"""Organizations and their members.

The organization is the tenant: every company, every ledger and — once there
are paid tiers — every subscription hangs off it, never off an individual user.
Adding a colleague is therefore an invitation into the organization, not a
shared password.

Authorization is uniform: every route below takes
:data:`~myntos.api.deps.CurrentMembership`, which resolves the caller's
membership from the path or answers 404, and then asks
:mod:`myntos.domain.identity.access` whether that role carries the permission.
No endpoint compares roles itself.
"""

from datetime import datetime

from fastapi import APIRouter, status
from pydantic import BaseModel, EmailStr, Field

from myntos.api.deps import CurrentMembership, CurrentUser, DbSession
from myntos.core.clock import as_utc
from myntos.core.config import get_settings
from myntos.domain.identity import service
from myntos.domain.identity.access import Permission, permissions_for, require_permission
from myntos.domain.identity.models import (
    OrganizationInvitation,
    OrganizationMembership,
    OrganizationRole,
)

router = APIRouter(prefix="/organizations", tags=["organizations"])


class OrganizationResponse(BaseModel):
    id: int
    name: str
    slug: str
    # See MembershipResponse in myntos.api.v1.auth — same flag, same purpose.
    name_is_placeholder: bool
    # The billing tier. Read-only for now: there is nothing to sell yet, and
    # when there is, it will be changed by the payment flow rather than by a
    # client PATCH.
    plan: str
    role: str
    permissions: list[str]


class MemberResponse(BaseModel):
    user_id: int
    email: EmailStr
    full_name: str
    role: str
    is_self: bool


class InvitationResponse(BaseModel):
    id: int
    email: EmailStr
    role: str
    expires_at: datetime
    # The full acceptance link, returned once when the invitation is created and
    # never again — only the token's hash is stored. Until Myntos sends email,
    # this is how the link reaches the invitee: the inviter passes it on.
    invite_url: str | None = None


class CreateOrganizationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class UpdateOrganizationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class InviteRequest(BaseModel):
    email: EmailStr
    role: OrganizationRole = OrganizationRole.MEMBER


class ChangeRoleRequest(BaseModel):
    role: OrganizationRole


def _organization_response(membership: OrganizationMembership) -> OrganizationResponse:
    organization = membership.organization
    return OrganizationResponse(
        id=organization.id,
        name=organization.name,
        slug=organization.slug,
        name_is_placeholder=organization.name_is_placeholder,
        plan=organization.plan,
        role=membership.role.value,
        permissions=sorted(permission.value for permission in permissions_for(membership.role)),
    )


def _invitation_response(
    invitation: OrganizationInvitation, *, token: str | None = None
) -> InvitationResponse:
    invite_url = None
    if token is not None:
        base = get_settings().frontend_base_url.rstrip("/")
        invite_url = f"{base}/invitasjon/?token={token}"
    return InvitationResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role.value,
        # as_utc, because SQLite hands the value back without its offset and an
        # expiry rendered without one is ambiguous to the client.
        expires_at=as_utc(invitation.expires_at),
        invite_url=invite_url,
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


@router.get("/{organization_id}/members")
async def list_members(membership: CurrentMembership, db: DbSession) -> list[MemberResponse]:
    require_permission(membership.role, Permission.MEMBER_READ)
    members = await service.list_members(db, membership.organization_id)
    return [
        MemberResponse(
            user_id=member.user_id,
            email=member.user.email,
            full_name=member.user.full_name,
            role=member.role.value,
            is_self=member.user_id == membership.user_id,
        )
        for member in members
    ]


@router.get("/{organization_id}/invitations")
async def list_invitations(
    membership: CurrentMembership, db: DbSession
) -> list[InvitationResponse]:
    require_permission(membership.role, Permission.MEMBER_MANAGE)
    invitations = await service.list_invitations(db, membership.organization_id)
    return [_invitation_response(invitation) for invitation in invitations]


@router.post("/{organization_id}/invitations", status_code=status.HTTP_201_CREATED)
async def invite_member(
    payload: InviteRequest, membership: CurrentMembership, db: DbSession
) -> InvitationResponse:
    """Invite an email address to join, at the given role.

    This is how an organization ends up with several accounts. The response
    carries the acceptance link exactly once.
    """
    invitation, token = await service.invite_member(
        db,
        organization=membership.organization,
        actor=membership,
        email=payload.email,
        role=payload.role,
    )
    return _invitation_response(invitation, token=token)


@router.delete(
    "/{organization_id}/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_invitation(
    invitation_id: int, membership: CurrentMembership, db: DbSession
) -> None:
    require_permission(membership.role, Permission.MEMBER_MANAGE)
    await service.revoke_invitation(
        db, organization_id=membership.organization_id, invitation_id=invitation_id
    )


@router.patch("/{organization_id}/members/{user_id}")
async def change_member_role(
    user_id: int, payload: ChangeRoleRequest, membership: CurrentMembership, db: DbSession
) -> MemberResponse:
    target = await service.get_membership(
        db, organization_id=membership.organization_id, user_id=user_id
    )
    await service.change_member_role(db, actor=membership, target=target, role=payload.role)
    await db.refresh(target, attribute_names=["user"])
    return MemberResponse(
        user_id=target.user_id,
        email=target.user.email,
        full_name=target.user.full_name,
        role=target.role.value,
        is_self=target.user_id == membership.user_id,
    )


@router.delete("/{organization_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(user_id: int, membership: CurrentMembership, db: DbSession) -> None:
    """Remove a member, or leave the organization by passing your own user id."""
    target = await service.get_membership(
        db, organization_id=membership.organization_id, user_id=user_id
    )
    await service.remove_member(db, actor=membership, target=target)
