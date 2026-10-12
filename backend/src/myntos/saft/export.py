"""The SAF-T Financial file, as a pure function of one company's financial year.

No database here, like :mod:`myntos.domain.mva.melding`: :mod:`.service` reads
the ledger into an :class:`AuditFile` and this module writes it out. Keeping the
two apart is what lets the XML be tested against Skatteetaten's XSD without a
database, and the cross-reference check in :mod:`.check` read the same snapshot
the file is written from rather than a second query that could disagree.

**Two versions, one writer.** v1.30 is valid for periods from 2025-01-01 and
usable until 2026-12-31; v1.40 is the only valid format for periods from
2027-01-01. The schemas share a namespace and v1.40 is fully backward
compatible: it widens the currency amounts, lets an owner name several accounts,
and adds NOK tax amounts for lines whose tax is presented in another currency.
Myntos presents every amount in NOK and adds the foreign amount alongside, so
the NOK tax structure never applies and the files differ only in
``AuditFileVersion``. The version is still chosen per file, by financial year,
because the auditor's tooling reads that element to pick a schema.

**Element order is the schema's.** XSD sequences are ordered, so every builder
below writes its children in the order the schema declares them, and an optional
element that is absent is left out rather than written empty: an empty element
fails validation where an absent one does not.
"""

from __future__ import annotations

import enum
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

NAMESPACE = "urn:StandardAuditFile-Taxation-Financial:NO"

SOFTWARE_COMPANY = "Myntos"
SOFTWARE_ID = "Myntos"

# The schema's prescribed filler for a mandatory text we do not have. An empty
# element fails validation; "NA" is what Skatteetaten's guidance says to write.
NOT_AVAILABLE = "NA"

ZERO = Decimal("0.00")
_CENT = Decimal("0.01")

# Dates in the file are Norwegian dates. A voucher entered at 00:30 on
# 1 January in Oslo is a 1 January entry, though UTC still says 31 December.
_OSLO = ZoneInfo("Europe/Oslo")


class SaftVersion(enum.StrEnum):
    V1_30 = "1.30"
    V1_40 = "1.40"


# v1.30 is "valid for periods from 2025-01-01". Before that the format was
# v1.10 with the retired StandardAccountID mapping, which Myntos never had data
# for: no company kept books here before 2025.
FIRST_EXPORTABLE_YEAR = 2025
# The first year for which v1.40 is the *only* valid format.
V1_40_REQUIRED_FROM = 2027


def version_for_year(year: int) -> SaftVersion:
    """The version a financial year is exported in unless the caller asks otherwise.

    v1.30 up to and including 2026, because it is what every auditor's tooling
    already reads for those years and v1.40 is only voluntary there. From 2027,
    v1.40, because nothing else is accepted.
    """
    return SaftVersion.V1_40 if year >= V1_40_REQUIRED_FROM else SaftVersion.V1_30


def allowed_versions(year: int) -> tuple[SaftVersion, ...]:
    """The versions a year may be exported in at all."""
    if year >= V1_40_REQUIRED_FROM:
        return (SaftVersion.V1_40,)
    return (SaftVersion.V1_30, SaftVersion.V1_40)


# --- The snapshot -----------------------------------------------------------------


@dataclass(frozen=True)
class Address:
    street: str | None
    postal_code: str | None
    city: str | None
    country: str | None

    @property
    def is_empty(self) -> bool:
        return not any((self.street, self.postal_code, self.city))


@dataclass(frozen=True)
class Balance:
    """A debit/credit pair, as the ledger stores it."""

    debit: Decimal = ZERO
    credit: Decimal = ZERO

    @property
    def net(self) -> Decimal:
        """Positive on the debit side, negative on the credit side."""
        return self.debit - self.credit

    def plus(self, debit: Decimal, credit: Decimal) -> Balance:
        return Balance(self.debit + debit, self.credit + credit)


@dataclass(frozen=True)
class CompanyHeader:
    orgnr: str
    name: str
    address: Address
    # Header/Company/Contact is mandatory and must name a person. It names the
    # one who produced the file: the auditor's first question about a file is
    # who to ask about it, and that is the honest answer.
    contact_name: str
    telephone: str | None
    email: str | None
    vat_registered: bool
    bank_accounts: tuple[tuple[str, str | None], ...] = ()


