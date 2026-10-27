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

import asyncio
import gzip
import os
import time
import zipfile
from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from myntos.api.deps import get_document_storage, get_working_files
from myntos.api.v1.saft_innlesing import get_import_sessions
from myntos.core.clock import utc_now
from myntos.saft import jobs
from myntos.saft.models import SaftImport
from myntos.storage.base import StorageError
from myntos.storage.working import LocalWorkingStore
from tests.conftest import join_company, write
from tests.test_arsoppgjor_api import LAST_YEAR, _books, _close, _company
from tests.test_cashflow_api import record
from tests.test_saft_import import ACCOUNTS, ORGNR, saft_file
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
        orgnr=orgnr or ORGNR,
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


# --- What was uploaded, and where it is kept ----------------------------------------------

EHF = (
    b'<?xml version="1.0" encoding="UTF-8"?><Invoice xmlns="urn:oasis:names:specification:'
    b'ubl:schema:xsd:Invoice-2"><ID>12</ID></Invoice>'
)
CAMT = (
    b'<?xml version="1.0"?><Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">'
    b"<BkToCstmrStmt/></Document>"
)


def _zip(*members: tuple[str, bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members:
            archive.writestr(name, content)
    return buffer.getvalue()


def _store(client: TestClient) -> LocalWorkingStore:
    store: LocalWorkingStore = client.app.dependency_overrides[get_working_files]()  # type: ignore[attr-defined]
    return store


def _kept(client: TestClient) -> list[bytes]:
    """Every working copy in the store: a SAF-T file decompressed, an export as it came."""
    store = _store(client)
    stored = [asyncio.run(store.load(item.key)) for item in asyncio.run(store.listing())]
    return [gzip.decompress(item) if item[:2] == b"\x1f\x8b" else item for item in stored]


def _refused(client: TestClient, company_id: int, name: str, content: bytes) -> dict[str, Any]:
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/saft/innlesing",
        files=[("files", (name, content, "application/octet-stream"))],
    )
    assert response.status_code == 422, response.text
    body: dict[str, Any] = response.json()
    return body


KVITTERING = "%PDF-1.7 kvittering fra Felleskjøpet".encode()
REGNEARK = b"PK\x03\x04 regneark"


def _export() -> bytes:
    """Fiken's complete export as far as it can be guessed: the years, the documents, the rest."""
    return _zip(
        (f"fiken-komplett-gard/SAF-T/saft-gard-{EARLIER}.xml", earlier_file()),
        (f"fiken-komplett-gard/SAF-T/saft-gard-{LAST_YEAR}.xml", unfinished_year_file()),
        ("fiken-komplett-gard/Bilag/2025/kvittering-17.pdf", KVITTERING),
        ("fiken-komplett-gard/Fakturaer/faktura-12.xml", EHF),
        ("fiken-komplett-gard/Excel/fiken-regnskap-gard.xlsx", REGNEARK),
        # What a Mac leaves in a ZIP it made: not a document.
        ("__MACOSX/fiken-komplett-gard/._kvittering-17.pdf", b"\x00\x05\x16\x07"),
        ("fiken-komplett-gard/.DS_Store", b"\x00\x00\x00\x01Bud1"),
    )


def _documents(client: TestClient, company_id: int, **params: Any) -> dict[str, Any]:
    response = client.get(f"/api/v1/companies/{company_id}/saft/dokumenter", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_fikens_complete_export_comes_over_with_its_documents(client: TestClient) -> None:
    """Uploaded as it downloads: the years are read, and every document is kept on the post."""
    company = _books(client)
    export = _export()
    uploaded = _upload(client, company["id"], ("fiken-komplett-gard.zip", export))
    read = _get(client, company["id"], uploaded["id"])
    assert read["status"] == "ready", read
    assert [(f["filename"], f["kind"]) for f in read["files"]] == [
        (f"saft-gard-{EARLIER}.xml", "read"),
        (f"saft-gard-{LAST_YEAR}.xml", "read"),
        ("fiken-komplett-gard.zip", "export"),
    ]
    assert [f["year"] for f in read["files"]] == [EARLIER, LAST_YEAR, None]
    review = read["review"]
    assert review["postable"] is True, review["findings"]
    # The two SAF-T files as they were in the export, and the export whole.
    assert sorted(_kept(client)) == sorted([earlier_file(), unfinished_year_file(), export])
    [archive] = review["archives"]
    assert archive["file_name"] == "fiken-komplett-gard.zip"
    # Everything but what the Mac left: the SAF-T files are the books'
    # documentation before Myntos as much as the receipts are.
    assert archive["documents"] == 5
    assert archive["skipped_count"] == 0
    assert archive["kept"] is None
    # The folder every file is in says nothing; the ones inside it do.
    assert {f["name"]: f["documents"] for f in archive["folders"]} == {
        "SAF-T": 2,
        "Bilag": 1,
        "Fakturaer": 1,
        "Excel": 1,
    }
    assert _documents(client, company["id"])["total_count"] == 0, "nothing before the yes"

    assert _post(client, company["id"], read["id"]).status_code == 202
    done = _get(client, company["id"], read["id"])
    assert done["status"] == "posted", done
    assert done["error"] is None
    assert done["result"]["archives"][0]["kept"] == 5
    assert done["result"]["archives"][0]["already_kept"] == 0
    assert done["files_kept"] is False
    assert _kept(client) == []

    listed = _documents(client, company["id"])
    assert listed["total_count"] == 5
    assert [d["path"] for d in listed["documents"]] == sorted(
        [
            "fiken-komplett-gard/Bilag/2025/kvittering-17.pdf",
            "fiken-komplett-gard/Excel/fiken-regnskap-gard.xlsx",
            "fiken-komplett-gard/Fakturaer/faktura-12.xml",
            f"fiken-komplett-gard/SAF-T/saft-gard-{EARLIER}.xml",
            f"fiken-komplett-gard/SAF-T/saft-gard-{LAST_YEAR}.xml",
        ]
    )
    types = {d["filename"]: d["content_type"] for d in listed["documents"]}
    # By what the bytes are, never by the name the export gave them.
    assert types["kvittering-17.pdf"] == "application/pdf"
    assert types["faktura-12.xml"] == "application/xml"
    assert types["fiken-regnskap-gard.xlsx"] == "application/octet-stream"
    found = _documents(client, company["id"], sok="KVITTERING")
    assert [d["filename"] for d in found["documents"]] == ["kvittering-17.pdf"]
    assert _documents(client, company["id"], sok="%")["total_count"] == 0

    document = found["documents"][0]
    url = f"/api/v1/companies/{company['id']}/saft/dokumenter/{document['id']}"
    downloaded = client.get(url)
    assert downloaded.status_code == 200
    assert downloaded.content == KVITTERING
    assert downloaded.headers["content-type"] == "application/pdf"
    assert downloaded.headers["content-disposition"] == 'attachment; filename="kvittering-17.pdf"'


def test_another_companys_documents_are_not_found(client: TestClient) -> None:
    company = _books(client)
    uploaded = _upload(client, company["id"], ("fiken-komplett-gard.zip", _export()))
    _post(client, company["id"], uploaded["id"])
    document = _documents(client, company["id"])["documents"][0]

    # The same person's other books: the wall is between companies, not people.
    other_id = _company(
        client, company["organization_id"], orgnr="915933149", name="Annen Gard AS"
    )["id"]
    assert _documents(client, other_id)["total_count"] == 0
    missing = client.get(f"/api/v1/companies/{other_id}/saft/dokumenter/{document['id']}")
    assert missing.status_code == 404
    assert missing.json()["code"] == "imported_document_not_found"


def test_the_export_again_keeps_its_documents_alone_and_none_twice(client: TestClient) -> None:
    """Books brought over before the documents were kept: the export again, for its documents."""
    company = _books(client)
    first = _upload(
        client,
        company["id"],
        (f"{EARLIER}.xml", earlier_file()),
        (f"{LAST_YEAR}.xml", unfinished_year_file()),
    )
    assert _post(client, company["id"], first["id"]).status_code == 202
    assert _get(client, company["id"], first["id"])["status"] == "posted"

    again = _upload(client, company["id"], ("fiken-komplett-gard.zip", _export()))
    read = _get(client, company["id"], again["id"])
    assert read["status"] == "ready", read
    assert read["review"]["postable"] is False, "the years are in the books already"
    assert read["review"]["archives"][0]["documents"] == 5
    assert read["may_post"] is True
    assert _post(client, company["id"], again["id"]).status_code == 202
    done = _get(client, company["id"], again["id"])
    assert done["status"] == "posted", done
    assert done["result"]["vouchers"] == 0
    assert done["result"]["archives"][0]["kept"] == 5

    # And once more: the same files at the same places are there already.
    third = _upload(client, company["id"], ("fiken-komplett-gard.zip", _export()))
    _post(client, company["id"], third["id"])
    result = _get(client, company["id"], third["id"])["result"]["archives"][0]
    assert (result["kept"], result["already_kept"]) == (0, 5)
    assert _documents(client, company["id"])["total_count"] == 5


def test_a_comparison_keeps_no_export(client: TestClient) -> None:
    company = _books(client)
    one = _zip(
        (f"saft-gard-{LAST_YEAR}.xml", unfinished_year_file()),
        ("Bilag/kvittering-17.pdf", KVITTERING),
    )
    uploaded = _upload(client, company["id"], ("fiken-komplett-gard.zip", one), compare=True)
    assert [f["kind"] for f in uploaded["files"]] == ["read"]


class _Flaky:
    """The document store, failing after ``works`` saves, as R2 does on a bad afternoon."""

    def __init__(self, store: Any, works: int) -> None:
        self._store = store
        self.works = works

    async def save(self, *, content: bytes, filename: str, content_type: str) -> str:
        if self.works <= 0:
            raise StorageError("R2 svarte 503")
        self.works -= 1
        key: str = await self._store.save(
            content=content, filename=filename, content_type=content_type
        )
        return key

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)


def test_documents_the_store_did_not_take_are_kept_on_the_next_post(client: TestClient) -> None:
    company = _books(client)
    storage = client.app.dependency_overrides[get_document_storage]()  # type: ignore[attr-defined]
    flaky = _Flaky(storage, works=2)
    client.app.dependency_overrides[get_document_storage] = lambda: flaky  # type: ignore[attr-defined]
    uploaded = _upload(client, company["id"], ("fiken-komplett-gard.zip", _export()))
    assert _post(client, company["id"], uploaded["id"]).status_code == 202
    stopped = _get(client, company["id"], uploaded["id"])
    assert stopped["status"] == "posted"
    assert stopped["error_code"] == "documents_not_kept"
    assert stopped["error"].startswith("Regnskapet er ført, men ikke alle dokumentene")
    # The books are in, the files wait for the rest, and the person may try again.
    assert stopped["result"]["vouchers"] > 0
    assert stopped["files_kept"] is True
    assert stopped["may_post"] is True
    assert _documents(client, company["id"])["total_count"] == 2

    flaky.works = 100
    assert _post(client, company["id"], uploaded["id"]).status_code == 202
    done = _get(client, company["id"], uploaded["id"])
    assert done["status"] == "posted", done
    assert done["error_code"] is None
    kept = done["result"]["archives"][0]
    assert (kept["kept"], kept["already_kept"]) == (3, 2)
    assert done["files_kept"] is False
    assert _documents(client, company["id"])["total_count"] == 5
    vouchers = client.get(
        f"/api/v1/companies/{company['id']}/bilag", params={"aar": LAST_YEAR, "journal": "IMPORT"}
    ).json()["total_count"]
    assert vouchers == stopped["result"]["years"][-1]["vouchers"], "the books went in once"


def test_a_restart_after_the_books_keeps_the_documents_and_posts_nothing(
    client: TestClient,
) -> None:
    """Stopped between the books' commit and the documents: the restart keeps only the documents."""
    company = _books(client)
    uploaded = _upload(client, company["id"], ("fiken-komplett-gard.zip", _export()))
    review = _get(client, company["id"], uploaded["id"])["review"]
    sessions = client.app.dependency_overrides[get_import_sessions]()  # type: ignore[attr-defined]
    storage = client.app.dependency_overrides[get_document_storage]()  # type: ignore[attr-defined]

    async def stopped_after_the_books() -> None:
        # What _post_books leaves in its own transaction, as a restart finds it.
        async with sessions() as db:
            await db.execute(
                update(SaftImport)
                .where(SaftImport.id == uploaded["id"])
                .values(status="posting", result=review, posted_at=utc_now())
            )
            await db.commit()
        await jobs.resume(sessions, storage, _store(client))

    asyncio.run(stopped_after_the_books())
    done = _get(client, company["id"], uploaded["id"])
    assert done["status"] == "posted", done
    assert done["result"]["archives"][0]["kept"] == 5
    imported = client.get(
        f"/api/v1/companies/{company['id']}/bilag", params={"aar": LAST_YEAR, "journal": "IMPORT"}
    ).json()["total_count"]
    assert imported == 0, "the books were not posted a second time"


def test_a_printout_sent_as_a_saft_file_is_told_where_it_goes(client: TestClient) -> None:
    company = _books(client)
    body = _refused(client, company["id"], "Bilag september.pdf", b"%PDF-1.7 utskrift")
    assert body["code"] == "printout_not_saft"
    assert "Vedlegg fra Tripletex" in body["detail"]
    assert _kept(client) == []


@pytest.mark.parametrize(
    ("name", "content", "says"),
    [
        pytest.param("konto.xml", CAMT, "kontoutskrift", id="a bank statement"),
        pytest.param("faktura.xml", EHF, "EHF-faktura", id="an invoice"),
        pytest.param("notat.txt", b"Saldobalanse 2025", "ikke en SAF-T-fil", id="not XML"),
        pytest.param(
            "fiken-regnskap-gard-2025.xlsx",
            _zip(("[Content_Types].xml", b"<Types/>"), ("xl/workbook.xml", b"<workbook/>")),
            "regneark",
            id="a spreadsheet",
        ),
        pytest.param(
            "fiken-bilag-gard.zip",
            _zip(("Bilag/1.pdf", b"%PDF-1.7"), ("Bilag/2.jpg", b"jpeg")),
            "Komplett eksport",
            id="Fiken's documents alone",
        ),
    ],
)
def test_what_is_not_a_saft_file_is_told_what_it_is(
    client: TestClient, name: str, content: bytes, says: str
) -> None:
    company = _books(client)
    body = _refused(client, company["id"], name, content)
    assert body["code"] == "invalid_saft_file"
    assert says in body["detail"]
    assert _kept(client) == []


def test_a_file_delivered_in_parts_is_refused_before_it_is_kept(client: TestClient) -> None:
    company = _books(client)
    parts = _zip(("saft_1_2.xml", earlier_file()), ("saft_2_2.xml", earlier_file()))
    body = _refused(client, company["id"], "saft.zip", parts)
    assert "delt i 2 deler" in body["detail"]
    assert _kept(client) == []


def test_more_years_than_a_move_takes_are_counted_before_anything_is_unpacked(
    client: TestClient,
) -> None:
    company = _books(client)
    many = _zip(*[(f"saft-{index:02d}.xml", earlier_file()) for index in range(11)])
    body = _refused(client, company["id"], "mange.zip", many)
    assert "høyst 10" in body["detail"]
    assert _kept(client) == []


def test_the_one_file_routes_tell_the_years_of_a_complete_export_apart(
    client: TestClient,
) -> None:
    """``…/saft/bilag/kontroll`` reads one year: the export's two are named, not miscounted."""
    company = _books(client)
    export = _zip(
        (f"saft-gard-{EARLIER}.xml", earlier_file()),
        (f"saft-gard-{LAST_YEAR}.xml", year_file()),
        ("faktura-12.xml", EHF),
    )
    url = f"/api/v1/companies/{company['id']}/saft/bilag/kontroll"
    several = write(client, "POST", url, files={"file": ("fiken-komplett.zip", export)})
    assert several.status_code == 422, several.text
    assert "har 2 SAF-T-filer" in several.json()["detail"]
    printout = write(client, "POST", url, files={"file": ("Bilag.pdf", b"%PDF-1.7")})
    assert printout.json()["code"] == "printout_not_saft"
    # One SAF-T file among documents is read as the one it is.
    one = _zip((f"saft-gard-{LAST_YEAR}.xml", year_file()), ("faktura-12.xml", EHF))
    read = write(client, "POST", url, files={"file": ("saft.zip", one)})
    assert read.status_code == 200, read.text


def _age(client: TestClient, import_id: int, days: int) -> None:
    """Make an import and its stored files ``days`` old."""
    sessions = client.app.dependency_overrides[get_import_sessions]()  # type: ignore[attr-defined]

    async def backdate() -> None:
        async with sessions() as db:
            await db.execute(
                update(SaftImport)
                .where(SaftImport.id == import_id)
                .values(created_at=utc_now() - timedelta(days=days))
            )
            await db.commit()

    asyncio.run(backdate())
    then = time.time() - days * 86400
    for item in asyncio.run(_store(client).listing()):
        os.utime(_store(client)._path(item.key), (then, then))


def _sweep(client: TestClient) -> int:
    sessions = client.app.dependency_overrides[get_import_sessions]()  # type: ignore[attr-defined]
    return asyncio.run(jobs.sweep(sessions, _store(client)))


def test_the_files_go_fourteen_days_after_the_upload(client: TestClient) -> None:
    company = _books(client)
    uploaded = _upload(client, company["id"], (f"{EARLIER}.xml", earlier_file()))
    assert _get(client, company["id"], uploaded["id"])["files_kept"] is True
    _age(client, uploaded["id"], 13)
    assert _sweep(client) == 0
    assert len(_kept(client)) == 1

    # Nobody looks at it again, and the daily sweep lets go of it all the same.
    _age(client, uploaded["id"], 15)
    assert _sweep(client) == 1
    assert _kept(client) == []
    assert _get(client, company["id"], uploaded["id"])["files_kept"] is False
    refused = _post(client, company["id"], uploaded["id"])
    assert refused.status_code == 422


def test_a_file_no_import_names_is_deleted_once_it_is_an_hour_old(client: TestClient) -> None:
    """An upload whose client left halfway, or a delete that failed."""
    store = _store(client)

    async def orphan() -> str:
        async def piece() -> AsyncIterator[bytes]:
            yield b"halvveis"

        return (await store.save(piece())).key

    key = asyncio.run(orphan())
    # Younger than an hour, it may be an upload whose row is not committed yet.
    assert _sweep(client) == 0
    then = time.time() - 2 * 3600
    os.utime(store._path(key), (then, then))
    assert _sweep(client) == 1
    assert asyncio.run(store.listing()) == []


def test_throwing_an_import_away_deletes_its_files(client: TestClient) -> None:
    company = _books(client)
    uploaded = _upload(client, company["id"], (f"{EARLIER}.xml", earlier_file()))
    assert len(_kept(client)) == 1
    url = f"/api/v1/companies/{company['id']}/saft/innlesing/{uploaded['id']}"
    assert write(client, "DELETE", url).status_code == 204
    assert _kept(client) == []
