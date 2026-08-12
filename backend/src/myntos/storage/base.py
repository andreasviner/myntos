"""Document storage abstraction for bilag attachments (PDFs etc.).

Bookkeeping law requires vouchers to stay retrievable for the whole retention
period, so implementations must be durable and must never overwrite or
silently delete stored documents. Planned backends: local filesystem (dev)
and Cloudflare R2 (production) — see docs/ROADMAP.md.
"""

from typing import Protocol


class DocumentStorage(Protocol):
    async def save(self, *, content: bytes, filename: str, content_type: str) -> str:
        """Store a document and return its storage key."""
        ...

    async def load(self, key: str) -> bytes:
        """Return the stored document's content."""
        ...
