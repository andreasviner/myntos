"""Buying a month of a KI plan: the Mollie client, and the routes that use it.

What has to hold is mostly about money and trust. Only an owner of the company
may start a payment; the amount Mollie is asked for is the plan's price with
VAT, to the øre; a month opens only when Mollie itself (asked with Myntos's
own key) says the payment for exactly that purchase is paid, and then exactly
once; a month bought while one is running waits for it. The webhook is the
one route anybody on the internet can call, so it is tested as such.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from myntos.core.clock import utc_now
from myntos.core.config import get_settings
from myntos.domain.ki import service as ki_service
from myntos.integrations import loynd, mollie
from myntos.integrations.mollie import MolliePayment
from tests.conftest import insert_ki_period, join_company, write
from tests.test_cashflow_api import create_company, signed_in_company

WEBHOOK = "/api/v1/webhooks/mollie"
TEST_KEY = "test_dHar8ylfXAzEVi3nk3PoqrMEEStSa2"


def _configure(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Settings changed for one test; the cache is cleared on the way in and out."""
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


# --- The Mollie client ----------------------------------------------------------------


def test_nothing_is_on_sale_without_a_key_for_the_mode_in_use(
    settings_env: pytest.MonkeyPatch,
) -> None:
    assert mollie.active_mode() is None
    _configure(settings_env, MOLLIE_TEST_KEY="   ")
    assert mollie.active_mode() is None
    _configure(settings_env, MOLLIE_TEST_KEY=TEST_KEY)
    assert mollie.active_mode() == "test"


def test_live_needs_a_live_key_and_off_is_off(settings_env: pytest.MonkeyPatch) -> None:
    """A test key never takes real money because somebody wrote «live» next to it."""
    _configure(settings_env, MOLLIE_TEST_KEY=TEST_KEY, MOLLIE_MODE="live")
    assert mollie.active_mode() is None
    _configure(settings_env, MOLLIE_LIVE_KEY="live_abc")
    assert mollie.active_mode() == "live"
    _configure(settings_env, MOLLIE_MODE="off")
    assert mollie.active_mode() is None


@pytest.mark.parametrize(
    ("url", "public"),
    [
        ("https://api.myntos.no/api/v1/webhooks/mollie", True),
        ("http://api.myntos.no/api/v1/webhooks/mollie", False),
        ("https://localhost/api/v1/webhooks/mollie", False),
        ("https://127.0.0.1:8001/api/v1/webhooks/mollie", False),
        ("https://myntos.local/api/v1/webhooks/mollie", False),
        ("", False),
        (None, False),
    ],
)
def test_a_webhook_is_only_offered_where_mollie_could_reach_it(
    url: str | None, public: bool
) -> None:
    assert mollie.is_public_https(url) is public


