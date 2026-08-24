"""The Altinn token exchange and the instance lifecycle, with Altinn stubbed.

What these tests are really about is the set of steps that have no error message
of their own. Altinn answers 401 for a token that was never exchanged, 400 for a
dataType it does not know, and nothing at all for a submission that stopped after
the first ``process/next`` — the instance just sits there, filled and filed with
nobody. So the stub records the exact calls made, and the tests assert the
sequence rather than the return value.

The MVA and skattemelding clients are exercised through the same stub, because
the thing worth checking about them is which token each step is given: a machine
token where a machine may act, and a person's where the state requires one.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest

from myntos.integrations.altinn import instances as instances_module
from myntos.integrations.altinn import tokens as tokens_module
from myntos.integrations.altinn.instances import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS,
    AltinnInstanceError,
    AppInstanceClient,
    InstanceRef,
)
from myntos.integrations.altinn.tokens import (
    AltinnAuthError,
    MachineTokenSource,
    exchange_id_porten_token,
    exchange_token,
)
from myntos.integrations.skatteetaten import client as skattemelding_module
from myntos.integrations.skatteetaten import mva as mva_module
from myntos.integrations.skatteetaten.client import (
    ATTACHMENT_DATA_TYPE,
    ENVELOPE_DATA_TYPE,
    SkatteetatenError,
    SkatteetatenNotConfiguredError,
    SkattemeldingClient,
)
from myntos.integrations.skatteetaten.mva import (
    FEEDBACK_DATA_TYPES,
    MELDING_DATA_TYPE,
    METADATA_DATA_TYPE,
    MvaMeldingClient,
)

APP_URL = "https://skd.apps.tt02.altinn.no/skd/formueinntekt-skattemelding-v2"
MVA_APP_URL = "https://skd.apps.tt02.altinn.no/skd/mva-melding-innsending-etm2"
SKATTEETATEN_URL = "https://api-test.sits.no"
# The same API on the ID-porten plane. Note "idporten-api-test" here against
# "idporten-api-sbstest" for MVA below: three hosts, no shared pattern.
IDPORTEN_SKATTEETATEN_URL = "https://idporten-api-test.sits.no"
MVA_VALIDATION_URL = "https://idporten-api-sbstest.sits.no"

PARTY_ID = 50001337
INSTANCE_GUID = "6c9e1b1a-3a3c-4a1e-9b3f-2f0a7d5c8e11"
INSTANCE_ID = f"{PARTY_ID}/{INSTANCE_GUID}"
ORGNR = "811074582"
PID = "01015450301"  # Synthetic, Tenor-style. Never a real fødselsnummer.


def _jwt(*, minutes: int = 5) -> str:
    """An unsigned-for-our-purposes Altinn token: only ``exp`` is ever read."""
    expires_at = datetime.now(UTC) + timedelta(minutes=minutes)
    return jwt.encode({"exp": int(expires_at.timestamp())}, "x" * 32)


class StubAltinn:
    """Altinn's exchange endpoint and one app's instance API."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.tokens: list[str | None] = []
        self.uploads: list[tuple[str, str, bytes, str | None]] = []
        self.process_calls = 0
        self.exchange_status = httpx.codes.OK
        self.exchange_quoted = True
        self.data_elements: list[dict[str, Any]] = []
        self.downloads: dict[str, bytes] = {}

    def instance_body(self, *, ended: bool = False) -> dict[str, Any]:
        return {
            "id": INSTANCE_ID,
            "data": self.data_elements,
            "process": (
                {"ended": datetime.now(UTC).isoformat()}
                if ended
                else {"currentTask": {"elementId": "Task_1"}}
            ),
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        self.calls.append((method, path))
        authorization = request.headers.get("authorization")
        self.tokens.append(authorization.removeprefix("Bearer ") if authorization else None)

        if "/authentication/api/v1/exchange/" in path:
            if self.exchange_status != httpx.codes.OK:
                return httpx.Response(self.exchange_status, text="no")
            token = _jwt()
            body = f'"{token}"' if self.exchange_quoted else token
            return httpx.Response(200, text=body)

        if method == "POST" and path.endswith("/instances"):
            return httpx.Response(201, json=self.instance_body())

        if method == "POST" and path.endswith("/data"):
            data_type = request.url.params["dataType"]
            element = {
                "id": f"element-{len(self.uploads) + 1}",
                "dataType": data_type,
                "contentType": request.headers.get("content-type"),
                "size": len(request.content),
                "filename": None,
            }
            self.uploads.append(
                (
                    data_type,
                    request.headers.get("content-type", ""),
                    request.content,
                    request.headers.get("content-disposition"),
                )
            )
            self.data_elements.append(element)
            return httpx.Response(201, json=element)

        if method == "PUT" and path.endswith("/process/next"):
            self.process_calls += 1
            # The second call is the one that submits, and only then does the
            # process end — which is how a filed melding is told from a filled one.
            return httpx.Response(200, json=self.instance_body(ended=self.process_calls >= 2))

        if method == "GET" and path.endswith(f"/instances/{INSTANCE_ID}"):
            return httpx.Response(200, json=self.instance_body(ended=self.process_calls >= 2))

        if method == "GET" and "/data/" in path:
            element_id = path.rsplit("/", 1)[-1]
            return httpx.Response(200, content=self.downloads.get(element_id, b""))

        raise AssertionError(f"unexpected {method} {request.url}")


@pytest.fixture
def altinn(monkeypatch: pytest.MonkeyPatch) -> Iterator[StubAltinn]:
    stub = StubAltinn()

    def stub_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(stub))

    for module in (tokens_module, instances_module, skattemelding_module, mva_module):
        monkeypatch.setattr(module, "_client", stub_client)
    yield stub


