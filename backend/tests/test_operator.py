"""The operator's page, ``/drift/``, and the key that opens it (docs/operator.md).

What these hold: the routes do not exist until a deployment sets a key; only
that key opens them, never a person's session or an agent's key; the digest in
``.env`` is not the key; the operator reads every item and every failure; and a
leaked key can change a status and nothing else.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from myntos.auth.operator import new_key
from myntos.core.config import Settings, get_settings
from tests.conftest import (
    TEST_ORIGIN,
    agent_header,
    register,
    write,
)
from tests.test_server_errors import NAME, _store_pending

FEEDBACK = "/api/v1/drift/tilbakemeldinger"
ERRORS = "/api/v1/drift/feil"


@pytest.fixture
def operator_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Turn the page on for one test, as ``OPERATOR_KEY_SHA256`` in ``.env`` does."""
    key, digest = new_key()
    monkeypatch.setenv("OPERATOR_KEY_SHA256", digest)
    get_settings.cache_clear()
    yield key
    get_settings.cache_clear()


def _as(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _send_feedback(client: TestClient, text: str) -> str:
    sent = write(client, "POST", "/api/v1/tilbakemeldinger", json={"kind": "bug", "text": text})
    assert sent.status_code == 201, sent.text
    reference: str = sent.json()["reference"]
    return reference


# --- Off until set, and only the key opens it ---------------------------------------


def test_the_page_does_not_exist_until_a_key_is_set(client: TestClient) -> None:
    for url in (FEEDBACK, ERRORS):
        refused = client.get(url, headers=_as("myntos_op_anything"))

        assert refused.status_code == 404, url
        assert refused.json()["code"] == "operator_page_off"


def test_no_key_a_wrong_key_or_the_digest_is_refused(client: TestClient, operator_key: str) -> None:
    digest = get_settings().operator_key_digest
    assert digest is not None
    for headers in ({}, _as("myntos_op_" + "A" * 43), _as(digest), _as(operator_key + "x")):
        refused = client.get(FEEDBACK, headers=headers)

        assert refused.status_code == 401, headers
        assert refused.json()["code"] == "not_the_operator"


def test_a_signed_in_person_is_not_the_operator(client: TestClient, operator_key: str) -> None:
    register(client)

    assert client.get(FEEDBACK).status_code == 401
    assert client.get(ERRORS).status_code == 401


def test_an_agent_key_is_not_the_operator(client: TestClient, operator_key: str) -> None:
    session = register(client)
    company = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": session["memberships"][0]["organization_id"],
            "orgnr": "910000004",
            "name": "Klient En AS",
            "first_financial_year": 2026,
            "vat_registered": True,
            "bank_account": "15062033158",
        },
    )
    assert company.status_code == 201, company.text

    refused = client.get(FEEDBACK, headers=agent_header(client, company.json()["id"]))

    assert refused.status_code == 401


def test_the_setting_takes_a_digest_and_reads_the_key_itself_as_off() -> None:
    key, digest = new_key()

    assert Settings(operator_key_sha256=f" {digest.upper()} ").operator_key_digest == digest
    assert Settings(operator_key_sha256="").operator_key_sha256 is None
    # Not a refusal at startup: a typo in an optional setting must not keep
    # the books from opening on the next restart.
    assert Settings(operator_key_sha256=key).operator_key_digest is None


