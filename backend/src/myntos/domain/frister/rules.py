"""The yearly deadlines, per organisation form, as the law names them.

Pure: an organisation form and an income year in, the deadlines out. The MVA
terms are not here; :mod:`myntos.domain.mva.periods` already owns them, and a
second copy would be a second place for a wrong date.

Where each date comes from:

- **Skattemelding**, 31 May in the year after: skatteforvaltningsloven § 8-2,
  for an aksjeselskap and for a person with business income alike (the
  person's 30 April does not apply to an ENK's owner).
- **Årsregnskap** to Regnskapsregisteret, 31 July in the year after: the
  general meeting adopts it within six months (aksjeloven § 5-5), and it is
  filed within a month of that (regnskapsloven § 8-2). Brønnøysund's late fee
  starts on 1 August, which is why the date is the end of July and not a month
  after whenever the meeting was held.
- **Aksjonærregisteroppgaven** (RF-1086), 31 January in the year after, as
  Skatteetaten's page for the form gives it. Every aksjeselskap files it,
  whether or not anything changed, and one wound up during the year too.
- **Forskuddsskatt**: skattebetalingsloven § 10-20. A company pays in two
  terms, 15 February and 15 April in the year after (third paragraph); a
  person, the owner of an ENK, in four terms in the year itself, 15 March,
  June, September and **December** (first paragraph; November was the old
  fourth term, and a list that still says so is wrong by a month). The amount
  is Skatteetaten's, and may be nothing: the date is shown because it is a
  date, and Myntos does not claim to know what is owed on it. The law moves a
  term to three weeks after the notice when the notice is late, which only
  ever makes the real date later than the one shown here.

**No a-melding.** It is due on the 5th of every month for a company that pays
wages, and Myntos does not know whether a company does. Twelve dates a year
for a company with no employees would teach people to ignore the list.

**No other forms yet.** An NUF, a DA or an SA has deadlines of its own that
nobody has written down here; it gets its MVA terms and nothing else, and the
API says so (``covers_yearly``), rather than a list that looks complete.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date


class Kind(enum.StrEnum):
    MVA_MELDING = "mva_melding"
    SKATTEMELDING = "skattemelding"
    ARSREGNSKAP = "arsregnskap"
    AKSJONAERREGISTEROPPGAVE = "aksjonaerregisteroppgave"
    FORSKUDDSSKATT = "forskuddsskatt"


class Recipient(enum.StrEnum):
    SKATTEETATEN = "skatteetaten"
    BRONNOYSUND = "bronnoysund"


#: Aksjeselskap and allmennaksjeselskap: the same yearly duties.
AKSJESELSKAP_FORMS = frozenset({"AS", "ASA"})
#: The forms Myntos files the skattemelding for. An ASA reaches the same
#: næringsspesifikasjon code path and is refused there
#: (``organisasjonsform_ikke_stottet``), so it is not promised here either.
FILES_SKATTEMELDING = frozenset({"AS"})


@dataclass(frozen=True)
class YearlyDeadline:
    kind: Kind
    #: The income year the duty is about, not the year the date falls in.
    year: int
    deadline: date
    label: str
    recipient: Recipient
    #: Whether Myntos can file or send it today.
    files_from_myntos: bool
    note: str | None = None


def _form(organisation_form: str | None) -> str:
    return (organisation_form or "").strip().upper()


def covers_yearly(organisation_form: str | None) -> bool:
    """Whether this module knows the form's yearly deadlines at all."""
    form = _form(organisation_form)
    return form in AKSJESELSKAP_FORMS or form == "ENK"


def yearly(organisation_form: str | None, year: int) -> list[YearlyDeadline]:
    """Every yearly deadline about income year ``year``, whenever it falls."""
    form = _form(organisation_form)
    if form in AKSJESELSKAP_FORMS:
        return _aksjeselskap(form, year)
    if form == "ENK":
        return _enkeltpersonforetak(year)
    return []


_FORSKUDD_NOTE_SELSKAP = (
    "Beløpet fastsetter Skatteetaten, og Myntos vet ikke hva det er. Har selskapet "
    "ingen forskuddsskatt, er det ingenting å betale."
)
_FORSKUDD_NOTE_PERSON = (
    "Forskuddsskatten er innehaverens egen. Beløpet fastsetter Skatteetaten, og Myntos "
    "vet ikke hva det er. Er den under 2 000 kroner, betales alt 15. juni."
)


def _aksjeselskap(form: str, year: int) -> list[YearlyDeadline]:
    after = year + 1
    files = form in FILES_SKATTEMELDING
    return [
        YearlyDeadline(
            kind=Kind.AKSJONAERREGISTEROPPGAVE,
            year=year,
            deadline=date(after, 1, 31),
            label=f"Aksjonærregisteroppgaven for {year}",
            recipient=Recipient.SKATTEETATEN,
            files_from_myntos=False,
            note="Myntos kan ikke sende den ennå.",
        ),
        YearlyDeadline(
            kind=Kind.FORSKUDDSSKATT,
            year=year,
            deadline=date(after, 2, 15),
            label=f"Forskuddsskatt for {year}, 1. termin",
            recipient=Recipient.SKATTEETATEN,
            files_from_myntos=False,
            note=_FORSKUDD_NOTE_SELSKAP,
        ),
        YearlyDeadline(
            kind=Kind.FORSKUDDSSKATT,
            year=year,
            deadline=date(after, 4, 15),
            label=f"Forskuddsskatt for {year}, 2. termin",
            recipient=Recipient.SKATTEETATEN,
            files_from_myntos=False,
            note=_FORSKUDD_NOTE_SELSKAP,
        ),
        YearlyDeadline(
            kind=Kind.SKATTEMELDING,
            year=year,
            deadline=date(after, 5, 31),
            label=f"Skattemeldingen for {year}",
            recipient=Recipient.SKATTEETATEN,
            files_from_myntos=files,
            note=None if files else "Myntos sender skattemeldingen bare for AS ennå.",
        ),
        YearlyDeadline(
            kind=Kind.ARSREGNSKAP,
            year=year,
            deadline=date(after, 7, 31),
            label=f"Årsregnskapet for {year}",
            recipient=Recipient.BRONNOYSUND,
            files_from_myntos=False,
            note=(
                "Sendes til Regnskapsregisteret i Brønnøysund etter at generalforsamlingen "
                "har godkjent det. Myntos kan ikke sende det ennå."
            ),
        ),
    ]


def _enkeltpersonforetak(year: int) -> list[YearlyDeadline]:
    terms = [
        YearlyDeadline(
            kind=Kind.FORSKUDDSSKATT,
            year=year,
            deadline=date(year, month, 15),
            label=f"Forskuddsskatt for {year}, {number}. termin",
            recipient=Recipient.SKATTEETATEN,
            files_from_myntos=False,
            note=_FORSKUDD_NOTE_PERSON,
        )
        for number, month in enumerate((3, 6, 9, 12), start=1)
    ]
    return [
        *terms,
        YearlyDeadline(
            kind=Kind.SKATTEMELDING,
            year=year,
            deadline=date(year + 1, 5, 31),
            label=f"Skattemeldingen for {year}",
            recipient=Recipient.SKATTEETATEN,
            files_from_myntos=False,
            note=(
                "Det er innehaverens egen skattemelding, med næringsspesifikasjonen. Myntos "
                "kan ikke sende den for et enkeltpersonforetak ennå."
            ),
        ),
    ]
