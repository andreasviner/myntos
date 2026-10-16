"""Årsavslutning: the year's result onto equity, the year locked, the next one opened.

**What is posted.** At most two vouchers in the ``AVS`` journal, dated the last
day of the year:

1. For an AS that states one, the **tax cost**: 8300 Betalbar skatt against 2500
   Betalbar skatt, ikke fastsatt. Myntos does not compute the tax; the person
   closing states it, and the review says so when an AS with a profit states
   none.
2. The **disposition**, which brings every income and cost account to zero for
   the year so the next one opens them at nothing, while the balance sheet
   carries the result on equity. This is where the organisation forms differ,
   and why the form is read off the company rather than asked:

   - **AS** (aksjeloven): a profit first covers any udekket tap already on 2080
     (8990 against 2080), and the rest is transferred to annen egenkapital (8960
     against 2050). A loss is covered by the free equity on 2050 as far as it
     goes (2050 against 8960), and what it cannot cover is carried as udekket
     tap on 2080 (2080 against 8990) rather than driving 2050 negative: the
     balance sheet has to show it as a deduction of its own, and the next
     year's profit has to cover it before anything reaches 2050. Avsatt utbytte
     (8920/2800) is a generalforsamling's decision and not offered yet.
   - **ENK**: there is nothing to decide. The result is the owner's, so it goes
     straight onto equity (8800 against 2050), and the year's privatuttak on
     2060 are closed against the same equity. No tax is booked: an
     enkeltpersonforetak is not a taxpayer, its owner is.
   - **Anything else is refused**, by name. An ANS splits the result between
     partners by agreement, an SA has andelskapital and member rules, a NUF
     closes in its foreign head office's books. Posting an AS disposition for
     any of them would be a confident wrong answer, which is worse than none.

**The lock.** ``financial_year.closed_at`` is set, and every path that posts
goes through :func:`myntos.domain.ledger.service.ensure_financial_year`, which
refuses a closed year. Years close in order: a year whose predecessor in Myntos
is still open cannot close, since its opening equity is not settled yet.

**Reopening: allowed, explicit, logged, and never an erasure.** Bokføringsloven
asks that posted entries stay as they were and that every change can be traced
(§ 4, sporbarhet). It says nothing about a software lock, and the lock is not
what it protects: the entries are. So reopening does not touch a single posted
voucher. It **reverses** the closing vouchers, which stay in the books beside
their reversals, clears the lock, and writes a :class:`FinancialYearEvent`
naming who did it and the reason they gave. Refusing outright was the
alternative, and it would push the ordinary case (the accountant finds a missing
supplier invoice in February, before the årsregnskap is adopted) into a worse
one: a correction booked into the wrong year. Two limits keep it honest:

- only the latest closed year may be reopened, because a later closed year's
  opening rests on this one's closing;
- the person must confirm that the årsregnskap has not been fastsatt. Once it
  has been adopted and filed, an error in it is corrected in the open year
  against equity, under god regnskapsskikk, and not by changing the year that
  was reported. Myntos cannot see the adoption yet (Regnskapsregisteret is phase
  6), so for now the confirmation is the person's.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from myntos.core.clock import utc_now
from myntos.domain.arsoppgjor import opening
from myntos.domain.arsoppgjor.errors import (
    FinancialYearNotFoundError,
    ReopeningReasonRequiredError,
    YearNotClosableError,
    YearNotReopenableError,
)
from myntos.domain.arsoppgjor.findings import Finding, blocking, feil, merknad
from myntos.domain.arsoppgjor.models import FinancialYearEvent, YearAction
from myntos.domain.ledger import posting
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.chart import (
    CLOSING_JOURNAL_DESCRIPTION,
    CLOSING_JOURNAL_ID,
    CLOSING_JOURNAL_TYPE,
    IMPORT_JOURNAL_ID,
)
from myntos.domain.ledger.errors import ChartIncompleteError
from myntos.domain.ledger.models import (
    Account,
    AccountType,
    Company,
    FinancialYear,
    Journal,
    Transaction,
    TransactionLine,
)
from myntos.domain.mva import service as mva_service

ZERO = Decimal("0.00")
_CENT = Decimal("0.01")

SHARE_EQUITY = "2050"  # Annen egenkapital
UNCOVERED_LOSS = "2080"  # Udekket tap (balance)
PRIVATE_ACCOUNT = "2060"  # Privatkonto
TAX_PAYABLE = "2500"  # Betalbar skatt, ikke fastsatt
TAX_COST = "8300"  # Betalbar skatt
ENK_RESULT = "8800"  # Årsresultat overført egenkapital
AS_TRANSFER = "8960"  # Overføringer annen egenkapital
AS_UNCOVERED = "8990"  # Udekket tap (disposition)

# Accounts the closing itself posts to on the P&L side. Kept out of the
# result it computes, so a reopened year (closing plus reversal, netting to
# zero) and a year never closed read the same.
CLOSING_ACCOUNTS = frozenset({TAX_COST, ENK_RESULT, AS_TRANSFER, AS_UNCOVERED})

TAX_VOUCHER_TYPE = "skattekostnad"
DISPOSITION_VOUCHER_TYPE = "arsavslutning"

SUPPORTED_FORMS = ("AS", "ENK")


@dataclass(frozen=True)
class PlannedLine:
    account_number: str
    description: str
    debit: Decimal = ZERO
    credit: Decimal = ZERO


@dataclass(frozen=True)
class ClosingPlan:
    year: int
    organisation_form: str | None
    closed_at: datetime | None
    revenue: Decimal
    costs: Decimal
    result_before_tax: Decimal
    tax: Decimal
    result: Decimal
    tax_lines: list[PlannedLine]
    disposition_lines: list[PlannedLine]
    findings: list[Finding] = field(default_factory=list)

    @property
    def closable(self) -> bool:
        return not blocking(self.findings)


@dataclass(frozen=True)
class YearStatus:
    year: int
    start_date: date
    end_date: date
    closed_at: datetime | None
    events: list[FinancialYearEvent]


def normalised_form(company: Company) -> str | None:
    form = (company.organisation_form or "").strip().upper()
    return form or None


# --- Reading balances ---------------------------------------------------------------


async def _net_by_account(
    db: AsyncSession,
    company: Company,
    *,
    year: int | None = None,
    up_to: int | None = None,
    journal: str | None = None,
) -> dict[str, Decimal]:
    """Debit minus credit per account number, for one year or every year up to one.

    ``journal`` narrows it to one journal: the vouchers brought from the
    previous program, for the closing they already carry.
    """
    query = (
        select(
            Account.account_number,
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .join(TransactionLine, TransactionLine.account_id == Account.id)
        .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
        .join(Journal, Journal.id == Transaction.journal_id)
        .where(Journal.company_id == company.id, Transaction.posted_at.is_not(None))
        .group_by(Account.account_number)
    )
    if year is not None:
        query = query.where(Transaction.financial_year == year)
    if up_to is not None:
        query = query.where(Transaction.financial_year <= up_to)
    if journal is not None:
        query = query.where(Journal.journal_id == journal)
    return {
        number: Decimal(str(debit)) - Decimal(str(credit))
        for number, debit, credit in await db.execute(query)
    }


async def _financial_year(db: AsyncSession, company: Company, year: int) -> FinancialYear:
    found = await db.scalar(
        select(FinancialYear).where(
            FinancialYear.company_id == company.id, FinancialYear.year == year
        )
    )
    if found is None:
        raise FinancialYearNotFoundError(f"selskapet har ikke noe regnskapsår {year} i Myntos")
    return found


async def _unsettled_terms(db: AsyncSession, company: Company, year: int) -> list[str]:
    """MVA periods in the year whose 2700/2710 have not been moved onto 2740.

    The MVA module answers, because with two meldinger (a farm with other
    business beside it) the accounts' two-monthly windows would call every
    alminnelig term open until the yearly primærnæring one is settled; only
    each melding's own terms can say what is still to settle.
    """
    return await mva_service.unsettled_terms(db, company, year)


# --- The plan ------------------------------------------------------------------------


def _check_tax(tax: Decimal) -> str | None:
    if tax < ZERO:
        return "Skattekostnaden kan ikke være negativ."
    if tax != tax.quantize(_CENT):
        return "Skattekostnaden har brøkdeler av et øre."
    return None


def _as_disposition(result: Decimal, free_equity: Decimal, uncovered: Decimal) -> list[PlannedLine]:
    lines: list[PlannedLine] = []
    if result > ZERO:
        cover = min(result, max(uncovered, ZERO))
        if cover > ZERO:
            lines += [
                PlannedLine(AS_UNCOVERED, "Dekning av udekket tap", debit=cover),
                PlannedLine(UNCOVERED_LOSS, "Dekning av udekket tap", credit=cover),
            ]
        rest = result - cover
        if rest > ZERO:
            lines += [
                PlannedLine(AS_TRANSFER, "Overført til annen egenkapital", debit=rest),
                PlannedLine(SHARE_EQUITY, "Overført til annen egenkapital", credit=rest),
            ]
    elif result < ZERO:
        loss = -result
        covered = min(loss, max(free_equity, ZERO))
        if covered > ZERO:
            lines += [
                PlannedLine(SHARE_EQUITY, "Underskudd dekket av annen egenkapital", debit=covered),
                PlannedLine(AS_TRANSFER, "Underskudd dekket av annen egenkapital", credit=covered),
            ]
        rest = loss - covered
        if rest > ZERO:
            lines += [
                PlannedLine(UNCOVERED_LOSS, "Udekket tap", debit=rest),
                PlannedLine(AS_UNCOVERED, "Udekket tap", credit=rest),
            ]
    return lines


def _enk_disposition(result: Decimal, private: Decimal) -> list[PlannedLine]:
    lines: list[PlannedLine] = []
    if result > ZERO:
        lines += [
            PlannedLine(ENK_RESULT, "Årsresultat overført egenkapital", debit=result),
            PlannedLine(SHARE_EQUITY, "Årsresultat overført egenkapital", credit=result),
        ]
    elif result < ZERO:
        lines += [
            PlannedLine(SHARE_EQUITY, "Årsunderskudd overført egenkapital", debit=-result),
            PlannedLine(ENK_RESULT, "Årsunderskudd overført egenkapital", credit=-result),
        ]
    # Drawings are equity going out, not a balance of their own: closed against
    # the owner's equity so the next year's privatkonto shows that year's.
    if private > ZERO:
        lines += [
            PlannedLine(SHARE_EQUITY, "Privatkonto avsluttet mot egenkapital", debit=private),
            PlannedLine(PRIVATE_ACCOUNT, "Privatkonto avsluttet mot egenkapital", credit=private),
        ]
    elif private < ZERO:
        lines += [
            PlannedLine(PRIVATE_ACCOUNT, "Privatkonto avsluttet mot egenkapital", debit=-private),
            PlannedLine(SHARE_EQUITY, "Privatkonto avsluttet mot egenkapital", credit=-private),
        ]
    return lines


async def plan_closing(
    db: AsyncSession, *, company: Company, year: int, today: date, tax: Decimal = ZERO
) -> ClosingPlan:
    """Everything the review step shows, and everything :func:`close_year` will post."""
    financial_year = await _financial_year(db, company, year)
    form = normalised_form(company)
    findings: list[Finding] = []

    if financial_year.closed_at is not None:
        findings.append(feil("allerede_avsluttet", f"Regnskapsåret {year} er allerede avsluttet."))
    if today <= financial_year.end_date:
        findings.append(
            feil(
                "aaret_er_ikke_over",
                f"Regnskapsåret {year} varer til {financial_year.end_date:%d.%m.%Y}. Et år "
                "avsluttes etter at det er over, ellers låses det mens det fortsatt skjer ting.",
            )
        )
    earlier_open = await db.scalar(
        select(func.min(FinancialYear.year)).where(
            FinancialYear.company_id == company.id,
            FinancialYear.year < year,
            FinancialYear.closed_at.is_(None),
        )
    )
    if earlier_open is not None:
        findings.append(
            feil(
                "tidligere_aar_aapent",
                f"Regnskapsåret {int(earlier_open)} er ikke avsluttet. Årene avsluttes i "
                "rekkefølge, fordi egenkapitalen ved årets start kommer fra året før.",
            )
        )
    if form is None:
        findings.append(
            feil(
                "ukjent_organisasjonsform",
                "Selskapet har ingen registrert organisasjonsform, og disponeringen av "
                "resultatet avhenger av den. Hent opplysningene fra Enhetsregisteret under "
                "Selskapet.",
            )
        )
    elif form not in SUPPORTED_FORMS:
        findings.append(
            feil(
                "organisasjonsform_ikke_stottet",
                f"Myntos avslutter foreløpig bare aksjeselskap (AS) og enkeltpersonforetak "
                f"(ENK). For {form} gjelder egne regler for hvordan resultatet fordeles, og "
                "en disponering som for et AS ville vært feil.",
            )
        )
    tax_problem = _check_tax(tax)
    if tax_problem is not None:
        findings.append(feil("ugyldig_skatt", tax_problem))
        tax = ZERO
    if tax > ZERO and form == "ENK":
        findings.append(
            feil(
                "skatt_for_enk",
                "Et enkeltpersonforetak betaler ikke skatt selv. Skatten er innehaverens og "
                "føres ikke i foretakets regnskap.",
            )
        )
        tax = ZERO

    this_year = await _net_by_account(db, company, year=year)
    # A year brought over from the previous program (myntos.saft.move) was
    # closed there, and its file may carry the tax cost and the disposition the
    # accountant booked. Those stand: they are the year as it was reported, and
    # an imported voucher is corrected in the old program, never here. Myntos
    # then disposes only what is left. Refusing such a year as already
    # disposed, which is what every closing account used to mean, left it open
    # for good, and every year after it with it, since years close in order.
    imported = await _net_by_account(db, company, year=year, journal=IMPORT_JOURNAL_ID)
    types = {
        account.account_number: account.account_type
        for account in await db.scalars(select(Account).where(Account.company_id == company.id))
    }
    revenue = ZERO
    costs = ZERO
    already = ZERO
    imported_tax = ZERO
    imported_disposed = ZERO
    for number, net in this_year.items():
        kind = types.get(number)
        if kind is None or kind.is_balance_account:
            continue
        if number in CLOSING_ACCOUNTS:
            booked = imported.get(number, ZERO)
            already += net - booked
            if number == TAX_COST:
                imported_tax += booked
            else:
                imported_disposed += booked
        elif kind is AccountType.INNTEKT:
            revenue -= net
        else:
            costs += net
    if already != ZERO:
        findings.append(
            feil(
                "allerede_disponert",
                f"Året har allerede {-already} på skatte- og disponeringskontoene "
                "(8300, 8800, 8960, 8990) som ikke er reversert. Myntos fører disse selv ved "
                "årsavslutningen.",
            )
        )
    if imported_tax != ZERO and tax > ZERO:
        findings.append(
            feil(
                "skatt_allerede_fort",
                f"Det gamle programmet har ført skattekostnad {imported_tax} for {year}. Den står, "
                "så oppgi ikke skatt på nytt her.",
            )
        )
        tax = ZERO
    if imported_tax != ZERO or imported_disposed != ZERO:
        findings.append(
            merknad(
                "avsluttet_i_tidligere_program",
                f"Bilagene fra det gamle programmet har skattekostnad {imported_tax} og "
                f"disponering {imported_disposed} for {year}. De står som de ble ført, og Myntos "
                "fører bare det som er igjen av resultatet.",
            )
        )
    result_before_tax = revenue - costs
    # What is left to dispose: after the tax stated here and the tax and
    # disposition the old program already booked.
    result = result_before_tax - tax - imported_tax - imported_disposed

    cumulative = await _net_by_account(db, company, up_to=year)
    free_equity = -cumulative.get(SHARE_EQUITY, ZERO)
    uncovered = cumulative.get(UNCOVERED_LOSS, ZERO)
    private = cumulative.get(PRIVATE_ACCOUNT, ZERO)

    tax_lines: list[PlannedLine] = []
    disposition: list[PlannedLine] = []
    if form == "AS":
        if tax > ZERO:
            tax_lines = [
                PlannedLine(TAX_COST, f"Betalbar skatt {year}", debit=tax),
                PlannedLine(TAX_PAYABLE, f"Betalbar skatt {year}", credit=tax),
            ]
        elif result_before_tax > ZERO and imported_tax == ZERO:
            findings.append(
                merknad(
                    "ingen_skattekostnad",
                    "Det er ikke ført noen skattekostnad. Et aksjeselskap med overskudd skal "
                    "normalt kostnadsføre betalbar skatt før resultatet disponeres. Myntos "
                    "beregner ikke skatten, og heller ikke utsatt skatt.",
                )
            )
        disposition = _as_disposition(result, free_equity, uncovered)
        if private != ZERO:
            findings.append(
                merknad(
                    "privatkonto_i_as",
                    f"Konto {PRIVATE_ACCOUNT} Privatkonto har en saldo på {private}. Et "
                    "aksjeselskap har ingen privatkonto: penger tatt ut til en aksjonær er lån "
                    "(aksjeloven § 8-7) eller utbytte (§ 8-1), og bør føres om. Saldoen står "
                    "urørt ved avslutningen.",
                )
            )
    elif form == "ENK":
        disposition = _enk_disposition(result, private)

    unsettled = await _unsettled_terms(db, company, year)
    if unsettled:
        findings.append(
            merknad(
                "mva_ikke_gjort_opp",
                "Mva for " + ", ".join(unsettled) + f" er ikke gjort opp mot 2740. Oppgjøret "
                f"føres i {year}, og det går ikke an å føre noe i et avsluttet år. Send meldingen "
                "fra Mva-siden først hvis den skal sendes fra Myntos.",
            )
        )
    first = await opening.first_year(db, company)
    if first is not None and first.year == year and not await opening.has_opening(db, company):
        findings.append(
            merknad(
                "ingen_inngaende_balanse",
                f"Det er ikke ført noen inngående balanse for {year}. Hvis selskapet drev før "
                "det kom til Myntos, mangler balansen ved årets start, og egenkapitalen "
                "disponeringen regner med er for lav.",
            )
        )
    # Imported here: anlegg reaches the ledger service, which reaches kontoplan,
    # which reaches this module, so a top-level import would close the circle.
    from myntos.domain.anlegg import avskrivning

    findings.extend(await avskrivning.closing_findings(db, company=company, year=year, today=today))
    # A farm's herd and feed are on the balance sheet at what they were counted
    # at, and the count is the year's last voucher before this one. A merknad,
    # not a feil: a farm that sold its last animal has nothing to count, and
    # only the person knows that. Imported here for the circle above.
    from myntos.domain.arsoppgjor import buskap
    from myntos.domain.ledger import landbruk

    if landbruk.is_enabled(company) and not await buskap.has_count(db, company=company, year=year):
        findings.append(
            merknad(
                "ingen_telling_buskap",
                f"Det er ikke ført noen telling av buskap og varelager for {year}. Dyr, fôr og "
                "korn står da i balansen med verdien fra i fjor. Før tellingen under Buskap og "
                "lager før året avsluttes, hvis gården har noe å telle.",
            )
        )

    return ClosingPlan(
        year=year,
        organisation_form=form,
        closed_at=financial_year.closed_at,
        revenue=revenue,
        costs=costs,
        result_before_tax=result_before_tax,
        tax=tax + imported_tax,
        result=result,
        tax_lines=tax_lines,
        disposition_lines=disposition,
        findings=findings,
    )


# --- Closing -------------------------------------------------------------------------


async def _journal(db: AsyncSession, company: Company) -> Journal:
    journal = await db.scalar(
        select(Journal).where(
            Journal.company_id == company.id, Journal.journal_id == CLOSING_JOURNAL_ID
        )
    )
    if journal is None:
        journal = Journal(
            company_id=company.id,
            journal_id=CLOSING_JOURNAL_ID,
            description=CLOSING_JOURNAL_DESCRIPTION,
            journal_type=CLOSING_JOURNAL_TYPE,
        )
        db.add(journal)
        await db.flush()
    return journal


async def _post_lines(
    db: AsyncSession,
    *,
    company: Company,
    journal: Journal,
    financial_year: FinancialYear,
    lines: Sequence[PlannedLine],
    description: str,
    voucher_type: str,
    created_by_user_id: int | None,
) -> Transaction:
    accounts = await ledger_service._accounts_by_number(db, company)
    missing = sorted({line.account_number for line in lines} - set(accounts))
    if missing:
        raise ChartIncompleteError(
            "kontoplanen mangler " + ", ".join(missing) + ", som årsavslutningen fører på"
        )
    end = financial_year.end_date
    transaction = Transaction(
        journal_id=journal.id,
        transaction_id=await ledger_service._next_voucher_number(
            db, journal.id, financial_year.year
        ),
        financial_year=financial_year.year,
        period=end.month,
        transaction_date=end,
        gl_posting_date=end,
        description=description,
        voucher_type=voucher_type,
        created_by_user_id=created_by_user_id,
    )
    transaction.lines = [
        TransactionLine(
            record_id=str(number),
            account=accounts[line.account_number],
            description=line.description,
            debit=line.debit,
            credit=line.credit,
        )
        for number, line in enumerate(lines, start=1)
    ]
    posting.post(transaction)
    db.add(transaction)
    await db.flush()
    return transaction


async def close_year(
    db: AsyncSession,
    *,
    company: Company,
    year: int,
    today: date,
    tax: Decimal = ZERO,
    created_by_user_id: int | None = None,
) -> tuple[ClosingPlan, list[Transaction]]:
    """Post the plan, lock the year, log it, and open the next one.

    The plan is computed again here rather than accepted from the caller, so
    what is posted is what the rules say on the moment of posting, not what a
    page showed a minute ago.
    """
    plan = await plan_closing(db, company=company, year=year, today=today, tax=tax)
    problems = blocking(plan.findings)
    if problems:
        raise YearNotClosableError(
            f"regnskapsåret {year} kan ikke avsluttes ennå. Se funnene", problems
        )
    financial_year = await _financial_year(db, company, year)
    journal = await _journal(db, company)
    posted: list[Transaction] = []
    if plan.tax_lines:
        posted.append(
            await _post_lines(
                db,
                company=company,
                journal=journal,
                financial_year=financial_year,
                lines=plan.tax_lines,
                description=f"Skattekostnad {year}",
                voucher_type=TAX_VOUCHER_TYPE,
                created_by_user_id=created_by_user_id,
            )
        )
    if plan.disposition_lines:
        posted.append(
            await _post_lines(
                db,
                company=company,
                journal=journal,
                financial_year=financial_year,
                lines=plan.disposition_lines,
                description=f"Årsavslutning {year}: disponering av resultatet",
                voucher_type=DISPOSITION_VOUCHER_TYPE,
                created_by_user_id=created_by_user_id,
            )
        )
    financial_year.closed_at = utc_now()
    db.add(
        FinancialYearEvent(
            company_id=company.id,
            financial_year=year,
            action=YearAction.AVSLUTTET.value,
            user_id=created_by_user_id,
            result=plan.result,
            tax=plan.tax,
        )
    )
    await db.flush()
    # The next year opens now rather than on its first posting, so the
    # opening balance SAF-T derives for it has a year to belong to, and so
    # "what comes after 2026" has an answer on the page that just closed it.
    await ledger_service.ensure_financial_year(db, company, year + 1)
    return plan, posted


# --- Reopening -----------------------------------------------------------------------


async def reopen_year(
    db: AsyncSession,
    *,
    company: Company,
    year: int,
    reason: str,
    confirmed: bool,
    created_by_user_id: int | None = None,
) -> list[Transaction]:
    """Unlock a closed year by reversing its closing, and say who and why."""
    financial_year = await _financial_year(db, company, year)
    if financial_year.closed_at is None:
        raise YearNotReopenableError(f"regnskapsåret {year} er ikke avsluttet")
    later_closed = await db.scalar(
        select(func.min(FinancialYear.year)).where(
            FinancialYear.company_id == company.id,
            FinancialYear.year > year,
            FinancialYear.closed_at.is_not(None),
        )
    )
    if later_closed is not None:
        raise YearNotReopenableError(
            f"regnskapsåret {int(later_closed)} er avsluttet og bygger på {year}. "
            f"Gjenåpne {int(later_closed)} først"
        )
    cleaned = reason.strip()
    if len(cleaned) < 10 or not confirmed:
        raise ReopeningReasonRequiredError(
            "gjenåpning av et avsluttet år krever en begrunnelse og en bekreftelse på at "
            "årsregnskapet ikke er fastsatt. Er det fastsatt, rettes feilen i inneværende år"
        )

    financial_year.closed_at = None
    await db.flush()

    reversal = aliased(Transaction, name="closing_reversal")
    standing = list(
        await db.scalars(
            select(Transaction.id)
            .join(Journal, Journal.id == Transaction.journal_id)
            .where(
                Journal.company_id == company.id,
                Journal.journal_id == CLOSING_JOURNAL_ID,
                Transaction.financial_year == year,
                Transaction.posted_at.is_not(None),
                Transaction.reverses_transaction_pk.is_(None),
                ~select(reversal.id)
                .where(reversal.reverses_transaction_pk == Transaction.id)
                .exists(),
            )
            .order_by(
                # The disposition first, then the tax: undone in the opposite
                # order of the doing, so the voucher numbers read as a story.
                case((Transaction.voucher_type == DISPOSITION_VOUCHER_TYPE, 0), else_=1),
                Transaction.id.desc(),
            )
        )
    )
    reversals: list[Transaction] = []
    for voucher_pk in standing:
        original = await ledger_service.get_voucher(db, company=company, voucher_pk=voucher_pk)
        undone = posting.build_reversal(
            original,
            transaction_id=await ledger_service._next_voucher_number(db, original.journal_id, year),
            gl_posting_date=original.gl_posting_date,
            period=original.period,
            financial_year=year,
            description=f"Gjenåpning {year}: reversering av {original.transaction_id}",
        )
        undone.reverses_transaction_pk = original.id
        undone.created_by_user_id = created_by_user_id
        posting.post(undone)
        db.add(undone)
        await db.flush()
        reversals.append(undone)

    db.add(
        FinancialYearEvent(
            company_id=company.id,
            financial_year=year,
            action=YearAction.GJENAPNET.value,
            user_id=created_by_user_id,
            reason=cleaned[:1000],
        )
    )
    await db.flush()
    return reversals


# --- The years ------------------------------------------------------------------------


async def list_years(db: AsyncSession, *, company: Company) -> list[YearStatus]:
    years = list(
        await db.scalars(
            select(FinancialYear)
            .where(FinancialYear.company_id == company.id)
            .order_by(FinancialYear.year)
        )
    )
    events = list(
        await db.scalars(
            select(FinancialYearEvent)
            .where(FinancialYearEvent.company_id == company.id)
            .order_by(FinancialYearEvent.id)
        )
    )
    return [
        YearStatus(
            year=fy.year,
            start_date=fy.start_date,
            end_date=fy.end_date,
            closed_at=fy.closed_at,
            events=[e for e in events if e.financial_year == fy.year],
        )
        for fy in years
    ]
