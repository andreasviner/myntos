"""Deleting a company from Myntos, and the five years its books are kept after.

What is under test is the line bokføringsloven § 13 draws: books with nothing
in them go at once, books with anything in them are locked away (nobody can
open them, nobody else keeps access, every key stops, the orgnr is free) until
31 December five years after their last financial year, and are then deleted
for good, stored files included. Until then an owner can open them again. See
myntos.domain.identity.closure and docs/gdpr.md, «Deleting a company».
"""

import asyncio
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from myntos.core.clock import utc_now
from myntos.domain.identity import closure
from myntos.domain.ledger import service as ledger_service
from myntos.storage.local import LocalDocumentStorage
from tests.conftest import PASSWORD, agent_header, join_company, register, write
from tests.test_cashflow_api import PDF_BYTES, create_company, record, upload
from tests.test_faktura_api import create_draft, issue, make_company

KARI = "kari@example.no"
OLA = "ola@example.no"
PER = "per@example.no"
THIS_YEAR = utc_now().year
KEPT_UNTIL = date(THIS_YEAR + 5, 12, 31)


@pytest.fixture
def database(database_url: str) -> Iterator[sa.Engine]:
    engine = sa.create_engine(database_url.replace("+aiosqlite", ""))
    yield engine
    engine.dispose()


def scalar(database: sa.Engine, statement: str, **params: Any) -> Any:
    with database.connect() as connection:
        return connection.execute(sa.text(statement), params).scalar()


def run(database: sa.Engine, statement: str, **params: Any) -> None:
    with database.begin() as connection:
        connection.execute(sa.text(statement), params)


def sign_in(client: TestClient, email: str) -> None:
    write(client, "POST", "/api/v1/auth/logout")
    response = write(
        client, "POST", "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text


def books_of(client: TestClient, email: str = KARI) -> dict[str, Any]:
    session = register(client, email, full_name="Kari Nordmann")
    return create_company(client, session["memberships"][0]["organization_id"])


def plan_of(client: TestClient, company_id: int) -> Any:
    return client.get(f"/api/v1/companies/{company_id}/sletting")


def close(
    client: TestClient,
    company_id: int,
    password: str | None = PASSWORD,
    name: str = "Nordmann Consulting AS",
) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/sletting",
        json={"password": password, "company_name": name},
    )


