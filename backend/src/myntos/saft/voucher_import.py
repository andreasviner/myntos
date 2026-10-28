"""The year so far, brought from the previous program's SAF-T file.

The opening balance (:mod:`myntos.saft.opening_import`) is enough for a company
that moves on 1 January. One that moves in October, or that keeps its old
program for a while and runs Myntos beside it to see whether the two agree,
needs January to September as well, and the same file holds them: every
voucher, line by line, with its VAT. This reads them into vouchers of the
``IMPORT`` journal, reviewed first and posted only when nothing in the review
stops it.

Built for the farm pilot, whose Tripletex file was the first one read
(docs/landbruk.md), and written for every program that writes SAF-T. The rules,
each one a thing that file did or a way a second import could go wrong:

**The opening balance first, and nothing else in the period.** The vouchers
are the year's movements; without the balance they move from, every account is
wrong by its opening. And a voucher already registered in Myntos in the same
weeks would be the same purchase twice, so the import refuses to run over one
and says from which day it may.

**One voucher per date.** Tripletex adds an invoice's payment to the invoice's
own voucher, each line dated the day it happened (``ValueDate``), so voucher 1
is a cost on 8 January and its payment on 9 January. A voucher in Myntos has one
date, and an entry has to land on the day it happened or the VAT period and the
bank balance are wrong on every day in between. So the lines are split by date,
provided every date's lines balance on their own. Where they do not, the
program meant something else by the date, and the voucher is taken whole on its
posting date instead.

**The old program's numbers.** ``2026-240`` is Tripletex's bilag 240 of 2026,
and a part on another date carries the date after it (``2026-1-0109``). The
documentation stays in the old program's archive, and the number is how it is
found there. The numbers are also what makes a second import safe: a voucher
already brought over is skipped, so a later file can bring the weeks since.

**The accounts the opening balance used.** The same four digits, unless the
opening balance moved one: when the accountant had the driftskreditt's row put
on 1920, its payments go to 1920 too, or the bank would start right and drift
from the first payment. The old account id stays in each line's text.

**Output VAT on 2700 and input VAT on 2710.** Tripletex keeps a sub-account
per rate (2711 for 15 % input VAT); Myntos keeps one of each, and the MVA page
reconciles the return against those two. A line on 2701-2709 goes on 2700 and
one on 2711-2719 on 2710, with the old account in its text.

**VAT as the old program reported it.** Each line's tax information is copied
as it stands (code, rate, basis and the two amounts), because those are the
figures behind the VAT returns already filed. The code is the file's standard
code, and Tripletex's ``UTTAK-3``, ``TAP-1``, ``JUST-1`` and ``TILB-1`` become
code 3 or 1 with the melding's spesifikasjon, which is what they stand for.
A non-reportable code with no VAT on it (0, 7, 20-22) is left off rather than
refused. Compensation codes (``-K``) belong to a category Myntos does not file,
and are refused.

**A line with several codes becomes a line per code.** SAF-T lets one line
carry several ``TaxInformation``. Myntos keeps one code on a line, so each
code gets a line of its own on the same account, its base as its amount and its
tax information as the file has it (:func:`vat_parts`). Every balance stays the
file's, and so does every VAT figure, code by code, which is how
:mod:`myntos.saft.compare` reads the file. A line whose bases add up to more
than its amount is still refused: then the codes are not parts of it.

**Fiken's VAT is read as Fiken meant it, not as it is written.** The first
person to bring their books over from Fiken could post none of their vouchers,
because lines that looked like several codes stopped the whole import, and
they were not several codes at all. Fiken writes a service bought
abroad as two entries of code 86 on the cost line, both with a base of 0.00:
the VAT as debit, which is the deduction, and the same VAT as credit, which is
what is owed. Split as above that is twice the base and no VAT. Running the
whole export through the VAT page then showed two habits more, on every line:
the base is always 0.00, and the VAT is always a debit, a sale's too, so a
sale read as written is a deduction and a term's melding came out tens of
thousands away from the VAT accounts, while the comparison, reading the file
the same wrong way, saw nothing. :func:`vat_as_kept` reads each back: the reverse charge as
the single entry Myntos and Skatteetaten's examples use, an ordinary code's
VAT on its line's side, and a base of 0 as the line's amount where the VAT is
the rate of it to the øre. The comparison reads the file through it too, so
the independent check of this is the VAT page's, against 2700 and 2710.

**Avdelinger, prosjekter and driftsgrener come along; the rest of the file's
dimensions do not.** A line's ``Analysis`` is kept when the file's name for the
dimension says it is one Myntos knows (:func:`myntos.domain.ledger.driftsgrener.kind_of`):
Tripletex's ``A`` «Avdeling» is. Its ``B`` «Bilagsart» repeats the voucher type
and its ``E`` «Ansatte» names employees, which the books do not need copied into
a second program, so both stay behind with a note. Each line gets the value for
its whole amount: Tripletex writes a debit analysis amount on credit lines too,
so the file's own amount says nothing the line does not.

**On a farm, the account id carries the avdeling and the product.**
Tripletex's farm chart writes NIBIO's department after the four digits and,
on some accounts, its own three-digit product code after that (``145120512``:
1451, avdeling 20, product 512 «Kyr tilført i året»). In books with the farming
add-on, such an id gives the line an «Avdeling» and a «Produkt», unless the line
names its avdeling itself, which wins: the two disagree on some balance lines,
and the line is what the old program reported on. Outside the add-on six
digits are only a sub-account, as they always were.

**Dimensions the books lack are added first, like accounts and parties.** A
posted line cannot be given a dimension afterwards, so a value the company
does not have yet stops the import (``dimensjoner_mangler``) and the review
lists it in ``dimensions_to_add``, to be added through the dimension routes.

**What is not checked.** Whether the old program booked it right. The import
copies; :mod:`myntos.saft.compare` is what says whether the two programs agree.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from myntos.domain.arsoppgjor import opening
from myntos.domain.arsoppgjor.findings import Finding, blocking, feil, merknad
from myntos.domain.ledger import dimensions, driftsgrener, landbruk, posting
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.chart import (
    IMPORT_JOURNAL_DESCRIPTION,
    IMPORT_JOURNAL_ID,
    IMPORT_JOURNAL_TYPE,
    IMPORT_VOUCHER_TYPE,
    INPUT_VAT_ACCOUNT,
    OPENING_JOURNAL_ID,
    OUTPUT_VAT_ACCOUNT,
    SETTLEMENT_VOUCHER_TYPE,
)
from myntos.domain.ledger.models import (
    Account,
    Company,
    DimensionType,
    DimensionValue,
    FinancialYear,
    Journal,
    Party,
    PartyType,
    TaxCode,
    Transaction,
    TransactionLine,
)
from myntos.domain.mva.codes import MVA_CODES, CodeKind, Spesifikasjon
from myntos.saft.errors import InvalidVoucherImportError, SaftWrongYearError
from myntos.saft.opening_import import (
    AccountToAdd,
    PartyToAdd,
    chart_of,
    check_company,
    folds_into_vat_settlement,
    match_party,
    party_to_add,
    propose_accounts,
    target_account,
)
from myntos.saft.reader import (
    AnalysisRecord,
    PartyKind,
    PartyRecord,
    SaftEntries,
    SourceAccount,
    SourceLine,
    SourceTax,
    SourceTransaction,
)

ZERO = Decimal("0.00")
VAT_SETTLEMENT_ACCOUNT = "2740"
_RATE = Decimal("0.000001")

#: How far each code's base may be rounded, when a line's codes share it out.
_BASE_ROUNDING = Decimal("0.01")

#: SAF-T's RecordID is 18 characters at most.
_RECORD_ID_LENGTH = 18

#: Tripletex's own codes for a standard code used with a spesifikasjon.
_SPESIFIKASJON_PREFIXES: tuple[tuple[str, Spesifikasjon], ...] = (
    ("UTTAK-", Spesifikasjon.UTTAK),
    ("TAP-", Spesifikasjon.TAP_PAA_KRAV),
    ("JUST-", Spesifikasjon.JUSTERING),
    ("TILB-", Spesifikasjon.TILBAKEFOERING),
)

#: Standard codes that never reach a melding. On a line with no VAT they are
#: left off, since Myntos does not keep a code for each of them.
_SILENT_CODES = frozenset({"0", "7", "20", "21", "22"})

#: The most vouchers named in one finding; the rest are counted.
_NAMED = 5

#: A farm account id with NIBIO's department after the four digits, and
#: perhaps Tripletex's product code after that: ``291610``, ``145120512``.
_FARM_ACCOUNT = re.compile(r"^\d{4}(\d{2})(\d{3})?$")

#: What a product is called, from the account it sits on: «Lagerbeh.: Korn
#: (Hvete)» is «Hvete».
_PARENTHETICAL = re.compile(r"\(([^()]+)\)\s*$")


@dataclass(frozen=True)
class PlannedLine:
    record_id: str
    account_number: str
    description: str
    debit: Decimal
    credit: Decimal
    party_pk: int | None
    tax_code: str | None = None
    tax_rate: Decimal | None = None
    tax_base: Decimal | None = None
    debit_tax: Decimal | None = None
    credit_tax: Decimal | None = None
    spesifikasjon: str | None = None
    currency_code: str | None = None
    currency_amount: Decimal | None = None
    exchange_rate: Decimal | None = None
    #: ``(kind, value)``: ``("AVDELING", "20")``, ``("PRODUKT", "512")``.
    dimensions: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class DimensionToAdd:
    """A value the vouchers carry that the company's dimensions do not have yet."""

    #: The dimension's code in Myntos: the company's own when it has a
    #: dimension of this kind, else the one to make it with (``AVDELING``).
    code: str
    #: Its name: the company's, or the one to make it with («Avdeling»).
    dimension_description: str
    #: The company's dimension the value goes in, or None when it has to be
    #: made first.
    dimension_id: int | None
    value: str
    description: str


