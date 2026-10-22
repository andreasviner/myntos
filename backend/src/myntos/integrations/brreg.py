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
class Role:
    """One person or company holding a registered role in a unit.

    The register identifies a person by name **and** birth date. Only the name
    is carried: a board list is what an accountant needs, a date of birth is
    personal data with no bookkeeping use, and the moment it is in the dataclass
    it is one careless response model away from a browser. The same reasoning
    that keeps fødselsnummer out of
    :class:`myntos.api.v1.skatt.AuthorizedPartyResponse`.

    ``orgnr`` is set instead of ``name`` being a person when the holder is a
    company, which is the ordinary case for revisor and regnskapsfører.

    ``group`` is the rollegruppe the register nests this under (``STYR``,
    ``DAGL``, ``REVI``…) and ``code`` the role within it (``LEDE``, ``MEDL``,
    ``VARA``…). Both are kept as the register's own codes, untranslated,
    alongside its own descriptions — the same choice as Altinn's role codes, and
    for the same reason: a paraphrase here would be a second vocabulary for one
    fact.
    """

    group: str
    group_description: str
    code: str
    description: str
    name: str
    # Set when the holder is a company rather than a person.
    orgnr: str | None = None
    # "Representant for de ansatte" and the like. Present only when the register
    # says who elected this person.
    elected_by: str | None = None
    # A role the register has ended. Kept out of the answer by
    # :func:`fetch_roles`, but modelled so the filter is visible rather than
    # implicit in a comprehension.
    resigned: bool = False


@dataclass(frozen=True)
class Capital:
    """Registered share capital, for the forms that have one."""

    amount: str
    currency: str
    shares: int | None


@dataclass(frozen=True)
class RegisteredUnit:
    """What Enhetsregisteret holds about a unit.

    Deliberately wide. Everything here is open data keyed on a number the user
    already typed, and each field it does *not* carry is a field somebody has to
    type instead — which is the one step where a mistake produces books for a
    subtly wrong entity. The subset that gets **stored** on a company is much
    narrower and is decided in
    :func:`myntos.domain.ledger.service.refresh_company_from_register`; the rest
    is shown and thrown away.
    """

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

    # Everything below carries a default. The block above is the set the rest of
    # the system has always relied on and must always be present; these are
    # additions the register may or may not have for a given unit, and a stub in
    # a test should not have to spell out forty of them.

    # The third of the set above, and the one people forget exists.
    under_forced_liquidation: bool = False

    # --- Address --------------------------------------------------------------
    # The kommune the address sits in, and its number. Shown because it is how a
    # Norwegian business is placed, and because two towns share a poststed more
    # often than people expect.
    municipality: str | None = None
    municipality_number: str | None = None
    country: str | None = None
    # The postal address, when it differs from the one above — typically a PO
    # box. Kept apart rather than merged: an invoice must carry the business
    # address, and a postboks is not one.
    postal_address_line: str | None = None
    postal_address_postal_code: str | None = None
    postal_address_city: str | None = None

    # --- Næring ---------------------------------------------------------------
    # SN2007 code and its text. Up to three, in the register's own order of
    # importance. The first one is the one a næringsspesifikasjon asks for, and
    # the reason this is stored on the company rather than only shown.
    industry_code: str | None = None
    industry_description: str | None = None
    secondary_industries: tuple[tuple[str, str], ...] = ()

    # --- Contact --------------------------------------------------------------
    # What a salgsdokument may carry about the seller besides the address. All
    # three are optional in the register and frequently absent.
    website: str | None = None
    phone: str | None = None
    mobile: str | None = None
    email: str | None = None

    # --- Dates ----------------------------------------------------------------
    # ISO dates as the register gives them. Strings rather than `date`, because
    # every one of them is shown rather than calculated with, and parsing a
    # field only to format it again is a way to turn a missing value into a 500.
    established_on: str | None = None
    registered_on: str | None = None
    registered_in_foretaksregisteret_on: str | None = None
    registered_in_vat_register_on: str | None = None
    # The last annual accounts the register has received, as a year. The one
    # field here that says something about *bookkeeping* rather than about the
    # entity: it is the answer to "which year does this company still owe".
    last_submitted_annual_accounts: str | None = None
    # Set when the register has deleted the unit (``slettedato``). Such an entry
    # carries little more than the number, the name and the form, and is
    # answered rather than refused: a deleted aksjeselskap still owes the
    # skattemelding for its last year, and the books it is filed from have to
    # be set up from somewhere. Every caller that shows a unit shows this beside
    # «konkurs» and «under avvikling».
    deleted_on: str | None = None

    # --- Structure ------------------------------------------------------------
    # Set on a subunit: the orgnr of the main unit it belongs to. Roles are
    # registered on the main unit only, so this is also the key
    # :func:`fetch_roles` follows.
    parent_orgnr: str | None = None
    # True for a unit the register marks as part of a group.
    in_group: bool = False
    employees: int | None = None
    # Bokmål / Nynorsk. Which one the register corresponds in.
    language: str | None = None
    # Sector code, e.g. "2100 Private aksjeselskaper mv.".
    sector_code: str | None = None
    sector_description: str | None = None
    capital: Capital | None = None
    # Registered purpose and activity, as free text lines. Shown because they
    # are how a person recognises that the number they typed is their own
    # business rather than one with a similar name.
    purpose: tuple[str, ...] = ()
    activity: tuple[str, ...] = ()
    # Names this unit used to have, newest first. The reason an old invoice can
    # be reconciled against a company nobody recognises any more.
    former_names: tuple[str, ...] = ()
    registered_in_frivillighetsregisteret: bool = False
    registered_in_stiftelsesregisteret: bool = False
    # True when the entry came from /underenheter rather than /enheter.
    is_subunit: bool = False


