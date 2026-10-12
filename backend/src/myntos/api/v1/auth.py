"""Sign-up, sign-in and sign-out.

Several ways in, one account model behind them: an email/password pair, a
Google or Microsoft account, or BankID through a broker. Every way ends with a
session cookie and a membership in at least one organization — signing up
creates one, and an invitation link joins an existing one.

This is Myntos' own login. It says nothing to Skatteetaten: submitting an
MVA-melding still requires a separate ID-porten login by a real person
(:mod:`myntos.auth.idporten`), and that holds for a BankID sign-in too, which is
bought from a broker and is not ID-porten. ID-porten itself is never a way in
here: Digdir's terms do not allow it as a private service's own login.

An account that signs in with BankID signs in with nothing else, because
BankID's terms forbid using a BankID login to set up or restore a sign-in of our
own (docs/integrations/bankid.md). That is why the BankID link starts with a
POST that asks for the password, why a reset request for a BankID account gets
a notice instead of a link, and why leaving BankID goes through a mailed link
(:func:`confirm_switch_to_email`).
"""

import logging
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from myntos.api.deps import (
    CurrentSession,
    CurrentUser,
    DbSession,
    OptionalSession,
    OptionalUser,
    OutgoingMail,
    clear_session_cookie,
    set_session_cookie,
)
from myntos.api.errors import error_code
from myntos.auth import agent_keys
from myntos.auth.oidc import (
    OidcClient,
    OidcError,
    OidcProfile,
    ProviderNotConfiguredError,
    configured_providers,
    get_oidc_client,
    new_pkce_pair,
)
from myntos.auth.oidc.providers import LABELS
from myntos.auth.sessions import issue_session, revoke_all_sessions, revoke_session
from myntos.auth.tokens import generate_token
from myntos.core.clock import as_utc, utc_now
from myntos.core.config import get_settings
from myntos.core.mail import Delivery, Mail, Mailer
from myntos.domain.identity import email_tokens, provider_signup, service
from myntos.domain.identity.access import permissions_for
from myntos.domain.identity.account_mail import (
    bankid_sign_in_mail,
    password_reset_mail,
    switched_to_bankid_mail,
    verification_mail,
)
from myntos.domain.identity.email_tokens import BankidNotice, IssuedLink
from myntos.domain.identity.errors import (
    BankidAccountError,
    EmailDeliveryUnavailableError,
    IdentityAlreadyLinkedError,
    IdentityError,
    NoLinkedAccountError,
)
from myntos.domain.identity.models import (
    AgentKeyStop,
    IdentityProvider,
    OAuthIntent,
    OAuthLoginAttempt,
    OrganizationMembership,
    User,
    UserIdentity,
    UserSession,
)

router = APIRouter(prefix="/auth", tags=["auth"])

logger = logging.getLogger(__name__)

# The redirect to Google and back has to complete in one sitting; anything older
# than this is an abandoned attempt (or a replay).
OAUTH_ATTEMPT_LIFETIME = timedelta(minutes=15)


class UserResponse(BaseModel):
    id: int
    email: EmailStr
    full_name: str
    email_verified: bool
    # Which ways this account can sign in. The frontend needs it to decide
    # whether to offer "change password" or "set a password".
    has_password: bool
    providers: list[IdentityProvider]


class MembershipResponse(BaseModel):
    """The caller's standing on one account.

    Deliberately thin, and it used to be the centre of everything. The account
    is not shown to anybody any more: what a person may do with a set of books
    comes back with the books (``role`` and ``permissions`` on
    :class:`~myntos.api.v1.companies.CompanyResponse`), and what those books
    cost is on them too.

    What is left is the pair the frontend genuinely needs: which account new
    companies would be added to, and whether this caller may add them. The
    name is not here on purpose. An account is routinely called after the first
    company set up under it, and a name like that printed anywhere near a
    different company reads as that company's data having crossed over.
    """

    organization_id: int
    role: str
    permissions: list[str]


class SessionResponse(BaseModel):
    """What the frontend needs after a successful sign-in."""

    user: UserResponse
    memberships: list[MembershipResponse]
    # Populated only when the caller asked for a bearer token. Browser clients
    # get the httpOnly cookie instead and must never see the token in a body
    # their own scripts can read.
    session_token: str | None = None


