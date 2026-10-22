"""The skattemelding over HTTP: a closed year from real vouchers to Skatteetaten's answer.

Real postings through the API (an opening balance, a year of sales and costs,
the year closed with its tax), real SQLite, and Skatteetaten and Altinn stubbed
at the module-level ``_client`` seams the integration tests use, with the
ID-porten login replaced by a token, as in ``test_mva_api``. So everything
between the ledger and the wire runs: the SAF-T snapshot the figures come
from, the næringsspesifikasjon written against the year's XSD, the utkast
completed, the envelope checked and then sent byte for byte.

What the tests assert is mostly what nobody would see going wrong: that the
envelope uploaded is the one that was checked; that books which moved after
the check stop the submission; that a company with a revisor gets an instance
with the revisor's step, and stops at it; and that nothing is checked while
the review has a feil.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from lxml import etree

from myntos.api.v1 import approvals as approvals_api
from myntos.api.v1 import skattemelding as skattemelding_api
from myntos.core.clock import utc_now
from myntos.integrations.altinn import instances as instances_module
from myntos.integrations.altinn.instances import AppInstanceClient
from myntos.integrations.altinn.tokens import AltinnToken
from myntos.integrations.skatteetaten import client as client_module
from myntos.integrations.skatteetaten.client import SkattemeldingClient
from myntos.skattemelding import forhaandsfastsetting, schema, service
from tests import test_winding_up as winding_up_tests
from tests.conftest import agent_header, register, write
from tests.test_arsoppgjor_api import _company, _line, _post_opening
from tests.test_cashflow_api import THIS_YEAR, record

LAST_YEAR = THIS_YEAR - 1
ORGNR = "910000004"
PARTSNUMMER = 3001570199
API_URL = "https://api-test.sits.no"
IDPORTEN_URL = "https://idporten-api-test.sits.no"
APP_URL = "https://skd.apps.tt02.altinn.no/skd/formueinntekt-skattemelding-v2"
PARTY_ID = 50001337
INSTANCE_GUID = "1d6c3f0e-7b2a-4c55-9a8e-0f4b6c2d9e71"
INSTANCE_ID = f"{PARTY_ID}/{INSTANCE_GUID}"
FIXTURES = Path(__file__).parent / "fixtures" / "skattemelding"
ACCEPTED = (FIXTURES / "tt02-validering-314067630-2025.xml").read_bytes()
ANSWER = (FIXTURES / "tt02-tilbakemelding-314067630-2025.xml").read_bytes()

pytestmark = pytest.mark.skipif(
    LAST_YEAR not in service.supported_years(),
    reason="Myntos has Skatteetaten's schemas for some years only, and the tests file last year",
)

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)


def _refused() -> bytes:
    namespace = schema.load(schema.RESPONSE).namespace
    return (
        f'<skattemeldingOgNaeringsspesifikasjonResponse xmlns="{namespace}">'
        "<resultatAvValidering>validertMedFeil</resultatAvValidering>"
        "<aarsakTilValidertMedFeil>avvikNaeringsopplysninger</aarsakTilValidertMedFeil>"
        "<veiledningEtterKontroll><veiledning><veiledningstype>N_FEIL</veiledningstype>"
        "<hjelpetekst>Noe stemmer ikke</hjelpetekst>"
        "<betjeningsstrategi>faktiskFeil</betjeningsstrategi></veiledning>"
        "</veiledningEtterKontroll></skattemeldingOgNaeringsspesifikasjonResponse>"
    ).encode()


def _utkast(year: int, *, forhaandsfastsetting: str | None = None) -> bytes:
    """The fetch's answer: an AS's empty utkast for ``year``, as TT02 serves one.

    With ``forhaandsfastsetting``, the one TT02 served after
    ``klargjoerforhaandsfastsetting`` on 2026-10-22, which carries the format.
    """
    namespace = schema.skattemelding_upersonlig(year).namespace
    marker = (
        "<gjelderForhaandsfastsetting><innsendingsformat><forhaandsfastsettingsformattype>"
        f"{forhaandsfastsetting}</forhaandsfastsettingsformattype></innsendingsformat>"
        "</gjelderForhaandsfastsetting>"
        if forhaandsfastsetting is not None
        else ""
    )
    melding = (
        f'<?xml version="1.0" encoding="UTF-8"?><skattemelding xmlns="{namespace}">'
        f"<partsnummer>{PARTSNUMMER}</partsnummer><inntektsaar>{year}</inntektsaar>"
        f"{marker}</skattemelding>"
    ).encode()
    return (
        "<skattemeldingOgNaeringsspesifikasjonforespoerselResponse "
        'xmlns="no:skatteetaten:fastsetting:formueinntekt:skattemeldingognaeringsspesifikasjon:'
        'forespoersel:response:v2"><dokumenter><skattemeldingdokument>'
        "<id>SKI:755:1</id><encoding>utf-8</encoding>"
        f"<content>{base64.b64encode(melding).decode()}</content>"
        "<type>skattemeldingUpersonligUtkast</type>"
        "</skattemeldingdokument></dokumenter>"
        "</skattemeldingOgNaeringsspesifikasjonforespoerselResponse>"
    ).encode()


@dataclass
class Skatteetaten:
    """Skatteetaten's skattemelding API and the Altinn app, in one stub."""

    validation_answer: bytes = ACCEPTED
    fetched: list[str] = field(default_factory=list)
    validated: list[bytes] = field(default_factory=list)
    created: dict[str, Any] | None = None
    uploads: list[tuple[str | None, str | None, bytes]] = field(default_factory=list)
    process_calls: int = 0
    # Skatteetaten has processed the melding once the process has ended.
    answers: bool = True
    refuse_upload: bool = False
    # A wound-up company's year before the ordinary assessment: no melding
    # (403) until klargjoerforhaandsfastsetting, which answers this status; then
    # an utkast in this format, unless ``never_ready``.
    prepare_answer: str | None = None
    prepare_format: str = "aaretsSkattemelding"
    never_ready: bool = False
    prepared: int = 0

    @property
    def revisor(self) -> bool:
        values = (self.created or {}).get("dataValues", {})
        return bool(values.get("skalBekreftesAvRevisor") == "true")

    def instance(self) -> dict[str, Any]:
        ended = self.process_calls >= 2
        data: list[dict[str, Any]] = []
        if self.uploads:
            data.append({"id": "envelope", "dataType": "skattemeldingOgNaeringsspesifikasjon"})
        body: dict[str, Any] = {"id": INSTANCE_ID, "data": data}
        if ended:
            body["process"] = {"ended": utc_now().isoformat()}
            if self.answers:
                data.append(
                    {
                        "id": "fb-1",
                        "dataType": "tilbakemelding",
                        "contentType": "application/xml",
                        "filename": "tilbakemelding.xml",
                    }
                )
                body["status"] = {
                    "isArchived": True,
                    "substatus": {"label": "Godkjent", "description": "Skattemeldingen er mottatt"},
                }
        elif self.revisor and self.process_calls == 1:
            body["process"] = {"currentTask": {"elementId": "Task_2Revisor"}}
        else:
            body["process"] = {"currentTask": {"elementId": "Task_1"}}
        return body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/valider/" in path:
            self.validated.append(request.content)
            return httpx.Response(200, content=self.validation_answer)
        if "/klargjoerforhaandsfastsetting/" in path:
            assert request.method == "POST"
            assert request.headers["authorization"] == "Bearer person-token"
            self.prepared += 1
            answer = self.prepare_answer or "OK"
            return httpx.Response(
                200 if answer == "OK" else 400,
                json={"status": answer, "korrelasjonsId": "c0ffee"},
            )
        if path.startswith("/api/skattemelding/v2/"):
            self.fetched.append(f"{request.url.host}{path}")
            year = int(path.rstrip("/").split("/")[-2])
            if self.prepare_answer is not None:
                if not self.prepared or self.never_ready or self.prepare_answer != "OK":
                    return httpx.Response(403, text="Skatteplikt ikke registrert")
                return httpx.Response(
                    200, content=_utkast(year, forhaandsfastsetting=self.prepare_format)
                )
            return httpx.Response(200, content=_utkast(year))
        if request.method == "POST" and path.endswith("/instances"):
            self.created = json.loads(request.content)
            return httpx.Response(201, json=self.instance())
        if request.method == "POST" and path.endswith("/data"):
            if self.refuse_upload:
                return httpx.Response(400, text="Ugyldig innhold")
            self.uploads.append(
                (
                    request.headers.get("content-type"),
                    request.headers.get("content-disposition"),
                    request.content,
                )
            )
            return httpx.Response(
                201, json={"id": "envelope", "dataType": "skattemeldingOgNaeringsspesifikasjon"}
            )
        if request.method == "PUT" and path.endswith("/process/next"):
            self.process_calls += 1
            return httpx.Response(200, json=self.instance())
        if request.method == "GET" and path.endswith(f"/instances/{INSTANCE_ID}"):
            return httpx.Response(200, json=self.instance())
        if request.method == "GET" and path.endswith("/data/fb-1"):
            return httpx.Response(200, content=ANSWER)
        raise AssertionError(f"unexpected {request.method} {request.url}")


