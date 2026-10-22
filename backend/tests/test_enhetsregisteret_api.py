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
from tests.conftest import join_company, register, write

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

# The same entry with everything the register actually returns for it. Kept
# apart from UNIT so the tests above still describe the minimum an entry can be:
# most of these fields are absent for most companies, and a lookup has to cope
# with that rather than with Equinor.
FULL_UNIT: dict[str, Any] = UNIT | {
    "historiskeNavn": [{"navn": "STATOIL ASA", "fraDato": "2001-05-11", "tilDato": "2007-10-01"}],
    "hjemmeside": "www.equinor.com",
    "telefon": "51 99 00 00",
    # The real entry carries the kommune on the business address too; UNIT above
    # deliberately does not, so the minimal case stays minimal.
    "forretningsadresse": {
        "adresse": ["Forusbeen 50"],
        "postnummer": "4035",
        "poststed": "STAVANGER",
        "kommune": "STAVANGER",
        "kommunenummer": "1103",
        "land": "Norge",
    },
    "postadresse": {
        "adresse": ["Postboks 8500"],
        "postnummer": "4035",
        "poststed": "STAVANGER",
        "kommune": "STAVANGER",
        "kommunenummer": "1103",
        "land": "Norge",
    },
    "naeringskode1": {"kode": "06.100", "beskrivelse": "Utvinning av råolje"},
    "naeringskode2": {"kode": "06.200", "beskrivelse": "Utvinning av naturgass"},
    "antallAnsatte": 21239,
    "harRegistrertAntallAnsatte": True,
    "stiftelsesdato": "1972-09-18",
    "registreringsdatoEnhetsregisteret": "1995-03-12",
    "sisteInnsendteAarsregnskap": "2025",
    "institusjonellSektorkode": {"kode": "1120", "beskrivelse": "Statlig eide aksjeselskaper mv."},
    "erIKonsern": True,
    "maalform": "Bokmål",
    "kapital": {"belop": 5976872600.00, "antallAksjer": 2390749040, "valuta": "NOK"},
    "konkurs": False,
    "underAvvikling": False,
}

# The address key a subunit carries — and the one the lookup used to miss.
SUBUNIT: dict[str, Any] = {
    "organisasjonsnummer": ORGNR,
    "navn": "EQUINOR ASA AVD BERGEN",
    "organisasjonsform": {"kode": "BEDR", "beskrivelse": "Underenhet"},
    "beliggenhetsadresse": {
        "adresse": ["Møllendalsveien 4"],
        "postnummer": "5009",
        "poststed": "BERGEN",
    },
    "overordnetEnhet": "826649992",
    "registrertIMvaregisteret": False,
}

