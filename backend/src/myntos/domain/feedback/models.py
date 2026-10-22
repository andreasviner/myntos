"""The one table feedback is kept in, and what it deliberately leaves out.

Anybody may send Myntos a bug report, an improvement or a wish, signed in or
not, from the page or through the API, and nothing about who sent it is kept.
That is a property of the table, not of the code that writes to it: there is no
``user_id``, no ``company_id``, no IP address and no user agent, so no future
query, export or careless join can recover what was never stored. A sender who
is signed in sends their session cookie with the request like any other, and
the route never reads it (``myntos.api.v1.feedback``).

What the sender gets instead of an identity is a **reference**: 120 random
bits, shown once, that lets whoever holds it read the answer and withdraw what
they wrote. It is stored as a SHA-256 digest like every other credential in
Myntos, because it is one: it deletes a row. The owner refers to items by
``id``, which is never a way in.

Two texts, kept apart on purpose:

- ``text`` is what the sender wrote. It is read by the operator and nobody
  else, because people paste what they are looking at, and what they are
  looking at is somebody's books.
- ``summary`` is the operator's own one line about it. Writing one is what
  publishing *is*: the public list (``GET /api/v1/tilbakemeldinger``) shows the
  summary, the status and the operator's reply, and never the sender's words.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from myntos.core.clock import utc_now
from myntos.db.base import Base

# SHA-256, hex.
REFERENCE_HASH_LENGTH = 64

# Long enough for a page address with its query string, short enough that the
# field cannot carry a second message.
PAGE_LENGTH = 300

# The name an agent gives itself («Claude», «mitt-skript»), as for a key's
# last_used_client. A label, not an identity: it is whatever the sender typed.
AGENT_NAME_LENGTH = 80

# The operator's public line. A title, not an essay; the reply is for the rest.
SUMMARY_LENGTH = 300


class FeedbackKind(enum.StrEnum):
    """What the sender thinks it is. Bokmål on the page: «Feil», «Forbedring», «Ønske»."""

    BUG = "bug"
    IMPROVEMENT = "improvement"
    REQUEST = "request"


class FeedbackSource(enum.StrEnum):
    """Who the sender says they are. Taken on trust, and never more than a hint.

    Kept because the two read differently: an agent reports the route that
    refused it and the field it could not fill, a person reports the button
    they could not find. Nothing is decided on it, so a sender who lies about
    it gains nothing.
    """

    PERSON = "person"
    AGENT = "agent"


class FeedbackStatus(enum.StrEnum):
    """Where an item has got to, as the operator sets it.

    ``new`` is the only state the application sets itself, and the only one
    the cap on unread items counts (``feedback_max_unread``).
    """

    NEW = "new"
    READ = "read"
    PLANNED = "planned"
    DONE = "done"
    DECLINED = "declined"


# The states an item ends in. Items in them are deleted after
# ``feedback_retention_days``; an item still being worked on is kept.
CLOSED_STATUSES = frozenset({FeedbackStatus.DONE, FeedbackStatus.DECLINED})


def _enum(kind: type[enum.StrEnum], name: str) -> Enum:
    # VARCHAR, not a native enum, as in identity.models: these lists are
    # expected to grow, and a native enum would make each addition an ALTER TYPE.
    return Enum(
        kind,
        name=name,
        native_enum=False,
        length=16,
        values_callable=lambda e: [member.value for member in e],
    )


class Feedback(Base):
    """One bug report, improvement or wish. See the module docstring for what is not here."""

    __tablename__ = "feedback"
    __table_args__ = (
        UniqueConstraint("reference_hash", name="uq_feedback_reference_hash"),
        # The cap's query: how many arrived in the last hour.
        Index("ix_feedback_created_at", "created_at"),
        # The cap on unread items, the operator's list, and the purge.
        Index("ix_feedback_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reference_hash: Mapped[str] = mapped_column(String(REFERENCE_HASH_LENGTH))
    kind: Mapped[FeedbackKind] = mapped_column(_enum(FeedbackKind, "feedback_kind"))
    source: Mapped[FeedbackSource] = mapped_column(_enum(FeedbackSource, "feedback_source"))
    agent_name: Mapped[str | None] = mapped_column(String(AGENT_NAME_LENGTH))
    text: Mapped[str] = mapped_column(Text)
    # Where the sender was, if they said: a path on the site («/faktura/») or
    # a route («POST /api/v1/…»). Saves a round of «which page?».
    page: Mapped[str | None] = mapped_column(String(PAGE_LENGTH))
    status: Mapped[FeedbackStatus] = mapped_column(
        _enum(FeedbackStatus, "feedback_status"), default=FeedbackStatus.NEW
    )
    # The operator's answer, read by whoever holds the reference, and shown on
    # the public list when the item is published.
    reply: Mapped[str | None] = mapped_column(Text)
    # Set means published; see the module docstring.
    summary: Mapped[str | None] = mapped_column(String(SUMMARY_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    # When the status, reply or summary last changed: what the sender polling
    # their reference is waiting for, and where the retention period starts.
    changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
