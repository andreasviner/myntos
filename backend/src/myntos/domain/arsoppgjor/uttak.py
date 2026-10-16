"""Private uttak: the farm's own produce taken out for the household.

The milk the family drinks, the potatoes, the meat from an animal slaughtered
for the freezer, the firewood, and the stall and hay for a horse kept for
pleasure are income to the farm and a withdrawal by its owner. Bokføringsforskriften
§ 3-1 første ledd nr. 5 asks for a list of them; this is that list, posted as
**one voucher** in its own journal (``UTTAK``):

::

    2060 Privatkonto                          D  value + VAT
    3161-3166 Uttak av … til privat bruk      K  value        ← one line per product
    2700 Utgående mva                         K  VAT          ← only when there is any

**What it is worth** (FOR-2025-12-01-2404, read on Lovdata 2026-10-15): milk
410 kr and potatoes 130 kr per husstandsmedlem a year (§ 3-2-3; half for those
over 70, and for potatoes for children under 10, which is a quantity of 0.5);
firewood 750 kr per løs m³ and flis 270 kr (§ 3-2-6); stall and hay for a horse
outside the farm 21 700, 14 900 or 9 800 kr (§ 3-2-7). Everything else, meat
and eggs included, at **omsetningsverdi**, what it would have fetched (§ 3-2-2),
which the person states. A year Skattedirektoratet has not published has no
norms, and a norm line in it is refused (``ingen_satser``).

**The VAT is decided per line, by the melding the line falls in**
(:func:`myntos.domain.mva.naering.Naeringer.of_line`), read on the voucher's date:

- *Primærnæring, filed once a year*: **fritatt**, code 5. «Uttak av produkter
  fra eget fiske, skogbruk eller jordbruk med binæringer, er fritatt for
  merverdiavgift dersom produktene skal brukes privat» (merverdiavgiftsloven
  § 6-20 første ledd), and the fritak «gjelder avgiftssubjekter som skal levere
  skattemelding en gang i året etter skatteforvaltningsforskriften § 8-3-7
  første ledd» (FMVA § 6-20-1 første ledd). Both read on Lovdata 2026-10-15.
  Code 5 is «omsetning og uttak fritatt», so the line is reported there; the
  ``uttak`` spesifikasjon is written only when Myntos records it for code 5
  (:data:`myntos.domain.mva.codes.SPESIFIKASJON_CODES`), which it does not yet.
- *Primærnæring on two-monthly terms, or an alminnelig melding*: the fritak does
  not reach it, and the uttak is taxed as a sale (§ 3-21) at its
  omsetningsverdi (§ 4-9): 15 % for food (§ 5-2: potatoes, meat, eggs,
  vegetables), 25 % for the rest (raw milk, firewood, a horse's hay). Code 31
  or 3, spesifikasjon ``uttak``. The norms are the tax rules' values; a merknad
  says so, since the VAT basis is what the produce would have fetched.
- *Not registered on the date*: no code. Income all the same.

**Who may.** An ENK's owner takes out of their own business, and the
privatkonto closes against the owner's equity at year end. An AS has no
privatkonto: what its shareholder takes is salary, a loan or a dividend, and
the list is refused. Other forms (ANS, DA) get a merknad and may go on: the
partner's withdrawal is a privatuttak there too, but the split is theirs.

**Not here.** Kår (føderåd) is a benefit in kind to the previous owner, paid
through the a-melding (§ 3-2-4 for the housing), which Myntos does not file.
Gifts of produce are not private use and are taxed in any melding; book them
on /mva-justeringer/.

**Corrections and withdrawal** work as for the count (:mod:`.buskap`): a new
list replaces the standing one in one request (``korriger``), and a withdrawal
reverses the voucher and stores an empty list in its place.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from myntos.domain.arsoppgjor import takseringsregler as regler
from myntos.domain.arsoppgjor.buskap import catalogue, year_refusal
from myntos.domain.arsoppgjor.closing import normalised_form
from myntos.domain.arsoppgjor.errors import (
    FarmYearNotOpenError,
    InvalidUttakError,
    NoUttakError,
    UttakExistsError,
)
from myntos.domain.arsoppgjor.findings import Finding, blocking, feil, merknad
from myntos.domain.arsoppgjor.landbruk_models import FarmWithdrawal, FarmWithdrawalLine
from myntos.domain.ledger import kontoplan, landbruk, posting
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.chart import (
    KNOWN_ACCOUNTS,
    OUTPUT_VAT_ACCOUNT,
    WITHDRAWAL_JOURNAL_DESCRIPTION,
    WITHDRAWAL_JOURNAL_ID,
    WITHDRAWAL_JOURNAL_TYPE,
)
from myntos.domain.ledger.errors import SegmentNotEnabledError
from myntos.domain.ledger.models import (
    Account,
    AccountType,
    Company,
    FinancialYear,
    Journal,
    Transaction,
    TransactionLine,
)
from myntos.domain.ledger.vatstatus import registered_on
from myntos.domain.mva import naering
from myntos.domain.mva.adjustments import DEFAULT_ACCOUNTS
from myntos.domain.mva.codes import SPESIFIKASJON_CODES, Meldingskategori, Spesifikasjon
from myntos.domain.mva.periods import PeriodType

ZERO = Decimal("0.00")
_QUANTITY_STEP = Decimal("0.001")
_UNIT_STEP = Decimal("0.0001")

UTTAK_VOUCHER_TYPE = "privat_uttak"
#: The owner's side: the privatkonto, as for every other uttak.
PRIVATE_ACCOUNT = DEFAULT_ACCOUNTS[Spesifikasjon.UTTAK]

#: Fritatt: the uttak of a primary producer on the yearly melding.
EXEMPT_CODE = "5"
FOOD_CODE = "31"
STANDARD_CODE = "3"

# A withdrawn list is an empty list whose lines say nothing; it has no column of
# its own because the list is never updated (landbruk_models).


# --- Shapes ------------------------------------------------------------------------


@dataclass(frozen=True)
class UttakLineInput:
    product: str
    quantity: Decimal
    description: str = ""
    #: Required for products valued at omsetningsverdi; for a norm, only to
    #: depart from it (a foal's stall), with the reason in the description.
    unit_value: Decimal | None = None
    unit: str | None = None
    #: None: the product's own uttak account.
    account_number: str | None = None


@dataclass(frozen=True)
class UttakLine:
    product: str
    label: str
    description: str
    quantity: Decimal
    unit: str
    unit_value: Decimal
    value: Decimal
    account_number: str
    tax_code: str | None
    vat_rate: Decimal | None
    vat: Decimal
    #: ``fritatt``, ``avgiftspliktig`` or ``uregistrert``: why the line has the
    #: code it has, so the page can say it in words.
    vat_reason: str


@dataclass(frozen=True)
class UttakReview:
    year: int
    occurred_on: date | None
    lines: list[UttakLine]
    total_value: Decimal
    total_vat: Decimal
    findings: list[Finding]

    @property
    def postable(self) -> bool:
        return not blocking(self.findings)


@dataclass(frozen=True)
class UttakView:
    id: int
    year: int
    occurred_on: date
    corrects_id: int | None
    voucher_id: int | None
    voucher_no: str | None
    created_by_user_id: int | None
    created_at: datetime
    withdrawn: bool
    lines: list[UttakLine]
    total_value: Decimal
    total_vat: Decimal


@dataclass(frozen=True)
class UttakState:
    year: int
    #: The default date: the last day of the year.
    occurred_on: date | None
    refusal: str | None
    rates: regler.YearRates | None
    catalogue: regler.YearRates
    standing: UttakView | None
    history: list[UttakView]


def _require_landbruk(company: Company) -> None:
    if not landbruk.is_enabled(company):
        raise SegmentNotEnabledError(
            "private uttak av gårdens produkter hører til landbrukstillegget, og det er ikke "
            "slått på for dette selskapet. Slå det på under Kontoplan"
        )


def _product(known: regler.YearRates, code: str) -> regler.UttakProduct | None:
    if code == regler.ANNET_UTTAK:
        return regler.ANNET_PRODUCT
    return known.product(code)


async def _financial_year(db: AsyncSession, company: Company, year: int) -> FinancialYear | None:
    found: FinancialYear | None = await db.scalar(
        select(FinancialYear).where(
            FinancialYear.company_id == company.id, FinancialYear.year == year
        )
    )
    return found


# --- Reading ------------------------------------------------------------------------


def _vat_reason(tax_code: str | None) -> str:
    if tax_code is None:
        return "uregistrert"
    return "fritatt" if tax_code == EXEMPT_CODE else "avgiftspliktig"


def _line_of(row: FarmWithdrawalLine, known: regler.YearRates) -> UttakLine:
    product = _product(known, row.product)
    return UttakLine(
        product=row.product,
        label=product.label if product is not None else row.description,
        description=row.description,
        quantity=row.quantity,
        unit=row.unit,
        unit_value=row.unit_value,
        value=row.value,
        account_number=row.account_number,
        tax_code=row.tax_code,
        vat_rate=row.vat_rate,
        vat=row.vat,
        vat_reason=_vat_reason(row.tax_code),
    )


async def _lists(db: AsyncSession, company: Company, year: int) -> list[FarmWithdrawal]:
    return list(
        await db.scalars(
            select(FarmWithdrawal)
            .where(FarmWithdrawal.company_id == company.id, FarmWithdrawal.financial_year == year)
            .options(selectinload(FarmWithdrawal.lines))
            .order_by(FarmWithdrawal.id)
        )
    )


async def _view_all(db: AsyncSession, rows: Sequence[FarmWithdrawal]) -> list[UttakView]:
    pks = [r.transaction_pk for r in rows if r.transaction_pk is not None]
    numbers: dict[int, str] = {}
    if pks:
        found = await db.execute(
            select(Transaction.id, Transaction.transaction_id).where(Transaction.id.in_(pks))
        )
        numbers = dict(found.tuples().all())
    views = []
    for row in rows:
        known = catalogue(row.financial_year)
        lines = [_line_of(line, known) for line in row.lines]
        views.append(
            UttakView(
                id=row.id,
                year=row.financial_year,
                occurred_on=row.occurred_on,
                corrects_id=row.corrects_id,
                voucher_id=row.transaction_pk,
                voucher_no=numbers.get(row.transaction_pk) if row.transaction_pk else None,
                created_by_user_id=row.created_by_user_id,
                created_at=row.created_at,
                withdrawn=not row.lines,
                lines=lines,
                total_value=sum((li.value for li in lines), ZERO),
                total_vat=sum((li.vat for li in lines), ZERO),
            )
        )
    return views


async def _standing_row(db: AsyncSession, company: Company, year: int) -> FarmWithdrawal | None:
    """The newest list for the year, unless it is a withdrawal (a list with no lines)."""
    rows = await _lists(db, company, year)
    if not rows or not rows[-1].lines:
        return None
    return rows[-1]


async def state(db: AsyncSession, *, company: Company, year: int) -> UttakState:
    _require_landbruk(company)
    fiscal = await _financial_year(db, company, year)
    history = await _view_all(db, await _lists(db, company, year))
    standing = history[-1] if history and not history[-1].withdrawn else None
    return UttakState(
        year=year,
        occurred_on=fiscal.end_date if fiscal is not None else None,
        refusal=year_refusal(fiscal, year),
        rates=regler.for_year(year),
        catalogue=catalogue(year),
        standing=standing,
        history=history,
    )


# --- The review -------------------------------------------------------------------


def _decimal_problem(value: Decimal, step: Decimal, what: str) -> str | None:
    if value < 0:
        return f"{what} kan ikke være negativ"
    if value != value.quantize(step):
        return f"{what} har for mange desimaler"
    return None


def _vat_code(
    company: Company,
    meldinger: naering.Naeringer,
    day: date,
    account_number: str,
    product: regler.UttakProduct,
) -> str | None:
    if not registered_on(company, day):
        return None
    category = meldinger.of_line(day, account_number)
    melding = meldinger.get(category.value)
    if category is Meldingskategori.PRIMAERNAERING and melding.period_type is PeriodType.AAR:
        return EXEMPT_CODE
    return FOOD_CODE if product.food else STANDARD_CODE


async def review(
    db: AsyncSession,
    *,
    company: Company,
    year: int,
    lines: Sequence[UttakLineInput],
    occurred_on: date | None = None,
) -> UttakReview:
    """Value the list and decide each line's VAT, without posting or storing anything."""
    _require_landbruk(company)
    findings: list[Finding] = []
    fiscal = await _financial_year(db, company, year)
    refusal = year_refusal(fiscal, year)
    if refusal is not None:
        findings.append(feil("aar_ikke_aapent", refusal[0].upper() + refusal[1:] + "."))
    day = occurred_on or (fiscal.end_date if fiscal is not None else None)
    if fiscal is not None and day is not None and not fiscal.start_date <= day <= fiscal.end_date:
        findings.append(
            feil(
                "dato_utenfor_aaret",
                f"Datoen {day.strftime('%d.%m.%Y')} er ikke i regnskapsåret {year}.",
            )
        )
    form = normalised_form(company)
    if form in {"AS", "ASA"}:
        findings.append(
            feil(
                "ikke_for_aksjeselskap",
                "Et aksjeselskap har ingen privatkonto: det eieren tar ut av selskapet er lønn, "
                "lån eller utbytte, og føres som det. Private uttak gjelder "
                "enkeltpersonforetak.",
            )
        )
    elif form not in {None, "ENK"}:
        findings.append(
            merknad(
                "ikke_enkeltpersonforetak",
                f"Selskapet er et {form}. Uttaket føres på privatkonto 2060; fordelingen "
                "mellom deltakerne er deres egen.",
            )
        )
    if not lines:
        findings.append(feil("ingen_linjer", "Listen over private uttak har ingen linjer."))

    rates = regler.for_year(year)
    known = catalogue(year)
    accounts = {
        a.account_number: a
        for a in await db.scalars(select(Account).where(Account.company_id == company.id))
    }
    meldinger = await naering.load(db, company)
    resolved: list[UttakLine] = []
    norm_taxed = False
    for number, line in enumerate(lines, start=1):
        where = f"Linje {number}"
        product = _product(known, line.product)
        if product is None:
            findings.append(
                feil(
                    "ukjent_produkt",
                    f"{where}: «{line.product}» er ikke et produkt Myntos kjenner.",
                )
            )
            continue
        problem = _decimal_problem(line.quantity, _QUANTITY_STEP, "mengden")
        if problem is None and line.quantity == 0:
            problem = "mengden må være større enn null"
        if problem is not None:
            findings.append(feil("ugyldig_mengde", f"{where}: {problem}."))
            continue
        if product.value is not None:
            if rates is None:
                findings.append(
                    feil(
                        "ingen_satser",
                        f"{where}: Skattedirektoratet har ikke publisert normene for {year} "
                        f"ennå (de kommer i desember). {product.label} kan ikke verdsettes med "
                        "fjorårets sats.",
                    )
                )
                continue
            unit_value = product.value
            if line.unit_value is not None and line.unit_value != product.value:
                if not line.description.strip():
                    findings.append(
                        feil(
                            "mangler_begrunnelse",
                            f"{where}: normen for {product.label.lower()} er {product.value} kr. "
                            "En annen verdi trenger en begrunnelse i beskrivelsen.",
                        )
                    )
                    continue
                findings.append(
                    merknad(
                        "avvik_fra_norm",
                        f"{where}: normen for {product.label.lower()} er {product.value} kr, "
                        f"og listen bruker {line.unit_value} kr ({line.description.strip()}).",
                    )
                )
                unit_value = line.unit_value
        else:
            if line.unit_value is None:
                findings.append(
                    feil(
                        "mangler_verdi",
                        f"{where}: {product.label} har ingen norm, og verdien er det varen "
                        "ville fått ved salg (takseringsreglene § 3-2-2). Oppgi den per "
                        f"{line.unit or product.unit}.",
                    )
                )
                continue
            unit_value = line.unit_value
        problem = _decimal_problem(unit_value, _UNIT_STEP, "verdien")
        if problem is not None:
            findings.append(feil("ugyldig_verdi", f"{where}: {problem}."))
            continue
        account_number = (line.account_number or "").strip() or product.account
        account = accounts.get(account_number)
        if account is None and account_number not in KNOWN_ACCOUNTS:
            findings.append(
                feil(
                    "ukjent_konto",
                    f"{where}: konto {account_number} finnes ikke i kontoplanen.",
                )
            )
            continue
        if account is not None and account.account_type is not AccountType.INNTEKT:
            findings.append(
                feil(
                    "ikke_inntektskonto",
                    f"{where}: et uttak er en inntekt, og konto {account_number} er ikke en "
                    "inntektskonto.",
                )
            )
            continue
        value = posting.quantize_money(line.quantity * unit_value)
        tax_code: str | None = None
        rate: Decimal | None = None
        vat = ZERO
        if day is not None:
            tax_code = _vat_code(company, meldinger, day, account_number, product)
        if tax_code is not None and day is not None:
            tax = await ledger_service._resolve_tax_code(db, company, tax_code)
            rate = posting.rate_on(tax, day)
            vat = posting.compute_tax(value, rate)
            if tax_code != EXEMPT_CODE and product.value is not None:
                norm_taxed = True
        resolved.append(
            UttakLine(
                product=line.product,
                label=product.label,
                description=line.description.strip(),
                quantity=line.quantity,
                unit=(line.unit or product.unit).strip()[:16],
                unit_value=unit_value,
                value=value,
                account_number=account_number,
                tax_code=tax_code,
                vat_rate=rate,
                vat=vat,
                vat_reason=_vat_reason(tax_code),
            )
        )
    if norm_taxed:
        findings.append(
            merknad(
                "norm_som_mva_grunnlag",
                "Uttaket er avgiftspliktig her, og mva-grunnlaget er det produktet ville fått ved "
                "salg (merverdiavgiftsloven § 4-9). Normene er skattereglenes verdi; er "
                "omsetningsverdien en annen, før den i stedet.",
            )
        )
    if any(li.tax_code is not None and li.tax_code != EXEMPT_CODE for li in resolved):
        findings.append(
            merknad(
                "kommer_i_mva_meldingen",
                "Uttaket kommer med i mva-meldingen for terminen datoen hører til. Er den "
                "terminen allerede sendt, viser mva-siden forskjellen.",
            )
        )
    return UttakReview(
        year=year,
        occurred_on=day,
        lines=resolved,
        total_value=sum((li.value for li in resolved), ZERO),
        total_vat=sum((li.vat for li in resolved), ZERO),
        findings=findings,
    )


