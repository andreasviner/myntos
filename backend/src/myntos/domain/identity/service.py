"""Account, organization and membership operations.

Everything that reads or writes the identity tables lives here, so the API layer
contains no business rules — it maps HTTP to these functions and their errors.
Sessions are the one exception: they are handled in
:mod:`myntos.auth.sessions`, because a session is a credential rather than a
domain object.

These functions do not commit — the request-scoped session owns the transaction
(:func:`myntos.db.session.get_session`), which is what keeps "create the user,
create their organization, make them its owner" from being able to half-happen.
The single exception is the failed-login counter, which has to survive the
request that failed and says so where it commits.
"""

import re
import secrets
import unicodedata
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from myntos.auth.oidc import OidcProfile
from myntos.auth.passwords import hash_password, needs_rehash, verify_password
from myntos.auth.tokens import generate_token, hash_token
from myntos.core.clock import as_utc, utc_now
from myntos.core.config import get_settings
from myntos.domain.identity import access
from myntos.domain.identity.errors import (
    AccountDisabledError,
    AccountLockedError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidInvitationError,
    InvitationEmailMismatchError,
    NoPasswordSetError,
    NotAMemberError,
    UnverifiedProviderEmailError,
)
from myntos.domain.identity.models import (
    IdentityProvider,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    OrganizationRole,
    User,
    UserIdentity,
)

# Norwegian letters have to be transliterated before Unicode decomposition:
# NFKD turns "å" into "a" + combining ring, which strips to "a" and loses the
# convention that "å" becomes "aa" in a Norwegian slug ("Håkon AS" → "haakon-as",
# not "hakon-as").
_SLUG_TRANSLITERATION = str.maketrans(
    {
        "æ": "ae",
        "ø": "oe",
        "å": "aa",
        "Æ": "ae",
        "Ø": "oe",
        "Å": "aa",
    }
)
_SLUG_MAX_LENGTH = 48
_SLUG_FALLBACK = "organisasjon"


def normalise_email(email: str) -> str:
    """Fold an address to its stored form.

    Only case and surrounding whitespace. Nothing clever: stripping dots or
    ``+tag`` suffixes (a "same Gmail account" trick) would merge addresses that
    are distinct at other providers, and would make the address we store differ
    from the one the user typed and expects invitations at.
    """
    return email.strip().lower()


def slugify(name: str) -> str:
    """A URL-safe handle derived from an organization name."""
    transliterated = name.strip().lower().translate(_SLUG_TRANSLITERATION)
    ascii_only = unicodedata.normalize("NFKD", transliterated).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")[:_SLUG_MAX_LENGTH].strip("-")
    return slug or _SLUG_FALLBACK


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    user: User | None = await db.scalar(select(User).where(User.email == normalise_email(email)))
    return user


async def _unique_slug(db: AsyncSession, name: str) -> str:
    """A free slug for ``name``, disambiguated with a random suffix if taken."""
    base = slugify(name)
    candidate = base
    for _ in range(6):
        taken = await db.scalar(select(Organization.id).where(Organization.slug == candidate))
        if taken is None:
            return candidate
        # A counter would need a second query to find the highest one in use and
        # would still race; four hex characters do not.
        candidate = f"{base[: _SLUG_MAX_LENGTH - 5]}-{secrets.token_hex(2)}"
    raise RuntimeError("could not find a free organization slug")


async def create_organization(
    db: AsyncSession,
    *,
    name: str,
    owner: User,
    name_is_placeholder: bool = False,
) -> Organization:
    """Create an organization with ``owner`` as its first owner.

    The membership is created in the same flush as the organization: an
    organization nobody belongs to is invisible to every endpoint and can only
    be cleaned up by hand.

    ``name_is_placeholder`` marks a name Myntos invented rather than one the
    owner chose — see :func:`adopt_name_if_placeholder`.
    """
    organization = Organization(
        name=name.strip(),
        slug=await _unique_slug(db, name),
        name_is_placeholder=name_is_placeholder,
    )
    db.add(organization)
    await db.flush()
    db.add(
        OrganizationMembership(
            organization_id=organization.id,
            user_id=owner.id,
            role=OrganizationRole.OWNER,
        )
    )
    await db.flush()
    return organization


def default_organization_name(full_name: str, email: str) -> str:
    """A placeholder name for the organization created at signup.

    Users who are not accountants do not think of themselves as having an
    "organization", so signup does not insist on a name. The result is always
    stored with ``name_is_placeholder`` set, so the first real name Myntos
    learns can replace it without anyone having to type it twice.
    """
    stem = full_name.strip() or email.split("@", maxsplit=1)[0]
    return f"{stem} (privat)"


