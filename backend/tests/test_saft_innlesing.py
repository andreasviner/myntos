"""The previous program's books brought over on the server: several years, in one go.

The files are the synthetic Tripletex-shaped ones from ``test_saft_import`` and
``test_saft_voucher_import``, with a year before them: a sale, and the year-end
the accountant booked in Tripletex (the tax cost and the result onto equity).
The company's first year in Myntos is last year, so that earlier file is a year
Myntos does not have yet, which is what the farm pilot uploaded and what used to
be refused.

Background work runs inside the request under the test client (Starlette runs
``BackgroundTasks`` before the call returns), so an import is read, or posted,
by the time the upload's answer is in hand.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import join_company, write
from tests.test_arsoppgjor_api import LAST_YEAR, _books, _close
from tests.test_cashflow_api import record
from tests.test_saft_import import ACCOUNTS, saft_file
from tests.test_saft_voucher_import import EXTRA_ACCOUNTS, _opening, year_file

EARLIER = LAST_YEAR - 1


def _line(record_id: str, account: str, amount: str, on: str, *, tax: str | None = None) -> str:
    value = Decimal(amount)
    side = "Debit" if value >= 0 else "Credit"
    taxed = ""
    if tax is not None:
        code, base, vat = tax.split("/")
        taxed = (
            f"<TaxInformation><TaxType>MVA</TaxType><TaxCode>{code}</TaxCode>"
            f"<TaxPercentage>25</TaxPercentage><TaxBase>{base}</TaxBase><{side}TaxAmount>"
            f"<Amount>{vat}</Amount></{side}TaxAmount></TaxInformation>"
        )
    return (
        f"<Line><RecordID>{record_id}</RecordID><AccountID>{account}</AccountID>"
        f"<ValueDate>{EARLIER}-{on}</ValueDate><Description>Linje</Description>"
        f"<{side}Amount><Amount>{abs(value):.2f}</Amount></{side}Amount>{taxed}</Line>"
    )


def _voucher(number: str, on: str, text: str, lines: list[str]) -> str:
    return (
        f"<Transaction><TransactionID>{number}</TransactionID><Period>{int(on[:2])}</Period>"
        f"<PeriodYear>{EARLIER}</PeriodYear><TransactionDate>{EARLIER}-{on}</TransactionDate>"
        f"<Description>{text}</Description><GLPostingDate>{EARLIER}-{on}</GLPostingDate>"
        f"{''.join(lines)}</Transaction>"
    )


#: The year before: a sale at 25 %, and the year-end as Tripletex books it, the
#: tax cost and the rest of the result onto annen egenkapital.
EARLIER_VOUCHERS = [
    _voucher(
        "17",
        "06-15",
        "Melk solgt",
        [
            _line("1", "1920", "1250.00", "06-15"),
            _line("2", "307120", "-1000.00", "06-15", tax="3/1000.00/250.00"),
            _line("3", "2700", "-250.00", "06-15"),
        ],
    ),
    _voucher(
        "99",
        "12-31",
        "Årsoppgjør",
        [
            _line("1", "8300", "100.00", "12-31"),
            _line("2", "2500", "-100.00", "12-31"),
            _line("3", "8960", "900.00", "12-31"),
            _line("4", "2050", "-900.00", "12-31"),
        ],
    ),
]


def earlier_file(*, end: str = "12-31", orgnr: str | None = None) -> bytes:
    entries = (
        f"<GeneralLedgerEntries><NumberOfEntries>{len(EARLIER_VOUCHERS)}</NumberOfEntries>"
        "<TotalDebit>2250.00</TotalDebit><TotalCredit>2250.00</TotalCredit>"
        "<Journal><JournalID>GL</JournalID><Description>Hovedbok</Description><Type>GL</Type>"
        f"{''.join(EARLIER_VOUCHERS)}</Journal></GeneralLedgerEntries>"
    )
    content = saft_file(
        year=EARLIER,
        accounts=ACCOUNTS + EXTRA_ACCOUNTS,
        after_master=entries,
        **({"orgnr": orgnr} if orgnr else {}),
    )
    return content.replace(
        f"<SelectionEndDate>{EARLIER}-12-31".encode(), f"<SelectionEndDate>{EARLIER}-{end}".encode()
    )


def unfinished_year_file() -> bytes:
    """This year's file as taken at the end of February: the year is still running."""
    return year_file().replace(
        f"<SelectionEndDate>{LAST_YEAR}-12-31".encode(),
        f"<SelectionEndDate>{LAST_YEAR}-02-28".encode(),
    )


