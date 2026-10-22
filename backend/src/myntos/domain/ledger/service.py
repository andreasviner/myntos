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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from myntos.core.clock import utc_now
from myntos.domain.identity.models import (
    AgentKey,
    CompanyMembership,
    Organization,
    OrganizationRole,
    User,
)
from myntos.domain.ledger import (
    cashflow,
    dimensions,
    kontoplan,
    landbruk,
    parties,
    posting,
    vatstatus,
)
from myntos.domain.ledger.bankkonto import format_bank_account, validate_bank_account
from myntos.domain.ledger.cashflow import CashFlowDirection
from myntos.domain.ledger.chart import (
    BANK_ACCOUNT,
    CASH_ACCOUNT,
    CASH_JOURNAL_DESCRIPTION,
    CASH_JOURNAL_ID,
    CASH_JOURNAL_TYPE,
    CLOSING_JOURNAL_ID,
    COUNT_JOURNAL_ID,
    DEPRECIATION_JOURNAL_ID,
    IMPORT_JOURNAL_ID,
    INPUT_VAT_ACCOUNT,
    LANDBRUK_ACCOUNTS_BY_NUMBER,
    MANUAL_JOURNAL_ID,
    OPENING_JOURNAL_ID,
    OUTPUT_VAT_ACCOUNT,
    SETTLEMENT_JOURNAL_ID,
    SETTLEMENT_VOUCHER_TYPE,
    STANDARD_ACCOUNTS,
    STANDARD_TAX_CODES,
    TAX_CODES_BY_CODE,
    VAT_SETTLEMENT_ACCOUNT,
    WITHDRAWAL_JOURNAL_ID,
)
from myntos.domain.ledger.errors import (
    AccountNotFoundError,
    AlreadyReversedError,
    BankAccountNotFoundError,
    CannotReverseHereError,
    ChartIncompleteError,
    ClosedFinancialYearError,
    CompanyAlreadyClaimedError,
    CompanyNotFoundError,
    CompanyOrgnrLockedError,
    DuplicateBankAccountError,
    DuplicateCompanyError,
    InactiveAccountError,
    InvalidAccountNumberError,
    InvalidAmountError,
    InvalidCompositeEntryError,
    NotAuthorizedForPartyError,
    PeriodOutsideBooksError,
    PrimaryBankAccountRequiredError,
    TaxCodeNotAllowedError,
    UnknownTaxCodeError,
    VoucherNotFoundError,
)
from myntos.domain.ledger.models import (
    Account,
    AccountGrouping,
    Company,
    CompanyBankAccount,
    CompanyRegisterSnapshot,
    DimensionValue,
    FinancialYear,
    Journal,
    Party,
    TaxCode,
    TaxRate,
    Transaction,
    TransactionLine,
    TransactionLineAnalysis,
)
from myntos.domain.ledger.orgnr import validate_orgnr
from myntos.integrations.brreg import RegisteredUnit

