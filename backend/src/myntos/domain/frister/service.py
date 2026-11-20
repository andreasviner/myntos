"""One company's deadlines for the coming year, and the ones it has missed.

The list is read, never stored: every fact in it is already somewhere else
(the MVA filings, the skattemelding filings, Brønnøysund's answer, the
company's settings), and a stored copy would be a second answer that goes
stale the moment one of them changes.

**The window** is today and the 365 days after it, so a company sees each
yearly duty once, and the whole of the next MVA year. Behind it, only what is
known to be missed, for at most a year: an MVA term the books cover with
nothing filed for it, or an årsregnskap Brønnøysund had not received when it
was asked after the deadline. A deadline that passed with the answer unknown
is left out rather than shown as missed; «Over fristen» on something filed
from another program is how a list like this stops being read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.frister import rules
from myntos.domain.frister.rules import Kind, Recipient, YearlyDeadline
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.models import Company, CompanyRegisterSnapshot, FinancialYear
from myntos.domain.ledger.vatstatus import registered_on
from myntos.domain.mva import naering as naering_module
from myntos.domain.mva import service as mva_service
from myntos.domain.mva.models import FilingStatus
from myntos.skattemelding.models import SkattemeldingFiling, SkattemeldingStatus

#: How far ahead the list reaches, and how far back a missed deadline stays on it.
WINDOW = timedelta(days=365)

_FILED_SKATTEMELDING = [s.value for s in SkattemeldingStatus if s.is_filed]


class DoneBy:
    """Who says a deadline is met. Strings, because the API repeats them as they are."""

    MYNTOS = "myntos"
    # The person said another program filed the MVA term (``annet_program``).
    ANNET_PROGRAM = "annet_program"
    # Brønnøysund's register lists the year's annual accounts as received.
    REGNSKAPSREGISTERET = "regnskapsregisteret"


@dataclass(frozen=True)
class Frist:
    kind: Kind
    deadline: date
    label: str
    #: The year the duty is about: the income year, or the MVA term's year.
    year: int
    recipient: Recipient
    files_from_myntos: bool
    #: True met, False not met (and Myntos knows), None not known to Myntos.
    done: bool | None
    done_by: str | None = None
    note: str | None = None
    #: The MVA term's number in its year, as the MVA routes take it.
    termin: int | None = None
    #: The MVA melding's category, only for a company that files two.
    naering: str | None = None

    def is_overdue(self, today: date) -> bool:
        return self.done is False and self.deadline < today


@dataclass(frozen=True)
class Frister:
    today: date
    until: date
    #: Whether the yearly duties of the company's form are in the list at all.
    covers_yearly: bool
    #: When Brønnøysund was last asked, which is as fresh as the årsregnskap line is.
    register_checked_at: datetime | None
    wound_up_on: date | None
    frister: list[Frist]


def _shown(frist: Frist, today: date) -> bool:
    """In the window ahead, or behind it and known to be missed."""
    if today <= frist.deadline <= today + WINDOW:
        return True
    return frist.done is False and today - WINDOW <= frist.deadline < today


async def for_company(db: AsyncSession, company: Company, today: date) -> Frister:
    snapshot = await ledger_service.get_register_snapshot(db, company=company)
    books_start = await db.scalar(
        select(func.min(FinancialYear.start_date)).where(FinancialYear.company_id == company.id)
    )
    found = [
        *await _mva(db, company, today, books_start),
        *await _yearly(db, company, today, snapshot),
    ]
    found.sort(key=lambda f: (f.deadline, f.label))
    return Frister(
        today=today,
        until=today + WINDOW,
        covers_yearly=rules.covers_yearly(company.organisation_form),
        register_checked_at=snapshot.fetched_at if snapshot is not None else None,
        wound_up_on=company.wound_up_on,
        frister=found,
    )


async def _mva(
    db: AsyncSession, company: Company, today: date, books_start: date | None
) -> list[Frist]:
    if not company.vat_registered:
        return []
    setup = await naering_module.load(db, company)
    # A year's terms fall due from February to the following April (a yearly
    # primærnæring melding), so the year before the window can still reach it.
    years = list(range((today - WINDOW).year - 1, (today + WINDOW).year + 1))
    found: list[Frist] = []
    for naering in setup.all:
        closing = await mva_service.closing_filings(db, company, naering, setup, years)
        for year in years:
            for termin in naering.terminer(year):
                # Registered by the term's last day: the term registration
                # starts in is the first melding, with the days before it in.
                if not registered_on(company, termin.end):
                    continue
                if company.wound_up_on is not None and termin.start > company.wound_up_on:
                    continue
                filing = closing.get((year, termin.index))
                # Only a term wholly inside the books can be called missing: one
                # before them was the old program's, and nothing in Myntos says
                # whether it went.
                in_books = books_start is not None and termin.start >= books_start
                note = None
                if filing is not None:
                    done: bool | None = True
                    done_by = (
                        DoneBy.MYNTOS
                        if FilingStatus(filing.status).is_filed
                        else DoneBy.ANNET_PROGRAM
                    )
                elif in_books:
                    done, done_by = False, None
                else:
                    done, done_by = None, None
                    note = (
                        "Perioden er fra før regnskapet i Myntos begynner. Er meldingen sendt "
                        "fra et annet program, kan du si det på mva-siden."
                    )
                label = f"Mva-melding for {termin.label}"
                if setup.is_split:
                    label += f", {naering.label.lower()}"
                frist = Frist(
                    kind=Kind.MVA_MELDING,
                    deadline=termin.deadline(naering.category),
                    label=label,
                    year=year,
                    recipient=Recipient.SKATTEETATEN,
                    files_from_myntos=True,
                    done=done,
                    done_by=done_by,
                    note=note,
                    termin=termin.index,
                    naering=naering.category.value if setup.is_split else None,
                )
                if _shown(frist, today):
                    found.append(frist)
    return found


def _register_year(value: object) -> int | None:
    """The year at the front of a register date or year string, or nothing."""
    if not isinstance(value, str) or len(value) < 4 or not value[:4].isdigit():
        return None
    return int(value[:4])


async def _yearly(
    db: AsyncSession,
    company: Company,
    today: date,
    snapshot: CompanyRegisterSnapshot | None,
) -> list[Frist]:
    if not rules.covers_yearly(company.organisation_form):
        return []
    unit = snapshot.unit if snapshot is not None and isinstance(snapshot.unit, dict) else {}
    # No duty for a year the company did not exist in. Stiftelsesdato first:
    # an AS exists from its founding, and is registered some days later.
    founded = _register_year(unit.get("established_on")) or _register_year(
        unit.get("registered_on")
    )
    years = range((today - WINDOW).year - 1, (today + WINDOW).year + 1)
    filed_skattemelding = set(
        await db.scalars(
            select(SkattemeldingFiling.year).where(
                SkattemeldingFiling.company_id == company.id,
                SkattemeldingFiling.status.in_(_FILED_SKATTEMELDING),
            )
        )
    )
    last_accounts = _register_year(unit.get("last_submitted_annual_accounts"))
    asked_on = snapshot.fetched_at.date() if snapshot is not None else None

    found: list[Frist] = []
    for year in years:
        if founded is not None and year < founded:
            continue
        # The last year of a wound-up company has no calendar date: its
        # skattemelding goes before the company is deleted
        # (docs/integrations/skatteetaten.md, «A dissolved company's last
        # year»), and the page says so instead of naming a day it is not.
        if company.wound_up_on is not None and year >= company.wound_up_on.year:
            continue
        for duty in rules.yearly(company.organisation_form, year):
            done, done_by = _yearly_done(duty, filed_skattemelding, last_accounts, asked_on)
            frist = Frist(
                kind=duty.kind,
                deadline=duty.deadline,
                label=duty.label,
                year=duty.year,
                recipient=duty.recipient,
                files_from_myntos=duty.files_from_myntos,
                done=done,
                done_by=done_by,
                note=duty.note,
            )
            if _shown(frist, today):
                found.append(frist)
    return found


def _yearly_done(
    duty: YearlyDeadline,
    filed_skattemelding: set[int],
    last_accounts: int | None,
    asked_on: date | None,
) -> tuple[bool | None, str | None]:
    if duty.kind is Kind.SKATTEMELDING and duty.files_from_myntos:
        # Filed from Myntos is known. Not filed from Myntos is not «not filed»:
        # the first year in Myntos usually went from the old program.
        return (True, DoneBy.MYNTOS) if duty.year in filed_skattemelding else (None, None)
    if duty.kind is Kind.ARSREGNSKAP and last_accounts is not None:
        if last_accounts >= duty.year:
            return True, DoneBy.REGNSKAPSREGISTERET
        # Missing only when the register was asked after the deadline. Asked
        # before it, «not yet received» is what every company's answer says.
        if asked_on is not None and asked_on > duty.deadline:
            return False, None
    return None, None