@dataclass(frozen=True)
class _Address:
    """One address block from the register, flattened."""

    line: str | None = None
    postal_code: str | None = None
    city: str | None = None
    municipality: str | None = None
    municipality_number: str | None = None
    country: str | None = None


def _address_block(value: Any) -> _Address:
    if not isinstance(value, dict):
        return _Address()
    lines = [line for line in (value.get("adresse") or []) if isinstance(line, str) and line]
    return _Address(
        line=", ".join(lines) or None,
        postal_code=value.get("postnummer"),
        city=value.get("poststed"),
        municipality=value.get("kommune"),
        municipality_number=value.get("kommunenummer"),
        country=value.get("land"),
    )


def _business_address(payload: dict[str, Any]) -> _Address:
    """Where the business actually is.

    Three keys for one concept, and which one is present depends on what kind of
    unit answered. A main unit has ``forretningsadresse``; a **subunit** has
    ``beliggenhetsadresse`` and no forretningsadresse at all, which is why it is
    named here rather than left to the fallback — without it every subunit came
    back with no address whatsoever. ``postadresse`` is last because it is
    frequently a postboks, and a postboks is not an address a salgsdokument may
    carry as the seller's.
    """
    for key in ("forretningsadresse", "beliggenhetsadresse", "postadresse"):
        block = _address_block(payload.get(key))
        if block.line is not None or block.postal_code is not None:
            return block
    return _Address()