@pytest.fixture
def skatteetaten(monkeypatch: pytest.MonkeyPatch) -> Iterator[Skatteetaten]:
    stub = Skatteetaten()

    def stub_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(stub))

    class NoMachine:
        async def get_access_token(self, scopes: tuple[str, ...]) -> str:
            raise AssertionError("a person's flow must not mint a machine token")

    monkeypatch.setattr(client_module, "_client", stub_client)
    monkeypatch.setattr(instances_module, "_client", stub_client)
    monkeypatch.setattr(
        client_module,
        "build_client",
        lambda settings=None: SkattemeldingClient(
            token_provider=NoMachine(),  # type: ignore[arg-type]
            base_url=API_URL,
            idporten_base_url=IDPORTEN_URL,
            app=AppInstanceClient(APP_URL),
        ),
    )

    async def logged_in(db: Any, user: Any, *, missing: str) -> tuple[None, str]:
        return None, "person-token"

    async def exchanged(token: str) -> AltinnToken:
        assert token == "person-token"
        return AltinnToken(value="altinn-token", usable_until=utc_now() + timedelta(minutes=5))

    monkeypatch.setattr(skattemelding_api, "government_login_for", logged_in)
    monkeypatch.setattr(skattemelding_api, "exchange_id_porten_token", exchanged)
    # Skatteetaten prepares a melding in the stub at once; no test waits.
    monkeypatch.setattr(forhaandsfastsetting, "WAITS", (0.0, 0.0))
    yield stub


