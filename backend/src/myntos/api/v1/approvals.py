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
make approval mean nothing (docs/agents-and-api.md rule 13). Approving
something that goes to Skatteetaten with the person's ID-porten login needs
that login first, and the response says which requests send anything out.

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
    KEY_ID_PORTEN_EXTENSION,
    KEY_READ,
    KEY_SEND_EXTENSION,
    CompanyContext,
    CurrentCaller,
    CurrentCompany,
    CurrentUser,
    DbSession,
)
from myntos.api.v1.skatt import has_usable_login
from myntos.auth import approval_targets, approvals
from myntos.core.clock import as_utc, utc_now
from myntos.core.config import get_settings
from myntos.domain.bilag import service as bilag_service
from myntos.domain.bilag.models import sniff_content_type
from myntos.domain.identity.access import Permission, has_permission, require_permission
from myntos.domain.identity.errors import (
    AgentKeyRequiredError,
    AlreadyFiledError,
    ApprovalChangedError,
    ApprovalNotFoundError,
    ApprovalNotPendingError,
    ApprovalTooLargeError,
    AttachmentsNotAcceptedError,
    FilingIsDoneOnThePageError,
    IdPortenLoginRequiredError,
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
from myntos.domain.ledger.models import Company
from myntos.domain.mva import naering as mva_naering
from myntos.domain.mva import service as mva_service
from myntos.domain.mva.errors import (
    FiledElsewhereError,
    InvalidPeriodError,
    NotVatRegisteredError,
    PeriodNotOverError,
)
from myntos.skattemelding import service as skattemelding_service
from myntos.skattemelding.errors import SkattemeldingYearNotSupportedError

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
    # The MVA termin. Left out for the skattemelding, which is yearly.
    termin: int | None = None
    # Which melding, by category, for a company that files two
    # (docs/integrations/mva.md «Two returns»); the main one when left out.
    naering: str | None = Field(default=None, max_length=32)


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
    # Whether running it sends something out of Myntos (to Skatteetaten, so
    # far). Such a request waits for its person even when the key may post on
    # its own, and cannot be called back once it has run.
    sends_out: bool
    # Whether it goes with the person's own ID-porten login, which they must
    # hold when they approve (409 id_porten_login_required until they do).
    needs_id_porten: bool
    # The agent's own sentence about it. Written by the agent, and so to be
    # read as the agent's claim, beside what was actually sent.
    note: str | None
    # What it acts on, one line per record its path names, written by Myntos
    # from the books: «Bilag 2026-0014, 14.03.2026: Kontorrekvisita, 1 250,00
    # kr» for a reversal. Filled while it waits (myntos.auth.approval_targets).
    target: list[str]
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


def _declared(app: Any, approval: AgentApproval) -> dict[str, Any]:
    """What the route a request goes to says about itself; nothing for a request to file."""
    if approval.kind is AgentApprovalKind.FILING:
        return {}
    return approvals.operation_for(app, approval.method, approval.path) or {}


def _response(
    approval: AgentApproval,
    *,
    user_id: int | None,
    app: Any,
    target: list[str] | None = None,
) -> ApprovalResponse:
    stored: dict[str, Any] = json.loads(approval.result) if approval.result else {}
    declared = _declared(app, approval)
    base = get_settings().frontend_base_url.rstrip("/")
    filing = None
    if approval.kind is AgentApprovalKind.FILING and approval.filing_type is not None:
        filing = FilingRequest(
            type=FilingType(approval.filing_type),
            aar=approval.filing_year or 0,
            termin=approval.filing_period,
            naering=approval.filing_category,
        )
    return ApprovalResponse(
        id=approval.id,
        company_id=approval.company_id,
        kind=approval.kind.value,
        status=approvals.status_of(approval, now=utc_now()),
        scope=approval.scope,
        label=approval.label,
        sends_out=declared.get(KEY_SEND_EXTENSION) is True,
        needs_id_porten=declared.get(KEY_ID_PORTEN_EXTENSION) is True,
        note=approval.note,
        target=target or [],
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


async def _shown(
    db: DbSession, app: Any, company: Company, approval: AgentApproval, *, user_id: int | None
) -> ApprovalResponse:
    """:func:`_response`, with what a waiting request acts on looked up in the books."""
    target: list[str] = []
    if (
        approval.kind is AgentApprovalKind.REQUEST
        and approvals.status_of(approval, now=utc_now()) == "pending"
    ):
        target = await approval_targets.describe(db, app, company, approval.method, approval.path)
    return _response(approval, user_id=user_id, app=app, target=target)


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
    target = _declared(request.app, approval).get(KEY_APPROVAL_ATTACHMENTS_EXTENSION)
    return target if isinstance(target, str) else None


def _user_id(caller: CurrentCaller, user: User) -> int | None:
    return None if caller.key is not None else user.id


# --- Reading ----------------------------------------------------------------------


@router.get("/companies/{company_id}/godkjenninger", openapi_extra=KEY_READ)
async def list_approvals(
    request: Request,
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
    shown = [
        await _shown(db, request.app, context.company, item, user_id=_user_id(caller, user))
        for item in found
    ]
    if status_filter == "pending":
        shown = [item for item in shown if item.status == "pending"]
    return shown


@router.get("/companies/{company_id}/godkjenninger/{approval_id}", openapi_extra=KEY_READ)
async def get_approval(
    approval_id: int,
    request: Request,
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
    return await _shown(db, request.app, context.company, approval, user_id=_user_id(caller, user))


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
    # The type is what the bytes are, never what the key said they were. A
    # held file keeps the Content-Type of the multipart part it came in, which
    # is the key's to choose, and this file is the one the person is asked to
    # open before approving: «kvittering.pdf» sent as text/html, served inline,
    # was a page of the key's own script running in the person's session. What
    # is not a document we take goes out as a download.
    sniffed = sniff_content_type(item.content)
    return Response(
        content=item.content,
        media_type=sniffed or "application/octet-stream",
        headers={
            "Content-Disposition": (
                f"{'inline' if sniffed else 'attachment'}; "
                f'filename="{_safe_filename(item.filename)}"'
            ),
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
    return await _shown(db, request.app, context.company, approval, user_id=_user_id(caller, user))


@router.post("/companies/{company_id}/godkjenninger/innsending", openapi_extra=KEY_READ)
async def request_filing(
    payload: FilingRequest,
    request: Request,
    context: CurrentCompany,
    caller: CurrentCaller,
    user: CurrentUser,
    db: DbSession,
) -> ApprovalResponse:
    """Ask the person to file something to the state: an MVA-melding or the skattemelding.

    For a key without ``post``. A key with it can send the check and the
    filing itself (``POST /api/v1/companies/{company_id}/skattemelding/kontroll``
    and the MVA routes alike), and each waits for the person's approval, which
    runs it with their ID-porten login (docs/agents-and-api.md rule 5). This
    one only asks: the agent prepares (``GET
    /api/v1/companies/{company_id}/mva/termin``, ``GET
    /api/v1/companies/{company_id}/skattemelding``), and the request goes on
    the person's «Godkjenning» page with a link to the page where they log
    in, check and send it themselves. It is marked ``done`` once it has been
    filed, by anyone.

    ``type`` is ``mva_melding`` or ``skattemelding``. For ``mva_melding``,
    ``aar`` and ``termin`` name the period as the MVA page does («3. termin
    2026»), and ``naering`` the melding (``alminnelig`` or ``primaernaering``)
    for a company that files two; the main one when left out. For
    ``skattemelding``, ``aar`` is the income year and ``termin`` and
    ``naering`` are left out. Send the reason in the ``Myntos-Note`` header.
    Asking again for something that already waits returns the request that
    waits. An MVA request is also marked ``done`` when the person records the
    termin as filed from another program.

    Refused: 409 ``already_filed`` for something filed already; for
    ``mva_melding``, 409 ``filed_elsewhere`` for a termin recorded as filed
    from another program, 409 ``period_not_over`` before the termin has ended,
    409 ``not_vat_registered`` for a company that files no MVA-melding, 422
    ``invalid_period`` for a termin that does not exist or is left out, 422
    ``unknown_naering`` for a melding the company does not file; for
    ``skattemelding``, 422 ``skattemelding_year_not_supported`` for a year
    Myntos cannot file or one not over yet. Only with a key (403
    ``agent_key_required``): a person files on the page.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    if caller.key is None:
        raise AgentKeyRequiredError("en person sender det selv, på siden der det sendes")
    company = context.company
    if payload.type is FilingType.SKATTEMELDING:
        year, period, category = await _skattemelding_request(db, company, payload)
        label = f"at du sender skattemeldingen for {year}"
    else:
        year, period, category, label = await _mva_request(db, company, payload)

    waiting = await db.scalar(
        select(AgentApproval).where(
            AgentApproval.company_id == company.id,
            AgentApproval.kind == AgentApprovalKind.FILING,
            AgentApproval.status == AgentApprovalStatus.PENDING,
            AgentApproval.filing_type == payload.type.value,
            AgentApproval.filing_year == year,
            AgentApproval.filing_period.is_(None)
            if period is None
            else AgentApproval.filing_period == period,
            AgentApproval.filing_category.is_(None)
            if category is None
            else AgentApproval.filing_category == category,
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
            label=label,
            note=approvals.read_note(request.headers.get(approvals.NOTE_HEADER)),
            method="",
            path="",
            query="",
            filing_type=payload.type.value,
            filing_year=year,
            filing_period=period,
            filing_category=category,
            created_at=now,
            expires_at=now + timedelta(days=get_settings().agent_approval_lifetime_days),
            files=[],
        )
        db.add(waiting)
        await db.flush()
    approval = await approvals.get_approval(db, company_id=company.id, approval_id=waiting.id)
    return _response(approval, user_id=None, app=request.app)


async def _mva_request(
    db: DbSession, company: Company, payload: FilingRequest
) -> tuple[int, int, str | None, str]:
    """The termin an MVA request names, refused as the MVA page would refuse filing it."""
    if not company.vat_registered:
        raise NotVatRegisteredError(
            "selskapet er ikke registrert i Merverdiavgiftsregisteret, så det sendes ingen "
            "mva-melding"
        )
    if payload.termin is None:
        raise InvalidPeriodError("si hvilken termin mva-meldingen gjelder")
    setup = await mva_naering.load(db, company)
    chosen = setup.get(payload.naering)
    termin = chosen.termin(payload.aar, payload.termin)
    # NULL is the main melding, which is all a company with one ever has, so
    # a request from before two meldinger existed still means what it meant.
    category = None if chosen.is_main else chosen.category.value
    # Oslo's date, as the MVA page has it: a termin ends at midnight here.
    if not mva_service.is_past(termin, utc_now().astimezone(_OSLO).date()):
        raise PeriodNotOverError(
            f"terminen slutter {termin.end.strftime('%d.%m.%Y')}; meldingen kan sendes fra "
            "dagen etter"
        )
    filings = await mva_service.filings_for(db, company, termin, chosen, setup)
    if mva_service.latest_filed(filings) is not None:
        raise AlreadyFiledError(f"{termin.index}. termin {termin.year} er allerede sendt")
    if mva_service.standing_elsewhere(filings) is not None:
        raise FiledElsewhereError(
            f"{termin.index}. termin {termin.year} er merket som sendt fra et annet program"
        )
    label = f"at du sender mva-meldingen for {termin.index}. termin {termin.year}" + (
        "" if category is None else f" ({chosen.label.lower()})"
    )
    return termin.year, termin.index, category, label


async def _skattemelding_request(
    db: DbSession, company: Company, payload: FilingRequest
) -> tuple[int, None, None]:
    """The income year a skattemelding request names, refused as the page would refuse it."""
    year = payload.aar
    skattemelding_service.require_supported(year)
    if not skattemelding_service.is_over(company, year, utc_now().astimezone(_OSLO).date()):
        first = skattemelding_service.last_day(company, year) + timedelta(days=1)
        raise SkattemeldingYearNotSupportedError(
            f"skattemeldingen for {year} kan sendes fra {first:%d.%m.%Y}"
        )
    filings = await skattemelding_service.filings_for(db, company, year)
    if skattemelding_service.latest_filed(filings) is not None:
        raise AlreadyFiledError(f"skattemeldingen for {year} er allerede sendt")
    return year, None, None


# --- A person's end ---------------------------------------------------------------


class ApproveRequest(BaseModel):
    # Every file the person was shown with the request, by the ``id`` in
    # ``files``; an empty list when there were none. See ApprovalChangedError.
    files: list[int] = Field(max_length=500)


@router.post("/companies/{company_id}/godkjenninger/{approval_id}/godkjenn")
async def approve(
    approval_id: int,
    payload: ApproveRequest,
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

    A request that sends something out of Myntos with the person's ID-porten
    login (a check or a filing with Skatteetaten) needs that login now: 409
    ``id_porten_login_required`` until they hold one, and the request still
    waits. It then goes with their login, as if they had pressed the button on
    the page themselves.

    A request to file is not approved here: the person files on the MVA page,
    and the request is marked done when they have (409
    ``filing_is_done_on_the_page``). No key may call this (rule 13).

    ``files`` names every file the person was shown with it. A key may add a
    receipt to what waits at any moment, so a request that carries other files
    than those is refused with 409 ``approval_changed``, still waits, and is to
    be looked at again: a yes covers what was on the screen, and nothing the
    key added after that.
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
    # Against the files loaded here, which are the ones Claimed.of copies below
    # and the run sends, so a file added after this check is not run either.
    if sorted(payload.files) != sorted(item.id for item in approval.files):
        raise ApprovalChangedError(
            "forespørselen har fått andre filer enn dem du så. Se over den igjen før du godkjenner."
        )
    app = request.app
    if _declared(app, approval).get(KEY_ID_PORTEN_EXTENSION) is True and not (
        await has_usable_login(db, user)
    ):
        raise IdPortenLoginRequiredError(
            "dette sendes til Skatteetaten med din egen ID-porten-innlogging. Logg inn med "
            "ID-porten, og godkjenn så igjen."
        )
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
    return _response(fresh, user_id=user.id, app=request.app)


class DeclineRequest(BaseModel):
    # Why, for the agent to read. Optional: "no" is a complete answer.
    note: str | None = Field(default=None, max_length=1000)


@router.post("/companies/{company_id}/godkjenninger/{approval_id}/avvis")
async def decline(
    approval_id: int,
    payload: DeclineRequest,
    request: Request,
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
    return _response(approval, user_id=user.id, app=request.app)
