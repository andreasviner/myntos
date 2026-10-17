"""A bilag that has been read, kept while the user answers questions about it.

The first read of a bilag is the expensive part: the files are uploaded,
photographs are converted and transcribed, the parser reads the text, the model
is asked two questions about it, and an app's name may be looked for in the
pixels. An answer to one of the questions that come back changes none of that.
So the result is kept here, and an answer
(``POST …/cashflow/forslag/{reading_id}/svar``) continues from it instead of
starting over: no upload, no conversion, no transcription, and a model that
continues the conversation it had (:mod:`myntos.domain.bilag.followup`).

The same promises as :mod:`myntos.domain.bilag.reading_cache`, which this sits
beside, and for the same reasons:

**In memory, and nowhere else.** A bilag that is read and then abandoned leaves
nothing behind. This holds the text of the documents, which the reading cache
already holds for as long, and the questions put to the model about them.

**Small and short-lived.** :data:`MAX_ENTRIES` and :data:`TTL_SECONDS` bound it.
The clock restarts on every answer, so somebody working through three questions
keeps their reading, and one who walked away loses it.

**Never a source of truth.** An entry may be gone on the next request, after a
restart or an eviction, and the answer to that is to read the bilag again, which
is what would have happened anyway. The answers themselves are not kept here:
the caller sends every answer it has with each request, so a reading read again
from scratch can be answered exactly as the lost one was.

Unlike the reading cache, an entry here **belongs to someone**. The cache is
keyed by the bytes, so only whoever holds the document can get its text out.
An entry here is keyed by a random id, and an id is a thing that ends up in
logs and URLs, so each entry also records the company and the person it was
read for, and is found only by the same company and the same person. Anyone
else gets the answer an id that never existed gets (docs/company-isolation.md).
"""

from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

#: How many readings are held, across every user of this process. A person works
#: on one bilag at a time; this is room for everybody's current one.
MAX_ENTRIES = 64

#: How long a reading lives without being answered. The same ten minutes as the
#: reading cache: long enough to read a question, think and answer it.
TTL_SECONDS = 600.0


@dataclass(frozen=True)
class _Owner:
    company_id: int
    user_id: int


class Readings[T]:
    """A bounded, expiring store of readings, each one owned."""

    def __init__(self, *, max_entries: int = MAX_ENTRIES, ttl: float = TTL_SECONDS) -> None:
        self._max_entries = max_entries
        self._ttl = ttl
        self._entries: OrderedDict[str, tuple[float, _Owner, T]] = OrderedDict()

    def open(self, value: T, *, company_id: int, user_id: int, now: float | None = None) -> str:
        """Keep ``value`` for this company and person, and say what it is called.

        The id is 128 random bits, so it cannot be guessed or counted towards. It
        is still checked against its owner on every use; being hard to guess is
        not the same as being somebody's.
        """
        moment = time.monotonic() if now is None else now
        reading_id = secrets.token_urlsafe(16)
        self._entries[reading_id] = (moment, _Owner(company_id, user_id), value)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        return reading_id

    def get(
        self, reading_id: str, *, company_id: int, user_id: int, now: float | None = None
    ) -> T | None:
        """The reading, if it is still here and belongs to this company and person.

        ``None`` for an id that is unknown, expired or somebody else's, the same
        answer for all three. A hit restarts the clock.
        """
        moment = time.monotonic() if now is None else now
        entry = self._entries.get(reading_id)
        if entry is None:
            return None
        stored_at, owner, value = entry
        if moment - stored_at > self._ttl:
            del self._entries[reading_id]
            return None
        if owner != _Owner(company_id, user_id):
            return None
        self._entries[reading_id] = (moment, owner, value)
        self._entries.move_to_end(reading_id)
        return value

    def clear(self) -> None:
        """Forget everything. For tests, and for a deployment that wants to."""
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


#: The process-wide store. One per worker, which is all it needs to be: this
#: deployment runs one, and a reading missed on another worker is read again.
STORE: Readings[Any] = Readings()
