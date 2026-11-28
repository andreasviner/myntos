"""A server's integration key (docs/agents-and-api.md rule 15).

An owner makes it on /organisasjon/ «Integrasjoner», bound to IP addresses,
for invoicing only; the server sends its secret as a bearer token and makes,
issues and sends invoices with no approval. What these tests hold down:

1. Only an owner makes, lists and stops one, from a session; no key does.
2. The secret is shown once and stored as a digest; the owner is mailed, never
   with the secret.
3. The gate, in its order: the address, then the route's purpose, then the
   limit, then the company wall. Refusals count towards the stop.
4. The invoice runs end to end with no approval, the EHF send included, and
   every voucher names the key. A credit note is never sent.
5. It stops on the person's events (role, membership, password) and not on the
   Tilgang switch, which is the agent keys' own.
6. Exactly the invoicing routes declare the purpose.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from myntos.api.deps import INTEGRATION_EXTENSION
from myntos.auth.integration_keys import address_allowed, parse_addresses, shown_addresses
from myntos.core.config import get_settings
from myntos.domain.identity.errors import InvalidIntegrationAddressError
from myntos.main import create_app
from tests import test_peppol
from tests.conftest import (
    PASSWORD,
    RecordingMailer,
    agent_header,
    join_company,
    register,
    write,
)
from tests.test_faktura_api import CUSTOMER_ORGNR, create_draft, draft_payload, issue, make_company
from tests.test_peppol import AdemicoStandIn, SmpStub, ready_to_send

# The Peppol tests' fixtures, under their own names, so a send here goes to the
# same stand-in for Ademico and the recipient lookup to the same stub.
stand_in = test_peppol.stand_in
smp = test_peppol.smp
database = test_peppol.database

LOCAL = "127.0.0.1"


def _books(client: TestClient) -> dict[str, Any]:
    session = register(client)
    return make_company(client, session["memberships"][0]["organization_id"])


def keys_url(company_id: int) -> str:
    return f"/api/v1/companies/{company_id}/integrasjonsnokler"


def make_key(
    client: TestClient,
    company_id: int,
    *,
    addresses: tuple[str, ...] = (LOCAL,),
    name: str = "Løynd",
) -> dict[str, Any]:
    """Make an integration key as whoever is signed in, as the page does."""
    made = write(
        client,
        "POST",
        keys_url(company_id),
        json={"name": name, "allowed_ips": list(addresses), "purpose": "faktura"},
    )
    assert made.status_code == 201, made.text
    body: dict[str, Any] = made.json()
    return body


def server(client: TestClient, host: str = LOCAL) -> TestClient:
    """A server calling Myntos from ``host``: no session cookie, only its key."""
    return TestClient(client.app, base_url=str(client.base_url), client=(host, 50000))


def bearer(made: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {made['secret']}"}


def sign_in(client: TestClient, email: str) -> None:
    write(client, "POST", "/api/v1/auth/logout")
    signed = write(
        client, "POST", "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert signed.status_code == 200, signed.text


# --- 1 and 2. Making, seeing and stopping ---------------------------------------------


def test_an_owner_makes_sees_and_stops_an_integration_key(
    client: TestClient, outbox: RecordingMailer, database: sa.Engine
) -> None:
    books = _books(client)
    outbox.sent.clear()

    made = make_key(client, books["id"], addresses=(LOCAL, "203.0.113.0/24"))
    secret = made["secret"]
    assert secret.startswith("myntos_ik_")
    assert made["key"]["allowed_ips"] == ["127.0.0.1", "203.0.113.0/24"]
    assert made["key"]["purpose"] == "faktura"
    assert made["key"]["state"] == "active"
    assert made["key"]["person_name"] == "Kari Nordmann"

    created = outbox.only
    assert created.subject == f"Ny integrasjonsnøkkel i {books['name']}"
    assert secret not in created.text
    assert secret not in (created.html or "")
    assert "/organisasjon/#integrasjoner" in created.text

    with database.connect() as connection:
        stored = connection.execute(
            sa.text("SELECT kind, secret_hash, connection_key_hash, expires_at FROM agent_key")
        ).one()
    assert stored.kind == "integration"
    assert stored.secret_hash == hashlib.sha256(secret.encode()).hexdigest()
    assert stored.connection_key_hash is None
    assert stored.expires_at is None

    listed = client.get(keys_url(books["id"]))
    assert listed.status_code == 200
    assert [key["name"] for key in listed.json()] == ["Løynd"]
    assert "secret" not in listed.text and secret not in listed.text

    used = server(client).get(f"/api/v1/companies/{books['id']}/parties", headers=bearer(made))
    assert used.status_code == 200, used.text
    assert client.get(keys_url(books["id"])).json()[0]["last_used_at"] is not None

    outbox.sent.clear()
    stopped = write(client, "DELETE", f"{keys_url(books['id'])}/{made['key']['id']}")
    assert stopped.status_code == 204
    shown = client.get(keys_url(books["id"])).json()[0]
    assert (shown["state"], shown["revoked_reason"]) == ("stopped", "person")
    assert outbox.only.subject == "Integrasjonsnøkkelen «Løynd» er stoppet"

    refused = server(client).get(f"/api/v1/companies/{books['id']}/parties", headers=bearer(made))
    assert refused.status_code == 401
    assert refused.json()["code"] == "integration_key_stopped"


@pytest.mark.parametrize("role", ["admin", "member"])
def test_only_an_owner_may_manage_integration_keys(client: TestClient, role: str) -> None:
    books = _books(client)
    made = make_key(client, books["id"])
    join_company(client, books["id"], email="ola@example.no", role=role)

    assert client.get(keys_url(books["id"])).status_code == 403
    attempt = write(
        client,
        "POST",
        keys_url(books["id"]),
        json={"name": "Min", "allowed_ips": [LOCAL], "purpose": "faktura"},
    )
    assert attempt.status_code == 403
    assert attempt.json()["code"] == "permission_denied"
    stop = write(client, "DELETE", f"{keys_url(books['id'])}/{made['key']['id']}")
    assert stop.status_code == 403


def test_no_key_manages_integration_keys(client: TestClient) -> None:
    books = _books(client)
    made = make_key(client, books["id"])
    agent = agent_header(client, books["id"])
    integration = bearer(made)
    body = {"name": "Ny", "allowed_ips": [LOCAL], "purpose": "faktura"}

    for header, code in ((agent, "not_for_agent_keys"), (integration, "not_for_integration_keys")):
        caller = server(client)
        for method, url in (
            ("GET", keys_url(books["id"])),
            ("POST", keys_url(books["id"])),
            ("DELETE", f"{keys_url(books['id'])}/{made['key']['id']}"),
        ):
            answer = caller.request(method, url, headers=header, json=body)
            assert answer.status_code == 403, (method, url, answer.text)
            assert answer.json()["code"] == code


def test_an_agent_keys_list_and_stop_never_reach_an_integration_key(client: TestClient) -> None:
    """An administrator manages agent keys; an integration is an owner's alone."""
    books = _books(client)
    made = make_key(client, books["id"])
    write(client, "PUT", f"/api/v1/companies/{books['id']}/agent-access", json={"enabled": True})

    assert client.get(f"/api/v1/companies/{books['id']}/agent-keys").json() == []
    stop = write(
        client, "DELETE", f"/api/v1/companies/{books['id']}/agent-keys/{made['key']['id']}"
    )
    assert stop.status_code == 404
    assert client.get(keys_url(books["id"])).json()[0]["state"] == "active"