# --- Books to file from -------------------------------------------------------------


def _register_snapshot(
    database_url: str,
    company_id: int,
    *,
    auditor: str | None,
    unit: dict[str, Any] | None = None,
) -> None:
    """What the company page keeps from Enhetsregisteret: the roles, a revisor among them."""
    roles: list[dict[str, Any]] = []
    if auditor is not None:
        roles.append(
            {
                "group": "REVI",
                "group_description": "Revisor",
                "code": "REVI",
                "description": "Revisor",
                "name": auditor,
                "orgnr": "910000099",
                "elected_by": None,
            }
        )
    engine = sa.create_engine(database_url.replace("+aiosqlite", ""))
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO company_register_snapshot (company_id, orgnr, fetched_at, unit, roles)"
                " VALUES (:company, :orgnr, :now, :unit, :roles)"
            ),
            {
                "company": company_id,
                "orgnr": ORGNR,
                "now": utc_now().isoformat(sep=" "),
                "unit": json.dumps(unit or {}),
                "roles": json.dumps(roles),
            },
        )
    engine.dispose()


def books(
    client: TestClient,
    database_url: str,
    *,
    form: str = "AS",
    auditor: str | None = None,
    register_snapshot: bool = True,
    close: bool = True,
) -> dict[str, Any]:
    """An AS with a year of sales of 10 000 and costs of 1 000, closed with 1 980 in tax."""
    session = register(client)
    company = _company(client, session["memberships"][0]["organization_id"], form=form)
    _post_opening(
        client, company["id"], [_line("1920", debit="30000.00"), _line("2000", credit="30000.00")]
    )
    for type_code, gross in (("salg_hoy_sats", "12500.00"), ("kontorrekvisita", "1250.00")):
        response = record(
            client,
            company["id"],
            type_code=type_code,
            gross_amount=gross,
            occurred_on=f"{LAST_YEAR}-03-14",
        )
        assert response.status_code == 201, response.text
    if close:
        closed = write(
            client,
            "POST",
            f"/api/v1/companies/{company['id']}/arsavslutning",
            json={
                "aar": LAST_YEAR,
                "bekreftet": True,
                **({"skattekostnad": "1980.00"} if form == "AS" else {}),
            },
        )
        assert closed.status_code == 201, closed.text
    if register_snapshot:
        _register_snapshot(database_url, company["id"], auditor=auditor)
    return company


def overview(client: TestClient, company_id: int) -> dict[str, Any]:
    response = client.get(
        f"/api/v1/companies/{company_id}/skattemelding", params={"aar": LAST_YEAR}
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def check(client: TestClient, company_id: int, *, ytelser: bool = False) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/skattemelding/kontroll",
        json={"aar": LAST_YEAR, "ytelser_til_aksjonaerer": ytelser},
    )


def send(client: TestClient, company_id: int, filing: dict[str, Any], **override: Any) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/skattemelding/innsendinger/{filing['id']}/send",
        json={
            "skattemessig_resultat": filing["skattemessig_resultat"],
            "bekreftet": True,
            **override,
        },
    )


def _codes(findings: list[dict[str, Any]]) -> set[str]:
    return {f["code"] for f in findings}


