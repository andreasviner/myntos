"""Who on the board can open the books, over HTTP.

:mod:`tests.test_register_match` covers the rule itself, which is pure. These
are about the two things only the endpoint can get wrong: that the mark is
*resolved on every read* rather than frozen into the stored register snapshot,
and that an ID-porten login is what turns a guess into an answer.

The freezing question is the one worth a test rather than a comment. The company
stores Brønnøysund's answer verbatim, and access is not part of that answer: it
changes when somebody is invited, accepts, or links a login, none of which the
register hears about. A mark written into the snapshot would be right on the day
it was stored and quietly wrong every day after.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from myntos.api.v1 import skatt as skatt_endpoints
from myntos.auth.idporten.client import GovernmentLogin
from myntos.core.config import get_settings
from myntos.integrations import brreg
from tests.conftest import agent_header, register, write

# The ID-porten stub is shared with the tests that own the login flow, so a
# change to what a login returns reaches both. Only the fixture wiring is
# local, because importing a fixture *and* naming it in a signature is a
# redefinition.
from tests.test_skatt_api import KEY, StubIdPorten

ORGNR = "923609016"

UNIT: dict[str, Any] = {
    "organisasjonsnummer": ORGNR,
    "navn": "FJORD REGNSKAP AS",
    "organisasjonsform": {"kode": "AS", "beskrivelse": "Aksjeselskap"},
    "registrertIMvaregisteret": True,
}

# One board, three shapes: somebody who signed up, somebody who has not, and a
# firm. The middle names are the point: the register spells them out and people
# do not.
ROLES: dict[str, Any] = {
    "rollegrupper": [
        {
            "type": {"kode": "STYR", "beskrivelse": "Styre"},
            "roller": [
                {
                    "type": {"kode": "LEDE", "beskrivelse": "Styrets leder"},
                    "person": {
                        "navn": {"fornavn": "Kari", "mellomnavn": "Berit", "etternavn": "Nordmann"}
                    },
                    "avregistrert": False,
                },
                {
                    "type": {"kode": "MEDL", "beskrivelse": "Styremedlem"},
                    "person": {
                        "navn": {
                            "fornavn": "Andreas",
                            "mellomnavn": "Edvardsen",
                            "etternavn": "Lindeman",
                        }
                    },
                    "avregistrert": False,
                },
            ],
        },
        {
            "type": {"kode": "REVI", "beskrivelse": "Revisor"},
            "roller": [
                {
                    "type": {"kode": "REVI", "beskrivelse": "Revisor"},
                    "enhet": {"organisasjonsnummer": "976389387", "navn": "ERNST & YOUNG AS"},
                    "avregistrert": False,
                }
            ],
        },
    ]
}


@pytest.fixture
def idporten(monkeypatch: pytest.MonkeyPatch) -> Iterator[StubIdPorten]:
    """A configured government login, with ID-porten itself stubbed."""
    monkeypatch.setenv("GOVERNMENT_TOKEN_KEY", KEY)
    get_settings.cache_clear()
    stub = StubIdPorten()
    monkeypatch.setattr(skatt_endpoints, "get_client", lambda: stub)
    monkeypatch.setattr(skatt_endpoints, "is_configured", lambda *a, **k: True)
    yield stub
    get_settings.cache_clear()


@pytest.fixture
def brreg_says(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A register that answers with UNIT and ROLES, and never reaches the network."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/roller"):
            return httpx.Response(200, json=ROLES)
        return httpx.Response(200, json=UNIT)

    monkeypatch.setattr(
        brreg,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    yield


def _with_name(login: GovernmentLogin, name: str) -> GovernmentLogin:
    """A copy of the stub's login carrying ``name``.

    ``GovernmentLogin`` is frozen, and the shared fixture builds one without a
    name because most of its own tests do not care whether there is one.
    """
    return dataclasses.replace(login, name=name)


def _company(client: TestClient, session: dict[str, Any]) -> int:
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": session["memberships"][0]["organization_id"],
            "orgnr": ORGNR,
            "name": "Fjord Regnskap AS",
            "first_financial_year": 2026,
        },
    )
    assert response.status_code == 201, response.text
    company_id: int = response.json()["id"]
    return company_id


def _roles(client: TestClient, company_id: int) -> dict[str, dict[str, Any]]:
    """The stored register's role list, keyed by name."""
    response = client.get(f"/api/v1/companies/{company_id}/register")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body is not None
    return {role["name"]: role for role in body["roles"]}


def _fetch(client: TestClient, company_id: int) -> dict[str, dict[str, Any]]:
    """Ask Brønnøysund again, and read the answer that was stored."""
    response = write(client, "POST", f"/api/v1/companies/{company_id}/hent")
    assert response.status_code == 200, response.text
    return {role["name"]: role for role in response.json()["roles"]}


