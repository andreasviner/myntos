"""The year's depreciation voucher: reviewed, then posted, corrected by reversal.

**One voucher per year, in its own journal** (``AVSKR``), dated the year's last
day. Its lines come from the saldo specification (:mod:`.saldo`), one pair per
saldo and account:

- *avskrivning*: debit the depreciation account of the group
  (:data:`~myntos.domain.anlegg.rules.DEPRECIATION_ACCOUNT`), credit the asset
  account. The books follow the tax figures (docs/anlegg.md), so the book value
  on 1200 is the saldo.
- *negativ saldo*: debit the asset account, credit 3895. A negative saldo in
  a, c, d or j is income at least at the group's rate (§ 14-46).
- *gevinst* / *tap*: an asset in e to i (or outside the saldo system) that left
  is cleared off its account against 3880 or 7880. The gevinst- og tapskonto
  behind it is a tax balance and is never posted.

**The choice is stored beside the voucher.** How much to take, up to the
maximum, is the company's (Skatte-ABC D-2-9.1). Next year's saldo is built on
the choice made, so every saldo the voucher saw gets
:class:`~myntos.domain.anlegg.models.FixedAssetDepreciation` rows per account:
the basis it was chosen on, and what was taken. Without the basis, a saldo
deliberately left alone and a saldo that did not exist when the voucher was
posted would look the same, and only the second is a reason to correct it; and
a group that grew after posting would look unchanged.

**Corrected only from here.** A correction reverses the standing voucher and
posts the replacement in one request, linked through
``corrects_transaction_pk``, like the opening balance. The ledger's own
reversal refuses this journal, because a voucher reversed there would leave
the register reading choices that no longer stand, and a later year's voucher
rests on this one's: correcting a year whose successor is posted is refused
until the successor is reversed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from myntos.domain.anlegg import rules, saldo
from myntos.domain.anlegg.errors import (
    DepreciationExistsError,
    DepreciationNotPostableError,
    LaterYearDepreciatedError,
    NoDepreciationError,
    RegisterYearClosedError,
)
from myntos.domain.anlegg.models import FixedAsset, FixedAssetDepreciation, FixedAssetOpening
from myntos.domain.anlegg.register import ASSET_ACCOUNT_PREFIXES
from myntos.domain.anlegg.rules import ZERO, Saldogruppe
from myntos.domain.arsoppgjor.findings import Finding, blocking, feil, merknad
from myntos.domain.ledger import posting
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.chart import (
    DEPRECIATION_JOURNAL_DESCRIPTION,
    DEPRECIATION_JOURNAL_ID,
    DEPRECIATION_JOURNAL_TYPE,
    DEPRECIATION_VOUCHER_TYPE,
    IMPORT_JOURNAL_ID,
    KNOWN_ACCOUNTS,
    OPENING_JOURNAL_ID,
)
from myntos.domain.ledger.models import (
    Account,
    Company,
    FinancialYear,
    Journal,
    Transaction,
    TransactionLine,
)

_CENT = Decimal("0.01")


# --- Shapes ------------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedLine:
    """One line of the depreciation voucher, and the saldo it belongs to."""

    account_number: str
    description: str
    debit: Decimal
    credit: Decimal
    saldo_key: str
    kind: str


@dataclass(frozen=True)
class DepreciationVoucher:
    id: int
    voucher_no: str
    year: int
    transaction_date: date
    posted_at: datetime | None
    created_by_user_id: int | None
    is_reversal: bool
    reversed: bool
    corrects_id: int | None
    total: Decimal


@dataclass(frozen=True)
class DepreciationPlan:
    """What the page shows for a year, and what :func:`post` would post."""

    year: int
    specification: saldo.SaldoSpecification
    lines: list[PlannedLine]
    findings: list[Finding]
    standing: DepreciationVoucher | None
    history: list[DepreciationVoucher] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((line.debit for line in self.lines), ZERO)

    @property
    def postable(self) -> bool:
        return not blocking(self.findings)


# --- The journal and what stands -------------------------------------------------------


async def _journal(db: AsyncSession, company: Company) -> Journal:
    journal = await db.scalar(
        select(Journal).where(
            Journal.company_id == company.id, Journal.journal_id == DEPRECIATION_JOURNAL_ID
        )
    )
    if journal is None:
        journal = Journal(
            company_id=company.id,
            journal_id=DEPRECIATION_JOURNAL_ID,
            description=DEPRECIATION_JOURNAL_DESCRIPTION,
            journal_type=DEPRECIATION_JOURNAL_TYPE,
        )
        db.add(journal)
        await db.flush()
    return journal


async def _vouchers(db: AsyncSession, company: Company, year: int | None) -> list[Transaction]:
    query = select(Transaction).join(Journal, Journal.id == Transaction.journal_id)
    query = query.where(
        Journal.company_id == company.id,
        Journal.journal_id == DEPRECIATION_JOURNAL_ID,
        Transaction.posted_at.is_not(None),
    )
    if year is not None:
        query = query.where(Transaction.financial_year == year)
    return list(await db.scalars(query.order_by(Transaction.id)))


async def _standing(db: AsyncSession, company: Company, year: int) -> Transaction | None:
    """The year's voucher that counts: posted, not a reversal, not reversed."""
    reversal = aliased(Transaction, name="depreciation_reversal")
    found: Transaction | None = await db.scalar(
        select(Transaction)
        .join(Journal, Journal.id == Transaction.journal_id)
        .where(
            Journal.company_id == company.id,
            Journal.journal_id == DEPRECIATION_JOURNAL_ID,
            Transaction.financial_year == year,
            Transaction.posted_at.is_not(None),
            Transaction.reverses_transaction_pk.is_(None),
            ~select(reversal.id).where(reversal.reverses_transaction_pk == Transaction.id).exists(),
        )
        .order_by(Transaction.id.desc())
        .limit(1)
    )
    return found


