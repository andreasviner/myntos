"""Requests that wait for a person's approval, from both ends.

**An agent's end** (or a program's: a key is the same thing to both): see what
it asked and how it went, withdraw it, add a receipt to a waiting payment, and
ask its person to file something to the state. Every one of these declares
``KEY_READ``, because none of them changes the books, and because a key that
may only *ask* to write must still be able to look after what it asked; a
waiting request about waiting requests would wait for ever.

**A person's end** is /godkjenning/: approve, which runs the request as the
key (:func:`myntos.auth.approvals.run`), or decline, with a reason the agent
can read. Neither declares a key scope. A key approving what keys ask would
make approval mean nothing (docs/agents-and-api.md rule 13).

The mechanism is :mod:`myntos.auth.approvals`; the rules are rule 13.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from myntos.api.deps import (
    KEY_APPROVAL_ATTACHMENTS_EXTENSION,
    KEY_READ,
    CompanyContext,
    CurrentCaller,
    CurrentCompany,
    CurrentUser,
    DbSession,
)
from myntos.auth import approvals
from myntos.core.clock import as_utc, utc_now
from myntos.core.config import get_settings
from myntos.domain.bilag import service as bilag_service
from myntos.domain.identity.access import Permission, has_permission, require_permission
from myntos.domain.identity.errors import (
    AgentKeyRequiredError,
    AlreadyFiledError,
    ApprovalNotFoundError,
    ApprovalNotPendingError,
    ApprovalTooLargeError,
    AttachmentsNotAcceptedError,
    FilingIsDoneOnThePageError,
    NotYourAgentError,
)
from myntos.domain.identity.models import (
    AgentApproval,
    AgentApprovalFile,
    AgentApprovalKind,
    AgentApprovalStatus,
    FilingType,
    User,
)
from myntos.domain.mva import service as mva_service
from myntos.domain.mva.errors import NotVatRegisteredError, PeriodNotOverError
from myntos.domain.mva.models import FilingStatus, MvaFiling

router = APIRouter(tags=["godkjenning"])

_OSLO = ZoneInfo("Europe/Oslo")

ApprovalState = Literal[
    "pending", "running", "done", "failed", "declined", "withdrawn", "expired", "key_stopped"
]


class ApprovalFileResponse(BaseModel):
    id: int
    filename: str
    content_type: str
    size: int
    # A file of the request's own form (false), or a bilag added to it, for the
    # voucher it will make (true).
    attach: bool
    # Whether it can still be downloaded. Emptied once the request no longer
    # waits: run, it is in the bilag archive.
    available: bool


class FilingRequest(BaseModel):
    type: FilingType
    aar: int
    termin: int


class ApprovalResponse(BaseModel):
    id: int
    company_id: int
    # request: a call the key made, run when approved. filing: the key asking
    # its person to file something to the state, which the person does on the
    # page that files.
    kind: Literal["request", "filing"]
    status: ApprovalState
    # "write" or "post" for a request; empty for a filing.
    scope: str
    # What is asked, in bokmål, as the end of «<key> vil …»: «føre en betaling».
    label: str
    # The agent's own sentence about it. Written by the agent, and so to be
    # read as the agent's claim, beside what was actually sent.
    note: str | None
    method: str
    path: str
    query: str
    # The JSON the key sent, when it sent JSON.
    body: Any | None
    # A form's text fields, when it sent a form.
    form: dict[str, list[str]] | None
    files: list[ApprovalFileResponse]
    filing: FilingRequest | None
    key_id: int
    key_name: str
    person_name: str
    # Whether the caller is the key's person, who alone may approve.
    is_own: bool
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    decision_note: str | None
    # What the route answered when it ran, and what each attached bilag did.
    result_status: int | None
    result: Any | None
    attachments: list[dict[str, Any]]
    # The page where the person sees it.
    url: str


def _body_of(approval: AgentApproval) -> Any | None:
    if approval.body is None or "json" not in (approval.content_type or ""):
        return None
    try:
        return json.loads(approval.body)
    except ValueError:
        return None


def _response(approval: AgentApproval, *, user_id: int | None) -> ApprovalResponse:
    stored: dict[str, Any] = json.loads(approval.result) if approval.result else {}
    base = get_settings().frontend_base_url.rstrip("/")
    filing = None
    if approval.kind is AgentApprovalKind.FILING and approval.filing_type is not None:
        filing = FilingRequest(
            type=FilingType(approval.filing_type),
            aar=approval.filing_year or 0,
            termin=approval.filing_period or 0,
        )
    return ApprovalResponse(
        id=approval.id,
        company_id=approval.company_id,
        kind=approval.kind.value,
        status=approvals.status_of(approval, now=utc_now()),
        scope=approval.scope,
        label=approval.label,
        note=approval.note,
        method=approval.method,
        path=approval.path,
        query=approval.query,
        body=_body_of(approval),
        form=json.loads(approval.form) if approval.form else None,
        files=[
            ApprovalFileResponse(
                id=item.id,
                filename=item.filename,
                content_type=item.content_type,
                size=item.size,
                attach=item.attach,
                available=item.content is not None,
            )
            for item in approval.files
        ],
        filing=filing,
        key_id=approval.key_id,
        key_name=approval.key.name,
        person_name=approval.key.user.full_name,
        is_own=user_id is not None and approval.key.user_id == user_id,
        created_at=as_utc(approval.created_at),
        expires_at=as_utc(approval.expires_at),
        decided_at=None if approval.decided_at is None else as_utc(approval.decided_at),
        decision_note=approval.decision_note,
        result_status=approval.result_status,
        result=stored.get("body", stored.get("filing_id")),
        attachments=stored.get("attachments", []),
        url=f"{base}/godkjenning/#godkjenning-{approval.id}",
    )


def _may_see(approval: AgentApproval, caller: CurrentCaller, context: CompanyContext) -> bool:
    """A key sees its own; a person their keys'; a manager everybody's."""
    if caller.key is not None:
        return approval.key_id == caller.key.key.id
    if approval.key.user_id == context.membership.user_id:
        return True
    return has_permission(context.membership.role, Permission.MEMBER_MANAGE)


async def _visible(
    db: DbSession, caller: CurrentCaller, context: CompanyContext, approval_id: int
) -> AgentApproval:
    approval = await approvals.get_approval(
        db, company_id=context.company.id, approval_id=approval_id
    )
    if not _may_see(approval, caller, context):
        # The same 404 as an id that does not exist, as for keys.
        raise ApprovalNotFoundError("fant ingen slik forespørsel")
    return approval


def _require_pending(approval: AgentApproval) -> None:
    state = approvals.status_of(approval, now=utc_now())
    if state == "pending":
        return
    reasons = {
        "expired": "forespørselen ventet for lenge og har gått ut",
        "key_stopped": "nøkkelen som ba om dette er stoppet",
        "running": "forespørselen kjøres allerede",
    }
    raise ApprovalNotPendingError(reasons.get(state, "forespørselen er allerede avgjort"))


def _attachments_route(request: Request, approval: AgentApproval) -> str | None:
    """Where bilag added to this waiting request go once it has run, if anywhere."""
    operation = approvals.operation_for(request.app, approval.method, approval.path) or {}
    target = operation.get(KEY_APPROVAL_ATTACHMENTS_EXTENSION)
    return target if isinstance(target, str) else None


def _user_id(caller: CurrentCaller, user: User) -> int | None:
    return None if caller.key is not None else user.id


# --- Reading ----------------------------------------------------------------------


@router.get("/companies/{company_id}/godkjenninger", openapi_extra=KEY_READ)
async def list_approvals(
    context: CurrentCompany,
    caller: CurrentCaller,
    user: CurrentUser,
    db: DbSession,
    status_filter: Annotated[Literal["pending", "all"], Query(alias="status")] = "all",
) -> list[ApprovalResponse]:
    """What keys asked to do and how it went, newest first («godkjenning»).

    A key whose person chose «spør meg først» for writing or posting does not
    do those things itself: each such call is stored, answered 202
    ``approval_pending``, and waits here until the person approves or declines
    it in Myntos. This lists those requests, and the requests to file
    (``POST …/godkjenninger/innsending``).

    With a key: only what this key asked. Signed in: what your own keys asked,
    and with ``member:manage`` everybody's. ``status=pending`` narrows it to
    what still waits (stored as waiting; a few of those may show as
    ``expired`` or ``key_stopped``).

    ``status`` on each: ``pending`` waits; ``done`` ran and ``result`` is what
    the route answered (a voucher, an invoice); ``failed`` ran and was refused,
    ``result`` says why; ``declined`` (``decision_note`` may say why),
    ``withdrawn``, ``expired`` (not decided within 14 days) and
    ``key_stopped`` were never run. ``running`` is a request being run right
    now, or one that stopped mid-run; check the books before asking again.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    everyone = caller.key is None and has_permission(
        context.membership.role, Permission.MEMBER_MANAGE
    )
    await approvals.forget_files(db, company_id=context.company.id)
    found = await approvals.list_approvals(
        db,
        company_id=context.company.id,
        key_id=caller.key.key.id if caller.key is not None else None,
        user_id=None if caller.key is not None or everyone else user.id,
        pending_only=status_filter == "pending",
    )
    await approvals.settle_filings(db, found)
    await db.flush()
    shown = [_response(item, user_id=_user_id(caller, user)) for item in found]
    if status_filter == "pending":
        shown = [item for item in shown if item.status == "pending"]
    return shown


