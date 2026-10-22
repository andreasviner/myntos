"""Application accounts, organizations and login sessions.

This is the *application* identity plane — who is logged into Myntos — and it
is deliberately separate from :mod:`myntos.auth.idporten` and
:mod:`myntos.auth.maskinporten`, which answer a different question: who we are
to Skatteetaten. A user signs into Myntos with an email/password, a Google or
Microsoft account, or BankID through a broker; when that same user later
submits an MVA-melding they authenticate *again* with ID-porten, because
Skatteetaten requires a personal ID-porten login for submissions and will never
accept a Myntos session as evidence of anything. Signing in with BankID does
not change that: it is a login to Myntos, bought from a broker, and says
nothing to the state.

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
   account would fork the memberships instead. **BankID is the exception**: an
   account with a ``bankid`` row has no password and no other provider, because
   BankID's terms forbid using it to set up or restore a sign-in of our own.
   The row is the whole marker; there is no flag to drift from it. See
   :func:`~myntos.domain.identity.service.is_bankid_account`.

3. **A federated identity is only linked to an existing account when the
   provider asserts a verified email**, or when the person linking it is
   already signed in to that account. Matching on the email address alone is
   an account-takeover primitive; see
   :func:`~myntos.domain.identity.service.sign_in_with_provider`. BankID asserts
   no address at all, so for it the second way is the only one; see
   :func:`~myntos.domain.identity.service.link_provider`.

4. **Sessions are server-side and revocable.** A stateless JWT cannot be
   withdrawn before it expires, which is the wrong default for a system holding
   accounting records: "log me out everywhere" and "lock the account we just
   found compromised" both have to take effect immediately. The cookie carries
   an opaque random token; the database stores only its hash.

5. **Role, provider and intent columns are VARCHAR, not PostgreSQL enums.**
   The lists are expected to change (another provider, another role, and one
   provider has already gone), and changing a native enum means an ``ALTER
   TYPE`` in a migration. The ledger's
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
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
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

    ``bankid`` is Norwegian BankID through an OpenID Connect broker (Idura by
    default; docs/ARCHITECTURE.md, "Federated providers"). Unlike the other two
    it says who a person *is* and nothing about how to reach them: no email
    address comes back, so none of the email-based linking rules can apply to
    it, and the broker's ``sub`` is the only thing stored.

    Adding a member here needs no migration, which is exactly why the column is
    a plain VARCHAR rather than a PostgreSQL enum type — see the module
    docstring. Removing one does need care: a row still holding the old value
    can no longer be read, which is why ``7a3e5c1d9b24`` refuses to run while a
    ``vipps`` row exists rather than deleting it.
    """

    GOOGLE = "google"
    MICROSOFT = "microsoft"
    BANKID = "bankid"


class OAuthIntent(enum.StrEnum):
    """What a federated flow was started *for*, decided before the provider is asked.

    The provider's answer is the same identity either way; what to do with it
    depends on which button the person pressed, and that cannot be inferred
    afterwards without guessing.

    - ``login``: sign in to the account this identity is linked to. For a
      provider that asserts an email address, this also creates or links an
      account by that address, as it always has. For one that asserts none
      (BankID), an identity linked to nothing is refused: a login button never
      creates an account.
    - ``signup``: the same, except that an address-less identity linked to
      nothing is held for a few minutes while the person types an email address
      (:mod:`myntos.domain.identity.provider_signup`).
    - ``link``: attach this identity to the account that started the flow,
      which must still be the one signed in when it comes back. For BankID
      this is a switch, not an addition: the account's password and other
      providers go, and the flow starts from ``POST /auth/bankid/link``, which
      asks for the password first, never from a GET.
    """

    LOGIN = "login"
    SIGNUP = "signup"
    LINK = "link"


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


