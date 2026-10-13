"""Næringer: a company that files one VAT return, or two.

Almost every company files one MVA-melding, of one category, on one schedule:
``company.vat_category`` and ``company.vat_period_type``. A farm, a forest or a
fisherman files the **primærnæring** melding, and may put other business in it
only while that business's taxable sales and uttak stay at or under 30 000
kroner a year (merverdiavgiftsloven § 15-1 (1)). Above that the company files
an **alminnelig** melding as well (Merverdiavgiftshåndboken 15-1.3), each with
its own terms and deadlines:

- primærnæring yearly, due 10 April (skatteforvaltningsforskriften § 8-3-7 (6),
  § 8-3-10 (3)), or every two months when Skatteetaten has agreed to it for at
  least two years (§ 8-3-8 (3) and (4));
- alminnelig every two months (§ 8-3-1), or yearly, due 10 March, under a
  million kroner (§ 8-3-3, where the twelve months' qualification does not
  apply to the other business of a primærnæring, § 8-3-3 (2) second sentence).

Each melding says which it is in ``meldingskategori``, in the melding and in
Altinn's envelope, and Skatteetaten holds the company's duty per category in
Merverdiavgiftsregisteret (TT02 answered a melding of the wrong category with
``MVA_PLIKT_OPPGITT_MELDINGSKATEGORI_ALMINNELIG_NÆRING_FINNES_IKKE``).

**The main melding** is the company's own two columns, untouched, so a company
with one melding is exactly what it was before this module existed. **The second
one** is :class:`~myntos.domain.mva.models.MvaSecondNaering`, at most one, whose
category is always the other of the two Myntos files.

**Which melding a line belongs to is decided by its account**, from the day the
second melding starts: a line on one of the second melding's accounts dated on
or after ``valid_from`` belongs to it, and every other line to the main one.
Three ways were weighed:

- *By register category* cannot decide a purchase. Diesel, repairs and a
  telephone are the same categories for the farm and for the snow-ploughing
  beside it, and the input VAT on them is exactly what has to go in the right
  melding. A category also decides nothing for an invoice, a bank line, an
  imported voucher or a manual voucher.
- *Explicitly on every line* is the most precise and the most error-prone: every
  way of posting would have to ask, and the one that forgets files the VAT in
  the wrong melding without a sound.
- *By account* reaches every way a line can arrive without any of them knowing
  (each already chooses an account, and the register lets the person pick
  another account of the same kind), and it is how farm books are kept anyway:
  NIBIO's kodesystem keeps annen næring in its own department, and the ENK's
  skattemelding wants a næringsspesifikasjon per næring, so the other
  business's income and costs are on accounts of their own regardless. A
  shared cost is split as two lines on two accounts, which is how
  Skatteetaten's own guidance on fellesanskaffelser has it divided.

The rule is read when the melding is built, not stamped on the line, so the
guard below is what keeps the past fixed: a change that would move a line into
or out of a term already filed (or recorded as filed elsewhere) is refused.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.ledger.chart import INPUT_VAT_ACCOUNT, OUTPUT_VAT_ACCOUNT
from myntos.domain.ledger.models import (
    Account,
    Company,
    Journal,
    TaxCode,
    Transaction,
    TransactionLine,
)
from myntos.domain.mva.codes import SUPPORTED_CATEGORIES, Meldingskategori
from myntos.domain.mva.errors import (
    FiledLinesWouldMoveError,
    InvalidPeriodError,
    UnknownNaeringError,
    UnsupportedCategoryError,
)
from myntos.domain.mva.models import (
    FilingStatus,
    MvaFiling,
    MvaSecondNaering,
    MvaSecondNaeringAccount,
)
from myntos.domain.mva.periods import (
    PeriodType,
    Termin,
    UnknownPeriodError,
    parse_period_type,
    terminer,
)

#: How each category reads to a person, on the MVA page and in the API.
CATEGORY_LABELS: dict[Meldingskategori, str] = {
    Meldingskategori.ALMINNELIG: "Alminnelig næring",
    Meldingskategori.PRIMAERNAERING: "Primærnæring (jord, skog, fiske)",
    Meldingskategori.KOMPENSASJON: "Kompensasjon",
    Meldingskategori.OMVENDT_AVGIFTSPLIKT: "Omvendt avgiftsplikt",
    Meldingskategori.EHANDEL: "E-handel (VOEC)",
}

#: The settlement account, where the VAT of a filed term is moved. Never a line
#: of a melding, so never an account of one.
_SETTLEMENT_ACCOUNT = "2740"

# The VAT accounts carry the VAT and not what it was charged on, and no taxed
# line is booked to them; assigning one would read as if it meant something.
_NOT_ASSIGNABLE = frozenset({OUTPUT_VAT_ACCOUNT, INPUT_VAT_ACCOUNT, _SETTLEMENT_ACCOUNT})


class InvalidNaeringSetupError(InvalidPeriodError):
    """The second melding cannot be set up as asked: an unknown account, a VAT account, a date.

    A 422 like an impossible period, which is what it would produce.
    """


def main_category(company: Company) -> Meldingskategori:
    try:
        category = Meldingskategori(company.vat_category)
    except ValueError:
        return Meldingskategori.ALMINNELIG
    return category if category in SUPPORTED_CATEGORIES else Meldingskategori.ALMINNELIG


def main_period_type(company: Company) -> PeriodType:
    try:
        return parse_period_type(company.vat_period_type)
    except UnknownPeriodError:
        # A value written by hand into the database. Two months is the law's
        # default, and the settings card shows the value so it can be fixed.
        return PeriodType.TO_MAANEDER


def other_category(category: Meldingskategori) -> Meldingskategori:
    """The category of the second melding: the other of the two Myntos files."""
    if category is Meldingskategori.PRIMAERNAERING:
        return Meldingskategori.ALMINNELIG
    return Meldingskategori.PRIMAERNAERING


@dataclass(frozen=True)
class Naering:
    """One of the company's meldinger: its category and its terms."""

    category: Meldingskategori
    period_type: PeriodType
    is_main: bool
    # Only on the second melding: the first day it covers.
    valid_from: date | None = None

    @property
    def label(self) -> str:
        return CATEGORY_LABELS[self.category]

    def has(self, termin: Termin) -> bool:
        """Whether the term is one of this melding's: the second has none before it starts."""
        return self.valid_from is None or termin.end >= self.valid_from

    def terminer(self, year: int) -> list[Termin]:
        return [termin for termin in terminer(self.period_type, year) if self.has(termin)]

    def termin(self, year: int, index: int) -> Termin:
        try:
            found = Termin(self.period_type, year, index)
        except UnknownPeriodError as error:
            raise InvalidPeriodError(str(error)) from error
        if not self.has(found):
            assert self.valid_from is not None  # noqa: S101 - has() is True without one
            raise InvalidPeriodError(
                f"meldingen for {self.label.lower()} begynner "
                f"{self.valid_from.strftime('%d.%m.%Y')}, og {found.label} er før det"
            )
        return found


