"""The default Analyse page in one answer: tiles, sentences and four charts.

docs/analyse.md, «The API», «Figures response». Everything here is
:mod:`.figures` and :mod:`.datasets` put together; no number is computed
twice in two ways, and nothing asks a model. Starting the background reading
is the route's, not this module's: it is work, and this is figures.

**Which split each pie uses is one function, :func:`split_for`.** With KI
off it is the account, always, and no tag is read (rule 10). With KI on it
is ``*_per_hva`` for a side whose amount is at least 90 % read by KI (D5),
each side deciding for itself; the page tells the split by the chart's
``dataset.name`` and the sentences take the cost split's slices whatever
they are split by.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.analyse import datasets, figures, sentences, vocabulary
from myntos.domain.analyse.charts import Chart
from myntos.domain.analyse.figures import Month, Period, Reading, Side, Totals
from myntos.domain.ledger.models import Company

#: The pies on the page: the five largest and «Andre», six slices at most.
PAGE_TOPP = datasets.TOPP_DEFAULT

#: Slices a sentence must never name as «the largest cost» or as grown: they
#: are not one thing the money went to. «Andre» is many, «Ikke lest ennå» is
#: not known yet, and the ``hva`` «Annet» is KI saying none of the others fit.
NOT_A_COST = frozenset({datasets.OTHER_KEY, figures.UNREAD_KEY, vocabulary.ANNET})

#: Per side, the share of its amount KI has read (``Reading.dekning``).
Dekning = Mapping[Side, Decimal | None]


@dataclass(frozen=True)
class Overview:
    periode: Period
    #: None when there is nothing to compare with: not asked for, or the
    #: books do not cover every month of the same dates a year earlier.
    sammenligning: Period | None
    #: The date of the last voucher outside the opening balance, or None
    #: for books with nothing in them yet.
    bokfort_til: date | None
    nokkeltall: Totals
    forrige: Totals | None
    tekst: list[str]
    #: Income per month, costs per month, income split, costs split.
    diagrammer: list[Chart]
    #: How much of each side KI has read in the period; None with KI off.
    dekning: Dekning | None = None


def split_for(side: Side, dekning: Dekning | None) -> str:
    """The dataset a side's pie on the default page splits by.

    The account with KI off (``dekning`` None) and while less than 90 % of
    the side's amount in the period is read: exact, needs no model, and what
    the person chose when they booked. ``*_per_hva`` from 90 % (D5), with
    what is not read yet as its own slice. WP5b: takes the coverage the
    overview has already read, rather than reading it again per side.
    """
    share = dekning.get(side) if dekning is not None else None
    if share is not None and share >= figures.READ_ENOUGH:
        return datasets.split_name(side, "hva")
    return datasets.split_name(side, "konto")


async def overview(
    db: AsyncSession, *, company: Company, chosen: Period, sammenlign: bool, today: date
) -> Overview:
    """The figures response for ``chosen``, compared with the year before when it can be."""
    books = await figures.books(db, company=company)
    before = chosen.year_before() if sammenlign and books.cover(chosen.year_before()) else None
    totals = await figures.totals(db, company=company, chosen=chosen)
    forrige = (
        await figures.totals(db, company=company, chosen=before) if before is not None else None
    )

    # Read only with KI on: with it off no tag is used, not even to measure (rule 10).
    readings: dict[Side, Reading] | None = None
    if company.ki_enabled:
        readings = {
            side: await figures.per_hva(db, company=company, chosen=chosen, side=side)
            for side in Side
        }
    dekning = {side: r.dekning for side, r in readings.items()} if readings is not None else None

    over_time = datasets.Params(periode=chosen, sammenlign=sammenlign)
    income_months = await datasets.get("inntekter_per_maned").chart(db, company, over_time)
    cost_months = await datasets.get("kostnader_per_maned").chart(db, company, over_time)
    split = datasets.Params(periode=chosen, sammenlign=sammenlign, topp=PAGE_TOPP, view="smultring")
    income_split = await datasets.get(split_for(Side.INNTEKTER, dekning)).chart(db, company, split)
    cost_split = await datasets.get(split_for(Side.KOSTNADER, dekning)).chart(db, company, split)

    grown = None
    if (
        readings is not None
        and before is not None
        and split_for(Side.KOSTNADER, dekning) == datasets.split_name(Side.KOSTNADER, "hva")
    ):
        # Only against a comparison KI has read as well: a year read halfway
        # would make every hva look grown.
        earlier = await figures.per_hva(db, company=company, chosen=before, side=Side.KOSTNADER)
        if earlier.dekning is not None and earlier.dekning >= figures.READ_ENOUGH:
            grown = sentences.grew_most(_changes(readings[Side.KOSTNADER], earlier), chosen, today)

    tekst = sentences.compose(
        sentences.income(
            chosen, today, totals.inntekter, forrige.inntekter if forrige is not None else None
        ),
        sentences.largest_cost(_named(cost_split), totals.kostnader, _unread(cost_split)),
        grown,
        sentences.best_month(_results(income_months, cost_months), today),
    )
    return Overview(
        periode=chosen,
        sammenligning=before,
        bokfort_til=books.last,
        nokkeltall=totals,
        forrige=forrige,
        tekst=tekst,
        diagrammer=[income_months, cost_months, income_split, cost_split],
        dekning=dekning,
    )


def _named(split: Chart) -> list[sentences.Named]:
    return [
        sentences.Named(label=s.label, amount=s.value)
        for s in split.slices or []
        if s.key not in NOT_A_COST
    ]


def _unread(split: Chart) -> Decimal:
    return sum((s.value for s in split.slices or [] if s.key == figures.UNREAD_KEY), figures.ZERO)


def _changes(now: Reading, before: Reading) -> list[sentences.Change]:
    """Each ``hva`` of the period beside the same one in the comparison, «Annet» left out."""
    earlier = {part.key: part.amount for part in before.parts}
    return [
        sentences.Change(
            label=part.label, now=part.amount, before=earlier.get(part.key, figures.ZERO)
        )
        for part in now.parts
        if part.key not in NOT_A_COST
    ]


def _results(income: Chart, costs: Chart) -> list[tuple[Month, Decimal]]:
    """Each month with books and its result, read off the two time charts' main series."""
    assert income.x is not None and costs.x is not None  # noqa: S101 - kolonner always has x
    found: list[tuple[Month, Decimal]] = []
    for label, earned, spent in zip(
        income.x.labels, income.series[0].values, costs.series[0].values, strict=True
    ):
        if earned is None or spent is None:
            continue
        year, month = label.split("-")
        found.append(((int(year), int(month)), earned - spent))
    return found
