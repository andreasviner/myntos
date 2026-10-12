"""Editing the kontoplan: adding accounts, renaming, deactivating, grouping.

The starter chart (:mod:`.chart`) is enough to record a first payment, and
nothing more. A business with a van, a second bank account or a loan from its
owner needs accounts the seed does not have, and until this module it had
nowhere to put them.

Four rules carry the weight, each with the failure it prevents:

1. **The number is NS 4102 and is checked by class.** Four digits, 1000-8999,
   and the account type follows from the first digit (1 eiendel, 2 egenkapital
   or gjeld, 3 inntekt, 4-7 kostnad, 8 finans). Class 9 is internal in NS 4102
   and never reaches a SAF-T file. A "kostnad" in class 1 is how a purchase
   ends up on the balance sheet and out of the næringsspesifikasjon, which is
   exactly the mistake capitalising 1250 was built to avoid in the other
   direction. The number never changes once created: every posting points at
   the account, and SAF-T exports it by number.
2. **An account with history is never deleted.** Postings, an opening balance
   or a customer settling against it each make it part of a year somebody may
   be asked to export. Deactivating is the alternative and hides it from new
   entries only (:attr:`~.models.Account.is_active`). Deleting is offered for
   the one case that has no history at all: an account added by mistake.
3. **Some accounts cannot be switched off.** The bank and cash accounts, the
   VAT accounts, the settlement account, equity, and the two party balance
   accounts are referred to by number in the posting logic, the MVA settlement
   and the SAF-T derivation. Switched off, the register would refuse every
   payment while the reason sat on another page.
4. **A grouping comes from the published list, per year, and a closed year is
   not edited.** Every pair is checked against Skatteetaten's list
   (:mod:`myntos.saft.codes`), so the SAF-T check keeps passing. A **closed**
   year is refused: its næringsspesifikasjon has been filed from those
   groupings, and a SAF-T file for it that disagreed with the filing would have
   nothing in it to say why. If a filed mapping was wrong, reopening the year is
   the explicit act that comes first; this module does not offer a quiet one.
   Myntos does not record the skattemelding itself, so "closed" is the honest
   proxy for "filed".

A person's choice is recorded (``chosen_at``, ``chosen_by_user_id``) and carries
forward into the next year (:func:`grouping_for_new_year`). That is also what
retires the assumption migration ``b7d40e2c9a15`` made, that every grouping row
was the seed's: a later correction of seeded rows must skip chosen ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.core.clock import utc_now
from myntos.domain.arsoppgjor import closing
from myntos.domain.ledger.chart import (
    ACCOUNTS_BY_NUMBER,
    BANK_ACCOUNT,
    CASH_ACCOUNT,
    INPUT_VAT_ACCOUNT,
    OUTPUT_VAT_ACCOUNT,
    PAYABLES_ACCOUNT,
    RECEIVABLES_ACCOUNT,
)
from myntos.domain.ledger.errors import (
    AccountInUseError,
    AccountNotFoundError,
    ClosedFinancialYearError,
    DuplicateAccountError,
    InvalidAccountNumberError,
    InvalidGroupingError,
    PeriodOutsideBooksError,
)
from myntos.domain.ledger.models import (
    GROUPING_NOT_APPLICABLE,
    Account,
    AccountGrouping,
    AccountOpeningBalance,
    AccountType,
    Company,
    FinancialYear,
    Party,
    TransactionLine,
)
from myntos.saft import codes

#: Accounts the code refers to by number, and which therefore stay active.
#: 2740 is where a filed melding is settled (myntos.domain.mva.service) and 2050
#: is where SAF-T's derived opening balance puts an undisposed result
#: (myntos.saft.service); both are named there rather than chosen. The
#: year-end close posts its tax and disposition to accounts it names too, so a
#: deactivated one would make the next årsavslutning fail halfway through a
#: year somebody is trying to close.
LOCKED_ACCOUNTS: frozenset[str] = frozenset(
    {
        BANK_ACCOUNT,
        CASH_ACCOUNT,
        OUTPUT_VAT_ACCOUNT,
        INPUT_VAT_ACCOUNT,
        RECEIVABLES_ACCOUNT,
        PAYABLES_ACCOUNT,
        "2740",
        "2050",
        closing.UNCOVERED_LOSS,
        closing.PRIVATE_ACCOUNT,
        closing.TAX_PAYABLE,
        closing.TAX_COST,
        closing.ENK_RESULT,
        closing.AS_TRANSFER,
        closing.AS_UNCOVERED,
    }
)

#: Which account types each NS 4102 class may hold. Class 2 holds an asset too,
#: because input VAT (2710) is one and sits among the VAT accounts by
#: convention; class 8 holds both finance income and finance cost.
_TYPES_BY_CLASS: dict[str, frozenset[AccountType]] = {
    "1": frozenset({AccountType.EIENDEL}),
    "2": frozenset({AccountType.EGENKAPITAL, AccountType.GJELD, AccountType.EIENDEL}),
    "3": frozenset({AccountType.INNTEKT}),
    "4": frozenset({AccountType.KOSTNAD}),
    "5": frozenset({AccountType.KOSTNAD}),
    "6": frozenset({AccountType.KOSTNAD}),
    "7": frozenset({AccountType.KOSTNAD}),
    "8": frozenset({AccountType.INNTEKT, AccountType.KOSTNAD}),
}

#: The categories a default is drawn from. The list also carries posts for
#: banks and insurers and for IFRS reporters, under the same numbers as the
#: ordinary ones; an ordinary business defaulted onto
#: ``bankOgForsikringskostnad`` would file a næringsspesifikasjon for a
#: different kind of company.
_ORDINARY_CATEGORIES: frozenset[str] = frozenset(
    {
        "balanseverdiForAnleggsmiddel",
        "balanseverdiForOmloepsmiddel",
        "egenkapital",
        "langsiktigGjeld",
        "kortsiktigGjeld",
        "salgsinntekt",
        "annenDriftsinntekt",
        "varekostnad",
        "loennskostnad",
        "annenDriftskostnad",
        "finansinntekt",
        "finanskostnad",
        "skattekostnad",
        "resultatDisponeringForSAF-T",
    }
)


#: Words marking a post no ordinary business would mean by default.
_SPECIALISED: tuple[str, ...] = (
    "petroleum",
    "olje",
    "gass",
    "rørledning",
    "konsern",
    "viderefakturert",
    "tømmer",
    "jordbruk",
    "ifrs",
)


def validate_number(number: str) -> str:
    """A four-digit NS 4102 account number in classes 1-8, or a refusal."""
    cleaned = number.strip()
    if len(cleaned) != 4 or not cleaned.isdigit():
        raise InvalidAccountNumberError("kontonummeret må være fire siffer, som i NS 4102")
    if cleaned[0] not in _TYPES_BY_CLASS:
        raise InvalidAccountNumberError(
            "kontoklasse 1 til 8 kan brukes; klasse 9 er til intern bruk og hører ikke "
            "hjemme i hovedboken"
        )
    return cleaned


def default_type(number: str) -> AccountType:
    """The account type an NS 4102 number implies."""
    first = number[0]
    if first == "1":
        return AccountType.EIENDEL
    if first == "2":
        return AccountType.EGENKAPITAL if number.startswith("20") else AccountType.GJELD
    if first == "3":
        return AccountType.INNTEKT
    if first == "8":
        # 80xx is finance income; 81xx onwards is finance cost, tax and the
        # disposition of the result, all of which reduce it.
        return AccountType.INNTEKT if number.startswith("80") else AccountType.KOSTNAD
    return AccountType.KOSTNAD


def check_type(number: str, account_type: AccountType) -> None:
    if account_type not in _TYPES_BY_CLASS[number[0]]:
        raise InvalidAccountNumberError(
            f"en konto i klasse {number[0]} kan ikke være «{account_type.value}»"
        )


def default_grouping(number: str, year: int) -> tuple[str, str]:
    """The næringsspesifikasjon pair to propose for an account, by its number.

    In order, and the order is the judgement:

    1. The seed's own pair for a seeded number.
    2. The post in the year's list with the same number, which is common
       because the list was built on NS 4102: a 2770 lands on «Skyldig
       arbeidsgiveravgift», a 3400 on «Offentlige tilskudd og refusjoner».
    3. The seeded account nearest below it in the same NS 4102 group (the
       first two digits), because the seed's pairs were each chosen by reading
       the list, and a 6820 is far more likely another IT or office cost like
       6810 than whatever post happens to sit just below it in the list.
    4. The nearest post below it in the same group, then in the same class.

    Posts that only a petroleum company, a group or a reinvoicing arrangement
    could mean are never proposed: «nearest below» found «Kostnadsreduksjon
    annen kostnad viderefakturert» for a hosting account the first time this
    ran. It is a proposal shown next to the choice and it can still be wrong; a
    person picking it is the point of showing it.
    """
    seeded = ACCOUNTS_BY_NUMBER.get(number)
    if seeded is not None:
        return seeded.grouping_category, seeded.grouping_code
    candidates = sorted(
        (entry.code, entry.category)
        for entry in codes.grouping_entries(year)
        if entry.category in _ORDINARY_CATEGORIES
        and len(entry.code) == 4
        and entry.code.isdigit()
        and entry.code[0] == number[0]
        and not any(word in entry.description.lower() for word in _SPECIALISED)
    )
    exact = [candidate for candidate in candidates if candidate[0] == number]
    if exact:
        return exact[0][1], exact[0][0]
    group = number[:2]
    neighbours = sorted(
        seed.number
        for seed in ACCOUNTS_BY_NUMBER.values()
        if seed.number[:2] == group and seed.number < number
    )
    if neighbours:
        neighbour = ACCOUNTS_BY_NUMBER[neighbours[-1]]
        return neighbour.grouping_category, neighbour.grouping_code
    for pool in (
        [candidate for candidate in candidates if candidate[0][:2] == group],
        candidates,
    ):
        below = [candidate for candidate in pool if candidate[0] <= number]
        if below:
            code, category = below[-1]
            return category, code
    if candidates:
        code, category = candidates[0]
        return category, code
    return GROUPING_NOT_APPLICABLE, GROUPING_NOT_APPLICABLE


def check_grouping(year: int, category: str, code: str) -> None:
    if not codes.is_choosable_grouping(year, category, code):
        raise InvalidGroupingError(
            f"{category} {code} finnes ikke i Skatteetatens liste over "
            f"næringsspesifikasjonsposter for {codes.latest_published_year(year)}"
        )


def grouping_for_new_year(
    account: Account, previous: AccountGrouping | None, year: int
) -> AccountGrouping:
    """The grouping row a newly opened year gives ``account``.

    A pair a person chose carries forward, with who chose it: they decided
    what this account means in the næringsspesifikasjon, and a new year is not
    a reason to quietly undo it. A seeded account otherwise takes the seed's
    current pair, which is how a corrected seed reaches the next year. An
    account added by hand and never re-grouped keeps its previous pair, and
    one with no history at all gets the default by number.

    Used by :func:`myntos.domain.ledger.service._open_financial_year`, which
    used to look every account up in the seed and would have raised
    StopIteration on the first account a person added.
    """
    seeded = ACCOUNTS_BY_NUMBER.get(account.account_number)
    if previous is not None and previous.chosen_at is not None:
        return AccountGrouping(
            account_id=account.id,
            financial_year=year,
            grouping_category=previous.grouping_category,
            grouping_code=previous.grouping_code,
            chosen_at=previous.chosen_at,
            chosen_by_user_id=previous.chosen_by_user_id,
        )
    if seeded is not None:
        category, code = seeded.grouping_category, seeded.grouping_code
    elif previous is not None:
        category, code = previous.grouping_category, previous.grouping_code
    else:
        category, code = default_grouping(account.account_number, year)
    return AccountGrouping(
        account_id=account.id,
        financial_year=year,
        grouping_category=category,
        grouping_code=code,
    )


# --- Reading --------------------------------------------------------------------


@dataclass(frozen=True)
class AccountView:
    id: int
    number: str
    description: str
    account_type: AccountType
    is_active: bool
    # Referred to by number in the code; cannot be deactivated or deleted.
    is_locked: bool
    # Whether anything has been posted to it, ever. Decides delete versus
    # deactivate, and the page says which.
    has_postings: bool
    created_date: date | None
    grouping_category: str | None
    grouping_code: str | None
    grouping_chosen_at: datetime | None


async def get_account(db: AsyncSession, *, company: Company, account_pk: int) -> Account:
    """One of this company's accounts, by id and company, never by id alone."""
    account = await db.scalar(
        select(Account).where(Account.id == account_pk, Account.company_id == company.id)
    )
    if account is None:
        raise AccountNotFoundError("finner ikke kontoen i kontoplanen")
    return account


