"""End-to-end HTTP API tests for årsregnskap (RR-0002) and Altinn 3 Brreg workflow."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from lxml import etree

from myntos.api.v1 import aarsregnskap as aarsregnskap_api
from myntos.api.v1 import systembruker as systembruker_api
from myntos.core.clock import utc_now
from myntos.core.config import get_settings
from myntos.integrations.altinn import instances as altinn_instances
from myntos.integrations.altinn import systemuser
from myntos.integrations.altinn import tokens as altinn_tokens
from myntos.integrations.altinn.tokens import AltinnToken
from tests.conftest import agent_header, register, write
from tests.test_aarsregnskap_xml import HOVED_2027, HOVED_LIVE, UNDER_2027, UNDER_LIVE

ORGNR = "915933149"
APP_URL = "https://brg.apps.tt02.altinn.no/brg/aarsregnskap-vanlig-202406"
PARTY_ID = 50099123


class _StubMaskinporten:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def get_access_token(
        self,
        scopes: Any,
        *,
        consumer_org: str | None = None,
        systemuser_org: str | None = None,
        external_ref: str | None = None,
    ) -> str:
        self.calls.append(
            {
                "scopes": list(scopes),
                "consumer_org": consumer_org,
                "systemuser_org": systemuser_org,
                "external_ref": external_ref,
            }
        )
        return "mp-token-stub"


def _create_closed_as(client: TestClient, year: int = 2025) -> dict[str, Any]:
    """Create an AS, book an opening balance + income/cost in ``year``, and close ``year``."""
    reg = register(client)
    org_id = reg["memberships"][0]["organization_id"]
    made = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": org_id,
            "orgnr": ORGNR,
            "name": "Nordmann Teknologi AS",
            "organisation_form": "AS",
            "vat_registered": False,
            "first_financial_year": year,
        },
    )
    assert made.status_code == 201, made.text
    company: dict[str, Any] = made.json()
    cid = company["id"]

    # Opening balance: 30 000 bank / 30 000 share capital
    ib = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/inngaende-balanse",
        json={
            "lines": [
                {"account_number": "1920", "debit": "30000.00", "credit": "0.00"},
                {"account_number": "2000", "debit": "0.00", "credit": "30000.00"},
            ],
        },
    )
    assert ib.status_code == 201, ib.text

    # Revenue 250 000, office expenses 50 000 -> profit before tax 200 000
    sale = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/cashflow",
        json={
            "type_code": "salg_hoy_sats",
            "occurred_on": f"{year}-06-15",
            "gross_amount": "250000.00",
            "description": "Konsulentbistand",
            "payment_method": "bank",
        },
    )
    assert sale.status_code == 201, sale.text

    cost = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/cashflow",
        json={
            "type_code": "kontorrekvisita",
            "occurred_on": f"{year}-07-01",
            "gross_amount": "50000.00",
            "description": "Kontorkostnader",
            "payment_method": "bank",
        },
    )
    assert cost.status_code == 201, cost.text

    # Close financial year with 44 000 tax -> net profit 156 000
    closed = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/arsavslutning",
        json={"aar": year, "skattekostnad": "44000.00", "bekreftet": True},
    )
    assert closed.status_code == 201, closed.text
    return company


def _enable_brreg_env(
    monkeypatch: pytest.MonkeyPatch,
) -> _StubMaskinporten:
    """Configure Altinn 3 årsregnskap URL, systembruker vendor settings, and ID-porten vault."""
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ALTINN_AARSREGNSKAP_APP_URL", APP_URL)
    monkeypatch.setenv("ALTINN_VENDOR_ORGNR", "938193045")
    monkeypatch.setenv("ALTINN_SYSTEM_NAME", "myntos")
    monkeypatch.setenv("ALTINN_SYSTEM_REDIRECT_URL", "https://myntos.no/organisasjon/")
    monkeypatch.setenv("MASKINPORTEN_CLIENT_ID", "f8293b48-86b5-466c-9823-526b1b619434")
    monkeypatch.setenv("MASKINPORTEN_JWK", '{"kty":"RSA"}')
    monkeypatch.setenv("IDPORTEN_CLIENT_ID", "idporten-client-test")
    monkeypatch.setenv("IDPORTEN_CLIENT_SECRET", "idporten-secret-test")
    monkeypatch.setenv("IDPORTEN_REDIRECT_URI", "https://app.example/callback")
    monkeypatch.setenv("GOVERNMENT_TOKEN_KEY", key)
    get_settings.cache_clear()

    provider = _StubMaskinporten()
    monkeypatch.setattr(systembruker_api, "get_token_provider", lambda: provider)
    altinn_tokens.reset_machine_tokens()

    async def fake_exchange(token: str, *, plane: str) -> AltinnToken:
        return AltinnToken(
            value=f"altinn-{plane}-jwt",
            usable_until=utc_now() + timedelta(minutes=10),
        )

    monkeypatch.setattr(altinn_tokens, "exchange_token", fake_exchange)
    monkeypatch.setattr(
        aarsregnskap_api,
        "exchange_id_porten_token",
        lambda tok: fake_exchange(tok, plane="id-porten"),
    )
    monkeypatch.setattr(
        altinn_tokens,
        "machine_tokens",
        lambda: altinn_tokens.MachineTokenSource(provider),
    )
    monkeypatch.setattr(
        aarsregnskap_api,
        "machine_tokens",
        lambda: altinn_tokens.MachineTokenSource(provider),
    )

    async def fake_gov_login(db: Any, user: Any, *, missing: str) -> tuple[None, str]:
        return None, "idporten-access-token"

    monkeypatch.setattr(aarsregnskap_api, "government_login_for", fake_gov_login)
    return provider


def _approve_systembruker(
    client: TestClient, company_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    req_id = str(uuid.uuid4())
    state = {"status": "New"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/systemuser/request/vendor" in str(request.url):
            return httpx.Response(
                201,
                json={
                    "id": req_id,
                    "systemId": "938193045_myntos",
                    "partyOrgNo": ORGNR,
                    "status": "New",
                    "confirmUrl": f"https://am.ui.tt02.altinn.no/request?id={req_id}",
                },
            )
        if request.method == "GET" and req_id in str(request.url):
            return httpx.Response(
                200,
                json={
                    "id": req_id,
                    "systemId": "938193045_myntos",
                    "partyOrgNo": ORGNR,
                    "status": state["status"],
                    "confirmUrl": f"https://am.ui.tt02.altinn.no/request?id={req_id}",
                },
            )
        return httpx.Response(404)

    monkeypatch.setattr(
        systemuser,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    created = write(client, "POST", f"/api/v1/companies/{company_id}/altinn/systembruker")
    assert created.status_code == 201, created.text
    state["status"] = "Accepted"
    refreshed = write(
        client, "POST", f"/api/v1/companies/{company_id}/altinn/systembruker/oppdater"
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["approved"] is True


def _stub_brreg_altinn(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_on_action: str | None = None,
    validation_issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Mount an in-memory HTTP mock for ``brg/aarsregnskap-vanlig-202406``."""
    guid = str(uuid.uuid4())
    hoved_id = str(uuid.uuid4())
    under_id = str(uuid.uuid4())
    receipt_id = str(uuid.uuid4())
    sig_id = str(uuid.uuid4())

    trace: dict[str, Any] = {
        "guid": guid,
        "actions": [],
        "auth_headers": [],
        "hoved_xml": None,
        "under_xml": None,
        "vedlegg_count": 0,
        "ended": False,
        "task": "Utfylling",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        trace["auth_headers"].append((request.method, path, request.headers.get("authorization")))

        if request.method == "POST" and path.endswith("/instances"):
            return httpx.Response(
                201,
                json={
                    "id": f"{PARTY_ID}/{guid}",
                    "data": [
                        {"id": hoved_id, "dataType": "Hovedskjema"},
                        {"id": under_id, "dataType": "Underskjema"},
                    ],
                    "process": {"currentTask": {"elementId": "Utfylling"}, "ended": None},
                },
            )
        if request.method == "PUT" and path.endswith(f"/data/{hoved_id}"):
            assert request.headers["content-type"].startswith("application/xml")
            trace["hoved_xml"] = request.content.decode("utf-8")
            return httpx.Response(200, json={"id": hoved_id, "dataType": "Hovedskjema"})
        if request.method == "PUT" and path.endswith(f"/data/{under_id}"):
            assert request.headers["content-type"].startswith("application/xml")
            trace["under_xml"] = request.content.decode("utf-8")
            return httpx.Response(200, json={"id": under_id, "dataType": "Underskjema"})
        if request.method == "POST" and path.endswith("/data"):
            assert request.url.params.get("dataType") == "Vedlegg"
            trace["vedlegg_count"] += 1
            return httpx.Response(
                201,
                json={
                    "id": str(uuid.uuid4()),
                    "dataType": "Vedlegg",
                    "contentType": "application/pdf",
                },
            )
        if request.method == "GET" and path.endswith("/validate"):
            return httpx.Response(200, json=validation_issues or [])
        if request.method == "PUT" and path.endswith("/process/next"):
            body = json.loads(request.content) if request.content else {}
            action = body.get("action")
            trace["actions"].append((action, request.headers.get("authorization")))
            if fail_on_action and action == fail_on_action:
                return httpx.Response(500, text="Altinn downstream error")
            if action == "confirm":
                trace["task"] = "Signering"
                return httpx.Response(
                    200,
                    json={
                        "id": f"{PARTY_ID}/{guid}",
                        "process": {"currentTask": {"elementId": "Signering"}, "ended": None},
                    },
                )
            if action == "sign":
                trace["task"] = None
                trace["ended"] = True
                return httpx.Response(
                    200,
                    json={
                        "id": f"{PARTY_ID}/{guid}",
                        "process": {"currentTask": None, "ended": "2026-06-01T12:00:00Z"},
                    },
                )
        if request.method == "GET" and path.endswith(f"/instances/{PARTY_ID}/{guid}"):
            data_elements = [
                {"id": hoved_id, "dataType": "Hovedskjema"},
                {"id": under_id, "dataType": "Underskjema"},
            ]
            if trace["ended"]:
                data_elements.append(
                    {
                        "id": receipt_id,
                        "dataType": "ref-data-as-pdf",
                        "contentType": "application/pdf",
                        "filename": "kvittering-rr0002.pdf",
                    }
                )
                data_elements.append(
                    {
                        "id": sig_id,
                        "dataType": "signature",
                        "contentType": "application/json",
                        "filename": "signature.json",
                    }
                )
            return httpx.Response(
                200,
                json={
                    "id": f"{PARTY_ID}/{guid}",
                    "data": data_elements,
                    "process": {
                        "currentTask": {"elementId": trace["task"]} if trace["task"] else None,
                        "ended": "2026-06-01T12:00:00Z" if trace["ended"] else None,
                    },
                },
            )
        if request.method == "GET" and path.endswith(f"/data/{receipt_id}"):
            return httpx.Response(200, content=b"%PDF-1.4\nreceipt\n%%EOF\n")
        if request.method == "GET" and path.endswith(f"/data/{sig_id}"):
            return httpx.Response(200, content=b'{"signed": true}')
        return httpx.Response(404, text=f"unhandled {request.method} {path}")

    monkeypatch.setattr(
        altinn_instances,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return trace


# --- Tests ------------------------------------------------------------------


def test_draft_prior_year_override_xml_and_pdf_endpoints(client: TestClient) -> None:
    company = _create_closed_as(client, year=2025)
    cid = company["id"]

    # Initial GET: closed year, missing fastsettelsedato + bekreftende_representant -> blocked.
    initial = client.get(f"/api/v1/companies/{cid}/aarsregnskap?aar=2025")
    assert initial.status_code == 200, initial.text
    body = initial.json()
    assert body["year"] == 2025
    assert body["year_closed"] is True
    assert body["driftsinntekter"] == 250_000
    assert body["driftsresultat"] == 200_000
    assert body["skattekostnad"] == 44_000
    assert body["aarsresultat"] == 156_000
    assert body["sum_eiendeler"] == 230_000
    assert body["sum_egenkapital"] == 186_000
    assert body["sum_gjeld"] == 44_000
    assert body["balance_diff"] == 0
    assert body["blocked"] is True
    assert {f["code"] for f in body["findings"] if f["severity"] == "feil"} == {
        "mangler_fastsettelsedato",
        "mangler_bekreftende_representant",
    }

    # Save draft with approval date, representative, prior-year overrides, and notes.
    saved = write(
        client,
        "PUT",
        f"/api/v1/companies/{cid}/aarsregnskap",
        json={
            "aar": 2025,
            "approval_date": "2026-05-15",
            "confirming_representative": "Kari Nordmann",
            "contact_email": "kari@nordmann.no",
            "board_meeting_date": "2026-04-20",
            "board_place": "Oslo",
            "board_members": [{"name": "Kari Nordmann", "role": "Styreleder"}],
            "general_meeting_date": "2026-05-15",
            "general_meeting_chair": "Kari Nordmann",
            "general_meeting_cosigner": "Ola Hansen",
            "prior_year_overrides": {
                "salgsinntekt": 180000,
                "annen_driftskostnad": 80000,
                "skattekostnad": 22000,
                "bankinnskudd_kontanter": 30000,
                "selskapskapital": 30000,
            },
            "notes": {
                "antall_aarsverk": "1",
                "ytterligere_opplysninger": "Ingen hendelser etter balansedagen.",
            },
        },
    )
    assert saved.status_code == 200, saved.text
    sbody = saved.json()
    assert sbody["blocked"] is False
    assert sbody["uses_prior_year_overrides"] is True

    # XML previews validate against BOTH live and January 2027 official XSDs!
    hoved = client.get(f"/api/v1/companies/{cid}/aarsregnskap/xml?aar=2025&del=hovedskjema")
    under = client.get(f"/api/v1/companies/{cid}/aarsregnskap/xml?aar=2025&del=underskjema")
    assert hoved.status_code == 200
    assert under.status_code == 200
    HOVED_LIVE.assertValid(etree.fromstring(hoved.content))
    HOVED_2027.assertValid(etree.fromstring(hoved.content))
    UNDER_LIVE.assertValid(etree.fromstring(under.content))
    UNDER_2027.assertValid(etree.fromstring(under.content))

    # All three PDF downloads return valid PDF bytes.
    for doc_kind in ("aarsregnskap", "styreprotokoll", "generalforsamling"):
        pdf_res = client.get(
            f"/api/v1/companies/{cid}/aarsregnskap/pdf?aar=2025&dokument={doc_kind}"
        )
        assert pdf_res.status_code == 200
        assert pdf_res.headers["content-type"] == "application/pdf"
        assert pdf_res.content.startswith(b"%PDF-1.4\n")


def test_two_step_systembruker_prepare_and_idporten_sign_archives_and_updates_frister(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = _create_closed_as(client, year=2025)
    cid = company["id"]
    _enable_brreg_env(monkeypatch)
    trace = _stub_brreg_altinn(monkeypatch)

    write(
        client,
        "PUT",
        f"/api/v1/companies/{cid}/aarsregnskap",
        json={
            "aar": 2025,
            "approval_date": "2026-05-15",
            "confirming_representative": "Kari Nordmann",
        },
    )

    # Without approved systembruker -> 409 system_user_not_approved.
    no_sys = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/aarsregnskap/klargjor",
        json={
            "aar": 2025,
            "aarsresultat": 156000,
            "sum_eiendeler": 230000,
            "bekreftet": True,
        },
    )
    assert no_sys.status_code == 409, no_sys.text
    assert no_sys.json()["code"] == "system_user_not_approved"

    # Approve systembruker in Altinn, then prepare & lock via systembruker!
    _approve_systembruker(client, cid, monkeypatch)
    prep = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/aarsregnskap/klargjor",
        json={
            "aar": 2025,
            "aarsresultat": 156000,
            "sum_eiendeler": 230000,
            "bekreftet": True,
            "laas": True,
            "legg_ved_pdf": True,
        },
    )
    assert prep.status_code == 200, prep.text
    filing = prep.json()
    assert filing["status"] == "laast"
    assert filing["auth_method"] == "systembruker"
    assert filing["altinn_instance_id"] == f"{PARTY_ID}/{trace['guid']}"
    assert trace["vedlegg_count"] == 1
    assert trace["actions"] == [("confirm", "Bearer altinn-maskinporten-jwt")]
    # Uploaded XMLs are XSD-valid.
    HOVED_2027.assertValid(etree.fromstring(trace["hoved_xml"].encode("utf-8")))
    UNDER_2027.assertValid(etree.fromstring(trace["under_xml"].encode("utf-8")))

    # Sign & auto-submit with ID-porten!
    signed = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/aarsregnskap/innsendinger/{filing['id']}/signer",
        json={"bekreftet": True},
    )
    assert signed.status_code == 200, signed.text
    sbody = signed.json()
    assert sbody["status"] == "mottatt"
    assert sbody["signed_at"] is not None
    assert sbody["receipt_received_at"] is not None
    assert trace["actions"][-1] == ("sign", "Bearer altinn-id-porten-jwt")
    doc_types = {d["document_type"] for d in sbody["documents"]}
    assert {
        "hovedskjema",
        "underskjema",
        "vedlegg_pdf",
        "kvittering_pdf",
        "signatur_json",
    } <= doc_types

    # Download archived receipt PDF!
    dl = client.get(
        f"/api/v1/companies/{cid}/aarsregnskap/innsendinger/{filing['id']}/dokument?type=kvittering_pdf"
    )
    assert dl.status_code == 200
    assert dl.content.startswith(b"%PDF-1.4")


