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


class NoLinkedAccountError(IdentityError):
    """A provider without an email address signed in somebody linked to nothing.

    Raised for BankID, where there is no address to find an account by. A
    login button never creates an account, so this is a refusal with two ways
    forward: sign in the usual way and link BankID from /konto/, or sign up
    with BankID and give an address.
    """


class IdentityAlreadyLinkedError(IdentityError):
    """This provider identity belongs to another account, or this account has one.

    One person, one account per provider identity (the unique constraint on
    ``user_identity``), and one identity per provider on an account. Saying
    which is safe: only somebody who just authenticated as that identity can
    get this answer.
    """


class IdentityNotFoundError(IdentityError):
    """The account has no sign-in through that provider to remove."""


class LastSignInMethodError(IdentityError):
    """Removing this sign-in would leave the account with no way in.

    An account with no password and no other provider would be reachable by
    nobody, including its owner, while still holding their companies.
    """


class BankidAccountError(IdentityError):
    """The account signs in with BankID, and so with nothing else.

    BankID's merchant terms forbid ID-veksling: using a BankID login to set up,
    restore or vouch for a sign-in method of our own (docs/integrations/
    bankid.md). So an account BankID signs in to has no password and no other
    provider, and every path that would give it one refuses with this: a
    password change, a reset link, linking Google or Microsoft, and removing
    BankID other than by switching to e-mail from /konto/.

    Saying so is safe. Each caller who gets it has already shown a claim on the
    account: a session, its password, a link from its inbox, or a provider's
    word that its address is theirs.
    """


class EmailBelongsToBankidAccountError(BankidAccountError):
    """A Google or Microsoft login whose verified address is a BankID account's.

    Its own code, because it is answered on the login page rather than on
    /konto/: the person is not signed in, and what they need to hear is which
    button to press instead. Only raised once the provider has vouched for the
    address; an unverified one gets :class:`UnverifiedProviderEmailError`
    first, which says nothing about how the account signs in.
    """


class InvalidProviderSignupError(IdentityError):
    """The held BankID signup is unknown, used or expired.

    One error for every reason, for the reason :class:`InvalidInvitationError`
    gives.
    """


class InvitationError(IdentityError):
    """Base class for invitation failures."""


class InvalidInvitationError(InvitationError):
    """The invitation token is unknown, already used, or expired.

    One error for all three, deliberately: distinguishing them would tell a
    stranger holding a guessed token that they had guessed a real one. This is
    about a *token*, presented by whoever holds it.
    """


class InvitationNotFoundError(InvitationError):
    """This company has no invitation with that id.

    A different question from the one above, asked by somebody who already has
    access to the books: they named an id, and it is not one of theirs. Answered
    404, like every other sub-resource that belongs to another company, so
    managing invitations cannot be used to find out that an id exists somewhere
    else. See ``tests/test_sibling_company_boundary.py``.
    """


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


class SessionNotFoundError(IdentityError):
    """The caller has no session with that id.

    Another person's session gets this answer too, so the session list cannot
    be used to find out which ids are live somewhere else.
    """


class ReauthenticationRequiredError(IdentityError):
    """Deleting the account needs proof that the owner is at the keyboard.

    A session alone is not enough: it is exactly what a borrowed laptop or a
    stolen cookie gives somebody, and erasure is the one action nothing can
    undo. Raised when no password was given to an account that has one, and
    when an account without one has not signed in recently.
    """


class ReauthenticationFailedError(IdentityError):
    """The password given to confirm a deletion was wrong.

    Not :class:`InvalidCredentialsError`: that one is a 401, and a 401 tells
    the frontend the person is signed out, which they are not.
    """


class ErasureBlockedError(IdentityError):
    """Deleting the account would leave a set of books without an owner.

    The message names each company and what to do about it. Books nobody owns
    can be neither read, billed nor shared, and books with entries in them have
    to stay readable for five years whatever happens to the person who kept
    them (bokføringsloven § 13).
    """


class EmailTokenError(IdentityError):
    """Base class for links mailed to an account's own address."""


class InvalidEmailTokenError(EmailTokenError):
    """A confirmation or reset link that cannot be used.

    Unknown, already spent, superseded by a newer link, expired, or sent to an
    address the account no longer has: one error for all of them, for the
    reason :class:`InvalidInvitationError` gives. Somebody holding a guessed
    token must not learn that they guessed a real one.
    """


