"""The farm's year-end count: livestock, the farm's own feed and grain, bought stock.

A farm's balance sheet carries three things nobody posts during the year: the
herd (1450), the hay, silage and grain grown for the farm's own animals (1490),
and the feed, fertiliser and seed bought and not yet used (1400). What they are
worth on 31 December is decided by counting them and pricing the count by
Skattedirektoratet's takseringsregler (:mod:`.takseringsregler`), and the
change since the books last said is the year's beholdningsendring: a cost when
the herd shrank, a reduction of cost when it grew.

**One list, one voucher.** The person types (or an agent sends) the count, line
by line: how many of what, at which value. The review prices each line, and
compares the counted value per account with what the books say on 31 December.
Posting stores the list (:class:`~.landbruk_models.FarmCount`) and **one**
voucher in its own journal (``TELLING``) that brings each balance account to the
counted value against its change account:

=============== ========= ======= ==========================================
Kind            Balance   Change  Næringsspesifikasjon (``SpesifikasjonAv…``)
=============== ========= ======= ==========================================
``buskap``       1450      4690    ``buskap``
``egenprodusert`` 1490     4290    ``selvprodusertVareBenyttetIEgenProduksjon``
``innkjopt``     1400      4090    ``raavareOgInnkjoeptHalvfabrikata``
=============== ========= ======= ==========================================

A line may name another account in its kind's range (NIBIO keeps livestock on
1451-1458 by species, and a farm whose opening balance came from Tripletex has
it there). An account in a range that holds a balance but has no line is a
``feil``: otherwise the pilot's opening balance on 1451 would stand beside a
count on 1450 and the herd would be on the balance sheet twice. Counting zero
of something is a line, and writes it down.

**How the values are decided** (FOR-2025-12-01-2404 kapittel 5 § 5-2, read on
Lovdata 2026-10-15):

- *Adults already adult at the start of the year and still there* are valued at
  the **average of all adult animals of the species in the opening count**,
  rounded to the nearest 10 kroner. Computed here (``snitt``), never typed:
  it is the rule that keeps the herd's value from moving with each year's
  rates. Adults are storfe over two years, avlsgriser over six months, geiter
  over nine months, sauer over a year.
- *New animals of the farm's own breeding*, and animals bought to renew or
  expand the herd, at the year's **enhetspriser** (``normalverdi``); the
  younger ones at the regulation's percentages, which the rates already are.
- *Fôringsdyr bought for sale*, chicks for fattening and bought stock at
  **kostpris**: what they cost, plus feed to the turn of the year for animals.
- Grain, potatoes and feed at the regulation's **tilvirkningsverdi** per kilo
  or FEm, «dersom ikke noe annet kan dokumenteres»; a documented value is
  ``verdsatt``, with the reason in the description.

A year Skattedirektoratet has not published yet (2026 until December) has no
enhetspriser, and a ``normalverdi`` line in it is refused rather than priced at
last year's: the values move every year. The average and kostpris need no
rates, so most of a count can be prepared before December.

**The opening rows are last year's closing rows, unchanged.** «Den årlige
sluttbalansen … blir uten endringer å regne som åpningsbalanse» (§ 5-2, last
paragraph). So when the year before has a count in Myntos, its closing rows are
copied as this year's opening and cannot be typed over; a wrong opening is
corrected by correcting last year's count. In the first year in Myntos the
person types them, from the previous program's list. The opening rows move no
money; they decide the average, and the næringsspesifikasjon wants them.

**Corrections and withdrawal.** A count is never edited. Correcting reverses
the standing voucher and posts the new list and voucher in one request
(``korriger``), as the opening balance does. Withdrawing reverses the voucher
and stores an empty list in its place, so the history keeps every list that
ever stood. The ordinary reversal in the register refuses the ``TELLING``
journal (:func:`myntos.domain.ledger.service.reverse_cash_flow`): reversed from
there, the voucher would be gone while its list still claimed to stand.

**For the næringsspesifikasjon.** :func:`year_count` is the one function the
ENK's næringsspesifikasjon reads: the standing count of a year with its lines
and the opening and closing total per kind, under the names the XSD uses.
"""

from __future__ import annotations

import enum
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import and_, func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from myntos.domain.arsoppgjor import takseringsregler as regler
from myntos.domain.arsoppgjor.errors import (
    CountExistsError,
    FarmYearNotOpenError,
    InvalidCountError,
    NoCountError,
)
from myntos.domain.arsoppgjor.findings import Finding, blocking, feil, merknad
from myntos.domain.arsoppgjor.landbruk_models import FarmCount, FarmCountLine
from myntos.domain.arsoppgjor.takseringsregler import Kind, Valuation
from myntos.domain.ledger import kontoplan, landbruk, posting
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.chart import (
    COUNT_JOURNAL_DESCRIPTION,
    COUNT_JOURNAL_ID,
    COUNT_JOURNAL_TYPE,
    KNOWN_ACCOUNTS,
)
from myntos.domain.ledger.errors import SegmentNotEnabledError
from myntos.domain.ledger.models import (
    Account,
    AccountType,
    Company,
    FinancialYear,
    Journal,
    Transaction,
    TransactionLine,
)

