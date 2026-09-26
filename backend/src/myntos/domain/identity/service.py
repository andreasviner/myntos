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
from dataclasses import dataclass
from datetime import datetime, timedelta

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
    InvitationNotFoundError,
    NoPasswordSetError,
    NotAMemberError,
    UnverifiedProviderEmailError,
)
from myntos.domain.identity.models import (
    CompanyMembership,
    IdentityProvider,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    OrganizationRole,
    User,
    UserGovernmentIdentity,
    UserIdentity,
)

# The ledger's *models* only, never its service: access to a set of books is an
# identity question about a company, so this module has to name the table. The
# dependency runs one way (ledger.service reads identity.models, and
# ledger.models imports neither) so naming it here closes no loop.
from myntos.domain.ledger.models import Company

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
    owner chose. Nothing takes a placeholder over any more; see the note further
    down where ``adopt_name_if_placeholder`` used to be.
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


# Here lived adopt_name_if_placeholder, which renamed an organization after the
# first company set up under it whenever the organization was still carrying the
# name signup invented ("Ola Nordmann (privat)").
#
# It is gone, and an account is now never named after one of its companies.
#
# The reason it existed was the header: an orgnr looked up correctly from
# Enhetsregisteret with "Ola Nordmann (privat)" printed beside it read as though
# the lookup had failed. The header has not shown the organization's name for a
# long time (it shows the company being worked on, with the signed-in person
# under it), so the nicety was buying nothing.
#
# What it cost was worse. An account called after one of its companies is
# indistinguishable, on screen, from a company's name having crossed over into
# another company's page, which is the one thing this application must never
# look like. Two pages had already grown workarounds for it (the eyebrow on
# /index.astro and showTittel on /organisasjon/, both of which explain
# themselves by pointing here), and it was still reported as a data leak the
# first time an account held two companies: the account was called "Lindeman
# Technologies" after the first of them, so the Organisasjonen card said one
# company's name while the header said the other's.
#
# A placeholder name is a question the owner can answer in one click on
# /organisasjon/, which is a better answer than a name that is true of something
# else. See migration 20260903_c4d7e81f2a90.


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
            "kontoen har ikke passord, så logg inn med Google, eller sett et passord først"
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


# --- Who may open one company's books ---------------------------------------------
#
# The account (:class:`OrganizationMembership`, above) says whose companies
# these are and who may add another. Everything below is about a single set of
# books: who can read it, who can write it, and who may invite the next person
# in. See :class:`CompanyMembership`.


async def get_company_membership(
    db: AsyncSession, *, company_id: int, user_id: int
) -> CompanyMembership:
    """The user's access to one company, or :class:`NotAMemberError`.

    Reported the same way whether the company exists or not, so the API cannot
    be used to probe for company ids.
    """
    membership = await db.scalar(
        select(CompanyMembership).where(
            CompanyMembership.company_id == company_id,
            CompanyMembership.user_id == user_id,
        )
    )
    if membership is None:
        raise NotAMemberError(f"no membership in company {company_id}")
    return membership


async def grant_company_access(
    db: AsyncSession, *, company_id: int, user_id: int, role: OrganizationRole
) -> CompanyMembership:
    """Give ``user_id`` access to one company, or leave the role they have.

    Idempotent on purpose: it is called when a company is created, when an
    invitation is accepted, and from the backfill in migration ``d1a7f2c6b483``.
    An existing role is *not* overwritten, because the row on the table is the
    decision somebody made about this person on these books, and re-running a
    grant is not a decision.
    """
    existing = await db.scalar(
        select(CompanyMembership).where(
            CompanyMembership.company_id == company_id,
            CompanyMembership.user_id == user_id,
        )
    )
    if existing is not None:
        return existing
    membership = CompanyMembership(company_id=company_id, user_id=user_id, role=role)
    db.add(membership)
    await db.flush()
    return membership


async def list_company_members(db: AsyncSession, company_id: int) -> list[CompanyMembership]:
    """Everyone with access to one company, with their ID-porten link loaded.

    The link rides along rather than being fetched per row: the member list is
    the page that shows who has confirmed who they are, and one query for the
    whole answer beats one per member.
    """
    result = await db.scalars(
        select(CompanyMembership)
        .where(CompanyMembership.company_id == company_id)
        .options(selectinload(CompanyMembership.user).selectinload(User.government_identity))
        .order_by(CompanyMembership.id)
    )
    return list(result)


