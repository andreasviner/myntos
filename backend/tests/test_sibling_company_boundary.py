"""The wall between two companies inside the *same* account.

``test_company_boundary.py`` guards the other wall: a company belonging to
somebody else's organization, refused by
:func:`~myntos.api.deps.get_company_context` before a route body ever runs. This
file guards the one that opens *after* that check has already passed.

A regnskapsfører's account holds one company per client, and an ordinary AS that
buys a second business holds two. For two companies in the same organization the
membership join succeeds for both, so every ``404`` the sibling file asserts is
a ``200`` here. What is left standing between a document id belonging to Klient
To and a URL naming Klient En is nothing but the ``company_id`` filter inside
each service call: no membership, no ownership, no second chance. That is the
narrower wall and the one worth a file of its own.

The routes are read out of the OpenAPI schema for the reason the sibling file
gives: a hand-written list is a list of what somebody remembered on the day. It
is read here with one extra demand. Every company-scoped route carrying a second
path parameter must name a kind of id this file knows how to build under the
other company, and one that does not **fails** rather than being skipped. A new
sub-resource is exactly the thing that needs a new check, and a test that
quietly ignores what it has not been taught is worth less than no test.

Each case also proves the id it smuggles is real and readable under the company
that owns it. Without that, every assertion here would pass just as well against
an id that never existed, which is the way a boundary test rots into a test of
nothing at all.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from myntos.api.v1 import approvals as approvals_api
from myntos.core.clock import utc_now
from myntos.integrations import loynd
from myntos.main import create_app
from tests.conftest import (
    agent_header,
    assert_key_refused,
    insert_mva_filing,
    insert_skattemelding_filing,
    is_send,
    register,
    write,
)

# Two real orgnrs, so the check digits hold and both creations succeed.
MINE = "910000004"
SIBLING = "910000012"
CUSTOMER_ORGNR = "915933149"

THIS_YEAR = utc_now().year

PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


# --- The account, and the two sets of books in it ------------------------------------


def _company(client: TestClient, organization_id: int, orgnr: str, name: str) -> dict[str, Any]:
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": organization_id,
            "orgnr": orgnr,
            "name": name,
            "first_financial_year": THIS_YEAR,
            "address_line": "Kirkegata 2",
            "postal_code": "0153",
            "city": "Oslo",
            "vat_registered": True,
            "bank_account": "15062033158",
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _two_companies(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    """One account, one organization, two clients' books inside it.

    The regnskapsfører case, which is the whole reason the organization rather
    than the company is the tenant. Both companies are reachable by the same
    session, which is what makes this different from the cross-account file.
    """
    session = register(client, organization_name="Fjord Regnskap AS")
    organization_id = session["memberships"][0]["organization_id"]
    mine = _company(client, organization_id, MINE, "Klient En AS")
    theirs = _company(client, organization_id, SIBLING, "Klient To AS")
    return mine, theirs


# --- Building a real sub-resource under the *other* company ---------------------------
#
# One factory per kind of id that appears in a path. Each returns the id it
# built and a check that reads it back through the company that owns it, so the
# refusal asserted further down is a refusal of something that exists.


def _a_document(client: TestClient, company_id: int) -> tuple[int, Callable[[], None]]:
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/faktura",
        json={
            "buyer_name": "Kunde AS",
            "buyer_orgnr": CUSTOMER_ORGNR,
            "buyer_address_line": "Storgata 1",
            "buyer_postal_code": "0155",
            "buyer_city": "Oslo",
            "lines": [
                {
                    "description": "Rådgivning mars",
                    "quantity": "10",
                    "unit_price": "1500.00",
                    "tax_code": "3",
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    document_id = int(response.json()["id"])

    def readable() -> None:
        owner = client.get(f"/api/v1/companies/{company_id}/faktura/{document_id}")
        assert owner.status_code == 200, owner.text

    return document_id, readable


def _a_voucher(client: TestClient, company_id: int) -> tuple[int, Callable[[], None]]:
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/cashflow",
        json={
            "type_code": "varekjop",
            "occurred_on": f"{THIS_YEAR}-03-14",
            "gross_amount": "1250.00",
            "description": "Kontorrekvisita",
        },
    )
    assert response.status_code == 201, response.text
    voucher_id = int(response.json()["id"])

    def readable() -> None:
        listed = client.get(f"/api/v1/companies/{company_id}/cashflow")
        assert listed.status_code == 200, listed.text
        assert voucher_id in {entry["id"] for entry in listed.json()}

    return voucher_id, readable


def _an_attachment(client: TestClient, company_id: int) -> tuple[int, Callable[[], None]]:
    voucher_id, _ = _a_voucher(client, company_id)
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/cashflow/{voucher_id}/dokument",
        files={"file": ("kvittering.pdf", PDF_BYTES, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    attachment_id = int(response.json()["id"])

    def readable() -> None:
        owner = client.get(f"/api/v1/companies/{company_id}/dokument/{attachment_id}")
        assert owner.status_code == 200, owner.text
        assert owner.content == PDF_BYTES

    return attachment_id, readable


def _a_party(client: TestClient, company_id: int) -> tuple[int, Callable[[], None]]:
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/parties",
        json={"party_type": "kunde", "name": "Hemmelig Kunde AS"},
    )
    assert response.status_code == 201, response.text
    party_pk = int(response.json()["id"])

    def readable() -> None:
        listed = client.get(f"/api/v1/companies/{company_id}/parties")
        assert listed.status_code == 200, listed.text
        assert party_pk in {party["id"] for party in listed.json()}

    return party_pk, readable


def _a_bank_account(client: TestClient, company_id: int) -> tuple[int, Callable[[], None]]:
    """A second kontonummer on these books.

    The second and not the first: both companies are created with one already,
    so adding another leaves a row that is safe to point a DELETE at without the
    primary-promotion rule deciding the outcome instead of the company filter.
    """
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/bankkontoer",
        json={"account": "98765432103", "label": "Skattetrekk"},
    )
    assert response.status_code == 201, response.text
    accounts = response.json()["bank_accounts"]
    added = next(account for account in accounts if account["account"] == "98765432103")
    bank_account_id = int(added["id"])

    def readable() -> None:
        owner = client.get(f"/api/v1/companies/{company_id}")
        assert owner.status_code == 200, owner.text
        assert bank_account_id in {account["id"] for account in owner.json()["bank_accounts"]}

    return bank_account_id, readable


def _an_invitation(client: TestClient, company_id: int) -> tuple[int, Callable[[], None]]:
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/invitations",
        json={"email": "hemmelig@example.no", "role": "member"},
    )
    assert response.status_code == 201, response.text
    invitation_id = int(response.json()["id"])

    def readable() -> None:
        listed = client.get(f"/api/v1/companies/{company_id}/invitations")
        assert listed.status_code == 200, listed.text
        assert invitation_id in {item["id"] for item in listed.json()}

    return invitation_id, readable


def _a_member(client: TestClient, company_id: int) -> tuple[int, Callable[[], None]]:
    """Somebody else with access to *these* books, and their user id.

    The whole round trip, because there is no other way to make one: access is
    a row per person per company, so a second person has to be invited to this
    company and accept. Their own browser, so the owner stays signed in here.
    """
    invited = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/invitations",
        json={"email": f"kollega{company_id}@example.no", "role": "member"},
    )
    assert invited.status_code == 201, invited.text
    token = invited.json()["invite_url"].split("token=", maxsplit=1)[1]

    colleague = TestClient(client.app, base_url=str(client.base_url))
    user_id = int(register(colleague, f"kollega{company_id}@example.no")["user"]["id"])
    accepted = write(colleague, "POST", "/api/v1/auth/invitations/accept", json={"token": token})
    assert accepted.status_code == 200, accepted.text

    def readable() -> None:
        listed = client.get(f"/api/v1/companies/{company_id}/members")
        assert listed.status_code == 200, listed.text
        assert user_id in {member["user_id"] for member in listed.json()}

    return user_id, readable


def _a_filing(client: TestClient, company_id: int) -> tuple[int, Callable[[], None]]:
    """An MVA-melding filing, already sent, with an Altinn instance on it.

    Written straight into the database rather than through ``/kontroll``,
    which would need ID-porten and Skatteetaten's validation standing up. The
    row is the thing the wall protects; how it got there is not. Sent, so that
    every route on it, the receipt fetch included, gets as far as the lookup.
    """
    filing_id = insert_mva_filing(
        client,
        company_id,
        year=THIS_YEAR - 1,
        index=1,
        status="sendt",
        fastsatt="1250.00",
        altinn_instance_id="50001337/6c9e1b1a-3a3c-4a1e-9b3f-2f0a7d5c8e11",
    )

    def readable() -> None:
        owner = client.get(f"/api/v1/companies/{company_id}/mva/innsendinger/{filing_id}")
        assert owner.status_code == 200, owner.text

    return filing_id, readable


def _a_skattemelding_filing(client: TestClient, company_id: int) -> tuple[int, Callable[[], None]]:
    """A skattemelding filing, already sent, with an Altinn instance on it.

    The MVA filing's reasoning: written straight in, and sent, so the answer
    fetch gets as far as the lookup too.
    """
    filing_id = insert_skattemelding_filing(
        client,
        company_id,
        year=THIS_YEAR - 1,
        status="sendt",
        skattemessig_resultat="381000.00",
        altinn_instance_id="50001337/0b7c2d4e-5f60-4a71-8b92-a3b4c5d6e7f8",
    )

    def readable() -> None:
        owner = client.get(f"/api/v1/companies/{company_id}/skattemelding/innsendinger/{filing_id}")
        assert owner.status_code == 200, owner.text

    return filing_id, readable


CAMT_MARCH = (Path(__file__).parent / "fixtures" / "bank" / "camt053_v02_mars.xml").read_bytes()


def _imported(client: TestClient, company_id: int) -> dict[str, Any]:
    """A March statement imported into ``company_id``'s books.

    Both companies are created with the same kontonummer, the one the fixture
    is for, so the same file imports into either: which is the point, because a
    statement only one of them could hold would prove nothing about the filter.
    """
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/bank/kontoutskrifter",
        files={"file": ("mars.xml", CAMT_MARCH, "application/xml")},
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _a_statement(client: TestClient, company_id: int) -> tuple[int, Callable[[], None]]:
    statement_id = int(_imported(client, company_id)["statements"][0]["id"])

    def readable() -> None:
        owner = client.get(
            f"/api/v1/companies/{company_id}/bank/kontoutskrifter/{statement_id}/fil"
        )
        assert owner.status_code == 200, owner.text
        assert owner.content == CAMT_MARCH

    return statement_id, readable


def _a_bank_line(client: TestClient, company_id: int) -> tuple[int, Callable[[], None]]:
    """The 49-krone fee, which no voucher explains, so every route reaches its lookup."""
    _imported(client, company_id)
    lines = client.get(f"/api/v1/companies/{company_id}/bank/linjer").json()
    line_id = int(next(line["id"] for line in lines if line["amount"] == "49.00"))

    def readable() -> None:
        listed = client.get(f"/api/v1/companies/{company_id}/bank/linjer")
        assert listed.status_code == 200, listed.text
        assert line_id in {line["id"] for line in listed.json()}

    return line_id, readable


def _an_account(client: TestClient, company_id: int) -> tuple[int, Callable[[], None]]:
    """An account somebody added to the other company's kontoplan.

    Added rather than seeded, so it has no postings and no lock: a DELETE aimed
    at it would succeed for its owner, and the only thing left to refuse it
    here is the company filter.
    """
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/kontoplan",
        json={"number": "6820", "description": "Hemmelig konto"},
    )
    assert response.status_code == 201, response.text
    account_id = int(
        next(item for item in response.json()["accounts"] if item["number"] == "6820")["id"]
    )

    def readable() -> None:
        owner = client.get(f"/api/v1/companies/{company_id}/kontoplan")
        assert owner.status_code == 200, owner.text
        assert account_id in {item["id"] for item in owner.json()["accounts"]}

    return account_id, readable


def _an_adjustment(client: TestClient, company_id: int) -> tuple[int, Callable[[], None]]:
    """An uttak in the other company's books, which a reversal would undo."""
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/mva/spesifikasjoner",
        json={
            "spesifikasjon": "uttak",
            "occurred_on": f"{THIS_YEAR}-03-14",
            "tax_code": "3",
            "amount": "1000.00",
            "description": "Varer til eget bruk",
        },
    )
    assert response.status_code == 201, response.text
    adjustment_id = int(response.json()["id"])

    def readable() -> None:
        owner = client.get(
            f"/api/v1/companies/{company_id}/mva/spesifikasjoner", params={"aar": THIS_YEAR}
        )
        assert owner.status_code == 200, owner.text
        assert adjustment_id in {item["id"] for item in owner.json()["entries"]}

    return adjustment_id, readable


