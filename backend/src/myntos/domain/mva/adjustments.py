"""Uttak, tap på krav, justering and tilbakeføring: VAT with a spesifikasjon.

Four events that change what a company owes in VAT without money moving through
the bank, and which the MVA-melding reports on lines of their own, marked with
a ``spesifikasjon``. Until this module the ledger had nowhere to record them, so
a business that wrote off a customer's invoice either left the VAT it had
already paid on that sale unclaimed, or booked it as an ordinary deduction and
told Skatteetaten something different from what happened.

Each is an ordinary posted voucher in a journal of its own (``MVAJ``), and the
line carrying the tax information also carries ``vat_specification``. That is
all the melding needs: :func:`myntos.domain.mva.melding.aggregate` groups on it.
Reversal is the ledger's ordinary reversal, which copies the specification, so
an undone tap på krav cancels inside its own melding line.

The shapes, each matching Skatteetaten's ``eksempelMedAlleTilfeller.xml``:

**Uttak** (merverdiavgiftsloven kapittel 3, «uttak»). Goods or services taken out
of the business, for the owner or for a purpose outside the VAT area, are taxed
as though sold at their ordinary value. Entered as that value (the basis)::

    2060 Privatkonto        D  1 250    ← or another account the person names
    3000 Salgsinntekt       K  1 000    ← kode 3, spesifikasjon uttak
    2700 Utgående mva       K    250

Reported as a sale: grunnlag, sats, positive merverdiavgift. Credited to the
sales account because the næringsspesifikasjon's post is «Salg og uttak med
mva-plikt»; uttak is turnover there, not a reduction of cost.

**Tap på krav** (§ 4-7). The VAT charged on a sale the customer never paid may
be taken back once the loss is final. Entered as the claim written off, VAT
included, since that is the figure on the customer's account::

    7830 Tap på fordringer  D  1 000    ← kode 1, spesifikasjon tapPåKrav
    2710 Inngående mva      D    250
    1500 Kundefordringer    K  1 250    ← the customer, when named

Reported as a deduction: merverdiavgift alone, negative. Code 1, 11, 12 or 13
by the rate the sale carried. On 2710 because the melding reports it among the
deductions, and the period's settlement then moves it like any other.

**Justering** (kapittel 9). The yearly adjustment of the deduction on a capital
good whose use has changed. Entered as the VAT amount, signed as the melding
signs it: positive is owed back, negative is a further deduction.

**Tilbakeføring** (also kapittel 9). A passenger car or a building whose deduction must
be repaid in full. Entered as the VAT amount, always owed.

For both of the last two the amount *is* the tax, as on a tollpass (codes 14
and 15), and there is no basis in the voucher. The tax information sits on the
other line, the account the effect lands on, with its tax side set by the
direction of the VAT rather than of the line: a tilbakeføring debits a cost and
credits 2710, and the VAT on it is owed, which is SAF-T's ``CreditTaxAmount``.
That is why :func:`~myntos.domain.ledger.posting.set_tax_information`, which
puts the tax on the line's own side, is not used for them. The basis written is
the amount divided by the rate, the same derivation the tollpass uses, because
SAF-T wants one and the melding reports none.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.ledger import kontoplan, parties, posting
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.chart import (
    INPUT_VAT_ACCOUNT,
    OUTPUT_VAT_ACCOUNT,
    RECEIVABLES_ACCOUNT,
)
from myntos.domain.ledger.errors import InactiveAccountError
from myntos.domain.ledger.models import (
    Account,
    AccountType,
    Company,
    Journal,
    Transaction,
    TransactionLine,
)
from myntos.domain.ledger.vatstatus import registered_on
from myntos.domain.mva.codes import SPESIFIKASJON_CODES, Spesifikasjon
from myntos.domain.mva.errors import (
    AdjustmentNotFoundError,
    InvalidAdjustmentError,
    NotVatRegisteredError,
)

ADJUSTMENT_JOURNAL_ID = "MVAJ"
ADJUSTMENT_JOURNAL_DESCRIPTION = "Uttak, tap på krav og justering av mva"
ADJUSTMENT_JOURNAL_TYPE = "mva"

#: The sales account an uttak is credited to, by code. The same accounts the
#: cash-flow catalogue sells on at each rate.
UTTAK_INCOME_ACCOUNTS: dict[str, str] = {"3": "3000", "31": "3010", "33": "3020"}

#: Where the other side goes unless the person names an account.
DEFAULT_ACCOUNTS: dict[Spesifikasjon, str] = {
    # The owner's private use is the ordinary uttak; a gift to a customer is
    # the other one, and the person names that cost account themselves.
    Spesifikasjon.UTTAK: "2060",
    Spesifikasjon.TAP_PAA_KRAV: "7830",
    # A cost when owed back, a reduction of cost when deducted. Neither has an
    # account of its own in NS 4102, and 7790 is the one the starter chart has.
    Spesifikasjon.JUSTERING: "7790",
    Spesifikasjon.TILBAKEFOERING: "7790",
}

ZERO = Decimal("0.00")


def voucher_type(spesifikasjon: Spesifikasjon) -> str:
    return f"mva_{spesifikasjon.value}"


async def _journal(db: AsyncSession, company: Company) -> Journal:
    journal = await db.scalar(
        select(Journal).where(
            Journal.company_id == company.id, Journal.journal_id == ADJUSTMENT_JOURNAL_ID
        )
    )
    if journal is None:
        journal = Journal(
            company_id=company.id,
            journal_id=ADJUSTMENT_JOURNAL_ID,
            description=ADJUSTMENT_JOURNAL_DESCRIPTION,
            journal_type=ADJUSTMENT_JOURNAL_TYPE,
        )
        db.add(journal)
        await db.flush()
    return journal


def _check_amount(amount: Decimal, *, signed: bool) -> Decimal:
    exponent = amount.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -2:
        raise InvalidAdjustmentError("beløpet kan ikke ha brøkdeler av et øre")
    if amount == ZERO or (amount < ZERO and not signed):
        raise InvalidAdjustmentError("beløpet må være større enn null")
    return amount


def _other_account(
    accounts: dict[str, Account], spesifikasjon: Spesifikasjon, chosen: str | None
) -> Account:
    number = chosen or DEFAULT_ACCOUNTS[spesifikasjon]
    account = accounts.get(number)
    if account is None:
        raise InvalidAdjustmentError(f"kontoplanen har ingen konto {number}")
    if number in {INPUT_VAT_ACCOUNT, OUTPUT_VAT_ACCOUNT, RECEIVABLES_ACCOUNT}:
        # Each of these is already a line of its own in the voucher; naming one
        # as the other side would post a VAT amount against itself.
        raise InvalidAdjustmentError(f"konto {number} kan ikke være motkonto her")
    if spesifikasjon is Spesifikasjon.TAP_PAA_KRAV and account.account_type is not (
        AccountType.KOSTNAD
    ):
        raise InvalidAdjustmentError("tap på krav føres på en kostnadskonto, som 7830")
    if not account.is_active:
        raise InactiveAccountError(
            f"konto {number} er deaktivert. Aktiver den i kontoplanen, eller velg en annen"
        )
    return account


def _line(
    record_id: str, account: Account, amount: Decimal, *, debit: bool, text: str
) -> TransactionLine:
    return TransactionLine(
        record_id=record_id,
        account=account,
        description=text,
        debit=amount if debit else ZERO,
        credit=ZERO if debit else amount,
    )


async def record_adjustment(
    db: AsyncSession,
    *,
    company: Company,
    spesifikasjon: Spesifikasjon,
    occurred_on: date,
    tax_code: str,
    amount: Decimal,
    description: str,
    account_number: str | None = None,
    party_pk: int | None = None,
    created_by_user_id: int | None = None,
) -> Transaction:
    """Post one uttak, tap på krav, justering or tilbakeføring.

    ``amount`` means what the module docstring says per kind: the basis for an
    uttak, the claim written off (VAT included) for a tap på krav, and the VAT
    itself, signed as the melding signs it, for the other two.

    Refused for a company that was not registered on the date: these are
    melding lines and nothing else, and an unregistered company files no
    melding to put them in.
    """
    if spesifikasjon not in SPESIFIKASJON_CODES:
        raise InvalidAdjustmentError(f"Myntos fører ikke «{spesifikasjon.value}» ennå")
    if tax_code not in SPESIFIKASJON_CODES[spesifikasjon]:
        allowed = ", ".join(sorted(SPESIFIKASJON_CODES[spesifikasjon], key=int))
        raise InvalidAdjustmentError(
            f"«{spesifikasjon.value}» rapporteres med mva-kode {allowed}, ikke {tax_code}"
        )
    if not registered_on(company, occurred_on):
        raise NotVatRegisteredError(
            f"selskapet var ikke mva-registrert {occurred_on.isoformat()}, og da finnes det "
            "ingen mva-melding å føre dette i"
        )
    signed = spesifikasjon is Spesifikasjon.JUSTERING
    figure = _check_amount(amount, signed=signed)
    text = description.strip() or spesifikasjon.value

    financial_year = await ledger_service.ensure_financial_year(
        db, company, occurred_on.year, on=occurred_on
    )
    if spesifikasjon is Spesifikasjon.TAP_PAA_KRAV and account_number is None:
        # A company set up before 7830 joined the starter chart gets it now,
        # from the seed, rather than a refusal about an account it never chose
        # to leave out.
        await kontoplan.ensure_seeded_account(db, company=company, number="7830")
    accounts = await ledger_service._accounts_by_number(db, company)
    other = _other_account(accounts, spesifikasjon, account_number)
    tax = await ledger_service._resolve_tax_code(db, company, tax_code)
    rate = posting.rate_on(tax, occurred_on)
    party = (
        None
        if party_pk is None
        else await parties.get_party(db, company=company, party_pk=party_pk)
    )

    lines: list[TransactionLine] = []
    if spesifikasjon is Spesifikasjon.UTTAK:
        vat = posting.compute_tax(figure, rate)
        income = accounts[UTTAK_INCOME_ACCOUNTS[tax_code]]
        basis_line = _line("2", income, figure, debit=False, text=text)
        posting.set_tax_information(basis_line, tax_code=tax, rate=rate, basis=figure, amount=vat)
        basis_line.vat_specification = spesifikasjon.value
        lines = [
            _line("1", other, figure + vat, debit=True, text=text),
            basis_line,
            _line("3", accounts[OUTPUT_VAT_ACCOUNT], vat, debit=False, text=text),
        ]
    elif spesifikasjon is Spesifikasjon.TAP_PAA_KRAV:
        net, vat = posting.split_gross(figure, rate)
        if vat == ZERO:
            raise InvalidAdjustmentError("beløpet er for lite til å inneholde mva")
        basis_line = _line("1", other, net, debit=True, text=text)
        posting.set_tax_information(basis_line, tax_code=tax, rate=rate, basis=net, amount=vat)
        basis_line.vat_specification = spesifikasjon.value
        receivable = _line("3", accounts[RECEIVABLES_ACCOUNT], figure, debit=False, text=text)
        # On the receivable, because that is the customer's balance going down;
        # a written-off claim that left the customer's sub-ledger untouched would
        # still show them owing it in the SAF-T file.
        receivable.party = party
        lines = [
            basis_line,
            _line("2", accounts[INPUT_VAT_ACCOUNT], vat, debit=True, text=text),
            receivable,
        ]
    else:
        owed = figure > ZERO
        vat = abs(figure)
        # Owed back: a cost, and 2710 goes down. Deducted: the reverse.
        effect = _line("1", other, vat, debit=owed, text=text)
        effect.tax_code = tax
        effect.tax_rate = rate
        effect.tax_base = posting.quantize_money(vat / rate) if rate > ZERO else vat
        effect.debit_tax_amount = ZERO if owed else vat
        effect.credit_tax_amount = vat if owed else ZERO
        effect.vat_specification = spesifikasjon.value
        lines = [effect, _line("2", accounts[INPUT_VAT_ACCOUNT], vat, debit=not owed, text=text)]

    journal = await _journal(db, company)
    transaction = Transaction(
        journal_id=journal.id,
        transaction_id=await ledger_service._next_voucher_number(
            db, journal.id, financial_year.year
        ),
        financial_year=financial_year.year,
        period=occurred_on.month,
        transaction_date=occurred_on,
        gl_posting_date=occurred_on,
        description=text,
        voucher_type=voucher_type(spesifikasjon),
        created_by_user_id=created_by_user_id,
    )
    transaction.lines = lines
    posting.post(transaction)
    db.add(transaction)
    await db.flush()
    return transaction


# --- Reading back ---------------------------------------------------------------


@dataclass(frozen=True)
class AdjustmentView:
    id: int
    voucher_no: str
    occurred_on: date
    spesifikasjon: str
    tax_code: str
    # As the melding will report it: positive owed, negative to the company's
    # credit. The grunnlag only for an uttak, which is the one reported with one.
    merverdiavgift: Decimal
    grunnlag: Decimal | None
    account_number: str
    account_description: str
    description: str
    is_reversal: bool
    reversed_by_id: int | None


def describe(transaction: Transaction, reversed_by_id: int | None) -> AdjustmentView | None:
    line = next((ln for ln in transaction.lines if ln.vat_specification is not None), None)
    if line is None or line.tax_code is None:
        return None
    vat = (line.credit_tax_amount or ZERO) - (line.debit_tax_amount or ZERO)
    uttak = line.vat_specification == Spesifikasjon.UTTAK.value
    grunnlag = None
    if uttak:
        base = line.tax_base or ZERO
        grunnlag = -base if (line.debit or ZERO) > ZERO else base
    other = next(
        (
            ln
            for ln in transaction.lines
            if ln is not line
            and ln.account.account_number
            not in {INPUT_VAT_ACCOUNT, OUTPUT_VAT_ACCOUNT, RECEIVABLES_ACCOUNT}
        ),
        line,
    )
    shown = other if uttak else line
    return AdjustmentView(
        id=transaction.id,
        voucher_no=transaction.transaction_id,
        occurred_on=transaction.transaction_date,
        spesifikasjon=line.vat_specification or "",
        tax_code=line.tax_code.code,
        merverdiavgift=vat,
        grunnlag=grunnlag,
        account_number=shown.account.account_number,
        account_description=shown.account.description,
        description=transaction.description,
        is_reversal=transaction.reverses_transaction_pk is not None,
        reversed_by_id=reversed_by_id,
    )


async def list_adjustments(
    db: AsyncSession, *, company: Company, financial_year: int
) -> list[AdjustmentView]:
    transactions = list(
        await db.scalars(
            ledger_service._voucher_query()
            .where(
                Journal.company_id == company.id,
                Journal.journal_id == ADJUSTMENT_JOURNAL_ID,
                Transaction.financial_year == financial_year,
            )
            .order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
        )
    )
    reversals = await ledger_service._reversal_map(db, [t.id for t in transactions])
    views = [describe(t, reversals.get(t.id)) for t in transactions]
    return [view for view in views if view is not None]


async def reverse_adjustment(
    db: AsyncSession, *, company: Company, voucher_pk: int, created_by_user_id: int | None
) -> Transaction:
    """Undo one, by the ledger's ordinary reversal, and only one of these.

    The journal is part of the lookup so this route cannot be used to reverse a
    payment or an invoice, which have their own routes and their own rules.
    """
    found = await db.scalar(
        select(Transaction.id)
        .join(Journal, Journal.id == Transaction.journal_id)
        .where(
            Transaction.id == voucher_pk,
            Journal.company_id == company.id,
            Journal.journal_id == ADJUSTMENT_JOURNAL_ID,
        )
    )
    if found is None:
        raise AdjustmentNotFoundError("finner ikke mva-føringen")
    return await ledger_service.reverse_cash_flow(
        db, company=company, voucher_pk=voucher_pk, created_by_user_id=created_by_user_id
    )