class StubTokenProvider:
    """A Maskinporten provider that hands out a token and counts requests."""

    def __init__(self) -> None:
        self.requests: list[tuple[tuple[str, ...], str | None]] = []

    async def get_access_token(self, scopes: Any, *, consumer_org: str | None = None) -> str:
        self.requests.append((tuple(scopes), consumer_org))
        return "machine-token"


# --- The exchange -----------------------------------------------------------------


async def test_a_maskinporten_token_is_exchanged_for_an_altinn_token(
    altinn: StubAltinn,
) -> None:
    token = await exchange_token("machine-token", plane="maskinporten")
    assert token.is_usable()
    assert altinn.calls[0][1].endswith("/authentication/api/v1/exchange/maskinporten")
    assert altinn.tokens[0] == "machine-token"


async def test_an_id_porten_token_uses_the_id_porten_path(altinn: StubAltinn) -> None:
    """A different endpoint, because it is a different claim about who is acting."""
    await exchange_id_porten_token("person-token")
    assert altinn.calls[0][1].endswith("/authentication/api/v1/exchange/id-porten")


async def test_the_token_is_read_whether_or_not_altinn_quotes_it(
    altinn: StubAltinn,
) -> None:
    """Altinn returns a bare JSON string; it has also returned it unquoted."""
    altinn.exchange_quoted = False
    token = await exchange_token("machine-token", plane="maskinporten")
    assert token.value.count(".") == 2


async def test_a_refused_exchange_is_an_auth_error(altinn: StubAltinn) -> None:
    altinn.exchange_status = httpx.codes.UNAUTHORIZED
    with pytest.raises(AltinnAuthError):
        await exchange_token("machine-token", plane="maskinporten")


async def test_the_machine_token_is_exchanged_once_and_then_reused(
    altinn: StubAltinn,
) -> None:
    provider = StubTokenProvider()
    source = MachineTokenSource(provider)
    first = await source.get_token()
    second = await source.get_token()
    assert first == second
    assert len(provider.requests) == 1
    # The instance scopes, not the serviceowner family.
    assert provider.requests[0][0] == ("altinn:instances.read", "altinn:instances.write")


