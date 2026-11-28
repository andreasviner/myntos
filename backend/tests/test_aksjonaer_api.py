"""End-to-end HTTP API tests for Altinn 3 systembruker, aksjeeierbok, and RF-1086."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from lxml import etree

from myntos.api.v1 import aksjonaer as aksjonaer_api
from myntos.api.v1 import systembruker as systembruker_api
from myntos.core.config import get_settings
from myntos.domain.identity import closure
from myntos.integrations.altinn import systemregister, systemuser
from myntos.integrations.skatteetaten import aksjonaer as ske_aksjonaer
from tests.conftest import agent_header, join_company, register, write

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "aksjonaer"
HOVED_XSD = etree.XMLSchema(
    etree.parse(str(FIXTURES_DIR / "aksjonaerregisteroppgaveHovedskjema.xsd"))
)
UNDER_XSD = etree.XMLSchema(
    etree.parse(str(FIXTURES_DIR / "aksjonaerregisteroppgaveUnderskjema.xsd"))
)

ORGNR = "910000004"
SIBLING_ORGNR = "910000012"
TENOR_FNR_1 = "26829398612"
TENOR_FNR_2 = "08866995401"


class StubTokenProvider:
    """Fake Maskinporten token provider capturing every requested scope and system-user org."""

    def __init__(self, access_value: str) -> None:
        self.token = access_value
        self.calls: list[dict[str, Any]] = []

    async def get_access_token(
        self,
        scopes: Sequence[str],
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
        return self.token


def _configure_altinn_and_ske(monkeypatch: pytest.MonkeyPatch) -> StubTokenProvider:
    """Enable Maskinporten + Altinn systembruker + Skatteetaten RF-1086 with stubs."""
    monkeypatch.setenv("MASKINPORTEN_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("MASKINPORTEN_JWK", '{"kty":"RSA","kid":"test"}')
    monkeypatch.setenv("ALTINN_VENDOR_ORGNR", "938193045")
    monkeypatch.setenv("ALTINN_SYSTEM_NAME", "myntos")
    monkeypatch.setenv("ALTINN_SYSTEM_REDIRECT_URL", "https://myntos.no/organisasjon/#altinn")
    monkeypatch.setenv("SKATTEETATEN_API_BASE_URL", "https://api-test.sits.no")
    get_settings.cache_clear()

    provider = StubTokenProvider("stub-mp-token")
    monkeypatch.setattr(systembruker_api, "get_token_provider", lambda: provider)
    monkeypatch.setattr(aksjonaer_api, "get_token_provider", lambda: provider)
    return provider


def _as_company(
    client: TestClient, *, orgnr: str = ORGNR, form: str = "AS", name: str = "Fjord Tech AS"
) -> dict[str, Any]:
    session = register(client)
    res = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": session["memberships"][0]["organization_id"],
            "orgnr": orgnr,
            "name": name,
            "first_financial_year": 2026,
            "vat_registered": False,
            "organisation_form": form,
            "email": "post@fjordtech.example",
            "address_line": "Bryggen 5",
            "postal_code": "5003",
            "city": "Bergen",
        },
    )
    assert res.status_code == 201, res.text
    body: dict[str, Any] = res.json()
    return body


def _approve_systembruker(
    client: TestClient,
    company_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Drive POST /altinn/systembruker and POST /altinn/systembruker/oppdater to accepted."""
    req_id = str(uuid.uuid4())
    state = {"status": "New"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/systemuser/request/vendor" in str(request.url):
            payload = request.read().decode("utf-8")
            assert systemregister.AKSJONAERREGISTER_RESOURCE in payload
            return httpx.Response(
                201,
                json={
                    "id": req_id,
                    "externalRef": "placeholder-ignored-when-overridden",
                    "systemId": "938193045_myntos",
                    "partyOrgNo": ORGNR,
                    "status": "New",
                    "confirmUrl": f"https://am.ui.tt02.altinn.no/accessmanagement/ui/systemuser/request?id={req_id}",
                },
            )
        if request.method == "GET" and f"/systemuser/request/vendor/{req_id}" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "id": req_id,
                    "systemId": "938193045_myntos",
                    "partyOrgNo": ORGNR,
                    "status": state["status"],
                    "confirmUrl": f"https://am.ui.tt02.altinn.no/accessmanagement/ui/systemuser/request?id={req_id}",
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
    assert created.json()["approved"] is False
    assert created.json()["system_user"]["status"] == "new"
    assert "confirmUrl" not in created.json()
    assert created.json()["system_user"]["confirm_url"].startswith("https://am.ui.tt02.altinn.no/")

    # Customer approves in Altinn; Myntos refreshes status.
    state["status"] = "Accepted"
    refreshed = write(
        client, "POST", f"/api/v1/companies/{company_id}/altinn/systembruker/oppdater"
    )
    assert refreshed.status_code == 200, refreshed.text
    body: dict[str, Any] = refreshed.json()
    assert body["approved"] is True
    assert body["system_user"]["status"] == "accepted"
    assert body["system_user"]["approved_at"] is not None
    return body


def _seed_register(client: TestClient, company_id: int) -> tuple[int, int, int]:
    """Create class 01, two shareholders, and a stiftelse in 2026."""
    sc_res = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/aksjonaer/aksjeklasser",
        json={"code": "01", "name": "Ordinære aksjer"},
    )
    assert sc_res.status_code == 201, sc_res.text
    sc_id = int(sc_res.json()["id"])

    h1_res = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/aksjonaer/aksjonaerer",
        json={
            "kind": "person",
            "name": "Kari Nordmann",
            "national_id": TENOR_FNR_1,
            "address": "Storgata 1",
            "postal_code": "5003",
            "city": "Bergen",
        },
    )
    assert h1_res.status_code == 201, h1_res.text
    h1_id = int(h1_res.json()["id"])

    h2_res = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/aksjonaer/aksjonaerer",
        json={
            "kind": "selskap",
            "name": "Vestland Invest AS",
            "orgnr": SIBLING_ORGNR,
            "address": "Kaigaten 9",
            "postal_code": "5015",
            "city": "Bergen",
        },
    )
    assert h2_res.status_code == 201, h2_res.text
    h2_id = int(h2_res.json()["id"])

    ev_res = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/aksjonaer/hendelser",
        json={
            "share_class_id": sc_id,
            "event_type": "stiftelse",
            "subtype": "N",
            "event_date": "2026-01-10",
            "total_shares": 1000,
            "par_value_after": "30",
            "total_amount": "30000.00",
            "lines": [
                {"shareholder_id": h1_id, "shares": 600, "amount": "18000.00"},
                {"shareholder_id": h2_id, "shares": 400, "amount": "12000.00"},
            ],
        },
    )
    assert ev_res.status_code == 201, ev_res.text
    return sc_id, h1_id, h2_id


