"""Keys for agents and integrations, end to end (docs/agents-and-api.md).

The rules are easy to state and each one is a sentence somebody will be tempted
to soften later, so each has a test that fails when it is:

- a company says yes before any key exists, and saying no again stops them all;
- the key a person copies is worth nothing after its first use;
- refresh tokens rotate, and a spent one coming back stops the key;
- a key is the intersection of its scopes and its person's role, now;
- some routes no key reaches, whatever it holds;
- a key's writes say so on the voucher;
- a key dies with its person's access, their password reset, their account.

The company walls with a key are in the two boundary files, beside the
session versions, so that a new route is checked both ways without anybody
remembering to.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from myntos.auth import agent_keys
from myntos.core.clock import utc_now
from myntos.core.config import get_settings
from tests.conftest import (
    RecordingMailer,
    agent_header,
    agent_tokens,
    connection_key,
    join_company,
    register,
    write,
)

THIS_YEAR = utc_now().year
ORGNR = "910000004"


def _company(client: TestClient, session: dict[str, Any]) -> dict[str, Any]:
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": session["memberships"][0]["organization_id"],
            "orgnr": ORGNR,
            "name": "Klient En AS",
            "first_financial_year": THIS_YEAR,
            "vat_registered": True,
            "bank_account": "15062033158",
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _books(client: TestClient) -> dict[str, Any]:
    return _company(client, register(client))


def _payment(client: TestClient, company_id: int, headers: dict[str, str] | None = None) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/cashflow",
        json={
            "type_code": "varekjop",
            "occurred_on": f"{THIS_YEAR}-03-14",
            "gross_amount": "1250.00",
            "description": "Kontorrekvisita",
        },
        headers=headers or {},
    )


def _refresh(client: TestClient, token: str) -> Any:
    return client.post(
        "/api/v1/agent/token", json={"grant_type": "refresh_token", "refresh_token": token}
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _later(monkeypatch: pytest.MonkeyPatch, delta: timedelta) -> None:
    """Move the key module's clock forward, as if the key had sat unused."""
    moment = utc_now() + delta
    monkeypatch.setattr(agent_keys, "utc_now", lambda: moment)


def _keys(client: TestClient, company_id: int) -> list[dict[str, Any]]:
    response = client.get(f"/api/v1/companies/{company_id}/agent-keys")
    assert response.status_code == 200, response.text
    body: list[dict[str, Any]] = response.json()
    return body


# --- The company says yes first ---------------------------------------------------


def test_keys_are_off_until_the_company_turns_them_on(client: TestClient) -> None:
    books = _books(client)
    assert books["agent_access_enabled"] is False

    refused = write(
        client,
        "POST",
        f"/api/v1/companies/{books['id']}/agent-keys",
        json={"name": "Claude"},
    )
    assert refused.status_code == 403
    assert refused.json()["code"] == "agent_access_disabled"


def test_only_somebody_who_manages_access_can_turn_keys_on(client: TestClient) -> None:
    books = _books(client)
    join_company(client, books["id"], email="ola@example.no", role="member")

    refused = write(
        client, "PUT", f"/api/v1/companies/{books['id']}/agent-access", json={"enabled": True}
    )
    assert refused.status_code == 403


def test_turning_keys_off_stops_every_key_for_good(client: TestClient) -> None:
    books = _books(client)
    header = agent_header(client, books["id"])

    off = write(
        client, "PUT", f"/api/v1/companies/{books['id']}/agent-access", json={"enabled": False}
    )
    assert off.status_code == 200
    assert off.json()["agent_access_enabled"] is False
    assert client.get("/api/v1/agent/me", headers=header).status_code == 401

    # And on again brings nothing back: a key somebody forgot does not wake up.
    on = write(
        client, "PUT", f"/api/v1/companies/{books['id']}/agent-access", json={"enabled": True}
    )
    assert on.status_code == 200
    assert client.get("/api/v1/agent/me", headers=header).status_code == 401
    assert [key["state"] for key in _keys(client, books["id"])] == ["stopped"]
    assert _keys(client, books["id"])[0]["revoked_reason"] == "access_off"


# --- Making a key -----------------------------------------------------------------