def _envelope_documents(content: bytes) -> dict[str, bytes]:
    root = etree.fromstring(content, _PARSER)
    found: dict[str, bytes] = {}
    for document in root.iter("{*}dokument"):
        kind = document.findtext("{*}type")
        payload = document.findtext("{*}content")
        assert kind is not None and payload is not None
        found[kind] = base64.b64decode(payload)
    return found


# --- Reading the year ---------------------------------------------------------------


def test_the_year_reads_as_skatteetaten_will_compute_it(
    client: TestClient, database_url: str
) -> None:
    company = books(client, database_url)
    body = overview(client, company["id"])
    assert body["blocked"] is False, body["findings"]
    assert body["year_closed"] is True
    assert body["is_over"] is True
    assert body["regnskapsplikt"] == "fullRegnskapsplikt"
    assert body["has_auditor"] is False
    # 10 000 in sales, 1 000 in costs, 1 980 in tax: the result after tax,
    # and the tax added back as a permanent difference.
    assert body["aarsresultat"] == "7020.00"
    assert body["tillegg"] == "1980.00"
    assert body["skattemessig_resultat"] == "9000.00"
    assert [(d["type"], d["amount"]) for d in body["permanent_differences"]] == [
        ("positivSkattekostnad", "1980.00")
    ]
    posts = {p["code"]: p["amount"] for p in body["posts"]}
    assert posts["3000"] == "10000.00"
    assert posts["8300"] == "1980.00"
    assert "8800" not in posts
    assert body["equity_closing"] == "37020.00"
    assert body["formue"]["debt"] > 0
    assert body["filings"] == []


def test_an_open_year_is_said_and_stops_the_check(
    client: TestClient, database_url: str, skatteetaten: Skatteetaten
) -> None:
    company = books(client, database_url, close=False)
    body = overview(client, company["id"])
    assert body["blocked"] is True
    assert "aret_er_apent" in _codes(body["findings"])
    refused = check(client, company["id"])
    assert refused.status_code == 409
    assert refused.json()["code"] == "skattemelding_not_ready"
    assert skatteetaten.fetched == []


def test_without_the_register_myntos_does_not_guess_the_revisor(
    client: TestClient, database_url: str
) -> None:
    company = books(client, database_url, register_snapshot=False)
    body = overview(client, company["id"])
    assert body["has_auditor"] is None
    assert "revisor_ukjent" in _codes(body["findings"])
    assert body["blocked"] is True


def test_an_enkeltpersonforetak_is_refused_by_name(client: TestClient, database_url: str) -> None:
    company = books(client, database_url, form="ENK")
    body = overview(client, company["id"])
    assert "enk_ikke_ferdig" in _codes(body["findings"])
    assert body["blocked"] is True


def test_a_year_without_schemas_is_refused(client: TestClient, database_url: str) -> None:
    company = books(client, database_url)
    response = client.get(f"/api/v1/companies/{company['id']}/skattemelding", params={"aar": 2019})
    assert response.status_code == 422
    assert response.json()["code"] == "skattemelding_year_not_supported"


# --- The check ----------------------------------------------------------------------


def test_the_check_sends_the_completed_pair_and_keeps_it(
    client: TestClient, database_url: str, skatteetaten: Skatteetaten
) -> None:
    company = books(client, database_url)
    response = check(client, company["id"], ytelser=True)
    assert response.status_code == 200, response.text
    filing = response.json()
    assert filing["status"] == "validert"
    assert filing["validation_status"] == "validertOK"
    assert filing["skattemessig_resultat"] == "9000.00"
    assert filing["beregnet_skatt"] == "83820"
    assert filing["ytelser_til_aksjonaerer"] is True
    assert "formue_fra_regnskapet" in _codes(filing["findings"])
    # The person's host, with the person's token: never the machine's.
    assert skatteetaten.fetched == [
        f"idporten-api-test.sits.no/api/skattemelding/v2/{LAST_YEAR}/{ORGNR}"
    ]

    [sent] = skatteetaten.validated
    documents = _envelope_documents(sent)
    nsp = documents["naeringsspesifikasjon"]
    etree.XMLSchema(
        etree.parse(
            str(Path(schema.__file__).parent / "xsd" / schema.NAERINGSSPESIFIKASJON[LAST_YEAR])
        )
    ).assertValid(etree.fromstring(nsp, _PARSER))
    assert f"<partsreferanse>{PARTSNUMMER}</partsreferanse>".encode() in nsp
    melding = documents["skattemeldingUpersonlig"]
    assert b"<formueOgGjeld>" in melding
    assert b">true</harYtelseMellomAksjonaer" in melding

    # And the stored documents are those bytes.
    stored = client.get(
        f"/api/v1/companies/{company['id']}/skattemelding/innsendinger/{filing['id']}/dokument",
        params={"type": "naeringsspesifikasjon"},
    )
    assert stored.status_code == 200
    assert stored.content == nsp
    whole = client.get(
        f"/api/v1/companies/{company['id']}/skattemelding/innsendinger/{filing['id']}/dokument",
        params={"type": "forespoersel"},
    )
    assert whole.content == sent

    listed = overview(client, company["id"])["filings"]
    assert [(f["id"], f["matches_books"]) for f in listed] == [(filing["id"], True)]