# The two accounts the register pays through, and the two VAT accounts a
# payment can touch. Used to read a voucher back as a cash-flow entry
# (:func:`describe`), which only ever meets the register's own vouchers and
# the bank reconciliation's candidates, all paid through 1920 or 1900. An
# account marked as a bank account (a driftskreditt on 2390) is the company's
# money too, and the overview counts it (:func:`summarise`), but no register
# entry is paid through it.
PAYMENT_ACCOUNTS = kontoplan.PAYMENT_ACCOUNTS
VAT_ACCOUNTS = frozenset({OUTPUT_VAT_ACCOUNT, INPUT_VAT_ACCOUNT})
#: What is owed to or by Skatteetaten now: the open terms on 2700 and 2710, and
#: the filed ones waiting on 2740 to be paid.
OWED_VAT_ACCOUNTS = frozenset({OUTPUT_VAT_ACCOUNT, INPUT_VAT_ACCOUNT, VAT_SETTLEMENT_ACCOUNT})

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
    created_by_user_id: int,
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

    **Only the creator gets access**, as its owner. Nobody else on the account
    is added, not even another account owner: a new set of books is private
    until somebody is invited to it, and sharing is a thing you do rather than a
    thing that happens. That is the whole point of access being per company, and
    it is why colleagues are invited to *this* company rather than to the
    account.
    """
    normalised = validate_orgnr(orgnr)
    # Checked here rather than after the insert, so a transposed pair is a 422
    # about the account number and not a company that half exists. The signup
    # form asks for one account; the rest are added afterwards through
    # :func:`add_bank_account`, and this one is the primary because it is the
    # only one.
    account_number = validate_bank_account(bank_account) if bank_account else None
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
        # Through the relationship and before the flush, unlike the kontoplan
        # below, which is added row by row. Setting a collection on an object
        # that is still transient is what marks it *loaded*: the caller reads
        # ``company.bank_accounts`` to build its response, and on a persistent
        # object an unloaded collection is a lazy load — which under asyncio is
        # not a slow query, it is a MissingGreenlet.
        bank_accounts=(
            []
            if account_number is None
            else [CompanyBankAccount(account=account_number, is_primary=True)]
        ),
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
    db.add(
        CompanyMembership(
            company_id=company.id,
            user_id=created_by_user_id,
            role=OrganizationRole.OWNER,
        )
    )
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
    orgnr: str | None = None,
    name: str | None = None,
    address_line: str | None = None,
    postal_code: str | None = None,
    city: str | None = None,
    vat_registered: bool | None = None,
    in_foretaksregisteret: bool | None = None,
    organisation_form: str | None = None,
    ki_enabled: bool | None = None,
    vat_registered_from: date | None = None,
    clear_vat_registered_from: bool = False,
) -> Company:
    """Correct the company's own details.

    The orgnr is editable exactly as long as nobody has confirmed the company.
    It is nine digits somebody typed, and one of them being wrong is both easy
    to do and, once the books are set up, expensive to discover — the whole set
    would have to be built again. Correcting it before anything rests on it is
    the cheap moment.

    A confirmed company is the other case, and :class:`CompanyOrgnrLockedError`
    is what says so. The confirmation is Altinn's word that this person may act
    for *that number*, so moving the number would carry evidence across to an
    entity nobody has shown any right to. A different orgnr is then a different
    company, and the way to it is a new set of books.

    Everything else here can legitimately change — a business moves or crosses
    the VAT registration threshold. Note that the flags only affect documents
    issued **after** the change: an issued salgsdokument carries its own snapshot
    of all of this, on purpose (see :mod:`myntos.domain.faktura.models`).

    Bank accounts are **not** here. They are a collection rather than a field —
    see :class:`~myntos.domain.ledger.models.CompanyBankAccount` — and a PATCH
    that took one number would only ever be able to describe the first of them.
    :func:`add_bank_account` and its neighbours below own that.
    """
    # Held in a local, because the message below is built after a failed flush,
    # and reading it off the instance there would send SQLAlchemy to a
    # connection the IntegrityError has already broken.
    number = company.orgnr
    if orgnr is not None:
        number = validate_orgnr(orgnr)
        # Only a real change is refused. Sending the orgnr back unchanged is what
        # a form that submits every field it shows does, and that must go on
        # working against a confirmed company.
        if number != company.orgnr:
            if company.is_verified:
                raise CompanyOrgnrLockedError(
                    "organisasjonsnummeret kan ikke endres etter at eierskapet er bekreftet "
                    "i Altinn. Et annet nummer er et annet selskap, og trenger sitt eget regnskap"
                )
            company.orgnr = number
    if name is not None:
        company.name = name.strip()
    if address_line is not None:
        company.address_line = address_line or None
    if postal_code is not None:
        company.postal_code = postal_code or None
    if city is not None:
        company.city = city or None
    if vat_registered is not None:
        # Future entries only. Every voucher already posted carries its own
        # tax code and rate and stays exactly as it is; what moves is what the
        # register proposes and accepts from now on (see .vatstatus).
        company.vat_registered = vat_registered
    if clear_vat_registered_from:
        company.vat_registered_from = None
    elif vat_registered_from is not None:
        company.vat_registered_from = vat_registered_from
    if in_foretaksregisteret is not None:
        company.in_foretaksregisteret = in_foretaksregisteret
    if ki_enabled is not None:
        company.ki_enabled = ki_enabled
    if organisation_form is not None:
        company.organisation_form = organisation_form or None
    try:
        await db.flush()
    except IntegrityError as error:
        # The only constraint an edit can reach: this organization already keeps
        # a second set of books under the orgnr just typed. Same answer as
        # creating one, because it is the same collision.
        raise DuplicateCompanyError(
            f"organisasjonen fører allerede regnskap for {number}"
        ) from error
    return company


# --- Bank accounts --------------------------------------------------------------
#
# A company is paid into as many accounts as its bank gave it, and which one an
# invoice names is a per-invoice choice with a per-company default. Everything
# here filters on ``company.id`` for the reason docs/company-isolation.md gives:
# passing the membership check says the caller may open *a* set of books, and
# two companies in the same account both pass it.


async def get_bank_account(
    db: AsyncSession, *, company: Company, bank_account_pk: int
) -> CompanyBankAccount:
    """One of this company's accounts, or :class:`BankAccountNotFoundError`.

    ``WHERE id = :id AND company_id = :company``, never ``db.get``: the id is
    sequential and belongs to the whole installation, so a lookup by primary key
    alone would answer with the company next door's account number.
    """
    account = await db.scalar(
        select(CompanyBankAccount).where(
            CompanyBankAccount.id == bank_account_pk,
            CompanyBankAccount.company_id == company.id,
        )
    )
    if account is None:
        raise BankAccountNotFoundError("kontonummeret finnes ikke på dette selskapet")
    return account


def _held_by(company: Company, number: str, *, except_pk: int | None = None) -> bool:
    return any(held.account == number and held.id != except_pk for held in company.bank_accounts)


async def _make_primary(db: AsyncSession, company: Company, account: CompanyBankAccount) -> None:
    """Promote one account and demote the rest — demotion first, in its own flush.

    Two flushes and not one, and that is the whole point. The partial unique
    index is checked statement by statement, so an instant where two rows carry
    the flag is a violation even though the same flush would have cleared it a
    statement later. The unit of work orders UPDATEs by primary key, so a single
    flush is free to write the promotion before the demotion — which is exactly
    what happens when the marking moves *back* to an older account, and it is
    what this used to do.

    The index stays the backstop for two requests racing. This is what keeps the
    ordinary case from meeting it.
    """
    for held in company.bank_accounts:
        if held is not account:
            held.is_primary = False
    await db.flush()
    account.is_primary = True
    await db.flush()


async def add_bank_account(
    db: AsyncSession,
    *,
    company: Company,
    account: str,
    label: str | None = None,
    is_primary: bool = False,
) -> CompanyBankAccount:
    """Add a kontonummer to the company.

    The first account added is always the primary whatever the caller asked for:
    a company with accounts and no default is a company whose next invoice has
    nowhere to be paid, and nobody would have chosen that on purpose.

    The number is checked before it is stored, because it leaves the building on
    every invoice and an issued salgsdokument can only be corrected with a
    kreditnota — see :mod:`myntos.domain.ledger.bankkonto`.
    """
    number = validate_bank_account(account)
    if _held_by(company, number):
        raise DuplicateBankAccountError(
            f"selskapet har allerede kontonummer {format_bank_account(number)}"
        )
    # Read before the append, so it counts the accounts as they were.
    wants_primary = is_primary or not company.bank_accounts
    # Inserted unmarked whatever the answer, and promoted afterwards. Carrying
    # the flag into the INSERT would put a second marked row in the table for
    # the length of one statement, because the unit of work writes inserts
    # before updates — and the index does not care that the update was coming.
    added = CompanyBankAccount(
        account=number, label=(label or "").strip() or None, is_primary=False
    )
    company.bank_accounts.append(added)
    await db.flush()
    if wants_primary:
        await _make_primary(db, company, added)
    return added


async def update_bank_account(
    db: AsyncSession,
    *,
    company: Company,
    bank_account_pk: int,
    account: str | None = None,
    label: str | None = None,
    is_primary: bool | None = None,
) -> CompanyBankAccount:
    """Correct an account, rename it, or make it the one invoices default to.

    Every field omitted is left alone, as everywhere else in this module. An
    empty ``label`` is how a name is removed; an empty ``account`` is not
    accepted, because an account row with no number is not a thing that can
    exist — deleting it is.

    ``is_primary=False`` is refused on the account that currently is primary,
    and accepted everywhere else. That asymmetry is not a nicety: a form
    resubmitting every field it shows must go on working, and the only way to
    move the marking is to promote the account it should move *to*.
    """
    held = await get_bank_account(db, company=company, bank_account_pk=bank_account_pk)
    if account is not None:
        number = validate_bank_account(account)
        if _held_by(company, number, except_pk=held.id):
            raise DuplicateBankAccountError(
                f"selskapet har allerede kontonummer {format_bank_account(number)}"
            )
        held.account = number
    if label is not None:
        held.label = label.strip() or None
    if is_primary is not None:
        if is_primary:
            await _make_primary(db, company, held)
        elif held.is_primary:
            raise PrimaryBankAccountRequiredError(
                "et selskap med kontoer har alltid én hovedkonto. Gjør en annen konto til "
                "hovedkonto i stedet, så flytter merkingen seg dit"
            )
    await db.flush()
    return held


async def delete_bank_account(db: AsyncSession, *, company: Company, bank_account_pk: int) -> None:
    """Remove an account. Issued documents keep theirs.

    Nothing is archived and nothing is refused, because nothing is lost: a
    salgsdokument snapshots the number it was issued with
    (``SalesDocument.seller_bank_account``), and the key a *draft* holds is
    ``ON DELETE SET NULL``, so a draft pointed at a closed account falls back to
    the primary the next time it is saved rather than breaking.

    Deleting the primary promotes the oldest account that is left. A company can
    end up with no accounts at all — that is the state it was created in — but it
    must never end up with several and no default.
    """
    held = await get_bank_account(db, company=company, bank_account_pk=bank_account_pk)
    was_primary = held.is_primary
    company.bank_accounts.remove(held)
    await db.flush()
    if was_primary and company.bank_accounts:
        oldest = min(company.bank_accounts, key=lambda account: account.id)
        await _make_primary(db, company, oldest)


# --- What the register says ------------------------------------------------------


# The fields Enhetsregisteret is the authority for, and which therefore get
# overwritten by a refresh rather than merged with what is stored. Two of them
# decide what a salgsdokument may claim (§ 5-1-2 and merverdiavgiftsloven), and
# a stale copy of those is worse than no copy: it is a wrong claim, repeated on
# every invoice, that nobody would think to check.
#
# Deliberately **not** in this set:
#
# - ``bank_account``, which the register does not hold at all.
# - ``ki_enabled`` and the confirmation columns, which are ours.
# - ``name``, which is the one field a person may reasonably keep their own
#   version of — a sole trader whose registered name is "OLA NORDMANN" in block
#   capitals should not have it shouted back at them on every invoice. It is
#   taken over only when it is still what we last wrote, the same rule
#   ``adopt_name_if_placeholder`` follows for organizations.
# Company attribute → the attribute on RegisteredUnit it is copied from. Two of
# them are spelled differently on each side, and a plain list of names would
# have silently skipped exactly those two — the VAT and Foretaksregisteret
# markings, which are the pair that decides what an invoice may claim.
REGISTER_FIELDS: dict[str, str] = {
    "address_line": "address_line",
    "postal_code": "postal_code",
    "city": "city",
    "organisation_form": "organisation_form",
    "vat_registered": "registered_in_vat_register",
    "in_foretaksregisteret": "registered_in_foretaksregisteret",
    "industry_code": "industry_code",
    "industry_description": "industry_description",
    "website": "website",
    "phone": "phone",
    "email": "email",
    "parent_orgnr": "parent_orgnr",
}


async def refresh_company_from_register(
    db: AsyncSession, *, company: Company, unit: RegisteredUnit
) -> list[str]:
    """Copy what Enhetsregisteret says onto ``company``, and report what moved.

    ``unit`` is passed in rather than fetched here for the reason every other
    integration result is: the domain owns the rule about what may be written,
    the API layer owns the network call and the failure modes that come with it.

    The list of changed field names is the point of the return value. A refresh
    that silently rewrote the VAT flag would be a change to what every future
    invoice claims, made without anybody being told; the caller shows the list.

    The **name is never overwritten**, which is why it is not in the set above.
    Two things are indistinguishable from here — a name somebody edited on
    purpose and a name that has simply gone stale — and one of those is a
    deliberate choice a refresh has no business undoing. A sole trader whose
    registered name is "OLA NORDMANN" in block capitals is the ordinary case,
    and having it shouted back onto every invoice by a background refresh is not
    a service. The register's name is shown next to the stored one instead, and
    adopting it is a click.
    """
    changed: list[str] = []
    for field, source in REGISTER_FIELDS.items():
        fresh = getattr(unit, source)
        # A register that has stopped holding a value does not clear ours. It
        # answers with nulls for fields it simply never had, and treating that
        # as "delete the address" would lose data on a lookup.
        if fresh is None or fresh == getattr(company, field):
            continue
        setattr(company, field, fresh)
        changed.append(field)

    company.register_synced_at = utc_now()
    await db.flush()
    # A næringskode that makes these books a farm, and nobody has said
    # otherwise: the farm's accounts go in now, the way switching the add-on on
    # would add them. A farm moving to Myntos brings an opening balance with
    # buskap on 1450 and its share in Tine on 1355 before anything is posted,
    # and those accounts have to be there to receive it.
    if "industry_code" in changed and landbruk.is_enabled(company):
        await landbruk.ensure_accounts(db, company=company)
    return changed


async def store_register_snapshot(
    db: AsyncSession,
    *,
    company: Company,
    unit: dict[str, object],
    roles: list[dict[str, object]] | None,
) -> CompanyRegisterSnapshot:
    """Keep the register's whole answer, not just the fields we read by name.

    The columns on :class:`Company` are what the system uses; this is everything
    else it said — styre, revisor, kapital, formål, tidligere navn — so that
    wanting one of them later is a question to the database rather than a second
    round trip to Brønnøysund.

    ``unit`` and ``roles`` arrive already reduced to JSON by the API layer, in
    exactly the shape the API serves them in. That is deliberate: the stored
    answer and the freshly fetched one are then the same document, and the page
    that renders one renders the other without knowing which it got.

    Replaced, never appended. This is the current picture of a business, and a
    superseded register answer describes our fetching rather than the company.

    ``roles`` of ``None`` means the role lookup did not answer — a separate
    request from the unit's, and one that fails on its own. The roles already
    stored are kept then, because an old board is a better answer than no board,
    and the ``fetched_at`` beside it says how old.
    """
    snapshot = await db.scalar(
        select(CompanyRegisterSnapshot).where(CompanyRegisterSnapshot.company_id == company.id)
    )
    if snapshot is None:
        snapshot = CompanyRegisterSnapshot(company_id=company.id, orgnr=company.orgnr, roles=[])
        db.add(snapshot)
    snapshot.orgnr = company.orgnr
    snapshot.unit = unit
    if roles is not None:
        snapshot.roles = roles
    snapshot.fetched_at = utc_now()
    await db.flush()
    return snapshot


async def get_register_snapshot(
    db: AsyncSession, *, company: Company
) -> CompanyRegisterSnapshot | None:
    """The stored register answer for this company, if one has been kept.

    Returns nothing for a snapshot taken under a different orgnr. An unconfirmed
    company's number can still be corrected, and the board of the company that
    was there before is not a stale answer about this one — it is an answer
    about somebody else.
    """
    snapshot = await db.scalar(
        select(CompanyRegisterSnapshot).where(CompanyRegisterSnapshot.company_id == company.id)
    )
    if snapshot is None or snapshot.orgnr != company.orgnr:
        return None
    return snapshot


# --- Confirmed ownership ---------------------------------------------------------
#
# Anyone can type nine digits into a form, so a freshly created set of books says
# nothing about who the company belongs to. The confirmation below is what turns
# it into a claim: the person logs in with ID-porten, Altinn is asked which
# parties they may act for, and only an orgnr on that list may be confirmed.
#
# More than one set of books may be confirmed for an orgnr, but never without
# the person being told. The first attempt on an orgnr somebody else has
# confirmed is refused with CompanyAlreadyClaimedError, and only a second one
# saying even_if_claimed goes through: Altinn's word is as good the second
# time, and the warning is there so that nobody files from a second set of
# books without knowing the first exists.


async def claimed_elsewhere(db: AsyncSession, orgnrs: Sequence[str], *, user_id: int) -> set[str]:
    """Which of ``orgnrs`` are confirmed by an organization ``user_id`` is not in.

    Used to mark the Altinn list before the person picks from it, so the
    conflict is visible where the choice is made rather than after a save that
    fails. A claim held by books the caller can already open is not "elsewhere"
    and is left out: those are theirs.

    It answers only for orgnrs the caller has already proved rights for in
    Altinn, so it discloses nothing they could not learn by confirming and
    reading the refusal. Whoever holds the claim is never named.
    """
    if not orgnrs:
        return set()
    mine = (
        select(CompanyMembership.company_id)
        .where(CompanyMembership.user_id == user_id)
        .scalar_subquery()
    )
    rows = await db.scalars(
        select(Company.orgnr).where(
            Company.orgnr.in_(list(orgnrs)),
            Company.verified_at.is_not(None),
            # Books deleted from Myntos give the claim up with everything else.
            Company.closed_at.is_(None),
            Company.id.not_in(mine),
        )
    )
    return set(rows)


async def confirm_company_ownership(
    db: AsyncSession,
    *,
    company: Company,
    user_id: int,
    authorized_parties: dict[str, int],
    even_if_claimed: bool = False,
) -> Company:
    """Record that somebody proved through Altinn that these books are theirs.

    ``authorized_parties`` maps orgnr to Altinn's party id, straight from the
    person's own authorized-parties list. Passed in rather than fetched here
    because the fetch needs a government token, which is an API-layer concern —
    the rule this function owns is what may be done with the answer.

    Re-confirming a company that is already confirmed is allowed and is a no-op
    on the timestamp: the claim was already established, and a second BankID
    should not look like a failure.

    ``even_if_claimed`` is the person's «Ja» to the warning that somebody else
    has confirmed the orgnr already. Without it that case is refused, so the
    warning is always seen first; with it, the claim is recorded like any
    other. Altinn is asked either way: the yes covers the other books, never
    the rights.
    """
    party_id = authorized_parties.get(company.orgnr)
    if party_id is None:
        raise NotAuthorizedForPartyError(
            "Altinn viser ikke at du kan opptre for dette selskapet. "
            "Rettighetene tildeles i Altinn av den som er registrert for selskapet."
        )
    if company.is_verified:
        # Still worth refreshing: the party id is Altinn's key and can be
        # reissued when a unit is restructured.
        company.altinn_party_id = party_id
        await db.flush()
        return company

    if not even_if_claimed:
        holder = await db.scalar(
            select(Company.id).where(
                Company.orgnr == company.orgnr,
                Company.verified_at.is_not(None),
                Company.closed_at.is_(None),
                Company.id != company.id,
            )
        )
        if holder is not None:
            raise CompanyAlreadyClaimedError(
                "Selskapet har allerede et regnskap registrert hos noen andre. "
                "Be en administrator der om tilgang, eller bekreft likevel."
            )

    # Two confirmations racing both succeed, and that is now allowed: each
    # person either saw the warning or confirmed before anyone else had.
    company.verified_at = utc_now()
    company.verified_by_user_id = user_id
    company.altinn_party_id = party_id
    await db.flush()
    return company


async def list_companies(
    db: AsyncSession, *, user_id: int
) -> list[tuple[Company, CompanyMembership]]:
    """Every set of books the user has been given access to, with their role.

    Through :class:`CompanyMembership`, one row per person per company, and not
    through the account: somebody invited to help with one client sees that
    client and not the twenty next to it.

    The role comes back with the company because every caller needs both. The
    frontend decides what to offer from the role it holds over the company on
    screen, and a list of companies with no roles in it would send it back for
    one request per company.

    Books deleted from Myntos are not in it: nobody works in them any more,
    and their owners find them under the account instead
    (myntos.domain.identity.closure).
    """
    rows = await db.execute(
        select(Company, CompanyMembership)
        .join(CompanyMembership, CompanyMembership.company_id == Company.id)
        .where(CompanyMembership.user_id == user_id, Company.closed_at.is_(None))
        .order_by(Company.name)
    )
    return [(company, membership) for company, membership in rows]


async def get_company_for_user(
    db: AsyncSession, *, company_id: int, user_id: int
) -> tuple[Company, CompanyMembership]:
    """The company and what this caller may do with it.

    The pair every company-scoped endpoint runs on: no route resolves a company
    without also learning the role held over *that* company, because that is
    exactly the pair a missing check separates.

    A company the caller has no membership on raises
    :class:`CompanyNotFoundError`, the same error as one that does not exist:
    the API must not become a way to discover which ids are in use.

    A company deleted from Myntos is the same 404, to its own owners too. Every
    route that reads or writes books runs through here, so this one condition
    is what keeps all of them away from books that are only being kept; the
    few routes that do reach them (seeing and reopening them) resolve them
    themselves, in myntos.domain.identity.closure.
    """
    row = (
        await db.execute(
            select(Company, CompanyMembership)
            .join(CompanyMembership, CompanyMembership.company_id == Company.id)
            .where(
                Company.id == company_id,
                CompanyMembership.user_id == user_id,
                Company.closed_at.is_(None),
            )
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

    accounts = list(await db.scalars(select(Account).where(Account.company_id == company.id)))
    # The latest earlier year's row per account, which is what carries a
    # person's choice forward. This used to look every account up in the seed
    # chart, which was right only while the seed was the whole kontoplan; the
    # first account somebody added would have made opening the next year raise.
    previous: dict[int, AccountGrouping] = {}
    for grouping in await db.scalars(
        select(AccountGrouping)
        .join(Account, Account.id == AccountGrouping.account_id)
        .where(Account.company_id == company.id, AccountGrouping.financial_year < year)
        .order_by(AccountGrouping.financial_year)
    ):
        previous[grouping.account_id] = grouping
    for account in accounts:
        db.add(kontoplan.grouping_for_new_year(account, previous.get(account.id), year))
    await db.flush()
    return financial_year


async def ensure_financial_year(
    db: AsyncSession, company: Company, year: int, *, on: date | None = None
) -> FinancialYear:
    """The company's financial year, opening it on demand.

    Refuses a year before the company's first one: the seeded VAT rates are
    only claimed to be valid from then, and posting into a period whose rates
    we never knew would snapshot an invented rate onto the entry.

    Refuses anything after a wound-up company's last day
    (``company.wound_up_on``): a year after it, and, when the caller passes the
    voucher's date as ``on``, a day after it in the last year. That year ends
    on the last day, not on 31 December, and a voucher dated past its end would
    be in no period the SAF-T file or the næringsspesifikasjon can report.
    """
    wound_up = company.wound_up_on
    if wound_up is not None and (year > wound_up.year or (on is not None and on > wound_up)):
        raise PeriodOutsideBooksError(
            f"selskapet er oppløst, og regnskapet sluttet {wound_up:%d.%m.%Y}; "
            "det går ikke an å føre etter det"
        )
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


async def open_earlier_years(
    db: AsyncSession,
    company: Company,
    first: int,
    *,
    observed_rates: Mapping[str, set[Decimal]] | None = None,
) -> list[int]:
    """Open every year from ``first`` up to the company's first one, for the books brought over.

    Only for :mod:`myntos.saft.move`, which brings earlier years over from the
    previous program's SAF-T files: everything else is refused a year before
    the first (:func:`ensure_financial_year`). Two things that rule protects are
    taken care of here instead:

    - **The VAT rates.** The seeded rates claim only the first year, because
      claiming them for 2019 would be a fabrication: the low rate was 8 % and
      then 6 % for most of 2020 and 2021. So a code's seeded rate is carried
      back to ``first`` only when the files prove it, every line they carry
      with that code having had that rate (``observed_rates``, by code, as
      fractions). A code the files did not use, or used at another rate, gets
      no rate for those years, and a new entry with it there is refused as
      before rather than given an invented one. The imported lines carry the
      rate the file reported whatever happens here. Only a code's seeded row
      moves (the one starting on the old first year's 1 January); a rate
      somebody added stays as it is.
    - **The seeded accounts**, dated the first year's 1 January as the seed is,
      move back with it, so the chart says they were there for the years they
      are used in.

    No floor on how far back: the years are the person's books, and what they
    can bring is what their old program can export.

    Returns the years opened, oldest first. Nothing is opened when ``first`` is
    not before the first year.
    """
    earliest = await db.scalar(
        select(func.min(FinancialYear.year)).where(FinancialYear.company_id == company.id)
    )
    if earliest is None or first >= int(earliest):
        return []
    old_start = date(int(earliest), 1, 1)
    new_start = date(first, 1, 1)
    seen = observed_rates or {}
    for code, rate in (
        await db.execute(
            select(TaxCode.code, TaxRate)
            .join(TaxCode, TaxCode.id == TaxRate.tax_code_id)
            .where(TaxCode.company_id == company.id, TaxRate.valid_from == old_start)
        )
    ).tuples():
        if seen.get(code) == {rate.rate}:
            rate.valid_from = new_start
    for account in await db.scalars(
        select(Account).where(Account.company_id == company.id, Account.created_date == old_start)
    ):
        account.created_date = new_start
    opened: list[int] = []
    for year in range(first, int(earliest)):
        await _open_financial_year(db, company, year)
        opened.append(year)
    return opened


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


def _basis_account(
    accounts: dict[str, Account], catalogue_number: str, chosen: str | None
) -> Account:
    """The account the amount is booked to: the category's, or one like it.

    A person may book a category to another account of the **same type** in
    their own kontoplan: «Datakostnad» to a 6820 they added for hosting, a
    sale to a 3050 for one product line. The type is what keeps that choice
    from moving the amount between the balance sheet and the income statement,
    which is the one thing a category exists to get right. The accounts the
    code refers to by number are never a choice; a cost booked to 2710 is a
    deduction nobody claimed.

    A deactivated account is refused whichever way it was reached, with a
    message that says where it can be switched back on.
    """
    default = accounts.get(catalogue_number)
    if default is None:
        raise ChartIncompleteError(f"kontoplanen mangler konto {catalogue_number}")
    account = default
    if chosen is not None and chosen != catalogue_number:
        picked = accounts.get(chosen)
        if picked is None:
            raise AccountNotFoundError(f"kontoplanen har ingen konto {chosen}")
        if picked.account_number in kontoplan.LOCKED_ACCOUNTS:
            raise InvalidAccountNumberError(
                f"konto {chosen} brukes av Myntos selv og kan ikke velges for en betaling"
            )
        if picked.account_type is not default.account_type:
            raise InvalidAccountNumberError(
                f"konto {chosen} er en {picked.account_type.value}skonto, og typen du valgte "
                f"føres på en {default.account_type.value}skonto"
            )
        account = picked
    if not account.is_active:
        raise InactiveAccountError(
            f"konto {account.account_number} er deaktivert. Aktiver den i kontoplanen, eller "
            "velg en annen konto"
        )
    return account


def _check_gross(amount: Decimal) -> Decimal:
    if not isinstance(amount, Decimal):  # pragma: no cover - pydantic coerces first
        raise InvalidAmountError("beløpet må være et desimaltall")
    exponent = amount.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -2:
        raise InvalidAmountError("beløpet kan ikke ha brøkdeler av et øre")
    if amount <= ZERO:
        raise InvalidAmountError("beløpet må være større enn null")
    return amount


async def _accounts_for(
    db: AsyncSession,
    company: Company,
    wanted: Sequence[tuple[cashflow.CashFlowType, str | None]],
) -> dict[str, Account]:
    """The kontoplan by number, with any add-on account these categories need.

    A farm category books to a NIBIO account the add-on adds to the kontoplan.
    Books that have the add-on through their næringskode, and that nobody has
    switched on by hand, may not have the account yet; it is added here the
    first time a category needs it, from the same table the switch adds from,
    rather than refusing a farm's first milk settlement with a missing account.
    The same holds for the account a person moved the category to, when that is
    one of the add-on's (the levy moved from milk to slaughter). Every other
    missing account stays the person's to create.
    """
    accounts = await _accounts_by_number(db, company)
    for entry_type, chosen in wanted:
        if entry_type.segment is None:
            continue
        for number in (entry_type.account_number, chosen):
            if number is None or number in accounts or number not in LANDBRUK_ACCOUNTS_BY_NUMBER:
                continue
            accounts[number] = await kontoplan.ensure_seeded_account(
                db, company=company, number=number
            )
    return accounts


@dataclass(frozen=True)
class _Trade:
    """The lines one category produces, and the money it moved.

    ``lines`` are the basis line and its VAT lines, in posting order and without
    record ids, which the voucher assigns once every trade is in. ``cash`` is
    signed: what this trade adds to the bank (positive) or takes from it.
    """

    lines: list[TransactionLine]
    cash: Decimal


async def _trade(
    db: AsyncSession,
    *,
    company: Company,
    entry_type: cashflow.CashFlowType,
    occurred_on: date,
    gross: Decimal,
    description: str,
    tax_code: str | None,
    account_number: str | None,
    party: Party | None,
    accounts: dict[str, Account],
) -> _Trade:
    """The basis and VAT lines for one category, as both kinds of entry book them.

    An ordinary entry is one of these and the payment line; a composite entry is
    several and one payment line for what they net to. Written once, so a
    settlement's milk line and a milk payment of its own cannot come to disagree
    about the rate, the deduction or which side the VAT goes on.
    """
    # Registration is read on the entry's own date, not today's: a purchase
    # made before the registration took effect carried no deduction, whenever
    # it is typed in. See .vatstatus for what an unregistered business gets.
    registered = vatstatus.registered_on(company, occurred_on)
    if not registered and entry_type.code in vatstatus.REGISTERED_ONLY_TYPES:
        raise TaxCodeNotAllowedError(
            f"selskapet var ikke mva-registrert {occurred_on.isoformat()}, og da er mva betalt "
            "ved innførsel en del av det varene kostet. Før hele regningen som varekjøp"
        )
    code = (
        tax_code
        if tax_code is not None
        else vatstatus.default_tax_code(entry_type.default_tax_code, registered)
    )
    allowed = vatstatus.allowed_tax_codes(registered)
    if allowed is not None and code not in allowed:
        raise TaxCodeNotAllowedError(
            f"selskapet var ikke mva-registrert {occurred_on.isoformat()}, så mva-kode {code} "
            "kan ikke brukes: den gir fradrag eller krever inn mva. Bruk kode 0, så føres hele "
            "beløpet"
        )
    basis_account = _basis_account(accounts, entry_type.account_number, account_number)
    tax = await _resolve_tax_code(db, company, code)
    rate = posting.rate_on(tax, occurred_on)
    seed = TAX_CODES_BY_CODE.get(code)
    reverse_charge = seed is not None and seed.reverse_charge
    vat_only = seed is not None and seed.vat_only
    if vat_only:
        # Codes 14 and 15: the bill is the tax and nothing else. The basis it
        # was calculated on is the customs value of goods paid for elsewhere,
        # so it is derived back from the amount rather than asked for: SAF-T
        # wants a basis on every taxed line, and the MVA-melding reports only
        # the deduction for these codes, so an øre of rounding in the derived
        # basis reaches nobody.
        net = gross
        vat = gross
    elif reverse_charge:
        # Innførsel and services bought abroad: the foreign seller charged no
        # Norwegian VAT, so nothing is carved out of what was paid. The whole
        # amount is the cost, and the VAT is computed on top of it as an amount
        # the buyer owes and, when deductible, reclaims in the same breath.
        net = gross
        vat = (gross * rate).quantize(Decimal("0.01"))
    else:
        net, vat = posting.split_gross(gross, rate)
        if net == ZERO:
            # Only reachable with an absurd rate, but a zero-amount line does
            # not post at all — booking the whole amount as basis is the safe
            # reading.
            net, vat = gross, ZERO

    is_incoming = entry_type.direction is CashFlowDirection.INN
    basis_line = TransactionLine(
        # Whatever the category says, a vat-only code lands on 2710: picking 14
        # under "Varekjøp" means the bill was the border VAT, and booking it to
        # 4000 would make a deductible tax a cost.
        account=accounts[INPUT_VAT_ACCOUNT] if vat_only else basis_account,
        # On the basis line, like the tax information: SAF-T puts
        # CustomerID/SupplierID on the line describing the trade, not on the
        # one describing the money movement.
        party=party,
        description=description,
        debit=ZERO if is_incoming else net,
        credit=net if is_incoming else ZERO,
        # What this line was booked as, so a composite entry reads back line
        # by line (see TransactionLine.entry_type).
        entry_type=entry_type.code,
    )
    # On the basis line, never on the VAT line — see posting.set_tax_information.
    # The one exception is a vat-only code, where the two are the same line.
    basis = posting.quantize_money(gross / rate) if vat_only and rate > ZERO else net
    posting.set_tax_information(basis_line, tax_code=tax, rate=rate, basis=basis, amount=vat)
    lines = [basis_line]

    if vat_only:
        pass
    elif vat > ZERO and reverse_charge:
        # Both sides, because the buyer is both parties to this VAT. 2700 is
        # what is owed on the import; 2710 is the deduction, present only when
        # the purchase carries the right to one. Deductible, they cancel and the
        # entry costs nothing extra — but both still have to be posted, because
        # the MVA-melding reports the amount twice and a single net-zero line
        # would report it nowhere.
        #
        # On a refund both sides go back the other way. The import was undone,
        # so the calculated VAT is no longer owed and the deduction taken
        # against it is no longer due.
        #
        # A refund *of an import*, which is the money-in half of the reversals.
        # A kreditnota is a reversal too and goes the other way, and reverse
        # charge is a purchase-side treatment, so it must not reach this flip.
        undone = entry_type.is_refund and is_incoming
        lines.append(
            TransactionLine(
                account=accounts[OUTPUT_VAT_ACCOUNT],
                description=f"Beregnet mva ved innførsel, kode {code}",
                debit=vat if undone else ZERO,
                credit=ZERO if undone else vat,
            )
        )
        if seed is not None and seed.deductible:
            lines.append(
                TransactionLine(
                    account=accounts[INPUT_VAT_ACCOUNT],
                    description=f"Fradrag innførselsmva, kode {code}",
                    debit=ZERO if undone else vat,
                    credit=vat if undone else ZERO,
                )
            )
        else:
            # Not deductible, so the amount is a real cost rather than a wash.
            # It belongs on the same account as the purchase it arose from, and
            # on a refund it leaves that account again with the purchase.
            lines.append(
                TransactionLine(
                    account=basis_account,
                    description=f"Ikke fradragsberettiget innførselsmva, kode {code}",
                    debit=ZERO if undone else vat,
                    credit=vat if undone else ZERO,
                )
            )
    elif vat > ZERO:
        # Which side of the VAT accounts this belongs to is the trade's
        # direction, not the bank's, and on the two reversal families those
        # differ. A refund is money in on a purchase: its VAT is the deduction
        # going back the way it came, so credited to 2710 it reduces the input
        # VAT for the period, while credited to 2700 it would report tax owed on
        # a sale that never happened — for the eBay refund, 5 253 kroner of tax
        # on a cancelled purchase. A kreditnota is money out on a sale, and it
        # is the same mistake in the other direction: debited to 2710 it claims
        # a deduction on goods the company sold, when what it should do is give
        # back the output VAT it charged the customer, on 2700.
        #
        # ``books_as`` is that distinction, stated once in the catalogue.
        owed_on_a_sale = entry_type.books_as is CashFlowDirection.INN
        lines.append(
            TransactionLine(
                account=accounts[OUTPUT_VAT_ACCOUNT if owed_on_a_sale else INPUT_VAT_ACCOUNT],
                description=f"Mva {code}",
                debit=ZERO if is_incoming else vat,
                credit=vat if is_incoming else ZERO,
            )
        )
    return _Trade(lines=lines, cash=gross if is_incoming else -gross)


def _numbered(lines: list[TransactionLine]) -> list[TransactionLine]:
    """Give each line its SAF-T RecordID, from one, in posting order."""
    for index, line in enumerate(lines, start=1):
        line.record_id = str(index)
    return lines


def _allocate(trade: _Trade, values: Sequence[DimensionValue]) -> None:
    """Put the avdeling, prosjekt or driftsgren the person chose on the trade's own lines.

    The basis line, and any further line on the same account: the
    non-deductible VAT on an import is part of what the purchase cost the
    driftsgren. Not the VAT lines and not the bank line, which are balance-sheet
    movements no result per avdeling reads.
    """
    if not values:
        return
    basis = trade.lines[0]
    for line in trade.lines:
        if line is basis or line.account is basis.account:
            dimensions.allocate(line, values)


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
    corrects_pk: int | None = None,
    created_by_user_id: int | None = None,
    account_number: str | None = None,
    dimension_value_ids: Sequence[int] = (),
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

    ``corrects_pk`` names the entry this one was written to replace, and is set
    only by the correction flow: reverse the original, post this in its place,
    and record which is which. Without it the replacement is indistinguishable
    from an unrelated payment made the same day, and the three vouchers a
    correction leaves behind cannot be read back as one event.

    ``created_by_user_id`` is the person doing the entering, and every caller
    with a session should pass it: on books with two people on them it is the
    only thing that says which of them booked this. It defaults to ``None``
    rather than being required because the column is honestly nullable — see
    :class:`~myntos.domain.ledger.models.Transaction` — and a required argument
    would tempt a caller without a user into passing a placeholder id, which is
    worse than an empty field.

    ``account_number`` books the amount to another account of the same type
    than the category's own, one the company added to its kontoplan; see
    :func:`_basis_account` for what may be chosen.

    ``dimension_value_ids`` are the avdeling, prosjekt or driftsgren the trade
    belongs to, at most one per dimension, resolved through the company
    (:func:`myntos.domain.ledger.dimensions.resolve`) and put on the trade's own
    lines (:func:`_allocate`).
    """
    entry_type = cashflow.get_type(type_code)
    landbruk.require_segment(company, entry_type.segment)
    gross = _check_gross(gross_amount)
    financial_year = await ensure_financial_year(db, company, occurred_on.year, on=occurred_on)
    journal = await _cash_journal(db, company)
    accounts = await _accounts_for(db, company, [(entry_type, account_number)])
    # Resolved through the company, so a party id belonging to another tenant
    # is not found rather than quietly attached.
    party = (
        None
        if party_pk is None
        else await parties.get_party(db, company=company, party_pk=party_pk)
    )
    chosen = await dimensions.resolve(db, company=company, value_ids=dimension_value_ids)
    trade = await _trade(
        db,
        company=company,
        entry_type=entry_type,
        occurred_on=occurred_on,
        gross=gross,
        description=description,
        tax_code=tax_code,
        account_number=account_number,
        party=party,
        accounts=accounts,
    )
    _allocate(trade, chosen)
    is_incoming = trade.cash > ZERO
    payment_line = TransactionLine(
        account=accounts[payment_method.account_number],
        description=description,
        debit=gross if is_incoming else ZERO,
        credit=ZERO if is_incoming else gross,
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
        corrects_transaction_pk=corrects_pk,
        created_by_user_id=created_by_user_id,
    )
    transaction.lines = _numbered([payment_line, *trade.lines])
    posting.post(transaction)
    db.add(transaction)
    await db.flush()
    return transaction


# --- Composite entries ------------------------------------------------------------

#: The voucher type (SAF-T TransactionType) of a composite entry. Not a category:
#: the categories are on its lines (``TransactionLine.entry_type``).
COMPOSITE_TYPE = "sammensatt"
COMPOSITE_LABEL = "Sammensatt bilag"

#: How many lines one composite entry may have. A Tine settlement with every
#: tillegg and trekk split out is a dozen or so; past this it is more likely a
#: whole statement pasted in as one voucher, which the bank import does better.
MAX_COMPOSITE_PARTS = 40


@dataclass(frozen=True)
class CompositePart:
    """One line of a composite entry: a category and the gross amount it moved.

    The same fields as an ordinary entry, minus the ones the voucher owns (the
    date, the payment method, the counterparty). ``description`` defaults to the
    voucher's own text.
    """

    type_code: str
    gross_amount: Decimal
    tax_code: str | None = None
    account_number: str | None = None
    description: str | None = None
    #: The avdeling, prosjekt or driftsgren this line belongs to: a settlement's
    #: milk to the milk production, the feed set off against it perhaps to the
    #: sheep.
    dimension_value_ids: tuple[int, ...] = ()


async def record_composite(
    db: AsyncSession,
    *,
    company: Company,
    occurred_on: date,
    description: str,
    parts: Sequence[CompositePart],
    payment_method: PaymentMethod = PaymentMethod.BANK,
    party_pk: int | None = None,
    corrects_pk: int | None = None,
    created_by_user_id: int | None = None,
) -> Transaction:
    """Record one bank movement that several trades netted into, as one voucher.

    The register's rule is one payment, one category. Plenty of real payments
    are several: the dairy pays for the month's milk less the levy, the share
    it keeps and the feed it sold the farmer; Vipps pays out the day's sales
    less its fee; a receipt from a shop has food at 15 % and a light bulb at
    25 %; a loan instalment is interest and repayment. Booked as one category,
    each of those is wrong somewhere: the net milk money on 3071 understates
    the sale and the VAT on it, the fee vanishes into the sales figure.

    So each trade is a line of its own, with its own category, amount, VAT code
    and account, built by exactly the same rules as an ordinary entry
    (:func:`_trade`), and one payment line carries what they net to. Lines going
    the other way from the bank movement are fine and expected: a settlement is
    money in with deductions out. Lines that cancel to nothing are refused,
    because then no money moved and there is nothing for the bank line to say.

    The voucher is filed as ``sammensatt`` and reads back line by line
    (:class:`CashFlowPart`). It is posted at once and immutable like any other
    entry, reversed or corrected as a whole.
    """
    if len(parts) < 2:
        raise InvalidCompositeEntryError(
            "et sammensatt bilag må ha minst to linjer. Én linje føres som en vanlig betaling"
        )
    if len(parts) > MAX_COMPOSITE_PARTS:
        raise InvalidCompositeEntryError(
            f"et sammensatt bilag kan ha høyst {MAX_COMPOSITE_PARTS} linjer"
        )
    types = [cashflow.get_type(part.type_code) for part in parts]
    for entry_type in types:
        landbruk.require_segment(company, entry_type.segment)
    grosses = [_check_gross(part.gross_amount) for part in parts]
    financial_year = await ensure_financial_year(db, company, occurred_on.year, on=occurred_on)
    journal = await _cash_journal(db, company)
    accounts = await _accounts_for(
        db, company, [(t, part.account_number) for t, part in zip(types, parts, strict=True)]
    )
    party = (
        None
        if party_pk is None
        else await parties.get_party(db, company=company, party_pk=party_pk)
    )
    chosen = [
        await dimensions.resolve(db, company=company, value_ids=part.dimension_value_ids)
        for part in parts
    ]
    trades = [
        await _trade(
            db,
            company=company,
            entry_type=entry_type,
            occurred_on=occurred_on,
            gross=gross,
            description=(part.description or "").strip() or description,
            tax_code=part.tax_code,
            account_number=part.account_number,
            party=party,
            accounts=accounts,
        )
        for entry_type, part, gross in zip(types, parts, grosses, strict=True)
    ]
    for trade, values in zip(trades, chosen, strict=True):
        _allocate(trade, values)
    net = sum((trade.cash for trade in trades), ZERO)
    if net == ZERO:
        raise InvalidCompositeEntryError(
            "linjene går i null, så ingen penger har flyttet seg, og bilaget har ingen "
            "betaling å vise. Sjekk beløpene mot det som kom inn eller gikk ut på kontoen"
        )
    payment_line = TransactionLine(
        account=accounts[payment_method.account_number],
        description=description,
        debit=net if net > ZERO else ZERO,
        credit=-net if net < ZERO else ZERO,
    )
    transaction = Transaction(
        journal_id=journal.id,
        transaction_id=await _next_voucher_number(db, journal.id, financial_year.year),
        financial_year=financial_year.year,
        period=occurred_on.month,
        transaction_date=occurred_on,
        gl_posting_date=occurred_on,
        description=description.strip(),
        voucher_type=COMPOSITE_TYPE,
        corrects_transaction_pk=corrects_pk,
        created_by_user_id=created_by_user_id,
    )
    transaction.lines = _numbered(
        [payment_line, *(line for trade in trades for line in trade.lines)]
    )
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
            # With the values, so a reversal's copy of an allocation knows its
            # dimension (posting._dimension_of) without a lazy load.
            selectinload(Transaction.lines)
            .selectinload(TransactionLine.analysis)
            .selectinload(TransactionLineAnalysis.dimension_value),
        )
    )


