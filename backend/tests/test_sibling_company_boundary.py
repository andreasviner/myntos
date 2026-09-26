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
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from myntos.core.clock import utc_now
from myntos.main import create_app
from tests.conftest import register, write

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


# Keyed by the path parameter's own name, because that is what the schema hands
# over and what a new route will spell out. A name missing from here is a
# failure below, never a skip.
FACTORIES: dict[str, Callable[[TestClient, int], tuple[int, Callable[[], None]]]] = {
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
}


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

_COMPANIES = "/api/v1/companies/{company_id}"

REQUESTS: dict[tuple[str, str], dict[str, Any]] = {
    ("PUT", f"{_COMPANIES}/cashflow/{{voucher_id}}"): {"json": _CASHFLOW_BODY},
    ("POST", f"{_COMPANIES}/cashflow/{{voucher_id}}/dokument"): {
        "files": {"file": ("kvittering.pdf", PDF_BYTES, "application/pdf")}
    },
    ("POST", f"{_COMPANIES}/cashflow/{{voucher_id}}/reversal"): {},
    ("GET", f"{_COMPANIES}/dokument/{{attachment_id}}"): {},
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
    ("POST", f"{_COMPANIES}/invitations/{{invitation_id}}/send-again"): {},
    ("PATCH", f"{_COMPANIES}/members/{{user_id}}"): {"json": {"role": "viewer"}},
    ("DELETE", f"{_COMPANIES}/members/{{user_id}}"): {},
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
    smuggled, readable_by_its_owner = FACTORIES[parameter](client, theirs["id"])

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