def test_a_refused_check_is_kept_and_cannot_be_sent(
    client: TestClient, database_url: str, skatteetaten: Skatteetaten
) -> None:
    skatteetaten.validation_answer = _refused()
    company = books(client, database_url)
    filing = check(client, company["id"]).json()
    assert filing["status"] == "avvist"
    assert filing["reasons"] == ["avvikNaeringsopplysninger"]
    assert [(v["type"], v["is_error"]) for v in filing["veiledninger"]] == [("N_FEIL", True)]
    refused = send(client, company["id"], filing)
    assert refused.status_code == 409
    assert refused.json()["code"] == "skattemelding_not_submittable"
    assert skatteetaten.created is None


# --- The submission -----------------------------------------------------------------


def test_the_checked_envelope_is_what_is_sent(
    client: TestClient, database_url: str, skatteetaten: Skatteetaten
) -> None:
    company = books(client, database_url)
    filing = check(client, company["id"]).json()

    unconfirmed = send(client, company["id"], filing, skattemessig_resultat="9001.00")
    assert unconfirmed.status_code == 422
    assert unconfirmed.json()["code"] == "skattemelding_confirmation_required"

    response = send(client, company["id"], filing)
    assert response.status_code == 200, response.text
    sent = response.json()
    assert sent["status"] == "mottatt"
    assert sent["outcome"] == "Godkjent"
    assert sent["altinn_instance_id"] == INSTANCE_ID
    assert sent["viewer_url"].endswith(f"instansId={INSTANCE_ID}")
    assert [d["data_type"] for d in sent["documents"]] == ["tilbakemelding"]

    assert skatteetaten.created is not None
    assert skatteetaten.created["dataValues"] == {"inntektsaar": str(LAST_YEAR)}
    assert skatteetaten.created["instanceOwner"] == {"organisationNumber": ORGNR}
    [(content_type, disposition, envelope)] = skatteetaten.uploads
    assert content_type == "text/xml"
    assert disposition == "attachment; filename=skattemelding.xml"
    assert envelope == skatteetaten.validated[0]
    assert skatteetaten.process_calls == 2

    answer = client.get(
        f"/api/v1/companies/{company['id']}/skattemelding/innsendinger/{filing['id']}/dokument",
        params={"type": "tilbakemelding"},
    )
    assert answer.content == ANSWER

    again = send(client, company["id"], filing)
    assert again.status_code == 409
    assert again.json()["code"] == "skattemelding_not_submittable"


def test_books_that_moved_after_the_check_stop_the_submission(
    client: TestClient, database_url: str, skatteetaten: Skatteetaten
) -> None:
    company = books(client, database_url)
    filing = check(client, company["id"]).json()
    # Reopen the year and book one more cost: the books now say something else.
    reopened = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/arsavslutning/gjenapning",
        json={
            "aar": LAST_YEAR,
            "begrunnelse": "Glemte en faktura fra mars",
            "bekreftet": True,
        },
    )
    assert reopened.status_code in (200, 201), reopened.text
    assert overview(client, company["id"])["filings"][0]["matches_books"] is False
    refused = send(client, company["id"], filing)
    assert refused.status_code == 409
    assert refused.json()["code"] == "skattemelding_changed"
    assert skatteetaten.created is None


