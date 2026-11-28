"""The three KI levels: which model is asked, live NOK pricing, and falling back.

The promise the pages make is narrow and easy to break from either side: a
paid level answers while the company has a plan with weekly or period quota
left, «Gratis» answers otherwise and the answer says why, and the plan deducts
the live NOK value from Løynd's ``/v1/models?currency=NOK`` pricing while
exposing only remaining percentage (0 to 100 %) externally.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from myntos.domain.ki import service as ki_service
from myntos.integrations import loynd
from tests.conftest import insert_ki_period, write
from tests.test_cashflow_api import signed_in_company

LINE = "Rådgivning mars | 10 | timer | 1500 | 1"
FREE_MODEL = "loynd-lite"

Answer = Callable[[dict[str, Any]], httpx.Response]

DEFAULT_LOYND_NOK_MODELS: list[dict[str, Any]] = [
    {
        "id": FREE_MODEL,
        "pricing": {
            "currency": "NOK",
            "free": True,
            "input_per_million": 0,
            "output_per_million": 0,
        },
    },
    {
        "id": "medium",
        "pricing": {
            "currency": "NOK",
            "free": False,
            "input_per_million": 1.38,
            "output_per_million": 6.44,
        },
    },
    {
        "id": "high",
        "pricing": {
            "currency": "NOK",
            "free": False,
            "input_per_million": 8.625,
            "output_per_million": 50.6,
        },
    },
]


def _answer(
    content: str,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    headers: dict[str, str] | None = None,
) -> Answer:
    def respond(body: dict[str, Any]) -> httpx.Response:
        usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
        extra = headers or {}
        if body.get("stream"):
            frames = [
                f"data: {json.dumps({'choices': [{'delta': {'content': content}}]})}",
                f"data: {json.dumps({'choices': [], 'usage': usage})}",
                "data: [DONE]",
            ]
            return httpx.Response(
                200,
                text="\n\n".join(frames) + "\n\n",
                headers={"content-type": "text/event-stream", **extra},
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}], "usage": usage},
            headers=extra,
        )

    return respond


def _status(code: int) -> Answer:
    return lambda _body: httpx.Response(code, json={"error": {"message": "nei"}})


class Wire:
    """Løynd, answering per model name and ``GET /v1/models?currency=NOK``."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.answers: dict[str, Answer] = {}
        self.models_requests: list[httpx.Request] = []
        self.model_pricing: list[dict[str, Any]] = [
            {"id": row["id"], "pricing": dict(row["pricing"])} for row in DEFAULT_LOYND_NOK_MODELS
        ]

    def models(self) -> list[str]:
        return [body["model"] for body in self.sent]

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/v1/models"):
            self.models_requests.append(request)
            return httpx.Response(200, json={"data": self.model_pricing})
        body = json.loads(request.content)
        self.sent.append(body)
        answer = self.answers.get(body["model"])
        if answer is None:
            return httpx.Response(500)
        return answer(body)


def install_wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    """Install a mock Løynd transport and return its Wire handle."""
    loynd.reset_pricing_cache()
    fake = Wire()
    fake.answers[FREE_MODEL] = _answer(LINE, prompt_tokens=900, completion_tokens=40)
    real = httpx.AsyncClient

    def client(**options: Any) -> httpx.AsyncClient:
        options.setdefault("transport", httpx.MockTransport(fake.handle))
        return real(**options)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(loynd, "_key", lambda: "loynd_sk_test")
    return fake


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    """Løynd on a mock transport. Free answers one invoice line unless told otherwise.

    Only clients built without a transport of their own get it: the
    register's refusing client (conftest) keeps refusing.
    """
    return install_wire(monkeypatch)


# --- The model handle ---------------------------------------------------------------


async def test_a_paid_level_asks_its_own_model_with_room_to_think(wire: Wire) -> None:
    wire.answers["high"] = _answer("1", prompt_tokens=1000, completion_tokens=300)
    model = loynd.Model(loynd.Level.THINKING)

    assert await model.complete(system="s", prompt="p", max_tokens=16) == "1"

    assert wire.models() == ["high"]
    assert wire.sent[0]["max_tokens"] > 16
    assert wire.sent[0]["search"] is False
    assert model.usage.total == 1300
    assert not model.fell_back


async def test_the_free_level_is_counted_against_nothing(wire: Wire) -> None:
    model = loynd.Model()
    await model.complete(system="s", prompt="p")
    assert wire.models() == [FREE_MODEL]
    assert model.usage.total == 0


async def test_usage_adds_up_across_every_question_of_one_request(wire: Wire) -> None:
    wire.answers["medium"] = _answer("1", prompt_tokens=100, completion_tokens=10)
    model = loynd.Model(loynd.Level.MEDIUM)
    await model.complete(system="s", prompt="p")
    await model.complete(system="s", prompt="q")
    assert model.usage.total == 220


