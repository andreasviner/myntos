"""Keys for agents and integrations, from both ends.

**An agent's end** is three routes and no session: exchange a connection key or
a refresh token for tokens (``POST /agent/token``), stop the key
(``POST /agent/revoke``), and ask what the key is (``GET /agent/me``).
``llms.txt`` and the ``myntos-connect`` skill teach them in that order.

**A person's end** is the Tilgang section on /organisasjon/: switch keys on for
a company, make one, see them, stop one. Those routes are under
``/companies/{company_id}/`` like every other thing about one set of books, and
declare no key scope, because a key managing keys is access management, and no
key may do that (docs/agents-and-api.md rule 5).

The rules are in docs/agents-and-api.md; the mechanism in
:mod:`myntos.auth.agent_keys`.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Request, status
from pydantic import BaseModel, Field, field_validator

from myntos.api.deps import (
    KEY_READ,
    NO_KEY_NEEDED,
    CurrentCaller,
    CurrentCompany,
    CurrentUser,
    DbSession,
    OutgoingMail,
)
from myntos.api.v1.companies import CompanyResponse, _company_response
from myntos.auth import agent_keys
from myntos.core.clock import as_utc, utc_now
from myntos.core.config import get_settings
from myntos.core.mail import Mail, Mailer
from myntos.domain.identity.access import Permission, has_permission, require_permission
from myntos.domain.identity.agent_key_mail import (
    key_created_mail,
    key_first_used_mail,
    key_stopped_mail,
)
from myntos.domain.identity.errors import (
    AgentKeyNotFoundError,
    AgentKeyRequiredError,
    InvalidGrantError,
)
from myntos.domain.identity.models import AgentKey, AgentKeyStop
from myntos.domain.ledger import service as ledger_service

router = APIRouter(tags=["agent"])


def _tilgang_url() -> str:
    return get_settings().frontend_base_url.rstrip("/") + "/organisasjon/#tilgang"


async def _send_quietly(post: Mailer, mail: Mail) -> None:
    """Send from a background task; the mailers log failures rather than raise."""
    await post.send(mail)


def _seconds_until(moment: datetime) -> int:
    return max(0, int((as_utc(moment) - utc_now()).total_seconds()))


# --- An agent's end ---------------------------------------------------------------


class TokenRequest(BaseModel):
    grant_type: Literal["connection_key", "refresh_token"]
    connection_key: str | None = None
    refresh_token: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105 - the scheme name, not a secret
    # Seconds, as OAuth has it.
    expires_in: int
    refresh_token: str
    refresh_token_expires_in: int
    # Space-separated, as OAuth has it: "read", "read write", "post read write".
    # What the key may do on its own.
    scope: str
    # The same, for what it may only ask to do: each such call is stored and
    # answered 202 approval_pending, and runs when its person approves it.
    approval_scope: str
    company_id: int
    company_name: str


@router.post("/agent/token", openapi_extra=NO_KEY_NEEDED)
async def exchange_token(
    payload: TokenRequest,
    request: Request,
    db: DbSession,
    post: OutgoingMail,
    background: BackgroundTasks,
) -> TokenResponse:
    """Exchange a connection key or a refresh token for an access token and a new refresh token.

    This is the first call an agent makes and the one it makes every fifteen
    minutes after that. No Authorization header: the credential is in the body.

    **The first time**, send the connection key the person pasted
    (``myntos_ck_…``) with ``grant_type: "connection_key"``. It works once. The
    copy in the conversation is worthless afterwards, which is the point: chat
    transcripts are stored by the model provider and are not a safe place for a
    password.

    **Afterwards**, send the refresh token (``myntos_rt_…``) with
    ``grant_type: "refresh_token"``. Every refresh returns a *new* refresh token
    and spends the old one. Store the new one before doing anything else. A
    spent refresh token presented again more than 30 seconds after it was spent
    means somebody else has a copy, so the whole key is stopped and the person
    is e-mailed (``refresh_token_reused``).

    Use the access token (``myntos_at_…``) as ``Authorization: Bearer …`` on
    every other call, until it expires (``expires_in`` seconds, 15 minutes) or
    a call answers 401 ``invalid_agent_token``; then refresh once and retry.
    Refresh once, not once per parallel request.

    Refusals, all 400 unless noted, and what to do:

    - ``invalid_grant``: unknown, mistyped or wrong kind of token. The detail
      says which.
    - ``connection_key_spent``: the connection key was already exchanged. Use
      the refresh token from that exchange, or ask the person for a new key.
    - ``connection_key_expired``: not used within 24 hours. Ask for a new key.
    - ``refresh_token_reused``: the key has been stopped. Ask for a new key.
    - ``agent_key_stopped``: the key is revoked, older than a year, unused for
      30 days, or its person lost access to the books. Ask for a new key.
    - 403 ``agent_access_disabled``: the company switched keys off. Only an
      owner or admin can switch them back on.

    "Ask the person for a new key" always means: in Myntos, «Innstillinger
    for selskapet» → «Tilgang» → «Inviter en KI», then paste what it shows
    into the conversation.
    """
    client = request.headers.get("user-agent")
    if payload.grant_type == "connection_key":
        if not payload.connection_key:
            raise InvalidGrantError("connection_key mangler")
        pair = await agent_keys.exchange_connection_key(
            db, payload.connection_key.strip(), client=client
        )
        # The one moment a key changes hands, so the one message about it.
        person = pair.person
        if post.configured:
            background.add_task(
                _send_quietly,
                post,
                key_first_used_mail(
                    to=person.email,
                    full_name=person.full_name,
                    key_name=pair.key.name,
                    company_name=pair.company.name,
                    client=agent_keys.client_name(client),
                    url=_tilgang_url(),
                ),
            )
    else:
        if not payload.refresh_token:
            raise InvalidGrantError("refresh_token mangler")
        outcome = await agent_keys.refresh(db, payload.refresh_token.strip(), client=client)
        if isinstance(outcome, agent_keys.StoppedForReuse):
            # Committed before the refusal, which rolls the request back, and
            # mailed inline, because a background task belongs to a response
            # and this request ends in an error.
            await db.commit()
            if post.configured:
                await post.send(
                    key_stopped_mail(
                        to=outcome.person.email,
                        full_name=outcome.person.full_name,
                        key_name=outcome.key.name,
                        company_name=outcome.company.name,
                        reason=AgentKeyStop.REUSE,
                        url=_tilgang_url(),
                    )
                )
            raise agent_keys.reused_error()
        pair = outcome
    return TokenResponse(
        access_token=pair.access_token,
        expires_in=_seconds_until(pair.access_expires_at),
        refresh_token=pair.refresh_token,
        refresh_token_expires_in=_seconds_until(pair.refresh_expires_at),
        scope=pair.key.scopes,
        approval_scope=pair.key.approval_scopes,
        company_id=pair.company.id,
        company_name=pair.company.name,
    )


class RevokeRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


@router.post("/agent/revoke", status_code=status.HTTP_204_NO_CONTENT, openapi_extra=NO_KEY_NEEDED)
async def revoke_token(payload: RevokeRequest, db: DbSession) -> None:
    """Stop the key a token belongs to. Any of its tokens will do.

    For an agent that suspects a leak: a token pasted somewhere it should not
    be, a refresh that answered something unexpected, a person who says "stop".
    Send any credential of the key (connection key, refresh token or access
    token) and the whole key stops working at once. No Authorization header:
    whoever holds a credential may always destroy it.

    Always 204, also for a token that does not exist, so the answer cannot be
    used to test which tokens are real. Afterwards, tell the person, and ask
    for a new key if they want to go on.
    """
    await agent_keys.stop_by_token(db, payload.token.strip())


class AgentSelfResponse(BaseModel):
    key_id: int
    name: str
    # What the key may do on its own.
    scopes: list[str]
    # What it may only ask to do: a call needing one of these answers 202
    # approval_pending and waits for its person (myntos-godkjenning).
    approval_scopes: list[str]
    company_id: int
    company_name: str
    orgnr: str
    # Whose permissions the key works with. Every write is registered in
    # their name, "via" the key.
    person_name: str
    # The person's role on these books right now. A key never does more than
    # its scopes, and never more than this role allows either.
    role: str
    expires_at: datetime


@router.get("/agent/me", openapi_extra=KEY_READ)
async def who_am_i(caller: CurrentCaller, db: DbSession) -> AgentSelfResponse:
    """What this key is: which company, whose permissions, which scopes.

    The second call an agent makes, after the exchange. Use ``company_id`` to
    build every other path (``/api/v1/companies/{company_id}/…``): a key works
    in exactly one company, and any other id answers 404.

    ``scopes`` decides what the key may do on its own: ``read`` reads,
    ``write`` makes drafts and other changes that can be undone, ``post`` does
    what cannot be undone (posting vouchers, issuing invoices).
    ``approval_scopes`` is what it may only *ask* to do: a call needing one of
    those is not carried out but stored, answered 202 ``approval_pending``, and
    run when the person approves it in Myntos (the skill myntos-godkjenning
    says how to follow it up). A scope in neither list answers 403
    ``missing_scope``. ``role`` is the person's
    role, which can refuse an action the scopes allow. Some things no key can
    do whatever it holds: file to Skatteetaten, move money or change a bank
    account number, manage who has access, delete the account. Those answer
    403 ``not_for_agent_keys``; tell the person to do them in Myntos.
    """
    if caller.key is None:
        raise AgentKeyRequiredError(
            "denne adressen svarer bare for en API-nøkkel, ikke for en innlogging"
        )
    key = caller.key.key
    company, membership = await ledger_service.get_company_for_user(
        db, company_id=key.company_id, user_id=key.user_id
    )
    return AgentSelfResponse(
        key_id=key.id,
        name=key.name,
        scopes=sorted(scope.value for scope in key.scope_set),
        approval_scopes=sorted(scope.value for scope in key.approval_scope_set),
        company_id=company.id,
        company_name=company.name,
        orgnr=company.orgnr,
        person_name=caller.key.user.full_name,
        role=membership.role.value,
        expires_at=as_utc(key.expires_at),
    )


# --- A person's end ---------------------------------------------------------------


class AgentKeyResponse(BaseModel):
    id: int
    company_id: int
    name: str
    scopes: list[str]
    # What the key may only ask to do, each request waiting for its person.
    approval_scopes: list[str]
    # waiting: made, not yet taken into use. active: in use. stopped: revoked,
    # by a person or by Myntos (revoked_reason says which). expired: past a
    # deadline nobody had to act on.
    state: Literal["waiting", "active", "stopped", "expired"]
    person_name: str
    is_own: bool
    created_at: datetime
    expires_at: datetime
    connection_key_expires_at: datetime
    exchanged_at: datetime | None
    last_used_at: datetime | None
    # The agent's software, coarsely ("python-httpx"), never the whole
    # User-Agent string.
    last_used_client: str | None
    revoked_at: datetime | None
    revoked_reason: str | None


def _optional(moment: datetime | None) -> datetime | None:
    return None if moment is None else as_utc(moment)


def _key_response(key: AgentKey, *, user_id: int) -> AgentKeyResponse:
    return AgentKeyResponse(
        id=key.id,
        company_id=key.company_id,
        name=key.name,
        scopes=sorted(scope.value for scope in key.scope_set),
        approval_scopes=sorted(scope.value for scope in key.approval_scope_set),
        state=agent_keys.key_state(key, now=utc_now()),
        person_name=key.user.full_name,
        is_own=key.user_id == user_id,
        created_at=as_utc(key.created_at),
        expires_at=as_utc(key.expires_at),
        connection_key_expires_at=as_utc(key.connection_key_expires_at),
        exchanged_at=_optional(key.exchanged_at),
        last_used_at=_optional(key.last_used_at),
        last_used_client=agent_keys.client_name(key.last_used_client),
        revoked_at=_optional(key.revoked_at),
        revoked_reason=None if key.revoked_reason is None else key.revoked_reason.value,
    )


@router.get("/companies/{company_id}/agent-keys")
async def list_agent_keys(
    context: CurrentCompany, user: CurrentUser, db: DbSession
) -> list[AgentKeyResponse]:
    """The keys made for these books, newest first, stopped and expired ones included.

    Everybody sees their own. Somebody with ``member:manage`` sees everyone's,
    because a key is access to the books and deciding who has access is
    theirs; it is also how an owner finds the key a colleague who left forgot
    to stop.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    everyone = has_permission(context.membership.role, Permission.MEMBER_MANAGE)
    keys = await agent_keys.list_keys(
        db, company_id=context.company.id, user_id=None if everyone else user.id
    )
    return [_key_response(key, user_id=user.id) for key in keys]


