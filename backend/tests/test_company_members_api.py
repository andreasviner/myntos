"""Who may open one company's books, over HTTP.

The scenario these keep honest is the one the design exists for: a
regnskapsfører with one set of books per client, letting a colleague help with
*one* of them. Access is a row per person per company
(:class:`~myntos.domain.identity.models.CompanyMembership`), so an invitation
names a company, a role is held over a company, and the last test in the first
section is the one that matters most: the sibling company stays shut.

It used to be account-wide. Inviting a colleague handed them every client at
once, and the form that did it sat under the account, where nothing on screen
said so. ``tests/test_organizations_api.py`` is what is left of that file: the
account itself, which now owns companies and nothing else.
"""

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import TEST_ORIGIN, register, write

ORGNR = "910000004"
SIBLING_ORGNR = "910000012"


def _client_for(client: TestClient) -> TestClient:
    """A second browser against the same application and database."""
    return TestClient(client.app, base_url=TEST_ORIGIN)


def _company(
    client: TestClient,
    session: dict[str, Any],
    *,
    orgnr: str = ORGNR,
    name: str = "Fjord Regnskap AS",
) -> dict[str, Any]:
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": session["memberships"][0]["organization_id"],
            "orgnr": orgnr,
            "name": name,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _books(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    """An owner and one company of theirs, which is what an invite needs."""
    owner = register(client, "kari@example.no", full_name="Kari Nordmann")
    return owner, _company(client, owner)


def _invite(client: TestClient, company_id: int, email: str, role: str) -> dict[str, Any]:
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/invitations",
        json={"email": email, "role": role},
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _accept(client: TestClient, invite_url: str) -> Any:
    token = invite_url.split("token=", maxsplit=1)[1]
    return write(client, "POST", "/api/v1/auth/invitations/accept", json={"token": token})


# --- Sharing one set of books ------------------------------------------------------


def test_two_accounts_share_one_set_of_books(client: TestClient) -> None:
    """The headline case: invite a colleague to a company you keep."""
    _owner, company = _books(client)

    invitation = _invite(client, company["id"], "ola@example.no", "member")
    assert invitation["invite_url"].startswith("https://app.example/invitasjon/?token=")

    colleague = _client_for(client)
    register(colleague, "ola@example.no", full_name="Ola Nordmann")
    # Their own account, with no books in it, before accepting anything.
    assert colleague.get("/api/v1/companies").json() == []

    assert _accept(colleague, invitation["invite_url"]).status_code == 200
    theirs = colleague.get("/api/v1/companies").json()
    assert [(item["id"], item["role"]) for item in theirs] == [(company["id"], "member")]

    members = client.get(f"/api/v1/companies/{company['id']}/members").json()
    assert {member["email"] for member in members} == {"kari@example.no", "ola@example.no"}
    # And the invitation is no longer pending.
    assert client.get(f"/api/v1/companies/{company['id']}/invitations").json() == []


def test_an_invitation_to_one_company_is_not_a_key_to_the_next(client: TestClient) -> None:
    """The reason access moved off the account.

    Two clients on one account. The colleague asked to help with the first must
    not be able to see the second, and the wall is the same 404 an outsider
    gets: nothing tells them the other books exist.
    """
    owner, first = _books(client)
    second = _company(client, owner, orgnr=SIBLING_ORGNR, name="Klient To AS")

    invitation = _invite(client, first["id"], "ola@example.no", "admin")
    colleague = _client_for(client)
    register(colleague, "ola@example.no", full_name="Ola Nordmann")
    assert _accept(colleague, invitation["invite_url"]).status_code == 200

    assert [item["id"] for item in colleague.get("/api/v1/companies").json()] == [first["id"]]
    assert colleague.get(f"/api/v1/companies/{second['id']}").status_code == 404
    assert colleague.get(f"/api/v1/companies/{second['id']}/members").status_code == 404
    assert colleague.get(f"/api/v1/companies/{second['id']}/cashflow").status_code == 404


def test_an_invited_colleague_does_not_join_the_account(client: TestClient) -> None:
    """They get books to work on, not a place on somebody else's account.

    Which is what stops them adding a company to it, and what keeps the
    account's other clients out of their sight.
    """
    owner, company = _books(client)
    invitation = _invite(client, company["id"], "ola@example.no", "admin")

    colleague = _client_for(client)
    register(colleague, "ola@example.no", full_name="Ola Nordmann")
    accepted = _accept(colleague, invitation["invite_url"])

    theirs = {item["organization_id"] for item in accepted.json()["memberships"]}
    assert owner["memberships"][0]["organization_id"] not in theirs


def test_the_plan_belongs_to_the_books_everyone_shares(client: TestClient) -> None:
    """The subscription is the company's, so whoever opens it sees the same tier.

    This is the property that makes "paid features belong to the books" true
    rather than aspirational, and it is why ``plan`` moved off the account: an
    account with twenty clients is not one subscription.
    """
    _owner, company = _books(client)
    invitation = _invite(client, company["id"], "ola@example.no", "member")

    colleague = _client_for(client)
    register(colleague, "ola@example.no")
    _accept(colleague, invitation["invite_url"])

    shared = colleague.get(f"/api/v1/companies/{company['id']}").json()
    assert shared["plan"] == company["plan"] == "free"


# --- What an invitation is, and is not --------------------------------------------


def test_invitation_cannot_be_redeemed_by_a_different_account(client: TestClient) -> None:
    """A forwarded invitation link is not a bearer ticket into the books."""
    _owner, company = _books(client)
    invitation = _invite(client, company["id"], "ola@example.no", "member")

    interloper = _client_for(client)
    register(interloper, "tredjepart@example.no")
    response = _accept(interloper, invitation["invite_url"])
    assert response.status_code == 403
    assert response.json()["code"] == "invitation_email_mismatch"


def test_invitation_is_single_use(client: TestClient) -> None:
    _owner, company = _books(client)
    invitation = _invite(client, company["id"], "ola@example.no", "member")

    colleague = _client_for(client)
    register(colleague, "ola@example.no")
    assert _accept(colleague, invitation["invite_url"]).status_code == 200
    replayed = _accept(colleague, invitation["invite_url"])
    assert replayed.status_code == 400
    assert replayed.json()["code"] == "invalid_invitation"


def test_accepting_an_invitation_requires_being_signed_in(client: TestClient) -> None:
    _owner, company = _books(client)
    invitation = _invite(client, company["id"], "ola@example.no", "member")

    anonymous = _client_for(client)
    assert _accept(anonymous, invitation["invite_url"]).status_code == 401


def test_reinviting_replaces_the_pending_invitation(client: TestClient) -> None:
    # At most one live token per address per company, so a re-send cannot leave
    # an older link working.
    _owner, company = _books(client)
    first = _invite(client, company["id"], "ola@example.no", "viewer")
    second = _invite(client, company["id"], "ola@example.no", "member")

    pending = client.get(f"/api/v1/companies/{company['id']}/invitations").json()
    assert len(pending) == 1
    assert pending[0]["role"] == "member"

    colleague = _client_for(client)
    register(colleague, "ola@example.no")
    assert _accept(colleague, first["invite_url"]).status_code == 400
    assert _accept(colleague, second["invite_url"]).status_code == 200


def test_inviting_the_same_person_to_a_second_company_keeps_both(client: TestClient) -> None:
    """Superseding is per company, or helping with two clients would be one.

    The pending-invitation rule ("one live token per address") is scoped to the
    books being shared. Account-wide, asking somebody to help with the second
    client would silently cancel the invitation to the first.
    """
    owner, first = _books(client)
    second = _company(client, owner, orgnr=SIBLING_ORGNR, name="Klient To AS")

    to_first = _invite(client, first["id"], "ola@example.no", "member")
    to_second = _invite(client, second["id"], "ola@example.no", "viewer")

    assert len(client.get(f"/api/v1/companies/{first['id']}/invitations").json()) == 1
    assert len(client.get(f"/api/v1/companies/{second['id']}/invitations").json()) == 1

    colleague = _client_for(client)
    register(colleague, "ola@example.no")
    assert _accept(colleague, to_first["invite_url"]).status_code == 200
    assert _accept(colleague, to_second["invite_url"]).status_code == 200
    assert {(item["id"], item["role"]) for item in colleague.get("/api/v1/companies").json()} == {
        (first["id"], "member"),
        (second["id"], "viewer"),
    }


def test_somebody_who_already_has_access_cannot_be_invited_again(client: TestClient) -> None:
    _owner, company = _books(client)
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/invitations",
        json={"email": "kari@example.no", "role": "admin"},
    )
    assert response.status_code == 400


