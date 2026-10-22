"""Links mailed to an account's own address: confirming it, resetting a password, leaving BankID.

All three are the same mechanism with different stakes. A link is minted here, its
digest stored in :class:`~myntos.domain.identity.models.UserEmailToken`, and the
plaintext handed back exactly once for the caller to put in a message. Redeeming
it proves one thing only: that whoever clicked could read the inbox it was sent
to. Everything below follows from taking that literally.

**A confirmation link stamps ``email_verified_at`` and does nothing else.** It
does not sign anybody in. The link can be opened in a different browser from the
one that asked for it, or by a mail scanner following every URL in a message,
and neither should come out of it holding a session.

**A reset link is a password, so it is treated like one.** Thirty minutes by
default, spent the moment it is used, and every older reset link for the account
dies with it. Completing a reset also stamps the address as confirmed (the
person has just shown they read that inbox) and lifts the login lockout, because
the lockout bounds password *guessing* and nobody guessed anything here.
Revoking the account's sessions is the API layer's job, beside the identical
call after a password change, because sessions live in :mod:`myntos.auth`.

**Redeeming is a conditional update, not a read and then a write.** Two
requests carrying the same link at the same instant would both read
``consumed_at`` as empty; only one of them can win
``UPDATE ... WHERE consumed_at IS NULL``. That is what makes "single-use" true
under concurrency rather than merely under test.

**The anonymous request never says whether an address exists.**
:func:`request_password_reset` returns ``None`` for an unknown address, a
deactivated account and an account that has hit the hourly cap alike, and the
endpoint answers all of them with the same 202. See its docstring for what that
does and does not cover.

**A BankID account never gets a password from a reset.** BankID's terms forbid
using a BankID login to set up or restore a sign-in of our own (ID-veksling,
docs/integrations/bankid.md), and a reset link on an account BankID opened would
be exactly that. The request is answered the same way, and the inbox gets a
message saying the account signs in with BankID, with no link that sets
anything. The one way from BankID to a password is
:func:`request_switch_to_email`, which only a signed-in BankID account can ask
for, and which takes effect only when the link it mails is used.
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.auth.passwords import hashed, validate_password
from myntos.auth.tokens import generate_token, hash_token
from myntos.core.clock import as_utc, utc_now
from myntos.core.config import get_settings
from myntos.domain.identity.errors import (
    AccountMailRateLimitedError,
    BankidAccountError,
    EmailAlreadyVerifiedError,
    IdentityNotFoundError,
    InvalidEmailTokenError,
)
from myntos.domain.identity.models import (
    EmailTokenPurpose,
    IdentityProvider,
    User,
    UserEmailToken,
    UserIdentity,
)
from myntos.domain.identity.service import get_user_by_email, is_bankid_account

_RATE_WINDOW = timedelta(hours=1)


@dataclass(frozen=True)
class IssuedLink:
    """A freshly minted link, for exactly one message.

    ``token`` is the only copy of the plaintext that will ever exist; the row
    holds its digest.
    """

    user: User
    token: str
    row: UserEmailToken


@dataclass(frozen=True)
class BankidNotice:
    """The answer to a reset request for a BankID account: a message, and no link.

    The account has no password to reset and may not be given one this way, so
    what goes to the inbox says that it signs in with BankID and where to do
    it. Recorded like a link, so the hourly cap counts it, but nothing here
    can be redeemed.
    """

    user: User


def _lifetime(purpose: EmailTokenPurpose) -> timedelta:
    settings = get_settings()
    # Both of these set a password, so both are short: a link in an inbox is a
    # password for as long as it lives.
    if purpose in (EmailTokenPurpose.RESET_PASSWORD, EmailTokenPurpose.SWITCH_TO_PASSWORD):
        return timedelta(minutes=settings.password_reset_lifetime_minutes)
    return timedelta(hours=settings.email_verification_lifetime_hours)


async def _recent_count(db: AsyncSession, user_id: int, purpose: EmailTokenPurpose) -> int:
    since = utc_now() - _RATE_WINDOW
    count = await db.scalar(
        select(func.count())
        .select_from(UserEmailToken)
        .where(
            UserEmailToken.user_id == user_id,
            UserEmailToken.purpose == purpose,
            UserEmailToken.created_at > since,
        )
    )
    return int(count or 0)


async def _retire_live_links(db: AsyncSession, user_id: int, purpose: EmailTokenPurpose) -> None:
    """Stamp every unspent link of ``purpose`` for this account as spent."""
    await db.execute(
        update(UserEmailToken)
        .where(
            UserEmailToken.user_id == user_id,
            UserEmailToken.purpose == purpose,
            UserEmailToken.consumed_at.is_(None),
        )
        .values(consumed_at=utc_now())
    )


async def issue_link(db: AsyncSession, user: User, purpose: EmailTokenPurpose) -> IssuedLink:
    """Mint a link of ``purpose`` for ``user``'s current address.

    Raises :class:`AccountMailRateLimitedError` once the account has had
    ``account_mail_max_per_hour`` links of this kind in the last hour. Earlier
    unspent links of the same kind are retired first, so only the newest
    message in the inbox works.
    """
    settings = get_settings()
    if await _recent_count(db, user.id, purpose) >= settings.account_mail_max_per_hour:
        raise AccountMailRateLimitedError(
            "du har bedt om for mange e-poster den siste timen, så vent litt og prøv igjen"
        )

    await _retire_live_links(db, user.id, purpose)
    now = utc_now()
    token = generate_token()
    row = UserEmailToken(
        user_id=user.id,
        purpose=purpose,
        email=user.email,
        token_hash=hash_token(token),
        created_at=now,
        expires_at=now + _lifetime(purpose),
    )
    db.add(row)
    await db.flush()
    return IssuedLink(user=user, token=token, row=row)


async def issue_verification_link(db: AsyncSession, user: User) -> IssuedLink:
    """A confirmation link for a signed-in account whose address is unconfirmed."""
    if user.email_verified:
        raise EmailAlreadyVerifiedError("e-postadressen er allerede bekreftet")
    return await issue_link(db, user, EmailTokenPurpose.VERIFY_EMAIL)


async def request_password_reset(
    db: AsyncSession, *, email: str
) -> IssuedLink | BankidNotice | None:
    """A reset link for ``email``, a BankID notice, or ``None`` without saying why.

    ``None`` covers an unknown address, a deactivated account and the hourly cap
    alike. The endpoint turns every one of them into the same 202 and sends the
    message off the request, so neither the body nor the time it takes to send a
    mail tells a stranger which addresses have accounts.

    What is left is a database write the unknown address does not get: one
    small insert, well inside the jitter of a request crossing the internet.
    Making the two paths identical would mean writing a decoy row for every
    address anyone types, which is a table of other people's addresses kept for
    no one's benefit.

    A Google or Microsoft account gets a link too. Following it sets a first
    password, which is what the person who owns that inbox would need if they
    lost access to their Google account, and is no more than the inbox already
    lets them do anywhere else.

    A BankID account does not (see the module docstring), and gets a
    :class:`BankidNotice` instead. The endpoint answers it with the same 202,
    and it costs the same one insert as a link, a ``bankid_notice`` row that
    the cap counts, so the database work does not tell the two apart either.
    """
    user = await get_user_by_email(db, email)
    if user is None or not user.is_active:
        return None
    try:
        if await is_bankid_account(db, user.id):
            # The token is minted and dropped: the row exists for the cap, and
            # no message ever carries it.
            await issue_link(db, user, EmailTokenPurpose.BANKID_NOTICE)
            return BankidNotice(user=user)
        return await issue_link(db, user, EmailTokenPurpose.RESET_PASSWORD)
    except AccountMailRateLimitedError:
        return None


async def _redeem(db: AsyncSession, token: str, purpose: EmailTokenPurpose) -> User:
    """Spend ``token`` and return the account it belongs to, or refuse.

    Every refusal is the same :class:`InvalidEmailTokenError`.
    """
    refused = InvalidEmailTokenError("lenken er ugyldig, allerede brukt eller utløpt")
    row = await db.scalar(
        select(UserEmailToken).where(
            UserEmailToken.token_hash == hash_token(token),
            # The purpose is part of the key: a confirmation link must never
            # reset a password, whatever else is true about it.
            UserEmailToken.purpose == purpose,
        )
    )
    now = utc_now()
    if row is None or row.consumed_at is not None or as_utc(row.expires_at) <= now:
        raise refused

    user = await db.get(User, row.user_id)
    if user is None or not user.is_active or user.email != row.email:
        raise refused

    # The conditional update is the real single-use check; the read above only
    # turns the common refusals into a clear answer without a write.
    spent = await db.execute(
        update(UserEmailToken)
        .where(UserEmailToken.id == row.id, UserEmailToken.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    if getattr(spent, "rowcount", 0) != 1:
        raise refused
    return user


async def verify_email(db: AsyncSession, *, token: str) -> User:
    """Confirm the address a confirmation link was sent to."""
    user = await _redeem(db, token, EmailTokenPurpose.VERIFY_EMAIL)
    if user.email_verified_at is None:
        user.email_verified_at = utc_now()
    # Any other confirmation link still sitting in the inbox now says nothing
    # new, and a live credential that says nothing is only a liability.
    await _retire_live_links(db, user.id, EmailTokenPurpose.VERIFY_EMAIL)
    return user


async def reset_password(db: AsyncSession, *, token: str, new_password: str) -> User:
    """Set a new password through a reset link.

    The password is checked against the policy before the link is spent, so a
    password that is too short costs the person a retry and not their link.

    Refused for a BankID account even with a good link. None should exist: a
    request for one gets a notice instead, and the switch to BankID retires any
    that were already in the inbox. This is the backstop for the one that got
    through anyway, and the refusal rolls back with the request, so the link is
    not spent by it.
    """
    validate_password(new_password)
    user = await _redeem(db, token, EmailTokenPurpose.RESET_PASSWORD)
    if await is_bankid_account(db, user.id):
        raise BankidAccountError(
            "kontoen logger inn med BankID og kan ikke få passord med denne lenken. Vil du "
            "heller logge inn med e-post og passord, velg «Bytt til innlogging med e-post» "
            "under «Din konto»."
        )
    user.password_hash = await hashed(new_password)
    if user.email_verified_at is None:
        # Until now nobody had shown the address was theirs, so any provider
        # on the account was linked by whoever made it: a Microsoft login, say,
        # whose unverified address was this one. A Google or Microsoft login is
        # matched on its own subject and never on the address, so leaving it
        # would let the squatter straight back in after the owner had taken
        # the account back. The owner links their own again from «Din konto».
        await db.execute(delete(UserIdentity).where(UserIdentity.user_id == user.id))
        user.email_verified_at = utc_now()
    user.failed_login_attempts = 0
    user.locked_until = None
    await _retire_live_links(db, user.id, EmailTokenPurpose.RESET_PASSWORD)
    return user


# --- Leaving BankID --------------------------------------------------------------
#
# The one step in Myntos where BankID is the gate to another way in: the person
# starts it signed in with BankID. It is built so that BankID is not what makes
# the password, because that would be ID-veksling. The request only mails a
# link to the address the account already has; nothing changes until somebody
# who can read that inbox follows it and chooses a password, and that is when
# BankID stops working. The link is the proof, exactly as it is for a reset,
# and a hijacked BankID session gets no further than sending it.


async def request_switch_to_email(db: AsyncSession, user: User) -> IssuedLink:
    """A link that moves ``user`` from BankID to e-mail and password.

    Mailed to the address on the account and to no other: the request takes
    none, so a session cannot point the link at an inbox of its choosing.
    Raises :class:`IdentityNotFoundError` for an account that does not sign in
    with BankID, which has nothing to leave, and
    :class:`AccountMailRateLimitedError` past the hourly cap, which a signed-in
    caller is told plainly.
    """
    if not await is_bankid_account(db, user.id):
        raise IdentityNotFoundError(
            "kontoen logger ikke inn med BankID, så det er ingenting å bytte fra"
        )
    return await issue_link(db, user, EmailTokenPurpose.SWITCH_TO_PASSWORD)


async def complete_switch_to_email(db: AsyncSession, *, token: str, new_password: str) -> User:
    """Set the password a switch link was for, and remove BankID from the account.

    In that order of proof and in one transaction: the link shows the inbox is
    the person's, the password becomes the way in, and the BankID link is
    deleted, so there is no moment when the account has both or neither. The
    address is stamped confirmed, the lockout lifted and every password link
    retired, as for a reset. Revoking every session is the API layer's job, as
    it is there.

    A link for an account that no longer signs in with BankID is refused like
    any unusable link: there is nothing left for it to do, and a link that set
    a password on an ordinary account would be a reset nobody asked for.
    """
    validate_password(new_password)
    user = await _redeem(db, token, EmailTokenPurpose.SWITCH_TO_PASSWORD)
    if not await is_bankid_account(db, user.id):
        raise InvalidEmailTokenError("lenken er ugyldig, allerede brukt eller utløpt")
    await db.execute(
        delete(UserIdentity).where(
            UserIdentity.user_id == user.id, UserIdentity.provider == IdentityProvider.BANKID
        )
    )
    user.password_hash = await hashed(new_password)
    if user.email_verified_at is None:
        user.email_verified_at = utc_now()
    user.failed_login_attempts = 0
    user.locked_until = None
    for purpose in (EmailTokenPurpose.SWITCH_TO_PASSWORD, EmailTokenPurpose.RESET_PASSWORD):
        await _retire_live_links(db, user.id, purpose)
    await db.flush()
    return user