async def _history(db: AsyncSession, company: Company, year: int) -> list[DepreciationVoucher]:
    vouchers = await _vouchers(db, company, year)
    reversed_pks = {v.reverses_transaction_pk for v in vouchers if v.reverses_transaction_pk}
    # .all() first: a Result has keys(), and dict() would take it for a mapping.
    totals = dict(
        (
            await db.execute(
                select(TransactionLine.transaction_pk, func.sum(TransactionLine.debit))
                .where(TransactionLine.transaction_pk.in_([v.id for v in vouchers]))
                .group_by(TransactionLine.transaction_pk)
            )
        )
        .tuples()
        .all()
    )
    return [
        DepreciationVoucher(
            id=v.id,
            voucher_no=v.transaction_id,
            year=v.financial_year,
            transaction_date=v.transaction_date,
            posted_at=v.posted_at,
            created_by_user_id=v.created_by_user_id,
            is_reversal=v.reverses_transaction_pk is not None,
            reversed=v.id in reversed_pks,
            corrects_id=v.corrects_transaction_pk,
            total=Decimal(str(totals.get(v.id) or 0)).quantize(_CENT),
        )
        for v in vouchers
    ]


async def _posted_record(
    db: AsyncSession, company: Company, voucher_pk: int
) -> tuple[dict[str, Decimal], dict[tuple[str, str], Decimal]]:
    """What the standing voucher chose per saldo, and the basis it was chosen on.

    Every saldo the voucher saw has a basis row, so a saldo with no
    depreciation row was left alone on purpose (chosen: nothing), and a saldo
    with no basis row did not exist when it was posted.
    """
    chosen: dict[str, Decimal] = {}
    basis: dict[tuple[str, str], Decimal] = {}
    for row in await db.scalars(
        select(FixedAssetDepreciation).where(
            FixedAssetDepreciation.company_id == company.id,
            FixedAssetDepreciation.transaction_pk == voucher_pk,
        )
    ):
        if row.kind == saldo.BASIS:
            chosen.setdefault(row.saldo_key, ZERO)
            basis[(row.saldo_key, row.account_number)] = row.amount
        elif row.kind in (saldo.DEPRECIATION, saldo.NEGATIVE_SALDO):
            chosen[row.saldo_key] = chosen.get(row.saldo_key, ZERO) + row.amount
    return chosen, basis