def test_revoked_invitation_stops_working(client: TestClient) -> None:
    _owner, company = _books(client)
    invitation = _invite(client, company["id"], "ola@example.no", "member")

    revoked = write(
        client,
        "DELETE",
        f"/api/v1/companies/{company['id']}/invitations/{invitation['id']}",
    )
    assert revoked.status_code == 204

    colleague = _client_for(client)
    register(colleague, "ola@example.no")
    assert _accept(colleague, invitation["invite_url"]).status_code == 400


def test_an_invitation_can_be_addressed_to_a_person(client: TestClient) -> None:
    """So the email opens with a name, and the board list can show who was asked."""
    _owner, company = _books(client)

    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/invitations",
        json={
            "email": "andreas@example.no",
            "role": "admin",
            "full_name": "Andreas Edvardsen Lindeman",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["full_name"] == "Andreas Edvardsen Lindeman"

    listed = client.get(f"/api/v1/companies/{company['id']}/invitations").json()
    assert [item["full_name"] for item in listed] == ["Andreas Edvardsen Lindeman"]
    assert listed[0]["invited_by"] == "Kari Nordmann"
    assert listed[0]["created_at"] is not None

    # And the welcome page sees it, so somebody invited by name is not asked to
    # retype what the invitation already says. It names the books, not the
    # account that sent it.
    token = response.json()["invite_url"].split("token=", maxsplit=1)[1]
    lookup = write(client, "POST", "/api/v1/auth/invitations/lookup", json={"token": token})
    assert lookup.json()["full_name"] == "Andreas Edvardsen Lindeman"
    assert lookup.json()["company_name"] == "Fjord Regnskap AS"


def test_a_name_that_is_only_whitespace_is_not_a_name(client: TestClient) -> None:
    """Otherwise every later "was a name given" check is quietly wrong."""
    _owner, company = _books(client)
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/invitations",
        json={"email": "ola@example.no", "full_name": "   "},
    )
    assert response.status_code == 201
    assert response.json()["full_name"] is None


