"""The MVA-melding against the database: build it, keep what was filed, settle it.

Like the ledger service, nothing here commits and nothing here talks to
Skatteetaten. The API layer does the talking, because the government login it
needs is an API-layer fact, and calls in here before and after: to build the
melding, to record what validation said, to record that it was filed, and to
store what came back.

**Which lines belong to a period** is decided by ``gl_posting_date``, the date
the entry hits the ledger. A reversal keeps its original's posting date
(:func:`myntos.domain.ledger.service.reverse_cash_flow`), so a corrected entry
nets out in the period it was booked in rather than leaving a hole there and a
bump somewhere else, which is what a filed period has to be corrected for.

**Settlement.** Filing a melding turns the period's 2700 and 2710 into one
amount owed to or by Skatteetaten. :func:`post_settlement` books exactly that:
the period's balances on the two VAT accounts moved onto 2740, where the
payment category ``mva_oppgjor`` (or ``mva_tilgode``) later clears it. Without
it 2700 and 2710 grow forever and 2740 swings negative with every payment, which
is a balance sheet that is right in total and wrong on every line.

The balances it moves are summed *including* any earlier settlement in the
same period. So a correction filed after a late reversal settles the
difference and nothing else, without this code having to know about
corrections at all.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import ColumnElement, CompoundSelect, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from myntos.core.clock import utc_now
from myntos.domain.ledger import kontoplan, posting
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.chart import (
    INPUT_VAT_ACCOUNT,
    OUTPUT_VAT_ACCOUNT,
    ROUNDING_ACCOUNT,
    SETTLEMENT_JOURNAL_DESCRIPTION,
    SETTLEMENT_JOURNAL_ID,
    SETTLEMENT_JOURNAL_TYPE,
    SETTLEMENT_VOUCHER_TYPE,
    VAT_SETTLEMENT_ACCOUNT,
)
from myntos.domain.ledger.models import (
    Account,
    Company,
    Journal,
    TaxCode,
    Transaction,
    TransactionLine,
)
from myntos.domain.mva import melding as melding_module
from myntos.domain.mva import naering as naering_module
from myntos.domain.mva.codes import SUPPORTED_CATEGORIES, Meldingskategori
from myntos.domain.mva.errors import (
    FilingNotFoundError,
    InvalidPeriodError,
    MeldingNotBuildableError,
    UnsupportedCategoryError,
)
from myntos.domain.mva.melding import Melding, TaxedLine, Valideringsresultat
from myntos.domain.mva.models import FilingStatus, MvaFiling, MvaFilingDocument, MvaFilingElsewhere
from myntos.domain.mva.periods import (
    PeriodType,
    Termin,
    UnknownPeriodError,
    parse_period_type,
    terminer,
)

SETTLEMENT_ACCOUNT = "2740"

ZERO = Decimal("0.00")


# --- Settings ---------------------------------------------------------------------


def period_type_of(company: Company) -> PeriodType:
    """The main melding's term length. A second melding has its own (:mod:`.naering`)."""
    return naering_module.main_period_type(company)


def category_of(company: Company) -> Meldingskategori:
    """The main melding's category. A second melding has the other one (:mod:`.naering`)."""
    return naering_module.main_category(company)


def update_settings(
    company: Company, *, period_type: str | None = None, category: str | None = None
) -> None:
    if period_type is not None:
        try:
            company.vat_period_type = parse_period_type(period_type).value
        except UnknownPeriodError as error:
            raise InvalidPeriodError(str(error)) from error
    if category is not None:
        try:
            chosen = Meldingskategori(category)
        except ValueError as error:
            raise UnsupportedCategoryError(f"ukjent meldingskategori: {category!r}") from error
        if chosen not in SUPPORTED_CATEGORIES:
            raise UnsupportedCategoryError(
                f"Myntos sender ikke mva-melding av typen «{chosen.value}» ennå"
            )
        company.vat_category = chosen.value


def termin_of(company: Company, year: int, index: int) -> Termin:
    try:
        return Termin(period_type_of(company), year, index)
    except UnknownPeriodError as error:
        raise InvalidPeriodError(str(error)) from error


# --- Building the melding ---------------------------------------------------------


_LineRow = tuple[
    date, str, Decimal | None, Decimal, Decimal | None, Decimal | None, Decimal | None, str | None
]