def test_a_new_key_is_shown_once_and_its_person_is_told(
    client: TestClient, outbox: RecordingMailer
) -> None:
    books = _books(client)
    write(client, "PUT", f"/api/v1/companies/{books['id']}/agent-access", json={"enabled": True})
    outbox.sent.clear()  # the signup's own confirmation link
    made = write(
        client,
        "POST",
        f"/api/v1/companies/{books['id']}/agent-keys",
        json={"name": "  Claude   på jobb  ", "scopes": ["write"]},
    )
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["connection_key"].startswith("myntos_ck_")
    assert body["key"]["name"] == "Claude på jobb"
    # Read is always there: a key that can write but not see what it wrote is a mistake.
    assert body["key"]["scopes"] == ["read", "write"]
    assert body["key"]["state"] == "waiting"
    assert body["company_id"] == books["id"]

    listed = _keys(client, books["id"])
    assert "connection_key" not in listed[0]
    assert outbox.only.subject == "Ny nøkkel for KI-agent i Klient En AS"
    assert body["connection_key"] not in outbox.only.text


def test_a_key_never_does_more_than_its_person(client: TestClient) -> None:
    books = _books(client)
    write(client, "PUT", f"/api/v1/companies/{books['id']}/agent-access", json={"enabled": True})
    join_company(client, books["id"], email="les@example.no", role="viewer")

    refused = write(
        client,
        "POST",
        f"/api/v1/companies/{books['id']}/agent-keys",
        json={"name": "Agent", "scopes": ["post"]},
    )
    assert refused.status_code == 422
    assert refused.json()["code"] == "invalid_agent_scope"

    reading = write(
        client, "POST", f"/api/v1/companies/{books['id']}/agent-keys", json={"name": "Agent"}
    )
    assert reading.status_code == 201
    assert reading.json()["key"]["scopes"] == ["read"]


def test_an_unknown_scope_is_refused(client: TestClient) -> None:
    books = _books(client)
    write(client, "PUT", f"/api/v1/companies/{books['id']}/agent-access", json={"enabled": True})
    refused = write(
        client,
        "POST",
        f"/api/v1/companies/{books['id']}/agent-keys",
        json={"name": "Agent", "scopes": ["admin"]},
    )
    assert refused.status_code == 422
    assert refused.json()["code"] == "invalid_agent_scope"


