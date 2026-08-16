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
                f"{settings.loynd_base_url.rstrip('/')}/v1/chat/completions",
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
