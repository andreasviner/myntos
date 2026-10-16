"""Deleting a company from Myntos, and opening deleted books again.

What deleting does is decided in :mod:`myntos.domain.identity.closure`, and
why in docs/gdpr.md under «Deleting a company»: books with nothing entered go
at once, books with anything entered are locked away until five years after
their last financial year (bokføringsloven § 13) and deleted the day after.

No key may call anything here (docs/agents-and-api.md rule 5: deleting a
company is never a key's). Deleting asks for the same proof as deleting the
account, because a stolen session must not be able to take a company's books
out of reach of the people who keep them.

Registered before :mod:`myntos.api.v1.companies`, because ``/companies/slettede``
would otherwise be read as ``/companies/{company_id}`` and refused as a bad id.
"""

from datetime import date, datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from myntos.api.deps import CurrentCompany, CurrentSession, CurrentUser, DbSession
from myntos.api.v1.companies import CompanyResponse, _company_response
from myntos.core.clock import as_utc
from myntos.domain.identity import closure, service
from myntos.domain.identity.access import Permission, require_permission

router = APIRouter(tags=["companies"])


class ClosurePlanResponse(BaseModel):
    """What deleting this company would do, for the page to say before it does."""

    # "deleted": nothing has been entered, so the books go at once.
    # "kept": something has, so the books are locked away until retain_until
    # and deleted for good the day after.
    outcome: str
    # The last day the books are kept. None when they are deleted at once.
    retain_until: date | None
    # The financial years with vouchers in them, oldest first. Download a SAF-T
    # file for each (GET /api/v1/companies/{company_id}/saft/fil?aar=…) before
    # deleting: it is the company's own copy of its entries.
    years: list[int]
    # People other than the caller whose access ends.
    members_leaving: int
    # Keys for agents and programs that stop for good.
    live_keys: int
    # How the caller proves it is them: a sign-in before recent_login_until
    # (always set here), or else the password when requires_password, or a
    # new sign-in through the provider when not.
    requires_password: bool
    recent_login_until: datetime | None


class CloseRequest(BaseModel):
    # The company's name, typed by the person: which books they meant. Compared
    # ignoring case and spacing.
    company_name: str = Field(max_length=255)
    # Required when the account has a password, ignored when it has none, as on
    # POST /account/erasure. An account without one (signed up with BankID,
    # Google or Microsoft) proves itself by having signed in within the last
    # ten minutes instead.
    password: str | None = Field(default=None, max_length=1024)


class CloseResponse(BaseModel):
    """What happened: deleted at once, or kept until a date."""

    outcome: str
    retain_until: date | None


class ClosedCompanyResponse(BaseModel):
    """A company the caller deleted from Myntos and still owns, while it is kept."""

    id: int
    orgnr: str
    name: str
    closed_at: datetime
    # Deleted for good the day after this.
    retain_until: date


def _plan_response(
    plan: closure.ClosurePlan, *, requires_password: bool, recent_login_until: datetime | None
) -> ClosurePlanResponse:
    return ClosurePlanResponse(
        outcome=plan.outcome.value,
        retain_until=plan.retain_until,
        years=plan.years,
        members_leaving=plan.members_leaving,
        live_keys=plan.live_keys,
        requires_password=requires_password,
        recent_login_until=recent_login_until,
    )


@router.get("/companies/slettede")
async def list_closed_companies(user: CurrentUser, db: DbSession) -> list[ClosedCompanyResponse]:
    """The companies the caller deleted from Myntos, while their books are still kept.

    Only companies the caller owns. Each is locked: nobody can read or write
    in it, it is in no other list, and every route under
    ``/companies/{company_id}`` answers 404 for it. Opening it again is
    ``POST /companies/slettede/{company_id}/gjenapne``.
    """
    return [
        ClosedCompanyResponse(
            id=company.id,
            orgnr=company.orgnr,
            name=company.name,
            closed_at=as_utc(company.closed_at),
            retain_until=company.retain_until,
        )
        for company in await closure.list_closed(db, user_id=user.id)
        if company.closed_at is not None and company.retain_until is not None
    ]