@dataclass(frozen=True)
class PlannedVoucher:
    #: The number in Myntos's ``IMPORT`` journal.
    voucher_no: str
    #: The old program's own number for it.
    source_id: str
    on: date
    description: str
    document: str
    lines: tuple[PlannedLine, ...]
    #: ``importert``, or the settlement's type for a term the old program
    #: settled, so the year's VAT figures leave it out as they do Myntos's own.
    voucher_type: str = IMPORT_VOUCHER_TYPE

    @property
    def total(self) -> Decimal:
        return sum((line.debit for line in self.lines), ZERO)


@dataclass(frozen=True)
class MonthCount:
    month: int
    vouchers: int
    total: Decimal


@dataclass(frozen=True)
class ImportSource:
    software: str | None
    audit_file_version: str
    orgnr: str
    company_name: str
    start: date
    end: date


@dataclass(frozen=True)
class VoucherImport:
    """What posting the file would do, and what stops it."""

    source: ImportSource
    year: int
    #: The last day brought over: the request's ``til``, or the file's last.
    through: date
    vouchers: list[PlannedVoucher]
    #: Vouchers in the period that an earlier import already brought over.
    already_imported: int
    #: Vouchers dated after ``through``, left for a later import.
    later: int
    months: list[MonthCount]
    accounts_to_add: list[AccountToAdd]
    parties_to_add: list[PartyToAdd]
    findings: list[Finding]
    dimensions_to_add: list[DimensionToAdd] = field(default_factory=list)

    @property
    def postable(self) -> bool:
        return bool(self.vouchers) and not blocking(self.findings)

    @property
    def first_date(self) -> date | None:
        return min((v.on for v in self.vouchers), default=None)

    @property
    def last_date(self) -> date | None:
        return max((v.on for v in self.vouchers), default=None)

    @property
    def line_count(self) -> int:
        return sum(len(v.lines) for v in self.vouchers)


@dataclass(frozen=True)
class ImportedVouchers:
    posted: int
    first_voucher_no: str
    last_voucher_no: str
    first_date: date
    last_date: date


def _day(value: date) -> str:
    return f"{value.day}.{value.month}.{value.year}"


def voucher_number(transaction: SourceTransaction, on: date) -> str:
    """``2026-240``, or ``2026-1-0109`` for the part of voucher 1 dated 9 January."""
    year = transaction.period_year or transaction.on.year
    number = f"{year}-{transaction.transaction_id}"
    if on != transaction.on:
        number += f"-{on:%m%d}"
    return number[:64]


def _document(software: str | None, transaction: SourceTransaction) -> str:
    """Where the documentation is: «Tripletex bilag 240-2026». SAF-T allows 35 characters."""
    year = transaction.period_year or transaction.on.year
    reference = f"{(software or 'SAF-T')[:12]} bilag {transaction.transaction_id}-{year}"
    return reference if len(reference) <= 35 else reference[-35:]