def test_the_addresses_are_read_as_a_firewall_reads_them(client: TestClient) -> None:
    books = _books(client)
    made = make_key(
        client,
        books["id"],
        addresses=(" 10.0.0.7/24 ", "10.0.0.0/24", "2001:db8::1", "::ffff:192.0.2.7", LOCAL),
    )
    assert made["key"]["allowed_ips"] == ["10.0.0.0/24", "127.0.0.1", "192.0.2.7", "2001:db8::1"]

    for addresses, code in (
        (["localhost"], "invalid_integration_address"),
        (["0.0.0.0/0"], "invalid_integration_address"),
        (["10.0.0.0/15"], "invalid_integration_address"),
        (["2001:db8::/32"], "invalid_integration_address"),
        ([], None),
        ([f"10.0.0.{n}" for n in range(11)], None),
    ):
        refused = write(
            client,
            "POST",
            keys_url(books["id"]),
            json={"name": "Feil", "allowed_ips": addresses, "purpose": "faktura"},
        )
        assert refused.status_code == 422, (addresses, refused.text)
        if code is not None:
            assert refused.json()["code"] == code, (addresses, refused.text)

    other_purpose = write(
        client,
        "POST",
        keys_url(books["id"]),
        json={"name": "Lønn", "allowed_ips": [LOCAL], "purpose": "lonn"},
    )
    assert other_purpose.status_code == 422


