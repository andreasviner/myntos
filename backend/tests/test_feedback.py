"""Feedback from anybody, kept without a name (docs/feedback.md).

What these hold: anybody can send, nobody is identified, the reference is the
only way back, the caps count everybody together, the operator's words are the
only ones made public, and the request leaves no line in the access log.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from myntos.api.v1.feedback import keep_out_of_the_access_log
from myntos.core.clock import utc_now
from myntos.core.config import get_settings
from myntos.db import session as db_session
from myntos.domain.feedback import triage
from myntos.domain.feedback.models import Feedback
from tests.conftest import RecordingMailer, register, write

URL = "/api/v1/tilbakemeldinger"

BUG = {"kind": "bug", "text": "Knappen «Lagre» på fakturasiden gjør ingenting på mobil."}


def _send(client: TestClient, **fields: Any) -> Any:
    return write(client, "POST", URL, json={**BUG, **fields})


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Set feedback settings for one test; the cache is cleared on the way in and out."""
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


@pytest.fixture
def operator(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Run the triage command against this test's database, as the operator would.

    The command builds its engine from ``DATABASE_URL`` through the module's
    cached engine, so both the setting and the cache are pointed here and put
    back afterwards.
    """
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr(db_session, "_engine", None)
    monkeypatch.setattr(db_session, "_session_factory", None)
    get_settings.cache_clear()

    def run(*argv: str) -> int:
        db_session._engine = None
        db_session._session_factory = None
        return triage.main(list(argv))

    yield run
    get_settings.cache_clear()


def _sync(database_url: str) -> sa.Engine:
    return sa.create_engine(database_url.replace("+aiosqlite", ""))


# --- Anybody may send, and nobody is named -----------------------------------------


def test_anybody_can_send_feedback_without_signing_in(client: TestClient) -> None:
    sent = _send(client)

    assert sent.status_code == 201, sent.text
    body = sent.json()
    assert body["status"] == "new"
    assert len(body["reference"]) >= 20
    # The fragment, never the query: the reference reaches no server log.
    assert body["url"].endswith(f"/tilbakemelding/#ref={body['reference']}")


def test_the_table_has_no_column_that_could_name_the_sender() -> None:
    """The anonymity is the absence of these columns. Adding one is a design change.

    If this fails because a column was added on purpose, read docs/feedback.md
    first: everything the page and the personvernerklæring promise rests on
    this list.
    """
    assert {column.name for column in Feedback.__table__.columns} == {
        "id",
        "reference_hash",
        "kind",
        "source",
        "agent_name",
        "text",
        "page",
        "status",
        "reply",
        "summary",
        "created_at",
        "changed_at",
    }
    assert not Feedback.__table__.foreign_keys


def test_a_signed_in_person_and_a_key_are_not_read(client: TestClient) -> None:
    """A session cookie and a bearer token are both sent along, and neither is looked at.

    A garbage access token would be a 401 on any route that asked who is
    calling, so a 201 here is the proof that this one does not.
    """
    register(client)

    signed_in = _send(client)
    with_token = _send(client, headers={"Authorization": "Bearer myntos_at_not-a-real-token"})

    assert signed_in.status_code == 201, signed_in.text
    assert with_token.status_code == 201, with_token.text


def test_an_agent_says_so_and_a_person_cannot_claim_a_program_name(
    client: TestClient, database_url: str
) -> None:
    agent = _send(client, source="agent", agent_name="Claude", page="POST /api/v1/x")
    person = _send(client, source="person", agent_name="Claude")

    read_agent = client.get(f"{URL}/{agent.json()['reference']}").json()
    assert read_agent["source"] == "agent"
    assert read_agent["page"] == "POST /api/v1/x"
    with _sync(database_url).connect() as db:
        names = db.execute(sa.text("select agent_name from feedback order by id")).scalars().all()
    assert names == ["Claude", None]
    assert person.status_code == 201


# --- The reference is the only way back --------------------------------------------


def test_the_sender_reads_it_back_with_the_reference(client: TestClient) -> None:
    reference = _send(client).json()["reference"]

    item = client.get(f"{URL}/{reference}")

    assert item.status_code == 200, item.text
    body = item.json()
    assert body["text"] == BUG["text"]
    assert body["kind"] == "bug"
    assert body["status"] == "new"
    assert body["published"] is False
    assert body["reply"] is None


def test_only_the_digest_of_the_reference_is_stored(client: TestClient, database_url: str) -> None:
    reference = _send(client).json()["reference"]

    with _sync(database_url).connect() as db:
        stored = db.execute(sa.text("select reference_hash from feedback")).scalar_one()
    assert reference not in stored
    assert len(stored) == 64


def test_a_reference_nobody_was_given_is_not_found(client: TestClient) -> None:
    missing = client.get(f"{URL}/AAAAAAAAAAAAAAAAAAAA")

    assert missing.status_code == 404
    assert missing.json()["code"] == "feedback_not_found"


def test_the_sender_can_withdraw_what_they_sent(client: TestClient, database_url: str) -> None:
    reference = _send(client).json()["reference"]

    withdrawn = write(client, "DELETE", f"{URL}/{reference}")

    assert withdrawn.status_code == 204, withdrawn.text
    assert client.get(f"{URL}/{reference}").status_code == 404
    with _sync(database_url).connect() as db:
        assert db.execute(sa.text("select count(*) from feedback")).scalar_one() == 0


# --- What is accepted -----------------------------------------------------------------


@pytest.mark.parametrize("text", ["for kort", "  \n\t  ", "x" * 5001])
def test_a_text_that_is_too_short_or_too_long_is_refused(client: TestClient, text: str) -> None:
    refused = _send(client, text=text)

    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "invalid_feedback"


def test_control_characters_are_taken_out(client: TestClient) -> None:
    reference = _send(
        client, text="Linje én\r\nLinje to\x1b[31m rød\x00", page="/faktura/\n"
    ).json()["reference"]

    item = client.get(f"{URL}/{reference}").json()
    assert item["text"] == "Linje én\nLinje to[31m rød"
    assert item["page"] == "/faktura/"


def test_an_unknown_kind_is_refused(client: TestClient) -> None:
    assert _send(client, kind="complaint").status_code == 422


# --- The caps count everybody together ----------------------------------------------


def test_the_hours_cap_counts_everybody_together(
    client: TestClient, settings_env: pytest.MonkeyPatch
) -> None:
    settings_env.setenv("FEEDBACK_MAX_PER_HOUR", "2")
    get_settings.cache_clear()

    assert _send(client).status_code == 201
    assert _send(client, headers={"Authorization": "Bearer anybody-else"}).status_code == 201
    third = _send(client)

    assert third.status_code == 429, third.text
    assert third.json()["code"] == "feedback_busy"
    assert third.headers["Retry-After"] == "3600"


def test_too_much_unread_closes_the_box_until_the_operator_reads(
    client: TestClient, settings_env: pytest.MonkeyPatch, operator: Any
) -> None:
    settings_env.setenv("FEEDBACK_MAX_UNREAD", "1")
    get_settings.cache_clear()
    assert _send(client).status_code == 201

    full = _send(client)
    assert full.status_code == 503, full.text
    assert full.json()["code"] == "feedback_inbox_full"

    # Showing it is reading it, and the box opens again.
    assert operator("show", "1") == 0
    assert _send(client).status_code == 201


# --- The operator's half --------------------------------------------------------------


def test_the_reply_and_the_status_reach_the_sender(client: TestClient, operator: Any) -> None:
    reference = _send(client).json()["reference"]

    assert operator("reply", "1", "Takk, rettet i dag.") == 0
    assert operator("status", "1", "done") == 0

    item = client.get(f"{URL}/{reference}").json()
    assert item["reply"] == "Takk, rettet i dag."
    assert item["status"] == "done"
    assert item["changed_at"] is not None


def test_the_public_list_shows_the_operators_words_and_never_the_senders(
    client: TestClient, operator: Any
) -> None:
    _send(client, text="Kari Nordmann i Fjell AS kan ikke sende faktura 1042 til kunden.")
    _send(client, kind="request", text="Lønn og a-melding, takk. Vi har tre ansatte.")
    assert client.get(URL).json() == []

    assert operator("publish", "1", "Faktura kunne ikke sendes fra mobil") == 0
    assert operator("reply", "1", "Rettet 21. oktober.") == 0

    listed = client.get(URL)
    assert listed.status_code == 200
    [item] = listed.json()
    assert item["summary"] == "Faktura kunne ikke sendes fra mobil"
    assert item["reply"] == "Rettet 21. oktober."
    assert item["status"] == "read"
    assert "Kari" not in listed.text
    assert "text" not in item

    assert operator("unpublish", "1") == 0
    assert client.get(URL).json() == []


def test_the_operator_can_delete_spam(client: TestClient, operator: Any) -> None:
    reference = _send(client).json()["reference"]

    assert operator("delete", "1") == 0

    assert client.get(f"{URL}/{reference}").status_code == 404


def test_an_unknown_number_is_an_error_not_a_crash(client: TestClient, operator: Any) -> None:
    assert operator("show", "99") == 1


def test_closed_items_are_deleted_after_the_retention_and_open_ones_are_kept(
    client: TestClient, database_url: str, operator: Any
) -> None:
    done = _send(client).json()["reference"]
    still_open = _send(client).json()["reference"]
    assert operator("status", "1", "done") == 0
    long_ago = utc_now() - timedelta(days=400)
    with _sync(database_url).begin() as db:
        db.execute(
            sa.text("update feedback set changed_at = :at, created_at = :at"), {"at": long_ago}
        )

    assert operator("purge") == 0

    assert client.get(f"{URL}/{done}").status_code == 404
    assert client.get(f"{URL}/{still_open}").status_code == 200


# --- Telling the operator, without the text ---------------------------------------------


def test_the_operator_is_told_once_per_quiet_hour_and_never_shown_the_text(
    client: TestClient, settings_env: pytest.MonkeyPatch, outbox: RecordingMailer
) -> None:
    settings_env.setenv("FEEDBACK_NOTIFY_TO", "tilbakemelding@myntos.no")
    get_settings.cache_clear()

    _send(client)
    _send(client, kind="request", text="Et ønske til, rett etter det første.")

    notice = outbox.only
    assert notice.to == "tilbakemelding@myntos.no"
    assert "nummer 1" in notice.text
    assert "Lagre" not in notice.text
    assert "Lagre" not in notice.subject


def test_nobody_is_told_when_nobody_is_configured(
    client: TestClient, outbox: RecordingMailer
) -> None:
    _send(client)

    assert outbox.sent == []


# --- Not in the access log --------------------------------------------------------------


def _access_record(path: str) -> logging.LogRecord:
    # The shape uvicorn logs: client address, method, path, HTTP version, status.
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("203.0.113.7:0", "POST", path, "1.1", 201),
        None,
    )


def test_the_feedback_routes_leave_no_line_in_the_access_log(client: TestClient) -> None:
    """``create_app`` has run (the client fixture), so the filter is on uvicorn's logger."""
    access = logging.getLogger("uvicorn.access")
    keep_out_of_the_access_log()

    assert not access.filter(_access_record(URL))
    assert not access.filter(_access_record(f"{URL}/AAAAAAAAAAAAAAAAAAAA"))
    assert access.filter(_access_record("/api/v1/health"))
    # Installed once, however many apps are made.
    assert sum(type(f).__name__ == "_NotTheFeedbackRoutes" for f in access.filters) == 1