@dataclass(frozen=True)
class GLAccount:
    account_id: str
    description: str
    # None when the account has no grouping row for the year, which the check
    # reports as blocking: the element is mandatory and has no honest filler.
    grouping_category: str | None
    grouping_code: str | None
    created: date | None
    opening: Balance
    closing: Balance


class PartyKind(enum.StrEnum):
    CUSTOMER = "kunde"
    SUPPLIER = "leverandor"


@dataclass(frozen=True)
class PartyEntry:
    kind: PartyKind
    party_id: str
    name: str
    orgnr: str | None
    address: Address
    balance_account_id: str
    opening: Balance
    closing: Balance


@dataclass(frozen=True)
class TaxRatePeriod:
    """One code at one rate for one stretch of time: a ``TaxCodeDetails``."""

    code: str
    description: str
    standard_tax_code: str
    rate: Decimal
    valid_from: date
    # Exclusive, as the ledger stores it; the file wants the last day inclusive.
    valid_to: date | None


@dataclass(frozen=True)
class AnalysisValue:
    analysis_type: str
    type_description: str
    analysis_id: str
    id_description: str


@dataclass(frozen=True)
class LineAnalysis:
    analysis_type: str
    analysis_id: str
    amount: Decimal


@dataclass(frozen=True)
class LineTax:
    code: str
    rate: Decimal | None
    base: Decimal | None
    debit_tax: Decimal
    credit_tax: Decimal


@dataclass(frozen=True)
class Line:
    record_id: str
    account_id: str
    description: str
    debit: Decimal
    credit: Decimal
    customer_id: str | None = None
    supplier_id: str | None = None
    source_document_id: str | None = None
    currency_code: str = "NOK"
    currency_amount: Decimal | None = None
    exchange_rate: Decimal | None = None
    tax: LineTax | None = None
    analysis: tuple[LineAnalysis, ...] = ()


@dataclass(frozen=True)
class Entry:
    """A posted voucher: a ``Transaction``."""

    transaction_id: str
    period: int
    year: int
    transaction_date: date
    gl_posting_date: date
    system_entry: datetime
    description: str
    voucher_type: str | None
    # SAF-T SourceID: who entered the voucher, as Myntos's user id. None when
    # not recorded, and then left out rather than guessed.
    source_id: str | None
    lines: tuple[Line, ...]


@dataclass(frozen=True)
class JournalEntries:
    journal_id: str
    description: str
    journal_type: str
    entries: tuple[Entry, ...]


@dataclass(frozen=True)
class AuditFile:
    year: int
    start: date
    end: date
    created_at: datetime
    software_version: str
    company: CompanyHeader
    accounts: tuple[GLAccount, ...]
    parties: tuple[PartyEntry, ...] = ()
    tax_rates: tuple[TaxRatePeriod, ...] = ()
    analysis_values: tuple[AnalysisValue, ...] = ()
    journals: tuple[JournalEntries, ...] = field(default_factory=tuple)

    @property
    def entries(self) -> list[Entry]:
        return [entry for journal in self.journals for entry in journal.entries]

    @property
    def total_debit(self) -> Decimal:
        return sum((line.debit for e in self.entries for line in e.lines), ZERO)

    @property
    def total_credit(self) -> Decimal:
        return sum((line.credit for e in self.entries for line in e.lines), ZERO)


# --- Writing it -------------------------------------------------------------------


def _tag(name: str) -> str:
    return f"{{{NAMESPACE}}}{name}"


def _sub(parent: ET.Element, name: str, text: str | None = None) -> ET.Element:
    element = ET.SubElement(parent, _tag(name))
    if text is not None:
        element.text = text
    return element


def _opt(parent: ET.Element, name: str, text: str | None) -> None:
    """An optional element, written only when there is something to put in it."""
    if text is not None and text.strip():
        _sub(parent, name, text)


def money(amount: Decimal) -> str:
    # format(..., "f") and not str(): str(Decimal) switches to exponent notation
    # for small values ("0E-2"), which xs:decimal does not accept.
    return format(amount.quantize(_CENT), "f")


def decimal_text(value: Decimal) -> str:
    """A decimal with no trailing zeros and never in exponent notation."""
    normalised = value.normalize()
    text = format(normalised, "f")
    return text if text not in {"-0", ""} else "0"


