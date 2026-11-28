"""The datasets: every chart Myntos can draw from the books, by name.

A registry rather than a function per chart, because it has three readers:
the default Analyse page, which picks datasets in code; an agent, which
reads the list at ``GET …/analyse/datasett`` and asks for one; and later a
model choosing the charts for «Dyp KI-analyse», which is shown the same list
and may only pick from it (docs/analyse.md, «The chart pipeline»). So each
entry's ``description`` is written in English for an agent and a model, not
for a person, and names what the numbers are and are not.

A builder takes ``(db, company, params)`` and returns a validated
:class:`~myntos.domain.analyse.charts.Chart`; every number in it comes from
:mod:`.figures`. Nothing here asks a model.

WP2's rows need no KI. WP5b's (``*_per_hva``, ``kostnader_per_motpart``)
group the same lines by the tags KI stored on them, say so with
``needs_tags``, and are refused with KI off rather than drawn from tags the
company has switched off (docs/analyse.md rule 10). The stacked and
recurring ones (WP6) join the same tuple.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.analyse import figures
from myntos.domain.analyse.charts import (
    MAX_BAR_SLICES,
    MAX_DONUT_SLICES,
    MAX_STACKED_SERIES,
    Chart,
    ChartAxis,
    ChartSeries,
    ChartSlice,
    ChartSource,
    ChartView,
)
from myntos.domain.analyse.errors import ChartRequestError, UnknownDatasetError
from myntos.domain.analyse.figures import Books, Part, Period, Side, Totals
from myntos.domain.ki.errors import KiTurnedOffError
from myntos.domain.ledger.models import Company

#: The slice the parts past the top N are summed into.
OTHER_KEY = "andre"
OTHER_LABEL = "Andre"

TOPP_DEFAULT = 5
TOPP_MIN = 2
TOPP_MAX = 10

#: Shares are sent to four places (``0.3812``): enough for a table to show
#: 38,1 % and a small slice not to read as nothing, while the rounding the
#: «Andre» slice absorbs stays far below anything a person could see.
_SHARE = Decimal("0.0001")

#: The comparison series' legend when the period is not within one year,
#: where no year number names it.
SAMMENLIGNING_LABEL = "Samme tid året før"

#: Under every split by ``hva``: what the slices are, and that KI read them,
#: so nobody takes them for accounts (docs/analyse.md, point 6).
HVA_CAPTION = "Fordelt etter hva bilagene gjelder, lest av KI."
COUNTERPARTY_CAPTION = "Motparten fra regnskapet, eller lest av KI der bilaget ikke har noen."


@dataclass(frozen=True)
class Params:
    """What a dataset is asked for. ``view`` None is the dataset's first view."""

    periode: Period
    sammenlign: bool = True
    topp: int = TOPP_DEFAULT
    view: ChartView | None = None

    def as_strings(self, *, with_topp: bool) -> dict[str, str]:
        """The parameters as the chart's ``dataset.params`` records them."""
        found = {
            "fra": self.periode.fra.isoformat(),
            "til": self.periode.til.isoformat(),
            "sammenlign": "true" if self.sammenlign else "false",
        }
        if with_topp:
            found["topp"] = str(self.topp)
        return found


ParameterKind = Literal["date", "boolean", "integer"]


@dataclass(frozen=True)
class Parameter:
    name: str
    kind: ParameterKind
    description: str
    default: str | None = None
    minimum: int | None = None
    maximum: int | None = None


_FRA = Parameter(
    "fra",
    "date",
    "First day of the period, ISO date. Default: 1 January of the year `til` is in.",
)
_TIL = Parameter(
    "til",
    "date",
    "Last day of the period, included, ISO date. Default: today in Norway. "
    "At most 60 months from `fra`.",
)
_SAMMENLIGN = Parameter(
    "sammenlign",
    "boolean",
    "Also give the same dates one year earlier (29 February becomes 28 February), "
    "when the books cover every month of them; otherwise there is no comparison.",
    default="true",
)
_TOPP = Parameter(
    "topp",
    "integer",
    "How many of the largest parts to name; the rest are summed as «Andre». "
    "At most 5 for `smultring` and 9 for `stolper`, so the chart stays readable.",
    default=str(TOPP_DEFAULT),
    minimum=TOPP_MIN,
    maximum=TOPP_MAX,
)