async def reverse_cash_flow(
    db: AsyncSession,
    *,
    company: Company,
    voucher_pk: int,
    created_by_user_id: int | None = None,
) -> Transaction:
    """Reverse a posted entry.

    The reversal lands in the **original's** period, so the year's figures net
    to zero rather than leaving a hole in one month and a bump in another. If
    that year is already closed the correction cannot go there, and the caller
    is told so instead of having it silently moved.

    ``created_by_user_id`` is whoever is undoing the entry, not whoever made it.
    The reversal is a voucher of its own and the two are frequently different
    people — the entry a colleague got wrong is exactly the one somebody else
    reverses — so copying the original's author would name the wrong person for
    the one action a reader most wants attributed.
    """
    original = await get_voucher(db, company=company, voucher_pk=voucher_pk)
    # The opening balance and the year-end closing have corrections of their
    # own (myntos.domain.arsoppgjor): replacing an opening balance reverses and
    # reposts in one step, and a closing is undone only by reopening the year,
    # which is logged with a reason. Reversed from here they would be undone
    # without either.
    journal_code = await db.scalar(
        select(Journal.journal_id).where(Journal.id == original.journal_id)
    )
    if journal_code in {OPENING_JOURNAL_ID, CLOSING_JOURNAL_ID}:
        raise CannotReverseHereError(
            "inngående balanse og årsavslutning rettes fra sine egne sider, ikke fra registeret"
        )
    # A voucher brought from the previous program is that program's entry,
    # documented in its archive. Corrected there, it comes across as the old
    # program's own reversal; reversed here, the two sets of books would part.
    if journal_code == IMPORT_JOURNAL_ID:
        raise CannotReverseHereError(
            "bilag hentet fra det gamle regnskapsprogrammet rettes der, ikke fra registeret"
        )
    # The farm's count and its private uttak are the postings of a stored list
    # (myntos.domain.arsoppgjor.buskap, .uttak). Reversed from here, the voucher
    # would be gone while its list still claimed to stand.
    if journal_code in {COUNT_JOURNAL_ID, WITHDRAWAL_JOURNAL_ID}:
        raise CannotReverseHereError(
            "telling av buskap og private uttak rettes fra sine egne sider, ikke fra registeret"
        )
    # A manual voucher is documented by a written reason, and so is undoing one
    # (bokføringsforskriften § 5-12). Its own page asks for that reason; this
    # route has nowhere to put it (myntos.domain.manuelt_bilag).
    if journal_code == MANUAL_JOURNAL_ID:
        raise CannotReverseHereError(
            "manuelle bilag reverseres og rettes under Manuelt bilag, med en begrunnelse"
        )
    # The year's depreciation is recorded per saldo beside its voucher
    # (myntos.domain.anlegg.avskrivning), and next year's saldo is built on it.
    # Reversed from here, the voucher would be gone while a later year's still
    # rested on it; its own page refuses that and reverses the later one first.
    if journal_code == DEPRECIATION_JOURNAL_ID:
        raise CannotReverseHereError(
            "avskrivningene rettes og reverseres under Anlegg, ikke fra registeret"
        )
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
    # Set here rather than in posting.build_reversal, for the same reason the
    # link above is: build_reversal mirrors the voucher's *contents*, and who
    # pressed the button is not one of them.
    reversal.created_by_user_id = created_by_user_id
    posting.post(reversal)
    db.add(reversal)
    await db.flush()
    return reversal


