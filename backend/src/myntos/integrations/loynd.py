"""Løynd — the model that reads a bilag.

Løynd (loynd.no, Lindeman Technologies AS) runs its inference in Norway and the
EU and retains nothing. That is the whole reason it is the one external service
allowed to see a user's documents: a bilag is a business record, often naming a
counterparty and sometimes a person, and sending it to a model hosted outside
the EEA would be a transfer this system has no basis for and no way to explain
in a personvernerklæring.

It said "in Norway" until 2026-11-25. The free model still runs on Løynd's own
machines in Norway, but the paid levels (:class:`Level`) are larger models, and
Løynd serves those with extra inference capacity in the EU as well. So the
promise Myntos can keep for every level is the EEA one, and that is the one it
makes: CLAUDE.md rule 5 was always about the EEA, and Norway was a stricter
answer than the rule asked for.

The API is OpenAI-shaped (``POST /v1/chat/completions``), so nothing here is
Løynd-specific beyond the base URL and the model names — all configurable, so
pointing this at a self-hosted model is an env change rather than a rewrite.

**Every call is optional.** The register works without a key: the caller is
expected to fall back to its own reading, which is why unconfigured, unreachable
and nonsense-answer all raise the same kind of error rather than propagating a
failure to the user. An accounting system must not stop working because a model
is down.
"""

from __future__ import annotations

import base64
import enum
import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
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

    #: True once this module has already logged the failure at ERROR, so a
    #: caller that catches ``LoyndError`` and degrades gracefully does not have
    #: to guess whether the failure was recorded or log a duplicate.
    logged: bool = False


def _error_detail(error: BaseException) -> str:
    """Short safe cause for logs: status code or exception type, never body or key."""
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}"
    return type(error).__name__


def _logged(error: LoyndError) -> LoyndError:
    error.logged = True
    return error


class LoyndNotConfiguredError(LoyndError):
    """No API key. The deployment has opted out of the model entirely."""


class LoyndUnavailableError(LoyndError):
    """Løynd could not be reached, or did not answer as expected."""


class LoyndPaidLevelRefusedError(LoyndUnavailableError):
    """Løynd answered a paid model with a refusal, and the free one may still answer.

    A subclass of "unavailable" because every caller already treats that as
    "no opinion" and fills the form in without the model. Separate so the one
    caller that can do better, a paid level falling back to the free one, can
    tell it apart from a model that is down altogether.
    """


class LoyndPaymentRequiredError(LoyndPaidLevelRefusedError):
    """Løynd refused a paid model because Myntos's own balance there is spent."""


class Level(enum.StrEnum):
    """How much model a question gets: what the person picks beside the text box.

    Three, and the names are the ones the page shows in bokmål («Gratis»,
    «Medium», «Smart»). Each is a *level* and not a model on purpose: the
    model behind it is configuration (``LOYND_MODEL``, ``LOYND_MODEL_MEDIUM``,
    ``LOYND_MODEL_THINKING``), and by default the paid two are Løynd's own live
    tiers, which follow the best model in their class without a release here.
    """

    # Løynd's own model on its own machines. Costs Myntos nothing per token, so
    # it is counted against nothing and every company has it.
    FREE = "free"
    # A larger model, answering directly. Paid: counted against the plan.
    MEDIUM = "medium"
    # The largest, which reasons before it answers. Paid, and the reasoning is
    # tokens too, which is why it is the level a plan runs out of first.
    THINKING = "thinking"

    @property
    def is_paid(self) -> bool:
        return self is not Level.FREE


# Room for the monologue. The callers ask for budgets sized to the answer (a
# menu number is 16 tokens), and a reasoning model spends its thinking out of
# the same max_tokens before it writes a word of the answer: asked for 16, it
# thinks for 16 and answers nothing. So the paid levels get this on top, and the
# tokens it actually uses are counted against the plan like any other.
_REASONING_ROOM: dict[Level, int] = {
    Level.FREE: 0,
    Level.MEDIUM: 4096,
    Level.THINKING: 12288,
}

