"""Asking a customer to approve a system user, so we may act without them.

This is the half of systembruker that involves the customer. The other half is
:mod:`myntos.integrations.altinn.systemregister`, which declares the system
once; this one runs per customer, once, at onboarding.

The flow, and only the middle step is theirs:

1. We create a *request* naming their organisasjonsnummer and the resources we
   want. Altinn answers with a ``confirmUrl`` on its own access-management UI.
2. Somebody there with the tilgangsstyring or hovedadministrator access package
   opens it, logs in, and approves. **A vendor cannot approve on a customer's
   behalf** - the approval is what authorizes us to act unattended, so being
   unable to grant it to ourselves is the point rather than a limitation. The
   access-management approve endpoint answers 403 to a vendor token, and 403 to
   an exchanged ID-porten token too unless the client holds
   ``altinn:accessmanagement/enduser:requests.write``.
3. From then on, Maskinporten issues systembruker tokens for that customer with
   nobody present. Verified end to end on 2026-08-28: a client company's
   skattemelding came back 200 on the machine plane, where the same call without
   a system user answers 400 ``SME-EKSTERN-005``.

**``externalRef`` is part of the system user's identity, not a label.** This is
the trap. Altinn's documentation describes it as needed "only when multiple
system users point to the same system", which reads as optional - but a request
created *with* one produces a system user keyed by it, and a later token request
that omits it matches nothing. Maskinporten then answers 404
``invalid_altinn_customer_configuration``, which says nothing about a reference.
So this module treats it as required and returns it on the request, and
:func:`myntos.auth.maskinporten.client.systemuser_authorization_details` takes it
back verbatim. Do not change one without the other.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from myntos.core.config import Settings, get_settings

# Creating a request needs write; reading one back needs read. Unlike the system
# register, where both are the write scope.
SCOPE_WRITE = "altinn:authentication/systemuser.request.write"
SCOPE_READ = "altinn:authentication/systemuser.request.read"

_PATH = "/authentication/api/v1/systemuser/request/vendor"

_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _client() -> httpx.AsyncClient:
    """The HTTP client one request runs on. A seam for tests."""
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT)


class SystemUserError(Exception):
    """Altinn refused a system-user call."""


@dataclass(frozen=True)
class SystemUserRequest:
    """One customer's pending or settled approval.

    ``external_ref`` is carried deliberately: it is needed again at token time,
    and a caller that has this object should never have to remember what it
    passed in.
    """

    id: str
    status: str
    customer_orgnr: str
    system_id: str
    external_ref: str
    confirm_url: str | None

    @property
    def is_approved(self) -> bool:
        """Whether a systembruker token can be obtained for this customer.

        Altinn spells it ``Accepted``. Compared case-insensitively because the
        alternative is a silent False if the spelling ever shifts, and a silent
        False here reads as "the customer has not got round to it".
        """
        return self.status.casefold() == "accepted"


def _request_from(payload: dict[str, Any]) -> SystemUserRequest:
    return SystemUserRequest(
        id=str(payload.get("id", "")),
        status=str(payload.get("status", "")),
        customer_orgnr=str(payload.get("partyOrgNo", "")),
        system_id=str(payload.get("systemId", "")),
        external_ref=str(payload.get("externalRef", "")),
        confirm_url=payload.get("confirmUrl"),
    )


def _rights(resources: tuple[str, ...]) -> list[dict[str, Any]]:
    return [
        {"resource": [{"id": "urn:altinn:resource", "value": resource}]} for resource in resources
    ]


async def request_system_user(
    token: str,
    *,
    customer_orgnr: str,
    external_ref: str,
    resources: tuple[str, ...],
    settings: Settings | None = None,
) -> SystemUserRequest:
    """Ask a customer to approve a system user for this system.

    ``external_ref`` is required rather than optional, and must be recorded
    against the customer: it becomes part of the system user's identity, and a
    token request that omits it is answered 404
    ``invalid_altinn_customer_configuration``. One stable value per customer per
    system is the intended use.

    ``resources`` must be a subset of what the system declared in the register;
    Altinn refuses anything beyond it, which is the register doing its job.
    """
    settings = settings or get_settings()
    base = settings.altinn_platform_base_url.rstrip("/")
    if not external_ref:
        raise SystemUserError(
            "external_ref is required: it is part of the system user's identity, "
            "and a token request that omits it cannot find the system user"
        )
    body = {
        "externalRef": external_ref,
        "systemId": _system_id(settings),
        "partyOrgNo": customer_orgnr,
        "rights": _rights(resources),
        "redirectUrl": settings.altinn_system_redirect_url,
    }
    async with _client() as http:
        try:
            response = await http.post(
                f"{base}{_PATH}",
                content=json.dumps(body).encode(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as error:
            raise SystemUserError(f"could not reach Altinn at {base}") from error
    if response.status_code not in (httpx.codes.OK, httpx.codes.CREATED):
        raise SystemUserError(
            f"Altinn refused the system-user request ({response.status_code}): "
            f"{response.text[:400]}"
        )
    return _request_from(response.json())


async def get_request(
    token: str, request_id: str, settings: Settings | None = None
) -> SystemUserRequest:
    """One request as Altinn holds it, to see whether it has been approved."""
    settings = settings or get_settings()
    base = settings.altinn_platform_base_url.rstrip("/")
    async with _client() as http:
        try:
            response = await http.get(
                f"{base}{_PATH}/{request_id}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except httpx.HTTPError as error:
            raise SystemUserError(f"could not reach Altinn at {base}") from error
    if response.status_code != httpx.codes.OK:
        raise SystemUserError(
            f"Altinn would not return the request ({response.status_code}): {response.text[:300]}"
        )
    return _request_from(response.json())


async def requests_for_system(
    token: str, settings: Settings | None = None
) -> list[SystemUserRequest]:
    """Every request against this system, approved or not.

    Useful for reconciling what we think a customer has approved against what
    Altinn thinks, which is the sort of drift that only shows up when a
    submission fails.
    """
    settings = settings or get_settings()
    base = settings.altinn_platform_base_url.rstrip("/")
    url = f"{base}{_PATH}/bysystem/{_system_id(settings)}"
    async with _client() as http:
        try:
            response = await http.get(
                url,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except httpx.HTTPError as error:
            raise SystemUserError(f"could not reach Altinn at {base}") from error
    if response.status_code != httpx.codes.OK:
        raise SystemUserError(
            f"Altinn would not list the requests ({response.status_code}): {response.text[:300]}"
        )
    payload = response.json()
    entries = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise SystemUserError("Altinn's answer did not carry a list of requests")
    return [_request_from(entry) for entry in entries]


def _system_id(settings: Settings) -> str:
    """This system's register id.

    Imported lazily to keep the two modules from importing each other: the
    register declares the system, this one uses its id, and nothing needs to go
    the other way.
    """
    from myntos.integrations.altinn.systemregister import system_id

    return system_id(settings)
