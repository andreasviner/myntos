"""Request-scoped dependencies: the database session, the caller, the cookie.

Two ways to present a session token, for two kinds of client:

- the ``myntos_session`` cookie, for the browser frontend; and
- ``Authorization: Bearer <token>``, for scripts, tests and any future CLI.

They carry the same token and are equally valid. The difference that matters is
CSRF: a cookie is attached by the browser automatically, so a cookie-authorised
state-changing request must additionally prove it came from our own frontend —
see :func:`_require_trusted_origin`. A bearer token has to be added by the
caller and is therefore immune to the same attack.

And a third credential, which is not a session at all: an access token from a
key a person made for their agent (``myntos_at_``, docs/agents-and-api.md). It
arrives as a bearer token and is told apart by its prefix. Everything a key may
and may not do is decided in one place, :func:`get_caller`, before any route
body runs:

- **the route must say a key may call it**, and with which scope
  (:data:`KEY_READ`, :func:`key_write`, :func:`key_post`, :func:`key_send`,
  :data:`KEY_PREVIEW` in its ``openapi_extra``). A route that says nothing is
  closed to keys, so a new route is safe before anybody has thought about
  agents, and rule 5 (no money, no access management) holds by those routes
  never saying so;
- **the key must carry that scope**, and the route's own permission check then
  runs against the person's role as it stands now, so a key is always the
  intersection of the two. A key that may only *ask* for the scope has its
  request stored to wait for its person instead (rule 13,
  :mod:`myntos.auth.approvals`), and a request the person approved comes back
  through here marked in the ASGI scope, and runs;
- **what leaves Myntos always waits** (:func:`key_send`): a filing, a check
  with Skatteetaten. It is held for the key's person even when the key may
  post on its own, because what has been sent cannot be called back;
- **the key's company is the only company**: any other id in the path is the
  same 404 a stranger gets (:func:`get_company_context`).
"""

import hmac
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any, NoReturn

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.auth import agent_keys, approvals
from myntos.auth.agent_keys import KeyAccess
from myntos.auth.sessions import resolve_session
from myntos.auth.tokens import hash_token
from myntos.core.config import get_settings
from myntos.core.mail import Mailer, build_mailer
from myntos.db.session import get_session
from myntos.domain.identity import service
from myntos.domain.identity.agent_key_mail import key_stopped_mail
from myntos.domain.identity.errors import (
    AgentRateLimitedError,
    MissingScopeError,
    NotForAgentKeysError,
)
from myntos.domain.identity.models import (
    AgentKey,
    AgentKeyScope,
    AgentKeyStop,
    CompanyMembership,
    OrganizationMembership,
    User,
    UserSession,
)
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.errors import CompanyNotFoundError
from myntos.domain.ledger.models import Company
from myntos.storage import DocumentStorage, WorkingStore, get_storage, get_working_store

# Methods that cannot change server state, and so need no CSRF defence.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

DbSession = Annotated[AsyncSession, Depends(get_session)]

# The OpenAPI extension a route uses to say a key may call it, and with what.
# Published in /api/v1/openapi.json, so a developer reads the scope a route
# needs in the same place they read the route.
KEY_SCOPE_EXTENSION = "x-myntos-key-scope"


# What a person is asked to approve when a key's request waits for them, as
# the end of the sentence «Claude vil …» on /godkjenning/. Bokmål, because a
# person reads it; required on every route that writes, because a request
# nobody can describe is a request nobody can judge (docs/agents-and-api.md
# rule 13).
KEY_APPROVAL_LABEL_EXTENSION = "x-myntos-approval-label"

# A write that stores nothing: a proposal read off a bilag, the review of an
# opening balance before it is posted. A key whose writes wait for approval
# runs these at once, because there is nothing to approve, and an answer that
# arrives after a person has pressed a button is not an answer to the question.
KEY_PREVIEW_EXTENSION = "x-myntos-key-preview"