async def _year(db: AsyncSession, company: Company, year: int) -> FinancialYear:
    found = await db.scalar(
        select(FinancialYear).where(
            FinancialYear.company_id == company.id, FinancialYear.year == year
        )
    )
    if found is None:
        raise PeriodOutsideBooksError(f"selskapet har ikke noe regnskapsår {year} i Myntos")
    return found


async def is_closed(db: AsyncSession, *, company: Company, year: int) -> bool:
    return (await _year(db, company, year)).closed_at is not None


async def _posted_account_ids(db: AsyncSession, company: Company) -> set[int]:
    rows = await db.scalars(
        select(TransactionLine.account_id)
        .join(Account, Account.id == TransactionLine.account_id)
        .where(Account.company_id == company.id)
        .distinct()
    )
    return set(rows)


async def list_accounts(db: AsyncSession, *, company: Company, year: int) -> list[AccountView]:
    """The whole kontoplan, with each account's grouping for ``year``."""
    await _year(db, company, year)
    accounts = list(
        await db.scalars(
            select(Account).where(Account.company_id == company.id).order_by(Account.account_number)
        )
    )
    groupings = {
        grouping.account_id: grouping
        for grouping in await db.scalars(
            select(AccountGrouping)
            .join(Account, Account.id == AccountGrouping.account_id)
            .where(Account.company_id == company.id, AccountGrouping.financial_year == year)
        )
    }
    posted = await _posted_account_ids(db, company)
    views: list[AccountView] = []
    for account in accounts:
        grouping = groupings.get(account.id)
        views.append(
            AccountView(
                id=account.id,
                number=account.account_number,
                description=account.description,
                account_type=account.account_type,
                is_active=account.is_active,
                is_locked=account.account_number in LOCKED_ACCOUNTS,
                has_postings=account.id in posted,
                created_date=account.created_date,
                grouping_category=grouping.grouping_category if grouping else None,
                grouping_code=grouping.grouping_code if grouping else None,
                grouping_chosen_at=grouping.chosen_at if grouping else None,
            )
        )
    return views


