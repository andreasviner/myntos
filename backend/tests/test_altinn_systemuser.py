"""Asking a customer to approve a system user.

The assertions worth having here are about ``externalRef``, which Altinn's
documentation calls optional and which is in fact part of the system user's
identity. Getting it wrong costs a 404 that names nothing
(``invalid_altinn_customer_configuration``) at token time, long after the
mistake.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
import pytest

from myntos.core.config import Settings
from myntos.integrations.altinn import systemuser
from myntos.integrations.altinn.systemuser import (
    SystemUserError,
    get_request,
    request_system_user,
    requests_for_system,
)

CUSTOMER = "314067630"
REF = "myntos-1"
RESOURCE = "app_skd_formueinntekt-skattemelding-v2"
REQUEST_ID = "ffa04a65-397d-4df9-ad64-4425ef0d4a45"


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "altinn_vendor_orgnr": "938193045",
        "altinn_system_name": "myntos",
        "altinn_system_redirect_url": "https://myntos.no/skatt/systembruker/godkjent",
        "altinn_platform_base_url": "https://platform.tt02.altinn.no",
        "maskinporten_client_id": "f8293b48-86b5-466c-9823-526b1b619434",
    }
    values.update(overrides)
    return Settings(**values)


def _transport(handler: Any) -> Any:
    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _body(status: str = "New", *, external_ref: str = REF) -> dict[str, Any]:
    return {
        "id": REQUEST_ID,
        "status": status,
        "partyOrgNo": CUSTOMER,
        "systemId": "938193045_myntos",
        "externalRef": external_ref,
        "confirmUrl": (
            f"https://am.ui.tt02.altinn.no/accessmanagement/ui/systemuser/request?id={REQUEST_ID}"
        ),
    }


# --- externalRef -----------------------------------------------------------------


async def test_the_external_ref_is_sent_and_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It has to survive the round trip, because it is needed again at token time.

    A caller holding the returned request should never have to remember what it
    passed in: the reference is part of the system user's identity, and losing
    it means the customer approving again.
    """
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json=_body())

    monkeypatch.setattr(systemuser, "_client", _transport(handler))
    created = await request_system_user(
        "token",
        customer_orgnr=CUSTOMER,
        external_ref=REF,
        resources=(RESOURCE,),
        settings=_settings(),
    )
    assert seen["body"]["externalRef"] == REF
    assert created.external_ref == REF


async def test_an_empty_external_ref_is_refused_before_sending() -> None:
    """Altinn would accept it and Maskinporten would answer 404 much later.

    Refused here because the failure is otherwise separated from its cause by a
    customer's approval and a token request.
    """
    with pytest.raises(SystemUserError, match="external_ref is required"):
        await request_system_user(
            "token",
            customer_orgnr=CUSTOMER,
            external_ref="",
            resources=(RESOURCE,),
            settings=_settings(),
        )


# --- The request -----------------------------------------------------------------


async def test_the_request_names_the_customer_the_system_and_the_rights(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json=_body())

    monkeypatch.setattr(systemuser, "_client", _transport(handler))
    await request_system_user(
        "token",
        customer_orgnr=CUSTOMER,
        external_ref=REF,
        resources=(RESOURCE,),
        settings=_settings(),
    )
    assert seen["path"] == "/authentication/api/v1/systemuser/request/vendor"
    assert seen["body"]["partyOrgNo"] == CUSTOMER
    assert seen["body"]["systemId"] == "938193045_myntos"
    assert seen["body"]["rights"] == [
        {"resource": [{"id": "urn:altinn:resource", "value": RESOURCE}]}
    ]


async def test_a_new_request_carries_the_url_the_customer_must_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The confirmUrl is the only way the approval can happen.

    A vendor cannot approve on the customer's behalf, so losing this URL means
    the request is unusable and a new one has to be made.
    """
    monkeypatch.setattr(
        systemuser, "_client", _transport(lambda request: httpx.Response(201, json=_body()))
    )
    created = await request_system_user(
        "token",
        customer_orgnr=CUSTOMER,
        external_ref=REF,
        resources=(RESOURCE,),
        settings=_settings(),
    )
    assert created.confirm_url is not None
    assert created.confirm_url.startswith("https://am.ui.tt02.altinn.no/")
    assert created.is_approved is False


async def test_a_refused_request_carries_altinn_s_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        systemuser,
        "_client",
        _transport(lambda request: httpx.Response(400, text="rights not in register")),
    )
    with pytest.raises(SystemUserError, match="rights not in register"):
        await request_system_user(
            "token",
            customer_orgnr=CUSTOMER,
            external_ref=REF,
            resources=("app_something_we_did_not_declare",),
            settings=_settings(),
        )


# --- Approval status -------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "approved"),
    [("Accepted", True), ("accepted", True), ("New", False), ("Rejected", False), ("", False)],
)
async def test_only_an_accepted_request_counts_as_approved(
    monkeypatch: pytest.MonkeyPatch, status: str, approved: bool
) -> None:
    """Case-insensitive, because a silent False reads as "not yet approved".

    Which is the one wrong answer that looks completely normal.
    """
    monkeypatch.setattr(
        systemuser,
        "_client",
        _transport(lambda request: httpx.Response(200, json=_body(status))),
    )
    found = await get_request("token", REQUEST_ID, _settings())
    assert found.is_approved is approved


async def test_listing_reads_the_data_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """Altinn wraps the list in ``{"links": ..., "data": [...]}``."""
    monkeypatch.setattr(
        systemuser,
        "_client",
        _transport(
            lambda request: httpx.Response(
                200, json={"links": {}, "data": [_body("Accepted"), _body("New")]}
            )
        ),
    )
    found = await requests_for_system("token", _settings())
    assert [r.is_approved for r in found] == [True, False]


async def test_a_bare_list_is_accepted_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The envelope has changed shape before elsewhere in this package."""
    monkeypatch.setattr(
        systemuser,
        "_client",
        _transport(lambda request: httpx.Response(200, json=[_body("Accepted")])),
    )
    assert len(await requests_for_system("token", _settings())) == 1


async def test_an_unreachable_altinn_names_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(systemuser, "_client", _transport(handler))
    with pytest.raises(SystemUserError, match=re.escape("platform.tt02.altinn.no")):
        await get_request("token", REQUEST_ID, _settings())
