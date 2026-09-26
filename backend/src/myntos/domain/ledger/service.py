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

from sqlalchemy import ColumnElement, Select, and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from myntos.core.clock import utc_now
from myntos.domain.identity.models import (
    CompanyMembership,
    Organization,
    OrganizationRole,
    User,
)
from myntos.domain.ledger import cashflow, parties, posting
from myntos.domain.ledger.bankkonto import format_bank_account, validate_bank_account
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
    TAX_CODES_BY_CODE,
)
from myntos.domain.ledger.errors import (
    AlreadyReversedError,
    BankAccountNotFoundError,
    ClosedFinancialYearError,
    CompanyAlreadyClaimedError,
    CompanyNotFoundError,
    CompanyOrgnrLockedError,
    DuplicateBankAccountError,
    DuplicateCompanyError,
    InvalidAmountError,
    NotAuthorizedForPartyError,
    PeriodOutsideBooksError,
    PrimaryBankAccountRequiredError,
    UnknownTaxCodeError,
    VoucherNotFoundError,
)
from myntos.domain.ledger.models import (
    Account,
    AccountGrouping,
    Company,
    CompanyBankAccount,
    CompanyRegisterSnapshot,
    FinancialYear,
    Journal,
    TaxCode,
    TaxRate,
    Transaction,
    TransactionLine,
)
from myntos.domain.ledger.orgnr import validate_orgnr
from myntos.integrations.brreg import RegisteredUnit

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
        company.vat_registered = vat_registered
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
# One confirmed claim exists per orgnr across the installation. That is the rule
# the message in CompanyAlreadyClaimedError describes, and it is deliberately
# narrower than "one set of books per orgnr" — an accountant may still keep
# unconfirmed books for a client whose own confirmation sits elsewhere.


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
) -> Company:
    """Record that somebody proved through Altinn that these books are theirs.

    ``authorized_parties`` maps orgnr to Altinn's party id, straight from the
    person's own authorized-parties list. Passed in rather than fetched here
    because the fetch needs a government token, which is an API-layer concern —
    the rule this function owns is what may be done with the answer.

    Re-confirming a company that is already confirmed is allowed and is a no-op
    on the timestamp: the claim was already established, and a second BankID
    should not look like a failure.
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

    holder = await db.scalar(
        select(Company.id).where(
            Company.orgnr == company.orgnr,
            Company.verified_at.is_not(None),
            Company.id != company.id,
        )
    )
    if holder is not None:
        raise CompanyAlreadyClaimedError(
            "Selskapet har allerede et regnskap registrert. "
            "Be en administrator der om tilgang, eller opprett et nytt."
        )

    company.verified_at = utc_now()
    company.verified_by_user_id = user_id
    company.altinn_party_id = party_id
    try:
        await db.flush()
    except IntegrityError as error:
        # Two confirmations of the same orgnr landing at once. The partial
        # unique index is what makes the loser lose; this turns it into the
        # same answer the check above would have given.
        raise CompanyAlreadyClaimedError(
            "Selskapet har allerede et regnskap registrert. "
            "Be en administrator der om tilgang, eller opprett et nytt."
        ) from error
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
    """
    rows = await db.execute(
        select(Company, CompanyMembership)
        .join(CompanyMembership, CompanyMembership.company_id == Company.id)
        .where(CompanyMembership.user_id == user_id)
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
    """
    row = (
        await db.execute(
            select(Company, CompanyMembership)
            .join(CompanyMembership, CompanyMembership.company_id == Company.id)
            .where(Company.id == company_id, CompanyMembership.user_id == user_id)
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
    corrects_pk: int | None = None,
    created_by_user_id: int | None = None,
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
    seed = TAX_CODES_BY_CODE.get(code)
    reverse_charge = seed is not None and seed.reverse_charge
    if reverse_charge:
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

    if vat > ZERO and reverse_charge:
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
                record_id="3",
                account=accounts[OUTPUT_VAT_ACCOUNT],
                description=f"Beregnet mva ved innførsel, kode {code}",
                debit=vat if undone else ZERO,
                credit=ZERO if undone else vat,
            )
        )
        if seed is not None and seed.deductible:
            lines.append(
                TransactionLine(
                    record_id="4",
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
                    record_id="4",
                    account=accounts[entry_type.account_number],
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
                record_id="3",
                account=accounts[OUTPUT_VAT_ACCOUNT if owed_on_a_sale else INPUT_VAT_ACCOUNT],
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
        corrects_transaction_pk=corrects_pk,
        created_by_user_id=created_by_user_id,
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


def describe(
    transaction: Transaction,
    *,
    reversed_by_id: int | None = None,
    created_by_name: str | None = None,
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
    authors = await _author_names(db, [t.created_by_user_id for t in transactions])
    views = [
        describe(
            t,
            reversed_by_id=reversals.get(t.id),
            created_by_name=(
                None if t.created_by_user_id is None else authors.get(t.created_by_user_id)
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
        .where(
            Journal.company_id == company_id,
            Account.account_number.in_(account_numbers),
            _still_stands(),
        )
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
    cash_rows = list(
        await db.execute(
            select(func.sum(TransactionLine.debit), func.sum(TransactionLine.credit))
            .join(Transaction, Transaction.id == TransactionLine.transaction_pk)
            .join(Journal, Journal.id == Transaction.journal_id)
            .join(Account, Account.id == TransactionLine.account_id)
            .where(
                Journal.company_id == company.id,
                Account.account_number.in_(PAYMENT_ACCOUNTS),
                _still_stands(),
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