def claimed_elsewhere(database_url: str, orgnr: str, *, user_id: int) -> set[str]:
    """Whether ``orgnr`` would warn ``user_id`` that somebody else has confirmed it."""

    async def go() -> set[str]:
        engine = create_async_engine(database_url)
        try:
            async with async_sessionmaker(engine)() as db:
                return await ledger_service.claimed_elsewhere(db, [orgnr], user_id=user_id)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def purge(database_url: str, storage_root: Path, today: date) -> list[int]:
    """Run the daily purge against the test's database, as of ``today``."""

    async def go() -> list[int]:
        engine = create_async_engine(database_url)

        @sa.event.listens_for(engine.sync_engine, "connect")
        def _foreign_keys(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                return await closure.purge_expired(
                    db, LocalDocumentStorage(storage_root), today=today
                )
        finally:
            await engine.dispose()

    return asyncio.run(go())


# --- What deleting does ----------------------------------------------------------


def test_the_retention_ends_five_years_after_the_last_financial_year() -> None:
    assert closure.retention_end(2026) == date(2031, 12, 31)


def test_empty_books_are_deleted_at_once(client: TestClient, database: sa.Engine) -> None:
    company = books_of(client)

    plan = plan_of(client, company["id"]).json()
    assert plan["outcome"] == "deleted"
    assert plan["retain_until"] is None
    assert plan["years"] == []

    response = close(client, company["id"])
    assert response.status_code == 200, response.text
    assert response.json() == {"outcome": "deleted", "retain_until": None}
    assert scalar(database, "SELECT count(*) FROM company WHERE id = :c", c=company["id"]) == 0
    assert client.get("/api/v1/companies/slettede").json() == []


def test_books_with_entries_are_locked_away_until_their_retention_ends(
    client: TestClient, database: sa.Engine, outbox: Any
) -> None:
    company = books_of(client)
    record(client, company["id"], type_code="varekjop", gross_amount="100.00")
    agent_header(client, company["id"])
    join_company(client, company["id"], email=OLA, role="admin")
    sign_in(client, KARI)
    # An invitation still waiting is a way in that must not outlive the books.
    pending = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/invitations",
        json={"email": PER, "role": "member"},
    )
    assert pending.status_code == 201, pending.text

    plan = plan_of(client, company["id"]).json()
    assert plan["outcome"] == "kept"
    assert plan["retain_until"] == KEPT_UNTIL.isoformat()
    assert plan["years"] == [THIS_YEAR]
    assert plan["members_leaving"] == 1
    assert plan["live_keys"] == 1
    assert plan["requires_password"] is True

    response = close(client, company["id"])
    assert response.status_code == 200, response.text
    assert response.json() == {"outcome": "kept", "retain_until": KEPT_UNTIL.isoformat()}

    # The books are all still there, untouched.
    assert scalar(database, "SELECT count(*) FROM company WHERE id = :c", c=company["id"]) == 1
    assert scalar(database, "SELECT count(*) FROM gl_transaction") == 1
    # But nobody can open them, the owner included, and they are in no list.
    assert client.get("/api/v1/companies").json() == []
    assert client.get(f"/api/v1/companies/{company['id']}/cashflow").status_code == 404
    assert client.get(f"/api/v1/companies/{company['id']}").status_code == 404
    # Everybody else has left, the invitation is gone, and every key has stopped.
    roles = scalar(
        database,
        "SELECT group_concat(role) FROM company_membership WHERE company_id = :c",
        c=company["id"],
    )
    assert roles == "owner"
    assert (
        scalar(
            database,
            "SELECT count(*) FROM organization_invitation"
            " WHERE company_id = :c AND accepted_at IS NULL",
            c=company["id"],
        )
        == 0
    )
    # Ola's accepted one stays, as the record of who let him in.
    assert (
        scalar(
            database,
            "SELECT count(*) FROM organization_invitation WHERE company_id = :c",
            c=company["id"],
        )
        == 1
    )
    assert scalar(database, "SELECT revoked_reason FROM agent_key") == "company_closed"
    assert scalar(database, "SELECT count(*) FROM agent_token") == 0
    assert not scalar(
        database, "SELECT agent_access_enabled FROM company WHERE id = :c", c=company["id"]
    )

    # The owner finds them under the account, with the day they go.
    closed = client.get("/api/v1/companies/slettede").json()
    assert [(c["id"], c["retain_until"]) for c in closed] == [
        (company["id"], KEPT_UNTIL.isoformat())
    ]
    # The colleague who left finds nothing at all.
    sign_in(client, OLA)
    assert client.get("/api/v1/companies/slettede").json() == []
    assert client.get(f"/api/v1/companies/{company['id']}/cashflow").status_code == 404


# --- Who may, and the proof ----------------------------------------------------------


def test_only_an_owner_may_delete_and_only_with_proof(
    client: TestClient, database: sa.Engine
) -> None:
    company = books_of(client)
    record(client, company["id"], type_code="varekjop", gross_amount="100.00")
    # Signed in an hour ago, so the sign-in alone is no longer proof.
    run(database, "UPDATE user_session SET created_at = :t", t=utc_now() - timedelta(hours=1))

    missing = close(client, company["id"], password=None)
    assert missing.status_code == 403
    assert missing.json()["code"] == "reauthentication_required"
    wrong = close(client, company["id"], password="ikke-det-riktige-passordet")
    assert wrong.status_code == 403
    assert wrong.json()["code"] == "reauthentication_failed"

    join_company(client, company["id"], email=OLA, role="admin")
    assert plan_of(client, company["id"]).status_code == 403
    refused = close(client, company["id"])
    assert refused.status_code == 403
    assert refused.json()["code"] == "permission_denied"

    # Nothing happened.
    sign_in(client, KARI)
    assert [c["id"] for c in client.get("/api/v1/companies").json()] == [company["id"]]