def _upload(
    client: TestClient, company_id: int, *files: tuple[str, bytes], compare: bool = False
) -> Any:
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/saft/innlesing",
        files=[("files", (name, content, "application/xml")) for name, content in files],
        data={"sammenlign": "true"} if compare else None,
    )
    assert response.status_code == 202, response.text
    return response.json()


def _get(client: TestClient, company_id: int, import_id: int) -> dict[str, Any]:
    response = client.get(f"/api/v1/companies/{company_id}/saft/innlesing/{import_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _post(client: TestClient, company_id: int, import_id: int) -> Any:
    return write(
        client, "POST", f"/api/v1/companies/{company_id}/saft/innlesing/{import_id}/foring"
    )


def _codes(review: dict[str, Any]) -> set[str]:
    return {finding["code"] for finding in review["findings"]}


def _both_years(client: TestClient, company_id: int) -> dict[str, Any]:
    uploaded = _upload(
        client,
        company_id,
        (f"{EARLIER}.xml", earlier_file()),
        (f"{LAST_YEAR}.xml", unfinished_year_file()),
    )
    return _get(client, company_id, uploaded["id"])


# --- The whole move ----------------------------------------------------------------------


def test_an_earlier_year_and_the_unfinished_one_come_over_in_one_go(client: TestClient) -> None:
    company = _books(client)
    read = _both_years(client, company["id"])
    assert read["status"] == "ready", read
    assert read["files_kept"] is True
    assert [f["year"] for f in read["files"]] == [EARLIER, LAST_YEAR]
    review = read["review"]
    assert review["kind"] == "import"
    assert review["postable"] is True, review["findings"]
    assert read["may_post"] is True
    # The books now start a year earlier, with the opening balance from that file.
    assert (review["first_year_before"], review["first_year_after"]) == (LAST_YEAR, EARLIER)
    assert review["opening"]["year"] == EARLIER
    assert review["opening"]["action"] == "post"
    years = {year["year"]: year for year in review["years"]}
    assert years[EARLIER]["complete"] is True
    assert years[EARLIER]["opened"] is True
    assert years[EARLIER]["vouchers"] == 2
    assert years[LAST_YEAR]["complete"] is False
    assert years[LAST_YEAR]["vouchers"] == 5
    # What the files name and the books lack is listed once, and added by the post.
    numbers = [add["number"] for add in review["accounts_to_add"]]
    assert len(numbers) == len(set(numbers))
    assert {"3071", "4020", "1451"} <= set(numbers)
    assert "Bygdemeieriet AS" in {add["name"] for add in review["parties_to_add"]}
    assert {"aar_lagt_til", "tidligere_aar_aapne", "mva_meldinger_sendt"} <= _codes(review)
    # A preview: nothing of it is in the books yet.
    state = client.get(f"/api/v1/companies/{company['id']}/inngaende-balanse").json()
    assert state["standing"] is None
    assert state["year"] == LAST_YEAR

    posted = _post(client, company["id"], read["id"])
    assert posted.status_code == 202, posted.text
    done = _get(client, company["id"], read["id"])
    assert done["status"] == "posted", done
    assert done["files_kept"] is False
    assert done["result"]["vouchers"] == 7

    state = client.get(f"/api/v1/companies/{company['id']}/inngaende-balanse").json()
    assert state["year"] == EARLIER
    assert state["standing"]["transaction_date"] == f"{EARLIER}-01-01"
    years_now = client.get(f"/api/v1/companies/{company['id']}/regnskapsaar").json()
    assert {year["year"] for year in years_now} >= {EARLIER, LAST_YEAR}

    # Read again, the same files have nothing new.
    again = _both_years(client, company["id"])
    assert again["review"]["postable"] is False
    assert {y["already_imported"] for y in again["review"]["years"]} == {2, 5}

    # And the running year agrees with the old program's, compared on the server.
    compared = _upload(
        client, company["id"], (f"{LAST_YEAR}.xml", unfinished_year_file()), compare=True
    )
    verdict = _get(client, company["id"], compared["id"])
    assert verdict["status"] == "ready", verdict
    assert verdict["review"]["kind"] == "sammenligning"
    assert verdict["review"]["matches"] is True, verdict["review"]
    assert verdict["files_kept"] is False


def test_the_earlier_year_closes_with_what_the_old_program_booked(client: TestClient) -> None:
    company = _books(client)
    read = _both_years(client, company["id"])
    assert _post(client, company["id"], read["id"]).status_code == 202

    plan = client.get(f"/api/v1/companies/{company['id']}/arsavslutning?aar={EARLIER}")
    assert plan.status_code == 200, plan.text
    body = plan.json()
    codes = {finding["code"] for finding in body["findings"]}
    assert "allerede_disponert" not in codes
    assert "avsluttet_i_tidligere_program" in codes
    # Tripletex booked the tax and put the rest on equity: nothing is left over.
    assert body["disposition_lines"] == []
    assert Decimal(body["tax"]) == Decimal("100.00")
    closed = _close(client, company["id"], year=EARLIER)
    assert closed.status_code == 201, closed.text


def test_rates_reach_back_only_where_the_files_prove_them(client: TestClient) -> None:
    company = _books(client)
    read = _both_years(client, company["id"])
    assert _post(client, company["id"], read["id"]).status_code == 202
    # The files sold at 25 % under code 3, so a sale in the earlier year books.
    sale = record(
        client,
        company["id"],
        type_code="salg_hoy_sats",
        gross_amount="125.00",
        occurred_on=date(EARLIER, 9, 1).isoformat(),
    )
    assert sale.status_code == 201, sale.text
    # Nothing in them was bought with deduction under code 1, so Myntos does not
    # claim today's rate for that year.
    purchase = record(
        client,
        company["id"],
        type_code="datakostnad",
        gross_amount="125.00",
        occurred_on=date(EARLIER, 9, 2).isoformat(),
        tax_code="1",
    )
    assert purchase.status_code == 422, purchase.text


def test_an_opening_balance_for_the_later_year_is_replaced_and_its_moves_kept(
    client: TestClient,
) -> None:
    company = _books(client)
    # The person had brought this year's balance over and moved the
    # driftskreditt onto the bank account.
    _opening(client, company["id"], year_file(), **{"239010": "1920"})
    read = _both_years(client, company["id"])
    review = read["review"]
    assert review["postable"] is True, review["findings"]
    assert review["opening"]["action"] == "replace"
    assert review["opening"]["replaces_year"] == LAST_YEAR
    assert "inngaende_balanse_flyttes" in _codes(review)
    assert _post(client, company["id"], read["id"]).status_code == 202
    assert _get(client, company["id"], read["id"])["status"] == "posted"

    state = client.get(f"/api/v1/companies/{company['id']}/inngaende-balanse").json()
    assert state["standing"]["transaction_date"] == f"{EARLIER}-01-01"
    # The old one is still there, reversed, beside its replacement.
    assert sum(1 for voucher in state["history"] if voucher["is_reversal"]) == 1
    accounts = {line["account_number"] for line in state["standing"]["lines"]}
    assert "1920" in accounts
    assert not any(
        (line["description"] or "").startswith("239010") and line["account_number"] != "1920"
        for line in state["standing"]["lines"]
    )


# --- What stops it -----------------------------------------------------------------------


def test_files_that_are_not_years_in_a_row_are_refused(client: TestClient) -> None:
    company = _books(client)
    cut_short = _upload(
        client,
        company["id"],
        ("tidligere.xml", earlier_file(end="06-30")),
        ("i-aar.xml", unfinished_year_file()),
    )
    failed = _get(client, company["id"], cut_short["id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "saft_wrong_year"
    assert "Bare det siste året kan være uferdig" in failed["error"]

    gap = _upload(
        client,
        company["id"],
        ("eldre.xml", earlier_file().replace(str(EARLIER).encode(), str(EARLIER - 1).encode())),
        ("i-aar.xml", unfinished_year_file()),
    )
    assert _get(client, company["id"], gap["id"])["error_code"] == "saft_wrong_year"

    other = _upload(client, company["id"], ("annen.xml", earlier_file(orgnr="910000012")))
    refused = _get(client, company["id"], other["id"])
    assert refused["error_code"] == "saft_wrong_company"
    assert refused["review"] is None


def test_a_post_runs_the_review_again_on_the_books_as_they_are(client: TestClient) -> None:
    company = _books(client)
    uploaded = _upload(client, company["id"], ("i-aar.xml", unfinished_year_file()))
    assert _get(client, company["id"], uploaded["id"])["review"]["postable"] is True
    # A payment registered in the same weeks after the files were read.
    entry = record(
        client,
        company["id"],
        type_code="datakostnad",
        gross_amount="125.00",
        occurred_on=date(LAST_YEAR, 2, 10).isoformat(),
        tax_code="1",
    )
    assert entry.status_code == 201, entry.text
    assert _post(client, company["id"], uploaded["id"]).status_code == 202
    stopped = _get(client, company["id"], uploaded["id"])
    assert stopped["status"] == "ready"
    assert stopped["error_code"] == "review_changed"
    assert "bilag_i_perioden" in _codes(stopped["review"])
    # Nothing was posted: no opening balance either.
    state = client.get(f"/api/v1/companies/{company['id']}/inngaende-balanse").json()
    assert state["standing"] is None
    again = _post(client, company["id"], uploaded["id"])
    assert again.status_code == 422
    assert again.json()["code"] == "import_not_postable"


def test_a_member_cannot_add_accounts_so_cannot_post_them(client: TestClient) -> None:
    company = _books(client)
    join_company(client, company["id"], email="medlem@example.no", role="member")
    uploaded = _upload(client, company["id"], ("i-aar.xml", unfinished_year_file()))
    read = _get(client, company["id"], uploaded["id"])
    assert read["review"]["accounts_to_add"]
    assert read["may_post"] is False
    refused = _post(client, company["id"], uploaded["id"])
    assert refused.status_code == 422
    assert "administrator" in refused.json()["detail"]


def test_an_import_is_thrown_away_but_a_posted_one_stays(client: TestClient) -> None:
    company = _books(client)
    first = _upload(client, company["id"], ("i-aar.xml", unfinished_year_file()))
    gone = write(
        client, "DELETE", f"/api/v1/companies/{company['id']}/saft/innlesing/{first['id']}"
    )
    assert gone.status_code == 204, gone.text
    assert (
        client.get(f"/api/v1/companies/{company['id']}/saft/innlesing/{first['id']}").status_code
        == 404
    )
    second = _upload(client, company["id"], ("i-aar.xml", unfinished_year_file()))
    assert _post(client, company["id"], second["id"]).status_code == 202
    kept = write(
        client, "DELETE", f"/api/v1/companies/{company['id']}/saft/innlesing/{second['id']}"
    )
    assert kept.status_code == 409
    listed = client.get(f"/api/v1/companies/{company['id']}/saft/innlesing").json()
    assert [(item["id"], item["status"]) for item in listed] == [(second["id"], "posted")]


# --- Where the vouchers are seen -----------------------------------------------------------


def test_the_bilagsjournal_shows_what_came_over_and_the_bank_carried(client: TestClient) -> None:
    company = _books(client)
    read = _both_years(client, company["id"])
    assert _post(client, company["id"], read["id"]).status_code == 202
    path = f"/api/v1/companies/{company['id']}/bilag"

    page = client.get(path, params={"aar": EARLIER}).json()
    assert page["total_count"] == 3  # the opening balance and the two vouchers
    assert {(j["code"], j["vouchers"]) for j in page["journals"]} == {("IB", 1), ("IMPORT", 2)}
    imported = client.get(path, params={"aar": EARLIER, "journal": "IMPORT"}).json()
    assert [v["voucher_no"] for v in imported["vouchers"]] == [f"{EARLIER}-17", f"{EARLIER}-99"]
    assert imported["vouchers"][0]["source_document"].startswith("Tripletex bilag 17")

    bank = client.get(path, params={"aar": EARLIER, "konto": "1920"}).json()
    assert Decimal(bank["opening_balance"]) == Decimal("0.00")
    assert [Decimal(v["movement"]) for v in bank["vouchers"]] == [
        Decimal("100000.00"),
        Decimal("1250.00"),
    ]
    assert Decimal(bank["closing_balance"]) == Decimal("101250.00")
    # The next year opens with it.
    after = client.get(path, params={"aar": LAST_YEAR, "konto": "1920"}).json()
    assert Decimal(after["opening_balance"]) == Decimal("101250.00")

    missing = client.get(path, params={"aar": EARLIER, "konto": "9999"})
    assert missing.status_code == 404


def test_books_that_started_in_the_earliest_file_open_at_zero(client: TestClient) -> None:
    """The farm pilot's own case: its Tripletex books began in June, so the file opens at zero."""
    company = _books(client)
    zero = [
        (account_id, text, cat, code, "0", "0") for account_id, text, cat, code, _, _ in ACCOUNTS
    ]
    entries = (
        f"<GeneralLedgerEntries><NumberOfEntries>{len(EARLIER_VOUCHERS)}</NumberOfEntries>"
        "<TotalDebit>2250.00</TotalDebit><TotalCredit>2250.00</TotalCredit>"
        "<Journal><JournalID>GL</JournalID><Description>Hovedbok</Description><Type>GL</Type>"
        f"{''.join(EARLIER_VOUCHERS)}</Journal></GeneralLedgerEntries>"
    )
    started = saft_file(
        year=EARLIER,
        accounts=zero + EXTRA_ACCOUNTS,
        customers=[],
        suppliers=[],
        after_master=entries,
    )
    uploaded = _upload(client, company["id"], (f"{EARLIER}.xml", started))
    review = _get(client, company["id"], uploaded["id"])["review"]
    assert review["postable"] is True, review["findings"]
    assert review["opening"]["action"] == "none"
    assert "ingen_inngaende_saldoer" in _codes(review)
    assert _post(client, company["id"], uploaded["id"]).status_code == 202
    state = client.get(f"/api/v1/companies/{company['id']}/inngaende-balanse").json()
    assert state["year"] == EARLIER
    assert state["standing"] is None
    bank = client.get(
        f"/api/v1/companies/{company['id']}/bilag", params={"aar": EARLIER, "konto": "1920"}
    ).json()
    assert Decimal(bank["closing_balance"]) == Decimal("1250.00")


def test_a_member_cannot_add_avdelinger_so_cannot_post_them(client: TestClient) -> None:
    """Dimensions are how the books are set up, like the kontoplan: ``ledger:chart``."""
    from tests.test_saft_import import _create_missing, upload
    from tests.test_saft_voucher_import import _analysed_file, _bilag

    company = _books(client)
    content = _analysed_file()
    # An administrator has added every account the file needs, and nothing else.
    _create_missing(client, company["id"], upload(client, company["id"], content).json())
    _create_missing(client, company["id"], _bilag(client, company["id"], content).json())
    join_company(client, company["id"], email="medlem@example.no", role="member")

    uploaded = _upload(client, company["id"], ("i-aar.xml", content))
    read = _get(client, company["id"], uploaded["id"])
    assert read["review"]["accounts_to_add"] == []
    assert [add["value"] for add in read["review"]["dimensions_to_add"]] == ["20"]
    assert read["may_post"] is False
    refused = _post(client, company["id"], uploaded["id"])
    assert refused.status_code == 422
    assert "administrator" in refused.json()["detail"]
    assert client.get(f"/api/v1/companies/{company['id']}/dimensjoner").json() == []
