"""Enhetsregisteret lookups (Brønnøysundregistrene).

Open data over plain HTTPS — no credentials, no token exchange, no
:mod:`myntos.auth` involvement. That makes this the odd one out in this package,
and the reason it is a module of functions rather than a configured client.

It exists so nobody types their own company name. The organisasjonsnummer keys
every filing this system will ever produce and the name has to be the registered
one; asking a person for both invites a mismatch that stays invisible until a
submission is rejected months later. Deriving the name from the number leaves
one field to get right, and the mod-11 check digit already guards that
(:func:`myntos.domain.ledger.orgnr.validate_orgnr`).

A lookup is a convenience, never a gate. The register being unreachable must not
stop anyone from setting up their books, so callers are expected to let the user
carry on by hand — which is why this module distinguishes "no such unit" from
"we could not ask" instead of collapsing both into a failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from myntos.core.config import get_settings

# Short on purpose. The answer is a convenience the caller can do without, so a
# slow register should cost the user a moment and a typed-in name, not a request
# that hangs for as long as the default timeout allows.
_HTTP_TIMEOUT = httpx.Timeout(5.0)

# Hovedenheter first, then underenheter. Most companies are the former, but an
# invoice may well carry the orgnr of a single branch, and to the person typing
# it there is no visible difference between the two.
_COLLECTIONS = ("enheter", "underenheter")


class BrregError(Exception):
    """Base class for every Enhetsregisteret lookup failure."""


class UnitNotFoundError(BrregError):
    """No unit is registered with that organisasjonsnummer."""


class UnitDeletedError(BrregError):
    """The unit was registered once but has since been removed."""


class RegisterUnavailableError(BrregError):
    """Enhetsregisteret could not be reached, or did not answer as expected."""


@dataclass(frozen=True)
class RegisteredUnit:
    """The parts of a register entry this system has a use for."""

    orgnr: str
    name: str
    organisation_form: str | None
    organisation_form_description: str | None
    address_line: str | None
    postal_code: str | None
    city: str | None
    # Whether the unit is in Merverdiavgiftsregisteret. Nothing is enforced on
    # it — it is surfaced so someone who is not VAT-registered does not start
    # booking output VAT in the belief that they should be.
    registered_in_vat_register: bool
    # Whether it is in Foretaksregisteret, which decides whether every
    # salgsdokument has to carry the word "Foretaksregisteret" together with the
    # head-office address (bokføringsforskriften § 5-1-2 second paragraph).
    # Taken from the register rather than inferred from the organisasjonsform,
    # for the same reason as the line above: it is evidence, not a guess.
    registered_in_foretaksregisteret: bool
    # Both are shown rather than acted on. Bookkeeping for a bankrupt or
    # dissolving entity is legitimate and often required; silently proceeding
    # when the register says this is not the ordinary case is what would be odd.
    bankrupt: bool
    under_liquidation: bool


def _address(payload: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Street address, postal code and city, preferring the business address.

    ``forretningsadresse`` is where the business actually is; ``postadresse`` is
    frequently a PO box, so it is only a fallback. Both carry ``adresse`` as a
    list, because a Norwegian address may legitimately need more than one line.
    """
    address = payload.get("forretningsadresse") or payload.get("postadresse") or {}
    if not isinstance(address, dict):
        return None, None, None
    lines = [line for line in (address.get("adresse") or []) if isinstance(line, str) and line]
    return ", ".join(lines) or None, address.get("postnummer"), address.get("poststed")


def _unit_from(payload: dict[str, Any]) -> RegisteredUnit:
    """Read a register entry, or refuse it if the two fields we need are absent.

    Missing name or number means the response is not the document we asked for,
    and guessing past that would put an empty company name in front of a user as
    though the register had supplied it.
    """
    orgnr = payload.get("organisasjonsnummer")
    name = payload.get("navn")
    if not isinstance(orgnr, str) or not isinstance(name, str):
        raise RegisterUnavailableError("Enhetsregisteret svarte ikke som forventet")

    form = payload.get("organisasjonsform")
    form = form if isinstance(form, dict) else {}
    address_line, postal_code, city = _address(payload)
    return RegisteredUnit(
        orgnr=orgnr,
        name=name,
        organisation_form=form.get("kode"),
        organisation_form_description=form.get("beskrivelse"),
        address_line=address_line,
        postal_code=postal_code,
        city=city,
        registered_in_vat_register=bool(payload.get("registrertIMvaregisteret")),
        registered_in_foretaksregisteret=bool(payload.get("registrertIForetaksregisteret")),
        bankrupt=bool(payload.get("konkurs")),
        under_liquidation=bool(payload.get("underAvvikling")),
    )


async def _fetch(http: httpx.AsyncClient, url: str) -> dict[str, Any] | None:
    """One lookup: the entry, or ``None`` when this collection has no such unit."""
    try:
        response = await http.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError as error:
        raise RegisterUnavailableError("vi fikk ikke kontakt med Enhetsregisteret") from error

    if response.status_code == httpx.codes.NOT_FOUND:
        return None
    if response.status_code == httpx.codes.GONE:
        # The register answers 410 for a unit it has deleted. Saying so is more
        # useful than "unknown number", which would send the user hunting for a
        # typo that is not there.
        raise UnitDeletedError("enheten er slettet fra Enhetsregisteret")
    if response.is_error:
        raise RegisterUnavailableError("Enhetsregisteret svarte ikke som forventet")

    try:
        payload = response.json()
    except ValueError as error:
        raise RegisterUnavailableError("Enhetsregisteret svarte ikke som forventet") from error
    return payload if isinstance(payload, dict) else None


def _client() -> httpx.AsyncClient:
    """The HTTP client one lookup runs on.

    Its own function so tests have a seam to replace with a stub transport, and
    so the timeout is decided in exactly one place.
    """
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT)


async def lookup_unit(orgnr: str) -> RegisteredUnit:
    """Look up an organisasjonsnummer that has already been validated.

    ``orgnr`` is expected to have passed
    :func:`~myntos.domain.ledger.orgnr.validate_orgnr`: this spends a network
    round trip, and there is no point spending one on a number whose check digit
    proves it was never issued.
    """
    base = get_settings().brreg_api_base_url.rstrip("/")
    async with _client() as http:
        for collection in _COLLECTIONS:
            payload = await _fetch(http, f"{base}/{collection}/{orgnr}")
            if payload is not None:
                return _unit_from(payload)
    raise UnitNotFoundError("fant ingen enhet med dette organisasjonsnummeret")
