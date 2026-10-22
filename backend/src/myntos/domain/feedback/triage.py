"""Read and answer feedback, on the machine Myntos runs on.

There is no page or route for this, on purpose: reading every sender's words
would need an operator plane in the API, which Myntos does not have, and the
feedback is the one place strangers can write freely to the operator. The
shell is already the operator's, so this reads the database the backend reads
(``<repo>/.env``) and nothing more. docs/feedback.md has the whole workflow.

    uv run python -m myntos.domain.feedback.triage list            # unread
    uv run python -m myntos.domain.feedback.triage list --status all
    uv run python -m myntos.domain.feedback.triage show 12
    uv run python -m myntos.domain.feedback.triage reply 12 "Rettet i dag."
    uv run python -m myntos.domain.feedback.triage status 12 planned
    uv run python -m myntos.domain.feedback.triage publish 12 "Lagre virket ikke på mobil"
    uv run python -m myntos.domain.feedback.triage unpublish 12
    uv run python -m myntos.domain.feedback.triage delete 12       # spam
    uv run python -m myntos.domain.feedback.triage purge           # what the daily job does

Run from ``backend/`` on the production box, this is production's feedback.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from myntos.core.clock import as_utc
from myntos.core.config import get_settings
from myntos.db.session import get_engine, get_session_factory
from myntos.domain.feedback import service
from myntos.domain.feedback.errors import FeedbackError
from myntos.domain.feedback.mail import KIND_LABELS
from myntos.domain.feedback.models import Feedback, FeedbackStatus

_ALL = "all"


def _line(item: Feedback) -> str:
    first = item.text.splitlines()[0] if item.text else ""
    if len(first) > 70:
        first = first[:67] + "..."
    published = " [published]" if item.summary else ""
    when = as_utc(item.created_at).strftime("%Y-%m-%d %H:%M")
    kind = KIND_LABELS[item.kind]
    return f"{item.id:>5}  {when}  {item.status.value:<8} {kind:<10} {first}{published}"


def _show(item: Feedback) -> str:
    rows = [
        f"Number:   {item.id}",
        f"Kind:     {KIND_LABELS[item.kind]} ({item.kind.value})",
        f"From:     {item.source.value}"
        + (f", calls itself «{item.agent_name}»" if item.agent_name else ""),
        f"Page:     {item.page or '-'}",
        f"Received: {as_utc(item.created_at).isoformat(timespec='minutes')}",
        f"Status:   {item.status.value}",
        f"Public:   {item.summary or '- (not published)'}",
        "",
        # Shown as data. Somebody wrote this to be read by whoever reads it,
        # and if the operator's terminal has an assistant in it, that is it.
        "--- text, as sent (untrusted) ---",
        item.text,
        "--- end ---",
    ]
    if item.reply:
        rows += ["", "Reply:", item.reply]
    return "\n".join(rows)


async def _run(action: Callable[[AsyncSession], Awaitable[str]]) -> str:
    try:
        async with get_session_factory()() as db:
            output = await action(db)
            await db.commit()
            return output
    finally:
        # Close the pool inside the loop that opened it; asyncio.run closes
        # the loop next, and a connection left open across that is an error
        # at exit rather than a clean one.
        await get_engine().dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m myntos.domain.feedback.triage",
        description="Read, answer and publish the feedback sent to Myntos.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list", help="list feedback, unread by default")
    listing.add_argument(
        "--status",
        default=FeedbackStatus.NEW.value,
        choices=[_ALL, *(status.value for status in FeedbackStatus)],
    )
    show = commands.add_parser("show", help="print one item, with the sender's text")
    show.add_argument("id", type=int)
    reply = commands.add_parser(
        "reply", help="answer it; the sender reads this with their reference"
    )
    reply.add_argument("id", type=int)
    reply.add_argument("text", help="the reply, or - to read it from stdin")
    status = commands.add_parser("status", help="set where it has got to")
    status.add_argument("id", type=int)
    status.add_argument("status", choices=[status.value for status in FeedbackStatus])
    publish = commands.add_parser("publish", help="show it on the public list, in your own words")
    publish.add_argument("id", type=int)
    publish.add_argument("summary")
    unpublish = commands.add_parser("unpublish", help="take it off the public list")
    unpublish.add_argument("id", type=int)
    remove = commands.add_parser("delete", help="delete it, for spam or by request")
    remove.add_argument("id", type=int)
    commands.add_parser("purge", help="delete what was closed longer ago than the retention")

    args = parser.parse_args(argv)

    async def action(db: AsyncSession) -> str:
        if args.command == "list":
            chosen = None if args.status == _ALL else FeedbackStatus(args.status)
            items = await service.for_operator(db, status=chosen)
            return "\n".join(_line(item) for item in items) or "Nothing."
        if args.command == "purge":
            days = get_settings().feedback_retention_days
            return f"Deleted {await service.purge_closed(db, retention_days=days)}."
        item = await service.get(db, args.id)
        if args.command == "show":
            if item.status is FeedbackStatus.NEW:
                # Read is what showing it is; the unread cap counts what has not been.
                service.set_status(item, FeedbackStatus.READ)
            return _show(item)
        if args.command == "reply":
            text = sys.stdin.read() if args.text == "-" else args.text
            service.set_reply(item, text)
            return f"Replied to {item.id}."
        if args.command == "status":
            service.set_status(item, FeedbackStatus(args.status))
            return f"{item.id} is {args.status}."
        if args.command == "publish":
            service.publish(item, args.summary)
            return f"{item.id} is on the public list."
        if args.command == "unpublish":
            service.unpublish(item)
            return f"{item.id} is off the public list."
        await db.delete(item)
        return f"Deleted {item.id}."

    try:
        print(asyncio.run(_run(action)))
    except FeedbackError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