def test_an_invitation_can_be_sent_again_with_a_new_link(client: TestClient) -> None:
    """The same row, a fresh token, and the old link stops working.

    Keeping the row is the point: the role, the name and the id survive, so
    re-sending is not "revoke and retype". Killing the old token is the other
    half: two live links to one invitation is one more than anybody agreed to.
    """
    _owner, company = _books(client)
    first = _invite(client, company["id"], "ola@example.no", "member")

    again = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/invitations/{first['id']}/send-again",
    )
    assert again.status_code == 200, again.text
    assert again.json()["id"] == first["id"]
    assert again.json()["role"] == "member"
    assert again.json()["invite_url"] != first["invite_url"]

    colleague = _client_for(client)
    register(colleague, "ola@example.no")
    assert _accept(colleague, first["invite_url"]).status_code == 400
    assert _accept(colleague, again.json()["invite_url"]).status_code == 200


def test_an_invitation_that_is_not_there_cannot_be_resent(client: TestClient) -> None:
    """404, like every other id that is not this company's.

    Not 400: the request is well formed and the caller may manage these books.
    What they named is simply not one of their invitations, and answering
    differently for "never existed" and "belongs to another company" is how
    managing invitations becomes a way to probe for ids.
    """
    _owner, company = _books(client)
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/invitations/9999/send-again",
    )
    assert response.status_code == 404
    assert response.json()["code"] == "invitation_not_found"


# --- Roles, and what they may do to each other -------------------------------------