def test_addresses_match_by_network_and_mapped_ipv4() -> None:
    stored = " ".join(parse_addresses(["10.1.0.0/16", "2001:db8::/64", LOCAL]))
    assert shown_addresses(stored) == ["10.1.0.0/16", "127.0.0.1", "2001:db8::/64"]
    assert address_allowed(stored, "10.1.200.3")
    assert address_allowed(stored, "::ffff:127.0.0.1")
    assert address_allowed(stored, "2001:db8::abcd")
    assert not address_allowed(stored, "10.2.0.1")
    assert not address_allowed(stored, "testclient")
    assert not address_allowed(stored, None)
    assert not address_allowed(None, LOCAL)
    with pytest.raises(InvalidIntegrationAddressError):
        parse_addresses(["   "])


# --- 3. The gate ----------------------------------------------------------------------


def test_a_key_works_only_from_its_addresses(client: TestClient) -> None:
    books = _books(client)
    made = make_key(client, books["id"], addresses=(LOCAL, "10.1.0.0/16", "2001:db8::/64"))
    url = f"/api/v1/companies/{books['id']}/parties"

    for host in (LOCAL, "10.1.200.3", "2001:db8::5"):
        assert server(client, host).get(url, headers=bearer(made)).status_code == 200, host

    for host in ("203.0.113.9", "10.2.0.1", "testclient"):
        refused = server(client, host).get(url, headers=bearer(made))
        assert refused.status_code == 403, host
        assert refused.json()["code"] == "integration_key_wrong_address"


def test_a_key_calls_only_the_invoicing_routes(client: TestClient) -> None:
    books = _books(client)
    company = books["id"]
    invoice = issue(client, company, create_draft(client, company)["id"]).json()
    made = make_key(client, books["id"])
    caller = server(client)

    for method, url in (
        ("POST", f"/api/v1/companies/{company}/faktura/{invoice['id']}/innbetalinger"),
        ("POST", f"/api/v1/companies/{company}/faktura/{invoice['id']}/kreditnota"),
        ("PATCH", f"/api/v1/companies/{company}/faktura/{invoice['id']}"),
        ("GET", f"/api/v1/companies/{company}/cashflow"),
        ("GET", f"/api/v1/companies/{company}/members"),
        ("GET", f"/api/v1/companies/{company}/agent-keys"),
        ("GET", "/api/v1/agent/me"),
        ("GET", f"/api/v1/companies/{company}/godkjenninger"),
    ):
        refused = caller.request(method, url, headers=bearer(made), json={})
        assert refused.status_code == 403, (method, url, refused.text)
        assert refused.json()["code"] == "not_for_integration_keys", (method, url)


def test_the_company_wall_holds(client: TestClient) -> None:
    books = _books(client)
    made = make_key(client, books["id"])
    theirs = make_company(
        client,
        books["organization_id"],
        orgnr=CUSTOMER_ORGNR,
        name="Søsterselskap AS",
    )

    refused = server(client).get(f"/api/v1/companies/{theirs['id']}/faktura", headers=bearer(made))
    assert refused.status_code == 404
    assert refused.json()["code"] == "company_not_found"