@dataclass(frozen=True)
class Naeringer:
    """The company's meldinger, and the rule that sends each line to one of them."""

    main: Naering
    second: Naering | None = None
    # Account numbers whose lines go in the second melding from its first day.
    second_accounts: frozenset[str] = field(default_factory=frozenset)

    @property
    def all(self) -> tuple[Naering, ...]:
        return (self.main,) if self.second is None else (self.main, self.second)

    @property
    def is_split(self) -> bool:
        return self.second is not None

    def get(self, category: str | None) -> Naering:
        """The melding named by its category; the main one when none is named."""
        if category is None or category == "" or category == self.main.category.value:
            return self.main
        if self.second is not None and category == self.second.category.value:
            return self.second
        try:
            known = Meldingskategori(category)
        except ValueError as error:
            raise UnsupportedCategoryError(f"ukjent meldingskategori: {category!r}") from error
        raise UnknownNaeringError(
            f"selskapet leverer ikke mva-melding for {CATEGORY_LABELS[known].lower()}"
        )

    def of_line(self, day: date, account_number: str) -> Meldingskategori:
        second = self.second
        if (
            second is not None
            and second.valid_from is not None
            and day >= second.valid_from
            and account_number in self.second_accounts
        ):
            return second.category
        return self.main.category


async def load(db: AsyncSession, company: Company) -> Naeringer:
    main = Naering(
        category=main_category(company), period_type=main_period_type(company), is_main=True
    )
    row = await db.scalar(select(MvaSecondNaering).where(MvaSecondNaering.company_id == company.id))
    if row is None:
        return Naeringer(main=main)
    try:
        period_type = parse_period_type(row.period_type)
    except UnknownPeriodError:
        period_type = PeriodType.TO_MAANEDER
    numbers = await db.scalars(
        select(Account.account_number)
        .join(MvaSecondNaeringAccount, MvaSecondNaeringAccount.account_id == Account.id)
        .where(
            MvaSecondNaeringAccount.company_id == company.id,
            Account.company_id == company.id,
        )
    )
    return Naeringer(
        main=main,
        second=Naering(
            category=other_category(main.category),
            period_type=period_type,
            is_main=False,
            valid_from=row.valid_from,
        ),
        second_accounts=frozenset(numbers),
    )