# For a write whose result is a voucher that bilag can be attached to: the
# route, with ``{id}`` for the voucher's id, that a file an agent adds to the
# waiting request is sent to once the request has run. It is how a receipt
# reaches the person in the same approval as the payment it documents, instead
# of in a second one that cannot be made until the first has run.
KEY_APPROVAL_ATTACHMENTS_EXTENSION = "x-myntos-approval-attachments"
ATTACH_TO_VOUCHER = "/api/v1/companies/{company_id}/cashflow/{id}/dokument"

# A route that sends something out of Myntos: to Skatteetaten and Altinn now,
# to a Peppol access point or a bank when those are built. A key's request to
# one always waits for its person on /godkjenning/, whatever the key may do on
# its own: the owner's rule (2026-10-22) is that an agent may do everything,
# and that nothing leaves the program without a person's yes, because what has
# been sent cannot be called back (docs/agents-and-api.md rule 5).
KEY_SEND_EXTENSION = "x-myntos-key-send"

# A send that goes as the person, with their own ID-porten login, which is
# Skatteetaten's condition for an end-user system: the person has to hold one
# when they approve (409 ``id_porten_login_required`` until they do), and the
# request then runs with it.
KEY_ID_PORTEN_EXTENSION = "x-myntos-key-id-porten"


def _for_keys(scope: AgentKeyScope) -> dict[str, Any]:
    return {KEY_SCOPE_EXTENSION: scope.value}


def _writing(scope: AgentKeyScope, label: str, attachments: str | None) -> dict[str, Any]:
    extra = _for_keys(scope) | {KEY_APPROVAL_LABEL_EXTENSION: label}
    if attachments is not None:
        extra[KEY_APPROVAL_ATTACHMENTS_EXTENSION] = attachments
    return extra


# Put on a route as ``openapi_extra=KEY_READ`` (or ``key_write(…)``,
# ``key_post(…)``, ``KEY_PREVIEW``) and nowhere else. Which one is a decision
# about the route, and the scopes are explained on
# :class:`~myntos.domain.identity.models.AgentKeyScope`: ``post`` is for
# whatever cannot be undone.
KEY_READ = _for_keys(AgentKeyScope.READ)
KEY_PREVIEW = _for_keys(AgentKeyScope.WRITE) | {KEY_PREVIEW_EXTENSION: True}


def key_write(label: str, *, attachments: str | None = None) -> dict[str, Any]:
    """A route that changes something that can be changed back, and what to call it.

    ``label`` finishes the sentence «Agenten vil …» for the person who
    approves it: «lage et utkast til faktura».
    """
    return _writing(AgentKeyScope.WRITE, label, attachments)


def key_post(label: str, *, attachments: str | None = None) -> dict[str, Any]:
    """A route that does what cannot be undone, and what to call it: «utstede en faktura»."""
    return _writing(AgentKeyScope.POST, label, attachments)


def key_send(label: str, *, id_porten: bool = False) -> dict[str, Any]:
    """A route that sends something out of Myntos, and what to call it.

    «sende skattemeldingen til Skatteetaten». It needs ``post``, on the key's
    own or by asking, and always waits for the person, never runs on the
    key's own. ``id_porten`` for a send that goes with the person's ID-porten
    login.
    """
    extra = _writing(AgentKeyScope.POST, label, None) | {KEY_SEND_EXTENSION: True}
    if id_porten:
        extra[KEY_ID_PORTEN_EXTENSION] = True
    return extra


# For the few routes an agent is taught that need no credential at all: the
# token exchange, revocation, the health check. Nothing checks it at runtime,
# because nothing there asks who is calling; it is said so that the published
# document answers the question for every route, and so the documentation test
# can tell a public route a skill may teach from one that is closed to keys.
NO_KEY_NEEDED: dict[str, Any] = {KEY_SCOPE_EXTENSION: "none"}


def key_scope_of(route: object) -> AgentKeyScope | None:
    """The scope a route asks of a key, or ``None`` when no key may call it.

    "none" is ``None`` here too: a public route that one day starts asking who
    is calling must not thereby become open to keys without anybody deciding
    so.
    """
    extra = getattr(route, "openapi_extra", None) or {}
    value = extra.get(KEY_SCOPE_EXTENSION)
    if value is None or value == NO_KEY_NEEDED[KEY_SCOPE_EXTENSION]:
        return None
    return AgentKeyScope(value)


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


