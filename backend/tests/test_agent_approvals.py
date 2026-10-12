"""Requests a key may only ask for, end to end (docs/agents-and-api.md rule 13).

Each rule here is a sentence somebody will be tempted to soften, so each has a
test that fails when it is:

- a request the key may only ask for does nothing until its person says yes,
  and then runs exactly as it was sent, as the key, through every check;
- only the key's own person can say yes, once, and never a key;
- a request nobody decided, or whose key stopped, never runs;
- a malformed request is refused at once rather than held to fail later;
- a key can ask its person to file, and can never file;
- a payment and its receipt are one approval, not two.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient

from myntos.core.clock import utc_now
from tests.conftest import (
    agent_header,
    insert_mva_filing,
    join_company,
    register,
    write,
)

THIS_YEAR = utc_now().year
ORGNR = "910000004"
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def _books(client: TestClient) -> dict[str, Any]:
    session = register(client)
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": session["memberships"][0]["organization_id"],
            "orgnr": ORGNR,
            "name": "Klient En AS",
            "first_financial_year": THIS_YEAR,
            "vat_registered": True,
            "bank_account": "15062033158",
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _asking(client: TestClient, company_id: int, *, post: str = "approve") -> dict[str, str]:
    """A key that writes on its own and may only ask to post (or as ``post`` says)."""
    if post == "approve":
        return agent_header(client, company_id, scopes=("read", "write"), approval_scopes=("post",))
    return agent_header(client, company_id, scopes=("read",), approval_scopes=("write", "post"))


PAYMENT = {
    "type_code": "varekjop",
    "occurred_on": f"{THIS_YEAR}-03-14",
    "gross_amount": "1250.00",
    "description": "Kontorrekvisita",
}


def _pay(client: TestClient, company_id: int, headers: dict[str, str], **extra: Any) -> Any:
    return client.post(
        f"/api/v1/companies/{company_id}/cashflow", json=PAYMENT | extra, headers=headers
    )


def _register(client: TestClient, company_id: int) -> list[dict[str, Any]]:
    response = client.get(f"/api/v1/companies/{company_id}/cashflow")
    assert response.status_code == 200, response.text
    body: list[dict[str, Any]] = response.json()
    return body


def _approve(client: TestClient, company_id: int, approval_id: int) -> Any:
    return write(
        client, "POST", f"/api/v1/companies/{company_id}/godkjenninger/{approval_id}/godkjenn"
    )


# --- Asking does nothing ----------------------------------------------------------


def test_a_request_the_key_may_only_ask_for_waits_and_does_nothing(client: TestClient) -> None:
    books = _books(client)
    header = _asking(client, books["id"])

    held = _pay(client, books["id"], header)

    assert held.status_code == 202, held.text
    body = held.json()
    assert body["code"] == "approval_pending"
    assert body["status"] == "pending"
    assert held.headers["Location"] == (
        f"/api/v1/companies/{books['id']}/godkjenninger/{body['approval_id']}"
    )
    assert _register(client, books["id"]) == []

    seen = client.get(held.headers["Location"], headers=header).json()
    assert seen["status"] == "pending"
    assert seen["label"] == "føre en betaling"
    assert seen["body"]["gross_amount"] == "1250.00"


def test_the_agents_note_travels_with_the_request(client: TestClient) -> None:
    books = _books(client)
    header = _asking(client, books["id"])

    held = _pay(
        client,
        books["id"],
        header | {"Myntos-Note": "F%C3%B8rer%20kvittering%20fra%20Clas%20Ohlson"},
    )

    seen = client.get(held.headers["Location"], headers=header).json()
    assert seen["note"] == "Fører kvittering fra Clas Ohlson"


def test_what_the_key_may_do_on_its_own_it_still_does(client: TestClient) -> None:
    books = _books(client)
    header = _asking(client, books["id"])

    party = client.post(
        f"/api/v1/companies/{books['id']}/parties",
        json={"name": "Clas Ohlson AS", "party_type": "leverandor"},
        headers=header,
    )
    assert party.status_code == 201, party.text


def test_a_preview_runs_for_a_key_that_may_only_ask_to_write(client: TestClient) -> None:
    """A proposal stores nothing, so there is nothing to approve and no point waiting."""
    books = _books(client)
    header = _asking(client, books["id"], post="all")

    review = client.post(
        f"/api/v1/companies/{books['id']}/inngaende-balanse/kontroll",
        json={"lines": []},
        headers=header,
    )
    assert review.status_code != 202, review.text


def test_a_malformed_request_is_refused_not_held(client: TestClient) -> None:
    """A typo is the agent's to hear about now, not the person's to find tomorrow."""
    books = _books(client)
    header = _asking(client, books["id"])

    refused = client.post(
        f"/api/v1/companies/{books['id']}/cashflow",
        json={"type_code": "varekjop", "gross_amount": "ikke et tall"},
        headers=header,
    )
    assert refused.status_code == 422, refused.text
    listed = client.get(f"/api/v1/companies/{books['id']}/godkjenninger", headers=header).json()
    assert listed == []


def test_a_held_request_for_another_company_is_refused(client: TestClient) -> None:
    books = _books(client)
    header = _asking(client, books["id"])

    refused = client.post(
        f"/api/v1/companies/{books['id'] + 1000}/cashflow", json=PAYMENT, headers=header
    )
    assert refused.status_code == 404


def test_a_key_without_the_scope_at_all_is_still_refused(client: TestClient) -> None:
    books = _books(client)
    header = agent_header(client, books["id"], scopes=("read",))

    refused = _pay(client, books["id"], header)
    assert refused.status_code == 403
    assert refused.json()["code"] == "missing_scope"


# --- Saying yes -------------------------------------------------------------------


def test_approving_runs_the_request_as_the_key(client: TestClient) -> None:
    books = _books(client)
    header = _asking(client, books["id"])
    held = _pay(client, books["id"], header).json()

    approved = _approve(client, books["id"], held["approval_id"])

    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "done"
    assert approved.json()["result_status"] == 201
    voucher = approved.json()["result"]
    assert voucher["gross_amount"] == "1250.00"
    # Registered in the person's name, via the key, as if the key had posted it.
    assert voucher["created_by_name"] == "Kari Nordmann"
    assert voucher["created_via"] == "Testagent"
    assert [entry["id"] for entry in _register(client, books["id"])] == [voucher["id"]]

    seen = client.get(
        f"/api/v1/companies/{books['id']}/godkjenninger/{held['approval_id']}", headers=header
    ).json()
    assert seen["status"] == "done"
    assert seen["result"]["id"] == voucher["id"]


def test_a_request_runs_once_however_often_it_is_approved(client: TestClient) -> None:
    books = _books(client)
    header = _asking(client, books["id"])
    held = _pay(client, books["id"], header).json()

    assert _approve(client, books["id"], held["approval_id"]).status_code == 200
    again = _approve(client, books["id"], held["approval_id"])

    assert again.status_code == 409
    assert again.json()["code"] == "approval_not_pending"
    assert len(_register(client, books["id"])) == 1


def test_a_request_the_route_refuses_when_it_runs_says_so(client: TestClient) -> None:
    books = _books(client)
    header = _asking(client, books["id"])
    held = _pay(client, books["id"], header, type_code="finnes-ikke").json()

    approved = _approve(client, books["id"], held["approval_id"])

    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "failed"
    assert approved.json()["result_status"] >= 400
    assert _register(client, books["id"]) == []


def test_only_the_keys_own_person_may_approve(client: TestClient) -> None:
    books = _books(client)
    header = _asking(client, books["id"])
    held = _pay(client, books["id"], header).json()

    join_company(client, books["id"], email="ola@example.no", role="admin")
    refused = _approve(client, books["id"], held["approval_id"])

    # An admin sees it (they manage access) but it is not theirs to approve.
    assert refused.status_code == 403
    assert refused.json()["code"] == "not_your_agent"


def test_a_colleague_who_does_not_manage_access_does_not_see_it(client: TestClient) -> None:
    books = _books(client)
    header = _asking(client, books["id"])
    held = _pay(client, books["id"], header).json()

    join_company(client, books["id"], email="ola@example.no", role="member")
    listed = client.get(f"/api/v1/companies/{books['id']}/godkjenninger").json()
    assert listed == []
    hidden = client.get(f"/api/v1/companies/{books['id']}/godkjenninger/{held['approval_id']}")
    assert hidden.status_code == 404


def test_no_key_may_approve_or_decline(client: TestClient) -> None:
    books = _books(client)
    header = _asking(client, books["id"])
    held = _pay(client, books["id"], header).json()

    for action in ("godkjenn", "avvis"):
        refused = client.post(
            f"/api/v1/companies/{books['id']}/godkjenninger/{held['approval_id']}/{action}",
            json={},
            headers=header,
        )
        assert refused.status_code == 403
        assert refused.json()["code"] == "not_for_agent_keys"


# --- Saying no, or never deciding -------------------------------------------------


def test_a_declined_request_never_runs_and_the_agent_reads_why(client: TestClient) -> None:
    books = _books(client)
    header = _asking(client, books["id"])
    held = _pay(client, books["id"], header).json()

    declined = write(
        client,
        "POST",
        f"/api/v1/companies/{books['id']}/godkjenninger/{held['approval_id']}/avvis",
        json={"note": "Dette er privat, ikke firmaets."},
    )
    assert declined.status_code == 200, declined.text

    seen = client.get(
        f"/api/v1/companies/{books['id']}/godkjenninger/{held['approval_id']}", headers=header
    ).json()
    assert (seen["status"], seen["decision_note"]) == (
        "declined",
        "Dette er privat, ikke firmaets.",
    )
    assert _approve(client, books["id"], held["approval_id"]).status_code == 409
    assert _register(client, books["id"]) == []


def test_an_agent_can_withdraw_what_it_asked(client: TestClient) -> None:
    books = _books(client)
    header = _asking(client, books["id"])
    held = _pay(client, books["id"], header).json()

    gone = client.delete(
        f"/api/v1/companies/{books['id']}/godkjenninger/{held['approval_id']}", headers=header
    )
    assert gone.status_code == 204
    assert _approve(client, books["id"], held["approval_id"]).status_code == 409


def test_a_request_nobody_decided_expires(client: TestClient, database_url: str) -> None:
    books = _books(client)
    header = _asking(client, books["id"])
    held = _pay(client, books["id"], header).json()

    engine = sa.create_engine(database_url.replace("+aiosqlite", ""))
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE agent_approval SET created_at = :then, expires_at = :gone WHERE id = :id"
            ),
            {
                "then": "2020-01-01 00:00:00",
                "gone": "2020-01-15 00:00:00",
                "id": held["approval_id"],
            },
        )
    engine.dispose()

    seen = client.get(f"/api/v1/companies/{books['id']}/godkjenninger/{held['approval_id']}")
    assert seen.json()["status"] == "expired"
    assert _approve(client, books["id"], held["approval_id"]).status_code == 409
    assert _register(client, books["id"]) == []


def test_a_stopped_key_leaves_nothing_to_approve(client: TestClient) -> None:
    books = _books(client)
    header = _asking(client, books["id"])
    held = _pay(client, books["id"], header).json()
    keys = client.get(f"/api/v1/companies/{books['id']}/agent-keys").json()
    write(client, "DELETE", f"/api/v1/companies/{books['id']}/agent-keys/{keys[0]['id']}")

    seen = client.get(f"/api/v1/companies/{books['id']}/godkjenninger/{held['approval_id']}")
    assert seen.json()["status"] == "key_stopped"
    assert _approve(client, books["id"], held["approval_id"]).status_code == 409
    assert _register(client, books["id"]) == []


# --- A payment and its receipt are one approval -----------------------------------


def test_a_receipt_added_to_a_waiting_payment_is_archived_with_it(client: TestClient) -> None:
    books = _books(client)
    header = _asking(client, books["id"])
    held = _pay(client, books["id"], header).json()

    added = client.post(
        f"/api/v1/companies/{books['id']}/godkjenninger/{held['approval_id']}/vedlegg",
        files={"file": ("kvittering.pdf", PDF_BYTES, "application/pdf")},
        headers=header,
    )
    assert added.status_code == 201, added.text
    receipt = added.json()["files"][0]
    assert (receipt["filename"], receipt["attach"], receipt["available"]) == (
        "kvittering.pdf",
        True,
        True,
    )
    # The person can look at it before deciding.
    shown = client.get(
        f"/api/v1/companies/{books['id']}/godkjenninger/{held['approval_id']}/fil?id={receipt['id']}"
    )
    assert shown.content == PDF_BYTES

    approved = _approve(client, books["id"], held["approval_id"]).json()

    assert approved["status"] == "done"
    assert [item["status"] for item in approved["attachments"]] == [201]
    assert approved["files"][0]["available"] is False
    [entry] = _register(client, books["id"])
    assert [item["filename"] for item in entry["attachments"]] == ["kvittering.pdf"]


def test_a_receipt_cannot_be_added_where_it_has_nowhere_to_go(client: TestClient) -> None:
    books = _books(client)
    header = _asking(client, books["id"], post="all")
    held = client.post(
        f"/api/v1/companies/{books['id']}/parties",
        json={"name": "Clas Ohlson AS", "party_type": "leverandor"},
        headers=header,
    ).json()

    refused = client.post(
        f"/api/v1/companies/{books['id']}/godkjenninger/{held['approval_id']}/vedlegg",
        files={"file": ("kvittering.pdf", PDF_BYTES, "application/pdf")},
        headers=header,
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "attachments_not_accepted"


def test_a_file_sent_with_the_request_itself_is_held_and_sent_again(client: TestClient) -> None:
    books = _books(client)
    writer = agent_header(client, books["id"])
    voucher = _pay(client, books["id"], writer).json()
    header = _asking(client, books["id"])

    held = client.post(
        f"/api/v1/companies/{books['id']}/cashflow/{voucher['id']}/dokument",
        files={"file": ("faktura.pdf", PDF_BYTES, "application/pdf")},
        headers=header,
    )
    assert held.status_code == 202, held.text

    approved = _approve(client, books["id"], held.json()["approval_id"]).json()
    assert approved["status"] == "done", approved
    [entry] = _register(client, books["id"])
    assert [item["filename"] for item in entry["attachments"]] == ["faktura.pdf"]


# --- Filing is the person's -------------------------------------------------------


def test_a_key_can_ask_its_person_to_file_and_never_file(client: TestClient) -> None:
    books = _books(client)
    header = agent_header(client, books["id"], scopes=("read",))

    asked = client.post(
        f"/api/v1/companies/{books['id']}/godkjenninger/innsending",
        json={"type": "mva_melding", "aar": THIS_YEAR - 1, "termin": 1},
        headers=header | {"Myntos-Note": "Meldingen er klar til kontroll."},
    )
    assert asked.status_code == 200, asked.text
    body = asked.json()
    assert (body["kind"], body["status"]) == ("filing", "pending")
    assert body["filing"] == {"type": "mva_melding", "aar": THIS_YEAR - 1, "termin": 1}

    again = client.post(
        f"/api/v1/companies/{books['id']}/godkjenninger/innsending",
        json={"type": "mva_melding", "aar": THIS_YEAR - 1, "termin": 1},
        headers=header,
    )
    assert again.json()["id"] == body["id"]

    pressed = _approve(client, books["id"], body["id"])
    assert pressed.status_code == 409
    assert pressed.json()["code"] == "filing_is_done_on_the_page"


def test_a_request_to_file_is_done_once_the_period_is_filed(
    client: TestClient, database_url: str
) -> None:
    books = _books(client)
    header = agent_header(client, books["id"], scopes=("read",))
    asked = client.post(
        f"/api/v1/companies/{books['id']}/godkjenninger/innsending",
        json={"type": "mva_melding", "aar": THIS_YEAR - 1, "termin": 1},
        headers=header,
    ).json()

    filing_id = insert_mva_filing(client, books["id"], year=THIS_YEAR - 1, index=1, status="sendt")
    engine = sa.create_engine(database_url.replace("+aiosqlite", ""))
    with engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE mva_filing SET submitted_at = :now WHERE id = :id"),
            {"now": utc_now().isoformat(sep=" "), "id": filing_id},
        )
    engine.dispose()

    seen = client.get(f"/api/v1/companies/{books['id']}/godkjenninger/{asked['id']}").json()
    assert seen["status"] == "done"

    refused = client.post(
        f"/api/v1/companies/{books['id']}/godkjenninger/innsending",
        json={"type": "mva_melding", "aar": THIS_YEAR - 1, "termin": 1},
        headers=header,
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "already_filed"


def test_a_termin_that_has_not_ended_cannot_be_asked_for(client: TestClient) -> None:
    books = _books(client)
    header = agent_header(client, books["id"], scopes=("read",))

    refused = client.post(
        f"/api/v1/companies/{books['id']}/godkjenninger/innsending",
        json={"type": "mva_melding", "aar": THIS_YEAR + 1, "termin": 6},
        headers=header,
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "period_not_over"


# --- What a key may ask for -------------------------------------------------------


def _make(client: TestClient, company_id: int, scopes: list[str], asks: list[str]) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/agent-keys",
        json={"name": "Claude", "scopes": scopes, "approval_scopes": asks},
    )


def test_a_key_says_what_it_may_only_ask_for(client: TestClient) -> None:
    books = _books(client)
    header = _asking(client, books["id"])

    me = client.get("/api/v1/agent/me", headers=header).json()
    assert (me["scopes"], me["approval_scopes"]) == (["read", "write"], ["post"])
    listed = client.get(f"/api/v1/companies/{books['id']}/agent-keys").json()
    assert listed[0]["approval_scopes"] == ["post"]


def test_the_choices_for_a_key_are_consistent(client: TestClient) -> None:
    books = _books(client)
    write(client, "PUT", f"/api/v1/companies/{books['id']}/agent-access", json={"enabled": True})

    # Posting on its own while having to ask to write.
    assert _make(client, books["id"], ["read", "post"], ["write"]).status_code == 422
    # Posting at all while unable to write.
    assert _make(client, books["id"], ["read"], ["post"]).status_code == 422
    # The same scope both ways.
    assert _make(client, books["id"], ["read", "write"], ["write"]).status_code == 422
    # Reading needs nobody's approval.
    assert _make(client, books["id"], ["read"], ["read"]).status_code == 422
    # Asking for everything, doing nothing but read on its own, is fine.
    assert _make(client, books["id"], ["read"], ["write", "post"]).status_code == 201


def test_a_reader_cannot_make_a_key_that_asks_to_write(client: TestClient) -> None:
    books = _books(client)
    write(client, "PUT", f"/api/v1/companies/{books['id']}/agent-access", json={"enabled": True})
    join_company(client, books["id"], email="ola@example.no", role="viewer")

    refused = _make(client, books["id"], ["read"], ["write"])
    assert refused.status_code == 422
