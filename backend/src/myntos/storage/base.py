"""Document storage abstraction for bilag attachments (PDFs etc.).

Bookkeeping law requires vouchers to stay retrievable for the whole retention
period, so implementations must be durable and must never overwrite or
silently delete stored documents. Derived copies (thumbnails) are the one
exception, and they are kept apart by key so the exception cannot reach an
original. :meth:`DocumentStorage.delete` exists for the end of that period
and for nothing else: its one caller is the purge of books deleted from
Myntos whose retention has run out (myntos.domain.identity.closure). Backends: local filesystem
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

    async def save_derived(self, key: str, *, content: bytes, content_type: str) -> None:
        """Store a regenerable copy (a thumbnail) under ``key``, replacing any.

        The one write that may overwrite, and it only accepts keys under the
        derived prefix (:func:`myntos.storage.keys.validate_derived_key`), so it
        cannot be pointed at a bilag. Two requests making the same thumbnail at
        once is ordinary, and both results are the same picture.
        """
        ...

    async def delete(self, key: str) -> None:
        """Remove what is stored under ``key``, original or derived copy.

        Nothing being there already is not an error: the purge that calls this
        runs again after a failure, and a thumbnail is only made when somebody
        looked. Called by :func:`myntos.domain.identity.closure.purge_expired`
        and nowhere else, because every other document in the archive is one
        the law says to keep.
        """
        ...