ZERO = Decimal("0.00")
_CENT = Decimal("0.01")
_QUANTITY_STEP = Decimal("0.001")
_UNIT_STEP = Decimal("0.0001")

# SAF-T TransactionType on the count's voucher and its reversals.
COUNT_VOUCHER_TYPE = "beholdningsendring"


class Side(enum.StrEnum):
    #: 1 January: last year's closing rows, or typed in the first year.
    INNGAENDE = "inngaende"
    #: 31 December: what was counted, and what posts.
    UTGAENDE = "utgaende"


class OpeningSource(enum.StrEnum):
    FORRIGE_TELLING = "forrige_telling"
    OPPGITT = "oppgitt"
    #: Stored by a withdrawal: an empty list that says no count stands.
    TRUKKET = "trukket"


@dataclass(frozen=True)
class KindAccounts:
    balance: str
    change: str
    #: The næringsspesifikasjon's element in ``SpesifikasjonAvSkattemessigVarelager``.
    naeringsspesifikasjon: str
    label: str


KIND_ACCOUNTS: dict[Kind, KindAccounts] = {
    Kind.BUSKAP: KindAccounts("1450", "4690", "buskap", "Buskap"),
    Kind.EGENPRODUSERT: KindAccounts(
        "1490",
        "4290",
        "selvprodusertVareBenyttetIEgenProduksjon",
        "Egenproduserte varer til eget bruk",
    ),
    Kind.INNKJOPT: KindAccounts(
        "1400", "4090", "raavareOgInnkjoeptHalvfabrikata", "Innkjøpt fôr, gjødsel og såvarer"
    ),
}


def kind_of_account(number: str) -> Kind | None:
    """The kind an account in NIBIO's inventory ranges holds, or None outside them.

    NIBIO: 1400-1439 innkjøpte varer, 1440-1449 egenproduserte, 1451-1458
    buskap by species. Myntos's own 1490 is the farm's own feed (NIBIO's 1490
    is «Biologiske eiendeler»; docs/landbruk.md has the conflict).
    """
    if len(number) != 4 or not number.isdigit():
        return None
    n = int(number)
    if 1450 <= n <= 1459:
        return Kind.BUSKAP
    if 1400 <= n <= 1439:
        return Kind.INNKJOPT
    if 1440 <= n <= 1449 or n == 1490:
        return Kind.EGENPRODUSERT
    return None


# --- Shapes ------------------------------------------------------------------------


@dataclass(frozen=True)
class CountLineInput:
    """One row as a person typed it."""

    side: str
    category: str
    quantity: Decimal
    description: str = ""
    #: Only for ``annet``: the category says it otherwise.
    kind: str | None = None
    unit: str | None = None
    #: None: the category's own rule.
    valuation: str | None = None
    #: Required for kostpris and verdsatt, and for every opening row typed.
    unit_value: Decimal | None = None
    account_number: str | None = None


@dataclass(frozen=True)
class CountLine:
    side: Side
    kind: Kind
    category: str
    label: str
    description: str
    quantity: Decimal
    unit: str
    valuation: str
    unit_value: Decimal
    value: Decimal
    account_number: str
    nibio: str | None = None


@dataclass(frozen=True)
class AccountChange:
    account_number: str
    account_description: str | None
    kind: Kind
    #: What the books say on 31 December without this year's count.
    booked: Decimal
    counted: Decimal
    #: The opening rows' total, and the books' on 1 January.
    opening_counted: Decimal
    opening_booked: Decimal

    @property
    def change(self) -> Decimal:
        return self.counted - self.booked


@dataclass(frozen=True)
class PlannedCountLine:
    account_number: str
    description: str
    debit: Decimal = ZERO
    credit: Decimal = ZERO


@dataclass(frozen=True)
class KindTotal:
    kind: Kind
    naeringsspesifikasjon: str
    opening: Decimal
    closing: Decimal


@dataclass(frozen=True)
class CountReview:
    year: int
    counted_on: date | None
    opening_source: OpeningSource
    opening: list[CountLine]
    closing: list[CountLine]
    accounts: list[AccountChange]
    voucher_lines: list[PlannedCountLine]
    totals: list[KindTotal]
    findings: list[Finding]

    @property
    def postable(self) -> bool:
        return not blocking(self.findings)


@dataclass(frozen=True)
class CountView:
    """A stored list, as it was posted."""

    id: int
    year: int
    counted_on: date
    opening_source: str
    corrects_id: int | None
    voucher_id: int | None
    voucher_no: str | None
    created_by_user_id: int | None
    created_at: datetime
    opening: list[CountLine]
    closing: list[CountLine]
    totals: list[KindTotal]

    @property
    def withdrawn(self) -> bool:
        return self.opening_source == OpeningSource.TRUKKET


@dataclass(frozen=True)
class BookedAccount:
    account_number: str
    account_description: str
    kind: Kind
    balance: Decimal


