"""Signing up with a provider that proves who you are but not how to reach you.

BankID is the case this exists for. It answers "this is Kari Nordmann" with
more certainty than any address could, and it has no address to give. A Myntos
account needs one anyway: invitations are addressed to one, the confirmation
and reset links go to one, and the reset link is what answers somebody who
signed up as an address that is not theirs.

So signup is two steps. The callback holds the verified identity here
(:func:`hold`) and sends the browser to a page that asks for an address; the
page posts it back (:func:`complete_signup`), and the account is created with
the name BankID gave, the address the person typed, **unconfirmed**, and the
ordinary confirmation mail on its way.

**An address that already has an account is refused, never joined.** This is
the rule the whole module turns on. Every other provider may reach an existing
account through a verified address, because the provider vouches for it. Here
the address is only what somebody typed, and attaching a BankID to whatever
account owns that address would let anyone with a BankID take over anyone's
account by typing their address. The refusal tells the person to sign in the
usual way and link BankID from /konto/, where both halves are proved.

**What this makes is a BankID account**, which signs in with BankID and nothing
else (:func:`~myntos.domain.identity.service.is_bankid_account`): no password,
and no Google or Microsoft added later. The page says so before the button.

Like the rest of the identity domain this module does not commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.auth.oidc import OidcProfile
from myntos.auth.tokens import generate_token, hash_token
from myntos.core.clock import as_utc, utc_now
from myntos.domain.identity import service
from myntos.domain.identity.errors import (
    EmailAlreadyRegisteredError,
    IdentityAlreadyLinkedError,
    InvalidProviderSignupError,
)
from myntos.domain.identity.models import (
    IdentityProvider,
    PendingProviderSignup,
    User,
    UserIdentity,
)

# Long enough to find and type an address, short enough that a held identity
# does not outlive the sitting it was made in.
HOLD_LIFETIME = timedelta(minutes=30)


@dataclass(frozen=True)
class HeldSignup:
    """What the page asking for an address is told about the held identity."""

    provider: IdentityProvider
    full_name: str
    redirect_to: str | None
    expires_at: datetime


async def _sweep(db: AsyncSession) -> None:
    """Delete every expired hold.

    Run whenever a hold is written or redeemed, so nothing needs a scheduled
    job for these rows to go: each one holds a name and a pseudonym for
    somebody who may never come back, and keeping them past their deadline
    buys nothing.
    """
    await db.execute(
        delete(PendingProviderSignup).where(PendingProviderSignup.expires_at <= utc_now())
    )


async def hold(
    db: AsyncSession,
    provider: IdentityProvider,
    profile: OidcProfile,
    *,
    redirect_to: str | None,
) -> str:
    """Keep a verified, unlinked identity for :data:`HOLD_LIFETIME`; return its token.

    The token is handed to the browser once and only its digest is stored, the
    way every other token in this domain is.
    """
    if profile.full_name is None:
        # bankid_profile refuses a token without a name, so this is a provider
        # wired up without one rather than a person without one.
        raise ValueError(f"{provider.value} gave no name to put on the account")
    await _sweep(db)
    token = generate_token()
    now = utc_now()
    db.add(
        PendingProviderSignup(
            provider=provider,
            subject=profile.subject,
            full_name=profile.full_name[:255],
            token_hash=hash_token(token),
            redirect_to=redirect_to,
            created_at=now,
            expires_at=now + HOLD_LIFETIME,
        )
    )
    await db.flush()
    return token


async def _held(db: AsyncSession, token: str) -> PendingProviderSignup:
    """The live hold for ``token``, or one refusal for every reason it is not."""
    row: PendingProviderSignup | None = await db.scalar(
        select(PendingProviderSignup).where(PendingProviderSignup.token_hash == hash_token(token))
    )
    if row is None or as_utc(row.expires_at) <= utc_now():
        raise InvalidProviderSignupError(
            "lenken til registreringen er utløpt eller allerede brukt. Start på nytt med BankID."
        )
    return row


async def describe(db: AsyncSession, *, token: str) -> HeldSignup:
    """What the page needs to say before it asks for an address."""
    row = await _held(db, token)
    return HeldSignup(
        provider=row.provider,
        full_name=row.full_name,
        redirect_to=row.redirect_to,
        expires_at=as_utc(row.expires_at),
    )


async def complete_signup(
    db: AsyncSession,
    *,
    token: str,
    email: str,
    organization_name: str | None = None,
) -> User:
    """Create the account a held identity was waiting for.

    The address is refused if any account has it, and the hold survives the
    refusal, so a person who typed the wrong one can try another without going
    through BankID again. The address goes in unconfirmed: the person typed it
    and nothing has shown it is theirs. The caller sends the confirmation mail,
    the same as for a password signup.
    """
    row = await _held(db, token)
    normalised = service.normalise_email(email)
    if await service.get_user_by_email(db, normalised) is not None:
        raise EmailAlreadyRegisteredError(
            "det finnes allerede en konto med denne e-postadressen. Logg inn på vanlig måte, "
            "og koble til BankID under «Din konto». Da blir BankID den eneste måten du logger "
            "inn på den kontoen."
        )
    already = await db.scalar(
        select(UserIdentity.id).where(
            UserIdentity.provider == row.provider, UserIdentity.subject == row.subject
        )
    )
    if already is not None:
        # Linked from /konto/ in another tab while this page sat open.
        raise IdentityAlreadyLinkedError(
            "denne BankID-en er allerede koblet til en Myntos-konto. Logg inn med BankID."
        )

    # Spent before anything is created, and conditionally: of two submissions
    # of the same hold, only the one that deletes the row goes on.
    spent = await db.execute(
        delete(PendingProviderSignup).where(PendingProviderSignup.id == row.id)
    )
    if getattr(spent, "rowcount", 0) != 1:
        raise InvalidProviderSignupError(
            "lenken til registreringen er allerede brukt. Logg inn med BankID."
        )

    now = utc_now()
    user = User(
        email=normalised,
        full_name=row.full_name.strip(),
        # No password, and an address nobody has confirmed. Both are honest:
        # BankID is the way in, and the address is only what was typed.
        password_hash=None,
        email_verified_at=None,
        last_login_at=now,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as error:
        # The same address signed up in the same instant by somebody else. The
        # unique index decides; this only names the loser's error.
        raise EmailAlreadyRegisteredError(normalised) from error

    db.add(
        UserIdentity(
            user_id=user.id,
            provider=row.provider,
            subject=row.subject,
            email_at_provider=None,
            created_at=now,
            last_login_at=now,
        )
    )
    await db.flush()
    await service.create_organization(
        db,
        name=organization_name or service.default_organization_name(user.full_name, normalised),
        owner=user,
        name_is_placeholder=not organization_name,
    )
    return user