async def count_company_owners(db: AsyncSession, company_id: int) -> int:
    total = await db.scalar(
        select(func.count())
        .select_from(CompanyMembership)
        .where(
            CompanyMembership.company_id == company_id,
            CompanyMembership.role == OrganizationRole.OWNER,
        )
    )
    return int(total or 0)


async def invite_member(
    db: AsyncSession,
    *,
    company: Company,
    actor: CompanyMembership,
    email: str,
    role: OrganizationRole,
    full_name: str | None = None,
) -> tuple[OrganizationInvitation, str]:
    """Invite an address to one company's books; returns the invitation and token.

    The plaintext token is returned exactly once, to be put in a link. Only its
    hash is stored, so it cannot be recovered afterwards — a lost invitation is
    re-sent (:func:`resend_invitation`), never looked up.

    ``full_name`` is who the inviter says this is. It is a label, never a check:
    the invitation is still addressed to the *address*, and acceptance still
    requires signing in as it. What the name buys is a message that greets a
    person and a register role list that can show they have been asked.

    The invitation is for **one company**, and the account that owns it is
    recorded alongside only so the row goes away with the account. Inviting a
    colleague to one client's books used to hand them every client's at once,
    which is not something anybody would have asked for out loud.
    """
    access.require_can_grant_role(actor.role, role)
    normalised = normalise_email(email)

    already_member = await db.scalar(
        select(CompanyMembership.id)
        .join(User, User.id == CompanyMembership.user_id)
        .where(
            CompanyMembership.company_id == company.id,
            User.email == normalised,
        )
    )
    if already_member is not None:
        raise InvalidInvitationError("denne e-postadressen har allerede tilgang til selskapet")

    # Re-inviting replaces the pending invitation rather than adding a second
    # one, so an address always has at most one live token per company. Scoped
    # to the company, so inviting the same person to a second set of books does
    # not cancel the first invitation.
    pending = await db.scalars(
        select(OrganizationInvitation).where(
            OrganizationInvitation.company_id == company.id,
            OrganizationInvitation.email == normalised,
            OrganizationInvitation.accepted_at.is_(None),
        )
    )
    for superseded in pending:
        await db.delete(superseded)

    token = generate_token()
    now = utc_now()
    invitation = OrganizationInvitation(
        organization_id=company.organization_id,
        company_id=company.id,
        email=normalised,
        full_name=_clean_name(full_name),
        role=role,
        token_hash=hash_token(token),
        invited_by_user_id=actor.user_id,
        created_at=now,
        expires_at=now + timedelta(days=get_settings().invitation_lifetime_days),
    )
    db.add(invitation)
    await db.flush()
    return invitation, token


def _clean_name(name: str | None) -> str | None:
    """Whitespace-trimmed, or ``None`` for anything that is not a name.

    An empty string from a form field somebody tabbed through is not a name,
    and storing one would make every later ``is not None`` check wrong.
    """
    if name is None:
        return None
    trimmed = " ".join(name.split())
    return trimmed or None


async def resend_invitation(
    db: AsyncSession,
    *,
    company_id: int,
    invitation_id: int,
    actor: CompanyMembership,
) -> tuple[OrganizationInvitation, str]:
    """Issue a new token for a pending invitation, and return it.

    The one thing that could not be done before: only the token's hash is
    stored, so an invitation whose email bounced, went to spam or was deleted
    unread could not be sent again: it had to be revoked and the address
    retyped, which loses the role, the name and who sent it.

    A **new** token, not the old one, because the old one is unrecoverable by
    design. That has a consequence worth stating: the previous link stops
    working the moment this is called, and the message says so. The expiry is
    extended too, because an invitation being re-sent is one somebody still
    wants honoured, and handing out a link that expires tomorrow would be a
    formality.
    """
    access.require_permission(actor.role, access.Permission.MEMBER_MANAGE)
    invitation = await db.scalar(
        select(OrganizationInvitation).where(
            OrganizationInvitation.id == invitation_id,
            OrganizationInvitation.company_id == company_id,
            OrganizationInvitation.accepted_at.is_(None),
        )
    )
    if invitation is None:
        raise InvitationNotFoundError("invitasjonen finnes ikke")

    token = generate_token()
    now = utc_now()
    invitation.token_hash = hash_token(token)
    invitation.expires_at = now + timedelta(days=get_settings().invitation_lifetime_days)
    # Whoever pressed the button owns the message that goes out, and its
    # Reply-To. Leaving the original inviter on it would send a puzzled reply to
    # somebody who did nothing today.
    invitation.invited_by_user_id = actor.user_id
    await db.flush()
    return invitation, token