def test_a_key_that_keeps_being_refused_is_stopped_and_its_owner_told(
    client: TestClient, outbox: RecordingMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    books = _books(client)
    made = make_key(client, books["id"])
    monkeypatch.setattr(get_settings(), "agent_key_refusals_before_suspension", 3)
    outbox.sent.clear()

    elsewhere = server(client, "203.0.113.9")
    url = f"/api/v1/companies/{books['id']}/parties"
    assert elsewhere.get(url, headers=bearer(made)).status_code == 403
    off_purpose = f"/api/v1/companies/{books['id']}/cashflow"
    assert server(client).get(off_purpose, headers=bearer(made)).status_code == 403
    assert elsewhere.get(url, headers=bearer(made)).status_code == 403

    after = server(client).get(url, headers=bearer(made))
    assert after.status_code == 401
    assert after.json()["code"] == "integration_key_stopped"
    shown = client.get(keys_url(books["id"])).json()[0]
    assert (shown["state"], shown["revoked_reason"]) == ("stopped", "refusals")
    told = outbox.only
    assert told.subject == "Integrasjonsnøkkelen «Løynd» er stoppet"
    assert "/organisasjon/#integrasjoner" in told.text


def test_an_unknown_secret_is_a_401(client: TestClient) -> None:
    books = _books(client)
    answer = server(client).get(
        f"/api/v1/companies/{books['id']}/parties",
        headers={"Authorization": "Bearer myntos_ik_finnesikke"},
    )
    assert answer.status_code == 401
    assert answer.json()["code"] == "invalid_integration_key"


# --- 4. The invoice, end to end -------------------------------------------------------


def test_the_whole_invoice_runs_without_asking(
    client: TestClient,
    database: sa.Engine,
    stand_in: AdemicoStandIn,
    smp: SmpStub,
) -> None:
    """What Løynd does for a postpaid customer, as the server does it."""
    company = ready_to_send(client, database, stand_in)
    made = make_key(client, company["id"])
    key_id = made["key"]["id"]
    loynd = server(client)
    header = bearer(made)
    base = f"/api/v1/companies/{company['id']}"
    smp.register(CUSTOMER_ORGNR, invoice=True, credit_note=False)

    assert loynd.get("/api/v1/faktura/katalog", headers=header).status_code == 200
    party = loynd.post(
        f"{base}/parties",
        headers=header,
        json={
            "party_type": "kunde",
            "name": "Kunde AS",
            "orgnr": CUSTOMER_ORGNR,
            "address_line": "Storgata 1",
            "postal_code": "0155",
            "city": "Oslo",
        },
    )
    assert party.status_code == 201, party.text
    assert loynd.get(f"{base}/parties", headers=header).status_code == 200

    payload = draft_payload(order_reference="LOYND-17", buyer_reference="Ola")
    draft = loynd.post(f"{base}/faktura", headers=header, json=payload)
    assert draft.status_code == 201, draft.text
    listed = loynd.get(f"{base}/faktura", headers=header)
    assert listed.status_code == 200
    assert draft.json()["id"] in [entry["id"] for entry in listed.json()]

    spare = loynd.post(f"{base}/faktura", headers=header, json=payload).json()
    assert loynd.delete(f"{base}/faktura/{spare['id']}", headers=header).status_code == 204

    issued = loynd.post(f"{base}/faktura/{draft.json()['id']}/utsted", headers=header)
    assert issued.status_code == 201, issued.text
    invoice = issued.json()

    recipient = loynd.get(
        f"{base}/peppol/mottaker", headers=header, params={"orgnr": CUSTOMER_ORGNR}
    )
    assert recipient.json() == {"orgnr": CUSTOMER_ORGNR, "can_receive": True}

    sent = loynd.post(f"{base}/faktura/{invoice['id']}/send", headers=header)
    assert sent.status_code == 201, sent.text
    assert [r for r in stand_in.requests if r[1].endswith("/ubl-submissions")]
    followed = loynd.get(f"{base}/faktura/{invoice['id']}/forsendelse", headers=header)
    assert followed.status_code == 200, followed.text

    read = loynd.get(f"{base}/faktura/{invoice['id']}", headers=header)
    assert read.status_code == 200
    assert "payment_state" in read.json()
    pdf = loynd.get(f"{base}/faktura/{invoice['id']}/pdf", headers=header)
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")
    assert loynd.get(f"{base}/faktura/{invoice['id']}/ehf", headers=header).status_code == 200

    with database.connect() as connection:
        via = connection.execute(
            sa.text("SELECT created_via_agent_key_id FROM gl_transaction")
        ).scalars()
        assert set(via) == {key_id}
        waiting = connection.execute(sa.text("SELECT count(*) FROM agent_approval")).scalar()
        assert waiting == 0


def test_an_integration_never_sends_a_credit_note(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    company = ready_to_send(client, database, stand_in)
    base = f"/api/v1/companies/{company['id']}"
    invoice = issue(client, company["id"], create_draft(client, company["id"])["id"]).json()
    credit = write(client, "POST", f"{base}/faktura/{invoice['id']}/kreditnota", json={})
    assert credit.status_code == 201, credit.text
    made = make_key(client, company["id"])

    refused = server(client).post(
        f"{base}/faktura/{credit.json()['id']}/send", headers=bearer(made)
    )
    assert refused.status_code == 403
    assert refused.json()["code"] == "not_for_integration_keys"
    assert not [r for r in stand_in.requests if r[1].endswith("/ubl-submissions")]


# --- 5. When it stops, and when it does not ------------------------------------------


def test_the_tilgang_switch_does_not_govern_integrations(client: TestClient) -> None:
    books = _books(client)
    made = make_key(client, books["id"])
    url = f"/api/v1/companies/{books['id']}/parties"
    switch = f"/api/v1/companies/{books['id']}/agent-access"

    assert server(client).get(url, headers=bearer(made)).status_code == 200
    write(client, "PUT", switch, json={"enabled": True})
    agent = agent_header(client, books["id"])
    assert write(client, "PUT", switch, json={"enabled": False}).status_code == 200

    assert server(client).get(url, headers=agent).status_code != 200
    assert server(client).get(url, headers=bearer(made)).status_code == 200


def test_losing_the_owner_role_stops_the_key(client: TestClient, outbox: RecordingMailer) -> None:
    books = _books(client)
    ola = join_company(client, books["id"], email="ola@example.no", role="owner")
    made = make_key(client, books["id"])
    sign_in(client, "kari@example.no")
    outbox.sent.clear()

    demoted = write(
        client,
        "PATCH",
        f"/api/v1/companies/{books['id']}/members/{ola['user']['id']}",
        json={"role": "admin"},
    )
    assert demoted.status_code == 200, demoted.text
    shown = client.get(keys_url(books["id"])).json()[0]
    assert (shown["state"], shown["revoked_reason"]) == ("stopped", "not_owner")
    told = outbox.only
    assert told.to == "ola@example.no"
    assert told.subject == "Integrasjonsnøkkelen «Løynd» er stoppet"
    url = f"/api/v1/companies/{books['id']}/parties"
    assert server(client).get(url, headers=bearer(made)).status_code == 401


def test_a_role_kept_as_owner_stops_nothing(client: TestClient) -> None:
    books = _books(client)
    ola = join_company(client, books["id"], email="ola@example.no", role="admin")
    sign_in(client, "kari@example.no")
    made = make_key(client, books["id"])

    promoted = write(
        client,
        "PATCH",
        f"/api/v1/companies/{books['id']}/members/{ola['user']['id']}",
        json={"role": "owner"},
    )
    assert promoted.status_code == 200, promoted.text
    url = f"/api/v1/companies/{books['id']}/parties"
    assert server(client).get(url, headers=bearer(made)).status_code == 200


def test_leaving_the_books_stops_the_key(client: TestClient, outbox: RecordingMailer) -> None:
    books = _books(client)
    ola = join_company(client, books["id"], email="ola@example.no", role="owner")
    made = make_key(client, books["id"])
    outbox.sent.clear()

    left = write(client, "DELETE", f"/api/v1/companies/{books['id']}/members/{ola['user']['id']}")
    assert left.status_code == 204, left.text
    assert outbox.only.to == "ola@example.no"
    url = f"/api/v1/companies/{books['id']}/parties"
    assert server(client).get(url, headers=bearer(made)).status_code == 401


def test_a_password_reset_stops_the_key(client: TestClient, outbox: RecordingMailer) -> None:
    books = _books(client)
    made = make_key(client, books["id"])
    outbox.sent.clear()

    write(client, "POST", "/api/v1/auth/password/forgot", json={"email": "kari@example.no"})
    token = outbox.only.text.split("#token=", maxsplit=1)[1].split()[0]
    reset = write(
        client,
        "POST",
        "/api/v1/auth/password/reset",
        json={"token": token, "new_password": "et-helt-annet-langt-passord"},
    )
    assert reset.status_code == 204, reset.text
    url = f"/api/v1/companies/{books['id']}/parties"
    assert server(client).get(url, headers=bearer(made)).status_code == 401


def test_a_server_can_stop_its_own_key(client: TestClient) -> None:
    books = _books(client)
    made = make_key(client, books["id"])

    revoked = server(client).post("/api/v1/agent/revoke", json={"token": made["secret"]})
    assert revoked.status_code == 204
    shown = client.get(keys_url(books["id"])).json()[0]
    assert (shown["state"], shown["revoked_reason"]) == ("stopped", "agent")


def test_an_integration_key_is_never_exchanged(client: TestClient) -> None:
    books = _books(client)
    made = make_key(client, books["id"])

    answer = server(client).post(
        "/api/v1/agent/token",
        json={"grant_type": "connection_key", "connection_key": made["secret"]},
    )
    assert answer.status_code == 400
    assert answer.json()["code"] == "invalid_grant"
    assert "myntos_ik_" in answer.json()["detail"]


# --- 6. What declares the purpose -----------------------------------------------------


def test_exactly_the_invoicing_routes_are_open_to_integrations() -> None:
    """The owner's list of 2026-11-26, and nothing else: no payment, no credit note."""
    schema = create_app().openapi()
    declared = {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method, operation in operations.items()
        if operation.get(INTEGRATION_EXTENSION) == "faktura"
    }
    company = "/api/v1/companies/{company_id}"
    invoice = company + "/faktura/{document_id}"
    assert declared == {
        ("GET", company + "/parties"),
        ("POST", company + "/parties"),
        ("GET", "/api/v1/faktura/katalog"),
        ("GET", company + "/faktura"),
        ("POST", company + "/faktura"),
        ("GET", invoice),
        ("DELETE", invoice),
        ("POST", invoice + "/utsted"),
        ("GET", invoice + "/pdf"),
        ("GET", invoice + "/ehf"),
        ("GET", company + "/peppol/mottaker"),
        ("POST", invoice + "/send"),
        ("GET", invoice + "/forsendelse"),
    }