def _a_dimension(client: TestClient, company_id: int) -> tuple[int, Callable[[], None]]:
    """A dimension the other company sorts its books by, with nothing posted to it.

    Unused, so a DELETE aimed at it would succeed for its owner, and the only
    thing left to refuse it here is the company filter.
    """
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/dimensjoner",
        json={"code": "HEMMELIG", "description": "Hemmelig dimensjon"},
    )
    assert response.status_code == 201, response.text
    dimension_id = int(next(item for item in response.json() if item["code"] == "HEMMELIG")["id"])

    def readable() -> None:
        owner = client.get(f"/api/v1/companies/{company_id}/dimensjoner")
        assert owner.status_code == 200, owner.text
        assert dimension_id in {item["id"] for item in owner.json()}

    return dimension_id, readable


def _a_dimension_value(client: TestClient, company_id: int) -> tuple[int, Callable[[], None]]:
    """An avdeling in the other company's books: its name would say how it is organised."""
    dimension_id, _ = _a_dimension(client, company_id)
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/dimensjoner/{dimension_id}/verdier",
        json={"value": "20", "description": "Hemmelig avdeling"},
    )
    assert response.status_code == 201, response.text
    dimension = next(item for item in response.json() if item["id"] == dimension_id)
    value_id = int(dimension["values"][0]["id"])

    def readable() -> None:
        owner = client.get(f"/api/v1/companies/{company_id}/dimensjoner")
        assert owner.status_code == 200, owner.text
        ids = {value["id"] for item in owner.json() for value in item["values"]}
        assert value_id in ids

    return value_id, readable


