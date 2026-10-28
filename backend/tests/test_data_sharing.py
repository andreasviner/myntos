"""What a company lets Myntos use to improve Myntos (docs/data-sharing.md).

Nothing by default, for every company. Changed only by the people responsible
for the books, never by a key, logged with who changed it, and back to nothing
when the company is deleted.
"""

from collections.abc import Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from myntos.domain.data_sharing.models import DataSharingLevel
from myntos.domain.data_sharing.service import allows
from myntos.domain.ledger.models import Company
from tests.conftest import PASSWORD, agent_header, join_company, register, write
from tests.test_cashflow_api import create_company, record

KARI = "kari@example.no"
OLA = "ola@example.no"


@pytest.fixture
def database(database_url: str) -> Iterator[sa.Engine]:
    engine = sa.create_engine(database_url.replace("+aiosqlite", ""))
    yield engine
    engine.dispose()


def log(database: sa.Engine, company_id: int) -> list[tuple[str, int | None]]:
    with database.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT level, chosen_by_user_id FROM data_sharing_choice"
                " WHERE company_id = :c ORDER BY id"
            ),
            {"c": company_id},
        )
        return [(row.level, row.chosen_by_user_id) for row in rows]


def books_of(client: TestClient) -> dict[str, Any]:
    session = register(client, KARI, full_name="Kari Nordmann")
    return create_company(client, session["memberships"][0]["organization_id"])


def share(client: TestClient, company_id: int, level: str, **kwargs: Any) -> Any:
    return write(
        client,
        "PUT",
        f"/api/v1/companies/{company_id}/datadeling",
        json={"level": level},
        **kwargs,
    )


# --- The levels ---------------------------------------------------------------------


def test_nothing_allows_nothing_and_each_level_covers_the_ones_below() -> None:
    company = Company(data_sharing="none")
    assert not any(allows(company, level) for level in DataSharingLevel)

    company.data_sharing = "anonymised"
    assert allows(company, DataSharingLevel.USAGE)
    assert allows(company, DataSharingLevel.ANONYMISED)
    assert not allows(company, DataSharingLevel.EVERYTHING)
    assert not allows(company, DataSharingLevel.NONE)


def test_a_level_the_code_no_longer_knows_reads_as_nothing() -> None:
    """A yes covers what was shown, so a withdrawn level is never read as a higher one."""
    company = Company(data_sharing="something-retired")
    assert not allows(company, DataSharingLevel.USAGE)


# --- Choosing ----------------------------------------------------------------------


def test_every_company_starts_with_nothing(client: TestClient, database: sa.Engine) -> None:
    company = books_of(client)
    assert company["data_sharing"] == "none"
    assert client.get(f"/api/v1/companies/{company['id']}").json()["data_sharing"] == "none"
    assert log(database, company["id"]) == []


def test_an_owner_chooses_and_every_change_is_logged_with_who(
    client: TestClient, database: sa.Engine
) -> None:
    company = books_of(client)
    me = client.get("/api/v1/auth/me").json()["user"]["id"]

    response = share(client, company["id"], "everything")
    assert response.status_code == 200, response.text
    assert response.json()["data_sharing"] == "everything"
    # The same level again is not a second decision.
    assert share(client, company["id"], "everything").status_code == 200
    assert share(client, company["id"], "usage").json()["data_sharing"] == "usage"

    assert client.get(f"/api/v1/companies/{company['id']}").json()["data_sharing"] == "usage"
    assert log(database, company["id"]) == [("everything", me), ("usage", me)]


def test_an_admin_may_choose_and_a_member_may_only_see(client: TestClient) -> None:
    company = books_of(client)
    join_company(client, company["id"], email=OLA, role="admin")
    assert share(client, company["id"], "usage").json()["data_sharing"] == "usage"

    # Ola, an admin, invites Per as an ordinary member.
    join_company(client, company["id"], email="per@example.no", role="member")
    refused = share(client, company["id"], "everything")
    assert refused.status_code == 403
    assert client.get(f"/api/v1/companies/{company['id']}").json()["data_sharing"] == "usage"


def test_an_unknown_level_is_refused(client: TestClient) -> None:
    company = books_of(client)
    assert share(client, company["id"], "some").status_code == 422
    assert client.get(f"/api/v1/companies/{company['id']}").json()["data_sharing"] == "none"


def test_no_key_may_choose_whatever_its_scopes(client: TestClient) -> None:
    company = books_of(client)
    header = agent_header(client, company["id"])
    response = share(client, company["id"], "everything", headers=header)
    assert response.status_code == 403
    assert response.json()["code"] == "not_for_agent_keys"
    # A key may read it, with the rest of the company.
    seen = client.get(f"/api/v1/companies/{company['id']}", headers=header)
    assert seen.json()["data_sharing"] == "none"


def test_another_companys_level_is_not_found(client: TestClient) -> None:
    company = books_of(client)
    write(client, "POST", "/api/v1/auth/logout")
    register(client, OLA, full_name="Ola Nordmann")
    assert share(client, company["id"], "everything").status_code == 404


# --- Deleting the company, and the person ----------------------------------------------


def test_deleting_the_company_shares_nothing_and_opening_it_again_does_not_resume(
    client: TestClient, database: sa.Engine
) -> None:
    company = books_of(client)
    me = client.get("/api/v1/auth/me").json()["user"]["id"]
    # Entries, so the books are kept rather than deleted at once.
    record(client, company["id"], type_code="varekjop", gross_amount="100.00")
    assert share(client, company["id"], "anonymised").status_code == 200

    closed = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/sletting",
        json={"password": PASSWORD, "company_name": "Nordmann Consulting AS"},
    )
    assert closed.status_code == 200, closed.text
    assert log(database, company["id"]) == [("anonymised", me), ("none", me)]

    reopened = write(client, "POST", f"/api/v1/companies/slettede/{company['id']}/gjenapne")
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["data_sharing"] == "none"


def test_a_person_who_chose_to_share_leaves_their_name(client: TestClient) -> None:
    """Myntos keeps what it was given on that choice, so the choice keeps who made it."""
    company = books_of(client)
    assert client.get("/api/v1/account/erasure").json()["keeps_name"] is False

    assert share(client, company["id"], "usage").status_code == 200
    assert client.get("/api/v1/account/erasure").json()["keeps_name"] is True
