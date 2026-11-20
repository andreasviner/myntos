"""Sending an issued invoice as EHF over Peppol, against a stand-in for Recommand.

Two halves. The client tests check what goes over the wire to the access point:
the frozen XML unchanged, the document type named rather than guessed, and never
an e-mail, because Recommand's mail leaves the EEA. The API tests check the
walls Myntos builds in front of it: only a company confirmed through Altinn may
register, nothing goes before the identity check, an invoice that went cannot go
twice, and a key's send waits for its person.

The stand-in answers the way the published OpenAPI document says Recommand does;
it is not a recording, and the first real send is still owed
(docs/integrations/peppol.md, «Before the first customer»).
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from myntos.core.clock import utc_now
from myntos.core.config import get_settings
from myntos.integrations import recommand
from tests.conftest import agent_header, join_company, register, write
from tests.test_faktura_api import CUSTOMER_ORGNR, ORGNR, create_draft, issue, make_company

KEY = "test-key"
SECRET = "test-secret"


class StandIn:
    """Recommand, as far as Myntos uses it."""

    def __init__(self) -> None:
        self.companies: dict[str, dict[str, Any]] = {}
        self.documents: dict[str, dict[str, Any]] = {}
        self.requests: list[tuple[str, str, Any]] = []
        # What the next send is answered with: a status code and, for 200, the
        # delivery status the document starts in.
        self.send_answer: tuple[int, str] = (200, "pending")
        self.auth_seen: set[str] = set()
        # The Peppol addresses registered to receive BIS Billing 3.0.
        self.on_peppol: set[str] = set()

    def add_company(self, orgnr: str, *, verified: bool = False) -> dict[str, Any]:
        company_id = f"c_{len(self.companies) + 1}"
        company: dict[str, Any] = {
            "id": company_id,
            "enterpriseNumber": orgnr,
            "isVerified": verified,
        }
        self.companies[company_id] = company
        return company

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.auth_seen.add(request.headers.get("authorization", ""))
        body: Any = json.loads(request.content) if request.content else None
        path = request.url.path
        self.requests.append((request.method, path, body))

        if request.method == "GET" and path == "/api/v1/companies":
            number = request.url.params.get("enterpriseNumber")
            found = [c for c in self.companies.values() if c["enterpriseNumber"] == number]
            return httpx.Response(200, json={"success": True, "companies": found})
        if request.method == "POST" and path == "/api/v1/companies":
            company = self.add_company(body["enterpriseNumber"])
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "company": company,
                    "verificationUrl": f"https://verify.example/{company['id']}/1",
                },
            )
        if path.startswith("/api/v1/companies/"):
            known = self.companies.get(path.split("/")[4])
            if known is None:
                return httpx.Response(404, json={"success": False, "errors": {}})
            if path.endswith("/verify"):
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        "verificationUrl": f"https://verify.example/{known['id']}/2",
                    },
                )
            return httpx.Response(200, json={"success": True, "company": known})
        if request.method == "POST" and path == "/api/v1/verify-document-support":
            # A failed lookup is isValid false, not an error: Recommand's own words.
            return httpx.Response(
                200, json={"success": True, "isValid": body["peppolAddress"] in self.on_peppol}
            )
        if request.method == "POST" and path.endswith("/send"):
            code, status = self.send_answer
            if code == 422:
                return httpx.Response(
                    422,
                    json={"success": False, "errors": {"recipient": ["not registered"]}},
                )
            if code == 400:
                return httpx.Response(
                    400,
                    json={"success": False, "errors": {"document": ["[BR-CO-15] totals differ"]}},
                )
            document_id = f"doc_{len(self.documents) + 1}"
            document: dict[str, Any] = {
                "id": document_id,
                "deliveryStatus": status,
                "deliveries": [{"channel": "peppol", "status": status, "failure": None}],
            }
            self.documents[document_id] = document
            return httpx.Response(200, json={"success": True, **document})
        if request.method == "GET" and path.startswith("/api/v1/documents/"):
            stored = self.documents.get(path.rsplit("/", 1)[1])
            if stored is None:
                return httpx.Response(404, json={"success": False, "errors": {}})
            return httpx.Response(200, json={"success": True, "document": stored})
        return httpx.Response(500)

    def deliver(self, document_id: str, status: str, failure: dict[str, Any] | None = None) -> None:
        document = self.documents[document_id]
        document["deliveryStatus"] = status
        document["deliveries"] = [{"channel": "peppol", "status": status, "failure": failure}]


@pytest.fixture
def stand_in(monkeypatch: pytest.MonkeyPatch) -> Iterator[StandIn]:
    stub = StandIn()
    monkeypatch.setenv("RECOMMAND_API_KEY", KEY)
    monkeypatch.setenv("RECOMMAND_API_SECRET", SECRET)
    monkeypatch.setattr(
        recommand,
        "_client",
        lambda timeout=None: httpx.AsyncClient(transport=httpx.MockTransport(stub.handle)),
    )
    get_settings.cache_clear()
    yield stub
    get_settings.cache_clear()


@pytest.fixture
def database(database_url: str) -> Iterator[sa.Engine]:
    engine = sa.create_engine(database_url.replace("+aiosqlite", ""))
    yield engine
    engine.dispose()


def confirm(database: sa.Engine, company_id: int) -> None:
    """What confirming through Altinn leaves behind, without standing up Altinn."""
    with database.begin() as connection:
        connection.execute(
            sa.text("UPDATE company SET verified_at = :t WHERE id = :c"),
            {"t": utc_now(), "c": company_id},
        )


def books(client: TestClient, database: sa.Engine, *, confirmed: bool = True) -> dict[str, Any]:
    session = register(client)
    company = make_company(client, session["memberships"][0]["organization_id"])
    if confirmed:
        confirm(database, company["id"])
    return company


def issued_invoice(client: TestClient, company_id: int, **overrides: Any) -> dict[str, Any]:
    draft = create_draft(client, company_id, **overrides)
    response = issue(client, company_id, draft["id"])
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def register_peppol(client: TestClient, company_id: int) -> Any:
    return write(client, "POST", f"/api/v1/companies/{company_id}/peppol")


def send(client: TestClient, company_id: int, document_id: int, **kwargs: Any) -> Any:
    return write(
        client, "POST", f"/api/v1/companies/{company_id}/faktura/{document_id}/send", **kwargs
    )


def ready_to_send(client: TestClient, database: sa.Engine, stand_in: StandIn) -> dict[str, Any]:
    """Books that are confirmed, registered and through the identity check."""
    company = books(client, database)
    stand_in.add_company(ORGNR, verified=True)
    assert register_peppol(client, company["id"]).status_code == 200
    return company


# --- The client: what goes over the wire ----------------------------------------------


async def test_the_frozen_xml_goes_as_it_is_and_never_by_email(stand_in: StandIn) -> None:
    xml = "<Invoice>æøå &amp; nothing rewritten</Invoice>"

    sent = await recommand.send("c_1", recipient_orgnr=CUSTOMER_ORGNR, xml=xml, credit_note=False)

    method, path, body = stand_in.requests[-1]
    assert (method, path) == ("POST", "/api/v1/c_1/send")
    assert body == {
        "recipient": f"0192:{CUSTOMER_ORGNR}",
        "documentType": "xml",
        "document": xml,
        "doctypeId": recommand.INVOICE_DOCTYPE,
        "processId": recommand.BILLING_PROCESS,
    }
    assert "email" not in body
    expected = base64.b64encode(f"{KEY}:{SECRET}".encode()).decode()
    assert stand_in.auth_seen == {f"Basic {expected}"}
    assert sent.delivery.status == "pending"


async def test_a_credit_note_is_named_as_one(stand_in: StandIn) -> None:
    await recommand.send(
        "c_1", recipient_orgnr=CUSTOMER_ORGNR, xml="<CreditNote/>", credit_note=True
    )
    assert stand_in.requests[-1][2]["doctypeId"] == recommand.CREDIT_NOTE_DOCTYPE


async def test_a_company_is_registered_to_send_only_and_without_contact_details(
    stand_in: StandIn,
) -> None:
    await recommand.create_company(
        orgnr=ORGNR,
        name="Nordmann Consulting AS",
        address="Kirkegata 2",
        postal_code="0153",
        city="Oslo",
        vat_registered=True,
    )
    body = stand_in.requests[-1][2]
    assert body["isSmpRecipient"] is False
    assert body["enterpriseNumberScheme"] == "0192"
    assert body["vatNumber"] == f"NO{ORGNR}MVA"
    assert "email" not in body and "phone" not in body


async def test_the_access_points_refusals_are_told_apart(stand_in: StandIn) -> None:
    stand_in.send_answer = (422, "")
    with pytest.raises(recommand.RecipientUnreachableError):
        await recommand.send("c_1", recipient_orgnr=CUSTOMER_ORGNR, xml="<x/>", credit_note=False)

    stand_in.send_answer = (400, "")
    with pytest.raises(recommand.RecommandRefusedError) as refused:
        await recommand.send("c_1", recipient_orgnr=CUSTOMER_ORGNR, xml="<x/>", credit_note=False)
    assert refused.value.problems == ["document: [BR-CO-15] totals differ"]


async def test_a_send_with_no_answer_is_uncertain_not_unavailable(
    monkeypatch: pytest.MonkeyPatch, stand_in: StandIn
) -> None:
    def silent(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("no answer", request=request)

    monkeypatch.setattr(
        recommand,
        "_client",
        lambda timeout=None: httpx.AsyncClient(transport=httpx.MockTransport(silent)),
    )
    with pytest.raises(recommand.SendTimedOutError):
        await recommand.send("c_1", recipient_orgnr=CUSTOMER_ORGNR, xml="<x/>", credit_note=False)
    with pytest.raises(recommand.RecommandUnavailableError):
        await recommand.get_company("c_1")


async def test_a_blank_key_is_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOMMAND_API_KEY", " ")
    monkeypatch.setenv("RECOMMAND_API_SECRET", SECRET)
    get_settings.cache_clear()
    try:
        assert not recommand.is_configured()
        with pytest.raises(recommand.RecommandNotConfiguredError):
            await recommand.get_company("c_1")
    finally:
        get_settings.cache_clear()


async def test_a_failed_delivery_carries_the_access_points_reason(stand_in: StandIn) -> None:
    sent = await recommand.send(
        "c_1", recipient_orgnr=CUSTOMER_ORGNR, xml="<x/>", credit_note=False
    )
    stand_in.deliver(
        sent.id,
        "failed",
        {"category": "recipient_rejected", "message": "rejected by receiver", "providerCode": None},
    )
    delivery = await recommand.delivery(sent.id)
    assert delivery == recommand.Delivery(
        status="failed",
        failure_category="recipient_rejected",
        failure_message="rejected by receiver",
    )


async def test_asking_whether_a_customer_can_receive_names_the_document(
    stand_in: StandIn,
) -> None:
    stand_in.on_peppol.add(f"0192:{CUSTOMER_ORGNR}")

    assert await recommand.can_receive(CUSTOMER_ORGNR, credit_note=False)
    assert stand_in.requests[-1][2] == {
        "peppolAddress": f"0192:{CUSTOMER_ORGNR}",
        "documentType": recommand.INVOICE_DOCTYPE,
        "processId": recommand.BILLING_PROCESS,
    }
    assert await recommand.can_receive(CUSTOMER_ORGNR, credit_note=True)
    assert stand_in.requests[-1][2]["documentType"] == recommand.CREDIT_NOTE_DOCTYPE
    assert not await recommand.can_receive("999999999", credit_note=False)


# --- Registering ------------------------------------------------------------------------


def test_without_an_access_point_nothing_can_be_sent(
    client: TestClient, database: sa.Engine
) -> None:
    company = books(client, database)

    standing = client.get(f"/api/v1/companies/{company['id']}/peppol").json()
    assert standing == {
        "available": False,
        "company_confirmed": True,
        "registered": False,
        "verified": False,
        "can_send": False,
    }
    refused = register_peppol(client, company["id"])
    assert refused.status_code == 503
    assert refused.json()["code"] == "peppol_not_available"


def test_a_company_not_confirmed_through_altinn_cannot_register(
    client: TestClient, database: sa.Engine, stand_in: StandIn
) -> None:
    """Otherwise anybody who typed an orgnr could send invoices in its name."""
    company = books(client, database, confirmed=False)

    refused = register_peppol(client, company["id"])

    assert refused.status_code == 409
    assert refused.json()["code"] == "company_not_confirmed"
    assert stand_in.requests == []


def test_registering_creates_the_company_and_hands_out_the_identity_check(
    client: TestClient, database: sa.Engine, stand_in: StandIn
) -> None:
    company = books(client, database)

    registered = register_peppol(client, company["id"])

    assert registered.status_code == 200, registered.text
    body = registered.json()
    assert body["registered"] is True and body["verified"] is False
    assert body["can_send"] is False
    assert body["verification_url"] == "https://verify.example/c_1/1"
    created = [r for r in stand_in.requests if r[:2] == ("POST", "/api/v1/companies")]
    assert len(created) == 1
    assert created[0][2]["name"] == "Nordmann Consulting AS"

    # Asked again before the check is done: a fresh link, the same company.
    again = register_peppol(client, company["id"]).json()
    assert again["verification_url"] == "https://verify.example/c_1/2"
    assert len(stand_in.companies) == 1

    # The person finishes the check on the access point's page and comes back.
    stand_in.companies["c_1"]["isVerified"] = True
    standing = client.get(f"/api/v1/companies/{company['id']}/peppol").json()
    assert standing["verified"] is True and standing["can_send"] is True
    assert register_peppol(client, company["id"]).json()["verification_url"] is None


def test_an_existing_company_at_the_access_point_is_adopted(
    client: TestClient, database: sa.Engine, stand_in: StandIn
) -> None:
    company = books(client, database)
    stand_in.add_company(ORGNR, verified=True)

    body = register_peppol(client, company["id"]).json()

    assert body["verified"] is True and body["verification_url"] is None
    assert not [r for r in stand_in.requests if r[:2] == ("POST", "/api/v1/companies")]


def test_only_an_admin_registers(
    client: TestClient, database: sa.Engine, stand_in: StandIn
) -> None:
    company = books(client, database)
    join_company(client, company["id"], email="medarbeider@example.no", role="member")

    assert register_peppol(client, company["id"]).status_code == 403
    assert client.get(f"/api/v1/companies/{company['id']}/peppol").status_code == 200


# --- Sending ----------------------------------------------------------------------------


def test_whether_the_customer_can_receive_is_asked_before_issuing(
    client: TestClient, database: sa.Engine, stand_in: StandIn
) -> None:
    """So sending as EHF can be the default for a customer who can take it."""
    company = books(client, database)
    stand_in.on_peppol.add(f"0192:{CUSTOMER_ORGNR}")
    url = f"/api/v1/companies/{company['id']}/peppol/mottaker"

    on = client.get(url, params={"orgnr": CUSTOMER_ORGNR})
    assert on.status_code == 200, on.text
    assert on.json() == {"orgnr": CUSTOMER_ORGNR, "can_receive": True}
    off = client.get(url, params={"orgnr": "999999999", "kreditnota": "true"})
    assert off.json() == {"orgnr": "999999999", "can_receive": False}
    assert stand_in.requests[-1][2]["documentType"] == recommand.CREDIT_NOTE_DOCTYPE
    assert client.get(url, params={"orgnr": "12345"}).status_code == 422


def test_without_an_access_point_nobody_is_looked_up(
    client: TestClient, database: sa.Engine
) -> None:
    company = books(client, database)
    refused = client.get(
        f"/api/v1/companies/{company['id']}/peppol/mottaker", params={"orgnr": CUSTOMER_ORGNR}
    )
    assert refused.status_code == 503
    assert refused.json()["code"] == "peppol_not_available"


def test_an_issued_invoice_goes_once_and_its_delivery_is_followed(
    client: TestClient, database: sa.Engine, stand_in: StandIn
) -> None:
    company = ready_to_send(client, database, stand_in)
    invoice = issued_invoice(client, company["id"])
    frozen = client.get(f"/api/v1/companies/{company['id']}/faktura/{invoice['id']}/ehf").text

    sent = send(client, company["id"], invoice["id"])

    assert sent.status_code == 201, sent.text
    assert sent.json()["status"] == "pending"
    assert sent.json()["recipient"] == f"0192:{CUSTOMER_ORGNR}"
    assert stand_in.requests[-1][2]["document"] == frozen

    listed = client.get(f"/api/v1/companies/{company['id']}/faktura").json()
    assert listed[0]["ehf_sending"]["status"] == "pending"

    again = send(client, company["id"], invoice["id"])
    assert again.status_code == 409
    assert again.json()["code"] == "already_sent"

    stand_in.deliver("doc_1", "delivered")
    followed = client.get(f"/api/v1/companies/{company['id']}/faktura/{invoice['id']}/forsendelse")
    assert followed.json()["status"] == "delivered"
    assert followed.json()["status_checked_at"] is not None
    shown = client.get(f"/api/v1/companies/{company['id']}/faktura/{invoice['id']}").json()
    assert shown["ehf_sending"]["status"] == "delivered"


def test_a_failed_send_may_be_tried_again(
    client: TestClient, database: sa.Engine, stand_in: StandIn
) -> None:
    company = ready_to_send(client, database, stand_in)
    invoice = issued_invoice(client, company["id"])
    assert send(client, company["id"], invoice["id"]).status_code == 201

    stand_in.deliver(
        "doc_1", "failed", {"category": "transport", "message": "x", "providerCode": None}
    )
    followed = client.get(f"/api/v1/companies/{company['id']}/faktura/{invoice['id']}/forsendelse")
    assert followed.json()["status"] == "failed"
    assert followed.json()["failure_category"] == "transport"

    assert send(client, company["id"], invoice["id"]).status_code == 201


def test_a_customer_not_on_peppol_is_told_and_nothing_is_kept(
    client: TestClient, database: sa.Engine, stand_in: StandIn
) -> None:
    company = ready_to_send(client, database, stand_in)
    invoice = issued_invoice(client, company["id"])
    stand_in.send_answer = (422, "")

    refused = send(client, company["id"], invoice["id"])

    assert refused.status_code == 409
    assert refused.json()["code"] == "recipient_not_on_peppol"
    assert "PDF" in refused.json()["detail"]
    never = client.get(f"/api/v1/companies/{company['id']}/faktura/{invoice['id']}/forsendelse")
    assert never.status_code == 404
    assert never.json()["code"] == "transmission_not_found"


def test_a_refused_ehf_says_what_was_found(
    client: TestClient, database: sa.Engine, stand_in: StandIn
) -> None:
    company = ready_to_send(client, database, stand_in)
    invoice = issued_invoice(client, company["id"])
    stand_in.send_answer = (400, "")

    refused = send(client, company["id"], invoice["id"])

    assert refused.status_code == 502
    assert refused.json()["code"] == "access_point_refused"
    assert "BR-CO-15" in refused.json()["detail"]


def test_nothing_goes_before_the_identity_check(
    client: TestClient, database: sa.Engine, stand_in: StandIn
) -> None:
    company = books(client, database)
    invoice = issued_invoice(client, company["id"])

    unregistered = send(client, company["id"], invoice["id"])
    assert unregistered.status_code == 409
    assert unregistered.json()["code"] == "not_registered"

    assert register_peppol(client, company["id"]).status_code == 200
    unchecked = send(client, company["id"], invoice["id"])
    assert unchecked.status_code == 409
    assert unchecked.json()["code"] == "not_registered"
    assert not [r for r in stand_in.requests if r[1].endswith("/send")]


def test_a_draft_or_a_buyer_without_an_orgnr_cannot_go(
    client: TestClient, database: sa.Engine, stand_in: StandIn
) -> None:
    company = ready_to_send(client, database, stand_in)
    draft = create_draft(client, company["id"])
    consumer = issued_invoice(client, company["id"], buyer_orgnr=None, buyer_is_business=False)

    for document_id in (draft["id"], consumer["id"]):
        refused = send(client, company["id"], document_id)
        assert refused.status_code == 409, refused.text
        assert refused.json()["code"] == "not_sendable"


def test_a_viewer_cannot_send(client: TestClient, database: sa.Engine, stand_in: StandIn) -> None:
    company = ready_to_send(client, database, stand_in)
    invoice = issued_invoice(client, company["id"])
    join_company(client, company["id"], email="revisor@example.no", role="viewer")

    assert send(client, company["id"], invoice["id"]).status_code == 403


def test_a_keys_send_waits_for_its_person(
    client: TestClient, database: sa.Engine, stand_in: StandIn
) -> None:
    """Whatever the key may do on its own: nothing leaves Myntos without a yes."""
    company = ready_to_send(client, database, stand_in)
    invoice = issued_invoice(client, company["id"])
    header = agent_header(client, company["id"])

    held = send(client, company["id"], invoice["id"], headers=header)

    assert held.status_code == 202, held.text
    assert held.json()["code"] == "approval_pending"
    assert not [r for r in stand_in.requests if r[1].endswith("/send")]
    seen = client.get(held.headers["Location"], headers=header).json()
    assert seen["label"] == "sende fakturaen som EHF til kunden"
