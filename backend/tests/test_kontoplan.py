"""The editable kontoplan: accounts, groupings per year, and what may not change.

What these pin down is the set of things that would make the SAF-T file, the
næringsspesifikasjon or the register quietly wrong: an account whose type does
not match its class, a grouping not in Skatteetaten's list, a closed year's
grouping moved after it was filed, a deleted account with history, a chosen
grouping lost when the next year opens, and a sibling company's account
reachable through this one's URL (the last is also in
``test_sibling_company_boundary.py``, for every route).
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.db.session import get_session
from myntos.domain.arsoppgjor import closing
from myntos.domain.ledger import kontoplan
from myntos.domain.ledger.chart import STANDARD_ACCOUNTS
from myntos.domain.ledger.models import AccountGrouping, AccountType, Company, FinancialYear
from myntos.saft import codes
from tests.conftest import join_company, write
from tests.test_cashflow_api import (
    OTHER_ORGNR,
    THIS_YEAR,
    create_company,
    signed_in_company,
)


def in_app[T](client: TestClient, work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Run ``work`` in the app's own session and loop, committing as a request does."""
    override = client.app.dependency_overrides[get_session]  # type: ignore[attr-defined]

    async def run() -> T:
        sessions = override()
        session = await sessions.__anext__()
        result = await work(session)
        await session.flush()
        with contextlib.suppress(StopAsyncIteration):
            await sessions.__anext__()
        return result

    assert client.portal is not None
    return client.portal.call(run)


def close_year(client: TestClient, company_id: int, year: int) -> None:
    async def work(session: AsyncSession) -> None:
        from myntos.core.clock import utc_now

        await session.execute(
            update(FinancialYear)
            .where(FinancialYear.company_id == company_id, FinancialYear.year == year)
            .values(closed_at=utc_now())
        )

    in_app(client, work)


def chart(client: TestClient, company_id: int, year: int = THIS_YEAR) -> dict[str, Any]:
    response = client.get(f"/api/v1/companies/{company_id}/kontoplan", params={"aar": year})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def account(body: dict[str, Any], number: str) -> dict[str, Any]:
    found: dict[str, Any] = next(item for item in body["accounts"] if item["number"] == number)
    return found


def record(
    client: TestClient,
    company_id: int,
    *,
    type_code: str,
    gross_amount: str = "1250.00",
    occurred_on: str | None = None,
    account_number: str | None = None,
) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/cashflow",
        json={
            "type_code": type_code,
            "occurred_on": occurred_on or date(THIS_YEAR, 3, 14).isoformat(),
            "gross_amount": gross_amount,
            "description": "Test",
            "account_number": account_number,
        },
    )


def add(client: TestClient, company_id: int, **payload: Any) -> Any:
    return write(client, "POST", f"/api/v1/companies/{company_id}/kontoplan", json=payload)


# --- Pure rules ---------------------------------------------------------------------


def test_every_seeded_account_proposes_its_own_seed_pair() -> None:
    for seed in STANDARD_ACCOUNTS:
        assert kontoplan.default_grouping(seed.number, THIS_YEAR) == (
            seed.grouping_category,
            seed.grouping_code,
        )


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        # Same number in the list.
        ("2770", ("kortsiktigGjeld", "2770")),
        ("3400", ("annenDriftsinntekt", "3400")),
        # Nearest post below, or the seeded neighbour in the same group.
        ("1580", ("balanseverdiForOmloepsmiddel", "1500")),
        # The seeded neighbour in the same group, not whatever sits below it.
        ("6820", ("annenDriftskostnad", "6350")),
        ("6420", ("annenDriftskostnad", "6400")),
        ("8170", ("finanskostnad", "8160")),
    ],
)
def test_a_new_number_proposes_a_published_post_in_its_own_class(
    number: str, expected: tuple[str, str]
) -> None:
    proposed = kontoplan.default_grouping(number, 2026)
    assert proposed == expected
    assert codes.is_valid_grouping(2026, *proposed) is True