def get_mailer() -> Mailer:
    """Where outgoing mail goes, as a dependency for the same reason storage is.

    A test that asserts what an invitation says must not need an SMTP server,
    and a test that asserts what happens when sending *fails* must be able to
    make it fail on purpose.
    """
    return build_mailer()


OutgoingMail = Annotated[Mailer, Depends(get_mailer)]


@dataclass(frozen=True)
class Caller:
    """Who is making this request, and with what: a session or a key, never both."""

    session: UserSession | None
    key: KeyAccess | None


async def refuse_key(
    db: AsyncSession, post: Mailer, access: KeyAccess, error: Exception
) -> NoReturn:
    """Raise ``error``, and stop the key if it has been refused too often (rule 10).

    Only for the refusals an agent that read the documentation rarely meets:
    a route no key may call, a scope it was not given, another company's id.
    A burst of those is what a key being used to probe looks like. The stop is
    committed before the refusal goes out, because the request's transaction
    rolls back with the error, and the person is told at once, because a
    stopped key is something only they can replace.
    """
    key = access.key
    if agent_keys.count_refusal(key.id) and key.revoked_at is None:
        agent_keys.stop_key(key, AgentKeyStop.REFUSALS)
        await db.commit()
        if post.configured:
            await post.send(
                key_stopped_mail(
                    to=access.user.email,
                    full_name=access.user.full_name,
                    key_name=key.name,
                    company_name=access.company_name,
                    reason=AgentKeyStop.REFUSALS,
                    url=_frontend_url("/organisasjon/#tilgang"),
                )
            )
    raise error


def _frontend_url(path: str) -> str:
    return get_settings().frontend_base_url.rstrip("/") + path


def _is_preview(route: object) -> bool:
    extra = getattr(route, "openapi_extra", None) or {}
    return extra.get(KEY_PREVIEW_EXTENSION) is True


def _is_send(route: object) -> bool:
    extra = getattr(route, "openapi_extra", None) or {}
    return extra.get(KEY_SEND_EXTENSION) is True


def approval_label_of(route: object) -> str | None:
    """What the person is asked to approve when a key's call to ``route`` waits for them."""
    extra = getattr(route, "openapi_extra", None) or {}
    label = extra.get(KEY_APPROVAL_LABEL_EXTENSION)
    return label if isinstance(label, str) else None


async def _admit_key(request: Request, db: AsyncSession, post: Mailer, access: KeyAccess) -> None:
    """Let a key through to this route, hold its request for approval, or say why not.

    Three outcomes for a route a key may call at all: the key holds the scope
    and the route runs; the key may only *ask* for it, and the request is
    stored to wait for its person (:func:`myntos.auth.approvals.hold`, rule
    13); or neither, and it is refused. A preview, which stores nothing, runs
    for a key that may only ask to write, because there is nothing to approve.
    A send is held even for a key that may post on its own (:func:`key_send`).
    """
    route = request.scope.get("route")
    needed = key_scope_of(route)
    key = access.key
    if needed is None:
        await refuse_key(
            db,
            post,
            access,
            NotForAgentKeysError(
                "dette kan ikke gjøres med en API-nøkkel, uansett hvilken tilgang den har. "
                "Personen må gjøre det selv, innlogget i Myntos."
            ),
        )
    may_ask = needed in key.approval_scope_set
    if needed not in key.scope_set and not may_ask:
        await refuse_key(
            db,
            post,
            access,
            MissingScopeError(
                f"nøkkelen har ikke tilgangen «{needed.value}». Personen kan lage en ny nøkkel "
                "med mer tilgang under Innstillinger for selskapet → Tilgang i Myntos."
            ),
        )
    if not agent_keys.admit_request(key.id):
        raise AgentRateLimitedError(
            "for mange forespørsler med denne nøkkelen det siste minuttet. Vent litt og prøv igjen."
        )
    if _is_preview(route) or (needed in key.scope_set and not _is_send(route)):
        return
    # Held, never run, so the company wall a route would apply is applied
    # here: a request stored for another company's path would show the person
    # an id from books the key cannot see.
    company_id = request.path_params.get("company_id")
    if company_id is not None and str(company_id) != str(key.company_id):
        await refuse_key(db, post, access, CompanyNotFoundError("finner ikke selskapet"))
    label = approval_label_of(route)
    if label is None:
        # A writing route without a label is a test failure
        # (test_every_writing_route_says_what_it_asks), so this is the floor
        # under it rather than a path anybody takes.
        raise NotForAgentKeysError("denne handlingen kan ikke vente på godkjenning")
    await approvals.hold(request, db, key, scope=needed, label=label)