def parts_by_date(transaction: SourceTransaction) -> dict[date, list[SourceLine]]:
    """The voucher's lines per date, or all of them on one date when the dates do not balance."""
    parts: dict[date, list[SourceLine]] = defaultdict(list)
    for line in transaction.lines:
        parts[line.on].append(line)
    if len(parts) > 1 and any(
        sum((line.amount for line in lines), ZERO) != ZERO for lines in parts.values()
    ):
        return {transaction.posted_on or transaction.on: list(transaction.lines)}
    return dict(parts)


def _settles_vat(lines: Sequence[PlannedLine], vat_accounts: set[str]) -> bool:
    """A filed term's settlement: the VAT accounts moved onto 2740, and nothing else but øre.

    Tripletex's «Mva-melding, Januar - Februar» is 2700 and 2710 against 2740,
    with the return's rounding to whole kroner on 7740. The payment that
    follows is the bank against 2740, which is not one.

    The rounding may be up to a krone for each line it settles, not a krone in
    all: the old melding took each post in whole kroner, and Fiken's settlement
    of a term with two input VAT lines (223.50 and 70.93, taken as 223 and 70)
    put 1.43 on 7740. Dated on the term's last day, it then counted as the
    term's VAT and the VAT page put the whole payable amount down as a
    difference against the accounts.
    """
    if any(line.tax_code is not None for line in lines):
        return False
    on_vat = [line.account_number for line in lines if line.account_number in vat_accounts]
    others = [line for line in lines if line.account_number not in vat_accounts]
    settled = [number for number in on_vat if number != VAT_SETTLEMENT_ACCOUNT]
    return (
        VAT_SETTLEMENT_ACCOUNT in on_vat
        and bool(settled)
        and sum((line.debit + line.credit for line in others), ZERO) < len(settled)
    )


def vat_home(number: str) -> str:
    """2700 for any output VAT sub-account (270x), 2710 for any input one (271x)."""
    if number.startswith("270"):
        return OUTPUT_VAT_ACCOUNT
    if number.startswith("271"):
        return INPUT_VAT_ACCOUNT
    return number


def split_code(code: str) -> tuple[str, str | None]:
    """``("3", "uttak")`` for Tripletex's ``UTTAK-3``; the code itself otherwise."""
    for prefix, spesifikasjon in _SPESIFIKASJON_PREFIXES:
        if code.upper().startswith(prefix):
            return code[len(prefix) :], spesifikasjon.value
    return code, None


def vat_as_kept(line: SourceLine, file_codes: Mapping[str, str]) -> SourceLine:
    """The line with its VAT as Myntos keeps it, read from what the file wrote.

    Myntos reads a VAT entry's side as its direction (credit is owed, debit is
    deducted) and reports its base. Fiken's files write neither the way that
    reading needs, in three ways, each read back to what Fiken reported:

    - **The base is 0.00 on every entry.** The base is then the line's amount,
      but only where the VAT is the code's rate of that amount to the øre: a
      base that cannot be checked would be a guess on the melding.
    - **The VAT is always a debit**, on a sale as on a purchase. Read as written
      a sale is a deduction: the first Fiken export tried came out with output
      VAT below zero, tens of thousands away from 2700 in a term. On an ordinary
      code the VAT goes the way its line goes, a sale owed and a kreditnota
      taken back, so the amount is put on the line's side. A code with a
      spesifikasjon (Tripletex's ``TILB-1`` and the rest) is left as written,
      because its direction is the spesifikasjon's, not the line's.
    - **A reverse charge is two entries** of the same code on the cost line, the
      VAT as debit (the deduction) and as credit (what is owed), which read as
      written nets to nothing. Myntos keeps one entry with the VAT on the
      purchase's side, and the melding makes both lines of it.

    Tripletex's files do none of this, and their lines come back as they are.
    """
    merged = _reverse_charge_merged(line, file_codes)
    if merged is not None:
        return merged
    taxes = tuple(
        _ordinary(line, tax, file_codes, alone=len(line.taxes) == 1) for tax in line.taxes
    )
    return line if taxes == line.taxes else replace(line, taxes=taxes)


def _is_rate_of(base: Decimal, percentage: Decimal, vat: Decimal) -> bool:
    return base != ZERO and (
        abs((abs(base) * percentage / 100).quantize(ZERO) - abs(vat)) <= _BASE_ROUNDING
    )


def _base_of(line: SourceLine, tax: SourceTax, vat: Decimal) -> Decimal:
    """The entry's base, or the line's amount when the file gave 0 and the VAT is its rate."""
    if tax.base != ZERO:
        return tax.base
    return abs(line.amount) if _is_rate_of(line.amount, tax.percentage, vat) else ZERO


def _ordinary(
    line: SourceLine, tax: SourceTax, file_codes: Mapping[str, str], *, alone: bool
) -> SourceTax:
    """An ordinary code's entry, its VAT on its line's side and a base of 0 read off the line."""
    stripped, spesifikasjon = split_code(tax.code)
    known = MVA_CODES.get(file_codes.get(tax.code) or stripped)
    if spesifikasjon is not None or known is None or known.kind is CodeKind.OMVENDT:
        return tax
    if line.amount == ZERO or min(tax.debit_tax, tax.credit_tax) < ZERO:
        return tax
    if tax.debit_tax and tax.credit_tax:
        return tax
    vat = tax.debit_tax or tax.credit_tax
    # Several codes share their line, so only a lone code's base can be the line.
    base = _base_of(line, tax, vat) if alone else tax.base
    # The VAT is moved only where it is the rate of the base, which says the
    # entry is about this line: a code written on the customer's line instead of
    # the sale's would otherwise be turned into a deduction.
    if not _is_rate_of(base, tax.percentage, vat):
        return tax
    on_debit = line.amount > ZERO
    return replace(
        tax,
        base=base,
        debit_tax=vat if on_debit else ZERO,
        credit_tax=ZERO if on_debit else vat,
    )


def _reverse_charge_merged(line: SourceLine, file_codes: Mapping[str, str]) -> SourceLine | None:
    """The two entries of a reverse charge as one, on the purchase's side; None if not one."""
    if len(line.taxes) != 2:
        return None
    first, second = line.taxes
    if (first.code, first.percentage, abs(first.base)) != (
        second.code,
        second.percentage,
        abs(second.base),
    ):
        return None
    known = MVA_CODES.get(file_codes.get(first.code) or split_code(first.code)[0])
    if known is None or known.kind is not CodeKind.OMVENDT:
        return None
    owed = [tax.credit_tax for tax in line.taxes if tax.credit_tax and not tax.debit_tax]
    deducted = [tax.debit_tax for tax in line.taxes if tax.debit_tax and not tax.credit_tax]
    if len(owed) != 1 or owed != deducted:
        return None
    vat = owed[0]
    base = abs(_base_of(line, first, vat))
    if base == ZERO:
        return None
    on_debit = line.amount > ZERO
    merged = SourceTax(
        code=first.code,
        percentage=first.percentage,
        base=base,
        debit_tax=vat if on_debit else ZERO,
        credit_tax=ZERO if on_debit else vat,
    )
    return replace(line, taxes=(merged,))