def test_a_key_may_not_delete_a_company(client: TestClient) -> None:
    company = books_of(client)
    header = agent_header(client, company["id"])
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/sletting",
        json={"password": PASSWORD},
        headers=header,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "not_for_agent_keys"


def test_the_name_says_which_company_and_a_typo_is_not_a_wrong_password(
    client: TestClient, database: sa.Engine
) -> None:
    company = books_of(client)

    wrong = close(client, company["id"], name="Nordmann Holding AS")
    assert wrong.status_code == 422
    assert wrong.json()["code"] == "company_name_mismatch"
    assert "Nordmann Consulting AS" in wrong.json()["detail"]
    # Checked before the password, so it costs nothing towards the lockout.
    assert (
        scalar(database, "SELECT failed_login_attempts FROM app_user WHERE email = :e", e=KARI) == 0
    )
    # Case and spacing are not the question.
    assert close(client, company["id"], name="  nordmann   consulting as ").status_code == 200


def test_an_account_without_a_password_confirms_by_signing_in_again(
    client: TestClient, database: sa.Engine
) -> None:
    """Signed up with BankID or Google: nothing to type, so a fresh sign-in is the proof."""
    company = books_of(client)
    record(client, company["id"], type_code="varekjop", gross_amount="100.00")
    run(database, "UPDATE app_user SET password_hash = NULL WHERE email = :e", e=KARI)
    run(database, "UPDATE user_session SET created_at = :t", t=utc_now() - timedelta(hours=1))

    plan = plan_of(client, company["id"]).json()
    assert plan["requires_password"] is False
    stale = close(client, company["id"], password=None)
    assert stale.status_code == 403
    assert stale.json()["code"] == "reauthentication_required"

    # What «Bekreft med BankID» or «Bekreft med Google» amounts to: a new sign-in.
    run(database, "UPDATE user_session SET created_at = :t", t=utc_now())
    assert close(client, company["id"], password=None).status_code == 200


def test_signing_in_with_google_again_is_enough_even_with_a_password(
    client: TestClient, database: sa.Engine
) -> None:
    """A person who signs in with Google and once set a password is not asked for it.

    Deleting a company can be undone, so a fresh sign-in is proof enough for
    every account. Deleting the account cannot, and still wants the password.
    """
    company = books_of(client)
    record(client, company["id"], type_code="varekjop", gross_amount="100.00")

    plan = plan_of(client, company["id"]).json()
    assert plan["requires_password"] is True
    assert plan["recent_login_until"] is not None
    # Signed in just now: no password needed.
    assert close(client, company["id"], password=None).status_code == 200

    # The account itself still wants it.
    erase = write(client, "POST", "/api/v1/account/erasure", json={"password": None})
    assert erase.status_code == 403
    assert erase.json()["code"] == "reauthentication_required"


def test_a_password_account_signed_in_long_ago_must_give_it_or_sign_in_again(
    client: TestClient, database: sa.Engine
) -> None:
    company = books_of(client)
    run(database, "UPDATE user_session SET created_at = :t", t=utc_now() - timedelta(hours=1))
    stale = close(client, company["id"], password=None)
    assert stale.status_code == 403
    assert stale.json()["code"] == "reauthentication_required"
    assert "logg inn på nytt" in stale.json()["detail"]
    assert close(client, company["id"]).status_code == 200


# --- Opening them again --------------------------------------------------------------


def test_an_owner_can_open_deleted_books_again(client: TestClient, database: sa.Engine) -> None:
    company = books_of(client)
    record(client, company["id"], type_code="varekjop", gross_amount="100.00")
    assert close(client, company["id"]).status_code == 200

    reopened = write(client, "POST", f"/api/v1/companies/slettede/{company['id']}/gjenapne")
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["id"] == company["id"]
    assert reopened.json()["role"] == "owner"

    assert [c["id"] for c in client.get("/api/v1/companies").json()] == [company["id"]]
    assert client.get(f"/api/v1/companies/{company['id']}/cashflow").status_code == 200
    assert client.get("/api/v1/companies/slettede").json() == []
    assert (
        scalar(database, "SELECT retain_until FROM company WHERE id = :c", c=company["id"]) is None
    )


