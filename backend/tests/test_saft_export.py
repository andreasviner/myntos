"""The SAF-T file as a pure function of a snapshot, and the check that guards it.

No database here, like ``test_mva_melding``. What these pin down is the shape
of the file (valid against Skatteetaten's XSD for both versions, with
``xmllint`` when the machine has it) and the things the XSD cannot see: an
account grouped under a post that does not exist, a line on an account that is
not in the master files, a voucher that does not balance. Each of those is
schema-valid and wrong, which is exactly why the check exists.
"""

from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from myntos.domain.ledger.chart import STANDARD_ACCOUNTS, STANDARD_TAX_CODES
from myntos.saft import codes
from myntos.saft.check import Severity, check
from myntos.saft.export import (
    NAMESPACE,
    Address,
    AuditFile,
    Balance,
    CompanyHeader,
    Entry,
    GLAccount,
    JournalEntries,
    Line,
    LineTax,
    PartyEntry,
    PartyKind,
    SaftVersion,
    TaxRatePeriod,
    allowed_versions,
    build_xml,
    filename,
    percent,
    version_for_year,
)

FIXTURES = Path(__file__).parent / "fixtures" / "saft"
XSD = {
    SaftVersion.V1_30: FIXTURES / "Norwegian_SAF-T_Financial_Schema_v_1.30.xsd",
    SaftVersion.V1_40: FIXTURES / "Norwegian_SAF-T_Financial_Schema_v_1.40.xsd",
}
NS = {"s": NAMESPACE}
ORGNR = "910000004"
needs_xmllint = pytest.mark.skipif(shutil.which("xmllint") is None, reason="no xmllint")
D = Decimal


def _account(
    number: str, category: str, code: str, opening: str = "0", moved: str = "0"
) -> GLAccount:
    net_open, net_moved = D(opening), D(moved)
    opening_balance = Balance(max(net_open, D(0)), max(-net_open, D(0)))
    return GLAccount(
        account_id=number,
        description=f"Konto {number}",
        grouping_category=category,
        grouping_code=code,
        created=date(2026, 1, 1),
        opening=opening_balance,
        closing=opening_balance.plus(max(net_moved, D(0)), max(-net_moved, D(0))),
    )