async def list_invitations(
    db: AsyncSession, company_id: int, *, pending_only: bool = True
) -> list[OrganizationInvitation]:
    statement = (
        select(OrganizationInvitation)
        .where(OrganizationInvitation.company_id == company_id)
        # Who sent each one. A pending invitation nobody recognises is a
        # question an administrator cannot answer, and it is one join.
        .options(selectinload(OrganizationInvitation.invited_by))
    )
    if pending_only:
        statement = statement.where(OrganizationInvitation.accepted_at.is_(None))
    result = await db.scalars(statement.order_by(OrganizationInvitation.id))
    return list(result)


async def revoke_invitation(db: AsyncSession, *, company_id: int, invitation_id: int) -> None:
    invitation = await db.scalar(
        select(OrganizationInvitation).where(
            OrganizationInvitation.id == invitation_id,
            OrganizationInvitation.company_id == company_id,
            OrganizationInvitation.accepted_at.is_(None),
        )
    )
    if invitation is None:
        raise InvitationNotFoundError("invitasjonen finnes ikke")
    await db.delete(invitation)


@dataclass(frozen=True, slots=True)
class InvitationDescription:
    """What an invitation says about itself, before anyone has signed in.

    The welcome page needs this: a person following a link out of their inbox
    should see who invited them and into what *before* being asked to create an
    account, or the first thing Myntos ever says to them is a login form.

    Everything here is already in the invitation email, which the reader has in
    front of them, so the token buys no information it did not already carry.
    That is what makes it safe to answer without a session.
    """

    company_name: str
    email: str
    role: OrganizationRole
    expires_at: datetime
    # None when the inviter has since deleted their account: the invitation
    # survives them (invited_by_user_id is SET NULL), and it is still valid.
    inviter_name: str | None
    # Who the inviter addressed it to, when they said. The welcome page offers
    # it as the name on the account being created, so somebody invited by name
    # does not retype it.
    full_name: str | None = None


async def describe_invitation(db: AsyncSession, *, token: str) -> InvitationDescription:
    """Describe a live invitation, or refuse.

    One refusal for every unusable token — unknown, spent, expired — and
    deliberately so. Distinguishing them would tell a stranger holding a guessed
    token that they had guessed a real one.
    """
    invitation = await db.scalar(
        select(OrganizationInvitation).where(OrganizationInvitation.token_hash == hash_token(token))
    )
    if (
        invitation is None
        or invitation.accepted_at is not None
        or as_utc(invitation.expires_at) <= utc_now()
        # An invitation from before access was per company names no books, so
        # there is nothing truthful to put on the welcome page. Refused with the
        # same words as every other unusable token.
        or invitation.company_id is None
    ):
        raise InvalidInvitationError("invitasjonen er ugyldig eller utløpt")

    inviter_name: str | None = None
    if invitation.invited_by_user_id is not None:
        inviter_name = await db.scalar(
            select(User.full_name).where(User.id == invitation.invited_by_user_id)
        )

    company_name = await db.scalar(select(Company.name).where(Company.id == invitation.company_id))
    if company_name is None:
        # The books were deleted while the invitation was in flight.
        raise InvalidInvitationError("invitasjonen er ugyldig eller utløpt")

    return InvitationDescription(
        company_name=company_name,
        email=invitation.email,
        role=invitation.role,
        expires_at=as_utc(invitation.expires_at),
        inviter_name=inviter_name or None,
        full_name=invitation.full_name,
    )