# Keyed by the path parameter's own name, because that is what the schema hands
# over and what a new route will spell out. A name missing from here is a
# failure below, never a skip.
def _an_agent_key(client: TestClient, company_id: int) -> tuple[int, Callable[[], None]]:
    """A key made for the other client's books: access to them, by another name.

    The one sub-resource that is itself a way in. Stopping the sibling's key
    through this company's URL would be an outage for the other client; reading
    it would say who in the other client lets which agent in.
    """
    switched = write(
        client, "PUT", f"/api/v1/companies/{company_id}/agent-access", json={"enabled": True}
    )
    assert switched.status_code == 200, switched.text
    made = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/agent-keys",
        json={"name": "Klient Tos agent", "scopes": ["read"]},
    )
    assert made.status_code == 201, made.text
    key_id = int(made.json()["key"]["id"])

    def readable() -> None:
        owner = client.get(f"/api/v1/companies/{company_id}/agent-keys")
        assert owner.status_code == 200, owner.text
        assert key_id in [key["id"] for key in owner.json()]

    return key_id, readable


def _an_approval(client: TestClient, company_id: int) -> tuple[int, Callable[[], None]]:
    """A payment the sibling's agent asked to post, waiting for its person.

    Approving it through this company's URL would post a voucher in the other
    client's books; reading it would show the other client's payment and the
    receipt that came with it.
    """
    header = agent_header(client, company_id, scopes=("read", "write"), approval_scopes=("post",))
    held = client.post(
        f"/api/v1/companies/{company_id}/cashflow", json=_CASHFLOW_BODY, headers=header
    )
    assert held.status_code == 202, held.text
    approval_id = int(held.json()["approval_id"])

    def readable() -> None:
        owner = client.get(f"/api/v1/companies/{company_id}/godkjenninger/{approval_id}")
        assert owner.status_code == 200, owner.text

    return approval_id, readable


def _an_import(client: TestClient, company_id: int) -> tuple[int, Callable[[], None]]:
    """The sibling's SAF-T files, uploaded to be read on the server.

    Reading it through this company's URL would show the other client's books
    as its old program kept them; posting it would bring them into these; and
    throwing it away would lose the other client's upload. Any file will do:
    one that cannot be read is still an import, and still the sibling's.
    """
    made = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/saft/innlesing",
        files={"files": ("saft.xml", b"<AuditFile/>", "application/xml")},
    )
    assert made.status_code == 202, made.text
    import_id = int(made.json()["id"])

    def readable() -> None:
        owner = client.get(f"/api/v1/companies/{company_id}/saft/innlesing/{import_id}")
        assert owner.status_code == 200, owner.text

    return import_id, readable