async def second_accounts(db: AsyncSession, company: Company) -> list[Account]:
    """The second melding's accounts, in account order, for showing them."""
    return list(
        await db.scalars(
            select(Account)
            .join(MvaSecondNaeringAccount, MvaSecondNaeringAccount.account_id == Account.id)
            .where(
                MvaSecondNaeringAccount.company_id == company.id,
                Account.company_id == company.id,
            )
            .order_by(Account.account_number)
        )
    )


# --- Lines and the guard -----------------------------------------------------------


async def taxed_line_accounts(
    db: AsyncSession, company: Company, start: date, end: date
) -> list[tuple[date, str]]:
    """Posting date and account of every posted line with a reportable code in the dates.

    The same lines the melding is built from, which is what the guard has to
    see: a line with no reportable code is in no melding to move between.
    """
    rows = await db.execute(
        select(Transaction.gl_posting_date, Account.account_number)
        .join(TransactionLine, TransactionLine.transaction_pk == Transaction.id)
        .join(Account, Account.id == TransactionLine.account_id)
        .join(Journal, Journal.id == Transaction.journal_id)
        .join(TaxCode, TaxCode.id == TransactionLine.tax_code_id)
        .where(
            Journal.company_id == company.id,
            Transaction.posted_at.is_not(None),
            TaxCode.is_reportable.is_(True),
            Transaction.gl_posting_date >= start,
            Transaction.gl_posting_date <= end,
        )
    )
    return [(day, str(number)) for day, number in rows]


def _filed_termin(filing: MvaFiling) -> Termin | None:
    try:
        return Termin(parse_period_type(filing.period_type), filing.year, filing.period_index)
    except UnknownPeriodError:
        return None


async def _closed_terms(db: AsyncSession, company: Company) -> list[tuple[str, Termin]]:
    """Every term filed from Myntos or recorded as filed elsewhere, with its category."""
    closing = [s.value for s in FilingStatus if s.closes_term]
    found: list[tuple[str, Termin]] = []
    for filing in await db.scalars(
        select(MvaFiling).where(MvaFiling.company_id == company.id, MvaFiling.status.in_(closing))
    ):
        termin = _filed_termin(filing)
        if termin is not None:
            found.append((filing.category, termin))
    return found


async def guard(db: AsyncSession, company: Company, before: Naeringer, after: Naeringer) -> None:
    """Refuse a change that would move a line into or out of a closed term.

    Generic on purpose: an account moved between the meldinger, the second
    melding's first day moved, the second melding added or removed and the
    categories swapped all come down to lines whose melding changes, and the
    question is only whether one of them sits in a term somebody has filed.

    With one melding before and after, nothing moves between meldinger, and a
    change of category is what it always was: the next melding's category.
    """
    if before.second is None and after.second is None:
        return
    closed = await _closed_terms(db, company)
    if not closed:
        return
    start = min(termin.start for _, termin in closed)
    end = max(termin.end for _, termin in closed)
    lines = await taxed_line_accounts(db, company, start, end)
    for category, termin in sorted(closed, key=lambda item: item[1].start):
        for day, account in lines:
            if not termin.contains(day):
                continue
            was = before.of_line(day, account).value
            will = after.of_line(day, account).value
            if was != will and category in (was, will):
                label = CATEGORY_LABELS.get(Meldingskategori(category), category)
                raise FiledLinesWouldMoveError(
                    f"endringen ville flyttet mva på konto {account} inn i eller ut av "
                    f"{termin.label} for {label.lower()}, som er sendt. En sendt melding "
                    "endres ikke i ettertid: rett bilaget med en tilbakeføring og en ny "
                    "føring på riktig konto i stedet"
                )


# --- Changing them -----------------------------------------------------------------


