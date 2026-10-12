"""The wall between two companies, asserted route by route.

Every other test here checks that something *works*. These check that something
cannot happen: that no part of the API hands one organization anything belonging
to another's set of books — not a figure, not an invoice, not so much as a name.

The list of routes is read out of the application rather than written down. That
is the whole point. A hand-written list is a list of the routes somebody
remembered on the day, and the route that leaks will be the one added six months
later by someone who had no reason to think about this file. Reading the API's
own description means a new company-scoped endpoint is covered the moment it
exists, and one that forgets its scoping fails here rather than in production.

The mechanism being locked in is :func:`myntos.api.deps.get_company_context`,
which resolves ``{company_id}`` through a join on the caller's memberships and
raises ``CompanyNotFoundError`` — 404, the same answer as a company that does
not exist — for anything else. 404 and not 403 is deliberate: 403 would confirm
the id is real, turning the API into a way to enumerate other people's books.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from myntos.main import create_app
from tests.conftest import agent_header, assert_key_refused, register, write

# Two real orgnrs, so the check digits hold and the creations succeed.
MINE = "910000012"
THEIRS = "915933149"


def _company(client: TestClient, session: dict[str, Any], orgnr: str) -> dict[str, Any]:
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


def _company_scoped_routes() -> list[tuple[str, str]]:
    """Every (method, path) the API exposes under a company id.

    Read out of the generated OpenAPI schema rather than off ``app.routes``:
    the routers are mounted inside one another, so the attribute holds mount
    points while the schema holds the flattened truth. It is also the same
    description of the API a client would work from, which is the right thing
    to hold to this rule.
    """
    schema = create_app().openapi()
    found = [
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        if "{company_id}" in path
        for method in sorted(operations)
        if method in {"get", "post", "patch", "put", "delete"}
    ]
    assert found, "no company-scoped routes found — has the path parameter been renamed?"
    return found


def test_there_are_company_scoped_routes_to_check() -> None:
    """A guard on the guard.

    If ``{company_id}`` is ever renamed, every test below would pass by
    checking nothing at all, silently and for as long as it took somebody to
    notice. This is what notices.
    """
    assert len(_company_scoped_routes()) > 15


@pytest.mark.parametrize(("method", "path"), _company_scoped_routes())
def test_no_route_answers_for_another_organizations_company(
    client: TestClient, method: str, path: str
) -> None:
    """A company in somebody else's organization is a 404 on every route.

    Both accounts are real, both keep real books, and the second asks about the
    first's company by id — which is the shape an accident takes: not an attack,
    but a stale id in a bookmark, a copied link, a switched company that a
    request had already been built for.

    Never a 2xx is the assertion that matters; 404 rather than 403 is the one
    that keeps ids unguessable.
    """
    first = register(client)
    theirs = _company(client, first, MINE)

    # A different person, a different organization, with books of their own —
    # so a refusal cannot be explained away by the caller having none.
    second = register(client, "per@example.no", full_name="Per Hansen")
    _company(client, second, THEIRS)

    url = path.replace("{company_id}", str(theirs["id"]))
    assert url != path, f"no company id substituted in {path}"
    # Every other path parameter becomes 1, whatever it is called. Filling them
    # from a list of names was the first version of this, and the list went
    # stale within the hour — which is the same failure the whole file exists to
    # avoid. What they name is never looked up anyway: the company check runs
    # first, and this test is about it refusing before anything else happens.
    url = re.sub(r"\{[^}]+\}", "1", url)

    response = write(client, method, url, json={})

    assert response.status_code == 404, (
        f"{method} {path} answered {response.status_code} for another organization's company: "
        f"{response.text[:200]}"
    )


@pytest.mark.parametrize(("method", "path"), _company_scoped_routes())
def test_no_route_answers_a_key_for_another_organizations_company(
    client: TestClient, method: str, path: str
) -> None:
    """The same wall, with a key instead of a session (docs/agents-and-api.md rule 9).

    The second person makes a key for their own books and their agent asks
    about the first person's company. A key is checked by a gate of its own
    before the membership join runs, so this is a second wall and not the
    first one tested twice: either could be removed and the other would still
    have to hold.
    """
    first = register(client)
    theirs = _company(client, first, MINE)

    second = register(client, "per@example.no", full_name="Per Hansen")
    own = _company(client, second, THEIRS)
    header = agent_header(client, own["id"])

    url = re.sub(r"\{[^}]+\}", "1", path.replace("{company_id}", str(theirs["id"])))
    response = write(client, method, url, json={}, headers=header)

    assert_key_refused(response, method, path)


def test_the_company_list_shows_only_your_own(client: TestClient) -> None:
    """The listing is the other half of the wall.

    Scoping every by-id route would still leak if the collection that hands out
    the ids were unscoped.
    """
    first = register(client)
    mine = _company(client, first, MINE)

    second = register(client, "per@example.no", full_name="Per Hansen")
    theirs = _company(client, second, THEIRS)

    listed = client.get("/api/v1/companies").json()
    assert [item["id"] for item in listed] == [theirs["id"]]
    assert all(item["orgnr"] != MINE for item in listed)
    assert all(item["name"] != mine["name"] or item["id"] == theirs["id"] for item in listed)


def test_a_name_is_not_readable_across_the_wall(client: TestClient) -> None:
    """Named explicitly, because a name is the leak nobody thinks to test for.

    Figures are obviously private. A company *name* feels like public
    information — it is, in Enhetsregisteret — but which names sit inside a
    given Myntos account is not, and neither is the fact that an account keeps
    books for a particular business.
    """
    first = register(client)
    theirs = _company(client, first, MINE)

    register(client, "per@example.no", full_name="Per Hansen")

    for url in (
        f"/api/v1/companies/{theirs['id']}",
        f"/api/v1/companies/{theirs['id']}/register",
    ):
        response = client.get(url)
        assert response.status_code == 404, url
        assert "Nordmann" not in response.text
