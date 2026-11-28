"""The two or three sentences above the charts: templates over figures, never a model.

docs/analyse.md, «The Analyse page», point 4, and rule 1: the default page
asks no model, so these are fixed bokmål sentences with numbers from
:mod:`.figures` put in. A sentence whose premise is missing (no comparison
year, a single month, a single cost) is left out rather than filled with
«0 %» or a superlative about one thing, because a sentence that is
arithmetically true and says nothing reads as if it said something.

Numbers are written the Norwegian way: «412 000 kr», «12 %», with a
no-break space in the thousands and before the unit so a line never breaks
inside an amount.

With the costs split by ``hva`` (WP5b), a tag's label goes into a sentence:
the only text in one that a model made, a label from the company's own
vocabulary, capped at 80 characters (rule 8). The sentence around it is
still a template, and the numbers are still the books'.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from myntos.domain.analyse.figures import Month, Period

_NBSP = "\N{NO-BREAK SPACE}"

MONTH_NAMES = (
    "januar",
    "februar",
    "mars",
    "april",
    "mai",
    "juni",
    "juli",
    "august",
    "september",
    "oktober",
    "november",
    "desember",
)


def _grouped(whole: int) -> str:
    sign = "-" if whole < 0 else ""
    return sign + f"{abs(whole):,}".replace(",", _NBSP)


def kroner(amount: Decimal) -> str:
    """``Decimal("412000.40")`` as «412 000 kr»: whole kroner, as a person says it."""
    return f"{_grouped(int(amount.quantize(Decimal(1), rounding=ROUND_HALF_UP)))}{_NBSP}kr"


def percent(fraction: Decimal) -> str:
    """``Decimal("0.123")`` as «12 %»."""
    whole = int((fraction * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return f"{_grouped(whole)}{_NBSP}%"


def _whole_percent(fraction: Decimal) -> int:
    return int((fraction * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def month_name(month: Month, *, with_year: bool) -> str:
    name = MONTH_NAMES[month[1] - 1]
    return f"{name} {month[0]}" if with_year else name


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


@dataclass(frozen=True)
class Named:
    """A part of the costs a sentence may name: an account, or a ``hva`` tag."""

    label: str
    amount: Decimal


@dataclass(frozen=True)
class Change:
    """One ``hva`` of the costs in the period and in its comparison."""

    label: str
    now: Decimal
    before: Decimal


#: A ``hva`` is named as grown only past both (docs/analyse.md, point 4): a
#: quarter, so ordinary variation is not news, and 5 000 kr, so a small cost
#: doubling from 300 kr to 600 kr is not either.
GROWTH_SHARE = Decimal("0.25")
GROWTH_KRONER = Decimal("5000")


def income(chosen: Period, today: date, inntekter: Decimal, forrige: Decimal | None) -> str | None:
    """«Inntektene hittil i år er 412 000 kr, 12 % mer enn samme tid i fjor.»

    Left out with no income: the tiles say 0 kr already, and a sentence
    about it adds nothing. The comparison half is left out when there is no
    comparison or it had no income, since a change from nothing is no
    percentage at all.
    """
    if inntekter <= 0:
        return None
    if chosen.is_year_to_date(today):
        opening, against = "Inntektene hittil i år er", "samme tid i fjor"
    elif chosen.is_calendar_year():
        verb = "var" if chosen.til < today else "er"
        opening, against = f"Inntektene i {chosen.fra.year} {verb}", f"i {chosen.fra.year - 1}"
    else:
        opening, against = "Inntektene i perioden er", "samme tid året før"
    sentence = f"{opening} {kroner(inntekter)}"
    if forrige is None or forrige <= 0:
        return f"{sentence}."
    change = _whole_percent((inntekter - forrige) / forrige)
    if change == 0:
        return f"{sentence}, omtrent det samme som {against}."
    direction = "mer" if change > 0 else "mindre"
    return f"{sentence}, {_grouped(abs(change))}{_NBSP}% {direction} enn {against}."


def largest_cost(
    parts: Sequence[Named], kostnader: Decimal, unread: Decimal = Decimal(0)
) -> str | None:
    """«Den største kostnaden er 4000 Innkjøp av varer, 38 % av kostnadene.»

    ``parts`` are the named parts of the costs, largest first, without
    «Andre» (which is not one cost) or anything else that is not a thing the
    money went to. Left out when there are no costs, or when one part is all
    of them: «the largest of one» is not news. ``unread`` is what KI has not
    read yet on a split by ``hva``; when it is as large as the largest part,
    a larger cost could be in it, and the sentence is left out (WP5b).
    """
    if kostnader <= 0 or len(parts) < 2:
        return None
    largest = parts[0]
    if largest.amount <= 0 or largest.amount <= unread:
        return None
    share = _whole_percent(largest.amount / kostnader)
    if share <= 0 or share >= 100:
        return None
    return f"Den største kostnaden er {largest.label}, {_grouped(share)}{_NBSP}% av kostnadene."


def _since(chosen: Period, today: date) -> str:
    """What a change is measured from, worded as ``income`` words its comparison."""
    if chosen.is_year_to_date(today):
        return "fra samme tid i fjor"
    if chosen.is_calendar_year():
        return f"fra {chosen.fra.year - 1}"
    return "fra samme tid året før"


def grew_most(changes: Sequence[Change], chosen: Period, today: date) -> str | None:
    """«Drivstoff har økt med 40 % fra samme tid i fjor.»: the ``hva`` that grew the most.

    ``changes`` are the cost side's ``hva`` in the period and its comparison,
    both read by KI (the caller checks that each is read enough). Only one
    that grew by more than 25 % **and** more than 5 000 kr counts, and of
    those the one that grew by the most kroner, because that is what moved
    the result; the percentage is what the sentence then says. Left out
    when none did, when two grew by the same amount, and for one that was
    nothing the year before, since a change from nothing is no percentage.
    """
    grown = [
        change
        for change in changes
        if change.before > 0
        and change.now - change.before > GROWTH_KRONER
        and (change.now - change.before) / change.before > GROWTH_SHARE
    ]
    if not grown:
        return None
    ranked = sorted(grown, key=lambda change: change.now - change.before, reverse=True)
    top = ranked[0]
    if len(ranked) > 1 and ranked[1].now - ranked[1].before == top.now - top.before:
        return None
    share = _whole_percent((top.now - top.before) / top.before)
    return f"{_capital(top.label)} har økt med {_grouped(share)}{_NBSP}% {_since(chosen, today)}."


def best_month(results: Sequence[tuple[Month, Decimal]], today: date) -> str | None:
    """«Mars var den beste måneden.»: the month with the highest result before tax.

    ``results`` are the months of the period that have books. Left out with
    fewer than two (there is nothing to be best among), when two months
    share the best result, and when the best month still lost money,
    since «the best month» of a year of losses is not what the words say.
    The month this is written in is «så langt» the best, because it is not
    over.
    """
    if len(results) < 2:
        return None
    ranked = sorted(results, key=lambda item: item[1], reverse=True)
    (month, result), runner_up = ranked[0], ranked[1][1]
    if result <= 0 or result == runner_up:
        return None
    years = {m[0] for m, _ in results}
    name = _capital(month_name(month, with_year=len(years) > 1))
    if month == (today.year, today.month):
        return f"{name} er så langt den beste måneden."
    return f"{name} var den beste måneden."


def compose(*sentences: str | None) -> list[str]:
    """The sentences that had their premise, in the order given."""
    return [sentence for sentence in sentences if sentence]
