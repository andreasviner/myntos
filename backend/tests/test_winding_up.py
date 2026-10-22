"""A dissolved company's books end on their last day (oppløsning), over HTTP.

Real postings through the API into last year, which is always in the past, so
the last day can be any day in it: an opening balance, a cost, the payout of
what was left to the shareholders, then the books ended on the payout's day.
What the tests hold is what a person would not see going wrong until the
skattemelding: that nothing can be dated after the last day, that the year
closes the day after and opens nothing behind it, and that a day the books
contradict is refused rather than taken.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import register, write
from tests.test_arsoppgjor_api import LAST_YEAR, _close, _company, _line, _post_opening
from tests.test_cashflow_api import THIS_YEAR, record
from tests.test_manuelt_bilag import _post as _post_manual
from tests.test_manuelt_bilag import _voucher

LAST_DAY = date(LAST_YEAR, 6, 30)


def books(client: TestClient) -> dict[str, Any]:
    """An AS with 30 000 in the bank on 1 January last year and 500 spent in March."""
    session = register(client)
    company = _company(client, session["memberships"][0]["organization_id"])
    opened = _post_opening(
        client, company["id"], [_line("1920", debit="30000.00"), _line("2000", credit="30000.00")]
    )
    assert opened.status_code == 201, opened.text
    spent = _post_manual(
        client,
        company["id"],
        _voucher(
            [_line("6800", debit="500.00"), _line("1920", credit="500.00")],
            on=date(LAST_YEAR, 3, 14),
            description="Gebyrer",
        ),
    )
    assert spent.status_code == 201, spent.text
    return company


def pay_out(client: TestClient, company_id: int, amount: str = "29500.00") -> None:
    """What was left, paid to the shareholders on the last day."""
    paid = _post_manual(
        client,
        company_id,
        _voucher(
            [_line("2000", debit=amount), _line("1920", credit=amount)],
            on=LAST_DAY,
            description="Utbetaling til aksjonærene ved oppløsning",
        ),
    )
    assert paid.status_code == 201, paid.text


def wind_up(client: TestClient, company_id: int, last_day: date = LAST_DAY, **body: Any) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/arsavslutning/opplosning",
        json={"siste_dag": last_day.isoformat(), "bekreftet": True, **body},
    )


def years(client: TestClient, company_id: int) -> dict[int, dict[str, Any]]:
    response = client.get(f"/api/v1/companies/{company_id}/regnskapsaar")
    assert response.status_code == 200, response.text
    return {year["year"]: year for year in response.json()}


def test_the_books_end_on_the_last_day(client: TestClient) -> None:
    company = books(client)
    response = wind_up(client, company["id"])
    assert response.status_code == 200, response.text
    assert response.json() == {
        "company_id": company["id"],
        "last_day": LAST_DAY.isoformat(),
        "year": LAST_YEAR,
        "year_closed": False,
    }
    assert client.get(f"/api/v1/companies/{company['id']}").json()["wound_up_on"] == (
        LAST_DAY.isoformat()
    )
    year = years(client, company["id"])[LAST_YEAR]
    assert year["end_date"] == LAST_DAY.isoformat()
    assert [event["action"] for event in year["events"]] == ["opplost"]


def test_nothing_is_dated_after_the_last_day(client: TestClient) -> None:
    company = books(client)
    assert wind_up(client, company["id"]).status_code == 200
    for day in (date(LAST_YEAR, 7, 1), date(THIS_YEAR, 1, 15)):
        refused = record(
            client,
            company["id"],
            type_code="kontorrekvisita",
            gross_amount="125.00",
            occurred_on=day.isoformat(),
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["code"] == "period_outside_books"
    manual = _post_manual(
        client,
        company["id"],
        _voucher(
            [_line("6800", debit="10.00"), _line("1920", credit="10.00")],
            on=date(LAST_YEAR, 12, 31),
        ),
    )
    assert manual.status_code == 422, manual.text
    # The last day itself is inside the books.
    pay_out(client, company["id"])


def test_the_last_year_closes_the_day_after_and_opens_nothing(client: TestClient) -> None:
    company = books(client)
    assert wind_up(client, company["id"]).status_code == 200

    # Before the payout the bank still holds what was left, which a dissolved
    # company cannot: said, not refused.
    plan = client.get(
        f"/api/v1/companies/{company['id']}/arsavslutning", params={"aar": LAST_YEAR}
    ).json()
    codes = {finding["code"]: finding for finding in plan["findings"]}
    assert "siste_regnskapsaar" in codes
    assert "1920 (29 500,00)" in codes["saldo_ved_opplosning"]["message"]
    assert plan["closable"] is True

    pay_out(client, company["id"])
    plan = client.get(
        f"/api/v1/companies/{company['id']}/arsavslutning", params={"aar": LAST_YEAR}
    ).json()
    assert "saldo_ved_opplosning" not in {finding["code"] for finding in plan["findings"]}
    closed = _close(client, company["id"])
    assert closed.status_code == 201, closed.text
    # Dated on the last day, the year's end.
    assert {v["transaction_date"] for v in closed.json()["vouchers"]} == {LAST_DAY.isoformat()}

    assert list(years(client, company["id"])) == [LAST_YEAR]


def test_a_day_the_books_contradict_is_refused(client: TestClient) -> None:
    company = books(client)
    pay_out(client, company["id"])

    early = wind_up(client, company["id"], date(LAST_YEAR, 5, 31))
    assert early.status_code == 409
    assert early.json()["code"] == "winding_up_conflict"
    assert "30.06" in early.json()["detail"]

    future = wind_up(client, company["id"], date(THIS_YEAR + 1, 1, 10))
    assert future.status_code == 422
    assert future.json()["code"] == "invalid_winding_up_date"

    first_day = wind_up(client, company["id"], date(LAST_YEAR, 1, 1))
    assert first_day.status_code == 422
    assert first_day.json()["code"] == "invalid_winding_up_date"

    unconfirmed = wind_up(client, company["id"], bekreftet=False)
    assert unconfirmed.status_code == 422
    assert unconfirmed.json()["code"] == "confirmation_required"

    assert wind_up(client, company["id"]).status_code == 200
    again = wind_up(client, company["id"], date(LAST_YEAR, 7, 31))
    assert again.status_code == 409
    assert again.json()["code"] == "winding_up_conflict"


def test_a_year_already_opened_after_it_is_refused(client: TestClient) -> None:
    company = books(client)
    # Something posted this year means the company did not end last year.
    posted = record(
        client,
        company["id"],
        type_code="kontorrekvisita",
        gross_amount="125.00",
        occurred_on=date(THIS_YEAR, 1, 2).isoformat(),
    )
    assert posted.status_code == 201, posted.text
    refused = wind_up(client, company["id"])
    assert refused.status_code == 409
    assert refused.json()["code"] == "winding_up_conflict"


def test_undoing_it_lets_the_year_run_to_31_december(client: TestClient) -> None:
    company = books(client)
    assert wind_up(client, company["id"]).status_code == 200
    undo = f"/api/v1/companies/{company['id']}/arsavslutning/opplosning/angre"

    undone = write(client, "POST", undo)
    assert undone.status_code == 200, undone.text
    assert undone.json()["last_day"] is None
    assert client.get(f"/api/v1/companies/{company['id']}").json()["wound_up_on"] is None
    year = years(client, company["id"])[LAST_YEAR]
    assert year["end_date"] == date(LAST_YEAR, 12, 31).isoformat()
    assert [event["action"] for event in year["events"]] == ["opplost", "opplost_angret"]
    assert (
        record(
            client,
            company["id"],
            type_code="kontorrekvisita",
            gross_amount="125.00",
            occurred_on=date(LAST_YEAR, 9, 1).isoformat(),
        ).status_code
        == 201
    )

    nothing = write(client, "POST", undo)
    assert nothing.status_code == 409
    assert nothing.json()["code"] == "winding_up_conflict"


def test_a_closed_last_year_is_not_undone(client: TestClient) -> None:
    company = books(client)
    pay_out(client, company["id"])
    assert wind_up(client, company["id"]).status_code == 200
    assert _close(client, company["id"]).status_code == 201
    refused = write(
        client, "POST", f"/api/v1/companies/{company['id']}/arsavslutning/opplosning/angre"
    )
    assert refused.status_code == 409
    assert "Gjenåpne" in refused.json()["detail"]
