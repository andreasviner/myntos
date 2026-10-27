"""Read a SAF-T Financial file from another program, for the balances in it.

The other half of :mod:`myntos.saft.export`. A company moving to Myntos brings
its books as the SAF-T file its old program writes (Tripletex, Fiken,
PowerOffice, Visma and the rest all do, because bokføringsforskriften § 7-8
requires it), and the file says, per account and per customer and supplier,
what the balance was when the year opened and when it closed. That is the
inngående balanse, and reading it from the file is what spares the person
copying thirty balances off a printout.

Pure: bytes in, a snapshot out, no database. What the balances become in this
company's books (which account, which party, what is missing) is decided by
:mod:`myntos.saft.opening_import`, so this module can be tested on a file alone.

**Only the head of the file is read.** Everything the opening balance needs is
in ``Header`` and ``MasterFiles``, and the schema puts both before
``GeneralLedgerEntries``, which is the bulk: a year of vouchers. Parsing stops
where the entries start, so a file of a few hundred megabytes costs what its
master data costs. A cap on what is read before that point
(:data:`MAX_HEAD_BYTES`) is what keeps a crafted file from making the master
data unbounded instead.

**XML from outside, read the way the bank statement is.** Expat driven by hand
with every declaration refused (see :mod:`myntos.domain.bank.camt`): no SAF-T
file has a DTD, and the only thing a DTD in an uploaded file can be for is an
entity that expands or reaches out.

**A ZIP is opened, because that is how the file arrives.** Skatteetaten
recommends delivering it zipped and Tripletex downloads it that way. One XML
inside, or the first part of a file split in parts (``…_1_3.xml``); anything
else is refused rather than guessed between. The import of several years
(:mod:`myntos.saft.intake`) asks a different question of a ZIP, which SAF-T
files it holds (:func:`saft_members`), because Fiken's complete export is one
ZIP with a file per year and every document beside them.

**Versions.** The element names this reads are the same from 1.10 to 1.40.
Grouping (``GroupingCategory``/``GroupingCode``) exists from 1.30; an older
file has none, and the import then proposes Myntos's own default by number.

**The vouchers, when they are wanted.** :func:`read_entries` goes on past the
master data and reads ``GeneralLedgerEntries`` too, for a company that moves in
the middle of a year (:mod:`myntos.saft.voucher_import`) or runs both programs
side by side and wants them compared (:mod:`myntos.saft.compare`). Each
``Transaction`` is built as a small tree of its own, turned into a
:class:`SourceTransaction` and dropped, so memory follows the number of lines
rather than the size of the XML, and both have a ceiling
(:data:`MAX_DOCUMENT_BYTES`, :data:`MAX_LINES`). The file's own count and debit
total are checked against what was read: a file cut short in the download reads
as valid XML right up to where it stops, and a year missing its last month is
worse than a refusal.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import IO, BinaryIO, Literal, Protocol
from xml.etree import ElementTree as ET
from xml.parsers import expat

from myntos.saft.errors import InvalidSaftFileError, PrintoutNotSaftError

ZERO = Decimal("0.00")

#: The most of the file read before ``GeneralLedgerEntries``. Master data is
#: accounts, customers and suppliers: a farm's is a few megabytes with every
#: one of Tripletex's 2 437 accounts in it. Sixty-four megabytes is a company
#: with tens of thousands of customers, and past that a browser upload is not
#: how the move should be made.
MAX_HEAD_BYTES = 64 * 1024 * 1024

#: The largest upload the route accepts, compressed or not. Altinn takes 200 MB
#: per attachment of a SAF-T delivery, so no conforming file is larger.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

#: The most XML read in all when the vouchers are read too, decompressed. A ZIP
#: within the upload limit can unpack to far more than this; the ceiling is
#: what stops one from being unpacked without end.
MAX_DOCUMENT_BYTES = 1024 * 1024 * 1024

#: The most voucher lines one file may carry into an import. A farm writes
#: about two thousand a year and a busy shop a few hundred thousand, which is
#: past what one request should post; such a move is made a period at a time.
MAX_LINES = 300_000

_CHUNK = 1024 * 1024
_ZIP_MAGIC = b"PK\x03\x04"
_PART = re.compile(r"_1_\d+\.xml$", re.IGNORECASE)
#: Skatteetaten names a file delivered in parts ``…_2_3.xml``, the second of
#: three; ``_1_1`` is a file in one part, as Tripletex names every file.
_PARTS = re.compile(r"_(\d+)_(\d+)\.xml$", re.IGNORECASE)
#: How much of an XML file in a ZIP is read to see whether it is a SAF-T file.
#: The document element comes after at most a declaration and a comment.
_SNIFF_BYTES = 64 * 1024
_DIGITS = re.compile(r"\D")

PartyKind = Literal["kunde", "leverandor"]


@dataclass(frozen=True)
class SourceAccount:
    """One account in the file, with its balances signed debit-positive."""

    account_id: str
    description: str
    grouping_category: str | None
    grouping_code: str | None
    opening: Decimal
    closing: Decimal


@dataclass(frozen=True)
class SourceParty:
    """One customer or supplier's balance on one balance account."""

    kind: PartyKind
    party_id: str
    name: str
    orgnr: str | None
    account_id: str
    opening: Decimal
    closing: Decimal
    address_line: str | None
    postal_code: str | None
    city: str | None