def vat_parts(line: SourceLine) -> list[tuple[Decimal, SourceTax | None]] | None:
    """The line as Myntos posts it: one part per VAT code, each signed as the line is.

    A line with one code, or none, is one part. With several, each code's part
    is its base, and what the bases leave over goes with the largest: a part of
    the line no VAT was calculated on, as on a line with one code whose base is
    less than its amount, or the øre each base was rounded by. The tax
    information is not touched, so the VAT is the file's whatever the shares.
    An entry with neither base nor VAT carries nothing and is left out.

    None when the bases add up to more than the line, or a code would get
    nothing: then the codes are not parts of the line, and how much of it each
    one is cannot be read off the file.
    """
    if len(line.taxes) <= 1:
        return [(line.amount, line.taxes[0] if line.taxes else None)]
    taxes = [tax for tax in line.taxes if tax.base or tax.debit_tax or tax.credit_tax]
    if len(taxes) <= 1:
        return [(line.amount, taxes[0] if taxes else line.taxes[0])]
    shares = [abs(tax.base) for tax in taxes]
    left = abs(line.amount) - sum(shares, ZERO)
    if left < -_BASE_ROUNDING * len(taxes):
        return None
    shares[shares.index(max(shares))] += left
    if any(share <= ZERO for share in shares):
        return None
    sign = 1 if line.amount > ZERO else -1
    return [(sign * share, tax) for share, tax in zip(shares, taxes, strict=True)]


def _record_id(line: SourceLine, index: int, part: int, taken: set[str]) -> str:
    """The file's RecordID, with ``-2`` for the line's second code; else the line's place."""
    suffix = f"-{part + 1}" if part else ""
    candidate = f"{index}{suffix}"
    if line.record_id:
        own = f"{line.record_id}{suffix}"
        if len(own) <= _RECORD_ID_LENGTH and own not in taken:
            candidate = own
    taken.add(candidate)
    return candidate


def _shared(total: Decimal, parts: Sequence[Decimal]) -> list[Decimal]:
    """``total`` split as ``parts`` split their sum, to the øre, the rounding on the largest."""
    whole = sum((abs(part) for part in parts), ZERO)
    shares = [(total * abs(part) / whole).quantize(ZERO) for part in parts]
    largest = max(range(len(parts)), key=lambda index: abs(parts[index]))
    shares[largest] += total - sum(shares, ZERO)
    return shares


def account_map(
    standing: opening.OpeningVoucher | None, accounts: Sequence[SourceAccount]
) -> dict[str, str]:
    """The file's account ids that the standing opening balance put somewhere else.

    The opening balance's lines from a SAF-T file start with the file's account
    id (:mod:`myntos.saft.opening_import`), so a row the person moved, the
    driftskreditt to 1920, says so in its text. The VAT accounts moved to 2740
    are not followed: that is last year's VAT being settled, and this year's
    goes on 2700 and 2710 as before.
    """
    if standing is None:
        return {}
    by_id = {account.account_id: account for account in accounts}
    moved: dict[str, str] = {}
    for line in standing.lines:
        if line.party_pk is not None:
            continue
        token = (line.description or "").split(" ", 1)[0]
        source = by_id.get(token)
        if source is None:
            continue
        default = target_account(token)
        if line.account_number == default or folds_into_vat_settlement(default, source):
            continue
        moved[token] = line.account_number
    return moved


async def _journal(db: AsyncSession, company: Company, *, create: bool) -> Journal | None:
    journal = await db.scalar(
        select(Journal).where(
            Journal.company_id == company.id, Journal.journal_id == IMPORT_JOURNAL_ID
        )
    )
    if journal is None and create:
        journal = Journal(
            company_id=company.id,
            journal_id=IMPORT_JOURNAL_ID,
            description=IMPORT_JOURNAL_DESCRIPTION,
            journal_type=IMPORT_JOURNAL_TYPE,
        )
        db.add(journal)
        await db.flush()
    return journal


async def _company_dimensions(db: AsyncSession, company: Company) -> list[DimensionType]:
    return list(
        await db.scalars(
            select(DimensionType)
            .where(DimensionType.company_id == company.id)
            .options(selectinload(DimensionType.values))
            .order_by(DimensionType.code)
        )
    )


def _product_name(description: str, code: str) -> str:
    """«Hvete» from «Lagerbeh.: Korn (Hvete)»; the whole description when it has no brackets."""
    found = _PARENTHETICAL.search(description)
    name = found.group(1) if found else description
    return name.strip()[: dimensions.MAX_DESCRIPTION] or f"Produkt {code}"