async def get_caller(request: Request, db: DbSession, post: OutgoingMail) -> Caller:
    """The session or key behind this request, or 401.

    A key is admitted here or nowhere, so every route that knows who is
    calling, by any of the dependencies below, has been through the same gate.
    """
    settings = get_settings()
    replay = request.scope.get(approvals.REPLAY_SCOPE)
    if isinstance(replay, approvals.Replay):
        # A request the key's person approved on /godkjenning/, sent again by
        # myntos.auth.approvals.run. It runs as the key, past the scope it
        # could only ask for, because the person has now said yes; every other
        # check, the company wall and the route's own permission check
        # against the person's role included, is made as for any key.
        if key_scope_of(request.scope.get("route")) is None:
            raise NotForAgentKeysError("dette kan ikke gjøres med en API-nøkkel")
        access = await agent_keys.resolve_key_for_replay(db, replay.key_id)
        db.info[agent_keys.AGENT_KEY_INFO] = access.key.id
        return Caller(session=None, key=access)

    token = _bearer_token(request)
    if token is not None and agent_keys.is_agent_credential(token):
        access = await agent_keys.resolve_access_token(db, token)
        await _admit_key(request, db, post, access)
        # For the voucher stamp: see myntos.auth.agent_keys._stamp_vouchers.
        db.info[agent_keys.AGENT_KEY_INFO] = access.key.id
        return Caller(session=None, key=access)

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
    return Caller(session=session, key=None)


CurrentCaller = Annotated[Caller, Depends(get_caller)]


async def get_current_session(caller: CurrentCaller) -> UserSession:
    """The caller's live session, or 401; never a key.

    Only reachable with a key on a route that declared a key scope and then
    asked for a session anyway, which is a mistake in the route, so it is
    refused rather than guessed at.
    """
    if caller.session is None:
        raise NotForAgentKeysError("dette kan bare gjøres innlogget i Myntos, ikke med en nøkkel.")
    return caller.session


CurrentSession = Annotated[UserSession, Depends(get_current_session)]


async def get_current_user(caller: CurrentCaller, db: DbSession) -> User:
    """The person behind the request: the session's account, or the key's person."""
    if caller.key is not None:
        return caller.key.user
    assert caller.session is not None  # noqa: S101 - Caller holds exactly one
    user = await db.get(User, caller.session.user_id)
    if user is None or not user.is_active:
        raise _unauthenticated()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_optional_session(
    request: Request, db: DbSession, post: OutgoingMail
) -> UserSession | None:
    """The caller's live session, or ``None`` for somebody who is not signed in.

    For the federated sign-in redirects, which a browser navigates to whether
    or not it holds a session. The switch to BankID needs the session itself,
    not only its account: it ends every other session and must spare this one.

    A key is not "nobody": these routes declare no key scope, so a key is
    refused here with the reason, rather than quietly treated as a visitor.
    """
    try:
        caller = await get_caller(request, db, post)
    except HTTPException:
        return None
    return caller.session


OptionalSession = Annotated[UserSession | None, Depends(get_optional_session)]