def test_no_default_is_a_bank_or_insurance_post() -> None:
    for number in range(1000, 9000, 7):
        category, _ = kontoplan.default_grouping(str(number), 2026)
        assert "BankOgForsikring" not in category and "bankOgForsikring" not in category


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        ("1580", AccountType.EIENDEL),
        ("2010", AccountType.EGENKAPITAL),
        ("2770", AccountType.GJELD),
        ("3400", AccountType.INNTEKT),
        ("6420", AccountType.KOSTNAD),
        ("8070", AccountType.INNTEKT),
        ("8170", AccountType.KOSTNAD),
    ],
)
def test_the_type_follows_the_class(number: str, expected: AccountType) -> None:
    assert kontoplan.default_type(number) is expected


# --- Through the API ------------------------------------------------------------------


def test_the_kontoplan_lists_every_account_with_its_grouping(client: TestClient) -> None:
    company = signed_in_company(client)
    body = chart(client, company["id"])
    assert body["closed"] is False
    assert body["list_year"] == codes.latest_published_year(THIS_YEAR)
    assert {item["number"] for item in body["accounts"]} == {s.number for s in STANDARD_ACCOUNTS}
    bank = account(body, "1920")
    assert bank["is_locked"] is True
    assert (bank["grouping_category"], bank["grouping_code"]) == (
        "balanseverdiForOmloepsmiddel",
        "1920",
    )
    assert bank["grouping_chosen_at"] is None
    assert any(option["code"] == "7830" for option in body["groupings"])


def test_an_added_account_is_grouped_by_default_and_passes_the_saft_check(
    client: TestClient,
) -> None:
    company = signed_in_company(client)
    response = add(client, company["id"], number="6820", description="Hosting")
    assert response.status_code == 201, response.text
    added = account(response.json(), "6820")
    assert added["account_type"] == "kostnad"
    assert (added["grouping_category"], added["grouping_code"]) == ("annenDriftskostnad", "6350")
    assert added["grouping_chosen_at"] is None

    # Booked to through the register, as a datakostnad on the person's own account.
    posted = record(client, company["id"], type_code="datakostnad", account_number="6820")
    assert posted.status_code == 201, posted.text
    assert posted.json()["account_number"] == "6820"

    checked = client.get(f"/api/v1/companies/{company['id']}/saft/kontroll?aar={THIS_YEAR}")
    assert checked.status_code == 200, checked.text
    assert checked.json()["exportable"] is True, checked.json()["findings"]


def test_a_chosen_grouping_is_recorded_and_carried_into_the_next_year(
    client: TestClient,
) -> None:
    company = signed_in_company(client)
    response = add(
        client,
        company["id"],
        number="6820",
        description="Hosting",
        grouping_category="annenDriftskostnad",
        grouping_code="6350",
    )
    assert response.status_code == 201, response.text
    assert account(response.json(), "6820")["grouping_chosen_at"] is not None

    # The seeded 6800 regrouped by hand, too.
    office = account(chart(client, company["id"]), "6800")
    moved = write(
        client,
        "PUT",
        f"/api/v1/companies/{company['id']}/kontoplan/{office['id']}/gruppering",
        json={
            "year": THIS_YEAR,
            "grouping_category": "annenDriftskostnad",
            "grouping_code": "6500",
        },
    )
    assert moved.status_code == 200, moved.text

    # Posting into next year opens it. Both choices come along; an account
    # nobody touched takes the seed's pair.
    following = record(
        client,
        company["id"],
        type_code="kontorrekvisita",
        occurred_on=date(THIS_YEAR + 1, 2, 1).isoformat(),
    )
    assert following.status_code == 201, following.text
    next_year = chart(client, company["id"], THIS_YEAR + 1)
    assert account(next_year, "6820")["grouping_code"] == "6350"
    assert account(next_year, "6800")["grouping_code"] == "6500"
    assert account(next_year, "6800")["grouping_chosen_at"] is not None
    assert account(next_year, "6900")["grouping_code"] == "6995"
    assert account(next_year, "6900")["grouping_chosen_at"] is None


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"number": "123", "description": "Kort"}, None),
        ({"number": "9100", "description": "Intern"}, "invalid_account_number"),
        ({"number": "1580", "description": "Feil", "account_type": "kostnad"}, None),
        ({"number": "1920", "description": "Bank igjen"}, "duplicate_account"),
        (
            {
                "number": "6820",
                "description": "Hosting",
                "grouping_category": "annenDriftskostnad",
                "grouping_code": "6351",
            },
            "invalid_grouping",
        ),
    ],
)
def test_what_does_not_belong_in_a_kontoplan_is_refused(
    client: TestClient, payload: dict[str, str], code: str | None
) -> None:
    company = signed_in_company(client)
    response = add(client, company["id"], **payload)
    assert response.status_code in {409, 422}, response.text
    if code is not None:
        assert response.json()["code"] == code
    if payload["number"] == "1580":
        assert response.json()["code"] == "invalid_account_number"