async def set_second(
    db: AsyncSession,
    company: Company,
    *,
    period_type: str,
    valid_from: date,
    account_numbers: list[str],
) -> Naeringer:
    """Set up the second melding, or change it. Replaces the list of accounts whole."""
    try:
        parsed = parse_period_type(period_type)
    except UnknownPeriodError as error:
        raise InvalidPeriodError(str(error)) from error
    if not 2000 <= valid_from.year <= 2100:
        raise InvalidNaeringSetupError(f"året {valid_from.year} er utenfor det Myntos regner med")
    wanted = sorted({number.strip() for number in account_numbers if number.strip()})
    refused = [number for number in wanted if number in _NOT_ASSIGNABLE]
    if refused:
        raise InvalidNaeringSetupError(
            f"konto {', '.join(refused)} er en mva-konto og hører ikke til noen av meldingene; "
            "velg kontoene salget og kostnadene i den andre næringen føres på"
        )
    accounts = {
        account.account_number: account
        for account in await db.scalars(
            select(Account).where(
                Account.company_id == company.id, Account.account_number.in_(wanted)
            )
        )
    }
    unknown = [number for number in wanted if number not in accounts]
    if unknown:
        raise InvalidNaeringSetupError(
            f"kontoplanen har ikke konto {', '.join(unknown)}; legg den til under Kontoplan først"
        )

    before = await load(db, company)
    after = Naeringer(
        main=before.main,
        second=Naering(
            category=other_category(before.main.category),
            period_type=parsed,
            is_main=False,
            valid_from=valid_from,
        ),
        second_accounts=frozenset(wanted),
    )
    await guard(db, company, before, after)

    row = await db.scalar(select(MvaSecondNaering).where(MvaSecondNaering.company_id == company.id))
    if row is None:
        row = MvaSecondNaering(company_id=company.id)
        db.add(row)
    row.period_type = parsed.value
    row.valid_from = valid_from
    await db.execute(
        delete(MvaSecondNaeringAccount).where(MvaSecondNaeringAccount.company_id == company.id)
    )
    for number in wanted:
        db.add(MvaSecondNaeringAccount(company_id=company.id, account_id=accounts[number].id))
    await db.flush()
    return after


async def remove_second(db: AsyncSession, company: Company) -> Naeringer:
    """Go back to one melding. Refused once the second melding has any melding of its own.

    Not only the guard's lines: a filed nullmelding has no lines to move, and
    with one melding left the term list no longer tells the categories apart,
    so the second melding's rows would turn up under the main melding's terms.
    """
    before = await load(db, company)
    if before.second is None:
        return before
    kept = await db.scalar(
        select(MvaFiling.id)
        .where(
            MvaFiling.company_id == company.id,
            MvaFiling.category == before.second.category.value,
        )
        .limit(1)
    )
    if kept is not None:
        raise FiledLinesWouldMoveError(
            f"meldingen for {before.second.label.lower()} er allerede kontrollert, sendt eller "
            "merket som sendt fra et annet program, og kan ikke fjernes"
        )
    after = Naeringer(main=before.main)
    await guard(db, company, before, after)
    await db.execute(
        delete(MvaSecondNaeringAccount).where(MvaSecondNaeringAccount.company_id == company.id)
    )
    await db.execute(delete(MvaSecondNaering).where(MvaSecondNaering.company_id == company.id))
    await db.flush()
    return after


async def guard_main_category(db: AsyncSession, company: Company, category: str) -> None:
    """Refuse swapping the categories while two meldinger have filed anything.

    Swapping is what changing the main category does when there are two: the
    second melding takes the other one, every line changes melding, and the
    filed rows, which keep the category they were filed under, would be shown
    under the other melding's terms.
    """
    before = await load(db, company)
    if before.second is None or category == before.main.category.value:
        return
    try:
        chosen = Meldingskategori(category)
    except ValueError:
        return  # the caller refuses an unknown category with its own words
    closing = [s.value for s in FilingStatus if s.closes_term]
    filed = await db.scalar(
        select(MvaFiling.id)
        .where(MvaFiling.company_id == company.id, MvaFiling.status.in_(closing))
        .limit(1)
    )
    if filed is not None:
        raise FiledLinesWouldMoveError(
            "selskapet leverer to mva-meldinger og har sendt for dem; å bytte hvilken som er "
            "hovedmeldingen ville flyttet sendte terminer mellom dem"
        )
    after = Naeringer(
        main=Naering(category=chosen, period_type=before.main.period_type, is_main=True),
        second=Naering(
            category=other_category(chosen),
            period_type=before.second.period_type,
            is_main=False,
            valid_from=before.second.valid_from,
        ),
        second_accounts=before.second_accounts,
    )
    await guard(db, company, before, after)
