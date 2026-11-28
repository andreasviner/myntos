"""The chart spec: what the backend sends and ``lib/charts.ts`` draws.

docs/analyse.md, «The chart spec», is the contract between the figures (and
later a model choosing a chart) and the frontend's chart kit, which is built
from that section and not from this file. So the shape here is exactly the
doc's, field for field, and the rules of the shape are checked when a
:class:`Chart` is made: a wrong combination raises in the backend, where the
test suite sees it, instead of reaching a page that draws nothing or, worse,
draws something that looks right.

Amounts are :class:`~decimal.Decimal` inside (rule 4) and decimal strings in
JSON, which is how Pydantic serialises a ``Decimal``. A chart carries no
colour, font or markup: how it looks is the frontend's.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

ChartView = Literal["kolonner", "linje", "smultring", "stolper", "tabell", "tall"]
ChartUnit = Literal["kr", "prosent", "antall"]
SeriesRole = Literal["hoved", "sammenligning", "serie"]

#: Every view, in the doc's order, for a caller that has to list them.
CHART_VIEWS: tuple[ChartView, ...] = ("kolonner", "linje", "smultring", "stolper", "tabell", "tall")

#: More lines than this on one time chart and the colours run out before the
#: reader's patience does; four is income, costs, result and one comparison.
MAX_SERIES = 4
#: Stacked columns share the six-colour chart palette (five largest + «Andre»).
MAX_STACKED_SERIES = 6
#: A donut with more than six slices is a bar chart nobody can read.
MAX_DONUT_SLICES = 6
MAX_BAR_SLICES = 10

_MONTH_LABEL = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class _Spec(BaseModel):
    # Forbid unknown fields: a typo in a builder (``serie`` for ``series``)
    # would otherwise be dropped silently and the chart sent without it.
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChartAxis(_Spec):
    kind: Literal["maned", "kategori"]
    labels: list[str]

    @model_validator(mode="after")
    def _months_are_months(self) -> Self:
        if self.kind == "maned":
            wrong = [label for label in self.labels if not _MONTH_LABEL.match(label)]
            if wrong:
                raise ValueError(f"a month axis takes YYYY-MM labels, not {wrong[:3]}")
        return self


class ChartSeries(_Spec):
    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    role: SeriesRole
    #: One per x label; ``None`` is «no books that month», not zero.
    values: list[Decimal | None]


class ChartSlice(_Spec):
    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    value: Decimal
    #: The slice's part of the whole, a fraction (``0.3812``), not a percent.
    share: Decimal


class ChartSource(_Spec):
    """Where the numbers came from: the dataset and the parameters it was built with."""

    name: str = Field(min_length=1)
    params: dict[str, str]


class Chart(_Spec):
    """One chart. See docs/analyse.md, «The chart spec», for each field."""

    id: str = Field(min_length=1)
    view: ChartView
    title: str = Field(min_length=1, max_length=60)
    caption: str | None = Field(default=None, max_length=200)
    unit: ChartUnit
    x: ChartAxis | None = None
    series: list[ChartSeries] = Field(default_factory=list)
    slices: list[ChartSlice] | None = None
    total: Decimal | None = None
    #: Whether ``kolonner`` series stack vertically per slot rather than side by side (WP6).
    stacked: bool = False
    dataset: ChartSource

    @model_validator(mode="after")
    def _shape(self) -> Self:
        """The rules of the shape, from the doc, one view at a time.

        Anything the rule for a view does not mention is required to be empty,
        so a builder that fills a field the frontend will not read for that
        view is told so here, instead of the person never seeing the figure.
        """
        _unique("series", [s.key for s in self.series])
        if self.slices is not None:
            _unique("slices", [s.key for s in self.slices])
        if self.x is not None:
            for series in self.series:
                if len(series.values) != len(self.x.labels):
                    raise ValueError(
                        f"series {series.key!r} has {len(series.values)} values "
                        f"for {len(self.x.labels)} labels"
                    )
        if self.stacked and self.view != "kolonner":
            raise ValueError("stacked is only valid for kolonner")

        if self.view in ("kolonner", "linje"):
            if self.x is None or not self.series:
                raise ValueError(f"{self.view} needs x and at least one series")
            limit = MAX_STACKED_SERIES if self.stacked else MAX_SERIES
            if len(self.series) > limit:
                raise ValueError(f"{self.view} has at most {limit} series")
            if self.slices is not None:
                raise ValueError(f"{self.view} has no slices")
        elif self.view in ("smultring", "stolper"):
            limit = MAX_DONUT_SLICES if self.view == "smultring" else MAX_BAR_SLICES
            if self.slices is None:
                raise ValueError(f"{self.view} needs slices")
            if len(self.slices) > limit:
                raise ValueError(f"{self.view} has at most {limit} slices")
            if self.x is not None or self.series:
                raise ValueError(f"{self.view} has slices, not x and series")
            if self.view == "smultring":
                _a_whole_donut(self.slices)
        elif self.view == "tabell":
            by_slices = self.slices is not None and not self.series and self.x is None
            by_series = self.slices is None and bool(self.series) and self.x is not None
            if not (by_slices or by_series):
                raise ValueError("tabell uses slices, or x and series, not both")
        elif self.view == "tall":
            if self.total is None:
                raise ValueError("tall shows one value, in total")
            if self.x is not None or self.series or self.slices is not None:
                raise ValueError("tall has only total")
        return self


def _unique(what: str, keys: list[str]) -> None:
    if len(set(keys)) != len(keys):
        raise ValueError(f"two {what} share a key: {keys}")


def _a_whole_donut(slices: list[ChartSlice]) -> None:
    """A donut's slices are the whole: no negative slice, and shares summing to one.

    An empty list is allowed and means nothing to draw (a side with no
    income yet); the page says so in words rather than drawing an empty ring.
    A negative slice cannot be drawn as a part of a whole at all, which is
    why a builder that meets one sends bars instead (:mod:`.datasets`).
    """
    if not slices:
        return
    if any(s.value < 0 or s.share < 0 for s in slices):
        raise ValueError("a donut cannot show a negative slice")
    total = sum((s.share for s in slices), Decimal(0))
    if total != 1:
        raise ValueError(f"a donut's shares sum to 1, not {total}")