def _company_lines(company_id: int) -> Select[_LineRow]:
    """Posted lines carrying a reportable tax code, with their posting date."""
    return (
        select(
            Transaction.gl_posting_date,
            TaxCode.standard_tax_code,
            TransactionLine.tax_rate,
            TransactionLine.debit,
            TransactionLine.tax_base,
            TransactionLine.debit_tax_amount,
            TransactionLine.credit_tax_amount,
            TransactionLine.vat_specification,
        )
        .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
        .join(Journal, Journal.id == Transaction.journal_id)
        .join(TaxCode, TaxCode.id == TransactionLine.tax_code_id)
        .where(
            Journal.company_id == company_id,
            Transaction.posted_at.is_not(None),
            TaxCode.is_reportable.is_(True),
        )
    )


def _taxed(row: Sequence[object]) -> TaxedLine:
    # Read defensively: SQLite hands back what it stored, and a NULL tax column
    # on a line with a tax code is a broken posting that should count as zero
    # here rather than crash the page that would show it.
    _, code, rate, debit, base, debit_tax, credit_tax, spesifikasjon = row
    return TaxedLine(
        code=str(code),
        rate=rate if isinstance(rate, Decimal) else ZERO,
        is_debit=isinstance(debit, Decimal) and debit > ZERO,
        base=base if isinstance(base, Decimal) else ZERO,
        debit_tax=debit_tax if isinstance(debit_tax, Decimal) else ZERO,
        credit_tax=credit_tax if isinstance(credit_tax, Decimal) else ZERO,
        spesifikasjon=spesifikasjon if isinstance(spesifikasjon, str) else None,
    )


async def taxed_lines(
    db: AsyncSession, company: Company, start: date, end: date
) -> list[tuple[date, TaxedLine]]:
    rows = await db.execute(
        _company_lines(company.id).where(
            Transaction.gl_posting_date >= start, Transaction.gl_posting_date <= end
        )
    )
    return [(row[0], _taxed(row)) for row in rows]


async def taxed_lines_by_naering(
    db: AsyncSession,
    company: Company,
    start: date,
    end: date,
    setup: naering_module.Naeringer,
) -> list[tuple[date, Meldingskategori, TaxedLine]]:
    """The same lines, each with the melding it belongs to (:mod:`.naering`)."""
    rows = await db.execute(
        _company_lines(company.id)
        .add_columns(Account.account_number)
        .join(Account, Account.id == TransactionLine.account_id)
        .where(Transaction.gl_posting_date >= start, Transaction.gl_posting_date <= end)
    )
    return [(row[0], setup.of_line(row[0], str(row[8])), _taxed(tuple(row)[:8])) for row in rows]


def _build(
    company: Company,
    termin: Termin,
    lines: list[TaxedLine],
    category: Meldingskategori | None = None,
) -> Melding:
    try:
        return melding_module.build(
            lines,
            termin=termin,
            category=category if category is not None else category_of(company),
            orgnr=company.orgnr,
        )
    except melding_module.MeldingError as error:
        raise MeldingNotBuildableError(str(error)) from error


async def build_melding(
    db: AsyncSession,
    company: Company,
    termin: Termin,
    naering: naering_module.Naering | None = None,
    setup: naering_module.Naeringer | None = None,
) -> Melding:
    """One term's melding as the books have it now: the main melding's unless ``naering`` says.

    With one melding every reportable line of the term is in it, exactly as
    before there could be two. With two, each line goes to the melding its
    account and date say (:meth:`~.naering.Naeringer.of_line`).
    """
    if setup is None:
        setup = await naering_module.load(db, company)
    chosen = naering if naering is not None else setup.main
    if not setup.is_split:
        rows = await taxed_lines(db, company, termin.start, termin.end)
        return _build(company, termin, [line for _, line in rows], chosen.category)
    split = await taxed_lines_by_naering(db, company, termin.start, termin.end, setup)
    return _build(
        company,
        termin,
        [line for _, category, line in split if category is chosen.category],
        chosen.category,
    )


async def build_all_naeringer(db: AsyncSession, company: Company, termin: Termin) -> Melding:
    """Every reportable line in the term's dates, whichever melding it belongs to.

    Not a melding anybody files. It is what the VAT accounts 2700 and 2710 hold
    for those dates, so it is what the reconciliation compares them with: the
    accounts cannot tell the two meldinger apart, and a difference between them
    and *all* the coded lines is still exactly "something was booked on a VAT
    account without a code".
    """
    rows = await taxed_lines(db, company, termin.start, termin.end)
    return _build(company, termin, [line for _, line in rows])


