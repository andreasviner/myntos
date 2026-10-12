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

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from myntos.core.clock import utc_now
from myntos.domain.ledger import posting
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.chart import (
    INPUT_VAT_ACCOUNT,
    OUTPUT_VAT_ACCOUNT,
    SETTLEMENT_JOURNAL_DESCRIPTION,
    SETTLEMENT_JOURNAL_ID,
    SETTLEMENT_JOURNAL_TYPE,
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
from myntos.domain.mva.codes import SUPPORTED_CATEGORIES, Meldingskategori
from myntos.domain.mva.errors import (
    FilingNotFoundError,
    InvalidPeriodError,
    MeldingNotBuildableError,
    UnsupportedCategoryError,
)
from myntos.domain.mva.melding import Melding, TaxedLine, Valideringsresultat
from myntos.domain.mva.models import FilingStatus, MvaFiling, MvaFilingDocument
from myntos.domain.mva.periods import (
    PeriodType,
    Termin,
    UnknownPeriodError,
    parse_period_type,
    terminer,
)

SETTLEMENT_ACCOUNT = "2740"
SETTLEMENT_VOUCHER_TYPE = "mva_avregning"

ZERO = Decimal("0.00")


# --- Settings ---------------------------------------------------------------------


def period_type_of(company: Company) -> PeriodType:
    try:
        return parse_period_type(company.vat_period_type)
    except UnknownPeriodError:
        # A value written by hand into the database. Two months is the law's
        # default, and the settings card shows the value so it can be fixed.
        return PeriodType.TO_MAANEDER


def category_of(company: Company) -> Meldingskategori:
    try:
        category = Meldingskategori(company.vat_category)
    except ValueError:
        return Meldingskategori.ALMINNELIG
    return category if category in SUPPORTED_CATEGORIES else Meldingskategori.ALMINNELIG


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


def _build(company: Company, termin: Termin, lines: list[TaxedLine]) -> Melding:
    try:
        return melding_module.build(
            lines, termin=termin, category=category_of(company), orgnr=company.orgnr
        )
    except melding_module.MeldingError as error:
        raise MeldingNotBuildableError(str(error)) from error


async def build_melding(db: AsyncSession, company: Company, termin: Termin) -> Melding:
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
        query = query.where(Journal.journal_id != SETTLEMENT_JOURNAL_ID)
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
    latest: MvaFiling | None
    # The most recent filing that actually reached Skatteetaten, which is the
    # one the ledger is compared against.
    filed: MvaFiling | None

    @property
    def changed_since_filed(self) -> bool:
        return self.filed is not None and self.filed.content_hash != content_hash(self.melding)


async def year_overview(db: AsyncSession, company: Company, year: int) -> list[PeriodSummary]:
    """Every period of ``year``, each with its melding as the ledger has it now.

    One query for the lines and one for the filings, partitioned here, rather
    than a pair per period: a monthly filer has twelve.
    """
    period_type = period_type_of(company)
    category = category_of(company)
    periods = terminer(period_type, year)
    rows = await taxed_lines(db, company, periods[0].start, periods[-1].end)
    filings = list(
        await db.scalars(
            select(MvaFiling)
            .where(
                MvaFiling.company_id == company.id,
                MvaFiling.year == year,
                MvaFiling.period_type == period_type.value,
            )
            .order_by(MvaFiling.id.desc())
        )
    )

    summaries: list[PeriodSummary] = []
    for termin in periods:
        lines = [line for day, line in rows if termin.contains(day)]
        mine = [f for f in filings if f.period_index == termin.index]
        summaries.append(
            PeriodSummary(
                termin=termin,
                deadline=termin.deadline(category),
                melding=_build(company, termin, lines),
                latest=mine[0] if mine else None,
                filed=next((f for f in mine if FilingStatus(f.status).is_filed), None),
            )
        )
    return summaries


async def filings_for(db: AsyncSession, company: Company, termin: Termin) -> list[MvaFiling]:
    return list(
        await db.scalars(
            select(MvaFiling)
            .where(
                MvaFiling.company_id == company.id,
                MvaFiling.year == termin.year,
                MvaFiling.period_type == termin.period_type.value,
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


async def post_settlement(
    db: AsyncSession,
    *,
    company: Company,
    termin: Termin,
    created_by_user_id: int | None,
) -> Transaction | None:
    """Move the period's 2700 and 2710 onto 2740, dated the last day of the period.

    ``None`` when there is nothing to move, which is the ordinary case for a
    nullmelding and for a correction that changed nothing on the VAT accounts.
    """
    balances = await vat_balances(db, company, termin, include_settlement=True)
    if balances.output == ZERO and balances.input == ZERO:
        return None

    accounts = await ledger_service._accounts_by_number(db, company)
    financial_year = await ledger_service.ensure_financial_year(db, company, termin.end.year)
    journal = await _settlement_journal(db, company)
    text = f"Mva-oppgjør {termin.label}"

    lines: list[TransactionLine] = []
    if balances.output != ZERO:
        lines.append(
            _side("1", accounts[OUTPUT_VAT_ACCOUNT], balances.output, debit=True, text=text)
        )
    if balances.input != ZERO:
        lines.append(
            _side("2", accounts[INPUT_VAT_ACCOUNT], balances.input, debit=False, text=text)
        )
    if balances.net != ZERO:
        lines.append(_side("3", accounts[SETTLEMENT_ACCOUNT], balances.net, debit=False, text=text))
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
        period=termin.end.month,
        transaction_date=termin.end,
        gl_posting_date=termin.end,
        description=text,
        voucher_type=SETTLEMENT_VOUCHER_TYPE,
        created_by_user_id=created_by_user_id,
    )
    transaction.lines = lines
    posting.post(transaction)
    db.add(transaction)
    await db.flush()
    return transaction


def is_past(termin: Termin, today: date) -> bool:
    return today > termin.end


def latest_filed(filings: Sequence[MvaFiling]) -> MvaFiling | None:
    return next((f for f in filings if FilingStatus(f.status).is_filed), None)