# How long one whole answer may take. The free model was measured (the comment
# on _HTTP_TIMEOUT); the paid ones think first, and thinking for twelve
# thousand tokens takes minutes rather than seconds. A stream is not bound by
# these, because httpx applies a read timeout between chunks and a reasoning
# model streams its monologue as it goes.
_READ_TIMEOUT: dict[Level, float] = {
    Level.FREE: 20.0,
    Level.MEDIUM: 120.0,
    Level.THINKING: 300.0,
}


_MICRO_NOK = Decimal("0.000001")
_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class ModelPricing:
    """Live per-million-token pricing for one Løynd model or tier, ex. VAT, in NOK.

    Parsed directly from Løynd's ``GET /v1/models?currency=NOK`` response,
    where ``pricing`` gives net (ex. VAT) NOK prices per 1M tokens.
    """

    input_nok_per_million: Decimal
    output_nok_per_million: Decimal
    free: bool = False

    def cost_nok(self, prompt_tokens: int, completion_tokens: int) -> Decimal:
        """Cost in NOK (ex. VAT) quantized to 6 decimal places (micro-kroner)."""
        if self.free or (prompt_tokens <= 0 and completion_tokens <= 0):
            return Decimal("0.000000")
        prompt = Decimal(max(prompt_tokens, 0))
        completion = Decimal(max(completion_tokens, 0))
        raw = (
            prompt * self.input_nok_per_million + completion * self.output_nok_per_million
        ) / _MILLION
        return raw.quantize(_MICRO_NOK, rounding=ROUND_HALF_UP)


#: Cold-start fallback rates (ex. VAT, NOK per 1M tokens) used only when live
#: pricing from Løynd's ``/v1/models?currency=NOK`` has never been fetched yet.
#: Matches Løynd's published NOK tier prices (medium 1.38/6.44, high 8.625/50.60).
_FALLBACK_PRICING: dict[Level, ModelPricing] = {
    Level.FREE: ModelPricing(Decimal("0"), Decimal("0"), free=True),
    Level.MEDIUM: ModelPricing(Decimal("1.38"), Decimal("6.44")),
    Level.THINKING: ModelPricing(Decimal("8.625"), Decimal("50.60")),
}


@dataclass(frozen=True)
class PaidCall:
    """One paid completion answered by Løynd, for live NOK cost calculation."""

    level: Level
    model_id: str | None
    prompt_tokens: int
    completion_tokens: int


def _extract_tokens(reported: object) -> tuple[int, int]:
    """Read non-negative integer prompt/completion counts from a ``usage`` dict."""
    if not isinstance(reported, dict):
        return 0, 0
    raw_prompt = reported.get("prompt_tokens")
    raw_completion = reported.get("completion_tokens")
    prompt = (
        raw_prompt
        if isinstance(raw_prompt, int) and not isinstance(raw_prompt, bool) and raw_prompt > 0
        else 0
    )
    completion = (
        raw_completion
        if isinstance(raw_completion, int)
        and not isinstance(raw_completion, bool)
        and raw_completion > 0
        else 0
    )
    return prompt, completion


