"""Getting the text out of an uploaded PDF.

Kept apart from :mod:`myntos.domain.bilag.interpretation` because the two fail
in different ways and for different reasons. This module deals with a file
format — a PDF may be encrypted, damaged or built entirely out of scanned
images — while interpretation deals with what an invoice *says*. Splitting them
means the reading heuristics can be tested on plain strings, with no PDF in
sight.

Nothing here raises. A document we cannot read is not a rejected upload: the
user is allowed to attach it and fill the form in by hand, so the caller gets a
result that says "no text" and passes that on as an explanation.

Two bounds, because the bytes come from outside: at most
:data:`MAX_PAGES` pages are read and at most :data:`MAX_TEXT_CHARS` characters
are returned. An invoice states its total on the first page; a thousand-page
PDF is either a mistake or an attempt to spend our CPU.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError

logger = logging.getLogger(__name__)

MAX_PAGES = 5
MAX_TEXT_CHARS = 200_000


@dataclass(frozen=True)
class ExtractedText:
    """The text of a document, and why there might not be any."""

    text: str
    page_count: int
    # What the file says about itself, from the PDF's own metadata. A webshop
    # receipt often prints its own name nowhere in the body and carries it here
    # instead: "Order details | eBay" is the only place in an eBay order
    # confirmation that the word "eBay" occurs as text at all. Worth as much as
    # a whole page of the body to whoever has to work out who sold this.
    title: str | None = None
    # Password-protected, and not with the empty password. Nothing to read.
    encrypted: bool = False
    # Malformed enough that pypdf gave up. Distinguished from "no text" because
    # it is worth a different sentence to the user.
    unreadable: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


def extract_text(content: bytes) -> ExtractedText:
    """Read the text layer of ``content``.

    A PDF produced by an accounting system carries its text as text and comes
    out of here in full. A photographed or scanned receipt carries pixels and
    comes out empty — which is the honest answer, not an error, since OCR is
    not something this system does.
    """
    try:
        reader = PdfReader(BytesIO(content), strict=False)
        # An empty user password is common in invoices that are "protected" only
        # against editing; anything else is genuinely closed to us.
        if reader.is_encrypted and reader.decrypt("") == 0:
            return ExtractedText(text="", page_count=0, encrypted=True)
        pages = reader.pages[:MAX_PAGES]
        parts = [page.extract_text() or "" for page in pages]
        page_count = len(reader.pages)
        title = _title(reader)
    except (PyPdfError, ValueError, OSError, RecursionError):
        # Damaged, truncated or hostile. Logged rather than raised: the upload
        # itself is still a valid PDF as far as the archive is concerned.
        logger.info("could not extract text from an uploaded PDF", exc_info=True)
        return ExtractedText(text="", page_count=0, unreadable=True)

    return ExtractedText(text="\n".join(parts)[:MAX_TEXT_CHARS], page_count=page_count, title=title)


# A PDF title is written by whoever produced the file, so it is treated like any
# other untrusted string: collapsed, bounded, and discarded when it is one of the
# placeholders a word processor leaves behind.
MAX_TITLE_CHARS = 200
_USELESS_TITLES = frozenset(
    {"untitled", "uten navn", "document", "dokument", "microsoft word", "pdf", "print"}
)


def _title(reader: PdfReader) -> str | None:
    """The document's own title, when it has one worth having."""
    try:
        raw = reader.metadata.title if reader.metadata is not None else None
    except (PyPdfError, ValueError, KeyError, AttributeError):
        return None
    if not isinstance(raw, str):
        return None
    cleaned = " ".join(raw.split())[:MAX_TITLE_CHARS]
    if len(cleaned) < 3 or cleaned.casefold() in _USELESS_TITLES:
        return None
    return cleaned