_OVER_TIME = (_FRA, _TIL, _SAMMENLIGN)
_SPLIT = (_FRA, _TIL, _TOPP)

Builder = Callable[[AsyncSession, Company, Params], Awaitable[Chart]]


@dataclass(frozen=True)
class Dataset:
    name: str
    #: Bokmål, the chart's title: at most 60 characters.
    label: str
    #: English, for an agent or a model choosing a chart.
    description: str
    parameters: tuple[Parameter, ...]
    views: tuple[ChartView, ...]
    builder: Builder
    #: Whether it groups by KI's tags (WP5b and later); none of WP2's do.
    needs_tags: bool = False

    async def chart(self, db: AsyncSession, company: Company, params: Params) -> Chart:
        """The dataset drawn as ``params.view``, or refused when it cannot be drawn so.

        One that groups by tags is refused with KI off (``409 ki_turned_off``),
        not drawn with every line unread: «Ikke lest ennå» would promise a
        reading that will not happen, and the tags stored before KI was
        switched off are not used (rule 10).
        """
        if self.needs_tags and not company.ki_enabled:
            raise KiTurnedOffError(
                f"{self.name} er fordelt etter det KI har lest, og KI er slått av for dette "
                "selskapet"
            )
        view = params.view or self.views[0]
        if view not in self.views:
            raise ChartRequestError(
                f"{self.name} kan vises som {', '.join(self.views)}, ikke som {view}"
            )
        return await self.builder(db, company, replace(params, view=view))


# --- Over time -----------------------------------------------------------------


def _comparison(params: Params, books: Books) -> Period | None:
    if not params.sammenlign:
        return None
    before = params.periode.year_before()
    return before if books.cover(before) else None


def _values(
    months: list[figures.Month], rows: dict[figures.Month, Totals], books: Books, side: Side | None
) -> list[Decimal | None]:
    """One value per month, ``None`` where the company had no books (see :class:`Books`)."""
    found: list[Decimal | None] = []
    for month in months:
        if not books.has(month):
            found.append(None)
            continue
        totals = rows.get(month, Totals())
        found.append(totals.resultat if side is None else totals.of(side))
    return found


def _main_label(chosen: Period) -> str:
    if chosen.fra.year == chosen.til.year:
        return str(chosen.fra.year)
    return "Valgt periode"


def _comparison_label(chosen: Period, *, prefix: str | None = None) -> str:
    """«2025» beside «2026»: the legend names the years when one year names each.

    Not «Samme tid i fjor», because the period may be last year itself, and
    «i fjor» of last year is the year before that.
    """
    if chosen.fra.year == chosen.til.year:
        year = str(chosen.fra.year - 1)
        return f"{prefix} {year}" if prefix else year
    return f"{prefix}, {SAMMENLIGNING_LABEL.lower()}" if prefix else SAMMENLIGNING_LABEL


async def _months(
    db: AsyncSession, company: Company, params: Params, *, name: str, title: str, side: Side
) -> Chart:
    chosen = params.periode
    books = await figures.books(db, company=company)
    rows = await figures.per_month(db, company=company, chosen=chosen)
    months = chosen.months()
    series = [
        ChartSeries(
            key=side.value,
            label=_main_label(chosen),
            role="hoved",
            values=_values(months, rows, books, side),
        )
    ]
    before = _comparison(params, books)
    if before is not None:
        earlier = await figures.per_month(db, company=company, chosen=before)
        series.append(
            ChartSeries(
                key=f"{side.value}_forrige",
                label=_comparison_label(chosen),
                role="sammenligning",
                values=_values(before.months(), earlier, books, side),
            )
        )
    return Chart(
        id=name,
        view=params.view or "kolonner",
        title=title,
        unit="kr",
        x=ChartAxis(kind="maned", labels=[figures.month_label(m) for m in months]),
        series=series,
        total=sum((t.of(side) for t in rows.values()), figures.ZERO),
        dataset=ChartSource(name=name, params=params.as_strings(with_topp=False)),
    )


async def _income_per_month(db: AsyncSession, company: Company, params: Params) -> Chart:
    return await _months(
        db,
        company,
        params,
        name="inntekter_per_maned",
        title="Inntekter per måned",
        side=Side.INNTEKTER,
    )


