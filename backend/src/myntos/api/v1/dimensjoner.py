"""Dimensions (avdelinger, prosjekter, driftsgrener) and the result per value.

Reading needs ``ledger:read``. Every change needs ``ledger:chart``, like the
kontoplan, because a dimension is how the books are set up rather than an entry
in them: a new avdeling changes what every colleague is offered, and switching
one off changes what they may post to. Choosing a value on an entry is
``ledger:write`` and happens on the register's own routes
(``dimension_value_ids``). The rules are in
:mod:`myntos.domain.ledger.dimensions`; this module maps HTTP onto them.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, Field

from myntos.api.deps import KEY_READ, CurrentCompany, DbSession, key_write
from myntos.core.clock import utc_now
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.ledger import dimensions, driftsgrener, landbruk
from myntos.domain.ledger.dimensions import DimensionView, Figures, ResultRow

router = APIRouter(tags=["dimensjoner"])


class DimensionValueResponse(BaseModel):
    id: int
    #: The code (SAF-T ``AnalysisID``): «6110», «20», «P-104». Never changes.
    value: str
    description: str
    is_active: bool
    #: How many posted lines carry it. Above zero, it can be switched off but
    #: never deleted.
    lines: int


class DimensionResponse(BaseModel):
    id: int
    #: The code (SAF-T ``AnalysisType``): ``AVDELING``, ``PROSJEKT``,
    #: ``DRIFTSGR``, or the company's own. Never changes.
    code: str
    description: str
    is_active: bool
    #: ``AVDELING``, ``PROSJEKT``, ``DRIFTSGR`` or ``PRODUKT`` when Myntos
    #: recognises the dimension as one of those, by code or by name.
    kind: str | None
    #: What the result report calls lines with no value: «Uten driftsgren».
    without_label: str
    values: list[DimensionValueResponse]


class CreateDimensionRequest(BaseModel):
    code: str = Field(min_length=1, max_length=dimensions.MAX_CODE)
    description: str = Field(min_length=1, max_length=dimensions.MAX_DESCRIPTION)


class UpdateDimensionRequest(BaseModel):
    description: str | None = Field(
        default=None, min_length=1, max_length=dimensions.MAX_DESCRIPTION
    )
    is_active: bool | None = None


class CreateValueRequest(BaseModel):
    value: str = Field(min_length=1, max_length=dimensions.MAX_VALUE)
    description: str = Field(min_length=1, max_length=dimensions.MAX_DESCRIPTION)


class SuggestedValueResponse(BaseModel):
    code: str
    name: str
    #: NIBIO's heading the code sits under («Husdyrproduksjon»).
    group: str
    #: The company already has it.
    added: bool


class SuggestedSetResponse(BaseModel):
    #: The dimension's code in Myntos, and what to send as ``kind``.
    kind: str
    description: str
    #: The company's dimension of this kind, if it has one yet.
    dimension_id: int | None
    values: list[SuggestedValueResponse]


class SuggestionsResponse(BaseModel):
    #: Whether the company has the farming add-on. Without it, ``sets`` is empty.
    landbruk: bool
    source: str | None
    sets: list[SuggestedSetResponse]


class AddSuggestedRequest(BaseModel):
    #: ``DRIFTSGR`` for NIBIO's driftsgrener, ``AVDELING`` for its avdelinger.
    kind: str = Field(min_length=1, max_length=dimensions.MAX_CODE)
    codes: list[str] = Field(min_length=1, max_length=100)


class FiguresResponse(BaseModel):
    income: Decimal
    costs: Decimal
    result: Decimal


class MonthFiguresResponse(FiguresResponse):
    month: int


class ResultRowResponse(BaseModel):
    #: Null on the row of lines with no value, and on the total.
    value_id: int | None
    value: str | None
    description: str
    is_active: bool
    total: FiguresResponse
    months: list[MonthFiguresResponse]


class DimensionResultResponse(BaseModel):
    year: int
    dimension: DimensionResponse
    rows: list[ResultRowResponse]
    #: Everything no value carries: «Uten driftsgren».
    unallocated: ResultRowResponse
    total: ResultRowResponse
    #: Lines a file allocated for part of their amount only; the rest is in
    #: ``unallocated``.
    partly_allocated: int


def _dimension(view: DimensionView) -> DimensionResponse:
    return DimensionResponse(
        id=view.id,
        code=view.code,
        description=view.description,
        is_active=view.is_active,
        kind=view.kind,
        without_label=view.without_label,
        values=[
            DimensionValueResponse(
                id=value.id,
                value=value.value,
                description=value.description,
                is_active=value.is_active,
                lines=value.lines,
            )
            for value in view.values
        ],
    )


async def _all(db: DbSession, context: CurrentCompany) -> list[DimensionResponse]:
    views = await dimensions.list_dimensions(db, company=context.company)
    return [_dimension(view) for view in views]


def _figures(figures: Figures) -> FiguresResponse:
    return FiguresResponse(income=figures.income, costs=figures.costs, result=figures.result)


def _row(row: ResultRow) -> ResultRowResponse:
    return ResultRowResponse(
        value_id=row.value_id,
        value=row.value,
        description=row.description,
        is_active=row.is_active,
        total=_figures(row.total),
        months=[
            MonthFiguresResponse(
                month=month,
                income=figures.income,
                costs=figures.costs,
                result=figures.result,
            )
            for month, figures in enumerate(row.months, start=1)
        ],
    )


@router.get("/companies/{company_id}/dimensjoner", openapi_extra=KEY_READ)
async def list_dimensions(context: CurrentCompany, db: DbSession) -> list[DimensionResponse]:
    """The company's dimensions (dimensjoner) and every value in each.

    A dimension is a second way to sort the books, beside the account. The
    account says what an amount was (feed, milk, interest); a dimension says
    which part of the business it belongs to. The usual ones are an
    «avdeling» (department), a «prosjekt» (project), and on a farm a
    «driftsgren»: one line of production, such as milk from cows (NIBIO code
    6110), sheep (6300) or the forest (7000), whose result the farm wants to
    see on its own. In SAF-T a dimension is an ``AnalysisType`` and a value
    an ``AnalysisID``.

    Each dimension has a ``code`` (SAF-T's analysis type, at most nine
    characters, never changed), a ``description``, ``is_active`` (a
    switched-off dimension is offered for nothing new), and its ``values``,
    each with its ``id`` (what a register entry sends in
    ``dimension_value_ids``), its code ``value``, ``description``,
    ``is_active`` and ``lines``, the number of posted lines carrying it.

    Switched-off dimensions and values are listed too, because posted lines
    keep them; offer only active ones for new entries. Every role may read
    this, and every key.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    return await _all(db, context)