@dataclass
class Usage:
    """Tokens spent, added up across every call that shares one.

    Mutable and passed in, rather than returned, because a reading of a bilag is
    several calls (the category, the fields, a follow-up) made from different
    places, and the plan is charged for the reading. One counter per request,
    read once at the end.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, reported: object) -> None:
        """Add one response's ``usage`` object. Anything unreadable adds nothing.

        Nothing rather than an error: an answer that arrived is worth more than
        the count of what it cost, and a provider that stops reporting usage
        should cost Myntos money rather than cost the user their proposal.
        """
        prompt, completion = _extract_tokens(reported)
        self.prompt_tokens += prompt
        self.completion_tokens += completion


def model_name(level: Level) -> str:
    """The model Løynd is asked for at ``level``."""
    settings = get_settings()
    if level is Level.MEDIUM:
        return settings.loynd_model_medium
    if level is Level.THINKING:
        return settings.loynd_model_thinking
    return settings.loynd_model


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


# Said on every chat request, because Løynd can look things up on the web before
# it answers, and for a caller with a key of its own it does so by default: a
# routing pass reads the conversation, the first 2 000 characters of each
# message, and decides whether to send queries to the search instance beside
# it, which forwards them to the search engines it fronts. For Myntos the
# conversation is a bilag. A seller's name or an order number turned into a
# search query has left the EEA (CLAUDE.md rule 5), and no question asked here
# is one the web can answer. Nothing said either way until 2026-10-17. The
# routing pass is also a generation of its own ahead of every answer, so it cost
# time on every read as well.
_NEVER_SEARCH = {"search": False}


def _messages(
    *, system: str, prompt: str, history: Sequence[tuple[str, str]] = ()
) -> list[dict[str, str]]:
    """The conversation as the chat route takes it.

    ``history`` is the earlier questions and their answers, oldest first, for a
    question that continues a conversation instead of starting one (see
    :mod:`myntos.domain.bilag.followup`). Repeated exactly as they were asked,
    because that is what lets the model server reuse the work it already did on
    them: the bilag at the top of the first question is most of the prompt, and
    a follow-up whose beginning is byte for byte the same pays only for the new
    turn at the end.
    """
    messages = [{"role": "system", "content": system}]
    for asked, answered in history:
        messages.append({"role": "user", "content": asked})
        messages.append({"role": "assistant", "content": answered})
    messages.append({"role": "user", "content": prompt})
    return messages


#: Løynd's response headers naming the concrete model that answered and where
#: it ran (``no-local`` on its own machines in Norway, ``eu`` on the extra
#: capacity). Present on every chat reply.
MODEL_HEADER = "X-Loynd-Model"
LOCATION_HEADER = "X-Loynd-Location"

#: The handle a free-level question is asked on behalf of. :meth:`Model.complete`
#: answers the free level through the module's :func:`complete`, which a test
#: may stand in for and which makes a handle of its own; this is how what that
#: answer said about itself reaches the handle the caller holds.
_ON_BEHALF: ContextVar[Model | None] = ContextVar("loynd_on_behalf", default=None)


@dataclass
class Model:
    """One level of Løynd for one request, and the count of what it used there.

    What a caller holds instead of the module's functions when the person chose
    a level: ``Model(Level.THINKING).complete`` has the same signature as
    :func:`complete`, so it goes wherever an ``ask`` callable goes
    (:mod:`myntos.domain.bilag.classification`, the invoice drafting) and none of
    those modules learns that there is more than one model.

    The level is decided before this is built (:mod:`myntos.domain.ki.service`
    checks the plan); nothing here knows about plans or prices. Two things are
    learnt while it is used, and read by the caller when the request is done:

    - :attr:`usage`, the tokens spent at a paid level. Only those: the free
      level costs Myntos nothing per token and is counted against nothing.
    - :attr:`fell_back`, set when Løynd refused the paid level (a spent balance,
      a busy or missing model, capacity in the EU that is down) and the question
      was asked again at the free one. From then on every question
      on this handle goes to the free level, so one request does not ask a
      refusing model twice.
    """

    level: Level = Level.FREE
    usage: Usage = field(default_factory=Usage)
    #: Paid completions answered on this handle, each tagged with the level and
    #: concrete ``X-Loynd-Model`` so live NOK cost is computed accurately even
    #: if Løynd switches tiers or a later call falls back to ``free``.
    paid_calls: list[PaidCall] = field(default_factory=list)
    fell_back: bool = False
    #: Every token Løynd reported for this handle's answers, the free level
    #: included. Not charged to anybody: the free level costs Myntos GPU time
    #: rather than money, and a background run records it so the operator can
    #: see what reading a company's books took (``ki_run.tokens_total``).
    reported: Usage = field(default_factory=Usage)
    #: ``X-Loynd-Model`` and ``X-Loynd-Location`` of the last answer to
    #: :meth:`complete`: the concrete model behind a tier (``medium`` and
    #: ``high`` are live aliases, so the level alone cannot date an answer) and
    #: where it ran. What KI stores is stamped with them (docs/analyse.md «The
    #: stamp»). None until an answer carried them.
    answered_model: str | None = None
    answered_location: str | None = None
    #: How long one answer may take, in seconds, where the level's own allowance
    #: (``_READ_TIMEOUT``) is too short: a batch of vouchers answered in one go
    #: is a far longer answer than a bilag's few fields. Never shortens it.
    read_timeout: float | None = None

    def _refused(self) -> None:
        self.level = Level.FREE
        self.fell_back = True

    def _heard(self, response: httpx.Response) -> None:
        """Keep who answered, on this handle and the one asking."""
        asking = _ON_BEHALF.get()
        for handle in (self,) if asking is None or asking is self else (self, asking):
            handle.answered_model = response.headers.get(MODEL_HEADER) or None
            handle.answered_location = response.headers.get(LOCATION_HEADER) or None

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 16,
        history: Sequence[tuple[str, str]] = (),
    ) -> str:
        """One completion, as plain text, at this handle's level.

        The free level goes through the module's :func:`complete`, looked up when
        it is called, so the free path is the one it always was, and whatever
        stands in for it (a test's fake model) stands in for this too.
        """
        if self.level.is_paid:
            try:
                return await self._complete(
                    system=system, prompt=prompt, max_tokens=max_tokens, history=history
                )
            except LoyndPaidLevelRefusedError:
                self._refused()
        # The module's complete() answers on a handle of its own; this lets it
        # tell this one who answered, what it reported and how long to wait.
        token = _ON_BEHALF.set(self)
        try:
            if history:
                return await complete(
                    system=system, prompt=prompt, max_tokens=max_tokens, history=history
                )
            # Called exactly as callers called it before there were levels,
            # without a history it does not have.
            return await complete(system=system, prompt=prompt, max_tokens=max_tokens)
        finally:
            _ON_BEHALF.reset(token)

    async def stream(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 16,
        history: Sequence[tuple[str, str]] = (),
    ) -> AsyncIterator[str]:
        """:meth:`complete`, in pieces, falling back the same way.

        A refusal arrives as the status line, before the first piece, so asking
        again at the free level never repeats text the caller already has.
        """
        if self.level.is_paid:
            started = False
            try:
                async for piece in self._stream(
                    system=system, prompt=prompt, max_tokens=max_tokens, history=history
                ):
                    started = True
                    yield piece
                return
            except LoyndPaidLevelRefusedError:
                if started:
                    raise
                self._refused()
        # The module's stream, by name, for the reason given in complete().
        token = _ON_BEHALF.set(self)
        try:
            if history:
                async for piece in stream(
                    system=system, prompt=prompt, max_tokens=max_tokens, history=history
                ):
                    yield piece
            else:
                async for piece in stream(system=system, prompt=prompt, max_tokens=max_tokens):
                    yield piece
        finally:
            _ON_BEHALF.reset(token)

    def _count(self, reported: object) -> None:
        if self.level.is_paid:
            self.usage.add(reported)
            prompt, completion = _extract_tokens(reported)
            if prompt > 0 or completion > 0:
                self.paid_calls.append(
                    PaidCall(
                        level=self.level,
                        model_id=self.answered_model,
                        prompt_tokens=prompt,
                        completion_tokens=completion,
                    )
                )
        asking = _ON_BEHALF.get()
        for handle in (self,) if asking is None or asking is self else (self, asking):
            handle.reported.add(reported)

    async def cost_nok(self) -> Decimal:
        """Total live NOK cost (ex. VAT) of paid calls made on this handle."""
        return await cost_nok_for_calls(self.paid_calls)

    async def _complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 16,
        history: Sequence[tuple[str, str]] = (),
    ) -> str:
        """One completion, as plain text.

        ``temperature=0`` because the caller is asking a question with a right
        answer, not generating prose: the same document should classify the same
        way today and tomorrow, and a user who re-uploads a file expects the same
        proposal.
        """
        key = _key()
        if key is None:
            raise LoyndNotConfiguredError("Løynd er ikke konfigurert")

        payload: dict[str, Any] = {
            "model": model_name(self.level),
            "messages": _messages(system=system, prompt=prompt, history=history),
            "max_tokens": max_tokens + _REASONING_ROOM[self.level],
            "temperature": 0,
            "stream": False,
            **_NEVER_SEARCH,
        }
        asking = _ON_BEHALF.get()
        read = self.read_timeout or (asking.read_timeout if asking is not None else None)
        timeout = httpx.Timeout(max(read or 0.0, _READ_TIMEOUT[self.level]), connect=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{settings_base_url()}/v1/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                )
                _refuse_paid_level(response, self.level)
                response.raise_for_status()
                body: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError) as error:
            # Includes a non-2xx status, a timeout and a body that is not JSON.
            # Logged at ERROR without the prompt (the prompt is the user's bilag)
            # so Løynd outages always land in the journal and on /drift/.
            logger.error(
                "Løynd did not answer %s (%s): %s",
                model_name(self.level),
                self.level.value,
                _error_detail(error),
                exc_info=error,
            )
            raise _logged(LoyndUnavailableError("fikk ikke svar fra Løynd")) from error

        # Read headers before counting usage so PaidCall carries X-Loynd-Model.
        self._heard(response)
        if isinstance(body, dict):
            self._count(body.get("usage"))
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            logger.error(
                "Løynd returned an unexpected chat response for %s (%s)",
                model_name(self.level),
                self.level.value,
                exc_info=error,
            )
            raise _logged(LoyndUnavailableError("uventet svarformat fra Løynd")) from error
        if not isinstance(content, str):
            logger.error(
                "Løynd returned a non-text chat response for %s (%s)",
                model_name(self.level),
                self.level.value,
            )
            raise _logged(LoyndUnavailableError("uventet svarformat fra Løynd"))
        return content.strip()

    async def _stream(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 16,
        history: Sequence[tuple[str, str]] = (),
    ) -> AsyncIterator[str]:
        """The same completion as :meth:`complete`, in pieces, as they are produced.

        Yields the text of each delta. Nothing else about the answer changes:
        ``temperature=0``, the same model, the same prompt, so a caller that
        concatenates every piece has exactly what :meth:`complete` would have
        returned. That equivalence is the point: the streaming path exists so the
        user can watch a sentence being written instead of watching a spinner,
        and it must not become a second, subtly different way of asking.

        A failure after the first byte arrives as a frame rather than a status
        code, because the status line has already been sent. Those are raised as
        :class:`LoyndUnavailableError` like any other, which leaves the caller
        with a partial answer and a decision to make (see the callers in
        :mod:`myntos.api.v1.cashflow`, which keep what arrived and stop asking
        for more).
        """
        key = _key()
        if key is None:
            raise LoyndNotConfiguredError("Løynd er ikke konfigurert")

        payload: dict[str, Any] = {
            "model": model_name(self.level),
            "messages": _messages(system=system, prompt=prompt, history=history),
            "max_tokens": max_tokens + _REASONING_ROOM[self.level],
            "temperature": 0,
            "stream": True,
            **_NEVER_SEARCH,
        }
        try:
            async with (
                httpx.AsyncClient(timeout=_stream_timeout(self.level)) as client,
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
                _refuse_paid_level(response, self.level)
                response.raise_for_status()
                self._heard(response)
                async for line in response.aiter_lines():
                    for piece in _delta(line, count=self._count):
                        yield piece
        except (httpx.HTTPError, ValueError) as error:
            logger.error(
                "Løynd stream did not finish for %s (%s): %s",
                model_name(self.level),
                self.level.value,
                _error_detail(error),
                exc_info=error,
            )
            raise _logged(LoyndUnavailableError("fikk ikke svar fra Løynd")) from error


#: Refusals of a paid model that say nothing about the free one, which runs on
#: Løynd's own machines in Norway: 403 when the key is one Løynd keeps in Norway
#: (the paid models may run in the EU), 404 when a model name in .env is not one
#: Løynd has, 429 when the paid model is busy, and 5xx when the extra capacity in
#: the EU is down. A timeout is not among them: asking again after waiting out a
#: paid model's long timeout would double a wait the person is already in.
_PAID_LEVEL_REFUSALS = frozenset({403, 404, 429})


def _refuse_paid_level(response: httpx.Response, level: Level) -> None:
    """Raise the refusals a paid level can recover from, before the generic one.

    402 is Løynd saying Myntos's own balance there is spent (or, for a key with a
    token limit, that the limit is reached). The others are in
    ``_PAID_LEVEL_REFUSALS``. None is the company's fault, and the free level
    still answers: :class:`Model` asks it instead, and the answer says so.
    """
    status = response.status_code
    if status == httpx.codes.PAYMENT_REQUIRED:
        logger.error(
            "Løynd refused paid model %s: Myntos's balance there is spent", model_name(level)
        )
        raise _logged(LoyndPaymentRequiredError("Løynd krever påfyll for denne modellen"))
    if level.is_paid and (status in _PAID_LEVEL_REFUSALS or status >= 500):
        logger.error("Løynd refused paid model %s with HTTP %d", model_name(level), status)
        raise _logged(LoyndPaidLevelRefusedError("Løynd svarte ikke med den betalte modellen"))


async def complete(
    *,
    system: str,
    prompt: str,
    max_tokens: int = 16,
    history: Sequence[tuple[str, str]] = (),
) -> str:
    """:meth:`Model.complete` at the free level, counted against nothing.

    Kept as a function because it is what every caller passed as ``ask`` before
    there were levels, and the free level is still what a company without a plan
    gets.
    """
    return await Model()._complete(
        system=system, prompt=prompt, max_tokens=max_tokens, history=history
    )


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
        **_NEVER_SEARCH,
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
        # Logged at ERROR without the question and without the image: both are the bilag.
        logger.error(
            "Løynd did not answer about an image: %s", _error_detail(error), exc_info=error
        )
        raise _logged(LoyndUnavailableError("fikk ikke svar fra Løynd")) from error

    try:
        answer = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        logger.error("Løynd returned an unexpected image response", exc_info=error)
        raise _logged(LoyndUnavailableError("uventet svarformat fra Løynd")) from error
    if not isinstance(answer, str):
        logger.error("Løynd returned a non-text image response")
        raise _logged(LoyndUnavailableError("uventet svarformat fra Løynd"))
    return answer.strip()


# The stream's own allowance. Longer than _HTTP_TIMEOUT because httpx applies a
# read timeout between *chunks*, not to the whole response: a stream that keeps
# producing tokens never trips it, while one that has genuinely stopped does.
_STREAM_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


def _stream_timeout(level: Level) -> httpx.Timeout:
    """The gap a stream may go quiet for, at ``level``.

    The paid levels run on shared capacity in the EU, where a request can wait in
    a queue before its first token, and the free model on Løynd's own cards does
    not. Thirty seconds of silence from those is a dead stream; from these it can
    be a busy hour.
    """
    if level is Level.FREE:
        return _STREAM_TIMEOUT
    return httpx.Timeout(90.0, connect=5.0)


async def stream(
    *,
    system: str,
    prompt: str,
    max_tokens: int = 16,
    history: Sequence[tuple[str, str]] = (),
) -> AsyncIterator[str]:
    """:meth:`Model.stream` at the free level. See :func:`complete` for why."""
    async for piece in Model()._stream(
        system=system, prompt=prompt, max_tokens=max_tokens, history=history
    ):
        yield piece


def _delta(line: str, *, count: Callable[[object], None] | None = None) -> list[str]:
    """The text in one server-sent line, or nothing.

    Everything that is not a content delta is silently nothing: the opening
    frame carrying only a role, the closing one carrying only a finish reason,
    ``[DONE]``, blank separators, and ``reasoning_content``, which is a model's
    monologue and not its answer. The usage frame is nothing too, as text, but
    its counts go to ``count`` when the caller is counting.
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
        logger.error("Løynd aborted the stream with an error frame")
        raise _logged(LoyndUnavailableError("Løynd avbrøt svaret"))
    if count is not None and "usage" in frame:
        count(frame.get("usage"))
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
        # Logged at ERROR without the filename: a filename is the user's business too.
        logger.error("Løynd could not read a document: %s", _error_detail(error), exc_info=error)
        raise _logged(LoyndUnavailableError("fikk ikke lest dokumentet hos Løynd")) from error

    if not isinstance(body, dict):
        logger.error("Løynd returned an unexpected extract response")
        raise _logged(LoyndUnavailableError("uventet svarformat fra Løynd"))
    text = body.get("text")
    if not isinstance(text, str):
        logger.error("Løynd returned a non-text extract response")
        raise _logged(LoyndUnavailableError("uventet svarformat fra Løynd"))

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