def _a_printout_upload(client: TestClient, company_id: int) -> tuple[int, Callable[[], None]]:
    """The sibling's printout of its old program's vouchers, uploaded to be archived.

    Reading it through this company's URL would show which documents the other
    client has and on which vouchers; archiving it would put the other client's
    receipts in these books for good. A SAF-T import's id is not one: the
    documents' routes refuse it as unknown, which would prove nothing here. Any
    file will do, as for the import.
    """
    made = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/saft/vedlegg",
        files={"files": ("bilag.pdf", b"%PDF-1.4 ikke en utskrift", "application/pdf")},
    )
    assert made.status_code == 202, made.text
    import_id = int(made.json()["id"])

    def readable() -> None:
        owner = client.get(f"/api/v1/companies/{company_id}/saft/vedlegg/{import_id}")
        assert owner.status_code == 200, owner.text

    return import_id, readable


def _an_asset(client: TestClient, company_id: int) -> tuple[int, Callable[[], None]]:
    """A machine in the sibling's anleggsregister.

    Changing it, disposing of it or deleting it through this company's URL
    would change the other client's saldo, and so its skattemelding.
    """
    made = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/anlegg",
        json={
            "description": "Hemmelig maskin",
            "saldogruppe": "d",
            "account_number": "1250",
            "acquired_on": f"{THIS_YEAR}-03-01",
            "cost": "50000.00",
        },
    )
    assert made.status_code == 201, made.text
    asset_id = int(made.json()["id"])

    def readable() -> None:
        owner = client.get(f"/api/v1/companies/{company_id}/anlegg")
        assert owner.status_code == 200, owner.text
        assert asset_id in {item["id"] for item in owner.json()["assets"]}

    return asset_id, readable


@contextmanager
def _a_model() -> Iterator[None]:
    """A Løynd that reads every file as one receipt and answers every question.

    Only for as long as the block runs: a reading is kept only when a model was
    asked, so building one needs a model, and nothing after the block should
    find one there by accident.
    """

    async def extract(*, content: bytes, filename: str, content_type: str) -> Any:
        del content, filename, content_type
        return loynd.ExtractedDocument(text="Kvittering\nSkjermkort\nTotalt 4 990,00 kr")

    async def complete(**_: Any) -> str:
        return "1"

    async def stream(**_: Any) -> Any:
        yield "DATO: UKJENT\nVALUTA: NOK\nMOTPART: UKJENT\nBESKRIVELSE: Skjermkort\n"
        yield "SPØRSMÅL: Er skjermkortet til selskapets drift?"

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(loynd, "is_configured", lambda: True)
        patch.setattr(loynd, "extract_file", extract)
        patch.setattr(loynd, "complete", complete)
        patch.setattr(loynd, "stream", stream)
        yield


def _a_reading(client: TestClient, company_id: int) -> tuple[str, Callable[[], None]]:
    """A bilag the sibling has read and is answering questions about.

    The reading is the documents' text and the model's conversation about them,
    and answering through this company's URL would continue the other client's
    conversation and hand back what their bilag says.
    """
    with _a_model():
        read = write(
            client,
            "POST",
            f"/api/v1/companies/{company_id}/cashflow/forslag",
            files=[("files", ("kvittering.pdf", PDF_BYTES, "application/pdf"))],
        )
    assert read.status_code == 200, read.text
    reading_id = read.json()["reading_id"]
    assert isinstance(reading_id, str)

    def readable() -> None:
        with _a_model():
            owner = write(
                client,
                "POST",
                f"/api/v1/companies/{company_id}/cashflow/forslag/{reading_id}/svar",
                json={"answers": []},
            )
        assert owner.status_code == 200, owner.text

    return reading_id, readable


FACTORIES: dict[str, Callable[[TestClient, int], tuple[int | str, Callable[[], None]]]] = {
    "document_id": _a_document,
    "voucher_id": _a_voucher,
    "attachment_id": _an_attachment,
    "party_pk": _a_party,
    "bank_account_id": _a_bank_account,
    "invitation_id": _an_invitation,
    # A person, not a record, and the one whose boundary is easiest to get
    # wrong: the id is global, so a member of one company is a perfectly real
    # user id to hand a route about another.
    "user_id": _a_member,
    # What a company told Skatteetaten it owes, and the receipt for it.
    "filing_id": _a_filing,
    # The bank's own statement of a client's account, and each movement on it.
    "statement_id": _a_statement,
    "line_id": _a_bank_line,
    # The kontoplan: an account the sibling added, renamed or grouped here
    # would change what the other client's næringsspesifikasjon says.
    "account_id": _an_account,
    # An uttak or tap på krav, which is a line in the other client's melding.
    "adjustment_id": _an_adjustment,
    # A key an agent works through. Stopping it is access management.
    "key_id": _an_agent_key,
    # What a key asked to do and waits for its person: approving the sibling's
    # would post in the other client's books.
    "approval_id": _an_approval,
    # A dimension and one of its values: renaming the sibling's avdeling, or
    # reading its result, crosses into the other client's books.
    "dimension_id": _a_dimension,
    "dimension_value_id": _a_dimension_value,
    # The previous program's files, read on the server: the other client's
    # books as they were before Myntos.
    "import_id": _an_import,
    # A driftsmiddel: the other client's saldo, and what it deducts.
    "asset_id": _an_asset,
    # A bilag being answered about: the other client's documents, read, and
    # the model's conversation about them. The id is random rather than
    # sequential, which is no reason to skip the wall.
    "reading_id": _a_reading,
}

