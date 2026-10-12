"""The signed-in person's own account: the session list, and deleting it.

Two walls are under test. Between people: a session id that is not yours is a
404, exactly like one that does not exist. Between the person and the books:
deleting an account takes everything that is the person and leaves every
voucher and bilag they entered, still saying who entered it, because
bokføringsloven keeps the books for five years and the books must go on
answering that question for as long as they are kept. See docs/gdpr.md.
"""

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from myntos.auth.devices import describe_device
from myntos.core.clock import utc_now
from tests.conftest import PASSWORD, join_company, register, write
from tests.test_cashflow_api import PDF_BYTES, create_company, listing, record, upload

KARI = "kari@example.no"
OLA = "ola@example.no"
FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0"
IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)


@pytest.fixture
def database(database_url: str) -> Iterator[sa.Engine]:
    """A synchronous handle on the test's database, for asserting what is stored.

    The API answers what a caller may see; whether a row is really gone is a
    question only the table can answer, and it is the question erasure is about.
    """
    engine = sa.create_engine(database_url.replace("+aiosqlite", ""))
    yield engine
    engine.dispose()


def scalar(database: sa.Engine, statement: str, **params: Any) -> Any:
    with database.connect() as connection:
        return connection.execute(sa.text(statement), params).scalar()


def run(database: sa.Engine, statement: str, **params: Any) -> None:
    with database.begin() as connection:
        connection.execute(sa.text(statement), params)


def bearer(client: TestClient, email: str, *, user_agent: str = "testclient") -> dict[str, str]:
    """Sign in as ``email`` once more and return headers that use that session.

    A bearer token rather than the cookie, so one test client can hold several
    sessions, for several people, without each sign-in replacing the last.
    """
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD, "issue_bearer_token": True},
        headers={"User-Agent": user_agent},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['session_token']}"}


def sign_in(client: TestClient, email: str) -> None:
    write(client, "POST", "/api/v1/auth/logout")
    response = write(
        client, "POST", "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text


def erase(client: TestClient, password: str | None = PASSWORD) -> Any:
    return write(client, "POST", "/api/v1/account/erasure", json={"password": password})


def books_of(client: TestClient) -> dict[str, Any]:
    """Kari's account and a set of books, with the client signed in as her."""
    session = register(client, KARI, full_name="Kari Nordmann")
    return create_company(client, session["memberships"][0]["organization_id"])


# --- Device descriptions ------------------------------------------------------------


def test_a_device_is_described_by_browser_and_system_only() -> None:
    assert describe_device(FIREFOX) == "Firefox på Linux"
    # Every iOS browser says Safari and every Mac says Mac OS X; the phone wins.
    assert describe_device(IPHONE) == "Safari på iPhone"
    edge = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
    )
    assert describe_device(edge) == "Edge på Windows"
    assert describe_device("curl/8.5.0") == "Skript eller program"
    assert describe_device(None) is None
    assert describe_device("something nobody has heard of") is None


# --- The session list ---------------------------------------------------------------


def test_the_session_list_shows_every_live_login_and_marks_this_one(client: TestClient) -> None:
    register(client, KARI)
    laptop = bearer(client, KARI, user_agent=FIREFOX)
    bearer(client, KARI, user_agent=IPHONE)

    response = client.get("/api/v1/account/sessions", headers=laptop)
    assert response.status_code == 200, response.text
    sessions = response.json()
    # The signup's own session, the laptop and the phone.
    assert len(sessions) == 3
    assert [row["device"] for row in sessions if row["current"]] == ["Firefox på Linux"]
    assert "Safari på iPhone" in {row["device"] for row in sessions}
    # The raw user agent and the address are never handed back.
    assert all(
        set(row) == {"id", "created_at", "last_seen_at", "expires_at", "device", "current"}
        for row in sessions
    )


def test_ending_one_session_signs_that_device_out_and_no_other(client: TestClient) -> None:
    register(client, KARI)
    laptop = bearer(client, KARI)
    phone = bearer(client, KARI)
    phone_id = next(
        row["id"]
        for row in client.get("/api/v1/account/sessions", headers=phone).json()
        if row["current"]
    )

    assert client.delete(f"/api/v1/account/sessions/{phone_id}", headers=laptop).status_code == 204

    assert client.get("/api/v1/auth/me", headers=phone).status_code == 401
    assert client.get("/api/v1/auth/me", headers=laptop).status_code == 200
    remaining = {row["id"] for row in client.get("/api/v1/account/sessions", headers=laptop).json()}
    assert phone_id not in remaining