@dataclass(frozen=True)
class Served:
    """What answers at one level today."""

    #: The concrete model: a tier's ``resolves_to``, or the model's own name.
    model: str
    #: The checkpoint, for Løynd's own model, whose name stays the same when
    #: the weights behind it change. None for the rest: their name is the
    #: version.
    revision: str | None


# An hour: a re-pointed tier or a swapped checkpoint is a reason to read
# vouchers again some day, not this minute, and asking for every batch would
# put two requests in front of every question.
_MODELS_TTL = 3600.0
_models_cache: tuple[float, dict[str, Served]] | None = None

# 15 minutes for live pricing from Løynd's /v1/models so tier or price changes
# update automatically without adding an HTTP round-trip to every completion.
# When X-Loynd-Model names a model not yet in the cache, a forced refresh runs
# immediately (rate-limited to once per minute).
_PRICING_TTL = 900.0
_PRICING_MIN_REFRESH = 60.0
_pricing_cache: tuple[float, dict[str, ModelPricing]] | None = None
_pricing_last_attempt: float = 0.0


def reset_pricing_cache() -> None:
    """Clear cached Løynd model pricing (intended for tests)."""
    global _pricing_cache, _pricing_last_attempt
    _pricing_cache = None
    _pricing_last_attempt = 0.0


