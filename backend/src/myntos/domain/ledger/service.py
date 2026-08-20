"""Company setup and the cash-flow register.

Everything that reads or writes the accounting tables lives here; the API layer
maps HTTP onto these functions and their errors. Like the identity service,
nothing here commits — the request owns the transaction, so "create the
company, seed its kontoplan, open its first financial year" cannot half-happen.

The register is deliberately thin over the double-entry core rather than beside
it. A payment recorded here is an ordinary posted voucher: two or three lines,
balanced, immutable, carrying its VAT code on the basis line. It shows up in
the SAF-T export and the MVA-melding with no special-casing, and a correction
is a reversal like any other. The only thing the register adds is the
*question* — what was this payment for — and the mapping from that answer to an
account and a VAT code (:mod:`.cashflow`).
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from myntos.domain.identity.models import Organization, OrganizationMembership
from myntos.domain.ledger import cashflow, parties, posting
from myntos.domain.ledger.bankkonto import validate_bank_account
from myntos.domain.ledger.cashflow import CashFlowDirection
from myntos.domain.ledger.chart import (
    BANK_ACCOUNT,
    CASH_ACCOUNT,
    CASH_JOURNAL_DESCRIPTION,
    CASH_JOURNAL_ID,
    CASH_JOURNAL_TYPE,
    INPUT_VAT_ACCOUNT,
    OUTPUT_VAT_ACCOUNT,
    STANDARD_ACCOUNTS,
    STANDARD_TAX_CODES,
)
from myntos.domain.ledger.errors import (
    AlreadyReversedError,
    ClosedFinancialYearError,
    CompanyNotFoundError,
    DuplicateCompanyError,
    InvalidAmountError,
    PeriodOutsideBooksError,
    UnknownTaxCodeError,
    VoucherNotFoundError,
)
from myntos.domain.ledger.models import (
    Account,
    AccountGrouping,
    Company,
    FinancialYear,
    Journal,
    TaxCode,
    TaxRate,
    Transaction,
    TransactionLine,
)
from myntos.domain.ledger.orgnr import validate_orgnr

# The two accounts a payment can move through, and the two VAT accounts it can
# touch. Used to read a voucher back as a cash-flow entry.
PAYMENT_ACCOUNTS = frozenset({BANK_ACCOUNT, CASH_ACCOUNT})
VAT_ACCOUNTS = frozenset({OUTPUT_VAT_ACCOUNT, INPUT_VAT_ACCOUNT})

ZERO = Decimal("0.00")


class PaymentMethod(enum.StrEnum):
    """Where the money moved through."""

    BANK = "bank"
    KONTANT = "kontant"

    @property
    def account_number(self) -> str:
        return BANK_ACCOUNT if self is PaymentMethod.BANK else CASH_ACCOUNT


# --- Companies ------------------------------------------------------------------


async def create_company(
    db: AsyncSession,
    *,
    organization: Organization,
    orgnr: str,
    name: str,
    first_financial_year: int,
    address_line: str | None = None,
    postal_code: str | None = None,
    city: str | None = None,
    vat_registered: bool = False,
    in_foretaksregisteret: bool = False,
    organisation_form: str | None = None,
    bank_account: str | None = None,
) -> Company:
    """Create a company under ``organization`` and make it ready to book into.

    Seeding the kontoplan, the VAT codes and the first financial year here
    rather than on first use is what lets a new user record a payment as their
    first action. An empty company would otherwise be a dead end: the register
    needs an account to post to, and asking someone to build a chart of
    accounts before their first receipt is how bookkeeping software loses them.

    The registration flags default to False rather than being guessed from the
    orgnr. They decide what a salgsdokument is allowed to claim — whether "MVA"
    follows the orgnr, and whether output VAT may be charged at all — and the
    safe default for both is "not registered": an unregistered business that
    invoices VAT by accident still owes it to Skatteetaten. The signup flow fills
    them in from Enhetsregisteret, which is evidence rather than a guess.
    """
    normalised = validate_orgnr(orgnr)
    company = Company(
        organization_id=organization.id,
        orgnr=normalised,
        name=name.strip(),
        address_line=address_line,
        postal_code=postal_code,
        city=city,
        vat_registered=vat_registered,
        in_foretaksregisteret=in_foretaksregisteret,
        organisation_form=organisation_form,
        bank_account=None if not bank_account else validate_bank_account(bank_account),
    )
    db.add(company)
    try:
        await db.flush()
    except IntegrityError as error:
        raise DuplicateCompanyError(
            f"organisasjonen fører allerede regnskap for {normalised}"
        ) from error

    await _seed_chart_of_accounts(db, company, first_financial_year)
    await _seed_tax_codes(db, company, first_financial_year)
    db.add(
        Journal(
            company_id=company.id,
            journal_id=CASH_JOURNAL_ID,
            description=CASH_JOURNAL_DESCRIPTION,
            journal_type=CASH_JOURNAL_TYPE,
        )
    )
    await _open_financial_year(db, company, first_financial_year)
    await db.flush()
    return company


async def _seed_chart_of_accounts(db: AsyncSession, company: Company, year: int) -> None:
    for seed in STANDARD_ACCOUNTS:
        db.add(
            Account(
                company_id=company.id,
                account_number=seed.number,
                description=seed.description,
                account_type=seed.account_type,
                created_date=date(year, 1, 1),
            )
        )
    await db.flush()


async def _seed_tax_codes(db: AsyncSession, company: Company, year: int) -> None:
    """Create the VAT codes with a rate valid from the first financial year.

    ``valid_from`` is the year start rather than some early sentinel date
    because these are *today's* rates. Claiming they applied in 2019 would be a
    fabrication the ledger then snapshots onto every historical posting — hence
    also why :func:`ensure_financial_year` refuses dates before this year.
    """
    for seed in STANDARD_TAX_CODES:
        tax_code = TaxCode(
            company_id=company.id,
            code=seed.code,
            description=seed.description,
            standard_tax_code=seed.standard_tax_code,
            is_reportable=seed.is_reportable,
        )
        db.add(tax_code)
        await db.flush()
        db.add(TaxRate(tax_code_id=tax_code.id, valid_from=date(year, 1, 1), rate=seed.rate))
    await db.flush()


async def update_company_profile(
    db: AsyncSession,
    *,
    company: Company,
    name: str | None = None,
    address_line: str | None = None,
    postal_code: str | None = None,
    city: str | None = None,
    vat_registered: bool | None = None,
    in_foretaksregisteret: bool | None = None,
    organisation_form: str | None = None,
    bank_account: str | None = None,
) -> Company:
    """Correct the company's own details.

    The orgnr is not editable. It keys every filing this system will ever
    produce, and a set of books whose orgnr changed would silently claim that
    past periods belonged to a different entity; a new orgnr is a new company.

    Everything else here can legitimately change — a business moves, opens a bank
    account, or crosses the VAT registration threshold. Note that the flags only
    affect documents issued **after** the change: an issued salgsdokument carries
    its own snapshot of all of this, on purpose (see
    :mod:`myntos.domain.faktura.models`).
    """
    if name is not None:
        company.name = name.strip()
    if address_line is not None:
        company.address_line = address_line or None
    if postal_code is not None:
        company.postal_code = postal_code or None
    if city is not None:
        company.city = city or None
    if vat_registered is not None:
        company.vat_registered = vat_registered
    if in_foretaksregisteret is not None:
        company.in_foretaksregisteret = in_foretaksregisteret
    if organisation_form is not None:
        company.organisation_form = organisation_form or None
    if bank_account is not None:
        # Stored without the separators people type; the renderer puts them back.
        # The empty string is how the field is cleared — anything else has to be
        # a real account number, because this one ends up on invoices that cannot
        # be corrected once issued.
        company.bank_account = validate_bank_account(bank_account) if bank_account else None
    await db.flush()
    return company


async def list_companies(db: AsyncSession, *, user_id: int) -> list[Company]:
    """Every company the user can reach through any of their organizations."""
    result = await db.scalars(
        select(Company)
        .join(
            OrganizationMembership,
            OrganizationMembership.organization_id == Company.organization_id,
        )
        .where(OrganizationMembership.user_id == user_id)
        .order_by(Company.name)
    )
    return list(result)


async def get_company_for_user(
    db: AsyncSession, *, company_id: int, user_id: int
) -> tuple[Company, OrganizationMembership]:
    """The company and the caller's membership in the organization that owns it.

    A company belonging to someone else's organization raises
    :class:`CompanyNotFoundError`, the same error as one that does not exist —
    the API must not become a way to discover which ids are in use.
    """
    row = (
        await db.execute(
            select(Company, OrganizationMembership)
            .join(
                OrganizationMembership,
                OrganizationMembership.organization_id == Company.organization_id,
            )
            .where(Company.id == company_id, OrganizationMembership.user_id == user_id)
            .options(selectinload(OrganizationMembership.organization))
        )
    ).first()
    if row is None:
        raise CompanyNotFoundError("finner ikke selskapet")
    company, membership = row
    return company, membership


# --- Financial years ------------------------------------------------------------


async def _open_financial_year(db: AsyncSession, company: Company, year: int) -> FinancialYear:
    """Create a financial year and this year's grouping for every account.

    The grouping rows are the reason this is not a one-liner: the
    næringsspesifikasjon mapping is *per financial year* with history retained,
    so a year opened without them would export without a mapping and fail
    SAF-T validation at the worst possible moment.
    """
    financial_year = FinancialYear(
        company_id=company.id,
        year=year,
        start_date=date(year, 1, 1),
        end_date=date(year, 12, 31),
    )
    db.add(financial_year)

    accounts = await db.scalars(select(Account).where(Account.company_id == company.id))
    for account in accounts:
        seed = next(s for s in STANDARD_ACCOUNTS if s.number == account.account_number)
        db.add(
            AccountGrouping(
                account_id=account.id,
                financial_year=year,
                grouping_category=seed.grouping_category,
                grouping_code=seed.grouping_code,
            )
        )
    await db.flush()
    return financial_year


async def ensure_financial_year(db: AsyncSession, company: Company, year: int) -> FinancialYear:
    """The company's financial year, opening it on demand.

    Refuses a year before the company's first one: the seeded VAT rates are
    only claimed to be valid from then, and posting into a period whose rates
    we never knew would snapshot an invented rate onto the entry.
    """
    existing = await db.scalar(
        select(FinancialYear).where(
            FinancialYear.company_id == company.id, FinancialYear.year == year
        )
    )
    if existing is not None:
        if existing.closed_at is not None:
            raise ClosedFinancialYearError(f"regnskapsåret {year} er avsluttet")
        return existing

    earliest = await db.scalar(
        select(func.min(FinancialYear.year)).where(FinancialYear.company_id == company.id)
    )
    if earliest is not None and year < int(earliest):
        raise PeriodOutsideBooksError(
            f"selskapets første regnskapsår i Myntos er {int(earliest)}; "
            f"det går ikke an å føre på {year}"
        )
    return await _open_financial_year(db, company, year)


# --- Recording a payment --------------------------------------------------------


async def _cash_journal(db: AsyncSession, company: Company) -> Journal:
    journal = await db.scalar(
        select(Journal).where(
            Journal.company_id == company.id, Journal.journal_id == CASH_JOURNAL_ID
        )
    )
    if journal is None:
        # A company created before the register existed, or one built by hand.
        journal = Journal(
            company_id=company.id,
            journal_id=CASH_JOURNAL_ID,
            description=CASH_JOURNAL_DESCRIPTION,
            journal_type=CASH_JOURNAL_TYPE,
        )
        db.add(journal)
        await db.flush()
    return journal


async def _accounts_by_number(db: AsyncSession, company: Company) -> dict[str, Account]:
    accounts = await db.scalars(select(Account).where(Account.company_id == company.id))
    return {account.account_number: account for account in accounts}


async def _resolve_tax_code(db: AsyncSession, company: Company, code: str) -> TaxCode:
    tax_code = await db.scalar(
        select(TaxCode)
        .where(TaxCode.company_id == company.id, TaxCode.code == code)
        .options(selectinload(TaxCode.rates))
    )
    if tax_code is None:
        raise UnknownTaxCodeError(f"mva-koden {code!r} finnes ikke for dette selskapet")
    return tax_code


async def _next_voucher_number(db: AsyncSession, journal_id: int, year: int) -> str:
    """``2026-0001`` and upwards, per journal and year.

    Bilagsnummer must be sequential, so this counts from the highest one in
    use. The zero padding is what makes the plain string ordering agree with
    the numeric one — both here and in every listing an accountant sorts.
    """
    highest = await db.scalar(
        select(func.max(Transaction.transaction_id)).where(
            Transaction.journal_id == journal_id, Transaction.financial_year == year
        )
    )
    number = 1
    if highest is not None:
        _, _, suffix = str(highest).partition("-")
        if suffix.isdigit():
            number = int(suffix) + 1
    return f"{year}-{number:04d}"


def _check_gross(amount: Decimal) -> Decimal:
    if not isinstance(amount, Decimal):  # pragma: no cover - pydantic coerces first
        raise InvalidAmountError("beløpet må være et desimaltall")
    exponent = amount.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -2:
        raise InvalidAmountError("beløpet kan ikke ha brøkdeler av et øre")
    if amount <= ZERO:
        raise InvalidAmountError("beløpet må være større enn null")
    return amount


async def record_cash_flow(
    db: AsyncSession,
    *,
    company: Company,
    type_code: str,
    occurred_on: date,
    gross_amount: Decimal,
    description: str,
    tax_code: str | None = None,
    payment_method: PaymentMethod = PaymentMethod.BANK,
    party_pk: int | None = None,
) -> Transaction:
    """Record one payment in or out as a posted voucher.

    ``gross_amount`` is what actually moved — VAT included — because that is
    the number on the bank statement and the only one the user can read off
    without doing arithmetic. The split into net and VAT is derived, and
    derived in the one way that keeps the voucher balanced
    (:func:`~myntos.domain.ledger.posting.split_gross`).

    ``party_pk`` names the customer or supplier, and is optional: plenty of
    payments have no counterparty worth registering, and demanding one would
    make people invent placeholder suppliers — which is worse than the field
    being empty.

    The entry is posted immediately rather than left as a draft. The money has
    moved; there is nothing provisional about it. That makes it immutable, so
    :func:`reverse_cash_flow` is how a mistake is undone.
    """
    entry_type = cashflow.get_type(type_code)
    gross = _check_gross(gross_amount)
    financial_year = await ensure_financial_year(db, company, occurred_on.year)
    journal = await _cash_journal(db, company)
    accounts = await _accounts_by_number(db, company)
    # Resolved through the company, so a party id belonging to another tenant
    # is not found rather than quietly attached.
    party = (
        None
        if party_pk is None
        else await parties.get_party(db, company=company, party_pk=party_pk)
    )

    code = tax_code if tax_code is not None else entry_type.default_tax_code
    tax = await _resolve_tax_code(db, company, code)
    rate = posting.rate_on(tax, occurred_on)
    net, vat = posting.split_gross(gross, rate)
    if net == ZERO:
        # Only reachable with an absurd rate, but a zero-amount line does not
        # post at all — booking the whole amount as basis is the safe reading.
        net, vat = gross, ZERO

    is_incoming = entry_type.direction is CashFlowDirection.INN
    payment_line = TransactionLine(
        record_id="1",
        account=accounts[payment_method.account_number],
        description=description,
        debit=gross if is_incoming else ZERO,
        credit=ZERO if is_incoming else gross,
    )
    basis_line = TransactionLine(
        record_id="2",
        account=accounts[entry_type.account_number],
        # On the basis line, like the tax information: SAF-T puts
        # CustomerID/SupplierID on the line describing the trade, not on the
        # one describing the money movement.
        party=party,
        description=description,
        debit=ZERO if is_incoming else net,
        credit=net if is_incoming else ZERO,
    )
    # On the basis line, never on the VAT line — see posting.set_tax_information.
    posting.set_tax_information(basis_line, tax_code=tax, rate=rate, basis=net, amount=vat)
    lines = [payment_line, basis_line]

    if vat > ZERO:
        vat_account = OUTPUT_VAT_ACCOUNT if is_incoming else INPUT_VAT_ACCOUNT
        lines.append(
            TransactionLine(
                record_id="3",
                account=accounts[vat_account],
                description=f"Mva {code}",
                debit=ZERO if is_incoming else vat,
                credit=vat if is_incoming else ZERO,
            )
        )

    transaction = Transaction(
        journal_id=journal.id,
        transaction_id=await _next_voucher_number(db, journal.id, financial_year.year),
        financial_year=financial_year.year,
        period=occurred_on.month,
        transaction_date=occurred_on,
        gl_posting_date=occurred_on,
        description=description.strip(),
        # SAF-T TransactionType. Storing the catalogue code here is what lets a
        # voucher be read back as the category the user chose, without a
        # parallel table that could disagree with the postings.
        voucher_type=entry_type.code,
    )
    transaction.lines = lines
    posting.post(transaction)
    db.add(transaction)
    await db.flush()
    return transaction


async def get_voucher(db: AsyncSession, *, company: Company, voucher_pk: int) -> Transaction:
    """One voucher belonging to ``company``, with its lines and accounts loaded."""
    transaction = await db.scalar(
        _voucher_query().where(Transaction.id == voucher_pk, Journal.company_id == company.id)
    )
    if transaction is None:
        raise VoucherNotFoundError("finner ikke bilaget")
    return transaction


def _voucher_query() -> Select[tuple[Transaction]]:
    return (
        select(Transaction)
        .join(Journal, Journal.id == Transaction.journal_id)
        .options(
            selectinload(Transaction.lines).selectinload(TransactionLine.account),
            selectinload(Transaction.lines).selectinload(TransactionLine.party),
            selectinload(Transaction.lines).selectinload(TransactionLine.tax_code),
            selectinload(Transaction.lines).selectinload(TransactionLine.analysis),
        )
    )


async def reverse_cash_flow(db: AsyncSession, *, company: Company, voucher_pk: int) -> Transaction:
    """Reverse a posted entry.

    The reversal lands in the **original's** period, so the year's figures net
    to zero rather than leaving a hole in one month and a bump in another. If
    that year is already closed the correction cannot go there, and the caller
    is told so instead of having it silently moved.
    """
    original = await get_voucher(db, company=company, voucher_pk=voucher_pk)
    already = await db.scalar(
        select(Transaction.id).where(Transaction.reverses_transaction_pk == original.id)
    )
    if already is not None:
        raise AlreadyReversedError("bilaget er allerede reversert")

    financial_year = await ensure_financial_year(db, company, original.financial_year)
    reversal = posting.build_reversal(
        original,
        transaction_id=await _next_voucher_number(db, original.journal_id, financial_year.year),
        gl_posting_date=original.gl_posting_date,
        period=original.period,
        financial_year=financial_year.year,
    )
    reversal.reverses_transaction_pk = original.id
    posting.post(reversal)
    db.add(reversal)
    await db.flush()
    return reversal


# --- Reading the register back --------------------------------------------------


@dataclass(frozen=True)
class CashFlowView:
    """A posted voucher read back as the cash-flow entry it was recorded as."""

    id: int
    voucher_no: str
    occurred_on: date
    direction: CashFlowDirection
    type_code: str | None
    gross_amount: Decimal
    net_amount: Decimal
    vat_amount: Decimal
    account_number: str
    account_description: str
    tax_code: str | None
    party_id: int | None
    party_name: str | None
    payment_account: str
    description: str
    is_reversal: bool
    reversed_by_id: int | None


def describe(transaction: Transaction, *, reversed_by_id: int | None = None) -> CashFlowView | None:
    """Project a voucher onto the cash-flow shape, or ``None`` if it has none.

    Only vouchers with a payment-account line can be shown as a payment. Every
    entry the register creates has one; a voucher posted by some future feature
    into the same journal might not, and dropping it from this listing is
    better than misreporting its amount as a cash movement.
    """
    payment = next(
        (line for line in transaction.lines if line.account.account_number in PAYMENT_ACCOUNTS),
        None,
    )
    if payment is None:
        return None
    basis = next(
        (
            line
            for line in transaction.lines
            if line.account.account_number not in PAYMENT_ACCOUNTS
            and line.account.account_number not in VAT_ACCOUNTS
        ),
        None,
    )
    if basis is None:
        return None

    debit = payment.debit or ZERO
    credit = payment.credit or ZERO
    gross = debit if debit > ZERO else credit
    net = (basis.debit or ZERO) if (basis.debit or ZERO) > ZERO else (basis.credit or ZERO)

    return CashFlowView(
        id=transaction.id,
        voucher_no=transaction.transaction_id,
        occurred_on=transaction.transaction_date,
        direction=CashFlowDirection.INN if debit > ZERO else CashFlowDirection.UT,
        type_code=transaction.voucher_type,
        gross_amount=gross,
        net_amount=net,
        vat_amount=gross - net,
        account_number=basis.account.account_number,
        account_description=basis.account.description,
        tax_code=basis.tax_code.code if basis.tax_code is not None else None,
        party_id=basis.party.id if basis.party is not None else None,
        party_name=basis.party.name if basis.party is not None else None,
        payment_account=payment.account.account_number,
        description=transaction.description,
        is_reversal=transaction.reverses_transaction_pk is not None,
        reversed_by_id=reversed_by_id,
    )


async def list_cash_flows(
    db: AsyncSession,
    *,
    company: Company,
    financial_year: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[CashFlowView]:
    """The register, newest first."""
    query = _voucher_query().where(Journal.company_id == company.id)
    if financial_year is not None:
        query = query.where(Transaction.financial_year == financial_year)
    transactions = list(
        await db.scalars(
            query.order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    reversals = await _reversal_map(db, [t.id for t in transactions])
    views = [describe(t, reversed_by_id=reversals.get(t.id)) for t in transactions]
    return [view for view in views if view is not None]


async def _reversal_map(db: AsyncSession, voucher_pks: Sequence[int]) -> dict[int, int]:
    """``{original id: reversal id}`` for the given vouchers."""
    if not voucher_pks:
        return {}
    rows = await db.execute(
        select(Transaction.reverses_transaction_pk, Transaction.id).where(
            Transaction.reverses_transaction_pk.in_(voucher_pks)
        )
    )
    return {original: reversal for original, reversal in rows if original is not None}


# --- Summary --------------------------------------------------------------------


@dataclass(frozen=True)
class TypeTotal:
    type_code: str | None
    direction: CashFlowDirection
    total: Decimal
    count: int


@dataclass(frozen=True)
class CashFlowSummary:
    """What the overview shows: the year's money in, out and owed."""

    financial_year: int
    total_in: Decimal
    total_out: Decimal
    net: Decimal
    entry_count: int
    output_vat: Decimal
    input_vat: Decimal
    vat_position: Decimal
    cash_position: Decimal
    by_type: list[TypeTotal]


