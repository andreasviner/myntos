"""Skattleggingsperioder: which dates a melding covers, and when it is due.

The XSD names a period by a word, not by dates (``"mars-april"`` inside a
``skattleggingsperiodeToMaaneder`` element), and accepts only the words it
enumerates. So a period here is the pair the XSD wants plus the dates the ledger
needs, computed from one index so the two cannot disagree.

Three lengths are supported, the three an ordinary Norwegian business can have:
two months (the default in merverdiavgiftsloven § 15-1), one month (granted on
application, usually to businesses that are owed refunds), and a year (for
turnover under one million kroner, also on application, and the normal case for
primærnæring). The XSD has four more, ``uke``, ``halvMaaned``,
``treMaaneder`` and ``seksMaaneder``; they belong to special arrangements
(kompensasjon, among others) that Myntos does not file, and refusing them here is
better than producing a period no ordinary filer can have.

**Deadlines** follow skatteforvaltningsforskriften § 8-3-1: one month and ten
days after the period ends, except that May-June is due on 31 August, a yearly
melding on 10 March, and a primærnæring yearly melding on 10 April. A deadline
on a Saturday or Sunday moves to the Monday. Public holidays move it too, and
that is not modelled: the date shown is the latest one Myntos is sure of, never
a later one, so an error in it can only make somebody early.
"""

from __future__ import annotations

import calendar
import enum
from dataclasses import dataclass
from datetime import date, timedelta

from myntos.domain.mva.codes import Meldingskategori


class PeriodType(enum.StrEnum):
    MAANED = "maaned"
    TO_MAANEDER = "toMaaneder"
    AAR = "aar"

    @property
    def months(self) -> int:
        return {PeriodType.MAANED: 1, PeriodType.TO_MAANEDER: 2, PeriodType.AAR: 12}[self]

    @property
    def per_year(self) -> int:
        """How many periods a year has."""
        return 12 // self.months

    @property
    def element(self) -> str:
        """The XSD element the period's name goes in."""
        return {
            PeriodType.MAANED: "skattleggingsperiodeMaaned",
            PeriodType.TO_MAANEDER: "skattleggingsperiodeToMaaneder",
            PeriodType.AAR: "skattleggingsperiodeAar",
        }[self]

    @property
    def label(self) -> str:
        return {
            PeriodType.MAANED: "Månedlig",
            PeriodType.TO_MAANEDER: "Annenhver måned (vanlig)",
            PeriodType.AAR: "Årlig",
        }[self]


_MONTHS = (
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


class UnknownPeriodError(ValueError):
    """The year and index do not name a period of this length."""


@dataclass(frozen=True)
class Termin:
    period_type: PeriodType
    year: int
    # 1-based, so the sixth two-month period is index 6 and reads as
    # "6. termin" the way Skatteetaten's letters number them.
    index: int

    def __post_init__(self) -> None:
        if not 1 <= self.index <= self.period_type.per_year:
            count = self.period_type.per_year
            raise UnknownPeriodError(
                f"{self.period_type.value} har {count} terminer, ikke {self.index}"
            )
        if not 2000 <= self.year <= 2100:
            raise UnknownPeriodError(f"året {self.year} er utenfor det Myntos regner med")

    @property
    def start(self) -> date:
        return date(self.year, (self.index - 1) * self.period_type.months + 1, 1)

    @property
    def end(self) -> date:
        """The last day of the period, inclusive."""
        month = self.index * self.period_type.months
        return date(self.year, month, calendar.monthrange(self.year, month)[1])

    @property
    def name(self) -> str:
        """The XSD enumeration value: ``"mars-april"``, ``"januar"``, ``"aarlig"``."""
        if self.period_type is PeriodType.AAR:
            return "aarlig"
        first = _MONTHS[self.start.month - 1]
        if self.period_type is PeriodType.MAANED:
            return first
        return f"{first}-{_MONTHS[self.end.month - 1]}"

    @property
    def label(self) -> str:
        """How the period reads to a person."""
        if self.period_type is PeriodType.AAR:
            return f"Hele {self.year}"
        if self.period_type is PeriodType.MAANED:
            return f"{self.name.capitalize()} {self.year}"
        return f"{self.index}. termin {self.year} ({self.name})"

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def deadline(self, category: Meldingskategori) -> date:
        """The filing and payment deadline, moved off a weekend."""
        if self.period_type is PeriodType.AAR:
            month = 4 if category is Meldingskategori.PRIMAERNAERING else 3
            due = date(self.year + 1, month, 10)
        elif self.end.month == 6 and self.period_type is PeriodType.TO_MAANEDER:
            due = date(self.year, 8, 31)
        else:
            following = self.end + timedelta(days=1)
            month = following.month + 1
            year = following.year + (1 if month > 12 else 0)
            due = date(year, (month - 1) % 12 + 1, 10)
        while due.weekday() >= 5:
            due += timedelta(days=1)
        return due


def terminer(period_type: PeriodType, year: int) -> list[Termin]:
    return [Termin(period_type, year, index) for index in range(1, period_type.per_year + 1)]


def termin_for(period_type: PeriodType, day: date) -> Termin:
    """The period ``day`` falls in."""
    return Termin(period_type, day.year, (day.month - 1) // period_type.months + 1)


def parse_period_type(value: str) -> PeriodType:
    try:
        return PeriodType(value)
    except ValueError as error:
        raise UnknownPeriodError(f"ukjent terminlengde: {value!r}") from error