@dataclass(frozen=True)
class CountState:
    year: int
    counted_on: date | None
    #: Why nothing may be posted for the year now, or None.
    refusal: str | None
    rates: regler.YearRates | None
    #: The categories offered: the year's, else the latest published year's,
    #: whose normalverdi lines the review then refuses.
    catalogue: regler.YearRates
    opening_source: OpeningSource
    #: Last year's closing rows when there is a count to copy them from.
    opening: list[CountLine]
    #: Accounts in the ranges with a balance on 31 December before the count:
    #: each needs a line, so the page proposes one per account.
    booked: list[BookedAccount]
    standing: CountView | None
    history: list[CountView]


# --- Reading ------------------------------------------------------------------------


def _require_landbruk(company: Company) -> None:
    if not landbruk.is_enabled(company):
        raise SegmentNotEnabledError(
            "telling av buskap og varelager hører til landbrukstillegget, og det er ikke slått på "
            "for dette selskapet. Slå det på under Kontoplan"
        )


def catalogue(year: int) -> regler.YearRates:
    """The year's categories, or the nearest published year's when the year has none."""
    found = regler.for_year(year)
    if found is not None:
        return found
    years = regler.known_years()
    earlier = [y for y in years if y < year]
    chosen = earlier[-1] if earlier else years[0]
    return regler.RATES[chosen]


async def _financial_year(db: AsyncSession, company: Company, year: int) -> FinancialYear | None:
    found: FinancialYear | None = await db.scalar(
        select(FinancialYear).where(
            FinancialYear.company_id == company.id, FinancialYear.year == year
        )
    )
    return found


def year_refusal(fiscal: FinancialYear | None, year: int) -> str | None:
    if fiscal is None:
        return (
            f"selskapet har ikke regnskapsåret {year} i Myntos. En telling føres i et år "
            "regnskapet føres her"
        )
    if fiscal.closed_at is not None:
        return f"regnskapsåret {year} er avsluttet. Gjenåpne året først hvis tellingen må rettes"
    return None


