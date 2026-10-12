"""The role → permission table and the invariants that protect an organization.

Pure logic, so these are the cheap tests — which is exactly why the rules that
would be expensive to get wrong (privilege escalation, orphaning an
organization) are pinned here rather than only through the API.
"""

from itertools import pairwise

import pytest

from myntos.domain.identity.access import (
    ROLE_PERMISSIONS,
    Permission,
    has_permission,
    require_can_change_member,
    require_can_grant_role,
    require_owner_remains,
    require_permission,
)
from myntos.domain.identity.errors import LastOwnerError, PermissionDeniedError
from myntos.domain.identity.models import OrganizationRole


def test_every_role_has_a_permission_set() -> None:
    assert set(ROLE_PERMISSIONS) == set(OrganizationRole)


def test_roles_are_strictly_nested() -> None:
    """Each role holds everything the one below it holds.

    Not a formality: an accidental gap (an admin who cannot read the ledger a
    member can post to) would only surface as a confusing 403 in production.
    """
    ladder = [
        OrganizationRole.VIEWER,
        OrganizationRole.MEMBER,
        OrganizationRole.ADMIN,
        OrganizationRole.OWNER,
    ]
    for lower, higher in pairwise(ladder):
        assert ROLE_PERMISSIONS[lower] < ROLE_PERMISSIONS[higher]


def test_viewer_can_read_but_not_post() -> None:
    assert has_permission(OrganizationRole.VIEWER, Permission.LEDGER_READ)
    assert not has_permission(OrganizationRole.VIEWER, Permission.LEDGER_WRITE)


def test_posting_does_not_include_changing_the_kontoplan() -> None:
    """A bookkeeper who posts is not thereby the one who regroups the accounts."""
    assert has_permission(OrganizationRole.MEMBER, Permission.LEDGER_WRITE)
    assert not has_permission(OrganizationRole.MEMBER, Permission.LEDGER_CHART)
    assert has_permission(OrganizationRole.ADMIN, Permission.LEDGER_CHART)
    assert has_permission(OrganizationRole.OWNER, Permission.LEDGER_CHART)


def test_member_cannot_manage_members() -> None:
    assert not has_permission(OrganizationRole.MEMBER, Permission.MEMBER_MANAGE)
    with pytest.raises(PermissionDeniedError):
        require_permission(OrganizationRole.MEMBER, Permission.MEMBER_MANAGE)


def test_billing_and_deletion_are_owner_only() -> None:
    for permission in (Permission.BILLING_MANAGE, Permission.ORGANIZATION_DELETE):
        assert has_permission(OrganizationRole.OWNER, permission)
        assert not has_permission(OrganizationRole.ADMIN, permission)


def test_admin_cannot_create_an_owner() -> None:
    """The escalation path that makes owner/admin mean something.

    An admin who could grant the owner role could grant it to themselves and
    take over billing and deletion rights.
    """
    with pytest.raises(PermissionDeniedError):
        require_can_grant_role(OrganizationRole.ADMIN, OrganizationRole.OWNER)
    require_can_grant_role(OrganizationRole.OWNER, OrganizationRole.OWNER)


def test_admin_cannot_touch_an_owner() -> None:
    # The other half of the same rule: an admin must not be able to demote or
    # remove the owners instead of promoting themselves.
    with pytest.raises(PermissionDeniedError):
        require_can_change_member(OrganizationRole.ADMIN, OrganizationRole.OWNER)
    require_can_change_member(OrganizationRole.ADMIN, OrganizationRole.MEMBER)


def test_admin_can_grant_ordinary_roles() -> None:
    for role in (OrganizationRole.ADMIN, OrganizationRole.MEMBER, OrganizationRole.VIEWER):
        require_can_grant_role(OrganizationRole.ADMIN, role)


def test_last_owner_cannot_be_demoted_or_removed() -> None:
    """An organization with no owner cannot be billed, staffed or deleted."""
    with pytest.raises(LastOwnerError):
        require_owner_remains(
            owner_count=1,
            current_role=OrganizationRole.OWNER,
            new_role=OrganizationRole.ADMIN,
        )
    with pytest.raises(LastOwnerError):
        require_owner_remains(owner_count=1, current_role=OrganizationRole.OWNER, new_role=None)


def test_owner_may_step_down_when_another_owner_remains() -> None:
    require_owner_remains(
        owner_count=2, current_role=OrganizationRole.OWNER, new_role=OrganizationRole.MEMBER
    )
    require_owner_remains(owner_count=2, current_role=OrganizationRole.OWNER, new_role=None)


def test_non_owners_are_unaffected_by_the_owner_rule() -> None:
    require_owner_remains(owner_count=1, current_role=OrganizationRole.MEMBER, new_role=None)
