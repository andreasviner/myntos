"""The signed-in person's own account: where they are signed in, and leaving.

Everything here is about one person and none of it about a company, so nothing
takes a ``company_id`` and nothing is checked against a CompanyMembership. The
wall that matters instead is between people: every session id is looked up
with the caller's own user id beside it, so naming somebody else's is a 404.

Separate from :mod:`myntos.api.v1.auth`, which signs people in and out. These
endpoints assume somebody is already in and is managing what that means.
"""

from datetime import datetime

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, EmailStr, Field

from myntos.api.deps import (
    CurrentSession,
    CurrentUser,
    DbSession,
    OutgoingMail,
    clear_session_cookie,
)
from myntos.auth.devices import describe_device
from myntos.auth.oidc.providers import LABELS
from myntos.auth.sessions import (
    get_own_session,
    list_live_sessions,
    revoke_all_sessions,
    revoke_session,
)
from myntos.core.clock import as_utc
from myntos.core.config import get_settings
from myntos.domain.identity import email_tokens, erasure, service
from myntos.domain.identity.account_mail import switch_to_email_mail
from myntos.domain.identity.errors import EmailDeliveryUnavailableError
from myntos.domain.identity.models import IdentityProvider

router = APIRouter(prefix="/account", tags=["account"])


class SessionInfo(BaseModel):
    """One place the caller is signed in, described just enough to recognise it."""

    id: int
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    # "Firefox på Linux", or None when the browser said nothing recognisable.
    # Never the raw user agent and never the IP address: see myntos.auth.devices.
    device: str | None
    # The session this request came in on. The page marks it rather than
    # offering to end it, because ending it is signing out.
    current: bool


class CompanyOutcomeResponse(BaseModel):
    company_id: int
    company_name: str
    # leaves | deleted | blocks_members | blocks_books
    fate: str


class ErasurePlanResponse(BaseModel):
    """What deleting the account would do, for the page to say before it does."""

    companies: list[CompanyOutcomeResponse]
    # Whether the account stays as a tombstone with the name on it, because a
    # set of books names this person as the one who registered something.
    keeps_name: bool
    blocked: bool
    # How the person proves it is them: their password, or a login within the
    # last few minutes when there is no password to type.
    requires_password: bool
    # For an account without a password: until when the current login counts as
    # recent. None for an account with one.
    recent_login_until: datetime | None


class IdentityInfo(BaseModel):
    """One provider the caller can sign in with."""

    provider: IdentityProvider
    label: str
    linked_at: datetime
    last_login_at: datetime | None
    # Whether removing it would still leave a way in. Decided here, so the page
    # never offers a button the backend would refuse. Never true for BankID,
    # which is left only by switching to e-mail.
    removable: bool


class SwitchToEmailResponse(BaseModel):
    """Whether the link left, and for which address, so the page can say where."""

    sent: bool
    email: EmailStr


class EraseRequest(BaseModel):
    # Required when the account has a password, ignored when it has none. Not
    # validated against the password policy: an old password set under a
    # different policy must still be accepted as proof.
    password: str | None = Field(default=None, max_length=1024)


