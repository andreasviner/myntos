"""Integration keys, from an owner's end: make one, see them, stop one.

The «Integrasjoner» section on /organisasjon/. An integration key is a
server's own secret that makes, issues and sends invoices without asking
anybody (docs/agents-and-api.md rule 15, the owner's decision of 2026-11-26);
the mechanism is :mod:`myntos.auth.integration_keys` and the gate that admits
it :func:`myntos.api.deps.get_caller`.

Owners only (``integration:manage``), and never a key: these routes declare
nothing to a key, so an agent key is refused with ``not_for_agent_keys`` and
an integration key with ``not_for_integration_keys`` (both 403). A key that
could make keys would be access management, which no key does (rule 5). Not
governed by the Tilgang switch either way: switching agent keys off stops no
integration, and making an integration needs no switch on.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, status
from pydantic import BaseModel, Field, field_validator

from myntos.api.deps import CurrentCompany, CurrentUser, DbSession, OutgoingMail
from myntos.auth import agent_keys, integration_keys
from myntos.core.clock import as_utc
from myntos.core.config import get_settings
from myntos.core.mail import Mail, Mailer
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.identity.agent_key_mail import (
    integration_key_created_mail,
    integration_key_stopped_mail,
)
from myntos.domain.identity.models import AgentKey, AgentKeyStop, IntegrationPurpose, User

router = APIRouter(tags=["integrasjoner"])


def integrations_url() -> str:
    return get_settings().frontend_base_url.rstrip("/") + "/organisasjon/#integrasjoner"


async def _send_quietly(post: Mailer, mail: Mail) -> None:
    """Send from a background task; the mailers log failures rather than raise."""
    await post.send(mail)


def tell_owner_stopped(
    background: BackgroundTasks,
    post: Mailer,
    keys: list[AgentKey],
    *,
    person: User,
    company_name: str,
    reason: AgentKeyStop,
) -> None:
    """Mail ``person`` about each of their integration keys that just stopped.

    For the stops a member route makes: a role taken away, a membership
    ended. The server that held the key only starts failing, so its owner
    hears why from Myntos.
    """
    if not post.configured:
        return
    for key in keys:
        if not key.is_integration or key.user_id != person.id:
            continue
        background.add_task(
            _send_quietly,
            post,
            integration_key_stopped_mail(
                to=person.email,
                full_name=person.full_name,
                key_name=key.name,
                company_name=company_name,
                reason=reason,
                url=integrations_url(),
            ),
        )


class IntegrationKeyResponse(BaseModel):
    id: int
    company_id: int
    name: str
    # What the key may do. "faktura" is the only purpose: make, issue and send
    # invoices, and read what became of them.
    purpose: Literal["faktura"]
    # The addresses it works from, as written: one address, or a range in
    # CIDR form ("203.0.113.0/24").
    allowed_ips: list[str]
    # active until stopped: an integration key has no expiry.
    state: Literal["active", "stopped"]
    # The owner who made it, whose role every request works with.
    person_name: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    revoked_reason: str | None


def _optional(moment: datetime | None) -> datetime | None:
    return None if moment is None else as_utc(moment)


def _key_response(key: AgentKey) -> IntegrationKeyResponse:
    return IntegrationKeyResponse(
        id=key.id,
        company_id=key.company_id,
        name=key.name,
        purpose="faktura",
        allowed_ips=integration_keys.shown_addresses(key.allowed_ips),
        state=integration_keys.integration_key_state(key),
        person_name=key.user.full_name,
        created_at=as_utc(key.created_at),
        last_used_at=_optional(key.last_used_at),
        revoked_at=_optional(key.revoked_at),
        revoked_reason=None if key.revoked_reason is None else key.revoked_reason.value,
    )


@router.get("/companies/{company_id}/integrasjonsnokler")
async def list_integration_keys(
    context: CurrentCompany, db: DbSession
) -> list[IntegrationKeyResponse]:
    """The integration keys made for these books, newest first, stopped ones included.

    Owners only (403 ``permission_denied`` otherwise). Each says which owner
    made it, the addresses it works from and when it was last used.
    """
    require_permission(context.membership.role, Permission.INTEGRATION_MANAGE)
    keys = await integration_keys.list_integration_keys(db, company_id=context.company.id)
    return [_key_response(key) for key in keys]


Address = Annotated[str, Field(min_length=1, max_length=64)]


class CreateIntegrationKeyRequest(BaseModel):
    # What the owner calls it: the server or the service, «Løynd».
    name: str = Field(min_length=1, max_length=64)
    # IPv4 or IPv6 addresses, or ranges in CIDR form, that the server calls
    # from. 1 to 10; no range wider than /16 (IPv4) or /48 (IPv6).
    allowed_ips: list[Address] = Field(min_length=1, max_length=integration_keys.MAX_ADDRESSES)
    # What it may do. Only "faktura" exists.
    purpose: Literal["faktura"]

    @field_validator("name")
    @classmethod
    def _one_line(cls, value: str) -> str:
        # Shown in a list and inside «» in a mail, as an agent key's name is.
        tidy = " ".join(value.split())
        if not tidy:
            raise ValueError("nøkkelen må ha et navn")
        return tidy


class IssuedIntegrationKeyResponse(BaseModel):
    company_id: int
    key: IntegrationKeyResponse
    # Shown once. Only its digest is stored, so it cannot be shown again. It
    # goes in the server's settings and is sent as "Authorization: Bearer".
    secret: str


@router.post("/companies/{company_id}/integrasjonsnokler", status_code=status.HTTP_201_CREATED)
async def create_integration_key(
    payload: CreateIntegrationKeyRequest,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    post: OutgoingMail,
    background: BackgroundTasks,
) -> IssuedIntegrationKeyResponse:
    """Make an integration key for a server, and show its secret once.

    The server sends the secret as ``Authorization: Bearer myntos_ik_…`` from
    one of ``allowed_ips``, and may then call the routes that declare
    ``x-myntos-integration: faktura`` in this company: find and add customers,
    make, issue and delete invoice drafts, check whether the customer takes
    EHF, send the invoice as EHF and follow it, download the PDF. Those run at
    once, the send included, with no approval, as the owner who made the key
    and with their role as it stands on each request.

    Owners only (403 ``permission_denied``). 422 ``invalid_integration_address``
    for an entry that is not an address or a range, or a range wider than /16
    (IPv4) or /48 (IPv6). 409 ``bankid_account`` for an account that signs in
    with BankID, as for agent keys. The owner is mailed, never the secret.
    """
    require_permission(context.membership.role, Permission.INTEGRATION_MANAGE)
    issued = await integration_keys.issue_integration_key(
        db,
        company=context.company,
        membership=context.membership,
        user=user,
        name=payload.name,
        addresses=integration_keys.parse_addresses(payload.allowed_ips),
        purpose=IntegrationPurpose(payload.purpose),
    )
    if post.configured:
        background.add_task(
            _send_quietly,
            post,
            integration_key_created_mail(
                to=user.email,
                full_name=user.full_name,
                key_name=issued.key.name,
                company_name=context.company.name,
                url=integrations_url(),
            ),
        )
    issued.key.user = user
    return IssuedIntegrationKeyResponse(
        company_id=context.company.id,
        key=_key_response(issued.key),
        secret=issued.secret,
    )


@router.delete(
    "/companies/{company_id}/integrasjonsnokler/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def stop_integration_key(
    key_id: int,
    context: CurrentCompany,
    db: DbSession,
    post: OutgoingMail,
    background: BackgroundTasks,
) -> None:
    """Stop an integration key at once («Stopp»). Its next request is refused.

    Any owner may stop any of the company's integration keys; the owner who
    made it is mailed. A stopped key cannot be started again. Owners only
    (403 ``permission_denied``); 404 ``agent_key_not_found`` for an id that is
    not one of these books' integration keys.
    """
    require_permission(context.membership.role, Permission.INTEGRATION_MANAGE)
    key = await integration_keys.get_integration_key(
        db, company_id=context.company.id, key_id=key_id
    )
    if key.revoked_at is not None:
        return
    agent_keys.stop_key(key, AgentKeyStop.PERSON)
    tell_owner_stopped(
        background,
        post,
        [key],
        person=key.user,
        company_name=context.company.name,
        reason=AgentKeyStop.PERSON,
    )