def test_a_bankid_account_makes_no_keys_yet(client: TestClient, database_url: str) -> None:
    session = register(client)
    books = _company(client, session)
    write(client, "PUT", f"/api/v1/companies/{books['id']}/agent-access", json={"enabled": True})
    engine = sa.create_engine(database_url.replace("+aiosqlite", ""))
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO user_identity (user_id, provider, subject, created_at) "
                "VALUES (:user_id, 'bankid', 'sub-1', CURRENT_TIMESTAMP)"
            ),
            {"user_id": session["user"]["id"]},
        )
    engine.dispose()

    refused = write(
        client, "POST", f"/api/v1/companies/{books['id']}/agent-keys", json={"name": "Agent"}
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "bankid_account"


# --- The exchange -----------------------------------------------------------------


def test_the_pasted_key_works_once(client: TestClient, outbox: RecordingMailer) -> None:
    books = _books(client)
    key = connection_key(client, books["id"])
    outbox.sent.clear()

    first = client.post(
        "/api/v1/agent/token",
        json={"grant_type": "connection_key", "connection_key": key},
        headers={"User-Agent": "python-httpx/0.28.1"},
    )
    assert first.status_code == 200, first.text
    tokens = first.json()
    assert tokens["access_token"].startswith("myntos_at_")
    assert tokens["refresh_token"].startswith("myntos_rt_")
    assert tokens["token_type"] == "Bearer"
    assert 15 * 60 - 5 <= tokens["expires_in"] <= 15 * 60
    assert tokens["company_id"] == books["id"]
    assert tokens["company_name"] == "Klient En AS"
    assert tokens["scope"] == "post read write"

    again = client.post(
        "/api/v1/agent/token", json={"grant_type": "connection_key", "connection_key": key}
    )
    assert again.status_code == 400
    assert again.json()["code"] == "connection_key_spent"

    # The person hears that their key changed hands, and what took it.
    assert outbox.only.subject == "Nøkkelen «Testagent» er tatt i bruk"
    assert "python-httpx" in outbox.only.text
    listed = _keys(client, books["id"])[0]
    assert listed["state"] == "active"
    assert listed["last_used_client"] == "python-httpx"


def test_an_unused_key_dies_after_a_day(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    books = _books(client)
    key = connection_key(client, books["id"])
    _later(
        monkeypatch, timedelta(hours=get_settings().agent_connection_key_lifetime_hours, minutes=1)
    )

    late = client.post(
        "/api/v1/agent/token", json={"grant_type": "connection_key", "connection_key": key}
    )
    assert late.status_code == 400
    assert late.json()["code"] == "connection_key_expired"


def test_the_wrong_kind_of_token_is_named(client: TestClient) -> None:
    books = _books(client)
    key = connection_key(client, books["id"])

    as_bearer = client.get("/api/v1/agent/me", headers=_bearer(key))
    assert as_bearer.status_code == 401
    assert as_bearer.json()["code"] == "not_an_access_token"

    as_refresh = _refresh(client, key)
    assert as_refresh.status_code == 400
    assert "grant_type «connection_key»" in as_refresh.json()["detail"]


def test_a_key_says_what_it_is(client: TestClient) -> None:
    books = _books(client)
    header = agent_header(client, books["id"], scopes=("read",))

    me = client.get("/api/v1/agent/me", headers=header)
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["company_id"] == books["id"]
    assert body["orgnr"] == ORGNR
    assert body["person_name"] == "Kari Nordmann"
    assert body["role"] == "owner"
    assert body["scopes"] == ["read"]


def test_agent_me_is_for_keys(client: TestClient) -> None:
    _books(client)
    refused = client.get("/api/v1/agent/me")
    assert refused.status_code == 403
    assert refused.json()["code"] == "agent_key_required"


@pytest.mark.parametrize(
    ("user_agent", "shown"),
    [
        ("python-httpx/0.28.1", "python-httpx"),
        (
            "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; "
            "+https://openai.com/bot",
            "ChatGPT-User",
        ),
        ("Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/131.0", "Mozilla"),
        ("curl", "curl"),
        (None, None),
    ],
)
def test_the_list_names_the_agent_not_the_browser(
    user_agent: str | None, shown: str | None
) -> None:
    assert agent_keys.client_name(user_agent) == shown


# --- Rotation and reuse -----------------------------------------------------------


def test_a_refresh_rotates_the_refresh_token(client: TestClient) -> None:
    books = _books(client)
    first = agent_tokens(client, connection_key(client, books["id"]))

    second = _refresh(client, first["refresh_token"])
    assert second.status_code == 200, second.text
    rotated = second.json()
    assert rotated["refresh_token"] != first["refresh_token"]
    assert rotated["access_token"] != first["access_token"]
    assert (
        client.get("/api/v1/agent/me", headers=_bearer(rotated["access_token"])).status_code == 200
    )


def test_two_refreshes_at_once_do_not_stop_the_key(client: TestClient) -> None:
    """The parallel tool calls every agent makes, inside the grace window."""
    books = _books(client)
    first = agent_tokens(client, connection_key(client, books["id"]))

    one = _refresh(client, first["refresh_token"])
    two = _refresh(client, first["refresh_token"])
    assert one.status_code == 200
    assert two.status_code == 200
    assert _keys(client, books["id"])[0]["state"] == "active"


def test_a_spent_refresh_token_coming_back_stops_the_key(
    client: TestClient, outbox: RecordingMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    books = _books(client)
    first = agent_tokens(client, connection_key(client, books["id"]))
    rotated = _refresh(client, first["refresh_token"]).json()
    outbox.sent.clear()

    _later(monkeypatch, agent_keys.REFRESH_REUSE_GRACE + timedelta(seconds=1))
    stolen = _refresh(client, first["refresh_token"])
    assert stolen.status_code == 400
    assert stolen.json()["code"] == "refresh_token_reused"

    # Both holders are locked out: nothing can tell which one was the agent.
    assert _refresh(client, rotated["refresh_token"]).json()["code"] == "agent_key_stopped"
    assert (
        client.get("/api/v1/agent/me", headers=_bearer(rotated["access_token"])).status_code == 401
    )
    assert outbox.only.subject == "Nøkkelen «Testagent» er stoppet"
    key = _keys(client, books["id"])[0]
    assert (key["state"], key["revoked_reason"]) == ("stopped", "reuse")


def test_an_access_token_lasts_fifteen_minutes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    books = _books(client)
    tokens = agent_tokens(client, connection_key(client, books["id"]))
    _later(monkeypatch, timedelta(minutes=16))

    expired = client.get("/api/v1/agent/me", headers=_bearer(tokens["access_token"]))
    assert expired.status_code == 401
    assert expired.json()["code"] == "invalid_agent_token"
    assert expired.headers["WWW-Authenticate"] == "Bearer"
    assert _refresh(client, tokens["refresh_token"]).status_code == 200


def test_a_chain_left_unused_for_a_month_dies(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    books = _books(client)
    tokens = agent_tokens(client, connection_key(client, books["id"]))
    _later(monkeypatch, timedelta(days=get_settings().agent_refresh_idle_days, minutes=1))

    idle = _refresh(client, tokens["refresh_token"])
    assert idle.status_code == 400
    assert idle.json()["code"] in {"agent_key_stopped", "invalid_grant"}


def test_a_key_dies_after_a_year_however_busy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    books = _books(client)
    tokens = agent_tokens(client, connection_key(client, books["id"]))
    _later(monkeypatch, timedelta(days=get_settings().agent_key_lifetime_days, minutes=1))

    old = _refresh(client, tokens["refresh_token"])
    assert old.status_code == 400


# --- What a key may reach ---------------------------------------------------------


def test_a_key_works_where_its_scope_reaches(client: TestClient) -> None:
    books = _books(client)
    header = agent_header(client, books["id"], scopes=("read",))

    assert (
        client.get(f"/api/v1/companies/{books['id']}/cashflow", headers=header).status_code == 200
    )
    posted = _payment(client, books["id"], header)
    assert posted.status_code == 403
    assert posted.json()["code"] == "missing_scope"


def test_some_routes_no_key_reaches(client: TestClient) -> None:
    """Rule 5: access, money and the account are the person's, whatever the key holds."""
    books = _books(client)
    header = agent_header(client, books["id"])

    for method, url, payload in (
        ("GET", f"/api/v1/companies/{books['id']}/members", None),
        ("POST", f"/api/v1/companies/{books['id']}/agent-keys", {"name": "Barnebarn"}),
        ("PUT", f"/api/v1/companies/{books['id']}/agent-access", {"enabled": False}),
        ("POST", f"/api/v1/companies/{books['id']}/bankkontoer", {"account": "15062033158"}),
        ("POST", "/api/v1/account/erasure", {"password": "x"}),
        ("GET", "/api/v1/companies", None),
    ):
        response = write(client, method, url, json=payload, headers=header)
        assert response.status_code == 403, f"{method} {url}: {response.text}"
        assert response.json()["code"] == "not_for_agent_keys"


def test_a_key_is_its_persons_role_as_it_stands_now(client: TestClient) -> None:
    owner = register(client)
    books = _company(client, owner)
    write(client, "PUT", f"/api/v1/companies/{books['id']}/agent-access", json={"enabled": True})
    member = join_company(client, books["id"], email="ola@example.no", role="member")
    header = agent_header_without_switch(client, books["id"])

    assert _payment(client, books["id"], header).status_code == 201

    # The owner demotes Ola to a reader; Ola's key loses posting with him.
    write(client, "POST", "/api/v1/auth/logout")
    write(
        client,
        "POST",
        "/api/v1/auth/login",
        json={"email": "kari@example.no", "password": "korrekt-hest-batteri-stift"},
    )
    demoted = write(
        client,
        "PATCH",
        f"/api/v1/companies/{books['id']}/members/{member['user']['id']}",
        json={"role": "viewer"},
    )
    assert demoted.status_code == 200, demoted.text

    refused = _payment(client, books["id"], header)
    assert refused.status_code == 403
    assert refused.json()["code"] == "permission_denied"
    assert (
        client.get(f"/api/v1/companies/{books['id']}/cashflow", headers=header).status_code == 200
    )


def agent_header_without_switch(client: TestClient, company_id: int) -> dict[str, str]:
    """A key made by whoever is signed in, on books whose switch is already on."""
    made = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/agent-keys",
        json={"name": "Olas agent", "scopes": ["read", "write", "post"]},
    )
    assert made.status_code == 201, made.text
    tokens = agent_tokens(client, made.json()["connection_key"])
    return _bearer(tokens["access_token"])


def test_too_many_requests_are_slowed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    books = _books(client)
    header = agent_header(client, books["id"])
    monkeypatch.setattr(get_settings(), "agent_key_requests_per_minute", 2)

    answers = [client.get("/api/v1/agent/me", headers=header) for _ in range(3)]
    assert [answer.status_code for answer in answers] == [200, 200, 429]
    assert answers[-1].headers["Retry-After"] == "60"
    assert answers[-1].json()["code"] == "agent_rate_limited"


def test_a_key_that_keeps_probing_is_stopped(
    client: TestClient, outbox: RecordingMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    books = _books(client)
    header = agent_header(client, books["id"])
    monkeypatch.setattr(get_settings(), "agent_key_refusals_before_suspension", 3)
    outbox.sent.clear()

    for other in (books["id"] + 1, books["id"] + 2, books["id"] + 3):
        probe = client.get(f"/api/v1/companies/{other}/cashflow", headers=header)
        assert probe.status_code == 404

    assert client.get("/api/v1/agent/me", headers=header).status_code == 401
    key = _keys(client, books["id"])[0]
    assert (key["state"], key["revoked_reason"]) == ("stopped", "refusals")
    assert outbox.only.subject == "Nøkkelen «Testagent» er stoppet"


# --- The record on the voucher ----------------------------------------------------


def test_a_voucher_says_which_key_registered_it(client: TestClient) -> None:
    books = _books(client)
    header = agent_header(client, books["id"])

    by_key = _payment(client, books["id"], header)
    assert by_key.status_code == 201, by_key.text
    assert by_key.json()["created_by_name"] == "Kari Nordmann"
    assert by_key.json()["created_via"] == "Testagent"

    by_hand = _payment(client, books["id"])
    assert by_hand.json()["created_via"] is None

    listed = {
        entry["id"]: entry
        for entry in client.get(f"/api/v1/companies/{books['id']}/cashflow").json()
    }
    assert listed[by_key.json()["id"]]["created_via"] == "Testagent"
    assert listed[by_hand.json()["id"]]["created_via"] is None


# --- Stopping ---------------------------------------------------------------------


def test_an_agent_can_stop_its_own_key(client: TestClient) -> None:
    books = _books(client)
    tokens = agent_tokens(client, connection_key(client, books["id"]))

    stopped = client.post("/api/v1/agent/revoke", json={"token": tokens["refresh_token"]})
    assert stopped.status_code == 204
    assert (
        client.get("/api/v1/agent/me", headers=_bearer(tokens["access_token"])).status_code == 401
    )
    assert _keys(client, books["id"])[0]["revoked_reason"] == "agent"

    # And an unknown token answers the same, so the route tests nothing for anybody.
    assert client.post("/api/v1/agent/revoke", json={"token": "myntos_rt_nei"}).status_code == 204


def test_a_person_stops_their_own_key_and_a_manager_anyones(client: TestClient) -> None:
    books = _books(client)
    write(client, "PUT", f"/api/v1/companies/{books['id']}/agent-access", json={"enabled": True})
    owners_key = write(
        client, "POST", f"/api/v1/companies/{books['id']}/agent-keys", json={"name": "Karis"}
    ).json()["key"]["id"]

    join_company(client, books["id"], email="ola@example.no", role="member")
    olas_key = write(
        client, "POST", f"/api/v1/companies/{books['id']}/agent-keys", json={"name": "Olas"}
    ).json()["key"]["id"]

    # Ola sees his own key and nobody else's, and cannot stop Kari's.
    assert [key["id"] for key in _keys(client, books["id"])] == [olas_key]
    assert (
        write(
            client, "DELETE", f"/api/v1/companies/{books['id']}/agent-keys/{owners_key}"
        ).status_code
        == 404
    )
    assert (
        write(
            client, "DELETE", f"/api/v1/companies/{books['id']}/agent-keys/{olas_key}"
        ).status_code
        == 204
    )

    # Kari, who manages access, sees both.
    write(client, "POST", "/api/v1/auth/logout")
    write(
        client,
        "POST",
        "/api/v1/auth/login",
        json={"email": "kari@example.no", "password": "korrekt-hest-batteri-stift"},
    )
    seen = {key["id"]: key for key in _keys(client, books["id"])}
    assert set(seen) == {owners_key, olas_key}
    assert seen[olas_key]["person_name"] == "Ola Nordmann"
    assert seen[olas_key]["revoked_reason"] == "person"


def test_stopping_a_key_ends_the_token_the_agent_holds(client: TestClient) -> None:
    """«Stopp» under Tilgang is the answer to a key in the wrong hands.

    A password change or signing out other devices leaves keys alone on
    purpose, so this has to work on the very next request, with the token the
    agent already has, not only for new ones.
    """
    books = _books(client)
    header = agent_header(client, books["id"])
    assert client.get("/api/v1/agent/me", headers=header).status_code == 200
    [key] = _keys(client, books["id"])

    stopped = write(client, "DELETE", f"/api/v1/companies/{books['id']}/agent-keys/{key['id']}")

    assert stopped.status_code == 204
    assert client.get("/api/v1/agent/me", headers=header).status_code == 401
    register = client.get(f"/api/v1/companies/{books['id']}/cashflow", headers=header)
    assert register.status_code == 401


def test_leaving_the_books_stops_your_keys(client: TestClient) -> None:
    books = _books(client)
    write(client, "PUT", f"/api/v1/companies/{books['id']}/agent-access", json={"enabled": True})
    ola = join_company(client, books["id"], email="ola@example.no", role="member")
    header = agent_header_without_switch(client, books["id"])

    left = write(client, "DELETE", f"/api/v1/companies/{books['id']}/members/{ola['user']['id']}")
    assert left.status_code == 204
    assert client.get("/api/v1/agent/me", headers=header).status_code == 401


def test_a_password_reset_stops_every_key(client: TestClient, outbox: RecordingMailer) -> None:
    books = _books(client)
    header = agent_header(client, books["id"])
    outbox.sent.clear()

    write(client, "POST", "/api/v1/auth/password/forgot", json={"email": "kari@example.no"})
    token = outbox.only.text.split("#token=", maxsplit=1)[1].split()[0]
    reset = write(
        client,
        "POST",
        "/api/v1/auth/password/reset",
        json={"token": token, "new_password": "et-helt-annet-langt-passord"},
    )
    assert reset.status_code == 204, reset.text
    assert client.get("/api/v1/agent/me", headers=header).status_code == 401


def test_deleting_the_account_stops_its_keys_and_keeps_the_record(
    client: TestClient, database_url: str
) -> None:
    books = _books(client)
    write(client, "PUT", f"/api/v1/companies/{books['id']}/agent-access", json={"enabled": True})
    join_company(client, books["id"], email="ola@example.no", role="member")
    header = agent_header_without_switch(client, books["id"])
    assert _payment(client, books["id"], header).status_code == 201

    erased = write(
        client,
        "POST",
        "/api/v1/account/erasure",
        json={"password": "korrekt-hest-batteri-stift"},
    )
    assert erased.status_code == 204, erased.text
    assert client.get("/api/v1/agent/me", headers=header).status_code == 401

    engine = sa.create_engine(database_url.replace("+aiosqlite", ""))
    with engine.connect() as connection:
        key = connection.execute(
            sa.text("SELECT name, revoked_reason, last_used_client FROM agent_key")
        ).one()
        tokens = connection.execute(sa.text("SELECT count(*) FROM agent_token")).scalar()
        via = connection.execute(
            sa.text(
                "SELECT created_via_agent_key_id FROM gl_transaction "
                "WHERE created_via_agent_key_id IS NOT NULL"
            )
        ).all()
    engine.dispose()
    # The key stays as the voucher's record of how it was registered, and
    # nothing that could identify a device or still open anything.
    assert (key.name, key.revoked_reason, key.last_used_client) == (
        "Olas agent",
        "account_deleted",
        None,
    )
    assert tokens == 0
    assert len(via) == 1
