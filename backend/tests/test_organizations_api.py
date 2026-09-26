"""The account, over HTTP: what it is called and who owns it.

An **organisasjon** is what a person signs up for, and it owns the companies
set up under it. That is now all it does. Who may open one company's books, who
was invited to it and what it costs are the company's, and their tests are in
``tests/test_company_members_api.py``.

What is left here is the account's own name, which nothing displays. It is
tested because it is still written (signup invents one), still reachable, and
because the rule that it is **never** taken from a company is the one that
stopped a settings page from looking like a data leak.
"""

from fastapi.testclient import TestClient

from tests.conftest import PASSWORD, TEST_ORIGIN, register, write


def _client_for(client: TestClient) -> TestClient:
    """A second browser against the same application and database."""
    return TestClient(client.app, base_url=TEST_ORIGIN)


def test_outsiders_get_404_not_403(client: TestClient) -> None:
    """A non-member must not learn whether an organization exists."""
    owner = register(client, "kari@example.no")
    organization_id = owner["memberships"][0]["organization_id"]

    outsider = _client_for(client)
    register(outsider, "fremmed@example.no")
    assert outsider.get(f"/api/v1/organizations/{organization_id}").status_code == 404
    assert outsider.get("/api/v1/organizations/999999").status_code == 404


def test_a_user_can_own_several_organizations(client: TestClient) -> None:
    """A regnskapsfører separating clients into their own organizations."""
    register(client, "kari@example.no", organization_name="Fjord Regnskap AS")
    created = write(client, "POST", "/api/v1/organizations", json={"name": "Klient Holding AS"})
    assert created.status_code == 201
    assert created.json()["role"] == "owner"

    organizations = client.get("/api/v1/organizations").json()
    assert {organization["name"] for organization in organizations} == {
        "Fjord Regnskap AS",
        "Klient Holding AS",
    }


def test_signup_without_a_name_marks_the_organization_as_a_placeholder(
    client: TestClient,
) -> None:
    """Signup does not insist on a name, and the account says so about itself."""
    register(client, "kari@example.no", full_name="Kari Nordmann")
    account = client.get("/api/v1/organizations").json()[0]
    assert account["name"] == "Kari Nordmann (privat)"
    assert account["name_is_placeholder"] is True


def test_a_name_given_at_signup_is_not_a_placeholder(client: TestClient) -> None:
    register(client, "kari@example.no", organization_name="Fjord Regnskap AS")
    assert client.get("/api/v1/organizations").json()[0]["name_is_placeholder"] is False


def test_setting_up_a_company_never_renames_the_account_after_it(
    client: TestClient,
) -> None:
    """The account is never called after one of its companies. Not even once.

    It used to be: an organization still carrying the name signup invented took
    over the name of the first company set up under it. That made an account
    routinely called after one of its own companies, and the first time such an
    account held two, the settings page showed one company's name on the
    account card while the header showed the other's. It was reported as data
    leaking between the two companies, which is exactly what it looked like.

    A provisional name that is obviously the account's own is worth more than a
    real one that is true of something else, so the placeholder stays until
    somebody names it.
    """
    session = register(client, "kari@example.no", full_name="Kari Nordmann")
    organization_id = session["memberships"][0]["organization_id"]

    created = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": organization_id,
            "orgnr": "910000004",
            "name": "Nordmann Consulting AS",
        },
    )
    assert created.status_code == 201, created.text

    account = client.get("/api/v1/organizations").json()[0]
    assert account["name"] == "Kari Nordmann (privat)"
    assert account["name_is_placeholder"] is True


def test_a_chosen_organization_name_survives_adding_companies(client: TestClient) -> None:
    """The regnskapsfører case: adding a client must not rename the firm."""
    session = register(client, "kari@example.no", organization_name="Fjord Regnskap AS")
    organization_id = session["memberships"][0]["organization_id"]

    for orgnr in ("910000004", "910000012"):
        created = write(
            client,
            "POST",
            "/api/v1/companies",
            json={
                "organization_id": organization_id,
                "orgnr": orgnr,
                "name": f"Klient {orgnr} AS",
            },
        )
        assert created.status_code == 201, created.text

    assert client.get("/api/v1/organizations").json()[0]["name"] == "Fjord Regnskap AS"


def test_two_companies_leave_the_account_name_alone(client: TestClient) -> None:
    """The case that was reported as a leak: an account with two companies.

    Neither company's name may end up on the account, because a screen showing
    the account beside a company is then showing two names that look like they
    belong to the same thing.
    """
    session = register(client, "kari@example.no", full_name="Kari Nordmann")
    organization_id = session["memberships"][0]["organization_id"]

    for orgnr, name in (("910000004", "Nordmann Consulting AS"), ("910000012", "Andre Klient AS")):
        created = write(
            client,
            "POST",
            "/api/v1/companies",
            json={"organization_id": organization_id, "orgnr": orgnr, "name": name},
        )
        assert created.status_code == 201, created.text

    organization = client.get("/api/v1/organizations").json()[0]
    assert organization["name"] == "Kari Nordmann (privat)"
    companies = client.get("/api/v1/companies").json()
    assert organization["name"] not in {company["name"] for company in companies}


def test_renaming_clears_the_placeholder_flag(client: TestClient) -> None:
    session = register(client, "kari@example.no", full_name="Kari Nordmann")
    organization_id = session["memberships"][0]["organization_id"]

    renamed = write(
        client, "PATCH", f"/api/v1/organizations/{organization_id}", json={"name": "Kari AS"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name_is_placeholder"] is False

    # And a company added afterwards leaves the chosen name alone.
    write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": organization_id,
            "orgnr": "910000004",
            "name": "Nordmann Consulting AS",
        },
    )
    assert client.get("/api/v1/organizations").json()[0]["name"] == "Kari AS"


def test_renaming_keeps_the_slug(client: TestClient) -> None:
    # Slugs can already be in circulation in links; a rename is not a reason to
    # break them.
    owner = register(client, "kari@example.no", organization_name="Fjord Regnskap AS")
    organization_id = owner["memberships"][0]["organization_id"]
    original_slug = client.get("/api/v1/organizations").json()[0]["slug"]

    renamed = write(
        client, "PATCH", f"/api/v1/organizations/{organization_id}", json={"name": "Fjord AS"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Fjord AS"
    assert renamed.json()["slug"] == original_slug


def test_colliding_organization_names_get_distinct_slugs(client: TestClient) -> None:
    register(client, "kari@example.no", organization_name="Fjord Regnskap AS")
    second = write(client, "POST", "/api/v1/organizations", json={"name": "Fjord Regnskap AS"})
    assert second.status_code == 201
    assert second.json()["slug"] != "fjord-regnskap-as"
    assert second.json()["slug"].startswith("fjord-regnskap-as-")


def test_organization_endpoints_require_a_session(client: TestClient) -> None:
    assert client.get("/api/v1/organizations").status_code == 401
    assert (
        write(client, "POST", "/api/v1/organizations", json={"name": "Uten pålogging"}).status_code
        == 401
    )


def test_login_password_is_not_needed_to_list_organizations(client: TestClient) -> None:
    # Sanity check that the session, not the password, authorises subsequent
    # requests.
    register(client, "kari@example.no")
    assert client.get("/api/v1/organizations").status_code == 200
    assert PASSWORD not in client.get("/api/v1/auth/me").text