# --- Altinn 3 systembruker tests --------------------------------------------------


def test_systembruker_unconfigured_returns_503_and_available_false(client: TestClient) -> None:
    company = _as_company(client)
    status_res = client.get(f"/api/v1/companies/{company['id']}/altinn/systembruker")
    assert status_res.status_code == 200
    assert status_res.json()["available"] is False
    assert status_res.json()["approved"] is False

    create_res = write(client, "POST", f"/api/v1/companies/{company['id']}/altinn/systembruker")
    assert create_res.status_code == 503
    assert create_res.json()["code"] == "system_user_not_available"


def test_systembruker_lifecycle_permissions_and_agent_keys(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_altinn_and_ske(monkeypatch)
    company = _as_company(client)
    cid = company["id"]

    # Refreshing before any request exists returns 404.
    early = write(client, "POST", f"/api/v1/companies/{cid}/altinn/systembruker/oppdater")
    assert early.status_code == 404
    assert early.json()["code"] == "system_user_request_not_found"

    # Unknown Altinn resource returns 422.
    bad_res = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/altinn/systembruker",
        json={"resources": ["ikke-en-gyldig-ressurs"]},
    )
    assert bad_res.status_code == 422
    assert bad_res.json()["code"] == "invalid_system_user_resource"

    # Happy lifecycle: request -> approve -> refresh.
    _approve_systembruker(client, cid, monkeypatch)

    # Agent key can READ systembruker status, never CREATE/REFRESH it.
    key_hdr = agent_header(client, cid)
    key_get = client.get(f"/api/v1/companies/{cid}/altinn/systembruker", headers=key_hdr)
    assert key_get.status_code == 200
    assert key_get.json()["approved"] is True

    key_post = write(
        client, "POST", f"/api/v1/companies/{cid}/altinn/systembruker", headers=key_hdr
    )
    assert key_post.status_code == 403
    assert key_post.json()["code"] == "not_for_agent_keys"

    # A regular member cannot create a systembruker (needs organization:manage).
    join_company(client, cid, role="member", email="medlem@example.com")
    member_post = write(client, "POST", f"/api/v1/companies/{cid}/altinn/systembruker")
    assert member_post.status_code == 403