# Two kinds of filing are both ``{filing_id}``: the MVA-melding's and the
# skattemelding's, in two tables. Keyed by the name alone, the skattemelding
# routes would be handed an MVA filing's id, which no skattemelding filing
# has, and the 404 would prove nothing. A path naming its own kind of filing
# gets its own factory.
FACTORIES_BY_SECTION: dict[
    tuple[str, str], Callable[[TestClient, int], tuple[int | str, Callable[[], None]]]
] = {
    ("/skattemelding/", "filing_id"): _a_skattemelding_filing,
    # The documents' uploads share the SAF-T imports' table and ``{import_id}``,
    # but each set of routes reads only its own kind.
    ("/saft/vedlegg/", "import_id"): _a_printout_upload,
}


def _factory(
    path: str, parameter: str
) -> Callable[[TestClient, int], tuple[int | str, Callable[[], None]]]:
    for (section, name), factory in FACTORIES_BY_SECTION.items():
        if name == parameter and section in path:
            return factory
    return FACTORIES[parameter]


# --- What each route needs before it will look the id up ------------------------------
#
# The cross-account file sends an empty body to every route and still gets its
# 404, because ``get_company_context`` refuses a company the caller is not in
# before FastAPI ever reads the payload. Here that dependency *succeeds*, because
# both companies are the caller's, so an empty body reaches validation and comes back
# 422, and a 422 proves nothing at all: the lookup this file is about never ran.
#
# So the writes carry a payload valid enough to get past validation and reach
# the service call. Keyed by the route itself and checked for completeness
# below, so a new sub-resource route fails here with an instruction rather than
# being quietly exercised with a body it rejects.

_CASHFLOW_BODY: dict[str, Any] = {
    "type_code": "varekjop",
    "occurred_on": f"{THIS_YEAR}-03-14",
    "gross_amount": "990.00",
    "description": "Rettet beløp",
}

# A composite correction any business may post: a sale less a bank fee. Valid
# for the voucher's owner, so the only thing that can refuse it for the sibling
# is the company filter on the voucher it reverses.
_COMPOSITE_BODY: dict[str, Any] = {
    "occurred_on": f"{THIS_YEAR}-03-14",
    "description": "Rettet oppgjør",
    "lines": [
        {"type_code": "salg_hoy_sats", "gross_amount": "1250.00"},
        {"type_code": "bankgebyr", "gross_amount": "25.00"},
    ],
}

_FAKTURA_BODY: dict[str, Any] = {
    "buyer_name": "Kunde AS",
    "lines": [
        {
            "description": "Rådgivning",
            "quantity": "1",
            "unit_price": "1000.00",
            "tax_code": "3",
        }
    ],
}

_PARTY_BODY: dict[str, Any] = {"name": "Rettet Navn AS"}

# A rename and nothing else. Not the account number: a valid one would have to
# be a number neither company already holds, and the duplicate check would then
# be what answered rather than the company filter this file is about.
_BANK_ACCOUNT_BODY: dict[str, Any] = {"label": "Rettet navn"}

# A balanced manual voucher, so the correction gets past its own review and as
# far as looking the original up.
_MANUAL_BODY: dict[str, Any] = {
    "occurred_on": "2026-03-31",
    "description": "Periodisering",
    "explanation": "Husleie for april betalt i mars.",
    "lines": [
        {"account_number": "1700", "debit": "1000.00"},
        {"account_number": "6300", "credit": "1000.00"},
    ],
}

_COMPANIES = "/api/v1/companies/{company_id}"

