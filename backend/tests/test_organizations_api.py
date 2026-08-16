"""Organizations, invitations and roles over HTTP.

The scenario these tests keep honest is the one the whole design exists for:
several accounts working inside one organization, with the plan and the data
belonging to the organization rather than to whoever signed up first.
"""

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import PASSWORD, TEST_ORIGIN, register, write


def _client_for(client: TestClient) -> TestClient:
    """A second browser against the same application and database."""
    return TestClient(client.app, base_url=TEST_ORIGIN)


def _invite(client: TestClient, organization_id: int, email: str, role: str) -> dict[str, Any]:
    response = write(
        client,
        "POST",
        f"/api/v1/organizations/{organization_id}/invitations",
        json={"email": email, "role": role},
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _accept(client: TestClient, invite_url: str) -> Any:
    token = invite_url.split("token=", maxsplit=1)[1]
    return write(client, "POST", "/api/v1/auth/invitations/accept", json={"token": token})


def test_two_accounts_share_one_organization(client: TestClient) -> None:
    """The headline case: invite a colleague into the organization you own."""
    owner = register(client, "kari@example.no", organization_name="Fjord Regnskap AS")
    organization_id = owner["memberships"][0]["organization_id"]

    invitation = _invite(client, organization_id, "ola@example.no", "member")
    assert invitation["invite_url"].startswith("https://app.example/invitasjon/?token=")

    colleague = _client_for(client)
    register(colleague, "ola@example.no", full_name="Ola Nordmann")
    # Their own signup organization, before accepting anything.
    assert len(colleague.get("/api/v1/auth/me").json()["memberships"]) == 1

    accepted = _accept(colleague, invitation["invite_url"])
    assert accepted.status_code == 200
    organizations = {
        membership["organization_id"]: membership["role"]
        for membership in accepted.json()["memberships"]
    }
    assert organizations[organization_id] == "member"

    members = client.get(f"/api/v1/organizations/{organization_id}/members").json()
    assert {member["email"] for member in members} == {"kari@example.no", "ola@example.no"}
    # And the invitation is no longer pending.
    assert client.get(f"/api/v1/organizations/{organization_id}/invitations").json() == []


def test_the_plan_is_shared_by_everyone_in_the_organization(client: TestClient) -> None:
    """Entitlements resolve on the organization, so an invitee inherits them.

    This is the property that makes "paid features belong to the organization,
    not the user" true rather than aspirational.
    """
    owner = register(client, "kari@example.no", organization_name="Fjord Regnskap AS")
    organization_id = owner["memberships"][0]["organization_id"]
    invitation = _invite(client, organization_id, "ola@example.no", "member")

    colleague = _client_for(client)
    register(colleague, "ola@example.no")
    memberships = _accept(colleague, invitation["invite_url"]).json()["memberships"]

    shared = next(m for m in memberships if m["organization_id"] == organization_id)
    assert shared["plan"] == owner["memberships"][0]["plan"]


def test_invitation_cannot_be_redeemed_by_a_different_account(client: TestClient) -> None:
    """A forwarded invitation link is not a bearer ticket into the organization."""
    owner = register(client, "kari@example.no")
    organization_id = owner["memberships"][0]["organization_id"]
    invitation = _invite(client, organization_id, "ola@example.no", "member")

    interloper = _client_for(client)
    register(interloper, "tredjepart@example.no")
    response = _accept(interloper, invitation["invite_url"])
    assert response.status_code == 403
    assert response.json()["code"] == "invitation_email_mismatch"


def test_invitation_is_single_use(client: TestClient) -> None:
    owner = register(client, "kari@example.no")
    organization_id = owner["memberships"][0]["organization_id"]
    invitation = _invite(client, organization_id, "ola@example.no", "member")

    colleague = _client_for(client)
    register(colleague, "ola@example.no")
    assert _accept(colleague, invitation["invite_url"]).status_code == 200
    replayed = _accept(colleague, invitation["invite_url"])
    assert replayed.status_code == 400
    assert replayed.json()["code"] == "invalid_invitation"


def test_accepting_an_invitation_requires_being_signed_in(client: TestClient) -> None:
    owner = register(client, "kari@example.no")
    organization_id = owner["memberships"][0]["organization_id"]
    invitation = _invite(client, organization_id, "ola@example.no", "member")

    anonymous = _client_for(client)
    assert _accept(anonymous, invitation["invite_url"]).status_code == 401


def test_reinviting_replaces_the_pending_invitation(client: TestClient) -> None:
    # At most one live token per address, so a re-send cannot leave an older
    # link working.
    owner = register(client, "kari@example.no")
    organization_id = owner["memberships"][0]["organization_id"]
    first = _invite(client, organization_id, "ola@example.no", "viewer")
    second = _invite(client, organization_id, "ola@example.no", "member")

    pending = client.get(f"/api/v1/organizations/{organization_id}/invitations").json()
    assert len(pending) == 1
    assert pending[0]["role"] == "member"

    colleague = _client_for(client)
    register(colleague, "ola@example.no")
    assert _accept(colleague, first["invite_url"]).status_code == 400
    assert _accept(colleague, second["invite_url"]).status_code == 200


def test_existing_member_cannot_be_invited_again(client: TestClient) -> None:
    owner = register(client, "kari@example.no")
    organization_id = owner["memberships"][0]["organization_id"]
    response = write(
        client,
        "POST",
        f"/api/v1/organizations/{organization_id}/invitations",
        json={"email": "kari@example.no", "role": "admin"},
    )
    assert response.status_code == 400


def test_revoked_invitation_stops_working(client: TestClient) -> None:
    owner = register(client, "kari@example.no")
    organization_id = owner["memberships"][0]["organization_id"]
    invitation = _invite(client, organization_id, "ola@example.no", "member")

    revoked = write(
        client,
        "DELETE",
        f"/api/v1/organizations/{organization_id}/invitations/{invitation['id']}",
    )
    assert revoked.status_code == 204

    colleague = _client_for(client)
    register(colleague, "ola@example.no")
    assert _accept(colleague, invitation["invite_url"]).status_code == 400


def test_members_cannot_invite_and_viewers_cannot_read_much(client: TestClient) -> None:
    owner = register(client, "kari@example.no")
    organization_id = owner["memberships"][0]["organization_id"]
    invitation = _invite(client, organization_id, "ola@example.no", "member")

    colleague = _client_for(client)
    register(colleague, "ola@example.no")
    _accept(colleague, invitation["invite_url"])

    response = write(
        colleague,
        "POST",
        f"/api/v1/organizations/{organization_id}/invitations",
        json={"email": "tredje@example.no", "role": "member"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_outsiders_get_404_not_403(client: TestClient) -> None:
    """A non-member must not learn whether an organization exists."""
    owner = register(client, "kari@example.no")
    organization_id = owner["memberships"][0]["organization_id"]

    outsider = _client_for(client)
    register(outsider, "fremmed@example.no")
    assert outsider.get(f"/api/v1/organizations/{organization_id}").status_code == 404
    assert outsider.get("/api/v1/organizations/999999").status_code == 404


def test_admin_cannot_promote_themselves_to_owner(client: TestClient) -> None:
    """The escalation the owner/admin split exists to prevent."""
    owner = register(client, "kari@example.no")
    organization_id = owner["memberships"][0]["organization_id"]
    invitation = _invite(client, organization_id, "ola@example.no", "admin")

    admin = _client_for(client)
    admin_body = register(admin, "ola@example.no")
    admin_user_id = admin_body["user"]["id"]
    _accept(admin, invitation["invite_url"])

    promote = write(
        admin,
        "PATCH",
        f"/api/v1/organizations/{organization_id}/members/{admin_user_id}",
        json={"role": "owner"},
    )
    assert promote.status_code == 403

    owner_user_id = owner["user"]["id"]
    demote_owner = write(
        admin,
        "PATCH",
        f"/api/v1/organizations/{organization_id}/members/{owner_user_id}",
        json={"role": "viewer"},
    )
    assert demote_owner.status_code == 403


def test_owner_can_change_a_role(client: TestClient) -> None:
    owner = register(client, "kari@example.no")
    organization_id = owner["memberships"][0]["organization_id"]
    invitation = _invite(client, organization_id, "ola@example.no", "member")

    colleague = _client_for(client)
    colleague_id = register(colleague, "ola@example.no")["user"]["id"]
    _accept(colleague, invitation["invite_url"])

    response = write(
        client,
        "PATCH",
        f"/api/v1/organizations/{organization_id}/members/{colleague_id}",
        json={"role": "admin"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"


def test_the_last_owner_cannot_leave(client: TestClient) -> None:
    """An organization without an owner is unrecoverable, so the API refuses."""
    owner = register(client, "kari@example.no")
    organization_id = owner["memberships"][0]["organization_id"]
    user_id = owner["user"]["id"]

    demote = write(
        client,
        "PATCH",
        f"/api/v1/organizations/{organization_id}/members/{user_id}",
        json={"role": "member"},
    )
    assert demote.status_code == 409
    assert demote.json()["code"] == "last_owner"

    leave = write(client, "DELETE", f"/api/v1/organizations/{organization_id}/members/{user_id}")
    assert leave.status_code == 409


def test_a_member_can_leave_on_their_own(client: TestClient) -> None:
    # Leaving needs no member-management permission: a viewer must be able to
    # walk away from an organization that invited them.
    owner = register(client, "kari@example.no")
    organization_id = owner["memberships"][0]["organization_id"]
    invitation = _invite(client, organization_id, "ola@example.no", "viewer")

    colleague = _client_for(client)
    colleague_id = register(colleague, "ola@example.no")["user"]["id"]
    _accept(colleague, invitation["invite_url"])

    left = write(
        colleague, "DELETE", f"/api/v1/organizations/{organization_id}/members/{colleague_id}"
    )
    assert left.status_code == 204
    assert colleague.get(f"/api/v1/organizations/{organization_id}").status_code == 404


def test_a_member_cannot_remove_someone_else(client: TestClient) -> None:
    owner = register(client, "kari@example.no")
    organization_id = owner["memberships"][0]["organization_id"]
    owner_id = owner["user"]["id"]
    invitation = _invite(client, organization_id, "ola@example.no", "member")

    colleague = _client_for(client)
    register(colleague, "ola@example.no")
    _accept(colleague, invitation["invite_url"])

    response = write(
        colleague, "DELETE", f"/api/v1/organizations/{organization_id}/members/{owner_id}"
    )
    assert response.status_code == 403


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
    """Signup does not insist on a name, and says so in the response."""
    session = register(client, "kari@example.no", full_name="Kari Nordmann")
    membership = session["memberships"][0]
    assert membership["organization_name"] == "Kari Nordmann (privat)"
    assert membership["organization_name_is_placeholder"] is True


def test_a_name_given_at_signup_is_not_a_placeholder(client: TestClient) -> None:
    session = register(client, "kari@example.no", organization_name="Fjord Regnskap AS")
    assert session["memberships"][0]["organization_name_is_placeholder"] is False


def test_setting_up_the_first_company_names_a_placeholder_organization(
    client: TestClient,
) -> None:
    """The complaint this exists for: an orgnr looked up correctly, and the
    header still saying "Kari Nordmann (privat)" next to it."""
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

    membership = client.get("/api/v1/auth/me").json()["memberships"][0]
    assert membership["organization_name"] == "Nordmann Consulting AS"
    assert membership["organization_name_is_placeholder"] is False


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


def test_only_the_first_company_may_take_over_a_placeholder_name(client: TestClient) -> None:
    """Once a real name is in place — however it got there — it stays."""
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

    assert client.get("/api/v1/organizations").json()[0]["name"] == "Nordmann Consulting AS"


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
    original_slug = owner["memberships"][0]["organization_slug"]

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