def _parse_pricing(entry: object) -> ModelPricing | None:
    """Extract live NOK per-million-token pricing from one ``/v1/models?currency=NOK`` entry."""
    if not isinstance(entry, dict):
        return None
    pricing = entry.get("pricing")
    if not isinstance(pricing, dict):
        return None
    if pricing.get("free") is True:
        return ModelPricing(Decimal("0"), Decimal("0"), free=True)
    raw_in = pricing.get("input_per_million")
    raw_out = pricing.get("output_per_million")
    if raw_in is None or raw_out is None:
        return None
    try:
        in_price = Decimal(str(raw_in))
        out_price = Decimal(str(raw_out))
    except InvalidOperation:
        return None
    if in_price < 0 or out_price < 0:
        return None
    currency = str(pricing.get("currency") or "").upper()
    if currency == "NOK":
        rate = Decimal("1")
    else:
        # Never treat non-NOK prices as NOK or guess an FX rate; only convert
        # when the response itself provides an explicit positive rate.
        raw_rate = pricing.get("eur_to_nok_rate")
        if raw_rate is None:
            return None
        try:
            rate = Decimal(str(raw_rate))
        except InvalidOperation:
            return None
        if rate <= 0:
            return None
    return ModelPricing(
        input_nok_per_million=in_price * rate,
        output_nok_per_million=out_price * rate,
        free=in_price == 0 and out_price == 0,
    )