# --- Writing --------------------------------------------------------------------


async def create_account(
    db: AsyncSession,
    *,
    company: Company,
    number: str,
    description: str,
    account_type: AccountType | None = None,
    grouping: tuple[str, str] | None = None,
    created_by_user_id: int | None = None,
    today: date | None = None,
) -> Account:
    """Add an account, grouped for every year still open.

    ``grouping`` is the person's choice for the open years; without one each
    year gets :func:`default_grouping`. Closed years get nothing: the account
    did not exist then, no posting can reach a closed year, and the SAF-T
    export leaves such an account out of that year's file rather than writing a
    grouping into a year whose filing has already been made.
    """
    cleaned = validate_number(number)
    chosen_type = account_type or default_type(cleaned)
    check_type(cleaned, chosen_type)
    text = description.strip()
    if not text:
        raise InvalidAccountNumberError("kontoen må ha et navn")
    existing = await db.scalar(
        select(Account.id).where(
            Account.company_id == company.id, Account.account_number == cleaned
        )
    )
    if existing is not None:
        raise DuplicateAccountError(f"kontoplanen har allerede konto {cleaned}")

    open_years = list(
        await db.scalars(
            select(FinancialYear.year).where(
                FinancialYear.company_id == company.id, FinancialYear.closed_at.is_(None)
            )
        )
    )
    if grouping is not None:
        for year in open_years:
            check_grouping(year, *grouping)

    account = Account(
        company_id=company.id,
        account_number=cleaned,
        description=text,
        account_type=chosen_type,
        created_date=today or utc_now().date(),
        is_active=True,
    )
    db.add(account)
    await db.flush()
    chosen_at = utc_now() if grouping is not None else None
    for year in open_years:
        category, code = grouping if grouping is not None else default_grouping(cleaned, year)
        db.add(
            AccountGrouping(
                account_id=account.id,
                financial_year=year,
                grouping_category=category,
                grouping_code=code,
                chosen_at=chosen_at,
                chosen_by_user_id=created_by_user_id if grouping is not None else None,
            )
        )
    await db.flush()
    return account


