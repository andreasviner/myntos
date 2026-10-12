"""SAF-T end to end: real postings in, a file Skatteetaten's schema accepts out.

The pure tests in ``test_saft_export`` cover the writer and the check. What
these add is the reading: that the service finds this company's vouchers and
nobody else's, that balances carry from one year into the next though nothing
stores an opening balance, and that the download refuses rather than hand over
a file that would fail at the auditor.
"""

from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from myntos.saft.export import NAMESPACE
from tests.conftest import join_company, register, write
from tests.test_cashflow_api import (
    OTHER_ORGNR,
    THIS_YEAR,
    create_company,
    record,
    signed_in_company,
)

FIXTURES = Path(__file__).parent / "fixtures" / "saft"
NS = {"s": NAMESPACE}


def _parse(xml: bytes) -> ET.Element:
    # Our own server's answer in a test: there is no untrusted sender here.
    return ET.fromstring(xml)  # noqa: S314


def _validate(xml: bytes, version: str, tmp_path: Path) -> None:
    if shutil.which("xmllint") is None:
        pytest.skip("xmllint is not installed")
    target = tmp_path / "saft.xml"
    target.write_bytes(xml)
    schema = FIXTURES / f"Norwegian_SAF-T_Financial_Schema_v_{version}.xsd"
    result = subprocess.run(  # noqa: S603 - fixed arguments, test-only
        ["xmllint", "--noout", "--schema", str(schema), str(target)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _add_party(client: TestClient, company_id: int, party_type: str, name: str) -> int:
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/parties",
        json={"party_type": party_type, "name": name},
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def _a_year_of_books(client: TestClient, company_id: int) -> None:
    supplier = _add_party(client, company_id, "leverandor", "Papirhuset AS")
    customer = _add_party(client, company_id, "kunde", "Kunde AS")
    for kwargs in (
        {"type_code": "varekjop", "gross_amount": "1250.00", "party_id": supplier},
        {"type_code": "salg_hoy_sats", "gross_amount": "2500.00", "party_id": customer},
        {"type_code": "kontorrekvisita", "gross_amount": "99.90"},
        {"type_code": "reise", "gross_amount": "1180.00"},
    ):
        response = record(client, company_id, **kwargs)
        assert response.status_code == 201, response.text


def _accounts(root: ET.Element) -> dict[str, ET.Element]:
    return {
        str(a.findtext("s:AccountID", namespaces=NS)): a
        for a in root.findall(".//s:GeneralLedgerAccounts/s:Account", NS)
    }


def test_a_year_of_real_postings_exports_as_a_valid_file(
    client: TestClient, tmp_path: Path
) -> None:
    company = signed_in_company(client)
    _a_year_of_books(client, company["id"])

    checked = client.get(f"/api/v1/companies/{company['id']}/saft/kontroll?aar={THIS_YEAR}")
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["exportable"] is True, body["findings"]
    assert body["number_of_entries"] == 4
    assert Decimal(body["total_debit"]) == Decimal(body["total_credit"])

    response = client.get(f"/api/v1/companies/{company['id']}/saft/fil?aar={THIS_YEAR}")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/xml")
    disposition = response.headers["content-disposition"]
    assert f'filename="SAF-T Financial_{company["orgnr"]}_' in disposition
    assert disposition.endswith('_1_1.xml"')
    _validate(response.content, body["version"], tmp_path)

    root = _parse(response.content)
    assert (
        root.findtext("s:Header/s:Company/s:RegistrationNumber", namespaces=NS)
        == (company["orgnr"])
    )
    # Every line's account and party is in the master files: the export's own
    # check said so, and this is the file agreeing with it.
    accounts = _accounts(root)
    for line in root.iter(f"{{{NAMESPACE}}}Line"):
        assert line.findtext("s:AccountID", namespaces=NS) in accounts
    suppliers = root.findall(".//s:Suppliers/s:Supplier", NS)
    assert [s.findtext("s:Name", namespaces=NS) for s in suppliers] == ["Papirhuset AS"]
    # The seeded groupings are the published ones, not "NA".
    assert accounts["1920"].findtext("s:GroupingCategory", namespaces=NS) == (
        "balanseverdiForOmloepsmiddel"
    )


def test_the_newer_version_is_offered_for_a_year_that_still_accepts_both(
    client: TestClient, tmp_path: Path
) -> None:
    company = signed_in_company(client)
    _a_year_of_books(client, company["id"])
    response = client.get(
        f"/api/v1/companies/{company['id']}/saft/fil?aar={THIS_YEAR}&versjon=1.40"
    )
    assert response.status_code == 200, response.text
    root = _parse(response.content)
    assert root.findtext("s:Header/s:AuditFileVersion", namespaces=NS) == "1.40"
    _validate(response.content, "1.40", tmp_path)


def test_the_bank_balance_carries_into_the_next_year(client: TestClient, tmp_path: Path) -> None:
    """Nothing stores an opening balance, and 1 January must not empty the bank.

    The year's result has not been disposed of either, so it opens on equity,
    and the opening balance sheet still sums to zero.
    """
    company = signed_in_company(client)
    _a_year_of_books(client, company["id"])
    following = record(
        client,
        company["id"],
        type_code="kontorrekvisita",
        gross_amount="50.00",
        occurred_on=date(THIS_YEAR + 1, 2, 1).isoformat(),
    )
    assert following.status_code == 201, following.text

    first = _parse(
        client.get(f"/api/v1/companies/{company['id']}/saft/fil?aar={THIS_YEAR}").content
    )
    checked = client.get(f"/api/v1/companies/{company['id']}/saft/kontroll?aar={THIS_YEAR + 1}")
    assert checked.status_code == 200, checked.text
    codes = {f["code"] for f in checked.json()["findings"]}
    assert "udisponert_resultat" in codes
    assert checked.json()["exportable"] is True, checked.json()["findings"]

    response = client.get(f"/api/v1/companies/{company['id']}/saft/fil?aar={THIS_YEAR + 1}")
    assert response.status_code == 200, response.text
    second = _parse(response.content)
    _validate(response.content, checked.json()["version"], tmp_path)

    def side(account: ET.Element, prefix: str) -> Decimal:
        debit = account.findtext(f"s:{prefix}DebitBalance", namespaces=NS)
        credit = account.findtext(f"s:{prefix}CreditBalance", namespaces=NS)
        return Decimal(debit) if debit is not None else -Decimal(str(credit))

    closing = side(_accounts(first)["1920"], "Closing")
    assert closing != Decimal("0")
    assert side(_accounts(second)["1920"], "Opening") == closing
    # Profit and loss opens at zero.
    assert side(_accounts(second)["3000"], "Opening") == Decimal("0")
    assert sum(side(a, "Opening") for a in _accounts(second).values()) == Decimal("0")


def test_another_companys_vouchers_never_reach_the_file(client: TestClient) -> None:
    """The file is the whole of a set of books in one download; the wall is the journal join."""
    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    mine = create_company(client, organization_id)
    other = create_company(client, organization_id, orgnr=OTHER_ORGNR, name="Annen AS")
    record(client, other["id"], type_code="varekjop", gross_amount="777.00", description="Feil")
    record(client, mine["id"], type_code="varekjop", gross_amount="100.00", description="Riktig")

    root = _parse(client.get(f"/api/v1/companies/{mine['id']}/saft/fil?aar={THIS_YEAR}").content)
    descriptions = {
        t.findtext("s:Description", namespaces=NS) for t in root.findall(".//s:Transaction", NS)
    }
    assert descriptions == {"Riktig"}
    assert (
        b"777.00"
        not in client.get(f"/api/v1/companies/{mine['id']}/saft/fil?aar={THIS_YEAR}").content
    )


def test_a_company_you_are_not_a_member_of_does_not_exist(client: TestClient) -> None:
    company = signed_in_company(client)
    write(client, "POST", "/api/v1/auth/logout")
    register(client, "fremmed@example.no")
    response = client.get(f"/api/v1/companies/{company['id']}/saft/fil?aar={THIS_YEAR}")
    assert response.status_code == 404


def test_a_viewer_may_export(client: TestClient) -> None:
    """The person most likely to want this file is a revisor with read access only."""
    company = signed_in_company(client)
    record(client, company["id"], type_code="varekjop", gross_amount="100.00")
    join_company(client, company["id"], email="revisor@example.no", role="viewer")
    response = client.get(f"/api/v1/companies/{company['id']}/saft/fil?aar={THIS_YEAR}")
    assert response.status_code == 200, response.text
    root = _parse(response.content)
    person = root.find(".//s:Header/s:Company/s:Contact/s:ContactPerson", NS)
    assert person is not None
    # The contact is whoever produced the file.
    assert person.findtext("s:LastName", namespaces=NS) == "Nordmann"
    assert person.findtext("s:FirstName", namespaces=NS) == "Ola"


def test_a_year_with_no_books_is_not_found(client: TestClient) -> None:
    company = signed_in_company(client)
    response = client.get(f"/api/v1/companies/{company['id']}/saft/fil?aar=2025")
    assert response.status_code == 404
    assert response.json()["code"] == "saft_year_not_found"


def test_a_year_before_v1_30_is_refused_rather_than_written_in_the_wrong_format(
    client: TestClient,
) -> None:
    company = signed_in_company(client)
    response = client.get(f"/api/v1/companies/{company['id']}/saft/fil?aar=2024")
    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_saft_version"


def test_a_version_the_year_does_not_accept_is_refused(client: TestClient) -> None:
    company = signed_in_company(client)
    response = client.get(f"/api/v1/companies/{company['id']}/saft/kontroll?aar=2027&versjon=1.30")
    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_saft_version"


def test_an_ungrouped_account_blocks_the_download_with_the_reason(
    client: TestClient, database_url: str
) -> None:
    """The file is refused, and the refusal says which account and why."""
    company = signed_in_company(client)
    record(client, company["id"], type_code="varekjop", gross_amount="100.00")
    engine = sa.create_engine(database_url.replace("+aiosqlite", ""))
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE account_grouping SET grouping_category = 'lonnskostnad' WHERE "
                "account_id IN (SELECT id FROM account WHERE company_id = :c "
                "AND account_number = '5000')"
            ),
            {"c": company["id"]},
        )
    engine.dispose()

    response = client.get(f"/api/v1/companies/{company['id']}/saft/fil?aar={THIS_YEAR}")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "saft_not_exportable"
    assert [f["code"] for f in body["findings"]] == ["ukjent_gruppering"]
    assert "5000" in body["findings"][0]["message"]