# --- Aksjeeierbok CRUD, ledger 2000 check & XML preview ---------------------------


def test_aksjeeierbok_crud_masking_and_xml_preview(client: TestClient) -> None:
    company = _as_company(client)
    cid = company["id"]
    sc_id, h1_id, _ = _seed_register(client, cid)

    # Duplicate share class code is refused with 409.
    dup = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/aksjonaer/aksjeklasser",
        json={"code": "01", "name": "Duplikat"},
    )
    assert dup.status_code == 409
    assert dup.json()["code"] == "duplicate_share_class"

    # Deleting a share class or shareholder referenced by an event is refused with 409.
    assert (
        write(
            client, "DELETE", f"/api/v1/companies/{cid}/aksjonaer/aksjeklasser/{sc_id}"
        ).status_code
        == 409
    )
    assert (
        write(
            client, "DELETE", f"/api/v1/companies/{cid}/aksjonaer/aksjonaerer/{h1_id}"
        ).status_code
        == 409
    )

    # Updating shareholder name without resending national_id preserves the stored fnr.
    upd = write(
        client,
        "PATCH",
        f"/api/v1/companies/{cid}/aksjonaer/aksjonaerer/{h1_id}",
        json={"name": "Kari Nordmann-Berg"},
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["name"] == "Kari Nordmann-Berg"
    assert upd.json()["national_id_masked"] == "268293 *****"
    assert upd.json()["has_national_id"] is True
    assert TENOR_FNR_1 not in upd.text  # raw fnr never leaked in JSON

    # Year overview computes closing state cleanly and masks fnr.
    overview = client.get(f"/api/v1/companies/{cid}/aksjonaer", params={"aar": 2026})
    assert overview.status_code == 200, overview.text
    data = overview.json()
    assert data["blocked"] is False
    assert data["company_closing_capital"] == "30000.00"
    assert TENOR_FNR_1 not in overview.text

    # Ledger cross-check against account 2000 (manual voucher credit 50 000 triggers merknad).
    mv = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/manuelt-bilag",
        json={
            "occurred_on": "2026-01-10",
            "description": "Innbetalt aksjekapital",
            "explanation": "Aksjekapital ved stiftelse.",
            "lines": [
                {"account_number": "1920", "debit": "50000.00"},
                {"account_number": "2000", "credit": "50000.00"},
            ],
        },
    )
    assert mv.status_code == 201, mv.text

    after_gl = client.get(f"/api/v1/companies/{cid}/aksjonaer", params={"aar": 2026}).json()
    assert after_gl["ledger_share_capital"] == "50000.00"
    assert after_gl["blocked"] is False
    assert any(f["code"] == "aksjekapital_avviker_fra_regnskap" for f in after_gl["findings"])

    # XML preview returns XSD-valid Hovedskjema and Underskjema.
    hoved_xml = client.get(
        f"/api/v1/companies/{cid}/aksjonaer/xml",
        params={"aar": 2026, "klasse": "01", "skjema": "hovedskjema"},
    )
    assert hoved_xml.status_code == 200
    HOVED_XSD.assertValid(etree.fromstring(hoved_xml.content))

    under_xml = client.get(
        f"/api/v1/companies/{cid}/aksjonaer/xml",
        params={"aar": 2026, "klasse": "01", "skjema": "underskjema", "aksjonaer_id": h1_id},
    )
    assert under_xml.status_code == 200
    UNDER_XSD.assertValid(etree.fromstring(under_xml.content))


