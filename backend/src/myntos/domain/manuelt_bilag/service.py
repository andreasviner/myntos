"""Reviewing, posting, reading back and correcting manual vouchers.

The package docstring says what a manual voucher is and why each rule is there.
This module is the mechanics, in the order a voucher meets them: the lines a
person typed are planned into the lines that will be posted (:func:`_plan`),
the plan is shown (:func:`review`), the same plan is posted
(:func:`post_voucher`), and a posted voucher is read back as both (:func:`get`,
:func:`list_vouchers`). Nothing here commits; the request owns the transaction,
so a correction's reversal and its replacement happen together or not at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from myntos.domain.arsoppgjor.closing import CLOSING_ACCOUNTS
from myntos.domain.arsoppgjor.findings import Finding, blocking, feil, merknad
from myntos.domain.bilag.models import VoucherExplanation
from myntos.domain.ledger import dimensions, posting, vatstatus
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.chart import (
    BANK_ACCOUNT,
    CASH_ACCOUNT,
    INPUT_VAT_ACCOUNT,
    MANUAL_JOURNAL_DESCRIPTION,
    MANUAL_JOURNAL_ID,
    MANUAL_JOURNAL_TYPE,
    MANUAL_VOUCHER_TYPE,
    OUTPUT_VAT_ACCOUNT,
    PAYABLES_ACCOUNT,
    RECEIVABLES_ACCOUNT,
    TAX_CODES_BY_CODE,
)
from myntos.domain.ledger.errors import (
    AlreadyReversedError,
    CannotReverseHereError,
    ClosedFinancialYearError,
)
from myntos.domain.ledger.models import (
    Account,
    Company,
    DimensionValue,
    FinancialYear,
    Journal,
    Party,
    TaxCode,
    Transaction,
    TransactionLine,
)
from myntos.domain.manuelt_bilag.errors import (
    InvalidManualVoucherError,
    ManualVoucherNotFoundError,
)
from myntos.domain.mva.codes import MVA_CODES, CodeKind

ZERO = Decimal("0.00")
_CENT = Decimal("0.01")

#: The fewest characters a reason may have. A floor against an empty box and a
#: single «x», not a measure of whether the reason is good: that is the
#: person's to write and the auditor's to read. The same floor as a reopened
#: year's reason (myntos.domain.arsoppgjor.closing).
MIN_EXPLANATION = 10
MAX_EXPLANATION = 4000

#: How many lines one voucher may have. A year-end adjustment is a handful; a
#: hundred-line voucher is more likely a saldobalanse, which the opening balance
#: reads better.
MAX_LINES = 200

_VAT_ACCOUNTS = frozenset({OUTPUT_VAT_ACCOUNT, INPUT_VAT_ACCOUNT})
# The accounts the register pays through, for the ``betalingskonto`` merknad,
# which sends the person to the register. Not every account marked as a bank
# account (kontoplan.counts_as_bank): the register never posts to a
# driftskreditt on 2390, so for that one a manual voucher is the way, and the
# merknad would point somewhere that cannot help.
_PAYMENT_ACCOUNTS = frozenset({BANK_ACCOUNT, CASH_ACCOUNT})


# --- Shapes --------------------------------------------------------------------------


@dataclass(frozen=True)
class LineInput:
    """One line as a person typed it.

    ``debit`` or ``credit`` is the amount **with VAT included** when
    ``tax_code`` is set (the package docstring says why), and the amount itself
    when it is not. ``dimension_value_ids`` (avdeling, prosjekt, driftsgren)
    are given for the line as typed and land on the basis line the VAT split
    leaves behind, never on the VAT line: VAT is a balance-sheet movement no
    result per avdeling reads, and the register allocates the same way.
    """

    account_number: str
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    description: str | None = None
    party_pk: int | None = None
    tax_code: str | None = None
    dimension_value_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class VoucherInput:
    occurred_on: date
    # The bilagstekst: short, on the voucher and in the SAF-T file.
    description: str
    # Why it is posted (bokføringsforskriften § 5-12). Kept beside the voucher.
    explanation: str
    lines: Sequence[LineInput]


@dataclass(frozen=True)
class VoucherLine:
    """One ledger line, planned or posted.

    ``from_line`` is the typed line it came from, counted from one. ``generated``
    marks the VAT lines Myntos added; nobody typed those, and the page says so.
    ``vat_amount`` is the VAT carried in the line's tax information, on the
    basis line only.
    """

    from_line: int
    account_number: str
    account_description: str | None
    description: str
    debit: Decimal
    credit: Decimal
    party_pk: int | None
    party_name: str | None
    tax_code: str | None
    vat_amount: Decimal | None
    generated: bool
    dimension_value_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class ManualReview:
    year: int
    occurred_on: date
    description: str
    explanation: str
    lines: list[VoucherLine]
    total_debit: Decimal
    total_credit: Decimal
    findings: list[Finding]
    corrects_pk: int | None

    @property
    def difference(self) -> Decimal:
        return self.total_debit - self.total_credit

    @property
    def postable(self) -> bool:
        return not blocking(self.findings)


@dataclass(frozen=True)
class ManualVoucher:
    """A posted manual voucher, read back with what documents it and what it is linked to."""

    id: int
    voucher_no: str
    occurred_on: date
    posted_at: datetime | None
    description: str
    # None only for a voucher posted in this journal by something other than
    # this module, which nothing does today.
    explanation: str | None
    lines: list[VoucherLine]
    # The lines as they would be typed again: what a correction starts from.
    input_lines: list[LineInput]
    total: Decimal
    is_reversal: bool
    reverses_pk: int | None
    reversed_by_pk: int | None
    corrects_pk: int | None
    corrected_by_pk: int | None
    created_by_id: int | None
    created_by_name: str | None = None
    created_via: str | None = None


# --- Planning: typed lines into posted lines -----------------------------------------


@dataclass
class _Planned:
    """A line to post, holding the rows it will point at."""

    from_line: int
    account: Account
    party: Party | None
    description: str
    debit: Decimal
    credit: Decimal
    tax: TaxCode | None = None
    rate: Decimal | None = None
    basis: Decimal | None = None
    vat: Decimal | None = None
    generated: bool = False
    values: tuple[DimensionValue, ...] = ()

    def view(self) -> VoucherLine:
        return VoucherLine(
            from_line=self.from_line,
            account_number=self.account.account_number,
            account_description=self.account.description,
            description=self.description,
            debit=self.debit,
            credit=self.credit,
            party_pk=self.party.id if self.party is not None else None,
            party_name=self.party.name if self.party is not None else None,
            tax_code=self.tax.code if self.tax is not None else None,
            vat_amount=self.vat,
            generated=self.generated,
            dimension_value_ids=tuple(value.id for value in self.values),
        )

    def line(self, record_id: int) -> TransactionLine:
        line = TransactionLine(
            record_id=str(record_id),
            account=self.account,
            party=self.party,
            description=self.description[:255],
            debit=self.debit,
            credit=self.credit,
        )
        if self.tax is not None:
            assert self.rate is not None and self.basis is not None  # noqa: S101
            assert self.vat is not None  # noqa: S101
            # The single writer of tax information, so it lands on the line's
            # own side exactly as the register's does.
            posting.set_tax_information(
                line, tax_code=self.tax, rate=self.rate, basis=self.basis, amount=self.vat
            )
        # After the amounts are final: the allocation is for the line's whole
        # (net) amount, and posting.validate refuses one larger than the line,
        # which is what allocating the gross typed amount would be.
        dimensions.allocate(line, self.values)
        return line


@dataclass
class _Plan:
    year: int
    lines: list[_Planned] = field(default_factory=list)
    total_debit: Decimal = ZERO
    total_credit: Decimal = ZERO
    findings: list[Finding] = field(default_factory=list)
    original: Transaction | None = None


def _amount_problem(line: LineInput) -> str | None:
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


async def _year_findings(db: AsyncSession, company: Company, day: date) -> list[Finding]:
    """Whether ``day`` falls in a year that takes vouchers, read without opening one.

    The same rule :func:`~myntos.domain.ledger.service.ensure_financial_year`
    enforces when the voucher is posted, asked here without its side effect,
    because the review stores nothing, not even a financial year.
    """
    years = {
        row.year: row
        for row in await db.scalars(
            select(FinancialYear).where(FinancialYear.company_id == company.id)
        )
    }
    found = years.get(day.year)
    if found is not None:
        if found.closed_at is None:
            return []
        return [
            feil(
                "aar_avsluttet",
                f"Regnskapsåret {day.year} er avsluttet, og et avsluttet år tar ikke imot bilag. "
                "Hører justeringen til det året, gjenåpne det under Årsavslutning først. Er "
                "årsregnskapet fastsatt, føres rettingen i inneværende år.",
            )
        ]
    if years and day.year < min(years):
        return [
            feil(
                "foer_forste_aar",
                f"Selskapets første regnskapsår i Myntos er {min(years)}, så det går ikke an å "
                f"føre på {day.year}. Det som skjedde før, hører hjemme i inngående balanse.",
            )
        ]
    return [
        merknad(
            "aar_ikke_aapnet",
            f"Regnskapsåret {day.year} er ikke åpnet i Myntos ennå. Det åpnes når bilaget føres.",
        )
    ]


async def _manual(db: AsyncSession, company: Company, voucher_pk: int) -> Transaction:
    """A manual voucher of this company's, or 404.

    Through the company and the journal both: another company's voucher and a
    payment of this company's are equally unknown here, so the routes of one
    journal cannot be used on another's.
    """
    found = await db.scalar(
        ledger_service._voucher_query().where(
            Transaction.id == voucher_pk,
            Journal.company_id == company.id,
            Journal.journal_id == MANUAL_JOURNAL_ID,
        )
    )
    if found is None:
        raise ManualVoucherNotFoundError("finner ikke det manuelle bilaget")
    return found


async def _why_not_reversible(
    db: AsyncSession, company: Company, original: Transaction
) -> tuple[str, str, type[Exception]] | None:
    """``(finding code, message, error)`` when ``original`` cannot be reversed now."""
    if original.reverses_transaction_pk is not None:
        return (
            "reversering_kan_ikke_rettes",
            f"Bilag {original.transaction_id} er selv en reversering. Den reverseres ikke; før "
            "det som skal stå, som et nytt manuelt bilag.",
            CannotReverseHereError,
        )
    already = await db.scalar(
        select(Transaction.id).where(Transaction.reverses_transaction_pk == original.id)
    )
    if already is not None:
        return (
            "allerede_reversert",
            f"Bilag {original.transaction_id} er allerede reversert eller rettet. Rett det "
            "nyeste bilaget i stedet.",
            AlreadyReversedError,
        )
    closed = await db.scalar(
        select(FinancialYear.closed_at).where(
            FinancialYear.company_id == company.id,
            FinancialYear.year == original.financial_year,
        )
    )
    if closed is not None:
        return (
            "originalens_aar_avsluttet",
            f"Bilag {original.transaction_id} er i regnskapsåret {original.financial_year}, som "
            "er avsluttet, og reverseringen skal stå i samme periode. Før rettingen som et nytt "
            "manuelt bilag i inneværende år, eller gjenåpne året under Årsavslutning.",
            ClosedFinancialYearError,
        )
    return None


def _split(
    *,
    index: int,
    line: LineInput,
    account: Account,
    party: Party | None,
    text: str,
    tax: TaxCode | None,
    rate: Decimal,
    accounts: dict[str, Account],
    values: tuple[DimensionValue, ...] = (),
) -> list[_Planned]:
    """The ledger lines one typed line becomes: itself, and the VAT Myntos adds.

    Without a code the line is posted as typed. With an ordinary code its
    amount includes VAT and is split the register's way (``split_gross``, so
    the halves add back to the øre), the VAT on 2700 for a sales code and 2710
    for a purchase code, on the line's own side: a credit with a purchase code
    gives a deduction back, a debit with a sales code reduces what is owed. A
    reverse-charge code has no VAT inside the amount; it is computed on top and
    posted to both 2700 and 2710, so the pair cancels and the melding still
    reports both, as the register does it.
    """
    debit_side = line.debit > ZERO
    amount = line.debit if debit_side else line.credit

    def planned(
        on: Account, value: Decimal, *, generated: bool = False, side: bool = debit_side
    ) -> _Planned:
        return _Planned(
            from_line=index,
            account=on,
            party=None if generated else party,
            description=text,
            debit=value if side else ZERO,
            credit=ZERO if side else value,
            generated=generated,
            values=() if generated else values,
        )

    if tax is None:
        return [planned(account, amount)]

    seed = TAX_CODES_BY_CODE.get(tax.code)
    if seed is not None and seed.reverse_charge:
        vat = posting.quantize_money(amount * rate)
        basis = planned(account, amount)
        basis.tax, basis.rate, basis.basis, basis.vat = tax, rate, amount, vat
        lines = [basis]
        if vat > ZERO:
            # The VAT owed on the purchase goes on 2700 against the line, and the
            # deduction on 2710 with it. A purchase is the debit side; a credit
            # undoes one, and both go back.
            lines.append(
                planned(accounts[OUTPUT_VAT_ACCOUNT], vat, generated=True, side=not debit_side)
            )
            lines.append(planned(accounts[INPUT_VAT_ACCOUNT], vat, generated=True))
        return lines

    net, vat = posting.split_gross(amount, rate)
    if net == ZERO:
        # Only reachable with an absurd rate; the whole amount is the basis.
        net, vat = amount, ZERO
    basis = planned(account, net)
    basis.tax, basis.rate, basis.basis, basis.vat = tax, rate, net, vat
    lines = [basis]
    if vat > ZERO:
        known = MVA_CODES.get(tax.code)
        on_sale = known is not None and known.kind is CodeKind.UTGAAENDE
        lines.append(
            planned(
                accounts[OUTPUT_VAT_ACCOUNT if on_sale else INPUT_VAT_ACCOUNT], vat, generated=True
            )
        )
    return lines


def _code_problem(code: str, tax: TaxCode | None, registered: bool, day: date) -> str | None:
    if tax is None:
        return f"mva-kode {code} finnes ikke for dette selskapet"
    seed = TAX_CODES_BY_CODE.get(code)
    if seed is None:
        return (
            f"Myntos vet ikke hvordan mva-kode {code} skal føres, og fører den ikke i et "
            "manuelt bilag"
        )
    if seed.vat_only:
        return (
            f"mva-kode {code} er mva betalt ved innførsel, der hele beløpet er avgiften. Den føres "
            "i registeret som «Mva ved innførsel»"
        )
    if seed.reverse_charge and not seed.deductible:
        return (
            f"mva-kode {code} er omvendt avgiftsplikt uten fradrag, der avgiften blir en kostnad "
            "på en linje av sin egen. Den føres i registeret"
        )
    allowed = vatstatus.allowed_tax_codes(registered)
    if allowed is not None and code not in allowed:
        return (
            f"selskapet var ikke mva-registrert {day.isoformat()}, så mva-kode {code} kan ikke "
            "brukes: den gir fradrag eller krever inn mva"
        )
    return None


async def _plan(
    db: AsyncSession,
    *,
    company: Company,
    entry: VoucherInput,
    today: date,
    corrects_pk: int | None,
) -> _Plan:
    """Every check, and the lines that would be posted. Stores nothing."""
    day = entry.occurred_on
    plan = _Plan(year=day.year)
    findings = plan.findings
    findings.extend(await _year_findings(db, company, day))
    if day > today:
        findings.append(
            merknad(
                "fram_i_tid",
                f"Bilaget er datert {day.isoformat()}, som er fram i tid. Det går an, men "
                "et bilag dateres normalt den dagen det gjelder.",
            )
        )

    description = entry.description.strip()
    if not description:
        findings.append(
            feil(
                "mangler_tekst",
                "Bilaget mangler tekst. Skriv kort hva det er, som «Avskrivning 2026».",
            )
        )
    explanation = entry.explanation.strip()
    if len(explanation) < MIN_EXPLANATION:
        findings.append(
            feil(
                "mangler_begrunnelse",
                "Skriv hvorfor bilaget føres. Et bilag uten kvittering eller faktura skal "
                "dokumenteres med årsaken til føringen (bokføringsforskriften § 5-12), og for et "
                "anslag også hvordan det er beregnet (§ 6-4).",
            )
        )
    if len(explanation) > MAX_EXPLANATION:
        findings.append(
            feil("begrunnelse_for_lang", f"Begrunnelsen kan være høyst {MAX_EXPLANATION} tegn.")
        )
    if len(entry.lines) < 2:
        findings.append(
            feil("for_faa_linjer", "Et bilag har minst to linjer: noe i debet og noe i kredit.")
        )
    if len(entry.lines) > MAX_LINES:
        findings.append(feil("for_mange_linjer", f"Et bilag kan ha høyst {MAX_LINES} linjer."))

    accounts = {
        account.account_number: account
        for account in await db.scalars(select(Account).where(Account.company_id == company.id))
    }
    wanted_parties = {line.party_pk for line in entry.lines if line.party_pk is not None}
    parties = (
        {
            party.id: party
            for party in await db.scalars(
                # Through the company, so a party id from the company next door
                # is simply unknown here rather than quietly attached.
                select(Party).where(Party.company_id == company.id, Party.id.in_(wanted_parties))
            )
        }
        if wanted_parties
        else {}
    )
    # Every account a sub-ledger settles against: the two the seed names, and
    # any other a party was given. A line on one of these moves somebody's
    # balance, so it has to say whose.
    reskontro = set(
        await db.scalars(
            select(Party.balance_account_id).where(Party.company_id == company.id).distinct()
        )
    ) | {
        accounts[number].id
        for number in (RECEIVABLES_ACCOUNT, PAYABLES_ACCOUNT)
        if number in accounts
    }
    wanted_codes = {line.tax_code for line in entry.lines if line.tax_code is not None}
    codes = (
        {
            tax.code: tax
            for tax in await db.scalars(
                select(TaxCode)
                .where(TaxCode.company_id == company.id, TaxCode.code.in_(wanted_codes))
                .options(selectinload(TaxCode.rates))
            )
        }
        if wanted_codes
        else {}
    )
    registered = vatstatus.registered_on(company, day)

    unsplit: set[str] = set()
    on_payment_account: set[str] = set()
    reported = False
    for index, line in enumerate(entry.lines, start=1):
        where = f"Linje {index} (konto {line.account_number})"
        ok = True
        problem = _amount_problem(line)
        if problem is not None:
            findings.append(feil("ugyldig_belop", f"{where}: {problem}."))
            ok = False
        else:
            plan.total_debit += line.debit
            plan.total_credit += line.credit

        account = accounts.get(line.account_number)
        if account is None:
            findings.append(
                feil(
                    "ukjent_konto",
                    f"{where}: kontoen finnes ikke i kontoplanen. Legg den til under Kontoplan, "
                    "eller før på kontoen den hører sammen med.",
                )
            )
            ok = False
        elif not account.is_active:
            findings.append(
                feil(
                    "deaktivert_konto",
                    f"{where}: {account.description} er deaktivert. Aktiver den under Kontoplan, "
                    "eller velg en annen konto.",
                )
            )
            ok = False
        elif account.account_number in _VAT_ACCOUNTS:
            findings.append(
                feil(
                    "mva_konto",
                    f"{where}: mva føres ikke rett på {account.account_number}. Velg mva-kode på "
                    "linjen det gjelder, så lager Myntos mva-linjen selv og mva-meldingen og "
                    "kontoene stemmer med hverandre.",
                )
            )
            ok = False
        elif account.account_number in CLOSING_ACCOUNTS:
            findings.append(
                feil(
                    "aarsavslutningskonto",
                    f"{where}: {account.account_number} {account.description} føres av "
                    "årsavslutningen selv. Ført her ville årsavslutningen nekte å avslutte året.",
                )
            )
            ok = False
        elif account.account_number in _PAYMENT_ACCOUNTS:
            on_payment_account.add(account.account_number)

        party = parties.get(line.party_pk) if line.party_pk is not None else None
        if line.party_pk is not None and party is None:
            findings.append(feil("ukjent_part", f"{where}: kunden eller leverandøren finnes ikke."))
            ok = False
        elif account is not None and account.id in reskontro:
            if party is None:
                unsplit.add(account.account_number)
            elif party.balance_account_id != account.id:
                findings.append(
                    feil(
                        "part_paa_feil_konto",
                        f"{where}: {party.name} avregnes mot en annen konto enn "
                        f"{account.account_number}, så saldoen hos parten ville ikke stemt med "
                        "kontoen.",
                    )
                )
                ok = False

        tax: TaxCode | None = None
        rate = ZERO
        if line.tax_code is not None:
            tax = codes.get(line.tax_code)
            code_problem = _code_problem(line.tax_code, tax, registered, day)
            if code_problem is not None:
                findings.append(feil("ugyldig_mva_kode", f"{where}: {code_problem}."))
                ok = False
            elif tax is not None:
                try:
                    rate = posting.rate_on(tax, day)
                except posting.PostingError:
                    findings.append(
                        feil(
                            "ingen_mva_sats",
                            f"{where}: mva-kode {tax.code} har ingen sats {day.isoformat()}.",
                        )
                    )
                    ok = False
                if tax.is_reportable:
                    reported = True

        values: tuple[DimensionValue, ...] = ()
        if line.dimension_value_ids:
            try:
                # Through the company: another company's value is "not found",
                # the answer resolve gives every way of posting.
                values = await dimensions.resolve(
                    db, company=company, value_ids=line.dimension_value_ids
                )
            except dimensions.DimensionError as error:
                # A finding rather than the register's 404/409, so the review
                # shows it beside the rest instead of stopping at the first.
                findings.append(feil("ugyldig_dimensjon", f"{where}: {error}."))
                ok = False

        if ok and account is not None:
            plan.lines.extend(
                _split(
                    index=index,
                    line=line,
                    account=account,
                    party=party,
                    text=(line.description or "").strip() or description,
                    tax=tax,
                    rate=rate,
                    accounts=accounts,
                    values=values,
                )
            )

    for number in sorted(unsplit):
        findings.append(
            merknad(
                "uten_part",
                f"Linjen på {number} har ingen kunde eller leverandør. Da endres saldoen på "
                "kontoen uten at noen part skylder mer eller mindre, og reskontroen stemmer ikke "
                "lenger med kontoen. Velg parten hvis føringen gjelder én.",
            )
        )
    if on_payment_account:
        findings.append(
            merknad(
                "betalingskonto",
                f"Bilaget fører på {', '.join(sorted(on_payment_account))}. Betalinger føres i "
                "registeret under Kontantstrøm, der de får kategori og kan avstemmes mot "
                "kontoutskriften. Et manuelt bilag på bankkontoen passer for omklassifiseringer "
                "og rettinger.",
            )
        )
    if reported:
        findings.append(
            merknad(
                "mva_i_meldingen",
                f"Bilaget har mva-kode og kommer med i mva-meldingen for terminen "
                f"{day.isoformat()} hører til. Er den terminen allerede sendt, viser mva-siden at "
                "den er endret etter innsending.",
            )
        )
    if entry.lines and plan.total_debit != plan.total_credit:
        findings.append(
            feil(
                "ubalanse",
                f"Debet {plan.total_debit} og kredit {plan.total_credit} går ikke i null "
                f"(differanse {plan.total_debit - plan.total_credit}).",
            )
        )

    if corrects_pk is not None:
        plan.original = await _manual(db, company, corrects_pk)
        why = await _why_not_reversible(db, company, plan.original)
        if why is not None:
            findings.append(feil(why[0], why[1]))
    return plan


def _review(entry: VoucherInput, plan: _Plan, corrects_pk: int | None) -> ManualReview:
    return ManualReview(
        year=plan.year,
        occurred_on=entry.occurred_on,
        description=entry.description.strip(),
        explanation=entry.explanation.strip(),
        lines=[planned.view() for planned in plan.lines],
        total_debit=plan.total_debit,
        total_credit=plan.total_credit,
        findings=plan.findings,
        corrects_pk=corrects_pk,
    )


async def review(
    db: AsyncSession,
    *,
    company: Company,
    entry: VoucherInput,
    today: date,
    corrects_pk: int | None = None,
) -> ManualReview:
    """What posting ``entry`` would do, and what is wrong with it. Posts and stores nothing.

    ``corrects_pk`` reviews it as the replacement of that manual voucher, which
    adds whether the original can be reversed now.
    """
    plan = await _plan(db, company=company, entry=entry, today=today, corrects_pk=corrects_pk)
    return _review(entry, plan, corrects_pk)


# --- Posting -------------------------------------------------------------------------


async def _journal(db: AsyncSession, company: Company) -> Journal:
    journal = await db.scalar(
        select(Journal).where(
            Journal.company_id == company.id, Journal.journal_id == MANUAL_JOURNAL_ID
        )
    )
    if journal is None:
        journal = Journal(
            company_id=company.id,
            journal_id=MANUAL_JOURNAL_ID,
            description=MANUAL_JOURNAL_DESCRIPTION,
            journal_type=MANUAL_JOURNAL_TYPE,
        )
        db.add(journal)
        await db.flush()
    return journal


async def _write_explanation(
    db: AsyncSession, company: Company, transaction: Transaction, text: str
) -> None:
    """File the reason beside the voucher. Once: there is no path that changes it."""
    db.add(
        VoucherExplanation(
            company_id=company.id, transaction_pk=transaction.id, text=text[:MAX_EXPLANATION]
        )
    )
    await db.flush()


async def _reverse(
    db: AsyncSession, *, company: Company, original: Transaction, created_by_user_id: int | None
) -> Transaction:
    """The mirror of ``original``, in its own period, so the year nets to zero there.

    ``created_by_user_id`` is whoever reverses it, not whoever posted it, for the
    reason the register's reversal gives.
    """
    year = await ledger_service.ensure_financial_year(db, company, original.financial_year)
    reversal = posting.build_reversal(
        original,
        transaction_id=await ledger_service._next_voucher_number(
            db, original.journal_id, year.year
        ),
        gl_posting_date=original.gl_posting_date,
        period=original.period,
        financial_year=year.year,
        description=f"Reversering av manuelt bilag {original.transaction_id}",
    )
    reversal.reverses_transaction_pk = original.id
    reversal.created_by_user_id = created_by_user_id
    posting.post(reversal)
    db.add(reversal)
    await db.flush()
    return reversal


async def post_voucher(
    db: AsyncSession,
    *,
    company: Company,
    entry: VoucherInput,
    today: date,
    created_by_user_id: int | None = None,
    corrects_pk: int | None = None,
) -> Transaction:
    """Post ``entry`` as a manual voucher, after running the review on it again.

    With ``corrects_pk`` it is a correction: that manual voucher is reversed in
    its own period and ``entry`` posted as its replacement, linked through
    ``corrects_transaction_pk``, in this one transaction. The reversal carries
    the correction's reason, prefixed with the voucher that replaced it, so the
    two pieces of documentation name each other as § 5-12 asks.

    Raises :class:`InvalidManualVoucherError` with every blocking finding.
    """
    plan = await _plan(db, company=company, entry=entry, today=today, corrects_pk=corrects_pk)
    problems = blocking(plan.findings)
    if problems:
        raise InvalidManualVoucherError(
            "det manuelle bilaget kan ikke føres slik det er. Se funnene", problems
        )
    year = await ledger_service.ensure_financial_year(
        db, company, entry.occurred_on.year, on=entry.occurred_on
    )
    journal = await _journal(db, company)
    explanation = entry.explanation.strip()

    reversal: Transaction | None = None
    if plan.original is not None:
        reversal = await _reverse(
            db, company=company, original=plan.original, created_by_user_id=created_by_user_id
        )

    day = entry.occurred_on
    transaction = Transaction(
        journal_id=journal.id,
        transaction_id=await ledger_service._next_voucher_number(db, journal.id, year.year),
        financial_year=year.year,
        period=day.month,
        transaction_date=day,
        gl_posting_date=day,
        description=entry.description.strip()[:255],
        voucher_type=MANUAL_VOUCHER_TYPE,
        corrects_transaction_pk=corrects_pk,
        created_by_user_id=created_by_user_id,
    )
    transaction.lines = [planned.line(number) for number, planned in enumerate(plan.lines, start=1)]
    posting.post(transaction)
    db.add(transaction)
    await db.flush()
    await _write_explanation(db, company, transaction, explanation)
    if reversal is not None:
        await _write_explanation(
            db,
            company,
            reversal,
            f"Rettet med bilag {transaction.transaction_id}. {explanation}",
        )
    return transaction


async def reverse_voucher(
    db: AsyncSession,
    *,
    company: Company,
    voucher_pk: int,
    explanation: str,
    created_by_user_id: int | None = None,
) -> Transaction:
    """Undo a manual voucher by posting its mirror, with a reason of its own.

    For a voucher that should never have been posted. One that was posted with
    the wrong figures is corrected instead (:func:`post_voucher` with
    ``corrects_pk``), which leaves the right figures standing in the same step.
    """
    original = await _manual(db, company, voucher_pk)
    text = explanation.strip()
    if len(text) < MIN_EXPLANATION:
        raise InvalidManualVoucherError(
            "reverseringen må ha en begrunnelse",
            [
                feil(
                    "mangler_begrunnelse",
                    "Skriv hvorfor bilaget reverseres. En retting skal dokumenteres med årsaken "
                    "(bokføringsforskriften § 5-12).",
                )
            ],
        )
    why = await _why_not_reversible(db, company, original)
    if why is not None:
        _, message, error = why
        raise error(message[0].lower() + message[1:])
    reversal = await _reverse(
        db, company=company, original=original, created_by_user_id=created_by_user_id
    )
    await _write_explanation(db, company, reversal, text)
    return reversal


# --- Reading back --------------------------------------------------------------------


def _ledger_lines(transaction: Transaction) -> list[TransactionLine]:
    return sorted(transaction.lines, key=lambda line: int(line.record_id or 0))


def _lines(transaction: Transaction) -> tuple[list[VoucherLine], list[LineInput]]:
    """The posted lines, and the typed lines they were made from.

    Exact, because of what the review refuses: in this journal a line on 2700
    or 2710 can only be one Myntos added, and every code that would add any
    other line is left to the register. So a basis line's typed amount is its
    amount plus its VAT, or its amount alone for reverse charge, and the VAT
    lines are dropped.
    """
    posted: list[VoucherLine] = []
    typed: list[LineInput] = []
    index = 0
    for line in _ledger_lines(transaction):
        generated = line.account.account_number in _VAT_ACCOUNTS
        debit = line.debit or ZERO
        credit = line.credit or ZERO
        code = line.tax_code.code if line.tax_code is not None else None
        vat = (
            (line.debit_tax_amount or ZERO) + (line.credit_tax_amount or ZERO)
            if code is not None
            else None
        )
        if not generated:
            index += 1
            seed = TAX_CODES_BY_CODE.get(code) if code is not None else None
            extra = (
                vat if vat is not None and not (seed is not None and seed.reverse_charge) else ZERO
            )
            typed.append(
                LineInput(
                    account_number=line.account.account_number,
                    debit=debit + extra if debit > ZERO else ZERO,
                    credit=credit + extra if credit > ZERO else ZERO,
                    description=line.description,
                    party_pk=line.party.id if line.party is not None else None,
                    tax_code=code,
                    dimension_value_ids=dimensions.allocated_ids(line),
                )
            )
        posted.append(
            VoucherLine(
                from_line=max(index, 1),
                account_number=line.account.account_number,
                account_description=line.account.description,
                description=line.description,
                debit=debit,
                credit=credit,
                party_pk=line.party.id if line.party is not None else None,
                party_name=line.party.name if line.party is not None else None,
                tax_code=code,
                vat_amount=vat,
                generated=generated,
                dimension_value_ids=dimensions.allocated_ids(line),
            )
        )
    return posted, typed


async def _views(
    db: AsyncSession, company: Company, transactions: list[Transaction]
) -> list[ManualVoucher]:
    pks = [t.id for t in transactions]
    if not pks:
        return []
    reversals = await ledger_service._reversal_map(db, pks)
    corrections = {
        original: replacement
        for original, replacement in await db.execute(
            select(Transaction.corrects_transaction_pk, Transaction.id).where(
                Transaction.corrects_transaction_pk.in_(pks)
            )
        )
        if original is not None
    }
    explanations = {
        row.transaction_pk: row.text
        for row in await db.scalars(
            select(VoucherExplanation).where(
                VoucherExplanation.company_id == company.id,
                VoucherExplanation.transaction_pk.in_(pks),
            )
        )
    }
    authors = await ledger_service._author_names(db, [t.created_by_user_id for t in transactions])
    keys = await ledger_service._key_names(db, [t.created_via_agent_key_id for t in transactions])
    views: list[ManualVoucher] = []
    for transaction in transactions:
        posted, typed = _lines(transaction)
        views.append(
            ManualVoucher(
                id=transaction.id,
                voucher_no=transaction.transaction_id,
                occurred_on=transaction.transaction_date,
                posted_at=transaction.posted_at,
                description=transaction.description,
                explanation=explanations.get(transaction.id),
                lines=posted,
                input_lines=typed,
                total=sum((line.debit for line in posted), ZERO),
                is_reversal=transaction.reverses_transaction_pk is not None,
                reverses_pk=transaction.reverses_transaction_pk,
                reversed_by_pk=reversals.get(transaction.id),
                corrects_pk=transaction.corrects_transaction_pk,
                corrected_by_pk=corrections.get(transaction.id),
                created_by_id=transaction.created_by_user_id,
                created_by_name=(
                    None
                    if transaction.created_by_user_id is None
                    else authors.get(transaction.created_by_user_id)
                ),
                created_via=(
                    None
                    if transaction.created_via_agent_key_id is None
                    else keys.get(transaction.created_via_agent_key_id)
                ),
            )
        )
    return views


async def get(db: AsyncSession, *, company: Company, voucher_pk: int) -> ManualVoucher:
    """One manual voucher of this company's, read back whole."""
    transaction = await _manual(db, company, voucher_pk)
    return (await _views(db, company, [transaction]))[0]


async def list_vouchers(db: AsyncSession, *, company: Company, year: int) -> list[ManualVoucher]:
    """The year's manual vouchers, newest first, reversals and corrections included."""
    transactions = list(
        await db.scalars(
            ledger_service._voucher_query()
            .where(
                Journal.company_id == company.id,
                Journal.journal_id == MANUAL_JOURNAL_ID,
                Transaction.financial_year == year,
            )
            .order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
        )
    )
    return await _views(db, company, transactions)


async def usable_tax_codes(db: AsyncSession, *, company: Company, on: date) -> list[TaxCode]:
    """The company's VAT codes a manual voucher dated ``on`` may carry.

    The same rules the review applies, so the page offers nothing the review
    would refuse (docs/frontend-conventions.md: don't warn where you can decide).
    """
    registered = vatstatus.registered_on(company, on)
    found = list(
        await db.scalars(
            select(TaxCode)
            .where(TaxCode.company_id == company.id)
            .options(selectinload(TaxCode.rates))
            .order_by(TaxCode.id)
        )
    )
    return [tax for tax in found if _code_problem(tax.code, tax, registered, on) is None]