@pytest.mark.parametrize("code", [402, 403, 404, 429, 500, 503])
async def test_a_refused_paid_model_is_asked_again_at_the_free_level(wire: Wire, code: int) -> None:
    """Spent balance, a Norway-only key, a busy model, the extra capacity down."""
    wire.answers["medium"] = _status(code)
    model = loynd.Model(loynd.Level.MEDIUM)

    assert await model.complete(system="s", prompt="p") == LINE

    assert wire.models() == ["medium", FREE_MODEL]
    assert model.fell_back
    assert model.level is loynd.Level.FREE
    assert model.usage.total == 0


async def test_after_a_refusal_the_rest_of_the_request_stays_free(wire: Wire) -> None:
    """One request does not ask a refusing model twice."""
    wire.answers["medium"] = _status(402)
    model = loynd.Model(loynd.Level.MEDIUM)
    await model.complete(system="s", prompt="p")
    await model.complete(system="s", prompt="q")
    assert wire.models() == ["medium", FREE_MODEL, FREE_MODEL]


async def test_a_bad_request_is_not_a_refusal_to_route_around(wire: Wire) -> None:
    """A 400 is Myntos's mistake, and the free model would get the same request."""
    wire.answers["medium"] = _status(400)
    with pytest.raises(loynd.LoyndUnavailableError):
        await loynd.Model(loynd.Level.MEDIUM).complete(system="s", prompt="p")
    assert wire.models() == ["medium"]


async def test_the_free_level_falling_over_is_not_retried(wire: Wire) -> None:
    wire.answers[FREE_MODEL] = _status(503)
    with pytest.raises(loynd.LoyndUnavailableError):
        await loynd.Model().complete(system="s", prompt="p")
    assert wire.models() == [FREE_MODEL]


async def test_a_stream_refused_before_its_first_piece_falls_back_whole(wire: Wire) -> None:
    wire.answers["high"] = _status(402)
    model = loynd.Model(loynd.Level.THINKING)
    pieces = [piece async for piece in model.stream(system="s", prompt="p")]
    assert "".join(pieces) == LINE
    assert model.fell_back


async def test_a_streamed_paid_answer_is_counted(wire: Wire) -> None:
    wire.answers["medium"] = _answer("1", prompt_tokens=50, completion_tokens=5)
    model = loynd.Model(loynd.Level.MEDIUM)
    assert [piece async for piece in model.stream(system="s", prompt="p")] == ["1"]
    assert model.usage.total == 55


async def test_live_loynd_nok_pricing_is_fetched_and_thinking_costs_more_than_medium(
    wire: Wire,
) -> None:
    """Live NOK prices from /v1/models?currency=NOK weight Smart far above Medium."""
    wire.answers["medium"] = _answer("1", prompt_tokens=100_000, completion_tokens=50_000)
    wire.answers["high"] = _answer("1", prompt_tokens=100_000, completion_tokens=50_000)

    medium = loynd.Model(loynd.Level.MEDIUM)
    thinking = loynd.Model(loynd.Level.THINKING)
    await medium.complete(system="s", prompt="p")
    await thinking.complete(system="s", prompt="p")

    medium_cost = await medium.cost_nok()
    thinking_cost = await thinking.cost_nok()

    # Live NOK rates: medium 1.38/6.44 -> 0.460000 NOK; high 8.625/50.60 -> 3.392500 NOK.
    assert medium_cost == Decimal("0.460000")
    assert thinking_cost == Decimal("3.392500")
    assert thinking_cost > medium_cost * 7
    assert len(wire.models_requests) == 1
    assert wire.models_requests[0].url.params.get("currency") == "NOK"


