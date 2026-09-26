"""Sign-up, sign-in and sign-out.

Two ways in, one account model behind them: an email/password pair, or a Google
account. Either way the caller ends up with a session cookie and a membership in
at least one organization — signing up creates one, and an invitation link joins
an existing one.

This is Myntos' own login. It says nothing to Skatteetaten: submitting an
MVA-melding still requires a separate ID-porten login by a real person
(:mod:`myntos.auth.idporten`).
"""

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from myntos.api.deps import (
    CurrentSession,
    CurrentUser,
    DbSession,
    clear_session_cookie,
    set_session_cookie,
)
from myntos.api.errors import error_code
from myntos.auth.oidc import (
    OidcClient,
    OidcError,
    ProviderNotConfiguredError,
    configured_providers,
    get_oidc_client,
    new_pkce_pair,
)
from myntos.auth.sessions import issue_session, revoke_all_sessions, revoke_session
from myntos.auth.tokens import generate_token
from myntos.core.clock import as_utc, utc_now
from myntos.core.config import get_settings
from myntos.domain.identity import service
from myntos.domain.identity.access import permissions_for
from myntos.domain.identity.errors import IdentityError
from myntos.domain.identity.models import (
    IdentityProvider,
    OAuthLoginAttempt,
    OrganizationMembership,
    User,
    UserIdentity,
)

router = APIRouter(prefix="/auth", tags=["auth"])

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
async def providers() -> ProvidersResponse:
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
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest, request: Request, response: Response, db: DbSession
) -> SessionResponse:
    """Create an account with a password, plus the organization that owns its data."""
    user, _ = await service.register_with_password(
        db,
        email=payload.email,
        full_name=payload.full_name,
        password=payload.password,
        organization_name=payload.organization_name,
    )
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
    """
    service.change_password(user, current=payload.current_password, new=payload.new_password)
    await revoke_all_sessions(db, user.id, except_session_id=session.id)


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


# --- Federated sign-in (Google, Microsoft, Vipps) --------------------------------


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
    redirect_to: str | None = None,
) -> RedirectResponse:
    """Begin a federated sign-in flow.

    The browser is sent to the provider; ``state``, ``nonce`` and the PKCE
    verifier are parked in the database so the callback can be matched to this
    attempt — and to this provider, so a callback cannot be replayed against a
    different one.
    """
    pkce = new_pkce_pair()
    now = utc_now()
    attempt = OAuthLoginAttempt(
        provider=_resolve_provider(provider),
        state=generate_token(),
        nonce=generate_token(),
        code_verifier=pkce.verifier,
        redirect_to=_safe_redirect_path(redirect_to),
        created_at=now,
        expires_at=now + OAUTH_ATTEMPT_LIFETIME,
    )
    db.add(attempt)
    await db.flush()

    url = await client.authorization_url(
        state=attempt.state, nonce=attempt.nonce, code_challenge=pkce.challenge
    )
    # 303: the browser must follow this with GET regardless of how it arrived.
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


def _frontend_url(path: str) -> str:
    return f"{get_settings().frontend_base_url.rstrip('/')}{path}"


def _failed(marker: str) -> RedirectResponse:
    """Send the browser back to the login page with a fixed error marker.

    Never the provider's own message: that text is attacker-influenced and
    would end up reflected in our page.
    """
    return RedirectResponse(
        _frontend_url(f"/logg-inn/?error={marker}"), status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/{provider}/callback")
async def provider_callback(
    provider: str,
    client: ProviderClient,
    request: Request,
    db: DbSession,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Finish a federated sign-in flow and redirect into the frontend.

    A browser is looking at this URL, so failures redirect to the login page
    with a marker instead of returning JSON nobody will render.
    """
    identity_provider = _resolve_provider(provider)
    if error is not None or code is None or state is None:
        return _failed("innlogging_avbrutt")

    attempt = await db.scalar(
        select(OAuthLoginAttempt).where(
            OAuthLoginAttempt.state == state,
            # Matched on the provider too: an attempt started for one provider
            # must not be completable with another's authorization code.
            OAuthLoginAttempt.provider == identity_provider,
        )
    )
    now = utc_now()
    if attempt is None or attempt.consumed_at is not None or as_utc(attempt.expires_at) <= now:
        return _failed("innlogging_utlopt")

    # Stamped before the exchange, not after: a replayed callback URL must fail
    # even if the first exchange is still in flight or crashed halfway.
    attempt.consumed_at = now
    await db.flush()

    try:
        profile = await client.exchange_code(
            code=code, code_verifier=attempt.code_verifier, nonce=attempt.nonce
        )
    except OidcError:
        return _failed("innlogging_feilet")

    try:
        user, _created = await service.sign_in_with_provider(db, identity_provider, profile)
    except IdentityError as rejected:
        # A refusal the user needs to understand — an unverified provider email
        # on an address that already has an account, or a deactivated account.
        # The marker is our own error code, so nothing from the provider is
        # reflected into the page.
        return _failed(error_code(type(rejected)))

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