async def _costs_per_month(db: AsyncSession, company: Company, params: Params) -> Chart:
    return await _months(
        db,
        company,
        params,
        name="kostnader_per_maned",
        title="Kostnader per måned",
        side=Side.KOSTNADER,
    )


async def _result_per_month(db: AsyncSession, company: Company, params: Params) -> Chart:
    """Income, costs and result side by side; the comparison as the result only.

    Three series are the chart; a comparison of all three would be six, past
    the four a time chart may have, and the result is what the comparison is
    asked for.
    """
    name = "resultat_per_maned"
    chosen = params.periode
    books = await figures.books(db, company=company)
    rows = await figures.per_month(db, company=company, chosen=chosen)
    months = chosen.months()
    series = [
        ChartSeries(
            key=side.value,
            label=label,
            role="serie",
            values=_values(months, rows, books, side),
        )
        for side, label in ((Side.INNTEKTER, "Inntekter"), (Side.KOSTNADER, "Kostnader"))
    ]
    series.append(
        ChartSeries(
            key="resultat",
            label="Resultat",
            role="serie",
            values=_values(months, rows, books, None),
        )
    )
    before = _comparison(params, books)
    if before is not None:
        earlier = await figures.per_month(db, company=company, chosen=before)
        series.append(
            ChartSeries(
                key="resultat_forrige",
                label=_comparison_label(chosen, prefix="Resultat"),
                role="sammenligning",
                values=_values(before.months(), earlier, books, None),
            )
        )
    return Chart(
        id=name,
        view=params.view or "kolonner",
        title="Resultat per måned",
        unit="kr",
        x=ChartAxis(kind="maned", labels=[figures.month_label(m) for m in months]),
        series=series,
        total=sum((t.resultat for t in rows.values()), figures.ZERO),
        dataset=ChartSource(name=name, params=params.as_strings(with_topp=False)),
    )


# --- Splits ----------------------------------------------------------------------


def top_slices(parts: list[Part], topp: int, *, apart: Part | None = None) -> list[ChartSlice]:
    """The ``topp`` largest parts by name, the rest as «Andre», with shares summing to one.

    Parts that came to zero are left out: an account whose entries were all
    reversed is not a slice. When only one part would go into «Andre» it is
    named instead, since a slice called «Andre» hiding a single account
    hides a name for nothing.

    ``apart`` is a part that is never folded into «Andre» and goes last: «Ikke
    lest ennå» on a split by ``hva`` (D5). It takes one of the ``topp``
    places, so the chart has no more slices than without it (WP5b).

    The shares are rounded to four places and «Andre» takes up what the
    rounding left over, so they sum to exactly one (docs/analyse.md). With
    no «Andre», the largest part does, as it does too in the rare case where
    «Andre» is so small that taking the rounding would turn it negative. A
    whole of zero has no shares: every share is zero.
    """
    kept = [part for part in parts if part.amount != 0]
    named = topp
    if apart is not None and apart.amount != 0:
        named = topp - 1
    if len(kept) > named + 1:
        rest = kept[named:]
        kept = [
            *kept[:named],
            Part(OTHER_KEY, OTHER_LABEL, sum((p.amount for p in rest), figures.ZERO)),
        ]
    if apart is not None and apart.amount != 0:
        kept.append(apart)
    whole = sum((part.amount for part in kept), figures.ZERO)
    if not kept:
        return []
    if whole == 0:
        shares = [Decimal("0.0000") for _ in kept]
    else:
        shares = [(part.amount / whole).quantize(_SHARE, rounding=ROUND_HALF_UP) for part in kept]
        left = Decimal(1) - sum(shares, Decimal(0))
        # The first of the largest: «apart» may be larger than any named part.
        largest = max(range(len(kept)), key=lambda index: kept[index].amount)
        keys = [part.key for part in kept]
        takes = keys.index(OTHER_KEY) if OTHER_KEY in keys else largest
        if shares[takes] + left < 0:
            takes = largest
        shares[takes] += left
    return [
        ChartSlice(key=part.key, label=part.label, value=part.amount, share=share)
        for part, share in zip(kept, shares, strict=True)
    ]


def _drawable_as_donut(slices: list[ChartSlice]) -> bool:
    return all(s.value >= 0 for s in slices) and (
        not slices or sum((s.value for s in slices), figures.ZERO) > 0
    )


