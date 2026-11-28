"""Saved KI chat threads and messages, strictly scoped to one company.

Every query filters by ``company_id`` so one company can never read, append to,
or delete another company's conversation. Threads are deleted automatically
with the company on purge (``CASCADE``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.core.clock import utc_now
from myntos.domain.ki.errors import KiChatThreadNotFoundError
from myntos.domain.ki.models import KiChatMessage, KiChatThread

DEFAULT_TITLE = "Ny samtale"
MAX_TITLE_CHARS = 90
#: Maximum prior user/assistant turns sent as chat history to keep prompts well
#: inside Løynd's 32k context window alongside the full books snapshot.
MAX_HISTORY_TURNS = 10
MAX_HISTORY_CHARS = 12_000


@dataclass(frozen=True)
class ThreadSummary:
    """Thread row paired with message count and preview for the sidebar."""

    thread: KiChatThread
    message_count: int
    preview: str | None


def title_from_question(question: str) -> str:
    """Derive a concise sidebar title from the first question in a thread."""
    cleaned = " ".join(question.split()).strip()
    if not cleaned:
        return DEFAULT_TITLE
    if len(cleaned) <= MAX_TITLE_CHARS:
        return cleaned
    return cleaned[: MAX_TITLE_CHARS - 1].rstrip() + "…"


async def list_threads(
    db: AsyncSession, *, company_id: int, limit: int = 50
) -> list[ThreadSummary]:
    """List saved chat threads for ``company_id``, most recently updated first."""
    threads = list(
        (
            await db.scalars(
                select(KiChatThread)
                .where(KiChatThread.company_id == company_id)
                .order_by(KiChatThread.updated_at.desc(), KiChatThread.id.desc())
                .limit(limit)
            )
        ).all()
    )
    if not threads:
        return []
    thread_ids = [t.id for t in threads]

    counts_rows = await db.execute(
        select(KiChatMessage.thread_id, func.count(KiChatMessage.id))
        .where(
            KiChatMessage.company_id == company_id,
            KiChatMessage.thread_id.in_(thread_ids),
        )
        .group_by(KiChatMessage.thread_id)
    )
    counts = {int(tid): int(cnt) for tid, cnt in counts_rows}

    # Fetch the newest message per thread for a one-line preview.
    messages = list(
        (
            await db.scalars(
                select(KiChatMessage)
                .where(
                    KiChatMessage.company_id == company_id,
                    KiChatMessage.thread_id.in_(thread_ids),
                )
                .order_by(KiChatMessage.id.desc())
            )
        ).all()
    )
    previews: dict[int, str] = {}
    for msg in messages:
        if msg.thread_id not in previews:
            snippet = " ".join(msg.content.split()).strip()
            previews[msg.thread_id] = snippet[:120] + ("…" if len(snippet) > 120 else "")

    return [
        ThreadSummary(
            thread=thread,
            message_count=counts.get(thread.id, 0),
            preview=previews.get(thread.id),
        )
        for thread in threads
    ]


async def create_thread(
    db: AsyncSession,
    *,
    company_id: int,
    user_id: int | None,
    title: str | None = None,
) -> KiChatThread:
    """Create a new empty chat thread for ``company_id``."""
    now = utc_now()
    clean_title = (title or "").strip()[:160] or DEFAULT_TITLE
    thread = KiChatThread(
        company_id=company_id,
        title=clean_title,
        created_by_user_id=user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(thread)
    await db.flush()
    return thread


async def get_thread(db: AsyncSession, *, company_id: int, thread_id: int) -> KiChatThread:
    """Return one thread belonging to ``company_id``, or raise 404."""
    thread: KiChatThread | None = await db.scalar(
        select(KiChatThread).where(
            KiChatThread.id == thread_id,
            KiChatThread.company_id == company_id,
        )
    )
    if thread is None:
        raise KiChatThreadNotFoundError("finner ikke KI-samtalen")
    return thread


async def list_messages(
    db: AsyncSession, *, company_id: int, thread_id: int
) -> list[KiChatMessage]:
    """Return every message in ``thread_id`` in chronological order."""
    rows = await db.scalars(
        select(KiChatMessage)
        .where(
            KiChatMessage.thread_id == thread_id,
            KiChatMessage.company_id == company_id,
        )
        .order_by(KiChatMessage.id.asc())
    )
    return list(rows)


async def delete_thread(db: AsyncSession, *, company_id: int, thread_id: int) -> None:
    """Delete a chat thread and its messages, scoped to ``company_id``."""
    thread = await get_thread(db, company_id=company_id, thread_id=thread_id)
    await db.execute(
        delete(KiChatMessage).where(
            KiChatMessage.thread_id == thread.id,
            KiChatMessage.company_id == company_id,
        )
    )
    await db.execute(
        delete(KiChatThread).where(
            KiChatThread.id == thread.id,
            KiChatThread.company_id == company_id,
        )
    )
    await db.flush()


async def add_user_message(
    db: AsyncSession,
    *,
    thread: KiChatThread,
    content: str,
    requested_level: str,
    user_id: int | None,
) -> KiChatMessage:
    """Store a user message and auto-title the thread if it still has its default title."""
    now = utc_now()
    if thread.title == DEFAULT_TITLE:
        thread.title = title_from_question(content)
    thread.updated_at = now
    msg = KiChatMessage(
        thread_id=thread.id,
        company_id=thread.company_id,
        role="user",
        content=content.strip(),
        level=None,
        requested_level=requested_level,
        tokens_used=0,
        notices=[],
        created_by_user_id=user_id,
        created_at=now,
    )
    db.add(msg)
    await db.flush()
    return msg


async def add_assistant_message(
    db: AsyncSession,
    *,
    thread: KiChatThread,
    content: str,
    level: str,
    requested_level: str,
    tokens_used: int,
    cost_nok: Decimal = Decimal("0.000000"),
    notices: Sequence[str],
) -> KiChatMessage:
    """Store an assistant reply along with the level, internal cost, and any notices."""
    now = utc_now()
    thread.updated_at = now
    msg = KiChatMessage(
        thread_id=thread.id,
        company_id=thread.company_id,
        role="assistant",
        content=content.strip(),
        level=level,
        requested_level=requested_level,
        tokens_used=max(0, tokens_used),
        cost_nok=max(Decimal("0.000000"), cost_nok),
        notices=list(notices),
        created_by_user_id=None,
        created_at=now,
    )
    db.add(msg)
    await db.flush()
    return msg


def history_pairs(messages: Sequence[KiChatMessage]) -> list[tuple[str, str]]:
    """Build bounded ``(asked, answered)`` turn pairs for ``loynd`` from prior messages."""
    all_turns: list[tuple[str, str]] = []
    pending_user: str | None = None
    for msg in messages:
        text = msg.content.strip()
        if not text:
            continue
        if len(text) > 2500:
            text = text[:2500] + "…"
        if msg.role == "user":
            pending_user = text
        elif msg.role == "assistant" and pending_user is not None:
            all_turns.append((pending_user, text))
            pending_user = None

    selected: list[tuple[str, str]] = []
    total_chars = 0
    for asked, answered in reversed(all_turns[-MAX_HISTORY_TURNS:]):
        turn_len = len(asked) + len(answered)
        if total_chars + turn_len > MAX_HISTORY_CHARS and selected:
            break
        selected.append((asked, answered))
        total_chars += turn_len
    selected.reverse()
    return selected