def test_a_company_with_a_revisor_stops_at_the_revisor(
    client: TestClient, database_url: str, skatteetaten: Skatteetaten
) -> None:
    company = books(client, database_url, auditor="Revisjon og Råd AS")
    body = overview(client, company["id"])
    assert body["has_auditor"] is True
    assert body["auditor_name"] == "Revisjon og Råd AS"
    assert "revisor_bekrefter" in _codes(body["findings"])

    filing = check(client, company["id"]).json()
    assert filing["has_auditor"] is True
    nsp = _envelope_documents(skatteetaten.validated[0])["naeringsspesifikasjon"]
    assert b"<skalBekreftesAvRevisor>true</skalBekreftesAvRevisor>" in nsp

    sent = send(client, company["id"], filing).json()
    assert sent["status"] == "hos_revisor"
    assert skatteetaten.created is not None
    assert skatteetaten.created["dataValues"] == {
        "inntektsaar": str(LAST_YEAR),
        "skalBekreftesAvRevisor": "true",
    }
    # The person's own step and no further: the revisor's is the revisor's.
    assert skatteetaten.process_calls == 1

    still = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/skattemelding/innsendinger/{filing['id']}/tilbakemelding",
    )
    assert still.json()["status"] == "hos_revisor"
    # The revisor confirms, Skatteetaten processes, and the answer arrives.
    skatteetaten.process_calls = 2
    done = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/skattemelding/innsendinger/{filing['id']}/tilbakemelding",
    )
    assert done.json()["status"] == "mottatt"


def test_a_submission_that_stops_halfway_is_kept(
    client: TestClient, database_url: str, skatteetaten: Skatteetaten
) -> None:
    skatteetaten.refuse_upload = True
    company = books(client, database_url)
    filing = check(client, company["id"]).json()
    response = send(client, company["id"], filing)
    assert response.status_code == 502
    assert response.json()["code"] == "submission_failed"
    kept = client.get(
        f"/api/v1/companies/{company['id']}/skattemelding/innsendinger/{filing['id']}"
    ).json()
    assert kept["status"] == "feilet"
    assert kept["altinn_instance_id"] == INSTANCE_ID


# --- The walls ----------------------------------------------------------------------


def test_another_company_s_filing_is_not_found(
    client: TestClient, database_url: str, skatteetaten: Skatteetaten
) -> None:
    company = books(client, database_url)
    filing = check(client, company["id"]).json()
    # The same person keeps a second company: the wall is per company, not per person.
    other = _company(client, company["organization_id"], orgnr="910000012", name="Annen AS")
    response = client.get(
        f"/api/v1/companies/{other['id']}/skattemelding/innsendinger/{filing['id']}"
    )
    assert response.status_code == 404
    assert response.json()["code"] == "skattemelding_filing_not_found"


def test_a_key_reads_the_year_and_asks_its_person_to_file(
    client: TestClient, database_url: str, skatteetaten: Skatteetaten
) -> None:
    company = books(client, database_url)
    header = agent_header(client, company["id"], scopes=("read",))
    read = client.get(
        f"/api/v1/companies/{company['id']}/skattemelding",
        params={"aar": LAST_YEAR},
        headers=header,
    )
    assert read.status_code == 200, read.text

    refused = client.post(
        f"/api/v1/companies/{company['id']}/skattemelding/kontroll",
        json={"aar": LAST_YEAR, "ytelser_til_aksjonaerer": False},
        headers=header,
    )
    # Checking needs post, on the key's own or by asking; this key may do neither.
    assert refused.status_code == 403
    assert refused.json()["code"] == "missing_scope"

    asked = client.post(
        f"/api/v1/companies/{company['id']}/godkjenninger/innsending",
        json={"type": "skattemelding", "aar": LAST_YEAR},
        headers=header | {"Myntos-Note": "Skattemeldingen er klar til kontroll."},
    )
    assert asked.status_code == 200, asked.text
    request = asked.json()
    assert request["filing"] == {
        "type": "skattemelding",
        "aar": LAST_YEAR,
        "termin": None,
        "naering": None,
    }
    assert request["label"] == f"at du sender skattemeldingen for {LAST_YEAR}"

    filing = check(client, company["id"]).json()
    assert send(client, company["id"], filing).status_code == 200
    seen = client.get(
        f"/api/v1/companies/{company['id']}/godkjenninger/{request['id']}", headers=header
    ).json()
    assert seen["status"] == "done"
    again = client.post(
        f"/api/v1/companies/{company['id']}/godkjenninger/innsending",
        json={"type": "skattemelding", "aar": LAST_YEAR},
        headers=header,
    )
    assert again.status_code == 409
    assert again.json()["code"] == "already_filed"