# One role of each shape the register emits: a person, a person elected by the
# employees, and a company.
ROLES: dict[str, Any] = {
    "rollegrupper": [
        {
            "type": {"kode": "DAGL", "beskrivelse": "Daglig leder"},
            "roller": [
                {
                    "type": {"kode": "DAGL", "beskrivelse": "Daglig leder"},
                    "person": {
                        "fodselsdato": "1968-05-04",
                        "navn": {"fornavn": "Anders", "etternavn": "Opedal"},
                    },
                    "avregistrert": False,
                }
            ],
        },
        {
            "type": {"kode": "STYR", "beskrivelse": "Styre"},
            "roller": [
                {
                    "type": {"kode": "LEDE", "beskrivelse": "Styrets leder"},
                    "person": {"navn": {"fornavn": "Jarle Kjell", "etternavn": "Roth"}},
                    "avregistrert": False,
                },
                {
                    "type": {"kode": "MEDL", "beskrivelse": "Styremedlem"},
                    "person": {"navn": {"fornavn": "Hilde", "etternavn": "Møllerstad"}},
                    "valgtAv": {"kode": "AREP", "beskrivelse": "Representant for de ansatte"},
                    "avregistrert": False,
                },
            ],
        },
        {
            "type": {"kode": "REVI", "beskrivelse": "Revisor"},
            "roller": [
                {
                    "type": {"kode": "REVI", "beskrivelse": "Revisor"},
                    "enhet": {
                        "organisasjonsnummer": "976389387",
                        "navn": "ERNST & YOUNG AS",
                    },
                    "avregistrert": False,
                }
            ],
        },
    ]
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
    body = response.json()
    # The fields the signup form fills in, asserted exactly: these are the ones
    # a wrong answer would put on an invoice. Merged rather than compared whole,
    # because the entry now also carries everything else the register holds, and
    # listing forty mostly-null fields here would hide these twelve.
    assert (
        body
        | {
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
        == body
    )
    # A field the stub does not carry comes back absent rather than as a guess.
    # Worth asserting, because "the register did not say" and "we failed to read
    # it" render identically in a form and must not.
    assert body["industry_code"] is None
    assert body["employees"] is None


def test_the_whole_register_entry_comes_through(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    """Everything the register holds, not the handful the form asks for.

    Each field missing here is a field somebody types instead, which is the one
    step in the setup where a mistake produces books for a subtly wrong entity.
    """
    brreg_answers(lambda _request: httpx.Response(200, json=FULL_UNIT))

    body = signed_in(client).get(f"/api/v1/enhetsregisteret/{ORGNR}").json()

    assert body["industry_code"] == "06.100"
    assert body["industry_description"] == "Utvinning av råolje"
    assert body["secondary_industries"] == [["06.200", "Utvinning av naturgass"]]
    assert body["website"] == "www.equinor.com"
    assert body["phone"] == "51 99 00 00"
    assert body["employees"] == 21239
    assert body["established_on"] == "1972-09-18"
    assert body["registered_on"] == "1995-03-12"
    assert body["last_submitted_annual_accounts"] == "2025"
    assert body["sector_description"] == "Statlig eide aksjeselskaper mv."
    assert body["capital"] == {
        # Two decimals: JSON handed us a float, and str() is not a currency
        # formatter.
        "amount": "5976872600.00",
        "currency": "NOK",
        "shares": 2390749040,
    }
    assert body["former_names"] == ["STATOIL ASA"]
    assert body["in_group"] is True
    assert body["language"] == "Bokmål"
    # The postal address is reported only when it differs from the business one.
    assert body["postal_address_line"] == "Postboks 8500"
    assert body["municipality"] == "STAVANGER"


def test_an_employee_count_nobody_registered_is_not_zero(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    """``harRegistrertAntallAnsatte`` false means "we were not told".

    Reporting it as 0 would be an assertion the register never made, and a
    business with no employees is a different thing from one whose count nobody
    has reported.
    """
    payload = FULL_UNIT | {"antallAnsatte": 0, "harRegistrertAntallAnsatte": False}
    brreg_answers(lambda _request: httpx.Response(200, json=payload))

    body = signed_in(client).get(f"/api/v1/enhetsregisteret/{ORGNR}").json()
    assert body["employees"] is None


def test_a_subunit_keeps_its_beliggenhetsadresse(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    """A subunit has no ``forretningsadresse`` at all.

    It carries ``beliggenhetsadresse`` instead, and a lookup that knew only the
    two other keys returned a subunit with no address whatsoever — silently, as
    three nulls that look like a register which simply did not know.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if "/underenheter/" in request.url.path:
            return httpx.Response(200, json=SUBUNIT)
        return httpx.Response(404)

    brreg_answers(handler)

    body = signed_in(client).get(f"/api/v1/enhetsregisteret/{ORGNR}").json()
    assert body["address_line"] == "Møllendalsveien 4"
    assert body["city"] == "BERGEN"
    assert body["is_subunit"] is True
    assert body["parent_orgnr"] == "826649992"


def test_roles_come_back_flattened_with_their_group(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    """The board, the daglig leder and the revisor, in the register's own order.

    Flattened because every consumer wants a list; the group each role sits in
    is copied onto it rather than lost, since "styremedlem" and "varamedlem" are
    only meaningful under ``STYR``.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/roller"):
            return httpx.Response(200, json=ROLES)
        return httpx.Response(200, json=FULL_UNIT)

    brreg_answers(handler)

    body = signed_in(client).get(f"/api/v1/enhetsregisteret/{ORGNR}/roller").json()

    assert [(role["group"], role["code"], role["name"]) for role in body] == [
        ("DAGL", "DAGL", "Anders Opedal"),
        ("STYR", "LEDE", "Jarle Kjell Roth"),
        ("STYR", "MEDL", "Hilde Møllerstad"),
        ("REVI", "REVI", "ERNST & YOUNG AS"),
    ]
    # A company holding a role is identified by its orgnr, a person only by
    # name: the register pairs people with a birth date, and that is personal
    # data with no bookkeeping use.
    assert body[3]["orgnr"] == "976389387"
    assert all("fodselsdato" not in role for role in body)
    assert body[2]["elected_by"] == "Representant for de ansatte"


def test_a_resigned_role_is_not_offered_as_current(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    """The list answers "who may act for this company today".

    A former daglig leder in it is a wrong answer, not a historical note.
    """
    payload = {
        "rollegrupper": [
            {
                "type": {"kode": "DAGL", "beskrivelse": "Daglig leder"},
                "roller": [
                    {
                        "type": {"kode": "DAGL", "beskrivelse": "Daglig leder"},
                        "person": {"navn": {"fornavn": "Gammel", "etternavn": "Leder"}},
                        "fratraadt": True,
                    }
                ],
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/roller"):
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json=FULL_UNIT)

    brreg_answers(handler)

    assert signed_in(client).get(f"/api/v1/enhetsregisteret/{ORGNR}/roller").json() == []


def test_a_subunits_roles_are_read_from_its_main_unit(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    """``/underenheter/{orgnr}/roller`` is a 404, not an empty list.

    Roles are registered on the main unit only, so a department's are its
    parent's. Asserted on the path asked for, because the symptom of getting it
    wrong is an empty board rather than an error.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/roller"):
            return httpx.Response(200, json=ROLES)
        if "/underenheter/" in request.url.path:
            return httpx.Response(200, json=SUBUNIT)
        return httpx.Response(404)

    requested = brreg_answers(handler)

    response = signed_in(client).get(f"/api/v1/enhetsregisteret/{ORGNR}/roller")

    assert response.status_code == 200
    assert requested[-1] == "/enhetsregisteret/api/enheter/826649992/roller"


def test_a_unit_with_nothing_registered_is_an_empty_list_not_a_404(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    """A missing role list is not a missing company."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/roller"):
            return httpx.Response(404)
        return httpx.Response(200, json=FULL_UNIT)

    brreg_answers(handler)

    response = signed_in(client).get(f"/api/v1/enhetsregisteret/{ORGNR}/roller")
    assert response.status_code == 200
    assert response.json() == []


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


def test_deleted_unit_answered_as_a_document_says_so(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    # The register's newer answer for a deleted unit, as it came for a real one
    # on 2026-10-22: a 200 with almost nothing in it. Read as a live company it
    # was a company with no address that could be invoiced and set up as any
    # other; it has to say it is gone.
    brreg_answers(
        lambda _request: httpx.Response(
            200,
            json={
                "organisasjonsnummer": ORGNR,
                "navn": "EQUINOR ASA",
                "organisasjonsform": {"kode": "ASA", "beskrivelse": "Allmennaksjeselskap"},
                "historiskeNavn": [],
                "respons_klasse": "SlettetEnhet",
                "slettedato": "2026-07-21",
            },
        )
    )

    response = signed_in(client).get(f"/api/v1/enhetsregisteret/{ORGNR}")

    assert response.status_code == 200
    assert response.json()["deleted_on"] == "2026-07-21"
    assert response.json()["name"] == "EQUINOR ASA"
    assert response.json()["address_line"] is None


def test_live_unit_is_not_deleted(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    brreg_answers(lambda _request: httpx.Response(200, json=UNIT))

    response = signed_in(client).get(f"/api/v1/enhetsregisteret/{ORGNR}")

    assert response.json()["deleted_on"] is None


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


# --- Copying the register onto a company -----------------------------------------
#
# The lookup above is advisory: it fills a form somebody is looking at. These
# are about the copy that gets *stored*, which is a different question — a stale
# VAT flag is not a slow form, it is a wrong claim repeated on every invoice.


def _company(client: TestClient, session: dict[str, Any]) -> dict[str, Any]:
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": session["memberships"][0]["organization_id"],
            "orgnr": ORGNR,
            "name": "Navnet mitt AS",
            "first_financial_year": 2026,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def test_a_new_company_is_filled_in_from_the_register(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    """Set up with an orgnr and a name; get the address and the næringskode too.

    The signup form only ever looked up a name and an address. Every other field
    the register holds was a field somebody typed later, or did not — and the
    næringskode is one a næringsspesifikasjon asks for by name.
    """
    brreg_answers(lambda _request: httpx.Response(200, json=FULL_UNIT))
    session = register(client)

    company = _company(client, session)

    assert company["industry_code"] == "06.100"
    assert company["industry_description"] == "Utvinning av råolje"
    assert company["website"] == "www.equinor.com"
    assert company["phone"] == "51 99 00 00"
    assert company["address_line"] == "Forusbeen 50"
    assert company["register_synced_at"] is not None


def test_a_register_that_is_down_does_not_stop_a_signup(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    """The books are the point; the register is a convenience.

    Somebody setting up on a Sunday evening must not be told to come back later
    because Brønnøysund is not answering.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nede", request=request)

    brreg_answers(handler)
    session = register(client)

    company = _company(client, session)
    assert company["name"] == "Navnet mitt AS"
    # And it is visibly un-synced rather than quietly claiming to be current.
    assert company["register_synced_at"] is None
    assert company["industry_code"] is None


def test_a_refresh_says_what_it_moved(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    """A silent rewrite of the VAT flag is a change to every future invoice.

    So the answer names the fields rather than reporting success, and the second
    refresh — where the register agrees with everything — reports nothing moved
    rather than repeating the list.
    """
    answers = [httpx.Response(200, json=FULL_UNIT | {"registrertIMvaregisteret": False})]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/roller"):
            return httpx.Response(200, json=ROLES)
        return answers[-1]

    brreg_answers(handler)
    session = register(client)
    company = _company(client, session)
    assert company["vat_registered"] is False

    answers.append(httpx.Response(200, json=FULL_UNIT))
    body = write(client, "POST", f"/api/v1/companies/{company['id']}/hent").json()

    assert body["changed"] == ["vat_registered"]
    assert body["company"]["vat_registered"] is True
    # The whole register picture rides along, so the page showing it does not
    # need a second round trip to Brønnøysund.
    assert body["unit"]["capital"]["shares"] == 2390749040
    assert body["roles"][0]["name"] == "Anders Opedal"

    again = write(client, "POST", f"/api/v1/companies/{company['id']}/hent").json()
    assert again["changed"] == []


def test_a_refresh_never_overwrites_the_name(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    """A name somebody edited and a name gone stale look identical from here.

    Only one of those should be rewritten, and the server cannot tell which is
    which — so neither is. The register's name is shown beside the stored one
    instead, which is a fact the user can act on.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/roller"):
            return httpx.Response(200, json=ROLES)
        return httpx.Response(200, json=FULL_UNIT)

    brreg_answers(handler)
    session = register(client)
    company = _company(client, session)

    body = write(client, "POST", f"/api/v1/companies/{company['id']}/hent").json()

    assert body["company"]["name"] == "Navnet mitt AS"
    assert body["unit"]["name"] == "EQUINOR ASA"
    assert "name" not in body["changed"]


def test_a_register_that_forgot_a_field_does_not_clear_ours(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    """A null is "the register never had this", not "delete what you stored".

    Treating the two the same turns a lookup into data loss, and the fields at
    stake are the ones printed on invoices.
    """
    answers = [httpx.Response(200, json=FULL_UNIT)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/roller"):
            return httpx.Response(404)
        return answers[-1]

    brreg_answers(handler)
    session = register(client)
    company = _company(client, session)
    assert company["website"] == "www.equinor.com"

    stripped = {key: value for key, value in FULL_UNIT.items() if key != "hjemmeside"}
    answers.append(httpx.Response(200, json=stripped))
    body = write(client, "POST", f"/api/v1/companies/{company['id']}/hent").json()

    assert body["company"]["website"] == "www.equinor.com"


def test_refreshing_needs_the_permission_that_editing_needs(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    """It can change what every future salgsdokument claims about the seller.

    Which is the same reason editing those fields by hand needs the permission,
    and the reason it is not simply a read.
    """
    brreg_answers(lambda _request: httpx.Response(200, json=FULL_UNIT))
    session = register(client)
    company = _company(client, session)

    join_company(
        client, company["id"], email="leser@example.no", role="viewer", full_name="Lise Leser"
    )

    refused = write(client, "POST", f"/api/v1/companies/{company['id']}/hent")
    assert refused.status_code == 403
    assert refused.json()["code"] == "permission_denied"


# --- What is kept ----------------------------------------------------------------
#
# The columns on `company` are the handful the system reads by name. Everything
# else the register answers with is kept whole in a snapshot, so that wanting
# the styret or the aksjekapital later is a question to the database rather than
# another round trip to Brønnøysund.


def _register(client: TestClient, company_id: int) -> Any:
    return client.get(f"/api/v1/companies/{company_id}/register")


def test_the_stored_answer_is_served_without_asking_the_register(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    """A page load should not spend a round trip on an answer we already have.

    The company is created — which fetches once — and then the whole register
    picture is readable without Brønnøysund being asked a second time.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/roller"):
            return httpx.Response(200, json=ROLES)
        return httpx.Response(200, json=FULL_UNIT)

    requested = brreg_answers(handler)
    session = register(client)
    company = _company(client, session)
    asked = len(requested)

    body = _register(client, company["id"]).json()

    assert len(requested) == asked, "reading the stored answer must not call the register"
    assert body["unit"]["capital"]["shares"] == 2390749040
    assert body["unit"]["former_names"] == ["STATOIL ASA"]
    assert body["fetched_at"] is not None
    assert [role["name"] for role in body["roles"]] != []


def test_nothing_stored_is_null_rather_than_an_error(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    """A register that was down when the books were set up leaves no snapshot.

    The page has to be able to tell that apart from a company with no roles, and
    `null` is how it does.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nede", request=request)

    brreg_answers(handler)
    session = register(client)
    company = _company(client, session)

    response = _register(client, company["id"])
    assert response.status_code == 200
    assert response.json() is None


def test_a_failed_role_lookup_keeps_the_board_we_already_had(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    """An old board is a better answer than no board.

    The roles are a second request and fail on their own, so a refresh whose
    unit lookup succeeded must not empty out what the last one stored.
    """
    roles_answer = [httpx.Response(200, json=ROLES)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/roller"):
            return roles_answer[-1]
        return httpx.Response(200, json=FULL_UNIT)

    brreg_answers(handler)
    session = register(client)
    company = _company(client, session)
    before = _register(client, company["id"]).json()["roles"]
    assert before != []

    roles_answer.append(httpx.Response(503))
    body = write(client, "POST", f"/api/v1/companies/{company['id']}/hent").json()

    assert body["roles"] == before
    assert _register(client, company["id"]).json()["roles"] == before


def test_a_corrected_orgnr_does_not_inherit_the_previous_company_s_answer(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    """The snapshot describes a number, not a row.

    Correcting an unconfirmed orgnr makes everything stored under the old one an
    accurate answer about a different company, which is worse than none.
    """
    brreg_answers(lambda _request: httpx.Response(200, json=FULL_UNIT))
    session = register(client)
    company = _company(client, session)
    assert _register(client, company["id"]).json() is not None

    # Another real orgnr, so the check digit holds. The stub answers for it with
    # Equinor's entry either way; what matters is that the *old* snapshot is not
    # what comes back.
    moved = write(
        client, "PATCH", f"/api/v1/companies/{company['id']}", json={"orgnr": "914778271"}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["orgnr"] == "914778271"
    assert _register(client, company["id"]).json()["unit"]["orgnr"] == ORGNR


def test_reading_the_stored_answer_is_open_to_everyone_on_the_books(
    client: TestClient, brreg_answers: Callable[[Handler], list[str]]
) -> None:
    """Unlike the refresh, which can change what an invoice claims.

    This one writes nothing, so a viewer invited to these books can still see
    who the company is.
    """
    brreg_answers(lambda _request: httpx.Response(200, json=FULL_UNIT))
    session = register(client)
    company = _company(client, session)

    join_company(
        client, company["id"], email="leser@example.no", role="viewer", full_name="Lise Leser"
    )

    response = _register(client, company["id"])
    assert response.status_code == 200
    assert response.json()["unit"]["name"] == "EQUINOR ASA"