def _store_pricing_from_entries(entries: object) -> dict[str, ModelPricing]:
    """Parse pricing for every model and tier in ``entries`` and cache it."""
    global _pricing_cache
    if not isinstance(entries, list):
        return {}
    parsed: dict[str, ModelPricing] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        price = _parse_pricing(entry)
        if price is not None:
            parsed[entry["id"]] = price
    if parsed:
        _pricing_cache = (time.monotonic(), parsed)
    return parsed


async def current_pricing(*, force: bool = False) -> dict[str, ModelPricing]:
    """Live per-million-token NOK pricing keyed by Løynd model/tier id.

    Fetched from ``GET /v1/models?currency=NOK`` and cached for 15 minutes. On
    a temporary fetch failure, returns the last known live table rather than
    breaking accounting; before any live table has been fetched, callers fall
    back to :data:`_FALLBACK_PRICING`.
    """
    global _pricing_last_attempt
    now = time.monotonic()
    if not force and _pricing_cache is not None and now - _pricing_cache[0] < _PRICING_TTL:
        return dict(_pricing_cache[1])
    if force and _pricing_cache is not None and now - _pricing_last_attempt < _PRICING_MIN_REFRESH:
        return dict(_pricing_cache[1])
    if _key() is None:
        return {}
    _pricing_last_attempt = now
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            listed = (
                await client.get(
                    f"{settings_base_url()}/v1/models",
                    params={"currency": "NOK"},
                )
            ).raise_for_status()
            entries = listed.json().get("data")
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as error:
        logger.warning(
            "Løynd did not return live model pricing: %s",
            _error_detail(error),
        )
        return dict(_pricing_cache[1]) if _pricing_cache is not None else {}
    parsed = _store_pricing_from_entries(entries)
    if parsed:
        return dict(parsed)
    return dict(_pricing_cache[1]) if _pricing_cache is not None else {}