def test_a_key_checks_and_files_only_when_its_person_says_yes(
    client: TestClient,
    database_url: str,
    skatteetaten: Skatteetaten,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The owner's rule: an agent may do everything, and nothing leaves Myntos unapproved.

    A key that may post on its own still waits for its person for the check and
    for the filing, and each goes, once approved, with the person's login.
    """
    company = books(client, database_url)
    base = f"/api/v1/companies/{company['id']}"
    header = agent_header(client, company["id"], scopes=("read", "write", "post"))

    held = client.post(
        f"{base}/skattemelding/kontroll",
        json={"aar": LAST_YEAR, "ytelser_til_aksjonaerer": False},
        headers=header,
    )
    assert held.status_code == 202, held.text
    assert skatteetaten.validated == []
    waiting = client.get(held.headers["Location"]).json()
    assert waiting["label"] == "sende skattemeldingen til kontroll hos Skatteetaten"
    assert (waiting["sends_out"], waiting["needs_id_porten"]) == (True, True)

    async def logged_in(db: Any, user: Any) -> bool:
        return True

    monkeypatch.setattr(approvals_api, "has_usable_login", logged_in)
    checked = write(
        client, "POST", f"{base}/godkjenninger/{waiting['id']}/godkjenn", json={"files": []}
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "done"
    filing = checked.json()["result"]
    assert filing["status"] == "validert"
    assert len(skatteetaten.validated) == 1

    held = client.post(
        f"{base}/skattemelding/innsendinger/{filing['id']}/send",
        json={"skattemessig_resultat": filing["skattemessig_resultat"], "bekreftet": True},
        headers=header,
    )
    assert held.status_code == 202, held.text
    assert skatteetaten.uploads == []
    sent = write(
        client,
        "POST",
        f"{base}/godkjenninger/{held.json()['approval_id']}/godkjenn",
        json={"files": []},
    )
    assert sent.json()["status"] == "done", sent.text
    assert sent.json()["result"]["status"] == "mottatt"
    assert len(skatteetaten.uploads) == 1


# --- A wound-up company's last year: the forhåndsfastsetting ---------------------------
#
# Last year, ended on its last day and closed, filed as though it were still
# that autumn: before the ordinary assessment, when Skatteetaten has no melding
# for the year until Myntos asks it to prepare one. The shape of what is sent
# is the one TT02 validated and filed on 2026-10-22.


@pytest.fixture
def before_the_assessment(monkeypatch: pytest.MonkeyPatch) -> date:
    autumn = date(LAST_YEAR, 10, 22)
    monkeypatch.setattr(skattemelding_api, "_today", lambda: autumn)
    return autumn


def wound_up(client: TestClient, database_url: str, *, deleted: bool = False) -> dict[str, Any]:
    """An AS that spent 500, paid the rest to its shareholders on 30 June, and was dissolved."""
    company = winding_up_tests.books(client)
    winding_up_tests.pay_out(client, company["id"])
    assert winding_up_tests.wind_up(client, company["id"]).status_code == 200
    closed = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/arsavslutning",
        json={"aar": LAST_YEAR, "bekreftet": True},
    )
    assert closed.status_code == 201, closed.text
    _register_snapshot(
        database_url,
        company["id"],
        auditor=None,
        unit={"orgnr": ORGNR, "deleted_on": f"{LAST_YEAR}-08-15"} if deleted else None,
    )
    return company


def check_final(client: TestClient, company_id: int, **body: Any) -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/skattemelding/kontroll",
        json={"aar": LAST_YEAR, "ytelser_til_aksjonaerer": False, **body},
    )


def test_a_wound_up_company_files_its_last_year_early(
    client: TestClient,
    database_url: str,
    skatteetaten: Skatteetaten,
    before_the_assessment: date,
) -> None:
    skatteetaten.prepare_answer = "OK"
    company = wound_up(client, database_url)
    body = overview(client, company["id"])
    assert body["forhaandsfastsetting"] is True
    assert body["last_day"] == winding_up_tests.LAST_DAY.isoformat()
    assert body["is_over"] is True
    assert body["blocked"] is False, body["findings"]
    assert body["likvidasjonsvederlag_forslag"] == "29500.00"
    assert "utbetaling_ved_opplosning" in _codes(body["findings"])
    assert body["revisor_unknown"] is False

    # The payout is the person's to state; nothing is asked of Skatteetaten
    # without it.
    missing = check_final(client, company["id"])
    assert missing.status_code == 422
    assert missing.json()["code"] == "skattemelding_answer_required"
    assert skatteetaten.fetched == [] and skatteetaten.prepared == 0

    response = check_final(client, company["id"], likvidasjonsvederlag=29500)
    assert response.status_code == 200, response.text
    filing = response.json()
    assert filing["status"] == "validert"
    assert filing["forhaandsfastsetting"] is True
    assert filing["likvidasjonsvederlag"] == 29500
    # Fetched, found nothing, asked Skatteetaten to prepare it, fetched again.
    assert skatteetaten.prepared == 1
    assert len(skatteetaten.fetched) == 2

    [sent] = skatteetaten.validated
    root = etree.fromstring(sent, _PARSER)
    for document in root.iter("{*}dokument"):
        content = base64.b64decode(document.findtext("{*}content") or "")
        namespace = etree.QName(etree.fromstring(content, _PARSER)).namespace
        assert document.findtext("{*}navneromVedForhaandsfastsetting") == namespace
    documents = _envelope_documents(sent)
    xsd = Path(schema.__file__).parent / "xsd"
    melding = documents["skattemeldingUpersonlig"]
    etree.XMLSchema(etree.parse(str(xsd / schema.SKATTEMELDING_UPERSONLIG[LAST_YEAR]))).assertValid(
        etree.fromstring(melding, _PARSER)
    )
    assert b"<forhaandsfastsettingsformattype>aaretsSkattemelding<" in melding
    assert b"<erFulltAvvikletIInntektsaaret><boolsk>true</boolsk>" in melding
    assert b"<likvidasjonsvederlagVedForhaandsfastsetting><beloepSomHeltall>29500<" in melding
    nsp = documents["naeringsspesifikasjon"]
    etree.XMLSchema(etree.parse(str(xsd / schema.NAERINGSSPESIFIKASJON[LAST_YEAR]))).assertValid(
        etree.fromstring(nsp, _PARSER)
    )
    period = etree.fromstring(nsp, _PARSER).find(".//{*}regnskapsperiode")
    assert period is not None
    assert period.findtext("{*}slutt/{*}dato") == winding_up_tests.LAST_DAY.isoformat()

    sent_back = send(client, company["id"], filing)
    assert sent_back.status_code == 200, sent_back.text
    assert skatteetaten.created is not None
    assert skatteetaten.created["dataValues"]["inntektsaar"] == str(LAST_YEAR)
    assert skatteetaten.uploads[0][2] == sent

    # What Skatteetaten was told rests on the last day.
    undo = write(
        client, "POST", f"/api/v1/companies/{company['id']}/arsavslutning/opplosning/angre"
    )
    assert undo.status_code == 409


def test_skatteetaten_prepares_only_for_a_company_registered_as_dissolved(
    client: TestClient,
    database_url: str,
    skatteetaten: Skatteetaten,
    before_the_assessment: date,
) -> None:
    skatteetaten.prepare_answer = "PART_HAR_UGYLDIG_STATUS_I_ER"
    company = wound_up(client, database_url)
    refused = check_final(client, company["id"], likvidasjonsvederlag=29500)
    assert refused.status_code == 409
    assert refused.json()["code"] == "skattemelding_not_available"
    assert "Brønnøysundregistrene" in refused.json()["detail"]
    assert skatteetaten.validated == []


def test_a_melding_still_being_prepared_is_asked_for_again(
    client: TestClient,
    database_url: str,
    skatteetaten: Skatteetaten,
    before_the_assessment: date,
) -> None:
    skatteetaten.prepare_answer = "OK"
    skatteetaten.never_ready = True
    company = wound_up(client, database_url)
    waiting = check_final(client, company["id"], likvidasjonsvederlag=29500)
    assert waiting.status_code == 409
    assert waiting.json()["code"] == "skattemelding_not_available"
    assert "igjen om litt" in waiting.json()["detail"]


def test_last_years_format_is_not_written(
    client: TestClient,
    database_url: str,
    skatteetaten: Skatteetaten,
    before_the_assessment: date,
) -> None:
    skatteetaten.prepare_answer = "OK"
    skatteetaten.prepare_format = "fjoraaretsSkattemelding"
    company = wound_up(client, database_url)
    refused = check_final(client, company["id"], likvidasjonsvederlag=29500)
    assert refused.status_code == 422
    assert refused.json()["code"] == "skattemelding_year_not_supported"
    assert skatteetaten.validated == []


def test_a_deleted_company_is_asked_about_its_revisor(
    client: TestClient,
    database_url: str,
    skatteetaten: Skatteetaten,
    before_the_assessment: date,
) -> None:
    skatteetaten.prepare_answer = "OK"
    company = wound_up(client, database_url, deleted=True)
    body = overview(client, company["id"])
    # The register's empty role list for a deleted company is not "no revisor".
    assert body["revisor_unknown"] is True
    assert body["has_auditor"] is None
    assert "revisor_ukjent_slettet" in _codes(body["findings"])
    assert body["blocked"] is False

    unanswered = check_final(client, company["id"], likvidasjonsvederlag=29500)
    assert unanswered.status_code == 422
    assert unanswered.json()["code"] == "skattemelding_answer_required"

    answered = check_final(client, company["id"], likvidasjonsvederlag=29500, revisor=False)
    assert answered.status_code == 200, answered.text
    assert answered.json()["has_auditor"] is False
    listed = overview(client, company["id"])
    assert listed["revisor_unknown"] is True
    assert [f["matches_books"] for f in listed["filings"]] == [True]