def rename_organization(organization: Organization, name: str) -> None:
    """Give the organization a name a person chose.

    The slug is deliberately left alone: it may already be in circulation in
    links, and a rename is not a good enough reason to break them.
    """
    organization.name = name.strip()
    organization.name_is_placeholder = False


def adopt_name_if_placeholder(organization: Organization, name: str) -> bool:
    """Take ``name`` over, but only if the current one was invented by us.

    Setting up the first set of books is where a private-person account stops
    being one: the orgnr came from Enhetsregisteret, so the registered name is
    both correct and the name its owner would have typed. Leaving "Ola Nordmann
    (privat)" in the header next to it reads as though the lookup failed.

    Only a placeholder is taken over, and only once — after this the name is a
    real one, so a regnskapsfører adding their second client company does not
    get the organization renamed out from under them.

    Returns whether the name changed, for callers that log it.
    """
    if not organization.name_is_placeholder:
        return False
    rename_organization(organization, name)
    return True


async def register_with_password(
    db: AsyncSession,
    *,
    email: str,
    full_name: str,
    password: str,
    organization_name: str | None = None,
) -> tuple[User, Organization]:
    """Create an account, its first organization, and the owner membership."""
    normalised = normalise_email(email)
    # Hash before the existence check so a weak password is rejected on its own
    # terms rather than reported as a duplicate address.
    password_hash = hash_password(password)
    if await get_user_by_email(db, normalised) is not None:
        raise EmailAlreadyRegisteredError(normalised)

    user = User(email=normalised, full_name=full_name.strip(), password_hash=password_hash)
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as error:
        # Two signups for the same address in the same instant. The unique index
        # is the arbiter; the check above only exists to give the loser a
        # sensible error instead of a 500.
        raise EmailAlreadyRegisteredError(normalised) from error

    organization = await create_organization(
        db,
        name=organization_name or default_organization_name(full_name, normalised),
        owner=user,
        name_is_placeholder=not organization_name,
    )
    return user, organization


async def authenticate_with_password(db: AsyncSession, *, email: str, password: str) -> User:
    """Verify an email/password pair and return the user.

    Raises :class:`InvalidCredentialsError` for an unknown address, a wrong
    password, and an account that has no password at all — one error for three
    cases, so the response cannot be used to enumerate accounts. The verify runs
    even when there is no user, so the *timing* cannot either.
    """
    user = await get_user_by_email(db, email)
    now = utc_now()

    if user is not None and user.locked_until is not None and as_utc(user.locked_until) > now:
        raise AccountLockedError("kontoen er midlertidig låst etter for mange forsøk")

    if not verify_password(password, user.password_hash if user else None) or user is None:
        if user is not None:
            _record_failed_login(user)
            # Committed here, against the rule in the module docstring: the
            # request is about to fail, and the request-scoped rollback would
            # otherwise discard the counter that makes lockout work — turning
            # the throttle into a no-op.
            await db.commit()
        raise InvalidCredentialsError("feil e-postadresse eller passord")

    if not user.is_active:
        raise AccountDisabledError("kontoen er deaktivert")

    # Argon2 parameters may have been raised since this hash was made; the only
    # moment we hold the plaintext is right now.
    if user.password_hash is not None and needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    return user


def _record_failed_login(user: User) -> None:
    """Count a failed attempt and lock the account once the limit is reached."""
    settings = get_settings()
    user.failed_login_attempts += 1
    if user.failed_login_attempts >= settings.login_max_failed_attempts:
        user.locked_until = utc_now() + timedelta(minutes=settings.login_lockout_minutes)
        # Reset the counter with the lock, so the next lock needs another full
        # run of failures rather than a single attempt.
        user.failed_login_attempts = 0


def change_password(user: User, *, current: str, new: str) -> None:
    """Replace a user's password after re-checking the current one."""
    if user.password_hash is None:
        raise NoPasswordSetError(
            "kontoen har ikke passord — logg inn med Google, eller sett et passord først"
        )
    if not verify_password(current, user.password_hash):
        raise InvalidCredentialsError("feil nåværende passord")
    user.password_hash = hash_password(new)


