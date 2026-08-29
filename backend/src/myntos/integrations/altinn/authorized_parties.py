"""Which parties a person may act for, according to Altinn.

Altinn already knows the answer to the question every accounting program asks
its user to type in: *which companies do you have the right to report for?* The
roles and delegations behind that answer live in Altinn's access management, and
the list itself is one call::

    GET {platform}/accessmanagement/api/v1/authorizedparties?includeAltinn2=true
        Authorization: Bearer <an Altinn token>

This is the same list Altinn's own "Velg aktør" picker is built from. A
regnskapsfører with forty clients has forty entries in it, and typing forty
organisasjonsnumre by hand is both tedious and the one step in the setup where a
typo silently produces books for the wrong company.

Two things about it are easy to get wrong, and each costs an unexplained status
code:

- **It is a person's list, not a machine's.** The endpoint reads the ``userId``
  claim off the Altinn token and answers 401 when there is none — which is
  exactly what a Maskinporten-derived token has. So the token passed in here is
  the one exchanged from an ID-porten login
  (:func:`~myntos.integrations.altinn.tokens.exchange_id_porten_token`), never
  the machine one. The ``altinn:accessmanagement/authorizedparties.resourceowner``
  variant that *is* machine-callable is for service owners looking up somebody
  else's list, which Myntos is not and will not be.
- **The scope has to be on the ID-porten client.**
  ``altinn:accessmanagement/authorizedparties``, registered in
  Samarbeidsportalen and requested in ``IDPORTEN_SCOPES``. Missing, the answer
  is a 403 that names nothing — the same shape of failure the ``altinn:enduser``
  scope produced, and the reason :func:`fetch_authorized_parties` spells the cause out
  in its error message.

``includeAltinn2`` is sent as true. Altinn 2 is still where a large share of
Norwegian businesses' roles actually sit, and a list that quietly omitted them
would be missing precisely the older, smaller clients an accountant is most
likely to be keeping books for.

Nothing here is stored. The list is a suggestion to whoever is filling in the
setup form, exactly as :mod:`myntos.integrations.brreg` is: the person stays the
one who decides which books get created.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from myntos.core.config import get_settings
from myntos.integrations.altinn.tokens import AltinnAuthError, AltinnError

# The scope the ID-porten client must carry for the call to be answered. A
# constant rather than configuration, unlike the Skatteetaten scopes: there is
# no version of this call that works without exactly this one, so a deployment
# has nothing to choose.
SCOPE = "altinn:accessmanagement/authorizedparties"

_PATH = "/accessmanagement/api/v1/authorizedparties"

# The list is a few kilobytes even for a large accounting firm, but Altinn
# assembles it from several registers and is not fast about it.
_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _client() -> httpx.AsyncClient:
    """The HTTP client one request runs on.

    Its own function so tests have a seam to replace with a stub transport, and
    so the timeout is decided in exactly one place.
    """
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT)


@dataclass(frozen=True)
class AuthorizedParty:
    """One entry in the list: something the person may act for.

    ``party_id`` is Altinn's own identifier for the party, and it is what an
    instance is later created under. Carried even though nothing reads it yet —
    an orgnr is not the same key, and looking it up again at submission time
    would be a second call for something we have already been told.

    ``person_id`` is a fødselsnummer, present only for person parties (the user
    themselves, and anyone who has delegated to them personally). It is parsed
    because a personal skattemelding is filed for exactly such a party, and it
    is never sent to a browser — see
    :class:`myntos.api.v1.skatt.AuthorizedPartyResponse`.

    ``only_hierarchy_element_with_no_access`` marks a party that is in the list
    only to hold its subunits: the person has no rights on it at all. Altinn
    includes it so a tree can be drawn. Offering it as something to file for
    would be offering a 403.
    """

    party_id: int
    name: str
    orgnr: str | None
    person_id: str | None
    party_uuid: str | None
    type: str
    unit_type: str | None
    is_deleted: bool
    only_hierarchy_element_with_no_access: bool
    authorized_roles: tuple[str, ...] = ()
    authorized_resources: tuple[str, ...] = ()
    subunits: tuple[AuthorizedParty, ...] = ()

    @property
    def is_organization(self) -> bool:
        """Whether this party is something that can have a set of books.

        Keyed on the orgnr rather than on ``type``: the type is Altinn's own
        enum and has grown a member before (self-identified users), while "has
        an organisasjonsnummer" is the property that actually decides whether
        Myntos can keep books for it.
        """
        return self.orgnr is not None

    @property
    def is_usable(self) -> bool:
        """Whether the person can actually do anything as this party."""
        return not self.is_deleted and not self.only_hierarchy_element_with_no_access


async def fetch_authorized_parties(
    altinn_token: str, *, include_altinn2: bool = True
) -> list[AuthorizedParty]:
    """The parties the holder of ``altinn_token`` may act for.

    The tree is returned as Altinn returns it — main units carrying their
    subunits — because the hierarchy is information a chooser should be able to
    show. Use :func:`flatten` when a flat list is what is wanted.
    """
    base = get_settings().altinn_platform_base_url.rstrip("/")
    url = f"{base}{_PATH}"
    async with _client() as http:
        try:
            response = await http.get(
                url,
                params={"includeAltinn2": "true" if include_altinn2 else "false"},
                headers={
                    "Authorization": f"Bearer {altinn_token}",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as error:
            raise AltinnError(f"could not reach Altinn at {base}") from error

    if response.status_code in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
        # Both codes arrive bare, and each has one overwhelmingly likely cause.
        # Naming them here is the difference between a five-minute fix in
        # Samarbeidsportalen and an afternoon spent on the token exchange.
        raise AltinnAuthError(
            f"Altinn would not list the authorized parties ({response.status_code})"
            f" - the token needs the {SCOPE} scope, registered on the ID-porten"
            " client and requested in IDPORTEN_SCOPES, and it has to be exchanged"
            " from a *person's* ID-porten token: a machine token carries no user"
            " for Altinn to build a list for"
        )
    if response.status_code != httpx.codes.OK:
        raise AltinnError(f"Altinn would not list the authorized parties ({response.status_code})")

    try:
        payload: Any = response.json()
    except ValueError as error:
        raise AltinnError("Altinn's authorized-parties answer was not JSON") from error

    return [_party(entry) for entry in _entries(payload)]


def _entries(payload: Any) -> list[dict[str, Any]]:
    """The party objects out of the body, whichever envelope they arrived in.

    The v1 endpoint answers with a bare JSON array. The newer ``enduser`` API
    that is set to replace it answers ``{"data": [...], "links": {...}}``.
    Accepting both costs three lines and makes that migration a URL change
    rather than a parser change. Anything else is an error rather than an empty
    list: "no parties" and "we did not understand the answer" must not look
    alike to a user who is waiting to be shown their companies.
    """
    if isinstance(payload, list):
        return [entry for entry in payload if isinstance(entry, dict)]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [entry for entry in data if isinstance(entry, dict)]
    raise AltinnError("Altinn's authorized-parties answer was not a list of parties")


def _party(entry: dict[str, Any]) -> AuthorizedParty:
    """One party, and its subunits, out of the JSON."""
    return AuthorizedParty(
        party_id=_int(entry.get("partyId")),
        name=str(entry.get("name") or "").strip(),
        orgnr=_digits(entry.get("organizationNumber")),
        person_id=_digits(entry.get("personId")),
        party_uuid=_text(entry.get("partyUuid")),
        # Serialized as a string ("Organization", "Person") by the current API
        # and as a number by an older one. Kept as text either way: nothing here
        # branches on it, and a number rendered as if it were a name would be a
        # lie told to whoever reads the list.
        type=str(entry.get("type") or "").strip(),
        unit_type=_text(entry.get("unitType")),
        is_deleted=bool(entry.get("isDeleted")),
        only_hierarchy_element_with_no_access=bool(entry.get("onlyHierarchyElementWithNoAccess")),
        authorized_roles=_strings(entry.get("authorizedRoles")),
        authorized_resources=_strings(entry.get("authorizedResources")),
        subunits=tuple(_party(sub) for sub in _entries_of(entry.get("subunits"))),
    )


def _entries_of(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, dict)]


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _digits(value: Any) -> str | None:
    """An identifier reduced to its digits, or ``None`` when there is none.

    Altinn returns an empty string rather than omitting the field for a party
    that has no number, and a formatted orgnr would not match the one
    Enhetsregisteret is asked about a moment later.
    """
    if not isinstance(value, str):
        return None
    digits = "".join(character for character in value if character.isdigit())
    return digits or None


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def flatten(parties: Iterable[AuthorizedParty]) -> Iterator[AuthorizedParty]:
    """Every party in the tree, depth first, a main unit before its subunits.

    A subunit is reported for in its own right — it has its own orgnr and its
    own books — so a chooser that offered only main units would hide exactly the
    entities an accountant is most often engaged to handle.
    """
    for party in parties:
        yield party
        yield from flatten(party.subunits)
