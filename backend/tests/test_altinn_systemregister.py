"""Registering this system as an Altinn system vendor.

The registration itself happens once per environment by hand, so what these
tests protect is not a hot code path: it is the *payload*, which Altinn
validates strictly and answers with a numbered code rather than a sentence. Each
assertion below corresponds to something Altinn rejects.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
import pytest

from myntos.core.config import Settings
from myntos.integrations.altinn import systemregister
from myntos.integrations.altinn.systemregister import (
    MVA_RESOURCE,
    SKATTEMELDING_RESOURCE,
    NotConfiguredError,
    SystemRegisterError,
    get_system,
    register_system,
    registration_payload,
    system_id,
    update_system,
)

ORGNR = "938193045"
CLIENT_ID = "f8293b48-86b5-466c-9823-526b1b619434"


def _settings(**overrides: Any) -> Settings:
    """Settings with the vendor fields filled, and nothing read from ``.env``.

    Every field the module touches is passed explicitly for the reason
    conftest blanks the provider credentials: a developer with a real
    configuration must not see different results from CI.
    """
    values: dict[str, Any] = {
        "altinn_vendor_orgnr": ORGNR,
        "altinn_system_name": "myntos",
        "altinn_system_redirect_url": "https://myntos.no/skatt/systembruker/godkjent",
        "altinn_platform_base_url": "https://platform.tt02.altinn.no",
        "maskinporten_client_id": CLIENT_ID,
    }
    values.update(overrides)
    return Settings(**values)


# --- The system id ---------------------------------------------------------------


def test_the_system_id_is_orgnr_underscore_name() -> None:
    """Altinn requires the form, and rejects anything else."""
    assert system_id(_settings()) == f"{ORGNR}_myntos"


def test_a_missing_orgnr_refuses_rather_than_guesses() -> None:
    """An id cannot be corrected later.

    Once a customer has approved a system user against an id, changing it means
    every customer approving again - so a half-configured deployment must not be
    able to register anything at all.
    """
    with pytest.raises(NotConfiguredError, match="ALTINN_VENDOR_ORGNR"):
        system_id(_settings(altinn_vendor_orgnr=""))


# --- The payload -----------------------------------------------------------------


def test_the_vendor_orgnr_carries_the_iso6523_prefix() -> None:
    """Altinn rejects a bare organisation number.

    ``0192:`` is the ISO 6523 issuing agency for Norwegian orgnr, and the
    authority string has to say so too.
    """
    body = registration_payload(_settings())
    assert body["vendor"] == {
        "authority": "iso6523-actorid-upis",
        "ID": f"0192:{ORGNR}",
    }


def test_the_declared_rights_are_the_two_apps_we_submit_to() -> None:
    """``rights`` is a ceiling on what may ever be requested, per customer.

    Listing a resource we do not use asks each customer to agree to more than we
    need, so the list is asserted rather than left to drift as apps are added.
    """
    body = registration_payload(_settings())
    declared = [entry["resource"][0]["value"] for entry in body["rights"]]
    assert declared == [SKATTEMELDING_RESOURCE, MVA_RESOURCE]


def test_the_maskinporten_client_is_named_in_the_registration() -> None:
    """Altinn ties the system to the client a systembruker token comes from.

    Without it the registration succeeds and every later token request fails,
    which is a long way from cause to symptom.
    """
    assert registration_payload(_settings())["clientId"] == [CLIENT_ID]


def test_a_missing_client_id_is_refused_before_sending() -> None:
    with pytest.raises(NotConfiguredError, match="MASKINPORTEN_CLIENT_ID"):
        registration_payload(_settings(maskinporten_client_id=None))


def test_a_non_https_redirect_url_is_refused_before_sending() -> None:
    """Altinn requires https, so the loopback URL used for ID-porten is invalid.

    Caught locally because the alternative is a 400 naming a validation code,
    and because the obvious value to reach for here is exactly the wrong one.
    """
    with pytest.raises(NotConfiguredError, match="https"):
        registration_payload(_settings(altinn_system_redirect_url="http://127.0.0.1:8000/godkjent"))


def test_the_system_is_findable_by_default_and_can_be_hidden() -> None:
    """``isVisible`` is about being found, not about working.

    A visible system appears in Altinn's catalogue for a customer to pick; an
    invisible one can still have system users, because the vendor generates the
    confirmation URL. Both directions are asserted because the flag reads like a
    feature switch and is not one.
    """
    assert registration_payload(_settings())["isVisible"] is True
    assert registration_payload(_settings(), visible=False)["isVisible"] is False


async def test_an_update_replaces_the_whole_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PUT on the item path, carrying every field - not a patch.

    A body missing ``clientId`` would unhook the system from its Maskinporten
    client, so this asserts the update sends the full registration.
    """
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"succeeded": True})

    monkeypatch.setattr(systemregister, "_client", _transport(handler))
    await update_system("token", _settings())
    assert seen["method"] == "PUT"
    assert seen["path"] == f"/authentication/api/v1/systemregister/vendor/{ORGNR}_myntos"
    assert seen["body"]["clientId"] == [CLIENT_ID]
    assert seen["body"]["id"] == f"{ORGNR}_myntos"


