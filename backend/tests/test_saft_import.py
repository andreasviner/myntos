"""Reading another program's SAF-T file into the opening balance.

The file below is synthetic, in the shape Tripletex writes it, because the file
it was modelled on is a real farm's books and stays out of the repository
(regnaskaps_tests/, gitignored). Every quirk in it is one that file had: account
ids with a department and a product code after the four digits, a driftskreditt
grouped as equity, an ENK's cash deposit on the IFRS dividend post, VAT left on
2710, and last year's result never posted to equity, so the balance sheet does
not sum to zero.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from myntos.saft import reader
from myntos.saft.errors import InvalidSaftFileError
from myntos.saft.opening_import import _shared_name
from tests.conftest import write
from tests.test_arsoppgjor_api import LAST_YEAR, _books

ORGNR = "910000004"
BANK = "12345678903"

Account = tuple[str, str, str | None, str | None, str, str]


def _amounts(prefix: str, value: str) -> str:
    amount = Decimal(value)
    side = "Debit" if amount >= 0 else "Credit"
    return f"<{prefix}{side}Balance>{abs(amount):.2f}</{prefix}{side}Balance>"


def _account(account: Account) -> str:
    account_id, description, category, code, opening, closing = account
    grouping = (
        f"<GroupingCategory>{category}</GroupingCategory><GroupingCode>{code}</GroupingCode>"
        if category is not None
        else ""
    )
    return (
        f"<Account><AccountID>{account_id}</AccountID>"
        f"<AccountDescription>{description}</AccountDescription>{grouping}"
        f"<AccountType>GL</AccountType>{_amounts('Opening', opening)}"
        f"{_amounts('Closing', closing)}</Account>"
    )


def _party(tag: str, party_id: str, name: str, orgnr: str, account: str, opening: str) -> str:
    return (
        f"<{tag}><RegistrationNumber>{orgnr}</RegistrationNumber><Name>{name}</Name>"
        f"<Address><StreetName>Vegen 1</StreetName><City>NÆRBØ</City>"
        f"<PostalCode>4365</PostalCode></Address>"
        f"<{tag}ID>{party_id}</{tag}ID><BalanceAccount><AccountID>{account}</AccountID>"
        f"{_amounts('Opening', opening)}{_amounts('Closing', opening)}</BalanceAccount></{tag}>"
    )


#: A farm's balance sheet on 1 January, the way Tripletex's farm module writes it.
#: Assets 225 000, debt and equity 240 000: 15 000 short of credit is last
#: year's loss, never posted to equity.
FIXED = "balanseverdiForAnleggsmiddel"
CURRENT = "balanseverdiForOmloepsmiddel"
DRIFTSKREDITT = f"{BANK[:4]}.{BANK[4:6]}.{BANK[6:]} Driftskreditt"
ACCOUNTS: list[Account] = [
    ("1920", "Bankinnskudd", CURRENT, "1920", "100000", "100000"),
    ("125020", "Inventar (saldogruppe D)", FIXED, "1205", "50000", "50000"),
    ("125030", "Inventar skog", FIXED, "1205", "10000", "10000"),
    ("145120512", "Lagerbeh.: Storfe (Kyr)", CURRENT, "1400", "20000", "20000"),
    ("145120514", "Lagerbeh.: Storfe (Kviger)", CURRENT, "1400", "10000", "10000"),
    ("1500", "Kundefordringer", CURRENT, "1500", "30000", "30000"),
    ("2710", "Inngående merverdiavgift", "kortsiktigGjeld", "2740", "5000", "5000"),
    ("2400", "Leverandørgjeld", "kortsiktigGjeld", "2400", "-40000", "-40000"),
    ("239010", DRIFTSKREDITT, "egenkapital", "2050", "-80000", "-80000"),
    ("205510", "Innskudd kontanter", "egenkapital", "2055", "-120000", "-120000"),
    ("3071", "Melk", "salgsinntekt", "3000", "0", "0"),
    ("4020", "Kraftfôr", "varekostnad", "4005", "0", "0"),
]

CUSTOMERS = [
    ("10001", "Meieriet AS", "910000020", "1500", "20000"),
    ("10002", "Slakteriet SA", "910000039", "1500", "10000"),
]
SUPPLIERS = [("20001", "Landhandelen SA", "910000047", "2400", "-40000")]


def saft_file(
    *,
    orgnr: str = ORGNR,
    year: int = LAST_YEAR,
    accounts: list[Account] | None = None,
    customers: list[tuple[str, str, str, str, str]] | None = None,
    suppliers: list[tuple[str, str, str, str, str]] | None = None,
    after_master: str = "",
    doctype: str = "",
) -> bytes:
    body = "".join(_account(account) for account in (ACCOUNTS if accounts is None else accounts))
    parties = "".join(
        _party("Customer", *party) for party in (CUSTOMERS if customers is None else customers)
    )
    sellers = "".join(
        _party("Supplier", *party) for party in (SUPPLIERS if suppliers is None else suppliers)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"{doctype}"
        '<AuditFile xmlns="urn:StandardAuditFile-Taxation-Financial:NO"><Header>'
        "<AuditFileVersion>1.40</AuditFileVersion><AuditFileCountry>NO</AuditFileCountry>"
        "<SoftwareCompanyName>Tripletex</SoftwareCompanyName>"
        f"<Company><RegistrationNumber>{orgnr}</RegistrationNumber><Name>Overgang</Name>"
        f"<BankAccount><BankAccountNumber>{BANK}</BankAccountNumber></BankAccount></Company>"
        f"<SelectionCriteria><SelectionStartDate>{year}-01-01</SelectionStartDate>"
        f"<SelectionEndDate>{year}-12-31</SelectionEndDate></SelectionCriteria></Header>"
        f"<MasterFiles><GeneralLedgerAccounts>{body}</GeneralLedgerAccounts>"
        f"<Customers>{parties}</Customers><Suppliers>{sellers}</Suppliers></MasterFiles>"
        f"{after_master}</AuditFile>"
    ).encode()


def upload(client: TestClient, company_id: int, content: bytes, name: str = "saft.xml") -> Any:
    return write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/inngaende-balanse/saft",
        files={"file": (name, content, "application/xml")},
    )


def _codes(body: dict[str, Any]) -> set[str]:
    return {finding["code"] for finding in body["findings"]}


def _create_missing(client: TestClient, company_id: int, body: dict[str, Any]) -> None:
    for add in body["accounts_to_add"]:
        made = write(
            client,
            "POST",
            f"/api/v1/companies/{company_id}/kontoplan?aar={LAST_YEAR}",
            json={
                key: add[key]
                for key in (
                    "number",
                    "description",
                    "account_type",
                    "grouping_category",
                    "grouping_code",
                )
            },
        )
        assert made.status_code == 201, made.text
    for add in body["parties_to_add"]:
        made = write(
            client,
            "POST",
            f"/api/v1/companies/{company_id}/parties",
            json={
                key: add[key]
                for key in (
                    "party_type",
                    "name",
                    "orgnr",
                    "party_id",
                    "address_line",
                    "postal_code",
                    "city",
                )
            },
        )
        assert made.status_code == 201, made.text


# --- The whole move ---------------------------------------------------------------------


def test_a_tripletex_file_becomes_the_opening_balance(client: TestClient) -> None:
    company = _books(client)
    first = upload(client, company["id"], saft_file())
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["source"]["balances"] == "inngaende"
    assert body["source"]["software"] == "Tripletex"
    # Nothing can be posted until the kontoplan and the party register have
    # what the file names, and the answer says what that is.
    assert body["postable"] is False
    assert {"kontoer_mangler", "parter_mangler"} <= _codes(body)

    added = {add["number"]: add for add in body["accounts_to_add"]}
    # The four digits are the account; the cows and the heifers share one.
    assert added["1451"]["source_accounts"] == ["145120512", "145120514"]
    assert added["1451"]["description"] == "Lagerbeh.: Storfe"
    # A credit line is debt, whatever the old program grouped it as.
    assert (added["2390"]["grouping_category"], added["2390"]["grouping_code"]) == (
        "kortsiktigGjeld",
        "2380",
    )
    # «Avsatt utbytte - IFRS» is not where an ENK's deposit goes.
    assert added["2055"]["grouping_code"] != "2055"
    assert [(add["party_id"], add["name"]) for add in body["parties_to_add"]] == [
        ("10001", "Meieriet AS"),
        ("10002", "Slakteriet SA"),
        ("20001", "Landhandelen SA"),
    ]

    _create_missing(client, company["id"], body)
    second = upload(client, company["id"], saft_file()).json()
    assert second["accounts_to_add"] == [] and second["parties_to_add"] == []
    assert second["postable"] is True, second["findings"]
    lines = second["lines"]
    by_account: dict[str, Decimal] = {}
    for line in lines:
        by_account[line["account_number"]] = (
            by_account.get(line["account_number"], Decimal(0))
            + Decimal(line["debit"])
            - Decimal(line["credit"])
        )
    assert by_account == {
        "1920": Decimal("100000"),
        "1250": Decimal("60000"),
        "1451": Decimal("30000"),
        "1500": Decimal("30000"),
        # Last year's VAT is settled on 2740, not swept into this year's return.
        "2740": Decimal("5000"),
        "2400": Decimal("-40000"),
        "2390": Decimal("-80000"),
        "2055": Decimal("-120000"),
        # The loss nobody posted, on equity, so the balance balances.
        "2050": Decimal("15000"),
    }
    # One line per open item, each on its party.
    assert sorted(
        (line["party_name"], line["debit"]) for line in lines if line["account_number"] == "1500"
    ) == [("Meieriet AS", "20000.00"), ("Slakteriet SA", "10000.00")]
    codes = _codes(second)
    assert {"udisponert_resultat", "mva_flyttet_til_2740", "kontoer_slatt_sammen"} <= codes
    assert "bankkonto_utenfor_1920" in codes
    result = next(f for f in second["findings"] if f["code"] == "udisponert_resultat")
    assert "underskudd" in result["message"]

    posted = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/inngaende-balanse",
        json={
            "lines": [
                {
                    key: line[key]
                    for key in ("account_number", "debit", "credit", "party_id", "description")
                }
                for line in lines
            ]
        },
    )
    assert posted.status_code == 201, posted.text
    assert posted.json()["transaction_date"] == date(LAST_YEAR, 1, 1).isoformat()


def test_the_year_befores_file_gives_the_same_lines_from_its_closing_balances(
    client: TestClient,
) -> None:
    """The 2025 file's closing is the 2026 file's opening, read the other way.

    Its income statement still carries the year: the 15 000 loss is on the cost
    account, and it is what makes the balance sheet short, so it lands on 2050.
    """
    company = _books(client)
    current = upload(client, company["id"], saft_file()).json()
    _create_missing(client, company["id"], current)
    current = upload(client, company["id"], saft_file()).json()

    closing = [
        (account_id, description, category, code, "0", end)
        for account_id, description, category, code, _opening, end in ACCOUNTS
        if account_id not in {"4020"}
    ] + [("4020", "Kraftfôr", "varekostnad", "4005", "0", "15000")]
    before = upload(client, company["id"], saft_file(year=LAST_YEAR - 1, accounts=closing)).json()
    assert before["source"]["balances"] == "utgaende"

    def shape(body: dict[str, Any]) -> list[tuple[str, str, str, int | None]]:
        return [
            (line["account_number"], line["debit"], line["credit"], line["party_id"])
            for line in body["lines"]
        ]

    assert shape(before) == shape(current)


def test_a_zipped_file_is_read_as_it_is_downloaded(client: TestClient) -> None:
    company = _books(client)
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"SAF-T Financial_{ORGNR}_20261012200144_1_1.xml", saft_file())
    response = upload(client, company["id"], packed.getvalue(), name="saft.zip")
    assert response.status_code == 200, response.text
    assert response.json()["source"]["orgnr"] == ORGNR


# --- What is refused --------------------------------------------------------------------


def test_another_companys_file_is_refused_before_anything_is_shown(client: TestClient) -> None:
    company = _books(client)
    response = upload(client, company["id"], saft_file(orgnr="910000012"))
    assert response.status_code == 422
    assert response.json()["code"] == "saft_wrong_company"


def test_a_file_for_a_year_that_says_nothing_about_this_balance_is_refused(
    client: TestClient,
) -> None:
    company = _books(client)
    response = upload(client, company["id"], saft_file(year=LAST_YEAR - 3))
    assert response.status_code == 422
    assert response.json()["code"] == "saft_wrong_year"
    # It names the file to export instead.
    assert str(LAST_YEAR) in response.json()["detail"]


def test_a_file_with_a_dtd_is_not_read(client: TestClient) -> None:
    company = _books(client)
    response = upload(
        client,
        company["id"],
        saft_file(doctype='<!DOCTYPE AuditFile [<!ENTITY x "x">]>'),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_saft_file"


def test_a_sub_ledger_that_does_not_add_up_leaves_the_account_whole(client: TestClient) -> None:
    company = _books(client)
    body = upload(
        client,
        company["id"],
        saft_file(customers=[("10001", "Meieriet AS", "910000020", "1500", "25000")]),
    ).json()
    assert "reskontro_avviker" in _codes(body)
    receivables = [line for line in body["lines"] if line["account_number"] == "1500"]
    assert [(line["debit"], line["party_id"]) for line in receivables] == [("30000.00", None)]
    assert all(add["party_type"] != "kunde" for add in body["parties_to_add"])


# --- The reader on its own --------------------------------------------------------------


def test_the_vouchers_are_not_read() -> None:
    """Parsing stops where the entries begin: a year of them is not the opening balance."""
    broken = "<GeneralLedgerEntries><Journal><Amount>not a number</Amount></Journal>"
    found = reader.read_bytes(saft_file(after_master=broken + "</GeneralLedgerEntries>"))
    assert found.orgnr == ORGNR
    assert len(found.accounts) == len(ACCOUNTS)


def test_a_file_without_an_orgnr_is_refused() -> None:
    content = saft_file().replace(
        f"<RegistrationNumber>{ORGNR}</RegistrationNumber><Name>Overgang".encode(),
        b"<Name>Overgang",
    )
    with pytest.raises(InvalidSaftFileError):
        reader.read_bytes(content)


def test_several_accounts_on_one_number_share_the_start_of_their_names() -> None:
    assert _shared_name(["Lagerbeh.: Storfe (Kyr)", "Lagerbeh.: Storfe (Kviger)"]) == (
        "Lagerbeh.: Storfe"
    )
    assert _shared_name(["Bank", "Bank"]) == "Bank"
    assert _shared_name(["Inventar", "Maskiner"]) == "Inventar"