REQUESTS: dict[tuple[str, str], dict[str, Any]] = {
    ("PUT", f"{_COMPANIES}/cashflow/{{voucher_id}}"): {"json": _CASHFLOW_BODY},
    ("PUT", f"{_COMPANIES}/cashflow/sammensatt/{{voucher_id}}"): {"json": _COMPOSITE_BODY},
    ("POST", f"{_COMPANIES}/cashflow/{{voucher_id}}/dokument"): {
        "files": {"file": ("kvittering.pdf", PDF_BYTES, "application/pdf")}
    },
    ("POST", f"{_COMPANIES}/cashflow/{{voucher_id}}/reversal"): {},
    # The factory's voucher is a payment, which the manual-voucher routes do not
    # know either; test_manuelt_bilag.py smuggles a sibling's real manual voucher.
    ("GET", f"{_COMPANIES}/manuelt-bilag/{{voucher_id}}"): {},
    ("PUT", f"{_COMPANIES}/manuelt-bilag/{{voucher_id}}"): {"json": _MANUAL_BODY},
    ("POST", f"{_COMPANIES}/manuelt-bilag/{{voucher_id}}/reversering"): {
        "json": {"explanation": "Ført to ganger ved en feil."}
    },
    ("GET", f"{_COMPANIES}/dokument/{{attachment_id}}"): {},
    # The fixture attachment is a PDF, which has no thumbnail, so the owner gets
    # a 404 here too. The wall on this route is tested with a photograph in
    # test_bilag_images.py; this entry keeps the sibling check walking it.
    ("GET", f"{_COMPANIES}/dokument/{{attachment_id}}/miniatyr"): {},
    ("GET", f"{_COMPANIES}/faktura/{{document_id}}"): {},
    ("PATCH", f"{_COMPANIES}/faktura/{{document_id}}"): {"json": _FAKTURA_BODY},
    ("DELETE", f"{_COMPANIES}/faktura/{{document_id}}"): {},
    ("GET", f"{_COMPANIES}/faktura/{{document_id}}/ehf"): {},
    ("GET", f"{_COMPANIES}/faktura/{{document_id}}/kontroll"): {},
    ("POST", f"{_COMPANIES}/faktura/{{document_id}}/kreditnota"): {
        "json": {"note": "Feil fakturert"}
    },
    ("GET", f"{_COMPANIES}/faktura/{{document_id}}/pdf"): {},
    ("POST", f"{_COMPANIES}/faktura/{{document_id}}/utsted"): {},
    ("PATCH", f"{_COMPANIES}/parties/{{party_pk}}"): {"json": _PARTY_BODY},
    ("PATCH", f"{_COMPANIES}/bankkontoer/{{bank_account_id}}"): {"json": _BANK_ACCOUNT_BODY},
    ("DELETE", f"{_COMPANIES}/bankkontoer/{{bank_account_id}}"): {},
    ("DELETE", f"{_COMPANIES}/invitations/{{invitation_id}}"): {},
    ("DELETE", f"{_COMPANIES}/agent-keys/{{key_id}}"): {},
    ("GET", f"{_COMPANIES}/godkjenninger/{{approval_id}}"): {},
    ("DELETE", f"{_COMPANIES}/godkjenninger/{{approval_id}}"): {},
    ("GET", f"{_COMPANIES}/godkjenninger/{{approval_id}}/fil"): {"params": {"id": 1}},
    ("POST", f"{_COMPANIES}/godkjenninger/{{approval_id}}/godkjenn"): {"json": {"files": []}},
    ("POST", f"{_COMPANIES}/godkjenninger/{{approval_id}}/avvis"): {"json": {}},
    ("POST", f"{_COMPANIES}/godkjenninger/{{approval_id}}/vedlegg"): {
        "files": {"file": ("kvittering.pdf", PDF_BYTES, "application/pdf")}
    },
    ("POST", f"{_COMPANIES}/invitations/{{invitation_id}}/send-again"): {},
    ("PATCH", f"{_COMPANIES}/members/{{user_id}}"): {"json": {"role": "viewer"}},
    ("DELETE", f"{_COMPANIES}/members/{{user_id}}"): {},
    ("GET", f"{_COMPANIES}/mva/innsendinger/{{filing_id}}"): {},
    ("GET", f"{_COMPANIES}/mva/innsendinger/{{filing_id}}/melding.xml"): {},
    ("GET", f"{_COMPANIES}/mva/innsendinger/{{filing_id}}/dokument"): {
        "params": {"type": "kvittering"}
    },
    # The figure the sibling's filing carries, confirmed: a body the route would
    # accept from the filing's owner, so the only thing left to refuse it is
    # the company filter on the lookup.
    ("POST", f"{_COMPANIES}/mva/innsendinger/{{filing_id}}/send"): {
        "json": {"fastsatt_merverdiavgift": "1250.00", "bekreftet": True}
    },
    ("POST", f"{_COMPANIES}/mva/innsendinger/{{filing_id}}/tilbakemelding"): {},
    ("POST", f"{_COMPANIES}/mva/innsendinger/{{filing_id}}/angre"): {},
    ("GET", f"{_COMPANIES}/skattemelding/innsendinger/{{filing_id}}"): {},
    ("GET", f"{_COMPANIES}/skattemelding/innsendinger/{{filing_id}}/dokument"): {
        "params": {"type": "naeringsspesifikasjon"}
    },
    # The sibling filing's own figure, confirmed, as on the MVA side.
    ("POST", f"{_COMPANIES}/skattemelding/innsendinger/{{filing_id}}/send"): {
        "json": {"skattemessig_resultat": "381000.00", "bekreftet": True}
    },
    ("POST", f"{_COMPANIES}/skattemelding/innsendinger/{{filing_id}}/tilbakemelding"): {},
    ("GET", f"{_COMPANIES}/bank/kontoutskrifter/{{statement_id}}/fil"): {},
    # Any voucher id in the body: the line is looked up first, through the
    # company, and that lookup is the thing being refused.
    ("POST", f"{_COMPANIES}/bank/linjer/{{line_id}}/avstemming"): {"json": {"voucher_id": 1}},
    ("DELETE", f"{_COMPANIES}/bank/linjer/{{line_id}}/avstemming"): {},
    ("POST", f"{_COMPANIES}/bank/linjer/{{line_id}}/bilag"): {
        "json": {"type_code": "bankgebyr", "description": "Kontohold"}
    },
    ("PATCH", f"{_COMPANIES}/kontoplan/{{account_id}}"): {"json": {"description": "Rettet"}},
    ("DELETE", f"{_COMPANIES}/kontoplan/{{account_id}}"): {},
    # A pair that is in the published list, for a year both companies have, so
    # the grouping check passes and only the company filter can refuse it.
    ("PUT", f"{_COMPANIES}/kontoplan/{{account_id}}/gruppering"): {
        "json": {
            "year": THIS_YEAR,
            "grouping_category": "annenDriftskostnad",
            "grouping_code": "6350",
        }
    },
    ("POST", f"{_COMPANIES}/mva/spesifikasjoner/{{adjustment_id}}/reversering"): {},
    ("PATCH", f"{_COMPANIES}/dimensjoner/{{dimension_id}}"): {"json": {"description": "Rettet"}},
    ("DELETE", f"{_COMPANIES}/dimensjoner/{{dimension_id}}"): {},
    # A code neither company has, so the duplicate check cannot be what answers.
    ("POST", f"{_COMPANIES}/dimensjoner/{{dimension_id}}/verdier"): {
        "json": {"value": "99", "description": "Ny verdi"}
    },
    ("GET", f"{_COMPANIES}/dimensjoner/{{dimension_id}}/resultat"): {},
    ("PATCH", f"{_COMPANIES}/dimensjonsverdier/{{dimension_value_id}}"): {
        "json": {"description": "Rettet"}
    },
    ("DELETE", f"{_COMPANIES}/dimensjonsverdier/{{dimension_value_id}}"): {},
    ("GET", f"{_COMPANIES}/saft/innlesing/{{import_id}}"): {},
    ("DELETE", f"{_COMPANIES}/saft/innlesing/{{import_id}}"): {},
    ("POST", f"{_COMPANIES}/saft/innlesing/{{import_id}}/foring"): {},
    ("GET", f"{_COMPANIES}/saft/vedlegg/{{import_id}}"): {},
    ("DELETE", f"{_COMPANIES}/saft/vedlegg/{{import_id}}"): {},
    ("POST", f"{_COMPANIES}/saft/vedlegg/{{import_id}}/arkivering"): {},
    ("PATCH", f"{_COMPANIES}/anlegg/{{asset_id}}"): {"json": {"description": "Rettet"}},
    ("DELETE", f"{_COMPANIES}/anlegg/{{asset_id}}"): {},
    ("POST", f"{_COMPANIES}/anlegg/{{asset_id}}/avgang"): {
        "json": {"disposed_on": f"{THIS_YEAR}-06-01", "proceeds": "10000.00"}
    },
    ("DELETE", f"{_COMPANIES}/anlegg/{{asset_id}}/avgang"): {},
    ("POST", f"{_COMPANIES}/cashflow/forslag/{{reading_id}}/svar"): {"json": {"answers": []}},
}


