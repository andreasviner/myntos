"""Customers and suppliers, and attaching one to a payment."""

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import join_company, register, write
from tests.test_cashflow_api import OTHER_ORGNR, create_company, record, signed_in_company


def add_party(
    client: TestClient,
    company_id: int,
    *,
    party_type: str = "leverandor",
    name: str = "Papirhuset AS",
    **extra: Any,
) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/parties",
        json={"party_type": party_type, "name": name, **extra},
    )


def test_a_supplier_is_created_with_a_balance_account(client: TestClient) -> None:
    """SAF-T BalanceAccountStructure: derived from the type, never chosen."""
    company = signed_in_company(client)
    response = add_party(client, company["id"], party_type="leverandor")
    assert response.status_code == 201, response.text
    party = response.json()

    assert party["party_type"] == "leverandor"
    assert party["party_id"] == "20000"
    assert party["balance_account_id"] is not None


def test_customers_and_suppliers_are_numbered_apart(client: TestClient) -> None:
    company = signed_in_company(client)
    customer = add_party(client, company["id"], party_type="kunde", name="Kunde AS").json()
    supplier = add_party(client, company["id"], party_type="leverandor").json()

    assert customer["party_id"] == "10000"
    assert supplier["party_id"] == "20000"
    assert customer["balance_account_id"] != supplier["balance_account_id"]


def test_numbers_continue_from_the_highest_in_use(client: TestClient) -> None:
    """Bringing your own numbers must not make the generator collide with them."""
    company = signed_in_company(client)
    add_party(client, company["id"], name="Egen nummerering", party_id="20500")
    assert add_party(client, company["id"], name="Neste").json()["party_id"] == "20501"


def test_a_supplied_number_cannot_be_reused(client: TestClient) -> None:
    company = signed_in_company(client)
    add_party(client, company["id"], party_id="20500")
    response = add_party(client, company["id"], name="Annen", party_id="20500")
    assert response.status_code == 409
    assert response.json()["code"] == "duplicate_party"


def test_a_party_orgnr_is_check_digit_verified(client: TestClient) -> None:
    company = signed_in_company(client)
    response = add_party(client, company["id"], orgnr="910000005")
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_orgnr"


def test_a_party_may_have_no_orgnr(client: TestClient) -> None:
    """Private individuals and foreign suppliers do not have one."""
    company = signed_in_company(client)
    assert add_party(client, company["id"], name="Privatperson").json()["orgnr"] is None


def test_parties_can_be_listed_and_filtered(client: TestClient) -> None:
    company = signed_in_company(client)
    add_party(client, company["id"], party_type="kunde", name="Kunde AS")
    add_party(client, company["id"], party_type="leverandor", name="Leverandør AS")

    everyone = client.get(f"/api/v1/companies/{company['id']}/parties").json()
    assert len(everyone) == 2

    customers = client.get(f"/api/v1/companies/{company['id']}/parties?party_type=kunde").json()
    assert [party["name"] for party in customers] == ["Kunde AS"]


def test_a_party_can_be_corrected(client: TestClient) -> None:
    company = signed_in_company(client)
    party = add_party(client, company["id"], name="Feil Navn AS").json()

    updated = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/parties/{party['id']}",
        json={"name": "Riktig Navn AS", "orgnr": "910000012", "city": "Oslo"},
    ).json()
    assert updated["name"] == "Riktig Navn AS"
    assert updated["orgnr"] == "910000012"
    # The number and the type are referenced by posted vouchers; neither moves.
    assert updated["party_id"] == party["party_id"]
    assert updated["party_type"] == party["party_type"]


def test_another_companys_party_is_not_found(client: TestClient) -> None:
    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    first = create_company(client, organization_id)
    second = create_company(client, organization_id, orgnr=OTHER_ORGNR, name="Andre AS")
    party = add_party(client, first["id"]).json()

    response = write(
        client,
        "PATCH",
        f"/api/v1/companies/{second['id']}/parties/{party['id']}",
        json={"name": "Kapret"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "party_not_found"


# --- On an entry ----------------------------------------------------------------


def test_a_payment_can_name_its_supplier(client: TestClient) -> None:
    company = signed_in_company(client)
    supplier = add_party(client, company["id"], name="Papirhuset AS").json()

    entry = record(
        client,
        company["id"],
        type_code="kontorrekvisita",
        gross_amount="625.00",
        party_id=supplier["id"],
    ).json()

    assert entry["party_id"] == supplier["id"]
    assert entry["party_name"] == "Papirhuset AS"


def test_the_party_shows_up_in_the_register(client: TestClient) -> None:
    company = signed_in_company(client)
    customer = add_party(client, company["id"], party_type="kunde", name="Storkunde AS").json()
    record(
        client,
        company["id"],
        type_code="salg_hoy_sats",
        gross_amount="1250.00",
        party_id=customer["id"],
    )

    listed = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    assert listed[0]["party_name"] == "Storkunde AS"


def test_a_payment_without_a_party_is_fine(client: TestClient) -> None:
    """Most payments have no counterparty worth registering."""
    company = signed_in_company(client)
    entry = record(client, company["id"], type_code="bankgebyr", gross_amount="45.00").json()
    assert entry["party_id"] is None
    assert entry["party_name"] is None


def test_a_party_from_another_company_cannot_be_attached(client: TestClient) -> None:
    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    first = create_company(client, organization_id)
    second = create_company(client, organization_id, orgnr=OTHER_ORGNR, name="Andre AS")
    party = add_party(client, first["id"]).json()

    response = record(
        client,
        second["id"],
        type_code="kontorrekvisita",
        gross_amount="100.00",
        party_id=party["id"],
    )
    assert response.status_code == 404


def test_the_party_survives_a_reversal(client: TestClient) -> None:
    """A reversal that dropped the counterparty would leave the sub-ledger wrong."""
    company = signed_in_company(client)
    supplier = add_party(client, company["id"]).json()
    entry = record(
        client,
        company["id"],
        type_code="varekjop",
        gross_amount="500.00",
        party_id=supplier["id"],
    ).json()

    reversal = write(
        client, "POST", f"/api/v1/companies/{company['id']}/cashflow/{entry['id']}/reversal"
    ).json()
    assert reversal["party_id"] == supplier["id"]


def test_a_viewer_may_read_parties_but_not_add_them(client: TestClient) -> None:
    owner = register(client, "kari@example.no")
    organization_id = owner["memberships"][0]["organization_id"]
    company = create_company(client, organization_id)

    join_company(client, company["id"], email="viewer@example.no", role="viewer")

    assert client.get(f"/api/v1/companies/{company['id']}/parties").status_code == 200
    assert add_party(client, company["id"]).status_code == 403
