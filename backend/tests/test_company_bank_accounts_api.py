"""Several kontonummer per company, and which one an invoice is paid into.

A company has as many accounts as its bank gave it — a driftskonto to invoice
against, a skattetrekkskonto that skattebetalingsloven § 5-12 requires be kept
apart from the operating money — and exactly one of them is the default an
invoice falls back to. The two rules worth breaking a test over are that there
is never more than one default while the company has accounts at all, and that
deleting an account never rewrites what an issued document already said.
"""

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import join_company, register, write
from tests.test_cashflow_api import ORGNR, OTHER_ORGNR, THIS_YEAR, signed_in_company

# Two numbers whose mod-11 check digits work out. The third is the first with a
# transposed pair, which is exactly what the check digit exists to catch.
DRIFT = "15062033158"
SKATTETREKK = "98765432103"
SPARE = "12345678903"
TRANSPOSED = "15062033518"


def add_account(client: TestClient, company_id: int, account: str, **extra: Any) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/bankkontoer",
        json={"account": account, **extra},
    )


def accounts_of(client: TestClient, company_id: int) -> list[dict[str, Any]]:
    response = client.get(f"/api/v1/companies/{company_id}")
    assert response.status_code == 200, response.text
    listed: list[dict[str, Any]] = response.json()["bank_accounts"]
    return listed


def id_of(company: dict[str, Any], account: str) -> int:
    return int(next(held["id"] for held in company["bank_accounts"] if held["account"] == account))


def a_company_with(client: TestClient, *numbers: str) -> dict[str, Any]:
    """Books with the given accounts, in order. The first one is the primary."""
    company = signed_in_company(client)
    for number in numbers:
        assert add_account(client, company["id"], number).status_code == 201
    listed: dict[str, Any] = client.get(f"/api/v1/companies/{company['id']}").json()
    return listed


# --- The list -----------------------------------------------------------------------


def test_a_new_company_has_no_accounts_and_says_so(client: TestClient) -> None:
    """Empty rather than absent: it is the state that stops an invoice."""
    company = signed_in_company(client)
    assert company["bank_accounts"] == []


def test_the_account_given_at_signup_becomes_the_primary(client: TestClient) -> None:
    """The signup form asks for one, and one account is always the default."""
    session = register(client)
    created = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": session["memberships"][0]["organization_id"],
            "orgnr": ORGNR,
            "name": "Nordmann Consulting AS",
            "first_financial_year": THIS_YEAR,
            "bank_account": "1506.20.33158",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["bank_accounts"] == [
        {"id": id_of(created.json(), DRIFT), "account": DRIFT, "label": None, "is_primary": True}
    ]


def test_the_first_account_is_primary_however_it_was_asked_for(client: TestClient) -> None:
    """A company with accounts and no default has nowhere to be paid."""
    company = signed_in_company(client)
    added = add_account(client, company["id"], DRIFT, is_primary=False)
    assert added.status_code == 201, added.text
    assert added.json()["bank_accounts"][0]["is_primary"] is True


def test_a_second_account_does_not_take_the_marking(client: TestClient) -> None:
    company = a_company_with(client, DRIFT, SKATTETREKK)
    assert [(held["account"], held["is_primary"]) for held in company["bank_accounts"]] == [
        (DRIFT, True),
        (SKATTETREKK, False),
    ]


def test_the_primary_is_listed_first(client: TestClient) -> None:
    """The interface reads "the first one" as "where invoices are paid"."""
    company = a_company_with(client, DRIFT, SKATTETREKK)
    promoted = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/bankkontoer/{id_of(company, SKATTETREKK)}",
        json={"is_primary": True},
    )
    assert promoted.status_code == 200, promoted.text
    assert [held["account"] for held in promoted.json()["bank_accounts"]] == [SKATTETREKK, DRIFT]


# --- One default, always ------------------------------------------------------------


def test_promoting_one_account_demotes_the_other(client: TestClient) -> None:
    company = a_company_with(client, DRIFT, SKATTETREKK, SPARE)
    promoted = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/bankkontoer/{id_of(company, SPARE)}",
        json={"is_primary": True},
    )
    assert promoted.status_code == 200, promoted.text
    marked = [held["account"] for held in promoted.json()["bank_accounts"] if held["is_primary"]]
    assert marked == [SPARE]


def test_an_account_added_as_primary_takes_the_marking_over(client: TestClient) -> None:
    """The insert and the demotion are one request, and the index is checked per
    statement: a moment with two marked rows is a violation even when the same
    flush would have fixed it."""
    company = a_company_with(client, DRIFT)
    added = add_account(client, company["id"], SKATTETREKK, is_primary=True)
    assert added.status_code == 201, added.text
    assert [(h["account"], h["is_primary"]) for h in added.json()["bank_accounts"]] == [
        (SKATTETREKK, True),
        (DRIFT, False),
    ]