async def test_unknown_loynd_model_header_refreshes_live_nok_pricing(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When X-Loynd-Model names a model not yet cached, live pricing refreshes and applies it."""
    # Warm cache with initial list (no mistral-large-latest).
    await loynd.current_pricing()
    assert len(wire.models_requests) == 1

    # Løynd swaps the backend model behind 'high' and publishes its NOK rate.
    wire.model_pricing.append(
        {
            "id": "mistral-large-latest",
            "pricing": {
                "currency": "NOK",
                "free": False,
                "input_per_million": 20.0,
                "output_per_million": 60.0,
            },
        }
    )
    wire.answers["high"] = _answer(
        "1",
        prompt_tokens=100_000,
        completion_tokens=10_000,
        headers={loynd.MODEL_HEADER: "mistral-large-latest"},
    )
    # Allow the rate-limited forced refresh to run in the same test second.
    monkeypatch.setattr(loynd, "_pricing_last_attempt", 0.0)

    model = loynd.Model(loynd.Level.THINKING)
    await model.complete(system="s", prompt="p")

    # 0.1 * 20.0 + 0.01 * 60.0 = 2.600000 NOK from the refreshed model entry.
    assert await model.cost_nok() == Decimal("2.600000")
    assert len(wire.models_requests) == 2


def test_remaining_percent_reaches_zero_only_when_exhausted() -> None:
    assert ki_service.remaining_percent(Decimal("9.00"), Decimal("0.000000")) == 100
    assert ki_service.remaining_percent(Decimal("9.00"), Decimal("4.500000")) == 50
    # Any positive remainder stays >= 1 % so UI 0 % always agrees with exhausted.
    assert ki_service.remaining_percent(Decimal("9.00"), Decimal("8.999000")) == 1
    assert ki_service.remaining_percent(Decimal("9.00"), Decimal("9.000000")) == 0
    assert ki_service.remaining_percent(Decimal("9.00"), Decimal("12.000000")) == 0
    assert ki_service.remaining_percent(Decimal("0.00"), Decimal("0.000000")) == 0


# --- Through a proposal route --------------------------------------------------------


def _lines(client: TestClient, company_id: int, level: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"text": "10 timer rådgivning à 1500"}
    if level is not None:
        payload["level"] = level
    response = write(
        client, "POST", f"/api/v1/companies/{company_id}/faktura/linjeforslag", json=payload
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _month(client: TestClient, company_id: int) -> dict[str, Any]:
    status: dict[str, Any] = client.get(f"/api/v1/companies/{company_id}/ki").json()
    return status


def test_without_a_plan_a_paid_level_is_answered_free_and_says_why(
    client: TestClient, wire: Wire
) -> None:
    company = signed_in_company(client)

    proposed = _lines(client, company["id"], "thinking")

    assert proposed["ki_level"] == "free"
    assert proposed["lines"][0]["description"] == "Rådgivning mars"
    assert proposed["warnings"][0] == ki_service.NO_PLAN.format(level="Smart")
    assert wire.models() == [FREE_MODEL]
    month = _month(client, company["id"])
    assert [(row["level"], row["name"], row["available"]) for row in month["levels"]] == [
        ("free", "Gratis", True),
        ("medium", "Medium", False),
        ("thinking", "Smart", False),
    ]


def test_with_a_plan_the_paid_level_answers_and_the_month_pays_for_it(
    client: TestClient, wire: Wire
) -> None:
    # medium NOK rate: 1.38/M in, 6.44/M out -> 1M in + 100k out = 2.024 NOK.
    wire.answers["medium"] = _answer(LINE, prompt_tokens=1_000_000, completion_tokens=100_000)
    company = signed_in_company(client)
    insert_ki_period(
        client,
        company["id"],
        value_allowance_nok=Decimal("10.00"),
        value_used_nok=Decimal("5.000000"),
    )

    proposed = _lines(client, company["id"], "medium")

    assert proposed["ki_level"] == "medium"
    assert proposed["warnings"] == []
    assert wire.models() == ["medium"]
    current = _month(client, company["id"])["current"]
    # 5.00 + 2.024 = 7.024 used of 10.00 -> 2.976 left -> 30 % remaining, 70 % used.
    assert current["remaining_percent"] == 30
    assert current["used_percent"] == 70
    assert "tokens_used" not in current
    assert "tokens_allowance" not in current
    assert "tokens_left" not in current


def test_a_month_with_no_quota_left_answers_free_and_charges_nothing(
    client: TestClient, wire: Wire
) -> None:
    company = signed_in_company(client)
    insert_ki_period(
        client,
        company["id"],
        value_allowance_nok=Decimal("10.00"),
        value_used_nok=Decimal("10.000000"),
    )

    proposed = _lines(client, company["id"], "medium")

    assert proposed["ki_level"] == "free"
    assert proposed["warnings"][0] == ki_service.SPENT
    month = _month(client, company["id"])
    assert month["current"]["remaining_percent"] == 0
    assert month["current"]["used_percent"] == 100
    assert [level["available"] for level in month["levels"]] == [True, False, False]


def test_a_refused_paid_model_answers_free_and_charges_nothing(
    client: TestClient, wire: Wire
) -> None:
    wire.answers["medium"] = _status(503)
    company = signed_in_company(client)
    insert_ki_period(client, company["id"])

    proposed = _lines(client, company["id"], "medium")

    assert proposed["ki_level"] == "free"
    assert proposed["lines"]
    assert proposed["warnings"][0] == ki_service.REFUSED
    assert _month(client, company["id"])["current"]["remaining_percent"] == 100
    assert _month(client, company["id"])["current"]["used_percent"] == 0


def test_the_free_level_needs_no_plan_and_uses_none(client: TestClient, wire: Wire) -> None:
    company = signed_in_company(client)
    insert_ki_period(client, company["id"])

    proposed = _lines(client, company["id"])

    assert proposed["ki_level"] == "free"
    assert proposed["warnings"] == []
    assert _month(client, company["id"])["current"]["remaining_percent"] == 100
    assert _month(client, company["id"])["current"]["used_percent"] == 0


def test_another_companys_plan_is_not_this_ones(client: TestClient, wire: Wire) -> None:
    """A plan is the company's, not the account's: the sibling's month buys nothing here."""
    from tests.test_cashflow_api import create_company

    first = signed_in_company(client)
    second = create_company(client, first["organization_id"], orgnr="923609016", name="Søster AS")
    insert_ki_period(client, first["id"])

    proposed = _lines(client, second["id"], "medium")

    assert proposed["ki_level"] == "free"
    assert proposed["warnings"][0] == ki_service.NO_PLAN.format(level="Medium")