async def booked_balances(
    db: AsyncSession, company: Company, *, up_to: int, without_count_of: int | None = None
) -> dict[str, Decimal]:
    """Debit minus credit per account, every year up to ``up_to``.

    ``without_count_of`` leaves that year's ``TELLING`` vouchers out: the count
    is compared with the books as they were before it, so a correction's
    review shows the change from the books and not from the count it replaces.
    """
    query = (
        select(
            Account.account_number,
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .join(TransactionLine, TransactionLine.account_id == Account.id)
        .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
        .join(Journal, Journal.id == Transaction.journal_id)
        .where(
            Journal.company_id == company.id,
            Account.company_id == company.id,
            Transaction.posted_at.is_not(None),
            Transaction.financial_year <= up_to,
        )
        .group_by(Account.account_number)
    )
    if without_count_of is not None:
        query = query.where(
            not_(
                and_(
                    Journal.journal_id == COUNT_JOURNAL_ID,
                    Transaction.financial_year == without_count_of,
                )
            )
        )
    return {
        number: Decimal(str(debit)) - Decimal(str(credit))
        for number, debit, credit in await db.execute(query)
    }


def _line_of(row: FarmCountLine, year: int) -> CountLine:
    entry = catalogue(year).category(row.category)
    return CountLine(
        side=Side(row.side),
        kind=Kind(row.kind),
        category=row.category,
        label=entry.label if entry is not None else row.description,
        description=row.description,
        quantity=row.quantity,
        unit=row.unit,
        valuation=row.valuation,
        unit_value=row.unit_value,
        value=row.value,
        account_number=row.account_number,
        nibio=entry.nibio if entry is not None else None,
    )


def _totals(opening: Sequence[CountLine], closing: Sequence[CountLine]) -> list[KindTotal]:
    return [
        KindTotal(
            kind=kind,
            naeringsspesifikasjon=accounts.naeringsspesifikasjon,
            opening=sum((li.value for li in opening if li.kind is kind), ZERO),
            closing=sum((li.value for li in closing if li.kind is kind), ZERO),
        )
        for kind, accounts in KIND_ACCOUNTS.items()
    ]


async def _lists(db: AsyncSession, company: Company, year: int) -> list[FarmCount]:
    return list(
        await db.scalars(
            select(FarmCount)
            .where(FarmCount.company_id == company.id, FarmCount.financial_year == year)
            .options(selectinload(FarmCount.lines))
            .order_by(FarmCount.id)
        )
    )


async def _voucher_numbers(db: AsyncSession, pks: Sequence[int]) -> dict[int, str]:
    if not pks:
        return {}
    rows = await db.execute(
        select(Transaction.id, Transaction.transaction_id).where(Transaction.id.in_(pks))
    )
    return dict(rows.tuples().all())


def _view(row: FarmCount, numbers: dict[int, str]) -> CountView:
    lines = [_line_of(line, row.financial_year) for line in row.lines]
    opening = [li for li in lines if li.side is Side.INNGAENDE]
    closing = [li for li in lines if li.side is Side.UTGAENDE]
    return CountView(
        id=row.id,
        year=row.financial_year,
        counted_on=row.counted_on,
        opening_source=row.opening_source,
        corrects_id=row.corrects_id,
        voucher_id=row.transaction_pk,
        voucher_no=numbers.get(row.transaction_pk) if row.transaction_pk else None,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        opening=opening,
        closing=closing,
        totals=_totals(opening, closing),
    )


async def _standing_row(db: AsyncSession, company: Company, year: int) -> FarmCount | None:
    """The newest list for the year, unless it is a withdrawal."""
    rows = await _lists(db, company, year)
    if not rows or rows[-1].opening_source == OpeningSource.TRUKKET:
        return None
    return rows[-1]


async def year_count(db: AsyncSession, *, company: Company, year: int) -> CountView | None:
    """The count that stands for ``year``, or None: what the næringsspesifikasjon reads.

    ``totals`` carries the opening and closing value per kind under the
    element names of ``SpesifikasjonAvSkattemessigVarelager`` (``buskap``,
    ``selvprodusertVareBenyttetIEgenProduksjon``,
    ``raavareOgInnkjoeptHalvfabrikata``), and ``opening``/``closing`` the heads
    and kilos per category for a livestock card. Reads nothing but the stored
    list, so it is the count as it was posted, at the values it was posted with.
    """
    row = await _standing_row(db, company, year)
    if row is None:
        return None
    numbers = await _voucher_numbers(db, [row.transaction_pk] if row.transaction_pk else [])
    return _view(row, numbers)


async def state(db: AsyncSession, *, company: Company, year: int) -> CountState:
    """What the page needs before anybody types: the year, the rates, last year, what stands."""
    _require_landbruk(company)
    fiscal = await _financial_year(db, company, year)
    previous = await year_count(db, company=company, year=year - 1)
    rows = await _lists(db, company, year)
    numbers = await _voucher_numbers(db, [r.transaction_pk for r in rows if r.transaction_pk])
    history = [_view(row, numbers) for row in rows]
    standing = history[-1] if history and not history[-1].withdrawn else None
    balances = await booked_balances(db, company, up_to=year, without_count_of=year)
    descriptions = {
        a.account_number: a.description
        for a in await db.scalars(select(Account).where(Account.company_id == company.id))
    }
    booked = [
        BookedAccount(number, descriptions.get(number, ""), kind, balance)
        for number, balance in sorted(balances.items())
        if balance != ZERO and (kind := kind_of_account(number)) is not None
    ]
    return CountState(
        year=year,
        counted_on=fiscal.end_date if fiscal is not None else None,
        refusal=year_refusal(fiscal, year),
        rates=regler.for_year(year),
        catalogue=catalogue(year),
        opening_source=(
            OpeningSource.FORRIGE_TELLING if previous is not None else OpeningSource.OPPGITT
        ),
        opening=([_as_opening(li) for li in previous.closing] if previous is not None else []),
        booked=booked,
        standing=standing,
        history=history,
    )


def _as_opening(line: CountLine) -> CountLine:
    return CountLine(
        side=Side.INNGAENDE,
        kind=line.kind,
        category=line.category,
        label=line.label,
        description=line.description,
        quantity=line.quantity,
        unit=line.unit,
        valuation=line.valuation,
        unit_value=line.unit_value,
        value=line.value,
        account_number=line.account_number,
        nibio=line.nibio,
    )


# --- The review -------------------------------------------------------------------


def _decimal_problem(value: Decimal, step: Decimal, what: str) -> str | None:
    if value < 0:
        return f"{what} kan ikke være negativ"
    if value != value.quantize(step):
        return f"{what} har for mange desimaler"
    return None


@dataclass
class _Resolver:
    year: int
    rates: regler.YearRates | None
    known: regler.YearRates
    accounts: dict[str, Account]
    findings: list[Finding]

    def account(self, where: str, kind: Kind, chosen: str | None) -> str:
        number = (chosen or "").strip() or KIND_ACCOUNTS[kind].balance
        if kind_of_account(number) is not kind:
            self.findings.append(
                feil(
                    "konto_utenfor_omraade",
                    f"{where}: konto {number} hører ikke til {KIND_ACCOUNTS[kind].label.lower()}. "
                    "Buskap føres på 1450-1459, innkjøpte varer på 1400-1439 og egenproduserte "
                    "varer på 1440-1449 eller 1490.",
                )
            )
            return number
        account = self.accounts.get(number)
        if account is None and number not in KNOWN_ACCOUNTS:
            self.findings.append(
                feil(
                    "ukjent_konto",
                    f"{where}: konto {number} finnes ikke i kontoplanen. Legg den til under "
                    "Kontoplan først.",
                )
            )
        elif account is not None and account.account_type is not AccountType.EIENDEL:
            self.findings.append(
                feil("ikke_eiendelskonto", f"{where}: konto {number} er ikke en eiendelskonto.")
            )
        return number

    def kind(self, where: str, line: CountLineInput, entry: regler.CountCategory | None) -> Kind:
        if entry is not None:
            return entry.kind
        try:
            return Kind(line.kind or "")
        except ValueError:
            self.findings.append(
                feil(
                    "mangler_type",
                    f"{where}: «Annet» må si om det er buskap, innkjøpt eller egenprodusert.",
                )
            )
            return Kind.BUSKAP


def _resolve_opening(resolver: _Resolver, lines: Sequence[CountLineInput]) -> list[CountLine]:
    """The opening rows typed in the first year: stated values, from the old list."""
    resolved: list[CountLine] = []
    for number, line in enumerate(lines, start=1):
        where = f"Inngående linje {number}"
        entry = resolver.known.category(line.category) if line.category != regler.ANNET else None
        if line.category != regler.ANNET and entry is None:
            resolver.findings.append(
                feil(
                    "ukjent_kategori",
                    f"{where}: «{line.category}» er ikke en kategori Myntos kjenner.",
                )
            )
            continue
        kind = resolver.kind(where, line, entry)
        problem = _decimal_problem(line.quantity, _QUANTITY_STEP, "antallet")
        if problem is not None:
            resolver.findings.append(feil("ugyldig_antall", f"{where}: {problem}."))
            continue
        if line.unit_value is None:
            resolver.findings.append(
                feil(
                    "mangler_verdi",
                    f"{where}: inngående verdi må oppgis, slik den sto i fjorårets utgående "
                    "balanse.",
                )
            )
            continue
        problem = _decimal_problem(line.unit_value, _UNIT_STEP, "enhetsverdien")
        if problem is not None:
            resolver.findings.append(feil("ugyldig_verdi", f"{where}: {problem}."))
            continue
        resolved.append(
            CountLine(
                side=Side.INNGAENDE,
                kind=kind,
                category=line.category,
                label=entry.label if entry is not None else (line.description or "Annet"),
                description=line.description.strip(),
                quantity=line.quantity,
                unit=(line.unit or (entry.unit if entry is not None else "stk")).strip()[:16],
                valuation=line.valuation or Valuation.VERDSATT.value,
                unit_value=line.unit_value,
                value=posting.quantize_money(line.quantity * line.unit_value),
                account_number=resolver.account(where, kind, line.account_number),
                nibio=entry.nibio if entry is not None else None,
            )
        )
    return resolved


def _averages(opening: Sequence[CountLine], known: regler.YearRates) -> dict[str, Decimal]:
    """The average value of the adult animals per species in the opening rows (§ 5-2)."""
    value: dict[str, Decimal] = defaultdict(lambda: ZERO)
    heads: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for line in opening:
        entry = known.category(line.category)
        if entry is None or entry.species is None or not entry.adult:
            continue
        value[entry.species] += line.value
        heads[entry.species] += line.quantity
    return {
        species: regler.round_average(value[species] / heads[species])
        for species in value
        if heads[species] > 0
    }


def _resolve_closing(
    resolver: _Resolver, lines: Sequence[CountLineInput], averages: dict[str, Decimal]
) -> list[CountLine]:
    resolved: list[CountLine] = []
    for number, line in enumerate(lines, start=1):
        where = f"Linje {number}"
        entry = resolver.known.category(line.category) if line.category != regler.ANNET else None
        if line.category != regler.ANNET and entry is None:
            resolver.findings.append(
                feil(
                    "ukjent_kategori",
                    f"{where}: «{line.category}» er ikke en kategori i takseringsreglene.",
                )
            )
            continue
        kind = resolver.kind(where, line, entry)
        problem = _decimal_problem(line.quantity, _QUANTITY_STEP, "antallet")
        if problem is not None:
            resolver.findings.append(feil("ugyldig_antall", f"{where}: {problem}."))
            continue
        default = entry.valuation if entry is not None else Valuation.VERDSATT
        try:
            valuation = Valuation(line.valuation) if line.valuation else default
        except ValueError:
            resolver.findings.append(
                feil("ukjent_verdsetting", f"{where}: «{line.valuation}» er ikke en verdsetting.")
            )
            continue
        label = entry.label if entry is not None else (line.description.strip() or "Annet")
        unit_value: Decimal | None = None
        if valuation is Valuation.NORMALVERDI:
            if entry is None or entry.value is None:
                resolver.findings.append(
                    feil(
                        "ingen_normalverdi",
                        f"{where}: {label} har ingen normalverdi i takseringsreglene. Velg "
                        "kostpris eller oppgitt verdi.",
                    )
                )
                continue
            if resolver.rates is None:
                resolver.findings.append(
                    feil(
                        "ingen_satser",
                        f"{where}: Skattedirektoratet har ikke publisert takseringsreglene for "
                        f"{resolver.year} ennå (de kommer i desember). {label} kan ikke "
                        "verdsettes med fjorårets satser; verdiene endres hvert år.",
                    )
                )
                continue
            if line.unit_value is not None and line.unit_value != entry.value:
                resolver.findings.append(
                    feil(
                        "normalverdi_fast",
                        f"{where}: normalverdien for {label} er {entry.value} kr i "
                        f"{resolver.year}. En annen verdi føres som «oppgitt verdi», med "
                        "begrunnelsen i beskrivelsen.",
                    )
                )
                continue
            unit_value = entry.value
        elif valuation is Valuation.SNITT:
            if entry is None or entry.species is None or entry.valuation is not Valuation.SNITT:
                resolver.findings.append(
                    feil(
                        "snitt_ikke_tillatt",
                        f"{where}: gjennomsnittsverdien gjelder bare voksne dyr som var med i "
                        "inngående balanse (kyr, avlsgriser, sauer og geiter fra inngående "
                        "balanse).",
                    )
                )
                continue
            average = averages.get(entry.species)
            if average is None:
                resolver.findings.append(
                    feil(
                        "ingen_voksne_inngaende",
                        f"{where}: inngående balanse har ingen voksne dyr av samme art å "
                        f"regne gjennomsnittet av. Før {label.lower()} inn i inngående linjer "
                        "først.",
                    )
                )
                continue
            unit_value = average
        else:
            if line.unit_value is None:
                resolver.findings.append(
                    feil(
                        "mangler_verdi",
                        f"{where}: {label} verdsettes til "
                        + ("kostpris" if valuation is Valuation.KOSTPRIS else "en oppgitt verdi")
                        + ", og enhetsverdien må oppgis.",
                    )
                )
                continue
            if valuation is Valuation.VERDSATT and not line.description.strip():
                resolver.findings.append(
                    feil(
                        "mangler_begrunnelse",
                        f"{where}: en oppgitt verdi trenger en begrunnelse i beskrivelsen "
                        "(for eksempel dokumentert tilvirkningsverdi).",
                    )
                )
                continue
            unit_value = line.unit_value
        problem = _decimal_problem(unit_value, _UNIT_STEP, "enhetsverdien")
        if problem is not None:
            resolver.findings.append(feil("ugyldig_verdi", f"{where}: {problem}."))
            continue
        resolved.append(
            CountLine(
                side=Side.UTGAENDE,
                kind=kind,
                category=line.category,
                label=label,
                description=line.description.strip(),
                quantity=line.quantity,
                unit=(line.unit or (entry.unit if entry is not None else "stk")).strip()[:16],
                valuation=valuation.value,
                unit_value=unit_value,
                value=posting.quantize_money(line.quantity * unit_value),
                account_number=resolver.account(where, kind, line.account_number),
                nibio=entry.nibio if entry is not None else None,
            )
        )
    return resolved


async def review(
    db: AsyncSession, *, company: Company, year: int, lines: Sequence[CountLineInput]
) -> CountReview:
    """Price the count and say what it would post, without posting or storing anything.

    The review the page shows is this one, not a sum computed in the browser;
    :func:`post_count` runs it again on what it posts.
    """
    _require_landbruk(company)
    findings: list[Finding] = []
    fiscal = await _financial_year(db, company, year)
    refusal = year_refusal(fiscal, year)
    if refusal is not None:
        findings.append(feil("aar_ikke_aapent", refusal[0].upper() + refusal[1:] + "."))
    accounts = {
        a.account_number: a
        for a in await db.scalars(select(Account).where(Account.company_id == company.id))
    }
    resolver = _Resolver(year, regler.for_year(year), catalogue(year), accounts, findings)

    sides: dict[str, list[CountLineInput]] = {Side.INNGAENDE: [], Side.UTGAENDE: []}
    for number, line in enumerate(lines, start=1):
        if line.side not in sides:
            findings.append(
                feil(
                    "ukjent_side",
                    f"Linje {number}: «{line.side}» er verken inngaende eller utgaende.",
                )
            )
            continue
        sides[line.side].append(line)

    previous = await year_count(db, company=company, year=year - 1)
    if previous is not None:
        source = OpeningSource.FORRIGE_TELLING
        opening = [_as_opening(li) for li in previous.closing]
        if sides[Side.INNGAENDE]:
            findings.append(
                feil(
                    "inngaende_fra_forrige_telling",
                    f"Inngående balanse er tellingen for {year - 1}, uendret "
                    "(takseringsreglene § 5-2 siste ledd). Send ingen inngående linjer; "
                    f"er den feil, rett tellingen for {year - 1}.",
                )
            )
    else:
        source = OpeningSource.OPPGITT
        opening = _resolve_opening(resolver, sides[Side.INNGAENDE])
    closing = _resolve_closing(resolver, sides[Side.UTGAENDE], _averages(opening, resolver.known))
    if not closing and not findings:
        findings.append(feil("ingen_linjer", "Tellingen har ingen linjer for 31. desember."))

    booked = await booked_balances(db, company, up_to=year, without_count_of=year)
    booked_opening = await booked_balances(db, company, up_to=year - 1)
    counted: dict[str, Decimal] = defaultdict(lambda: ZERO)
    opening_counted: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for li in closing:
        counted[li.account_number] += li.value
    for li in opening:
        opening_counted[li.account_number] += li.value
    numbers = sorted(
        set(counted)
        | {n for n, v in booked.items() if v != ZERO and kind_of_account(n) is not None}
        | set(opening_counted)
    )
    changes: list[AccountChange] = []
    voucher: list[PlannedCountLine] = []
    for account_number in numbers:
        kind = kind_of_account(account_number)
        if kind is None:
            continue
        account = accounts.get(account_number)
        seed = KNOWN_ACCOUNTS.get(account_number)
        description = (
            account.description if account is not None else seed.description if seed else None
        )
        change = AccountChange(
            account_number=account_number,
            account_description=description,
            kind=kind,
            booked=booked.get(account_number, ZERO),
            counted=counted.get(account_number, ZERO),
            opening_counted=opening_counted.get(account_number, ZERO),
            opening_booked=booked_opening.get(account_number, ZERO),
        )
        changes.append(change)
        if account_number not in counted and change.booked != ZERO:
            findings.append(
                feil(
                    "konto_uten_telling",
                    f"Konto {account_number} ({description or 'ukjent'}) har en saldo på "
                    f"{change.booked} kr, men ingen linje i tellingen. Tell det som står der "
                    "(også 0 hvis det er borte), ellers ville verdien stått i balansen to ganger "
                    "eller ikke blitt skrevet ned.",
                )
            )
            continue
        if not opening and change.opening_booked != ZERO and kind is Kind.BUSKAP:
            findings.append(
                merknad(
                    "mangler_inngaende",
                    f"Konto {account_number} sto i {change.opening_booked} kr 1. januar, men "
                    "tellingen har ingen inngående linjer. Voksne dyr fra inngående balanse "
                    "verdsettes til gjennomsnittet av dem (takseringsreglene § 5-2), og det "
                    "regnes av de inngående linjene.",
                )
            )
        if change.opening_counted != change.opening_booked and opening:
            findings.append(
                merknad(
                    "inngaende_avvik",
                    f"Inngående linjer på konto {account_number} er {change.opening_counted} kr, "
                    f"men regnskapet sto i {change.opening_booked} kr 1. januar. Det kan "
                    "være riktig (en fjorårstelling som ble ført et annet sted), men da bør "
                    "forskjellen kunne forklares.",
                )
            )
        if change.change == ZERO:
            continue
        text = f"Telling {year}: {KIND_ACCOUNTS[kind].label.lower()}"
        amount = abs(change.change)
        change_account = KIND_ACCOUNTS[kind].change
        if change.change > ZERO:
            voucher.append(PlannedCountLine(account_number, text, debit=amount))
            voucher.append(PlannedCountLine(change_account, text, credit=amount))
        else:
            voucher.append(PlannedCountLine(change_account, text, debit=amount))
            voucher.append(PlannedCountLine(account_number, text, credit=amount))

    if regler.for_year(year) is None and not blocking(findings):
        findings.append(
            merknad(
                "satser_ikke_publisert",
                f"Takseringsreglene for {year} er ikke publisert ennå. Linjer med "
                "gjennomsnittsverdi og kostpris kan føres; normalverdiene kommer i desember.",
            )
        )
    return CountReview(
        year=year,
        counted_on=fiscal.end_date if fiscal is not None else None,
        opening_source=source,
        opening=opening,
        closing=closing,
        accounts=changes,
        voucher_lines=voucher,
        totals=_totals(opening, closing),
        findings=findings,
    )


# --- Posting ------------------------------------------------------------------------


async def _journal(db: AsyncSession, company: Company) -> Journal:
    journal = await db.scalar(
        select(Journal).where(
            Journal.company_id == company.id, Journal.journal_id == COUNT_JOURNAL_ID
        )
    )
    if journal is None:
        journal = Journal(
            company_id=company.id,
            journal_id=COUNT_JOURNAL_ID,
            description=COUNT_JOURNAL_DESCRIPTION,
            journal_type=COUNT_JOURNAL_TYPE,
        )
        db.add(journal)
        await db.flush()
    return journal


async def _post_voucher(
    db: AsyncSession,
    *,
    company: Company,
    fiscal: FinancialYear,
    lines: Sequence[PlannedCountLine],
    corrects_pk: int | None,
    created_by_user_id: int | None,
) -> Transaction:
    journal = await _journal(db, company)
    # 4090 and 4290 joined the add-on after farms had switched it on, and a
    # farm whose add-on the næringskode switched on may have none of them yet:
    # created from the seed rather than refused for an account nobody chose to
    # leave out. A number the seed does not know was checked by the review.
    for number in sorted({line.account_number for line in lines}):
        if number in KNOWN_ACCOUNTS:
            await kontoplan.ensure_seeded_account(db, company=company, number=number)
    accounts = await ledger_service._accounts_by_number(db, company)
    day = fiscal.end_date
    transaction = Transaction(
        journal_id=journal.id,
        transaction_id=await ledger_service._next_voucher_number(db, journal.id, fiscal.year),
        financial_year=fiscal.year,
        period=day.month,
        transaction_date=day,
        gl_posting_date=day,
        description=f"Telling av buskap og varelager {fiscal.year}",
        voucher_type=COUNT_VOUCHER_TYPE,
        corrects_transaction_pk=corrects_pk,
        created_by_user_id=created_by_user_id,
    )
    transaction.lines = [
        TransactionLine(
            record_id=str(number),
            account=accounts[line.account_number],
            description=line.description,
            debit=line.debit,
            credit=line.credit,
        )
        for number, line in enumerate(lines, start=1)
    ]
    posting.post(transaction)
    db.add(transaction)
    await db.flush()
    return transaction


async def _reverse(
    db: AsyncSession, *, company: Company, voucher_pk: int, created_by_user_id: int | None
) -> Transaction:
    original = await ledger_service.get_voucher(db, company=company, voucher_pk=voucher_pk)
    reversal = posting.build_reversal(
        original,
        transaction_id=await ledger_service._next_voucher_number(
            db, original.journal_id, original.financial_year
        ),
        gl_posting_date=original.gl_posting_date,
        period=original.period,
        financial_year=original.financial_year,
        description=f"Reversering av telling {original.transaction_id}",
    )
    reversal.reverses_transaction_pk = original.id
    reversal.created_by_user_id = created_by_user_id
    posting.post(reversal)
    db.add(reversal)
    await db.flush()
    return reversal


def _row(position: int, line: CountLine) -> FarmCountLine:
    return FarmCountLine(
        position=position,
        side=line.side.value,
        kind=line.kind.value,
        category=line.category,
        description=(line.description or line.label)[:255],
        quantity=line.quantity,
        unit=line.unit,
        valuation=line.valuation,
        unit_value=line.unit_value,
        value=line.value,
        account_number=line.account_number,
    )


async def post_count(
    db: AsyncSession,
    *,
    company: Company,
    year: int,
    lines: Sequence[CountLineInput],
    correct: bool = False,
    created_by_user_id: int | None = None,
) -> FarmCount:
    """Store the count and post its voucher; with ``correct`` replace the standing one.

    The review runs again here on what is posted. A count that changes nothing
    (the same herd at the same values) is stored without a voucher.
    """
    checked = await review(db, company=company, year=year, lines=lines)
    problems = blocking(checked.findings)
    if problems:
        raise InvalidCountError("tellingen kan ikke føres slik den er. Se funnene", problems)
    fiscal = await _financial_year(db, company, year)
    assert fiscal is not None  # noqa: S101 - the review refuses a year Myntos does not keep
    standing = await _standing_row(db, company, year)
    if standing is not None and not correct:
        raise CountExistsError(
            f"det er allerede ført en telling for {year}. Retting gjøres ved å føre en ny "
            "telling som erstatter den, som et eget valg"
        )
    if standing is None and correct:
        raise NoCountError(f"det finnes ingen telling for {year} å rette")
    if standing is not None and standing.transaction_pk is not None:
        await _reverse(
            db,
            company=company,
            voucher_pk=standing.transaction_pk,
            created_by_user_id=created_by_user_id,
        )
    transaction = (
        await _post_voucher(
            db,
            company=company,
            fiscal=fiscal,
            lines=checked.voucher_lines,
            corrects_pk=standing.transaction_pk if standing is not None else None,
            created_by_user_id=created_by_user_id,
        )
        if checked.voucher_lines
        else None
    )
    stored = FarmCount(
        company_id=company.id,
        financial_year=year,
        counted_on=fiscal.end_date,
        transaction_pk=transaction.id if transaction is not None else None,
        corrects_id=standing.id if standing is not None else None,
        opening_source=checked.opening_source.value,
        created_by_user_id=created_by_user_id,
    )
    stored.lines = [
        _row(position, line)
        for position, line in enumerate([*checked.opening, *checked.closing], start=1)
    ]
    db.add(stored)
    await db.flush()
    return stored


async def withdraw_count(
    db: AsyncSession, *, company: Company, year: int, created_by_user_id: int | None = None
) -> FarmCount:
    """Withdraw the standing count: reverse its voucher, store an empty list in its place."""
    _require_landbruk(company)
    fiscal = await _financial_year(db, company, year)
    refusal = year_refusal(fiscal, year)
    if refusal is not None or fiscal is None:
        raise FarmYearNotOpenError(refusal or f"regnskapsåret {year} finnes ikke")
    standing = await _standing_row(db, company, year)
    if standing is None:
        raise NoCountError(f"det finnes ingen telling for {year} å trekke tilbake")
    if standing.transaction_pk is not None:
        await _reverse(
            db,
            company=company,
            voucher_pk=standing.transaction_pk,
            created_by_user_id=created_by_user_id,
        )
    stored = FarmCount(
        company_id=company.id,
        financial_year=year,
        counted_on=fiscal.end_date,
        transaction_pk=None,
        corrects_id=standing.id,
        opening_source=OpeningSource.TRUKKET.value,
        created_by_user_id=created_by_user_id,
    )
    stored.lines = []
    db.add(stored)
    await db.flush()
    return stored


async def has_count(db: AsyncSession, *, company: Company, year: int) -> bool:
    return await _standing_row(db, company, year) is not None