def _address(payload: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """The three address fields the rest of the system has always used."""
    block = _business_address(payload)
    return block.line, block.postal_code, block.city


def _code_pair(value: Any) -> tuple[str | None, str | None]:
    """``{"kode": …, "beskrivelse": …}`` → the two of them, or two Nones."""
    if not isinstance(value, dict):
        return None, None
    code = value.get("kode")
    description = value.get("beskrivelse")
    return (
        code if isinstance(code, str) else None,
        description if isinstance(description, str) else None,
    )


def _lines(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(line for line in value if isinstance(line, str) and line)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _capital(value: Any) -> Capital | None:
    """Share capital, with the amount kept as a string.

    A float here would be a rounding bug waiting for a large enough
    aksjekapital, and nothing calculates with it — it is shown.
    """
    if not isinstance(value, dict):
        return None
    amount = value.get("belop")
    if not isinstance(amount, int | float):
        return None
    shares = value.get("antallAksjer")
    return Capital(
        # Two decimals, so a whole-krone capital does not come out as
        # "5976872600.0" — JSON gave us a float and str() is not a currency
        # formatter.
        amount=f"{amount:.2f}",
        currency=_text(value.get("valuta")) or "NOK",
        shares=shares if isinstance(shares, int) else None,
    )


def _unit_from(payload: dict[str, Any], *, is_subunit: bool = False) -> RegisteredUnit:
    """Read a register entry, or refuse it if the two fields we need are absent.

    Missing name or number means the response is not the document we asked for,
    and guessing past that would put an empty company name in front of a user as
    though the register had supplied it.
    """
    orgnr = payload.get("organisasjonsnummer")
    name = payload.get("navn")
    if not isinstance(orgnr, str) or not isinstance(name, str):
        raise RegisterUnavailableError("Enhetsregisteret svarte ikke som forventet")

    form_code, form_description = _code_pair(payload.get("organisasjonsform"))
    business = _business_address(payload)
    postal = _address_block(payload.get("postadresse"))
    industry_code, industry_description = _code_pair(payload.get("naeringskode1"))
    sector_code, sector_description = _code_pair(payload.get("institusjonellSektorkode"))
    secondary = tuple(
        (code, description)
        for code, description in (
            _code_pair(payload.get("naeringskode2")),
            _code_pair(payload.get("naeringskode3")),
        )
        if code is not None and description is not None
    )
    employees = payload.get("antallAnsatte")
    former = payload.get("historiskeNavn")
    return RegisteredUnit(
        orgnr=orgnr,
        name=name,
        organisation_form=form_code,
        organisation_form_description=form_description,
        address_line=business.line,
        postal_code=business.postal_code,
        city=business.city,
        municipality=business.municipality,
        municipality_number=business.municipality_number,
        country=business.country,
        # Only when it is a different address; repeating the business address
        # under a second heading says nothing and reads as though it did.
        postal_address_line=None if postal.line == business.line else postal.line,
        postal_address_postal_code=(None if postal.line == business.line else postal.postal_code),
        postal_address_city=None if postal.line == business.line else postal.city,
        registered_in_vat_register=bool(payload.get("registrertIMvaregisteret")),
        registered_in_foretaksregisteret=bool(payload.get("registrertIForetaksregisteret")),
        bankrupt=bool(payload.get("konkurs")),
        under_liquidation=bool(payload.get("underAvvikling")),
        under_forced_liquidation=bool(payload.get("underTvangsavviklingEllerTvangsopplosning")),
        industry_code=industry_code,
        industry_description=industry_description,
        secondary_industries=secondary,
        website=_text(payload.get("hjemmeside")),
        phone=_text(payload.get("telefon")),
        mobile=_text(payload.get("mobil")),
        email=_text(payload.get("epostadresse")),
        established_on=_text(payload.get("stiftelsesdato")),
        registered_on=_text(payload.get("registreringsdatoEnhetsregisteret")),
        registered_in_foretaksregisteret_on=_text(
            payload.get("registreringsdatoForetaksregisteret")
        ),
        registered_in_vat_register_on=_text(
            payload.get("registreringsdatoMerverdiavgiftsregisteret")
        ),
        last_submitted_annual_accounts=_text(payload.get("sisteInnsendteAarsregnskap")),
        # The register used to answer a deleted unit with 410 and now answers
        # 200 with ``respons_klasse: SlettetEnhet`` and a ``slettedato``
        # (seen 2026-10-22). Read from the date, which both shapes carry, so a
        # deleted unit is never mistaken for a live one with no address.
        deleted_on=_text(payload.get("slettedato")),
        parent_orgnr=_text(payload.get("overordnetEnhet")),
        in_group=bool(payload.get("erIKonsern")),
        # ``harRegistrertAntallAnsatte`` is the register's own way of saying
        # "nobody has told us", which is a different thing from zero employees.
        employees=(
            employees
            if isinstance(employees, int) and payload.get("harRegistrertAntallAnsatte")
            else None
        ),
        language=_text(payload.get("maalform")),
        sector_code=sector_code,
        sector_description=sector_description,
        capital=_capital(payload.get("kapital")),
        purpose=_lines(payload.get("vedtektsfestetFormaal")),
        activity=_lines(payload.get("aktivitet")),
        former_names=tuple(
            _text(entry.get("navn")) or ""
            for entry in (former if isinstance(former, list) else [])
            if isinstance(entry, dict) and _text(entry.get("navn"))
        ),
        registered_in_frivillighetsregisteret=bool(
            payload.get("registrertIFrivillighetsregisteret")
        ),
        registered_in_stiftelsesregisteret=bool(payload.get("registrertIStiftelsesregisteret")),
        # From which collection answered, not from a field in the body: the
        # register has added and renamed body fields before, and "which URL
        # returned this" cannot drift.
        is_subunit=is_subunit,
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
        # The register's older answer for a unit it has deleted, which carries
        # no name to set books up under. Saying so is more useful than "unknown
        # number", which would send the user hunting for a typo that is not
        # there. Its newer answer is a 200, read in _unit_from.
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
                return _unit_from(payload, is_subunit=collection == "underenheter")
    raise UnitNotFoundError("fant ingen enhet med dette organisasjonsnummeret")


def _roles_from(payload: dict[str, Any]) -> list[Role]:
    """Flatten the register's rollegrupper into one list.

    The nesting carries one fact — which group a role belongs to — and that fact
    is copied onto each role instead, because every consumer wants a list and
    would otherwise re-flatten it. Order is preserved: the register sorts the
    board with its chair first, and that is the order it should be read in.

    A role whose holder is neither a named person nor a unit is dropped. The
    register does emit those (a person who has asked to be shielded), and a row
    with an empty name is worse than no row.
    """
    groups = payload.get("rollegrupper")
    if not isinstance(groups, list):
        raise RegisterUnavailableError("Enhetsregisteret svarte ikke som forventet")

    roles: list[Role] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_code, group_description = _code_pair(group.get("type"))
        for entry in group.get("roller") or []:
            if not isinstance(entry, dict):
                continue
            code, description = _code_pair(entry.get("type"))
            person = entry.get("person")
            unit = entry.get("enhet")
            name: str | None = None
            orgnr: str | None = None
            if isinstance(unit, dict):
                name = _text(unit.get("navn"))
                orgnr = _text(unit.get("organisasjonsnummer"))
            elif isinstance(person, dict):
                parts = person.get("navn")
                if isinstance(parts, dict):
                    name = (
                        " ".join(
                            part
                            for key in ("fornavn", "mellomnavn", "etternavn")
                            if (part := _text(parts.get(key))) is not None
                        )
                        or None
                    )
            if name is None or code is None or group_code is None:
                continue
            _, elected_by = _code_pair(entry.get("valgtAv"))
            roles.append(
                Role(
                    group=group_code,
                    group_description=group_description or group_code,
                    code=code,
                    description=description or code,
                    name=name,
                    orgnr=orgnr,
                    elected_by=elected_by,
                    resigned=bool(entry.get("fratraadt")) or bool(entry.get("avregistrert")),
                )
            )
    # Roles the register has ended are dropped rather than shown greyed out:
    # this list answers "who may act for this company today", and a former
    # daglig leder in it is a wrong answer rather than a historical note.
    return [role for role in roles if not role.resigned]


async def fetch_roles(orgnr: str, *, parent_orgnr: str | None = None) -> list[Role]:
    """Who holds a registered role in the unit: board, daglig leder, revisor…

    Roles are registered on the **main unit only** — ``/underenheter/{orgnr}/roller``
    is a 404, not an empty list — so a subunit is asked about through its
    ``overordnetEnhet``. Pass it as ``parent_orgnr``; the roles that govern a
    department are the ones registered on the company it belongs to.

    An empty list is a legitimate answer (a unit with nothing registered), and
    so is a 404, which is why that is not raised as "no such unit": the caller
    already has the unit, and what is absent is its role list.
    """
    base = get_settings().brreg_api_base_url.rstrip("/")
    target = parent_orgnr or orgnr
    async with _client() as http:
        payload = await _fetch(http, f"{base}/enheter/{target}/roller")
    if payload is None:
        return []
    return _roles_from(payload)
