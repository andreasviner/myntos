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

import base64
import json
import logging
from collections.abc import AsyncIterator
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


# Looking at an image is nearly free; writing the answer out is what costs.
# Measured on the deployment: 0.19 s to encode a screenshot and prefill its
# 3 144 tokens, then 12 to 15 tokens a second. A question answered with a name
# is therefore a few seconds, where transcribing the same page is twenty.
_LOOK_TIMEOUT = httpx.Timeout(60.0, connect=5.0)


async def ask_about_image(
    content: bytes,
    *,
    content_type: str,
    question: str,
    max_tokens: int = 24,
) -> str:
    """Put one question to the vision model about one image.

    Separate from :func:`extract_file`, which asks for the page as text. This
    asks for something the text does not contain and the pixels do. The case it
    exists for is the counterparty of a webshop purchase: nine screenshots of an
    AliExpress order name two shops and never AliExpress, because an app does
    not print its own name where a letterhead would be. It draws its logo there
    instead, and a model looking at the picture answers "AliExpress" while a
    model reading the transcription cannot.

    The image goes as a ``data:`` URI, never a URL. Løynd does not fetch http(s)
    for a document, and neither does anything here: a bilag reaches the model as
    bytes we already hold, or not at all.
    """
    settings = get_settings()
    key = _key()
    if key is None:
        raise LoyndNotConfiguredError("Løynd er ikke konfigurert")
    if content_type not in IMAGE_CONTENT_TYPES:
        raise LoyndUnavailableError("bare bilder kan beskrives")
    if len(content) > MAX_UPLOAD_BYTES:
        raise LoyndUnavailableError("filen er for stor for Løynd")

    encoded = base64.b64encode(content).decode("ascii")
    payload: dict[str, Any] = {
        "model": settings.loynd_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{content_type};base64,{encoded}"},
                    },
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=_LOOK_TIMEOUT) as client:
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
        # Logged without the question and without the image: both are the bilag.
        logger.info("Løynd did not answer about an image: %s", type(error).__name__)
        raise LoyndUnavailableError("fikk ikke svar fra Løynd") from error

    try:
        answer = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise LoyndUnavailableError("uventet svarformat fra Løynd") from error
    if not isinstance(answer, str):
        raise LoyndUnavailableError("uventet svarformat fra Løynd")
    return answer.strip()


# The stream's own allowance. Longer than _HTTP_TIMEOUT because httpx applies a
# read timeout between *chunks*, not to the whole response: a stream that keeps
# producing tokens never trips it, while one that has genuinely stopped does.
_STREAM_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


