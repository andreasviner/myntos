"""Løynd — the model that reads a bilag.

Løynd (loynd.no, Lindeman Technologies AS) runs its inference servers in Norway
and retains nothing. That is the whole reason it is the one external service
allowed to see a user's documents: a bilag is a business record, often naming a
counterparty and sometimes a person, and sending it to a model hosted outside
the EEA would be a transfer this system has no basis for and no way to explain
in a personvernerklæring. Norwegian soil keeps the answer short.

The API is OpenAI-shaped (``POST /v1/chat/completions``), so nothing here is
Løynd-specific beyond the base URL and the model name — both configurable, so
pointing this at a self-hosted model is an env change rather than a rewrite.

**Every call is optional.** The register works without a key: the caller is
expected to fall back to its own reading, which is why unconfigured, unreachable
and nonsense-answer all raise the same kind of error rather than propagating a
failure to the user. An accounting system must not stop working because a model
is down.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from myntos.core.config import get_settings

logger = logging.getLogger(__name__)

# Bounded on purpose. The answer is a suggestion the user is going to check
# anyway, and a form that takes half a minute to fill itself in is worse than one
# the user fills in by hand. The read allowance is the generous half of the pair
# because a cold model can take several seconds to produce its first token, while
# a connection that will not open is not going to start working.
_HTTP_TIMEOUT = httpx.Timeout(20.0, connect=5.0)


class LoyndError(Exception):
    """Base class for every reason an answer did not arrive."""


class LoyndNotConfiguredError(LoyndError):
    """No API key. The deployment has opted out of the model entirely."""


class LoyndUnavailableError(LoyndError):
    """Løynd could not be reached, or did not answer as expected."""


def _key() -> str | None:
    """The API key, treating a blank one as no key at all.

    ``LOYND_KEY=`` in a ``.env`` is how a deployment says "not this one", and
    reading that as a key would send every uploaded bilag at an endpoint with an
    empty bearer token. It is also what keeps a developer's own key out of the
    test suite.
    """
    secret = get_settings().loynd_key
    if secret is None:
        return None
    value = secret.get_secret_value().strip()
    return value or None


def is_configured() -> bool:
    """Whether this deployment has a key, and so may ask the model anything."""
    return _key() is not None


async def complete(*, system: str, prompt: str, max_tokens: int = 16) -> str:
    """One completion, as plain text.

    ``temperature=0`` because the caller is asking a question with a right
    answer, not generating prose: the same document should classify the same way
    today and tomorrow, and a user who re-uploads a file expects the same
    proposal.
    """
    settings = get_settings()
    key = _key()
    if key is None:
        raise LoyndNotConfiguredError("Løynd er ikke konfigurert")

    payload: dict[str, Any] = {
        "model": settings.loynd_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.post(
                f"{settings_base_url()}/v1/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            body: dict[str, Any] = response.json()
    except (httpx.HTTPError, ValueError) as error:
        # Includes a non-2xx status, a timeout and a body that is not JSON.
        # Logged without the prompt: the prompt is the user's bilag.
        logger.info("Løynd did not answer: %s", type(error).__name__)
        raise LoyndUnavailableError("fikk ikke svar fra Løynd") from error

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise LoyndUnavailableError("uventet svarformat fra Løynd") from error
    if not isinstance(content, str):
        raise LoyndUnavailableError("uventet svarformat fra Løynd")
    return content.strip()


# --- Reading a document, rather than reasoning about one ---------------------------

# Løynd's own limit is 10 MB per file, and the archive's limit is lower still,
# so a document that reaches here has already been accepted by
# myntos.domain.bilag.service.validate_pdf. Stated anyway, because the two
# limits are set by different people for different reasons.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# Extraction is the slow half: the server is opening a PDF rather than emitting
# a dozen tokens, and a long document takes longer than a short question does.
_UPLOAD_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


@dataclass(frozen=True)
class ExtractedDocument:
    """What Løynd made of an uploaded file."""

    text: str
    # Whatever the file says about itself. A webshop receipt prints its own name
    # nowhere in the body and carries it in the PDF title: "Order details | eBay"
    # is the only place in that document the word "eBay" appears at all.
    title: str | None = None
    author: str | None = None
    page_count: int | None = None
    # True when the file is pages of pixels. Løynd does not run OCR, and neither
    # do we, so this is the honest answer rather than a failure.
    empty: bool = False


async def extract_file(*, content: bytes, filename: str) -> ExtractedDocument:
    """Send one document to Løynd and get its text back.

    ``POST /v1/files/extract`` takes a file and returns the text, and stores
    nothing — the same bargain as the chat route, which is what makes sending
    the whole document acceptable where sending it to a model abroad would not
    be.

    **Why the file rather than text we extracted ourselves.** Both ends of this
    pipeline are going to move. Løynd's extractor already handles formats and
    malformed PDFs that pypdf gives up on, and the day it can look at a page
    instead of only reading its text layer, the change belongs entirely on
    Løynd's side: Myntos is already handing over the document. A pipeline that
    hands over a string it extracted locally can never be upgraded that way,
    because the pixels were thrown away before the request was made.

    :func:`myntos.domain.bilag.text.extract_text` stays as the local fallback,
    and it is a real fallback rather than a formality: a deployment without a
    key reads its own PDFs and the register works exactly as before.
    """
    key = _key()
    if key is None:
        raise LoyndNotConfiguredError("Løynd er ikke konfigurert")
    if len(content) > MAX_UPLOAD_BYTES:
        raise LoyndUnavailableError("filen er for stor for Løynd")

    try:
        async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT) as client:
            response = await client.post(
                f"{settings_base_url()}/v1/files/extract",
                files={"file": (filename or "bilag.pdf", content, "application/pdf")},
                headers={"Authorization": f"Bearer {key}"},
            )
            response.raise_for_status()
            body: dict[str, Any] = response.json()
    except (httpx.HTTPError, ValueError) as error:
        # Logged without the filename: a filename is the user's business too.
        logger.info("Løynd could not read a document: %s", type(error).__name__)
        raise LoyndUnavailableError("fikk ikke lest dokumentet hos Løynd") from error

    if not isinstance(body, dict):
        raise LoyndUnavailableError("uventet svarformat fra Løynd")
    text = body.get("text")
    if not isinstance(text, str):
        raise LoyndUnavailableError("uventet svarformat fra Løynd")

    metadata = body.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    return ExtractedDocument(
        text=text,
        title=_string(metadata.get("title")),
        author=_string(metadata.get("author")),
        page_count=_count(metadata.get("pages")),
        empty=bool(body.get("empty")) or not text.strip(),
    )


def _string(value: object) -> str | None:
    """One metadata field, or nothing. PDF metadata is written by anybody."""
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned[:200] or None


def _count(value: object) -> int | None:
    """A page count, which the response carries as a string."""
    try:
        pages = int(str(value))
    except (TypeError, ValueError):
        return None
    return pages if pages > 0 else None


def settings_base_url() -> str:
    return get_settings().loynd_base_url.rstrip("/")
