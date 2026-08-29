"""The list of companies a person may report for, from Altinn.

Two halves, and they fail in different ways.

The integration half is a parser against a shape nobody controls: Altinn nests
subunits inside their main unit, includes parties the person cannot touch so a
tree can be drawn, and identifies person parties by fødselsnummer. Getting any
of those wrong produces a plausible-looking list that is quietly wrong — an
offered company that answers 403 at submission time, or a missing subunit the
accountant was actually engaged for.

The endpoint half is about what a browser is allowed to be told and what the
government login is allowed to be spent on. Reading who you may act for is not
acting, so the token survives the call; and a fødselsnummer has no business
leaving the server for a picker of companies.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import httpx
import jwt
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from myntos.api.v1 import skatt as skatt_endpoints
from myntos.auth.idporten.client import GovernmentLogin
from myntos.core.clock import utc_now
from myntos.core.config import get_settings
from myntos.integrations.altinn import authorized_parties as parties_module
from myntos.integrations.altinn import tokens as tokens_module
from myntos.integrations.altinn.authorized_parties import (
    SCOPE,
    fetch_authorized_parties,
    flatten,
)
from myntos.integrations.altinn.tokens import AltinnAuthError, AltinnError
from tests.conftest import register

KEY = Fernet.generate_key().decode()
PID = "08866995401"  # Synthetic. Never a real fødselsnummer.

# One main unit with a subunit, one company reached through an Altinn 2 role,
# one hierarchy placeholder the person has no rights on, one deleted company and
# one person party. Between them they cover every filter the endpoint applies.
ALTINN_PARTIES: list[dict[str, Any]] = [
    {
        "partyUuid": "00000000-0000-0000-0000-000000000001",
        "name": "Nordmann Consulting AS",
        "organizationNumber": "811074582",
        "personId": "",
        "partyId": 50001337,
        "type": "Organization",
        "unitType": "AS",
        "isDeleted": False,
        "onlyHierarchyElementWithNoAccess": False,
        "authorizedRoles": ["DAGL", "REGN"],
        "authorizedResources": [],
        "subunits": [
            {
                "partyUuid": "00000000-0000-0000-0000-000000000002",
                "name": "Nordmann Consulting AS avd Bergen",
                "organizationNumber": "910041156",
                "partyId": 50001338,
                "type": "Organization",
                "unitType": "BEDR",
                "isDeleted": False,
                "onlyHierarchyElementWithNoAccess": False,
                "authorizedRoles": ["REGN"],
                "subunits": [],
            }
        ],
    },
    {
        # The person is in this one's list only to hold the subunit below it.
        "partyUuid": "00000000-0000-0000-0000-000000000003",
        "name": "Holding uten tilgang AS",
        "organizationNumber": "999888777",
        "partyId": 50001339,
        "type": "Organization",
        "unitType": "AS",
        "isDeleted": False,
        "onlyHierarchyElementWithNoAccess": True,
        "authorizedRoles": [],
        "subunits": [
            {
                "partyUuid": "00000000-0000-0000-0000-000000000004",
                "name": "Datterselskapet AS",
                "organizationNumber": "811074590",
                "partyId": 50001340,
                "type": "Organization",
                "unitType": "AS",
                "isDeleted": False,
                "onlyHierarchyElementWithNoAccess": False,
                "authorizedRoles": ["DAGL"],
                "subunits": [],
            }
        ],
    },
    {
        "partyUuid": "00000000-0000-0000-0000-000000000005",
        "name": "Slettet Foretak AS",
        "organizationNumber": "811074604",
        "partyId": 50001341,
        "type": "Organization",
        "unitType": "AS",
        "isDeleted": True,
        "onlyHierarchyElementWithNoAccess": False,
        "authorizedRoles": ["DAGL"],
        "subunits": [],
    },
    {
        "partyUuid": "00000000-0000-0000-0000-000000000006",
        "name": "Kari Nordmann",
        "organizationNumber": "",
        "personId": PID,
        "partyId": 50001342,
        "type": "Person",
        "unitType": None,
        "isDeleted": False,
        "onlyHierarchyElementWithNoAccess": False,
        "authorizedRoles": [],
        "subunits": [],
    },
]


# --- The integration -------------------------------------------------------------


def _transport(handler: Any, monkeypatch: pytest.MonkeyPatch, module: Any = parties_module) -> None:
    monkeypatch.setattr(
        module,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_the_tree_comes_back_with_its_subunits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nesting is preserved, because the hierarchy is information."""
    _transport(lambda request: httpx.Response(200, json=ALTINN_PARTIES), monkeypatch)
    parties = await fetch_authorized_parties("altinn-token")

    assert [party.orgnr for party in parties] == [
        "811074582",
        "999888777",
        "811074604",
        None,
    ]
    main_unit = parties[0]
    assert main_unit.name == "Nordmann Consulting AS"
    assert main_unit.unit_type == "AS"
    assert main_unit.authorized_roles == ("DAGL", "REGN")
    assert [sub.orgnr for sub in main_unit.subunits] == ["910041156"]