async def sign_in_with_provider(
    db: AsyncSession, provider: IdentityProvider, profile: OidcProfile
) -> tuple[User, bool]:
    """Sign in (or sign up) with a verified profile from an identity provider.

    Returns the user and whether the account was created just now.

    Three rules, and the middle one is the security-critical one:

    - A known ``sub`` signs in as its linked user. The email address is not
      consulted at all, so someone who changes their address at the provider
      keeps their account.
    - An unknown ``sub`` whose email matches an **existing** account is linked
      to it only if the provider says it verified that address. Otherwise
      anyone able to set ``someone@else.no`` on an account at a provider that
      does not check ownership could sign in as that Myntos user. This is the
      classic federated-login takeover — Microsoft's nOAuth advisory is the
      same bug — and refusing here is the whole defence.
    - An unknown ``sub`` with no matching account gets a new one, verified or
      not. The account is fresh, so there is nobody to take over; what an
      unverified address still allows is squatting an address in order to
      collect an organization invitation sent to it, and that is equally open
      through password signup until address verification exists (ROADMAP,
      phase 1b). Refusing here would therefore buy no security while making
      Microsoft sign-in impossible for anyone who has not switched on the
      ``xms_edov`` optional claim.

    ``email_verified_at`` is only stamped when the provider actually vouched,
    so the distinction survives in the record rather than being asserted by the
    act of signing in.
    """
    identity = await db.scalar(
        select(UserIdentity)
        .where(
            UserIdentity.provider == provider,
            UserIdentity.subject == profile.subject,
        )
        .options(selectinload(UserIdentity.user))
    )
    now = utc_now()

    if identity is not None:
        user = identity.user
        if not user.is_active:
            raise AccountDisabledError("kontoen er deaktivert")
        identity.last_login_at = now
        identity.email_at_provider = normalise_email(profile.email)
        user.last_login_at = now
        return user, False

    email = normalise_email(profile.email)
    existing = await get_user_by_email(db, email)
    created = existing is None

    if existing is not None and not profile.email_verified:
        raise UnverifiedProviderEmailError(
            "det finnes allerede en konto med denne e-postadressen, og "
            f"{provider.value} har ikke bekreftet at adressen tilhører deg"
        )

    if existing is None:
        user = User(
            email=email,
            full_name=(profile.full_name or email.split("@", maxsplit=1)[0]).strip(),
            # No password: this account can only be reached through the
            # provider until the user sets one.
            password_hash=None,
            email_verified_at=now if profile.email_verified else None,
        )
        db.add(user)
        await db.flush()
    else:
        user = existing
        if not user.is_active:
            raise AccountDisabledError("kontoen er deaktivert")
        if user.email_verified_at is None:
            # A verified provider assertion is stronger evidence than the
            # confirmation email we never sent.
            user.email_verified_at = now

    db.add(
        UserIdentity(
            user_id=user.id,
            provider=provider,
            subject=profile.subject,
            email_at_provider=email,
            last_login_at=now,
        )
    )
    user.last_login_at = now
    await db.flush()

    if created:
        await create_organization(
            db,
            name=default_organization_name(user.full_name, email),
            owner=user,
            name_is_placeholder=True,
        )
    return user, created


async def list_memberships(db: AsyncSession, user_id: int) -> list[OrganizationMembership]:
    """Every organization the user belongs to, with the organization loaded."""
    result = await db.scalars(
        select(OrganizationMembership)
        .where(OrganizationMembership.user_id == user_id)
        .options(selectinload(OrganizationMembership.organization))
        .order_by(OrganizationMembership.id)
    )
    return list(result)


async def get_membership(
    db: AsyncSession, *, organization_id: int, user_id: int
) -> OrganizationMembership:
    """The user's membership, or :class:`NotAMemberError`.

    Non-membership is reported the same way whether the organization exists or
    not, so the API cannot be used to probe for organization ids.
    """
    membership = await db.scalar(
        select(OrganizationMembership)
        .where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == user_id,
        )
        .options(selectinload(OrganizationMembership.organization))
    )
    if membership is None:
        raise NotAMemberError(f"no membership in organization {organization_id}")
    return membership


async def list_members(db: AsyncSession, organization_id: int) -> list[OrganizationMembership]:
    result = await db.scalars(
        select(OrganizationMembership)
        .where(OrganizationMembership.organization_id == organization_id)
        .options(selectinload(OrganizationMembership.user))
        .order_by(OrganizationMembership.id)
    )
    return list(result)


async def count_owners(db: AsyncSession, organization_id: int) -> int:
    total = await db.scalar(
        select(func.count())
        .select_from(OrganizationMembership)
        .where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.role == OrganizationRole.OWNER,
        )
    )
    return int(total or 0)


