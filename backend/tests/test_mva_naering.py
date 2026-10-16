"""Two VAT returns in one company, and terms filed from another program.

A farm with other business above 30 000 kroner beside it files a primærnæring
melding and an alminnelig one, each on its own terms (docs/integrations/mva.md,
«Two returns»). Which melding a line goes in is decided by its account, so what
these assert is that the two meldinger never see each other's lines, that each
is settled on its own, and that a change which would move VAT into or out of a
filed term is refused rather than quietly rewriting it.

A term the previous program filed is recorded, not filed («Filed elsewhere»):
after that Myntos must not check or send it, because a second melding replaces
the first at Skatteetaten, and taking the statement back must make it fileable
again.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import agent_header, write
from tests.test_mva_api import (
    LAST_YEAR,
    Skatteetaten,
    book,
    books,
    check,
    send,
    settlement_lines,
    skatteetaten,  # noqa: F401 - the fixture, by name
)

SECOND_SALES = "3550"
SECOND_COSTS = "4060"


def _account(client: TestClient, company_id: int, number: str, name: str) -> None:
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/kontoplan",
        params={"aar": LAST_YEAR},
        json={"number": number, "description": name},
    )
    assert response.status_code == 201, response.text


def _farm(client: TestClient) -> dict[str, Any]:
    """A farm on a yearly primærnæring melding, with snow ploughing on a bimonthly one."""
    company = books(client)
    settings = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/mva/innstillinger",
        json={"category": "primaernaering", "period_type": "aar"},
    )
    assert settings.status_code == 200, settings.text
    _account(client, company["id"], SECOND_SALES, "Salg brøyting")
    _account(client, company["id"], SECOND_COSTS, "Innkjøp brøyting")
    second = write(
        client,
        "PUT",
        f"/api/v1/companies/{company['id']}/mva/andre-melding",
        json={
            "period_type": "toMaaneder",
            "valid_from": date(LAST_YEAR, 1, 1).isoformat(),
            "accounts": [SECOND_SALES, SECOND_COSTS],
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["second"]["category"] == "alminnelig"
    return company


def _sell(client: TestClient, company_id: int, gross: str, account: str | None = None) -> None:
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/cashflow",
        json={
            "type_code": "salg_hoy_sats",
            "occurred_on": date(LAST_YEAR, 2, 10).isoformat(),
            "gross_amount": gross,
            "description": "Salg",
            "account_number": account,
        },
    )
    assert response.status_code == 201, response.text


def _period(client: TestClient, company_id: int, termin: int, naering: str | None) -> Any:
    params: dict[str, Any] = {"aar": LAST_YEAR, "termin": termin}
    if naering is not None:
        params["naering"] = naering
    response = client.get(f"/api/v1/companies/{company_id}/mva/termin", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# --- Two meldinger ---------------------------------------------------------------------


def test_each_melding_has_its_own_terms_and_only_its_own_lines(client: TestClient) -> None:
    company = _farm(client)
    _sell(client, company["id"], "12500.00")  # the farm: 2500 of output VAT
    _sell(client, company["id"], "5000.00", SECOND_SALES)  # the ploughing: 1000

    overview = client.get(f"/api/v1/companies/{company['id']}/mva", params={"aar": LAST_YEAR})
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["naering"] == "primaernaering"
    assert [n["category"] for n in body["naeringer"]] == ["primaernaering", "alminnelig"]
    assert len(body["periods"]) == 1
    assert body["periods"][0]["fastsatt_merverdiavgift"] == "2500.00"

    other = client.get(
        f"/api/v1/companies/{company['id']}/mva",
        params={"aar": LAST_YEAR, "naering": "alminnelig"},
    ).json()
    assert other["naering"] == "alminnelig"
    assert len(other["periods"]) == 6
    assert other["periods"][0]["fastsatt_merverdiavgift"] == "1000.00"

    farm = _period(client, company["id"], 1, None)
    assert farm["category"] == "primaernaering"
    # Yearly primærnæring is due 10 April (skatteforvaltningsforskriften § 8-3-10).
    assert farm["deadline"].startswith(f"{LAST_YEAR + 1}-04-1")
    # The VAT accounts hold both meldinger, so they reconcile against both.
    assert farm["reconciliation"]["difference"] == "0.00"
    ploughing = _period(client, company["id"], 1, "alminnelig")
    assert ploughing["category"] == "alminnelig"
    assert ploughing["reconciliation"]["difference"] == "0.00"

    xml = client.get(
        f"/api/v1/companies/{company['id']}/mva/termin/xml",
        params={"aar": LAST_YEAR, "termin": 1, "naering": "alminnelig"},
    )
    assert xml.status_code == 200
    assert b"<meldingskategori>alminnelig</meldingskategori>" in xml.content
    assert "alminnelig" in xml.headers["content-disposition"]


def test_a_company_with_one_melding_is_asked_for_a_second_it_does_not_file(
    client: TestClient,
) -> None:
    company = books(client)
    refused = client.get(
        f"/api/v1/companies/{company['id']}/mva",
        params={"aar": LAST_YEAR, "naering": "primaernaering"},
    )
    assert refused.status_code == 422
    assert refused.json()["code"] == "unknown_naering"
    body = client.get(f"/api/v1/companies/{company['id']}/mva").json()
    assert [n["category"] for n in body["naeringer"]] == ["alminnelig"]
    assert body["settings"]["second"] is None


def test_a_vat_account_cannot_belong_to_a_melding(client: TestClient) -> None:
    company = _farm(client)
    refused = write(
        client,
        "PUT",
        f"/api/v1/companies/{company['id']}/mva/andre-melding",
        json={
            "period_type": "toMaaneder",
            "valid_from": date(LAST_YEAR, 1, 1).isoformat(),
            "accounts": ["2700"],
        },
    )
    assert refused.status_code == 422
    assert refused.json()["code"] == "invalid_naering_setup"


def test_each_melding_is_filed_and_settled_on_its_own(
    client: TestClient,
    skatteetaten: Skatteetaten,  # noqa: F811  (the imported fixture)
) -> None:
    company = _farm(client)
    _sell(client, company["id"], "12500.00")
    _sell(client, company["id"], "5000.00", SECOND_SALES)

    checked = check(client, company["id"], naering="alminnelig")
    assert checked.status_code == 200, checked.text
    assert checked.json()["category"] == "alminnelig"
    assert b"<meldingskategori>alminnelig</meldingskategori>" in skatteetaten.validated[-1]
    assert send(client, company["id"], checked.json()).status_code == 200
    # Only the ploughing's 1000 moved: the farm's 2500 waits for its own melding.
    assert settlement_lines(client, company["id"]) == [
        ("2700", "1000.00", "0.00"),
        ("2740", "0.00", "1000.00"),
    ]

    skatteetaten.process_calls = 0
    skatteetaten.melding = None
    farm = check(client, company["id"])
    assert farm.status_code == 200, farm.text
    assert farm.json()["category"] == "primaernaering"
    assert farm.json()["fastsatt_merverdiavgift"] == "2500.00"
    assert send(client, company["id"], farm.json()).status_code == 200
    assert settlement_lines(client, company["id"])[2:] == [
        ("2700", "2500.00", "0.00"),
        ("2740", "0.00", "2500.00"),
    ]


def test_a_change_that_would_move_filed_vat_is_refused(
    client: TestClient,
    skatteetaten: Skatteetaten,  # noqa: F811  (the imported fixture)
) -> None:
    company = _farm(client)
    _sell(client, company["id"], "5000.00", SECOND_SALES)
    filing = check(client, company["id"], naering="alminnelig").json()
    assert send(client, company["id"], filing).status_code == 200

    # Taking the account away would move the filed ploughing sale into the
    # farm's melding for the same dates.
    moved = write(
        client,
        "PUT",
        f"/api/v1/companies/{company['id']}/mva/andre-melding",
        json={
            "period_type": "toMaaneder",
            "valid_from": date(LAST_YEAR, 1, 1).isoformat(),
            "accounts": [SECOND_COSTS],
        },
    )
    assert moved.status_code == 409, moved.text
    assert moved.json()["code"] == "filed_lines_would_move"
    removed = write(client, "DELETE", f"/api/v1/companies/{company['id']}/mva/andre-melding")
    assert removed.status_code == 409
    swapped = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/mva/innstillinger",
        json={"category": "alminnelig"},
    )
    assert swapped.status_code == 409


def test_a_second_melding_set_up_by_mistake_can_be_removed(client: TestClient) -> None:
    company = _farm(client)
    removed = write(client, "DELETE", f"/api/v1/companies/{company['id']}/mva/andre-melding")
    assert removed.status_code == 200, removed.text
    assert removed.json()["second"] is None


# --- Filed from another program --------------------------------------------------------


def _mark(client: TestClient, company_id: int, **extra: Any) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/mva/termin/annet-program",
        json={
            "aar": LAST_YEAR,
            "termin": 1,
            "filed_on": date(LAST_YEAR, 4, 8).isoformat(),
            **extra,
        },
    )


def test_a_term_filed_elsewhere_is_not_overdue_and_cannot_be_filed_again(
    client: TestClient,
    skatteetaten: Skatteetaten,  # noqa: F811  (the imported fixture)
) -> None:
    company = books(client)
    book(client, company["id"], "salg_hoy_sats", "12500.00")
    book(client, company["id"], "varekjop", "1250.00")

    before = client.get(f"/api/v1/companies/{company['id']}/mva", params={"aar": LAST_YEAR})
    assert before.json()["periods"][0]["is_overdue"] is True

    marked = _mark(client, company["id"], amount="2249.60")
    assert marked.status_code == 200, marked.text
    body = marked.json()
    assert body["status"] == "annet_program"
    assert body["validation_blocks"] is False
    # Myntos says 2250.00; Tripletex rounds to kroner, so 40 øre is agreement.
    assert body["elsewhere"]["difference"] == "0.40"
    assert body["elsewhere"]["agrees"] is True

    row = client.get(f"/api/v1/companies/{company['id']}/mva", params={"aar": LAST_YEAR}).json()[
        "periods"
    ][0]
    assert row["status"] == "annet_program"
    assert row["filed_elsewhere"] is True
    assert row["is_overdue"] is False
    assert row["elsewhere_difference"] == "0.40"

    period = client.get(
        f"/api/v1/companies/{company['id']}/mva/termin", params={"aar": LAST_YEAR, "termin": 1}
    ).json()
    assert "filed_elsewhere" in {blocker["code"] for blocker in period["blockers"]}
    assert period["filed_elsewhere"]["id"] == body["id"]

    refused = check(client, company["id"])
    assert refused.status_code == 409
    assert refused.json()["code"] == "filed_elsewhere"
    assert skatteetaten.validated == []
    no_xml = client.get(
        f"/api/v1/companies/{company['id']}/mva/innsendinger/{body['id']}/melding.xml"
    )
    assert no_xml.status_code == 409
    twice = _mark(client, company["id"])
    assert twice.status_code == 409
    assert twice.json()["code"] == "filed_elsewhere"

    undone = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/mva/innsendinger/{body['id']}/angre",
    )
    assert undone.status_code == 200, undone.text
    assert undone.json()["status"] == "opphevet"
    assert undone.json()["elsewhere"]["withdrawn_at"] is not None
    again = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/mva/innsendinger/{body['id']}/angre",
    )
    assert again.status_code == 409
    assert again.json()["code"] == "not_filed_elsewhere"

    # Taken back, the term is Myntos's to file again.
    filing = check(client, company["id"])
    assert filing.status_code == 200, filing.text
    assert send(client, company["id"], filing.json()).status_code == 200
    late = _mark(client, company["id"])
    assert late.status_code == 409
    assert late.json()["code"] == "already_filed"


def test_a_filing_day_must_be_after_the_term_and_not_in_the_future(client: TestClient) -> None:
    company = books(client)
    early = _mark(client, company["id"], filed_on=date(LAST_YEAR, 2, 28).isoformat())
    assert early.status_code == 422
    assert early.json()["code"] == "invalid_filing_date"
    future = _mark(client, company["id"], filed_on=(date.today() + timedelta(days=2)).isoformat())
    assert future.status_code == 422


def test_a_key_that_may_only_ask_leaves_the_statement_to_its_person(
    client: TestClient,
) -> None:
    company = books(client)
    header = agent_header(client, company["id"], scopes=("read",), approval_scopes=("write",))
    held = client.post(
        f"/api/v1/companies/{company['id']}/mva/termin/annet-program",
        json={"aar": LAST_YEAR, "termin": 1, "filed_on": date(LAST_YEAR, 4, 8).isoformat()},
        headers=header,
    )
    assert held.status_code == 202, held.text
    # Nothing is recorded until the person approves it.
    row = client.get(f"/api/v1/companies/{company['id']}/mva", params={"aar": LAST_YEAR}).json()[
        "periods"
    ][0]
    assert row["filed_elsewhere"] is False


def test_a_request_to_file_is_done_when_the_term_is_marked_as_filed_elsewhere(
    client: TestClient,
) -> None:
    company = books(client)
    header = agent_header(client, company["id"], scopes=("read",))
    asked = client.post(
        f"/api/v1/companies/{company['id']}/godkjenninger/innsending",
        json={"type": "mva_melding", "aar": LAST_YEAR, "termin": 1},
        headers=header,
    ).json()
    assert asked["status"] == "pending"
    assert _mark(client, company["id"]).status_code == 200
    seen = client.get(f"/api/v1/companies/{company['id']}/godkjenninger/{asked['id']}").json()
    assert seen["status"] == "done"
    refused = client.post(
        f"/api/v1/companies/{company['id']}/godkjenninger/innsending",
        json={"type": "mva_melding", "aar": LAST_YEAR, "termin": 2},
        headers=header,
    )
    assert refused.status_code == 200
    marked = client.post(
        f"/api/v1/companies/{company['id']}/godkjenninger/innsending",
        json={"type": "mva_melding", "aar": LAST_YEAR, "termin": 1},
        headers=header,
    )
    assert marked.status_code == 409
    assert marked.json()["code"] == "filed_elsewhere"


def test_a_request_to_file_names_the_melding_when_there_are_two(client: TestClient) -> None:
    company = _farm(client)
    header = agent_header(client, company["id"], scopes=("read",))
    asked = client.post(
        f"/api/v1/companies/{company['id']}/godkjenninger/innsending",
        json={"type": "mva_melding", "aar": LAST_YEAR, "termin": 1, "naering": "alminnelig"},
        headers=header,
    )
    assert asked.status_code == 200, asked.text
    assert asked.json()["filing"]["naering"] == "alminnelig"
    assert "alminnelig" in asked.json()["label"]
    # The farm's yearly term 1 is another request: the same number, another melding.
    farm = client.post(
        f"/api/v1/companies/{company['id']}/godkjenninger/innsending",
        json={"type": "mva_melding", "aar": LAST_YEAR, "termin": 1},
        headers=header,
    )
    assert farm.json()["id"] != asked.json()["id"]
    # Marking the ploughing term settles its request and leaves the farm's waiting.
    assert _mark(client, company["id"], naering="alminnelig").status_code == 200
    seen = client.get(f"/api/v1/companies/{company['id']}/godkjenninger/{asked.json()['id']}")
    assert seen.json()["status"] == "done"
    waiting = client.get(f"/api/v1/companies/{company['id']}/godkjenninger/{farm.json()['id']}")
    assert waiting.json()["status"] == "pending"


def test_the_old_programs_settlement_is_proposed_and_recorded_from(client: TestClient) -> None:
    """Tripletex's own settlement of January-February, brought over by the SAF-T import."""
    from tests.test_saft_voucher_import import (
        _bilag,
        _create_missing,
        _opening,
        _post,
        year_file,
    )
    from tests.test_saft_voucher_import import _books as saft_books

    company = saft_books(client)
    content = year_file()
    _opening(client, company["id"], content)
    _create_missing(client, company["id"], _bilag(client, company["id"], content).json())
    assert _post(client, company["id"], content).status_code == 201

    row = client.get(f"/api/v1/companies/{company['id']}/mva", params={"aar": LAST_YEAR}).json()[
        "periods"
    ][0]
    proposal = row["elsewhere_proposal"]
    assert proposal is not None, row
    # 250 out, 200 + 15 in, 35 onto 2740: exactly Myntos's melding for the term.
    assert (proposal["output_vat"], proposal["input_vat"], proposal["amount"]) == (
        "250.00",
        "215.00",
        "35.00",
    )
    assert proposal["matches"] is True

    marked = _mark(
        client,
        company["id"],
        amount=proposal["amount"],
        voucher_id=proposal["voucher_id"],
    )
    assert marked.status_code == 200, marked.text
    assert marked.json()["elsewhere"]["voucher_id"] == proposal["voucher_id"]
    assert marked.json()["elsewhere"]["agrees"] is True
    after = client.get(f"/api/v1/companies/{company['id']}/mva", params={"aar": LAST_YEAR}).json()[
        "periods"
    ][0]
    assert after["filed_elsewhere"] is True
    assert after["elsewhere_proposal"] is None