async def test_the_call_asks_for_altinn_2_as_well(monkeypatch: pytest.MonkeyPatch) -> None:
    """A large share of Norwegian roles still live there.

    Asserted on the request rather than trusted to the default, because the
    symptom of getting it wrong is not an error: it is a shorter list, and the
    clients missing from it are the small old ones nobody would notice.
    """
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["includeAltinn2"] = request.url.params.get("includeAltinn2")
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json=[])

    _transport(handler, monkeypatch)
    await fetch_authorized_parties("altinn-token")

    assert seen["path"] == "/accessmanagement/api/v1/authorizedparties"
    assert seen["includeAltinn2"] == "true"
    assert seen["authorization"] == "Bearer altinn-token"


async def test_the_newer_paged_envelope_is_read_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """``{"data": [...]}`` as well as a bare array.

    The ``enduser`` API that is replacing this one answers with the envelope.
    Reading both means that migration is a URL, not a parser.
    """
    _transport(
        lambda request: httpx.Response(200, json={"links": {"next": None}, "data": ALTINN_PARTIES}),
        monkeypatch,
    )
    parties = await fetch_authorized_parties("altinn-token")
    assert parties[0].orgnr == "811074582"


async def test_an_answer_that_is_not_a_list_is_an_error_not_an_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-list answer is an error, not an empty list.

    They render as the same empty picker, and one of them is a bug that would
    otherwise be reported as "Altinn does not know about my clients".
    """
    _transport(lambda request: httpx.Response(200, json={"message": "hm"}), monkeypatch)
    with pytest.raises(AltinnError):
        await fetch_authorized_parties("altinn-token")


@pytest.mark.parametrize("code", [401, 403])
async def test_a_refusal_names_the_scope_and_the_token_plane(
    code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both codes arrive bare, and each has one likely cause.

    This is the whole reason the error message is long: the fix is either a
    checkbox in Samarbeidsportalen or the wrong token being passed, and the
    status code distinguishes neither.
    """
    _transport(lambda request: httpx.Response(code, text=""), monkeypatch)
    with pytest.raises(AltinnAuthError) as raised:
        await fetch_authorized_parties("altinn-token")
    assert SCOPE in str(raised.value)
    assert "IDPORTEN_SCOPES" in str(raised.value)


async def test_an_unreachable_altinn_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    _transport(handler, monkeypatch)
    with pytest.raises(AltinnError) as raised:
        await fetch_authorized_parties("altinn-token")
    assert "could not reach Altinn" in str(raised.value)