async def stream(*, system: str, prompt: str, max_tokens: int = 16) -> AsyncIterator[str]:
    """The same completion as :func:`complete`, in pieces, as they are produced.

    Yields the text of each delta. Nothing else about the answer changes —
    ``temperature=0``, the same model, the same prompt — so a caller that
    concatenates every piece has exactly what :func:`complete` would have
    returned. That equivalence is the point: the streaming path exists so the
    user can watch a sentence being written instead of watching a spinner, and
    it must not become a second, subtly different way of asking.

    A failure after the first byte arrives as a frame rather than a status code,
    because the status line has already been sent. Those are raised as
    :class:`LoyndUnavailableError` like any other, which leaves the caller with a
    partial answer and a decision to make — see the callers in
    :mod:`myntos.api.v1.cashflow`, which keep what arrived and stop asking for
    more.
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
        "stream": True,
    }
    try:
        async with (
            httpx.AsyncClient(timeout=_STREAM_TIMEOUT) as client,
            client.stream(
                "POST",
                f"{settings_base_url()}/v1/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            ) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                for piece in _delta(line):
                    yield piece
    except (httpx.HTTPError, ValueError) as error:
        logger.info("Løynd's stream did not finish: %s", type(error).__name__)
        raise LoyndUnavailableError("fikk ikke svar fra Løynd") from error


def _delta(line: str) -> list[str]:
    """The text in one server-sent line, or nothing.

    Everything that is not a content delta is silently nothing: the opening
    frame carrying only a role, the closing one carrying only a finish reason,
    the usage frame, ``[DONE]``, blank separators, and ``reasoning_content``,
    which is a model's monologue and not its answer.
    """
    if not line.startswith("data:"):
        return []
    body = line[len("data:") :].strip()
    if not body or body == "[DONE]":
        return []
    try:
        frame = json.loads(body)
    except ValueError:
        # A frame we cannot read is not a reason to abandon the ones we can.
        return []
    if not isinstance(frame, dict):
        return []
    if "error" in frame:
        raise LoyndUnavailableError("Løynd avbrøt svaret")
    choices = frame.get("choices")
    if not isinstance(choices, list) or not choices:
        return []
    first = choices[0]
    if not isinstance(first, dict):
        return []
    delta = first.get("delta")
    if not isinstance(delta, dict):
        return []
    content = delta.get("content")
    return [content] if isinstance(content, str) and content else []


# --- Reading a document, rather than reasoning about one ---------------------------

# Løynd's own limit is 10 MB per file, and the archive's limit is lower still,
# so a document that reaches here has already been accepted by
# myntos.domain.bilag.service.validate_document. Stated anyway, because the two
# limits are set by different people for different reasons.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# Extraction is the slow half, and how slow depends on what was uploaded.
# Reading a PDF's text layer is parsing; reading a photograph is inference, and
# a phone screenshot of a receipt measured between ten and thirty-five seconds
# on the current single-card deployment. The old thirty-second ceiling was sized
# for the parsing case and would have timed out the first image of every batch,
# which fails in the most confusing possible way: the same file succeeds on the
# retry, because by then the model is warm.
_UPLOAD_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
_TRANSCRIBE_TIMEOUT = httpx.Timeout(150.0, connect=5.0)

#: What Myntos will hand Løynd. PDFs have a text layer; the rest are pixels and
#: come back transcribed by the vision model. Kept in step with
#: :data:`myntos.domain.bilag.models.ACCEPTED_CONTENT_TYPES` by the test suite,
#: because a file the archive accepts and the reader refuses is a bilag that can
#: be filed and never read.
IMAGE_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})


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
    # True when nothing readable came back. It used to mean "this is a scan and
    # nobody here runs OCR"; now that Løynd transcribes images, it means the page
    # genuinely carried no text — a blank scan, a photograph of a wall — or that
    # a PDF's text layer is empty and the file was not sent as an image. Either
    # way it is an answer about the document, not a failure to get one.
    empty: bool = False


async def extract_file(
    *, content: bytes, filename: str, content_type: str = "application/pdf"
) -> ExtractedDocument:
    """Send one document to Løynd and get its text back.

    ``POST /v1/files/extract`` takes a file and returns the text, and stores
    nothing — the same bargain as the chat route, which is what makes sending
    the whole document acceptable where sending it to a model abroad would not
    be.

    **Why the file rather than text we extracted ourselves.** This paragraph used
    to be a prediction: "the day it can look at a page instead of only reading
    its text layer, the change belongs entirely on Løynd's side". That day was
    2026-09-06. Løynd's vision model now transcribes PNG, JPEG, WebP and GIF, and
    Myntos gained the ability to read a photographed receipt without a single
    line of reading code changing here — the only edit was passing the real
    content type instead of a hardcoded "application/pdf". A pipeline that hands
    over a string it extracted locally could not have been upgraded that way,
    because the pixels would have been thrown away before the request was made.

    :func:`myntos.domain.bilag.text.extract_text` stays as the local fallback,
    and it is a real fallback rather than a formality: a deployment without a
    key reads its own PDFs and the register works exactly as before.
    """
    key = _key()
    if key is None:
        raise LoyndNotConfiguredError("Løynd er ikke konfigurert")
    if len(content) > MAX_UPLOAD_BYTES:
        raise LoyndUnavailableError("filen er for stor for Løynd")

    is_image = content_type in IMAGE_CONTENT_TYPES
    timeout = _TRANSCRIBE_TIMEOUT if is_image else _UPLOAD_TIMEOUT
    fallback_name = "bilag.png" if is_image else "bilag.pdf"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{settings_base_url()}/v1/files/extract",
                # The real content type, not a hardcoded one. Løynd picks its
                # extractor from the extension first and this second, so sending
                # "application/pdf" for a screenshot named by a phone — IMG_9355
                # with no extension after a browser is done with it — would have
                # it try to parse pixels as a PDF and answer 400.
                files={"file": (filename or fallback_name, content, content_type)},
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
