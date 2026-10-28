"""Sending, reading back, withdrawing and handling feedback.

The sender's half (:func:`submit`, :func:`find`, :func:`withdraw`,
:func:`published`) is what the public routes call. The operator's half, from
:func:`for_operator` down, is called by ``python -m myntos.domain.feedback.triage``
on the machine, and the reading and the status by the operator's page
(``/drift/``, :mod:`myntos.api.v1.operator`), which only the operator key
opens. Replying, publishing and deleting stay on the machine: docs/feedback.md
and docs/operator.md say why.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.core.clock import utc_now
from myntos.domain.feedback.errors import (
    FeedbackBusyError,
    FeedbackInboxFullError,
    FeedbackNotFoundError,
    InvalidFeedbackError,
)
from myntos.domain.feedback.models import (
    AGENT_NAME_LENGTH,
    CLOSED_STATUSES,
    PAGE_LENGTH,
    SUMMARY_LENGTH,
    Feedback,
    FeedbackKind,
    FeedbackSource,
    FeedbackStatus,
)

# Short enough to say something («Knappen Lagre gjør ingenting»), long enough
# that a stray click on «Send» with an empty box is refused rather than read.
TEXT_MIN_LENGTH = 10
# A long bug report with steps is a couple of thousand characters. Twice that
# is somebody pasting a document, which is usually somebody's books.
TEXT_MAX_LENGTH = 5000

# 120 bits, as 20 URL-safe characters: unguessable, and short enough to read
# out of a link. Not prefixed like the key credentials (myntos_ck_ and so on),
# because it opens nothing but the one item it was made for.
_REFERENCE_BYTES = 15

_WINDOW = timedelta(hours=1)

# Every C0 control except tab and newline, and DEL. Pasted text carries them
# (a carriage return from Windows, an escape sequence from a terminal), and the
# operator reads this in a terminal.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_BLANK_LINES = re.compile(r"\n{3,}")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Submission:
    """What a sender sends, before anything is checked."""

    kind: FeedbackKind
    text: str
    page: str | None = None
    source: FeedbackSource = FeedbackSource.PERSON
    agent_name: str | None = None


def hash_reference(reference: str) -> str:
    return hashlib.sha256(reference.encode("utf-8")).hexdigest()


def _clean_text(value: str) -> str:
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text)
    return _BLANK_LINES.sub("\n\n", text).strip()


def _clean_line(value: str | None, *, label: str, limit: int) -> str | None:
    """One line of something, or ``None``; never a second message hidden in a field."""
    if value is None:
        return None
    line = _WHITESPACE.sub(" ", _CONTROL.sub("", value)).strip()
    if not line:
        return None
    if len(line) > limit:
        raise InvalidFeedbackError(f"{label} kan være høyst {limit} tegn.")
    return line


def _checked(submission: Submission) -> tuple[str, str | None, str | None]:
    text = _clean_text(submission.text)
    if len(text) < TEXT_MIN_LENGTH:
        raise InvalidFeedbackError(
            f"Skriv litt mer, minst {TEXT_MIN_LENGTH} tegn, så vi forstår hva det gjelder."
        )
    if len(text) > TEXT_MAX_LENGTH:
        raise InvalidFeedbackError(
            f"Teksten kan være høyst {TEXT_MAX_LENGTH} tegn. Del det opp, eller skriv det "
            "viktigste først."
        )
    page = _clean_line(submission.page, label="Siden", limit=PAGE_LENGTH)
    agent_name = _clean_line(
        submission.agent_name, label="Navnet på agenten", limit=AGENT_NAME_LENGTH
    )
    if submission.source is FeedbackSource.PERSON:
        # A name for a program, on something a person sent, would be a field
        # nobody can explain later. Dropped rather than refused: the person
        # did nothing wrong, the client did.
        agent_name = None
    return text, page, agent_name


async def _count(db: AsyncSession, *conditions: ColumnElement[bool]) -> int:
    count = await db.scalar(select(func.count()).select_from(Feedback).where(*conditions))
    return int(count or 0)


async def submit(
    db: AsyncSession,
    submission: Submission,
    *,
    max_per_hour: int,
    max_unread: int,
    now: datetime | None = None,
) -> tuple[Feedback, str]:
    """Store one piece of feedback and return it with its reference.

    The reference is returned here and nowhere else, ever: only its digest is
    stored. Refused with :class:`FeedbackBusyError` when the hour's cap or the
    cap on unread items is reached, which is about everybody's feedback
    together and never about this sender (docs/feedback.md, the cap).
    """
    text, page, agent_name = _checked(submission)
    now = now or utc_now()
    if await _count(db, Feedback.created_at > now - _WINDOW) >= max_per_hour:
        raise FeedbackBusyError(
            "Det har kommet mange tilbakemeldinger den siste timen. Prøv igjen om en time."
        )
    if await _count(db, Feedback.status == FeedbackStatus.NEW) >= max_unread:
        raise FeedbackInboxFullError(
            "Vi har mange tilbakemeldinger vi ikke har lest ennå, og tar ikke imot flere før vi "
            "har det. Prøv igjen om noen dager, eller skriv til kontakt@myntos.no."
        )
    reference = secrets.token_urlsafe(_REFERENCE_BYTES)
    item = Feedback(
        reference_hash=hash_reference(reference),
        kind=submission.kind,
        source=submission.source,
        agent_name=agent_name,
        text=text,
        page=page,
        status=FeedbackStatus.NEW,
        created_at=now,
    )
    db.add(item)
    await db.flush()
    return item, reference


async def find(db: AsyncSession, reference: str) -> Feedback:
    """The item ``reference`` was made for, or :class:`FeedbackNotFoundError`."""
    item = await db.scalar(
        select(Feedback).where(Feedback.reference_hash == hash_reference(reference.strip()))
    )
    if item is None:
        raise FeedbackNotFoundError(
            "Finner ingen tilbakemelding med den referansen. Den kan være trukket tilbake, "
            "eller slettet etter at den var ferdig behandlet."
        )
    return item


async def withdraw(db: AsyncSession, reference: str) -> None:
    """Delete what the holder of ``reference`` sent, published summary and all.

    The only way a sender can take back what they wrote, and therefore the
    whole of their right to erasure: nothing else ties the row to them.
    """
    await db.delete(await find(db, reference))
    await db.flush()


async def published(db: AsyncSession, *, limit: int = 200) -> list[Feedback]:
    """What the operator chose to show, most recently changed first."""
    rows = await db.scalars(
        select(Feedback)
        .where(Feedback.summary.is_not(None))
        .order_by(
            func.coalesce(Feedback.changed_at, Feedback.created_at).desc(), Feedback.id.desc()
        )
        .limit(limit)
    )
    return list(rows)


async def arrived_since(db: AsyncSession, since: datetime, *, before_id: int) -> int:
    """How many items came in after ``since``, not counting ``before_id`` and later.

    For the operator's notice: one mail for the first item after a quiet
    hour, not one per item in a burst.
    """
    return await _count(db, Feedback.created_at > since, Feedback.id < before_id)


async def unread_count(db: AsyncSession) -> int:
    return await _count(db, Feedback.status == FeedbackStatus.NEW)


# --- The operator's half ------------------------------------------------------------


async def for_operator(
    db: AsyncSession,
    *,
    status: FeedbackStatus | None = None,
    limit: int = 100,
    newest_first: bool = False,
) -> list[Feedback]:
    """Items to read, oldest first by default: the order a queue is worked in.

    The page asks for the newest first instead, because it is read top down
    and what arrived since the last visit belongs at the top.
    """
    if newest_first:
        query = select(Feedback).order_by(Feedback.created_at.desc(), Feedback.id.desc())
    else:
        query = select(Feedback).order_by(Feedback.created_at, Feedback.id)
    query = query.limit(limit)
    if status is not None:
        query = query.where(Feedback.status == status)
    return list(await db.scalars(query))


async def get(db: AsyncSession, item_id: int) -> Feedback:
    # A select rather than db.get: docs/company-isolation.md counts the
    # db.get calls in the backend, and this one is not company-scoped either
    # way, since only the operator (on the machine, or with the operator key)
    # ever names an item by id.
    item = await db.scalar(select(Feedback).where(Feedback.id == item_id))
    if item is None:
        raise FeedbackNotFoundError(f"Ingen tilbakemelding har nummer {item_id}.")
    return item


def set_status(item: Feedback, status: FeedbackStatus, *, now: datetime | None = None) -> None:
    item.status = status
    item.changed_at = now or utc_now()


def set_reply(item: Feedback, reply: str | None, *, now: datetime | None = None) -> None:
    """The operator's answer, which the sender reads with their reference.

    Setting it marks a new item read: an item somebody has answered has been
    read, and leaving it ``new`` would keep it in the unread cap.
    """
    cleaned = _clean_text(reply) if reply is not None else ""
    item.reply = cleaned or None
    if item.status is FeedbackStatus.NEW:
        item.status = FeedbackStatus.READ
    item.changed_at = now or utc_now()


def publish(item: Feedback, summary: str, *, now: datetime | None = None) -> None:
    """Show the item on the public list, under the operator's own words.

    The sender's text is never published, because it is the one part nobody
    but the sender has read for what it might contain (the models docstring).
    """
    line = _clean_line(summary, label="Sammendraget", limit=SUMMARY_LENGTH)
    if line is None:
        raise InvalidFeedbackError("Et sammendrag trengs for å vise den offentlig.")
    item.summary = line
    if item.status is FeedbackStatus.NEW:
        item.status = FeedbackStatus.READ
    item.changed_at = now or utc_now()


def unpublish(item: Feedback, *, now: datetime | None = None) -> None:
    item.summary = None
    item.changed_at = now or utc_now()


async def purge_closed(
    db: AsyncSession, *, retention_days: int, now: datetime | None = None
) -> int:
    """Delete items that ended (done or declined) more than ``retention_days`` ago.

    An item still open is kept however old, because somebody is still waiting
    for it. The count is returned for the log line.
    """
    cutoff = (now or utc_now()) - timedelta(days=retention_days)
    result = await db.execute(
        delete(Feedback).where(
            Feedback.status.in_(CLOSED_STATUSES),
            func.coalesce(Feedback.changed_at, Feedback.created_at) < cutoff,
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)
