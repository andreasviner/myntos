"""The operator's page, ``/drift/``: every stranger's feedback, and the server's failures.

Four routes, all behind the operator key (:func:`myntos.api.deps.require_operator`)
and none reachable with a person's session or an agent's key. What they show
belongs to no company: the words strangers sent through the feedback box, and
what failed on the server, recorded without anybody's data
(:mod:`myntos.domain.server_errors`). See docs/operator.md.

What they may change is chosen for a key that has leaked. The status of a
feedback item and whether a failure is resolved: wrong values there cost the
operator a re-read. Replying, publishing and deleting stay in the shell
(``python -m myntos.domain.feedback.triage``), because a reply reaches the
sender, a summary reaches the public list, and a deletion cannot be undone,
and a stolen key should be able to do none of them.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from myntos.api.deps import DbSession, require_operator
from myntos.core.clock import as_utc
from myntos.domain.feedback import service as feedback
from myntos.domain.feedback.models import Feedback, FeedbackKind, FeedbackSource, FeedbackStatus
from myntos.domain.server_errors import service as server_errors
from myntos.domain.server_errors.models import ServerError
from myntos.domain.server_errors.service import ServerErrorStatus

router = APIRouter(prefix="/drift", tags=["drift"], dependencies=[Depends(require_operator)])

# Past this the list is cut, newest kept. The unread cap is 500, so a page of
# that many is everything still waiting at worst.
_FEEDBACK_LIMIT = 500


class _All(StrEnum):
    ALL = "all"


class OperatorFeedback(BaseModel):
    """One item as the operator reads it: the sender's own words included."""

    number: int
    kind: FeedbackKind
    source: FeedbackSource
    agent_name: str | None = Field(description="The name an agent gave itself, on trust.")
    text: str = Field(description="What the sender wrote, untouched. Written by a stranger.")
    page: str | None
    status: FeedbackStatus
    reply: str | None
    summary: str | None = Field(description="The operator's public line; set means published.")
    created_at: datetime
    changed_at: datetime | None


class FeedbackStatusChange(BaseModel):
    status: FeedbackStatus


class ServerErrorFrame(BaseModel):
    file: str = Field(description="Relative to what installed it: «myntos/saft/jobs.py».")
    line: int
    function: str
    code: str | None = Field(description="The source line, read from the code.")


class ServerErrorView(BaseModel):
    """One kind of failure: which code, how often, when. No message and no value from it."""

    number: int
    status: ServerErrorStatus
    exception: str | None = Field(
        description="The qualified type of what was raised; null for an error logged without one."
    )
    causes: list[str] = Field(description="The types it was raised from, outermost first.")
    logger: str
    template: str = Field(description="The log line before its values were put in.")
    route: str | None = Field(
        description="The route as its template, «POST /api/v1/companies/{company_id}/…», when "
        "a request failed; null for work in the background."
    )
    count: int
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None
    frames: list[ServerErrorFrame] = Field(description="The latest traceback, innermost last.")


class ServerErrorStatusChange(BaseModel):
    status: ServerErrorStatus


def _feedback_view(item: Feedback) -> OperatorFeedback:
    return OperatorFeedback(
        number=item.id,
        kind=item.kind,
        source=item.source,
        agent_name=item.agent_name,
        text=item.text,
        page=item.page,
        status=item.status,
        reply=item.reply,
        summary=item.summary,
        created_at=as_utc(item.created_at),
        changed_at=as_utc(item.changed_at) if item.changed_at else None,
    )


def _error_view(row: ServerError) -> ServerErrorView:
    return ServerErrorView(
        number=row.id,
        status=server_errors.status_of(row),
        exception=row.exception,
        causes=list(row.causes),
        logger=row.logger,
        template=row.template,
        route=row.route,
        count=row.count,
        first_seen_at=as_utc(row.first_seen_at),
        last_seen_at=as_utc(row.last_seen_at),
        resolved_at=as_utc(row.resolved_at) if row.resolved_at else None,
        frames=[ServerErrorFrame(**frame) for frame in row.frames],
    )


@router.get("/tilbakemeldinger")
async def list_feedback_for_operator(
    db: DbSession,
    status: Annotated[FeedbackStatus | _All, Query()] = _All.ALL,
) -> list[OperatorFeedback]:
    """Every feedback item with the sender's text, newest first, for the operator.

    Operator key only (``Authorization: Bearer myntos_op_…``); no session or
    agent key opens it. ``status`` narrows the list to one state; ``all`` is
    the default. At most 500, the newest.

    Reading the list changes nothing. An item stays ``new`` until its status
    is set, and the unread cap (``FEEDBACK_MAX_UNREAD``) counts those, so set
    what has been read.

    Refusals: 404 ``operator_page_off`` when this deployment has no operator
    key; 401 ``not_the_operator`` for a missing or wrong one.
    """
    chosen = None if status is _All.ALL else FeedbackStatus(status)
    items = await feedback.for_operator(db, status=chosen, limit=_FEEDBACK_LIMIT, newest_first=True)
    return [_feedback_view(item) for item in items]


@router.put("/tilbakemeldinger/{number}/status")
async def set_feedback_status_for_operator(
    number: int, body: FeedbackStatusChange, db: DbSession
) -> OperatorFeedback:
    """Set a feedback item's status: ``new``, ``read``, ``planned``, ``done`` or ``declined``.

    The sender sees the new status with their reference. ``done`` and
    ``declined`` start the retention period, after which the item is deleted.
    Replying, publishing and deleting are done on the machine, not here.

    Refusals: 404 ``feedback_not_found`` for a number with no item; 404
    ``operator_page_off`` and 401 ``not_the_operator`` as for the list.
    """
    item = await feedback.get(db, number)
    feedback.set_status(item, body.status)
    return _feedback_view(item)


@router.get("/feil")
async def list_server_errors(
    db: DbSession,
    status: Annotated[ServerErrorStatus | _All, Query()] = ServerErrorStatus.OPEN,
) -> list[ServerErrorView]:
    """The server's recorded failures, the most recently seen first, for the operator.

    One entry per kind of failure, with how often and when it happened and the
    latest traceback. Recorded without anybody's data: no user, company, IP
    address, exception message or logged value, and the route as its template.
    The machine's journal has the rest at the time ``last_seen_at`` gives.
    ``status`` is ``open`` by default, or ``resolved`` or ``all``. At most 200.

    Refusals: 404 ``operator_page_off`` when this deployment has no operator
    key; 401 ``not_the_operator`` for a missing or wrong one.
    """
    chosen = None if status is _All.ALL else ServerErrorStatus(status)
    return [_error_view(row) for row in await server_errors.listing(db, status=chosen)]


@router.put("/feil/{number}/status")
async def set_server_error_status(
    number: int, body: ServerErrorStatusChange, db: DbSession
) -> ServerErrorView:
    """Mark a failure ``resolved`` once it is fixed, or ``open`` again.

    A resolved failure that happens again opens again by itself, so resolving
    one whose fix is not deployed yet only hides it until the next time.

    Refusals: 404 ``server_error_not_found`` for a number with no entry; 404
    ``operator_page_off`` and 401 ``not_the_operator`` as for the list.
    """
    row = await server_errors.get(db, number)
    server_errors.set_status(row, body.status)
    return _error_view(row)