class RegisterRequest(BaseModel):
    email: EmailStr
    # No length bound here: the password policy lives in
    # myntos.auth.passwords.validate_password, so its message is the same
    # wherever a password is set.
    password: str
    full_name: str = Field(min_length=1, max_length=255)
    # Optional: signup creates an organization either way, and most users have
    # no name in mind for it yet.
    organization_name: str | None = Field(default=None, max_length=255)
    issue_bearer_token: bool = False


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    issue_bearer_token: bool = False


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class AcceptInvitationRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)


class InvitationLookupResponse(BaseModel):
    """What a live invitation says about itself, for the welcome page."""

    # The books being shared, which is what an invitation is for. Never the
    # account: the invitee gets access to this company and to nothing else on
    # it.
    company_name: str
    # The address the invitation was sent to. Shown so the recipient knows which
    # account to sign in with — the acceptance is refused for any other.
    email: EmailStr
    role: str
    expires_at: datetime
    # None when the person who sent it has since deleted their account.
    inviter_name: str | None = None
    # Who the inviter addressed it to, when they named somebody. Offered as the
    # name on the account being created, so a person invited by name does not
    # retype what the invitation already says.
    full_name: str | None = None


class ProviderResponse(BaseModel):
    """One federated sign-in method the interface may offer."""

    # Matches the path segment in /auth/{code}/start.
    code: str
    label: str


class ProvidersResponse(BaseModel):
    """Which sign-in methods this deployment offers."""

    password: bool
    # The policy's own number, so the sign-up form can state the rule instead of
    # repeating a literal that drifts the day the setting changes.
    password_min_length: int
    providers: list[ProviderResponse]
    # Whether this deployment sends mail at all. Address confirmation and
    # "forgot your password" are both a link only an inbox can carry, so the
    # interface offers neither when this is false, rather than offering a
    # button that can only fail.
    email_delivery: bool = False


class EmailTokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    # No length bound here, for the reason RegisterRequest gives.
    new_password: str


class MailSentResponse(BaseModel):
    """Whether a message the caller asked for actually left."""

    sent: bool


def _user_response(user: User, providers: list[IdentityProvider]) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        email_verified=user.email_verified,
        has_password=user.has_password,
        providers=providers,
    )


def _membership_response(membership: OrganizationMembership) -> MembershipResponse:
    return MembershipResponse(
        organization_id=membership.organization_id,
        role=membership.role.value,
        permissions=sorted(permission.value for permission in permissions_for(membership.role)),
    )


async def _session_response(
    db: DbSession, user: User, *, session_token: str | None = None
) -> SessionResponse:
    memberships = await service.list_memberships(db, user.id)
    # Queried rather than reached through ``user.identities``: a lazy load on an
    # AsyncSession raises, and eager-loading it on every request that only needs
    # the user is waste.
    providers = list(
        await db.scalars(select(UserIdentity.provider).where(UserIdentity.user_id == user.id))
    )
    return SessionResponse(
        user=_user_response(user, providers),
        memberships=[_membership_response(membership) for membership in memberships],
        session_token=session_token,
    )


async def _sign_in(
    db: DbSession, request: Request, response: Response, user: User, *, want_token: bool
) -> SessionResponse:
    """Issue a session for ``user`` and build the response body."""
    issued = await issue_session(
        db,
        user,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.client.host if request.client else None,
    )
    set_session_cookie(response, issued.token)
    return await _session_response(db, user, session_token=issued.token if want_token else None)


@router.get("/providers")
async def providers(post: OutgoingMail) -> ProvidersResponse:
    """Report the configured sign-in methods, so the UI can hide what is absent.

    Deliberately unauthenticated: the login page has to render before anyone is
    signed in. It reveals only which buttons exist, never a client id.
    """
    return ProvidersResponse(
        password=True,
        password_min_length=get_settings().password_min_length,
        providers=[
            ProviderResponse(code=provider.value, label=label)
            for provider, label in configured_providers()
        ],
        # Asked of the mailer the endpoints will actually use, not of the
        # settings, so this can never promise a button the send then refuses.
        email_delivery=post.configured,
    )


