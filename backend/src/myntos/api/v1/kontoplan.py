"""The kontoplan: list, add, rename, deactivate, delete, and group per year.

Reading needs ``ledger:read``; every change needs ``ledger:chart``, which only
whoever administers the books holds (see :mod:`myntos.domain.identity.access`
for why it is not ``ledger:write``). The rules themselves are in
:mod:`myntos.domain.ledger.kontoplan`; this module maps HTTP onto them.
"""

from datetime import date, datetime

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, Field

from myntos.api.deps import KEY_READ, CurrentCompany, CurrentUser, DbSession, key_write
from myntos.core.clock import utc_now
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.ledger import kontoplan
from myntos.domain.ledger.models import AccountType
from myntos.saft import codes

router = APIRouter(tags=["kontoplan"])


class AccountResponse(BaseModel):
    id: int
    number: str
    description: str
    account_type: AccountType
    is_active: bool
    # Used by Myntos itself; cannot be deactivated or deleted.
    is_locked: bool
    # Anything ever posted to it. Decides whether delete is offered at all.
    has_postings: bool
    created_date: date | None
    # The næringsspesifikasjon post for the year asked about. Null only for an
    # account added after that year was closed.
    grouping_category: str | None
    grouping_code: str | None
    # Set when a person chose the post; null when the seed or the default did.
    grouping_chosen_at: datetime | None


class GroupingOption(BaseModel):
    category: str
    code: str
    description: str


class KontoplanResponse(BaseModel):
    year: int
    # Whether the year is closed, in which case no grouping may change.
    closed: bool
    # The year whose published list the options come from. Equal to ``year``
    # whenever Skatteetaten has published one for it.
    list_year: int
    accounts: list[AccountResponse]
    groupings: list[GroupingOption]


class CreateAccountRequest(BaseModel):
    number: str = Field(min_length=4, max_length=4)
    description: str = Field(min_length=1, max_length=255)
    # Follows from the number when left out; see kontoplan.default_type.
    account_type: AccountType | None = None
    # Both or neither. Left out, each open year gets the default by number.
    grouping_category: str | None = Field(default=None, max_length=64)
    grouping_code: str | None = Field(default=None, max_length=64)


class UpdateAccountRequest(BaseModel):
    description: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None


class GroupingRequest(BaseModel):
    year: int = Field(ge=2000, le=2100)
    grouping_category: str = Field(min_length=1, max_length=64)
    grouping_code: str = Field(min_length=1, max_length=64)


class DefaultGroupingResponse(BaseModel):
    account_type: AccountType
    grouping_category: str
    grouping_code: str


async def _response(db: DbSession, context: CurrentCompany, year: int) -> KontoplanResponse:
    views = await kontoplan.list_accounts(db, company=context.company, year=year)
    closed = await kontoplan.is_closed(db, company=context.company, year=year)
    return KontoplanResponse(
        year=year,
        closed=closed,
        list_year=codes.latest_published_year(year),
        accounts=[
            AccountResponse(
                id=view.id,
                number=view.number,
                description=view.description,
                account_type=view.account_type,
                is_active=view.is_active,
                is_locked=view.is_locked,
                has_postings=view.has_postings,
                created_date=view.created_date,
                grouping_category=view.grouping_category,
                grouping_code=view.grouping_code,
                grouping_chosen_at=view.grouping_chosen_at,
            )
            for view in views
        ],
        groupings=[
            GroupingOption(category=entry.category, code=entry.code, description=entry.description)
            for entry in codes.grouping_entries(year)
        ],
    )