class CreateAgentKeyRequest(BaseModel):
    # What the person calls it: the agent, the machine, the purpose.
    name: str = Field(min_length=1, max_length=64)
    # "read" is always included. "write" and "post" need ledger:write.
    scopes: list[str] = Field(default_factory=lambda: ["read"])
    # What the key may only ask to do: "write", "post" or both, never one it
    # already holds in ``scopes``, and never "post" further than "write".
    approval_scopes: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _one_line(cls, value: str) -> str:
        # Shown in a list and inside «» in a sentence, so it is one line with
        # single spaces, and something has to be left once that is done.
        tidy = " ".join(value.split())
        if not tidy:
            raise ValueError("nøkkelen må ha et navn")
        return tidy


class IssuedAgentKeyResponse(BaseModel):
    company_id: int
    key: AgentKeyResponse
    # Shown once. Only its digest is stored, so it cannot be shown again.
    connection_key: str


@router.post("/companies/{company_id}/agent-keys", status_code=status.HTTP_201_CREATED)
async def create_agent_key(
    payload: CreateAgentKeyRequest,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    post: OutgoingMail,
    background: BackgroundTasks,
) -> IssuedAgentKeyResponse:
    """Make a key for an agent or a program, and show its connection key once.

    The key works with the caller's own permissions, narrowed to ``scopes``,
    in this company only. ``approval_scopes`` is what it may only ask to do:
    each such request waits for the caller on /godkjenning/ (rule 13). For
    each of ``write`` and ``post`` the choice is one of three, on its own,
    only with approval, or not at all, and posting never reaches further than
    writing (422 ``invalid_agent_scope``). Refused while the company has keys switched off
    (403 ``agent_access_disabled``), for a scope the caller's role cannot give
    (422 ``invalid_agent_scope``), and for an account that signs in with
    BankID (409 ``bankid_account``), until BankID says its terms allow it.

    The caller is mailed, so a key made with a stolen session does not go
    unnoticed.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    scopes = agent_keys.parse_scopes(payload.scopes)
    issued = await agent_keys.issue_key(
        db,
        company=context.company,
        membership=context.membership,
        user=user,
        name=payload.name,
        scopes=scopes,
        approval_scopes=agent_keys.parse_approval_scopes(payload.approval_scopes, scopes=scopes),
    )
    if post.configured:
        background.add_task(
            _send_quietly,
            post,
            key_created_mail(
                to=user.email,
                full_name=user.full_name,
                key_name=issued.key.name,
                company_name=context.company.name,
                url=_tilgang_url(),
            ),
        )
    issued.key.user = user
    return IssuedAgentKeyResponse(
        company_id=context.company.id,
        key=_key_response(issued.key, user_id=user.id),
        connection_key=issued.connection_key,
    )


@router.delete(
    "/companies/{company_id}/agent-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def stop_agent_key(
    key_id: int, context: CurrentCompany, user: CurrentUser, db: DbSession
) -> None:
    """Stop a key at once. Its tokens stop working on their next request.

    Your own, always. Anybody's, with ``member:manage``. Somebody else's key,
    without it, is a 404, the same as a key that does not exist, because you
    cannot see it in the list either.
    """
    key = await agent_keys.get_key(db, company_id=context.company.id, key_id=key_id)
    if key.user_id != user.id and not has_permission(
        context.membership.role, Permission.MEMBER_MANAGE
    ):
        raise AgentKeyNotFoundError("fant ingen slik nøkkel")
    agent_keys.stop_key(key, AgentKeyStop.PERSON)


class AgentAccessRequest(BaseModel):
    enabled: bool


@router.put("/companies/{company_id}/agent-access")
async def set_agent_access(
    payload: AgentAccessRequest, context: CurrentCompany, db: DbSession
) -> CompanyResponse:
    """Switch keys for agents and integrations on or off for these books.

    Off by default. Owners and admins only (``member:manage``): letting a tool
    of somebody's own choosing read the books, with whatever model is behind
    it and wherever that model runs, is a decision about access, and the page
    says what it means before the switch is offered.

    Switching off stops every key at once, for good. Switching on again later
    brings none of them back, so nobody's forgotten key wakes up with it.
    """
    require_permission(context.membership.role, Permission.MEMBER_MANAGE)
    company = context.company
    if company.agent_access_enabled and not payload.enabled:
        await agent_keys.stop_keys(db, reason=AgentKeyStop.ACCESS_OFF, company_id=company.id)
    company.agent_access_enabled = payload.enabled
    await db.flush()
    return _company_response(company, context.membership)