def _frontend_link(path: str, token: str) -> str:
    """A link to a frontend page carrying ``token`` in the fragment.

    The fragment rather than the query string, unlike the older invitation
    link: a fragment never leaves the browser, so the token is not in the
    frontend host's access log, not in a Referer header, and not in anything a
    proxy records. A reset link is a password for thirty minutes, and the page
    reads it from ``location.hash`` and posts it, which is all it needs.
    """
    return f"{get_settings().frontend_base_url.rstrip('/')}{path}#token={token}"


def _verification_message(link: IssuedLink) -> Mail:
    return verification_mail(
        to=link.user.email,
        full_name=link.user.full_name,
        url=_frontend_link("/bekreft-epost/", link.token),
        expires_at=as_utc(link.row.expires_at),
    )


async def _send_quietly(post: Mailer, mail: Mail) -> Delivery:
    """Send from a background task, where nobody is waiting for the answer.

    The mailers already log and return every failure rather than raising, so
    this exists only to give the task a name in a traceback.
    """
    return await post.send(mail)


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: DbSession,
    post: OutgoingMail,
    background: BackgroundTasks,
) -> SessionResponse:
    """Create an account with a password, plus the organization that owns its data.

    When the deployment sends mail, a confirmation link goes to the address as
    well. Off the request: signing up must not wait on a mail relay, and the
    account works either way (see :mod:`myntos.domain.identity.email_tokens`
    for what confirming changes).
    """
    user, _ = await service.register_with_password(
        db,
        email=payload.email,
        full_name=payload.full_name,
        password=payload.password,
        organization_name=payload.organization_name,
    )
    if post.configured:
        link = await email_tokens.issue_verification_link(db, user)
        background.add_task(_send_quietly, post, _verification_message(link))
    return await _sign_in(db, request, response, user, want_token=payload.issue_bearer_token)


@router.post("/login")
async def login(
    payload: LoginRequest, request: Request, response: Response, db: DbSession
) -> SessionResponse:
    user = await service.authenticate_with_password(
        db, email=payload.email, password=payload.password
    )
    return await _sign_in(db, request, response, user, want_token=payload.issue_bearer_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(session: CurrentSession, response: Response) -> None:
    revoke_session(session)
    clear_session_cookie(response)


@router.get("/me")
async def me(user: CurrentUser, db: DbSession) -> SessionResponse:
    return await _session_response(db, user)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest, user: CurrentUser, session: CurrentSession, db: DbSession
) -> None:
    """Change the password and end every other session.

    Ending the others is the point of a password change after a suspected
    compromise: leaving them alive would mean the attacker keeps their access
    while the user believes they just locked them out.

    Refused with 409 ``bankid_account`` for an account that signs in with
    BankID, which has no password and may not be given one this way.
    """
    await service.change_password(
        db, user, current=payload.current_password, new=payload.new_password
    )
    await revoke_all_sessions(db, user.id, except_session_id=session.id)


# --- The address: confirming it, and getting back in through it ---------------
#
# What an unconfirmed account may do is deliberately almost everything. It signs
# in, keeps books, invites and accepts invitations exactly as before this
# existed, because every password account made before it is unconfirmed and
# none of them may be locked out by a feature they never had the chance to use.
# What it does not get is its address vouched for to somebody else: an
# invitation from an unconfirmed account carries no Reply-To (see
# myntos.api.v1.members), because a reply should never go to an address nobody
# has shown is theirs.


@router.post("/email/verify", status_code=status.HTTP_204_NO_CONTENT)
async def verify_email(payload: EmailTokenRequest, db: DbSession) -> None:
    """Confirm an address with the link that was mailed to it.

    No session required and none issued: the link may be opened in another
    browser, or fetched by a mail scanner, and neither should end up signed in.
    POST with the token in the body, for the reason the invitation lookup gives.
    """
    await email_tokens.verify_email(db, token=payload.token)


@router.post("/email/verification", status_code=status.HTTP_202_ACCEPTED)
async def send_verification(
    user: CurrentUser, db: DbSession, post: OutgoingMail
) -> MailSentResponse:
    """Mail the signed-in account a fresh confirmation link.

    Inline, unlike the one sent at signup: the person pressed a button and is
    waiting to hear whether it went, which is the invitation's reasoning. The
    previous link stops working, so only the newest message is live.
    """
    if not post.configured:
        raise EmailDeliveryUnavailableError("denne installasjonen sender ikke e-post")
    link = await email_tokens.issue_verification_link(db, user)
    delivery = await post.send(_verification_message(link))
    return MailSentResponse(sent=delivery.sent)