# --- The routes -----------------------------------------------------------------------


def _sub_resource_routes() -> list[tuple[str, str, str]]:
    """``(method, path, parameter)`` for every company-scoped route with a second id.

    The second id is the one that can cross the wall. A route whose only
    parameter is ``{company_id}`` is already covered by
    ``test_company_boundary.py`` and has nothing to smuggle.
    """
    schema = create_app().openapi()
    found: list[tuple[str, str, str]] = []
    for path, operations in sorted(schema["paths"].items()):
        if "{company_id}" not in path:
            continue
        extra = [name for name in re.findall(r"\{([^}]+)\}", path) if name != "company_id"]
        if not extra:
            continue
        # More than one would mean a route nesting two sub-resources, which does
        # not exist yet. Asserting it rather than looping keeps the day it does
        # from being the day this file silently checks half of it.
        assert len(extra) == 1, f"{path} nests more than one sub-resource id: {extra}"
        for method in sorted(operations):
            if method in {"get", "post", "patch", "put", "delete"}:
                found.append((method.upper(), path, extra[0]))
    return found


def test_there_are_sub_resource_routes_to_check() -> None:
    """A guard on the guard, as in the sibling file.

    If the paths are ever restructured so that no route carries a second id,
    every case below would pass by checking nothing, silently, and for as long
    as it took somebody to notice.
    """
    assert len(_sub_resource_routes()) > 8


def test_every_sub_resource_id_can_be_built() -> None:
    """No route may name an id this file does not know how to forge.

    The point of failing rather than skipping: a route added next year for, say,
    ``{avstemming_id}`` is a new way for one client's books to be read through
    another client's URL, and the moment to notice is when it is written.
    """
    unknown = {parameter for _, _, parameter in _sub_resource_routes()} - set(FACTORIES)
    assert not unknown, (
        f"no factory for {sorted(unknown)}: add one to FACTORIES so the routes "
        "using it are actually checked"
    )


def test_every_route_has_a_request_that_reaches_the_lookup() -> None:
    """And no route may be exercised with a body it would reject out of hand.

    Both directions are asserted. A missing entry is a route going unchecked; a
    leftover one is a route that has moved, taking its check with it and leaving
    a payload behind that no longer describes anything.
    """
    routes = {(method, path) for method, path, _ in _sub_resource_routes()}
    missing = routes - set(REQUESTS)
    stale = set(REQUESTS) - routes
    assert not missing, (
        f"no request recorded for {sorted(missing)}: add one to REQUESTS, valid "
        "enough that the route gets as far as looking the id up"
    )
    assert not stale, f"REQUESTS describes routes that no longer exist: {sorted(stale)}"


@pytest.mark.parametrize(("method", "path", "parameter"), _sub_resource_routes())
def test_no_route_reaches_a_sibling_companys_records(
    client: TestClient, method: str, path: str, parameter: str
) -> None:
    """A sibling's id, in your own company's URL, is a 404 on every route.

    Both companies belong to the caller. The company id in the path is one they
    may certainly use, the session is theirs, and the permission check passes.
    Only the ``company_id`` filter inside the service call stands between the
    request and another client's invoice, which is why it is asserted here one
    route at a time rather than trusted because it is written down somewhere.

    404 and not 403, for the reason the cross-account file gives: 403 would
    confirm the id is real, and these ids are sequential.
    """
    mine, theirs = _two_companies(client)
    smuggled, readable_by_its_owner = _factory(path, parameter)(client, theirs["id"])

    # The record exists and its own company can read it. Without this the
    # refusal below would be indistinguishable from a refusal of nothing.
    readable_by_its_owner()

    url = path.replace("{company_id}", str(mine["id"])).replace(f"{{{parameter}}}", str(smuggled))
    assert "{" not in url, f"unsubstituted parameter left in {url}"

    response = write(client, method, url, **REQUESTS[(method, path)])

    assert response.status_code == 404, (
        f"{method} {path} answered {response.status_code} for a sibling company's "
        f"{parameter}: {response.text[:200]}"
    )