async def invite_member(
    db: AsyncSession,
    *,
    organization: Organization,
    actor: OrganizationMembership,
    email: str,
    role: OrganizationRole,
) -> tuple[OrganizationInvitation, str]:
    """Invite an address to join ``organization``; returns the invitation and token.

    The plaintext token is returned exactly once, to be put in a link. Only its
    hash is stored, so it cannot be recovered afterwards — a lost invitation is
    re-sent, never looked up.
    """
    access.require_can_grant_role(actor.role, role)
    normalised = normalise_email(email)

    already_member = await db.scalar(
        select(OrganizationMembership.id)
        .join(User, User.id == OrganizationMembership.user_id)
        .where(
            OrganizationMembership.organization_id == organization.id,
            User.email == normalised,
        )
    )
    if already_member is not None:
        raise InvalidInvitationError("denne e-postadressen er allerede medlem")

    # Re-inviting replaces the pending invitation rather than adding a second
    # one, so an address always has at most one live token.
    pending = await db.scalars(
        select(OrganizationInvitation).where(
            OrganizationInvitation.organization_id == organization.id,
            OrganizationInvitation.email == normalised,
            OrganizationInvitation.accepted_at.is_(None),
        )
    )
    for superseded in pending:
        await db.delete(superseded)

    token = generate_token()
    now = utc_now()
    invitation = OrganizationInvitation(
        organization_id=organization.id,
        email=normalised,
        role=role,
        token_hash=hash_token(token),
        invited_by_user_id=actor.user_id,
        created_at=now,
        expires_at=now + timedelta(days=get_settings().invitation_lifetime_days),
    )
    db.add(invitation)
    await db.flush()
    return invitation, token


async def list_invitations(
    db: AsyncSession, organization_id: int, *, pending_only: bool = True
) -> list[OrganizationInvitation]:
    statement = select(OrganizationInvitation).where(
        OrganizationInvitation.organization_id == organization_id
    )
    if pending_only:
        statement = statement.where(OrganizationInvitation.accepted_at.is_(None))
    result = await db.scalars(statement.order_by(OrganizationInvitation.id))
    return list(result)


async def revoke_invitation(db: AsyncSession, *, organization_id: int, invitation_id: int) -> None:
    invitation = await db.scalar(
        select(OrganizationInvitation).where(
            OrganizationInvitation.id == invitation_id,
            OrganizationInvitation.organization_id == organization_id,
            OrganizationInvitation.accepted_at.is_(None),
        )
    )
    if invitation is None:
        raise InvalidInvitationError("invitasjonen finnes ikke")
    await db.delete(invitation)


async def accept_invitation(db: AsyncSession, *, token: str, user: User) -> OrganizationMembership:
    """Turn an invitation token into a membership for ``user``.

    The signed-in account's email must equal the invited address. An invitation
    is addressed to a person at an address; honouring a forwarded one for
    whoever happens to be logged in would make it a bearer credential for
    joining an organization, which is not what the sender agreed to.
    """
    invitation = await db.scalar(
        select(OrganizationInvitation)
        .where(OrganizationInvitation.token_hash == hash_token(token))
        .options(selectinload(OrganizationInvitation.organization))
    )
    now = utc_now()
    if (
        invitation is None
        or invitation.accepted_at is not None
        or as_utc(invitation.expires_at) <= now
    ):
        raise InvalidInvitationError("invitasjonen er ugyldig eller utløpt")

    if invitation.email != normalise_email(user.email):
        raise InvitationEmailMismatchError(
            "invitasjonen er sendt til en annen e-postadresse enn den du er logget inn med"
        )

    invitation.accepted_at = now
    invitation.accepted_by_user_id = user.id

    existing = await db.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == invitation.organization_id,
            OrganizationMembership.user_id == user.id,
        )
    )
    if existing is not None:
        return existing

    membership = OrganizationMembership(
        organization_id=invitation.organization_id,
        user_id=user.id,
        role=invitation.role,
    )
    db.add(membership)
    await db.flush()
    return membership


async def change_member_role(
    db: AsyncSession,
    *,
    actor: OrganizationMembership,
    target: OrganizationMembership,
    role: OrganizationRole,
) -> OrganizationMembership:
    """Change a member's role, refusing the two changes that break an organization."""
    access.require_can_change_member(actor.role, target.role)
    access.require_can_grant_role(actor.role, role)
    access.require_owner_remains(
        owner_count=await count_owners(db, target.organization_id),
        current_role=target.role,
        new_role=role,
    )
    target.role = role
    return target


async def remove_member(
    db: AsyncSession,
    *,
    actor: OrganizationMembership,
    target: OrganizationMembership,
) -> None:
    """Remove a member, or leave the organization yourself.

    Leaving needs no member-management permission — a viewer must be able to
    walk away — but it is still subject to the last-owner rule.
    """
    if actor.id != target.id:
        access.require_can_change_member(actor.role, target.role)
    access.require_owner_remains(
        owner_count=await count_owners(db, target.organization_id),
        current_role=target.role,
        new_role=None,
    )
    await db.delete(target)