@dataclass(frozen=True)
class PartyRecord:
    """A customer or supplier as the master data names it, with or without a balance."""

    kind: PartyKind
    party_id: str
    name: str
    orgnr: str | None
    address_line: str | None
    postal_code: str | None
    city: str | None


@dataclass(frozen=True)
class AnalysisRecord:
    """One value in the file's ``AnalysisTypeTable``: avdeling 20, «Jordbruk»."""

    #: The file's own code for the dimension (Tripletex writes ``A``).
    analysis_type: str
    #: What the file calls the dimension («Avdeling»), which is how Myntos
    #: recognises it: the one-letter codes are each program's own.
    type_description: str
    analysis_id: str
    description: str


@dataclass(frozen=True)
class SaftBalances:
    """What the head of a SAF-T file says about one company's year."""

    audit_file_version: str
    software: str | None
    orgnr: str
    company_name: str
    start: date
    end: date
    #: The company's own bank account numbers, digits only, from the header.
    bank_accounts: tuple[str, ...]
    accounts: tuple[SourceAccount, ...]
    parties: tuple[SourceParty, ...]
    #: Every customer and supplier in the master data. ``parties`` holds only
    #: the balances; a supplier first used this year has none and is here.
    register: tuple[PartyRecord, ...] = ()
    #: The file's own VAT codes and the standard code each stands for
    #: (``TaxTable``): Tripletex writes ``UTTAK-3`` for code 3 on a withdrawal.
    tax_codes: dict[str, str] | None = None
    #: The dimensions and their values (``AnalysisTypeTable``). Tripletex
    #: writes its avdelinger here, and beside them its voucher types and its
    #: employees by name, which is why the import picks among them.
    analysis: tuple[AnalysisRecord, ...] = ()


@dataclass(frozen=True)
class SourceAnalysis:
    """A line's ``Analysis``: which value of which dimension it belongs to."""

    analysis_type: str
    analysis_id: str
    #: ``DebitAnalysisAmount`` or ``CreditAnalysisAmount``, unsigned, or None
    #: when the file gives none. Not trusted for the side: Tripletex writes a
    #: debit analysis amount on a credit line.
    amount: Decimal | None


@dataclass(frozen=True)
class SourceTax:
    """A line's ``TaxInformation``, amounts as the file has them."""

    code: str
    percentage: Decimal
    base: Decimal
    debit_tax: Decimal
    credit_tax: Decimal


@dataclass(frozen=True)
class SourceLine:
    """One voucher line, signed debit-positive."""

    record_id: str
    account_id: str
    description: str
    amount: Decimal
    #: The line's own date (``ValueDate``), which is not always the voucher's:
    #: Tripletex adds an invoice's payment to the invoice's voucher, dated the
    #: day it was paid.
    on: date
    party_kind: PartyKind | None
    party_id: str | None
    taxes: tuple[SourceTax, ...]
    currency_code: str | None = None
    currency_amount: Decimal | None = None
    exchange_rate: Decimal | None = None
    analysis: tuple[SourceAnalysis, ...] = ()


@dataclass(frozen=True)
class SourceTransaction:
    """One voucher as the old program numbered and dated it."""

    transaction_id: str
    #: ``PeriodYear``: the year the old program numbers the voucher in. A 2025
    #: invoice paid in January is in the 2026 file with only its payment lines,
    #: under its 2025 number.
    period_year: int | None
    on: date
    posted_on: date | None
    description: str
    voucher_type: str | None
    lines: tuple[SourceLine, ...]


@dataclass(frozen=True)
class SaftEntries:
    """The head of the file and every voucher in it."""

    balances: SaftBalances
    transactions: tuple[SourceTransaction, ...]


# --- Getting at the XML -------------------------------------------------------------