async def ensure_seeded_account(db: AsyncSession, *, company: Company, number: str) -> Account:
    """The account with ``number``, created from the seed when it is missing.

    For an account that joined the starter chart after the company was set up
    (7830, for tap på krav). Only for seeded numbers: anything else is the
    person's to create, under the name they choose.
    """
    account = await db.scalar(
        select(Account).where(Account.company_id == company.id, Account.account_number == number)
    )
    if account is not None:
        return account
    seed = ACCOUNTS_BY_NUMBER[number]
    return await create_account(
        db,
        company=company,
        number=seed.number,
        description=seed.description,
        account_type=seed.account_type,
    )


async def _history(db: AsyncSession, account: Account) -> str | None:
    """Why ``account`` cannot be deleted, or ``None`` when nothing refers to it."""
    if await db.scalar(
        select(func.count())
        .select_from(TransactionLine)
        .where(TransactionLine.account_id == account.id)
    ):
        return "det er ført på den"
    if await db.scalar(
        select(func.count())
        .select_from(AccountOpeningBalance)
        .where(AccountOpeningBalance.account_id == account.id)
    ):
        return "den har en inngående balanse"
    if await db.scalar(
        select(func.count()).select_from(Party).where(Party.balance_account_id == account.id)
    ):
        return "en kunde eller leverandør avregnes mot den"
    return None