@router.post("/password/forgot", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    payload: ForgotPasswordRequest,
    db: DbSession,
    post: OutgoingMail,
    background: BackgroundTasks,
) -> None:
    """Mail a reset link to ``email`` if it has an account. Never say whether it does.

    The same 202 and the same empty body for an address with an account, one
    without, a deactivated one and one that has hit the hourly cap. The message
    is sent in a background task, after the response, so the time a relay takes
    to answer is not a second way of saying the same thing. The remaining
    difference is spelled out in
    :func:`myntos.domain.identity.email_tokens.request_password_reset`.

    A deployment that sends no mail answers 503 before looking anything up:
    that is a fact about the deployment, the same for every address.

    An account that signs in with BankID gets the same 202 and a message that
    says so, with a link to the login page and nothing that sets a password
    (:func:`myntos.domain.identity.account_mail.bankid_sign_in_mail`).
    """
    if not post.configured:
        raise EmailDeliveryUnavailableError("denne installasjonen sender ikke e-post")
    link = await email_tokens.request_password_reset(db, email=payload.email)
    if link is None:
        return
    if isinstance(link, BankidNotice):
        background.add_task(
            _send_quietly,
            post,
            bankid_sign_in_mail(
                to=link.user.email,
                full_name=link.user.full_name,
                url=_frontend_url("/logg-inn/"),
            ),
        )
        return
    background.add_task(
        _send_quietly,
        post,
        password_reset_mail(
            to=link.user.email,
            full_name=link.user.full_name,
            url=_frontend_link("/nytt-passord/", link.token),
            minutes=get_settings().password_reset_lifetime_minutes,
        ),
    )