def test_nobody_else_can_open_them_and_nobody_can_open_open_books(client: TestClient) -> None:
    company = books_of(client)
    record(client, company["id"], type_code="varekjop", gross_amount="100.00")
    # Open books are not deleted books: the same 404 as an id nobody holds.
    not_closed = write(client, "POST", f"/api/v1/companies/slettede/{company['id']}/gjenapne")
    assert not_closed.status_code == 404
    assert close(client, company["id"]).status_code == 200

    write(client, "POST", "/api/v1/auth/logout")
    register(client, PER, full_name="Per Hansen")
    stranger = write(client, "POST", f"/api/v1/companies/slettede/{company['id']}/gjenapne")
    assert stranger.status_code == 404
    assert client.get("/api/v1/companies/slettede").json() == []


def test_deleting_gives_up_the_confirmed_claim_on_the_orgnr(
    client: TestClient, database: sa.Engine, database_url: str
) -> None:
    """Whoever runs the business next confirms it without a warning about these books.

    And the deleted books still open again afterwards, still confirmed: a
    second confirmed claim on an orgnr is allowed, and nothing about the other
    account's confirmation makes these books' Altinn proof any less true.
    """
    company = books_of(client)
    record(client, company["id"], type_code="varekjop", gross_amount="100.00")
    run(
        database,
        "UPDATE company SET verified_at = :t WHERE id = :c",
        t=utc_now(),
        c=company["id"],
    )
    assert close(client, company["id"]).status_code == 200

    # Another account asks about the same orgnr: the deleted books do not count
    # as somebody else's claim, so nobody is warned about them.
    write(client, "POST", "/api/v1/auth/logout")
    theirs = books_of(client, PER)
    per = scalar(database, "SELECT id FROM app_user WHERE email = :e", e=PER)
    assert claimed_elsewhere(database_url, company["orgnr"], user_id=per) == set()
    run(
        database,
        "UPDATE company SET verified_at = :t WHERE id = :c",
        t=utc_now(),
        c=theirs["id"],
    )

    sign_in(client, KARI)
    reopened = write(client, "POST", f"/api/v1/companies/slettede/{company['id']}/gjenapne")
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["verified"] is True
    assert client.get("/api/v1/companies/slettede").json() == []
    # Open again, the claim counts again, from where Per stands.
    assert claimed_elsewhere(database_url, company["orgnr"], user_id=per) == {company["orgnr"]}


def test_the_same_orgnr_can_be_set_up_again_while_the_deleted_books_are_kept(
    client: TestClient, database: sa.Engine
) -> None:
    """Deleting to start over must not stop you for five years.

    Reported with a test company on a made-up orgnr: deleted, then refused when
    set up again. The old books stay locked beside the new ones; it is opening
    them again that has to wait until the new ones are gone.
    """
    company = books_of(client)
    record(client, company["id"], type_code="varekjop", gross_amount="100.00")
    assert close(client, company["id"]).status_code == 200

    def set_up() -> Any:
        return write(
            client,
            "POST",
            "/api/v1/companies",
            json={
                "organization_id": company["organization_id"],
                "orgnr": company["orgnr"],
                "name": "Nordmann Consulting AS",
                "first_financial_year": THIS_YEAR,
            },
        )

    again = set_up()
    assert again.status_code == 201, again.text
    fresh = again.json()
    assert fresh["id"] != company["id"]
    assert [c["id"] for c in client.get("/api/v1/companies").json()] == [fresh["id"]]
    # Nothing of the old books in the new ones.
    assert client.get(f"/api/v1/companies/{fresh['id']}/cashflow").json() == []
    assert (
        scalar(database, "SELECT retain_until FROM company WHERE id = :c", c=company["id"])
        == KEPT_UNTIL.isoformat()
    )

    # Two open sets for one orgnr is still one too many.
    twice = set_up()
    assert twice.status_code == 409
    assert twice.json()["code"] == "duplicate_company"

    # And the old ones cannot be opened over the new ones.
    blocked = write(client, "POST", f"/api/v1/companies/slettede/{company['id']}/gjenapne")
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "reopen_blocked"
    assert "nytt regnskap" in blocked.json()["detail"]

    # Delete the new ones (empty, so gone at once), and the old ones open.
    assert close(client, fresh["id"]).status_code == 200
    reopened = write(client, "POST", f"/api/v1/companies/slettede/{company['id']}/gjenapne")
    assert reopened.status_code == 200, reopened.text