def _split_chart(
    params: Params,
    *,
    name: str,
    title: str,
    parts: list[Part],
    apart: Part | None = None,
    caption: str | None = None,
) -> Chart:
    """A split as the view asked for, within the view's limit on slices.

    A donut that would have a negative slice (a cost account in credit, more
    refunds than purchases in a category) cannot be drawn as parts of a
    whole, so it is sent as bars, which can: ``view`` then says ``stolper``.
    Every split dataset that offers a donut offers bars too. ``apart`` is
    :func:`top_slices`' slice that is never «Andre»; ``total`` counts it.
    """
    view = params.view or "smultring"
    limit = {"smultring": MAX_DONUT_SLICES, "stolper": MAX_BAR_SLICES}.get(view)
    if limit is not None and params.topp + 1 > limit:
        raise ChartRequestError(
            f"{view} kan ha høyst {limit} deler, så topp kan være høyst {limit - 1}"
        )
    slices = top_slices(parts, params.topp, apart=apart)
    if view == "smultring" and not _drawable_as_donut(slices):
        view = "stolper"
    whole = sum((part.amount for part in parts), figures.ZERO)
    return Chart(
        id=name,
        view=view,
        title=title,
        caption=caption,
        unit="kr",
        slices=slices,
        total=whole + apart.amount if apart is not None else whole,
        dataset=ChartSource(name=name, params=params.as_strings(with_topp=True)),
    )


def _split(
    name: str,
    title: str,
    side: Side,
    reader: Callable[..., Awaitable[list[Part]]],
) -> Builder:
    async def build(db: AsyncSession, company: Company, params: Params) -> Chart:
        parts = await reader(db, company=company, chosen=params.periode, side=side)
        return _split_chart(params, name=name, title=title, parts=parts)

    return build


def _hva_split(name: str, title: str, side: Side) -> Builder:
    async def build(db: AsyncSession, company: Company, params: Params) -> Chart:
        reading = await figures.per_hva(db, company=company, chosen=params.periode, side=side)
        return _split_chart(
            params,
            name=name,
            title=title,
            parts=reading.parts,
            apart=reading.unread_part,
            caption=HVA_CAPTION,
        )

    return build


async def _costs_per_counterparty(db: AsyncSession, company: Company, params: Params) -> Chart:
    parts = await figures.per_counterparty(db, company=company, chosen=params.periode)
    return _split_chart(
        params,
        name="kostnader_per_motpart",
        title="Kostnader per motpart",
        parts=parts,
        caption=COUNTERPARTY_CAPTION,
    )


RECURRING_CAPTION = "Samme motpart med omtrent likt beløp i minst tre måneder på rad."


async def _hva_per_month(db: AsyncSession, company: Company, params: Params) -> Chart:
    """The largest cost ``hva`` categories stacked per month (WP6)."""
    if params.topp > MAX_STACKED_SERIES - 1:
        raise ChartRequestError(
            f"hva_per_maned kan ha høyst {MAX_STACKED_SERIES} serier, "
            f"så topp kan være høyst {MAX_STACKED_SERIES - 1}"
        )
    chosen = params.periode
    books = await figures.books(db, company=company)
    monthly = await figures.per_hva_per_month(
        db, company=company, chosen=chosen, side=Side.KOSTNADER
    )
    months = chosen.months()
    kept = [part for part in monthly.parts if part.amount != 0]
    has_unread = any(v != 0 for v in monthly.unread_by_month.values())
    named_limit = params.topp - 1 if has_unread else params.topp
    if len(kept) > named_limit + 1:
        top_parts = kept[:named_limit]
        other_slugs = {part.key for part in kept[named_limit:]}
    else:
        top_parts = kept
        other_slugs = set()

    series: list[ChartSeries] = []
    for part in top_parts:
        per_m = monthly.by_month.get(part.key, {})
        series.append(
            ChartSeries(
                key=part.key,
                label=part.label,
                role="serie",
                values=[per_m.get(m, figures.ZERO) if books.has(m) else None for m in months],
            )
        )
    if other_slugs:
        series.append(
            ChartSeries(
                key=OTHER_KEY,
                label=OTHER_LABEL,
                role="serie",
                values=[
                    sum(
                        (
                            monthly.by_month.get(slug, {}).get(m, figures.ZERO)
                            for slug in other_slugs
                        ),
                        figures.ZERO,
                    )
                    if books.has(m)
                    else None
                    for m in months
                ],
            )
        )
    if has_unread:
        series.append(
            ChartSeries(
                key=figures.UNREAD_KEY,
                label=figures.UNREAD_LABEL,
                role="serie",
                values=[
                    monthly.unread_by_month.get(m, figures.ZERO) if books.has(m) else None
                    for m in months
                ],
            )
        )
    if not series:
        series.append(
            ChartSeries(
                key="kostnader",
                label="Kostnader",
                role="serie",
                values=[figures.ZERO if books.has(m) else None for m in months],
            )
        )
    whole = sum((part.amount for part in monthly.parts), monthly.unread_total)
    return Chart(
        id="hva_per_maned",
        view="kolonner",
        title="Kostnader per måned etter hva de gjelder",
        caption=HVA_CAPTION,
        unit="kr",
        x=ChartAxis(kind="maned", labels=[figures.month_label(m) for m in months]),
        series=series,
        total=whole,
        stacked=True,
        dataset=ChartSource(name="hva_per_maned", params=params.as_strings(with_topp=True)),
    )


