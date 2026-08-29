"""Confirming, through Altinn, that a set of books belongs to somebody.

Creating a company proves nothing: anyone can type nine digits into a form. The
confirmation is what turns it into a claim, and the rule it establishes is the
one these tests exist to hold — **one confirmed claim per orgnr across the
installation**, while unconfirmed books stay as unconstrained as they were.

Both halves of that matter, and they pull in opposite directions:

- Without the exclusivity, "this company already has a regnskap" could never be
  said, and two organizations would each believe they were the ones filing.
- Without the permissiveness, a regnskapsfører could not keep books for a client
  whose own confirmation sits elsewhere — which is most clients.

The stubs come from :mod:`tests.test_altinn_authorized_parties` rather than
being copied: they belong next to the ``ALTINN_PARTIES`` fixture they answer
with, and a second copy would drift from it silently.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import register, write
from tests.test_altinn_authorized_parties import (  # noqa: F401  (altinn is a fixture)
    StubAltinn,
    _sign_in_to_the_government,
    altinn,
)

# Two companies the stubbed person may act for, and one they may not. These are
# check-digit-valid organisasjonsnumre, unlike the ones in ALTINN_PARTIES: those
# tests only ever read the list, while these create the books it describes, and
# a set of books is refused before the orgnr can be confirmed.
MINE = "910000012"
SUBUNIT = "910000004"
NOT_MINE = "915933149"

# The same shape Altinn answers with: a main unit holding its subunit, because
# a subunit is reported for in its own right and has to survive the round trip.
PARTIES: list[dict[str, Any]] = [
    {
        "partyUuid": "00000000-0000-0000-0000-0000000000a1",
        "name": "Nordmann Consulting AS",
        "organizationNumber": MINE,
        "partyId": 50002001,
        "type": "Organization",
        "unitType": "AS",
        "isDeleted": False,
        "onlyHierarchyElementWithNoAccess": False,
        "authorizedRoles": ["DAGL", "REGN"],
        "subunits": [
            {
                "partyUuid": "00000000-0000-0000-0000-0000000000a2",
                "name": "Nordmann Consulting AS avd Bergen",
                "organizationNumber": SUBUNIT,
                "partyId": 50002002,
                "type": "Organization",
                "unitType": "BEDR",
                "isDeleted": False,
                "onlyHierarchyElementWithNoAccess": False,
                "authorizedRoles": ["REGN"],
                "subunits": [],
            }
        ],
    },
]


@pytest.fixture
def parties(altinn: StubAltinn) -> StubAltinn:  # noqa: F811  (the imported fixture)
    """The stubbed Altinn, answering with companies that can be created."""
    altinn.payload = PARTIES
    return altinn


def _create(client: TestClient, session: dict[str, Any], orgnr: str) -> dict[str, Any]:
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": session["memberships"][0]["organization_id"],
            "orgnr": orgnr,
            "name": "Nordmann Consulting AS",
            "first_financial_year": 2026,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _confirm(client: TestClient, company_id: int) -> Any:
    return write(client, "POST", f"/api/v1/companies/{company_id}/bekreft")


# --- The ordinary state ----------------------------------------------------------


def test_a_new_set_of_books_is_unconfirmed(client: TestClient) -> None:
    """Nine digits in a form is not evidence, and the API says so plainly.

    Not hidden and not degraded: an unconfirmed company books, invoices and
    reports exactly like a confirmed one. What is missing is an answer to whose
    company it is.
    """
    session = register(client)
    company = _create(client, session, MINE)
    assert company["verified"] is False
    assert company["verified_at"] is None


def test_confirming_without_a_government_login_is_a_409(
    client: TestClient, parties: StubAltinn
) -> None:
    """409, not 401: the caller is signed in to Myntos, just not to ID-porten.

    A 401 would send the frontend to its own sign-in page, where the user is
    already signed in and nothing would be explained.
    """
    session = register(client)
    company = _create(client, session, MINE)
    response = _confirm(client, company["id"])
    assert response.status_code == 409
    assert "ID-porten" in response.json()["detail"]


# --- The evidence ----------------------------------------------------------------


def test_confirming_records_when_it_happened(client: TestClient, parties: StubAltinn) -> None:
    """The happy path: Altinn lists the orgnr, so the claim stands."""
    session = register(client)
    _sign_in_to_the_government(client)
    company = _create(client, session, MINE)

    response = _confirm(client, company["id"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verified"] is True
    assert body["verified_at"] is not None
    # And it survives being read back, rather than living only in the answer to
    # the request that made it.
    assert client.get(f"/api/v1/companies/{company['id']}").json()["verified"] is True


def test_a_company_altinn_does_not_list_cannot_be_confirmed(
    client: TestClient, parties: StubAltinn
) -> None:
    """The whole point: the evidence is Altinn's, not the caller's assertion.

    403 rather than 404, because the company is one the caller can plainly see —
    what is missing is the right to act for it, and that is granted in Altinn.
    """
    session = register(client)
    _sign_in_to_the_government(client)
    company = _create(client, session, NOT_MINE)

    response = _confirm(client, company["id"])
    assert response.status_code == 403
    assert response.json()["code"] == "not_authorized_for_party"


def test_a_subunit_can_be_confirmed_in_its_own_right(
    client: TestClient, parties: StubAltinn
) -> None:
    """A department has its own orgnr and its own books.

    Altinn nests it under its main unit, so a confirmation that only looked at
    top-level parties would refuse exactly the entities an accountant is most
    often engaged for.
    """
    session = register(client)
    _sign_in_to_the_government(client)
    company = _create(client, session, SUBUNIT)
    assert _confirm(client, company["id"]).status_code == 200


def test_confirming_twice_is_not_an_error(client: TestClient, parties: StubAltinn) -> None:
    """The claim was already established; a second BankID is not a failure."""
    session = register(client)
    _sign_in_to_the_government(client)
    company = _create(client, session, MINE)

    first = _confirm(client, company["id"]).json()
    second = _confirm(client, company["id"])
    assert second.status_code == 200
    # The timestamp is the moment the claim was made, not the moment it was last
    # looked at, so re-confirming must not move it.
    assert second.json()["verified_at"] == first["verified_at"]


def test_confirming_does_not_spend_the_login(client: TestClient, parties: StubAltinn) -> None:
    """Same reasoning as the party list: reading who you may act for is not acting.

    Somebody confirming two clients should not go through BankID twice.
    """
    session = register(client)
    _sign_in_to_the_government(client)
    first = _create(client, session, MINE)
    second = _create(client, session, SUBUNIT)

    assert _confirm(client, first["id"]).status_code == 200
    assert _confirm(client, second["id"]).status_code == 200
    assert client.get("/api/v1/skatt/status").json()["logged_in"] is True


# --- One claim per orgnr ---------------------------------------------------------


def _second_account(client: TestClient) -> dict[str, Any]:
    """A different person, a different organization, the same Altinn rights.

    The stub answers the same list whoever asks, which is the case that matters:
    two people who both genuinely may act for a company, one of whom got there
    first.
    """
    session = register(client, "per@example.no", full_name="Per Hansen")
    _sign_in_to_the_government(client)
    return session


def test_a_second_organization_cannot_confirm_the_same_orgnr(
    client: TestClient, parties: StubAltinn
) -> None:
    """The refusal the whole feature exists to produce.

    409 with a code the frontend branches on, and prose that names the two ways
    forward. It does not name the organization holding the claim: the caller is
    not a member of it, and the way in is to ask them.
    """
    first = register(client)
    _sign_in_to_the_government(client)
    assert _confirm(client, _create(client, first, MINE)["id"]).status_code == 200

    second = _second_account(client)
    response = _confirm(client, _create(client, second, MINE)["id"])
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "company_already_claimed"
    assert "allerede et regnskap registrert" in body["detail"]
    # The prose is the whole answer, so it has to carry both ways out.
    assert "administrator" in body["detail"]


def test_the_second_organization_keeps_its_own_unconfirmed_books(
    client: TestClient, parties: StubAltinn
) -> None:
    """A refused confirmation must not take the books with it.

    This is the regnskapsfører case, and it is the reason the exclusivity is on
    the *claim* rather than on the orgnr: an accounting firm engaged for a
    client whose own confirmation sits elsewhere still has to be able to keep
    the books.
    """
    first = register(client)
    _sign_in_to_the_government(client)
    _confirm(client, _create(client, first, MINE)["id"])

    second = _second_account(client)
    company = _create(client, second, MINE)
    _confirm(client, company["id"])

    mine = client.get("/api/v1/companies").json()
    assert [item["id"] for item in mine] == [company["id"]]
    assert mine[0]["verified"] is False


def test_the_altinn_list_marks_what_somebody_else_has_claimed(
    client: TestClient, parties: StubAltinn
) -> None:
    """Said where the choice is made, not after a save that fails.

    Only the claimed one is marked: a list where everything looked contested
    would be as useless as one where nothing did.
    """
    first = register(client)
    _sign_in_to_the_government(client)
    _confirm(client, _create(client, first, MINE)["id"])

    _second_account(client)
    offered = client.get("/api/v1/skatt/altinn/authorized-parties").json()
    claimed = {party["orgnr"]: party["claimed"] for party in offered}
    assert claimed[MINE] is True
    assert claimed[SUBUNIT] is False


def test_your_own_claim_is_not_reported_as_somebody_else_s(
    client: TestClient, parties: StubAltinn
) -> None:
    """Claimed means claimed *elsewhere*.

    Flagging your own confirmation would tell you to go ask yourself for access.
    """
    session = register(client)
    _sign_in_to_the_government(client)
    _confirm(client, _create(client, session, MINE)["id"])

    offered = client.get("/api/v1/skatt/altinn/authorized-parties").json()
    assert all(party["claimed"] is False for party in offered)


# --- Who may do it ---------------------------------------------------------------


def test_another_organization_s_company_is_a_404(client: TestClient, parties: StubAltinn) -> None:
    """The same answer as a company that does not exist.

    Otherwise the endpoint becomes a way to discover which company ids are in
    use, one confirmation attempt at a time.
    """
    first = register(client)
    _sign_in_to_the_government(client)
    company = _create(client, first, MINE)

    _second_account(client)
    assert _confirm(client, company["id"]).status_code == 404
