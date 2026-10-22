"""Bug reports, improvements and wishes from anybody, kept without a name.

Four routes, all public, and none of them asks who is calling: there is no
``CurrentUser``, ``CurrentCaller`` or ``CurrentCompany`` anywhere in this
file, and that is the whole of how a signed-in person stays anonymous here.
Their browser sends the session cookie with the request like any other, and
nothing reads it; an agent's bearer token is not looked at either. The table
has no column that could hold either (``myntos.domain.feedback.models``).

The request itself would still name the sender in one place: uvicorn's access
log, which records the client's address for every request because Caddy
forwards it. :func:`keep_out_of_the_access_log` drops those lines for these
routes, and ``create_app`` installs it. See docs/feedback.md.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Path, status
from pydantic import BaseModel, Field

from myntos.api.deps import NO_KEY_NEEDED, DbSession, OutgoingMail
from myntos.core.clock import as_utc, utc_now
from myntos.core.config import get_settings
from myntos.core.mail import Mailer
from myntos.domain.feedback import service
from myntos.domain.feedback.mail import feedback_notice
from myntos.domain.feedback.models import (
    AGENT_NAME_LENGTH,
    PAGE_LENGTH,
    Feedback,
    FeedbackKind,
    FeedbackSource,
    FeedbackStatus,
)

logger = logging.getLogger(__name__)

PATH = "/tilbakemeldinger"
_FULL_PATH = f"/api/v1{PATH}"

router = APIRouter(prefix=PATH, tags=["tilbakemelding"])

# A reference is 20 characters; anything much longer is not one, and is
# refused before it is hashed and looked for.
Reference = Annotated[str, Path(min_length=8, max_length=64)]


class _NotTheFeedbackRoutes(logging.Filter):
    """Drops uvicorn's access-log line for a request to these routes.

    uvicorn logs ``'%s - "%s %s HTTP/%s" %d'`` with the client address first
    and the path third. Behind Caddy that address is the sender's own, from
    ``X-Forwarded-For``, and the journal keeps it beside the path. For every
    other route that is the incident log the personvernerklæring describes;
    for these it would be the one record of who sent what.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            return not args[2].startswith(_FULL_PATH)
        return True


def keep_out_of_the_access_log() -> None:
    """Install the filter above on uvicorn's access logger, once.

    Called from ``create_app``, which uvicorn runs after it has configured its
    logging, so the filter is not replaced. Configuring logging again does not
    clear a logger's filters, only its handlers.
    """
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(existing, _NotTheFeedbackRoutes) for existing in access.filters):
        access.addFilter(_NotTheFeedbackRoutes())


class FeedbackRequest(BaseModel):
    kind: FeedbackKind = Field(
        description="What it is: bug (something does not work as it says), improvement "
        "(something works but could be better) or request (something Myntos does not do)."
    )
    # A hard ceiling well above the real one, so an absurd body is refused by
    # the parser; the real limit, with its bokmål message, is the domain's.
    text: str = Field(
        max_length=4 * service.TEXT_MAX_LENGTH,
        description=f"What happened or what is wanted, in any language. "
        f"{service.TEXT_MIN_LENGTH} to {service.TEXT_MAX_LENGTH} characters. Leave out "
        "names, amounts and anything else from somebody's books: only the operator reads "
        "it, and nobody can tell who sent it to ask.",
    )
    page: str | None = Field(
        default=None,
        max_length=4 * PAGE_LENGTH,
        description="Where it happened: a page («/faktura/») or a route "
        "(«POST /api/v1/companies/{company_id}/faktura»). Optional.",
    )
    source: FeedbackSource = Field(
        default=FeedbackSource.PERSON,
        description="agent when an AI agent or a program is sending this on its own account; "
        "person when a person wrote it, even if an agent sends it for them.",
    )
    agent_name: str | None = Field(
        default=None,
        max_length=4 * AGENT_NAME_LENGTH,
        description="The agent's or program's name («Claude», «mitt-skript»), when source is "
        "agent. Ignored otherwise.",
    )


class FeedbackSent(BaseModel):
    reference: str = Field(
        description="The only key to this item: shown once, stored only as a digest. Keep it "
        "to read the answer and to withdraw what was sent."
    )
    number: int
    status: FeedbackStatus
    created_at: datetime
    url: str = Field(description="The page where a person can follow it with the reference.")


class FeedbackItem(BaseModel):
    """One item, as its sender sees it with the reference."""

    number: int
    kind: FeedbackKind
    source: FeedbackSource
    text: str
    page: str | None
    status: FeedbackStatus
    reply: str | None
    published: bool
    created_at: datetime
    changed_at: datetime | None


class PublishedFeedback(BaseModel):
    """One item on the public list: the operator's words, never the sender's."""

    number: int
    kind: FeedbackKind
    summary: str
    status: FeedbackStatus
    reply: str | None
    received_on: date
    changed_at: datetime | None


def _status_url(reference: str) -> str:
    # The fragment, like a mailed link (docs/frontend-conventions.md): it never
    # reaches a server log, the static site's included.
    return f"{get_settings().frontend_base_url.rstrip('/')}/tilbakemelding/#ref={reference}"


def _as_item(item: Feedback) -> FeedbackItem:
    return FeedbackItem(
        number=item.id,
        kind=item.kind,
        source=item.source,
        text=item.text,
        page=item.page,
        status=item.status,
        reply=item.reply,
        published=item.summary is not None,
        created_at=as_utc(item.created_at),
        changed_at=as_utc(item.changed_at) if item.changed_at else None,
    )