async def _recurring_costs(db: AsyncSession, company: Company, params: Params) -> Chart:
    """Counterparties billed roughly the same amount for 3+ consecutive months (WP6)."""
    found = await figures.recurring_costs(db, company=company, chosen=params.periode)
    labels = [f"{item.label} ({item.months} mnd.)" for item in found]
    series = [
        ChartSeries(
            key="snitt",
            label="Per måned",
            role="hoved",
            values=[item.average for item in found],
        ),
        ChartSeries(
            key="totalt",
            label="Totalt",
            role="serie",
            values=[item.total for item in found],
        ),
    ]
    return Chart(
        id="faste_kostnader",
        view="tabell",
        title="Faste kostnader",
        caption=RECURRING_CAPTION,
        unit="kr",
        x=ChartAxis(kind="kategori", labels=labels),
        series=series,
        total=sum((item.total for item in found), figures.ZERO),
        dataset=ChartSource(
            name="faste_kostnader",
            params={
                "fra": params.periode.fra.isoformat(),
                "til": params.periode.til.isoformat(),
            },
        ),
    )


# --- The registry ----------------------------------------------------------------

_DONUT_VIEWS: tuple[ChartView, ...] = ("smultring", "stolper", "tabell")
_BAR_VIEWS: tuple[ChartView, ...] = ("stolper", "tabell")
_TIME_VIEWS: tuple[ChartView, ...] = ("kolonner", "linje")

_BASIS = (
    "Accounting basis, like the income statement: posted lines on income and cost accounts "
    "by voucher date, the opening balance and the year-end closing left out, tax cost and "
    "disposition (accounts 83-89) left out, amounts excluding VAT."
)

_TAGS = (
    "Only with the company's AI switched on; otherwise refused with 409 ki_turned_off. "
    "The amounts are the books', to the øre; the AI only chose which slice each line goes in, "
    "reading the vouchers in the background at the free level (see the ki object of "
    "GET …/analyse)."
)