def _movement_query(
    company_id: int, account_numbers: frozenset[str]
) -> Select[tuple[str | None, Decimal, Decimal, int]]:
    """Debit and credit totals on ``account_numbers``, grouped by voucher type.

    SQLAlchemy types ``SUM`` as non-optional here. It is not — an empty group
    cannot occur, but the rows are still read defensively below, because the
    difference between ``NULL`` and ``0.00`` in a total is the difference
    between "nothing happened" and a crash.
    """
    return (
        select(
            Transaction.voucher_type,
            func.sum(TransactionLine.debit),
            func.sum(TransactionLine.credit),
            func.count(),
        )
        .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
        .join(Journal, Journal.id == Transaction.journal_id)
        .join(Account, Account.id == TransactionLine.account_id)
        .where(Journal.company_id == company_id, Account.account_number.in_(account_numbers))
        .group_by(Transaction.voucher_type)
    )


async def summarise(db: AsyncSession, *, company: Company, financial_year: int) -> CashFlowSummary:
    """Aggregate one financial year of the register.

    Summed in SQL over the payment and VAT accounts rather than over the
    ``CashFlowView`` projection: the totals must stay right when the register
    holds more entries than a request should ever load.
    """
    payments = list(
        await db.execute(
            _movement_query(company.id, PAYMENT_ACCOUNTS).where(
                Transaction.financial_year == financial_year
            )
        )
    )
    total_in = ZERO
    total_out = ZERO
    entry_count = 0
    by_type: list[TypeTotal] = []
    for voucher_type, debit, credit, count in payments:
        incoming = debit or ZERO
        outgoing = credit or ZERO
        total_in += incoming
        total_out += outgoing
        entry_count += count
        # A reversal makes the same type appear on both sides; report the net
        # movement on the side it actually falls, so the totals still add up.
        movement = incoming - outgoing
        by_type.append(
            TypeTotal(
                type_code=voucher_type,
                direction=CashFlowDirection.INN if movement >= ZERO else CashFlowDirection.UT,
                total=abs(movement),
                count=count,
            )
        )

    vat_rows = list(
        await db.execute(
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
                Transaction.financial_year == financial_year,
                Account.account_number.in_(VAT_ACCOUNTS),
            )
            .group_by(Account.account_number)
        )
    )
    output_vat = ZERO
    input_vat = ZERO
    for account_number, debit, credit in vat_rows:
        if account_number == OUTPUT_VAT_ACCOUNT:
            output_vat = (credit or ZERO) - (debit or ZERO)
        else:
            input_vat = (debit or ZERO) - (credit or ZERO)

    # Across every year, not just this one: the bank balance is a position, not
    # a movement, and resetting it each January would be nonsense.
    cash_rows = list(
        await db.execute(
            select(func.sum(TransactionLine.debit), func.sum(TransactionLine.credit))
            .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
            .join(Journal, Journal.id == Transaction.journal_id)
            .join(Account, Account.id == TransactionLine.account_id)
            .where(
                Journal.company_id == company.id,
                Account.account_number.in_(PAYMENT_ACCOUNTS),
            )
        )
    )
    cash_debit, cash_credit = cash_rows[0] if cash_rows else (None, None)

    return CashFlowSummary(
        financial_year=financial_year,
        total_in=total_in,
        total_out=total_out,
        net=total_in - total_out,
        entry_count=entry_count,
        output_vat=output_vat,
        input_vat=input_vat,
        vat_position=output_vat - input_vat,
        cash_position=(cash_debit or ZERO) - (cash_credit or ZERO),
        by_type=sorted(by_type, key=lambda t: t.total, reverse=True),
    )