class _Dimensions:
    """Which avdeling, prosjekt or driftsgren each line of the file belongs to.

    Gathers, while the lines are read, what the review says about them: the
    values the company lacks, the file's dimensions left behind, and the lines
    where the account and the line disagree.
    """

    def __init__(
        self,
        company: Company,
        balances_analysis: Sequence[AnalysisRecord],
        accounts: dict[str, SourceAccount],
        existing: Sequence[DimensionType],
    ) -> None:
        self.farm = landbruk.is_enabled(company)
        self.accounts = accounts
        self.existing = existing
        self.type_names: dict[str, str] = {}
        self.type_kinds: dict[str, str] = {}
        self.names: dict[tuple[str, str], str] = {}
        for record in balances_analysis:
            self.type_names.setdefault(record.analysis_type, record.type_description)
            kind = driftsgrener.kind_of(record.type_description)
            if kind is None:
                continue
            self.type_kinds[record.analysis_type] = kind
            self.names.setdefault((kind, record.analysis_id), record.description)
        self.used: dict[tuple[str, str], None] = {}
        self.dropped: set[str] = set()
        self.from_account = 0
        self.disagree = 0
        self.split = 0

    def of(self, line: SourceLine) -> tuple[tuple[str, str], ...]:
        chosen: dict[str, str] = {}
        for item in line.analysis:
            kind = self.type_kinds.get(item.analysis_type)
            if kind is None:
                self.dropped.add(self.type_names.get(item.analysis_type, item.analysis_type))
                continue
            if kind in chosen:
                # SAF-T lets a file split a line between two values of one
                # dimension; Myntos puts a line on one. The first stands.
                if chosen[kind] != item.analysis_id:
                    self.split += 1
                continue
            chosen[kind] = item.analysis_id
        matched = _FARM_ACCOUNT.match(line.account_id) if self.farm else None
        if matched is not None and matched.group(1) in driftsgrener.DEPARTMENT_NAMES:
            department, product = matched.group(1), matched.group(2)
            self.from_account += 1
            own = chosen.get(driftsgrener.AVDELING)
            if own is None:
                chosen[driftsgrener.AVDELING] = department
                self.names.setdefault(
                    (driftsgrener.AVDELING, department),
                    driftsgrener.DEPARTMENT_NAMES[department],
                )
            elif own != department:
                self.disagree += 1
            if product is not None and driftsgrener.PRODUKT not in chosen:
                chosen[driftsgrener.PRODUKT] = product
                account = self.accounts.get(line.account_id)
                self.names.setdefault(
                    (driftsgrener.PRODUKT, product),
                    _product_name(account.description if account else "", product),
                )
        for key in chosen.items():
            self.used.setdefault(key)
        return tuple(sorted(chosen.items()))

    def to_add(self) -> list[DimensionToAdd]:
        found: list[DimensionToAdd] = []
        for kind, value in self.used:
            dimension = dimensions.find_kind(self.existing, kind)
            if dimension is not None and any(item.value == value for item in dimension.values):
                continue
            found.append(
                DimensionToAdd(
                    code=dimension.code if dimension is not None else kind,
                    dimension_description=(
                        dimension.description
                        if dimension is not None
                        else driftsgrener.KIND_NAMES[kind]
                    ),
                    dimension_id=dimension.id if dimension is not None else None,
                    value=value,
                    description=(self.names.get((kind, value)) or value)[
                        : dimensions.MAX_DESCRIPTION
                    ],
                )
            )
        return sorted(found, key=lambda item: (item.code, item.value.zfill(12)))

    def findings(self) -> list[Finding]:
        found: list[Finding] = []
        missing = self.to_add()
        if missing:
            kinds = sorted({item.dimension_description for item in missing})
            found.append(
                feil(
                    "dimensjoner_mangler",
                    f"Bilagene fører på {len(missing)} verdier for "
                    + ", ".join(kinds)
                    + " som ikke finnes i Myntos ennå. Legg dem til, så får linjene dem: en "
                    "linje som er ført, kan ikke få en avdeling etterpå.",
                )
            )
        if self.dropped:
            found.append(
                merknad(
                    "dimensjoner_utelatt",
                    "Filen sorterer også linjene etter "
                    + ", ".join(f"«{name}»" for name in sorted(self.dropped))
                    + ". Myntos tar med avdelinger, prosjekter, driftsgrener og produkter, og "
                    "lar resten bli i det gamle programmet.",
                )
            )
        if self.from_account:
            found.append(
                merknad(
                    "avdeling_fra_kontonummer",
                    "Kontonumrene i filen har avdelingen etter de fire sifrene, og noen også et "
                    "produkt (145120512 er konto 1451, avdeling 20, produkt 512). Linjene får dem "
                    "som «Avdeling» og «Produkt».",
                )
            )
        if self.disagree:
            found.append(
                merknad(
                    "avdeling_ulik_kontonummer",
                    f"På {self.disagree} linjer sier linjen selv en annen avdeling enn "
                    "kontonummeret. Linjens egen avdeling er brukt, som i det gamle programmet.",
                )
            )
        if self.split:
            found.append(
                merknad(
                    "dimensjon_delt",
                    f"{self.split} linjer er delt mellom to verdier i samme dimensjon. Myntos "
                    "fører en linje på én verdi, og den første i filen er brukt.",
                )
            )
        return found