def _current_basis(spec: saldo.SaldoSpecification) -> dict[tuple[str, str], Decimal]:
    return {
        (s.key, share.account_number): share.basis
        for s in spec.saldi
        for share in s.accounts
        if share.basis != ZERO
    }


# --- Lines -------------------------------------------------------------------------------


def _lines(year: int, saldi: tuple[saldo.SaldoYear, ...]) -> list[PlannedLine]:
    lines: list[PlannedLine] = []
    for s in saldi:
        expense = rules.DEPRECIATION_ACCOUNT.get(s.saldogruppe)
        for share in s.accounts:
            if share.depreciation > ZERO and expense is not None:
                text = f"Avskrivning {year}: {s.label}"
                lines += [
                    PlannedLine(expense, text, share.depreciation, ZERO, s.key, saldo.DEPRECIATION),
                    PlannedLine(
                        share.account_number,
                        text,
                        ZERO,
                        share.depreciation,
                        s.key,
                        saldo.DEPRECIATION,
                    ),
                ]
            if share.income > ZERO:
                text = f"Inntektsføring av negativ saldo {year}: {s.label}"
                lines += [
                    PlannedLine(
                        share.account_number, text, share.income, ZERO, s.key, saldo.NEGATIVE_SALDO
                    ),
                    PlannedLine(
                        rules.NEGATIVE_SALDO_INCOME_ACCOUNT,
                        text,
                        ZERO,
                        share.income,
                        s.key,
                        saldo.NEGATIVE_SALDO,
                    ),
                ]
            if share.settled_gain > ZERO:
                text = f"Gevinst ved avgang {year}: {s.label}"
                lines += [
                    PlannedLine(
                        share.account_number, text, share.settled_gain, ZERO, s.key, saldo.GAIN
                    ),
                    PlannedLine(
                        rules.GAIN_ACCOUNT, text, ZERO, share.settled_gain, s.key, saldo.GAIN
                    ),
                ]
            elif share.settled_gain < ZERO:
                loss = -share.settled_gain
                text = f"Tap ved avgang {year}: {s.label}"
                lines += [
                    PlannedLine(rules.LOSS_ACCOUNT, text, loss, ZERO, s.key, saldo.LOSS),
                    PlannedLine(share.account_number, text, ZERO, loss, s.key, saldo.LOSS),
                ]
    return lines


def _comparable(lines: list[PlannedLine]) -> list[tuple[str, Decimal, Decimal]]:
    return sorted((line.account_number, line.debit, line.credit) for line in lines)


async def _voucher_lines(db: AsyncSession, voucher_pk: int) -> list[tuple[str, Decimal, Decimal]]:
    rows = await db.execute(
        select(Account.account_number, TransactionLine.debit, TransactionLine.credit)
        .join(Account, Account.id == TransactionLine.account_id)
        .where(TransactionLine.transaction_pk == voucher_pk)
    )
    return sorted((number, debit, credit) for number, debit, credit in rows.tuples())


# --- Findings ----------------------------------------------------------------------------