# --- End-to-end RF-1086 submission, prefill, partial failure & approvals ----------


def test_end_to_end_rf1086_submission_archiving_and_frister(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _configure_altinn_and_ske(monkeypatch)
    company = _as_company(client)
    cid = company["id"]
    sc_id, h1_id, h2_id = _seed_register(client, cid)

    # Without bekreftet=True -> 422.
    unconfirmed = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/aksjonaer/send",
        json={"aar": 2026, "bekreftet": False},
    )
    assert unconfirmed.status_code == 422
    assert unconfirmed.json()["code"] == "aksjonaer_confirmation_required"

    # Without approved systembruker -> 409.
    no_sysuser = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/aksjonaer/send",
        json={"aar": 2026, "bekreftet": True},
    )
    assert no_sysuser.status_code == 409
    assert no_sysuser.json()["code"] == "system_user_not_approved"

    # Approve systembruker in Altinn.
    _approve_systembruker(client, cid, monkeypatch)

    # Stub Skatteetaten RF-1086 endpoints and validate every XML sent over HTTP!
    hoved_uuid = str(uuid.uuid4())
    dialog_uuid = str(uuid.uuid4())
    forsendelse_uuid = str(uuid.uuid4())
    seen_keys: set[str] = set()
    calls: list[str] = []

    def ske_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer stub-mp-token"
        path = request.url.path
        calls.append(f"{request.method} {path}")
        if path.endswith("/2026/1086H"):
            key = request.headers["idempotencykey"]
            assert key not in seen_keys
            seen_keys.add(key)
            HOVED_XSD.assertValid(etree.fromstring(request.content))
            return httpx.Response(200, json={"hovedskjemaId": hoved_uuid})
        if path.endswith(f"/2026/{hoved_uuid}/1086U"):
            key = request.headers["idempotencykey"]
            assert key not in seen_keys
            seen_keys.add(key)
            UNDER_XSD.assertValid(etree.fromstring(request.content))
            return httpx.Response(200, content=b"")
        if path.endswith(f"/2026/{hoved_uuid}/bekreft"):
            assert request.url.params["antall_underskjema"] == "2"
            return httpx.Response(
                200,
                json={
                    "oppgavegiversLeveranseReferanse": "SKE-2026-REF-001",
                    "dialogId": dialog_uuid,
                    "forsendelseId": forsendelse_uuid,
                },
            )
        return httpx.Response(404)

    monkeypatch.setattr(
        ske_aksjonaer,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(ske_handler)),
    )

    sent = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/aksjonaer/send",
        json={
            "aar": 2026,
            "share_class_id": sc_id,
            "contact_email": "post@fjordtech.example",
            "contact_name": "Kari Nordmann",
            "contact_role": "Daglig leder",
            "bekreftet": True,
        },
    )
    assert sent.status_code == 200, sent.text
    filing = sent.json()
    assert filing["status"] == "bekreftet"
    assert filing["hovedskjema_id"] == hoved_uuid
    assert filing["leveranse_referanse"] == "SKE-2026-REF-001"
    assert filing["dialog_id"] == dialog_uuid
    assert filing["forsendelse_id"] == forsendelse_uuid
    assert filing["underskjema_count"] == 2
    assert len(filing["documents"]) == 3  # hovedskjema + 2 underskjemaer
    assert len(seen_keys) == 3
    assert calls == [
        "POST /api/aksjonaerregister/v1/2026/1086H",
        f"POST /api/aksjonaerregister/v1/2026/{hoved_uuid}/1086U",
        f"POST /api/aksjonaerregister/v1/2026/{hoved_uuid}/1086U",
        f"POST /api/aksjonaerregister/v1/2026/{hoved_uuid}/bekreft",
    ]

    # Maskinporten was asked for the RF-1086 scope with customer's systemuser_org + external_ref.
    rf_calls = [c for c in provider.calls if ske_aksjonaer.SCOPE in c["scopes"]]
    assert len(rf_calls) == 1
    assert rf_calls[0]["systemuser_org"] == ORGNR
    assert rf_calls[0]["external_ref"] is not None

    # Archived Hovedskjema and Underskjema documents can be downloaded verbatim.
    fid = filing["id"]
    dl_hoved = client.get(
        f"/api/v1/companies/{cid}/aksjonaer/innsendinger/{fid}/dokument",
        params={"type": "hovedskjema"},
    )
    assert dl_hoved.status_code == 200
    HOVED_XSD.assertValid(etree.fromstring(dl_hoved.content))

    dl_u1 = client.get(
        f"/api/v1/companies/{cid}/aksjonaer/innsendinger/{fid}/dokument",
        params={"type": f"underskjema_{h1_id}"},
    )
    assert dl_u1.status_code == 200
    UNDER_XSD.assertValid(etree.fromstring(dl_u1.content))

    dl_u2 = client.get(
        f"/api/v1/companies/{cid}/aksjonaer/innsendinger/{fid}/dokument",
        params={"type": f"underskjema_{h2_id}"},
    )
    assert dl_u2.status_code == 200
    UNDER_XSD.assertValid(etree.fromstring(dl_u2.content))

    # Unknown document type returns 404.
    assert (
        client.get(
            f"/api/v1/companies/{cid}/aksjonaer/innsendinger/{fid}/dokument",
            params={"type": "finnes_ikke"},
        ).status_code
        == 404
    )

    # Company closure treats RF-1086 register + filings as statutory books to retain 5 years.
    plan = client.get(f"/api/v1/companies/{cid}/sletting")
    assert plan.status_code == 200
    assert plan.json()["outcome"] == "kept"
    assert plan.json()["retain_until"] is not None


