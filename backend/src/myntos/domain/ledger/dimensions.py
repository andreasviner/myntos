"""Dimensions: avdelinger, prosjekter, a farm's driftsgrener, and the result per value.

A dimension is a second way to sort the books beside the account. The account
says *what* an amount was (feed, milk, interest); the dimension says *which part
of the business* it belongs to: the milk production or the sheep, the shop in
Bergen or the one in Voss, the project for one customer. A farm wants its result
per driftsgren, as Tripletex, Duett and PowerOffice give it; every business
with two departments wants theirs. SAF-T has had the shape since the start
(``MasterFiles/AnalysisTypeTable`` and ``Line/Analysis``), and so has the schema
(:class:`~.models.DimensionType`, :class:`~.models.DimensionValue`,
:class:`~.models.TransactionLineAnalysis`). This module is what lets a person
use it.

The rules, each one a property of the books rather than of a page:

**A dimension belongs to one company.** Every lookup goes through
``DimensionType.company_id``, and :func:`resolve`, which every way of posting
calls before it attaches an allocation, refuses a value of another company's
the way the register refuses another company's party: as not found
(docs/company-isolation.md).

**A value in use is never deleted, and its code never changes.** A posted line
points at it, and SAF-T exports the code on the line and the name in the master
data, so a value is renamed or switched off, as an account is. Deleting is for
the value added by mistake, before anything used it; the foreign key from
``gl_line_analysis`` is RESTRICT in case anything forgets.

**A line has at most one value per dimension, for its whole amount.** A line
belongs to the milk or to the sheep, and an entry that is both is two lines.
SAF-T allows several allocations of one type that split a line, and
:mod:`myntos.domain.ledger.posting` accepts that shape when a file brings it,
but nothing Myntos posts by itself splits a line.

**Switched off is for new entries only.** A deactivated value or dimension is
refused by :func:`resolve` and not offered by the register; every line already
allocated keeps it, and the report and the SAF-T file still show it.

**The farm's codes are NIBIO's, offered and never imposed** (docs/landbruk.md
rule 1). With the farming add-on on, :func:`suggestions` offers NIBIO's
driftsgrener and avdelinger (:mod:`.driftsgrener`), and the farm adds the ones
it has. A company without the add-on is offered nothing of the kind.

**The result per value** (:func:`result`) is the income and the costs on the
income-statement accounts, per value and per month, with what no value carries
as a row of its own («Uten driftsgren»), so the rows always add up to the
year's result. The year-end closing (``AVS``) is left out: its tax cost and
its disposition of the result belong to no driftsgren, and the disposition
would bring every row to zero.

**For whoever posts next.** :func:`resolve` and :func:`allocate` are the whole
of what a new way of posting needs: resolve the ids the person chose, through
the company, then allocate them to the line after its amounts are set and
before :func:`myntos.domain.ledger.posting.post`.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from myntos.domain.ledger import driftsgrener, landbruk
from myntos.domain.ledger.chart import CLOSING_JOURNAL_ID, OPENING_JOURNAL_ID
from myntos.domain.ledger.errors import LedgerError
from myntos.domain.ledger.models import (
    Account,
    AccountType,
    Company,
    DimensionType,
    DimensionValue,
    Journal,
    Transaction,
    TransactionLine,
    TransactionLineAnalysis,
)

ZERO = Decimal("0.00")
_ORE = Decimal("0.01")

#: SAF-T's limits: ``AnalysisType`` is a ``SAFcodeType`` (9 characters) and the
#: master data's ``AnalysisID`` a ``SAFmiddle1textType`` (35). Refused here
#: rather than truncated in the export, where two codes differing past the limit
#: would merge.
MAX_CODE = 9
MAX_VALUE = 35
MAX_DESCRIPTION = 255

#: The most values one line may carry: one per dimension, and no company keeps
#: more dimensions than this.
MAX_PER_LINE = 9

_CODE = re.compile(r"^[A-Z0-9ÆØÅ_-]{1,9}$")


# --- Refusals ------------------------------------------------------------------------


class DimensionError(LedgerError):
    """Base class for every refusal about dimensions."""


class DimensionNotFoundError(DimensionError):
    """No such dimension or value in these books.

    The same answer for one that never existed and one belonging to the company
    next door, for the reason every sub-resource gives: the ids are sequential,
    and a 403 would say which of them are real.
    """


class DuplicateDimensionError(DimensionError):
    """The company already has a dimension with that code, or the value in that dimension."""


class DimensionInUseError(DimensionError):
    """A value that lines are allocated to, or a dimension holding one, cannot be deleted."""


class InactiveDimensionError(DimensionError):
    """The value, or its whole dimension, is switched off and is not used for new entries."""


class InvalidDimensionError(DimensionError):
    """A code, value or name SAF-T cannot carry, or two values of one dimension on a line."""


# --- Reading -------------------------------------------------------------------------


@dataclass(frozen=True)
class ValueView:
    id: int
    value: str
    description: str
    is_active: bool
    #: How many posted lines carry it. Anything above zero and it is never
    #: deleted, only switched off.
    lines: int


@dataclass(frozen=True)
class DimensionView:
    id: int
    code: str
    description: str
    is_active: bool
    #: The kind Myntos knows it as (:data:`.driftsgrener.AVDELING` …), or None
    #: for one of the company's own making.
    kind: str | None
    #: What the report calls the lines with no value: «Uten driftsgren».
    without_label: str
    values: tuple[ValueView, ...]


def without_label(description: str) -> str:
    """«Uten avdeling», «Uten driftsgren»: what a line with no value is shown as."""
    name = description.strip()
    return f"Uten {name[:1].lower()}{name[1:]}" if name else "Uten verdi"


def _kind(dimension: DimensionType) -> str | None:
    if dimension.code in driftsgrener.KIND_NAMES:
        return dimension.code
    return driftsgrener.kind_of(dimension.description)


def find_kind(dimensions: Sequence[DimensionType], kind: str) -> DimensionType | None:
    """The company's dimension of ``kind``: the one with its code, else one named for it.

    By code first, because that is Myntos's own. By name second, so a dimension
    a person made by hand as «Avdeling» with code «A» is the avdeling a farm's
    NIBIO codes and an imported file's departments go into, rather than a
    second one beside it.
    """
    for dimension in dimensions:
        if dimension.code == kind:
            return dimension
    for dimension in dimensions:
        if driftsgrener.kind_of(dimension.description) == kind:
            return dimension
    return None


async def _dimensions(db: AsyncSession, company: Company) -> list[DimensionType]:
    return list(
        await db.scalars(
            select(DimensionType)
            .where(DimensionType.company_id == company.id)
            .options(selectinload(DimensionType.values))
            .order_by(DimensionType.code)
        )
    )


async def _line_counts(db: AsyncSession, value_pks: Sequence[int]) -> dict[int, int]:
    if not value_pks:
        return {}
    rows = await db.execute(
        select(TransactionLineAnalysis.dimension_value_id, func.count())
        .where(TransactionLineAnalysis.dimension_value_id.in_(value_pks))
        .group_by(TransactionLineAnalysis.dimension_value_id)
    )
    return {int(value_pk): int(count) for value_pk, count in rows}


async def list_dimensions(db: AsyncSession, *, company: Company) -> list[DimensionView]:
    """Every dimension of the company with its values, switched-off ones included."""
    dimensions = await _dimensions(db, company)
    counts = await _line_counts(db, [v.id for d in dimensions for v in d.values])
    return [
        DimensionView(
            id=dimension.id,
            code=dimension.code,
            description=dimension.description,
            is_active=dimension.is_active,
            kind=_kind(dimension),
            without_label=without_label(dimension.description),
            values=tuple(
                ValueView(
                    id=value.id,
                    value=value.value,
                    description=value.description,
                    is_active=value.is_active,
                    lines=counts.get(value.id, 0),
                )
                for value in sorted(dimension.values, key=lambda v: _sort_key(v.value))
            ),
        )
        for dimension in dimensions
    ]


def _sort_key(value: str) -> tuple[int, int, str]:
    """Numbers in numeric order (2 before 10), then everything else by text."""
    return (0, int(value), value) if value.isdigit() else (1, 0, value.casefold())


# --- Changing ------------------------------------------------------------------------


def validate_code(code: str) -> str:
    """A dimension's code as SAF-T will carry it: up to nine letters, digits, - or _."""
    cleaned = code.strip().upper()
    if not _CODE.match(cleaned):
        raise InvalidDimensionError(
            f"koden må være 1 til {MAX_CODE} bokstaver eller tall (også - og _), uten mellomrom. "
            "SAF-T-filen tar ikke mer"
        )
    return cleaned