@router.get("/sessions")
async def sessions(user: CurrentUser, session: CurrentSession, db: DbSession) -> list[SessionInfo]:
    """Every session that could still be used to act as the caller."""
    return [
        SessionInfo(
            id=row.id,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            expires_at=row.expires_at,
            device=describe_device(row.user_agent),
            current=row.id == session.id,
        )
        for row in await list_live_sessions(db, user.id)
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def end_session(
    session_id: int,
    user: CurrentUser,
    session: CurrentSession,
    db: DbSession,
    response: Response,
) -> None:
    """Sign out one device. Ending the one in hand is an ordinary sign-out."""
    target = await get_own_session(db, user_id=user.id, session_id=session_id)
    revoke_session(target)
    if target.id == session.id:
        clear_session_cookie(response)


@router.post("/sessions/revoke-others", status_code=status.HTTP_204_NO_CONTENT)
async def end_other_sessions(user: CurrentUser, session: CurrentSession, db: DbSession) -> None:
    """Sign out everywhere except here.

    The one in hand is spared because the person pressing the button is the
    one who should still be signed in afterwards; "everywhere including here"
    is the sign-out button followed by this one.
    """
    await revoke_all_sessions(db, user.id, except_session_id=session.id)


@router.get("/identities")
async def identities(user: CurrentUser, db: DbSession) -> list[IdentityInfo]:
    """The providers linked to the caller's account, and which of them may go.

    Linking one is not here: it is a trip through the provider, which starts at
    ``/auth/{provider}/start?intent=link`` with the caller's session.
    """
    rows = await service.list_identities(db, user.id)
    return [
        IdentityInfo(
            provider=row.provider,
            label=LABELS[row.provider],
            linked_at=as_utc(row.created_at),
            last_login_at=as_utc(row.last_login_at) if row.last_login_at else None,
            removable=service.can_remove_identity(user, rows, row),
        )
        for row in rows
    ]


@router.delete("/identities/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_identity(provider: IdentityProvider, user: CurrentUser, db: DbSession) -> None:
    """Stop letting a provider sign in to this account.

    Refused with 409 ``last_sign_in_method`` when it is the only way in left,
    and with 409 ``bankid_account`` for BankID, always: on a BankID account it
    is the only way in by rule, and the way out is
    ``POST /account/switch-to-email``. Sessions it opened are left alone: they
    are the person's own, on their own devices, and the session list beside
    this is where to end them.
    """
    await service.unlink_provider(db, user, provider)


@router.post("/switch-to-email", status_code=status.HTTP_202_ACCEPTED)
async def switch_to_email(
    user: CurrentUser, db: DbSession, post: OutgoingMail
) -> SwitchToEmailResponse:
    """Mail a BankID account the link that moves it to e-mail and password.

    Nothing changes yet. BankID goes on working until the link is opened and a
    password chosen (``POST /auth/switch-to-email/confirm``); asking and not
    following it up leaves the account as it was. The link goes to the address
    on the account, which this takes no other of, so a session cannot point it
    at an inbox of its own: the inbox is the proof, and BankID is only what let
    the person ask (docs/integrations/bankid.md).

    Inline, unlike the signup mail, because the person pressed a button and is
    waiting to hear whether it went. Refused with 404 ``identity_not_found`` for
    an account that does not sign in with BankID, 429 past the hourly cap, and
    503 on a deployment that sends no mail.
    """
    if not post.configured:
        raise EmailDeliveryUnavailableError("denne installasjonen sender ikke e-post")
    link = await email_tokens.request_switch_to_email(db, user)
    settings = get_settings()
    delivery = await post.send(
        switch_to_email_mail(
            to=user.email,
            full_name=user.full_name,
            # In the fragment, as every mailed link is: see auth._frontend_link.
            url=f"{settings.frontend_base_url.rstrip('/')}/bytt-til-epost/#token={link.token}",
            minutes=settings.password_reset_lifetime_minutes,
        )
    )
    return SwitchToEmailResponse(sent=delivery.sent, email=user.email)


@router.get("/erasure")
async def erasure_plan(
    user: CurrentUser, session: CurrentSession, db: DbSession
) -> ErasurePlanResponse:
    """Say what deleting the account would delete, keep and refuse, before asking."""
    plan = await erasure.plan_erasure(db, user)
    return ErasurePlanResponse(
        companies=[
            CompanyOutcomeResponse(
                company_id=outcome.company_id,
                company_name=outcome.company_name,
                fate=outcome.fate.value,
            )
            for outcome in plan.companies
        ],
        keeps_name=plan.keeps_name,
        blocked=plan.blocked,
        requires_password=user.has_password,
        recent_login_until=(
            None if user.has_password else as_utc(session.created_at) + service.RECENT_LOGIN_WINDOW
        ),
    )


@router.post("/erasure", status_code=status.HTTP_204_NO_CONTENT)
async def erase(
    payload: EraseRequest,
    user: CurrentUser,
    session: CurrentSession,
    db: DbSession,
    response: Response,
) -> None:
    """Delete the account. Cannot be undone, which is why it asks for proof first.

    POST with a body rather than DELETE: the password travels in the body, and
    a DELETE with a body is something proxies and fetch implementations are
    allowed to mishandle.
    """
    await service.confirm_identity(
        db, user, password=payload.password, session=session, action="slette kontoen"
    )
    await erasure.erase_account(db, user)
    clear_session_cookie(response)