@router.post("/password/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(payload: ResetPasswordRequest, db: DbSession) -> None:
    """Set a new password with a reset link, and sign the account out everywhere.

    Every session ends, with no exception for the caller's own: unlike a
    password change, nobody here has proved they were the one signed in. A
    reset is what somebody does when they think another person has their
    password, and leaving that person's session alive would make it theatre.
    No session is issued either; the person signs in with the new password,
    which is also the proof that they typed what they meant to.

    A link for an account that signs in with BankID is refused with 409
    ``bankid_account``, however it came to exist.
    """
    user = await email_tokens.reset_password(
        db, token=payload.token, new_password=payload.new_password
    )
    await revoke_all_sessions(db, user.id)
    # And every key, for the same reason: whoever had the password could have
    # made one, and a key outlives the session it was made in.
    await agent_keys.stop_keys(db, reason=AgentKeyStop.PASSWORD_RESET, user_id=user.id)


@router.post("/switch-to-email/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_switch_to_email(payload: ResetPasswordRequest, db: DbSession) -> None:
    """Finish moving a BankID account to e-mail and password, with the mailed link.

    The link from ``POST /account/switch-to-email``, opened at /bytt-til-epost/.
    This, and not the request, is when BankID stops working: the password is
    set, the BankID link deleted and the address confirmed together, because
    the link is the proof that the inbox is the person's, and BankID must not
    be what makes the password (docs/integrations/bankid.md).

    Every session ends, the caller's included, for the reason a reset gives:
    nobody here proved they were the one signed in, and the sessions BankID
    opened were opened by a way in that no longer exists. No session is issued;
    the person signs in with the new password. Same body as a reset, because
    it is the same question: which link, and which password.
    """
    user = await email_tokens.complete_switch_to_email(
        db, token=payload.token, new_password=payload.new_password
    )
    await revoke_all_sessions(db, user.id)


@router.post("/invitations/lookup")
async def lookup_invitation(
    payload: AcceptInvitationRequest, db: DbSession
) -> InvitationLookupResponse:
    """Describe an invitation without accepting it, and without a session.

    The page an invitation link opens has to say who is asking and into what
    before it asks anybody to create an account. Everything it answers is
    already in the email the reader is holding, so the token buys no information
    it did not carry; an unusable token gets one refusal for every reason, so a
    guessed one cannot be told from a spent one.

    POST rather than GET, with the token in the body, for the reason the
    acceptance is a POST: a secret in a query string ends up in access logs,
    browser history and Referer headers.
    """
    described = await service.describe_invitation(db, token=payload.token)
    return InvitationLookupResponse(
        company_name=described.company_name,
        email=described.email,
        role=described.role.value,
        expires_at=described.expires_at,
        inviter_name=described.inviter_name,
        full_name=described.full_name,
    )


@router.post("/invitations/accept")
async def accept_invitation(
    payload: AcceptInvitationRequest, user: CurrentUser, db: DbSession
) -> SessionResponse:
    """Take up an invitation to one company's books.

    Requires being signed in: the invitation grants access *to an account of
    your own*, so that account has to exist first. A recipient without one
    registers (or signs in with Google) using the invited address, then comes
    back here.

    What it grants is the invited company and nothing else. The invitee does
    not join the sender's account, cannot see its other companies, and cannot
    add companies to it.
    """
    await service.accept_invitation(db, token=payload.token, user=user)
    return await _session_response(db, user)


# --- Federated sign-in (Google, Microsoft, BankID) -------------------------------


def _resolve_provider(provider: str) -> IdentityProvider:
    """Turn the path segment into a provider, or 404.

    404 rather than 422: ``/auth/nonsense/start`` is simply not a route this
    deployment serves, and a validation error would invite probing for which
    names exist.
    """
    try:
        return IdentityProvider(provider)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="ukjent innloggingsmetode"
        ) from error


def _client_for(provider: str) -> OidcClient:
    """Resolve a provider's client, or answer 503 rather than 500.

    A dependency rather than a direct call, so tests can substitute a stub and
    an unconfigured deployment reports "this sign-in method is unavailable"
    instead of an internal error.
    """
    try:
        return get_oidc_client(_resolve_provider(provider))
    except ProviderNotConfiguredError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error


ProviderClient = Annotated[OidcClient, Depends(_client_for)]


def _safe_redirect_path(candidate: str | None) -> str | None:
    """Keep only site-relative paths.

    ``/dashboard`` is fine; ``https://evil.example`` and ``//evil.example`` are
    not. Without this the sign-in endpoint would be an open redirect that
    borrows our domain's credibility — and one that carries a fresh session
    cookie at the moment it fires.
    """
    if candidate is None:
        return None
    if not candidate.startswith("/") or candidate.startswith(("//", "/\\")):
        return None
    return candidate[:512]


@router.get("/{provider}/start")
async def provider_start(
    provider: str,
    client: ProviderClient,
    db: DbSession,
    signed_in: OptionalUser,
    redirect_to: str | None = None,
    intent: OAuthIntent = OAuthIntent.LOGIN,
) -> RedirectResponse:
    """Begin a federated sign-in flow, a BankID signup, or a link from /konto/.

    The browser is sent to the provider; ``state``, ``nonce`` and the PKCE
    verifier are parked in the database so the callback can be matched to this
    attempt — and to this provider, so a callback cannot be replayed against a
    different one.

    ``intent=link`` needs somebody signed in, and records who: the callback
    attaches the identity to that account and to no other. Without a session
    it goes back to /konto/, which sends a signed-out visitor to the login page.

    Two links are refused here rather than at the callback, so nobody is sent
    through a provider only to be told no. **BankID**, because linking it
    removes the password and every other way in, and a GET is something anybody
    can put in front of a signed-in browser: it starts from
    :func:`start_bankid_link` instead, which asks for the password first. And
    **anything on a BankID account**, which signs in with BankID and nothing
    else.
    """
    identity_provider = _resolve_provider(provider)
    owner_id: int | None = None
    if intent is OAuthIntent.LINK:
        if signed_in is None:
            return _failed("innlogging_utlopt", _FAILURE_PAGE[intent])
        if identity_provider is IdentityProvider.BANKID:
            return _failed("bytte_ikke_bekreftet", _FAILURE_PAGE[intent])
        if await service.is_bankid_account(db, signed_in.id):
            return _failed(error_code(BankidAccountError), _FAILURE_PAGE[intent])
        owner_id = signed_in.id

    try:
        url = await _begin(
            db,
            client,
            identity_provider,
            intent=intent,
            owner_id=owner_id,
            redirect_to=redirect_to,
        )
    except OidcError:
        # The provider's discovery document could not be read. Our problem,
        # not the person's, and said so rather than shown as a 500.
        logger.exception("could not start a %s sign-in", identity_provider.value)
        return _failed("innlogging_ikke_mulig", _FAILURE_PAGE[intent])
    # 303: the browser must follow this with GET regardless of how it arrived.
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


async def _begin(
    db: DbSession,
    client: OidcClient,
    provider: IdentityProvider,
    *,
    intent: OAuthIntent,
    owner_id: int | None,
    redirect_to: str | None,
) -> str:
    """Park a new attempt and return the provider URL to send the browser to.

    Raises :class:`OidcError` when the provider's discovery document cannot be
    read; what to answer then depends on whether a browser is being redirected
    or a page is waiting for JSON.
    """
    pkce = new_pkce_pair()
    now = utc_now()
    attempt = OAuthLoginAttempt(
        provider=provider,
        state=generate_token(),
        nonce=generate_token(),
        code_verifier=pkce.verifier,
        redirect_to=_safe_redirect_path(redirect_to),
        created_at=now,
        expires_at=now + OAUTH_ATTEMPT_LIFETIME,
        intent=intent,
        user_id=owner_id,
    )
    db.add(attempt)
    await db.flush()
    return await client.authorization_url(
        state=attempt.state, nonce=attempt.nonce, code_challenge=pkce.challenge
    )


class BankidLinkRequest(BaseModel):
    # Required when the account has a password, ignored when it has none, as
    # for deleting the account: see myntos.domain.identity.service.confirm_identity.
    password: str | None = Field(default=None, max_length=1024)
    # Where the browser goes once the switch is done. Kept only if site-relative.
    redirect_to: str | None = Field(default=None, max_length=512)


class AuthorizationUrlResponse(BaseModel):
    """Where to send the browser next: the provider's own page."""

    authorization_url: str


def _bankid_client() -> OidcClient:
    """BankID's client, answering 503 when the deployment has none."""
    return _client_for(IdentityProvider.BANKID.value)


BankidClient = Annotated[OidcClient, Depends(_bankid_client)]


@router.post("/bankid/link")
async def start_bankid_link(
    payload: BankidLinkRequest,
    user: CurrentUser,
    session: CurrentSession,
    db: DbSession,
    client: BankidClient,
) -> AuthorizationUrlResponse:
    """Begin switching the signed-in account to BankID, once its owner has proved it.

    Linking BankID is a switch: when the callback completes, the password and
    every other provider are gone and every other session is ended, and from
    then on the account signs in with BankID only (docs/integrations/bankid.md).
    The page says so before this is called and asks for a confirmation.

    The proof is the deletion's, because the stakes are: a hijacked session
    that could do this would link its own BankID, wipe the owner's password and
    sign the owner out everywhere, and the account would be the attacker's for
    good. So the password is required on an account that has one, and a login
    in the last ten minutes on one that does not. A POST, so the cookie's
    origin check applies, which a GET to ``/auth/bankid/start`` would not.

    Answers with the BankID URL for the page to send the browser to, rather
    than a redirect a fetch would follow invisibly. Refused with 409
    ``identity_already_linked`` for an account that signs in with BankID already.
    """
    if await service.is_bankid_account(db, user.id):
        raise IdentityAlreadyLinkedError("kontoen logger allerede inn med BankID")
    await service.confirm_identity(
        db, user, password=payload.password, session=session, action="bytte til BankID"
    )
    try:
        url = await _begin(
            db,
            client,
            IdentityProvider.BANKID,
            intent=OAuthIntent.LINK,
            owner_id=user.id,
            redirect_to=payload.redirect_to,
        )
    except OidcError as error:
        logger.exception("could not start a bankid link")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "innloggingen med BankID kunne ikke starte. Det er en feil hos oss, ikke hos deg."
            ),
        ) from error
    return AuthorizationUrlResponse(authorization_url=url)


