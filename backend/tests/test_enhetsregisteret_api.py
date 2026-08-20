"""Looking a company up in Enhetsregisteret over HTTP.

Every test here answers for Brønnøysundregistrene with a stub transport. The
real register is somebody else's uptime, and a suite that reaches it would fail
for reasons that have nothing to do with this code.
"""

from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from myntos.integrations import brreg
from tests.conftest import register

# Equinor's, because it is real and its check digit is therefore correct.
ORGNR = "923609016"

UNIT: dict[str, Any] = {
    "organisasjonsnummer": ORGNR,
    "navn": "EQUINOR ASA",
    "organisasjonsform": {"kode": "ASA", "beskrivelse": "Allmennaksjeselskap"},
    "forretningsadresse": {
        "adresse": ["Forusbeen 50"],
        "postnummer": "4035",
        "poststed": "STAVANGER",
    },
    "registrertIMvaregisteret": True,
    "registrertIForetaksregisteret": True,
}

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def brreg_answers(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[Handler], list[str]]]:
    """Install a stub register, and record the paths it was asked for."""
    requested: list[str] = []

    def install(handler: Handler) -> list[str]:
        def recording(request: httpx.Request) -> httpx.Response:
            requested.append(request.url.path)
            return handler(request)

        monkeypatch.setattr(
            brreg,
            "_client",
            lambda: httpx.AsyncClient(transport=httpx.MockTransport(recording)),
        )
        return requested

    yield install


def signed_in(client: TestClient) -> TestClient:
    register(client)
    return client


def test_lookup_returns_name_and_address(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    brreg_answers(lambda _request: httpx.Response(200, json=UNIT))

    response = signed_in(client).get(f"/api/v1/enhetsregisteret/{ORGNR}")

    assert response.status_code == 200
    assert response.json() == {
        "orgnr": ORGNR,
        "name": "EQUINOR ASA",
        "organisation_form": "ASA",
        "organisation_form_description": "Allmennaksjeselskap",
        # The register gives the street address as a list of lines.
        "address_line": "Forusbeen 50",
        "postal_code": "4035",
        "city": "STAVANGER",
        "registered_in_vat_register": True,
        "registered_in_foretaksregisteret": True,
        "bankrupt": False,
        "under_liquidation": False,
    }


def test_lookup_requires_a_session(client: TestClient) -> None:
    # The register is open data; this endpoint is not, so that Myntos does not
    # become an anonymous proxy in front of it.
    assert client.get(f"/api/v1/enhetsregisteret/{ORGNR}").status_code == 401


def test_invalid_orgnr_is_rejected_without_asking_the_register(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    requested = brreg_answers(lambda _request: httpx.Response(200, json=UNIT))

    # Right length, wrong check digit — a typo, not an unknown company.
    response = signed_in(client).get("/api/v1/enhetsregisteret/923609011")

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_orgnr"
    assert requested == []


def test_underenhet_is_found_after_the_hovedenhet_lookup_misses(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/underenheter/" in request.url.path:
            return httpx.Response(200, json=UNIT)
        return httpx.Response(404)

    requested = brreg_answers(handler)

    response = signed_in(client).get(f"/api/v1/enhetsregisteret/{ORGNR}")

    assert response.status_code == 200
    assert response.json()["name"] == "EQUINOR ASA"
    assert [path.rsplit("/", 2)[1] for path in requested] == ["enheter", "underenheter"]


def test_unknown_number_is_a_404(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    brreg_answers(lambda _request: httpx.Response(404))

    response = signed_in(client).get(f"/api/v1/enhetsregisteret/{ORGNR}")

    assert response.status_code == 404
    assert response.json()["code"] == "unit_not_found"


def test_deleted_unit_is_reported_as_deleted(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    # Distinct from "unknown": the number is right, the company is gone, and
    # telling the two apart saves the user hunting for a typo that is not there.
    brreg_answers(lambda _request: httpx.Response(410, json={"slettedato": "2019-01-01"}))

    response = signed_in(client).get(f"/api/v1/enhetsregisteret/{ORGNR}")

    assert response.status_code == 410
    assert response.json()["code"] == "unit_deleted"


def test_unreachable_register_is_a_bad_gateway_not_a_server_error(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nede", request=request)

    brreg_answers(handler)

    response = signed_in(client).get(f"/api/v1/enhetsregisteret/{ORGNR}")

    # 502, not 500: nothing is wrong here, and the frontend's job is to let the
    # user type the name in rather than to report a Myntos fault.
    assert response.status_code == 502
    assert response.json()["code"] == "register_unavailable"
