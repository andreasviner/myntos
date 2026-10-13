"""The books brought over: every year in the previous program's SAF-T files, in one go.

:mod:`myntos.saft.opening_import` reads one file's balances and
:mod:`myntos.saft.voucher_import` one year's vouchers, each for the company's
first year in Myntos and nothing else. That was enough for a company moving on
1 January, or in the middle of its first year, and it was all the farm pilot's
first parallel run needed. It was not what the pilot's farmer did next: he
uploaded the files for the years before as well, expecting them to come over,
and found that Myntos took the accounts from them and nothing else.

So this takes the files a person has, one per financial year, and brings all of
them over at once:

**The earliest file is where the books start.** Its opening balances are the
inngående balanse, posted on its 1 January, and the years in between are the
vouchers of each file. Myntos opens the years before its first one to take them
(:func:`myntos.domain.ledger.service.open_earlier_years`), which is the one
place a year before the first may be opened. Every later year then opens with
the year before's closing, derived from the postings, as it always does.

**An opening balance already posted for a later year is replaced, not doubled.**
A company that posted its 2026 opening balance and now brings 2025 over would
otherwise have 2025's closing twice in 2026: once from the postings, once from
the voucher. The standing one is reversed and the new one posted for the
earliest year, in one step (:func:`myntos.domain.arsoppgjor.opening.post_opening`
with ``correct``), and any account the person had moved in the old one (the
driftskreditt onto 1920) is moved in the new one too. One posted for the
earliest year is kept as it stands: the person may have checked it line by line.

**The files must be in a row, and only the last may be unfinished.** A gap
would leave a year whose movements nobody has, and every balance after it
wrong by them. A year cut short before the last one is the same gap at the end
of a year. The last one is the year the company moves in, and runs to the day
its file was taken.

**What the files name and the books lack is added first**: accounts, customers
and suppliers, and avdelinger and products, through the same functions the
kontoplan, party and dimension routes use, under the person's name. The page
used to do that one request at a time from the browser, 120 of them for the
farm, and read the file again after; that, more than the parsing, was what made
it slow.

**All of it, or none of it.** :func:`apply` does everything in the session it
is given and says what it did; the caller commits, or rolls back to show the
same report as a preview (:mod:`myntos.saft.jobs`). A preview made that way
cannot disagree with the posting, because it *is* the posting, undone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.core.clock import utc_now

from myntos.domain.arsoppgjor import opening
from myntos.domain.arsoppgjor.errors import (
    InvalidOpeningBalanceError,
    OpeningBalanceNotAllowedError,
)
from myntos.domain.arsoppgjor.findings import Finding, Severity, blocking, feil, merknad
from myntos.domain.ledger import dimensions, kontoplan, parties
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.chart import IMPORT_JOURNAL_ID, OPENING_JOURNAL_ID
from myntos.domain.ledger.errors import DuplicatePartyError
from myntos.domain.ledger.models import (
    Company,
    DimensionType,
    FinancialYear,
    Journal,
    PartyType,
    Transaction,
)
from myntos.saft import codes
from myntos.saft.errors import SaftWrongYearError
from myntos.saft.opening_import import (
    AccountToAdd,
    PartyToAdd,
    check_company,
    from_balances,
    target_account,
)
from myntos.saft.reader import SaftEntries
from myntos.saft.voucher_import import (
    DimensionToAdd,
    MonthCount,
    VoucherImport,
    account_map,
    plan,
    post,
    split_code,
)

ZERO = Decimal("0.00")
# A year ends at midnight in Norway.
_OSLO = ZoneInfo("Europe/Oslo")
_RATE = Decimal("0.000001")

#: The most files one move takes: SAF-T allows ten attachments to a delivery,
#: and ten years is twice what bokføringsloven asks anybody to keep.
MAX_FILES = 10

#: Findings the move resolves itself by adding what is missing, so the person is
#: shown what will be added instead of being told to add it.
_RESOLVED_BY_ADDING = frozenset({"kontoer_mangler", "parter_mangler", "dimensjoner_mangler"})

#: ``none``: the earliest file opens at zero (the books start in its year), so
#: there is no opening balance to post, and one standing for a later year is
#: only reversed.
OpeningAction = Literal["post", "replace", "keep", "none"]


@dataclass(frozen=True)
class SourceFile:
    """One uploaded file, read, with the name the person knows it by."""

    name: str
    entries: SaftEntries


@dataclass(frozen=True)
class YearFinding:
    """A finding, and the year it is about (None for the move as a whole)."""

    year: int | None
    finding: Finding


@dataclass(frozen=True)
class YearReport:
    year: int
    file_name: str
    software: str | None
    start: date
    end: date
    #: Whether the file runs to 31 December. Only the last year may not.
    complete: bool
    through: date
    #: Whether the year is new in Myntos: opened by this move.
    opened: bool
    vouchers: int
    lines: int
    first_date: date | None
    last_date: date | None
    already_imported: int
    later: int
    months: list[MonthCount]


@dataclass(frozen=True)
class OpeningReport:
    year: int
    action: OpeningAction
    #: The voucher number of the opening balance replaced, when one is.
    replaces: str | None
    replaces_year: int | None
    lines: int
    total: Decimal


@dataclass
class MoveReport:
    """What the move does, or did."""

    years: list[YearReport]
    opening: OpeningReport | None
    #: The company's first year in Myntos before the move, and after it.
    first_year_before: int | None
    first_year_after: int
    years_opened: list[int]
    accounts_to_add: list[AccountToAdd]
    parties_to_add: list[PartyToAdd]
    dimensions_to_add: list[DimensionToAdd]
    findings: list[YearFinding] = field(default_factory=list)

    @property
    def vouchers(self) -> int:
        return sum(year.vouchers for year in self.years)

    @property
    def blocking(self) -> list[YearFinding]:
        return [item for item in self.findings if item.finding.severity is Severity.FEIL]

    @property
    def postable(self) -> bool:
        opening = self.opening
        changes = self.vouchers > 0 or (
            opening is not None
            and (opening.action in ("post", "replace") or opening.replaces is not None)
        )
        return changes and not self.blocking


# --- The files ---------------------------------------------------------------------


def _day(value: date) -> str:
    return f"{value.day}.{value.month}.{value.year}"


def order_files(company: Company, files: Sequence[SourceFile]) -> dict[int, SourceFile]:
    """The files by year, after the checks that refuse the whole move.

    Refused outright, not reported: another company's file (nothing of it may
    be shown, CLAUDE.md rule 1), a file that is not one year from 1 January, two
    files for one year, a gap, and an unfinished year before the last.
    """
    if not files:
        raise SaftWrongYearError("ingen SAF-T-fil å lese")
    if len(files) > MAX_FILES:
        raise SaftWrongYearError(f"Myntos leser høyst {MAX_FILES} filer, én per år, på én gang")
    by_year: dict[int, SourceFile] = {}
    for item in files:
        balances = item.entries.balances
        check_company(company, balances.orgnr, balances.company_name)
        start, end = balances.start, balances.end
        if (start.month, start.day) != (1, 1) or end.year != start.year or end < start:
            raise SaftWrongYearError(
                f"«{item.name}» gjelder {_day(start)} til {_day(end)}. Hver fil må gjelde ett "
                "regnskapsår, fra 1. januar: ta ut én fil per år i det gamle programmet"
            )
        other = by_year.get(start.year)
        if other is not None:
            raise SaftWrongYearError(
                f"«{other.name}» og «{item.name}» gjelder begge {start.year}. Last opp én fil "
                "per år"
            )
        by_year[start.year] = item
    years = sorted(by_year)
    missing = [year for year in range(years[0], years[-1] + 1) if year not in by_year]
    if missing:
        raise SaftWrongYearError(
            "filen for " + ", ".join(str(year) for year in missing) + " mangler. Årene må komme "
            "etter hverandre, ellers vet ikke Myntos hva året etter startet med"
        )
    for year in years[:-1]:
        end = by_year[year].entries.balances.end
        if end != date(year, 12, 31):
            raise SaftWrongYearError(
                f"«{by_year[year].name}» slutter {_day(end)}. Bare det siste året kan være "
                f"uferdig: ta ut filen for hele {year}"
            )
    return dict(sorted(by_year.items()))


# --- Adding what is missing ----------------------------------------------------------


def _merge_accounts(groups: Sequence[Sequence[AccountToAdd]]) -> list[AccountToAdd]:
    merged: dict[str, AccountToAdd] = {}
    for group in groups:
        for add in group:
            known = merged.get(add.number)
            if known is None:
                merged[add.number] = add
                continue
            sources = tuple(dict.fromkeys(known.source_accounts + add.source_accounts))
            merged[add.number] = AccountToAdd(
                number=known.number,
                description=known.description,
                account_type=known.account_type,
                grouping_category=known.grouping_category,
                grouping_code=known.grouping_code,
                source_accounts=sources,
            )
    return sorted(merged.values(), key=lambda add: add.number)


def _party_key(add: PartyToAdd) -> tuple[PartyType, str]:
    if add.orgnr:
        return add.party_type, f"orgnr:{add.orgnr}"
    return add.party_type, "navn:" + "".join(ch for ch in add.name.lower() if ch.isalnum())


def _merge_parties(groups: Sequence[Sequence[PartyToAdd]]) -> list[PartyToAdd]:
    merged: dict[tuple[PartyType, str], PartyToAdd] = {}
    numbers: set[tuple[PartyType, str]] = set()
    for group in groups:
        for add in group:
            key = _party_key(add)
            if key in merged:
                continue
            wanted = add.party_id
            # Each year's proposal took the old program's number when it was
            # free in Myntos; two years could each have found the same number
            # free for two different parties.
            if wanted is not None and (add.party_type, wanted) in numbers:
                wanted = None
            if wanted is not None:
                numbers.add((add.party_type, wanted))
            merged[key] = PartyToAdd(
                party_type=add.party_type,
                name=add.name,
                orgnr=add.orgnr,
                party_id=wanted,
                address_line=add.address_line,
                postal_code=add.postal_code,
                city=add.city,
                source_id=add.source_id,
            )
    return sorted(merged.values(), key=lambda add: (add.party_type.value, add.name.lower()))


def _merge_dimensions(groups: Sequence[Sequence[DimensionToAdd]]) -> list[DimensionToAdd]:
    merged: dict[tuple[str, str], DimensionToAdd] = {}
    for group in groups:
        for add in group:
            merged.setdefault((add.code, add.value), add)
    return sorted(merged.values(), key=lambda add: (add.code, add.value.zfill(12)))


async def _add_accounts(
    db: AsyncSession,
    company: Company,
    to_add: Sequence[AccountToAdd],
    *,
    created: date,
    user_id: int | None,
) -> None:
    open_years = list(
        await db.scalars(
            select(FinancialYear.year).where(
                FinancialYear.company_id == company.id, FinancialYear.closed_at.is_(None)
            )
        )
    )
    for add in to_add:
        pair = (add.grouping_category, add.grouping_code)
        # The file's pair was checked against one year's list. A pair the list
        # dropped or added in another open year is not forced on that year: the
        # account takes Myntos's default there, as any new account does.
        usable = all(codes.is_choosable_grouping(year, *pair) for year in open_years)
        await kontoplan.create_account(
            db,
            company=company,
            number=add.number,
            description=add.description,
            account_type=add.account_type,
            grouping=pair if usable else None,
            created_by_user_id=user_id,
            today=created,
        )


async def _add_parties(db: AsyncSession, company: Company, to_add: Sequence[PartyToAdd]) -> None:
    for add in to_add:
        try:
            async with db.begin_nested():
                await parties.create_party(
                    db,
                    company=company,
                    party_type=add.party_type,
                    name=add.name,
                    orgnr=add.orgnr,
                    party_id=add.party_id,
                    address_line=add.address_line,
                    postal_code=add.postal_code,
                    city=add.city,
                )
        except DuplicatePartyError:
            # The old program's number was taken after all; the party gets the
            # next free one, as any new party does.
            await parties.create_party(
                db,
                company=company,
                party_type=add.party_type,
                name=add.name,
                orgnr=add.orgnr,
                address_line=add.address_line,
                postal_code=add.postal_code,
                city=add.city,
            )


async def _add_dimensions(
    db: AsyncSession, company: Company, to_add: Sequence[DimensionToAdd]
) -> None:
    made: dict[str, int] = {}
    for add in to_add:
        dimension_pk = add.dimension_id or made.get(add.code)
        if dimension_pk is None:
            existing = await db.scalar(
                select(DimensionType).where(
                    DimensionType.company_id == company.id, DimensionType.code == add.code
                )
            )
            dimension = existing or await dimensions.create_dimension(
                db, company=company, code=add.code, description=add.dimension_description
            )
            dimension_pk = dimension.id
            made[add.code] = dimension_pk
        await dimensions.create_value(
            db,
            company=company,
            dimension_pk=dimension_pk,
            value=add.value,
            description=add.description,
        )


# --- The move ------------------------------------------------------------------------


def observed_rates(files: Sequence[SaftEntries]) -> dict[str, set[Decimal]]:
    """Every rate each standard VAT code was used at in ``files``, as fractions.

    What lets the seeded rates be carried back to the years these files cover
    (:func:`myntos.domain.ledger.service.open_earlier_years`): a code whose
    lines all had today's rate proves today's rate for those years.
    """
    seen: dict[str, set[Decimal]] = {}
    for entries in files:
        file_codes = entries.balances.tax_codes or {}
        for transaction in entries.transactions:
            for line in transaction.lines:
                for tax in line.taxes:
                    stripped, _ = split_code(tax.code)
                    standard = file_codes.get(tax.code) or stripped
                    seen.setdefault(standard, set()).add((tax.percentage / 100).quantize(_RATE))
    return seen


async def _has_books_before(db: AsyncSession, company: Company, year: int) -> bool:
    found = await db.scalar(
        select(Transaction.id)
        .join(Journal, Journal.id == Transaction.journal_id)
        .where(
            Journal.company_id == company.id,
            Transaction.posted_at.is_not(None),
            Transaction.gl_posting_date < date(year, 1, 1),
        )
        .limit(1)
    )
    return found is not None


def _moved(
    lines: Sequence[opening.OpeningLineInput], moves: dict[str, str]
) -> list[opening.OpeningLineInput]:
    """The opening lines with the accounts the replaced balance had moved, moved again."""
    if not moves:
        return list(lines)
    moved: list[opening.OpeningLineInput] = []
    for line in lines:
        token = (line.description or "").split(" ", 1)[0]
        target = moves.get(token)
        if line.party_pk is None and target is not None and target_account(token) != target:
            line = opening.OpeningLineInput(
                account_number=target,
                debit=line.debit,
                credit=line.credit,
                party_pk=line.party_pk,
                description=line.description,
            )
        moved.append(line)
    return moved


async def apply(
    db: AsyncSession,
    *,
    company: Company,
    files: Sequence[SourceFile],
    til: date | None = None,
    created_by_user_id: int | None = None,
    may_chart: bool = True,
    today: date | None = None,
) -> MoveReport:
    """Bring every year in ``files`` over, in ``db``, and say what was done.

    Nothing is committed: the caller commits when :attr:`MoveReport.postable`,
    and rolls back otherwise or to preview. ``til`` stops the last year early.
    ``may_chart`` is whether the person may add accounts to the kontoplan; when
    they may not and the files need some, that is a ``feil``.
    """
    by_year = order_files(company, files)
    years = list(by_year)
    today = today or utc_now().astimezone(_OSLO).date()
    first_file, last_file = years[0], years[-1]
    findings: list[YearFinding] = []

    def note(finding: Finding, year: int | None = None) -> None:
        findings.append(YearFinding(year, finding))

    first_row = await opening.first_year(db, company)
    first_before = first_row.year if first_row is not None else None
    state = await opening.state(db, company=company)
    standing = state.standing

    # --- The years ------------------------------------------------------------------
    closed = set(
        await db.scalars(
            select(FinancialYear.year).where(
                FinancialYear.company_id == company.id, FinancialYear.closed_at.is_not(None)
            )
        )
    )
    if first_before is not None and first_file < first_before and closed:
        raise SaftWrongYearError(
            f"regnskapsåret {min(closed)} er avsluttet i Myntos. År før {first_before} kan bare "
            f"hentes inn mens årene etter er åpne, fordi de endrer saldoene de startet med: "
            f"gjenåpne {max(closed)} under Årsavslutning først"
        )
    if (
        first_before is not None
        and first_file > first_before
        and standing is None
        and not await _has_books_before(db, company, first_file)
    ):
        raise SaftWrongYearError(
            f"regnskapet i Myntos starter i {first_before}, men filene starter i {first_file}. "
            f"Last opp filene fra og med {first_before}, så kommer inngående balanse fra den "
            "første av dem"
        )
    opened = (
        await ledger_service.open_earlier_years(
            db,
            company,
            first_file,
            observed_rates=observed_rates(
                [by_year[year].entries for year in years if year < first_before]
            ),
        )
        if first_before is not None
        else []
    )
    for year in years:
        if year not in closed:
            opened_now = await db.scalar(
                select(FinancialYear.id).where(
                    FinancialYear.company_id == company.id, FinancialYear.year == year
                )
            )
            if opened_now is None:
                await ledger_service.ensure_financial_year(db, company, year)
                opened.append(year)
    first_after = min(first_file, first_before) if first_before is not None else first_file

    # --- The opening balance ----------------------------------------------------------
    action: OpeningAction | None = None
    first_balances = by_year[first_file].entries.balances
    opens_at_zero = not any(a.opening for a in first_balances.accounts) and not any(
        p.opening for p in first_balances.parties
    )
    if first_file == first_after:
        if standing is not None and standing.transaction_date.year == first_file:
            action = "keep"
        elif opens_at_zero:
            action = "none"
        elif standing is None:
            action = "post"
        else:
            action = "replace"
    moves = (
        account_map(standing, by_year[first_file].entries.balances.accounts)
        if standing is not None and action == "replace"
        else {}
    )

    # --- What the years need, before anything is added --------------------------------
    opening_needs = (
        await from_balances(db, company=company, found=by_year[first_file].entries.balances)
        if action in ("post", "replace")
        else None
    )
    needs: list[VoucherImport] = []
    for year in years:
        if year in closed:
            continue
        needs.append(
            await plan(
                db,
                company=company,
                found=by_year[year].entries,
                til=til if year == last_file else None,
                year=year,
                require_opening=False,
            )
        )
    accounts_to_add = _merge_accounts(
        ([opening_needs.accounts_to_add] if opening_needs else [])
        + [need.accounts_to_add for need in needs]
    )
    parties_to_add = _merge_parties(
        ([opening_needs.parties_to_add] if opening_needs else [])
        + [need.parties_to_add for need in needs]
    )
    dimensions_to_add = _merge_dimensions([need.dimensions_to_add for need in needs])

    # --- Adding it ---------------------------------------------------------------------
    created = date(first_after, 1, 1)
    if accounts_to_add and not may_chart:
        note(
            feil(
                "kontoplan_krever_administrator",
                f"Filene fører på {len(accounts_to_add)} kontoer som ikke er i kontoplanen, og "
                "bare en administrator eller eier kan legge til kontoer. Be en av dem om å føre "
                "regnskapet fra filene.",
            )
        )
    else:
        await _add_accounts(
            db, company, accounts_to_add, created=created, user_id=created_by_user_id
        )
    await _add_parties(db, company, parties_to_add)
    await _add_dimensions(db, company, dimensions_to_add)

    # --- Posting the opening balance -----------------------------------------------------
    opening_report: OpeningReport | None = None
    if action == "keep" and standing is not None:
        opening_report = OpeningReport(
            year=first_file,
            action="keep",
            replaces=None,
            replaces_year=None,
            lines=len(standing.lines),
            total=standing.total,
        )
    elif action in ("post", "replace"):
        read = await from_balances(db, company=company, found=by_year[first_file].entries.balances)
        lines = _moved(read.lines, moves)
        for finding in read.review.findings:
            # The review's own refusals come back from the posting below, for
            # the lines as moved; only the file's notes are read from here.
            if finding.severity is Severity.MERKNAD:
                note(finding, first_file)
        try:
            await opening.post_opening(
                db,
                company=company,
                lines=lines,
                correct=action == "replace",
                created_by_user_id=created_by_user_id,
            )
        except InvalidOpeningBalanceError as error:
            for finding in error.findings:
                if finding.code not in _RESOLVED_BY_ADDING or not may_chart:
                    note(finding, first_file)
        except OpeningBalanceNotAllowedError as error:
            note(feil("aar_ikke_tillatt", f"{str(error)[:1].upper()}{str(error)[1:]}."), first_file)
        opening_report = OpeningReport(
            year=first_file,
            action=action,
            replaces=standing.voucher_no if standing is not None else None,
            replaces_year=standing.transaction_date.year if standing is not None else None,
            lines=len(lines),
            total=sum((line.debit for line in lines), ZERO),
        )
        if action == "replace" and standing is not None:
            moved_text = (
                " Kontoene du hadde flyttet i den, er flyttet i den nye også ("
                + ", ".join(f"{source} på {target}" for source, target in sorted(moves.items()))
                + ")."
                if moves
                else ""
            )
            note(
                merknad(
                    "inngaende_balanse_flyttes",
                    f"Inngående balanse for {standing.transaction_date.year} (bilag "
                    f"{standing.voucher_no}) reverseres, og en ny føres per 1. januar "
                    f"{first_file} fra filen for {first_file}. "
                    f"{standing.transaction_date.year} åpner da med det "
                    f"{standing.transaction_date.year - 1} sluttet med i Myntos." + moved_text,
                ),
                first_file,
            )
    elif action == "none":
        replaced = standing if standing is not None else None
        if replaced is not None:
            await opening.reverse_opening(
                db, company=company, created_by_user_id=created_by_user_id
            )
        opening_report = OpeningReport(
            year=first_file,
            action="none",
            replaces=replaced.voucher_no if replaced is not None else None,
            replaces_year=replaced.transaction_date.year if replaced is not None else None,
            lines=0,
            total=ZERO,
        )
        note(
            merknad(
                "ingen_inngaende_saldoer",
                f"Filen for {first_file} har ingen saldoer per 1. januar: regnskapet i det gamle "
                f"programmet startet i {first_file}, og det starter på null her også. Alt står i "
                "bilagene."
                + (
                    f" Inngående balanse for {replaced.transaction_date.year} (bilag "
                    f"{replaced.voucher_no}) reverseres, fordi {replaced.transaction_date.year} nå "
                    f"åpner med det {replaced.transaction_date.year - 1} sluttet med."
                    if replaced is not None
                    else ""
                ),
            ),
            first_file,
        )
    elif first_before is not None and first_file > first_before:
        note(
            merknad(
                "aapning_fra_myntos",
                f"{first_file} åpner med det {first_file - 1} sluttet med i Myntos. Saldoene per "
                f"1. januar {first_file} i filen brukes ikke.",
            ),
            first_file,
        )
    if opened and first_before is not None and first_after < first_before:
        note(
            merknad(
                "aar_lagt_til",
                f"Regnskapet i Myntos starter i {first_after}, ikke i {first_before}: "
                + ", ".join(str(year) for year in sorted(opened) if year < first_before)
                + " legges til foran.",
            )
        )

    # --- Posting the years ---------------------------------------------------------------
    reports: list[YearReport] = []
    vat_filed = False
    for year in years:
        source = by_year[year]
        balances = source.entries.balances
        if year in closed:
            note(
                feil(
                    "aaret_er_avsluttet",
                    f"Regnskapsåret {year} er avsluttet i Myntos, og bilag kan bare føres i et "
                    "åpent år. Gjenåpne det under Årsavslutning, eller ta filen for året ut av "
                    "opplastingen.",
                ),
                year,
            )
            continue
        planned = await plan(
            db,
            company=company,
            found=source.entries,
            til=til if year == last_file else None,
            year=year,
            require_opening=False,
        )
        for finding in planned.findings:
            if finding.code in _RESOLVED_BY_ADDING and may_chart:
                continue
            if finding.code == "mva_meldinger_sendt":
                vat_filed = True
                continue
            note(finding, year)
        if planned.postable:
            await post(
                db,
                company=company,
                found=source.entries,
                til=planned.through,
                created_by_user_id=created_by_user_id,
                year=year,
                planned=planned,
            )
        reports.append(
            YearReport(
                year=year,
                file_name=source.name,
                software=balances.software,
                start=balances.start,
                end=balances.end,
                # Tripletex writes the whole year as the selection whenever the
                # file was taken; a year still running is not complete.
                complete=balances.end == date(year, 12, 31) and date(year, 12, 31) < today,
                through=planned.through,
                opened=year in opened,
                vouchers=len(planned.vouchers),
                lines=planned.line_count,
                first_date=planned.first_date,
                last_date=planned.last_date,
                already_imported=planned.already_imported,
                later=planned.later,
                months=planned.months,
            )
        )
    if vat_filed:
        note(
            merknad(
                "mva_meldinger_sendt",
                "Bilagene har mva, og Mva-siden i Myntos regner dem med i terminene sine. Har "
                "det gamle programmet sendt mva-meldingen for en termin, skal den ikke sendes på "
                "nytt fra Myntos: merk den «Sendt fra et annet program» på Mva-siden, der Myntos "
                "foreslår terminene det gamle programmet gjorde opp.",
            )
        )
    previous = [report.year for report in reports if report.complete and report.year < last_file]
    if previous:
        note(
            merknad(
                "tidligere_aar_aapne",
                "Årene "
                + ", ".join(str(year) for year in previous)
                + " er ferdige i det gamle programmet, men åpne i Myntos. Avslutt dem under "
                "Årsavslutning, eldste først: skatten og disponeringen det gamle programmet "
                "førte, blir stående, og Myntos fører bare det som mangler.",
            )
        )

    return MoveReport(
        years=reports,
        opening=opening_report,
        first_year_before=first_before,
        first_year_after=first_after,
        years_opened=sorted(opened),
        accounts_to_add=accounts_to_add,
        parties_to_add=parties_to_add,
        dimensions_to_add=dimensions_to_add,
        findings=findings,
    )


async def imported_counts(db: AsyncSession, company: Company) -> dict[str, int]:
    """How many vouchers stand in the journals a move writes to, for the page's summary."""
    rows = await db.execute(
        select(Journal.journal_id, Transaction.id)
        .join(Transaction, Transaction.journal_id == Journal.id)
        .where(
            Journal.company_id == company.id,
            Journal.journal_id.in_((IMPORT_JOURNAL_ID, OPENING_JOURNAL_ID)),
            Transaction.posted_at.is_not(None),
        )
    )
    counts: dict[str, int] = {}
    for journal_id, _ in rows:
        counts[journal_id] = counts.get(journal_id, 0) + 1
    return counts


__all__ = [
    "MAX_FILES",
    "MoveReport",
    "OpeningReport",
    "SourceFile",
    "YearFinding",
    "YearReport",
    "apply",
    "blocking",
    "order_files",
]