def percent(rate: Decimal) -> str:
    """0.250000 -> "25", 0.111100 -> "11.11"."""
    return decimal_text(rate * 100)


def _local_date(moment: datetime) -> date:
    # SQLite hands back naive datetimes; everything Myntos stores is UTC.
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo("UTC"))
    return moment.astimezone(_OSLO).date()


def _split_name(full_name: str) -> tuple[str, str]:
    """First and last name for ContactPerson, both of which are mandatory.

    Everything but the last word is the first name, which is how a Norwegian
    name with a middle name reads ("Kari Anne Nordmann"). A one-word name has no
    last name to give, and gets the prescribed filler rather than a duplicate.
    """
    parts = full_name.split()
    if not parts:
        return NOT_AVAILABLE, NOT_AVAILABLE
    if len(parts) == 1:
        return parts[0], NOT_AVAILABLE
    return " ".join(parts[:-1]), parts[-1]


def _address(parent: ET.Element, address: Address) -> None:
    if address.is_empty:
        return
    element = _sub(parent, "Address")
    _opt(element, "StreetName", address.street)
    _opt(element, "City", address.city)
    _opt(element, "PostalCode", address.postal_code)
    _opt(element, "Country", address.country)


def _balance_pair(parent: ET.Element, prefix: str, balance: Balance) -> None:
    """``OpeningDebitBalance`` or ``OpeningCreditBalance``, never both.

    The schema offers a choice, so a balance is written on the side its net
    falls on. A zero goes on the debit side, as in Skatteetaten's own example.
    """
    net = balance.net
    if net >= ZERO:
        _sub(parent, f"{prefix}DebitBalance", money(net))
    else:
        _sub(parent, f"{prefix}CreditBalance", money(-net))


def _header(root: ET.Element, audit: AuditFile, version: SaftVersion) -> None:
    header = _sub(root, "Header")
    _sub(header, "AuditFileVersion", version.value)
    _sub(header, "AuditFileCountry", "NO")
    _sub(header, "AuditFileDateCreated", _local_date(audit.created_at).isoformat())
    _sub(header, "SoftwareCompanyName", SOFTWARE_COMPANY)
    _sub(header, "SoftwareID", SOFTWARE_ID)
    _sub(header, "SoftwareVersion", audit.software_version)

    company = _sub(header, "Company")
    _sub(company, "RegistrationNumber", audit.company.orgnr)
    _sub(company, "Name", audit.company.name)
    _address(company, audit.company.address)
    contact = _sub(company, "Contact")
    person = _sub(contact, "ContactPerson")
    first, last = _split_name(audit.company.contact_name)
    _sub(person, "FirstName", first)
    _sub(person, "LastName", last)
    _opt(contact, "Telephone", audit.company.telephone)
    _opt(contact, "Email", audit.company.email)
    if audit.company.vat_registered:
        registration = _sub(company, "TaxRegistration")
        _sub(registration, "TaxRegistrationNumber", audit.company.orgnr)
        _sub(registration, "TaxAuthority", "Skatteetaten")
    for number, label in audit.company.bank_accounts:
        bank = _sub(company, "BankAccount")
        _sub(bank, "BankAccountNumber", number)
        _opt(bank, "BankAccountName", label)

    _sub(header, "DefaultCurrencyCode", "NOK")
    criteria = _sub(header, "SelectionCriteria")
    # One financial year per file is a rule of the format, so the selection is
    # always the whole year, stated as periods the way Skatteetaten's example
    # states it.
    _sub(criteria, "PeriodStart", str(audit.start.month))
    _sub(criteria, "PeriodStartYear", str(audit.start.year))
    _sub(criteria, "PeriodEnd", str(audit.end.month))
    _sub(criteria, "PeriodEndYear", str(audit.end.year))
    # "A" (accounting) is the only value the Norwegian schema allows.
    _sub(header, "TaxAccountingBasis", "A")