def test_the_key_pasted_where_the_digest_goes_says_so(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    key, _ = new_key()
    monkeypatch.setenv("OPERATOR_KEY_SHA256", key)
    get_settings.cache_clear()

    refused = client.get(FEEDBACK, headers=_as(key))

    get_settings.cache_clear()
    assert refused.status_code == 404
    assert refused.json()["code"] == "operator_page_off"
    assert "ikke en sjekksum" in refused.json()["detail"]


def test_a_leaked_key_can_change_a_status_and_nothing_else(client: TestClient) -> None:
    """Replying, publishing and deleting stay in the shell (docs/operator.md)."""
    paths = client.get("/api/v1/openapi.json").json()["paths"]
    operations = {
        (method.upper(), path)
        for path, item in paths.items()
        if path.startswith("/api/v1/drift")
        for method in item
    }

    assert operations == {
        ("GET", FEEDBACK),
        ("PUT", FEEDBACK + "/{number}/status"),
        ("GET", ERRORS),
        ("PUT", ERRORS + "/{number}/status"),
    }


# --- Feedback --------------------------------------------------------------------------


def test_the_operator_reads_every_item_newest_first(client: TestClient, operator_key: str) -> None:
    _send_feedback(client, "Den første tilbakemeldingen, om bilag.")
    _send_feedback(client, "Den andre tilbakemeldingen, om faktura.")

    items = client.get(FEEDBACK, headers=_as(operator_key))

    assert items.status_code == 200, items.text
    assert [item["text"] for item in items.json()] == [
        "Den andre tilbakemeldingen, om faktura.",
        "Den første tilbakemeldingen, om bilag.",
    ]
    assert {item["status"] for item in items.json()} == {"new"}


def test_a_status_the_operator_sets_reaches_the_sender(
    client: TestClient, operator_key: str
) -> None:
    reference = _send_feedback(client, "Lagre-knappen gjør ingenting på mobil.")
    [item] = client.get(FEEDBACK, headers=_as(operator_key)).json()

    changed = client.put(
        f"{FEEDBACK}/{item['number']}/status", json={"status": "planned"}, headers=_as(operator_key)
    )

    assert changed.status_code == 200, changed.text
    assert changed.json()["status"] == "planned"
    assert client.get(f"/api/v1/tilbakemeldinger/{reference}").json()["status"] == "planned"
    assert client.get(FEEDBACK, params={"status": "new"}, headers=_as(operator_key)).json() == []


def test_an_unknown_feedback_number_is_a_404(client: TestClient, operator_key: str) -> None:
    missing = client.put(
        f"{FEEDBACK}/999/status", json={"status": "read"}, headers=_as(operator_key)
    )

    assert missing.status_code == 404
    assert missing.json()["code"] == "feedback_not_found"


# --- Failures --------------------------------------------------------------------------


def _fail_once(client: TestClient) -> None:
    app: FastAPI = client.app  # type: ignore[assignment]

    @app.get("/api/v1/companies/{company_id}/_test_boom")
    async def boom(company_id: int) -> None:
        raise RuntimeError(f"{NAME} ({company_id})")

    response = client.get("/api/v1/companies/4711/_test_boom", headers={"Origin": TEST_ORIGIN})
    assert response.status_code == 500


def test_the_operator_sees_a_failure_and_resolves_it(
    client: TestClient, database_url: str, operator_key: str
) -> None:
    _fail_once(client)
    _store_pending(database_url)

    [failure] = client.get(ERRORS, headers=_as(operator_key)).json()

    assert failure["status"] == "open"
    assert failure["exception"] == "RuntimeError"
    assert failure["route"] == "GET /api/v1/companies/{company_id}/_test_boom"
    assert failure["count"] == 1
    assert failure["frames"][-1]["function"] == "boom"
    assert NAME not in str(failure)
    assert "4711" not in str(failure)

    resolved = client.put(
        f"{ERRORS}/{failure['number']}/status",
        json={"status": "resolved"},
        headers=_as(operator_key),
    )

    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["resolved_at"] is not None
    assert client.get(ERRORS, headers=_as(operator_key)).json() == []
    every: list[dict[str, Any]] = client.get(
        ERRORS, params={"status": "all"}, headers=_as(operator_key)
    ).json()
    assert [entry["status"] for entry in every] == ["resolved"]


def test_an_unknown_failure_number_is_a_404(client: TestClient, operator_key: str) -> None:
    missing = client.put(
        f"{ERRORS}/999/status", json={"status": "resolved"}, headers=_as(operator_key)
    )

    assert missing.status_code == 404
    assert missing.json()["code"] == "server_error_not_found"
