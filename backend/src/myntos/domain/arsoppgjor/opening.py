"""Inngående balanse: the books a company brings with it into Myntos.

A company whose earlier years were kept in another system starts here with a
balance sheet, not with nothing: money in the bank, customers who owe it,
suppliers it owes, share capital, the equity the earlier years built up. This is
that balance sheet, posted as **one balanced voucher** in its own journal
(``IB``), dated the first day of the company's first financial year in Myntos.

**Why a voucher and not stored opening rows.** :class:`AccountOpeningBalance`
exists and nothing writes it, and that stays so. A voucher is immutable once
posted, carries who entered it, is corrected the way every other entry is
(reversal plus a linked replacement), and is already counted by everything that
reads the ledger: the bank position on the overview, the customer's balance,
the next year's derived opening. Rows on the side would be a second source of
truth that every reader had to remember to add in, and the first one that forgot
would show an empty bank account. The SAF-T export is the one reader that
treats the journal differently, and it does so on purpose (see
:mod:`myntos.saft.service`).

**When it may be posted: the decision.** Only into the company's *first*
financial year in Myntos, and only while that year is open.

- *The first year*, because every later year's opening balance already exists:
  it is the year before's closing, derived from the postings. A second opening
  voucher in 2027 would count the same bank balance twice.
- *While the year is open*, because closing is the one thing that relies on it.
  Årsavslutning computes the equity the disposition is made against (whether a
  loss in an AS is covered by annen egenkapital or becomes udekket tap), and
  locks the year's balance sheet as the next year's opening.

What it deliberately does **not** require is a year with no other postings.
That rule sounds tidy and is wrong for the ordinary case: books move to Myntos
in January, receipts are entered from the first week, and the previous
accountant's final saldobalanse arrives in March or April, after their
årsoppgjør. Nothing posted in an open year reads the opening balance (the
register's year figures, the MVA-melding and the invoice flow all leave the
``IB`` journal alone), so refusing would protect nothing and would make the
import unusable by the people it is for.

**What it may contain.** Balance-sheet accounts only. A saldobalanse taken
before the previous year was closed still carries income and cost accounts;
their sum is a result nobody has disposed of, and posting it here would make it
this year's result. Refused, with the reason. And not the VAT accounts 2700 and
2710: the VAT owed for the last period before Myntos belongs on 2740, where the
``mva_oppgjor`` payment clears it, because 2700 and 2710 are swept onto 2740 by
each *Myntos* period's settlement and would carry last year's VAT into this
year's first one.

**Customers and suppliers (åpne poster).** A line on 1500 or 2400 may name the
party it is owed by or to, one line per open item, and the party's sub-ledger in
SAF-T then opens with it. An account is either fully specified per party or not
at all: half a reskontro does not reconcile with the account, and an auditor
reconciles exactly that.

**Corrections.** Reversal plus replacement, linked through
``corrects_transaction_pk``, in one request: there is never a moment with two
opening balances, or with none where one was meant.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from myntos.domain.arsoppgjor import saldobalanse
from myntos.domain.arsoppgjor.errors import (
    InvalidOpeningBalanceError,
    NoOpeningBalanceError,
    OpeningBalanceExistsError,
    OpeningBalanceNotAllowedError,
)
from myntos.domain.arsoppgjor.findings import Finding, blocking, feil, merknad
from myntos.domain.ledger import posting
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.chart import (
    INPUT_VAT_ACCOUNT,
    OPENING_JOURNAL_DESCRIPTION,
    OPENING_JOURNAL_ID,
    OPENING_JOURNAL_TYPE,
    OUTPUT_VAT_ACCOUNT,
    PAYABLES_ACCOUNT,
    RECEIVABLES_ACCOUNT,
)
from myntos.domain.ledger.models import (
    Account,
    Company,
    FinancialYear,
    Journal,
    Party,
    Transaction,
    TransactionLine,
)

ZERO = Decimal("0.00")
_CENT = Decimal("0.01")

# SAF-T TransactionType on the opening voucher and its reversals.
OPENING_VOUCHER_TYPE = "inngaende_balanse"


@dataclass(frozen=True)
class OpeningLineInput:
    """One line as a person typed it or as the saldobalanse said it."""

    account_number: str
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    party_pk: int | None = None
    description: str | None = None


@dataclass(frozen=True)
class ReviewLine:
    account_number: str
    # None when the account is not in the kontoplan, which is a finding.
    account_description: str | None
    debit: Decimal
    credit: Decimal
    party_pk: int | None
    party_name: str | None
    description: str | None


@dataclass(frozen=True)
class OpeningReview:
    year: int | None
    lines: list[ReviewLine]
    total_debit: Decimal
    total_credit: Decimal
    findings: list[Finding]

    @property
    def difference(self) -> Decimal:
        return self.total_debit - self.total_credit

    @property
    def postable(self) -> bool:
        return not blocking(self.findings)


@dataclass(frozen=True)
class OpeningVoucher:
    id: int
    voucher_no: str
    transaction_date: date
    posted_at: datetime | None
    created_by_user_id: int | None
    is_reversal: bool
    reversed: bool
    corrects_id: int | None
    total: Decimal
    lines: list[ReviewLine]


@dataclass(frozen=True)
class OpeningState:
    year: int | None
    # Why nothing may be posted now, or None when it may.
    refusal: str | None
    standing: OpeningVoucher | None
    history: list[OpeningVoucher]


# --- Where it may go ------------------------------------------------------------


async def first_year(db: AsyncSession, company: Company) -> FinancialYear | None:
    found: FinancialYear | None = await db.scalar(
        select(FinancialYear)
        .where(FinancialYear.company_id == company.id)
        .order_by(FinancialYear.year)
        .limit(1)
    )
    return found


def _refusal(year: FinancialYear | None) -> str | None:
    if year is None:
        return "selskapet har ikke noe regnskapsår i Myntos"
    if year.closed_at is not None:
        return (
            f"regnskapsåret {year.year} er avsluttet, og inngående balanse kan bare føres i et "
            "åpent år. Gjenåpne året først hvis balansen må rettes"
        )
    return None


async def _journal(db: AsyncSession, company: Company) -> Journal:
    journal = await db.scalar(
        select(Journal).where(
            Journal.company_id == company.id, Journal.journal_id == OPENING_JOURNAL_ID
        )
    )
    if journal is None:
        journal = Journal(
            company_id=company.id,
            journal_id=OPENING_JOURNAL_ID,
            description=OPENING_JOURNAL_DESCRIPTION,
            journal_type=OPENING_JOURNAL_TYPE,
        )
        db.add(journal)
        await db.flush()
    return journal


async def _standing_pk(db: AsyncSession, company: Company) -> int | None:
    """The opening voucher that currently counts: posted, not a reversal, not reversed."""
    reversal = aliased(Transaction, name="opening_reversal")
    found: int | None = await db.scalar(
        select(Transaction.id)
        .join(Journal, Journal.id == Transaction.journal_id)
        .where(
            Journal.company_id == company.id,
            Journal.journal_id == OPENING_JOURNAL_ID,
            Transaction.posted_at.is_not(None),
            Transaction.reverses_transaction_pk.is_(None),
            ~select(reversal.id).where(reversal.reverses_transaction_pk == Transaction.id).exists(),
        )
        .order_by(Transaction.id.desc())
        .limit(1)
    )
    return found


def _review_line(line: TransactionLine) -> ReviewLine:
    return ReviewLine(
        account_number=line.account.account_number,
        account_description=line.account.description,
        debit=line.debit,
        credit=line.credit,
        party_pk=line.party.id if line.party is not None else None,
        party_name=line.party.name if line.party is not None else None,
        description=line.description,
    )


async def state(db: AsyncSession, *, company: Company) -> OpeningState:
    """What the page needs before anybody types: the year, whether it may be posted, and history."""
    year = await first_year(db, company)
    vouchers = list(
        await db.scalars(
            ledger_service._voucher_query()
            .where(Journal.company_id == company.id, Journal.journal_id == OPENING_JOURNAL_ID)
            .order_by(Transaction.id)
        )
    )
    reversed_pks = {v.reverses_transaction_pk for v in vouchers if v.reverses_transaction_pk}
    history = [
        OpeningVoucher(
            id=v.id,
            voucher_no=v.transaction_id,
            transaction_date=v.transaction_date,
            posted_at=v.posted_at,
            created_by_user_id=v.created_by_user_id,
            is_reversal=v.reverses_transaction_pk is not None,
            reversed=v.id in reversed_pks,
            corrects_id=v.corrects_transaction_pk,
            total=sum((line.debit for line in v.lines), ZERO),
            lines=[
                _review_line(line)
                for line in sorted(v.lines, key=lambda li: int(li.record_id or 0))
            ],
        )
        for v in vouchers
    ]
    standing = next((v for v in reversed(history) if not v.is_reversal and not v.reversed), None)
    return OpeningState(
        year=year.year if year is not None else None,
        refusal=_refusal(year),
        standing=standing,
        history=history,
    )


# --- The review -------------------------------------------------------------------


def _amount_problem(line: OpeningLineInput) -> str | None:
    for value in (line.debit, line.credit):
        if value < ZERO:
            return "et beløp kan ikke være negativt; før det på motsatt side i stedet"
        if value != value.quantize(_CENT):
            return "beløpet har brøkdeler av et øre"
    if line.debit > ZERO and line.credit > ZERO:
        return "en linje har beløp enten i debet eller i kredit, ikke begge"
    if line.debit == ZERO and line.credit == ZERO:
        return "linjen har ikke noe beløp"
    return None


async def review(
    db: AsyncSession, *, company: Company, lines: Sequence[OpeningLineInput]
) -> OpeningReview:
    """Check the lines without posting anything.

    The review step shows *this*, not a sum computed in the browser: the backend
    is the one that decides, so it is the one whose opinion is shown.
    """
    year = await first_year(db, company)
    findings: list[Finding] = []
    refusal = _refusal(year)
    if refusal is not None:
        findings.append(feil("aar_ikke_tillatt", refusal[0].upper() + refusal[1:] + "."))
    if not lines:
        findings.append(feil("ingen_linjer", "Inngående balanse har ingen linjer."))

    accounts = {
        account.account_number: account
        for account in await db.scalars(select(Account).where(Account.company_id == company.id))
    }
    wanted_parties = {line.party_pk for line in lines if line.party_pk is not None}
    parties = (
        {
            party.id: party
            for party in await db.scalars(
                # Through the company, so a party id from the company next door is
                # simply unknown here rather than quietly attached.
                select(Party).where(Party.company_id == company.id, Party.id.in_(wanted_parties))
            )
        }
        if wanted_parties
        else {}
    )

    resolved: list[ReviewLine] = []
    total_debit = ZERO
    total_credit = ZERO
    by_account: dict[str, list[bool]] = defaultdict(list)
    for number, line in enumerate(lines, start=1):
        where = f"Linje {number} (konto {line.account_number})"
        account = accounts.get(line.account_number)
        party = parties.get(line.party_pk) if line.party_pk is not None else None
        problem = _amount_problem(line)
        if problem is not None:
            findings.append(feil("ugyldig_belop", f"{where}: {problem}."))
        else:
            total_debit += line.debit
            total_credit += line.credit
        if account is None:
            findings.append(
                feil(
                    "ukjent_konto",
                    f"{where}: kontoen finnes ikke i kontoplanen. Legg den til i kontoplanen, "
                    "eller før saldoen på kontoen den hører sammen med.",
                )
            )
        elif not account.account_type.is_balance_account:
            findings.append(
                feil(
                    "resultatkonto",
                    f"{where}: {account.description} er en resultatkonto. Inngående balanse "
                    "har bare balansekontoer; resultatet for fjoråret skal allerede stå på "
                    "egenkapitalen. Bruk saldobalansen etter årsoppgjøret.",
                )
            )
        elif account.account_number in {OUTPUT_VAT_ACCOUNT, INPUT_VAT_ACCOUNT}:
            findings.append(
                feil(
                    "mva_konto",
                    f"{where}: mva som ikke var gjort opp ved årsskiftet føres på 2740 "
                    "Oppgjørskonto merverdiavgift, der betalingen avregner den. På "
                    f"{account.account_number} ville den blitt med i årets første "
                    "mva-oppgjør.",
                )
            )
        if line.party_pk is not None:
            if party is None:
                findings.append(
                    feil("ukjent_part", f"{where}: kunden eller leverandøren finnes ikke.")
                )
            elif account is not None and party.balance_account_id != account.id:
                findings.append(
                    feil(
                        "part_paa_feil_konto",
                        f"{where}: {party.name} avregnes mot en annen konto enn "
                        f"{account.account_number}. Åpne poster føres på kontoen parten hører til.",
                    )
                )
        by_account[line.account_number].append(line.party_pk is not None)
        resolved.append(
            ReviewLine(
                account_number=line.account_number,
                account_description=account.description if account is not None else None,
                debit=line.debit,
                credit=line.credit,
                party_pk=line.party_pk if party is not None else None,
                party_name=party.name if party is not None else None,
                description=(line.description or "").strip() or None,
            )
        )

    for account_number, with_party in by_account.items():
        if any(with_party) and not all(with_party):
            findings.append(
                feil(
                    "reskontro_ufullstendig",
                    f"Konto {account_number} er delvis fordelt på kunder eller leverandører. Enten "
                    "fordeles hele saldoen på åpne poster, eller ingenting av den: halvparten "
                    "av en reskontro kan ikke avstemmes mot kontoen.",
                )
            )
        elif not any(with_party) and account_number in {
            RECEIVABLES_ACCOUNT,
            PAYABLES_ACCOUNT,
        }:
            findings.append(
                merknad(
                    "uten_aapne_poster",
                    f"Saldoen på {account_number} er ikke fordelt på "
                    + ("kunder" if account_number == RECEIVABLES_ACCOUNT else "leverandører")
                    + ". Det går an, men SAF-T-filen får da ingen inngående saldo per part, "
                    "og innbetalinger kan ikke avstemmes mot åpne poster.",
                )
            )

    if lines and total_debit != total_credit:
        findings.append(
            feil(
                "ubalanse",
                f"Debet {total_debit} og kredit {total_credit} går ikke i null "
                f"(differanse {total_debit - total_credit}). En balanse som ikke balanserer, "
                "er nesten alltid en konto som mangler eller står på feil side.",
            )
        )

    return OpeningReview(
        year=year.year if year is not None else None,
        lines=resolved,
        total_debit=total_debit,
        total_credit=total_credit,
        findings=findings,
    )


async def from_saldobalanse(
    db: AsyncSession, *, company: Company, content: bytes
) -> tuple[list[OpeningLineInput], OpeningReview]:
    """Read an uploaded saldobalanse into lines, and review them. Posts nothing.

    The lines go back to the form rather than straight into the ledger: a file
    read by a heuristic is a proposal, and the person who knows the books looks
    at it, splits 1500 into open items, and confirms.
    """
    rows = saldobalanse.parse(content)
    lines = [
        OpeningLineInput(
            account_number=row.account_number,
            debit=row.debit,
            credit=row.credit,
            description=row.name,
        )
        for row in rows
    ]
    return lines, await review(db, company=company, lines=lines)


# --- Posting ------------------------------------------------------------------------


async def _post(
    db: AsyncSession,
    *,
    company: Company,
    year: FinancialYear,
    lines: Sequence[OpeningLineInput],
    corrects_pk: int | None,
    created_by_user_id: int | None,
) -> Transaction:
    journal = await _journal(db, company)
    accounts = {
        account.account_number: account
        for account in await db.scalars(select(Account).where(Account.company_id == company.id))
    }
    parties = {
        party.id: party
        for party in await db.scalars(select(Party).where(Party.company_id == company.id))
    }
    transaction = Transaction(
        journal_id=journal.id,
        transaction_id=await ledger_service._next_voucher_number(db, journal.id, year.year),
        financial_year=year.year,
        period=year.start_date.month,
        transaction_date=year.start_date,
        gl_posting_date=year.start_date,
        description=f"Inngående balanse {year.year}",
        voucher_type=OPENING_VOUCHER_TYPE,
        corrects_transaction_pk=corrects_pk,
        created_by_user_id=created_by_user_id,
    )
    transaction.lines = [
        TransactionLine(
            record_id=str(number),
            account=accounts[line.account_number],
            party=parties[line.party_pk] if line.party_pk is not None else None,
            description=(line.description or "").strip()[:255] or "Inngående balanse",
            debit=line.debit,
            credit=line.credit,
        )
        for number, line in enumerate(lines, start=1)
    ]
    posting.post(transaction)
    db.add(transaction)
    await db.flush()
    return transaction


async def _reverse(
    db: AsyncSession, *, company: Company, voucher_pk: int, created_by_user_id: int | None
) -> Transaction:
    original = await ledger_service.get_voucher(db, company=company, voucher_pk=voucher_pk)
    reversal = posting.build_reversal(
        original,
        transaction_id=await ledger_service._next_voucher_number(
            db, original.journal_id, original.financial_year
        ),
        gl_posting_date=original.gl_posting_date,
        period=original.period,
        financial_year=original.financial_year,
        description=f"Reversering av inngående balanse {original.transaction_id}",
    )
    reversal.reverses_transaction_pk = original.id
    reversal.created_by_user_id = created_by_user_id
    posting.post(reversal)
    db.add(reversal)
    await db.flush()
    return reversal


async def post_opening(
    db: AsyncSession,
    *,
    company: Company,
    lines: Sequence[OpeningLineInput],
    correct: bool = False,
    created_by_user_id: int | None = None,
) -> Transaction:
    """Post the opening balance, or with ``correct`` replace the standing one.

    The review runs again here, on the lines actually being posted, so the
    button the page enabled and the rule the server enforces are the same rule.
    """
    year = await first_year(db, company)
    refusal = _refusal(year)
    if refusal is not None or year is None:
        raise OpeningBalanceNotAllowedError(refusal or "selskapet har ikke noe regnskapsår")
    checked = await review(db, company=company, lines=lines)
    problems = blocking(checked.findings)
    if problems:
        raise InvalidOpeningBalanceError(
            "inngående balanse kan ikke føres slik den er. Se funnene", problems
        )
    standing = await _standing_pk(db, company)
    if standing is not None and not correct:
        raise OpeningBalanceExistsError(
            "selskapet har allerede en inngående balanse. Retting gjøres ved å reversere den "
            "og føre en ny, som et eget valg"
        )
    if standing is None and correct:
        raise NoOpeningBalanceError("det finnes ingen inngående balanse å rette")
    if standing is not None:
        await _reverse(
            db, company=company, voucher_pk=standing, created_by_user_id=created_by_user_id
        )
    return await _post(
        db,
        company=company,
        year=year,
        lines=lines,
        corrects_pk=standing,
        created_by_user_id=created_by_user_id,
    )


async def reverse_opening(
    db: AsyncSession, *, company: Company, created_by_user_id: int | None = None
) -> Transaction:
    """Withdraw the standing opening balance without replacing it."""
    year = await first_year(db, company)
    refusal = _refusal(year)
    if refusal is not None:
        raise OpeningBalanceNotAllowedError(refusal)
    standing = await _standing_pk(db, company)
    if standing is None:
        raise NoOpeningBalanceError("det finnes ingen inngående balanse å reversere")
    return await _reverse(
        db, company=company, voucher_pk=standing, created_by_user_id=created_by_user_id
    )


async def has_opening(db: AsyncSession, company: Company) -> bool:
    return await _standing_pk(db, company) is not None
