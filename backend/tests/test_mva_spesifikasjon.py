"""Uttak, tap på krav, justering and tilbakeføring, from the ledger to the XSD.

Three layers, each against something Skatteetaten published rather than against
our own reading of it:

- the table of which code may carry which spesifikasjon, against the vendored
  ``mvaKodeSAFT.xml`` and ``mvaSpesifikasjon.xml``;
- the aggregator's output for each, against the *shape* of the same line in
  Skatteetaten's ``eksempelMedAlleTilfeller.xml`` (which elements, which sign),
  and the whole melding against the XSD;
- the posting through the API, read back as the melding the period would file,
  including that a reversal nets out inside its own line and that another
  company's books never see it.
"""

from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from myntos.domain.mva import melding as m
from myntos.domain.mva.codes import SPESIFIKASJON_CODES, Meldingskategori, Spesifikasjon
from myntos.domain.mva.periods import PeriodType, Termin
from tests.conftest import join_company, register, write

FIXTURES = Path(__file__).parent / "fixtures" / "mva"
XSD = FIXTURES / "no.skatteetaten.fastsetting.avgift.mva.skattemeldingformerverdiavgift.v1.0.xsd"
EXAMPLE = FIXTURES / "eksempelMedAlleTilfeller.xml"
KODELISTER = FIXTURES / "kodelister"
NS = {"m": m.MELDING_NAMESPACE}
ORGNR = "910000004"
OTHER_ORGNR = "910000012"
TERMIN = Termin(PeriodType.TO_MAANEDER, 2026, 2)
YEAR = date.today().year - 1

ZERO = Decimal("0.00")

needs_xmllint = pytest.mark.skipif(shutil.which("xmllint") is None, reason="no xmllint")