def _xml_stream(upload: BinaryIO, *, whole: bool = False) -> IO[bytes]:
    """The XML inside ``upload``: the upload itself, or the one file in its ZIP.

    ``whole`` when the vouchers are wanted: a file delivered in parts has them
    spread across the parts, and reading the first alone would be a year with
    months missing.
    """
    head = upload.read(4)
    upload.seek(0)
    if head != _ZIP_MAGIC:
        refuse_unless_saft(upload, "Filen")
        return upload
    try:
        archive = zipfile.ZipFile(upload)
    except zipfile.BadZipFile as error:
        raise InvalidSaftFileError("ZIP-filen kan ikke åpnes. Last den ned på nytt") from error
    names = [info.filename for info in archive.infolist() if info.filename.lower().endswith(".xml")]
    if len(names) > 1:
        # Fiken's complete export, or any ZIP with documents beside the file:
        # only the XML whose document element is AuditFile is a SAF-T file.
        names = [
            info.filename
            for info in archive.infolist()
            if info.filename in names and _saft(archive, info)
        ]
    if len(names) > 1:
        # A file too big for one part is delivered as several, each a whole
        # SAF-T document; the first one is the one with the master data.
        parts = [name for name in names if _PART.search(name)]
        if whole and parts:
            raise InvalidSaftFileError(
                f"SAF-T-filen er delt i {len(names)} deler, og bilagene leses bare fra en fil "
                "som ikke er delt. Ta ut filen for en kortere periode, for eksempel et halvår"
            )
        names = parts or names
    if not names:
        raise InvalidSaftFileError(f"ZIP-filen {without_saft(archive)}")
    if len(names) > 1:
        raise InvalidSaftFileError(
            f"ZIP-filen har {len(names)} SAF-T-filer, en for hvert år. Her leses en fil om gangen: "
            "pakk ut og last opp den for riktig år, eller hent alle årene samlet under «Hent "
            "regnskapet fra et annet program»"
        )
    return archive.open(names[0])