def test_partial_failure_records_feilet_filing_with_hovedskjema_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_altinn_and_ske(monkeypatch)
    company = _as_company(client)
    cid = company["id"]
    _seed_register(client, cid)
    _approve_systembruker(client, cid, monkeypatch)

    hoved_uuid = str(uuid.uuid4())

    def failing_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/1086H"):
            return httpx.Response(200, json={"hovedskjemaId": hoved_uuid})
        if request.url.path.endswith("/1086U"):
            return httpx.Response(
                400,
                json={
                    "kode": "GLD_008",
                    "melding": "Valideringsfeil i underskjema",
                    "korrelasjonsid": "corr-123",
                    "spesifisering": [
                        {
                            "kode": "VAL_1",
                            "melding": "Ugyldig aksjeantall",
                            "sti": "/Skjema/Antall",
                        }
                    ],
                },
            )
        return httpx.Response(404)

    monkeypatch.setattr(
        ske_aksjonaer,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(failing_handler)),
    )

    res = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/aksjonaer/send",
        json={"aar": 2026, "bekreftet": True},
    )
    assert res.status_code == 502
    assert res.json()["code"] == "aksjonaer_remote"
    assert res.json()["filing"]["status"] == "feilet"
    assert res.json()["filing"]["hovedskjema_id"] == hoved_uuid
    assert "GLD_008" in res.json()["filing"]["error"]

    # The failed filing row survived the 502 response and appears in GET /aksjonaer.
    listed = client.get(f"/api/v1/companies/{cid}/aksjonaer", params={"aar": 2026}).json()
    assert len(listed["filings"]) == 1
    assert listed["filings"][0]["status"] == "feilet"
    assert listed["filings"][0]["hovedskjema_id"] == hoved_uuid


