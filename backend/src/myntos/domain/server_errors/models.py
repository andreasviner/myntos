"""The one table the server's failures are kept in, and what it deliberately leaves out.

When something fails, the person is told «Noe gikk galt hos oss» and the
journal on the machine gets the traceback, next to uvicorn's line with the
client's address. Until 2026-10-28 that was all there was. A customer's mail
that day («Noe gikk galt hos oss», then a preview he could not post) was
answered by grepping the journal over ssh, which is the wrong first place to
look: it names who it happened to, and only somebody on the machine can read it.

So each failure is also kept here, boiled down to the part that is Myntos's
own: which code failed, and when, and how often. One row per *kind* of
failure, not per occurrence, so a broken route that a page polls every two
seconds is one row with a count rather than forty thousand rows a day.

What the row does not have is the design, as for ``feedback``:

- no ``user_id``, no ``company_id``, no IP address and no user agent, and no
  foreign key at all;
- **no exception message.** A message is data: ``KeyError: 'Ola Nordmann
  AS'``, a database error quoting the row it refused, a parser quoting the
  bilag. The type is kept (``KeyError``), the words are not;
- **no log arguments.** A log line's template is kept («SAF-T-innlesing %s
  feilet»), never what was put into it. ruff's ``G`` rules keep every line in
  the package a template with arguments, which is what makes the template safe
  to keep;
- **the route as its template** (``POST /api/v1/companies/{company_id}/…``),
  never the address that was asked for, which names the company.

What is left is code: the exception's type and its causes' types, the frames of
the traceback (file, line, function and the source line, all read from the code
and never a local variable), the logger and the times. The journal still has the
whole of it, at the minute ``last_seen_at`` says.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from myntos.core.clock import utc_now
from myntos.db.base import Base

# SHA-256, hex.
FINGERPRINT_LENGTH = 64

# A logger's dotted name, an exception's qualified type, a route template: all
# from the code, so these are ceilings against a surprise, not limits anybody
# meets.
LOGGER_LENGTH = 120
TEMPLATE_LENGTH = 300
ROUTE_LENGTH = 300
EXCEPTION_LENGTH = 200


class ServerError(Base):
    """One kind of failure, with how often and when. The module docstring says what is not here."""

    __tablename__ = "server_error"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_server_error_fingerprint"),
        # The operator's list, newest first, and the purge.
        Index("ix_server_error_last_seen_at", "last_seen_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # What makes two occurrences the same failure: the logger, the template,
    # the route, the types, and the functions of Myntos's own frames, without
    # line numbers, so a deploy that moves the code does not split a failure
    # in two (myntos.domain.server_errors.recorder).
    fingerprint: Mapped[str] = mapped_column(String(FINGERPRINT_LENGTH))
    logger: Mapped[str] = mapped_column(String(LOGGER_LENGTH))
    template: Mapped[str] = mapped_column(String(TEMPLATE_LENGTH))
    route: Mapped[str | None] = mapped_column(String(ROUTE_LENGTH))
    # The qualified type of what was raised, and of what it was raised from
    # (``__cause__``, or ``__context__`` when not suppressed), outermost first.
    # Null for a line logged as an error with no exception behind it.
    exception: Mapped[str | None] = mapped_column(String(EXCEPTION_LENGTH))
    causes: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    # The traceback of the latest occurrence, innermost last, as
    # ``{"file", "line", "function", "code"}``: the line numbers of the code
    # that is running now, not of the deploy that first failed.
    frames: Mapped[list[dict[str, Any]]] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    count: Mapped[int] = mapped_column(Integer, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    # Set by the operator once it is fixed. A later occurrence clears it, since
    # a failure that happens again was not fixed.
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