class EmailAlreadyVerifiedError(EmailTokenError):
    """A confirmation link was asked for on an address that is already confirmed."""


class AccountMailRateLimitedError(EmailTokenError):
    """Too many links of one kind for one account in the last hour.

    Only ever raised to somebody signed in as that account. The anonymous
    password-reset request swallows the limit instead, because answering it
    differently would tell a stranger that the address has an account.
    """


class EmailDeliveryUnavailableError(EmailTokenError):
    """This deployment sends no mail, so there is no link to send.

    A fact about the deployment rather than about any account, so saying it
    plainly reveals nothing an attacker could use.
    """


# --- Keys for agents and integrations (docs/agents-and-api.md) ------------------
#
# Every one of these reaches an agent, not a person at a form, and the agent's
# next move depends on telling them apart: refresh and retry, ask the person for
# a new connection key, or tell the person this is something only they can do.
# So each has its own class and therefore its own ``code``, and the detail says
# what to do next.


class AgentKeyError(IdentityError):
    """Base class for keys made for agents and integrations."""


class AgentAccessDisabledError(AgentKeyError):
    """The company has not turned keys on, or has turned them off again."""


class AgentKeyNotFoundError(AgentKeyError):
    """No such key in this company, or none the caller may see."""


class InvalidAgentScopeError(AgentKeyError):
    """A scope that does not exist, one the person's role does not carry, or none."""


class InvalidAgentTokenError(AgentKeyError):
    """An access token that is unknown, expired, or belongs to a stopped key.

    The ordinary answer to an agent whose access token has run out, and the
    one it should handle without bothering anybody: refresh, then retry.
    """


class NotAnAccessTokenError(AgentKeyError):
    """A connection key or a refresh token sent where an access token belongs.

    The commonest mistake an agent makes on its first request, so it is named
    rather than folded into an unknown token: the fix is one call away.
    """


class InvalidGrantError(AgentKeyError):
    """A connection key or refresh token that cannot be exchanged.

    Named after OAuth's ``invalid_grant`` because that is the word an agent that
    knows OAuth looks for. The subclasses below say *why*, which decides whether
    the agent can recover on its own.
    """


class ConnectionKeySpentError(InvalidGrantError):
    """The connection key has already been exchanged once.

    Usually the agent itself, in a new conversation, finding the old key in
    what the person pasted. The tokens from the first exchange still work if
    the agent kept them; otherwise the person makes a new key.
    """


class ConnectionKeyExpiredError(InvalidGrantError):
    """The connection key was never used within its lifetime."""


class RefreshTokenReusedError(InvalidGrantError):
    """A refresh token that was already exchanged came back.

    Two parties hold the chain and nothing can tell which is the agent, so the
    key has been stopped and its person told. Only a new connection key helps.
    """


class AgentKeyStoppedError(InvalidGrantError):
    """The key behind this token is revoked, expired or no longer allowed."""


class NotForAgentKeysError(AgentKeyError):
    """This route cannot be called with a key, whatever its scopes.

    Filing to Skatteetaten, moving money, changing a kontonummer, managing
    access, deleting the account: the person does these in Myntos, signed in.
    """


class MissingScopeError(AgentKeyError):
    """The key was not given the scope this route needs."""


class AgentKeyRequiredError(AgentKeyError):
    """A route about the key itself, called without one."""


class AgentRateLimitedError(AgentKeyError):
    """Too many requests with one key in the last minute."""


# --- Requests that wait for a person's approval -----------------------------------


class ApprovalError(AgentKeyError):
    """Base class for requests a key may only ask for."""


class ApprovalNotFoundError(ApprovalError):
    """No such request in this company, or none the caller may see."""


class ApprovalNotPendingError(ApprovalError):
    """The request has been decided, withdrawn, has expired, or its key has stopped."""


class NotYourAgentError(ApprovalError):
    """Only the person whose key asked may approve what it asked.

    The key works with that person's permissions and registers in their name,
    so the yes has to be theirs: a colleague approving it would put Kari's
    name on an entry Ola decided.
    """


class ApprovalTooLargeError(ApprovalError):
    """The waiting request would carry more than a stored request may."""


class AttachmentsNotAcceptedError(ApprovalError):
    """A file added to a waiting request whose route has nowhere to put it."""


class FilingIsDoneOnThePageError(ApprovalError):
    """A request to file is approved by filing, on the page that files."""


class AlreadyFiledError(ApprovalError):
    """The period a key asks its person to file has been filed already."""
