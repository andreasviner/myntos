"""Oppløsning: a wound-up company's books end on their last day, not on 31 December.

An aksjeselskap that is dissolved (aksjeloven kapittel 16) pays its debts,
hands what is left to its shareholders and is deleted from Enhetsregisteret.
Its last financial year ends there, and skatteforvaltningsloven § 8-2 wants
that year's skattemelding *before* the company is gone, as a
forhåndsfastsetting, not the spring after. Myntos needs to know the day for
three things, and this module is where it is set:

- **the last year ends on it.** ``financial_year.end_date`` moves from 31
  December to the day, so the year can be closed the day after (closing waits
  for a year to be over, :func:`myntos.domain.arsoppgjor.closing.plan_closing`),
  and the SAF-T file and the næringsspesifikasjon report the period the
  company actually existed;
- **nothing is posted after it.**
  :func:`myntos.domain.ledger.service.ensure_financial_year` refuses a later
  year and, given the voucher's date, a later day, and closing the last year
  opens no next one;
- **the skattemelding for that year is a forhåndsfastsetting.**
  :mod:`myntos.skattemelding` reads ``company.wound_up_on`` and files the year
  early, marked as fully wound up (docs/integrations/skatteetaten.md).

**The day is the person's to state.** Enhetsregisteret's ``slettedato`` is when
Brønnøysund removed the company, usually weeks after the creditors' deadline
and the last payout; the books end where the last thing happened. Myntos only
refuses a day the books contradict: one with entries after it.

**Undoing it is allowed while the year is open**, logged like the setting, and
refused once a forhåndsfastsetting for the year has been sent: what Skatteetaten
was told rests on the day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.arsoppgjor.errors import (
    FinancialYearNotFoundError,
    InvalidWindingUpDateError,
    WindingUpConflictError,
)
from myntos.domain.arsoppgjor.models import FinancialYearEvent, YearAction
from myntos.domain.ledger.models import Company, FinancialYear, Journal, Transaction
from myntos.skattemelding.models import SkattemeldingFiling, SkattemeldingStatus


@dataclass(frozen=True)
class WindingUp:
    """Where a company's books stand: ended on a day, or still running."""

    #: The last day, or None for a company still in business.
    last_day: date | None
    #: The year that ends on it, or None.
    year: int | None
    #: Whether that year is closed, which is when it may no longer be undone.
    year_closed: bool


async def status(db: AsyncSession, *, company: Company) -> WindingUp:
    last_day = company.wound_up_on
    if last_day is None:
        return WindingUp(last_day=None, year=None, year_closed=False)
    financial_year = await _year(db, company, last_day.year)
    return WindingUp(
        last_day=last_day,
        year=last_day.year,
        year_closed=financial_year.closed_at is not None,
    )


async def _year(db: AsyncSession, company: Company, year: int) -> FinancialYear:
    found = await db.scalar(
        select(FinancialYear).where(
            FinancialYear.company_id == company.id, FinancialYear.year == year
        )
    )
    if found is None:
        raise FinancialYearNotFoundError(f"selskapet har ikke noe regnskapsår {year} i Myntos")
    return found


async def end_books(
    db: AsyncSession,
    *,
    company: Company,
    last_day: date,
    today: date,
    created_by_user_id: int | None,
) -> WindingUp:
    """End the books on ``last_day``: the year ends there, and nothing comes after it."""
    if company.wound_up_on is not None:
        raise WindingUpConflictError(
            f"regnskapet slutter allerede {company.wound_up_on:%d.%m.%Y}. Angre det først "
            "for å velge en annen dag"
        )
    if last_day > today:
        raise InvalidWindingUpDateError(
            "siste dag i regnskapet kan ikke være etter i dag. Velg dagen da det siste "
            "skjedde i selskapet, som utbetalingen til aksjonærene"
        )
    financial_year = await _year(db, company, last_day.year)
    if last_day <= financial_year.start_date:
        # end_date > start_date is a constraint on the table, and a company
        # that ended on 1 January did nothing in that year: its last year is
        # the one before.
        raise InvalidWindingUpDateError(
            f"regnskapet kan ikke slutte {last_day:%d.%m.%Y}, årets første dag. Var det "
            f"siste som skjedde i selskapet i {last_day.year - 1}, velg 31.12.{last_day.year - 1}"
        )
    if financial_year.closed_at is not None:
        raise WindingUpConflictError(
            f"regnskapsåret {last_day.year} er avsluttet. Gjenåpne det under Årsavslutning "
            "før regnskapet kan slutte i det"
        )
    later = await db.scalar(
        select(func.min(FinancialYear.year)).where(
            FinancialYear.company_id == company.id, FinancialYear.year > last_day.year
        )
    )
    if later is not None:
        raise WindingUpConflictError(
            f"selskapet har et regnskapsår {int(later)} i Myntos, etter {last_day.year}. "
            "Et oppløst selskap har ikke noe år etter det siste"
        )
    after = (
        await db.execute(
            select(func.count(Transaction.id), func.min(Transaction.gl_posting_date))
            .join(Journal, Journal.id == Transaction.journal_id)
            .where(
                Journal.company_id == company.id,
                or_(
                    Transaction.financial_year > last_day.year,
                    Transaction.gl_posting_date > last_day,
                ),
            )
        )
    ).one()
    if after[0]:
        raise WindingUpConflictError(
            f"{after[0]} bilag er datert etter {last_day:%d.%m.%Y}, det første "
            f"{after[1]:%d.%m.%Y}. Regnskapet kan ikke slutte før det siste som er ført"
        )

    financial_year.end_date = last_day
    company.wound_up_on = last_day
    db.add(
        FinancialYearEvent(
            company_id=company.id,
            financial_year=last_day.year,
            action=YearAction.OPPLOST.value,
            user_id=created_by_user_id,
            reason=f"Selskapet er oppløst. Siste dag i regnskapet: {last_day:%d.%m.%Y}",
        )
    )
    await db.flush()
    return WindingUp(last_day=last_day, year=last_day.year, year_closed=False)


async def undo(db: AsyncSession, *, company: Company, created_by_user_id: int | None) -> WindingUp:
    """Let the books run to 31 December again, while the last year is still open."""
    last_day = company.wound_up_on
    if last_day is None:
        raise WindingUpConflictError("regnskapet er ikke avsluttet ved oppløsning")
    financial_year = await _year(db, company, last_day.year)
    if financial_year.closed_at is not None:
        raise WindingUpConflictError(
            f"regnskapsåret {last_day.year} er avsluttet. Gjenåpne det under Årsavslutning først"
        )
    filed = await db.scalars(
        select(SkattemeldingFiling.status).where(
            SkattemeldingFiling.company_id == company.id,
            SkattemeldingFiling.year == last_day.year,
        )
    )
    if any(SkattemeldingStatus(value).is_filed for value in filed):
        raise WindingUpConflictError(
            f"skattemeldingen for {last_day.year} er sendt til Skatteetaten som "
            "forhåndsfastsetting, med regnskapet slik det sluttet. Det kan ikke angres her"
        )

    financial_year.end_date = date(last_day.year, 12, 31)
    company.wound_up_on = None
    db.add(
        FinancialYearEvent(
            company_id=company.id,
            financial_year=last_day.year,
            action=YearAction.OPPLOST_ANGRET.value,
            user_id=created_by_user_id,
            reason=f"Regnskapet løper igjen til 31.12.{last_day.year}",
        )
    )
    await db.flush()
    return WindingUp(last_day=None, year=None, year_closed=False)