def test_prefill_import_and_agent_key_approval_hold(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_altinn_and_ske(monkeypatch)
    company = _as_company(client)
    cid = company["id"]
    _approve_systembruker(client, cid, monkeypatch)

    # First verify 404 from Skatteetaten yields 409 aksjonaer_prefill_not_found.
    monkeypatch.setattr(
        ske_aksjonaer,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(404))),
    )
    missing = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/aksjonaer/prefill",
        json={"aar": 2026},
    )
    assert missing.status_code == 409
    assert missing.json()["code"] == "aksjonaer_prefill_not_found"

    # Now return realistic RF-1086 + RF-1086-U XML in Skatteetaten's prefill format.
    prefill_hoved = (
        '<Skjema skjemanummer="890" blankettnummer="RF-1086">'
        "<GenerellInformasjon-grp-2587><Selskap-grp-2588>"
        f"<EnhetOrganisasjonsnummer-datadef-18>{ORGNR}</EnhetOrganisasjonsnummer-datadef-18>"
        "<AksjeType-datadef-17659>01</AksjeType-datadef-17659>"
        "<Inntektsar-datadef-692>2026</Inntektsar-datadef-692>"
        "</Selskap-grp-2588></GenerellInformasjon-grp-2587>"
        "<Selskapsopplysninger-grp-2589>"
        "<AksjekapitalISINAksjetypeFjoraret-datadef-17663>30000</AksjekapitalISINAksjetypeFjoraret-datadef-17663>"
        "<AksjeMvPalydendeFjoraret-datadef-23944>30</AksjeMvPalydendeFjoraret-datadef-23944>"
        "<AksjerMvAntallFjoraret-datadef-29166>1000</AksjerMvAntallFjoraret-datadef-29166>"
        "<AksjekapitalInnbetaltFjoraret-datadef-8020>30000</AksjekapitalInnbetaltFjoraret-datadef-8020>"
        "<AksjeOverkursISINAksjetypeFjoraret-datadef-17662>0</AksjeOverkursISINAksjetypeFjoraret-datadef-17662>"
        "</Selskapsopplysninger-grp-2589></Skjema>"
    )
    prefill_under = (
        '<Skjema skjemanummer="923" blankettnummer="RF-1086-U">'
        "<SelskapsOgAksjonaropplysninger-grp-3987><NorskUtenlandskAksjonar-grp-3988>"
        f"<AksjonarFodselsnummer-datadef-1156>{TENOR_FNR_2}</AksjonarFodselsnummer-datadef-1156>"
        "<AksjonarNavn-datadef-1153>Ola Prefill</AksjonarNavn-datadef-1153>"
        "<AksjonarLandkode-datadef-17740>NO</AksjonarLandkode-datadef-17740>"
        "</NorskUtenlandskAksjonar-grp-3988></SelskapsOgAksjonaropplysninger-grp-3987>"
        "<AntallAksjerUtbytteOgTilbakebetalingAvTidligereInnbetaltKapit-grp-3990>"
        "<AntallAksjerPerAksjonar-grp-3989>"
        "<AksjerAntallFjoraret-datadef-29168>1000</AksjerAntallFjoraret-datadef-29168>"
        "</AntallAksjerPerAksjonar-grp-3989>"
        "</AntallAksjerUtbytteOgTilbakebetalingAvTidligereInnbetaltKapit-grp-3990></Skjema>"
    )

    def prefill_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(f"/prefill/2026/{ORGNR}")
        return httpx.Response(
            200,
            json={
                "totalItems": 2,
                "totalPages": 1,
                "currentPage": 0,
                "oppgaveStatus": "GODKJENT",
                "dokumenter": [
                    {"aksjeklasse": "01", "namespace": "", "dokument": prefill_hoved},
                    {"aksjeklasse": "01", "namespace": "", "dokument": prefill_under},
                ],
            },
        )

    monkeypatch.setattr(
        ske_aksjonaer,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(prefill_handler)),
    )

    # An agent key calling POST /aksjonaer/prefill is held on /godkjenning/ (202).
    key_hdr = agent_header(client, cid)
    held = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/aksjonaer/prefill",
        json={"aar": 2026},
        headers=key_hdr,
    )
    assert held.status_code == 202, held.text
    approval_id = held.json()["approval_id"]

    # Approving executes the prefill cleanly.
    approved = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/godkjenninger/{approval_id}/godkjenn",
        json={"files": []},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "done"
    assert approved.json()["result_status"] == 200

    overview = client.get(f"/api/v1/companies/{cid}/aksjonaer", params={"aar": 2026}).json()
    assert not overview["blocked"]
    assert len(overview["classes"]) == 1
    assert overview["classes"][0]["opening"]["shares"] == 1000
    assert overview["classes"][0]["closing"]["share_capital"] == "30000.00"
    assert len(overview["shareholders"]) == 1
    assert overview["shareholders"][0]["name"] == "Ola Prefill"


