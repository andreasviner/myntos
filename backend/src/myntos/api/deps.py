"""Request-scoped dependencies: the database session, the caller, the cookie.

Two ways to present a session token, for two kinds of client:

- the ``myntos_session`` cookie, for the browser frontend; and
- ``Authorization: Bearer <token>``, for scripts, tests and any future CLI.

They carry the same token and are equally valid. The difference that matters is
CSRF: a cookie is attached by the browser automatically, so a cookie-authorised
state-changing request must additionally prove it came from our own frontend —
see :func:`_require_trusted_origin`. A bearer token has to be added by the
caller and is therefore immune to the same attack.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.auth.sessions import resolve_session
from myntos.core.config import get_settings
from myntos.core.mail import Mailer, build_mailer
from myntos.db.session import get_session
from myntos.domain.identity import service
from myntos.domain.identity.models import (
    CompanyMembership,
    OrganizationMembership,
    User,
    UserSession,
)
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.models import Company
from myntos.storage import DocumentStorage, get_storage

# Methods that cannot change server state, and so need no CSRF defence.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

DbSession = Annotated[AsyncSession, Depends(get_session)]


def _unauthenticated() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="ikke innlogget",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("Authorization")
    if header is None:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def _require_trusted_origin(request: Request) -> None:
    """Reject cookie-authorised writes that did not come from our frontend.

    ``SameSite=Lax`` already stops a cross-site form POST from carrying the
    cookie, so this is the second layer rather than the only one — it covers
    clients where the cookie policy is weaker than we assume, and it fails
    closed when a browser sends no ``Origin`` on a state-changing request.

    ``Origin`` is set by the browser and cannot be forged by page script, which
    is what makes it usable as evidence.
    """
    settings = get_settings()
    allowed = set(settings.cors_origins)
    if "*" in allowed:
        # An open CORS policy makes origin checking meaningless; do not pretend
        # otherwise. Such a deployment must use bearer tokens.
        return
    origin = request.headers.get("Origin")
    if origin is None or origin not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="forespørselen kom fra et ukjent opphav",
        )


async def get_current_session(request: Request, db: DbSession) -> UserSession:
    """The caller's live session, or 401."""
    settings = get_settings()
    token = _bearer_token(request)
    from_cookie = False
    if token is None:
        token = request.cookies.get(settings.session_cookie_name)
        from_cookie = token is not None
    if not token:
        raise _unauthenticated()

    if from_cookie and request.method not in SAFE_METHODS:
        _require_trusted_origin(request)

    session = await resolve_session(db, token)
    if session is None:
        raise _unauthenticated()
    return session


CurrentSession = Annotated[UserSession, Depends(get_current_session)]


async def get_current_user(session: CurrentSession, db: DbSession) -> User:
    user = await db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise _unauthenticated()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_membership(
    organization_id: int, user: CurrentUser, db: DbSession
) -> OrganizationMembership:
    """The caller's membership in the path's organization.

    Raises :class:`~myntos.domain.identity.errors.NotAMemberError`, which the
    error handler renders as 404 — a non-member gets the same answer whether or
    not the organization exists.
    """
    return await service.get_membership(db, organization_id=organization_id, user_id=user.id)


CurrentMembership = Annotated[OrganizationMembership, Depends(get_current_membership)]


@dataclass(frozen=True)
class CompanyContext:
    """A company the caller may see, plus the role they hold over it.

    The two always travel together: no endpoint should be able to load a
    company without having also resolved what the caller is allowed to do with
    it, because that is exactly the pair a missing check separates.

    ``membership`` is a :class:`CompanyMembership`, one row per person per
    company. Access is not account-wide: somebody invited to help with one
    client's books has a row for that company and nothing for the twenty next
    to it, and can hold a different role on each.
    """

    company: Company
    membership: CompanyMembership


async def get_company_context(company_id: int, user: CurrentUser, db: DbSession) -> CompanyContext:
    """Resolve the path's company through the caller's company memberships.

    Raises :class:`~myntos.domain.ledger.errors.CompanyNotFoundError` — 404 —
    both when the company does not exist and when the caller has no membership
    on it. The same answer for both, so the API cannot be used to find out
    which company ids are real.
    """
    company, membership = await ledger_service.get_company_for_user(
        db, company_id=company_id, user_id=user.id
    )
    return CompanyContext(company=company, membership=membership)


CurrentCompany = Annotated[CompanyContext, Depends(get_company_context)]


def get_document_storage() -> DocumentStorage:
    """The bilag storage backend, as a dependency so tests can substitute one."""
    return get_storage()


Storage = Annotated[DocumentStorage, Depends(get_document_storage)]


def get_mailer() -> Mailer:
    """Where outgoing mail goes, as a dependency for the same reason storage is.

    A test that asserts what an invitation says must not need an SMTP server,
    and a test that asserts what happens when sending *fails* must be able to
    make it fail on purpose.
    """
    return build_mailer()


OutgoingMail = Annotated[Mailer, Depends(get_mailer)]


def set_session_cookie(response: Response, token: str) -> None:
    """Attach a session cookie to ``response``.

    ``httponly`` keeps the token away from any script on the page, which is the
    single most valuable property here — an XSS bug then cannot exfiltrate a
    login. ``secure`` is off only in development, where there is no HTTPS.
    """
    settings = get_settings()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_lifetime_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.session_cookie_secure,
        # "lax", not "strict": the Google callback arrives as a cross-site
        # top-level redirect, and "strict" would withhold the cookie we just
        # set, leaving the user looking at a logged-out page.
        samesite="lax",
        domain=settings.session_cookie_domain,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Remove the session cookie. Attributes must match the ones it was set with."""
    settings = get_settings()
    response.delete_cookie(
        key=settings.session_cookie_name,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        domain=settings.session_cookie_domain,
        path="/",
    )