async def accept_invitation(db: AsyncSession, *, token: str, user: User) -> CompanyMembership:
    """Turn an invitation token into access to one company for ``user``.

    The signed-in account's email must equal the invited address. An invitation
    is addressed to a person at an address; honouring a forwarded one for
    whoever happens to be logged in would make it a bearer credential for
    reaching somebody's books, which is not what the sender agreed to.

    What it grants is access to the invited company and **nothing else**: no
    membership of the account, so the invitee cannot set up further companies
    under somebody else's name, and no sight of the account's other clients.
    """
    invitation = await db.scalar(
        select(OrganizationInvitation).where(OrganizationInvitation.token_hash == hash_token(token))
    )
    now = utc_now()
    if (
        invitation is None
        or invitation.accepted_at is not None
        or as_utc(invitation.expires_at) <= now
        # See describe_invitation: an invitation with no company names nothing
        # to give access to.
        or invitation.company_id is None
    ):
        raise InvalidInvitationError("invitasjonen er ugyldig eller utløpt")

    if invitation.email != normalise_email(user.email):
        raise InvitationEmailMismatchError(
            "invitasjonen er sendt til en annen e-postadresse enn den du er logget inn med"
        )

    invitation.accepted_at = now
    invitation.accepted_by_user_id = user.id

    return await grant_company_access(
        db, company_id=invitation.company_id, user_id=user.id, role=invitation.role
    )


async def change_member_role(
    db: AsyncSession,
    *,
    actor: CompanyMembership,
    target: CompanyMembership,
    role: OrganizationRole,
) -> CompanyMembership:
    """Change what somebody may do with one company's books.

    Refuses the two changes that would break it: handing out a role the actor
    does not hold themselves, and removing its last owner.
    """
    access.require_can_change_member(actor.role, target.role)
    access.require_can_grant_role(actor.role, role)
    access.require_owner_remains(
        owner_count=await count_company_owners(db, target.company_id),
        current_role=target.role,
        new_role=role,
    )
    target.role = role
    return target


async def remove_member(
    db: AsyncSession,
    *,
    actor: CompanyMembership,
    target: CompanyMembership,
) -> None:
    """Take away access to one company, or give up your own.

    Giving up your own needs no member-management permission (a viewer must be
    able to walk away) but it is still subject to the last-owner rule: books
    nobody owns can be neither billed nor shared, and no endpoint may create
    that state.
    """
    if actor.id != target.id:
        access.require_can_change_member(actor.role, target.role)
    access.require_owner_remains(
        owner_count=await count_company_owners(db, target.company_id),
        current_role=target.role,
        new_role=None,
    )
    await db.delete(target)


# --- The person behind the account ------------------------------------------------


async def link_government_identity(
    db: AsyncSession,
    *,
    user_id: int,
    pid: str,
    full_name: str | None = None,
    acr: str | None = None,
) -> UserGovernmentIdentity:
    """Record that ``user_id`` has proved, through ID-porten, who they are.

    Called by the ID-porten callback once and only once per login, after the
    assurance level has been checked: a login that did not reach the required
    level is not evidence of anything and must not leave a link behind.

    Idempotent by user: the row is created on the first login and updated on
    every one after it. Updated rather than added to, because the question it
    answers has one answer at a time. See
    :class:`~myntos.domain.identity.models.UserGovernmentIdentity` for why the
    pid is not unique across accounts and why a change of person is allowed
    rather than refused.

    ``full_name`` is ``None`` unless the deployment requests ID-porten's
    ``profile`` scope. A link with no name is still worth having, since it is
    what a submission is attributed to, but it cannot be matched against
    Enhetsregisteret, so the previous name is kept rather than blanked: a
    deployment that loses the scope should not silently lose every name it
    already learned.
    """
    now = utc_now()
    existing = await db.scalar(
        select(UserGovernmentIdentity).where(UserGovernmentIdentity.user_id == user_id)
    )
    cleaned = _clean_name(full_name)
    if existing is None:
        identity = UserGovernmentIdentity(
            user_id=user_id,
            pid=pid,
            full_name=cleaned,
            acr=acr,
            linked_at=now,
            verified_at=now,
        )
        db.add(identity)
        await db.flush()
        return identity

    existing.pid = pid
    if cleaned is not None:
        existing.full_name = cleaned
    existing.acr = acr
    existing.verified_at = now
    await db.flush()
    return existing


async def government_identity_for(db: AsyncSession, user_id: int) -> UserGovernmentIdentity | None:
    """The ID-porten link on one account, or ``None``."""
    identity: UserGovernmentIdentity | None = await db.scalar(
        select(UserGovernmentIdentity).where(UserGovernmentIdentity.user_id == user_id)
    )
    return identity