async def update_account(
    db: AsyncSession,
    *,
    company: Company,
    account_pk: int,
    description: str | None = None,
    is_active: bool | None = None,
) -> Account:
    """Rename or (de)activate an account. The number and type do not change.

    A rename is allowed on any account, posted to or not. The description is
    master data, not a posting: a voucher points at the account, and SAF-T
    exports the account's current name for every year, as any kontoplan does.
    What a rename must not do is change what the account *is*, and that is the
    number and the type, which is why neither is here.
    """
    account = await get_account(db, company=company, account_pk=account_pk)
    if description is not None:
        text = description.strip()
        if not text:
            raise InvalidAccountNumberError("kontoen må ha et navn")
        account.description = text
    if is_active is not None and is_active != account.is_active:
        if not is_active and account.account_number in LOCKED_ACCOUNTS:
            raise AccountInUseError(
                f"konto {account.account_number} brukes av Myntos selv og kan ikke deaktiveres"
            )
        account.is_active = is_active
    await db.flush()
    return account


async def delete_account(db: AsyncSession, *, company: Company, account_pk: int) -> None:
    """Remove an account that has no history at all; refuse every other."""
    account = await get_account(db, company=company, account_pk=account_pk)
    if account.account_number in LOCKED_ACCOUNTS:
        raise AccountInUseError(
            f"konto {account.account_number} brukes av Myntos selv og kan ikke slettes"
        )
    reason = await _history(db, account)
    if reason is not None:
        raise AccountInUseError(
            f"konto {account.account_number} kan ikke slettes fordi {reason}. "
            "Deaktiver den i stedet, så forsvinner den fra nye føringer"
        )
    await db.delete(account)
    await db.flush()


async def set_grouping(
    db: AsyncSession,
    *,
    company: Company,
    account_pk: int,
    year: int,
    category: str,
    code: str,
    chosen_by_user_id: int | None,
) -> AccountGrouping:
    """Choose an account's næringsspesifikasjon post for one open year."""
    account = await get_account(db, company=company, account_pk=account_pk)
    financial_year = await _year(db, company, year)
    if financial_year.closed_at is not None:
        raise ClosedFinancialYearError(
            f"regnskapsåret {year} er avsluttet, og næringsspesifikasjonen for det er levert "
            "med grupperingene det hadde. Åpne året igjen før du endrer dem"
        )
    check_grouping(year, category, code)
    grouping = await db.scalar(
        select(AccountGrouping).where(
            AccountGrouping.account_id == account.id, AccountGrouping.financial_year == year
        )
    )
    if grouping is None:
        grouping = AccountGrouping(account_id=account.id, financial_year=year)
        db.add(grouping)
    grouping.grouping_category = category
    grouping.grouping_code = code
    grouping.chosen_at = utc_now()
    grouping.chosen_by_user_id = chosen_by_user_id
    await db.flush()
    return grouping