async def _notify(post: Mailer, mail_to: str, item_id: int, item: Feedback, unread: int) -> None:
    delivery = await post.send(
        feedback_notice(
            to=mail_to, number=item_id, kind=item.kind, source=item.source, unread=unread
        )
    )
    if not delivery.sent and delivery.error:
        # The feedback is stored whatever the mail does; the operator finds it
        # in the list. Logged without anything from the item but its number.
        logger.warning("Feedback notice for %s not sent: %s", item_id, delivery.error)


@router.post("", status_code=status.HTTP_201_CREATED, openapi_extra=NO_KEY_NEEDED)
async def send_feedback(
    body: FeedbackRequest, db: DbSession, post: OutgoingMail, background: BackgroundTasks
) -> FeedbackSent:
    """Send Myntos a bug report, an improvement or a wish («tilbakemelding»), anonymously.

    Anybody may call this: a person from the page «Tilbakemelding», an AI
    agent that met something in Myntos that did not work or was missing, a
    developer. No credential is needed and none is read. A session cookie or
    an agent's bearer token sent along is ignored, nothing about the caller is
    stored (no account, company, IP address or user agent), and the request is
    kept out of the server's access log. What is stored is the five fields of
    the body and the time.

    The answer holds a ``reference``, shown this once and stored only as a
    digest. It is the only way back to the item: ``GET`` it for the status and
    the operator's reply, ``DELETE`` it to withdraw what was sent. Lose it and
    the item can no longer be followed, because nothing else says whose it is.

    Write in any language, and leave out names, amounts and other content of
    somebody's books. Only the operator reads the text. Published items show
    the operator's own one-line summary, never the text
    (``GET /api/v1/tilbakemeldinger``), so look there first for something
    already reported.

    Refusals:

    - 422 ``invalid_feedback``: the text is under 10 or over 5000 characters
      after trimming, or ``page`` or ``agent_name`` is too long. The detail
      says which; fix it and send again.
    - 429 ``feedback_busy``: more than the hour's cap has arrived from
      everybody together (not from you: nobody is counted separately). Wait
      for ``Retry-After`` seconds.
    - 503 ``feedback_inbox_full``: too much is unread. Try again in a few days,
      or write to kontakt@myntos.no.
    """
    settings = get_settings()
    item, reference = await service.submit(
        db,
        service.Submission(
            kind=body.kind,
            text=body.text,
            page=body.page,
            source=body.source,
            agent_name=body.agent_name,
        ),
        max_per_hour=settings.feedback_max_per_hour,
        max_unread=settings.feedback_max_unread,
    )
    # One mail for the first item after a quiet hour, not one per item.
    quiet_hour = utc_now() - timedelta(hours=1)
    if (
        settings.feedback_notify_to
        and post.configured
        and await service.arrived_since(db, quiet_hour, before_id=item.id) == 0
    ):
        unread = await service.unread_count(db)
        background.add_task(_notify, post, settings.feedback_notify_to, item.id, item, unread)
    return FeedbackSent(
        reference=reference,
        number=item.id,
        status=item.status,
        created_at=as_utc(item.created_at),
        url=_status_url(reference),
    )


@router.get("", openapi_extra=NO_KEY_NEEDED)
async def list_published_feedback(db: DbSession) -> list[PublishedFeedback]:
    """What the operator has read and chosen to show, newest change first.

    Public, no credential. Each entry is the operator's one-line ``summary``
    of a report, its ``status`` (``read``, ``planned``, ``done``,
    ``declined``) and the operator's ``reply``, if any. The sender's own text
    is never here, and neither is anything about who sent it.

    Read this before sending something: if it is already on the list, the
    answer is there too, and a second report of the same thing adds nothing.
    Most feedback is never published; an item missing here may well have been
    read and acted on. At most 200 entries.
    """
    return [
        PublishedFeedback(
            number=item.id,
            kind=item.kind,
            summary=item.summary or "",
            status=item.status,
            reply=item.reply,
            received_on=as_utc(item.created_at).date(),
            changed_at=as_utc(item.changed_at) if item.changed_at else None,
        )
        for item in await service.published(db)
    ]


@router.get("/{reference}", openapi_extra=NO_KEY_NEEDED)
async def get_feedback(reference: Reference, db: DbSession) -> FeedbackItem:
    """One item, by the reference its sender was given: the status and the operator's reply.

    The reference is the only key, so this answers whoever holds it, with the
    text as it was sent. Poll it now and then, not in a loop: the operator
    reads feedback by hand. ``changed_at`` moves when the status, the reply or
    the publishing changes.

    Refusals: 404 ``feedback_not_found`` for a reference that was never given
    out, one that was withdrawn, and one whose item was deleted after it was
    closed. They cannot be told apart, because nothing else names an item.
    """
    return _as_item(await service.find(db, reference))


@router.delete("/{reference}", status_code=status.HTTP_204_NO_CONTENT, openapi_extra=NO_KEY_NEEDED)
async def withdraw_feedback(reference: Reference, db: DbSession) -> None:
    """Withdraw what was sent: the item is deleted, published summary and all.

    For the holder of the reference only, and the only way to erase an item,
    since nothing else ties it to anybody. Cannot be undone. An agent should
    do this only when the person it works for asks.

    Refusals: 404 ``feedback_not_found``, as for reading it.
    """
    await service.withdraw(db, reference)