async def _ledger_by_account(
    db: AsyncSession, company: Company, year: int, exclude_pk: int | None
) -> dict[str, Decimal]:
    """Debit minus credit per asset account, every posted voucher up to ``year``.

    The year's own standing depreciation is left out, so the balance compares
    with the saldo's basis, which is before the year's depreciation.
    """
    query = (
        select(
            Account.account_number,
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .join(TransactionLine, TransactionLine.account_id == Account.id)
        .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
        .where(
            Account.company_id == company.id,
            Transaction.posted_at.is_not(None),
            Transaction.financial_year <= year,
        )
        .group_by(Account.account_number)
    )
    if exclude_pk is not None:
        query = query.where(
            Transaction.id != exclude_pk,
            # A reversal of the standing voucher cannot exist, but a voucher
            # and its reversal both left out keep the sum honest either way.
            func.coalesce(Transaction.reverses_transaction_pk, 0) != exclude_pk,
        )
    return {
        number: Decimal(str(debit)) - Decimal(str(credit))
        for number, debit, credit in (await db.execute(query)).tuples()
    }


async def _unregistered_purchases(
    db: AsyncSession, company: Company, year: int, assets: list[FixedAsset]
) -> list[str]:
    """Vouchers in ``year`` that debit an asset account and no asset in the register explains.

    An asset explains a voucher by naming it (``transaction_pk``), or by being
    on the same account, delivered the same year, at the same cost: a person
    who typed the asset in by hand has registered the purchase all the same.
    """
    linked = {a.transaction_pk for a in assets if a.transaction_pk is not None}
    typed = {
        (a.account_number, a.cost)
        for a in assets
        if a.transaction_pk is None and a.acquired_on.year == year
    }
    reversal = aliased(Transaction, name="purchase_reversal")
    rows = await db.execute(
        select(
            Transaction.id,
            Transaction.transaction_id,
            Account.account_number,
            TransactionLine.debit,
        )
        .join(Journal, Journal.id == Transaction.journal_id)
        .join(TransactionLine, TransactionLine.transaction_pk == Transaction.id)
        .join(Account, Account.id == TransactionLine.account_id)
        .where(
            Journal.company_id == company.id,
            Journal.journal_id.not_in(
                (DEPRECIATION_JOURNAL_ID, OPENING_JOURNAL_ID, IMPORT_JOURNAL_ID)
            ),
            Transaction.financial_year == year,
            Transaction.posted_at.is_not(None),
            Transaction.reverses_transaction_pk.is_(None),
            ~select(reversal.id).where(reversal.reverses_transaction_pk == Transaction.id).exists(),
            TransactionLine.debit > 0,
        )
        .order_by(Transaction.id)
    )
    found: list[str] = []
    for pk, voucher_no, number, debit in rows.tuples():
        if not number.startswith(ASSET_ACCOUNT_PREFIXES) or pk in linked:
            continue
        if (number, debit) in typed:
            continue
        label = f"{voucher_no} ({number})"
        if label not in found:
            found.append(label)
    return found


async def _findings(
    db: AsyncSession,
    company: Company,
    *,
    year: int,
    today: date,
    financial_year: FinancialYear | None,
    spec: saldo.SaldoSpecification,
    lines: list[PlannedLine],
    standing: Transaction | None,
    posted_basis: dict[tuple[str, str], Decimal],
    choices: Mapping[str, Decimal] | None,
) -> list[Finding]:
    findings: list[Finding] = []
    year_rules = rules.rules_for(year)
    first = await db.scalar(
        select(func.min(FinancialYear.year)).where(FinancialYear.company_id == company.id)
    )
    if financial_year is None and (first is None or year < int(first)):
        findings.append(
            feil("ingen_regnskapsaar", f"Selskapet har ikke noe regnskapsår {year} i Myntos.")
        )
    elif financial_year is None:
        # A later year is opened on demand, as for any voucher, once there is
        # something to post in it; until then it ends on 31 December.
        if today <= date(year, 12, 31):
            findings.append(
                feil(
                    "aaret_er_ikke_over",
                    f"Regnskapsåret {year} varer til 31.12.{year}. Avskrivningene føres når "
                    "året er over, fordi kjøp og salg fram til årsskiftet endrer saldoen.",
                )
            )
    elif financial_year.closed_at is not None:
        findings.append(
            feil(
                "aaret_er_avsluttet",
                f"Regnskapsåret {year} er avsluttet, og et avsluttet år tar ikke imot bilag. "
                "Gjenåpne det under Årsavslutning hvis avskrivningene må føres eller rettes.",
            )
        )
    elif today <= financial_year.end_date:
        findings.append(
            feil(
                "aaret_er_ikke_over",
                f"Regnskapsåret {year} varer til {financial_year.end_date:%d.%m.%Y}. "
                "Avskrivningene føres når året er over, fordi kjøp og salg fram til "
                "årsskiftet endrer saldoen.",
            )
        )
    if year_rules is None:
        findings.append(
            feil(
                "satser_mangler",
                f"Myntos har satsene og beløpsgrensene fra inntektsåret "
                f"{rules.earliest_year()}, og regner ikke ut avskrivninger for {year}.",
            )
        )
    elif year > rules.CONFIRMED_THROUGH:
        findings.append(
            merknad(
                "satser_ikke_bekreftet",
                f"Satsene for {year} er ikke kontrollert mot loven ennå. Myntos bruker satsene "
                f"fra {rules.CONFIRMED_THROUGH}; sjekk at de ikke er endret i statsbudsjettet.",
            )
        )
    if year < spec.start_year:
        findings.append(
            feil(
                "for_registerets_start",
                f"Anleggsregisteret begynner i {spec.start_year}. Avskrivningene for {year} ble "
                "ført i det gamle programmet.",
            )
        )

    assets = list(await db.scalars(select(FixedAsset).where(FixedAsset.company_id == company.id)))
    for asset in assets:
        group = Saldogruppe(asset.saldogruppe)
        if (
            group not in rules.COLLECTIVE | rules.PER_BUILDING
            and asset.acquired_on.year < spec.start_year
            and asset.opening_value is None
        ):
            findings.append(
                feil(
                    "mangler_inngaende_verdi",
                    f"«{asset.description}» ble levert før {spec.start_year}, det første året "
                    "registeret dekker, og har ingen skattemessig verdi ved inngangen til året. "
                    "Oppgi den på driftsmidlet.",
                )
            )

    accounts = {
        a.account_number
        for a in await db.scalars(select(Account).where(Account.company_id == company.id))
    }
    missing = sorted({line.account_number for line in lines} - accounts - set(KNOWN_ACCOUNTS))
    for number in missing:
        findings.append(
            feil(
                "ukjent_konto",
                f"Konto {number} finnes ikke i kontoplanen. Legg den til, eller flytt "
                "driftsmidlene som står på den til en konto som finnes.",
            )
        )

    if spec.unposted_years:
        years = ", ".join(str(y) for y in spec.unposted_years)
        findings.append(
            merknad(
                "tidligere_aar_uten_avskrivning",
                f"Det er ikke ført avskrivninger fra registeret for {years}. Saldoen er da "
                "regnet uten avskrivning de årene; før dem først hvis de skulle vært tatt.",
            )
        )

    if choices:
        by_key = {s.key: s for s in spec.saldi}
        for key, value in choices.items():
            found = by_key.get(key)
            if value != value.quantize(_CENT) or value < ZERO:
                findings.append(
                    feil("ugyldig_valg", f"Beløpet for saldo {key} er ikke et gyldig kronebeløp.")
                )
            elif found is None:
                findings.append(feil("ukjent_saldo", f"Det finnes ingen saldo {key} i {year}."))
            elif found.max_depreciation > ZERO and value > found.max_depreciation:
                findings.append(
                    feil(
                        "over_maks",
                        f"{found.label}: høyst {found.max_depreciation} kan avskrives i {year}.",
                    )
                )
            elif found.min_income > ZERO and not found.min_income <= value <= -found.basis:
                findings.append(
                    feil(
                        "under_minstebelop",
                        f"{found.label}: minst {found.min_income} og høyst {-found.basis} av "
                        "den negative saldoen skal inntektsføres.",
                    )
                )
            elif found.max_depreciation == ZERO and found.min_income == ZERO:
                findings.append(
                    feil("ingen_valg", f"{found.label}: her er det ingenting å velge i {year}.")
                )

    ledger = await _ledger_by_account(
        db, company, year, standing.id if standing is not None else None
    )
    basis_by_account: dict[str, Decimal] = {}
    for s in spec.saldi:
        for share in s.accounts:
            if share.account_number:
                basis_by_account[share.account_number] = (
                    basis_by_account.get(share.account_number, ZERO) + share.basis
                )
    for number, basis in sorted(basis_by_account.items()):
        booked = ledger.get(number, ZERO)
        if booked != basis:
            findings.append(
                merknad(
                    "konto_avviker",
                    f"Konto {number} står med {booked} i regnskapet, men registeret har "
                    f"{basis} på den før årets avskrivning. Det kan være et kjøp som ikke er "
                    "registrert, et salg ført på en annen konto, eller en inngående saldo som "
                    "ikke stemmer med balansen.",
                )
            )

    unregistered = await _unregistered_purchases(db, company, year, assets)
    if unregistered:
        findings.append(
            merknad(
                "kjop_ikke_i_registeret",
                "Disse bilagene fører noe på en konto for anleggsmidler uten at det står i "
                "registeret: " + ", ".join(unregistered) + ". Registrer driftsmidlet, og knytt "
                "det til bilaget.",
            )
        )

    if year_rules is not None:
        small = [
            a.description
            for a in assets
            if a.acquired_on.year == year
            and a.opening_value is None
            and Saldogruppe(a.saldogruppe) in rules.SALDO_GROUPS
            and a.cost < year_rules.capitalisation_limit
        ]
        if small:
            findings.append(
                merknad(
                    "under_belopsgrensen",
                    f"Kostet under {year_rules.capitalisation_limit:,.0f}".replace(",", " ")
                    + " kroner: "
                    + ", ".join(f"«{d}»" for d in small)
                    + ". Slike kjøp kan også føres som kostnad med en gang (skatteloven § 14-40 "
                    "annet ledd). Påkostning på et driftsmiddel som avskrives, skal likevel "
                    "på saldo.",
                )
            )

    if standing is not None:
        # Out of date when the voucher would come out different, or when a
        # saldo's basis moved under it: a choice of 10 000 out of 20 000 is
        # still a lawful voucher once a new machine joins the group, but it was
        # not made on the saldo the group has now.
        recorded = {k: v for k, v in posted_basis.items() if v != ZERO}
        if _comparable(lines) != await _voucher_lines(db, standing.id) or (
            recorded != _current_basis(spec)
        ):
            findings.append(
                merknad(
                    "avskrivning_utdatert",
                    f"Avskrivningene for {year} er ført, men registeret gir ikke lenger det "
                    "samme bilaget. Rett dem hvis endringen i registeret er riktig.",
                )
            )
    elif not lines and not blocking(findings):
        findings.append(
            feil("ingenting_a_fore", f"Registeret har ingen avskrivning å føre for {year}.")
        )
    return findings


# --- The plan ----------------------------------------------------------------------------


async def plan(
    db: AsyncSession,
    *,
    company: Company,
    year: int,
    today: date,
    choices: Mapping[str, Decimal] | None = None,
) -> DepreciationPlan:
    """The year's saldi and the voucher Myntos would post now.

    Without ``choices`` a year already posted is shown with the choices its
    voucher made, so the plan and the standing voucher agree unless the
    register changed since; a year not posted is shown at the maximum the law
    allows, and the minimum for a negative saldo. ``choices`` (saldo key to
    amount) overrides either, per saldo.
    """
    financial_year = await db.scalar(
        select(FinancialYear).where(
            FinancialYear.company_id == company.id, FinancialYear.year == year
        )
    )
    standing = await _standing(db, company, year)
    effective: dict[str, Decimal] = {}
    posted_basis: dict[tuple[str, str], Decimal] = {}
    if standing is not None:
        posted_choices, posted_basis = await _posted_record(db, company, standing.id)
        effective.update(posted_choices)
    if choices:
        effective.update(choices)
    rolled = await saldo.roll_forward(db, company, year, choices=effective or None, proposal=True)
    if rolled is None:
        # Before the register, or before the first year with rates: the
        # specification says so with no saldi, and the findings say why.
        spec = await saldo.specification(db, company=company, year=year)
    else:
        spec = saldo.SaldoSpecification(
            company_id=company.id,
            year=year,
            start_year=rolled.register.start_year,
            rules_from_year=rolled.year_rules.from_year,
            rates_confirmed=year <= rules.CONFIRMED_THROUGH,
            posted=standing is not None,
            saldi=tuple(rolled.saldi),
            gain_loss_account=rolled.gain_loss,
            unposted_years=rolled.unposted_years,
        )
    lines = _lines(year, spec.saldi)
    findings = await _findings(
        db,
        company,
        year=year,
        today=today,
        financial_year=financial_year,
        spec=spec,
        lines=lines,
        standing=standing,
        posted_basis=posted_basis,
        choices=choices,
    )
    history = await _history(db, company, year)
    return DepreciationPlan(
        year=year,
        specification=spec,
        lines=lines,
        findings=findings,
        standing=next((v for v in history if standing is not None and v.id == standing.id), None),
        history=history,
    )


# --- Posting -----------------------------------------------------------------------------


async def _refuse_if_later_posted(db: AsyncSession, company: Company, year: int) -> None:
    reversal = aliased(Transaction, name="later_reversal")
    later = await db.scalar(
        select(func.min(Transaction.financial_year))
        .join(Journal, Journal.id == Transaction.journal_id)
        .where(
            Journal.company_id == company.id,
            Journal.journal_id == DEPRECIATION_JOURNAL_ID,
            Transaction.financial_year > year,
            Transaction.posted_at.is_not(None),
            Transaction.reverses_transaction_pk.is_(None),
            ~select(reversal.id).where(reversal.reverses_transaction_pk == Transaction.id).exists(),
        )
    )
    if later is not None:
        raise LaterYearDepreciatedError(
            f"avskrivningene for {int(later)} er ført, og saldoen de bygger på kommer fra "
            f"{year}. Reverser {int(later)} først"
        )


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
        description=f"Reversering av avskrivninger {original.transaction_id}",
    )
    reversal.reverses_transaction_pk = original.id
    reversal.created_by_user_id = created_by_user_id
    posting.post(reversal)
    db.add(reversal)
    await db.flush()
    return reversal


