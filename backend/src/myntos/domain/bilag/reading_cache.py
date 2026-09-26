"""What a document turned out to say, remembered for the next few minutes.

Reading a bilag is not free. A PDF's text layer is a millisecond, but a
photographed receipt is a vision model looking at a page, and nine screenshots
of an AliExpress order take about ninety seconds on one card. That was
tolerable while the only way to read a bilag twice was to press a button
labelled "read it again". It stopped being tolerable the moment the register
began re-reading on its own — which it now does whenever the user edits the
description, because a sentence from the person who made the payment is often
the only thing that settles what the entry is.

So the expensive half is remembered: the **text**, keyed by the bytes it came
from. Content-addressed, and that is what makes it safe to share between
callers. The key is a SHA-256 of the file's own content, so the only way to get
an entry out is to present exactly the bytes that put it in — a caller who can
do that already has the document. There is no user, session or company in the
key, and none is needed.

Three properties it is built to keep:

**In memory, and nowhere else.** The same promise the image path makes: a bilag
that is uploaded and then abandoned leaves nothing behind. A cache that spilled
to disk would quietly turn "we do not keep your documents" into "we keep them
for a while", which is a different sentence and a worse one.

**Small and short-lived.** :data:`MAX_ENTRIES` and :data:`TTL_SECONDS` bound it
in both directions. It exists to make one user's next thirty seconds fast, not
to be a store.

**Never a source of truth.** Every entry can vanish between two requests and
nothing breaks: a miss costs the reading, which is what would have happened
anyway. Nothing in the register may depend on a hit.
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass

#: How many readings are held. A bilag is a handful of files and a user works on
#: one at a time; this is enough for the current one and the last few.
MAX_ENTRIES = 64

#: How long an entry lives. Long enough to answer a question and re-read, short
#: enough that a document nobody is working on is gone.
TTL_SECONDS = 600.0


@dataclass(frozen=True)
class Reading:
    """One file, as text, exactly as the reader produced it."""

    text: str
    title: str | None
    encrypted: bool
    unreadable: bool


def key(content: bytes, *, filename: str, content_type: str, use_loynd: bool) -> str:
    """The cache key for one file: everything the reading depends on.

    The bytes are only most of it. ``content_type`` and ``use_loynd`` change
    where the file goes — a vision model or a PDF parser, and a company with KI
    switched off gets the parser's answer and must never be handed the model's.
    And the **filename** goes in because the extractor is told it: the reader on
    the other side picks a format from the extension, so the same bytes under
    two names are two readings and keying only on content would hand the second
    file the first one's text.

    Two genuinely identical uploads — the same document attached twice, which
    happens — still share an entry, which is the case worth having.
    """
    digest = hashlib.sha256(content).hexdigest()
    return f"{digest}:{filename}:{content_type}:{int(use_loynd)}"


class ReadingCache:
    """A bounded, expiring, content-addressed store of document text."""

    def __init__(self, *, max_entries: int = MAX_ENTRIES, ttl: float = TTL_SECONDS) -> None:
        self._max_entries = max_entries
        self._ttl = ttl
        self._entries: OrderedDict[str, tuple[float, Reading]] = OrderedDict()

    def get(self, cache_key: str, *, now: float | None = None) -> Reading | None:
        """The remembered reading, or ``None`` — which is never an error."""
        moment = time.monotonic() if now is None else now
        entry = self._entries.get(cache_key)
        if entry is None:
            return None
        stored_at, reading = entry
        if moment - stored_at > self._ttl:
            del self._entries[cache_key]
            return None
        # Touched, so that the file somebody is actually working on is the last
        # to be evicted rather than the first.
        self._entries.move_to_end(cache_key)
        return reading

    def put(self, cache_key: str, reading: Reading, *, now: float | None = None) -> None:
        """Remember one reading, evicting the least recently used if full.

        A reading that came back empty is remembered too. "This file yields no
        text" is a real and repeatable answer, and re-asking a vision model for
        it on every keystroke is the exact cost this exists to avoid.
        """
        moment = time.monotonic() if now is None else now
        self._entries[cache_key] = (moment, reading)
        self._entries.move_to_end(cache_key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        """Forget everything. For tests, and for a deployment that wants to."""
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


#: The process-wide cache. One per worker, which is all it needs to be: a miss
#: after a restart or on another worker costs a reading, and costing a reading
#: is the thing this is allowed to do.
CACHE = ReadingCache()
