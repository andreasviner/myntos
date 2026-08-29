"""Registering Myntos as a system vendor in Altinn, for systembruker access.

Everything else in this package acts for a taxpayer who is present: a person
logs in with ID-porten and the calls are made on the strength of what that
person may do. That is the whole reason retrieval on the machine plane answers
400 ``SME-EKSTERN-005`` - "cannot check whether the logged-in user has the
rights to represent the taxpayer" - because a client-credentials token has no
logged-in user for anyone's rights to be checked against.

**Systembruker is the answer to that.** A vendor registers its system here,
declaring which Altinn resources the system may ever ask for and which
Maskinporten client it authenticates as. A customer then approves a *system
user* for that system, and the vendor can afterwards obtain tokens that carry
both the customer's system user and this system - through RFC 9396 rich
authorization requests, type ``urn:altinn:systemuser``. Such a token has a
subject whose rights Skatteetaten can check, which is exactly what the plain
machine token lacked.

It also matters for a deadline. The route proven in test today works because
being daglig leder is an Altinn *role*, and roles stop counting in access checks
on **2026-12-01**, replaced by tilgangspakker. Systembruker does not depend on
them.

Three things about this module are worth knowing before changing it.

**The system id is permanent in practice.** Altinn requires the form
``{vendor orgnr}_{name}``, and once a customer has approved a system user
against it, changing it means every customer approving again. It is built from
configuration rather than written inline so that test and production cannot
silently share one id.

**``rights`` is a ceiling, not a grant.** It lists the resources the system may
*ever* request; a customer's approval is what actually grants them, per
customer. Listing a resource we do not use costs nothing at approval time but
asks the customer to agree to more than we need, so the list is exactly the two
apps this program submits to.

**``isVisible`` controls being found, not being usable.** A visible system
appears in Altinn's own catalogue, so a customer can pick it there; an invisible
one can still have system users, because the vendor generates the confirmation
URL itself. It defaults to true because this system is meant to be findable, and
is a parameter so a scratch registration can avoid advertising itself.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from myntos.core.config import Settings, get_settings

# The scope the registration calls check. Both reading and writing a vendor's
# own systems use the *write* scope; there is no separate read scope, which is
# surprising enough to write down.
SCOPE = "altinn:authentication/systemregister.write"

_PATH = "/authentication/api/v1/systemregister/vendor"

# The Altinn resources this system submits to. Resource ids, not app URLs: the
# same app is addressed by URL when creating an instance and by resource id when
# declaring rights, and the two are easy to confuse.
SKATTEMELDING_RESOURCE = "app_skd_formueinntekt-skattemelding-v2"
MVA_RESOURCE = "app_skd_mva-melding-innsending-v1"

# ISO 6523 issuing agency for Norwegian organisation numbers. Altinn requires
# the prefix, and rejects a bare orgnr.
_NORWEGIAN_ORG = "iso6523-actorid-upis"
_ORG_PREFIX = "0192:"

_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _client() -> httpx.AsyncClient:
    """The HTTP client one request runs on. A seam for tests."""
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT)


class SystemRegisterError(Exception):
    """Altinn refused a system-register call."""


class NotConfiguredError(SystemRegisterError):
    """The vendor settings needed to identify this system are missing."""


def system_id(settings: Settings | None = None) -> str:
    """This system's id in Altinn's register.

    ``{vendor orgnr}_{name}``, which Altinn requires. Raises rather than
    guessing when the orgnr is unset: a system registered under the wrong id
    cannot be renamed once a customer has approved a system user against it.
    """
    settings = settings or get_settings()
    orgnr = settings.altinn_vendor_orgnr.strip()
    name = settings.altinn_system_name.strip()
    if not orgnr or not name:
        raise NotConfiguredError(
            "ALTINN_VENDOR_ORGNR and ALTINN_SYSTEM_NAME are both required to "
            "build the system id, which Altinn requires to be "
            "'{vendor orgnr}_{name}'"
        )
    return f"{orgnr}_{name}"


def registration_payload(
    settings: Settings | None = None,
    *,
    resources: tuple[str, ...] = (SKATTEMELDING_RESOURCE, MVA_RESOURCE),
    visible: bool = True,
) -> dict[str, Any]:
    """The body that registers this system.

    A pure function, so the shape can be asserted without a network: it is a
    nested document with several Altinn-specific spellings, and a typo in it
    surfaces as a 400 with a validation code rather than as anything readable.
    """
    settings = settings or get_settings()
    orgnr = settings.altinn_vendor_orgnr.strip()
    client_id = (settings.maskinporten_client_id or "").strip()
    if not client_id:
        raise NotConfiguredError(
            "MASKINPORTEN_CLIENT_ID is required: it is the client a systembruker "
            "token will be requested with, and Altinn ties the system to it"
        )
    redirect = settings.altinn_system_redirect_url.strip()
    if not redirect.startswith("https://"):
        raise NotConfiguredError(
            "ALTINN_SYSTEM_REDIRECT_URL must be an https URL - Altinn rejects "
            "anything else, loopback development URLs included"
        )
    return {
        "id": system_id(settings),
        "vendor": {"authority": _NORWEGIAN_ORG, "ID": f"{_ORG_PREFIX}{orgnr}"},
        # All three languages, because Altinn shows this to the customer who is
        # asked to approve a system user. A name only we can read is a name that
        # makes somebody refuse.
        "name": {
            "nb": "Myntos regnskap",
            "nn": "Myntos rekneskap",
            "en": "Myntos accounting",
        },
        "description": {
            "nb": "Leverer skattemelding, næringsspesifikasjon og MVA-melding "
            "direkte fra regnskapet.",
            "nn": "Leverer skattemelding, næringsspesifikasjon og MVA-melding "
            "direkte frå rekneskapen.",
            "en": "Files the skattemelding, næringsspesifikasjon and "
            "MVA-melding directly from the accounts.",
        },
        "rights": [
            {"resource": [{"id": "urn:altinn:resource", "value": resource}]}
            for resource in resources
        ],
        "clientId": [client_id],
        "allowedredirecturls": [redirect],
        "isVisible": visible,
    }


async def get_system(token: str, settings: Settings | None = None) -> dict[str, Any] | None:
    """This system as Altinn holds it, or ``None`` if it is not registered.

    ``None`` rather than an error for a 404: "not registered yet" is the
    ordinary state before the first registration, and the caller's next step
    differs from the one it would take on a real failure.
    """
    settings = settings or get_settings()
    base = settings.altinn_platform_base_url.rstrip("/")
    url = f"{base}{_PATH}/{system_id(settings)}"
    async with _client() as http:
        try:
            response = await http.get(
                url,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except httpx.HTTPError as error:
            raise SystemRegisterError(f"could not reach Altinn at {base}") from error
    if response.status_code == httpx.codes.NOT_FOUND:
        return None
    if response.status_code != httpx.codes.OK:
        raise SystemRegisterError(
            f"Altinn would not return the system ({response.status_code}): {response.text[:300]}"
        )
    payload: dict[str, Any] = response.json()
    return payload


async def update_system(
    token: str,
    settings: Settings | None = None,
    *,
    payload: dict[str, Any] | None = None,
) -> None:
    """Replace this system's registration.

    A whole-document **replace**, not a patch: Altinn takes the same body as
    registration on ``PUT {vendor}/{system id}``, so the payload is rebuilt from
    :func:`registration_payload` rather than assembled by hand. A PUT missing
    ``clientId`` would unhook the system from its Maskinporten client, and
    nothing would say so until the next token request.

    Verified against tt02 on 2026-08-28, answering ``{"succeeded": true}``.
    """
    settings = settings or get_settings()
    base = settings.altinn_platform_base_url.rstrip("/")
    body = payload if payload is not None else registration_payload(settings)
    async with _client() as http:
        try:
            response = await http.put(
                f"{base}{_PATH}/{system_id(settings)}",
                content=json.dumps(body).encode(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as error:
            raise SystemRegisterError(f"could not reach Altinn at {base}") from error
    if response.status_code not in (httpx.codes.OK, httpx.codes.NO_CONTENT):
        raise SystemRegisterError(
            f"Altinn refused the update ({response.status_code}): {response.text[:500]}"
        )


async def register_system(
    token: str,
    settings: Settings | None = None,
    *,
    payload: dict[str, Any] | None = None,
) -> str | None:
    """Register this system with Altinn, returning Altinn's id for it.

    The answer is a bare JSON string holding a uuid, not an object - verified
    against tt02 on 2026-08-28, where it was
    ``"426f7e05-23e1-4fd3-b256-e929063b0a30"``. That uuid is Altinn's internal
    handle; the *system id* we address the system by remains
    :func:`system_id`, so this is returned for logging rather than for use.

    ``None`` when the body is empty, which is treated as success: the
    registration is confirmed by reading the system back with
    :func:`get_system`, never by parsing this.
    """
    settings = settings or get_settings()
    base = settings.altinn_platform_base_url.rstrip("/")
    body = payload if payload is not None else registration_payload(settings)
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
            raise SystemRegisterError(f"could not reach Altinn at {base}") from error
    if response.status_code not in (httpx.codes.OK, httpx.codes.CREATED):
        # Altinn answers 400 with codes AUTH.VLD-0000x naming the field it
        # objected to, which is the only useful part of the failure.
        raise SystemRegisterError(
            f"Altinn refused the registration ({response.status_code}): {response.text[:500]}"
        )
    if not response.content.strip():
        return None
    answer = response.json()
    return answer if isinstance(answer, str) else json.dumps(answer)