def test_direct_idporten_flow_partial_failure_persistence_and_agent_key_approval(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = _create_closed_as(client, year=2025)
    cid = company["id"]
    _enable_brreg_env(monkeypatch)

    write(
        client,
        "PUT",
        f"/api/v1/companies/{cid}/aarsregnskap",
        json={
            "aar": 2025,
            "approval_date": "2026-05-15",
            "confirming_representative": "Kari Nordmann",
        },
    )

    # 1. Partial failure during confirm: must return 502 AND persist feilet row with instance id!
    trace_fail = _stub_brreg_altinn(monkeypatch, fail_on_action="confirm")
    failed = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/aarsregnskap/send",
        json={
            "aar": 2025,
            "aarsresultat": 156000,
            "sum_eiendeler": 230000,
            "bekreftet": True,
        },
    )
    assert failed.status_code == 502, failed.text
    assert failed.json()["code"] == "aarsregnskap_remote"
    assert failed.json()["altinn_instance_id"] == f"{PARTY_ID}/{trace_fail['guid']}"

    overview = client.get(f"/api/v1/companies/{cid}/aarsregnskap?aar=2025").json()
    assert len(overview["filings"]) == 1
    assert overview["filings"][0]["status"] == "feilet"
    assert overview["filings"][0]["altinn_instance_id"] == f"{PARTY_ID}/{trace_fail['guid']}"

    # 2. Agent key calling /klargjor or /send is ALWAYS held with 202 approval_pending!
    key_hdr = agent_header(client, cid)
    held = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/aarsregnskap/send",
        headers=key_hdr,
        json={
            "aar": 2025,
            "aarsresultat": 156000,
            "sum_eiendeler": 230000,
            "bekreftet": True,
            "signer_direkte": True,
        },
    )
    assert held.status_code == 202, held.text
    assert held.json()["code"] == "approval_pending"

    # 3. Direct ID-porten sign succeeds end-to-end.
    trace_ok = _stub_brreg_altinn(monkeypatch)
    direct = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/aarsregnskap/send",
        json={
            "aar": 2025,
            "aarsresultat": 156000,
            "sum_eiendeler": 230000,
            "bekreftet": True,
            "signer_direkte": True,
        },
    )
    assert direct.status_code == 200, direct.text
    assert direct.json()["status"] == "mottatt"
    assert [a for a, _ in trace_ok["actions"]] == ["confirm", "sign"]


def test_wrong_figures_rejected_with_aarsregnskap_changed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = _create_closed_as(client, year=2025)
    cid = company["id"]
    _enable_brreg_env(monkeypatch)
    write(
        client,
        "PUT",
        f"/api/v1/companies/{cid}/aarsregnskap",
        json={
            "aar": 2025,
            "approval_date": "2026-05-15",
            "confirming_representative": "Kari Nordmann",
        },
    )
    mismatch = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/aarsregnskap/send",
        json={
            "aar": 2025,
            "aarsresultat": 999999,
            "sum_eiendeler": 230000,
            "bekreftet": True,
        },
    )
    assert mismatch.status_code == 409, mismatch.text
    assert mismatch.json()["code"] == "aarsregnskap_changed"