def test_a_grouping_not_in_the_list_is_refused(client: TestClient) -> None:
    company = signed_in_company(client)
    office = account(chart(client, company["id"]), "6800")
    response = write(
        client,
        "PUT",
        f"/api/v1/companies/{company['id']}/kontoplan/{office['id']}/gruppering",
        json={"year": THIS_YEAR, "grouping_category": "lonnskostnad", "grouping_code": "5000"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_grouping"


def test_a_closed_years_grouping_is_not_moved(client: TestClient) -> None:
    company = signed_in_company(client)
    office = account(chart(client, company["id"]), "6800")
    close_year(client, company["id"], THIS_YEAR)
    response = write(
        client,
        "PUT",
        f"/api/v1/companies/{company['id']}/kontoplan/{office['id']}/gruppering",
        json={
            "year": THIS_YEAR,
            "grouping_category": "annenDriftskostnad",
            "grouping_code": "6500",
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "closed_financial_year"
    assert account(chart(client, company["id"]), "6800")["grouping_code"] == "6995"


def test_an_account_added_after_a_year_closed_stays_out_of_that_years_file(
    client: TestClient,
) -> None:
    company = signed_in_company(client)
    record(client, company["id"], type_code="kontorrekvisita")
    # Next year is open, this one closed: the new account is grouped for the
    # open one only, and the closed year's file is still exportable.
    record(client, company["id"], type_code="kontorrekvisita", occurred_on=f"{THIS_YEAR + 1}-01-15")
    close_year(client, company["id"], THIS_YEAR)

    async def work(session: AsyncSession) -> None:
        books = await session.scalar(select(Company).where(Company.id == company["id"]))
        assert books is not None
        await kontoplan.create_account(
            session,
            company=books,
            number="6820",
            description="Hosting",
            today=date(THIS_YEAR + 1, 3, 1),
        )

    in_app(client, work)

    assert account(chart(client, company["id"], THIS_YEAR + 1), "6820")["grouping_code"]
    assert account(chart(client, company["id"], THIS_YEAR), "6820")["grouping_code"] is None
    closed = client.get(f"/api/v1/companies/{company['id']}/saft/kontroll?aar={THIS_YEAR}")
    assert closed.status_code == 200, closed.text
    assert closed.json()["exportable"] is True, closed.json()["findings"]


def test_deactivating_hides_an_account_from_new_entries_only(client: TestClient) -> None:
    company = signed_in_company(client)
    before = record(client, company["id"], type_code="telefon_internett")
    assert before.status_code == 201
    phone = account(chart(client, company["id"]), "6900")

    off = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/kontoplan/{phone['id']}",
        json={"is_active": False},
    )
    assert off.status_code == 200, off.text
    assert account(off.json(), "6900")["is_active"] is False

    refused = record(client, company["id"], type_code="telefon_internett")
    assert refused.status_code == 409
    assert refused.json()["code"] == "inactive_account"

    catalogue = client.get(f"/api/v1/companies/{company['id']}/cashflow/types").json()
    assert "telefon_internett" not in {t["code"] for t in catalogue["types"]}

    # The entry already posted is still there, on the same account.
    listed = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    assert [(e["id"], e["account_number"]) for e in listed] == [(before.json()["id"], "6900")]


def test_an_account_the_code_relies_on_cannot_be_switched_off(client: TestClient) -> None:
    company = signed_in_company(client)
    for number in ("1920", "2700", "2710", "1500", "2400", "2740"):
        target = account(chart(client, company["id"]), number)
        response = write(
            client,
            "PATCH",
            f"/api/v1/companies/{company['id']}/kontoplan/{target['id']}",
            json={"is_active": False},
        )
        assert response.status_code == 409, number
        assert response.json()["code"] == "account_in_use"


def test_an_account_with_postings_is_never_deleted(client: TestClient) -> None:
    company = signed_in_company(client)
    record(client, company["id"], type_code="kontorrekvisita")
    office = account(chart(client, company["id"]), "6800")
    response = write(
        client, "DELETE", f"/api/v1/companies/{company['id']}/kontoplan/{office['id']}"
    )
    assert response.status_code == 409
    assert "Deaktiver" in response.json()["detail"]


def test_an_account_added_by_mistake_can_be_deleted(client: TestClient) -> None:
    company = signed_in_company(client)
    added = account(
        add(client, company["id"], number="6821", description="Tastefeil").json(), "6821"
    )
    response = write(client, "DELETE", f"/api/v1/companies/{company['id']}/kontoplan/{added['id']}")
    assert response.status_code == 204
    assert "6821" not in {item["number"] for item in chart(client, company["id"])["accounts"]}

    async def groupings(session: AsyncSession) -> list[int]:
        return list(
            await session.scalars(
                select(AccountGrouping.id).where(AccountGrouping.account_id == added["id"])
            )
        )

    assert in_app(client, groupings) == []


def test_a_rename_keeps_the_number_and_the_history(client: TestClient) -> None:
    company = signed_in_company(client)
    posted = record(client, company["id"], type_code="kontorrekvisita").json()
    office = account(chart(client, company["id"]), "6800")
    response = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/kontoplan/{office['id']}",
        json={"description": "Kontorrekvisita og porto"},
    )
    assert response.status_code == 200
    assert account(response.json(), "6800")["description"] == "Kontorrekvisita og porto"
    listed = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    assert listed[0]["id"] == posted["id"]
    assert listed[0]["account_number"] == "6800"
    assert Decimal(listed[0]["gross_amount"]) == Decimal(posted["gross_amount"])


def test_a_category_may_move_only_to_an_account_of_its_own_type(client: TestClient) -> None:
    company = signed_in_company(client)
    add(client, company["id"], number="1580", description="Andre fordringer")
    wrong = record(client, company["id"], type_code="datakostnad", account_number="1580")
    assert wrong.status_code == 422
    locked = record(client, company["id"], type_code="datakostnad", account_number="2710")
    assert locked.status_code == 422
    missing = record(client, company["id"], type_code="datakostnad", account_number="6899")
    assert missing.status_code == 404


def test_only_someone_who_administers_the_books_changes_the_kontoplan(
    client: TestClient,
) -> None:
    company = signed_in_company(client)
    join_company(client, company["id"], email="bokforer@example.no", role="member")
    # A member reads it and posts, and does not change it.
    assert chart(client, company["id"])["accounts"]
    response = add(client, company["id"], number="6820", description="Hosting")
    assert response.status_code == 403
    assert (
        "ledger:chart"
        not in next(c for c in client.get("/api/v1/companies").json() if c["id"] == company["id"])[
            "permissions"
        ]
    )


def test_the_owner_is_told_they_may_change_it(client: TestClient) -> None:
    company = signed_in_company(client)
    assert "ledger:chart" in company["permissions"]


def test_another_companys_accounts_stay_out(client: TestClient) -> None:
    from tests.conftest import register

    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    mine = create_company(client, organization_id)
    theirs = create_company(client, organization_id, orgnr=OTHER_ORGNR, name="Andre AS")
    add(client, theirs["id"], number="6820", description="Hemmelig")
    assert "6820" not in {item["number"] for item in chart(client, mine["id"])["accounts"]}
    # And a payment in mine cannot be booked to it.
    response = record(client, mine["id"], type_code="datakostnad", account_number="6820")
    assert response.status_code == 404


def test_every_account_the_year_end_close_names_is_locked() -> None:
    # The close posts to these by number. One deactivated through the kontoplan
    # would make the next årsavslutning fail on a year somebody is closing.
    named = {
        closing.SHARE_EQUITY,
        closing.UNCOVERED_LOSS,
        closing.PRIVATE_ACCOUNT,
        closing.TAX_PAYABLE,
        *closing.CLOSING_ACCOUNTS,
    }
    assert named <= kontoplan.LOCKED_ACCOUNTS