async def plan(
    db: AsyncSession,
    *,
    company: Company,
    found: SaftEntries,
    til: date | None = None,
    year: int | None = None,
    require_opening: bool = True,
) -> VoucherImport:
    """What posting ``found``'s vouchers through ``til`` would do. Stores nothing.

    ``year`` is the year the file is for. Left out, it is the company's first
    year in Myntos, which is the only year one file on its own can bring: a
    later year needs the years before it in Myntos already, and an earlier one
    needs a year opened, which is :mod:`myntos.saft.move`'s to do.

    ``require_opening`` False is for :mod:`myntos.saft.move`, which posts the
    opening balance itself in the same transaction, or knows there is none to
    post: a company whose books start in the file's own year opens at zero.
    """
    balances = found.balances
    check_company(company, balances.orgnr, balances.company_name)
    first_row = await opening.first_year(db, company)
    first = first_row.year if first_row is not None else balances.start.year
    wanted = first if year is None else year
    if balances.start != date(wanted, 1, 1) or wanted < first:
        raise SaftWrongYearError(
            f"filen gjelder {balances.start.isoformat()} til {balances.end.isoformat()}. Bilagene "
            f"hentes for {wanted}, det første året i Myntos: last opp SAF-T-filen for {wanted}, "
            "fra 1. januar"
            if year is None
            else f"filen gjelder {balances.start.isoformat()} til {balances.end.isoformat()}, "
            f"ikke {wanted} fra 1. januar"
        )
    year_row = (
        first_row
        if wanted == first
        else await db.scalar(
            select(FinancialYear).where(
                FinancialYear.company_id == company.id, FinancialYear.year == wanted
            )
        )
    )
    first = wanted
    start = date(first, 1, 1)
    through = min(til or balances.end, balances.end, date(first, 12, 31))
    source = ImportSource(
        software=balances.software,
        audit_file_version=balances.audit_file_version,
        orgnr=balances.orgnr,
        company_name=balances.company_name,
        start=balances.start,
        end=balances.end,
    )
    findings: list[Finding] = []

    # --- What has to be in place -----------------------------------------------
    standing = await opening.state(db, company=company)
    if standing.standing is None and require_opening:
        findings.append(
            feil(
                "inngaende_balanse_mangler",
                f"Inngående balanse for {first} er ikke ført. Før den fra den samme SAF-T-filen "
                "på siden Inngående balanse først: bilagene bygger videre på den.",
            )
        )
    if year_row is not None and year_row.closed_at is not None:
        findings.append(
            feil(
                "aaret_er_avsluttet",
                f"Regnskapsåret {first} er avsluttet i Myntos, og bilag kan bare føres i et åpent "
                "år.",
            )
        )
    if through < start:
        findings.append(
            feil("ingen_periode", f"«Til og med» må være 1. januar {first} eller senere.")
        )
    count, earliest = (
        await db.execute(
            select(func.count(), func.min(Transaction.gl_posting_date))
            .join(Journal, Journal.id == Transaction.journal_id)
            .where(
                Journal.company_id == company.id,
                Journal.journal_id.not_in((OPENING_JOURNAL_ID, IMPORT_JOURNAL_ID)),
                Transaction.posted_at.is_not(None),
                Transaction.gl_posting_date >= start,
                Transaction.gl_posting_date <= through,
            )
        )
    ).one()
    if count:
        findings.append(
            feil(
                "bilag_i_perioden",
                f"Myntos har allerede {count} bilag fra {_day(earliest)} til {_day(through)} som "
                "ikke kom fra filen. Bilagene i filen ville kommet i tillegg, og de samme kjøpene "
                f"og salgene ville stått to ganger. Hent bilagene til og med dagen før "
                f"{_day(earliest)}, eller reverser bilagene i Myntos først.",
            )
        )

    journal = await _journal(db, company, create=False)
    existing: set[str] = (
        set(
            await db.scalars(
                select(Transaction.transaction_id).where(Transaction.journal_id == journal.id)
            )
        )
        if journal is not None
        else set()
    )
    accounts, groupings = await chart_of(db, company, first)
    moved = account_map(standing.standing, balances.accounts)
    source_accounts = {account.account_id: account for account in balances.accounts}
    sorting = _Dimensions(
        company, balances.analysis, source_accounts, await _company_dimensions(db, company)
    )
    parties = list(await db.scalars(select(Party).where(Party.company_id == company.id)))
    register = {(record.kind, record.party_id): record for record in balances.register}
    tax_codes = {
        code.code: code
        for code in await db.scalars(select(TaxCode).where(TaxCode.company_id == company.id))
    }
    file_codes = balances.tax_codes or {}
    vat_accounts = {OUTPUT_VAT_ACCOUNT, INPUT_VAT_ACCOUNT, VAT_SETTLEMENT_ACCOUNT} | {
        number
        for account in balances.accounts
        if folds_into_vat_settlement(number := target_account(account.account_id), account)
        and number is not None
    }

    # --- The vouchers --------------------------------------------------------------
    vouchers: list[PlannedVoucher] = []
    already = later = before = split = dropped_codes = codes_shared = 0
    reverse_charges: set[str] = set()
    reverse_charge_lines = turned = based = 0
    unbalanced: list[str] = []
    unknown_accounts: set[str] = set()
    unknown_codes: set[str] = set()
    vat_folded: set[str] = set()
    several_codes: list[str] = []
    missing: dict[str, dict[str, SourceAccount]] = defaultdict(dict)
    parties_to_add: list[PartyToAdd] = []
    party_pks: dict[tuple[PartyKind, str], int | None] = {}
    taken = {(party.party_type, party.party_id) for party in parties}

    def party_for(kind: PartyKind, party_id: str) -> int | None:
        key = (kind, party_id)
        if key not in party_pks:
            record = register.get(key) or PartyRecord(
                kind=kind,
                party_id=party_id,
                name=party_id,
                orgnr=None,
                address_line=None,
                postal_code=None,
                city=None,
            )
            party_type = PartyType(kind)
            matched = match_party(parties, party_type, record.orgnr, record.name)
            if matched is None:
                parties_to_add.append(party_to_add(record, party_type, taken))
            party_pks[key] = matched.id if matched is not None else None
        return party_pks[key]

    for transaction in found.transactions:
        parts = parts_by_date(transaction)
        if len(parts) > 1:
            split += 1
        for on, source_lines in sorted(parts.items()):
            if on < start:
                before += 1
                continue
            if on > through:
                later += 1
                continue
            number = voucher_number(transaction, on)
            if number in existing:
                already += 1
                continue
            if sum((line.amount for line in source_lines), ZERO) != ZERO:
                unbalanced.append(number)
                continue
            lines: list[PlannedLine] = []
            records: set[str] = set()
            for index, source_line in enumerate(source_lines, start=1):
                if source_line.amount == ZERO and all(
                    tax.debit_tax == tax.credit_tax == ZERO for tax in source_line.taxes
                ):
                    continue
                line = vat_as_kept(source_line, file_codes)
                if len(line.taxes) < len(source_line.taxes):
                    reverse_charge_lines += 1
                    reverse_charges.add(line.taxes[0].code)
                elif line is not source_line:
                    pairs = list(zip(source_line.taxes, line.taxes, strict=True))
                    turned += any(
                        (written.debit_tax, written.credit_tax) != (kept.debit_tax, kept.credit_tax)
                        for written, kept in pairs
                    )
                    based += any(written.base != kept.base for written, kept in pairs)
                account_number = moved.get(line.account_id) or target_account(line.account_id)
                if account_number is None:
                    unknown_accounts.add(line.account_id)
                    continue
                if vat_home(account_number) != account_number:
                    vat_folded.add(line.account_id)
                    account_number = vat_home(account_number)
                if account_number not in accounts:
                    named = source_accounts.get(line.account_id) or SourceAccount(
                        account_id=line.account_id,
                        description="",
                        grouping_category=None,
                        grouping_code=None,
                        opening=ZERO,
                        closing=ZERO,
                    )
                    missing[account_number][line.account_id] = named
                party_pk = (
                    party_for(line.party_kind, line.party_id)
                    if line.party_kind is not None and line.party_id
                    else None
                )
                text = line.description or transaction.description
                if line.account_id != account_number:
                    text = f"{text} ({line.account_id})"
                shares = vat_parts(line)
                if shares is None:
                    several_codes.append(number)
                    # Read on with the first code alone, so the rest of the
                    # review is still there to read; the finding stops the post.
                    shares = [(line.amount, line.taxes[0])]
                elif len(shares) > 1:
                    codes_shared += 1
                currency_amounts = (
                    _shared(line.currency_amount, [amount for amount, _ in shares])
                    if line.currency_amount is not None and len(shares) > 1
                    else [line.currency_amount] * len(shares)
                )
                sorted_by = sorting.of(line)
                for part, ((amount, taxed), currency_amount) in enumerate(
                    zip(shares, currency_amounts, strict=True)
                ):
                    tax_code: str | None = None
                    spesifikasjon: str | None = None
                    if taxed is not None:
                        stripped, spesifikasjon = split_code(taxed.code)
                        standard = file_codes.get(taxed.code) or stripped
                        code = tax_codes.get(standard)
                        no_vat = taxed.debit_tax == taxed.credit_tax == ZERO
                        if taxed.code.upper().endswith("-K") or (
                            code is None and not (no_vat and standard in _SILENT_CODES)
                        ):
                            unknown_codes.add(taxed.code)
                        elif code is None:
                            dropped_codes += 1
                        else:
                            tax_code = code.code
                    lines.append(
                        PlannedLine(
                            record_id=_record_id(line, index, part, records),
                            account_number=account_number,
                            description=(text.strip() or "Bilag fra SAF-T-filen")[:255],
                            debit=amount if amount > ZERO else ZERO,
                            credit=-amount if amount < ZERO else ZERO,
                            party_pk=party_pk,
                            tax_code=tax_code,
                            tax_rate=(
                                (taxed.percentage / 100).quantize(_RATE)
                                if tax_code is not None and taxed is not None
                                else None
                            ),
                            tax_base=abs(taxed.base) if tax_code and taxed else None,
                            debit_tax=taxed.debit_tax if tax_code and taxed else None,
                            credit_tax=taxed.credit_tax if tax_code and taxed else None,
                            spesifikasjon=spesifikasjon if tax_code else None,
                            currency_code=line.currency_code,
                            currency_amount=currency_amount,
                            exchange_rate=line.exchange_rate,
                            dimensions=sorted_by,
                        )
                    )
            if len(lines) < 2:
                # Nothing but lines of nothing: there is no entry to make.
                continue
            vouchers.append(
                PlannedVoucher(
                    voucher_no=number,
                    source_id=transaction.transaction_id,
                    on=on,
                    description=(transaction.description or f"Bilag {transaction.transaction_id}")[
                        :255
                    ],
                    document=_document(balances.software, transaction),
                    lines=tuple(lines),
                    voucher_type=(
                        SETTLEMENT_VOUCHER_TYPE
                        if _settles_vat(lines, vat_accounts)
                        else IMPORT_VOUCHER_TYPE
                    ),
                )
            )
    vouchers.sort(key=lambda v: (v.on, v.voucher_no))

    # --- What it found -------------------------------------------------------------
    if unbalanced:
        findings.append(
            feil(
                "bilag_i_ubalanse",
                f"{len(unbalanced)} bilag i filen går ikke i null og kan ikke føres: "
                + ", ".join(unbalanced[:_NAMED])
                + (" …" if len(unbalanced) > _NAMED else "")
                + ". Ta ut filen på nytt; rett dem i det gamle programmet hvis det gjentar seg.",
            )
        )
    if unknown_accounts:
        findings.append(
            feil(
                "ukjent_konto",
                "Disse kontoene i filen begynner ikke med et firesifret kontonummer i klasse 1 "
                "til 8, og Myntos vet ikke hvor de skal: "
                + ", ".join(sorted(unknown_accounts)[:_NAMED])
                + ".",
            )
        )
    if unknown_codes:
        findings.append(
            feil(
                "mva_kode_ukjent",
                "Bilagene bruker mva-koder Myntos ikke fører: "
                + ", ".join(sorted(unknown_codes))
                + ". Koder for kompensasjon (-K) gjelder kommuner og ideelle organisasjoner.",
            )
        )
    if several_codes:
        findings.append(
            feil(
                "flere_mva_koder",
                f"{len(several_codes)} av linjene har flere mva-koder med grunnlag som til sammen "
                "er mer enn linjens beløp, så Myntos kan ikke se hvor mye av linjen hver kode "
                "gjelder. Bilag "
                + ", ".join(list(dict.fromkeys(several_codes))[:_NAMED])
                + (" …" if len(set(several_codes)) > _NAMED else "")
                + ". Si fra under «Meld feil eller ønsker», så ser vi på hvordan det gamle "
                "programmet har ført dem.",
            )
        )
    by_number = {number: list(sources.values()) for number, sources in missing.items()}
    accounts_to_add, notes = propose_accounts(
        first, by_number, accounts, groupings, compare_existing=False
    )
    findings.extend(notes)
    if accounts_to_add:
        findings.append(
            feil(
                "kontoer_mangler",
                f"Bilagene fører på {len(accounts_to_add)} kontoer som ikke finnes i kontoplanen "
                "ennå. Legg dem til med navnet og grupperingen fra filen.",
            )
        )
    findings.extend(sorting.findings())
    if parties_to_add:
        findings.append(
            feil(
                "parter_mangler",
                f"Bilagene nevner {len(parties_to_add)} kunder og leverandører som ikke finnes i "
                "Myntos ennå. Legg dem til, så får linjene dem.",
            )
        )
    if moved:
        findings.append(
            merknad(
                "konto_som_inngaende_balanse",
                "Inngående balanse fører "
                + ", ".join(f"{source} på {target}" for source, target in sorted(moved.items()))
                + ", og bilagene følger den.",
            )
        )
    if vat_folded:
        findings.append(
            merknad(
                "mva_underkonto",
                "Filen fører mva på " + ", ".join(sorted(vat_folded)) + ". Myntos har en "
                "konto for utgående mva (2700) og en for inngående (2710), og linjene står der, "
                "med kontoen fra filen i teksten.",
            )
        )
    if reverse_charge_lines:
        findings.append(
            merknad(
                "omvendt_avgift_samlet",
                f"{reverse_charge_lines} av linjene i filen fører omvendt avgiftsplikt (kode "
                + ", ".join(sorted(reverse_charges))
                + ") som to mva-oppføringer på samme linje, en for mva-en som skal betales og "
                "en for fradraget, slik Fiken gjør. Myntos fører dem som en oppføring, med "
                "linjens beløp som grunnlag når filen har 0, og mva-meldingen får både den "
                "utgående mva-en og fradraget.",
            )
        )
    if turned:
        findings.append(
            merknad(
                "mva_paa_linjens_side",
                f"{turned} av linjene i filen har mva-beløpet på motsatt side av linjen. Fiken "
                "fører all mva som debet, også på salg; Myntos fører mva-en samme vei som "
                "linjen, så et salg gir utgående mva og en kreditnota trekker den fra.",
            )
        )
    if based:
        findings.append(
            merknad(
                "mva_grunnlag_fra_linjen",
                f"{based} av linjene i filen har grunnlag 0, men mva som er satsen av linjens "
                "beløp. Myntos bruker linjens beløp som grunnlag, så mva-meldingen får "
                "grunnlaget med.",
            )
        )
    if codes_shared:
        findings.append(
            merknad(
                "mva_koder_delt",
                f"{codes_shared} av linjene i filen har flere mva-koder, som et salg med både 25 "
                "og 15 % mva på samme inntektskonto. Myntos fører en kode per linje, så hver av "
                "dem blir en linje per kode på samme konto, med grunnlaget og mva-beløpet fra "
                "filen.",
            )
        )
    if split:
        findings.append(
            merknad(
                "bilag_delt_paa_dato",
                f"{split} bilag i filen har linjer på flere datoer, som en faktura og betalingen "
                "av den. Myntos fører hver dato for seg, med datoen bak bilagsnummeret.",
            )
        )
    if already:
        findings.append(
            merknad(
                "allerede_hentet",
                f"{already} bilag er hentet før og hoppes over.",
            )
        )
    if later:
        findings.append(
            merknad(
                "bilag_etter_perioden",
                f"{later} bilag er datert etter {_day(through)} og blir ikke med nå.",
            )
        )
    if before:
        findings.append(
            merknad(
                "bilag_for_aaret",
                f"{before} bilag er datert før {_day(start)}. De hører til året før, og er med "
                "i inngående balanse eller i filen for det året.",
            )
        )
    if dropped_codes:
        findings.append(
            merknad(
                "mva_kode_utelatt",
                f"{dropped_codes} linjer har en mva-kode uten mva som ikke kommer med i "
                "mva-meldingen (0, 7 eller 20-22). Linjene føres uten kode.",
            )
        )
    if any(line.tax_code for voucher in vouchers for line in voucher.lines):
        findings.append(
            merknad(
                "mva_meldinger_sendt",
                "Bilagene har mva, og Mva-siden i Myntos regner dem med i terminene sine. Har det "
                "gamle programmet sendt mva-meldingen for en termin, skal den ikke sendes på nytt "
                "fra Myntos: Myntos vet ikke at den er sendt.",
            )
        )
    if not vouchers and not unbalanced:
        findings.append(
            merknad("ingen_bilag", f"Filen har ingen nye bilag til og med {_day(through)}.")
        )

    months: dict[int, list[PlannedVoucher]] = defaultdict(list)
    for voucher in vouchers:
        months[voucher.on.month].append(voucher)
    return VoucherImport(
        source=source,
        year=first,
        through=through,
        vouchers=vouchers,
        already_imported=already,
        later=later,
        months=[
            MonthCount(
                month=month,
                vouchers=len(items),
                total=sum((v.total for v in items), ZERO),
            )
            for month, items in sorted(months.items())
        ],
        accounts_to_add=accounts_to_add,
        parties_to_add=parties_to_add,
        findings=findings,
        dimensions_to_add=sorting.to_add(),
    )