def validate_value(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise InvalidDimensionError("verdien trenger en kode, for eksempel et nummer")
    if len(cleaned) > MAX_VALUE:
        raise InvalidDimensionError(
            f"koden kan ha høyst {MAX_VALUE} tegn, som er det SAF-T-filen tar"
        )
    return cleaned


def _validate_description(description: str, what: str) -> str:
    cleaned = description.strip()
    if not cleaned:
        raise InvalidDimensionError(f"{what} trenger et navn")
    if len(cleaned) > MAX_DESCRIPTION:
        raise InvalidDimensionError(f"navnet kan ha høyst {MAX_DESCRIPTION} tegn")
    return cleaned


async def get_dimension(db: AsyncSession, *, company: Company, dimension_pk: int) -> DimensionType:
    """One dimension of ``company``, with its values; another company's is not found."""
    dimension = await db.scalar(
        select(DimensionType)
        .where(DimensionType.id == dimension_pk, DimensionType.company_id == company.id)
        .options(selectinload(DimensionType.values))
    )
    if dimension is None:
        raise DimensionNotFoundError("finner ikke dimensjonen")
    return dimension


async def get_value(db: AsyncSession, *, company: Company, value_pk: int) -> DimensionValue:
    """One value of one of ``company``'s dimensions; another company's is not found."""
    value = await db.scalar(
        select(DimensionValue)
        .join(DimensionType, DimensionType.id == DimensionValue.dimension_type_id)
        .where(DimensionValue.id == value_pk, DimensionType.company_id == company.id)
        .options(selectinload(DimensionValue.dimension_type))
    )
    if value is None:
        raise DimensionNotFoundError("finner ikke verdien")
    return value


async def create_dimension(
    db: AsyncSession, *, company: Company, code: str, description: str
) -> DimensionType:
    """A new dimension (avdeling, prosjekt, driftsgren …), with no values yet."""
    cleaned = validate_code(code)
    name = _validate_description(description, "dimensjonen")
    existing = await _dimensions(db, company)
    if any(dimension.code == cleaned for dimension in existing):
        raise DuplicateDimensionError(f"selskapet har allerede en dimensjon med koden {cleaned}")
    if any(dimension.description.casefold() == name.casefold() for dimension in existing):
        raise DuplicateDimensionError(f"selskapet har allerede en dimensjon som heter «{name}»")
    dimension = DimensionType(company_id=company.id, code=cleaned, description=name, is_active=True)
    db.add(dimension)
    await db.flush()
    return dimension


async def update_dimension(
    db: AsyncSession,
    *,
    company: Company,
    dimension_pk: int,
    description: str | None = None,
    is_active: bool | None = None,
) -> DimensionType:
    """Rename a dimension, or switch it off or on. The code never changes."""
    dimension = await get_dimension(db, company=company, dimension_pk=dimension_pk)
    if description is not None:
        name = _validate_description(description, "dimensjonen")
        others = await _dimensions(db, company)
        if any(
            other.id != dimension.id and other.description.casefold() == name.casefold()
            for other in others
        ):
            raise DuplicateDimensionError(f"selskapet har allerede en dimensjon som heter «{name}»")
        dimension.description = name
    if is_active is not None:
        dimension.is_active = is_active
    await db.flush()
    return dimension


async def delete_dimension(db: AsyncSession, *, company: Company, dimension_pk: int) -> None:
    """Delete a dimension nothing was ever allocated to, with its unused values."""
    dimension = await get_dimension(db, company=company, dimension_pk=dimension_pk)
    counts = await _line_counts(db, [value.id for value in dimension.values])
    used = sum(counts.values())
    if used:
        raise DimensionInUseError(
            f"{used} linjer i regnskapet er ført på verdier i «{dimension.description}», så den "
            "kan ikke slettes. Deaktiver den i stedet: da tilbys den ikke i nye føringer"
        )
    await db.delete(dimension)
    await db.flush()


async def create_value(
    db: AsyncSession, *, company: Company, dimension_pk: int, value: str, description: str
) -> DimensionValue:
    """A new value in one of the company's dimensions: «6110 Melkeproduksjon storfe»."""
    dimension = await get_dimension(db, company=company, dimension_pk=dimension_pk)
    code = validate_value(value)
    name = _validate_description(description, "verdien")
    if any(existing.value.casefold() == code.casefold() for existing in dimension.values):
        raise DuplicateDimensionError(
            f"«{dimension.description}» har allerede {code}. Er den deaktivert, slå den på igjen"
        )
    made = DimensionValue(dimension_type=dimension, value=code, description=name, is_active=True)
    db.add(made)
    await db.flush()
    return made


async def update_value(
    db: AsyncSession,
    *,
    company: Company,
    value_pk: int,
    description: str | None = None,
    is_active: bool | None = None,
) -> DimensionValue:
    """Rename a value, or switch it off or on. The code never changes."""
    value = await get_value(db, company=company, value_pk=value_pk)
    if description is not None:
        value.description = _validate_description(description, "verdien")
    if is_active is not None:
        value.is_active = is_active
    await db.flush()
    return value


async def delete_value(db: AsyncSession, *, company: Company, value_pk: int) -> None:
    """Delete a value no line was ever allocated to."""
    value = await get_value(db, company=company, value_pk=value_pk)
    used = (await _line_counts(db, [value.id])).get(value.id, 0)
    if used:
        raise DimensionInUseError(
            f"{used} linjer i regnskapet er ført på {value.value} {value.description}, så den kan "
            "ikke slettes. Deaktiver den i stedet: da tilbys den ikke i nye føringer, og linjene "
            "beholder den"
        )
    await db.delete(value)
    await db.flush()


# --- What the farming add-on offers --------------------------------------------------


@dataclass(frozen=True)
class SuggestedValue:
    code: str
    name: str
    group: str
    #: Whether the company's dimension of this kind already holds the code.
    added: bool


@dataclass(frozen=True)
class SuggestedSet:
    #: The dimension's code in Myntos (``DRIFTSGR``, ``AVDELING``).
    kind: str
    description: str
    #: The company's dimension of this kind, when it has one; the values are
    #: added there.
    dimension_id: int | None
    values: tuple[SuggestedValue, ...]


async def suggestions(db: AsyncSession, *, company: Company) -> list[SuggestedSet]:
    """NIBIO's driftsgrener and avdelinger, when the farming add-on is on; nothing otherwise."""
    if not landbruk.is_enabled(company):
        return []
    dimensions = await _dimensions(db, company)
    found: list[SuggestedSet] = []
    for kind, codes in driftsgrener.FARM_SETS.items():
        dimension = find_kind(dimensions, kind)
        have = {value.value.casefold() for value in dimension.values} if dimension else set[str]()
        found.append(
            SuggestedSet(
                kind=kind,
                description=driftsgrener.KIND_NAMES[kind],
                dimension_id=dimension.id if dimension is not None else None,
                values=tuple(
                    SuggestedValue(
                        code=code.code,
                        name=code.name,
                        group=code.group,
                        added=code.code.casefold() in have,
                    )
                    for code in codes
                ),
            )
        )
    return found


async def add_suggested(
    db: AsyncSession, *, company: Company, kind: str, codes: Sequence[str]
) -> DimensionType:
    """Add NIBIO's ``codes`` of ``kind`` to the farm's books, making the dimension if needed.

    A code already there is left as it is, renamed or not, so pressing twice
    does nothing the second time. A switched-off one is switched on again,
    because asking for it is saying the farm has it.
    """
    landbruk.require_segment(company, landbruk.SEGMENT)
    offered = driftsgrener.FARM_SETS.get(kind)
    if offered is None:
        raise InvalidDimensionError(
            f"Myntos har ingen standardliste for {kind}. Velg DRIFTSGR eller AVDELING"
        )
    by_code = {code.code: code for code in offered}
    unknown = [code for code in codes if code not in by_code]
    if unknown:
        raise InvalidDimensionError(
            "disse kodene er ikke i NIBIOs liste: " + ", ".join(sorted(unknown)[:10])
        )
    if not codes:
        raise InvalidDimensionError("velg minst én kode fra listen")
    dimensions = await _dimensions(db, company)
    dimension = find_kind(dimensions, kind)
    if dimension is None:
        dimension = DimensionType(
            company_id=company.id,
            code=kind,
            description=driftsgrener.KIND_NAMES[kind],
            is_active=True,
        )
        dimension.values = []
        db.add(dimension)
    dimension.is_active = True
    have = {value.value: value for value in dimension.values}
    for code in dict.fromkeys(codes):
        existing = have.get(code)
        if existing is not None:
            existing.is_active = True
            continue
        dimension.values.append(
            DimensionValue(value=code, description=by_code[code].name, is_active=True)
        )
    await db.flush()
    return dimension


# --- Attaching to a line -------------------------------------------------------------


async def resolve(
    db: AsyncSession, *, company: Company, value_ids: Sequence[int]
) -> tuple[DimensionValue, ...]:
    """The values a person chose for a line, checked against ``company``, in the order given.

    Every way of posting calls this before :func:`allocate`. Refuses another
    company's value or one that does not exist (404 ``dimension_not_found``),
    a switched-off value or dimension (409 ``inactive_dimension``), and the
    same value twice or two values of one dimension (422 ``invalid_dimension``).
    """
    if not value_ids:
        return ()
    if len(value_ids) > MAX_PER_LINE:
        raise InvalidDimensionError(f"en linje kan ha høyst {MAX_PER_LINE} dimensjoner")
    if len(set(value_ids)) != len(value_ids):
        raise InvalidDimensionError("den samme verdien er valgt to ganger på én linje")
    rows = await db.execute(
        select(DimensionValue, DimensionType)
        .join(DimensionType, DimensionType.id == DimensionValue.dimension_type_id)
        .where(DimensionValue.id.in_(value_ids), DimensionType.company_id == company.id)
    )
    found = {value.id: (value, dimension) for value, dimension in rows}
    missing = [value_id for value_id in value_ids if value_id not in found]
    if missing:
        raise DimensionNotFoundError(
            "finner ikke avdelingen, prosjektet eller driftsgrenen som er valgt (id "
            + ", ".join(str(value_id) for value_id in missing)
            + ")"
        )
    seen: set[int] = set()
    chosen: list[DimensionValue] = []
    for value_id in value_ids:
        value, dimension = found[value_id]
        if not dimension.is_active:
            raise InactiveDimensionError(
                f"«{dimension.description}» er deaktivert og brukes ikke i nye føringer. Slå den "
                "på igjen under Avdelinger, eller la feltet stå tomt"
            )
        if not value.is_active:
            raise InactiveDimensionError(
                f"{value.value} {value.description} er deaktivert og brukes ikke i nye føringer. "
                "Slå den på igjen under Avdelinger, eller velg en annen"
            )
        if dimension.id in seen:
            raise InvalidDimensionError(
                f"en linje kan ha bare én verdi for «{dimension.description}». Er beløpet delt "
                "mellom to, før det som to linjer"
            )
        seen.add(dimension.id)
        chosen.append(value)
    return tuple(chosen)


def allocate(line: TransactionLine, values: Sequence[DimensionValue]) -> None:
    """Put ``values`` on ``line``, each for the line's whole amount.

    Call it after the line's amounts are set and before the voucher is posted:
    :func:`myntos.domain.ledger.posting.validate` checks the allocation against
    the amount. The values must come from :func:`resolve`, which is where the
    company is checked.
    """
    amount = (line.debit or ZERO) + (line.credit or ZERO)
    line.analysis = [
        TransactionLineAnalysis(dimension_value=value, amount=amount) for value in values
    ]


def allocated_ids(line: TransactionLine) -> tuple[int, ...]:
    """The values on ``line``, by id, for reading an entry back.

    Reads only what is already loaded and never a relationship, so it costs no
    query and cannot lazy-load on an async session (MissingGreenlet): the
    register's own query loads the allocations, a freshly posted line has them
    and their ids once it is flushed, and a caller that loaded neither (the
    bank's candidate vouchers) gets an empty answer rather than an exception.
    """
    loaded = line.__dict__.get("analysis")
    if not loaded:
        return ()
    return tuple(
        sorted(
            allocation.dimension_value_id
            for allocation in loaded
            if allocation.dimension_value_id is not None
        )
    )


# --- The result per value ------------------------------------------------------------


@dataclass(frozen=True)
class Figures:
    income: Decimal = ZERO
    costs: Decimal = ZERO

    @property
    def result(self) -> Decimal:
        return self.income - self.costs

    def __add__(self, other: Figures) -> Figures:
        return Figures(income=self.income + other.income, costs=self.costs + other.costs)

    def __sub__(self, other: Figures) -> Figures:
        return Figures(income=self.income - other.income, costs=self.costs - other.costs)


@dataclass(frozen=True)
class ResultRow:
    #: None on the row of lines with no value, and on the total.
    value_id: int | None
    value: str | None
    description: str
    is_active: bool
    total: Figures
    #: Twelve, January first.
    months: tuple[Figures, ...]


@dataclass(frozen=True)
class DimensionResult:
    year: int
    dimension: DimensionView
    rows: list[ResultRow]
    unallocated: ResultRow
    total: ResultRow
    #: Lines carrying a value for only part of their amount: the rest is in
    #: ``unallocated``. Zero for everything Myntos posts by itself.
    partly_allocated: int = 0


def _money(value: object) -> Decimal:
    if value is None:
        return ZERO
    return Decimal(str(value)).quantize(_ORE)


def _figures(account_type: AccountType, debit: Decimal, credit: Decimal) -> Figures:
    """Income is what an income account was credited, costs what a cost account was debited."""
    if account_type is AccountType.INNTEKT:
        return Figures(income=credit - debit)
    return Figures(costs=debit - credit)


def _row(
    value_id: int | None,
    value: str | None,
    description: str,
    is_active: bool,
    months: dict[int, Figures],
) -> ResultRow:
    by_month = tuple(months.get(month, Figures()) for month in range(1, 13))
    return ResultRow(
        value_id=value_id,
        value=value,
        description=description,
        is_active=is_active,
        total=sum(by_month, Figures()),
        months=by_month,
    )


async def result(
    db: AsyncSession, *, company: Company, dimension_pk: int, year: int
) -> DimensionResult:
    """Income, costs and result per value of one dimension, for ``year`` and per month.

    Summed in SQL over every posted line on an income or cost account: the
    register's entries, the invoices, the vouchers brought from the previous
    program, a manual voucher, reversals included (they net their originals to
    nothing). The opening balance touches no such account, and the year-end
    closing is left out (see the module docstring). What no value carries,
    including any part of a line a file allocated only partly, is the row
    «Uten …», so the rows add up to the year's result.
    """
    dimension = await get_dimension(db, company=company, dimension_pk=dimension_pk)
    views = {view.id: view for view in await list_dimensions(db, company=company)}
    view = views[dimension.id]
    income_statement = (AccountType.INNTEKT, AccountType.KOSTNAD)
    scope = (
        Journal.company_id == company.id,
        Journal.journal_id.not_in((OPENING_JOURNAL_ID, CLOSING_JOURNAL_ID)),
        Transaction.posted_at.is_not(None),
        Transaction.financial_year == year,
        Account.account_type.in_(income_statement),
    )
    totals = await db.execute(
        select(
            Transaction.period,
            Account.account_type,
            func.sum(TransactionLine.debit),
            func.sum(TransactionLine.credit),
        )
        .select_from(TransactionLine)
        .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
        .join(Journal, Journal.id == Transaction.journal_id)
        .join(Account, Account.id == TransactionLine.account_id)
        .where(*scope)
        .group_by(Transaction.period, Account.account_type)
    )
    whole: dict[int, Figures] = defaultdict(Figures)
    for period, account_type, debit, credit in totals:
        whole[period] = whole[period] + _figures(account_type, _money(debit), _money(credit))

    allocated = await db.execute(
        select(
            Transaction.period,
            Account.account_type,
            TransactionLineAnalysis.dimension_value_id,
            func.sum(case((TransactionLine.debit > 0, TransactionLineAnalysis.amount), else_=ZERO)),
            func.sum(
                case((TransactionLine.credit > 0, TransactionLineAnalysis.amount), else_=ZERO)
            ),
        )
        .select_from(TransactionLineAnalysis)
        .join(TransactionLine, TransactionLine.id == TransactionLineAnalysis.line_id)
        .join(DimensionValue, DimensionValue.id == TransactionLineAnalysis.dimension_value_id)
        .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
        .join(Journal, Journal.id == Transaction.journal_id)
        .join(Account, Account.id == TransactionLine.account_id)
        .where(*scope, DimensionValue.dimension_type_id == dimension.id)
        .group_by(
            Transaction.period, Account.account_type, TransactionLineAnalysis.dimension_value_id
        )
    )
    per_value: dict[int, dict[int, Figures]] = defaultdict(lambda: defaultdict(Figures))
    for period, account_type, value_pk, debit, credit in allocated:
        per_value[value_pk][period] = per_value[value_pk][period] + _figures(
            account_type, _money(debit), _money(credit)
        )

    partly = await db.scalar(
        select(func.count())
        .select_from(TransactionLineAnalysis)
        .join(TransactionLine, TransactionLine.id == TransactionLineAnalysis.line_id)
        .join(DimensionValue, DimensionValue.id == TransactionLineAnalysis.dimension_value_id)
        .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
        .join(Journal, Journal.id == Transaction.journal_id)
        .join(Account, Account.id == TransactionLine.account_id)
        .where(
            *scope,
            DimensionValue.dimension_type_id == dimension.id,
            TransactionLineAnalysis.amount < TransactionLine.debit + TransactionLine.credit,
        )
    )

    rows: list[ResultRow] = []
    carried: dict[int, Figures] = defaultdict(Figures)
    for value in view.values:
        months = per_value.get(value.id, {})
        # A switched-off value with nothing this year is history, not a row.
        if not value.is_active and not months:
            continue
        rows.append(_row(value.id, value.value, value.description, value.is_active, dict(months)))
        for period, figures in months.items():
            carried[period] = carried[period] + figures
    unallocated = _row(
        None,
        None,
        view.without_label,
        True,
        {
            period: whole.get(period, Figures()) - carried.get(period, Figures())
            for period in set(whole) | set(carried)
        },
    )
    total = _row(None, None, "Sum", True, dict(whole))
    return DimensionResult(
        year=year,
        dimension=view,
        rows=rows,
        unallocated=unallocated,
        total=total,
        partly_allocated=int(partly or 0),
    )


__all__ = [
    "MAX_CODE",
    "MAX_PER_LINE",
    "MAX_VALUE",
    "DimensionError",
    "DimensionInUseError",
    "DimensionNotFoundError",
    "DimensionResult",
    "DimensionView",
    "DuplicateDimensionError",
    "Figures",
    "InactiveDimensionError",
    "InvalidDimensionError",
    "ResultRow",
    "SuggestedSet",
    "SuggestedValue",
    "ValueView",
    "add_suggested",
    "allocate",
    "allocated_ids",
    "create_dimension",
    "create_value",
    "delete_dimension",
    "delete_value",
    "find_kind",
    "get_dimension",
    "get_value",
    "list_dimensions",
    "resolve",
    "result",
    "suggestions",
    "update_dimension",
    "update_value",
    "validate_code",
    "validate_value",
    "without_label",
]