DATASETS: tuple[Dataset, ...] = (
    Dataset(
        name="inntekter_per_maned",
        label="Inntekter per måned",
        description=(
            "Income per calendar month in the period, with the same months a year earlier "
            "as a second series when the books cover them. A month before the company's "
            f"first voucher or after its last is null, not zero. {_BASIS}"
        ),
        parameters=_OVER_TIME,
        views=_TIME_VIEWS,
        builder=_income_per_month,
    ),
    Dataset(
        name="kostnader_per_maned",
        label="Kostnader per måned",
        description=(
            "Costs per calendar month in the period, with the same months a year earlier "
            "as a second series when the books cover them. A month before the company's "
            f"first voucher or after its last is null, not zero. {_BASIS}"
        ),
        parameters=_OVER_TIME,
        views=_TIME_VIEWS,
        builder=_costs_per_month,
    ),
    Dataset(
        name="resultat_per_maned",
        label="Resultat per måned",
        description=(
            "Income, costs and the result before tax per calendar month, as three series, "
            "plus the result for the same months a year earlier when the books cover them. "
            f"A negative result is a loss that month. {_BASIS}"
        ),
        parameters=_OVER_TIME,
        views=_TIME_VIEWS,
        builder=_result_per_month,
    ),
    Dataset(
        name="inntekter_per_konto",
        label="Inntekter per konto",
        description=(
            "Income split by the account it was booked on (the Norwegian standard chart, "
            "NS 4102: 3000 is sales with VAT, 3100 without), the largest named and the rest "
            "as «Andre». The key of each slice is the account number. Exact and needs no AI: "
            f"the account is what the person chose when they booked. {_BASIS}"
        ),
        parameters=_SPLIT,
        views=_DONUT_VIEWS,
        builder=_split(
            "inntekter_per_konto", "Inntekter per konto", Side.INNTEKTER, figures.per_account
        ),
    ),
    Dataset(
        name="kostnader_per_konto",
        label="Kostnader per konto",
        description=(
            "Costs split by the account they were booked on (NS 4102: 4000 goods for resale, "
            "5000 wages, 6300 rent, 6800 office supplies, 7140 travel), the largest named and "
            "the rest as «Andre». The key of each slice is the account number. Exact and needs "
            f"no AI. {_BASIS}"
        ),
        parameters=_SPLIT,
        views=_DONUT_VIEWS,
        builder=_split(
            "kostnader_per_konto", "Kostnader per konto", Side.KOSTNADER, figures.per_account
        ),
    ),
    Dataset(
        name="inntekter_per_gruppe",
        label="Inntekter per gruppe",
        description=(
            "Income split by the groups of the income statement: sales (salgsinntekter, "
            "30-32), other operating income (33-39) and financial income (80-82). The same "
            f"groups as the regnskap endpoint. {_BASIS}"
        ),
        parameters=_SPLIT,
        views=_DONUT_VIEWS,
        builder=_split(
            "inntekter_per_gruppe", "Inntekter per gruppe", Side.INNTEKTER, figures.per_group
        ),
    ),
    Dataset(
        name="kostnader_per_gruppe",
        label="Kostnader per gruppe",
        description=(
            "Costs split by the groups of the income statement: cost of goods (varekostnad, "
            "40-49), payroll (50-59), depreciation (60), other operating costs (61-79) and "
            f"financial costs (81-82). The same groups as the regnskap endpoint. {_BASIS}"
        ),
        parameters=_SPLIT,
        views=_DONUT_VIEWS,
        builder=_split(
            "kostnader_per_gruppe", "Kostnader per gruppe", Side.KOSTNADER, figures.per_group
        ),
    ),
    Dataset(
        name="inntekter_per_kunde",
        label="Inntekter per kunde",
        description=(
            "Income split by the customer on the income line, the largest named and the rest "
            "as «Andre». A line with no customer (a cash sale entered without one, a voucher "
            "from a previous program that kept no customer ledger) is «Uten motpart». The key "
            f"is the party's id in Myntos. {_BASIS}"
        ),
        parameters=_SPLIT,
        views=_BAR_VIEWS,
        builder=_split(
            "inntekter_per_kunde", "Inntekter per kunde", Side.INNTEKTER, figures.per_party
        ),
    ),
    Dataset(
        name="kostnader_per_leverandor",
        label="Kostnader per leverandør",
        description=(
            "Costs split by the supplier on the cost line, the largest named and the rest as "
            "«Andre». A line with no supplier is «Uten motpart». The key is the party's id in "
            f"Myntos. {_BASIS}"
        ),
        parameters=_SPLIT,
        views=_BAR_VIEWS,
        builder=_split(
            "kostnader_per_leverandor",
            "Kostnader per leverandør",
            Side.KOSTNADER,
            figures.per_party,
        ),
    ),
    Dataset(
        name="kostnader_per_kategori",
        label="Kostnader per kategori",
        description=(
            "Costs split by the category the person chose in Myntos's cash-flow register "
            "(«Kontorrekvisita», «Reise», «Varekjøp»), each line of a composite voucher by its "
            "own. A refund counts against the category it refunds. Invoices, manual vouchers "
            "and vouchers from a previous program have no category and are «Uten kategori». "
            f"The key is the category's code. {_BASIS}"
        ),
        parameters=_SPLIT,
        views=("smultring", "stolper"),
        builder=_split(
            "kostnader_per_kategori",
            "Kostnader per kategori",
            Side.KOSTNADER,
            figures.per_category,
        ),
    ),
    Dataset(
        name="inntekter_per_hva",
        label="Hva inntektene kom fra",
        description=(
            "Income split by what each line is for, as Myntos's AI read the vouchers "
            "(«Salg av varer», «Salg av tjenester», «Leieinntekter»): finer than the "
            "account, and in the company's own words. The largest named, the rest as "
            "«Andre», and lines the AI has not read yet as their own slice «Ikke lest ennå» "
            "(key ikke_lest), which is never part of «Andre» and takes one of the `topp` "
            "places. The key of each other slice "
            f"is the tag's slug. {_TAGS} {_BASIS}"
        ),
        parameters=_SPLIT,
        views=_DONUT_VIEWS,
        builder=_hva_split("inntekter_per_hva", "Hva inntektene kom fra", Side.INNTEKTER),
        needs_tags=True,
    ),
    Dataset(
        name="kostnader_per_hva",
        label="Hva kostnadene gikk til",
        description=(
            "Costs split by what each line was for, as Myntos's AI read the vouchers "
            "(«Drivstoff», «Programvare og abonnementer», «Husleie og lokaler»): finer than the "
            "account, so fuel and tolls booked on the same car account are two slices. The largest "
            "named, the rest as «Andre», and lines the AI has not read yet as their own slice "
            "«Ikke lest ennå» (key ikke_lest), which is never part of «Andre» and takes one of "
            "the `topp` places. The key of each other slice is the tag's slug. "
            f"{_TAGS} {_BASIS}"
        ),
        parameters=_SPLIT,
        views=_DONUT_VIEWS,
        builder=_hva_split("kostnader_per_hva", "Hva kostnadene gikk til", Side.KOSTNADER),
        needs_tags=True,
    ),
    Dataset(
        name="kostnader_per_motpart",
        label="Kostnader per motpart",
        description=(
            "Costs split by who was paid: the supplier on the cost line when the books name "
            "one, otherwise the counterparty the AI read off the voucher (a shop on a receipt, "
            "which can be a private person's name). Neither is «Uten motpart». The key is the "
            "party's id in Myntos, or motpart: and the tag's slug for one the AI read. "
            f"{_TAGS} {_BASIS}"
        ),
        parameters=_SPLIT,
        views=_BAR_VIEWS,
        builder=_costs_per_counterparty,
        needs_tags=True,
    ),
    Dataset(
        name="hva_per_maned",
        label="Kostnader per måned etter hva de gjelder",
        description=(
            "Monthly costs stacked by what each line was for (up to five largest hva categories "
            "plus «Andre» or «Ikke lest ennå»). Shows how the largest cost types move month by "
            f"month. A month outside the books is null. {_TAGS} {_BASIS}"
        ),
        parameters=_SPLIT,
        views=("kolonner",),
        builder=_hva_per_month,
        needs_tags=True,
    ),
    Dataset(
        name="faste_kostnader",
        label="Faste kostnader",
        description=(
            "Recurring costs detected deterministically: the same counterparty billed roughly "
            "the same monthly amount (within 20 % of the streak median) for at least three "
            "consecutive calendar months in the period. Shows average per month and period "
            "total as a table. Uses registered suppliers, plus KI counterparties when AI is on; "
            f"works with AI off too. {_BASIS}"
        ),
        parameters=(_FRA, _TIL),
        views=("tabell",),
        builder=_recurring_costs,
        needs_tags=False,
    ),
)

REGISTRY: dict[str, Dataset] = {dataset.name: dataset for dataset in DATASETS}


def get(name: str) -> Dataset:
    """The dataset called ``name``, or :class:`UnknownDatasetError`."""
    try:
        return REGISTRY[name]
    except KeyError as error:
        raise UnknownDatasetError(
            f"det finnes ikke noe datasett som heter {name!r}; "
            "se GET /api/v1/companies/{company_id}/analyse/datasett"
        ) from error


def split_name(side: Side, by: str = "konto") -> str:
    """``kostnader_per_konto`` from the side and what it is split by."""
    return f"{side.value}_per_{by}"
