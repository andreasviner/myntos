"""KI chat over a company's books: threads, snapshot, voucher lookup, streaming, and limits."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from myntos.api.v1.saft_innlesing import get_import_sessions
from myntos.core.config import get_settings
from myntos.domain.ki.models import KiChatMessage, KiWeeklyUsage
from myntos.integrations import loynd
from tests.conftest import register, write
from tests.test_cashflow_api import create_company
from tests.test_ki_levels import FREE_MODEL, Wire, _answer, install_wire


@pytest.fixture
def loynd_wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    return install_wire(monkeypatch)


def _parse_sse(raw: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        name = ""
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data_lines.append(line[len("data: ") :])
        if name and data_lines:
            events.append((name, json.loads("\n".join(data_lines))))
    return events


def test_chat_thread_lifecycle_books_snapshot_and_voucher_lookup(
    client: TestClient, loynd_wire: Wire
) -> None:
    loynd_wire.answers[FREE_MODEL] = _answer(
        "Du har betalt 12 500,00 kr til Telenor på konto 6900.",
        prompt_tokens=1500,
        completion_tokens=40,
    )
    session = register(client)
    org_id = session["memberships"][0]["organization_id"]
    company = create_company(client, org_id, orgnr="910000004", name="Fjord Konsult AS")

    # Book a distinct voucher so we can verify both the books snapshot and targeted lookup.
    posted = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/cashflow",
        json={
            "type_code": "varekjop",
            "occurred_on": "2026-04-15",
            "gross_amount": "12500.00",
            "description": "Telenor fiberabonnement kontor",
        },
    )
    assert posted.status_code == 201, posted.text

    # Create a thread.
    created = write(client, "POST", f"/api/v1/companies/{company['id']}/ki/samtaler", json={})
    assert created.status_code == 201, created.text
    thread_id = created.json()["id"]
    assert created.json()["title"] == "Ny samtale"

    # Ask about Telenor -> system prompt must include books snapshot + matching voucher.
    turn1 = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/ki/samtaler/{thread_id}/meldinger",
        json={"content": "Hvor mye har vi brukt på Telenor i april?", "level": "free"},
    )
    assert turn1.status_code == 200, turn1.text
    body = turn1.json()
    assert body["ki_level"] == "free"
    assert body["notices"] == []
    assert body["thread"]["title"] == "Hvor mye har vi brukt på Telenor i april?"
    assert "Telenor" in body["assistant_message"]["content"]

    system_msg = loynd_wire.sent[0]["messages"][0]["content"]
    assert "Fjord Konsult AS" in system_msg
    assert "Telenor fiberabonnement kontor" in system_msg
    assert "<regnskapsdata>" in system_msg
    assert "<relevante_bilag>" in system_msg

    # Follow-up question carries prior conversation history.
    turn2 = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/ki/samtaler/{thread_id}/meldinger",
        json={"content": "Hvilken konto ble det ført på?", "level": "free"},
    )
    assert turn2.status_code == 200, turn2.text
    second_messages = loynd_wire.sent[1]["messages"]
    roles = [m["role"] for m in second_messages]
    assert roles == ["system", "user", "assistant", "user"]
    assert second_messages[1]["content"] == "Hvor mye har vi brukt på Telenor i april?"

    # List, read back, and delete thread.
    listed = client.get(f"/api/v1/companies/{company['id']}/ki/samtaler")
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["message_count"] == 4

    detail = client.get(f"/api/v1/companies/{company['id']}/ki/samtaler/{thread_id}")
    assert detail.status_code == 200
    assert len(detail.json()["messages"]) == 4

    deleted = write(client, "DELETE", f"/api/v1/companies/{company['id']}/ki/samtaler/{thread_id}")
    assert deleted.status_code == 204
    gone = client.get(f"/api/v1/companies/{company['id']}/ki/samtaler/{thread_id}")
    assert gone.status_code == 404


def test_chat_streaming_emits_sse_frames_and_counts_weekly_quota(
    client: TestClient, loynd_wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    # high NOK rate: 8.625/M in, 50.60/M out -> 200k in + 50k out = 4.255000 NOK.
    loynd_wire.answers["high"] = _answer(
        "Resultatet er positivt.", prompt_tokens=200_000, completion_tokens=50_000
    )
    session = register(client)
    org_id = session["memberships"][0]["organization_id"]
    seller = create_company(client, org_id, orgnr="915933149", name="Lindeman Technologies AS")
    write(
        client,
        "PATCH",
        f"/api/v1/companies/{seller['id']}",
        json={
            "in_foretaksregisteret": True,
            "address_line": "Storgata 1",
            "postal_code": "0155",
            "city": "Oslo",
        },
    )
    write(
        client,
        "POST",
        f"/api/v1/companies/{seller['id']}/bankkontoer",
        json={"account": "15062033158", "label": "Driftskonto"},
    )
    buyer = create_company(
        client, seller["organization_id"], orgnr="923609016", name="Chat Kunde AS"
    )

    monkeypatch.setenv("KI_BILLING_COMPANY_ID", str(seller["id"]))
    monkeypatch.setattr(loynd, "_key", lambda: "loynd_sk_test")
    get_settings.cache_clear()
    try:
        sub = write(
            client, "PUT", f"/api/v1/companies/{buyer['id']}/ki/abonnement", json={"plan": "start"}
        )
        assert sub.status_code == 200, sub.text

        thread_id = write(
            client, "POST", f"/api/v1/companies/{buyer['id']}/ki/samtaler", json={}
        ).json()["id"]

        streamed = write(
            client,
            "POST",
            f"/api/v1/companies/{buyer['id']}/ki/samtaler/{thread_id}/meldinger/strom",
            json={"content": "Hvordan går det?", "level": "thinking"},
        )
        assert streamed.status_code == 200, streamed.text
        assert streamed.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(streamed.text)
        names = [name for name, _ in events]
        assert names[0] == "start"
        assert "delta" in names
        assert names[-1] == "ferdig"
        done_payload = events[-1][1]
        assert done_payload["ki_level"] == "thinking"
        assert done_payload["assistant_message"]["content"] == "Resultatet er positivt."
        assert "tokens_used" not in done_payload["assistant_message"]

        # Weekly quota reflects live NOK cost (4.255 / 9.00 -> 53 % left, 47 % used).
        ki = client.get(f"/api/v1/companies/{buyer['id']}/ki").json()
        assert ki["weekly"]["remaining_percent"] == 53
        assert ki["weekly"]["used_percent"] == 47
        assert "tokens_used" not in ki["weekly"]

        # Internal NOK cost audit stored on both weekly usage and chat message.
        sessions = client.app.dependency_overrides[get_import_sessions]()  # type: ignore[attr-defined]

        async def read_costs() -> tuple[Decimal, Decimal]:
            async with sessions() as db:
                weekly = await db.scalar(
                    select(KiWeeklyUsage).where(KiWeeklyUsage.company_id == buyer["id"])
                )
                msg = await db.scalar(
                    select(KiChatMessage).where(
                        KiChatMessage.thread_id == thread_id,
                        KiChatMessage.role == "assistant",
                    )
                )
                assert weekly is not None and msg is not None
                return Decimal(str(weekly.value_used_nok)), Decimal(str(msg.cost_nok))

        assert client.portal is not None
        weekly_nok, msg_nok = client.portal.call(read_costs)
        assert weekly_nok == Decimal("4.255000")
        assert msg_nok == Decimal("4.255000")
    finally:
        get_settings.cache_clear()


def test_chat_refuses_when_ki_turned_off_and_isolates_companies(
    client: TestClient, loynd_wire: Wire
) -> None:
    session = register(client)
    org_id = session["memberships"][0]["organization_id"]
    first = create_company(client, org_id, orgnr="910000004", name="Selskap En AS")
    second = create_company(
        client, first["organization_id"], orgnr="910000012", name="Selskap To AS"
    )

    thread_id = write(
        client, "POST", f"/api/v1/companies/{first['id']}/ki/samtaler", json={}
    ).json()["id"]

    # Cross-company read/send/delete all answer 404.
    other_read = client.get(f"/api/v1/companies/{second['id']}/ki/samtaler/{thread_id}")
    assert other_read.status_code == 404
    other_send = write(
        client,
        "POST",
        f"/api/v1/companies/{second['id']}/ki/samtaler/{thread_id}/meldinger",
        json={"content": "Hei", "level": "free"},
    )
    assert other_send.status_code == 404
    other_delete = write(
        client, "DELETE", f"/api/v1/companies/{second['id']}/ki/samtaler/{thread_id}"
    )
    assert other_delete.status_code == 404

    # Turning KI off blocks creating threads and sending messages with 409.
    write(client, "PATCH", f"/api/v1/companies/{first['id']}", json={"ki_enabled": False})
    off_create = write(client, "POST", f"/api/v1/companies/{first['id']}/ki/samtaler", json={})
    assert off_create.status_code == 409
    assert off_create.json()["code"] == "ki_turned_off"

    off_send = write(
        client,
        "POST",
        f"/api/v1/companies/{first['id']}/ki/samtaler/{thread_id}/meldinger",
        json={"content": "Hei", "level": "free"},
    )
    assert off_send.status_code == 409
    assert off_send.json()["code"] == "ki_turned_off"
    assert loynd_wire.sent == []