def test_the_marking_moves_back_to_an_older_account(client: TestClient) -> None:
    """The direction that orders the two updates the other way round."""
    company = a_company_with(client, DRIFT, SKATTETREKK)
    write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/bankkontoer/{id_of(company, SKATTETREKK)}",
        json={"is_primary": True},
    )
    back = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/bankkontoer/{id_of(company, DRIFT)}",
        json={"is_primary": True},
    )
    assert back.status_code == 200, back.text
    assert [h["account"] for h in back.json()["bank_accounts"] if h["is_primary"]] == [DRIFT]


def test_the_primary_cannot_simply_be_unmarked(client: TestClient) -> None:
    """The way to move it is to promote another account, and the message says so."""
    company = a_company_with(client, DRIFT, SKATTETREKK)
    refused = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/bankkontoer/{id_of(company, DRIFT)}",
        json={"is_primary": False},
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "primary_bank_account_required"
    assert accounts_of(client, company["id"])[0]["is_primary"] is True


def test_resubmitting_a_non_primary_account_unchanged_is_accepted(client: TestClient) -> None:
    """A form that sends every field it shows must go on working.

    ``is_primary: false`` on an account that is not primary changes nothing, and
    refusing it would make the edit form unusable on every account but one.
    """
    company = a_company_with(client, DRIFT, SKATTETREKK)
    saved = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/bankkontoer/{id_of(company, SKATTETREKK)}",
        json={"account": SKATTETREKK, "is_primary": False},
    )
    assert saved.status_code == 200, saved.text


# --- What is refused ----------------------------------------------------------------


def test_a_number_with_a_bad_check_digit_is_refused(client: TestClient) -> None:
    company = signed_in_company(client)
    response = add_account(client, company["id"], TRANSPOSED)
    assert response.status_code == 422
    assert "feil kontrollsiffer" in response.json()["detail"]
    assert accounts_of(client, company["id"]) == []


def test_the_same_number_twice_on_one_company_is_refused(client: TestClient) -> None:
    company = a_company_with(client, DRIFT)
    response = add_account(client, company["id"], "1506.20.33158")
    assert response.status_code == 409
    assert response.json()["code"] == "duplicate_bank_account"


def test_two_companies_may_name_the_same_account(client: TestClient) -> None:
    """The ENK that became an AS is the ordinary case, not a collision."""
    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    for orgnr in (ORGNR, OTHER_ORGNR):
        created = write(
            client,
            "POST",
            "/api/v1/companies",
            json={
                "organization_id": organization_id,
                "orgnr": orgnr,
                "name": f"Selskap {orgnr}",
                "first_financial_year": THIS_YEAR,
                "bank_account": DRIFT,
            },
        )
        assert created.status_code == 201, created.text


def test_an_ordinary_member_cannot_change_where_the_money_goes(client: TestClient) -> None:
    """ORGANIZATION_MANAGE, not LEDGER_WRITE.

    Where a customer is told to send money is a statement about the entity that
    every future invoice repeats, not a bookkeeping entry.
    """
    company = a_company_with(client, DRIFT)
    join_company(client, company["id"], email="kollega@example.no")
    assert add_account(client, company["id"], SKATTETREKK).status_code == 403


# --- Deleting -----------------------------------------------------------------------


def test_deleting_the_primary_promotes_the_oldest_that_remains(client: TestClient) -> None:
    company = a_company_with(client, DRIFT, SKATTETREKK, SPARE)
    removed = write(
        client,
        "DELETE",
        f"/api/v1/companies/{company['id']}/bankkontoer/{id_of(company, DRIFT)}",
    )
    assert removed.status_code == 200, removed.text
    assert [(held["account"], held["is_primary"]) for held in removed.json()["bank_accounts"]] == [
        (SKATTETREKK, True),
        (SPARE, False),
    ]


def test_deleting_the_last_account_leaves_the_company_with_none(client: TestClient) -> None:
    """Allowed: it is the state the company was created in, and it is honest."""
    company = a_company_with(client, DRIFT)
    removed = write(
        client,
        "DELETE",
        f"/api/v1/companies/{company['id']}/bankkontoer/{id_of(company, DRIFT)}",
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["bank_accounts"] == []


def test_an_account_this_company_does_not_have_is_not_found(client: TestClient) -> None:
    company = a_company_with(client, DRIFT)
    missing = write(
        client,
        "DELETE",
        f"/api/v1/companies/{company['id']}/bankkontoer/{id_of(company, DRIFT) + 9000}",
    )
    assert missing.status_code == 404


# --- Labels -------------------------------------------------------------------------


def test_an_account_can_be_named_and_the_name_removed(client: TestClient) -> None:
    """Optional, because a company with one account has nothing to tell apart."""
    company = a_company_with(client, DRIFT)
    account_id = id_of(company, DRIFT)
    named = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/bankkontoer/{account_id}",
        json={"label": "  Drift  "},
    )
    assert named.status_code == 200, named.text
    assert named.json()["bank_accounts"][0]["label"] == "Drift"

    cleared = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/bankkontoer/{account_id}",
        json={"label": ""},
    )
    assert cleared.json()["bank_accounts"][0]["label"] is None
