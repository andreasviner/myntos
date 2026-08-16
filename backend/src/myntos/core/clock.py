"""Time helpers.

Everything the application stores is UTC. The only reason this module exists
is :func:`as_utc`: PostgreSQL returns ``TIMESTAMPTZ`` as an aware datetime,
SQLite (used by the test suite) silently drops the offset and returns a naive
one. Comparing the two raises ``TypeError``, so every datetime that comes back
out of the database goes through :func:`as_utc` before it is compared with
:func:`utc_now`.
"""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """The current instant, timezone-aware, in UTC."""
    return datetime.now(UTC)


def as_utc(moment: datetime) -> datetime:
    """Interpret a datetime read from the database as UTC.

    A naive value can only have come from a backend that discarded the offset
    on write, and every value we write is UTC — so attaching UTC is a
    restoration, not a guess.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