# --- Reading the register back --------------------------------------------------


@dataclass(frozen=True)
class CashFlowPart:
    """One line of a composite entry, read back as the category it was entered as.

    ``direction`` is which way *this line* moved money, which on a settlement
    differs from the voucher's: the milk is money in, the levy deducted from it
    money out. On a reversal every line's direction flips with the amounts.
    ``gross_amount`` is what the line contributed to the bank movement: net plus
    VAT for an ordinary trade, the net alone for reverse charge (the VAT there
    is computed on top and never paid to anyone), the VAT alone for a tollpass.
    """

    type_code: str | None
    direction: CashFlowDirection
    gross_amount: Decimal
    net_amount: Decimal
    vat_amount: Decimal
    account_number: str
    account_description: str
    tax_code: str | None
    description: str
    #: The avdeling, prosjekt or driftsgren the line was allocated to, by id.
    dimension_value_ids: tuple[int, ...] = ()


def _parts(transaction: Transaction) -> tuple[CashFlowPart, ...]:
    """The trade lines of a composite voucher, in the order they were entered."""
    parts: list[CashFlowPart] = []
    for line in sorted(transaction.lines, key=lambda line: int(line.record_id)):
        if line.entry_type is None:
            continue
        amount = (line.debit or ZERO) + (line.credit or ZERO)
        vat = (line.debit_tax_amount or ZERO) + (line.credit_tax_amount or ZERO)
        code = line.tax_code.code if line.tax_code is not None else None
        seed = TAX_CODES_BY_CODE.get(code) if code is not None else None
        if seed is not None and seed.vat_only:
            net, gross = ZERO, vat
        elif seed is not None and seed.reverse_charge:
            net, gross = amount, amount
        else:
            net, gross = amount, amount + vat
        parts.append(
            CashFlowPart(
                type_code=line.entry_type,
                direction=(
                    CashFlowDirection.INN if (line.credit or ZERO) > ZERO else CashFlowDirection.UT
                ),
                gross_amount=gross,
                net_amount=net,
                vat_amount=vat,
                account_number=line.account.account_number,
                account_description=line.account.description,
                tax_code=code,
                description=line.description,
                dimension_value_ids=dimensions.allocated_ids(line),
            )
        )
    return tuple(parts)


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
    # The entry this one replaced, when it was posted as a correction. Lets the
    # register show a correction as one line instead of three.
    corrects_id: int | None
    # Who entered it. Both are ``None`` for a voucher with no recorded author —
    # see :class:`~myntos.domain.ledger.models.Transaction` on the three ways
    # that happens. The id travels alongside the name so the register can group
    # by person without matching on a string two colleagues may share.
    created_by_id: int | None
    created_by_name: str | None
    # The name of the key the author worked through, when they used one
    # (docs/agents-and-api.md rule 6): "Kari Nordmann via «Claude»".
    created_via: str | None = None
    # The lines of a composite entry (type_code ``sammensatt``); empty for an
    # ordinary one, whose single line is the fields above.
    parts: tuple[CashFlowPart, ...] = ()
    # The avdeling, prosjekt or driftsgren of an ordinary entry, by id. Empty on
    # a composite entry, whose lines carry their own in ``parts``.
    dimension_value_ids: tuple[int, ...] = ()