@pytest.mark.parametrize(("method", "path", "parameter"), _sub_resource_routes())
def test_no_route_reaches_a_sibling_companys_records_with_a_key(
    client: TestClient,
    method: str,
    path: str,
    parameter: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The narrow wall again, with a key for the caller's own company (rule 9).

    The key is pinned to Klient En and the URL names Klient En, so the key's
    own gate lets the request through: what is left is the same ``company_id``
    filter the session version tests. A route a key may not call at all
    answers that instead, before any id is read.

    A send (``key_send``) is held for the person before any id is read, so its
    202 is the same for a real id and a made-up one and says nothing; the wall
    is where it runs, so it is approved here and must end in the 404.
    """
    mine, theirs = _two_companies(client)
    smuggled, readable_by_its_owner = _factory(path, parameter)(client, theirs["id"])
    readable_by_its_owner()
    header = agent_header(client, mine["id"])

    url = path.replace("{company_id}", str(mine["id"])).replace(f"{{{parameter}}}", str(smuggled))
    response = write(client, method, url, headers=header, **REQUESTS[(method, path)])

    if is_send(method, path):
        assert response.status_code == 202, response.text
        approval_id = response.json()["approval_id"]

        async def logged_in(db: Any, user: Any) -> bool:
            return True

        monkeypatch.setattr(approvals_api, "has_usable_login", logged_in)
        ran = write(
            client,
            "POST",
            f"/api/v1/companies/{mine['id']}/godkjenninger/{approval_id}/godkjenn",
            json={"files": []},
        )
        assert ran.status_code == 200, ran.text
        assert (ran.json()["status"], ran.json()["result_status"]) == ("failed", 404), ran.text
        return
    assert_key_refused(response, method, path)


def _company_routes() -> list[tuple[str, str]]:
    schema = create_app().openapi()
    return [
        (method.upper(), path)
        for path, operations in sorted(schema["paths"].items())
        if "{company_id}" in path
        for method in sorted(operations)
        if method in {"get", "post", "patch", "put", "delete"}
    ]


@pytest.mark.parametrize(("method", "path"), _company_routes())
def test_a_key_opens_no_company_but_its_own(client: TestClient, method: str, path: str) -> None:
    """A key for Klient En cannot open Klient To, though its person can.

    The membership join would let this through: the person behind the key is
    a member of both. Only the key's own pin stands in the way, and the answer
    is the 404 a stranger gets, so an agent cannot even learn that its person
    keeps other books (docs/agents-and-api.md rule 3).
    """
    mine, theirs = _two_companies(client)
    header = agent_header(client, mine["id"])

    url = re.sub(r"\{[^}]+\}", "1", path.replace("{company_id}", str(theirs["id"])))
    response = write(client, method, url, json={}, headers=header)

    assert_key_refused(response, method, path)


def test_a_sibling_companys_records_stay_out_of_the_listings(client: TestClient) -> None:
    """The other half of the wall, as in the cross-account file.

    Scoping every by-id route would still leak if the collections that hand out
    the ids were scoped to the organization rather than to the company. They are
    the same collections a regnskapsfører reads all day with one client's books
    open, and a supplier's name showing up under the wrong client is a
    confidentiality failure between two businesses that never agreed to share
    anything.
    """
    mine, theirs = _two_companies(client)

    document_id, _ = _a_document(client, theirs["id"])
    voucher_id, _ = _a_voucher(client, theirs["id"])
    party_pk, _ = _a_party(client, theirs["id"])

    faktura = client.get(f"/api/v1/companies/{mine['id']}/faktura").json()
    assert [item["id"] for item in faktura] == []
    assert document_id not in {item["id"] for item in faktura}

    cashflow = client.get(f"/api/v1/companies/{mine['id']}/cashflow").json()
    assert [item["id"] for item in cashflow] == []
    assert voucher_id not in {item["id"] for item in cashflow}

    parties = client.get(f"/api/v1/companies/{mine['id']}/parties").json()
    assert [item["id"] for item in parties] == []
    assert party_pk not in {item["id"] for item in parties}

    # Named explicitly, because a name is the leak nobody thinks to test for:
    # which businesses a given set of books trades with is not public, whatever
    # Enhetsregisteret says about the businesses themselves.
    assert all(item["name"] != "Hemmelig Kunde AS" for item in parties)

    summary = client.get(f"/api/v1/companies/{mine['id']}/cashflow/summary").json()
    assert summary["entry_count"] == 0
    assert summary["total_out"] == "0.00"


def test_a_sibling_companys_party_cannot_be_attached_to_a_payment(client: TestClient) -> None:
    """Reading is one direction; writing across the wall is the other.

    A party id travels in the *body* rather than the path, so nothing in the
    routing layer looks at it. If it were resolved by primary key, a payment in
    Klient En's books would carry Klient To's customer, and the party is what
    SAF-T reports as the counterparty, so the crossing would be exported to
    Skatteetaten rather than merely displayed.
    """
    mine, theirs = _two_companies(client)
    party_pk, _ = _a_party(client, theirs["id"])

    response = write(
        client,
        "POST",
        f"/api/v1/companies/{mine['id']}/cashflow",
        json={
            "type_code": "varekjop",
            "occurred_on": f"{THIS_YEAR}-03-14",
            "gross_amount": "500.00",
            "description": "Betaling",
            "party_id": party_pk,
        },
    )
    assert response.status_code == 404, response.text


def test_a_sibling_companys_party_cannot_be_billed(client: TestClient) -> None:
    """The same crossing on the invoice side, where the party becomes a buyer.

    An invoice snapshots the buyer's name and address onto the document, so a
    party accepted from the wrong company would not merely be referenced: it
    would be copied into another client's records and printed on a PDF sent to
    somebody outside.
    """
    mine, theirs = _two_companies(client)
    party_pk, _ = _a_party(client, theirs["id"])

    response = write(
        client,
        "POST",
        f"/api/v1/companies/{mine['id']}/faktura",
        json={
            "party_id": party_pk,
            "lines": [
                {
                    "description": "Rådgivning",
                    "quantity": "1",
                    "unit_price": "1000.00",
                    "tax_code": "3",
                }
            ],
        },
    )
    assert response.status_code == 404, response.text