def content_hash(melding: Melding) -> str:
    """A fingerprint of what the melding *says*, for "has the ledger moved".

    Over the figures, the period, the category and the orgnr, and nothing that
    differs between two builds of the same thing.
    """
    payload = {
        "orgnr": melding.orgnr,
        "category": melding.category.value,
        "period": [melding.termin.period_type.value, melding.termin.year, melding.termin.index],
        "lines": [
            [
                line.mva_kode,
                line.spesifikasjon,
                None if line.grunnlag is None else str(line.grunnlag),
                None if line.sats is None else str(line.sats),
                str(line.merverdiavgift),
            ]
            for line in melding.lines
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def lines_snapshot(melding: Melding) -> list[dict[str, object]]:
    return [
        {
            "mva_kode": line.mva_kode,
            "spesifikasjon": line.spesifikasjon,
            "grunnlag": None if line.grunnlag is None else str(line.grunnlag),
            "sats": None if line.sats is None else str(line.sats),
            "merverdiavgift": str(line.merverdiavgift),
        }
        for line in melding.lines
    ]


# --- Reconciling against the VAT accounts -----------------------------------------


@dataclass(frozen=True)
class VatBalances:
    """Output VAT owed (2700, credit side) and input VAT claimed (2710, debit)."""

    output: Decimal
    input: Decimal

    @property
    def net(self) -> Decimal:
        return self.output - self.input


async def vat_balances(
    db: AsyncSession, company: Company, termin: Termin, *, include_settlement: bool
) -> VatBalances:
    query = (
        select(
            Account.account_number,
            func.sum(TransactionLine.debit),
            func.sum(TransactionLine.credit),
        )
        .join(TransactionLine, TransactionLine.account_id == Account.id)
        .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
        .join(Journal, Journal.id == Transaction.journal_id)
        .where(
            Journal.company_id == company.id,
            Transaction.posted_at.is_not(None),
            Transaction.gl_posting_date >= termin.start,
            Transaction.gl_posting_date <= termin.end,
            Account.account_number.in_((OUTPUT_VAT_ACCOUNT, INPUT_VAT_ACCOUNT)),
        )
        .group_by(Account.account_number)
    )
    if not include_settlement:
        query = query.where(*ledger_service.not_a_settlement())
    output = ZERO
    input_ = ZERO
    for number, debit, credit in await db.execute(query):
        debit = debit or ZERO
        credit = credit or ZERO
        if number == OUTPUT_VAT_ACCOUNT:
            output = credit - debit
        else:
            input_ = debit - credit
    return VatBalances(output=output, input=input_)


# --- Overview ---------------------------------------------------------------------


@dataclass(frozen=True)
class PeriodSummary:
    termin: Termin
    deadline: date
    melding: Melding
    # The newest row for the term that still stands: a statement taken back
    # (``opphevet``) is history, and says nothing about the term now.
    latest: MvaFiling | None
    # The most recent filing that actually reached Skatteetaten, which is the
    # one the ledger is compared against.
    filed: MvaFiling | None
    # A standing «filed from another program» statement, if there is one.
    elsewhere: MvaFiling | None = None

    @property
    def changed_since_filed(self) -> bool:
        return self.filed is not None and self.filed.content_hash != content_hash(self.melding)

    @property
    def closed(self) -> bool:
        """Filed from Myntos or said to be filed elsewhere: nothing is due here."""
        return self.filed is not None or self.elsewhere is not None


def standing(filings: Sequence[MvaFiling]) -> list[MvaFiling]:
    """The rows that still say something about the term, newest first."""
    return [f for f in filings if f.status != FilingStatus.OPPHEVET.value]


def standing_elsewhere(filings: Sequence[MvaFiling]) -> MvaFiling | None:
    return next((f for f in filings if f.status == FilingStatus.ANNET_PROGRAM.value), None)


def _filings_where(
    company: Company,
    chosen: naering_module.Naering,
    setup: naering_module.Naeringer,
) -> tuple[ColumnElement[bool], ...]:
    """Which filing rows belong to ``chosen``'s terms.

    With one melding, every row of the term length, whatever its category: a
    company that changed category kept seeing what it had filed before, and a
    company with one melding still does. With two, the category tells them
    apart, since both may well file every two months.
    """
    where: tuple[ColumnElement[bool], ...] = (
        MvaFiling.company_id == company.id,
        MvaFiling.period_type == chosen.period_type.value,
    )
    if setup.is_split:
        where += (MvaFiling.category == chosen.category.value,)
    return where


async def year_overview(
    db: AsyncSession,
    company: Company,
    year: int,
    naering: naering_module.Naering | None = None,
    setup: naering_module.Naeringer | None = None,
) -> list[PeriodSummary]:
    """Every period of ``year`` of one melding, each as the ledger has it now.

    One query for the lines and one for the filings, partitioned here, rather
    than a pair per period: a monthly filer has twelve. The second melding has
    no terms before the day it starts.
    """
    if setup is None:
        setup = await naering_module.load(db, company)
    chosen = naering if naering is not None else setup.main
    periods = chosen.terminer(year)
    if not periods:
        return []
    if setup.is_split:
        split = await taxed_lines_by_naering(db, company, periods[0].start, periods[-1].end, setup)
        rows = [(day, line) for day, category, line in split if category is chosen.category]
    else:
        rows = await taxed_lines(db, company, periods[0].start, periods[-1].end)
    filings = list(
        await db.scalars(
            select(MvaFiling)
            .where(*_filings_where(company, chosen, setup), MvaFiling.year == year)
            .order_by(MvaFiling.id.desc())
        )
    )

    summaries: list[PeriodSummary] = []
    for termin in periods:
        lines = [line for day, line in rows if termin.contains(day)]
        mine = standing([f for f in filings if f.period_index == termin.index])
        summaries.append(
            PeriodSummary(
                termin=termin,
                deadline=termin.deadline(chosen.category),
                melding=_build(company, termin, lines, chosen.category),
                latest=mine[0] if mine else None,
                filed=next((f for f in mine if FilingStatus(f.status).is_filed), None),
                elsewhere=standing_elsewhere(mine),
            )
        )
    return summaries


async def closing_filings(
    db: AsyncSession,
    company: Company,
    naering: naering_module.Naering,
    setup: naering_module.Naeringer,
    years: Sequence[int],
) -> dict[tuple[int, int], MvaFiling]:
    """The row that closes each term of ``naering`` in ``years``, keyed by (year, index).

    Without building a melding: the deadline list asks only whether each term
    is done, and computing every term's figures from the ledger to answer that
    would cost the whole year's lines per year asked. A filing from Myntos is
    preferred over a statement that another program filed it, because it is
    the one with a receipt.
    """
    filings = list(
        await db.scalars(
            select(MvaFiling)
            .where(*_filings_where(company, naering, setup), MvaFiling.year.in_(list(years)))
            .order_by(MvaFiling.id.desc())
        )
    )
    closing: dict[tuple[int, int], MvaFiling] = {}
    for key in {(f.year, f.period_index) for f in filings}:
        mine = standing([f for f in filings if (f.year, f.period_index) == key])
        found = next((f for f in mine if FilingStatus(f.status).is_filed), None)
        found = found or standing_elsewhere(mine)
        if found is not None:
            closing[key] = found
    return closing


async def filings_for(
    db: AsyncSession,
    company: Company,
    termin: Termin,
    naering: naering_module.Naering | None = None,
    setup: naering_module.Naeringer | None = None,
) -> list[MvaFiling]:
    """Every row for one term of one melding, newest first, statements taken back included."""
    if setup is None:
        setup = await naering_module.load(db, company)
    chosen = naering if naering is not None else setup.main
    return list(
        await db.scalars(
            select(MvaFiling)
            .where(
                *_filings_where(company, chosen, setup),
                MvaFiling.year == termin.year,
                MvaFiling.period_index == termin.index,
            )
            .options(selectinload(MvaFiling.documents))
            .order_by(MvaFiling.id.desc())
        )
    )


async def get_filing(
    db: AsyncSession, company: Company, filing_id: int, *, lock: bool = False
) -> MvaFiling:
    """One filing, looked up through its company like every sub-resource.

    ``lock`` takes the row for the rest of the request (``SELECT … FOR UPDATE``
    on PostgreSQL; SQLite has one writer anyway). The submission asks for it,
    because two presses of the button that both read "validert" before either
    wrote "sendt" would file the melding twice.
    """
    query = (
        select(MvaFiling)
        .where(MvaFiling.id == filing_id, MvaFiling.company_id == company.id)
        .options(selectinload(MvaFiling.documents))
    )
    if lock:
        query = query.with_for_update(of=MvaFiling)
    filing = await db.scalar(query)
    if filing is None:
        raise FilingNotFoundError("finner ikke mva-meldingen")
    return filing


def termin_of_filing(filing: MvaFiling) -> Termin:
    return Termin(parse_period_type(filing.period_type), filing.year, filing.period_index)


# --- Recording what happened ------------------------------------------------------


async def record_validation(
    db: AsyncSession,
    *,
    company: Company,
    melding: Melding,
    reference: str,
    xml: bytes,
    validation_xml: bytes,
    result: Valideringsresultat,
    merknad: str | None,
    user_id: int | None,
) -> MvaFiling:
    filing = MvaFiling(
        company_id=company.id,
        year=melding.termin.year,
        period_type=melding.termin.period_type.value,
        period_index=melding.termin.index,
        category=melding.category.value,
        status=(FilingStatus.AVVIST if result.blocks_submission else FilingStatus.VALIDERT).value,
        reference=reference,
        melding_xml=xml.decode("utf-8"),
        content_hash=content_hash(melding),
        fastsatt_merverdiavgift=melding.fastsatt_merverdiavgift,
        lines=lines_snapshot(melding),
        merknad=merknad,
        validation_status=result.status[:64],
        validation_xml=validation_xml.decode("utf-8", errors="replace"),
        validated_at=utc_now(),
        created_by_user_id=user_id,
        documents=[],
    )
    db.add(filing)
    await db.flush()
    return filing


async def record_document(
    db: AsyncSession,
    filing: MvaFiling,
    *,
    data_type: str,
    filename: str,
    content_type: str,
    content: bytes,
    storage_key: str,
) -> MvaFilingDocument:
    document = MvaFilingDocument(
        filing_id=filing.id,
        data_type=data_type,
        filename=filename,
        content_type=content_type,
        byte_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        storage_key=storage_key,
        fetched_at=utc_now(),
    )
    filing.documents.append(document)
    await db.flush()
    return document


def record_payment(filing: MvaFiling, payment: melding_module.Betalingsinformasjon) -> None:
    filing.payment_kid = payment.kid
    filing.payment_amount = payment.beloep
    filing.payment_due = payment.betalingsfrist
    filing.payment_account = payment.kontonummer


# --- Settlement -------------------------------------------------------------------


async def _settlement_journal(db: AsyncSession, company: Company) -> Journal:
    journal = await db.scalar(
        select(Journal).where(
            Journal.company_id == company.id, Journal.journal_id == SETTLEMENT_JOURNAL_ID
        )
    )
    if journal is None:
        journal = Journal(
            company_id=company.id,
            journal_id=SETTLEMENT_JOURNAL_ID,
            description=SETTLEMENT_JOURNAL_DESCRIPTION,
            journal_type=SETTLEMENT_JOURNAL_TYPE,
        )
        db.add(journal)
        await db.flush()
    return journal


def _side(
    record_id: str, account: Account, amount: Decimal, *, debit: bool, text: str
) -> TransactionLine:
    """A line for ``amount``, flipped to the other side when it is negative."""
    if amount < ZERO:
        amount = -amount
        debit = not debit
    return TransactionLine(
        record_id=record_id,
        account=account,
        description=text,
        debit=amount if debit else ZERO,
        credit=ZERO if debit else amount,
    )


def _settlement_vouchers(
    company: Company, termin: Termin, category: Meldingskategori
) -> CompoundSelect[tuple[int | None]]:
    """The vouchers that earlier filings of this term of this melding settled it with.

    A term recorded as filed from another program counts the old program's own
    settlement, when the statement was made from the voucher the import brought
    over: that voucher moved the term's VAT onto 2740 as surely as one of
    Myntos's.
    """
    term = (
        MvaFiling.company_id == company.id,
        MvaFiling.year == termin.year,
        MvaFiling.period_type == termin.period_type.value,
        MvaFiling.period_index == termin.index,
        MvaFiling.category == category.value,
    )
    own = select(MvaFiling.settlement_transaction_pk).where(
        *term, MvaFiling.settlement_transaction_pk.is_not(None)
    )
    theirs = (
        select(MvaFilingElsewhere.voucher_pk)
        .join(MvaFiling, MvaFiling.id == MvaFilingElsewhere.filing_id)
        .where(
            *term,
            MvaFiling.status == FilingStatus.ANNET_PROGRAM.value,
            MvaFilingElsewhere.voucher_pk.is_not(None),
        )
    )
    return own.union(theirs)


async def _settled_already(
    db: AsyncSession, company: Company, termin: Termin, category: Meldingskategori
) -> VatBalances:
    """What earlier filings of this term of this melding already moved onto 2740.

    Read from the settlement vouchers those filings point at, not from the VAT
    accounts: with two meldinger the accounts hold both meldingers' VAT for the
    same dates and cannot say whose a krone is.
    """
    rows = await db.execute(
        select(
            Account.account_number,
            func.sum(TransactionLine.debit),
            func.sum(TransactionLine.credit),
        )
        .join(TransactionLine, TransactionLine.account_id == Account.id)
        .where(
            Account.company_id == company.id,
            TransactionLine.transaction_pk.in_(_settlement_vouchers(company, termin, category)),
            Account.account_number.in_((OUTPUT_VAT_ACCOUNT, INPUT_VAT_ACCOUNT)),
        )
        .group_by(Account.account_number)
    )
    output = ZERO
    input_ = ZERO
    for number, debit, credit in rows:
        # A settlement debits 2700 by the output VAT it moves and credits 2710
        # by the input VAT, so these are the amounts moved, signed as owed.
        if number == OUTPUT_VAT_ACCOUNT:
            output = (debit or ZERO) - (credit or ZERO)
        else:
            input_ = (credit or ZERO) - (debit or ZERO)
    return VatBalances(output=output, input=input_)


async def _rounding_to_settle(
    db: AsyncSession,
    company: Company,
    termin: Termin,
    naering: naering_module.Naering,
    setup: naering_module.Naeringer,
) -> Decimal:
    """What a settlement of this term puts on 7740 now, credited when positive.

    The melding's rounding to whole kroner, less what this term's earlier
    settlements already put there. Then 2740 holds, after every filing of the
    term, exactly what the newest one reported, which is what Skatteetaten
    assesses and what the payment clears. That includes an earlier settlement
    by the previous program: its own rounding on 7740 is counted like Myntos's.
    """
    melding = await build_melding(db, company, termin, naering, setup)
    debit, credit = (
        await db.execute(
            select(func.sum(TransactionLine.debit), func.sum(TransactionLine.credit))
            .join(Account, Account.id == TransactionLine.account_id)
            .where(
                Account.company_id == company.id,
                TransactionLine.transaction_pk.in_(
                    _settlement_vouchers(company, termin, naering.category)
                ),
                Account.account_number == ROUNDING_ACCOUNT,
            )
        )
    ).one()
    return melding.rounding - ((credit or ZERO) - (debit or ZERO))


async def to_settle(
    db: AsyncSession,
    company: Company,
    termin: Termin,
    naering: naering_module.Naering | None = None,
    setup: naering_module.Naeringer | None = None,
) -> VatBalances:
    """What a settlement of this term would move onto 2740 now.

    **One melding:** the term's balances on 2700 and 2710, settlements
    included, exactly as it has always been. Whatever sits on the two accounts
    for the term's dates is the term's, so a correction moves the difference
    and an amount booked on a VAT account without a code is swept along with
    the rest.

    **Two meldinger:** the melding's own output and input VAT as booked, less
    what this melding's earlier settlements of the term moved. The accounts
    hold both meldingers' VAT and cannot be split, so the melding is the only
    thing that can say what is this melding's; an amount booked on 2700
    without a code stays there, where the reconciliation on the MVA page shows
    it. As booked, in øre, and not as reported: the kroner would leave the
    øre on 2700 and 2710 for good.
    """
    if setup is None:
        setup = await naering_module.load(db, company)
    if not setup.is_split:
        return await vat_balances(db, company, termin, include_settlement=True)
    chosen = naering if naering is not None else setup.main
    melding = await build_melding(db, company, termin, chosen, setup)
    already = await _settled_already(db, company, termin, chosen.category)
    return VatBalances(
        output=melding.booked_output_vat - already.output,
        input=melding.booked_input_vat - already.input,
    )


async def post_settlement(
    db: AsyncSession,
    *,
    company: Company,
    termin: Termin,
    created_by_user_id: int | None,
    naering: naering_module.Naering | None = None,
    setup: naering_module.Naeringer | None = None,
) -> Transaction | None:
    """Move the period's 2700 and 2710 onto 2740, dated the last day of the period.

    ``None`` when there is nothing to move, which is the ordinary case for a
    nullmelding and for a correction that changed nothing on the VAT accounts.

    With two meldinger each is settled in a voucher of its own, onto the same
    2740, and the voucher's text names the melding. One account is enough: no
    rule asks for one per melding, Skatteetaten answers each filing with its own
    payment information (KID, amount, due date), and the two payment categories
    that clear 2740 (``mva_oppgjor``, ``mva_tilgode``) then need no choice of
    melding. What 2740 holds is what the company owes or is owed in VAT, in
    total, which is the figure the balance sheet wants.

    The melding is in whole kroner and 2700 and 2710 are not, so 2740 is
    credited with what was reported and the øre between the two go on 7740
    (:func:`_rounding_to_settle`). Without that the payment, which is in
    kroner, would leave a few øre on 2740 every term.
    """
    if setup is None:
        setup = await naering_module.load(db, company)
    chosen = naering if naering is not None else setup.main
    balances = await to_settle(db, company, termin, chosen, setup)
    rounding = await _rounding_to_settle(db, company, termin, chosen, setup)
    if balances.output == ZERO and balances.input == ZERO and rounding == ZERO:
        return None

    if rounding != ZERO:
        await kontoplan.ensure_seeded_account(db, company=company, number=ROUNDING_ACCOUNT)
    accounts = await ledger_service._accounts_by_number(db, company)
    # Dated on the term's last day, unless the company was wound up inside the
    # term: its books end on that day, and nothing may be dated after it
    # (company.wound_up_on).
    on = termin.end if company.wound_up_on is None else min(termin.end, company.wound_up_on)
    financial_year = await ledger_service.ensure_financial_year(db, company, on.year, on=on)
    journal = await _settlement_journal(db, company)
    text = (
        f"Mva-oppgjør {chosen.label.lower()} {termin.label}"
        if setup.is_split
        else f"Mva-oppgjør {termin.label}"
    )[:255]

    lines: list[TransactionLine] = []
    if balances.output != ZERO:
        lines.append(
            _side("1", accounts[OUTPUT_VAT_ACCOUNT], balances.output, debit=True, text=text)
        )
    if balances.input != ZERO:
        lines.append(
            _side("2", accounts[INPUT_VAT_ACCOUNT], balances.input, debit=False, text=text)
        )
    if balances.net - rounding != ZERO:
        lines.append(
            _side(
                "3", accounts[SETTLEMENT_ACCOUNT], balances.net - rounding, debit=False, text=text
            )
        )
    if rounding != ZERO:
        lines.append(_side("4", accounts[ROUNDING_ACCOUNT], rounding, debit=False, text=text))
    if len(lines) < 2:
        # Output and input equal and opposite on a single account is not a
        # shape the two-account sum can produce; refusing beats posting a
        # one-line voucher the engine would reject anyway.
        return None

    transaction = Transaction(
        journal_id=journal.id,
        transaction_id=await ledger_service._next_voucher_number(
            db, journal.id, financial_year.year
        ),
        financial_year=financial_year.year,
        period=on.month,
        transaction_date=on,
        gl_posting_date=on,
        description=text,
        voucher_type=SETTLEMENT_VOUCHER_TYPE,
        created_by_user_id=created_by_user_id,
    )
    transaction.lines = lines
    posting.post(transaction)
    db.add(transaction)
    await db.flush()
    return transaction


async def unsettled_terms(db: AsyncSession, company: Company, year: int) -> list[str]:
    """The year's terms whose VAT has not been moved onto 2740, for the year-end check.

    With one melding, the term's balances on 2700 and 2710, as the year-end has
    always asked. With two, each melding's own terms, by what is still to
    settle: a yearly primærnæring melding settles on 31 December what was booked
    in March, so the two-monthly windows of the accounts would call every
    alminnelig term open until the yearly one is filed.
    """
    if not company.vat_registered:
        return []
    setup = await naering_module.load(db, company)
    labels: list[str] = []
    if not setup.is_split:
        for termin in terminer(setup.main.period_type, year):
            balances = await vat_balances(db, company, termin, include_settlement=True)
            if balances.output != ZERO or balances.input != ZERO:
                labels.append(termin.label)
        return labels
    for chosen in setup.all:
        for termin in chosen.terminer(year):
            remaining = await to_settle(db, company, termin, chosen, setup)
            if remaining.output != ZERO or remaining.input != ZERO:
                labels.append(f"{termin.label} ({chosen.label.lower()})")
    return labels


def is_past(termin: Termin, today: date) -> bool:
    return today > termin.end


def latest_filed(filings: Sequence[MvaFiling]) -> MvaFiling | None:
    return next((f for f in filings if FilingStatus(f.status).is_filed), None)


# --- Payment ------------------------------------------------------------------------


@dataclass(frozen=True)
class Payment:
    """The voucher that paid what a filed melding owed, or took its refund in."""

    voucher_pk: int
    voucher_no: str
    date: date


@dataclass(frozen=True)
class Settlement:
    """What the books have paid of a term's latest filed melding.

    ``unpaid`` is what is still to pay, signed as a melding is (negative: a
    refund still to come in), and zero once it is settled. ``paid_by`` is the
    voucher that settled it, set only then.
    """

    unpaid: Decimal
    paid_by: Payment | None


async def payments(
    db: AsyncSession, company: Company, filed: Sequence[MvaFiling]
) -> dict[int, Settlement]:
    """``{filing id: what the books have paid of it}`` for the filed meldinger given.

    Nothing links a payment to the melding it pays. It is booked like any other
    payment («Oppgjør av mva-melding til Skatteetaten» in the register), on the
    day the money left, or arrives from a bank import or the previous
    program's books. So it is recognised by what it does: a voucher outside
    the settlement itself that moves an expected amount on 2740 the right way
    (debit for a payment, credit for a refund), dated after the term ended,
    neither reversed nor a reversal. Each voucher is used once, the earliest
    term first, so two terms with the same figure are not both paid by one
    payment.

    The expected amount follows the term's filed meldinger in the order they
    were sent. A correction replaces the melding, not the payment already
    made: a term filed at 10 000, paid, and corrected to 12 000 owes 2 000,
    and a correction down to 8 000 has 2 000 coming back. Matched against the
    latest figure alone, the paid first melding stopped counting the moment
    the correction went in, and «Før betalingen» came back offering the whole
    12 000 a second time.

    Only the latest filed melding of a term gets an answer; one a correction
    replaced has none, since what it owed is now the latest's to say. Pass any
    filed melding of the terms wanted: the ones before it are fetched here.
    Built 2026-11-02, so that the button goes away once the payment is in the
    books rather than inviting a second one. A payment split in two that
    matches neither step is not recognised, and the page goes on offering it.
    """
    if not filed:
        return {}
    wanted = {(f.year, f.period_type, f.period_index, f.category) for f in filed}
    every = await db.scalars(
        select(MvaFiling)
        .where(
            MvaFiling.company_id == company.id,
            MvaFiling.year.in_(sorted({key[0] for key in wanted})),
            MvaFiling.status.in_([s.value for s in FilingStatus if s.is_filed]),
        )
        .order_by(MvaFiling.id)
    )
    chains: dict[tuple[int, str, int, str], list[MvaFiling]] = {}
    for filing in every:
        key = (filing.year, filing.period_type, filing.period_index, filing.category)
        if key in wanted:
            chains.setdefault(key, []).append(filing)
    terms = sorted(chains.values(), key=lambda chain: termin_of_filing(chain[0]).end)
    if not terms:
        return {}
    reversed_ = select(Transaction.reverses_transaction_pk).where(
        Transaction.reverses_transaction_pk.is_not(None)
    )
    rows = await db.execute(
        select(
            Transaction.id,
            Transaction.transaction_id,
            Transaction.transaction_date,
            TransactionLine.debit,
            TransactionLine.credit,
        )
        .join(TransactionLine, TransactionLine.transaction_pk == Transaction.id)
        .join(Account, Account.id == TransactionLine.account_id)
        .join(Journal, Journal.id == Transaction.journal_id)
        .where(
            Journal.company_id == company.id,
            Account.account_number == VAT_SETTLEMENT_ACCOUNT,
            Transaction.posted_at.is_not(None),
            Transaction.transaction_date > termin_of_filing(terms[0][0]).end,
            Transaction.reverses_transaction_pk.is_(None),
            Transaction.id.not_in(reversed_),
            *ledger_service.not_a_settlement(),
        )
        .order_by(Transaction.transaction_date, Transaction.id)
    )
    # Signed as a melding is: a debit on 2740 pays what is owed (positive), a
    # credit takes a refund in (negative).
    moved = [
        (pk, no, day, (debit or ZERO) - (credit or ZERO)) for pk, no, day, debit, credit in rows
    ]
    found: dict[int, Settlement] = {}
    used: set[int] = set()
    for chain in terms:
        end = termin_of_filing(chain[0]).end
        paid = ZERO
        last: Payment | None = None
        for filing in chain:
            due = filing.fastsatt_merverdiavgift - paid
            if due == ZERO:
                continue
            for pk, no, day, amount in moved:
                if pk not in used and day > end and amount == due:
                    used.add(pk)
                    paid += amount
                    last = Payment(voucher_pk=pk, voucher_no=no, date=day)
                    break
        latest = chain[-1]
        unpaid = latest.fastsatt_merverdiavgift - paid
        found[latest.id] = Settlement(unpaid=unpaid, paid_by=last if unpaid == ZERO else None)
    return found
