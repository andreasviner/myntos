"""Errors raised by the identity domain.

They live in their own module so both :mod:`myntos.domain.identity.access` and
:mod:`myntos.domain.identity.service` can raise from one hierarchy, and so the
API layer can map that hierarchy to status codes in a single place
(:mod:`myntos.api.errors`). No HTTP status codes here: the domain does not know
it is being served over HTTP.
"""


class IdentityError(Exception):
    """Base class for every account/organization failure."""


class EmailAlreadyRegisteredError(IdentityError):
    """Someone already signed up with that email address."""


class WeakPasswordError(IdentityError):
    """The chosen password does not satisfy the password policy."""


class InvalidCredentialsError(IdentityError):
    """Unknown email, wrong password, or an account with no password set.

    Deliberately one error for all three: distinguishing them tells an
    attacker which addresses have accounts.
    """


class AccountLockedError(IdentityError):
    """Too many failed logins; the account is temporarily locked."""


class AccountDisabledError(IdentityError):
    """The account exists but has been deactivated."""


class NoPasswordSetError(IdentityError):
    """A password change was attempted on a provider-only account."""


class UnverifiedProviderEmailError(IdentityError):
    """A federated login tried to reach an existing account on an unverified email.

    Refusing here is what stops an identity provider that lets users type any
    address from becoming an account-takeover path — see
    :func:`myntos.domain.identity.service.sign_in_with_provider`. It is only
    raised when the address already belongs to someone; a brand-new account has
    nobody to take over.
    """


class InvitationError(IdentityError):
    """Base class for invitation failures."""


class InvalidInvitationError(InvitationError):
    """The invitation token is unknown, already used, or expired."""


class InvitationEmailMismatchError(InvitationError):
    """The signed-in account is not the account the invitation was sent to."""


class AccessError(IdentityError):
    """Base class for authorization failures inside an organization."""


class NotAMemberError(AccessError):
    """The user has no membership in the organization."""


class PermissionDeniedError(AccessError):
    """The user's role does not carry the required permission."""


class LastOwnerError(AccessError):
    """The change would leave the organization without an owner."""
