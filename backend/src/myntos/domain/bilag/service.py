"""Attaching documents to vouchers.

Validation happens on the **bytes**, not on what the upload claims about them.
A ``Content-Type`` header and a file extension are both chosen by whoever is
uploading, so neither is evidence; the PDF magic number is. The filename is
kept only to display and to download under, and is stripped of everything that
could make it mean something to a filesystem or to a response header.
"""

from __future__ import annotations

import hashlib
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.bilag.errors import (
    AttachmentNotFoundError,
    DocumentTooLargeError,
    UnsupportedDocumentError,
)
from myntos.domain.bilag.models import (
    MAX_ATTACHMENT_BYTES,
    PDF_CONTENT_TYPE,
    PDF_MAGIC,
    Attachment,
)
from myntos.domain.ledger.models import Company, Transaction
from myntos.storage.base import DocumentStorage

# Anything outside this is replaced. Keeps Norwegian letters, drops quotes,
# newlines, slashes and control characters — the three things that would let a
# filename escape into a path or into a Content-Disposition header.
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9æøåÆØÅ._ -]+")
_FILENAME_MAX_LENGTH = 200


def sanitise_filename(filename: str) -> str:
    """A display name that is safe to store and to echo back in a header."""
    stem = filename.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    cleaned = _UNSAFE_FILENAME.sub("_", stem).strip(" .")[:_FILENAME_MAX_LENGTH]
    return cleaned or "bilag.pdf"


def validate_pdf(content: bytes) -> None:
    """Reject anything that is not a PDF of usable size."""
    if not content:
        raise UnsupportedDocumentError("filen er tom")
    if len(content) > MAX_ATTACHMENT_BYTES:
        megabytes = MAX_ATTACHMENT_BYTES // (1024 * 1024)
        raise DocumentTooLargeError(f"filen er større enn {megabytes} MB")
    if not content.startswith(PDF_MAGIC):
        raise UnsupportedDocumentError("bare PDF-filer kan lastes opp")


async def attach_document(
    db: AsyncSession,
    storage: DocumentStorage,
    *,
    company: Company,
    transaction: Transaction,
    content: bytes,
    filename: str,
    uploaded_by_user_id: int | None,
) -> Attachment:
    """Store a document and link it to ``transaction``.

    The file is written before the row is added, so a failed write never leaves
    a database row pointing at nothing. The reverse — an orphaned file after a
    failed transaction — is the harmless direction: it wastes bytes, where the
    other loses a legally required document.
    """
    validate_pdf(content)
    display_name = sanitise_filename(filename)
    key = await storage.save(content=content, filename=display_name, content_type=PDF_CONTENT_TYPE)
    attachment = Attachment(
        company_id=company.id,
        transaction_pk=transaction.id,
        filename=display_name,
        content_type=PDF_CONTENT_TYPE,
        byte_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        storage_key=key,
        uploaded_by_user_id=uploaded_by_user_id,
    )
    db.add(attachment)
    await db.flush()
    return attachment


async def list_attachments(
    db: AsyncSession, *, company: Company, voucher_pks: list[int]
) -> dict[int, list[Attachment]]:
    """Documents per voucher, for the given vouchers."""
    if not voucher_pks:
        return {}
    rows = await db.scalars(
        select(Attachment)
        .where(Attachment.company_id == company.id, Attachment.transaction_pk.in_(voucher_pks))
        .order_by(Attachment.id)
    )
    grouped: dict[int, list[Attachment]] = {}
    for attachment in rows:
        grouped.setdefault(attachment.transaction_pk, []).append(attachment)
    return grouped


async def get_attachment(db: AsyncSession, *, company: Company, attachment_id: int) -> Attachment:
    """One document belonging to ``company``, or :class:`AttachmentNotFoundError`."""
    attachment = await db.scalar(
        select(Attachment).where(
            Attachment.id == attachment_id, Attachment.company_id == company.id
        )
    )
    if attachment is None:
        raise AttachmentNotFoundError("finner ikke dokumentet")
    return attachment