def _xsd_valid(path: Path) -> None:
    result = subprocess.run(  # noqa: S603 - fixed arguments, test-only
        ["xmllint", "--noout", "--schema", str(XSD), str(path)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


# --- The code list ------------------------------------------------------------------

# mvaKodeSAFT.xml spells the flags in ASCII; the spesifikasjon values themselves
# carry the Norwegian letters (mvaSpesifikasjon.xml).
_FLAG = {
    Spesifikasjon.UTTAK: "uttak",
    Spesifikasjon.TAP_PAA_KRAV: "tapPaaKrav",
    Spesifikasjon.JUSTERING: "justering",
    Spesifikasjon.TILBAKEFOERING: "tilbakefoeringAvInngaaendeMerverdiavgift",
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _flags_by_code() -> dict[str, set[str]]:
    root = ET.parse(KODELISTER / "mvaKodeSAFT.xml").getroot()  # noqa: S314 - vendored file
    found: dict[str, set[str]] = {}
    for kode in root:
        if _local(kode.tag) != "kode":
            continue
        name = next(c.text for c in kode if _local(c.tag) == "tekniskNavn")
        flags: set[str] = set()
        for extra in (c for c in kode if _local(c.tag) == "kodetillegg"):
            for item in extra:
                if (item.text or "").strip() == "SANN":
                    flags.add(_local(item.tag))
        found[str(name)] = flags
    return found


def test_every_code_and_spesifikasjon_pair_is_one_the_code_list_allows() -> None:
    flags = _flags_by_code()
    for kind, codes in SPESIFIKASJON_CODES.items():
        for code in codes:
            assert _FLAG[kind] in flags[code], f"{kind.value} on code {code}"


def test_the_spesifikasjon_values_are_spelled_as_published() -> None:
    root = ET.parse(KODELISTER / "mvaSpesifikasjon.xml").getroot()  # noqa: S314
    published = {
        next(c.text for c in kode if _local(c.tag) == "tekniskNavn")
        for kode in root
        if _local(kode.tag) == "kode"
    }
    # A subset: the list also has a kompensasjon-only value Myntos never files.
    assert {kind.value for kind in Spesifikasjon} <= published


# --- The aggregator against Skatteetaten's example ---------------------------------


def _example_lines() -> dict[str, ET.Element]:
    root = ET.parse(EXAMPLE).getroot()  # noqa: S314 - vendored file
    return {
        str(line.findtext("m:spesifikasjon", namespaces=NS)): line
        for line in root.iterfind(".//m:mvaSpesifikasjonslinje", NS)
        if line.find("m:spesifikasjon", NS) is not None
    }


@needs_xmllint
def test_skatteetatens_example_is_valid_against_the_schema() -> None:
    """The control: if this fails, the fixture or the schema moved, not our code."""
    _xsd_valid(EXAMPLE)


def _line(
    code: str,
    spes: Spesifikasjon,
    *,
    is_debit: bool,
    base: str,
    debit_tax: str = "0.00",
    credit_tax: str = "0.00",
    rate: str = "0.250000",
) -> m.TaxedLine:
    return m.TaxedLine(
        code=code,
        rate=Decimal(rate),
        is_debit=is_debit,
        base=Decimal(base),
        debit_tax=Decimal(debit_tax),
        credit_tax=Decimal(credit_tax),
        spesifikasjon=spes.value,
    )


# What the ledger holds for each, as myntos.domain.mva.adjustments posts it.
LEDGER_LINES = {
    Spesifikasjon.UTTAK: _line(
        "3", Spesifikasjon.UTTAK, is_debit=False, base="1000.00", credit_tax="250.00"
    ),
    Spesifikasjon.TAP_PAA_KRAV: _line(
        "1", Spesifikasjon.TAP_PAA_KRAV, is_debit=True, base="1000.00", debit_tax="250.00"
    ),
    Spesifikasjon.JUSTERING: _line(
        "1", Spesifikasjon.JUSTERING, is_debit=True, base="400.00", credit_tax="100.00"
    ),
    Spesifikasjon.TILBAKEFOERING: _line(
        "1", Spesifikasjon.TILBAKEFOERING, is_debit=True, base="1600.00", credit_tax="400.00"
    ),
}


@pytest.mark.parametrize("kind", list(LEDGER_LINES))
def test_each_spesifikasjon_has_the_shape_skatteetatens_example_gives_it(
    kind: Spesifikasjon,
) -> None:
    (line,) = m.aggregate([LEDGER_LINES[kind]])
    example = _example_lines()[kind.value]

    assert line.spesifikasjon == kind.value
    assert line.mva_kode == example.findtext("m:mvaKode", namespaces=NS)
    # The same elements present: a grunnlag and a sats on an uttak, neither on
    # the three that are reported like a deduction.
    assert (line.grunnlag is not None) == (example.find("m:grunnlag", NS) is not None)
    assert (line.sats is not None) == (example.find("m:sats", NS) is not None)
    example_sign = Decimal(str(example.findtext("m:merverdiavgift", namespaces=NS))) > 0
    assert (line.merverdiavgift > ZERO) == example_sign


def test_a_spesifikasjon_is_its_own_line_beside_the_plain_one() -> None:
    plain = m.TaxedLine("1", Decimal("0.25"), True, Decimal("800.00"), Decimal("200.00"), ZERO)
    lines = m.aggregate([plain, LEDGER_LINES[Spesifikasjon.TAP_PAA_KRAV]])
    assert [(ln.mva_kode, ln.spesifikasjon, str(ln.merverdiavgift)) for ln in lines] == [
        ("1", None, "-200.00"),
        ("1", "tapPåKrav", "-250.00"),
    ]


def test_a_spesifikasjon_on_a_code_it_does_not_belong_to_is_refused() -> None:
    wrong = _line("6", Spesifikasjon.UTTAK, is_debit=False, base="100.00", rate="0")
    with pytest.raises(m.MeldingError, match="uttak"):
        m.aggregate([wrong])


@needs_xmllint
def test_a_melding_with_every_spesifikasjon_is_valid(tmp_path: Path) -> None:
    melding = m.build(
        list(LEDGER_LINES.values()),
        termin=TERMIN,
        category=Meldingskategori.ALMINNELIG,
        orgnr=ORGNR,
    )
    assert {line.spesifikasjon for line in melding.lines} == {k.value for k in LEDGER_LINES}
    # 250 owed on the uttak, 250 back on the loss, 100 and 400 owed back.
    assert melding.fastsatt_merverdiavgift == Decimal("500.00")
    target = tmp_path / "melding.xml"
    target.write_bytes(m.melding_xml(melding, reference="spes-1", system_version="1.0"))
    _xsd_valid(target)
    written = ET.parse(target).getroot()  # noqa: S314 - written by this test
    assert sorted(
        str(e.text) for e in written.iterfind(".//m:mvaSpesifikasjonslinje/m:spesifikasjon", NS)
    ) == sorted(k.value for k in LEDGER_LINES)


@needs_xmllint
@pytest.mark.parametrize("kind", list(LEDGER_LINES))
def test_a_melding_with_one_spesifikasjon_is_valid(kind: Spesifikasjon, tmp_path: Path) -> None:
    melding = m.build(
        [LEDGER_LINES[kind]], termin=TERMIN, category=Meldingskategori.ALMINNELIG, orgnr=ORGNR
    )
    target = tmp_path / f"{kind.name}.xml"
    target.write_bytes(m.melding_xml(melding, reference=f"spes-{kind.name}", system_version="1"))
    _xsd_valid(target)


# --- Through the API -------------------------------------------------------------------


def _books(
    client: TestClient, *, orgnr: str = ORGNR, vat_registered: bool = True, session: Any = None
) -> dict[str, Any]:
    session = session or register(client)
    response = write(
        client,
        "POST",
        "/api/v1/companies",
        json={
            "organization_id": session["memberships"][0]["organization_id"],
            "orgnr": orgnr,
            "name": f"Firma {orgnr}",
            "first_financial_year": YEAR,
            "vat_registered": vat_registered,
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    body["session"] = session
    return body


def _record(client: TestClient, company_id: int, **fields: Any) -> Any:
    payload = {"occurred_on": f"{YEAR}-02-10", "description": "test", **fields}
    return write(
        client, "POST", f"/api/v1/companies/{company_id}/mva/spesifikasjoner", json=payload
    )


def _period_lines(client: TestClient, company_id: int) -> list[tuple[str, str | None, str]]:
    response = client.get(
        f"/api/v1/companies/{company_id}/mva/termin", params={"aar": YEAR, "termin": 1}
    )
    assert response.status_code == 200, response.text
    return [
        (line["mva_kode"], line["spesifikasjon"], line["merverdiavgift"])
        for line in response.json()["lines"]
    ]


def test_each_kind_reaches_the_melding_on_its_own_line(client: TestClient) -> None:
    company = _books(client)
    cid = company["id"]
    assert (
        _record(client, cid, spesifikasjon="uttak", tax_code="3", amount="1000.00").status_code
        == 201
    )
    assert (
        _record(client, cid, spesifikasjon="tapPåKrav", tax_code="1", amount="1250.00").status_code
        == 201
    )
    assert (
        _record(client, cid, spesifikasjon="justering", tax_code="1", amount="-80.00").status_code
        == 201
    )
    assert (
        _record(
            client,
            cid,
            spesifikasjon="tilbakeføringAvInngåendeMerverdiavgift",
            tax_code="1",
            amount="400.00",
        ).status_code
        == 201
    )

    lines = _period_lines(client, cid)
    assert ("3", "uttak", "250.00") in lines
    assert ("1", "tapPåKrav", "-250.00") in lines
    assert ("1", "justering", "-80.00") in lines
    assert ("1", "tilbakeføringAvInngåendeMerverdiavgift", "400.00") in lines

    # The period still agrees with the VAT accounts: every one of these went
    # through 2700 or 2710, which is what the settlement later moves.
    period = client.get(
        f"/api/v1/companies/{cid}/mva/termin", params={"aar": YEAR, "termin": 1}
    ).json()
    assert Decimal(period["fastsatt_merverdiavgift"]) == Decimal("320.00")

    xml = client.get(f"/api/v1/companies/{cid}/mva/termin/xml", params={"aar": YEAR, "termin": 1})
    assert xml.status_code == 200
    assert "<spesifikasjon>tapPåKrav</spesifikasjon>".encode() in xml.content


@needs_xmllint
def test_the_downloaded_melding_with_spesifikasjoner_is_valid(
    client: TestClient, tmp_path: Path
) -> None:
    company = _books(client)
    _record(client, company["id"], spesifikasjon="uttak", tax_code="31", amount="200.00")
    _record(client, company["id"], spesifikasjon="tapPåKrav", tax_code="13", amount="112.00")
    xml = client.get(
        f"/api/v1/companies/{company['id']}/mva/termin/xml", params={"aar": YEAR, "termin": 1}
    )
    target = tmp_path / "termin.xml"
    target.write_bytes(xml.content)
    _xsd_valid(target)


def test_a_tap_paa_krav_reduces_the_customers_balance(client: TestClient) -> None:
    company = _books(client)
    party = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/parties",
        json={"party_type": "kunde", "name": "Konkurs AS"},
    ).json()
    response = _record(
        client,
        company["id"],
        spesifikasjon="tapPåKrav",
        tax_code="1",
        amount="1250.00",
        party_id=party["id"],
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["account_number"] == "7830"
    assert Decimal(body["merverdiavgift"]) == Decimal("-250.00")


def test_a_reversal_cancels_inside_its_own_line(client: TestClient) -> None:
    company = _books(client)
    first = _record(
        client, company["id"], spesifikasjon="tapPåKrav", tax_code="1", amount="1250.00"
    )
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/mva/spesifikasjoner/{first.json()['id']}/reversering",
    )
    assert response.status_code == 201, response.text
    assert response.json()["is_reversal"] is True
    assert _period_lines(client, company["id"]) == []

    listed = client.get(
        f"/api/v1/companies/{company['id']}/mva/spesifikasjoner", params={"aar": YEAR}
    ).json()
    original = next(item for item in listed["entries"] if item["id"] == first.json()["id"])
    assert original["reversed_by_id"] == response.json()["id"]


@pytest.mark.parametrize(
    ("fields", "status"),
    [
        # Uttak is a sale; it has no place under an input code.
        ({"spesifikasjon": "uttak", "tax_code": "1", "amount": "100.00"}, 422),
        # Only justering is signed.
        ({"spesifikasjon": "tapPåKrav", "tax_code": "1", "amount": "-100.00"}, 422),
        ({"spesifikasjon": "justering", "tax_code": "1", "amount": "0.00"}, 422),
        # A VAT account as the other side would post VAT against itself.
        (
            {
                "spesifikasjon": "justering",
                "tax_code": "1",
                "amount": "100.00",
                "account_number": "2710",
            },
            422,
        ),
        # Not a spesifikasjon Myntos records.
        ({"spesifikasjon": "kompensasjonsrett", "tax_code": "1", "amount": "100.00"}, 422),
    ],
)
def test_what_does_not_fit_is_refused(
    client: TestClient, fields: dict[str, str], status: int
) -> None:
    company = _books(client)
    assert _record(client, company["id"], **fields).status_code == status


def test_an_unregistered_company_has_no_melding_to_put_it_in(client: TestClient) -> None:
    company = _books(client, vat_registered=False)
    response = _record(client, company["id"], spesifikasjon="uttak", tax_code="3", amount="10.00")
    assert response.status_code == 409
    assert response.json()["code"] == "not_vat_registered"


def test_a_viewer_cannot_record_one(client: TestClient) -> None:
    company = _books(client)
    join_company(client, company["id"], email="revisor@example.no", role="viewer")
    response = _record(client, company["id"], spesifikasjon="uttak", tax_code="3", amount="1.00")
    assert response.status_code == 403


def test_another_companys_adjustments_stay_out(client: TestClient) -> None:
    mine = _books(client)
    theirs = _books(client, orgnr=OTHER_ORGNR, session=mine["session"])
    _record(client, theirs["id"], spesifikasjon="uttak", tax_code="3", amount="1000.00")

    listed = client.get(
        f"/api/v1/companies/{mine['id']}/mva/spesifikasjoner", params={"aar": YEAR}
    ).json()
    assert listed["entries"] == []
    assert _period_lines(client, mine["id"]) == []
