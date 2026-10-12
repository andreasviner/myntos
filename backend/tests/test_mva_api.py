"""The MVA-melding over HTTP: a period from real vouchers to a stored receipt.

Skatteetaten and Altinn are stubbed at the same seam the integration tests use
(the module-level ``_client`` functions), and the ID-porten login is replaced by
a token, so everything between the ledger and the wire runs for real: the
melding is built from vouchers the register posted, sent, settled, and its
receipt is stored through the same storage the bilag archive uses.

What these assert is mostly what a person cannot see going wrong. That the
melding sent is byte for byte the one that was checked. That a ledger that moved
after the check stops the submission. That a failure halfway through Altinn is
recorded rather than rolled back with the request. That the settlement moves
exactly the period's VAT, and that a correction moves only the difference.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from myntos.api.v1 import mva as mva_api
from myntos.core.clock import utc_now
from myntos.db.session import get_session
from myntos.domain.ledger.errors import ClosedFinancialYearError
from myntos.domain.ledger.models import Account, Journal, Transaction, TransactionLine
from myntos.domain.mva import service as mva_service
from myntos.domain.mva.codes import Meldingskategori
from myntos.domain.mva.periods import PeriodType, Termin
from myntos.integrations.altinn import instances as instances_module
from myntos.integrations.altinn.instances import AppInstanceClient
from myntos.integrations.altinn.tokens import AltinnToken
from myntos.integrations.skatteetaten import mva as mva_module
from myntos.integrations.skatteetaten.mva import METADATA_DATA_TYPE, MvaMeldingClient
from tests.conftest import join_company, register, write

FIXTURES = Path(__file__).parent / "fixtures" / "mva"
LAST_YEAR = utc_now().year - 1
ORGNR = "910000004"
VALIDATION_URL = "https://idporten-api-sbstest.sits.no"
APP_URL = "https://skd.apps.tt02.altinn.no/skd/mva-melding-innsending-v1"
PARTY_ID = 50001337
INSTANCE_GUID = "6c9e1b1a-3a3c-4a1e-9b3f-2f0a7d5c8e11"
INSTANCE_ID = f"{PARTY_ID}/{INSTANCE_GUID}"
RECEIPT = b"%PDF-1.7 kvittering"

CLEAN = (FIXTURES / "valideringsresultat.xml").read_bytes()
REFUSED = (
    b'<valideringsresultat xmlns="no:skatteetaten:fastsetting:avgift:mva:valideringsresultat:v1">'
    b"<avvikVedMeldingslevering>ugyldig skattemelding</avvikVedMeldingslevering>"
    b"<avvik><stiTilAvvik>//mvaSpesifikasjonslinje[1]</stiTilAvvik><avviksinformasjon>"
    b"<begrunnelse>Feil sats</begrunnelse><avvikstype>ugyldig skattemelding</avvikstype>"
    b"<avvikKode>X1</avvikKode><regelDefinisjon>sats</regelDefinisjon></avviksinformasjon>"
    b"</avvik></valideringsresultat>"
)


@dataclass
class Skatteetaten:
    """The validation endpoint and the MVA app, in one stub."""

    validation_answer: bytes = CLEAN
    validated: list[bytes] = field(default_factory=list)
    calls: list[tuple[str, str]] = field(default_factory=list)
    envelope: bytes | None = None
    melding: bytes | None = None
    process_calls: int = 0
    # Make the n-th process/next answer 409, as the app does when it refuses.
    refuse_process_at: int | None = None
    # Feedback documents appear once the second process/next has run.
    feedback: bool = True

    def elements(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = [
            {"id": "envelope", "dataType": METADATA_DATA_TYPE, "contentType": "application/xml"}
        ]
        if self.melding is not None:
            found.append({"id": "melding", "dataType": "mvamelding", "contentType": "text/xml"})
        if self.feedback and self.process_calls >= 2:
            found += [
                {
                    "id": "fb-1",
                    "dataType": "valideringsresultat",
                    "contentType": "text/xml",
                    "filename": "valideringsresultat.xml",
                },
                {
                    "id": "fb-2",
                    "dataType": "betalingsinformasjon",
                    "contentType": "text/xml",
                    "filename": "betalingsinformasjon.xml",
                },
                {
                    "id": "fb-3",
                    "dataType": "kvittering",
                    "contentType": "application/pdf",
                    "filename": "kvittering.pdf",
                },
            ]
        return found

    def instance(self) -> dict[str, Any]:
        ended = self.process_calls >= 2
        return {
            "id": INSTANCE_ID,
            "data": self.elements(),
            "process": (
                {"ended": datetime.now(UTC).isoformat()}
                if ended
                else {"currentTask": {"elementId": "Task_1"}}
            ),
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if path.endswith("/mva-melding/valider"):
            self.validated.append(request.content)
            return httpx.Response(200, content=self.validation_answer)
        if request.method == "POST" and path.endswith("/instances"):
            return httpx.Response(201, json=self.instance())
        if request.method == "PUT" and path.endswith("/data/envelope"):
            self.envelope = request.content
            return httpx.Response(200, json=self.elements()[0])
        if request.method == "POST" and path.endswith("/data"):
            self.melding = request.content
            return httpx.Response(201, json=self.elements()[1])
        if request.method == "PUT" and path.endswith("/process/next"):
            self.process_calls += 1
            if self.refuse_process_at == self.process_calls:
                return httpx.Response(409, text="Valideringsfeil: Ugyldig mva-melding")
            return httpx.Response(200, json=self.instance())
        if request.method == "GET" and path.endswith(f"/instances/{INSTANCE_ID}"):
            return httpx.Response(200, json=self.instance())
        if request.method == "GET" and "/data/fb-" in path:
            documents = {
                "fb-1": CLEAN,
                "fb-2": (FIXTURES / "betalingsinformasjon.xml").read_bytes(),
                "fb-3": RECEIPT,
            }
            return httpx.Response(200, content=documents[path.rsplit("/", 1)[-1]])
        raise AssertionError(f"unexpected {request.method} {request.url}")


@pytest.fixture
def skatteetaten(monkeypatch: pytest.MonkeyPatch) -> Iterator[Skatteetaten]:
    stub = Skatteetaten()

    def stub_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(stub))

    monkeypatch.setattr(mva_module, "_client", stub_client)
    monkeypatch.setattr(instances_module, "_client", stub_client)
    monkeypatch.setattr(
        mva_module,
        "build_client",
        lambda settings=None: MvaMeldingClient(
            validation_base_url=VALIDATION_URL, app=AppInstanceClient(APP_URL)
        ),
    )

    async def logged_in(db: Any, user: Any, *, missing: str) -> tuple[None, str]:
        return None, "person-token"

    async def exchanged(token: str) -> AltinnToken:
        assert token == "person-token"
        return AltinnToken(value="altinn-token", usable_until=utc_now() + timedelta(minutes=5))

    monkeypatch.setattr(mva_api, "government_login_for", logged_in)
    monkeypatch.setattr(mva_api, "exchange_id_porten_token", exchanged)
    yield stub


# --- Books to file from -----------------------------------------------------------


def books(client: TestClient, *, vat_registered: bool = True) -> dict[str, Any]:
    session = register(client)
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": session["memberships"][0]["organization_id"],
            "orgnr": ORGNR,
            "name": "Fjordferje AS",
            "first_financial_year": LAST_YEAR,
            "vat_registered": vat_registered,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def book(
    client: TestClient,
    company_id: int,
    type_code: str,
    gross: str,
    *,
    on: date | None = None,
    tax_code: str | None = None,
) -> dict[str, Any]:
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/cashflow",
        json={
            "type_code": type_code,
            "occurred_on": (on or date(LAST_YEAR, 2, 10)).isoformat(),
            "gross_amount": gross,
            "description": type_code,
            "tax_code": tax_code,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def first_period(client: TestClient, company_id: int) -> dict[str, Any]:
    response = client.get(
        f"/api/v1/companies/{company_id}/mva/termin", params={"aar": LAST_YEAR, "termin": 1}
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def check(client: TestClient, company_id: int, **extra: Any) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/mva/termin/kontroll",
        json={"aar": LAST_YEAR, "termin": 1, **extra},
    )


def send(client: TestClient, company_id: int, filing: dict[str, Any], **override: Any) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/mva/innsendinger/{filing['id']}/send",
        json={
            "fastsatt_merverdiavgift": filing["fastsatt_merverdiavgift"],
            "bekreftet": True,
            **override,
        },
    )


def settlement_lines(client: TestClient, company_id: int) -> list[tuple[str, str, str]]:
    """Every line in the MVA journal, read straight from the database."""
    override = client.app.dependency_overrides[get_session]  # type: ignore[attr-defined]

    async def read() -> list[tuple[str, str, str]]:
        sessions = override()
        session = await sessions.__anext__()
        rows = await session.execute(
            select(Account.account_number, TransactionLine.debit, TransactionLine.credit)
            .join(TransactionLine, TransactionLine.account_id == Account.id)
            .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
            .join(Journal, Journal.id == Transaction.journal_id)
            .where(Journal.company_id == company_id, Journal.journal_id == "MVA")
            .order_by(Transaction.id, TransactionLine.record_id)
        )
        found = [(str(n), str(d), str(c)) for n, d, c in rows]
        await sessions.aclose()
        return found

    portal = client.portal
    assert portal is not None
    return portal.call(read)


# --- Reading a period -------------------------------------------------------------


def test_the_overview_shows_every_period_with_what_the_ledger_says(client: TestClient) -> None:
    company = books(client)
    book(client, company["id"], "salg_hoy_sats", "12500.00")
    book(client, company["id"], "varekjop", "1250.00")
    # In the third period, so it must not reach the first.
    book(client, company["id"], "salg_hoy_sats", "1000.00", on=date(LAST_YEAR, 5, 2))

    overview = client.get(f"/api/v1/companies/{company['id']}/mva", params={"aar": LAST_YEAR})
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["settings"]["period_type"] == "toMaaneder"
    assert [row["name"] for row in body["periods"]][:2] == ["januar-februar", "mars-april"]
    first = body["periods"][0]
    assert first["fastsatt_merverdiavgift"] == "2250.00"
    assert first["is_over"] is True
    assert first["status"] is None
    assert body["periods"][2]["fastsatt_merverdiavgift"] == "200.00"


def test_the_period_shows_every_line_and_agrees_with_the_vat_accounts(client: TestClient) -> None:
    company = books(client)
    book(client, company["id"], "salg_hoy_sats", "12500.00")
    book(client, company["id"], "salg_lav_sats", "1120.00")
    book(client, company["id"], "varekjop", "1250.00")
    period = first_period(client, company["id"])

    lines = [(ln["mva_kode"], ln["grunnlag"], ln["merverdiavgift"]) for ln in period["lines"]]
    assert lines == [
        ("3", "10000.00", "2500.00"),
        # 33, not 32: the 12 % code, now that 32 is fish.
        ("33", "1000.00", "120.00"),
        ("1", None, "-250.00"),
    ]
    assert period["output_vat"] == "2620.00"
    assert period["input_vat"] == "250.00"
    assert period["fastsatt_merverdiavgift"] == "2370.00"
    assert period["reconciliation"]["difference"] == "0.00"
    assert period["blockers"] == []
    # The test settings configure no MVA app, and the page is told so.
    assert period["submission_available"] is False
    expected = Termin(PeriodType.TO_MAANEDER, LAST_YEAR, 1).deadline(Meldingskategori.ALMINNELIG)
    assert period["deadline"] == expected.isoformat()
    assert expected >= date(LAST_YEAR, 4, 10)


def test_border_vat_on_a_tollpass_is_a_deduction_and_stays_in_the_register(
    client: TestClient,
) -> None:
    """Code 14: the whole bill is VAT, booked to 2710, and shown as a payment."""
    company = books(client)
    entry = book(client, company["id"], "mva_innforsel", "2500.00")
    assert entry["account_number"] == "2710"
    assert entry["vat_amount"] == "2500.00"
    assert entry["net_amount"] == "0.00"
    listed = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    assert entry["id"] in {item["id"] for item in listed}

    period = first_period(client, company["id"])
    assert [(ln["mva_kode"], ln["merverdiavgift"]) for ln in period["lines"]] == [
        ("14", "-2500.00")
    ]
    assert period["reconciliation"]["difference"] == "0.00"


def test_an_import_is_reported_on_both_sides(client: TestClient) -> None:
    company = books(client)
    book(client, company["id"], "datakostnad", "1000.00", tax_code="86")
    period = first_period(client, company["id"])
    assert [(ln["mva_kode"], ln["side"], ln["merverdiavgift"]) for ln in period["lines"]] == [
        ("86", "utgaaende", "250.00"),
        ("86", "inngaaende", "-250.00"),
    ]
    assert period["fastsatt_merverdiavgift"] == "0.00"


def test_an_unregistered_company_is_told_why_it_files_nothing(
    client: TestClient, skatteetaten: Skatteetaten
) -> None:
    company = books(client, vat_registered=False)
    period = first_period(client, company["id"])
    assert [b["code"] for b in period["blockers"]] == ["not_vat_registered"]
    refused = check(client, company["id"])
    assert refused.status_code == 409
    assert refused.json()["code"] == "not_vat_registered"
    assert skatteetaten.validated == []


def test_a_period_still_running_cannot_be_filed(
    client: TestClient, skatteetaten: Skatteetaten
) -> None:
    company = books(client)
    this_year = utc_now().year
    last_index = 6
    response = client.get(
        f"/api/v1/companies/{company['id']}/mva/termin",
        params={"aar": this_year, "termin": last_index},
    )
    assert response.status_code == 200, response.text
    assert "period_not_over" in [b["code"] for b in response.json()["blockers"]]
    refused = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/mva/termin/kontroll",
        json={"aar": this_year, "termin": last_index},
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "period_not_over"


def test_the_melding_can_be_downloaded_for_upload_by_hand(client: TestClient) -> None:
    company = books(client)
    book(client, company["id"], "salg_hoy_sats", "12500.00")
    response = client.get(
        f"/api/v1/companies/{company['id']}/mva/termin/xml",
        params={"aar": LAST_YEAR, "termin": 1},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert b"<mvaKode>3</mvaKode>" in response.content
    assert f"<organisasjonsnummer>{ORGNR}</organisasjonsnummer>".encode() in response.content


# --- Checking and filing ----------------------------------------------------------


def test_a_period_is_checked_filed_settled_and_its_receipt_kept(
    client: TestClient, skatteetaten: Skatteetaten
) -> None:
    company = books(client)
    book(client, company["id"], "salg_hoy_sats", "12500.00")
    book(client, company["id"], "varekjop", "1250.00")
    assert first_period(client, company["id"])["submission_available"] is True

    checked = check(client, company["id"], merknad="Første melding")
    assert checked.status_code == 200, checked.text
    filing = checked.json()
    assert filing["status"] == "validert"
    assert filing["validation_blocks"] is False
    assert filing["fastsatt_merverdiavgift"] == "2250.00"
    assert len(skatteetaten.validated) == 1
    assert b"<merknad><beskrivelse>F" in skatteetaten.validated[0]

    sent = send(client, company["id"], filing)
    assert sent.status_code == 200, sent.text
    filed = sent.json()
    assert filed["status"] == "mottatt"
    assert filed["altinn_instance_id"] == INSTANCE_ID
    assert filed["submitted_by_name"] == "Kari Nordmann"

    # Exactly the bytes that were checked, not a fresh build of the same thing.
    assert skatteetaten.melding == skatteetaten.validated[0]
    assert skatteetaten.envelope is not None
    assert f"<organisasjonsnummer>{ORGNR}</organisasjonsnummer>".encode() in skatteetaten.envelope
    assert b"januar-februar" in skatteetaten.envelope
    assert skatteetaten.process_calls == 2

    assert filed["payment_kid"] == "99999999999999"
    assert {d["data_type"] for d in filed["documents"]} == {
        "valideringsresultat",
        "betalingsinformasjon",
        "kvittering",
    }
    receipt = client.get(
        f"/api/v1/companies/{company['id']}/mva/innsendinger/{filed['id']}/dokument",
        params={"type": "kvittering"},
    )
    assert receipt.status_code == 200
    assert receipt.content == RECEIPT

    # 2500 of output and 250 of input moved onto 2740 as 2250 owed.
    assert settlement_lines(client, company["id"]) == [
        ("2700", "2500.00", "0.00"),
        ("2710", "0.00", "250.00"),
        ("2740", "0.00", "2250.00"),
    ]
    # And the year's figures still say what the year's VAT was.
    summary = client.get(
        f"/api/v1/companies/{company['id']}/cashflow/summary",
        params={"financial_year": LAST_YEAR},
    ).json()
    assert summary["vat_position"] == "2250.00"

    overview = client.get(f"/api/v1/companies/{company['id']}/mva", params={"aar": LAST_YEAR})
    first = overview.json()["periods"][0]
    assert first["status"] == "mottatt"
    assert first["changed_since_filed"] is False


def test_a_ledger_that_moved_after_the_check_is_not_filed(
    client: TestClient, skatteetaten: Skatteetaten
) -> None:
    company = books(client)
    book(client, company["id"], "salg_hoy_sats", "12500.00")
    filing = check(client, company["id"]).json()

    book(client, company["id"], "varekjop", "1250.00")
    # The page knows before the button is pressed.
    (shown,) = first_period(client, company["id"])["filings"]
    assert shown["matches_ledger"] is False
    refused = send(client, company["id"], filing)
    assert refused.status_code == 409
    assert refused.json()["code"] == "melding_changed"
    assert skatteetaten.process_calls == 0


def test_the_confirmation_must_name_the_checked_figure(
    client: TestClient, skatteetaten: Skatteetaten
) -> None:
    company = books(client)
    book(client, company["id"], "salg_hoy_sats", "12500.00")
    filing = check(client, company["id"]).json()

    wrong = send(client, company["id"], filing, fastsatt_merverdiavgift="2499.00")
    assert wrong.status_code == 422
    assert wrong.json()["code"] == "confirmation_required"
    unconfirmed = send(client, company["id"], filing, bekreftet=False)
    assert unconfirmed.status_code == 422
    assert skatteetaten.calls == [("POST", "/api/mva/grensesnittstoette/mva-melding/valider")]


def test_a_melding_skatteetaten_would_refuse_is_kept_and_not_sendable(
    client: TestClient, skatteetaten: Skatteetaten
) -> None:
    skatteetaten.validation_answer = REFUSED
    company = books(client)
    book(client, company["id"], "salg_hoy_sats", "12500.00")
    checked = check(client, company["id"])
    assert checked.status_code == 200
    filing = checked.json()
    assert filing["status"] == "avvist"
    assert filing["validation_blocks"] is True
    assert filing["findings"][0]["melding"] == "Feil sats"

    refused = send(client, company["id"], filing)
    assert refused.status_code == 409
    assert refused.json()["code"] == "filing_not_submittable"


def test_a_submission_that_stops_halfway_is_recorded_not_rolled_back(
    client: TestClient, skatteetaten: Skatteetaten
) -> None:
    skatteetaten.refuse_process_at = 1
    company = books(client)
    book(client, company["id"], "salg_hoy_sats", "12500.00")
    filing = check(client, company["id"]).json()

    failed = send(client, company["id"], filing)
    assert failed.status_code == 502
    assert failed.json()["code"] == "submission_failed"

    stored = client.get(f"/api/v1/companies/{company['id']}/mva/innsendinger/{filing['id']}")
    body = stored.json()
    assert body["status"] == "feilet"
    # The instance may exist in Altinn now, and this is how anyone finds it.
    assert body["altinn_instance_id"] == INSTANCE_ID
    assert "409" in body["error"]
    # Nothing settled: the melding was not filed.
    assert settlement_lines(client, company["id"]) == []


def test_a_filed_melding_stays_filed_when_the_settlement_cannot_be_posted(
    client: TestClient, skatteetaten: Skatteetaten, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A closed year refuses the settlement voucher after Skatteetaten has the melding.

    The request must not roll back the row that says the melding was filed: a
    filed melding Myntos believes unfiled is one it offers to file twice.
    """

    async def closed(*args: Any, **kwargs: Any) -> None:
        raise ClosedFinancialYearError(f"regnskapsåret {LAST_YEAR} er avsluttet")

    monkeypatch.setattr(mva_service, "post_settlement", closed)
    company = books(client)
    book(client, company["id"], "salg_hoy_sats", "12500.00")
    filing = check(client, company["id"]).json()

    sent = send(client, company["id"], filing)
    assert sent.status_code == 200, sent.text
    stored = client.get(f"/api/v1/companies/{company['id']}/mva/innsendinger/{filing['id']}")
    body = stored.json()
    assert body["status"] == "mottatt"
    assert body["submitted_at"] is not None
    assert "oppgjøret mot 2740" in body["error"]
    # And it cannot be sent a second time.
    again = send(client, company["id"], filing)
    assert again.status_code == 409