async def post(
    db: AsyncSession,
    *,
    company: Company,
    year: int,
    today: date,
    choices: Mapping[str, Decimal] | None = None,
    correct: bool = False,
    created_by_user_id: int | None = None,
) -> Transaction:
    """Post the year's depreciation, or with ``correct`` replace the standing voucher.

    The plan runs again here, on what is about to be posted, so the button the
    page enabled and the rule enforced here are the same rule.
    """
    checked = await plan(db, company=company, year=year, today=today, choices=choices)
    problems = blocking(checked.findings)
    if problems:
        raise DepreciationNotPostableError(
            "avskrivningene kan ikke føres slik registeret står. Se funnene", problems
        )
    standing = checked.standing
    if standing is not None and not correct:
        raise DepreciationExistsError(
            f"avskrivningene for {year} er allerede ført. Retting gjøres ved å reversere dem og "
            "føre nye, som et eget valg"
        )
    if standing is None and correct:
        raise NoDepreciationError(f"det er ingen førte avskrivninger for {year} å rette")
    await _refuse_if_later_posted(db, company, year)
    financial_year = await ledger_service.ensure_financial_year(db, company, year)

    # Imported here: kontoplan reaches arsoppgjor.closing, which reaches this
    # module for its findings, and a top-level import would close the circle.
    from myntos.domain.ledger import kontoplan

    accounts: dict[str, Account] = {}
    for number in sorted({line.account_number for line in checked.lines}):
        if number in KNOWN_ACCOUNTS:
            accounts[number] = await kontoplan.ensure_seeded_account(
                db, company=company, number=number
            )
        else:
            account = await db.scalar(
                select(Account).where(
                    Account.company_id == company.id, Account.account_number == number
                )
            )
            if account is None:  # the plan's ukjent_konto, raced by a deletion
                raise DepreciationNotPostableError(f"konto {number} finnes ikke", [])
            accounts[number] = account

    if standing is not None:
        await _reverse(
            db, company=company, voucher_pk=standing.id, created_by_user_id=created_by_user_id
        )
    journal = await _journal(db, company)
    end = financial_year.end_date
    transaction = Transaction(
        journal_id=journal.id,
        transaction_id=await ledger_service._next_voucher_number(db, journal.id, year),
        financial_year=year,
        period=end.month,
        transaction_date=end,
        gl_posting_date=end,
        description=f"Avskrivninger {year}",
        voucher_type=DEPRECIATION_VOUCHER_TYPE,
        corrects_transaction_pk=standing.id if standing is not None else None,
        created_by_user_id=created_by_user_id,
    )
    transaction.lines = [
        TransactionLine(
            record_id=str(number),
            account=accounts[line.account_number],
            description=line.description[:255],
            debit=line.debit,
            credit=line.credit,
        )
        for number, line in enumerate(checked.lines, start=1)
    ]
    posting.post(transaction)
    db.add(transaction)
    await db.flush()

    rows: list[FixedAssetDepreciation] = []
    for s_ in checked.specification.saldi:
        for share in s_.accounts:
            # The basis is recorded for every saldo the voucher saw, zero and
            # negative included: it is what says a later change to the register
            # has moved the saldo under the voucher (avskrivning_utdatert).
            rows.append(
                _row(company, year, transaction, s_, share.account_number, saldo.BASIS, share.basis)
            )
            for kind, amount in (
                (saldo.DEPRECIATION, share.depreciation),
                (saldo.NEGATIVE_SALDO, share.income),
                (saldo.GAIN, max(share.settled_gain, ZERO)),
                (saldo.LOSS, max(-share.settled_gain, ZERO)),
            ):
                if amount > ZERO:
                    rows.append(
                        _row(company, year, transaction, s_, share.account_number, kind, amount)
                    )
    db.add_all(rows)
    await db.flush()
    return transaction


