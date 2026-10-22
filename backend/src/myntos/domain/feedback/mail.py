"""The one mail feedback causes: a line to the operator that something arrived.

Plain text, to ``FEEDBACK_NOTIFY_TO`` and nobody else, and **without the
sender's words**. Mail leaves through Cloudflare, which makes no promise about
where it is processed (docs/ARCHITECTURE.md, "What the mail provider sees"),
and what people write into a feedback box is whatever is on their screen, which
in an accounting program is often somebody's books. So the mail says that
something came and how to read it on the machine, where the text is.
"""

from __future__ import annotations

from myntos.core.mail import Mail
from myntos.domain.feedback.models import FeedbackKind, FeedbackSource

KIND_LABELS = {
    FeedbackKind.BUG: "Feil",
    FeedbackKind.IMPROVEMENT: "Forbedring",
    FeedbackKind.REQUEST: "Ønske",
}

_TRIAGE = "uv run python -m myntos.domain.feedback.triage"


def feedback_notice(
    *, to: str, number: int, kind: FeedbackKind, source: FeedbackSource, unread: int
) -> Mail:
    label = KIND_LABELS[kind]
    sender = "en agent" if source is FeedbackSource.AGENT else "en person"
    waiting = "Den er den eneste uleste." if unread <= 1 else f"{unread} er uleste nå."
    text = f"""Ny tilbakemelding i Myntos: nummer {number}, «{label}», fra {sender}.
{waiting}

Teksten står ikke her. E-post går gjennom Cloudflare uten noe løfte om EØS, og
folk skriver det de ser på, som ofte er noens regnskap. Les den på maskinen,
fra backend/:

  {_TRIAGE} show {number}

Flere som kommer i løpet av den neste timen, får ingen egen e-post.

Hilsen Myntos
"""
    return Mail(to=to, subject=f"Ny tilbakemelding i Myntos ({label.lower()})", text=text)
