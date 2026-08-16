"""Document storage abstraction for bilag attachments (PDFs etc.).

Bookkeeping law requires vouchers to stay retrievable for the whole retention
period, so implementations must be durable and must never overwrite or
silently delete stored documents. Backends: local filesystem
(:mod:`myntos.storage.local`, development) and Cloudflare R2
(:mod:`myntos.storage.r2`, production).
"""

from typing import Protocol


class StorageError(RuntimeError):
    """The storage backend could not complete the operation.

    Not a domain error: nothing the user did causes this, and it is not
    something they can fix, so it surfaces as a 500 rather than as a message in
    the interface.
    """


class ObjectNotFoundError(StorageError):
    """The key is valid but nothing is stored under it.

    Always an inconsistency — the database row that names the key exists — so
    it is reported loudly rather than as an empty download.
    """


class DocumentStorage(Protocol):
    async def save(self, *, content: bytes, filename: str, content_type: str) -> str:
        """Store a document and return its storage key."""
        ...

    async def load(self, key: str) -> bytes:
        """Return the stored document's content."""
        ...