def _intent_type() -> Enum:
    return Enum(
        OAuthIntent,
        name="oauth_intent",
        native_enum=False,
        length=16,
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

    # Set when the person deleted their account and the row stayed behind as a
    # tombstone: the id and the name, nothing to sign in with, nothing to reach
    # them by. It stays only when a set of books still points at it (a voucher
    # they entered, a bilag they uploaded, a filing they made), because the
    # books must go on saying who registered each entry for as long as they are
    # kept, and NULL there would say "nobody knows". An account that touched no
    # books is deleted outright. See myntos.domain.identity.erasure and
    # docs/gdpr.md.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
    """A federated login linked to a :class:`User`.

    For BankID this row is everything Myntos keeps about the login: which
    account, and the broker's ``sub``. Not the fødselsnummer (never requested),
    and not the birth date or the name the broker also sends (the name is read
    once, at signup, to name a new account, and otherwise dropped). See
    docs/gdpr.md.
    """

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
    # different person while ``sub`` stays put. For BankID it is the broker's
    # pseudonym, which Idura documents as stable *per tenant*: moving to another
    # tenant (or another broker) gives every person a new one, and every link
    # has to be made again.
    subject: Mapped[str] = mapped_column(String(255))
    # Only for display and support ("you signed up with this address"). Never
    # used to find the account; that is what ``subject`` is for. Always NULL for
    # BankID, which has no address to give.
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


class EmailTokenPurpose(enum.StrEnum):
    """What a link mailed to an account's own address is for.

    One table holds every kind, and the purpose is part of every lookup, so a
    confirmation link can never be replayed as a password reset. Like the role
    and provider columns, a VARCHAR with no CHECK (``native_enum=False`` makes
    none), which is why the two added for BankID needed no migration.

    - ``switch_to_password`` is the link a BankID account asks for to move to
      e-mail and password. Not ``reset_password``, though it also sets one:
      a reset is asked for anonymously and must never reach a BankID account,
      and this one is asked for by a signed-in BankID account and also removes
      the BankID. One purpose for both would make "refuse a reset for a BankID
      account" and "accept this link for a BankID account" the same lookup.
    - ``bankid_notice`` is not a link at all. It records that a reset request
      for a BankID account was answered with "you sign in with BankID", so the
      hourly cap counts those messages like any other. The token is minted
      only because the column requires one; it is never sent, and nothing
      redeems this purpose.
    """

    VERIFY_EMAIL = "verify_email"
    RESET_PASSWORD = "reset_password"  # noqa: S105 - a purpose name, not a password
    SWITCH_TO_PASSWORD = "switch_to_password"  # noqa: S105 - a purpose name, not a password
    BANKID_NOTICE = "bankid_notice"


def _purpose_type() -> Enum:
    return Enum(
        EmailTokenPurpose,
        name="email_token_purpose",
        native_enum=False,
        length=32,
        values_callable=lambda e: [member.value for member in e],
    )


class UserEmailToken(Base):
    """A single-use link mailed to a person's own address.

    Shaped like :class:`OrganizationInvitation` on purpose: 256 random bits,
    handed out once inside a message, stored here only as a SHA-256 digest, with
    a deadline and a stamp when it is spent. A database dump therefore holds no
    link anybody could follow, which matters more here than for an invitation,
    because a reset link is a password.

    Three decisions.

    **``email`` is stored, though ``user_id`` already points at an address.** A
    link proves that whoever holds it could read the inbox it was sent to, not
    the inbox the account names today. If the two ever differ, the link proves
    nothing about the current one and is refused rather than read generously.

    **Spent and superseded are the same stamp.** Asking for a new link kills the
    earlier ones of the same purpose by stamping ``consumed_at``, so the newest
    message in an inbox is the only one that works. A person holding two reset
    mails should never have to work out which.

    **Rows are kept after use**, and that is what the per-account mail cap counts
    (``account_mail_max_per_hour``): the number of links of one kind made for one
    account in the last hour. Nothing prunes them yet, the same housekeeping debt
    :class:`OAuthLoginAttempt` records; an expired row can never be redeemed.
    """

    __tablename__ = "user_email_token"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_user_email_token_token_hash"),
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        # The rate cap's query: one account, one purpose, newest first.
        Index("ix_user_email_token_user_purpose", "user_id", "purpose", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    purpose: Mapped[EmailTokenPurpose] = mapped_column(_purpose_type())
    # The address the link was mailed to; see the class docstring.
    email: Mapped[str] = mapped_column(String(EMAIL_LENGTH))
    token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Redeemed, or made worthless by a newer link of the same purpose.
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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


class AgentKeyScope(enum.StrEnum):
    """What a key may do, on top of what its person may do.

    Three, because the line that matters to a person is not read against write
    but undoable against final:

    - ``read``: everything a viewer sees. Every key has it.
    - ``write``: what can still be discarded or changed back. A draft invoice,
      a party, a proposal read off a bilag, a bank line matched to a voucher.
    - ``post``: what cannot. Posting or correcting a voucher, issuing an
      invoice or a kreditnota, closing a year, adding to the bilag archive.
      Never given by default (docs/agents-and-api.md rule 6): a posted voucher
      is history, so the person's yes has to come before, not after.

    Which routes need which scope is declared on each route
    (:data:`myntos.api.deps.KEY_READ` and its siblings), and a route that
    declares none cannot be called with a key at all.
    """

    READ = "read"
    WRITE = "write"
    POST = "post"


class AgentKeyStop(enum.StrEnum):
    """Why a key stopped working, as the Tilgang page will say it.

    A VARCHAR like the role and provider columns, so a new reason needs no
    migration. Each is recorded because the page has to tell the person what
    happened and whether it was them, and "revoked" alone answers neither.
    """

    PERSON = "person"
    AGENT = "agent"
    REUSE = "reuse"
    REFUSALS = "refusals"
    ACCESS_OFF = "access_off"
    MEMBER_REMOVED = "member_removed"
    PASSWORD_RESET = "password_reset"  # noqa: S105 - a reason name, not a password
    # An unconfirmed account taken back by a provider that verified the
    # address: whoever signed up before the owner could have made the key.
    RECLAIMED = "reclaimed"
    BANKID = "bankid"
    ACCOUNT_DELETED = "account_deleted"
    COMPANY_CLOSED = "company_closed"


class AgentTokenKind(enum.StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


def _stop_type() -> Enum:
    return Enum(
        AgentKeyStop,
        name="agent_key_stop",
        native_enum=False,
        length=32,
        values_callable=lambda e: [member.value for member in e],
    )


def _token_kind_type() -> Enum:
    return Enum(
        AgentTokenKind,
        name="agent_token_kind",
        native_enum=False,
        length=16,
        values_callable=lambda e: [member.value for member in e],
    )


class AgentKey(Base):
    """One person's permission for one agent to work in one company's books.

    The whole of docs/agents-and-api.md rules 3 and 4 is in the shape of this
    row, so it is worth reading them before changing it:

    - **One person, one company.** ``user_id`` is who issued it and whose
      permissions it works with, *as they stand on each request*: the key never
      holds a role of its own, only ``scopes`` that narrow the person's. Lose
      the role and the key loses it too; leave the company and the key is
      revoked with the membership. A key spanning companies would be an
      account that is shown, which the tenancy model forbids.
    - **What the person copies is spent on first use.** Most people will paste
      it into a conversation with their agent, and a conversation is stored by
      somebody else. So the copied value (the connection key, ``myntos_ck_``)
      opens nothing: it is exchanged once for a short access token and a
      refresh token (:class:`AgentToken`), and from then on the copy in the
      chat history is worthless. ``connection_key_hash`` stays after the
      exchange so a second attempt can be told "already used" rather than
      "unknown", which is the difference between an agent that asks for a new
      key and one that keeps guessing.
    - **Nothing lives for ever.** ``expires_at`` is a year from creation
      whatever happens; a refresh chain unused for a month dies with its last
      token (``agent_refresh_idle_days``).

    Every credential is stored as a SHA-256 digest only, like a session.

    ``user_id`` cascades, which is safe for a reason worth stating: a voucher
    registered with a key names the key's person as its author too, so a
    person any voucher names is kept as a tombstone (docs/gdpr.md) and their
    keys stay with them. A person deleted outright registered nothing, with a
    key or without one.
    """

    __tablename__ = "agent_key"
    __table_args__ = (
        UniqueConstraint("connection_key_hash", name="uq_agent_key_connection_key_hash"),
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    # What the person called it, so the list and the register can say
    # "via «Claude på jobb-PC»" rather than "via nøkkel 14".
    name: Mapped[str] = mapped_column(String(64))
    # Space-separated AgentKeyScope values, sorted. A string rather than a
    # table because the set is three words long and is only ever read whole.
    # What the key may do on its own.
    scopes: Mapped[str] = mapped_column(String(64))
    # The same, for what the key may only *ask* to do: a request needing one of
    # these is stored and waits for its person to approve it on /godkjenning/
    # (:class:`AgentApproval`) instead of running. Never overlaps ``scopes``,
    # and never holds ``read``, which every key does on its own.
    approval_scopes: Mapped[str] = mapped_column(String(64), default="", server_default="")
    connection_key_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH))
    connection_key_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exchanged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Slid at most once a minute, as a session's is, so a busy agent does not
    # turn this row into a write on every request.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The User-Agent the agent's HTTP client sent, truncated. Shown coarsely,
    # to let a person tell their own agent from somebody else's.
    last_used_client: Mapped[str | None] = mapped_column(String(255))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[AgentKeyStop | None] = mapped_column(_stop_type(), nullable=True)

    user: Mapped[User] = relationship()

    @property
    def scope_set(self) -> frozenset[AgentKeyScope]:
        return frozenset(AgentKeyScope(word) for word in self.scopes.split())

    @property
    def approval_scope_set(self) -> frozenset[AgentKeyScope]:
        return frozenset(AgentKeyScope(word) for word in self.approval_scopes.split())


class AgentToken(Base):
    """An access or refresh token in one key's chain.

    **Refresh tokens rotate, and an old one coming back kills the chain.** Each
    refresh is answered with a new pair and stamps ``used_at`` on the token it
    spent. A spent refresh token presented again means two parties hold the
    chain, the agent and whoever copied it, and nothing here can tell which is
    which, so the key is revoked and its person told
    (:func:`myntos.auth.agent_keys.refresh`). Without rotation a refresh token
    leaked in a transcript would work quietly for months.

    Spent rows are therefore kept for as long as they could still be presented,
    which is their own ``expires_at``, and pruned after it: past that a replay
    is refused for being expired anyway, and keeping more would buy nothing
    but rows. Expired access tokens go at the same time.
    """

    __tablename__ = "agent_token"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_agent_token_token_hash"),
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        Index("ix_agent_token_key_id", "key_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    key_id: Mapped[int] = mapped_column(ForeignKey("agent_key.id", ondelete="CASCADE"))
    kind: Mapped[AgentTokenKind] = mapped_column(_token_kind_type())
    token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Refresh tokens only: when it was exchanged for the next pair.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    key: Mapped[AgentKey] = relationship()


class AgentApprovalKind(enum.StrEnum):
    """What a person is asked to approve.

    - ``request``: a call a key made that needs a scope it may only ask for.
      It is stored whole and run, as the key, when its person approves it.
    - ``filing``: a key asking its person to file something to the state,
      which no key may ever do (docs/agents-and-api.md rule 5). Nothing is
      stored to run: approving it is the person going to the page that files
      and doing it there with their own ID-porten login.
    """

    REQUEST = "request"
    FILING = "filing"


class AgentApprovalStatus(enum.StrEnum):
    """Where a waiting request has got to, as stored.

    Two more states are derived on read and never stored, like a key's
    ``expired`` (:func:`myntos.domain.identity.approvals.status_of`): a pending
    request past its deadline has ``expired``, and one whose key has stopped is
    ``key_stopped``. Storing them would be one more thing for the clock, and
    for every place that stops a key, to make wrong.
    """

    PENDING = "pending"
    # Claimed by an approval and being run. Set and committed before the run
    # starts, so two presses of «Godkjenn» cannot run one request twice; a row
    # left here means the process died mid-run, and the page says to check.
    RUNNING = "running"
    # Ran, and the route answered 2xx.
    DONE = "done"
    # Ran, and the route refused it. ``result`` holds what it said.
    FAILED = "failed"
    DECLINED = "declined"
    WITHDRAWN = "withdrawn"


class FilingType(enum.StrEnum):
    """What a key may ask its person to file, on the page where the person files it."""

    MVA_MELDING = "mva_melding"
    # An aksjeselskap's skattemelding and næringsspesifikasjon for one income
    # year. Yearly, so a request for it has no period (``filing_period`` NULL).
    SKATTEMELDING = "skattemelding"


def _approval_kind_type() -> Enum:
    return Enum(
        AgentApprovalKind,
        name="agent_approval_kind",
        native_enum=False,
        length=16,
        values_callable=lambda e: [member.value for member in e],
    )


def _approval_status_type() -> Enum:
    return Enum(
        AgentApprovalStatus,
        name="agent_approval_status",
        native_enum=False,
        length=16,
        values_callable=lambda e: [member.value for member in e],
    )


class AgentApproval(Base):
    """Something a key asked to do that waits for its person's yes.

    The person decides, per key, which of ``write`` and ``post`` the agent may
    do on its own and which it may only ask for (``AgentKey.approval_scopes``).
    A request needing one it may only ask for is stopped at the gate
    (:func:`myntos.api.deps.get_caller`), stored here whole (method, path,
    body, files) and answered 202. When the person approves it on
    /godkjenning/, it is run exactly as it was sent, as the key, so every check
    the route makes is made then, against the books as they are then, and the
    voucher it posts says «via» the key like any other (rule 6).

    Stored whole rather than as a description of what it would do, because a
    description is a second implementation of every route that writes, and
    the one the person approved would be the one that drifted.

    ``label`` is the route's own description in bokmål as it stood when the
    request was made (``key_write("…")`` on the route), and ``note`` is the
    agent's one sentence about why. The note is written by the agent, which
    reads text written by strangers (rule 7), so the page shows it as the
    agent's words beside what was actually sent, never instead of it.

    ``decided_by_user_id`` is SET NULL like every other actor column
    (docs/gdpr.md): who approved is the key's person, who is also the voucher's
    author, so the voucher keeps the answer the books need.
    """

    __tablename__ = "agent_approval"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        Index("ix_agent_approval_company_id_status", "company_id", "status"),
        Index("ix_agent_approval_key_id", "key_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    key_id: Mapped[int] = mapped_column(ForeignKey("agent_key.id", ondelete="CASCADE"))
    kind: Mapped[AgentApprovalKind] = mapped_column(_approval_kind_type())
    status: Mapped[AgentApprovalStatus] = mapped_column(
        _approval_status_type(), default=AgentApprovalStatus.PENDING
    )
    # The scope the request needed: "write" or "post". Empty for a filing.
    scope: Mapped[str] = mapped_column(String(16), default="")
    label: Mapped[str] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(Text)

    # The request, as sent. Empty for a filing.
    method: Mapped[str] = mapped_column(String(8), default="")
    path: Mapped[str] = mapped_column(String(512), default="")
    query: Mapped[str] = mapped_column(String(2048), default="")
    content_type: Mapped[str | None] = mapped_column(String(255))
    # The body of anything but a form: JSON, almost always.
    body: Mapped[bytes | None] = mapped_column(LargeBinary)
    # A multipart form's text fields, as JSON ``{"name": ["value", …]}``. Its
    # files are rows of AgentApprovalFile.
    form: Mapped[str | None] = mapped_column(Text)

    # A filing: what, and for which period.
    filing_type: Mapped[str | None] = mapped_column(String(32))
    filing_year: Mapped[int | None] = mapped_column(Integer)
    filing_period: Mapped[int | None] = mapped_column(Integer)
    # Which of the company's meldinger, by its meldingskategori, when the
    # company files two (docs/integrations/mva.md, «Two returns»): a farm with
    # other business beside it has a «3. termin» in both, and the request has
    # to say which. NULL is the main melding, which is all a request made
    # before there could be two ever meant.
    filing_category: Mapped[str | None] = mapped_column(String(32))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    # Why it was declined, in the person's words, for the agent to read.
    decision_note: Mapped[str | None] = mapped_column(Text)
    # What the route answered when it ran: status and body (JSON, cut short).
    result_status: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[str | None] = mapped_column(Text)

    key: Mapped[AgentKey] = relationship()
    files: Mapped[list[AgentApprovalFile]] = relationship(
        back_populates="approval", order_by="AgentApprovalFile.id"
    )


class AgentApprovalFile(Base):
    """A file in a waiting request: part of its form, or a bilag added to it.

    ``attach`` tells the two apart. A file of the form is sent with the
    request when it runs. An attachment is a bilag the agent added to a
    waiting payment (``POST …/godkjenninger/{id}/vedlegg``), so the person sees
    the receipt in the same approval as the payment it documents; it is sent
    to the archive route the request's own route names, once the request has
    run and its voucher exists.

    ``content`` is emptied once the request leaves ``pending``: run, the file
    is in the bilag archive, which is where five years of keeping belong
    (bokføringsloven); declined, there is nothing to keep it for. The name and
    size stay, so the record still says what was sent.
    """

    __tablename__ = "agent_approval_file"
    __table_args__ = (Index("ix_agent_approval_file_approval_id", "approval_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    approval_id: Mapped[int] = mapped_column(ForeignKey("agent_approval.id", ondelete="CASCADE"))
    # The form field it came in, for a file of the form.
    field: Mapped[str] = mapped_column(String(64))
    attach: Mapped[bool] = mapped_column(Boolean, default=False)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(Integer)
    content: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    approval: Mapped[AgentApproval] = relationship(back_populates="files")


class OAuthLoginAttempt(Base):
    """An in-flight OpenID Connect authorization-code flow.

    The ``state``, ``nonce`` and PKCE verifier have to survive the redirect to
    the provider and come back matched, and a restart or a deploy can land
    between the two (``./update.sh`` restarts the unit) — so the flow's
    short-lived state lives in PostgreSQL like everything else, not in process
    memory. Rows are single-use: ``consumed_at`` is stamped before the code is
    exchanged, which is what makes a replayed callback URL useless.

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
    # See :class:`OAuthIntent`. Every attempt made before the column existed was
    # a sign-in, which is what its migration backfills.
    intent: Mapped[OAuthIntent] = mapped_column(_intent_type(), default=OAuthIntent.LOGIN)
    # Who started a ``link`` flow; NULL for the other two, which nobody is
    # signed in for yet. The callback refuses unless the session it arrives
    # with belongs to this same account: otherwise somebody could start a link
    # on their own account and hand the provider URL to a victim, whose
    # identity would from then on sign in to the attacker's books.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=True
    )


class PendingProviderSignup(Base):
    """A verified identity with no account yet, waiting for an email address.

    BankID proves who somebody is and says nothing about how to reach them, and
    an account needs an address: invitations are addressed to one, and the
    reset link is what answers somebody squatting one. So a BankID signup is two
    steps with the person's own typing in between. The broker answers, this row
    holds the answer, and
    :func:`~myntos.domain.identity.provider_signup.complete_signup` turns it
    into an account once an address is given.

    Shaped like :class:`UserEmailToken`: 256 random bits handed to the browser
    once, stored here only as a SHA-256 digest, with a deadline. Unlike that
    table, rows are **deleted** when used, and expired ones are swept whenever
    another is written: this one holds a person's name and their pseudonym at
    the broker, for somebody who may never come back, and there is no audit
    question it answers afterwards.
    """

    __tablename__ = "pending_provider_signup"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_pending_provider_signup_token_hash"),
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[IdentityProvider] = mapped_column(_provider_type())
    subject: Mapped[str] = mapped_column(String(255))
    # The name the provider asserted, which becomes the account's name. Not
    # editable on the way: that it came from BankID is the point.
    full_name: Mapped[str] = mapped_column(String(255))
    token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH))
    # Carried over from the attempt, so somebody who came from an invitation
    # goes back to it once the account exists.
    redirect_to: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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