@router.get("/companies/{company_id}/dimensjoner/forslag", openapi_extra=KEY_READ)
async def suggested(context: CurrentCompany, db: DbSession) -> SuggestionsResponse:
    """NIBIO's driftsgrener and avdelinger for a farm, ready to add. Changes nothing.

    Farms in Norway keep their books on NIBIO's «Landbrukets kodesystem»,
    which every farm accountant and farm program uses. When the company has
    the farming add-on (``GET …/landbruk``), this answers with two lists:
    ``DRIFTSGR``, NIBIO's driftsgrein codes at the level a farm reports its
    result on (1100 Kornproduksjon … 6110 Melkeproduksjon storfe, 6300
    Saueproduksjon … 7000 Skogbruksproduksjon), and ``AVDELING``, NIBIO's
    departments (10 Finans, 20 Jordbruk, 30 Skogbruk, 50 Pelsdyr, 60 Annen
    næring, 90 Privat). ``added`` marks a code the company already has.
    Without the add-on ``sets`` is empty: a company that is not a farm is
    offered nothing of the kind.

    Add the ones the farm has with ``POST …/dimensjoner/forslag``. Needs
    ``ledger:read``.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    sets = await dimensions.suggestions(db, company=context.company)
    return SuggestionsResponse(
        landbruk=landbruk.is_enabled(context.company),
        source=driftsgrener.SOURCE if sets else None,
        sets=[
            SuggestedSetResponse(
                kind=item.kind,
                description=item.description,
                dimension_id=item.dimension_id,
                values=[
                    SuggestedValueResponse(
                        code=value.code, name=value.name, group=value.group, added=value.added
                    )
                    for value in item.values
                ],
            )
            for item in sets
        ],
    )


@router.post(
    "/companies/{company_id}/dimensjoner/forslag",
    openapi_extra=key_write("legge til driftsgrener eller avdelinger fra NIBIOs liste"),
)
async def add_suggested(
    payload: AddSuggestedRequest, context: CurrentCompany, db: DbSession
) -> list[DimensionResponse]:
    """Add some of NIBIO's driftsgrener or avdelinger to a farm's books.

    Answers with every dimension, as ``GET …/dimensjoner`` does.

    ``kind`` is ``DRIFTSGR`` or ``AVDELING``, and ``codes`` the codes to add
    from that list in ``GET …/dimensjoner/forslag`` (``["6110", "6300"]``
    for milk and sheep). The dimension is made the first time, under Myntos's
    code for it, unless the company already has one of that kind (a dimension
    named «Avdeling» or «Driftsgren» counts). Each value gets NIBIO's name,
    which may be changed afterwards. A code already there is left as it is,
    and switched on again if it was off, so asking twice does nothing more.

    Refuses a company without the farming add-on (409
    ``segment_not_enabled``: switch it on under the kontoplan, with ``PUT
    …/landbruk``), and a ``kind`` or a code not in NIBIO's list (422
    ``invalid_dimension``). Needs ``ledger:chart`` (administrators and
    owners; 403 ``permission_denied`` otherwise). Undoable: a value nothing
    is posted to can be deleted again.
    """
    require_permission(context.membership.role, Permission.LEDGER_CHART)
    await dimensions.add_suggested(
        db, company=context.company, kind=payload.kind.strip().upper(), codes=payload.codes
    )
    return await _all(db, context)


@router.post(
    "/companies/{company_id}/dimensjoner",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_write("legge til en dimensjon, som avdeling, prosjekt eller driftsgren"),
)
async def create_dimension(
    payload: CreateDimensionRequest, context: CurrentCompany, db: DbSession
) -> list[DimensionResponse]:
    """Add a dimension, with no values yet. Answers with every dimension.

    ``code`` is the dimension's code in the SAF-T file: one to nine letters,
    digits, ``-`` or ``_``, upper-cased, and never changed afterwards. Use
    ``AVDELING`` for departments, ``PROSJEKT`` for projects and ``DRIFTSGR``
    for a farm's driftsgrener, which is what Myntos itself uses for them, or a
    code of the company's own. ``description`` is the name people see
    («Avdeling», «Prosjekt»). Then add values with ``POST
    …/dimensjoner/{dimension_id}/verdier``. A farm can instead take NIBIO's
    codes with ``POST …/dimensjoner/forslag``.

    Refuses a code or name the company already has (409
    ``duplicate_dimension``), and a code SAF-T cannot carry (422
    ``invalid_dimension``). Needs ``ledger:chart`` (administrators and owners;
    403 ``permission_denied`` otherwise): a dimension changes what every
    colleague is offered on every entry. Undoable while nothing is posted to
    it: delete it again.
    """
    require_permission(context.membership.role, Permission.LEDGER_CHART)
    await dimensions.create_dimension(
        db, company=context.company, code=payload.code, description=payload.description
    )
    return await _all(db, context)


@router.patch(
    "/companies/{company_id}/dimensjoner/{dimension_id}",
    openapi_extra=key_write("endre en dimensjon"),
)
async def update_dimension(
    dimension_id: int, payload: UpdateDimensionRequest, context: CurrentCompany, db: DbSession
) -> list[DimensionResponse]:
    """Rename a dimension, or switch it off or on. Answers with every dimension.

    Send ``description`` to rename, ``is_active`` to switch off (``false``)
    or on (``true``), or both. The code never changes. Switched off, none of
    its values is offered or accepted on a new entry (409
    ``inactive_dimension`` from the register), while every line already
    carrying one keeps it and the report still shows it.

    Refuses a name another dimension has (409 ``duplicate_dimension``), and
    a dimension that is not this company's (404 ``dimension_not_found``).
    Needs ``ledger:chart`` (administrators and owners).
    """
    require_permission(context.membership.role, Permission.LEDGER_CHART)
    await dimensions.update_dimension(
        db,
        company=context.company,
        dimension_pk=dimension_id,
        description=payload.description,
        is_active=payload.is_active,
    )
    return await _all(db, context)


@router.delete(
    "/companies/{company_id}/dimensjoner/{dimension_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=key_write("slette en dimensjon som aldri er brukt"),
)
async def delete_dimension(dimension_id: int, context: CurrentCompany, db: DbSession) -> Response:
    """Delete a dimension added by mistake, with its values. Answers 204.

    Only when no posted line carries any of its values. A posted voucher is
    history (bokføringsloven), and its lines keep their dimensions for good,
    so a dimension that was used is switched off instead (``PATCH
    …/dimensjoner/{dimension_id}`` with ``is_active: false``).

    Refuses one in use (409 ``dimension_in_use``) and one that is not this
    company's (404 ``dimension_not_found``). Needs ``ledger:chart``.
    """
    require_permission(context.membership.role, Permission.LEDGER_CHART)
    await dimensions.delete_dimension(db, company=context.company, dimension_pk=dimension_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/companies/{company_id}/dimensjoner/{dimension_id}/verdier",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_write("legge til en avdeling, et prosjekt eller en driftsgren"),
)
async def create_value(
    dimension_id: int, payload: CreateValueRequest, context: CurrentCompany, db: DbSession
) -> list[DimensionResponse]:
    """Add a value to a dimension: an avdeling, a prosjekt, a driftsgren.

    Answers with every dimension, as ``GET …/dimensjoner`` does.

    ``value`` is its code, at most 35 characters, never changed afterwards
    (NIBIO's «6110», a department number «20», a project number «P-104»);
    ``description`` is its name («Melkeproduksjon storfe»). The answer's
    value ``id`` is what a register entry sends in ``dimension_value_ids``.

    Refuses a code the dimension already has, perhaps switched off (409
    ``duplicate_dimension``: switch it on with ``PATCH
    …/dimensjonsverdier/{dimension_value_id}``), a code longer than SAF-T
    takes (422 ``invalid_dimension``), and a dimension that is not this
    company's (404 ``dimension_not_found``). Needs ``ledger:chart``
    (administrators and owners). Undoable while nothing is posted to it.
    """
    require_permission(context.membership.role, Permission.LEDGER_CHART)
    await dimensions.create_value(
        db,
        company=context.company,
        dimension_pk=dimension_id,
        value=payload.value,
        description=payload.description,
    )
    return await _all(db, context)


@router.patch(
    "/companies/{company_id}/dimensjonsverdier/{dimension_value_id}",
    openapi_extra=key_write("endre en avdeling, et prosjekt eller en driftsgren"),
)
async def update_value(
    dimension_value_id: int,
    payload: UpdateDimensionRequest,
    context: CurrentCompany,
    db: DbSession,
) -> list[DimensionResponse]:
    """Rename a value, or switch it off or on. Answers with every dimension.

    Send ``description`` to rename, ``is_active`` to switch off (``false``,
    for a driftsgren the farm has given up or a project that is over) or on
    (``true``). The code never changes. Switched off, it is refused on new
    entries (409 ``inactive_dimension``) and kept on every line that has it.

    Refuses a value that is not this company's (404 ``dimension_not_found``).
    Needs ``ledger:chart`` (administrators and owners).
    """
    require_permission(context.membership.role, Permission.LEDGER_CHART)
    await dimensions.update_value(
        db,
        company=context.company,
        value_pk=dimension_value_id,
        description=payload.description,
        is_active=payload.is_active,
    )
    return await _all(db, context)


@router.delete(
    "/companies/{company_id}/dimensjonsverdier/{dimension_value_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=key_write(
        "slette en avdeling, et prosjekt eller en driftsgren som aldri er brukt"
    ),
)
async def delete_value(dimension_value_id: int, context: CurrentCompany, db: DbSession) -> Response:
    """Delete a value added by mistake, one no posted line carries. Answers 204.

    A value any line carries is part of a posted voucher and is switched off
    instead (``PATCH …/dimensjonsverdier/{dimension_value_id}`` with
    ``is_active: false``). ``lines`` in ``GET …/dimensjoner`` says which.

    Refuses one in use (409 ``dimension_in_use``) and one that is not this
    company's (404 ``dimension_not_found``). Needs ``ledger:chart``.
    """
    require_permission(context.membership.role, Permission.LEDGER_CHART)
    await dimensions.delete_value(db, company=context.company, value_pk=dimension_value_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/companies/{company_id}/dimensjoner/{dimension_id}/resultat", openapi_extra=KEY_READ)
async def dimension_result(
    dimension_id: int,
    context: CurrentCompany,
    db: DbSession,
    aar: int | None = Query(default=None, ge=2000, le=2100),
) -> DimensionResultResponse:
    """The result per value of one dimension: income, costs and result per avdeling or driftsgren.

    For one financial year ``aar`` (a calendar year; this year when left
    out), and per month. On a farm with the dimension «Driftsgren» this is
    the driftsgrenregnskap: what the milk production earned against what it
    cost, beside the sheep and the forest.

    ``rows`` has one row per value, with ``total`` and twelve ``months``,
    each ``income`` (inntekter: what the income accounts in classes 3 and 8
    were credited, less what they were debited), ``costs`` (kostnader: the
    same for the cost accounts) and ``result`` (``income`` less ``costs``).
    A switched-off value is listed only when it has figures in the year.
    ``unallocated`` is everything no value carries, named as the page names
    it («Uten driftsgren», «Uten avdeling»): invoices, entries made before the
    dimension existed, lines nobody marked. ``total`` is the year's result,
    and the rows and ``unallocated`` always add up to it.

    Every posted voucher counts: the register's entries, invoices, vouchers
    brought from the previous program, manual vouchers, and reversals, which
    cancel what they reverse. Left out: the opening balance, which touches no
    income or cost account, and the year-end closing (årsavslutning), whose
    tax cost and disposition of the result belong to no driftsgren.

    Amounts are decimal strings. Refuses a dimension that is not this
    company's (404 ``dimension_not_found``). Every role may read this, and
    every key.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    year = aar or utc_now().year
    found = await dimensions.result(
        db, company=context.company, dimension_pk=dimension_id, year=year
    )
    return DimensionResultResponse(
        year=found.year,
        dimension=_dimension(found.dimension),
        rows=[_row(row) for row in found.rows],
        unallocated=_row(found.unallocated),
        total=_row(found.total),
        partly_allocated=found.partly_allocated,
    )