# --- Posting ------------------------------------------------------------------------


async def _journal(db: AsyncSession, company: Company) -> Journal:
    journal = await db.scalar(
        select(Journal).where(
            Journal.company_id == company.id, Journal.journal_id == WITHDRAWAL_JOURNAL_ID
        )
    )
    if journal is None:
        journal = Journal(
            company_id=company.id,
            journal_id=WITHDRAWAL_JOURNAL_ID,
            description=WITHDRAWAL_JOURNAL_DESCRIPTION,
            journal_type=WITHDRAWAL_JOURNAL_TYPE,
        )
        db.add(journal)
        await db.flush()
    return journal


async def _post_voucher(
    db: AsyncSession,
    *,
    company: Company,
    year: int,
    checked: UttakReview,
    corrects_pk: int | None,
    created_by_user_id: int | None,
) -> Transaction:
    assert checked.occurred_on is not None  # noqa: S101 - the review refuses a year without one
    day = checked.occurred_on
    journal = await _journal(db, company)
    # The uttak accounts joined the add-on after farms had switched it on.
    for number in sorted(
        {li.account_number for li in checked.lines} | {PRIVATE_ACCOUNT, OUTPUT_VAT_ACCOUNT}
    ):
        if number in KNOWN_ACCOUNTS:
            await kontoplan.ensure_seeded_account(db, company=company, number=number)
    accounts = await ledger_service._accounts_by_number(db, company)
    text = f"Private uttak {year}"
    lines: list[TransactionLine] = [
        TransactionLine(
            record_id="1",
            account=accounts[PRIVATE_ACCOUNT],
            description=text,
            debit=checked.total_value + checked.total_vat,
            credit=ZERO,
        )
    ]
    for position, line in enumerate(checked.lines, start=2):
        row = TransactionLine(
            record_id=str(position),
            account=accounts[line.account_number],
            description=(line.description or line.label)[:255],
            debit=ZERO,
            credit=line.value,
        )
        if line.tax_code is not None and line.vat_rate is not None:
            tax = await ledger_service._resolve_tax_code(db, company, line.tax_code)
            posting.set_tax_information(
                row, tax_code=tax, rate=line.vat_rate, basis=line.value, amount=line.vat
            )
            if line.tax_code in SPESIFIKASJON_CODES[Spesifikasjon.UTTAK]:
                row.vat_specification = Spesifikasjon.UTTAK.value
        lines.append(row)
    if checked.total_vat > ZERO:
        lines.append(
            TransactionLine(
                record_id=str(len(lines) + 1),
                account=accounts[OUTPUT_VAT_ACCOUNT],
                description=text,
                debit=ZERO,
                credit=checked.total_vat,
            )
        )
    transaction = Transaction(
        journal_id=journal.id,
        transaction_id=await ledger_service._next_voucher_number(db, journal.id, year),
        financial_year=year,
        period=day.month,
        transaction_date=day,
        gl_posting_date=day,
        description=text,
        voucher_type=UTTAK_VOUCHER_TYPE,
        corrects_transaction_pk=corrects_pk,
        created_by_user_id=created_by_user_id,
    )
    transaction.lines = lines
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
        description=f"Reversering av private uttak {original.transaction_id}",
    )
    reversal.reverses_transaction_pk = original.id
    reversal.created_by_user_id = created_by_user_id
    posting.post(reversal)
    db.add(reversal)
    await db.flush()
    return reversal