# --- The account ---------------------------------------------------------------------


def test_deleted_books_do_not_stop_their_owner_deleting_the_account(
    client: TestClient, database: sa.Engine
) -> None:
    company = books_of(client)
    record(client, company["id"], type_code="varekjop", gross_amount="100.00")
    assert close(client, company["id"]).status_code == 200

    plan = client.get("/api/v1/account/erasure").json()
    assert plan["blocked"] is False
    assert [(c["company_id"], c["fate"]) for c in plan["companies"]] == [(company["id"], "closed")]
    erased = write(client, "POST", "/api/v1/account/erasure", json={"password": PASSWORD})
    assert erased.status_code == 204

    # The books stay until their retention ends, whoever owned them.
    assert (
        scalar(database, "SELECT retain_until FROM company WHERE id = :c", c=company["id"])
        == KEPT_UNTIL.isoformat()
    )
    assert scalar(database, "SELECT count(*) FROM gl_transaction") == 1


# --- The end of the five years -------------------------------------------------------


def test_the_purge_deletes_the_books_and_their_files_the_day_after(
    client: TestClient, database: sa.Engine, database_url: str, tmp_path: Path
) -> None:
    session = register(client, KARI, full_name="Kari Nordmann")
    company = make_company(client, session["memberships"][0]["organization_id"])
    # An invoice and its kreditnota, a payment with a receipt: every RESTRICT
    # inside the books (an invoice's voucher, a kreditnota's invoice, a line's
    # account and party) has to be undone in the right order.
    invoice = issue(client, company["id"], create_draft(client, company["id"])["id"]).json()
    credit = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/faktura/{invoice['id']}/kreditnota",
        json={},
    )
    assert credit.status_code == 201, credit.text
    entry = record(client, company["id"], type_code="varekjop", gross_amount="100.00").json()
    assert (
        upload(client, company["id"], entry["id"], PDF_BYTES, "kvittering.pdf").status_code == 201
    )
    storage_root = tmp_path / "bilag"
    files_before = [path for path in storage_root.rglob("*") if path.is_file()]
    assert files_before

    assert close(client, company["id"]).status_code == 200

    # On the last day it is kept, nothing happens.
    assert purge(database_url, storage_root, KEPT_UNTIL) == []
    assert scalar(database, "SELECT count(*) FROM company WHERE id = :c", c=company["id"]) == 1

    # The day after, everything goes: the rows and the stored files.
    assert purge(database_url, storage_root, KEPT_UNTIL + timedelta(days=1)) == [company["id"]]
    assert scalar(database, "SELECT count(*) FROM company WHERE id = :c", c=company["id"]) == 0
    for table in ("gl_transaction", "sales_document", "party", "bilag_attachment", "account"):
        # The table names are the literals above, never input.
        assert scalar(database, f"SELECT count(*) FROM {table}") == 0  # noqa: S608
    assert [path for path in storage_root.rglob("*") if path.is_file()] == []
    # Kari's account is hers and stays; only the books are gone.
    assert scalar(database, "SELECT count(*) FROM organization") == 1
    assert client.get("/api/v1/companies/slettede").json() == []


def test_the_purge_leaves_open_books_alone_however_old(
    client: TestClient, database: sa.Engine, database_url: str, tmp_path: Path
) -> None:
    company = books_of(client)
    record(client, company["id"], type_code="varekjop", gross_amount="100.00")
    assert purge(database_url, tmp_path / "bilag", date(THIS_YEAR + 50, 1, 1)) == []
    assert scalar(database, "SELECT count(*) FROM company WHERE id = :c", c=company["id"]) == 1