def describe(
    transaction: Transaction,
    *,
    reversed_by_id: int | None = None,
    created_by_name: str | None = None,
    created_via: str | None = None,
) -> CashFlowView | None:
    """Project a voucher onto the cash-flow shape, or ``None`` if it has none.

    Only vouchers with a payment-account line can be shown as a payment. Every
    entry the register creates has one; a voucher posted by some future feature
    into the same journal might not, and dropping it from this listing is
    better than misreporting its amount as a cash movement.

    ``created_by_name`` is passed in rather than read through a relationship on
    the voucher. This function is synchronous and is called on freshly flushed
    objects, so touching a lazy relationship here would raise ``MissingGreenlet``
    inside whatever request happened to be reading — the same reason
    ``reversed_by_id`` is a parameter. :func:`list_cash_flows` resolves the
    whole page's names in one query; a caller that just posted the voucher
    already knows the name, because it is the caller's own.
    """
    payment = next(
        (line for line in transaction.lines if line.account.account_number in PAYMENT_ACCOUNTS),
        None,
    )
    if payment is None:
        return None
    if transaction.voucher_type == COMPOSITE_TYPE:
        return _describe_composite(
            transaction,
            payment,
            reversed_by_id=reversed_by_id,
            created_by_name=created_by_name,
            created_via=created_via,
        )
    basis = next(
        (
            line
            for line in transaction.lines
            if line.account.account_number not in PAYMENT_ACCOUNTS
            and line.account.account_number not in VAT_ACCOUNTS
        ),
        None,
    )
    # Border VAT paid on a tollpass (codes 14 and 15) has no basis account: the
    # whole payment went to 2710, and that line carries the tax information.
    # Without this fallback such a payment would vanish from the register while
    # still sitting in the books.
    tax_only = basis is None
    if basis is None:
        basis = next((line for line in transaction.lines if line.tax_code is not None), None)
    if basis is None:
        return None

    debit = payment.debit or ZERO
    credit = payment.credit or ZERO
    gross = debit if debit > ZERO else credit
    net = (basis.debit or ZERO) if (basis.debit or ZERO) > ZERO else (basis.credit or ZERO)
    if tax_only:
        net = ZERO
    # Read from the line rather than derived as gross - net, because the two
    # disagree for an import: the VAT there is computed on top of what was paid
    # instead of carved out of it, so gross equals net and the subtraction says
    # zero for an amount that is really 6 777,12. The line carries the figure
    # the posting actually used, which is true for both shapes.
    stated = (basis.debit_tax_amount or ZERO) + (basis.credit_tax_amount or ZERO)
    vat = stated if stated > ZERO else gross - net

    return CashFlowView(
        id=transaction.id,
        voucher_no=transaction.transaction_id,
        occurred_on=transaction.transaction_date,
        direction=CashFlowDirection.INN if debit > ZERO else CashFlowDirection.UT,
        type_code=transaction.voucher_type,
        gross_amount=gross,
        net_amount=net,
        vat_amount=vat,
        account_number=basis.account.account_number,
        account_description=basis.account.description,
        tax_code=basis.tax_code.code if basis.tax_code is not None else None,
        party_id=basis.party.id if basis.party is not None else None,
        party_name=basis.party.name if basis.party is not None else None,
        payment_account=payment.account.account_number,
        description=transaction.description,
        is_reversal=transaction.reverses_transaction_pk is not None,
        reversed_by_id=reversed_by_id,
        # A plain column on the voucher, so no second query: the correction
        # knows what it replaced, and the register reads it straight off.
        corrects_id=transaction.corrects_transaction_pk,
        created_by_id=transaction.created_by_user_id,
        # Never a fallback name. A voucher with an author whose name the caller
        # did not resolve, and a voucher with no author at all, are different
        # facts, and the interface says «ukjent» for the second one only.
        created_by_name=created_by_name,
        created_via=created_via,
        dimension_value_ids=dimensions.allocated_ids(basis),
    )