def _accounts(master: ET.Element, accounts: Sequence[GLAccount]) -> None:
    ledger = _sub(master, "GeneralLedgerAccounts")
    for account in accounts:
        element = _sub(ledger, "Account")
        _sub(element, "AccountID", account.account_id)
        _sub(element, "AccountDescription", account.description or NOT_AVAILABLE)
        _sub(element, "GroupingCategory", account.grouping_category or NOT_AVAILABLE)
        _sub(element, "GroupingCode", account.grouping_code or NOT_AVAILABLE)
        _sub(element, "AccountType", "GL")
        if account.created is not None:
            _sub(element, "AccountCreationDate", account.created.isoformat())
        _balance_pair(element, "Opening", account.opening)
        _balance_pair(element, "Closing", account.closing)


def _parties(master: ET.Element, parties: Sequence[PartyEntry], kind: PartyKind) -> None:
    chosen = [party for party in parties if party.kind is kind]
    if not chosen:
        return
    section, item, id_name = (
        ("Customers", "Customer", "CustomerID")
        if kind is PartyKind.CUSTOMER
        else ("Suppliers", "Supplier", "SupplierID")
    )
    container = _sub(master, section)
    for party in chosen:
        element = _sub(container, item)
        _opt(element, "RegistrationNumber", party.orgnr)
        _sub(element, "Name", party.name or NOT_AVAILABLE)
        _address(element, party.address)
        _sub(element, id_name, party.party_id)
        balance = _sub(element, "BalanceAccount")
        _sub(balance, "AccountID", party.balance_account_id)
        _balance_pair(balance, "Opening", party.opening)
        _balance_pair(balance, "Closing", party.closing)


def _tax_table(master: ET.Element, rates: Sequence[TaxRatePeriod]) -> None:
    if not rates:
        return
    table = _sub(master, "TaxTable")
    entry = _sub(table, "TaxTableEntry")
    _sub(entry, "TaxType", "MVA")
    _sub(entry, "Description", "Merverdiavgift")
    for rate in rates:
        details = _sub(entry, "TaxCodeDetails")
        _sub(details, "TaxCode", rate.code)
        _sub(details, "EffectiveDate", rate.valid_from.isoformat())
        if rate.valid_to is not None:
            # The ledger's valid_to is exclusive, so the rate's last day is the
            # day before. Written as-is, a rate change on 1 July would claim both
            # rates applied on 1 July.
            _sub(details, "ExpirationDate", (rate.valid_to - timedelta(days=1)).isoformat())
        _opt(details, "Description", rate.description)
        _sub(details, "TaxPercentage", percent(rate.rate))
        _sub(details, "Country", "NO")
        _sub(details, "StandardTaxCode", rate.standard_tax_code)
        # The share of the basis the rate applies to. Always the whole of it in
        # Norway; the element exists for jurisdictions where it is not.
        _sub(details, "BaseRate", "100")


def _analysis_table(master: ET.Element, values: Sequence[AnalysisValue]) -> None:
    if not values:
        return
    table = _sub(master, "AnalysisTypeTable")
    for value in values:
        entry = _sub(table, "AnalysisTypeTableEntry")
        _sub(entry, "AnalysisType", value.analysis_type)
        _sub(entry, "AnalysisTypeDescription", value.type_description or NOT_AVAILABLE)
        _sub(entry, "AnalysisID", value.analysis_id)
        _sub(entry, "AnalysisIDDescription", value.id_description or NOT_AVAILABLE)


def _amount(parent: ET.Element, name: str, amount: Decimal, line: Line | None = None) -> None:
    element = _sub(parent, name)
    _sub(element, "Amount", money(amount))
    # The foreign amount rides alongside the NOK one, never instead of it: the
    # ledger is kept in NOK and so is every total in the file.
    if line is not None and line.currency_code != "NOK" and line.currency_amount is not None:
        _sub(element, "CurrencyCode", line.currency_code)
        _sub(element, "CurrencyAmount", money(abs(line.currency_amount)))
        if line.exchange_rate is not None:
            _sub(element, "ExchangeRate", decimal_text(line.exchange_rate))


