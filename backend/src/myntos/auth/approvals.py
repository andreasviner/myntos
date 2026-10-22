"""Requests a key may only ask for: held at the gate, run when its person says yes.

The rules are docs/agents-and-api.md rule 13; this module is the mechanism.

A person decides, per key, which of ``write`` and ``post`` the agent (or the
program: a key is the same thing to both) may do on its own and which it may
only ask for. A request needing one it may only ask for is stopped in the gate
(:func:`myntos.api.deps.get_caller`) before the route runs, stored whole
(:func:`hold`) and answered 202. When the key's person approves it on
/godkjenning/, it is sent again, through the whole application, as the key
(:func:`run`). Every check the route makes is made then, against the books as
they are then; the voucher it posts says «via» the key like any other; and
nothing here has to know what any route does.

That last point is the design. The alternative was a description of each
action ("post a payment of 1 250 kr to 6800") and code to carry each one out,
which is a second implementation of every route that writes, and the one the
person approved would be the one that drifted from the one that runs.

**The replay is recognised by a key in the ASGI scope** (:data:`REPLAY_SCOPE`),
never by a header. A scope key is set by whoever calls the application, and
from outside that is the server, which never sets it; a header is set by the
client, and a header that said "this was approved" would be the first thing a
stolen key sent.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, NoReturn
from urllib.parse import unquote

import httpx
from fastapi import Request
from fastapi import params as fastapi_params
from fastapi.dependencies.utils import request_body_to_args, request_params_to_args
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.datastructures import UploadFile
from starlette.types import ASGIApp, Receive, Scope, Send

from myntos.core.clock import as_utc, utc_now
from myntos.core.config import get_settings
from myntos.domain.identity.errors import ApprovalNotFoundError, ApprovalTooLargeError
from myntos.domain.identity.models import (
    AgentApproval,
    AgentApprovalFile,
    AgentApprovalKind,
    AgentApprovalStatus,
    AgentKey,
    AgentKeyScope,
    FilingType,
)
from myntos.domain.ledger.models import Company
from myntos.domain.mva import naering as mva_naering
from myntos.domain.mva.models import FilingStatus, MvaFiling
from myntos.skattemelding.models import SkattemeldingFiling

logger = logging.getLogger(__name__)

# Where a replayed request carries the approval it runs for. See the module
# docstring for why this is a scope key and not a header.
REPLAY_SCOPE = "myntos.approved_request"

# The agent's one sentence for the person: what it is doing and why.
NOTE_HEADER = "Myntos-Note"
_NOTE_LENGTH = 500

# What is kept of a route's answer, for the agent to read afterwards. The
# answers that matter (a voucher, an invoice) are a few kilobytes.
_RESULT_LENGTH = 64 * 1024


@dataclass(frozen=True)
class Replay:
    """The approval a replayed request runs for, and the key it runs as."""

    approval_id: int
    key_id: int


class ApprovalHeld(Exception):  # noqa: N818 - not an error: the request is stored and waiting
    """Raised by the gate once a request has been stored to wait, and answered 202.

    An exception because the gate is a dependency and a dependency cannot
    answer; it can only let the route run or stop it.
    """

    def __init__(self, approval: AgentApproval) -> None:
        super().__init__(approval.id)
        self.approval_id = approval.id
        self.company_id = approval.company_id
        self.expires_at = approval.expires_at


def read_note(raw: str | None) -> str | None:
    """The agent's sentence from ``Myntos-Note``, readable whatever it sent.

    The skill asks for percent-encoded UTF-8, because a header is Latin-1 on
    the wire and «føre» is not ASCII. Plenty of HTTP clients send the UTF-8
    bytes raw instead, which arrive here as Latin-1 mojibake; those are put
    back. One line, cut short: it is shown in a list.
    """
    if raw is None:
        return None
    try:
        text = raw.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        text = raw
    text = " ".join(unquote(text).split())
    return text[:_NOTE_LENGTH] or None


# --- Holding ----------------------------------------------------------------------


async def _received_body(request: Request, route: APIRoute) -> Any:
    """The body as FastAPI hands it to its validation, read the way it reads it."""
    field = route.body_field
    if field is not None and isinstance(field.field_info, fastapi_params.Form):
        return await request.form()
    raw = await request.body()
    if not raw:
        return None
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type == "application/json" or content_type.endswith("+json"):
        return await request.json()
    return raw


async def _refuse_what_would_be_refused(request: Request, route: APIRoute) -> None:
    """Answer 422 now for a request that would answer 422 when it ran.

    The gate is a dependency, and FastAPI resolves dependencies before it
    validates the route's own parameters and body, so without this a request
    with a misspelt field would be stored, answered 202, shown to the person,
    approved, and only then refused. The agent would learn of its typo a day
    later, from the person. So the route's own parameters are validated here
    the way FastAPI is about to, with the same functions.

    ``request_body_to_args`` and ``_embed_body_fields`` are FastAPI's
    internals. The version is pinned in uv.lock, and
    ``test_a_malformed_request_is_refused_not_held`` fails if an upgrade moves
    them.
    """
    dependant = route.dependant
    errors: list[Any] = []
    _, found = request_params_to_args(dependant.path_params, request.path_params)
    errors.extend(found)
    _, found = request_params_to_args(dependant.query_params, request.query_params)
    errors.extend(found)
    if dependant.body_params:
        _, found = await request_body_to_args(
            body_fields=dependant.body_params,
            received_body=await _received_body(request, route),
            embed_body_fields=route._embed_body_fields,
        )
        errors.extend(found)
    if errors:
        raise RequestValidationError(errors)


async def hold(
    request: Request, db: AsyncSession, key: AgentKey, *, scope: AgentKeyScope, label: str
) -> NoReturn:
    """Store this request to wait for the key's person, and answer 202.

    Committed before the answer, because the request's own transaction rolls
    back with the exception that ends it.
    """
    route = request.scope.get("route")
    if isinstance(route, APIRoute):
        await _refuse_what_would_be_refused(request, route)

    settings = get_settings()
    content_type = request.headers.get("content-type")
    body: bytes | None = None
    form: str | None = None
    files: list[AgentApprovalFile] = []
    if content_type and content_type.lower().startswith("multipart/form-data"):
        fields: dict[str, list[str]] = {}
        for name, value in (await request.form()).multi_items():
            if isinstance(value, UploadFile):
                content = await value.read()
                await value.seek(0)
                files.append(
                    AgentApprovalFile(
                        field=name[:64],
                        attach=False,
                        filename=(value.filename or "fil")[:255],
                        content_type=(value.content_type or "application/octet-stream")[:255],
                        size=len(content),
                        content=content,
                    )
                )
            else:
                fields.setdefault(name, []).append(value)
        form = json.dumps(fields, ensure_ascii=False)
    else:
        body = await request.body() or None

    size = len(body or b"") + len((form or "").encode()) + sum(item.size for item in files)
    if size > settings.agent_approval_max_bytes:
        raise ApprovalTooLargeError(
            "forespørselen er for stor til å vente på godkjenning. Del den opp, for eksempel "
            "ett bilag om gangen."
        )

    now = utc_now()
    approval = AgentApproval(
        company_id=key.company_id,
        key_id=key.id,
        kind=AgentApprovalKind.REQUEST,
        status=AgentApprovalStatus.PENDING,
        scope=scope.value,
        label=label,
        note=read_note(request.headers.get(NOTE_HEADER)),
        method=request.method,
        path=request.scope["path"],
        query=request.scope.get("query_string", b"").decode("latin-1"),
        content_type=content_type,
        body=body,
        form=form,
        created_at=now,
        expires_at=now + timedelta(days=settings.agent_approval_lifetime_days),
        files=files,
    )
    db.add(approval)
    await db.commit()
    raise ApprovalHeld(approval)


# --- Reading ----------------------------------------------------------------------


def status_of(approval: AgentApproval, *, now: datetime) -> str:
    """The stored status, or what a pending one has become: ``expired``, ``key_stopped``.

    Derived on read, like a key's ``expired``. ``approval.key`` must be loaded.
    """
    if approval.status is AgentApprovalStatus.PENDING:
        key = approval.key
        if key.revoked_at is not None or as_utc(key.expires_at) <= now:
            return "key_stopped"
        if as_utc(approval.expires_at) <= now:
            return "expired"
    return approval.status.value


def operation_for(app: ASGIApp, method: str, path: str) -> dict[str, Any] | None:
    """The OpenAPI operation a stored request goes to, with everything it declares.

    Read from the published document rather than the router: FastAPI keeps
    included routers nested and lazily resolved, and the document is the
    public, flat statement of what each route is, extensions included. The
    template with the fewest parameters wins, so a literal segment
    (``…/innsending``) is preferred to a parameter (``…/{approval_id}``) as the
    router prefers it.
    """
    openapi = getattr(app, "openapi", None)
    if openapi is None:
        return None
    paths: dict[str, dict[str, Any]] = openapi()["paths"]
    for template in sorted(paths, key=lambda each: each.count("{")):
        literal = re.split(r"\{[^/{}]+\}", template)
        pattern = "[^/]+".join(re.escape(part) for part in literal)
        if re.fullmatch(pattern, path):
            operation: dict[str, Any] | None = paths[template].get(method.lower())
            if operation is not None:
                return operation
    return None


async def list_approvals(
    db: AsyncSession,
    *,
    company_id: int,
    key_id: int | None = None,
    user_id: int | None = None,
    pending_only: bool = False,
) -> list[AgentApproval]:
    """The company's requests, newest first, narrowed to one key or one person's keys.

    A key sees what it asked itself. A person sees what their own keys asked,
    and somebody with ``member:manage`` everybody's, as with the keys
    themselves (:func:`myntos.auth.agent_keys.list_keys`).
    """
    query = (
        select(AgentApproval)
        .join(AgentKey, AgentKey.id == AgentApproval.key_id)
        .where(AgentApproval.company_id == company_id)
        .options(
            selectinload(AgentApproval.key).selectinload(AgentKey.user),
            selectinload(AgentApproval.files),
        )
        .order_by(AgentApproval.created_at.desc(), AgentApproval.id.desc())
        .limit(200)
    )
    if key_id is not None:
        query = query.where(AgentApproval.key_id == key_id)
    if user_id is not None:
        query = query.where(AgentKey.user_id == user_id)
    if pending_only:
        query = query.where(AgentApproval.status == AgentApprovalStatus.PENDING)
    return list(await db.scalars(query))


async def get_approval(
    db: AsyncSession, *, company_id: int, approval_id: int, reload: bool = False
) -> AgentApproval:
    """One request, looked up with its company like every other sub-resource.

    ``reload`` reads the row again over what the session holds, for after a
    run, which wrote it from a statement the session never saw.
    """
    query = (
        select(AgentApproval)
        .where(AgentApproval.id == approval_id, AgentApproval.company_id == company_id)
        .options(
            selectinload(AgentApproval.key).selectinload(AgentKey.user),
            selectinload(AgentApproval.files),
        )
    )
    if reload:
        query = query.execution_options(populate_existing=True)
    approval = await db.scalar(query)
    if approval is None:
        raise ApprovalNotFoundError("fant ingen slik forespørsel")
    return approval


async def forget_files(db: AsyncSession, *, company_id: int) -> None:
    """Empty the stored files of every request in the company that no longer waits.

    Run, a file is in the bilag archive; declined, withdrawn, expired or left
    behind by a stopped key, there is nothing to keep it for. Done in one
    statement whenever the list is read, rather than in every place a key can
    stop, of which there are several and will be more.
    """
    now = utc_now()
    over = (
        select(AgentApproval.id)
        .join(AgentKey, AgentKey.id == AgentApproval.key_id)
        .where(
            AgentApproval.company_id == company_id,
            or_(
                AgentApproval.status != AgentApprovalStatus.PENDING,
                AgentApproval.expires_at <= now,
                AgentKey.revoked_at.is_not(None),
                AgentKey.expires_at <= now,
            ),
        )
    )
    await db.execute(
        update(AgentApprovalFile)
        .where(AgentApprovalFile.content.is_not(None), AgentApprovalFile.approval_id.in_(over))
        .values(content=None)
        .execution_options(synchronize_session=False)
    )


async def settle_filings(db: AsyncSession, approvals: list[AgentApproval]) -> None:
    """Mark a request to file as done once the person has filed that period.

    Approving a request to file *is* filing it, on the MVA page with the
    person's own ID-porten login, so nothing presses a button here: the
    filing appearing is the answer. Checked when the requests are read.

    A period the person records as filed from another program is done too:
    there is nothing left to file, and the request would otherwise wait
    until it expired for something nobody will do.
    """
    for approval in approvals:
        if (
            approval.kind is not AgentApprovalKind.FILING
            or approval.status is not AgentApprovalStatus.PENDING
        ):
            continue
        if approval.filing_type == FilingType.SKATTEMELDING.value:
            sent = await db.scalar(
                select(SkattemeldingFiling)
                .where(
                    SkattemeldingFiling.company_id == approval.company_id,
                    SkattemeldingFiling.year == approval.filing_year,
                    SkattemeldingFiling.submitted_at.is_not(None),
                )
                .order_by(SkattemeldingFiling.submitted_at.desc())
                .limit(1)
            )
            if sent is not None:
                approval.status = AgentApprovalStatus.DONE
                approval.decided_at = sent.submitted_at
                approval.decided_by_user_id = sent.submitted_by_user_id
                approval.result = json.dumps({"filing_id": sent.id})
            continue
        query = select(MvaFiling).where(
            MvaFiling.company_id == approval.company_id,
            MvaFiling.year == approval.filing_year,
            MvaFiling.period_index == approval.filing_period,
            or_(
                MvaFiling.submitted_at.is_not(None),
                MvaFiling.status == FilingStatus.ANNET_PROGRAM.value,
            ),
        )
        category = approval.filing_category
        if category is None:
            # The main melding. With one melding, any category: a company that
            # changed category still filed the period it was asked about. With
            # two, the second melding's filing of the same number is not it.
            company = await db.get(Company, approval.company_id)
            if company is not None:
                setup = await mva_naering.load(db, company)
                if setup.is_split:
                    category = setup.main.category.value
        if category is not None:
            query = query.where(MvaFiling.category == category)
        filed = await db.scalar(query.order_by(MvaFiling.validated_at.desc()).limit(1))
        if filed is None:
            continue
        approval.status = AgentApprovalStatus.DONE
        if filed.submitted_at is not None:
            approval.decided_at = filed.submitted_at
            approval.decided_by_user_id = filed.submitted_by_user_id
        else:
            approval.decided_at = filed.validated_at
            approval.decided_by_user_id = filed.created_by_user_id
        approval.result = json.dumps({"filing_id": filed.id})


# --- Running ----------------------------------------------------------------------


@dataclass(frozen=True)
class _Answer:
    status: int
    content: bytes
    content_type: str | None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def parsed(self) -> Any:
        if self.content_type and "json" in self.content_type:
            try:
                return json.loads(self.content)
            except ValueError:
                pass
        return self.content[:_RESULT_LENGTH].decode("utf-8", errors="replace") or None


async def _send(
    app: ASGIApp,
    replay: Replay,
    *,
    method: str,
    path: str,
    query: str = "",
    content: bytes | None = None,
    content_type: str | None = None,
    fields: dict[str, list[str]] | None = None,
    files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
) -> _Answer:
    """Send one request through the whole application, as the approved key.

    Through httpx's ASGI transport rather than a scope built by hand, because
    the transport already speaks every part of ASGI a request can touch (the
    body, a disconnect, background tasks after the response), and the one
    thing it cannot do, add a key to the scope, is a three-line wrapper.
    """

    async def as_approved(scope: Scope, receive: Receive, send: Send) -> None:
        await app({**scope, REPLAY_SCOPE: replay}, receive, send)

    transport = httpx.ASGITransport(app=as_approved, raise_app_exceptions=False)
    target = path + (f"?{query}" if query else "")
    async with httpx.AsyncClient(transport=transport, base_url="http://myntos.internal") as client:
        if files:
            response = await client.request(method, target, data=fields or {}, files=files)
        else:
            headers = {"content-type": content_type} if content_type else {}
            response = await client.request(method, target, content=content or b"", headers=headers)
    return _Answer(response.status_code, response.content, response.headers.get("content-type"))


@dataclass(frozen=True)
class Claimed:
    """What a claimed request needs to run, copied out before the claim is committed.

    Copied, because after the commit nothing may read the row again until the
    run is over: on SQLite a read in the outer session can hold the lock the
    replayed request needs to write.
    """

    replay: Replay
    company_id: int
    method: str
    path: str
    query: str
    content_type: str | None
    body: bytes | None
    form: dict[str, list[str]] | None
    form_files: list[tuple[str, tuple[str, bytes, str]]]
    attachments: list[tuple[str, bytes, str]]
    attachments_route: str | None

    @classmethod
    def of(cls, approval: AgentApproval, attachments_route: str | None) -> Claimed:
        return cls(
            replay=Replay(approval_id=approval.id, key_id=approval.key_id),
            company_id=approval.company_id,
            method=approval.method,
            path=approval.path,
            query=approval.query,
            content_type=approval.content_type,
            body=approval.body,
            form=json.loads(approval.form) if approval.form is not None else None,
            form_files=[
                (item.field, (item.filename, item.content or b"", item.content_type))
                for item in approval.files
                if not item.attach
            ],
            attachments=[
                (item.filename, item.content or b"", item.content_type)
                for item in approval.files
                if item.attach
            ],
            attachments_route=attachments_route,
        )


@dataclass(frozen=True)
class Outcome:
    ok: bool
    status: int
    result: str


async def run(app: ASGIApp, claimed: Claimed) -> Outcome:
    """Send a claimed request, then its attachments to the voucher it made.

    An attachment that fails does not fail the request: the payment is posted
    by then and a posted voucher is history, so the outcome says which file
    did not reach the archive, for the person to add by hand.
    """
    try:
        answer = await _send(
            app,
            claimed.replay,
            method=claimed.method,
            path=claimed.path,
            query=claimed.query,
            content=claimed.body,
            content_type=claimed.content_type,
            fields=claimed.form,
            files=claimed.form_files or None,
        )
    except Exception:  # the row must not be left saying it is still running
        logger.exception("approved request %s could not be sent", claimed.replay.approval_id)
        return Outcome(ok=False, status=500, result=json.dumps({"body": None}))

    parsed = answer.parsed()
    attached: list[dict[str, Any]] = []
    target = parsed.get("id") if isinstance(parsed, dict) else None
    if answer.ok and claimed.attachments and claimed.attachments_route and target is not None:
        path = claimed.attachments_route.format(company_id=claimed.company_id, id=target)
        for filename, content, content_type in claimed.attachments:
            sent = await _send(
                app,
                claimed.replay,
                method="POST",
                path=path,
                fields={"filename": [filename]},
                files=[("file", (filename, content, content_type))],
            )
            attached.append({"filename": filename, "status": sent.status, "body": sent.parsed()})

    result = json.dumps({"body": parsed, "attachments": attached}, ensure_ascii=False, default=str)
    if len(result) > _RESULT_LENGTH:
        result = json.dumps({"body": None, "attachments": attached, "truncated": True})
    return Outcome(ok=answer.ok, status=answer.status, result=result)