async def test_a_delegated_token_is_kept_apart_from_the_undelegated_one(
    altinn: StubAltinn,
) -> None:
    provider = StubTokenProvider()
    source = MachineTokenSource(provider)
    await source.get_token()
    await source.get_token(consumer_org=ORGNR)
    assert [organization for _, organization in provider.requests] == [None, ORGNR]


# --- The instance lifecycle -------------------------------------------------------


def test_an_instance_id_is_a_party_and_a_guid_together() -> None:
    ref = InstanceRef.parse(INSTANCE_ID)
    assert ref.party_id == PARTY_ID
    assert ref.instance_guid == INSTANCE_GUID
    with pytest.raises(AltinnInstanceError):
        InstanceRef.parse("not-an-instance-id")


async def test_an_instance_is_owned_by_exactly_one_of_the_two_numbers(
    altinn: StubAltinn,
) -> None:
    """A personlig næringsdrivende files on the fødselsnummer, not the orgnr.

    Accepting both would let a caller file a correct melding against the wrong
    taxpayer, which is not a thing any error message would catch.
    """
    app = AppInstanceClient(APP_URL)
    with pytest.raises(AltinnInstanceError, match="exactly one"):
        await app.create_instance(token="t", organisation_number=ORGNR, person_number=PID)
    with pytest.raises(AltinnInstanceError, match="exactly one"):
        await app.create_instance(token="t")


async def test_submission_is_two_process_next_calls(altinn: StubAltinn) -> None:
    """One completes the filling step; the second is the one that submits."""
    app = AppInstanceClient(APP_URL)
    instance = await app.complete_and_submit(InstanceRef.parse(INSTANCE_ID), token="altinn-token")
    assert altinn.process_calls == 2
    assert instance.process_ended


async def test_attachment_limits_are_checked_before_anything_is_uploaded(
    altinn: StubAltinn,
) -> None:
    """A set that cannot succeed should not half-succeed.

    Altinn rejects duplicate filenames and files over 25 MB, and caps a melding
    at 57 attachments. Discovering that at file forty leaves an instance someone
    has to go and understand.
    """
    app = AppInstanceClient(APP_URL)
    ref = InstanceRef.parse(INSTANCE_ID)

    with pytest.raises(AltinnInstanceError, match="duplicate"):
        await app.upload_attachments(
            ref,
            token="t",
            data_type="vedlegg",
            files=[("bilag.pdf", b"a", "application/pdf")] * 2,
        )
    assert altinn.uploads == []

    with pytest.raises(AltinnInstanceError, match=str(MAX_ATTACHMENTS)):
        await app.upload_attachments(
            ref,
            token="t",
            data_type="vedlegg",
            files=[(f"{n}.pdf", b"a", "application/pdf") for n in range(MAX_ATTACHMENTS + 1)],
        )
    assert altinn.uploads == []

    with pytest.raises(AltinnInstanceError, match="limit"):
        await app.upload_attachments(
            ref,
            token="t",
            data_type="vedlegg",
            files=[("big.pdf", b"x" * (MAX_ATTACHMENT_BYTES + 1), "application/pdf")],
        )
    assert altinn.uploads == []


async def test_process_next_keeps_the_instance_reference_it_was_given(
    altinn: StubAltinn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Some app versions answer with the process state and no instance id.

    Losing track of the instance at the submit step would be the worst possible
    moment for it, so the reference already held is used instead.
    """

    def process_only(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"process": {"currentTask": {"elementId": "Task_2"}}})

    monkeypatch.setattr(
        instances_module,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(process_only)),
    )
    ref = InstanceRef.parse(INSTANCE_ID)
    instance = await AppInstanceClient(APP_URL).process_next(ref, token="t")
    assert instance.ref == ref


# --- Skattemelding ----------------------------------------------------------------


def _skattemelding(provider: StubTokenProvider) -> SkattemeldingClient:
    return SkattemeldingClient(
        token_provider=provider,
        base_url=SKATTEETATEN_URL,
        app=AppInstanceClient(APP_URL),
    )


class StubSkatteetaten:
    """Skatteetaten's REST API for the skattemelding."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.tokens: list[str | None] = []
        self.status = httpx.codes.OK

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        authorization = request.headers.get("authorization", "")
        self.tokens.append(authorization.removeprefix("Bearer "))
        if self.status != httpx.codes.OK:
            return httpx.Response(self.status, text="<feil/>")
        return httpx.Response(
            200, content=b"<skattemelding/>", headers={"content-type": "application/xml"}
        )


@pytest.fixture
def skatteetaten(monkeypatch: pytest.MonkeyPatch) -> Iterator[StubSkatteetaten]:
    stub = StubSkatteetaten()
    monkeypatch.setattr(
        skattemelding_module,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(stub)),
    )
    yield stub


