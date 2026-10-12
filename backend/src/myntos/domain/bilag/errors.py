"""Refusals from the bilag archive.

Subclasses of :class:`~myntos.domain.ledger.errors.LedgerError`: a rejected
attachment is an accounting-core refusal, and one hierarchy means one status
table in :mod:`myntos.api.errors`.
"""

from myntos.domain.ledger.errors import LedgerError


class AttachmentError(LedgerError):
    """Base class for a rejected document."""


class UnsupportedDocumentError(AttachmentError):
    """The upload is not a PDF."""


class DocumentTooLargeError(AttachmentError):
    """The upload exceeds the size limit."""


class AttachmentNotFoundError(AttachmentError):
    """No such document in this company."""


class NoThumbnailError(AttachmentNotFoundError):
    """The document exists and has no thumbnail: a PDF, or an image no decoder opens.

    A not-found rather than its own status, because to the page asking it is the
    same answer: show the badge instead. The original is untouched either way.
    """