def _audit(**overrides: object) -> AuditFile:
    """A small year: one purchase from a supplier, one sale to a customer."""
    purchase = Entry(
        transaction_id="2026-0001",
        period=3,
        year=2026,
        transaction_date=date(2026, 3, 14),
        gl_posting_date=date(2026, 3, 14),
        system_entry=datetime(2026, 3, 14, 23, 30, tzinfo=UTC),
        description="Papir",
        voucher_type="kontorrekvisita",
        source_id="7",
        lines=(
            Line("1", "1920", "Papir", D("0.00"), D("125.00")),
            Line(
                "2",
                "6800",
                "Papir",
                D("100.00"),
                D("0.00"),
                supplier_id="20000",
                tax=LineTax("1", D("0.250000"), D("100.00"), D("25.00"), D("0.00")),
            ),
            Line("3", "2710", "Mva 1", D("25.00"), D("0.00")),
        ),
    )
    sale = Entry(
        transaction_id="2026-0002",
        period=4,
        year=2026,
        transaction_date=date(2026, 4, 2),
        gl_posting_date=date(2026, 4, 2),
        system_entry=datetime(2026, 4, 2, 9, 0, tzinfo=UTC),
        description="Konsulenttime",
        voucher_type="salg_hoy_sats",
        source_id=None,
        lines=(
            Line("1", "1920", "", D("1250.00"), D("0.00")),
            Line(
                "2",
                "3000",
                "Konsulenttime",
                D("0.00"),
                D("1000.00"),
                customer_id="10000",
                source_document_id="F-1",
                tax=LineTax("3", D("0.250000"), D("1000.00"), D("0.00"), D("250.00")),
            ),
            Line("3", "2700", "Mva 3", D("0.00"), D("250.00")),
        ),
    )
    audit = AuditFile(
        year=2026,
        start=date(2026, 1, 1),
        end=date(2026, 12, 31),
        created_at=datetime(2026, 10, 9, 8, 15, 0, tzinfo=UTC),
        software_version="0.1.0",
        company=CompanyHeader(
            orgnr=ORGNR,
            name="Nordmann Consulting AS",
            address=Address("Storgata 1", "0155", "Oslo", "NO"),
            contact_name="Kari Anne Nordmann",
            telephone=None,
            email="kari@example.no",
            vat_registered=True,
            bank_accounts=(("12345678903", "Drift"),),
        ),
        accounts=(
            _account("1920", "balanseverdiForOmloepsmiddel", "1920", "500", "1125"),
            _account("2050", "egenkapital", "2050", "-500"),
            _account("2700", "kortsiktigGjeld", "2740", moved="-250"),
            _account("2710", "kortsiktigGjeld", "2740", moved="25"),
            _account("3000", "salgsinntekt", "3000", moved="-1000"),
            _account("6800", "annenDriftskostnad", "6995", moved="100"),
        ),
        parties=(
            PartyEntry(
                kind=PartyKind.CUSTOMER,
                party_id="10000",
                name="Kunde AS",
                orgnr="910000012",
                address=Address(None, None, None, "NO"),
                balance_account_id="1920",
                opening=Balance(),
                closing=Balance(),
            ),
            PartyEntry(
                kind=PartyKind.SUPPLIER,
                party_id="20000",
                name="Papirhuset AS",
                orgnr=None,
                address=Address("Gata 2", "5003", "Bergen", "NO"),
                balance_account_id="1920",
                opening=Balance(),
                closing=Balance(),
            ),
        ),
        tax_rates=(
            TaxRatePeriod(
                "1", "Inngående mva, høy sats (25 %)", "1", D("0.25"), date(2026, 1, 1), None
            ),
            TaxRatePeriod(
                "3", "Utgående mva, høy sats (25 %)", "3", D("0.25"), date(2026, 1, 1), None
            ),
            TaxRatePeriod(
                "31",
                "Utgående mva, middels sats",
                "31",
                D("0.25"),
                date(2025, 1, 1),
                date(2025, 7, 1),
            ),
            TaxRatePeriod(
                "31", "Utgående mva, middels sats", "31", D("0.15"), date(2025, 7, 1), None
            ),
        ),
        journals=(JournalEntries("KONTANT", "Inn- og utbetalinger", "bank", (purchase, sale)),),
    )
    return replace(audit, **overrides)  # type: ignore[arg-type]


def _parse(xml: bytes) -> ET.Element:
    # Our own output, built a line above: there is no untrusted sender here.
    return ET.fromstring(xml)  # noqa: S314