async def cost_nok_for_calls(calls: Sequence[PaidCall]) -> Decimal:
    """Compute total live NOK cost (ex. VAT) across ``calls``."""
    paid = [
        call
        for call in calls
        if call.level.is_paid and (call.prompt_tokens > 0 or call.completion_tokens > 0)
    ]
    if not paid:
        return Decimal("0.000000")
    pricing = await current_pricing()
    if any(call.model_id and call.model_id not in pricing for call in paid):
        pricing = await current_pricing(force=True)
    total = Decimal("0.000000")
    for call in paid:
        rate = (
            (pricing.get(call.model_id) if call.model_id else None)
            or pricing.get(model_name(call.level))
            or _FALLBACK_PRICING[call.level]
        )
        total += rate.cost_nok(call.prompt_tokens, call.completion_tokens)
    return total.quantize(_MICRO_NOK, rounding=ROUND_HALF_UP)


async def current_models() -> dict[str, Served]:
    """Which concrete model answers at each :class:`Level` today, keyed by its value.

    From ``/v1/models`` (where a tier points) and ``/healthz`` (the checkpoint
    Løynd's own model runs), the way ``regnaskaps_tests/ai_cache.py`` keys its
    answers. Cached for an hour. Empty when Løynd cannot say, and not cached
    then: a level missing here is never a reason to read anything again
    (:func:`myntos.domain.analyse.freshness.verdict`), since not knowing is no
    reason to pay.
    """
    global _models_cache
    if _models_cache is not None and time.monotonic() - _models_cache[0] < _MODELS_TTL:
        return dict(_models_cache[1])
    if _key() is None:
        return {}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            listed = (
                await client.get(
                    f"{settings_base_url()}/v1/models",
                    params={"currency": "NOK"},
                )
            ).raise_for_status()
            health = (await client.get(f"{settings_base_url()}/healthz")).raise_for_status()
            entries = listed.json()["data"]
            checkpoint = health.json().get("model")
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as error:
        logger.error("Løynd did not say what it serves: %s", _error_detail(error), exc_info=error)
        return {}
    _store_pricing_from_entries(entries)
    by_id = {entry["id"]: entry for entry in entries if isinstance(entry, dict) and "id" in entry}
    own = model_name(Level.FREE)
    own = _resolved(by_id.get(own)) or own
    served: dict[str, Served] = {}
    for level in Level:
        name = model_name(level)
        resolved = _resolved(by_id.get(name)) or (name if name in by_id else None)
        if resolved is None:
            continue
        revision = checkpoint if resolved == own and isinstance(checkpoint, str) else None
        served[level.value] = Served(model=resolved, revision=revision)
    _models_cache = (time.monotonic(), served)
    return dict(served)


def _resolved(entry: object) -> str | None:
    """A ``/v1/models`` entry's concrete model: where a tier points, or its own id."""
    if not isinstance(entry, dict):
        return None
    if entry.get("kind") == "tier":
        target = entry.get("resolves_to")
        return target if isinstance(target, str) and target else None
    identifier = entry.get("id")
    return identifier if isinstance(identifier, str) else None