def test_members_cannot_invite(client: TestClient) -> None:
    _owner, company = _books(client)
    invitation = _invite(client, company["id"], "ola@example.no", "member")

    colleague = _client_for(client)
    register(colleague, "ola@example.no")
    _accept(colleague, invitation["invite_url"])

    response = write(
        colleague,
        "POST",
        f"/api/v1/companies/{company['id']}/invitations",
        json={"email": "tredje@example.no", "role": "member"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_a_member_cannot_resend_an_invitation(client: TestClient) -> None:
    """Re-sending hands out a live credential, so it needs member:manage."""
    _owner, company = _books(client)
    joining = _invite(client, company["id"], "ola@example.no", "member")
    target = _invite(client, company["id"], "tredje@example.no", "member")

    colleague = _client_for(client)
    register(colleague, "ola@example.no")
    _accept(colleague, joining["invite_url"])

    refused = write(
        colleague,
        "POST",
        f"/api/v1/companies/{company['id']}/invitations/{target['id']}/send-again",
    )
    assert refused.status_code == 403
    assert refused.json()["code"] == "permission_denied"


def test_outsiders_get_404_not_403(client: TestClient) -> None:
    """A stranger must not learn whether a set of books exists."""
    _owner, company = _books(client)

    outsider = _client_for(client)
    register(outsider, "fremmed@example.no")
    assert outsider.get(f"/api/v1/companies/{company['id']}/members").status_code == 404
    assert outsider.get("/api/v1/companies/999999/members").status_code == 404


def test_admin_cannot_promote_themselves_to_owner(client: TestClient) -> None:
    """The escalation the owner/admin split exists to prevent."""
    owner, company = _books(client)
    invitation = _invite(client, company["id"], "ola@example.no", "admin")

    admin = _client_for(client)
    admin_user_id = register(admin, "ola@example.no")["user"]["id"]
    _accept(admin, invitation["invite_url"])

    promote = write(
        admin,
        "PATCH",
        f"/api/v1/companies/{company['id']}/members/{admin_user_id}",
        json={"role": "owner"},
    )
    assert promote.status_code == 403

    demote_owner = write(
        admin,
        "PATCH",
        f"/api/v1/companies/{company['id']}/members/{owner['user']['id']}",
        json={"role": "viewer"},
    )
    assert demote_owner.status_code == 403


def test_owner_can_change_a_role(client: TestClient) -> None:
    _owner, company = _books(client)
    invitation = _invite(client, company["id"], "ola@example.no", "member")

    colleague = _client_for(client)
    colleague_id = register(colleague, "ola@example.no")["user"]["id"]
    _accept(colleague, invitation["invite_url"])

    response = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/members/{colleague_id}",
        json={"role": "admin"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"


def test_the_last_owner_cannot_leave(client: TestClient) -> None:
    """Books nobody owns can be neither billed nor shared, so the API refuses."""
    owner, company = _books(client)
    user_id = owner["user"]["id"]

    demote = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/members/{user_id}",
        json={"role": "member"},
    )
    assert demote.status_code == 409
    assert demote.json()["code"] == "last_owner"

    leave = write(client, "DELETE", f"/api/v1/companies/{company['id']}/members/{user_id}")
    assert leave.status_code == 409


def test_a_member_can_leave_on_their_own(client: TestClient) -> None:
    # Leaving needs no member-management permission: a viewer must be able to
    # walk away from a client's books they were invited to.
    _owner, company = _books(client)
    invitation = _invite(client, company["id"], "ola@example.no", "viewer")

    colleague = _client_for(client)
    colleague_id = register(colleague, "ola@example.no")["user"]["id"]
    _accept(colleague, invitation["invite_url"])

    left = write(colleague, "DELETE", f"/api/v1/companies/{company['id']}/members/{colleague_id}")
    assert left.status_code == 204
    # And the books are gone from their list, not merely read-only.
    assert colleague.get("/api/v1/companies").json() == []
    assert colleague.get(f"/api/v1/companies/{company['id']}").status_code == 404


def test_a_member_cannot_remove_someone_else(client: TestClient) -> None:
    owner, company = _books(client)
    invitation = _invite(client, company["id"], "ola@example.no", "member")

    colleague = _client_for(client)
    register(colleague, "ola@example.no")
    _accept(colleague, invitation["invite_url"])

    response = write(
        colleague,
        "DELETE",
        f"/api/v1/companies/{company['id']}/members/{owner['user']['id']}",
    )
    assert response.status_code == 403


def test_the_member_list_says_who_has_linked_id_porten(client: TestClient) -> None:
    """The fact travels; the fødselsnummer behind it does not."""
    _owner, company = _books(client)
    member = client.get(f"/api/v1/companies/{company['id']}/members").json()[0]
    assert member["government_linked"] is False
    assert member["government_name"] is None
    assert "pid" not in member