async def post_uttak(
    db: AsyncSession,
    *,
    company: Company,
    year: int,
    lines: Sequence[UttakLineInput],
    occurred_on: date | None = None,
    correct: bool = False,
    created_by_user_id: int | None = None,
) -> FarmWithdrawal:
    """Store the list and post its voucher; with ``correct`` replace the standing one."""
    checked = await review(db, company=company, year=year, lines=lines, occurred_on=occurred_on)
    problems = blocking(checked.findings)
    if problems:
        raise InvalidUttakError("uttakene kan ikke føres slik de er. Se funnene", problems)
    standing = await _standing_row(db, company, year)
    if standing is not None and not correct:
        raise UttakExistsError(
            f"det er allerede ført private uttak for {year}. Retting gjøres ved å føre en ny "
            "liste som erstatter den, som et eget valg"
        )
    if standing is None and correct:
        raise NoUttakError(f"det finnes ingen liste over private uttak for {year} å rette")
    if standing is not None and standing.transaction_pk is not None:
        await _reverse(
            db,
            company=company,
            voucher_pk=standing.transaction_pk,
            created_by_user_id=created_by_user_id,
        )
    transaction = await _post_voucher(
        db,
        company=company,
        year=year,
        checked=checked,
        corrects_pk=standing.transaction_pk if standing is not None else None,
        created_by_user_id=created_by_user_id,
    )
    assert checked.occurred_on is not None  # noqa: S101 - checked by the review
    stored = FarmWithdrawal(
        company_id=company.id,
        financial_year=year,
        occurred_on=checked.occurred_on,
        transaction_pk=transaction.id,
        corrects_id=standing.id if standing is not None else None,
        created_by_user_id=created_by_user_id,
    )
    stored.lines = [
        FarmWithdrawalLine(
            position=position,
            product=line.product,
            description=(line.description or line.label)[:255],
            quantity=line.quantity,
            unit=line.unit,
            unit_value=line.unit_value,
            value=line.value,
            tax_code=line.tax_code,
            vat_rate=line.vat_rate,
            vat=line.vat,
            account_number=line.account_number,
        )
        for position, line in enumerate(checked.lines, start=1)
    ]
    db.add(stored)
    await db.flush()
    return stored


async def withdraw_uttak(
    db: AsyncSession, *, company: Company, year: int, created_by_user_id: int | None = None
) -> FarmWithdrawal:
    """Withdraw the standing list: reverse its voucher, store an empty list in its place."""
    _require_landbruk(company)
    fiscal = await _financial_year(db, company, year)
    refusal = year_refusal(fiscal, year)
    if refusal is not None or fiscal is None:
        raise FarmYearNotOpenError(refusal or f"regnskapsåret {year} finnes ikke")
    standing = await _standing_row(db, company, year)
    if standing is None:
        raise NoUttakError(f"det finnes ingen liste over private uttak for {year} å trekke tilbake")
    if standing.transaction_pk is not None:
        await _reverse(
            db,
            company=company,
            voucher_pk=standing.transaction_pk,
            created_by_user_id=created_by_user_id,
        )
    stored = FarmWithdrawal(
        company_id=company.id,
        financial_year=year,
        occurred_on=standing.occurred_on,
        transaction_pk=None,
        corrects_id=standing.id,
        created_by_user_id=created_by_user_id,
    )
    stored.lines = []
    db.add(stored)
    await db.flush()
    return stored