async def test_a_refused_update_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        systemregister, "_client", _transport(lambda request: httpx.Response(400, text="nope"))
    )
    with pytest.raises(SystemRegisterError, match="refused the update"):
        await update_system("token", _settings())


def test_the_customer_facing_text_is_in_all_three_languages() -> None:
    """Altinn shows it to whoever decides whether to approve the system user."""
    body = registration_payload(_settings())
    for field in ("name", "description"):
        assert set(body[field]) == {"nb", "nn", "en"}
        assert all(body[field].values())


# --- The calls -------------------------------------------------------------------


def _transport(handler: Any) -> Any:
    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_an_unregistered_system_reads_back_as_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """404 is the ordinary state before the first registration, not a failure."""
    monkeypatch.setattr(systemregister, "_client", _transport(lambda request: httpx.Response(404)))
    assert await get_system("token", _settings()) is None


async def test_reading_a_system_uses_the_vendor_path_and_the_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"id": f"{ORGNR}_myntos"})

    monkeypatch.setattr(systemregister, "_client", _transport(handler))
    system = await get_system("token", _settings())
    assert system == {"id": f"{ORGNR}_myntos"}
    assert seen["path"] == f"/authentication/api/v1/systemregister/vendor/{ORGNR}_myntos"
    assert seen["auth"] == "Bearer token"


async def test_registering_posts_to_the_vendor_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The create endpoint has no id in the path; the id is in the body."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["type"] = request.headers.get("content-type")
        return httpx.Response(200, json="426f7e05-23e1-4fd3-b256-e929063b0a30")

    monkeypatch.setattr(systemregister, "_client", _transport(handler))
    answer = await register_system("token", _settings())
    assert answer == "426f7e05-23e1-4fd3-b256-e929063b0a30"
    assert seen["method"] == "POST"
    assert seen["path"] == "/authentication/api/v1/systemregister/vendor"
    assert seen["type"] == "application/json"


async def test_an_empty_body_is_success_not_a_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirmation is reading the system back, never parsing the create answer."""
    monkeypatch.setattr(systemregister, "_client", _transport(lambda request: httpx.Response(200)))
    assert await register_system("token", _settings()) is None


async def test_a_refused_registration_carries_altinn_s_validation_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The AUTH.VLD-0000x code names the field, and is the only useful part."""
    monkeypatch.setattr(
        systemregister,
        "_client",
        _transport(
            lambda request: httpx.Response(
                400, json={"code": "AUTH.VLD-00002", "detail": "invalid resource"}
            )
        ),
    )
    with pytest.raises(SystemRegisterError, match=re.escape("AUTH.VLD-00002")):
        await register_system("token", _settings())


async def test_an_unreachable_altinn_names_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(systemregister, "_client", _transport(handler))
    with pytest.raises(SystemRegisterError, match=re.escape("platform.tt02.altinn.no")):
        await get_system("token", _settings())