def _frontend_url(path: str) -> str:
    return f"{get_settings().frontend_base_url.rstrip('/')}{path}"


# Where a failed flow goes back to, by what it was started for: the page with
# the button that was pressed, so the message is read where it makes sense.
_FAILURE_PAGE: dict[OAuthIntent, str] = {
    OAuthIntent.LOGIN: "/logg-inn/",
    OAuthIntent.SIGNUP: "/registrer/",
    OAuthIntent.LINK: "/konto/",
}


def _failed(marker: str, page: str = "/logg-inn/") -> RedirectResponse:
    """Send the browser back to ``page`` with a fixed error marker.

    Never the provider's own message: that text is attacker-influenced and
    would end up reflected in our page.
    """
    return RedirectResponse(
        _frontend_url(f"{page}?error={marker}"), status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/{provider}/callback")
async def provider_callback(
    provider: str,
    client: ProviderClient,
    request: Request,
    db: DbSession,
    signed_in: OptionalUser,
    current: OptionalSession,
    post: OutgoingMail,
    background: BackgroundTasks,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Finish a federated flow and redirect into the frontend.

    A browser is looking at this URL, so failures redirect to the page the flow
    started from with a marker instead of returning JSON nobody will render.
    """
    identity_provider = _resolve_provider(provider)

    # Looked up before anything is decided, because the attempt is what knows
    # which page the flow started on, and a refusal belongs there.
    attempt = None
    if state is not None:
        attempt = await db.scalar(
            select(OAuthLoginAttempt).where(
                OAuthLoginAttempt.state == state,
                # Matched on the provider too: an attempt started for one
                # provider must not be completable with another's code.
                OAuthLoginAttempt.provider == identity_provider,
            )
        )
    page = _FAILURE_PAGE[attempt.intent] if attempt is not None else "/logg-inn/"

    if error is not None or code is None or state is None:
        return _failed("innlogging_avbrutt", page)

    now = utc_now()
    if attempt is None or attempt.consumed_at is not None or as_utc(attempt.expires_at) <= now:
        return _failed("innlogging_utlopt", page)

    # Stamped before the exchange, not after: a replayed callback URL must fail
    # even if the first exchange is still in flight or crashed halfway.
    attempt.consumed_at = now
    await db.flush()

    try:
        profile = await client.exchange_code(
            code=code, code_verifier=attempt.code_verifier, nonce=attempt.nonce
        )
    except OidcError:
        logger.warning("a %s sign-in failed at the token exchange", identity_provider.value)
        return _failed("innlogging_feilet", page)

    try:
        if attempt.intent is OAuthIntent.LINK:
            return await _finish_link(
                db, attempt, identity_provider, profile, signed_in, current, post, background
            )
        if client.asserts_email:
            user, _created = await service.sign_in_with_provider(db, identity_provider, profile)
        else:
            linked = await service.sign_in_with_linked_identity(
                db, identity_provider, profile.subject
            )
            if linked is None:
                return await _unlinked(db, attempt, identity_provider, profile)
            user = linked
    except IdentityError as rejected:
        # A refusal the user needs to understand — an unverified provider email
        # on an address that already has an account, a Google login whose
        # address is a BankID account's, a BankID that belongs to somebody
        # else, or a deactivated account. The marker is our own error code, so
        # nothing from the provider is reflected into the page.
        return _failed(error_code(type(rejected)), page)

    issued = await issue_session(
        db,
        user,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.client.host if request.client else None,
    )
    response = RedirectResponse(
        _frontend_url(attempt.redirect_to or "/"), status_code=status.HTTP_303_SEE_OTHER
    )
    set_session_cookie(response, issued.token)
    return response


async def _finish_link(
    db: DbSession,
    attempt: OAuthLoginAttempt,
    provider: IdentityProvider,
    profile: OidcProfile,
    signed_in: User | None,
    current: UserSession | None,
    post: Mailer,
    background: BackgroundTasks,
) -> RedirectResponse:
    """Attach the identity to the account that asked, if it is still the one here.

    The session that comes back has to belong to the account that started the
    flow. Without that check, a link started on one account and completed
    in somebody else's browser would attach *their* BankID to the first
    account, and from then on their BankID login would open books that are not
    theirs, under a name they would recognise as their own. No new session is
    issued: the person was signed in already, and still is.

    When the link was BankID, the account has just become a BankID account
    (:func:`myntos.domain.identity.service.link_provider`), and two things
    follow here, where sessions and mail live. Every other session ends: each
    was opened with a password or a provider that no longer works, and a
    switch is exactly what somebody would do to lock the owner out. And the
    account's address is told, after the response, because the person who did
    not do this has to hear it somewhere other than a login page that no
    longer lets them in.
    """
    if signed_in is None or current is None or signed_in.id != attempt.user_id:
        return _failed("kobling_annen_konto", _FAILURE_PAGE[OAuthIntent.LINK])
    _identity, became_bankid = await service.link_provider(db, signed_in, provider, profile)
    if became_bankid:
        await revoke_all_sessions(db, signed_in.id, except_session_id=current.id)
        # A BankID account holds no keys until BankID says its terms allow it
        # (docs/integrations/bankid.md), so the ones it made before go too.
        await agent_keys.stop_keys(db, reason=AgentKeyStop.BANKID, user_id=signed_in.id)
        if post.configured:
            background.add_task(
                _send_quietly,
                post,
                switched_to_bankid_mail(
                    to=signed_in.email,
                    full_name=signed_in.full_name,
                    url=_frontend_url("/konto/"),
                ),
            )
    return RedirectResponse(
        _frontend_url(attempt.redirect_to or "/konto/"), status_code=status.HTTP_303_SEE_OTHER
    )


async def _unlinked(
    db: DbSession,
    attempt: OAuthLoginAttempt,
    provider: IdentityProvider,
    profile: OidcProfile,
) -> RedirectResponse:
    """A BankID that opens no account: refuse a login, hold a signup.

    A login button never creates an account. The signup button does, but not
    yet: BankID gave no address, so the identity is held and the browser goes
    to the page that asks for one, with the token in the fragment, where no
    server log or Referer can see it.
    """
    if attempt.intent is not OAuthIntent.SIGNUP:
        return _failed(error_code(NoLinkedAccountError), _FAILURE_PAGE[attempt.intent])
    token = await provider_signup.hold(db, provider, profile, redirect_to=attempt.redirect_to)
    return RedirectResponse(
        _frontend_url(f"/fullfor-registrering/#token={token}"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


# --- Finishing a BankID signup: the address BankID does not have ---------------
#
# The identity was held by the callback (myntos.domain.identity.provider_signup);
# these two endpoints are the page that asks for an address. Neither needs a
# session, because the token is the proof, and both take it in a POST body for
# the reason the invitation lookup gives.


class ProviderSignupLookupResponse(BaseModel):
    """What the page asking for an address says before it asks."""

    provider: IdentityProvider
    provider_label: str
    # The name the account will have, from BankID. Shown, not offered for
    # editing: that BankID vouched for it is the point.
    full_name: str
    expires_at: datetime
    # Where to go once the account exists: an invitation, typically.
    redirect_to: str | None


class CompleteProviderSignupRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    email: EmailStr
    organization_name: str | None = Field(default=None, max_length=255)
    issue_bearer_token: bool = False


@router.post("/provider-signup/lookup")
async def lookup_provider_signup(
    payload: EmailTokenRequest, db: DbSession
) -> ProviderSignupLookupResponse:
    """Describe a held BankID signup, so the page can greet the person by name."""
    held = await provider_signup.describe(db, token=payload.token)
    return ProviderSignupLookupResponse(
        provider=held.provider,
        provider_label=LABELS[held.provider],
        full_name=held.full_name,
        expires_at=held.expires_at,
        redirect_to=held.redirect_to,
    )


@router.post("/provider-signup", status_code=status.HTTP_201_CREATED)
async def complete_provider_signup(
    payload: CompleteProviderSignupRequest,
    request: Request,
    response: Response,
    db: DbSession,
    post: OutgoingMail,
    background: BackgroundTasks,
) -> SessionResponse:
    """Create the account a BankID signup was waiting for, and sign it in.

    Refused with 409 ``email_already_registered`` when the address has an
    account: that account is reached by signing in to it and linking BankID
    from /konto/, never by typing its address here. The confirmation mail goes
    out exactly as it does for a password signup.
    """
    user = await provider_signup.complete_signup(
        db,
        token=payload.token,
        email=payload.email,
        organization_name=(payload.organization_name or "").strip() or None,
    )
    if post.configured:
        link = await email_tokens.issue_verification_link(db, user)
        background.add_task(_send_quietly, post, _verification_message(link))
    return await _sign_in(db, request, response, user, want_token=payload.issue_bearer_token)