async def post(
    db: AsyncSession,
    *,
    company: Company,
    found: SaftEntries,
    til: date | None,
    created_by_user_id: int | None,
    year: int | None = None,
    planned: VoucherImport | None = None,
) -> ImportedVouchers:
    """Post what :func:`plan` proposes, all of it or nothing. Cannot be undone, only reversed.

    ``planned`` is a plan the caller has just made in the same transaction, so a
    move of several years does not read each one twice.
    """
    if planned is None:
        planned = await plan(db, company=company, found=found, til=til, year=year)
    if not planned.postable:
        problems = blocking(planned.findings)
        raise InvalidVoucherImportError(
            problems[0].message if problems else "filen har ingen nye bilag å føre",
            problems,
        )
    journal = await _journal(db, company, create=True)
    assert journal is not None  # noqa: S101
    accounts = {
        account.account_number: account
        for account in await db.scalars(select(Account).where(Account.company_id == company.id))
    }
    parties = {
        party.id: party
        for party in await db.scalars(select(Party).where(Party.company_id == company.id))
    }
    tax_codes = {
        code.code: code
        for code in await db.scalars(select(TaxCode).where(TaxCode.company_id == company.id))
    }
    values: dict[tuple[str, str], DimensionValue] = {}
    existing = await _company_dimensions(db, company)
    for kind in {
        kind
        for voucher in planned.vouchers
        for line in voucher.lines
        for kind, _ in line.dimensions
    }:
        dimension = dimensions.find_kind(existing, kind)
        # plan() refused anything missing (dimensjoner_mangler).
        assert dimension is not None  # noqa: S101
        for value in dimension.values:
            values[(kind, value.value)] = value
    years = {
        year: await ledger_service.ensure_financial_year(db, company, year)
        for year in {voucher.on.year for voucher in planned.vouchers}
    }
    for voucher in planned.vouchers:
        transaction = Transaction(
            journal_id=journal.id,
            transaction_id=voucher.voucher_no,
            financial_year=years[voucher.on.year].year,
            period=voucher.on.month,
            transaction_date=voucher.on,
            gl_posting_date=voucher.on,
            description=voucher.description,
            voucher_type=voucher.voucher_type,
            source_document_id=voucher.document,
            created_by_user_id=created_by_user_id,
        )
        transaction.lines = [
            _line(line, accounts, parties, tax_codes, values) for line in voucher.lines
        ]
        posting.post(transaction)
        db.add(transaction)
    await db.flush()
    return ImportedVouchers(
        posted=len(planned.vouchers),
        first_voucher_no=planned.vouchers[0].voucher_no,
        last_voucher_no=planned.vouchers[-1].voucher_no,
        first_date=planned.vouchers[0].on,
        last_date=planned.vouchers[-1].on,
    )


