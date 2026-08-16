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
    """A Myntos account: the unit of membership and of billing.

    ``plan`` is the seam for paid tiers. It is a plain string rather than an
    enum so a price change does not need a migration, and it sits here rather
    than on :class:`User` because that is the whole point — a plan bought by
    one person covers every colleague they invite and every company they keep
    books for.
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
    # actually typed is never overwritten. See
    # :func:`myntos.domain.identity.service.adopt_name_if_placeholder`.
    name_is_placeholder: Mapped[bool] = mapped_column(Boolean, default=False)
    plan: Mapped[str] = mapped_column(String(32), default="free")
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
    memberships: Mapped[list[OrganizationMembership]] = relationship(
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


class OrganizationInvitation(Base):
    """A pending invitation for someone to join an organization.

    This is how a second, third and fourth person end up in the same
    organization. The invitation is addressed to an email address and carries
    the role the invitee will get; only the token's hash is stored, so a
    database leak does not hand out memberships.
    """

    __tablename__ = "organization_invitation"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_organization_invitation_token_hash"),
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String(EMAIL_LENGTH))
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