async def get_optional_user(session: OptionalSession, db: DbSession) -> User | None:
    """The signed-in caller, or ``None`` for somebody who is not signed in.

    For the federated sign-in redirects, which a browser navigates to whether
    or not it holds a session, and which have to know which: linking a provider
    to an account needs the account. Only ever on GET, so the origin check that
    guards cookie-authorised writes has nothing to do here. Built on
    :func:`get_optional_session` rather than beside it, so a route that asks
    for both resolves the session once.
    """
    if session is None:
        return None
    user = await db.get(User, session.user_id)
    if user is None or not user.is_active:
        return None
    return user


OptionalUser = Annotated[User | None, Depends(get_optional_user)]


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
    # The key the request was made with, when it was. The person is still
    # ``membership``; this is only how they are acting.
    agent_key: AgentKey | None = None


async def get_company_context(
    company_id: int, caller: CurrentCaller, user: CurrentUser, db: DbSession, post: OutgoingMail
) -> CompanyContext:
    """Resolve the path's company through the caller's company memberships.

    Raises :class:`~myntos.domain.ledger.errors.CompanyNotFoundError` — 404 —
    both when the company does not exist and when the caller has no membership
    on it. The same answer for both, so the API cannot be used to find out
    which company ids are real.

    A key adds one wall before that: its own company and no other, even one its
    person is a member of (docs/agents-and-api.md rule 9). The same 404, so a
    key cannot tell its person's other companies from ids nobody holds.
    """
    if caller.key is not None and company_id != caller.key.key.company_id:
        await refuse_key(db, post, caller.key, CompanyNotFoundError("finner ikke selskapet"))
    company, membership = await ledger_service.get_company_for_user(
        db, company_id=company_id, user_id=user.id
    )
    return CompanyContext(
        company=company,
        membership=membership,
        agent_key=caller.key.key if caller.key is not None else None,
    )


CurrentCompany = Annotated[CompanyContext, Depends(get_company_context)]


def get_document_storage() -> DocumentStorage:
    """The bilag storage backend, as a dependency so tests can substitute one."""
    return get_storage()


Storage = Annotated[DocumentStorage, Depends(get_document_storage)]


def get_working_files() -> WorkingStore:
    """Where uploads a job reads and then deletes are kept, as a dependency for the same reason."""
    return get_working_store()


Working = Annotated[WorkingStore, Depends(get_working_files)]


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


# --- Which browser started a sign-in --------------------------------------------
#
# A provider's callback URL, code and state included, is an ordinary link, and
# whoever started the flow can stop short of it and hand it to somebody else.
# Opened in a stranger's browser, a login callback signed that browser into the
# starter's account, so whatever the stranger then typed, uploaded or proved
# with ID-porten landed on an account the starter could open. The state was
# single-use and unguessable, which is no help when the attacker is the one who
# was given it. So the browser that starts a flow is handed a cookie naming the
# state, and only a callback that brings it back is honoured.


def _flow_cookie(digest: str) -> str:
    # One cookie per flow rather than one for all of them: a person who starts
    # a Google link and then the switch to BankID before finishing the first
    # must still be able to finish it. Each lapses with its attempt.
    return f"myntos_flow_{digest[:16]}"


def bind_flow(response: Response, state: str, lifetime: timedelta) -> None:
    """Mark this browser as the one that started the flow with ``state``.

    Host-only on the API and lax, so it comes back on the provider's top-level
    redirect to the callback and goes nowhere else. Holds a digest rather than
    the state itself, which the callback URL already carries in the clear.
    """
    digest = hash_token(state)
    response.set_cookie(
        key=_flow_cookie(digest),
        value=digest,
        max_age=int(lifetime.total_seconds()),
        httponly=True,
        secure=get_settings().session_cookie_secure,
        samesite="lax",
        path="/api/v1/",
    )


def started_here(request: Request, state: str) -> bool:
    """Whether this browser is the one that started the flow with ``state``."""
    digest = hash_token(state)
    cookie = request.cookies.get(_flow_cookie(digest))
    return cookie is not None and hmac.compare_digest(cookie, digest)
