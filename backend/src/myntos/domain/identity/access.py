"""What each organization role is allowed to do.

Pure functions over :class:`~myntos.domain.identity.models.OrganizationRole` —
no database, no HTTP. Keeping the role → permission table in one place is the
point: the alternative is ``if role == OWNER or role == ADMIN`` scattered across
endpoints, which is how an authorization rule ends up being enforced in four
places and forgotten in a fifth.

The ledger permissions are declared here even though nothing enforces them yet
(there are no ledger endpoints), because the roles are meaningless without
them: the reason a fourth role exists is that a company wants to give its
auditor read access without giving them the ability to post.
"""

import enum
from collections.abc import Mapping

from myntos.domain.identity.errors import LastOwnerError, PermissionDeniedError
from myntos.domain.identity.models import OrganizationRole


class Permission(enum.StrEnum):
    """A single capability, checked at the point of use."""

    ORGANIZATION_READ = "organization:read"
    ORGANIZATION_MANAGE = "organization:manage"
    ORGANIZATION_DELETE = "organization:delete"
    # Reserved for the paid tiers. Owner-only, deliberately: whoever can move
    # money must be someone the organization chose, not anyone an admin invited.
    BILLING_MANAGE = "billing:manage"
    MEMBER_READ = "member:read"
    MEMBER_MANAGE = "member:manage"
    LEDGER_READ = "ledger:read"
    LEDGER_WRITE = "ledger:write"


_VIEWER: frozenset[Permission] = frozenset(
    {
        Permission.ORGANIZATION_READ,
        Permission.MEMBER_READ,
        Permission.LEDGER_READ,
    }
)
_MEMBER: frozenset[Permission] = _VIEWER | {Permission.LEDGER_WRITE}
_ADMIN: frozenset[Permission] = _MEMBER | {
    Permission.ORGANIZATION_MANAGE,
    Permission.MEMBER_MANAGE,
}
_OWNER: frozenset[Permission] = _ADMIN | {
    Permission.ORGANIZATION_DELETE,
    Permission.BILLING_MANAGE,
}

ROLE_PERMISSIONS: Mapping[OrganizationRole, frozenset[Permission]] = {
    OrganizationRole.OWNER: _OWNER,
    OrganizationRole.ADMIN: _ADMIN,
    OrganizationRole.MEMBER: _MEMBER,
    OrganizationRole.VIEWER: _VIEWER,
}


def permissions_for(role: OrganizationRole) -> frozenset[Permission]:
    return ROLE_PERMISSIONS[role]


def has_permission(role: OrganizationRole, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]


def require_permission(role: OrganizationRole, permission: Permission) -> None:
    """Raise :class:`PermissionDeniedError` unless ``role`` carries ``permission``."""
    if not has_permission(role, permission):
        raise PermissionDeniedError(f"role '{role.value}' lacks permission '{permission.value}'")


def require_can_grant_role(actor_role: OrganizationRole, target_role: OrganizationRole) -> None:
    """Check that ``actor_role`` may hand out ``target_role``.

    Two rules. First, managing members at all needs
    :attr:`Permission.MEMBER_MANAGE`. Second — the one that is easy to miss —
    only an owner may create another owner: without it an admin could promote
    themselves to owner and take over billing and deletion rights, which makes
    the owner/admin distinction decorative.
    """
    require_permission(actor_role, Permission.MEMBER_MANAGE)
    if target_role is OrganizationRole.OWNER and actor_role is not OrganizationRole.OWNER:
        raise PermissionDeniedError("only an owner can grant the owner role")


def require_can_change_member(actor_role: OrganizationRole, current_role: OrganizationRole) -> None:
    """Check that ``actor_role`` may modify or remove a member holding ``current_role``.

    The mirror of :func:`require_can_grant_role`: demoting or removing an owner
    is an owner-only act, or an admin could simply delete the owners instead of
    promoting themselves.
    """
    require_permission(actor_role, Permission.MEMBER_MANAGE)
    if current_role is OrganizationRole.OWNER and actor_role is not OrganizationRole.OWNER:
        raise PermissionDeniedError("only an owner can modify another owner")


def require_owner_remains(
    *, owner_count: int, current_role: OrganizationRole, new_role: OrganizationRole | None
) -> None:
    """Refuse changes that would leave an organization with no owner.

    ``new_role`` is ``None`` when the member is being removed. An organization
    without an owner cannot be billed, cannot appoint anyone, and cannot be
    deleted — it is unrecoverable without operator intervention, so it must not
    be reachable through the API.
    """
    if current_role is not OrganizationRole.OWNER:
        return
    if new_role is OrganizationRole.OWNER:
        return
    if owner_count <= 1:
        raise LastOwnerError(
            "the organization would be left without an owner; appoint another owner first"
        )