def test_a_receipt_that_arrives_later_is_fetched_later(
    client: TestClient, skatteetaten: Skatteetaten
) -> None:
    skatteetaten.feedback = False
    company = books(client)
    book(client, company["id"], "salg_hoy_sats", "12500.00")
    filing = check(client, company["id"]).json()
    filed = send(client, company["id"], filing).json()
    assert filed["status"] == "sendt"
    assert filed["documents"] == []

    skatteetaten.feedback = True
    fetched = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/mva/innsendinger/{filing['id']}/tilbakemelding",
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["status"] == "mottatt"
    assert len(fetched.json()["documents"]) == 3


def test_a_correction_settles_only_the_difference(
    client: TestClient, skatteetaten: Skatteetaten
) -> None:
    """Filed, then a voucher in the period reversed, then filed again."""
    company = books(client)
    book(client, company["id"], "salg_hoy_sats", "12500.00")
    purchase = book(client, company["id"], "varekjop", "1250.00")
    first = check(client, company["id"]).json()
    send(client, company["id"], first)

    reversed_ = write(
        client, "POST", f"/api/v1/companies/{company['id']}/cashflow/{purchase['id']}/reversal"
    )
    assert reversed_.status_code in (200, 201), reversed_.text
    overview = client.get(f"/api/v1/companies/{company['id']}/mva", params={"aar": LAST_YEAR})
    assert overview.json()["periods"][0]["changed_since_filed"] is True

    skatteetaten.process_calls = 0
    skatteetaten.melding = None
    second = check(client, company["id"]).json()
    assert second["fastsatt_merverdiavgift"] == "2500.00"
    assert send(client, company["id"], second).status_code == 200

    lines = settlement_lines(client, company["id"])
    # The first settlement, then one moving the deduction that was taken back.
    assert lines[3:] == [("2710", "250.00", "0.00"), ("2740", "0.00", "250.00")]


# --- Settings ---------------------------------------------------------------------


def test_the_period_length_is_a_setting_and_changes_the_periods(client: TestClient) -> None:
    company = books(client)
    changed = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/mva/innstillinger",
        json={"period_type": "maaned"},
    )
    assert changed.status_code == 200, changed.text
    overview = client.get(f"/api/v1/companies/{company['id']}/mva", params={"aar": LAST_YEAR})
    assert len(overview.json()["periods"]) == 12
    assert overview.json()["periods"][0]["name"] == "januar"

    refused = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/mva/innstillinger",
        json={"category": "kompensasjon"},
    )
    assert refused.status_code == 422


def test_only_someone_who_manages_the_company_changes_the_settings(client: TestClient) -> None:
    company = books(client)
    join_company(client, company["id"], email="regnskap@example.no", role="member")
    refused = write(
        client,
        "PATCH",
        f"/api/v1/companies/{company['id']}/mva/innstillinger",
        json={"period_type": "aar"},
    )
    assert refused.status_code == 403
    # Reading is ordinary ledger access.
    assert client.get(f"/api/v1/companies/{company['id']}/mva").status_code == 200