@router.post("/companies/slettede/{company_id}/gjenapne")
async def reopen_company(company_id: int, user: CurrentUser, db: DbSession) -> CompanyResponse:
    """Open a deleted company's books again, exactly as they were.

    For an owner, until the day the books are deleted for good. Only the books
    come back: colleagues who lost access are invited again, keys for agents
    are made again, and access for agents is switched on again.

    Refusals: 404 ``company_not_found`` for an id that is not a deleted company
    the caller owns (the same answer for every such id). 409
    ``reopen_blocked`` when new books for the same orgnr have been set up in
    the account since (delete those first), or another set of books has
    confirmed ownership of the orgnr since; the message says which.
    """
    company, membership = await closure.get_closed(db, company_id=company_id, user_id=user.id)
    await closure.reopen_company(db, company)
    return _company_response(company, membership)


@router.get("/companies/{company_id}/sletting")
async def closure_plan(
    context: CurrentCompany, user: CurrentUser, session: CurrentSession, db: DbSession
) -> ClosurePlanResponse:
    """Say what deleting the company would do, before asking.

    Norwegian law (bokføringsloven § 13) makes a company keep its books for
    five years after the end of each financial year: vouchers, bilag, filings
    and the annual accounts. So a company with anything entered is not deleted
    at once. It is locked away (out of every list, nobody can open it, every
    key stops) and deleted for good on the day after ``retain_until``, which is
    31 December five years after the last financial year with entries. A
    company with nothing entered is deleted at once.

    Owners only («Eier»); 403 ``permission_denied`` for anybody else.
    """
    require_permission(context.membership.role, Permission.ORGANIZATION_DELETE)
    plan = await closure.plan_closure(db, context.company, user_id=user.id)
    return _plan_response(
        plan,
        requires_password=user.has_password,
        # For every account, unlike the erasure plan: here a recent sign-in
        # is proof enough even where there is a password (or_recent_login).
        recent_login_until=as_utc(session.created_at) + service.RECENT_LOGIN_WINDOW,
    )


@router.post("/companies/{company_id}/sletting")
async def close_company(
    payload: CloseRequest,
    context: CurrentCompany,
    user: CurrentUser,
    session: CurrentSession,
    db: DbSession,
) -> CloseResponse:
    """Delete the company from Myntos: at once when empty, after its retention otherwise.

    Download the SAF-T file for every year in ``years`` from GET …/sletting
    first. SAF-T holds the entries but not the bilag; those stay locked in
    Myntos with the rest of the books, and an owner reaches them by opening
    the company again.

    Asks for two things. ``company_name``, the company's name as the person
    typed it, says which books they meant. The proof that it is them is the
    is a sign-in within the last ten minutes (``recent_login_until`` in GET
    …/sletting), which the page offers as «Bekreft med BankID/Google/…»; or,
    for an account with a password (``requires_password``), the password.
    Gentler than deleting the account, which always wants the password where
    there is one, because a deleted company can be opened again.
    Everybody but the owners loses access at once, every key stops, pending
    invitations go, and the confirmed claim on the orgnr is released.

    Refusals: 403 ``permission_denied`` for anybody but an owner; 422
    ``company_name_mismatch`` when the name is not the company's; 403
    ``reauthentication_required`` / ``reauthentication_failed`` without the
    proof; 409 ``company_closure_blocked`` while a SAF-T import is being read
    or posted into the books.

    POST with a body rather than DELETE, for the reason POST /account/erasure
    gives: the password travels in the body.
    """
    require_permission(context.membership.role, Permission.ORGANIZATION_DELETE)
    # The name before the password, so a typo in it never counts towards the
    # sign-in lockout.
    closure.require_name(context.company, payload.company_name)
    await service.confirm_identity(
        db,
        user,
        password=payload.password,
        session=session,
        action="slette selskapet",
        or_recent_login=True,
    )
    plan = await closure.close_company(db, context.company, user_id=user.id)
    return CloseResponse(outcome=plan.outcome.value, retain_until=plan.retain_until)