class MollieWire:
    """Mollie's API on a mock transport, and every request it was sent."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.answer: Callable[[httpx.Request], httpx.Response] = self._payment

    @staticmethod
    def _payment(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content) if request.content else {}
        return httpx.Response(
            201,
            json={
                "resource": "payment",
                "id": "tr_WDqYK6vllg",
                "mode": "test",
                "status": "open",
                "amount": sent.get("amount", {"value": "186.25", "currency": "NOK"}),
                "metadata": sent.get("metadata"),
                "_links": {
                    "checkout": {"href": "https://www.mollie.com/checkout/select-method/7UhSN1"}
                },
            },
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answer(request)


@pytest.fixture
def mollie_wire(settings_env: pytest.MonkeyPatch) -> MollieWire:
    wire = MollieWire()
    real = httpx.AsyncClient

    def client(**options: Any) -> httpx.AsyncClient:
        options.setdefault("transport", httpx.MockTransport(wire.handle))
        return real(**options)

    settings_env.setattr(httpx, "AsyncClient", client)
    _configure(settings_env, MOLLIE_TEST_KEY=TEST_KEY)
    return wire


async def _create() -> MolliePayment:
    return await mollie.create_payment(
        amount=Decimal("186.25"),
        description="Myntos KI Pluss, en måned",
        redirect_url="https://myntos.no/ki/?betaling=abc",
        metadata={"reference": "abc", "plan": "pluss"},
    )


async def test_a_payment_is_asked_for_to_the_ore_with_the_webhook(
    mollie_wire: MollieWire,
) -> None:
    payment = await _create()

    [request] = mollie_wire.requests
    assert request.method == "POST"
    assert str(request.url) == "https://api.mollie.com/v2/payments"
    assert request.headers["Authorization"] == f"Bearer {TEST_KEY}"
    # No "method": Mollie offers every way of paying switched on in its
    # dashboard, which is why no page promises a card.
    assert json.loads(request.content) == {
        "amount": {"currency": "NOK", "value": "186.25"},
        "description": "Myntos KI Pluss, en måned",
        "redirectUrl": "https://myntos.no/ki/?betaling=abc",
        "metadata": {"reference": "abc", "plan": "pluss"},
        "locale": "nb_NO",
        "webhookUrl": "https://api.myntos.no/api/v1/webhooks/mollie",
    }
    assert payment.id == "tr_WDqYK6vllg"
    assert payment.amount == Decimal("186.25")
    assert payment.checkout_url == "https://www.mollie.com/checkout/select-method/7UhSN1"


@pytest.mark.parametrize("webhook", ["", "http://127.0.0.1:8001/api/v1/webhooks/mollie"])
async def test_a_copy_mollie_cannot_reach_asks_for_no_webhook(
    mollie_wire: MollieWire, settings_env: pytest.MonkeyPatch, webhook: str
) -> None:
    """Mollie refuses a payment whose webhook it cannot reach; the browser's return settles it."""
    _configure(settings_env, MOLLIE_WEBHOOK_URL=webhook)
    await _create()
    assert "webhookUrl" not in json.loads(mollie_wire.requests[0].content)


async def test_live_mode_pays_with_the_live_key(
    mollie_wire: MollieWire, settings_env: pytest.MonkeyPatch
) -> None:
    _configure(settings_env, MOLLIE_MODE="live", MOLLIE_LIVE_KEY="live_abc")
    await _create()
    assert mollie_wire.requests[0].headers["Authorization"] == "Bearer live_abc"


async def test_without_a_key_mollie_is_never_asked(
    mollie_wire: MollieWire, settings_env: pytest.MonkeyPatch
) -> None:
    _configure(settings_env, MOLLIE_TEST_KEY="")
    with pytest.raises(mollie.MollieNotConfiguredError):
        await _create()
    assert mollie_wire.requests == []


async def test_an_id_that_is_not_mollies_never_reaches_a_url(mollie_wire: MollieWire) -> None:
    for forged in ("../customers", "tr_abc/../../x", "tr_", ""):
        with pytest.raises(mollie.MolliePaymentNotFoundError):
            await mollie.get_payment(forged)
    assert mollie_wire.requests == []


async def test_an_unknown_payment_is_not_found(mollie_wire: MollieWire) -> None:
    mollie_wire.answer = lambda _request: httpx.Response(404, json={"status": 404})
    with pytest.raises(mollie.MolliePaymentNotFoundError):
        await mollie.get_payment("tr_WDqYK6vllg")
    assert str(mollie_wire.requests[0].url).endswith("/v2/payments/tr_WDqYK6vllg")


async def test_a_refusal_is_logged_without_the_key_or_the_body(
    mollie_wire: MollieWire, caplog: pytest.LogCaptureFixture
) -> None:
    mollie_wire.answer = lambda _request: httpx.Response(
        401, json={"detail": f"Invalid API key: {TEST_KEY}"}
    )
    with caplog.at_level(logging.WARNING), pytest.raises(mollie.MollieError) as raised:
        await mollie.get_payment("tr_WDqYK6vllg")
    assert not isinstance(raised.value, mollie.MolliePaymentNotFoundError)
    assert TEST_KEY not in caplog.text
    assert TEST_KEY not in str(raised.value)