def test_another_persons_session_is_not_found(client: TestClient) -> None:
    register(client, KARI)
    kari = bearer(client, KARI)
    register(client, OLA)
    ola = bearer(client, OLA)
    ola_ids = [row["id"] for row in client.get("/api/v1/account/sessions", headers=ola).json()]

    for session_id in ola_ids:
        response = client.delete(f"/api/v1/account/sessions/{session_id}", headers=kari)
        assert response.status_code == 404
        assert response.json()["code"] == "session_not_found"
    # Still signed in: the 404 was not a revocation that merely lied about it.
    assert client.get("/api/v1/auth/me", headers=ola).status_code == 200
    # And an id that exists nowhere gets the same answer.
    assert client.delete("/api/v1/account/sessions/999999", headers=kari).status_code == 404


def test_signing_out_everywhere_else_keeps_this_session(client: TestClient) -> None:
    register(client, KARI)
    here = bearer(client, KARI)
    elsewhere = bearer(client, KARI)

    response = client.post("/api/v1/account/sessions/revoke-others", headers=here)
    assert response.status_code == 204

    assert client.get("/api/v1/auth/me", headers=here).status_code == 200
    assert client.get("/api/v1/auth/me", headers=elsewhere).status_code == 401
    assert [
        row["current"] for row in client.get("/api/v1/account/sessions", headers=here).json()
    ] == [True]


def test_the_session_list_needs_a_session(client: TestClient) -> None:
    assert client.get("/api/v1/account/sessions").status_code == 401


# --- Deleting an account that touched no books ------------------------------------


def test_an_account_with_empty_books_is_deleted_outright(
    client: TestClient, database: sa.Engine
) -> None:
    company = books_of(client)
    user_id = scalar(database, "SELECT id FROM app_user WHERE email = :e", e=KARI)

    plan = client.get("/api/v1/account/erasure").json()
    assert plan["blocked"] is False
    assert plan["keeps_name"] is False
    assert plan["requires_password"] is True
    assert [(c["company_id"], c["fate"]) for c in plan["companies"]] == [(company["id"], "deleted")]

    assert erase(client).status_code == 204

    # Nothing left that is her, and nothing left that was only hers.
    assert scalar(database, "SELECT count(*) FROM app_user WHERE id = :u", u=user_id) == 0
    assert scalar(database, "SELECT count(*) FROM user_session WHERE user_id = :u", u=user_id) == 0
    assert scalar(database, "SELECT count(*) FROM company WHERE id = :c", c=company["id"]) == 0
    assert scalar(database, "SELECT count(*) FROM organization") == 0

    assert client.get("/api/v1/auth/me").status_code == 401
    login = write(client, "POST", "/api/v1/auth/login", json={"email": KARI, "password": PASSWORD})
    assert login.status_code == 401
    # The address is free again, for a new account that starts from nothing.
    register(client, KARI)


def test_deletion_needs_the_password(client: TestClient, database: sa.Engine) -> None:
    books_of(client)

    missing = erase(client, password=None)
    assert missing.status_code == 403
    assert missing.json()["code"] == "reauthentication_required"

    wrong = erase(client, password="ikke-det-riktige-passordet")
    # 403, not 401: she is still signed in, and must not be sent to the login page.
    assert wrong.status_code == 403
    assert wrong.json()["code"] == "reauthentication_failed"
    # Counted towards the lockout, like a wrong login.
    assert (
        scalar(database, "SELECT failed_login_attempts FROM app_user WHERE email = :e", e=KARI) == 1
    )

    assert client.get("/api/v1/auth/me").status_code == 200


def test_an_account_without_a_password_must_have_signed_in_recently(
    client: TestClient, database: sa.Engine
) -> None:
    """A Google-only account has nothing to type, so a fresh login is the proof."""
    books_of(client)
    run(database, "UPDATE app_user SET password_hash = NULL WHERE email = :e", e=KARI)
    run(database, "UPDATE user_session SET created_at = :t", t=utc_now() - timedelta(hours=1))

    plan = client.get("/api/v1/account/erasure").json()
    assert plan["requires_password"] is False
    stale = erase(client, password=None)
    assert stale.status_code == 403
    assert stale.json()["code"] == "reauthentication_required"

    run(database, "UPDATE user_session SET created_at = :t", t=utc_now())
    assert erase(client, password=None).status_code == 204


# --- Refusals ------------------------------------------------------------------------


def test_the_last_owner_of_books_others_work_in_is_refused(
    client: TestClient, database: sa.Engine
) -> None:
    company = books_of(client)
    join_company(client, company["id"], email=OLA, role="member")
    sign_in(client, KARI)

    plan = client.get("/api/v1/account/erasure").json()
    assert plan["blocked"] is True
    assert plan["companies"][0]["fate"] == "blocks_members"

    response = erase(client)
    assert response.status_code == 409
    assert response.json()["code"] == "erasure_blocked"
    assert "Nordmann Consulting AS" in response.json()["detail"]
    assert "eier" in response.json()["detail"]
    # Refused means nothing happened, not that half of it did.
    assert scalar(database, "SELECT count(*) FROM app_user WHERE email = :e", e=KARI) == 1
    assert client.get("/api/v1/auth/me").status_code == 200