def _validate(xml: bytes, version: SaftVersion, tmp_path: Path) -> None:
    target = tmp_path / f"saft-{version.value}.xml"
    target.write_bytes(xml)
    result = subprocess.run(  # noqa: S603 - fixed arguments, test-only
        ["xmllint", "--noout", "--schema", str(XSD[version]), str(target)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


# --- The schema -------------------------------------------------------------------


@needs_xmllint
@pytest.mark.parametrize("version", list(SaftVersion))
def test_skatteetatens_own_example_validates(version: SaftVersion, tmp_path: Path) -> None:
    """The control: if this fails, the schema or xmllint is the problem, not us."""
    example = FIXTURES / f"example_v_{version.value}.xml"
    _validate(example.read_bytes(), version, tmp_path)


@needs_xmllint
@pytest.mark.parametrize("version", list(SaftVersion))
def test_the_file_is_valid_against_skatteetatens_schema(
    version: SaftVersion, tmp_path: Path
) -> None:
    _validate(build_xml(_audit(), version), version, tmp_path)


@needs_xmllint
@pytest.mark.parametrize("version", list(SaftVersion))
def test_an_empty_year_is_still_a_valid_file(version: SaftVersion, tmp_path: Path) -> None:
    """A company asked for its books in a year it booked nothing still answers."""
    _validate(build_xml(_audit(journals=(), parties=()), version), version, tmp_path)


@needs_xmllint
def test_a_foreign_currency_amount_rides_alongside_the_nok_one(tmp_path: Path) -> None:
    audit = _audit()
    journal = audit.journals[0]
    first = journal.entries[0]
    eur = replace(
        first.lines[0],
        currency_code="EUR",
        currency_amount=D("-10.87"),
        exchange_rate=D("11.50000000"),
    )
    entry = replace(first, lines=(eur, *first.lines[1:]))
    audit = replace(audit, journals=(replace(journal, entries=(entry, journal.entries[1])),))
    for version in SaftVersion:
        xml = build_xml(audit, version)
        _validate(xml, version, tmp_path)
    amount = _parse(xml).find(".//s:Line/s:CreditAmount", NS)
    assert amount is not None
    assert amount.findtext("s:Amount", namespaces=NS) == "125.00"
    assert amount.findtext("s:CurrencyCode", namespaces=NS) == "EUR"
    assert amount.findtext("s:CurrencyAmount", namespaces=NS) == "10.87"
    assert amount.findtext("s:ExchangeRate", namespaces=NS) == "11.5"


# --- What the file says -------------------------------------------------------------


def test_the_versions_differ_in_the_header_and_nowhere_else() -> None:
    old = build_xml(_audit(), SaftVersion.V1_30)
    new = build_xml(_audit(), SaftVersion.V1_40)
    assert _parse(old).findtext("s:Header/s:AuditFileVersion", namespaces=NS) == "1.30"
    assert _parse(new).findtext("s:Header/s:AuditFileVersion", namespaces=NS) == "1.40"
    assert old.replace(b">1.30<", b">1.40<") == new


def test_tax_information_is_on_the_basis_line_with_a_rate_in_percent() -> None:
    root = _parse(build_xml(_audit(), SaftVersion.V1_40))
    lines = root.findall(".//s:Transaction[s:TransactionID='2026-0002']/s:Line", NS)
    taxed = [line for line in lines if line.find("s:TaxInformation", NS) is not None]
    assert [line.findtext("s:AccountID", namespaces=NS) for line in taxed] == ["3000"]
    tax = taxed[0].find("s:TaxInformation", NS)
    assert tax is not None
    assert tax.findtext("s:TaxPercentage", namespaces=NS) == "25"
    assert tax.findtext("s:CreditTaxAmount/s:Amount", namespaces=NS) == "250.00"
    assert taxed[0].findtext("s:CustomerID", namespaces=NS) == "10000"
    assert taxed[0].findtext("s:SourceDocumentID", namespaces=NS) == "F-1"


def test_a_line_with_no_description_borrows_the_vouchers() -> None:
    """Description is mandatory on a line, and the voucher's is the honest stand-in."""
    root = _parse(build_xml(_audit(), SaftVersion.V1_40))
    line = root.find(".//s:Transaction[s:TransactionID='2026-0002']/s:Line[s:RecordID='1']", NS)
    assert line is not None
    assert line.findtext("s:Description", namespaces=NS) == "Konsulenttime"


def test_balances_sit_on_the_side_their_net_falls_on() -> None:
    root = _parse(build_xml(_audit(), SaftVersion.V1_40))
    accounts = {
        a.findtext("s:AccountID", namespaces=NS): a
        for a in root.findall(".//s:GeneralLedgerAccounts/s:Account", NS)
    }
    assert accounts["1920"].findtext("s:OpeningDebitBalance", namespaces=NS) == "500.00"
    assert accounts["1920"].findtext("s:ClosingDebitBalance", namespaces=NS) == "1625.00"
    assert accounts["2050"].findtext("s:OpeningCreditBalance", namespaces=NS) == "500.00"
    assert accounts["3000"].findtext("s:ClosingCreditBalance", namespaces=NS) == "1000.00"
    # Zero goes on the debit side, as in Skatteetaten's example.
    assert accounts["3000"].findtext("s:OpeningDebitBalance", namespaces=NS) == "0.00"


def test_a_rate_that_ended_expires_the_day_before_its_successor_starts() -> None:
    """valid_to is exclusive in the ledger; ExpirationDate is the last day it applied."""
    root = _parse(build_xml(_audit(), SaftVersion.V1_40))
    details = [
        d
        for d in root.findall(".//s:TaxCodeDetails", NS)
        if d.findtext("s:TaxCode", namespaces=NS) == "31"
    ]
    assert [
        (
            d.findtext("s:EffectiveDate", namespaces=NS),
            d.findtext("s:ExpirationDate", namespaces=NS),
            d.findtext("s:TaxPercentage", namespaces=NS),
        )
        for d in details
    ] == [("2025-01-01", "2025-06-30", "25"), ("2025-07-01", None, "15")]


def test_dates_are_norwegian_dates() -> None:
    """23:30 UTC on 14 March is 15 March in Oslo, and the file says 15 March."""
    root = _parse(build_xml(_audit(), SaftVersion.V1_40))
    entry = root.find(".//s:Transaction[s:TransactionID='2026-0001']", NS)
    assert entry is not None
    assert entry.findtext("s:SystemEntryDate", namespaces=NS) == "2026-03-15"


def test_the_contact_is_split_into_the_names_the_schema_wants() -> None:
    root = _parse(build_xml(_audit(), SaftVersion.V1_40))
    person = root.find(".//s:Header/s:Company/s:Contact/s:ContactPerson", NS)
    assert person is not None
    assert person.findtext("s:FirstName", namespaces=NS) == "Kari Anne"
    assert person.findtext("s:LastName", namespaces=NS) == "Nordmann"


def test_the_totals_add_up_the_lines() -> None:
    root = _parse(build_xml(_audit(), SaftVersion.V1_40))
    ledger = root.find("s:GeneralLedgerEntries", NS)
    assert ledger is not None
    assert ledger.findtext("s:NumberOfEntries", namespaces=NS) == "2"
    assert ledger.findtext("s:TotalDebit", namespaces=NS) == "1375.00"
    assert ledger.findtext("s:TotalCredit", namespaces=NS) == "1375.00"


def test_the_filename_is_the_one_rf_1363_prescribes() -> None:
    created = datetime(2026, 10, 9, 8, 15, 0, tzinfo=UTC)
    assert filename(ORGNR, created) == f"SAF-T Financial_{ORGNR}_20261009101500_1_1.xml"


def test_the_version_follows_the_year() -> None:
    assert version_for_year(2025) is SaftVersion.V1_30
    assert version_for_year(2026) is SaftVersion.V1_30
    assert version_for_year(2027) is SaftVersion.V1_40
    assert allowed_versions(2026) == (SaftVersion.V1_30, SaftVersion.V1_40)
    assert allowed_versions(2027) == (SaftVersion.V1_40,)


def test_percent_never_uses_exponent_notation() -> None:
    assert percent(D("0.250000")) == "25"
    assert percent(D("0.111100")) == "11.11"
    assert percent(D("0.000000")) == "0"


# --- The published lists --------------------------------------------------------


@pytest.mark.parametrize("year", sorted(codes.GROUPING_LISTS))
def test_every_seeded_account_is_grouped_under_a_published_post(year: int) -> None:
    """The test that would have caught ``lonnskostnad``.

    The first chart followed the list's documented shape without the list, and
    half its pairs did not exist. Nothing else in the system notices: the XSD
    declares the category as a string.
    """
    wrong = [
        (account.number, account.grouping_category, account.grouping_code)
        for account in STANDARD_ACCOUNTS
        if not codes.is_valid_grouping(year, account.grouping_category, account.grouping_code)
    ]
    assert wrong == []


def test_no_seeded_account_hides_behind_na() -> None:
    """NA is for an account with genuinely no post, and every seeded one has one."""
    assert [a.number for a in STANDARD_ACCOUNTS if a.grouping_code == "NA"] == []


def test_every_seeded_tax_code_maps_to_a_published_standard_code() -> None:
    published = codes.standard_tax_codes()
    assert [t.code for t in STANDARD_TAX_CODES if t.standard_tax_code not in published] == []


def test_a_year_with_no_published_list_is_unchecked_not_passed() -> None:
    assert codes.grouping_pairs(2027) is None
    assert codes.is_valid_grouping(2027, "salgsinntekt", "3000") is None
    assert codes.is_valid_grouping(2027, "NA", "NA") is True


# --- The check ------------------------------------------------------------------


def _codes(audit: AuditFile) -> set[str]:
    return {finding.code for finding in check(audit)}


def test_a_sound_year_has_nothing_to_say() -> None:
    assert check(_audit()) == []


def test_an_invented_grouping_blocks_the_file() -> None:
    audit = _audit()
    accounts = (*audit.accounts[:-1], replace(audit.accounts[-1], grouping_code="6800"))
    findings = check(replace(audit, accounts=accounts))
    assert [(f.severity, f.code) for f in findings] == [(Severity.FEIL, "ukjent_gruppering")]


def test_an_account_with_no_grouping_for_the_year_blocks_the_file() -> None:
    audit = _audit()
    accounts = (
        replace(audit.accounts[0], grouping_category=None, grouping_code=None),
        *audit.accounts[1:],
    )
    assert "gruppering_mangler" in _codes(replace(audit, accounts=accounts))


def test_a_year_without_a_published_list_is_a_note_not_a_refusal() -> None:
    findings = check(_audit(year=2027, start=date(2027, 1, 1), end=date(2027, 12, 31)))
    notes = [f for f in findings if f.code == "grupperingsliste_mangler"]
    assert [f.severity for f in notes] == [Severity.MERKNAD]


def test_references_the_master_files_do_not_hold_are_caught() -> None:
    audit = _audit(parties=(), accounts=_audit().accounts[1:], tax_rates=())
    found = _codes(audit)
    assert {"ukjent_konto", "ukjent_kunde", "ukjent_leverandor", "ukjent_mvakode"} <= found


def test_an_unbalanced_voucher_blocks_the_file() -> None:
    audit = _audit()
    journal = audit.journals[0]
    first = journal.entries[0]
    broken = replace(first, lines=first.lines[:-1])
    audit = replace(audit, journals=(replace(journal, entries=(broken, journal.entries[1])),))
    assert {"bilag_i_ubalanse", "hovedbok_i_ubalanse"} <= _codes(audit)


def test_an_identifier_too_long_for_the_schema_is_refused_not_truncated() -> None:
    """Two ids differing only past the limit would become one id in the file."""
    audit = _audit()
    journal = audit.journals[0]
    long = replace(journal, journal_id="INN-OG-UTBETALINGER-2026")
    assert "for_lang_journal" in _codes(replace(audit, journals=(long,)))


def test_tax_information_on_the_vat_line_is_pointed_out() -> None:
    audit = _audit()
    journal = audit.journals[0]
    first = journal.entries[0]
    vat_line = replace(first.lines[2], tax=LineTax("1", D("0.25"), D("100"), D("25"), D("0")))
    entry = replace(first, lines=(*first.lines[:2], vat_line))
    audit = replace(audit, journals=(replace(journal, entries=(entry, journal.entries[1])),))
    findings = [f for f in check(audit) if f.code == "mva_paa_mvalinje"]
    assert [f.severity for f in findings] == [Severity.MERKNAD]


def test_negative_equity_under_the_positive_post_is_pointed_out() -> None:
    audit = _audit()
    equity = replace(audit.accounts[1], closing=Balance(D("10.00"), D("0")))
    # Rebalance the opening so only the note under test fires.
    accounts = (audit.accounts[0], equity, *audit.accounts[2:])
    assert "negativ_egenkapital" in _codes(replace(audit, accounts=accounts))