async def test_mollie_out_of_reach_is_a_mollie_error(mollie_wire: MollieWire) -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nede", request=request)

    mollie_wire.answer = unreachable
    with pytest.raises(mollie.MollieError):
        await mollie.get_payment("tr_WDqYK6vllg")


async def test_an_answer_with_a_strange_id_is_not_believed(mollie_wire: MollieWire) -> None:
    mollie_wire.answer = lambda _request: httpx.Response(
        200, json={"id": "tr_../x", "status": "paid", "amount": {"value": "1.00"}}
    )
    with pytest.raises(mollie.MollieError):
        await mollie.get_payment("tr_WDqYK6vllg")


# --- The routes -----------------------------------------------------------------------


class FakeMollie:
    """Mollie as the routes see it: payments it created, and what it says about them."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.payments: dict[str, MolliePayment] = {}
        self.asked: list[str] = []
        self.down = False

    async def create_payment(
        self,
        *,
        amount: Decimal,
        description: str,
        redirect_url: str,
        metadata: dict[str, str],
        currency: str = "NOK",
    ) -> MolliePayment:
        if self.down:
            raise mollie.MollieError("fikk ikke kontakt med Mollie")
        self.created.append(
            {
                "amount": amount,
                "description": description,
                "redirect_url": redirect_url,
                "metadata": metadata,
            }
        )
        payment_id = f"tr_test{len(self.created)}"
        self.payments[payment_id] = MolliePayment(
            id=payment_id,
            mode="test",
            status="open",
            amount=amount,
            currency=currency,
            checkout_url=f"https://www.mollie.com/checkout/{payment_id}",
            metadata=dict(metadata),
        )
        return self.payments[payment_id]

    def says(self, payment_id: str, **changes: Any) -> None:
        self.payments[payment_id] = dataclasses.replace(self.payments[payment_id], **changes)

    async def get_payment(self, payment_id: str) -> MolliePayment:
        self.asked.append(payment_id)
        if self.down:
            raise mollie.MollieError("fikk ikke kontakt med Mollie")
        if payment_id not in self.payments:
            raise mollie.MolliePaymentNotFoundError("Mollie kjenner ikke betalingen")
        return self.payments[payment_id]


@pytest.fixture
def on_sale(settings_env: pytest.MonkeyPatch) -> FakeMollie:
    """A deployment that sells plans: a Mollie test key, a Løynd key, and a fake Mollie."""
    fake = FakeMollie()
    _configure(settings_env, MOLLIE_TEST_KEY=TEST_KEY)
    settings_env.setattr(loynd, "_key", lambda: "loynd_sk_test")
    settings_env.setattr(mollie, "create_payment", fake.create_payment)
    settings_env.setattr(mollie, "get_payment", fake.get_payment)
    return fake


def _buy(client: TestClient, company_id: int, plan: str = "pluss") -> Any:
    return write(client, "POST", f"/api/v1/companies/{company_id}/ki/betaling", json={"plan": plan})


def _bought(client: TestClient, company_id: int, plan: str = "pluss") -> str:
    response = _buy(client, company_id, plan)
    assert response.status_code == 201, response.text
    reference: str = response.json()["reference"]
    return reference


def _purchase(client: TestClient, company_id: int, reference: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/companies/{company_id}/ki/betaling/{reference}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _ki(client: TestClient, company_id: int) -> dict[str, Any]:
    response = client.get(f"/api/v1/companies/{company_id}/ki")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _moment(value: str) -> datetime:
    return datetime.fromisoformat(value)


def test_the_plans_and_levels_are_listed_with_their_prices(client: TestClient) -> None:
    signed_in_company(client)
    body = client.get("/api/v1/ki/planer").json()
    assert [plan["code"] for plan in body["plans"]] == ["start", "pluss", "pro"]
    assert [plan["price_incl_vat"] for plan in body["plans"]] == ["61.25", "186.25", "498.75"]
    assert [plan["quota_label"] for plan in body["plans"]] == [
        "Standard ukekvote",
        "3× ukekvote",  # noqa: RUF001
        "8× ukekvote",  # noqa: RUF001
    ]
    assert all("tokens" not in plan and "weekly_tokens" not in plan for plan in body["plans"])
    assert body["currency"] == "NOK"
    assert body["levels"] == ["free", "medium", "thinking"]


def test_without_a_payment_provider_nothing_is_sold(
    client: TestClient, settings_env: pytest.MonkeyPatch
) -> None:
    settings_env.setattr(loynd, "_key", lambda: "loynd_sk_test")
    company = signed_in_company(client)

    status = _ki(client, company["id"])
    assert status["plans_on_sale"] is False
    assert status["can_buy"] is False

    refused = _buy(client, company["id"])
    assert refused.status_code == 503
    assert refused.json()["code"] == "ki_purchase_not_available"


def test_without_the_paid_models_nothing_is_sold(
    client: TestClient, on_sale: FakeMollie, settings_env: pytest.MonkeyPatch
) -> None:
    settings_env.setattr(loynd, "_key", lambda: None)
    company = signed_in_company(client)

    assert _ki(client, company["id"])["plans_on_sale"] is False
    assert _buy(client, company["id"]).status_code == 503
    assert on_sale.created == []


def test_the_owner_is_sent_to_mollie_for_the_price_with_vat(
    client: TestClient, on_sale: FakeMollie
) -> None:
    company = signed_in_company(client)
    assert _ki(client, company["id"])["can_buy"] is True

    response = _buy(client, company["id"])

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["checkout_url"] == "https://www.mollie.com/checkout/tr_test1"
    [sent] = on_sale.created
    assert sent["amount"] == Decimal("186.25")
    assert sent["redirect_url"] == f"https://app.example/ki/about/?betaling={body['reference']}"
    assert sent["metadata"] == {"reference": body["reference"], "plan": "pluss"}
    # The bank statement and Mollie's page name the plan, not the company.
    assert sent["description"] == "Myntos KI Pluss, en måned"
    # Nothing is the company's until Mollie says it is paid.
    status = _ki(client, company["id"])
    assert status["current"] is None
    assert status["upcoming"] == []


@pytest.mark.parametrize("role", ["admin", "member"])
def test_only_an_owner_may_buy(client: TestClient, on_sale: FakeMollie, role: str) -> None:
    company = signed_in_company(client)
    join_company(client, company["id"], email="ola@example.no", role=role)

    status = _ki(client, company["id"])
    assert status["plans_on_sale"] is True
    assert status["can_buy"] is False

    assert _buy(client, company["id"]).status_code == 403
    assert on_sale.created == []


def test_a_company_with_ki_off_buys_nothing(client: TestClient, on_sale: FakeMollie) -> None:
    """Nothing could spend the tokens, and the month would run out regardless."""
    company = signed_in_company(client)
    write(client, "PATCH", f"/api/v1/companies/{company['id']}", json={"ki_enabled": False})

    refused = _buy(client, company["id"])

    assert refused.status_code == 409
    assert refused.json() == {
        "detail": "Slå på KI for selskapet før du kjøper en plan.",
        "code": "ki_turned_off",
    }
    assert on_sale.created == []


def test_an_unknown_plan_is_not_sold(client: TestClient, on_sale: FakeMollie) -> None:
    company = signed_in_company(client)
    refused = _buy(client, company["id"], "gull")
    assert refused.status_code == 404
    assert refused.json()["code"] == "ki_plan_not_found"
    assert on_sale.created == []


def test_mollie_not_answering_leaves_nothing_to_pay(
    client: TestClient, on_sale: FakeMollie
) -> None:
    on_sale.down = True
    company = signed_in_company(client)

    refused = _buy(client, company["id"])

    assert refused.status_code == 502
    assert refused.json()["detail"] == "Betalingen kunne ikke startes. Prøv igjen om litt."
    status = _ki(client, company["id"])
    assert status["current"] is None
    assert status["upcoming"] == []


def test_coming_back_paid_opens_the_month_from_now(client: TestClient, on_sale: FakeMollie) -> None:
    company = signed_in_company(client)
    reference = _bought(client, company["id"])
    on_sale.says("tr_test1", status="paid")
    before = utc_now()

    period = _purchase(client, company["id"], reference)

    assert period["status"] == "paid"
    assert period["test"] is True
    assert period["remaining_percent"] == 100
    assert period["used_percent"] == 0
    assert "tokens_allowance" not in period
    assert period["amount"] == "186.25"
    start, end = _moment(period["starts_at"]), _moment(period["ends_at"])
    assert abs(start - before) < timedelta(minutes=1)
    assert end == ki_service.add_month(start)
    status = _ki(client, company["id"])
    assert status["current"]["reference"] == reference
    assert [level["available"] for level in status["levels"]] == [True, True, True]


def test_a_month_bought_while_one_runs_starts_when_it_ends(
    client: TestClient, on_sale: FakeMollie
) -> None:
    company = signed_in_company(client)
    insert_ki_period(client, company["id"], days_left=20)
    running = _ki(client, company["id"])["current"]
    reference = _bought(client, company["id"], "start")
    on_sale.says("tr_test1", status="paid")

    period = _purchase(client, company["id"], reference)

    assert _moment(period["starts_at"]) == _moment(running["ends_at"])
    status = _ki(client, company["id"])
    assert status["current"]["reference"] == running["reference"]
    assert [month["reference"] for month in status["upcoming"]] == [reference]


def test_a_paid_month_is_opened_once_however_often_it_is_reported(
    client: TestClient, on_sale: FakeMollie
) -> None:
    company = signed_in_company(client)
    reference = _bought(client, company["id"])
    on_sale.says("tr_test1", status="paid")

    first = _purchase(client, company["id"], reference)
    assert client.post(WEBHOOK, data={"id": "tr_test1"}).status_code == 200
    again = _purchase(client, company["id"], reference)

    assert again["starts_at"] == first["starts_at"]
    assert again["ends_at"] == first["ends_at"]
    assert on_sale.asked == ["tr_test1"]
    assert _ki(client, company["id"])["upcoming"] == []


def test_an_open_payment_is_asked_about_again(client: TestClient, on_sale: FakeMollie) -> None:
    company = signed_in_company(client)
    reference = _bought(client, company["id"])

    assert _purchase(client, company["id"], reference)["status"] == "open"
    assert _purchase(client, company["id"], reference)["status"] == "open"
    assert on_sale.asked == ["tr_test1", "tr_test1"]


@pytest.mark.parametrize("outcome", ["canceled", "expired", "failed"])
def test_a_payment_that_did_not_go_through_opens_nothing(
    client: TestClient, on_sale: FakeMollie, outcome: str
) -> None:
    company = signed_in_company(client)
    reference = _bought(client, company["id"])
    on_sale.says("tr_test1", status=outcome)

    period = _purchase(client, company["id"], reference)

    assert period["status"] == outcome
    assert period["starts_at"] is None
    assert _ki(client, company["id"])["current"] is None
    # Final: not asked about again.
    _purchase(client, company["id"], reference)
    assert on_sale.asked == ["tr_test1"]


@pytest.mark.parametrize(
    "change",
    [
        {"amount": Decimal("1.00")},
        {"currency": "EUR"},
        {"metadata": {"reference": "en-annen", "plan": "pluss"}},
        {"id": "tr_another1"},
    ],
    ids=["amount", "currency", "reference", "id"],
)
def test_a_paid_payment_that_is_not_this_purchase_opens_nothing(
    client: TestClient, on_sale: FakeMollie, change: dict[str, Any]
) -> None:
    company = signed_in_company(client)
    reference = _bought(client, company["id"])
    on_sale.says("tr_test1", status="paid", **change)

    response = client.get(f"/api/v1/companies/{company['id']}/ki/betaling/{reference}")

    assert response.status_code == 502
    assert response.json()["detail"] == "Betalingen stemmer ikke med kjøpet."
    assert _ki(client, company["id"])["current"] is None


def test_a_purchase_is_found_only_under_its_own_company(
    client: TestClient, on_sale: FakeMollie
) -> None:
    first = signed_in_company(client)
    second = create_company(client, first["organization_id"], orgnr="923609016", name="Søster AS")
    reference = _bought(client, first["id"])
    on_sale.says("tr_test1", status="paid")

    response = client.get(f"/api/v1/companies/{second['id']}/ki/betaling/{reference}")

    assert response.status_code == 404
    assert response.json()["code"] == "ki_payment_not_found"
    assert on_sale.asked == []
    assert _ki(client, second["id"])["current"] is None


# --- The webhook ----------------------------------------------------------------------


def test_the_webhook_opens_the_month_without_the_browser(
    client: TestClient, on_sale: FakeMollie
) -> None:
    company = signed_in_company(client)
    reference = _bought(client, company["id"])
    on_sale.says("tr_test1", status="paid")

    assert client.post(WEBHOOK, data={"id": "tr_test1"}).status_code == 200

    assert _ki(client, company["id"])["current"]["reference"] == reference


@pytest.mark.parametrize(
    "body", [{"id": "../companies"}, {"id": "tr_unknown1"}, {}], ids=["forged", "unknown", "none"]
)
def test_the_webhook_answers_200_to_what_is_not_a_payment_of_ours(
    client: TestClient, on_sale: FakeMollie, body: dict[str, str]
) -> None:
    """So Mollie stops retrying, and so a stranger learns nothing from the status."""
    company = signed_in_company(client)
    _bought(client, company["id"])

    assert client.post(WEBHOOK, data=body).status_code == 200
    assert on_sale.asked == []


def test_the_webhook_asks_mollie_to_retry_when_mollie_cannot_be_reached(
    client: TestClient, on_sale: FakeMollie
) -> None:
    company = signed_in_company(client)
    reference = _bought(client, company["id"])
    on_sale.says("tr_test1", status="paid")
    on_sale.down = True

    assert client.post(WEBHOOK, data={"id": "tr_test1"}).status_code == 503
    assert _ki(client, company["id"])["current"] is None

    on_sale.down = False
    assert client.post(WEBHOOK, data={"id": "tr_test1"}).status_code == 200
    assert _ki(client, company["id"])["current"]["reference"] == reference


def test_the_webhook_does_not_ask_for_retries_that_cannot_help(
    client: TestClient, on_sale: FakeMollie
) -> None:
    """A payment that does not match its purchase will not match on the next try."""
    company = signed_in_company(client)
    _bought(client, company["id"])
    on_sale.says("tr_test1", status="paid", amount=Decimal("1.00"))

    assert client.post(WEBHOOK, data={"id": "tr_test1"}).status_code == 200
    assert _ki(client, company["id"])["current"] is None


def test_the_webhook_asks_nothing_where_plans_are_no_longer_sold(
    client: TestClient, on_sale: FakeMollie, settings_env: pytest.MonkeyPatch
) -> None:
    company = signed_in_company(client)
    _bought(client, company["id"])
    _configure(settings_env, MOLLIE_TEST_KEY="")

    assert client.post(WEBHOOK, data={"id": "tr_test1"}).status_code == 200
    assert on_sale.asked == []
