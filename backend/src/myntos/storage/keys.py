"""Storage keys — where a document is filed, decided by us and nobody else.

Shared by every backend so that a document moved from local disk to R2 keeps
its key, and so both backends agree on what a valid key looks like.

The key is generated and opaque. Nothing supplied by the uploader — filename,
content type, voucher number — takes part in building it: an upload endpoint is
precisely where a ``../../etc`` filename shows up, and a key that cannot express
a traversal cannot be talked into one. The original filename lives in the
database instead, for display and for the download header.
"""

from __future__ import annotations

import re
import uuid

from myntos.core.clock import utc_now

# The types something here actually stores: bilag from the upload endpoint
# (PDFs and photographs), and the XML Skatteetaten returns for a filed
# MVA-melding. Anything else is stored as .bin rather than guessed at.
# Photographs were stored as .bin until they had an entry here: harmless, since
# the type is on the row, but an operator looking in the bucket deserves to see
# what is in it. Keys already issued keep their .bin, because a key is never
# rewritten.
_EXTENSIONS = {
    "application/pdf": "pdf",
    "application/xml": "xml",
    "text/xml": "xml",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/heic": "heic",
}

# yyyy/<32 hex chars>.<ext> — exactly what new_key produces, and nothing else.
KEY_PATTERN = re.compile(r"^[0-9]{4}/[0-9a-f]{32}\.[a-z0-9]{1,8}$")

# --- Derived copies ---------------------------------------------------------------
#
# A thumbnail is made from an original and can be made again, so it is not part
# of the archive and must never be mistaken for it. It lives under its own
# prefix, its key is computed from the original's rather than stored, and it is
# the only kind of key a backend will overwrite (DocumentStorage.save_derived).
# An original key cannot be written through that path because it cannot match
# this pattern: the prefix is not four digits.
THUMBNAIL_PREFIX = "derived/thumbnail/"
DERIVED_KEY_PATTERN = re.compile(r"^derived/thumbnail/[0-9]{4}/[0-9a-f]{32}\.jpg$")


class UnknownStorageKeyError(ValueError):
    """The key is not one this application could have issued."""


def new_key(content_type: str) -> str:
    """A fresh opaque key, bucketed by year so a listing stays navigable."""
    extension = _EXTENSIONS.get(content_type, "bin")
    return f"{utc_now().year:04d}/{uuid.uuid4().hex}.{extension}"


def validate_key(key: str) -> str:
    """Return ``key`` if it has the shape we issue, else raise.

    Either shape: an original, or a copy derived from one. Both are read the
    same way; only :func:`validate_derived_key` decides what may be written over.
    """
    if not (KEY_PATTERN.match(key) or DERIVED_KEY_PATTERN.match(key)):
        raise UnknownStorageKeyError(f"ugyldig lagringsnøkkel: {key!r}")
    return key


def validate_derived_key(key: str) -> str:
    """Return ``key`` if it names a derived copy, else raise.

    The guard on the one write that may replace an object. An original key
    reaching it is a bug, and it fails here instead of overwriting a bilag.
    """
    if not DERIVED_KEY_PATTERN.match(key):
        raise UnknownStorageKeyError(f"ikke en nøkkel for en avledet kopi: {key!r}")
    return key


def thumbnail_key(original_key: str) -> str:
    """Where the thumbnail of the document under ``original_key`` is kept.

    Computed rather than stored, so there is no column to migrate and nothing to
    keep in step: deleting every derived object loses nothing that cannot be
    made again the next time the register is opened.
    """
    if not KEY_PATTERN.match(original_key):
        raise UnknownStorageKeyError(f"ugyldig lagringsnøkkel: {original_key!r}")
    stem = original_key.rsplit(".", maxsplit=1)[0]
    return f"{THUMBNAIL_PREFIX}{stem}.jpg"