def _describe_composite(
    transaction: Transaction,
    payment: TransactionLine,
    *,
    reversed_by_id: int | None,
    created_by_name: str | None,
    created_via: str | None,
) -> CashFlowView | None:
    """A composite voucher: the bank movement, and the lines it is made of.

    The headline figures describe the movement as a whole. ``vat_amount`` is
    the VAT that moved *with the money*, netted: on a settlement, the output VAT
    on the milk less the input VAT on the feed set off against it. Reverse
    charge does not count, since nobody paid it. ``net_amount`` is the rest. The
    account and the category on the headline are the first line's, which is
    what the person entered first and almost always the trade the voucher is
    about.
    """
    parts = _parts(transaction)
    if not parts:
        return None
    debit = payment.debit or ZERO
    credit = payment.credit or ZERO
    incoming = debit > ZERO
    gross = debit if incoming else credit
    moved_vat = ZERO
    for part in parts:
        seed = TAX_CODES_BY_CODE.get(part.tax_code) if part.tax_code is not None else None
        if seed is not None and seed.reverse_charge:
            continue
        same_way = (part.direction is CashFlowDirection.INN) is incoming
        moved_vat += part.vat_amount if same_way else -part.vat_amount
    first = parts[0]
    trade_lines = [line for line in transaction.lines if line.entry_type is not None]
    party = next((line.party for line in trade_lines if line.party is not None), None)
    return CashFlowView(
        id=transaction.id,
        voucher_no=transaction.transaction_id,
        occurred_on=transaction.transaction_date,
        direction=CashFlowDirection.INN if incoming else CashFlowDirection.UT,
        type_code=COMPOSITE_TYPE,
        gross_amount=gross,
        net_amount=gross - moved_vat,
        vat_amount=moved_vat,
        account_number=first.account_number,
        account_description=first.account_description,
        # Several codes, so none on the headline; each line carries its own.
        tax_code=None,
        party_id=party.id if party is not None else None,
        party_name=party.name if party is not None else None,
        payment_account=payment.account.account_number,
        description=transaction.description,
        is_reversal=transaction.reverses_transaction_pk is not None,
        reversed_by_id=reversed_by_id,
        corrects_id=transaction.corrects_transaction_pk,
        created_by_id=transaction.created_by_user_id,
        created_by_name=created_by_name,
        created_via=created_via,
        parts=parts,
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
    # The opening balance is not a payment, though it puts money on 1920: it is
    # what the bank held when the books arrived, and listed here it would read
    # as the year's largest receipt.
    # Nor the vouchers brought from the previous program: the register reads a
    # voucher back as one of its own categories, and those were booked by
    # another program's rules. They count in the year's figures all the same.
    # Nor manual vouchers, for the same reason: a reclassification onto 1920
    # read back as a payment would show a category nobody chose. They are listed
    # on their own page, and count in the figures too. So is the year's
    # depreciation, which moves no money at all.
    query = _voucher_query().where(
        Journal.company_id == company.id,
        Journal.journal_id.not_in(
            (OPENING_JOURNAL_ID, IMPORT_JOURNAL_ID, MANUAL_JOURNAL_ID, DEPRECIATION_JOURNAL_ID)
        ),
    )
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
    authors = await _author_names(db, [t.created_by_user_id for t in transactions])
    keys = await _key_names(db, [t.created_via_agent_key_id for t in transactions])
    views = [
        describe(
            t,
            reversed_by_id=reversals.get(t.id),
            created_by_name=(
                None if t.created_by_user_id is None else authors.get(t.created_by_user_id)
            ),
            created_via=(
                None if t.created_via_agent_key_id is None else keys.get(t.created_via_agent_key_id)
            ),
        )
        for t in transactions
    ]
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


async def _author_names(db: AsyncSession, user_ids: Sequence[int | None]) -> dict[int, str]:
    """``{user id: full name}`` for the people who entered a page of vouchers.

    One query for the whole page, and by id rather than through a relationship
    on the voucher: a ``selectinload`` would work for the listing and be a
    ``MissingGreenlet`` waiting to happen everywhere else :func:`describe` is
    called on a freshly built object.

    Not filtered by company, and it does not need to be. The ids come off this
    company's own vouchers, so the only names this can return are of people who
    were entering them — including somebody since removed from the books, which
    is the answer that is true: they did make the entry. Nothing here reaches a
    user the caller could not already name, so it is not a way around the
    company wall (docs/company-isolation.md).
    """
    wanted = {user_id for user_id in user_ids if user_id is not None}
    if not wanted:
        return {}
    rows = await db.execute(select(User.id, User.full_name).where(User.id.in_(wanted)))
    return {row.id: row.full_name for row in rows}


async def _key_names(db: AsyncSession, key_ids: Sequence[int | None]) -> dict[int, str]:
    """``{key id: name}`` for the keys a page of vouchers was registered through.

    Not filtered by company, for the reason :func:`_author_names` gives: the
    ids come off this company's own vouchers, never from a request, and a key
    belongs to one company, so every name this can return is one of these
    books' own keys.
    """
    wanted = {key_id for key_id in key_ids if key_id is not None}
    if not wanted:
        return {}
    rows = await db.execute(select(AgentKey.id, AgentKey.name).where(AgentKey.id.in_(wanted)))
    return {row.id: row.name for row in rows}


# --- Summary --------------------------------------------------------------------


@dataclass(frozen=True)
class TypeTotal:
    type_code: str | None
    direction: CashFlowDirection
    total: Decimal
    count: int


@dataclass(frozen=True)
class CashFlowSummary:
    """What the overview shows: the year's money in and out, and what is owed now.

    ``output_vat`` and ``input_vat`` are the year's VAT, settlements left out;
    ``vat_position`` and ``cash_position`` are positions today, across every
    year, and ``vat_settled`` is the part of ``vat_position`` already filed
    (on 2740). ``books_through`` is the date of the last voucher in the books,
    any year, any journal.
    """

    financial_year: int
    total_in: Decimal
    total_out: Decimal
    net: Decimal
    entry_count: int
    output_vat: Decimal
    input_vat: Decimal
    vat_position: Decimal
    vat_settled: Decimal
    cash_position: Decimal
    books_through: date | None
    by_type: list[TypeTotal]


def _still_stands() -> ColumnElement[bool]:
    """True for a voucher that has not been cancelled out.

    A correction leaves three vouchers where one payment happened: the
    original, the reversal that undoes it, and the replacement. All three are
    in the ledger permanently, which is the law working as intended, but a
    year's *figures* must not count the payment three times.

    Left in, they are worse than merely inflated. Reversing a purchase debits
    the bank account, so the reversal of a 27 108,49 kroner purchase lands in
    "inn i år" as if the company had been paid that much, and the same purchase
    lands in "ut i år" twice. The net stays right, because the pair genuinely
    cancels; every figure either side of it is wrong.

    The VAT and cash figures do not need this — the pair mirrors line for line,
    so it nets to zero there on its own — but they use it anyway, so that one
    rule covers the whole summary instead of three that agree by coincidence.
    """
    reversal = aliased(Transaction, name="cancelling_voucher")
    return and_(
        # Not a reversal itself.
        Transaction.reverses_transaction_pk.is_(None),
        # And nothing reverses it.
        ~select(reversal.id).where(reversal.reverses_transaction_pk == Transaction.id).exists(),
    )


def not_a_settlement() -> tuple[ColumnElement[bool], ...]:
    """Leaves out the vouchers that settle a filed VAT term onto 2740.

    Myntos's own are in the ``MVA`` journal; a term the previous program
    settled came across in the ``IMPORT`` journal with the settlement's
    voucher type. Both move the term's VAT to be paid, and neither changes what
    the VAT was. ``voucher_type`` is NULL on old vouchers, which a plain ``!=``
    would drop.
    """
    return (
        Journal.journal_id != SETTLEMENT_JOURNAL_ID,
        or_(
            Transaction.voucher_type.is_(None),
            Transaction.voucher_type != SETTLEMENT_VOUCHER_TYPE,
        ),
    )


def _movement_query(
    company_id: int, accounts: ColumnElement[bool]
) -> Select[tuple[str | None, Decimal, Decimal, int]]:
    """Debit and credit totals on the accounts ``accounts`` picks, grouped by voucher type.

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
        .where(
            Journal.company_id == company_id,
            accounts,
            # Money in and out *this year*. The opening balance on 1920 is a
            # position carried in, not a movement; the cash position below,
            # which is a position, keeps it.
            Journal.journal_id != OPENING_JOURNAL_ID,
            _still_stands(),
        )
        .group_by(Transaction.voucher_type)
    )


async def _balance(
    db: AsyncSession, company: Company, accounts: ColumnElement[bool]
) -> tuple[Decimal | None, Decimal | None]:
    """Debit and credit across every year on the accounts ``accounts`` picks.

    The opening balance included, since a position starts from it; cancelled
    pairs left out, though they net to nothing here anyway (:func:`_still_stands`).
    """
    rows = list(
        await db.execute(
            select(func.sum(TransactionLine.debit), func.sum(TransactionLine.credit))
            .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
            .join(Journal, Journal.id == Transaction.journal_id)
            .join(Account, Account.id == TransactionLine.account_id)
            .where(Journal.company_id == company.id, accounts, _still_stands())
        )
    )
    if not rows:
        return None, None
    debit, credit = rows[0]
    return debit, credit


async def summarise(db: AsyncSession, *, company: Company, financial_year: int) -> CashFlowSummary:
    """Aggregate one financial year of the register.

    Summed in SQL over the bank and VAT accounts rather than over the
    ``CashFlowView`` projection: the totals must stay right when the register
    holds more entries than a request should ever load.

    "The bank" is every account that counts as the company's money
    (:func:`myntos.domain.ledger.kontoplan.counts_as_bank`): 1920, 1900, and
    any account marked as a bank account. A farm brought over from Tripletex
    keeps its only bank account, a driftskreditt, on 2390, and counted on 1920
    alone its overview read «Inn i år 0,00» beside a year of bank lines.
    """
    bank = kontoplan.bank_accounts_clause()
    payments = list(
        await db.execute(
            _movement_query(company.id, bank).where(Transaction.financial_year == financial_year)
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
        # Cancelled vouchers are already filtered out, so a type falls on the
        # side its direction implies. Netting anyway rather than trusting that:
        # the figure shown is the movement, whichever way it turns out to run.
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
                # A settled period's VAT is still the year's VAT. The settlement
                # moves it to 2740 to be paid, and counting that move here would
                # show the year's VAT as nothing once every term is filed.
                *not_a_settlement(),
                _still_stands(),
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
    cash_debit, cash_credit = await _balance(db, company, bank)

    # What is owed now, which is what «anslått å betale» under the card reads
    # as. It used to be the year's output less input VAT above, settlements
    # left out, and that was wrong in the commonest case: a farm whose previous
    # program had filed and settled most of the year's terms showed a large
    # amount «til gode» while the term still open was owed. The balance across
    # every year on 2700, 2710 and 2740, opening balance included, is the open
    # terms plus the filed ones not yet paid. The old worry, that a company
    # which has filed would show as owing nothing, is answered by 2740: a
    # settlement moves the term there, and only the payment clears it.
    vat_debit, vat_credit = await _balance(
        db, company, Account.account_number.in_(OWED_VAT_ACCOUNTS)
    )
    # The filed part of it, which the overview has to tell apart: a term filed
    # and settled stays on 2740 until its payment is posted, and a company that
    # brought only last year over shows last year's final term there months
    # after it was paid, because the payment is in a year Myntos has not got.
    settled_debit, settled_credit = await _balance(
        db, company, Account.account_number == VAT_SETTLEMENT_ACCOUNT
    )
    # Where the books stop. Every voucher counts, the old program's included:
    # «Inn i år 0,00» beside a 31.12 last year says the year has not been
    # brought over yet, not that nothing happened.
    books_through = await db.scalar(
        select(func.max(Transaction.transaction_date))
        .join(Journal, Journal.id == Transaction.journal_id)
        .where(Journal.company_id == company.id, Transaction.posted_at.is_not(None))
    )

    return CashFlowSummary(
        financial_year=financial_year,
        total_in=total_in,
        total_out=total_out,
        net=total_in - total_out,
        entry_count=entry_count,
        output_vat=output_vat,
        input_vat=input_vat,
        # A liability: positive is owed to Skatteetaten.
        vat_position=(vat_credit or ZERO) - (vat_debit or ZERO),
        vat_settled=(settled_credit or ZERO) - (settled_debit or ZERO),
        cash_position=(cash_debit or ZERO) - (cash_credit or ZERO),
        books_through=books_through,
        by_type=sorted(by_type, key=lambda t: t.total, reverse=True),
    )