async def test_fetching_a_skattemelding_uses_a_maskinporten_token(
    skatteetaten: StubSkatteetaten,
) -> None:
    provider = StubTokenProvider()
    content = await _skattemelding(provider).fetch(inntektsaar=2025, identifier=PID)
    assert content == b"<skattemelding/>"
    assert skatteetaten.calls == [("GET", f"/api/skattemelding/v2/2025/{PID}")]
    assert skatteetaten.tokens == ["machine-token"]
    # The skattemelding scope, and only it.
    assert provider.requests[0][0] == ("skatteetaten:formueinntekt/skattemelding",)


async def test_the_draft_and_the_assessed_copy_have_their_own_paths(
    skatteetaten: StubSkatteetaten,
) -> None:
    client = _skattemelding(StubTokenProvider())
    await client.fetch(inntektsaar=2025, identifier=PID, variant="utkast")
    await client.fetch(inntektsaar=2025, identifier=PID, variant="fastsatt")
    assert [path for _, path in skatteetaten.calls] == [
        f"/api/skattemelding/v2/utkast/2025/{PID}",
        f"/api/skattemelding/v2/fastsatt/2025/{PID}",
    ]


async def test_validation_keeps_the_whole_answer(skatteetaten: StubSkatteetaten) -> None:
    """It carries beregnet skatt and the myndighetsfastsatt locks, not a verdict."""
    result = await _skattemelding(StubTokenProvider()).validate(
        inntektsaar=2025, identifier=PID, payload=b"<utkast/>"
    )
    assert result.content == b"<skattemelding/>"
    assert skatteetaten.calls[0][1] == f"/api/skattemelding/v2/valider/2025/{PID}"


async def test_a_rejected_call_reports_the_status_and_the_body(
    skatteetaten: StubSkatteetaten,
) -> None:
    skatteetaten.status = httpx.codes.BAD_REQUEST
    with pytest.raises(SkatteetatenError, match="400"):
        await _skattemelding(StubTokenProvider()).fetch(inntektsaar=2025, identifier=PID)


async def test_the_envelope_is_uploaded_as_text_xml(altinn: StubAltinn) -> None:
    """``text/xml``, not ``application/xml`` — Skatteetaten is explicit about it."""
    client = _skattemelding(StubTokenProvider())
    ref = InstanceRef.parse(INSTANCE_ID)
    await client.upload_envelope(ref, altinn_token="altinn-token", envelope=b"<envelope/>")
    data_type, content_type, content, _ = altinn.uploads[0]
    assert data_type == ENVELOPE_DATA_TYPE
    assert content_type == "text/xml"
    assert content == b"<envelope/>"