def test_purge_expired_deletes_archived_rf1086_documents(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a deleted company passes its 5-year retention, archived RF-1086 XMLs are purged."""
    _configure_altinn_and_ske(monkeypatch)
    company = _as_company(client)
    cid = company["id"]
    _seed_register(client, cid)
    _approve_systembruker(client, cid, monkeypatch)

    hoved_uuid = str(uuid.uuid4())

    def ok_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/1086H"):
            return httpx.Response(200, json={"hovedskjemaId": hoved_uuid})
        if request.url.path.endswith("/1086U"):
            return httpx.Response(200, content=b"")
        if request.url.path.endswith("/bekreft"):
            return httpx.Response(
                200,
                json={
                    "oppgavegiversLeveranseReferanse": "REF-PURGE",
                    "dialogId": str(uuid.uuid4()),
                    "forsendelseId": str(uuid.uuid4()),
                },
            )
        return httpx.Response(404)

    monkeypatch.setattr(
        ske_aksjonaer,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(ok_handler)),
    )

    sent = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/aksjonaer/send",
        json={"aar": 2026, "bekreftet": True},
    )
    assert sent.status_code == 200, sent.text

    # Close the company (enters 5-year retention).
    closed = write(
        client,
        "POST",
        f"/api/v1/companies/{cid}/sletting",
        json={"company_name": "Fjord Tech AS", "password": "korrekt-hest-batteri-stift"},
    )
    assert closed.status_code == 200, closed.text
    retain_until = date.fromisoformat(closed.json()["retain_until"])

    # Run purge_expired the day after retention ends.
    from myntos.api.deps import get_document_storage
    from myntos.db.session import get_session

    override_db = client.app.dependency_overrides[get_session]  # type: ignore[attr-defined]
    storage = client.app.dependency_overrides[get_document_storage]()  # type: ignore[attr-defined]

    async def run_purge() -> tuple[list[str], list[int]]:
        import contextlib

        sessions = override_db()
        session = await sessions.__anext__()
        keys_before = await closure._stored_keys(session, cid)
        purged = await closure.purge_expired(
            session, storage, today=date(retain_until.year + 1, 1, 2)
        )
        with contextlib.suppress(StopAsyncIteration):
            await sessions.__anext__()
        return keys_before, purged

    assert client.portal is not None
    keys_before, purged = client.portal.call(run_purge)
    xml_keys = [k for k in keys_before if k.endswith(".xml")]
    assert len(xml_keys) == 3  # hovedskjema + 2 underskjemaer
    assert purged == [cid]