@router.get("/companies/{company_id}/godkjenninger/{approval_id}", openapi_extra=KEY_READ)
async def get_approval(
    approval_id: int,
    context: CurrentCompany,
    caller: CurrentCaller,
    user: CurrentUser,
    db: DbSession,
) -> ApprovalResponse:
    """One waiting or decided request: the address in ``Location`` on a 202.

    Ask this to learn how a request that answered 202 ``approval_pending``
    went. Not more than once a minute or so: a person approves when they get
    to it, which may be tomorrow. When ``status`` is ``done``, ``result`` is
    exactly what the route would have answered had the key been allowed to
    call it directly (for a payment: the voucher, with its ``id``), and
    ``attachments`` says how each bilag added to it fared.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    approval = await _visible(db, caller, context, approval_id)
    await approvals.settle_filings(db, [approval])
    await db.flush()
    return _response(approval, user_id=_user_id(caller, user))


@router.get("/companies/{company_id}/godkjenninger/{approval_id}/fil", openapi_extra=KEY_READ)
async def get_approval_file(
    approval_id: int,
    context: CurrentCompany,
    caller: CurrentCaller,
    db: DbSession,
    file_id: Annotated[int, Query(alias="id")],
) -> Response:
    """Download a file sent with a waiting request: the receipt the person is asked about.

    Only while the request waits. Once it has run, the file is in the bilag
    archive (``GET /api/v1/companies/{company_id}/dokument/{attachment_id}``);
    declined or expired, it is gone. Either way this answers 404 then.

    The file is named by ``?id=`` rather than a second path segment, so the
    request stays the only sub-resource in the path, which is what the company
    wall is checked on (docs/company-isolation.md).
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    approval = await _visible(db, caller, context, approval_id)
    item = next((each for each in approval.files if each.id == file_id), None)
    if item is None or item.content is None:
        raise ApprovalNotFoundError("filen er ikke lagret her lenger")
    return Response(
        content=item.content,
        media_type=item.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{_safe_filename(item.filename)}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def _safe_filename(name: str) -> str:
    return "".join(char if char.isalnum() or char in "._- " else "_" for char in name)[:120]


# --- An agent's end ---------------------------------------------------------------


@router.delete(
    "/companies/{company_id}/godkjenninger/{approval_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=KEY_READ,
)
async def withdraw_approval(
    approval_id: int,
    context: CurrentCompany,
    caller: CurrentCaller,
    user: CurrentUser,
    db: DbSession,
) -> None:
    """Withdraw a request that still waits, so it can never run.

    For an agent that asked for the wrong thing, or asked twice: withdraw the
    one that should not happen, then ask again if it needs to. With a key, its
    own requests only; signed in, the key's person. Refused with 409
    ``approval_not_pending`` once it has been decided or has expired.
    """
    approval = await _visible(db, caller, context, approval_id)
    if caller.key is None and approval.key.user_id != user.id:
        raise NotYourAgentError("bare den som laget nøkkelen kan trekke tilbake det den ba om")
    _require_pending(approval)
    approval.status = AgentApprovalStatus.WITHDRAWN
    approval.decided_at = utc_now()
    approval.decided_by_user_id = None if caller.key is not None else user.id
    for item in approval.files:
        item.content = None


@router.post(
    "/companies/{company_id}/godkjenninger/{approval_id}/vedlegg",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=KEY_READ,
)
async def add_attachment(
    approval_id: int,
    request: Request,
    context: CurrentCompany,
    caller: CurrentCaller,
    user: CurrentUser,
    db: DbSession,
    file: Annotated[UploadFile, File()],
    filename: Annotated[str | None, Form()] = None,
) -> ApprovalResponse:
    """Add a bilag (a receipt, an invoice: a PDF or a photo) to a payment that waits for approval.

    A payment posted with a key that may only ask to post is not a voucher
    yet, so there is nothing to attach a document to with
    ``POST /api/v1/companies/{company_id}/cashflow/{voucher_id}/dokument``.
    Add it here instead: the person sees it beside the payment when they
    decide, and it goes into the bilag archive on the new voucher when the
    payment runs. Accepted only on requests whose route makes a voucher that
    documents belong to (409 ``attachments_not_accepted`` otherwise); the same
    file checks as the archive (415 ``unsupported_document``, 413
    ``document_too_large``).
    """
    approval = await _visible(db, caller, context, approval_id)
    if caller.key is None and approval.key.user_id != user.id:
        raise NotYourAgentError("bare den som laget nøkkelen kan legge til i det den ba om")
    _require_pending(approval)
    if _attachments_route(request, approval) is None:
        raise AttachmentsNotAcceptedError(
            "denne forespørselen lager ikke et bilag som dokumenter kan legges ved"
        )
    content = await file.read()
    content_type = bilag_service.validate_document(content)
    stored = sum(item.size for item in approval.files) + len(approval.body or b"")
    if stored + len(content) > get_settings().agent_approval_max_bytes:
        raise ApprovalTooLargeError("forespørselen kan ikke bære flere filer")
    approval.files.append(
        AgentApprovalFile(
            field="file",
            attach=True,
            filename=(filename or file.filename or "bilag")[:255],
            content_type=content_type,
            size=len(content),
            content=content,
        )
    )
    await db.flush()
    return _response(approval, user_id=_user_id(caller, user))


@router.post("/companies/{company_id}/godkjenninger/innsending", openapi_extra=KEY_READ)
async def request_filing(
    payload: FilingRequest,
    request: Request,
    context: CurrentCompany,
    caller: CurrentCaller,
    user: CurrentUser,
    db: DbSession,
) -> ApprovalResponse:
    """Ask the person to file something to the state: today, the MVA-melding for one termin.

    No key can file to Skatteetaten or Altinn, whatever it holds: Skatteetaten
    requires the person's own ID-porten login (docs/agents-and-api.md rule
    5). What an agent can do is prepare and check (``GET
    /api/v1/companies/{company_id}/mva/termin``), and then ask. This puts the
    request on the person's «Godkjenning» page, with a link to the MVA page
    where they log in, check the melding and send it themselves. It is marked
    ``done`` once that termin has been filed, by anyone.

    ``type`` is ``mva_melding``; ``aar`` and ``termin`` name the period as the
    MVA page does («3. termin 2026»). Send the reason in the ``Myntos-Note``
    header. Asking again for a termin that already waits returns the request
    that waits. Refused: 409 ``already_filed`` for a termin filed already, 409
    ``period_not_over`` before the termin has ended, 409
    ``not_vat_registered`` for a company that files no MVA-melding, 422
    ``invalid_period`` for a termin that does not exist. Only with a key (403
    ``agent_key_required``): a person files on the page.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    if caller.key is None:
        raise AgentKeyRequiredError("en person sender mva-meldingen selv, på MVA-siden")
    company = context.company
    if not company.vat_registered:
        raise NotVatRegisteredError(
            "selskapet er ikke registrert i Merverdiavgiftsregisteret, så det sendes ingen "
            "mva-melding"
        )
    termin = mva_service.termin_of(company, payload.aar, payload.termin)
    # Oslo's date, as the MVA page has it: a termin ends at midnight here.
    if not mva_service.is_past(termin, utc_now().astimezone(_OSLO).date()):
        raise PeriodNotOverError(
            f"terminen slutter {termin.end.strftime('%d.%m.%Y')}; meldingen kan sendes fra "
            "dagen etter"
        )
    filed = await db.scalar(
        select(MvaFiling.id).where(
            MvaFiling.company_id == company.id,
            MvaFiling.year == termin.year,
            MvaFiling.period_type == termin.period_type.value,
            MvaFiling.period_index == termin.index,
            MvaFiling.status.in_([FilingStatus.SENDT.value, FilingStatus.MOTTATT.value]),
        )
    )
    if filed is not None:
        raise AlreadyFiledError(f"{termin.index}. termin {termin.year} er allerede sendt")

    waiting = await db.scalar(
        select(AgentApproval).where(
            AgentApproval.company_id == company.id,
            AgentApproval.kind == AgentApprovalKind.FILING,
            AgentApproval.status == AgentApprovalStatus.PENDING,
            AgentApproval.filing_type == payload.type.value,
            AgentApproval.filing_year == termin.year,
            AgentApproval.filing_period == termin.index,
            AgentApproval.expires_at > utc_now(),
        )
    )
    if waiting is None:
        now = utc_now()
        waiting = AgentApproval(
            company_id=company.id,
            key_id=caller.key.key.id,
            kind=AgentApprovalKind.FILING,
            status=AgentApprovalStatus.PENDING,
            scope="",
            label=f"at du sender mva-meldingen for {termin.index}. termin {termin.year}",
            note=approvals.read_note(request.headers.get(approvals.NOTE_HEADER)),
            method="",
            path="",
            query="",
            filing_type=payload.type.value,
            filing_year=termin.year,
            filing_period=termin.index,
            created_at=now,
            expires_at=now + timedelta(days=get_settings().agent_approval_lifetime_days),
            files=[],
        )
        db.add(waiting)
        await db.flush()
    approval = await approvals.get_approval(db, company_id=company.id, approval_id=waiting.id)
    return _response(approval, user_id=None)


# --- A person's end ---------------------------------------------------------------


@router.post("/companies/{company_id}/godkjenninger/{approval_id}/godkjenn")
async def approve(
    approval_id: int,
    request: Request,
    context: CurrentCompany,
    caller: CurrentCaller,
    user: CurrentUser,
    db: DbSession,
) -> ApprovalResponse:
    """Approve what a key asked, and run it now, as the key.

    Only the key's own person, signed in: the key works with their
    permissions and registers in their name, so the yes has to be theirs (403
    ``not_your_agent`` for anybody else). The request is sent exactly as the
    key sent it, through every check its route makes, against the books as
    they are now; it can still be refused then, and the answer says so
    (``status`` ``failed``, ``result`` the refusal). A voucher it posts says
    «via» the key, like one the key had posted itself.

    A request to file is not approved here: the person files on the MVA page,
    and the request is marked done when they have (409
    ``filing_is_done_on_the_page``). No key may call this (rule 13).
    """
    approval = await _visible(db, caller, context, approval_id)
    if approval.kind is AgentApprovalKind.FILING:
        raise FilingIsDoneOnThePageError(
            "en mva-melding sender du selv på MVA-siden, med din egen ID-porten-innlogging"
        )
    if approval.key.user_id != user.id:
        raise NotYourAgentError(
            f"bare {approval.key.user.full_name}, som laget nøkkelen, kan godkjenne det den ber om"
        )
    _require_pending(approval)
    app = request.app
    claimed = approvals.Claimed.of(approval, _attachments_route(request, approval))

    # Claimed and committed before it runs, so a second press, or a second tab,
    # finds it running and runs nothing. The run is its own request with its
    # own transaction (myntos.auth.approvals), so nothing here may hold the
    # database while it goes.
    claim = await db.execute(
        update(AgentApproval)
        .where(
            AgentApproval.id == approval.id,
            AgentApproval.status == AgentApprovalStatus.PENDING,
        )
        .values(
            status=AgentApprovalStatus.RUNNING,
            decided_at=utc_now(),
            decided_by_user_id=user.id,
        )
        .execution_options(synchronize_session=False)
    )
    if getattr(claim, "rowcount", 0) != 1:
        raise ApprovalNotPendingError("forespørselen ble avgjort i samme øyeblikk et annet sted")
    await db.commit()

    outcome = await approvals.run(app, claimed)

    await db.execute(
        update(AgentApproval)
        .where(AgentApproval.id == claimed.replay.approval_id)
        .values(
            status=AgentApprovalStatus.DONE if outcome.ok else AgentApprovalStatus.FAILED,
            result_status=outcome.status,
            result=outcome.result,
        )
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        update(AgentApprovalFile)
        .where(AgentApprovalFile.approval_id == claimed.replay.approval_id)
        .values(content=None)
        .execution_options(synchronize_session=False)
    )
    fresh = await approvals.get_approval(
        db, company_id=claimed.company_id, approval_id=claimed.replay.approval_id, reload=True
    )
    return _response(fresh, user_id=user.id)


class DeclineRequest(BaseModel):
    # Why, for the agent to read. Optional: "no" is a complete answer.
    note: str | None = Field(default=None, max_length=1000)


@router.post("/companies/{company_id}/godkjenninger/{approval_id}/avvis")
async def decline(
    approval_id: int,
    payload: DeclineRequest,
    context: CurrentCompany,
    caller: CurrentCaller,
    user: CurrentUser,
    db: DbSession,
) -> ApprovalResponse:
    """Decline what a key asked. It never runs, and the agent can read why.

    The key's person, or somebody with ``member:manage``, who may clear a
    colleague's agent's requests the way they may stop a colleague's key. No
    key may call this (rule 13).
    """
    approval = await _visible(db, caller, context, approval_id)
    if approval.key.user_id != user.id and not has_permission(
        context.membership.role, Permission.MEMBER_MANAGE
    ):
        raise NotYourAgentError("bare den som laget nøkkelen kan avvise det den ber om")
    if approval.status is not AgentApprovalStatus.PENDING:
        raise ApprovalNotPendingError("forespørselen er allerede avgjort")
    approval.status = AgentApprovalStatus.DECLINED
    approval.decided_at = utc_now()
    approval.decided_by_user_id = user.id
    note = " ".join((payload.note or "").split())
    approval.decision_note = note or None
    for item in approval.files:
        item.content = None
    await db.flush()
    return _response(approval, user_id=user.id)