async def test_the_income_year_travels_with_the_instance(altinn: StubAltinn) -> None:
    """The app keys the melding on it, and it cannot be corrected afterwards."""
    captured: dict[str, Any] = {}

    def recording(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/instances"):
            captured.update(json.loads(request.content))
            return httpx.Response(201, json={"id": INSTANCE_ID, "data": [], "process": {}})
        raise AssertionError("unexpected request")

    client = SkattemeldingClient(
        token_provider=StubTokenProvider(),
        base_url=SKATTEETATEN_URL,
        app=AppInstanceClient(APP_URL),
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            instances_module,
            "_client",
            lambda: httpx.AsyncClient(transport=httpx.MockTransport(recording)),
        )
        await client.create_instance(
            altinn_token="altinn-token", inntektsaar=2025, person_number=PID
        )
    assert captured["dataValues"] == {"inntektsaar": "2025"}
    assert captured["instanceOwner"] == {"personNumber": PID}


async def test_a_person_authenticated_call_goes_to_the_id_porten_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same endpoint, other host: Skatteetaten splits them by auth plane.

    Presenting a person's token to the Maskinporten host (or the reverse) is
    refused by a service with no reason to say which of the two was wrong, so the
    host follows from the token rather than from a separate setting.
    """
    seen: dict[str, Any] = {}

    def recording(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=b"<skattemelding/>")

    monkeypatch.setattr(
        skattemelding_module,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(recording)),
    )
    provider = StubTokenProvider()
    client = SkattemeldingClient(
        token_provider=provider,
        base_url=SKATTEETATEN_URL,
        idporten_base_url=IDPORTEN_SKATTEETATEN_URL,
        app=AppInstanceClient(APP_URL),
    )
    await client.fetch(inntektsaar=2025, identifier=PID, id_porten_token="person-token")
    assert seen["host"] == "idporten-api-test.sits.no"
    assert seen["auth"] == "Bearer person-token"
    # And no machine token was minted for a call a person authenticated.
    assert provider.requests == []


async def test_a_person_authenticated_call_needs_that_host_configured() -> None:
    """Falling back to the machine host would fail in a way nothing explains."""
    client = SkattemeldingClient(
        token_provider=StubTokenProvider(),
        base_url=SKATTEETATEN_URL,
        app=AppInstanceClient(APP_URL),
    )
    with pytest.raises(SkatteetatenNotConfiguredError, match="IDPORTEN_API_BASE_URL"):
        await client.fetch(inntektsaar=2025, identifier=PID, id_porten_token="person-token")


async def test_skattemelding_attachments_use_the_vedlegg_data_type(
    altinn: StubAltinn,
) -> None:
    await _skattemelding(StubTokenProvider()).upload_attachments(
        InstanceRef.parse(INSTANCE_ID),
        altinn_token="altinn-token",
        files=[("bilag.pdf", b"%PDF-1.7", "application/pdf")],
    )
    data_type, _, _, disposition = altinn.uploads[0]
    assert data_type == ATTACHMENT_DATA_TYPE
    assert disposition == 'attachment; filename="bilag.pdf"'


async def test_submitting_a_skattemelding_uses_the_person_s_token(
    altinn: StubAltinn,
) -> None:
    """The argument is named for what Skatteetaten requires it to be.

    A machine token here is not a shortcut that happens to work — Altinn refuses
    the submission — so the signature is where the rule stays visible.
    """
    client = _skattemelding(StubTokenProvider())
    await client.submit(InstanceRef.parse(INSTANCE_ID), id_porten_altinn_token="person-token")
    assert altinn.process_calls == 2
    assert set(altinn.tokens) == {"person-token"}


# --- MVA-melding ------------------------------------------------------------------


def _mva() -> MvaMeldingClient:
    return MvaMeldingClient(
        validation_base_url=MVA_VALIDATION_URL, app=AppInstanceClient(MVA_APP_URL)
    )


async def test_the_mva_validation_api_takes_the_id_porten_token_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skatteetaten's own endpoint, so no Altinn exchange is involved."""
    seen: dict[str, Any] = {}

    def validating(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["content_type"] = request.headers.get("content-type")
        return httpx.Response(200, content=b"<valideringsresultat/>")

    monkeypatch.setattr(
        mva_module,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(validating)),
    )
    result = await _mva().validate(b"<mvaMeldingDto/>", id_porten_token="person-token")
    assert result.content == b"<valideringsresultat/>"
    assert seen["path"] == "/api/mva/grensesnittstoette/mva-melding/valider"
    assert seen["auth"] == "Bearer person-token"
    assert seen["content_type"] == "text/xml"


async def test_the_metadata_goes_up_before_the_melding(altinn: StubAltinn) -> None:
    """The app reads the melding in the light of who is filing, for which period."""
    await _mva().upload_melding(
        InstanceRef.parse(INSTANCE_ID),
        altinn_token="altinn-token",
        metadata=b"<mvaMeldingInnsending/>",
        melding=b"<mvaMeldingDto/>",
    )
    assert [data_type for data_type, _, _, _ in altinn.uploads] == [
        METADATA_DATA_TYPE,
        MELDING_DATA_TYPE,
    ]


async def test_the_three_receipt_documents_are_fetched_together(altinn: StubAltinn) -> None:
    """A submitted melding whose receipt was never fetched documents nothing.

    The validation result, the payment information (it holds the KID) and the
    kvittering PDF are what evidences the filing afterwards, so they are
    retrieved as a set and the caller is told when the set is complete.
    """
    for index, data_type in enumerate(FEEDBACK_DATA_TYPES, start=1):
        altinn.data_elements.append(
            {
                "id": f"feedback-{index}",
                "dataType": data_type,
                "contentType": "application/xml",
                "size": 3,
                "filename": None,
            }
        )
        altinn.downloads[f"feedback-{index}"] = data_type.encode()

    feedback = await _mva().feedback(InstanceRef.parse(INSTANCE_ID), altinn_token="altinn-token")
    assert feedback.is_complete
    assert feedback.documents["betalingsinformasjon"] == b"betalingsinformasjon"
    assert feedback.documents["kvittering"] == b"kvittering"


async def test_an_empty_feedback_right_after_submitting_is_not_a_failure(
    altinn: StubAltinn,
) -> None:
    """Skatteetaten processes the submission after Altinn accepts it."""
    feedback = await _mva().feedback(InstanceRef.parse(INSTANCE_ID), altinn_token="altinn-token")
    assert not feedback.is_complete
    assert feedback.documents == {}


# --- The test-environment exchange parameter --------------------------------------


async def test_a_test_platform_exchange_carries_test_true(altinn: StubAltinn) -> None:
    """Altinn requires it in test, and answers 403 without it saying nothing.

    Configured platforms other than production are test platforms, so this
    follows from the URL rather than from a flag someone has to remember to set
    when they move environments.
    """
    seen: dict[str, Any] = {}

    def recording(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, text=f'"{_jwt()}"')

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            tokens_module,
            "_client",
            lambda: httpx.AsyncClient(transport=httpx.MockTransport(recording)),
        )
        await exchange_token("person-token", plane="id-porten")
    assert seen["params"] == {"test": "true"}