@router.get("/companies/{company_id}/kontoplan", openapi_extra=KEY_READ)
async def get_kontoplan(
    context: CurrentCompany, db: DbSession, aar: int | None = Query(default=None)
) -> KontoplanResponse:
    """The company's chart of accounts (kontoplan), with each account's grouping for one year.

    Every amount in the books is posted to an account, named by a four-digit
    number from the Norwegian standard chart NS 4102. The first digit is the
    class: 1 assets (eiendeler), 2 equity and liabilities (egenkapital, gjeld),
    3 income (inntekter), 4 to 7 costs (kostnader), 8 financial items, tax and
    the result. The list holds every account the company has, deactivated
    ones included, ordered by number.

    Per account: ``number`` (never changes once created), ``description``
    (the name, which may be changed), ``account_type``, ``is_active`` (a
    deactivated account is kept for the years that used it and offered for
    nothing new), ``is_locked`` (Myntos itself posts to it by number, such as
    the bank account 1920 and the VAT accounts, so it can be renamed but never
    deactivated or deleted), and ``has_postings`` (anything was ever posted to
    it; such an account can never be deleted, only deactivated).

    ``grouping_category`` and ``grouping_code`` say which post of the
    næringsspesifikasjon the account is reported under in year ``aar``. The
    næringsspesifikasjon is the itemised statement of the business's figures
    that accompanies the tax return (skattemelding); the same pair is written
    into the SAF-T export. ``grouping_chosen_at`` is set when a person chose
    the post and null when Myntos's default stands; both are null for an
    account added after that year was closed. ``groupings`` lists every pair
    that may be chosen for the year, from Skatteetaten's published list for
    ``list_year`` (the latest published list at or before ``aar``).
    ``closed`` is true when the year has been closed, and then no grouping in
    it may change.

    ``aar`` is the financial year, defaulting to the current calendar year. A
    year the company has no financial year for in Myntos answers 422
    ``period_outside_books``. Needs ``ledger:read``, which every role has.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    return await _response(db, context, aar or utc_now().year)


@router.get("/companies/{company_id}/kontoplan/forslag", openapi_extra=KEY_READ)
async def suggest(
    context: CurrentCompany, nummer: str = Query(min_length=4, max_length=4), aar: int | None = None
) -> DefaultGroupingResponse:
    """What a new account with this number would be, before it is created."""
    require_permission(context.membership.role, Permission.LEDGER_READ)
    number = kontoplan.validate_number(nummer)
    category, code = kontoplan.default_grouping(number, aar or utc_now().year)
    return DefaultGroupingResponse(
        account_type=kontoplan.default_type(number),
        grouping_category=category,
        grouping_code=code,
    )


@router.post(
    "/companies/{company_id}/kontoplan",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_write("legge til en konto i kontoplanen"),
)
async def create_account(
    payload: CreateAccountRequest,
    context: CurrentCompany,
    db: DbSession,
    user: CurrentUser,
    aar: int | None = Query(default=None),
) -> KontoplanResponse:
    """Add an account to the kontoplan, and answer with the whole kontoplan for ``aar``.

    ``number`` is four digits from NS 4102, 1000 to 8999. Class 9 is refused:
    it is internal in the standard and never reaches a SAF-T file. The number
    can never be changed afterwards, because every posting points at the
    account and SAF-T exports it by number, so choose it beside similar
    accounts (hosting next to 6810 Datakostnad as 6820, say). ``account_type``
    follows from the number when left out and, when given, must fit the class:
    a cost (kostnad) in class 1 would put a purchase on the balance sheet and
    out of the tax return's figures.

    ``grouping_category`` and ``grouping_code`` go together. Left out, every
    open year gets the default by number, the same pair ``GET
    …/kontoplan/forslag`` shows. Given, the pair must be in Skatteetaten's list
    for every open year, and is recorded as the person's choice, which carries
    into later years. Closed years get no grouping: the account did not exist
    then, and their næringsspesifikasjon is already filed.

    Refusals: 409 ``duplicate_account`` when the number exists (perhaps
    deactivated; switch it back on with ``PATCH`` instead); 422
    ``invalid_account_number`` for a number that is not four digits, class 9,
    a type the class cannot hold, or an empty name; 422 ``invalid_grouping``
    for a pair not in the list; 422 ``period_outside_books`` when ``aar``
    names a year the company does not have, in which case nothing is created.

    Needs ``ledger:chart``, which administrators and owners hold and ordinary
    members do not (403 ``permission_denied``): an account changes what every
    member's entries can be booked to and what the tax return says the books
    contain. Undoable while nothing is posted to it: delete it again.
    """
    require_permission(context.membership.role, Permission.LEDGER_CHART)
    grouping = None
    if payload.grouping_category is not None and payload.grouping_code is not None:
        grouping = (payload.grouping_category, payload.grouping_code)
    await kontoplan.create_account(
        db,
        company=context.company,
        number=payload.number,
        description=payload.description,
        account_type=payload.account_type,
        grouping=grouping,
        created_by_user_id=user.id,
    )
    return await _response(db, context, aar or utc_now().year)


@router.patch(
    "/companies/{company_id}/kontoplan/{account_id}",
    openapi_extra=key_write("endre en konto i kontoplanen"),
)
async def update_account(
    account_id: int,
    payload: UpdateAccountRequest,
    context: CurrentCompany,
    db: DbSession,
    aar: int | None = Query(default=None),
) -> KontoplanResponse:
    """Rename an account, or switch it off or on again. Answers with the whole kontoplan.

    Send ``description`` to rename, ``is_active`` to deactivate (``false``) or
    reactivate (``true``), or both. Anything left out stays as it is.

    A rename is allowed on every account, posted to or not, including the
    ones Myntos uses itself. The name is master data rather than a posting,
    and SAF-T exports the account's current name for every year, as any
    kontoplan does. The number and the type never change, because they are
    what the account *is*: an account in the wrong class is replaced by
    adding the right one and deactivating the old.

    Deactivating hides the account from new entries only. Everything already
    posted to it stays, and it is still reported for the years that used it.
    A payment in a category whose account is deactivated is refused
    (``inactive_account``) until it is switched back on.

    Refusals: 409 ``account_in_use`` when deactivating an account Myntos posts
    to by number (``is_locked``: the bank and cash accounts, the VAT accounts,
    the customer and supplier balances, equity and the year-end accounts;
    switched off, every payment would be refused); 422
    ``invalid_account_number`` for an empty name; 404 ``account_not_found``
    for an account that is not this company's; 422 ``period_outside_books``
    when ``aar``, the year whose kontoplan is returned, is one the company
    does not have, in which case nothing is changed.

    Needs ``ledger:chart`` (administrators and owners; 403
    ``permission_denied`` otherwise), for the reason ``POST …/kontoplan``
    gives.
    """
    require_permission(context.membership.role, Permission.LEDGER_CHART)
    await kontoplan.update_account(
        db,
        company=context.company,
        account_pk=account_id,
        description=payload.description,
        is_active=payload.is_active,
    )
    return await _response(db, context, aar or utc_now().year)


@router.put(
    "/companies/{company_id}/kontoplan/{account_id}/gruppering",
    openapi_extra=key_write("endre grupperingen av en konto"),
)
async def set_grouping(
    account_id: int,
    payload: GroupingRequest,
    context: CurrentCompany,
    db: DbSession,
    user: CurrentUser,
) -> KontoplanResponse:
    """Choose which næringsspesifikasjon post an account is reported under, for one year.

    The næringsspesifikasjon is the itemised statement of the business's
    figures that goes with the tax return (skattemelding), and SAF-T carries
    the same mapping. Each account is mapped to one post per financial year.
    Myntos proposes one by the account's number; this records a person's
    choice instead, with who made it, and the choice carries into the next
    year when that year is opened.

    The body names the ``year`` and a ``grouping_category`` and
    ``grouping_code`` pair taken from ``groupings`` in ``GET
    …/kontoplan?aar=<year>``. ``NA``/``NA`` is also accepted: it is the
    placeholder Skatteetaten's list prescribes for an account with genuinely
    no post, and is rarely what an ordinary account needs. Answers with the
    whole kontoplan for that year.

    Refusals: 409 ``closed_financial_year`` when the year is closed, because
    its næringsspesifikasjon was filed with the groupings it had, and a SAF-T
    file that disagreed with the filing would have nothing in it to say why
    (reopening the year, on the year-end page, comes first); 422
    ``invalid_grouping`` for a pair not in the year's list; 422
    ``period_outside_books`` for a year the company does not have; 404
    ``account_not_found`` for an account that is not this company's.

    Needs ``ledger:chart`` (administrators and owners; 403
    ``permission_denied`` otherwise): the grouping decides what the tax
    return says, not just how one entry is booked.
    """
    require_permission(context.membership.role, Permission.LEDGER_CHART)
    await kontoplan.set_grouping(
        db,
        company=context.company,
        account_pk=account_id,
        year=payload.year,
        category=payload.grouping_category,
        code=payload.grouping_code,
        chosen_by_user_id=user.id,
    )
    return await _response(db, context, payload.year)


@router.delete(
    "/companies/{company_id}/kontoplan/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra=key_write("slette en konto fra kontoplanen"),
)
async def delete_account(account_id: int, context: CurrentCompany, db: DbSession) -> Response:
    """Delete an account added by mistake, one with no history at all. Answers 204.

    Only an account nothing refers to can go: no postings, no opening
    balance, no customer or supplier settling against it, and not one Myntos
    posts to by number. An account with any of those is part of a year
    somebody may be asked to account for or export, and is deactivated
    instead (``PATCH …/kontoplan/{account_id}`` with ``is_active: false``),
    which hides it from new entries and keeps the history readable.
    ``has_postings: false`` in the kontoplan is necessary but not enough: an
    opening balance or a party's balance account also keeps it.

    Refusals: 409 ``account_in_use``, whose ``detail`` says which of those
    reasons applies and suggests deactivating; 404 ``account_not_found`` for
    an account that is not this company's.

    Needs ``ledger:chart`` (administrators and owners; 403
    ``permission_denied`` otherwise). The number is free again afterwards,
    and adding the account back is a ``POST …/kontoplan``.
    """
    require_permission(context.membership.role, Permission.LEDGER_CHART)
    await kontoplan.delete_account(db, company=context.company, account_pk=account_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