def _line(
    line: PlannedLine,
    accounts: dict[str, Account],
    parties: dict[int, Party],
    tax_codes: dict[str, TaxCode],
    values: dict[tuple[str, str], DimensionValue],
) -> TransactionLine:
    made = TransactionLine(
        record_id=line.record_id,
        account=accounts[line.account_number],
        party=parties[line.party_pk] if line.party_pk is not None else None,
        description=line.description,
        debit=line.debit,
        credit=line.credit,
    )
    if line.currency_code is not None:
        made.currency_code = line.currency_code
        made.currency_amount = line.currency_amount
        made.exchange_rate = line.exchange_rate
    if line.tax_code is not None:
        made.tax_code = tax_codes[line.tax_code]
        made.tax_rate = line.tax_rate
        made.tax_base = line.tax_base
        made.debit_tax_amount = line.debit_tax
        made.credit_tax_amount = line.credit_tax
        made.vat_specification = line.spesifikasjon
    # Copied as the old program had them, switched off or not: this is history
    # being brought over, not a new entry choosing a value.
    dimensions.allocate(made, [values[key] for key in line.dimensions])
    return made


__all__ = [
    "DimensionToAdd",
    "ImportSource",
    "ImportedVouchers",
    "MonthCount",
    "PlannedLine",
    "PlannedVoucher",
    "VoucherImport",
    "account_map",
    "parts_by_date",
    "plan",
    "post",
    "split_code",
    "vat_home",
    "voucher_number",
]