async def test_a_formatted_organisasjonsnummer_is_reduced_to_digits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It is looked up in Enhetsregisteret next, which wants nine digits."""
    _transport(
        lambda request: httpx.Response(
            200,
            json=[{"name": "Med mellomrom AS", "organizationNumber": "811 074 582", "partyId": 1}],
        ),
        monkeypatch,
    )
    parties = await fetch_authorized_parties("altinn-token")
    assert parties[0].orgnr == "811074582"


async def test_flatten_puts_a_main_unit_before_its_subunits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _transport(lambda request: httpx.Response(200, json=ALTINN_PARTIES), monkeypatch)
    parties = await fetch_authorized_parties("altinn-token")
    assert [party.party_id for party in flatten(parties)] == [
        50001337,
        50001338,
        50001339,
        50001340,
        50001341,
        50001342,
    ]


# --- The endpoint ----------------------------------------------------------------


class StubIdPorten:
    """ID-porten, as much of it as the login this endpoint needs touches."""

    def __init__(self) -> None:
        self.login = GovernmentLogin(
            access_token="government-token",
            expires_at=utc_now() + timedelta(minutes=30),
            pid=PID,
            acr="idporten-loa-high",
            subject="subject-1",
            refresh_token=None,
        )

    async def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        return f"https://test.idporten.no/authorize?state={state}"

    async def exchange_code(self, *, code: str, code_verifier: str, nonce: str) -> GovernmentLogin:
        return self.login


def _altinn_jwt() -> str:
    """An Altinn token in the only respect the exchange checks: its shape.

    Three dot-separated parts and a readable ``exp``. Nothing verifies the
    signature — it is a credential to present, not a claim to trust.
    """
    return jwt.encode({"exp": int((utc_now() + timedelta(minutes=5)).timestamp())}, "x" * 32)


class StubAltinn:
    """The exchange endpoint and the authorized-parties list, recording tokens."""

    def __init__(self) -> None:
        self.payload: Any = ALTINN_PARTIES
        self.status = httpx.codes.OK
        self.altinn_token = _altinn_jwt()
        self.tokens: list[str | None] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        authorization = request.headers.get("authorization")
        self.tokens.append(authorization.removeprefix("Bearer ") if authorization else None)
        if "/authentication/api/v1/exchange/" in request.url.path:
            return httpx.Response(200, text=f'"{self.altinn_token}"')
        if request.url.path == "/accessmanagement/api/v1/authorizedparties":
            if self.status != httpx.codes.OK:
                return httpx.Response(self.status, text="")
            return httpx.Response(200, json=self.payload)
        raise AssertionError(f"unexpected {request.method} {request.url}")


@pytest.fixture
def altinn(monkeypatch: pytest.MonkeyPatch) -> Iterator[StubAltinn]:
    """A configured government login, with ID-porten and Altinn both stubbed."""
    monkeypatch.setenv("GOVERNMENT_TOKEN_KEY", KEY)
    get_settings.cache_clear()
    monkeypatch.setattr(skatt_endpoints, "get_client", lambda: StubIdPorten())
    monkeypatch.setattr(skatt_endpoints, "is_configured", lambda *a, **k: True)
    stub = StubAltinn()
    _transport(stub, monkeypatch, parties_module)
    _transport(stub, monkeypatch, tokens_module)
    yield stub
    get_settings.cache_clear()


def _sign_in_to_the_government(client: TestClient) -> None:
    """Complete the ID-porten flow, so the endpoint has a login to spend."""
    response = client.get("/api/v1/skatt/idporten/start", follow_redirects=False)
    assert response.status_code == 303, response.text
    state = str(response.headers["location"]).split("state=")[1]
    response = client.get(
        f"/api/v1/skatt/idporten/callback?code=auth-code&state={state}",
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def test_the_list_needs_a_myntos_login(client: TestClient) -> None:
    assert client.get("/api/v1/skatt/altinn/authorized-parties").status_code == 401


def test_an_unconfigured_deployment_says_unavailable(client: TestClient) -> None:
    """503, the same as the login itself: the feature is absent, not refused."""
    register(client)
    response = client.get("/api/v1/skatt/altinn/authorized-parties")
    assert response.status_code == 503


def test_without_a_government_login_the_answer_is_not_a_401(
    client: TestClient, altinn: StubAltinn
) -> None:
    """409, because the caller *is* signed in to Myntos.

    A 401 here would send the frontend to the sign-in page it just came from,
    and the user would arrive already signed in with nothing explained.
    """
    register(client)
    response = client.get("/api/v1/skatt/altinn/authorized-parties")
    assert response.status_code == 409
    assert "ID-porten" in response.json()["detail"]


def test_the_companies_come_back_with_their_main_unit_named(
    client: TestClient, altinn: StubAltinn
) -> None:
    """The happy path, and every filter that shapes it.

    One assertion on the whole list rather than several on parts of it: what
    matters is exactly which parties are offered, and a test that checked only
    the ones present would pass while offering a deleted company too.
    """
    register(client)
    _sign_in_to_the_government(client)

    response = client.get("/api/v1/skatt/altinn/authorized-parties")
    assert response.status_code == 200, response.text
    assert response.json() == [
        {
            "orgnr": "811074582",
            "name": "Nordmann Consulting AS",
            "party_id": 50001337,
            "unit_type": "AS",
            "parent_orgnr": None,
            "roles": ["DAGL", "REGN"],
            "claimed": False,
        },
        {
            "orgnr": "910041156",
            "name": "Nordmann Consulting AS avd Bergen",
            "party_id": 50001338,
            "unit_type": "BEDR",
            "parent_orgnr": "811074582",
            "roles": ["REGN"],
            "claimed": False,
        },
        {
            "orgnr": "811074590",
            "name": "Datterselskapet AS",
            "party_id": 50001340,
            "unit_type": "AS",
            # The main unit is named even though the person has no rights on it:
            # it is where the subunit sits, which is what makes the subunit
            # recognisable in a list of forty.
            "parent_orgnr": "999888777",
            "roles": ["DAGL"],
            # Nobody has confirmed any of these, which is the ordinary state.
            # The flag turns true only once another organization has claimed the
            # orgnr — see test_confirmed_ownership.py.
            "claimed": False,
        },
    ]


def test_no_fodselsnummer_reaches_the_browser(client: TestClient, altinn: StubAltinn) -> None:
    """Person parties are dropped, and their identifier with them.

    Asserted on the raw body rather than the parsed one, because a field added
    to the response model later would leak through before anybody re-read it.
    """
    register(client)
    _sign_in_to_the_government(client)
    raw = client.get("/api/v1/skatt/altinn/authorized-parties").text
    assert PID not in raw
    assert "Kari Nordmann" not in raw


def test_reading_the_list_does_not_spend_the_login(client: TestClient, altinn: StubAltinn) -> None:
    """Looking at who you may act for is not acting.

    A login consumed here would send the person back through BankID before they
    could file the thing they just picked a company for.
    """
    register(client)
    _sign_in_to_the_government(client)

    assert client.get("/api/v1/skatt/altinn/authorized-parties").status_code == 200
    assert client.get("/api/v1/skatt/status").json()["logged_in"] is True
    # And again, on the same login.
    assert client.get("/api/v1/skatt/altinn/authorized-parties").status_code == 200


def test_the_person_s_token_is_the_one_exchanged(client: TestClient, altinn: StubAltinn) -> None:
    """Never the machine token: the list is built from a ``userId`` claim.

    A Maskinporten-derived token has none, and Altinn answers 401 without saying
    that is why.
    """
    register(client)
    _sign_in_to_the_government(client)
    client.get("/api/v1/skatt/altinn/authorized-parties")
    assert altinn.tokens == ["government-token", altinn.altinn_token]


def test_an_altinn_refusal_is_passed_on_with_its_reason(
    client: TestClient, altinn: StubAltinn
) -> None:
    """502 with the message that names the scope.

    The failure is ours to fix, not the user's, and the message is the only
    thing in the whole chain that says which piece of configuration is missing.
    """
    register(client)
    _sign_in_to_the_government(client)
    altinn.status = httpx.codes.FORBIDDEN

    response = client.get("/api/v1/skatt/altinn/authorized-parties")
    assert response.status_code == 502
    assert SCOPE in response.json()["detail"]