def _row(
    company: Company,
    year: int,
    transaction: Transaction,
    s: saldo.SaldoYear,
    account_number: str,
    kind: str,
    amount: Decimal,
) -> FixedAssetDepreciation:
    return FixedAssetDepreciation(
        company_id=company.id,
        year=year,
        transaction_pk=transaction.id,
        saldo_key=s.key,
        saldogruppe=s.saldogruppe.value,
        account_number=account_number,
        kind=kind,
        amount=amount,
    )


async def reverse(
    db: AsyncSession, *, company: Company, year: int, created_by_user_id: int | None = None
) -> Transaction:
    """Withdraw the year's depreciation without replacing it."""
    financial_year = await db.scalar(
        select(FinancialYear).where(
            FinancialYear.company_id == company.id, FinancialYear.year == year
        )
    )
    if financial_year is not None and financial_year.closed_at is not None:
        raise RegisterYearClosedError(
            f"regnskapsåret {year} er avsluttet. Gjenåpne det under Årsavslutning først"
        )
    standing = await _standing(db, company, year)
    if standing is None:
        raise NoDepreciationError(f"det er ingen førte avskrivninger for {year} å reversere")
    await _refuse_if_later_posted(db, company, year)
    return await _reverse(
        db, company=company, voucher_pk=standing.id, created_by_user_id=created_by_user_id
    )


# --- What the year-end close says ---------------------------------------------------------


async def closing_findings(
    db: AsyncSession, *, company: Company, year: int, today: date
) -> list[Finding]:
    """Merknader for the year-end review: depreciation not posted, or posted and out of date.

    Merknader, not feil: a company may keep its driftsmidler elsewhere, or
    have none worth depreciating this year, and the close is still right.
    """
    has_register = await db.scalar(
        select(func.count()).select_from(FixedAsset).where(FixedAsset.company_id == company.id)
    ) or await db.scalar(
        select(func.count())
        .select_from(FixedAssetOpening)
        .where(FixedAssetOpening.company_id == company.id)
    )
    if not has_register:
        return []
    found = await plan(db, company=company, year=year, today=today)
    if found.standing is None and found.lines:
        return [
            merknad(
                "avskrivning_ikke_fort",
                f"Anleggsregisteret har avskrivninger for {year} som ikke er ført "
                f"({found.total} kr). Før dem under Anlegg før året avsluttes; et avsluttet "
                "år tar ikke imot flere bilag.",
            )
        ]
    return [f for f in found.findings if f.code == "avskrivning_utdatert"]