async def test_production_exchange_carries_no_test_parameter() -> None:
    """The parameter is a test-environment thing and must not leak into production."""
    seen: dict[str, Any] = {}

    def recording(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        seen["host"] = request.url.host
        return httpx.Response(200, text=f'"{_jwt()}"')

    class ProductionSettings:
        altinn_platform_base_url = "https://platform.altinn.no"

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            tokens_module,
            "_client",
            lambda: httpx.AsyncClient(transport=httpx.MockTransport(recording)),
        )
        patch.setattr(tokens_module, "get_settings", ProductionSettings)
        await exchange_token("machine-token", plane="maskinporten")
    assert seen["host"] == "platform.altinn.no"
    assert seen["params"] == {}


async def test_a_refused_person_exchange_names_the_likely_cause(
    altinn: StubAltinn,
) -> None:
    """403 here says nothing about scopes, and a missing scope is the cause.

    An ID-porten client with no Altinn scope issues a token carrying only
    ``openid`` and no audience, so Altinn has nothing to authorize and answers
    403. The error names ``altinn:enduser`` because the status code names
    nothing, and a reader who does not know that spends an afternoon on the
    token instead of on the client registration.
    """
    altinn.exchange_status = httpx.codes.FORBIDDEN
    with pytest.raises(AltinnAuthError, match="altinn:enduser"):
        await exchange_id_porten_token("person-token")
