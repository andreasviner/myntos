"""Application accounts, organizations and access control.

Who is logged into Myntos — as opposed to :mod:`myntos.auth.maskinporten` and
:mod:`myntos.auth.idporten`, which establish who we are to Skatteetaten. The
two planes never substitute for each other: a Myntos session authorises
bookkeeping in this application, and an ID-porten login is still required
before anything is submitted to the authorities.

Three layers:

- :mod:`myntos.domain.identity.models` — the tables, and the reasoning behind
  their shape (organization-as-tenant, credentials, revocable sessions).
- :mod:`myntos.domain.identity.access` — the role → permission table. Pure.
- :mod:`myntos.domain.identity.service` — the operations: register, sign in,
  invite, change roles.
"""

from myntos.domain.identity.access import (
    ROLE_PERMISSIONS,
    Permission,
    has_permission,
    permissions_for,
    require_can_change_member,
    require_can_grant_role,
    require_owner_remains,
    require_permission,
)
from myntos.domain.identity.errors import (
    AccessError,
    AccountDisabledError,
    AccountLockedError,
    EmailAlreadyRegisteredError,
    IdentityError,
    InvalidCredentialsError,
    InvalidInvitationError,
    InvitationEmailMismatchError,
    InvitationError,
    LastOwnerError,
    NoPasswordSetError,
    NotAMemberError,
    PermissionDeniedError,
    UnverifiedProviderEmailError,
    WeakPasswordError,
)
from myntos.domain.identity.models import (
    IdentityProvider,
    OAuthLoginAttempt,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    OrganizationRole,
    User,
    UserIdentity,
    UserSession,
)

__all__ = [
    "ROLE_PERMISSIONS",
    "AccessError",
    "AccountDisabledError",
    "AccountLockedError",
    "EmailAlreadyRegisteredError",
    "IdentityError",
    "IdentityProvider",
    "InvalidCredentialsError",
    "InvalidInvitationError",
    "InvitationEmailMismatchError",
    "InvitationError",
    "LastOwnerError",
    "NoPasswordSetError",
    "NotAMemberError",
    "OAuthLoginAttempt",
    "Organization",
    "OrganizationInvitation",
    "OrganizationMembership",
    "OrganizationRole",
    "Permission",
    "PermissionDeniedError",
    "UnverifiedProviderEmailError",
    "User",
    "UserIdentity",
    "UserSession",
    "WeakPasswordError",
    "has_permission",
    "permissions_for",
    "require_can_change_member",
    "require_can_grant_role",
    "require_owner_remains",
    "require_permission",
]