def test_the_sole_owner_of_books_with_entries_is_refused(client: TestClient) -> None:
    """Nobody else is on them, and they still have to be kept for five years."""
    company = books_of(client)
    record(client, company["id"], type_code="varekjop", gross_amount="100.00")

    plan = client.get("/api/v1/account/erasure").json()
    assert plan["companies"][0]["fate"] == "blocks_books"
    response = erase(client)
    assert response.status_code == 409
    assert response.json()["code"] == "erasure_blocked"
    assert client.get(f"/api/v1/companies/{company['id']}/cashflow").status_code == 200


# --- The books survive, and still say who --------------------------------------------


def test_a_departing_bookkeeper_leaves_a_tombstone_and_the_books_intact(
    client: TestClient, database: sa.Engine
) -> None:
    company = books_of(client)
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()
    assert (
        upload(client, company["id"], entry["id"], PDF_BYTES, "kvittering.pdf").status_code == 201
    )
    kari_id = entry["created_by_id"]

    # Ola becomes a second owner, so the books are not left without one.
    join_company(client, company["id"], email=OLA, role="owner", full_name="Ola Nordmann")
    sign_in(client, KARI)
    # A reset link she asked for and never used: it names her address, and the
    # tombstone keeps the user row the foreign key would otherwise cascade from.
    now = utc_now()
    run(
        database,
        "INSERT INTO user_email_token (user_id, purpose, email, token_hash, created_at,"
        " expires_at) VALUES (:u, 'reset_password', :e, :h, :c, :x)",
        u=kari_id,
        e=KARI,
        h="0" * 64,
        c=now,
        x=now + timedelta(minutes=30),
    )
    plan = client.get("/api/v1/account/erasure").json()
    assert plan["blocked"] is False
    assert plan["keeps_name"] is True
    assert plan["companies"][0]["fate"] == "leaves"

    assert erase(client).status_code == 204

    # The voucher and its bilag are still there, and still say who booked them.
    sign_in(client, OLA)
    entries = listing(client, company["id"])
    assert entries[entry["id"]]["created_by_id"] == kari_id
    assert entries[entry["id"]]["created_by_name"] == "Kari Nordmann"
    assert [a["filename"] for a in entries[entry["id"]]["attachments"]] == ["kvittering.pdf"]
    assert (
        scalar(
            database,
            "SELECT uploaded_by_user_id FROM bilag_attachment WHERE transaction_pk = :t",
            t=entry["id"],
        )
        == kari_id
    )
    # Ola's members list no longer has her.
    members = client.get(f"/api/v1/companies/{company['id']}/members").json()
    assert [m["email"] for m in members] == [OLA]

    # The tombstone: id and name, and nothing to sign in with or reach her by.
    with database.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT email, full_name, password_hash, is_active, deleted_at"
                " FROM app_user WHERE id = :u"
            ),
            {"u": kari_id},
        ).one()
    assert row.email == f"deleted-{kari_id}@erased.invalid"
    assert row.full_name == "Kari Nordmann"
    assert row.password_hash is None
    assert not row.is_active
    assert row.deleted_at is not None
    for table in (
        "user_session",
        "user_identity",
        "user_email_token",
        "company_membership",
        "organization_membership",
    ):
        # The table names are the literals above, never input.
        statement = f"SELECT count(*) FROM {table} WHERE user_id = :u"  # noqa: S608
        assert scalar(database, statement, u=kari_id) == 0
    assert (
        scalar(database, "SELECT count(*) FROM organization_invitation WHERE email = :e", e=KARI)
        == 0
    )

    login = write(client, "POST", "/api/v1/auth/login", json={"email": KARI, "password": PASSWORD})
    assert login.status_code == 401


def test_an_invitation_addressed_to_the_person_goes_with_them(
    client: TestClient, database: sa.Engine
) -> None:
    """The address is theirs; an invitation waiting for it has nobody to wait for."""
    company = books_of(client)
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/invitations",
        json={"email": OLA, "role": "member"},
    )
    assert response.status_code == 201, response.text
    write(client, "POST", "/api/v1/auth/logout")
    register(client, OLA)

    assert erase(client).status_code == 204
    assert (
        scalar(database, "SELECT count(*) FROM organization_invitation WHERE email = :e", e=OLA)
        == 0
    )
