"""Application accounts, organizations and login sessions.

This is the *application* identity plane — who is logged into Myntos — and it
is deliberately separate from :mod:`myntos.auth.idporten` and
:mod:`myntos.auth.maskinporten`, which answer a different question: who we are
to Skatteetaten. A user signs into Myntos with an email/password or a Google
account; when that same user later submits an MVA-melding they authenticate
*again* with ID-porten, because Skatteetaten requires a personal ID-porten
login for submissions and will never accept a Myntos session as evidence of
anything.

Five decisions shape the tables below, and they are the ones that would be
expensive to reverse:

1. **The organization is the tenant, not the company.** :class:`Organization`
   is the Myntos account: it owns the members, and it is what a subscription
   will attach to. :class:`~myntos.domain.ledger.models.Company` is a
   bookkeeping-obligated entity with an orgnr. A regnskapsfører is one
   organization holding many companies; a single AS is one organization holding
   one. Because entitlements resolve on the organization, inviting a colleague
   never multiplies the bill and every company in the organization is covered
   by the same plan.

2. **Password and federated credentials coexist on one user.** The password
   hash lives on :class:`User`; each federated login is a row in
   :class:`UserIdentity`. Someone who signed up with a password can add Google
   later — or drop it again — and keep the same user id, the same memberships
   and the same audit trail. Modelling "login with Google" as a second user
   account would fork the memberships instead.

3. **A federated identity is only linked to an existing account when the
   provider asserts a verified email.** Matching on the email address alone is
   an account-takeover primitive; see
   :func:`~myntos.domain.identity.service.sign_in_with_provider`.

4. **Sessions are server-side and revocable.** A stateless JWT cannot be
   withdrawn before it expires, which is the wrong default for a system holding
   accounting records: "log me out everywhere" and "lock the account we just
   found compromised" both have to take effect immediately. The cookie carries
   an opaque random token; the database stores only its hash.

5. **Role and provider columns are VARCHAR, not PostgreSQL enums.** Both lists
   are expected to grow (another provider, another role), and extending a
   native enum means an ``ALTER TYPE`` in a migration. The ledger's
   ``account_type``/``party_type`` stay native because those sets are closed by
   accounting rules — these are not.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from myntos.core.clock import utc_now
from myntos.db.base import Base

# 254 is the longest address RFC 5321 can deliver to; 320 is the conventional
# column width (64-char local part + "@" + 255-char domain) and costs nothing
# in PostgreSQL.
EMAIL_LENGTH = 320

# SHA-256 hex digest — the stored form of every bearer token in this module.
TOKEN_HASH_LENGTH = 64


class OrganizationRole(enum.StrEnum):
    """What a member may do inside one organization.

    Ordered from most to least privileged. The permissions each role carries
    are defined once, in :mod:`myntos.domain.identity.access` — never inferred
    from this ordering at a call site.
    """

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class IdentityProvider(enum.StrEnum):
    """An external login provider.

    "Gmail login" is Google's OpenID Connect: the same provider serves free
    ``@gmail.com`` accounts and Workspace domains, so there is nothing
    gmail-specific to model. The same holds for Microsoft, where one endpoint
    covers personal accounts and Entra ID tenants alike.

    Adding a member here needs no migration, which is exactly why the column is
    a plain VARCHAR rather than a PostgreSQL enum type — see the module
    docstring.
    """

    GOOGLE = "google"
    MICROSOFT = "microsoft"
    VIPPS = "vipps"


def _role_type() -> Enum:
    # values_callable stores "owner" rather than SQLAlchemy's default of the
    # member *name* ("OWNER"), matching the ledger models. A fresh instance per
    # column keeps two tables from sharing one SchemaType object.
    return Enum(
        OrganizationRole,
        name="organization_role",
        native_enum=False,
        length=32,
        values_callable=lambda e: [member.value for member in e],
    )


def _provider_type() -> Enum:
    return Enum(
        IdentityProvider,
        name="identity_provider",
        native_enum=False,
        length=32,
        values_callable=lambda e: [member.value for member in e],
    )


class Organization(Base):
    """A Myntos account: whose companies these are, and nothing else.

    It used to be the tenant, in the full sense: membership, billing and access
    all hung off it, and a colleague invited to the account could open every set
    of books under it. That collapsed two different questions into one row, and
    it showed. A regnskapsfører cannot say "help me with this client only", and
    an account named after its first company (which Myntos used to do) printed
    beside a *different* company's figures is indistinguishable from a leak.

    So this row now answers exactly two questions: which companies were set up
    together, and who may add another one. What a person may open is
    :class:`CompanyMembership`; what it costs is ``plan`` on the company.

    Nothing displays its ``name``. It is kept because it is what signup made and
    what invitations are cascaded by, and because a nameless row is harder to
    talk about in a support conversation than a named one.
    """

    __tablename__ = "organization"
    __table_args__ = (UniqueConstraint("slug", name="uq_organization_slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # URL-safe handle, derived from the name on creation. Stable even when the
    # display name changes, so it can appear in links.
    slug: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    # True while ``name`` is one Myntos invented at signup ("Ola Nordmann
    # (privat)") rather than one a person chose. It cannot be recomputed later —
    # the user may since have changed their own name — so it is stored. Anything
    # that learns a better name may take a placeholder over; a name someone
    # actually typed is never overwritten. Nothing takes one over any more: see
    # the note in myntos.domain.identity.service where
    # ``adopt_name_if_placeholder`` used to be.
    name_is_placeholder: Mapped[bool] = mapped_column(Boolean, default=False)
    # No ``plan`` here. What a subscription is for is a set of books, so it sits
    # on :class:`~myntos.domain.ledger.models.Company`; see migration
    # ``d1a7f2c6b483``. A second copy on the account would be a second answer to
    # "what does this cost", and the day there is billing, one of them would be
    # the one nobody charged from.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    memberships: Mapped[list[OrganizationMembership]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    invitations: Mapped[list[OrganizationInvitation]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class User(Base):
    """A person who can sign in.

    The table is ``app_user`` because ``user`` is a reserved word in
    PostgreSQL: usable when quoted, but it turns every hand-written query and
    every psql session into a quoting exercise.

    Timestamps default in Python rather than with ``server_default=now()``
    (which the ledger uses) for two reasons: the API echoes these values back
    inside the same request, and ``expires_at`` on the token tables is derived
    from the same clock as ``created_at``, so a CHECK comparing the two cannot
    trip on skew between the application and the database.
    """

    __tablename__ = "app_user"
    __table_args__ = (
        UniqueConstraint("email", name="uq_app_user_email"),
        # Uniqueness of an email address is meaningless if two rows can differ
        # only by case. Normalisation happens in
        # myntos.domain.identity.service.normalise_email; this is the backstop
        # that makes the unique constraint mean what it says.
        CheckConstraint("email = lower(email)", name="email_is_lowercase"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(EMAIL_LENGTH))
    # NULL until proven. Set immediately for provider logins (the provider
    # already verified it) and only after a confirmation round-trip for
    # password signups.
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    full_name: Mapped[str] = mapped_column(String(255))
    # NULL for accounts that only ever sign in through a provider. A NULL hash
    # must never authenticate — see myntos.auth.passwords.verify_password.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Per-account throttling of password guessing. This bounds an attack on one
    # known address; spraying many addresses from one source is an edge concern
    # and belongs in front of the application (Cloudflare rate limiting), not
    # in a table we would have to write on every request.
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    identities: Mapped[list[UserIdentity]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    # At most one, and deleted with the account: a person's fødselsnummer is
    # kept because they linked it to *this* account, and that reason ends when
    # the account does.
    government_identity: Mapped[UserGovernmentIdentity | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    memberships: Mapped[list[OrganizationMembership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    # What the person may actually open. ``memberships`` above says whose
    # account they are on; this says which books they can read and write.
    company_memberships: Mapped[list[CompanyMembership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def has_password(self) -> bool:
        return self.password_hash is not None

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None


class UserIdentity(Base):
    """A federated login linked to a :class:`User`."""

    __tablename__ = "user_identity"
    __table_args__ = (
        # One provider account signs in as exactly one Myntos user.
        UniqueConstraint("provider", "subject", name="uq_user_identity_provider"),
        # And one Myntos user links at most one account per provider.
        UniqueConstraint("user_id", "provider", name="uq_user_identity_user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    provider: Mapped[IdentityProvider] = mapped_column(_provider_type())
    # The provider's stable subject identifier (Google's ``sub``) — never the
    # email address, which a Workspace administrator can reassign to a
    # different person while ``sub`` stays put.
    subject: Mapped[str] = mapped_column(String(255))
    # Only for display and support ("you signed up with this address"). Never
    # used to find the account; that is what ``subject`` is for.
    email_at_provider: Mapped[str | None] = mapped_column(String(EMAIL_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="identities")


class UserGovernmentIdentity(Base):
    """Who a Myntos account belongs to, as ID-porten has asserted it.

    :class:`UserIdentity` answers "which Google account signs in here".  This
    answers a different and heavier question: which *person*, in the state's
    sense, has proved they are behind this account. It is written by the
    ID-porten callback in :mod:`myntos.api.v1.skatt` and by nothing else.

    Four decisions.

    **Separate from :class:`GovernmentLoginAttempt`, which already holds a pid.**
    That row is an authorization: minutes old, single-use, and worthless the
    moment its token expires. This one is a fact about the account that should
    outlive every login. That is the difference between "somebody is filing
    right now" and "this account is Kari". Folding them together would mean the link
    vanishing an hour after it was made, which is exactly when it starts being
    useful.

    **The fødselsnummer is stored, not hashed.** It is what Altinn and
    Skatteetaten identify a submitter by, so a hash would have to be un-hashed
    to be used, which is not a thing. It is never served over the API to anyone
    but its owner, and never at all in a member listing: colleagues see the
    *name* and the fact of a link, which is all the interface needs.

    **``pid`` is not globally unique.** One person legitimately holds two
    accounts (a personal one and one at a client's), and a unique constraint
    would turn the second person's login into a database error at the worst
    possible moment. What is unique is ``user_id``: an account has one person
    behind it.

    **The link is replaced, not appended to.** If a different person completes
    ID-porten on the same account, the row now names them, because the only
    claim it makes is who most recently proved themselves here. ``linked_at``
    keeps the first time it happened; ``verified_at`` moves each login.
    """

    __tablename__ = "user_government_identity"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_government_identity_user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    # The fødselsnummer from ID-porten's ``pid`` claim.
    pid: Mapped[str] = mapped_column(String(11))
    # The person's name as ID-porten gives it, which is Folkeregisteret's. NULL
    # when the deployment does not request the ``profile`` scope, and that has
    # to stay a supported state: the scope is granted per client, so a name is
    # something a deployment may simply not have. Everything that reads this
    # falls back to the name on the account.
    full_name: Mapped[str | None] = mapped_column(String(255))
    # The assurance level the linking login reached. Recorded for the same
    # reason the attempt records it: it is the evidence of how well this is
    # known, not a detail of how it was obtained.
    acr: Mapped[str | None] = mapped_column(String(64))
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(back_populates="government_identity")


class OrganizationMembership(Base):
    """A user's role in one organization.

    The row is the authorization record: every organization-scoped endpoint
    resolves it before doing anything, so a missing row is an access denial
    rather than an empty result.
    """

    __tablename__ = "organization_membership"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "user_id", name="uq_organization_membership_organization_id"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    role: Mapped[OrganizationRole] = mapped_column(_role_type())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    organization: Mapped[Organization] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class CompanyMembership(Base):
    """A person's access to one set of books, and what they may do with it.

    **This is the row that decides who may read and write a company.** Not
    :class:`OrganizationMembership`, which now says only whose account a
    company was set up under.

    Access used to be account-wide: one membership let you into every company
    the account held. That is right for a sole trader with one company and
    wrong for everybody else. A regnskapsfører keeps one company per client, and
    "invite my colleague to help with this client" was not expressible: the
    invitation let them into every client's books at once. Worse, it read as
    though it had not: the invitation form sat under the account, so what it
    actually granted was invisible.

    So access is per company, and the account is the thing that owns companies
    rather than the thing that grants them. One row per person per company,
    carrying its own role, so somebody can be an owner of their own books and a
    viewer of a client's.

    The role type is :class:`OrganizationRole` because the roles and the
    permissions they carry are the same either way; only what they are held
    *over* has changed. Renaming it would touch every call site in the
    application to say nothing new.
    """

    __tablename__ = "company_membership"
    __table_args__ = (
        UniqueConstraint("company_id", "user_id", name="uq_company_membership_company_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    role: Mapped[OrganizationRole] = mapped_column(_role_type())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    # No relationship to Company on purpose: it lives in the ledger models, and
    # every query that needs both joins them explicitly. A relationship here
    # would make the identity models import the ledger's, in the direction that
    # does not already exist.
    user: Mapped[User] = relationship(back_populates="company_memberships")


class OrganizationInvitation(Base):
    """A pending invitation for someone to join one company's books.

    This is how a second, third and fourth person end up with access to a set
    of books. The invitation is addressed to an email address and carries the
    company it is for and the role the invitee will get; only the token's hash
    is stored, so a database leak does not hand out memberships.

    The table is still called ``organization_invitation``, and ``company_id`` is
    nullable, because both are history rather than design: invitations were
    account-wide before access was per company. A row with no company is one
    written under those rules, and it is refused on acceptance rather than
    guessed at, because there is no way to know which of an account's companies
    the sender meant.
    """

    __tablename__ = "organization_invitation"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_organization_invitation_token_hash"),
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"))
    # The books being shared. Nullable only for rows that predate per-company
    # access; see the class docstring.
    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("company.id", ondelete="CASCADE"), nullable=True
    )
    email: Mapped[str] = mapped_column(String(EMAIL_LENGTH))
    # Who the invitation is addressed to, when the inviter said. Optional
    # because an address is enough to invite somebody, and it is a claim rather
    # than a fact, because the inviter typed it or picked it out of the
    # company's board list in Enhetsregisteret. It buys three things: the email can greet
    # a person by name, the register's role table can show that this board
    # member has been asked, and the person accepting sees the name they were
    # invited under rather than an empty signup form.
    full_name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[OrganizationRole] = mapped_column(_role_type())
    token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH))
    # SET NULL rather than CASCADE: deleting the admin who sent an invitation
    # must not silently delete the audit record of it having been sent.
    invited_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )

    organization: Mapped[Organization] = relationship(back_populates="invitations")
    # Read-only: the row that owns this relationship is ``invited_by_user_id``,
    # which is SET NULL rather than CASCADE, so the inviter may be gone. Loaded
    # so the pending-invitation list can say who sent each one without a query
    # per row.
    invited_by: Mapped[User | None] = relationship(foreign_keys=[invited_by_user_id])


class UserSession(Base):
    """One signed-in browser (or API client).

    Two independent deadlines, because they answer different questions:
    ``expires_at`` caps how long a session may live at all, and
    ``last_seen_at`` plus the configured idle timeout caps how long an
    unattended one stays usable. A bookkeeping session can post entries and
    read salary figures, so neither cap alone is enough.
    """

    __tablename__ = "user_session"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_user_session_token_hash"),
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    # SHA-256 of the token the client holds. The token itself is never stored,
    # so the database cannot be replayed as a set of live credentials.
    token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Set on logout and on "sign out everywhere". Rows are kept rather than
    # deleted: "when was I logged out, and from where" is a question users and
    # auditors ask after an incident.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Session audit trail. Both are personal data under GDPR, kept only for
    # the security purpose of letting a user recognise their own sessions;
    # they are deleted with the account.
    user_agent: Mapped[str | None] = mapped_column(String(255))
    ip_address: Mapped[str | None] = mapped_column(String(45))  # fits IPv6

    user: Mapped[User] = relationship(back_populates="sessions")

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None


class OAuthLoginAttempt(Base):
    """An in-flight OpenID Connect authorization-code flow.

    The ``state``, ``nonce`` and PKCE verifier have to survive the redirect to
    the provider and come back matched, and the backend runs as stateless
    containers that sleep between requests — so the flow's short-lived state
    lives in PostgreSQL like everything else, not in process memory. Rows are
    single-use: ``consumed_at`` is stamped before the code is exchanged, which
    is what makes a replayed callback URL useless.

    Nothing prunes this table yet. Rows are tiny and expire in minutes, so it is
    a housekeeping job rather than a correctness one — an expired row can never
    authenticate anyone — but it does need writing before the table is years
    old. The same applies to revoked :class:`UserSession` rows, which are kept
    deliberately for their audit value.
    """

    __tablename__ = "oauth_login_attempt"
    __table_args__ = (
        UniqueConstraint("state", name="uq_oauth_login_attempt_state"),
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[IdentityProvider] = mapped_column(_provider_type())
    state: Mapped[str] = mapped_column(String(64))
    nonce: Mapped[str] = mapped_column(String(64))
    # RFC 7636 allows 43 to 128 characters.
    code_verifier: Mapped[str] = mapped_column(String(128))
    # Where to send the browser afterwards. Validated as a site-relative path
    # before it is stored, so a crafted /auth/{provider}/start link cannot turn the
    # callback into an open redirect.
    redirect_to: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GovernmentLoginAttempt(Base):
    """An in-flight ID-porten login, and the token it produces.

    Deliberately **not** :class:`OAuthLoginAttempt`, though the OIDC mechanics
    are the same. That table signs somebody *into Myntos*; this one records a
    person who is already signed in proving to *Skatteetaten* who they are. One
    table for both would mean a government callback could complete a sign-in, or
    a sign-in callback could authorize a filing - and the distinction is the
    whole point: a Myntos session is never evidence of anything to Skatteetaten.

    The row carries the flow's state on the way out and the sealed token on the
    way back, because they share one short lifetime and one owner. ``user_id``
    is stamped at the start: the person who began the login is the only one who
    may finish it, so a leaked callback URL cannot attach a government identity
    to somebody else's account.

    ``sealed_token`` is Fernet ciphertext - see :mod:`myntos.auth.idporten.vault`
    for why it is encrypted rather than stored plainly, and why a missing key
    disables the feature instead of downgrading it.

    Nothing prunes this table yet, the same housekeeping debt
    :class:`OAuthLoginAttempt` records. Here it matters more: an expired row is
    harmless, but rows should not accumulate government identifiers for years,
    so pruning is a privacy job as much as a tidiness one.
    """

    __tablename__ = "government_login_attempt"
    __table_args__ = (
        UniqueConstraint("state", name="uq_government_login_attempt_state"),
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    state: Mapped[str] = mapped_column(String(64))
    nonce: Mapped[str] = mapped_column(String(64))
    # RFC 7636 allows 43 to 128 characters.
    code_verifier: Mapped[str] = mapped_column(String(128))
    # Site-relative, validated before storing, for the same open-redirect
    # reason as the sign-in flow.
    redirect_to: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    # When the *attempt* stops being completable. Minutes.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Filled in by the callback, once the person has authenticated --------

    sealed_token: Mapped[bytes | None] = mapped_column(LargeBinary)
    # ID-porten's own expiry for the access token. Never extended locally: a
    # token kept past it cannot do anything, so keeping it is pure liability.
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The person's fodselsnummer, from the pid claim. Stored because Skatteetaten
    # and Altinn identify the submitter by it and the UI must be able to show
    # *who* is about to file - not as an identifier for anything else.
    pid: Mapped[str | None] = mapped_column(String(11))
    # Level of assurance actually reached. Recorded rather than assumed: it is
    # what evidences that the submission met Skatteetaten's requirement.
    acr: Mapped[str | None] = mapped_column(String(64))
    # Stamped when the token has been used for a submission, so a second one
    # cannot ride on the same authorization.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
