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

# Only the types the upload endpoint accepts; anything else is stored as .bin
# rather than guessed at.
_EXTENSIONS = {"application/pdf": "pdf"}

# yyyy/<32 hex chars>.<ext> — exactly what new_key produces, and nothing else.
KEY_PATTERN = re.compile(r"^[0-9]{4}/[0-9a-f]{32}\.[a-z0-9]{1,8}$")


class UnknownStorageKeyError(ValueError):
    """The key is not one this application could have issued."""


def new_key(content_type: str) -> str:
    """A fresh opaque key, bucketed by year so a listing stays navigable."""
    extension = _EXTENSIONS.get(content_type, "bin")
    return f"{utc_now().year:04d}/{uuid.uuid4().hex}.{extension}"


def validate_key(key: str) -> str:
    """Return ``key`` if it has the shape we issue, else raise."""
    if not KEY_PATTERN.match(key):
        raise UnknownStorageKeyError(f"ugyldig lagringsnøkkel: {key!r}")
    return key