def test_an_account_name_that_matches_the_board_is_only_probable(
    client: TestClient, brreg_says: None
) -> None:
    """The name typed at signup is probably the Kari Berit on the board."""
    session = register(client, "kari@example.no", full_name="Kari Nordmann")
    company_id = _company(client, session)
    _fetch(client, company_id)

    roles = _roles(client, company_id)
    kari = roles["Kari Berit Nordmann"]
    assert kari["access"] == "trolig"
    assert kari["access_role"] == "owner"
    # The name that was matched, because it is not the one on screen.
    assert kari["access_name"] == "Kari Nordmann"


def test_a_board_member_nobody_invited_is_reported_as_having_no_access(
    client: TestClient, brreg_says: None
) -> None:
    session = register(client, "kari@example.no", full_name="Kari Nordmann")
    company_id = _company(client, session)
    _fetch(client, company_id)

    andreas = _roles(client, company_id)["Andreas Edvardsen Lindeman"]
    assert andreas["access"] == "ingen"
    assert andreas["access_role"] is None


def test_the_revisor_is_a_company_and_gets_no_verdict(client: TestClient, brreg_says: None) -> None:
    """Marking an auditing firm "ingen tilgang" answers a question nobody asked."""
    session = register(client, "kari@example.no", full_name="Kari Nordmann")
    company_id = _company(client, session)
    _fetch(client, company_id)

    assert _roles(client, company_id)["ERNST & YOUNG AS"]["access"] == "selskap"


def test_inviting_a_board_member_by_name_shows_up_beside_their_role(
    client: TestClient, brreg_says: None
) -> None:
    """And it shows up without asking Brønnøysund again.

    This is the freezing question: the register said nothing new, the snapshot
    is untouched, and the answer still has to change.
    """
    session = register(client, "kari@example.no", full_name="Kari Nordmann")
    company_id = _company(client, session)
    _fetch(client, company_id)
    assert _roles(client, company_id)["Andreas Edvardsen Lindeman"]["access"] == "ingen"

    invited = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/invitations",
        json={
            "email": "andreas@example.no",
            "role": "admin",
            "full_name": "Andreas Edvardsen Lindeman",
        },
    )
    assert invited.status_code == 201, invited.text

    andreas = _roles(client, company_id)["Andreas Edvardsen Lindeman"]
    assert andreas["access"] == "invitert"
    assert andreas["access_role"] == "admin"
    assert andreas["access_email"] == "andreas@example.no"


def test_a_viewer_sees_the_mark_but_not_the_invited_address(
    client: TestClient, brreg_says: None
) -> None:
    """This list is readable by everyone; the invitation list is not.

    A viewer holds `member:read` and not `member:manage`, so they cannot list
    pending invitations, and answering with an invited address here would be a
    way round that. The state stays: "this board member has been asked" is a
    fact about the board, and it is the useful half.
    """
    session = register(client, "kari@example.no", full_name="Kari Nordmann")
    company_id = _company(client, session)
    _fetch(client, company_id)

    joining = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/invitations",
        json={"email": "leser@example.no", "role": "viewer"},
    )
    write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/invitations",
        json={
            "email": "andreas@example.no",
            "role": "admin",
            "full_name": "Andreas Edvardsen Lindeman",
        },
    )

    viewer = TestClient(client.app, base_url=str(client.base_url))
    register(viewer, "leser@example.no", full_name="Leser Lesersen")
    token = joining.json()["invite_url"].split("token=", maxsplit=1)[1]
    assert (
        write(viewer, "POST", "/api/v1/auth/invitations/accept", json={"token": token})
    ).status_code == 200
    assert viewer.get(f"/api/v1/companies/{company_id}/invitations").status_code == 403

    andreas = _roles(viewer, company_id)["Andreas Edvardsen Lindeman"]
    assert andreas["access"] == "invitert"
    assert andreas["access_email"] is None


def test_accepting_the_invitation_turns_asked_into_a_member(
    client: TestClient, brreg_says: None
) -> None:
    session = register(client, "kari@example.no", full_name="Kari Nordmann")
    company_id = _company(client, session)
    _fetch(client, company_id)

    invited = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/invitations",
        json={
            "email": "andreas@example.no",
            "role": "admin",
            "full_name": "Andreas Edvardsen Lindeman",
        },
    )
    token = invited.json()["invite_url"].split("token=", maxsplit=1)[1]

    colleague = TestClient(client.app, base_url=str(client.base_url))
    register(colleague, "andreas@example.no", full_name="Andreas Lindeman")
    assert (
        write(colleague, "POST", "/api/v1/auth/invitations/accept", json={"token": token})
    ).status_code == 200

    andreas = _roles(client, company_id)["Andreas Edvardsen Lindeman"]
    assert andreas["access"] == "trolig"
    assert andreas["access_role"] == "admin"