def _saft(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bool:
    if info.flag_bits & 0x1:
        # Locked: nothing can be read from it, so it is not taken for one.
        return False
    with archive.open(info) as member:
        return _is_audit_file(member)


def without_saft(archive: zipfile.ZipFile) -> str:
    """Why a ZIP with no SAF-T file in it is not one, finishing a sentence about it.

    Fiken alone offers four ZIPs and an Excel workbook (itself a ZIP) on the
    page the SAF-T files are on, and the person picks one of the others.
    """
    names = [info.filename.lower() for info in archive.infolist()]
    if "xl/workbook.xml" in names:
        return "er et regneark, ikke en SAF-T-fil. Last opp SAF-T-filen (XML eller ZIP)"
    if any(name.endswith((".pdf", ".jpg", ".jpeg", ".png")) for name in names):
        return (
            "har dokumenter, men ingen SAF-T-fil. Last opp SAF-T-filen for hvert år, eller en "
            "eksport som har dem med"
        )
    return "har ingen SAF-T-fil i seg. Last opp SAF-T-filen for hvert år fra det gamle programmet"


def refuse_unless_saft(upload: BinaryIO, name: str) -> None:
    """Refuse a file that is not XML with ``AuditFile`` at the top, saying what it is instead.

    From its first bytes, before anything is read or stored: a printout, a bank
    statement or an invoice sent where a SAF-T file goes is told so by name,
    and a printout with a code of its own (:class:`PrintoutNotSaftError`) so a
    program can send it to the printout's route. ``name`` starts the sentence
    («Filen», «2025.pdf»). Leaves the stream at the start.
    """
    head = upload.read(_SNIFF_BYTES)
    upload.seek(0)
    if head.startswith(b"%PDF"):
        raise PrintoutNotSaftError(
            f"{name} er en PDF, ikke en SAF-T-fil. Utskriften av bilagene fra Tripletex lastes "
            "opp under «Vedlegg fra Tripletex»"
        )
    element = document_element(io.BytesIO(head))
    if element is None:
        raise InvalidSaftFileError(
            f"{name} er ikke en SAF-T-fil. Last opp XML- eller ZIP-filen det gamle programmet "
            "laget som SAF-T"
        )
    namespace, local = element
    if local == "AuditFile":
        return
    if local == "Document" and "camt.05" in namespace:
        raise InvalidSaftFileError(
            f"{name} er en kontoutskrift fra banken (camt), ikke en SAF-T-fil"
        )
    if local in ("Invoice", "CreditNote"):
        raise InvalidSaftFileError(f"{name} er en EHF-faktura, ikke en SAF-T-fil")
    raise InvalidSaftFileError(
        f"{name} er en XML-fil, men ikke en SAF-T-fil (den begynner med «{local}», ikke "
        "«AuditFile»)"
    )


def saft_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """The SAF-T Financial files in a ZIP, found by what they are rather than what they are called.

    Tripletex's ZIP holds one. Fiken's complete export («Komplett eksport»,
    ``fiken-komplett-<firma>.zip``) holds one per year beside every document in
    the books, and Fiken publishes neither its folders nor its file names. A
    document can be XML too (an EHF invoice), so each ``.xml`` is opened and the
    ones whose document element is ``AuditFile`` are taken, reading at most
    :data:`_SNIFF_BYTES` of each. In name order.

    A file delivered in parts (``…_2_3.xml``) is refused here, as
    :func:`read_entries` refuses it: the vouchers are spread across the parts.
    """
    found: list[zipfile.ZipInfo] = []
    for info in sorted(archive.infolist(), key=lambda item: item.filename):
        name = info.filename
        if info.is_dir() or not name.lower().endswith(".xml") or "__MACOSX/" in name:
            continue
        if info.flag_bits & 0x1:
            raise InvalidSaftFileError(
                f"«{name.rsplit('/', 1)[-1]}» i ZIP-filen er låst med passord. Last den ned uten "
                "passord"
            )
        with archive.open(info) as member:
            if not _is_audit_file(member):
                continue
        parts = _PARTS.search(name)
        if parts and int(parts.group(2)) > 1:
            raise InvalidSaftFileError(
                f"SAF-T-filen er delt i {parts.group(2)} deler, og bilagene leses bare fra en fil "
                "som ikke er delt. Ta ut filen for en kortere periode, for eksempel et halvår"
            )
        found.append(info)
    return found


class _FirstElementError(Exception):
    def __init__(self, name: str) -> None:
        self.name = name


def _is_audit_file(stream: Readable) -> bool:
    """Whether the document element in ``stream`` is ``AuditFile``, from its first bytes."""
    element = document_element(stream)
    return element is not None and element[1] == "AuditFile"


def document_element(stream: Readable) -> tuple[str, str] | None:
    """The namespace and local name of the document element, from the first bytes of ``stream``.

    None for what is not XML, or declares a DTD, which no file read here has.
    """
    parser = expat.ParserCreate(namespace_separator="}")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.StartDoctypeDeclHandler = _refuse
    parser.EntityDeclHandler = _refuse
    parser.ExternalEntityRefHandler = _refuse_external

    def first(tag: str, _: dict[str, str]) -> None:
        raise _FirstElementError(tag)

    parser.StartElementHandler = first
    try:
        parser.Parse(stream.read(_SNIFF_BYTES), False)
    except _FirstElementError as element:
        namespace, _, local = element.name.rpartition("}")
        return namespace, local
    except (_DeclarationRefusedError, expat.ExpatError, zipfile.BadZipFile, EOFError):
        return None
    return None


class Readable(Protocol):
    """What the parser needs of a stream: bytes, read forward. A gzip stream is one."""

    def read(self, size: int = -1, /) -> bytes: ...


class _DeclarationRefusedError(Exception):
    pass


class _HeadReadError(Exception):
    """Everything wanted has been read: the entries started, or ended."""


def _refuse(*_: object) -> None:
    raise _DeclarationRefusedError


def _refuse_external(*_: object) -> int:
    raise _DeclarationRefusedError


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


#: The totals ``GeneralLedgerEntries`` states about itself, checked after reading.
_ENTRY_TOTALS = frozenset({"NumberOfEntries", "TotalDebit", "TotalCredit"})


class _Reader:
    """Expat's three callbacks: the head as one tree, each voucher as its own.

    ``on_voucher`` is None when only the head is wanted, and reading then
    stops where the entries start.
    """

    def __init__(self, on_voucher: Callable[[ET.Element], None] | None) -> None:
        self.on_voucher = on_voucher
        self.head = ET.TreeBuilder()
        self.root: ET.Element | None = None
        self.in_entries = False
        self.voucher: ET.TreeBuilder | None = None
        self.depth = 0
        self.totals: dict[str, str] = {}
        self._capture: str | None = None
        self._text: list[str] = []

    def start(self, tag: str, attributes: dict[str, str]) -> None:
        name = _local(tag)
        if self.voucher is not None:
            self.voucher.start(tag, attributes)
            self.depth += 1
        elif self.in_entries:
            if name == "Transaction":
                self.voucher = ET.TreeBuilder()
                self.voucher.start(tag, attributes)
                self.depth = 1
            elif name in _ENTRY_TOTALS:
                self._capture = name
                self._text = []
        elif name == "GeneralLedgerEntries":
            if self.on_voucher is None:
                raise _HeadReadError
            self.in_entries = True
        else:
            element = self.head.start(tag, attributes)
            if self.root is None:
                self.root = element

    def end(self, tag: str) -> None:
        if self.voucher is not None:
            self.voucher.end(tag)
            self.depth -= 1
            if self.depth == 0:
                element = self.voucher.close()
                self.voucher = None
                assert self.on_voucher is not None  # noqa: S101
                self.on_voucher(element)
        elif self.in_entries:
            name = _local(tag)
            if name == "GeneralLedgerEntries":
                # SourceDocuments may follow, the invoices again in another
                # shape. Nothing in them is wanted.
                raise _HeadReadError
            if name == self._capture:
                self.totals[name] = "".join(self._text).strip()
                self._capture = None
        else:
            self.head.end(tag)

    def data(self, text: str) -> None:
        if self.voucher is not None:
            self.voucher.data(text)
        elif self.in_entries:
            if self._capture is not None:
                self._text.append(text)
        else:
            self.head.data(text)


def _parse(stream: Readable, reader: _Reader) -> ET.Element:
    """Run ``reader`` over ``stream`` and return the document's root."""
    parser = expat.ParserCreate(namespace_separator="}")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.StartDoctypeDeclHandler = _refuse
    parser.EntityDeclHandler = _refuse
    parser.UnparsedEntityDeclHandler = _refuse
    parser.NotationDeclHandler = _refuse
    parser.ExternalEntityRefHandler = _refuse_external
    parser.StartElementHandler = reader.start
    parser.EndElementHandler = reader.end
    parser.CharacterDataHandler = reader.data

    read = 0
    try:
        while True:
            chunk = stream.read(_CHUNK)
            read += len(chunk)
            if not reader.in_entries and read > MAX_HEAD_BYTES:
                raise InvalidSaftFileError(
                    "kontoplanen og kunde- og leverandørlistene i filen er for store til å "
                    "leses her"
                )
            if read > MAX_DOCUMENT_BYTES:
                raise InvalidSaftFileError(
                    "filen er for stor til at bilagene kan leses her. Ta ut SAF-T-filen for en "
                    "kortere periode"
                )
            parser.Parse(chunk, not chunk)
            if not chunk:
                break
    except _HeadReadError:
        pass
    except _DeclarationRefusedError as error:
        raise InvalidSaftFileError(
            "filen inneholder en DTD-deklarasjon. Det gjør aldri en SAF-T-fil, så den blir "
            "ikke lest"
        ) from error
    except (expat.ExpatError, zipfile.BadZipFile, EOFError) as error:
        raise InvalidSaftFileError(
            "filen er ikke gyldig XML. Last ned SAF-T-filen på nytt fra det gamle systemet"
        ) from error
    if reader.root is None or _local(reader.root.tag) != "AuditFile":
        raise InvalidSaftFileError("filen er ikke en SAF-T Financial-fil (AuditFile)")
    return reader.root


def _read_head(stream: IO[bytes]) -> ET.Element:
    """The document's root with ``Header`` and ``MasterFiles`` under it, and nothing after."""
    return _parse(stream, _Reader(None))


# --- Reading it ---------------------------------------------------------------------


def _child(element: ET.Element | None, *path: str) -> ET.Element | None:
    for name in path:
        if element is None:
            return None
        element = next((child for child in element if _local(child.tag) == name), None)
    return element


def _children(element: ET.Element | None, name: str) -> Iterator[ET.Element]:
    if element is None:
        return iter(())
    return (child for child in element if _local(child.tag) == name)


def _text(element: ET.Element | None, *path: str) -> str | None:
    found = _child(element, *path)
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def _amount(element: ET.Element | None, name: str) -> Decimal:
    raw = _text(element, name)
    if raw is None:
        return ZERO
    try:
        value = Decimal(raw)
    except InvalidOperation as error:
        raise InvalidSaftFileError(f"beløpet {raw!r} i {name} kan ikke leses") from error
    if value != value.quantize(Decimal("0.01")):
        raise InvalidSaftFileError(f"beløpet {raw} i {name} har brøkdeler av et øre")
    return value.quantize(Decimal("0.01"))


def _balance(element: ET.Element | None, when: str) -> Decimal:
    return _amount(element, f"{when}DebitBalance") - _amount(element, f"{when}CreditBalance")


def _date(raw: str | None, what: str) -> date:
    if raw is None:
        raise InvalidSaftFileError(f"filen sier ikke {what}")
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise InvalidSaftFileError(f"{what} i filen, {raw!r}, er ikke en dato") from error


def _period(header: ET.Element | None) -> tuple[date, date]:
    """The span the file covers, by date or by period number."""
    criteria = _child(header, "SelectionCriteria")
    start = _text(criteria, "SelectionStartDate")
    end = _text(criteria, "SelectionEndDate")
    if start is not None or end is not None:
        return _date(start, "når utvalget starter"), _date(end, "når utvalget slutter")
    try:
        first_month = int(_text(criteria, "PeriodStart") or "")
        first_year = int(_text(criteria, "PeriodStartYear") or "")
        last_month = int(_text(criteria, "PeriodEnd") or "")
        last_year = int(_text(criteria, "PeriodEndYear") or "")
        begins = date(first_year, first_month, 1)
        following = (
            date(last_year + 1, 1, 1) if last_month >= 12 else date(last_year, last_month + 1, 1)
        )
    except ValueError as error:
        raise InvalidSaftFileError("filen sier ikke hvilken periode den gjelder") from error
    return begins, date.fromordinal(following.toordinal() - 1)


def _orgnr(raw: str | None) -> str | None:
    """Nine digits, or nothing: "NO 990 943 567 MVA" and "990943567" alike."""
    if raw is None:
        return None
    digits = _DIGITS.sub("", raw)
    return digits if len(digits) == 9 else None


def _accounts(master: ET.Element | None) -> tuple[SourceAccount, ...]:
    found: list[SourceAccount] = []
    for account in _children(_child(master, "GeneralLedgerAccounts"), "Account"):
        account_id = _text(account, "AccountID")
        if account_id is None:
            raise InvalidSaftFileError("en konto i filen har ikke AccountID")
        found.append(
            SourceAccount(
                account_id=account_id,
                description=_text(account, "AccountDescription") or "",
                grouping_category=_text(account, "GroupingCategory"),
                grouping_code=_text(account, "GroupingCode"),
                opening=_balance(account, "Opening"),
                closing=_balance(account, "Closing"),
            )
        )
    return tuple(found)


_PARTY_TAGS: tuple[tuple[PartyKind, str, str, str], ...] = (
    ("kunde", "Customers", "Customer", "CustomerID"),
    ("leverandor", "Suppliers", "Supplier", "SupplierID"),
)


def _record(kind: PartyKind, party: ET.Element, id_tag: str) -> PartyRecord:
    party_id = _text(party, id_tag) or ""
    address = _child(party, "Address")
    street = " ".join(
        part for part in (_text(address, "StreetName"), _text(address, "Number")) if part
    )
    return PartyRecord(
        kind=kind,
        party_id=party_id,
        name=_text(party, "Name") or party_id,
        orgnr=_orgnr(_text(party, "RegistrationNumber")),
        address_line=street or None,
        postal_code=_text(address, "PostalCode"),
        city=_text(address, "City"),
    )


def _register(master: ET.Element | None) -> tuple[PartyRecord, ...]:
    return tuple(
        _record(kind, party, id_tag)
        for kind, plural, singular, id_tag in _PARTY_TAGS
        for party in _children(_child(master, plural), singular)
    )


def _parties(master: ET.Element | None) -> tuple[SourceParty, ...]:
    found: list[SourceParty] = []
    for kind, plural, singular, id_tag in _PARTY_TAGS:
        for party in _children(_child(master, plural), singular):
            record = _record(kind, party, id_tag)
            # 1.20 to 1.40 put the balance in BalanceAccount (and 1.10 in
            # BalanceAccountStructure), one per account the party settles
            # against; a party on two accounts is two balances.
            balances = list(_children(party, "BalanceAccount")) + list(
                _children(party, "BalanceAccountStructure")
            )
            for balance in balances:
                account_id = _text(balance, "AccountID")
                if account_id is None:
                    continue
                found.append(
                    SourceParty(
                        kind=kind,
                        party_id=record.party_id,
                        name=record.name,
                        orgnr=record.orgnr,
                        account_id=account_id,
                        opening=_balance(balance, "Opening"),
                        closing=_balance(balance, "Closing"),
                        address_line=record.address_line,
                        postal_code=record.postal_code,
                        city=record.city,
                    )
                )
    return tuple(found)


def _tax_codes(master: ET.Element | None) -> dict[str, str]:
    """``{file's code: standard code}`` from ``TaxTable``."""
    found: dict[str, str] = {}
    for entry in _children(_child(master, "TaxTable"), "TaxTableEntry"):
        for details in _children(entry, "TaxCodeDetails"):
            code = _text(details, "TaxCode")
            standard = _text(details, "StandardTaxCode")
            if code is not None and standard is not None:
                found[code] = standard
    return found


def _analysis_table(master: ET.Element | None) -> tuple[AnalysisRecord, ...]:
    found: list[AnalysisRecord] = []
    for entry in _children(_child(master, "AnalysisTypeTable"), "AnalysisTypeTableEntry"):
        analysis_type = _text(entry, "AnalysisType")
        analysis_id = _text(entry, "AnalysisID")
        if analysis_type is None or analysis_id is None:
            continue
        found.append(
            AnalysisRecord(
                analysis_type=analysis_type,
                type_description=_text(entry, "AnalysisTypeDescription") or analysis_type,
                analysis_id=analysis_id,
                description=_text(entry, "AnalysisIDDescription") or analysis_id,
            )
        )
    return tuple(found)


def _line_analysis(element: ET.Element) -> tuple[SourceAnalysis, ...]:
    found: list[SourceAnalysis] = []
    for analysis in _children(element, "Analysis"):
        analysis_type = _text(analysis, "AnalysisType")
        analysis_id = _text(analysis, "AnalysisID")
        if analysis_type is None or analysis_id is None:
            continue
        # An element's truth is whether it has children, so no ``or`` here.
        # 1.10 had one AnalysisAmount with no side.
        side = next(
            (
                amount
                for name in ("DebitAnalysisAmount", "CreditAnalysisAmount", "AnalysisAmount")
                if (amount := _child(analysis, name)) is not None
            ),
            None,
        )
        found.append(
            SourceAnalysis(
                analysis_type=analysis_type,
                analysis_id=analysis_id,
                amount=_amount(side, "Amount") if side is not None else None,
            )
        )
    return tuple(found)


def _decimal(raw: str | None, what: str) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation as error:
        raise InvalidSaftFileError(f"{what} i filen, {raw!r}, er ikke et tall") from error


def _tax(element: ET.Element, side_is_debit: bool) -> SourceTax:
    debit_tax = _amount(_child(element, "DebitTaxAmount"), "Amount")
    credit_tax = _amount(_child(element, "CreditTaxAmount"), "Amount")
    if _child(element, "DebitTaxAmount") is None and _child(element, "CreditTaxAmount") is None:
        # 1.10 had one TaxAmount and no side; it is on the line's.
        amount = _amount(_child(element, "TaxAmount"), "Amount")
        debit_tax, credit_tax = (amount, ZERO) if side_is_debit else (ZERO, amount)
    return SourceTax(
        code=_text(element, "TaxCode") or "",
        percentage=_decimal(_text(element, "TaxPercentage"), "mva-satsen") or ZERO,
        base=_amount(element, "TaxBase"),
        debit_tax=debit_tax,
        credit_tax=credit_tax,
    )


def _line(element: ET.Element, voucher: str, on: date) -> SourceLine:
    account_id = _text(element, "AccountID")
    if account_id is None:
        raise InvalidSaftFileError(f"en linje på bilag {voucher} har ikke AccountID")
    debit_side = _child(element, "DebitAmount")
    credit_side = _child(element, "CreditAmount")
    amount = _amount(debit_side, "Amount") - _amount(credit_side, "Amount")
    side = debit_side if debit_side is not None else credit_side
    customer = _text(element, "CustomerID")
    supplier = _text(element, "SupplierID")
    party_kind: PartyKind | None = (
        "kunde" if customer is not None else "leverandor" if supplier is not None else None
    )
    value_date = _text(element, "ValueDate")
    currency = _text(side, "CurrencyCode")
    foreign = currency is not None and currency.upper() != "NOK"
    return SourceLine(
        record_id=_text(element, "RecordID") or "",
        account_id=account_id,
        description=_text(element, "Description") or "",
        amount=amount,
        on=_date(value_date, f"datoen på en linje på bilag {voucher}") if value_date else on,
        party_kind=party_kind,
        party_id=customer if customer is not None else supplier,
        taxes=tuple(
            _tax(tax, debit_side is not None) for tax in _children(element, "TaxInformation")
        ),
        currency_code=currency.upper() if foreign and currency is not None else None,
        currency_amount=_amount(side, "CurrencyAmount") if foreign else None,
        exchange_rate=(_decimal(_text(side, "ExchangeRate"), "valutakursen") if foreign else None),
        analysis=_line_analysis(element),
    )


def _voucher(element: ET.Element) -> SourceTransaction:
    transaction_id = _text(element, "TransactionID")
    if transaction_id is None:
        raise InvalidSaftFileError("et bilag i filen har ikke TransactionID")
    on = _date(_text(element, "TransactionDate"), f"datoen på bilag {transaction_id}")
    posted = _text(element, "GLPostingDate")
    year = _text(element, "PeriodYear")
    return SourceTransaction(
        transaction_id=transaction_id,
        period_year=int(year) if year is not None and year.isdigit() else None,
        on=on,
        posted_on=_date(posted, f"føringsdatoen på bilag {transaction_id}") if posted else None,
        description=_text(element, "Description") or "",
        voucher_type=_text(element, "VoucherType") or _text(element, "TransactionType"),
        lines=tuple(_line(line, transaction_id, on) for line in _children(element, "Line")),
    )


def _balances(root: ET.Element) -> SaftBalances:
    header = _child(root, "Header")
    company = _child(header, "Company")
    orgnr = _orgnr(_text(company, "RegistrationNumber"))
    if orgnr is None:
        raise InvalidSaftFileError(
            "filen sier ikke hvilket organisasjonsnummer den gjelder (Header/Company/"
            "RegistrationNumber), så Myntos kan ikke se at det er dette selskapets regnskap"
        )
    start, end = _period(header)
    master = _child(root, "MasterFiles")
    bank_accounts = tuple(
        digits
        for digits in (
            _DIGITS.sub("", _text(bank, "BankAccountNumber") or _text(bank, "IBANNumber") or "")
            for bank in _children(company, "BankAccount")
        )
        if digits
    )
    return SaftBalances(
        audit_file_version=_text(header, "AuditFileVersion") or "",
        software=_text(header, "SoftwareCompanyName"),
        orgnr=orgnr,
        company_name=_text(company, "Name") or "",
        start=start,
        end=end,
        bank_accounts=bank_accounts,
        accounts=_accounts(master),
        parties=_parties(master),
        register=_register(master),
        tax_codes=_tax_codes(master),
        analysis=_analysis_table(master),
    )


def read(upload: BinaryIO) -> SaftBalances:
    """The header and the balances of a SAF-T Financial file, XML or zipped.

    ``upload`` must be seekable (an upload spooled to disk is), because a ZIP is
    read from its directory at the end.
    """
    return _balances(_read_head(_xml_stream(upload)))


def read_bytes(content: bytes) -> SaftBalances:
    """:func:`read` for content already in memory, as the tests have it."""
    return read(io.BytesIO(content))


def read_entries(upload: BinaryIO) -> SaftEntries:
    """The head and every voucher of a SAF-T Financial file, XML or zipped."""
    return read_entries_xml(_xml_stream(upload, whole=True))


def read_entries_xml(stream: Readable) -> SaftEntries:
    """:func:`read_entries` for a stream that is the XML itself, read from the start.

    Never seeks, so it parses a download from the store as it arrives
    (:mod:`myntos.saft.jobs`) instead of after it has all been fetched.
    """
    transactions: list[SourceTransaction] = []
    lines = 0

    def take(element: ET.Element) -> None:
        nonlocal lines
        voucher = _voucher(element)
        lines += len(voucher.lines)
        if lines > MAX_LINES:
            raise InvalidSaftFileError(
                f"filen har mer enn {MAX_LINES:,} bilagslinjer".replace(",", "\u00a0")
                + ", som er flere enn Myntos henter på en gang. Ta ut SAF-T-filen for en "
                "kortere periode"
            )
        transactions.append(voucher)

    reader = _Reader(take)
    root = _parse(stream, reader)
    balances = _balances(root)
    stated = reader.totals.get("NumberOfEntries")
    if stated is not None and stated.isdigit() and int(stated) != len(transactions):
        raise InvalidSaftFileError(
            f"filen sier at den har {stated} bilag, men Myntos fant {len(transactions)}. Den kan "
            "være avkortet: last den ned på nytt"
        )
    stated_debit = _decimal(reader.totals.get("TotalDebit"), "TotalDebit")
    debit = sum(
        (line.amount for voucher in transactions for line in voucher.lines if line.amount > 0),
        ZERO,
    )
    if stated_debit is not None and stated_debit != debit:
        raise InvalidSaftFileError(
            f"filen sier at bilagene har {stated_debit} i debet, men linjene summerer til {debit}. "
            "Den kan være avkortet: last den ned på nytt"
        )
    return SaftEntries(balances=balances, transactions=tuple(transactions))


def read_entries_bytes(content: bytes) -> SaftEntries:
    """:func:`read_entries` for content already in memory."""
    return read_entries(io.BytesIO(content))