def _line(parent: ET.Element, line: Line, fallback_description: str) -> None:
    element = _sub(parent, "Line")
    _sub(element, "RecordID", line.record_id)
    _sub(element, "AccountID", line.account_id)
    is_debit = line.debit > ZERO or line.credit == ZERO
    for allocation in line.analysis:
        analysis = _sub(element, "Analysis")
        _sub(analysis, "AnalysisType", allocation.analysis_type)
        _sub(analysis, "AnalysisID", allocation.analysis_id)
        _amount(
            analysis,
            "DebitAnalysisAmount" if is_debit else "CreditAnalysisAmount",
            allocation.amount,
        )
    _opt(element, "SourceDocumentID", line.source_document_id)
    _opt(element, "CustomerID", line.customer_id)
    _opt(element, "SupplierID", line.supplier_id)
    _sub(element, "Description", line.description.strip() or fallback_description)
    if is_debit:
        _amount(element, "DebitAmount", line.debit, line)
    else:
        _amount(element, "CreditAmount", line.credit, line)

    if line.tax is not None:
        tax = _sub(element, "TaxInformation")
        _sub(tax, "TaxType", "MVA")
        _sub(tax, "TaxCode", line.tax.code)
        if line.tax.rate is not None:
            _sub(tax, "TaxPercentage", percent(line.tax.rate))
        _sub(tax, "Country", "NO")
        if line.tax.base is not None:
            _sub(tax, "TaxBase", money(line.tax.base))
        # Mandatory whenever TaxInformation is present, and 0.00 is a valid
        # amount. It goes on the side the ledger put the tax on; a line with no
        # tax at all (code 0, 5, 6 …) puts its zero on the line's own side.
        if line.tax.debit_tax > ZERO:
            _amount(tax, "DebitTaxAmount", line.tax.debit_tax)
        elif line.tax.credit_tax > ZERO:
            _amount(tax, "CreditTaxAmount", line.tax.credit_tax)
        else:
            _amount(tax, "DebitTaxAmount" if is_debit else "CreditTaxAmount", ZERO)


def _entries(root: ET.Element, audit: AuditFile) -> None:
    ledger = _sub(root, "GeneralLedgerEntries")
    _sub(ledger, "NumberOfEntries", str(len(audit.entries)))
    _sub(ledger, "TotalDebit", money(audit.total_debit))
    _sub(ledger, "TotalCredit", money(audit.total_credit))
    for journal in audit.journals:
        element = _sub(ledger, "Journal")
        _sub(element, "JournalID", journal.journal_id)
        _sub(element, "Description", journal.description or NOT_AVAILABLE)
        _sub(element, "Type", journal.journal_type)
        for entry in journal.entries:
            transaction = _sub(element, "Transaction")
            _sub(transaction, "TransactionID", entry.transaction_id)
            _sub(transaction, "Period", str(entry.period))
            _sub(transaction, "PeriodYear", str(entry.year))
            _sub(transaction, "TransactionDate", entry.transaction_date.isoformat())
            _opt(transaction, "SourceID", entry.source_id)
            _opt(transaction, "VoucherType", entry.voucher_type)
            description = entry.description.strip() or NOT_AVAILABLE
            _sub(transaction, "Description", description)
            _sub(transaction, "SystemEntryDate", _local_date(entry.system_entry).isoformat())
            _sub(transaction, "GLPostingDate", entry.gl_posting_date.isoformat())
            for line in entry.lines:
                _line(transaction, line, description)


def build_xml(audit: AuditFile, version: SaftVersion) -> bytes:
    """The whole file, UTF-8 with a declaration."""
    ET.register_namespace("", NAMESPACE)
    root = ET.Element(_tag("AuditFile"))
    _header(root, audit, version)
    master = _sub(root, "MasterFiles")
    _accounts(master, audit.accounts)
    _parties(master, audit.parties, PartyKind.CUSTOMER)
    _parties(master, audit.parties, PartyKind.SUPPLIER)
    _tax_table(master, audit.tax_rates)
    _analysis_table(master, audit.analysis_values)
    _entries(root, audit)
    return bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))


def filename(orgnr: str, created_at: datetime) -> str:
    """The name Skatteetaten prescribes for an RF-1363 attachment.

    ``SAF-T Financial_<orgnr>_<yyyymmddhhmmss>_<n>_<total>.xml``. Always 1 of 1:
    a file is split only above 2 GB, which no set of books kept here will reach.
    """
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=ZoneInfo("UTC"))
    stamp = created_at.astimezone(_OSLO).strftime("%Y%m%d%H%M%S")
    return f"SAF-T Financial_{orgnr}_{stamp}_1_1.xml"