def test_an_id_porten_login_turns_a_guess_into_an_answer(
    client: TestClient, brreg_says: None, idporten: StubIdPorten
) -> None:
    """The whole reason linking is worth offering.

    Before the login the board's chair matches an account by a name somebody
    typed. After it, the same person matches a name Folkeregisteret asserted,
    the register's own source, and the mark says so.
    """
    session = register(client, "kari@example.no", full_name="Kari Nordmann")
    company_id = _company(client, session)
    _fetch(client, company_id)
    assert _roles(client, company_id)["Kari Berit Nordmann"]["access"] == "trolig"

    idporten.login = _with_name(idporten.login, "Kari Berit Nordmann")
    started = client.get("/api/v1/skatt/idporten/start", follow_redirects=False)
    state = str(started.headers["location"]).split("state=")[1]
    done = client.get(
        f"/api/v1/skatt/idporten/callback?code=auth-code&state={state}",
        follow_redirects=False,
    )
    assert done.status_code == 303, done.text

    kari = _roles(client, company_id)["Kari Berit Nordmann"]
    assert kari["access"] == "bekreftet"
    # Word for word, so there is no second name to show.
    assert kari["access_name"] is None

    # And the member list says the same thing about the same person.
    member = client.get(f"/api/v1/companies/{company_id}/members").json()[0]
    assert member["government_linked"] is True
    assert member["government_name"] == "Kari Berit Nordmann"
    assert member["government_verified_at"] is not None


def test_a_login_that_carries_no_name_still_links(
    client: TestClient, brreg_says: None, idporten: StubIdPorten
) -> None:
    """A deployment without the ``profile`` scope gets a link, just not a name.

    Which has to stay a supported state: the scope is ticked onto a client in
    Samarbeidsportalen, so whether a name arrives is a fact about the
    deployment rather than about the person.
    """
    session = register(client, "kari@example.no", full_name="Kari Nordmann")
    company_id = _company(client, session)
    _fetch(client, company_id)

    started = client.get("/api/v1/skatt/idporten/start", follow_redirects=False)
    state = str(started.headers["location"]).split("state=")[1]
    client.get(
        f"/api/v1/skatt/idporten/callback?code=auth-code&state={state}",
        follow_redirects=False,
    )

    status = client.get("/api/v1/skatt/status").json()
    assert status["linked"] is True
    assert status["linked_name"] is None
    # And with no verified name to match on, the mark falls back to the account's.
    assert _roles(client, company_id)["Kari Berit Nordmann"]["access"] == "trolig"


def test_the_access_mark_is_never_written_into_the_stored_snapshot(
    client: TestClient, brreg_says: None, database_url: str
) -> None:
    """The assertion behind RoleAccessResponse being its own model.

    A mark in the snapshot would be served back weeks later as though
    Brønnøysund had said it.
    """
    import json

    import sqlalchemy as sa

    session = register(client, "kari@example.no", full_name="Kari Nordmann")
    company_id = _company(client, session)
    _fetch(client, company_id)

    engine = sa.create_engine(database_url.replace("sqlite+aiosqlite", "sqlite"))
    with engine.connect() as connection:
        stored = connection.execute(
            sa.text("SELECT roles FROM company_register_snapshot WHERE company_id = :id"),
            {"id": company_id},
        ).scalar_one()
    engine.dispose()
    roles = json.loads(stored) if isinstance(stored, str) else stored
    assert roles, "the snapshot should hold the register's answer"
    for role in roles:
        assert "access" not in role


def test_a_key_fetching_the_register_is_not_told_who_has_access(
    client: TestClient, brreg_says: None
) -> None:
    """Who has access is never a key's to read (agents-and-api.md rule 5).

    ``GET …/register`` is closed to keys for that reason, and ``POST …/hent``
    answers with the same board list, so a key gets it without the marks: an
    empty list, and the public board from Enhetsregisteret's own route.
    """
    session = register(client, "kari@example.no", full_name="Kari Nordmann")
    company_id = _company(client, session)
    header = agent_header(client, company_id)

    response = client.post(f"/api/v1/companies/{company_id}/hent", headers=header)

    assert response.status_code == 200, response.text
    assert response.json()["roles"] == []
    # Signed in, the marks are there.
    assert _fetch(client, company_id)["Kari Berit Nordmann"]["access"] == "trolig"
