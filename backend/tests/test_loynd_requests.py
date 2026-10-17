"""What Myntos actually sends Løynd, read off the wire.

Two promises live in the request body rather than in any code that reads the
answer, so they are tested there: no question about a bilag may be turned into
a web search, and a follow-up repeats the earlier conversation exactly.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from myntos.integrations import loynd


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every chat request body, answered with "1" in whichever shape was asked for."""
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if body.get("stream"):
            return httpx.Response(
                200,
                text='data: {"choices":[{"delta":{"content":"1"}}]}\n\ndata: [DONE]\n\n',
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "1"}}]})

    real = httpx.AsyncClient

    def client(**options: Any) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(handler), **options)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(loynd, "_key", lambda: "loynd_sk_test")
    return bodies


async def test_no_question_about_a_bilag_may_search_the_web(sent: list[dict[str, Any]]) -> None:
    """Løynd searches by default for a caller with a key; every request here says no.

    A seller's name turned into a search query has left the EEA, and the routing
    pass that decides whether to search costs a generation before every answer.
    """
    await loynd.complete(system="s", prompt="p")
    async for _ in loynd.stream(system="s", prompt="p"):
        pass
    await loynd.ask_about_image(b"\x89PNG", content_type="image/png", question="Hvilken app?")

    assert len(sent) == 3
    assert all(body["search"] is False for body in sent)


async def test_a_follow_up_repeats_the_conversation_and_adds_one_turn(
    sent: list[dict[str, Any]],
) -> None:
    await loynd.complete(
        system="Du er en regnskapsassistent.",
        prompt="Brukeren har svart. Svar på nytt.",
        history=[("Her er bilaget …", "DATO: 17.10.2026")],
    )
    assert sent[0]["messages"] == [
        {"role": "system", "content": "Du er en regnskapsassistent."},
        {"role": "user", "content": "Her er bilaget …"},
        {"role": "assistant", "content": "DATO: 17.10.2026"},
        {"role": "user", "content": "Brukeren har svart. Svar på nytt."},
    ]


async def test_a_first_question_is_two_messages_as_it_always_was(
    sent: list[dict[str, Any]],
) -> None:
    await loynd.complete(system="s", prompt="p")
    assert sent[0]["messages"] == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "p"},
    ]
