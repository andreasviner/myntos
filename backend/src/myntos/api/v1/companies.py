"""Companies — the bookkeeping-obligated entities inside an organization.

An organization is the account and the tenant; a company is a set of books with
an orgnr. A sole trader has one of each, an accounting firm has one
organization and many companies. Nothing here is reachable without a membership
in the owning organization, and a company belonging to someone else's
organization answers 404 rather than 403.

Creating a company also seeds its kontoplan, VAT codes and first financial year
— see :func:`myntos.domain.ledger.service.create_company` for why that happens
here rather than lazily.

Creating one says nothing about *whose* company it is. ``POST
/companies/{id}/bekreft`` is the step that settles that: the caller's ID-porten
login is spent on Altinn's list of parties they may act for, and only an orgnr
on that list may be confirmed. One confirmed claim exists per orgnr across the
installation, so the second organization to try gets
``company_already_claimed`` and the choice of asking for access or keeping
unconfirmed books.
"""

import contextlib
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date, datetime

from fastapi import APIRouter, status
from pydantic import BaseModel, Field, ValidationError

from myntos.api.deps import KEY_READ, CurrentCompany, CurrentUser, DbSession, key_write
from myntos.api.v1.enhetsregisteret import RegisteredUnitResponse, RoleResponse
from myntos.api.v1.skatt import authorized_parties_for
from myntos.core.clock import as_utc, utc_now
from myntos.domain.identity import register_match
from myntos.domain.identity import service as identity_service
from myntos.domain.identity.access import (
    Permission,
    has_permission,
    permissions_for,
    require_permission,
)
from myntos.domain.identity.models import CompanyMembership
from myntos.domain.identity.register_match import RegisterAccess
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.models import Company
from myntos.integrations import brreg
from myntos.integrations.altinn.authorized_parties import flatten
from myntos.integrations.brreg import BrregError

router = APIRouter(tags=["companies"])


class BankAccountResponse(BaseModel):
    """One kontonummer, as stored.

    ``account`` is eleven digits with no separators — the form the EHF file and
    the PDF use. The interface groups it for reading (``1506.20.33158``); the
    wire does not, because a grouped number is a display of a value rather than
    the value.
    """

    id: int
    account: str
    label: str | None
    # The one an invoice is paid into when it names none itself. Exactly one
    # account carries it, for as long as the company has any at all.
    is_primary: bool


class CompanyResponse(BaseModel):
    id: int
    organization_id: int
    orgnr: str
    name: str
    address_line: str | None
    postal_code: str | None
    city: str | None
    currency_code: str
    # What a salgsdokument has to be able to state about the seller — see
    # bokføringsforskriften § 5-1-2 and myntos.domain.faktura.
    vat_registered: bool
    # The day the registration took effect, when later than the start of the
    # books. Decides the VAT defaults of new entries dated before it; null is
    # "registered for as long as these books go back".
    vat_registered_from: date | None
    in_foretaksregisteret: bool
    organisation_form: str | None
    # Every account the company is paid into, primary first. A list and not a
    # field: a business with a driftskonto and a skattetrekkskonto has two, and
    # which one an invoice names is a per-invoice choice with this default.
    # Empty is the ordinary state of new books, and it is what stops a
    # salgsdokument being issued — see myntos.domain.faktura.compliance.
    bank_accounts: list[BankAccountResponse]
    # Whether this company's bilag may be shown to a model. See
    # myntos.domain.ledger.models.Company.
    ki_enabled: bool
    # Whether keys for agents and integrations may be made for and used in these
    # books. Switched by PUT /companies/{id}/agent-access, never by the PATCH
    # below: it is access, and it has its own permission. See
    # myntos.domain.ledger.models.Company.
    agent_access_enabled: bool
    # --- Copied from Enhetsregisteret ---
    # Refreshed by POST /companies/{id}/hent and by a confirmation. The register
    # is the authority for all of them, so these are a cache with a timestamp
    # rather than a second opinion — register_synced_at is null until it has
    # been read for this company at least once.
    industry_code: str | None
    industry_description: str | None
    website: str | None
    phone: str | None
    email: str | None
    parent_orgnr: str | None
    register_synced_at: datetime | None
    # Whether somebody has proved through Altinn that they may act for this
    # orgnr. False is the ordinary state for books somebody just typed an orgnr
    # into, and the interface says "ubekreftet" rather than hiding anything:
    # bookkeeping is not gated on it, ownership is.
    verified: bool
    verified_at: datetime | None
    # What these books cost. On the company and not on the account, because a
    # set of books is the thing that has a size: see Company.plan. Read-only
    # here — there is nothing to sell yet, and when there is, the payment flow
    # will move it rather than a client PATCH.
    plan: str
    # What *this* caller may do with *these* books. Access is per company
    # (myntos.domain.identity.models.CompanyMembership), so the role travels
    # with the company rather than with the session: one person can own their
    # own books and merely read a client's. The frontend decides what to offer
    # from this and never from a role name.
    role: str
    permissions: list[str]


class CreateCompanyRequest(BaseModel):
    organization_id: int
    # Nine digits; the mod-11 check digit is verified in the domain, so the
    # bound here is only the shape.
    orgnr: str = Field(min_length=9, max_length=11)
    name: str = Field(min_length=1, max_length=255)
    address_line: str | None = Field(default=None, max_length=255)
    postal_code: str | None = Field(default=None, max_length=16)
    city: str | None = Field(default=None, max_length=128)
    # Which year the books start in. Everything before it is refused, because
    # the seeded VAT rates are only asserted from this year onwards.
    first_financial_year: int | None = Field(default=None, ge=2000, le=2100)
    # Normally taken from Enhetsregisteret by the signup flow rather than typed.
    vat_registered: bool = False
    in_foretaksregisteret: bool = False
    organisation_form: str | None = Field(default=None, max_length=16)
    # The account the signup form asks for, which becomes the company's primary.
    # It has no counterpart on the update below: accounts are a collection, and
    # /companies/{id}/bankkontoer owns them. Kept here because a new company has
    # no accounts to add one *to* yet, and asking for the first one while the
    # person is already filling in a form is the cheap moment.
    bank_account: str | None = Field(default=None, max_length=34)


class UpdateCompanyRequest(BaseModel):
    """Every field omitted is left alone.

    The orgnr is here, unlike the other keys in this system, because it is nine
    digits somebody typed and a typo in it is worth being able to correct. It
    stops being editable the moment ownership is confirmed — see
    :func:`myntos.domain.ledger.service.update_company_profile`.
    """

    orgnr: str | None = Field(default=None, min_length=9, max_length=11)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    address_line: str | None = Field(default=None, max_length=255)
    postal_code: str | None = Field(default=None, max_length=16)
    city: str | None = Field(default=None, max_length=128)
    vat_registered: bool | None = None
    # Sent as null to clear it, which is why the route reads whether the key
    # was present rather than whether it is None.
    vat_registered_from: date | None = None
    in_foretaksregisteret: bool | None = None
    organisation_form: str | None = Field(default=None, max_length=16)
    ki_enabled: bool | None = None


def _company_response(company: Company, membership: CompanyMembership) -> CompanyResponse:
    return CompanyResponse(
        id=company.id,
        organization_id=company.organization_id,
        orgnr=company.orgnr,
        name=company.name,
        address_line=company.address_line,
        postal_code=company.postal_code,
        city=company.city,
        currency_code=company.currency_code,
        vat_registered=company.vat_registered,
        vat_registered_from=company.vat_registered_from,
        in_foretaksregisteret=company.in_foretaksregisteret,
        organisation_form=company.organisation_form,
        # Sorted here rather than trusted from the relationship: the collection
        # is ordered on load, and a row added or promoted in this same request
        # sits wherever it was appended. The interface reads "the first one" as
        # "the one invoices go to", so the order has to be a fact rather than a
        # coincidence of when the flush happened.
        bank_accounts=[
            BankAccountResponse(
                id=account.id,
                account=account.account,
                label=account.label,
                is_primary=account.is_primary,
            )
            for account in sorted(
                company.bank_accounts, key=lambda account: (not account.is_primary, account.id)
            )
        ],
        ki_enabled=company.ki_enabled,
        agent_access_enabled=company.agent_access_enabled,
        industry_code=company.industry_code,
        industry_description=company.industry_description,
        website=company.website,
        phone=company.phone,
        email=company.email,
        parent_orgnr=company.parent_orgnr,
        register_synced_at=(
            None if company.register_synced_at is None else as_utc(company.register_synced_at)
        ),
        verified=company.is_verified,
        # Through as_utc, because SQLite hands back a naive datetime while
        # PostgreSQL hands back an aware one, and the same row would otherwise
        # serialise with a trailing "Z" on the response that wrote it and
        # without one on every response that read it back.
        verified_at=None if company.verified_at is None else as_utc(company.verified_at),
        plan=company.plan,
        role=membership.role.value,
        permissions=sorted(permission.value for permission in permissions_for(membership.role)),
    )


@router.get("/companies")
async def list_companies(user: CurrentUser, db: DbSession) -> list[CompanyResponse]:
    """Every set of books the caller has access to, with the role they hold."""
    companies = await ledger_service.list_companies(db, user_id=user.id)
    return [_company_response(company, membership) for company, membership in companies]


@router.post("/companies", status_code=status.HTTP_201_CREATED)
async def create_company(
    payload: CreateCompanyRequest, user: CurrentUser, db: DbSession
) -> CompanyResponse:
    """Add a company to one of the caller's organizations."""
    membership = await identity_service.get_membership(
        db, organization_id=payload.organization_id, user_id=user.id
    )
    # Adding a set of books is an administrative act, not a bookkeeping one:
    # it changes what the organization is responsible for reporting.
    require_permission(membership.role, Permission.ORGANIZATION_MANAGE)

    company = await ledger_service.create_company(
        db,
        organization=membership.organization,
        # The creator owns the books they set up, and starts as the only person
        # who can open them. Everyone else is invited to this company by name.
        created_by_user_id=user.id,
        orgnr=payload.orgnr,
        name=payload.name,
        first_financial_year=payload.first_financial_year or utc_now().year,
        address_line=payload.address_line,
        postal_code=payload.postal_code,
        city=payload.city,
        vat_registered=payload.vat_registered,
        in_foretaksregisteret=payload.in_foretaksregisteret,
        organisation_form=payload.organisation_form,
        bank_account=payload.bank_account,
    )
    # The organization is deliberately *not* renamed after these books, however
    # provisional its own name still is: an account wearing one of its
    # companies' names cannot be told apart, on screen, from one company's data
    # having appeared under another company's name. See the note where
    # ``adopt_name_if_placeholder`` used to be, in domain/identity/service.py.
    # Fill in everything else the register knows — næringskode, contact details,
    # the main unit a subunit belongs to — without the client having had to ask
    # for them. The signup form only ever looked up a name and an address, and
    # every field left empty here is one somebody types later, or does not.
    #
    # Best effort, like the confirmation: the books are set up either way, and a
    # slow Brønnøysund must not turn a completed signup into an error page.
    # The creator's own access, made by create_company: they own these books.
    created_for = await identity_service.get_company_membership(
        db, company_id=company.id, user_id=user.id
    )
    with contextlib.suppress(BrregError):
        await _refresh(db, company, created_for)
    return _company_response(company, created_for)


class RoleAccessResponse(RoleResponse):
    """A registered role, plus whether that person can open these books.

    The board list and the member list are two answers to almost the same
    question, and until this field existed nothing said whether they agreed. A
    daglig leder nobody ever invited looked exactly like one who logs in daily.

    Deliberately a subclass rather than three more fields on
    :class:`~myntos.api.v1.enhetsregisteret.RoleResponse`, for a reason that
    would otherwise bite silently: the stored register snapshot is a dump of
    ``RoleResponse``, so anything added there would be *written into the
    snapshot* and served back weeks later as though the register had said it.
    Access is resolved fresh on every read, from the membership table, which is
    the only way it can be right.

    ``access`` is never a permission and never enforces anything. See
    :mod:`myntos.domain.identity.register_match` for what a name match is
    actually worth.
    """

    access: RegisterAccess = RegisterAccess.NONE
    # The organization role they hold, or would hold on accepting. Null when
    # nobody was matched.
    access_role: str | None = None
    # The Myntos-side name, when it differs from the register's, so an
    # administrator can see why two spellings were treated as one person.
    access_name: str | None = None
    # Where a pending invitation went, so "invitert" can say to which address.
    access_email: str | None = None


class RegisterResponse(BaseModel):
    """What Enhetsregisteret says about a company, and when it said it.

    Served by two endpoints with the same shape on purpose. ``GET .../register``
    hands back what is stored, without leaving the building; ``POST .../hent``
    asks Brønnøysund and hands back the answer it stored. The page that renders
    one renders the other, and does not need to know which it got.
    """

    unit: RegisteredUnitResponse
    roles: list[RoleAccessResponse]
    # When the register was asked. Shown beside the data, because a cached
    # answer that does not say how old it is invites being read as current.
    fetched_at: datetime


class RefreshResponse(RegisterResponse):
    """A register answer that was just fetched, and what it moved on the way in."""

    company: CompanyResponse
    # Field names, in the response model's own vocabulary, so the interface can
    # name what changed rather than saying "something did". Empty when the
    # register agreed with everything already stored, which is the ordinary case
    # and worth saying plainly.
    changed: list[str]


async def _with_access(
    db: DbSession,
    *,
    company_id: int,
    roles: Sequence[RoleResponse],
    may_manage_members: bool,
) -> list[RoleAccessResponse]:
    """Say, for each registered role holder, whether they can see these books.

    One query for the members and one for the pending invitations, then a pure
    match per role, rather than a lookup per name, which on a board of nine is
    nine round trips for an answer that changes when somebody is invited.

    Nothing is short-circuited when the list is empty: a company with no
    pending invitations still has members, and one with no roles simply
    produces no rows.

    Both lists are the *company's*, because that is what access now means: a
    board member who can open these books has a membership on them, and a
    pending invitation names them too. Somebody who keeps the account's other
    companies does not appear here at all, which is the point.

    ``may_manage_members`` decides whether the *address* a pending invitation
    went to travels with it. This list is readable by everyone who can open the
    company, a viewer included; the invitation list is not
    (``MEMBER_MANAGE``), and answering with an invited address here would route
    around that. The state still shows, because "this person has been asked" is
    a fact about the board. Only the address is withheld.
    """
    if not roles:
        return []
    members = [
        register_match.MemberName(
            user_id=member.user_id,
            role=member.role,
            account_name=member.user.full_name,
            verified_name=(
                member.user.government_identity.full_name
                if member.user.government_identity is not None
                else None
            ),
        )
        for member in await identity_service.list_company_members(db, company_id)
    ]
    invitations = [
        register_match.InvitedName(
            invitation_id=invitation.id,
            role=invitation.role,
            email=invitation.email,
            full_name=invitation.full_name,
        )
        for invitation in await identity_service.list_invitations(db, company_id)
    ]
    decorated: list[RoleAccessResponse] = []
    for role in roles:
        found = register_match.match_person(
            role.name, orgnr=role.orgnr, members=members, invitations=invitations
        )
        decorated.append(
            RoleAccessResponse(
                **role.model_dump(),
                access=found.access,
                access_role=None if found.role is None else found.role.value,
                access_name=found.matched_name,
                access_email=found.email if may_manage_members else None,
            )
        )
    return decorated


def _role_response(role: brreg.Role) -> RoleResponse:
    return RoleResponse(
        group=role.group,
        group_description=role.group_description,
        code=role.code,
        description=role.description,
        name=role.name,
        orgnr=role.orgnr,
        elected_by=role.elected_by,
    )


async def _refresh(
    db: DbSession, company: Company, membership: CompanyMembership
) -> RefreshResponse:
    """Read the register and copy what it says onto ``company``.

    Every failure here is :mod:`myntos.integrations.brreg`'s own error, which
    the error handler renders — 404 for a number the register does not know,
    410 for a deleted unit, 502 for a register that would not answer. None of
    them is dressed up as success: a refresh that quietly did nothing would
    leave the interface claiming the company was up to date.
    """
    unit = await brreg.lookup_unit(company.orgnr)
    changed = await ledger_service.refresh_company_from_register(db, company=company, unit=unit)
    # The roles ride along on the unit's success rather than gating it: they are
    # a second request, and letting it throw the first one's result away would
    # mean a company with an odd role list could never be refreshed at all.
    # ``None`` rather than ``[]`` is the difference between "the register says
    # this company has no roles" and "we did not get to ask", and only the first
    # should replace what is already stored.
    roles: list[brreg.Role] | None = None
    with contextlib.suppress(BrregError):
        roles = await brreg.fetch_roles(company.orgnr, parent_orgnr=unit.parent_orgnr)

    unit_response = RegisteredUnitResponse(**asdict(unit))
    role_responses = None if roles is None else [_role_response(role) for role in roles]
    # Stored in the shape it is served in, so the cached answer and this one are
    # the same document. See ledger_service.store_register_snapshot.
    snapshot = await ledger_service.store_register_snapshot(
        db,
        company=company,
        unit=unit_response.model_dump(mode="json"),
        roles=(
            None
            if role_responses is None
            else [item.model_dump(mode="json") for item in role_responses]
        ),
    )
    return RefreshResponse(
        company=_company_response(company, membership),
        changed=changed,
        unit=unit_response,
        # What was just stored, which after a failed role lookup is what was
        # stored before, the board we last knew about rather than none. The
        # access marks are resolved here rather than stored, for the reason
        # RoleAccessResponse gives.
        roles=await _with_access(
            db,
            company_id=company.id,
            roles=[RoleResponse(**item) for item in snapshot.roles],
            # Every route that reaches here has already required
            # ORGANIZATION_MANAGE, which only admins and owners hold and which
            # carries MEMBER_MANAGE with it, so there is no viewer on this path
            # to withhold an invited address from.
            may_manage_members=True,
        ),
        fetched_at=as_utc(snapshot.fetched_at),
    )


@router.get("/companies/{company_id}/register")
async def read_register(context: CurrentCompany, db: DbSession) -> RegisterResponse | None:
    """What the register last said about this company, without asking it again.

    ``null`` when nothing has been stored yet, or when everything stored was
    fetched under an orgnr this company no longer has.

    This is what a page load should use. Asking Brønnøysund on every visit
    spends a round trip on an answer that changes a few times a year, and makes
    the page depend on a service that has nothing to do with whether the books
    work. ``POST .../hent`` stays the way to ask for a fresh one.

    Readable by anyone in the organization, unlike the refresh: this is a lookup
    of what is already stored, and it writes nothing.
    """
    snapshot = await ledger_service.get_register_snapshot(db, company=context.company)
    if snapshot is None:
        return None
    try:
        unit = RegisteredUnitResponse(**snapshot.unit)
        stored = [RoleResponse(**role) for role in snapshot.roles]
    except ValidationError:
        # A snapshot stored before a field was added or renamed. Answering
        # "nothing stored" sends the caller to the refresh, which replaces it —
        # a better outcome than a 500 on a cache.
        return None
    return RegisterResponse(
        unit=unit,
        # Resolved now, not when the snapshot was stored: who can see the books
        # changes when somebody is invited, and the register's answer does not.
        roles=await _with_access(
            db,
            company_id=context.company.id,
            roles=stored,
            may_manage_members=has_permission(context.membership.role, Permission.MEMBER_MANAGE),
        ),
        fetched_at=as_utc(snapshot.fetched_at),
    )


@router.post(
    "/companies/{company_id}/hent",
    openapi_extra=key_write("hente selskapets opplysninger fra Enhetsregisteret på nytt"),
)
async def refresh_company(context: CurrentCompany, db: DbSession) -> RefreshResponse:
    """Re-read everything Enhetsregisteret says about this company.

    The register is the authority for the address, the organisasjonsform, the
    næringskode, the contact details and — the two that decide what an invoice
    may claim — the VAT and Foretaksregisteret markings. Businesses move, cross
    the VAT threshold and change their næring, and none of those events tells
    Myntos anything. This is how the copy catches up.

    ``ORGANIZATION_MANAGE`` because it can change what every future
    salgsdokument claims about the seller, which is the same reason editing
    those fields by hand needs it.

    The name is deliberately not touched — see
    :func:`myntos.domain.ledger.service.refresh_company_from_register`.

    With a key, ``roles`` comes back empty. Signed in, each board member
    carries whether they have access to these books, and who has access is
    never a key's to read (docs/agents-and-api.md rule 5, the reason
    ``GET …/register`` is closed to keys). The board itself is public: read it
    from ``GET /api/v1/enhetsregisteret/{orgnr}/roller``.
    """
    require_permission(context.membership.role, Permission.ORGANIZATION_MANAGE)
    refreshed = await _refresh(db, context.company, context.membership)
    if context.agent_key is not None:
        refreshed.roles = []
    return refreshed


@router.post("/companies/{company_id}/bekreft")
async def verify_company(
    context: CurrentCompany, user: CurrentUser, db: DbSession
) -> CompanyResponse:
    """Confirm, through Altinn, that these books belong to the caller.

    Creating a company proves nothing: anyone can type nine digits. This is the
    step that turns it into a claim, and the evidence is the same one a
    submission will later rest on — Altinn's own list of parties the person may
    act for, read from their ID-porten login.

    Three refusals, and each is a different thing for the interface to say:

    - **409 without a code** — no usable government login. The way out is the
      ID-porten button, not an error message.
    - **403 ``not_authorized_for_party``** — the login worked and Altinn does not
      list this orgnr. Rights are granted in Altinn, by the company, not here.
    - **409 ``company_already_claimed``** — somebody else confirmed this orgnr
      first. The frontend branches on that code to offer the two ways forward:
      ask an administrator there for access, or keep these books unconfirmed.

    ``ORGANIZATION_MANAGE``, the same permission as creating a set of books:
    settling whose company this is is an administrative act, and the personal
    ID-porten login it rests on belongs to the person making it.
    """
    require_permission(context.membership.role, Permission.ORGANIZATION_MANAGE)
    parties = await authorized_parties_for(db, user)
    # Subunits included, and flattened the same way the picker shows them: a
    # regnskapsfører engaged for one department holds rights on the subunit's
    # own orgnr, not on the main unit's.
    by_orgnr = {
        party.orgnr: party.party_id
        for party in flatten(parties)
        if party.is_usable and party.orgnr is not None
    }
    company = await ledger_service.confirm_company_ownership(
        db, company=context.company, user_id=user.id, authorized_parties=by_orgnr
    )
    # And bring the register's picture along with it. Confirming is the moment
    # somebody has just told us this company is theirs, which is exactly when a
    # stale address or a missing næringskode is worth fixing — and doing it here
    # means the ordinary path never leaves a company half-filled waiting for
    # somebody to press a second button.
    #
    # Best effort: the claim is established either way, and Brønnøysund being
    # slow is not a reason to refuse a confirmation that already succeeded.
    with contextlib.suppress(BrregError):
        await _refresh(db, company, context.membership)
    return _company_response(company, context.membership)


@router.get("/companies/{company_id}", openapi_extra=KEY_READ)
async def get_company(context: CurrentCompany) -> CompanyResponse:
    """One company's details, and what the caller may do with its books.

    A company (selskap) is one set of books for one Norwegian business, and it
    is what every other path under ``/companies/{company_id}/`` is about. This
    is its record as the Selskapet page shows it: the name, the
    organisasjonsnummer (``orgnr``, the nine digits Brønnøysundregistrene gives
    every business), the address, and the facts every invoice has to repeat
    about the seller:

    - ``vat_registered`` and ``vat_registered_from``: whether the company is in
      Merverdiavgiftsregisteret ("mva-registrert"), and from which day. An
      unregistered company may not charge VAT on its invoices.
    - ``in_foretaksregisteret``: whether every invoice must say
      "Foretaksregisteret" and give the head office address.
    - ``bank_accounts``: every kontonummer the company is paid into, eleven
      digits, the main one ("hovedkonto") first. Empty is the ordinary state of
      new books, and no invoice can be issued until there is one.

    ``verified`` says whether somebody has proved through Altinn, with their
    own ID-porten login, that they may act for this orgnr ("bekreftet").
    Bookkeeping works either way. The fields copied from Enhetsregisteret are
    as fresh as ``register_synced_at``; ``POST /companies/{company_id}/hent``
    refreshes them. ``role`` and ``permissions`` are the caller's own on these
    books: decide what to offer from ``permissions``, never from a role name.

    Readable by every role, and by a key with the ``read`` scope. Changing the
    details by hand, and any change to a kontonummer, is the person's to do in
    the app; no key may. A company the caller cannot open answers 404.
    """
    return _company_response(context.company, context.membership)


@router.patch("/companies/{company_id}")
async def update_company(
    payload: UpdateCompanyRequest, context: CurrentCompany, db: DbSession
) -> CompanyResponse:
    """Correct the company's details, including what its invoices may claim.

    ``ORGANIZATION_MANAGE`` rather than ``LEDGER_WRITE``: whether the business is
    registered for VAT is not a bookkeeping entry, it is a statement about the
    entity that every future salgsdokument will repeat, and an ordinary member
    posting payments has no business changing it.

    Two refusals worth telling apart, both 409:
    ``company_orgnr_locked`` when the orgnr is moved on a company somebody has
    confirmed, and ``duplicate_company`` when the organization already keeps
    books under the number.
    """
    require_permission(context.membership.role, Permission.ORGANIZATION_MANAGE)
    # Held before the update, which mutates the company in place. Comparing the
    # two normalised numbers afterwards is the only way to tell a real move from
    # a form resubmitting the same orgnr with spaces in it.
    was = context.company.orgnr
    company = await ledger_service.update_company_profile(
        db,
        company=context.company,
        orgnr=payload.orgnr,
        name=payload.name,
        address_line=payload.address_line,
        postal_code=payload.postal_code,
        city=payload.city,
        vat_registered=payload.vat_registered,
        in_foretaksregisteret=payload.in_foretaksregisteret,
        organisation_form=payload.organisation_form,
        ki_enabled=payload.ki_enabled,
        vat_registered_from=payload.vat_registered_from,
        clear_vat_registered_from=(
            "vat_registered_from" in payload.model_fields_set
            and payload.vat_registered_from is None
        ),
    )
    if company.orgnr != was:
        # The stored address, næringskode and contact details describe the orgnr
        # that was there a moment ago. Leaving them would be worse than never
        # having had them: they would read as this company's details while
        # belonging to a different one.
        #
        # Best effort, like every other refresh here: the correction has already
        # been made, and Brønnøysund being slow is not a reason to refuse it.
        with contextlib.suppress(BrregError):
            await _refresh(db, company, context.membership)
    return _company_response(company, context.membership)


# --- Bank accounts ----------------------------------------------------------------
#
# Every one of these answers with the whole :class:`CompanyResponse` rather than
# with the account that changed. Deliberate: the frontend keeps one record per
# company (docs/company-isolation.md), and handing back half a company would
# leave the page merging two shapes and deciding which is newer. Promoting an
# account changes another account's row as well, so "the account that changed"
# was never one row in the first place.


class CreateBankAccountRequest(BaseModel):
    # Eleven digits; separators and a Norwegian IBAN prefix are accepted and
    # stripped, and the mod-11 check digit is verified in the domain, so the
    # bound here is only the shape.
    account: str = Field(min_length=1, max_length=34)
    label: str | None = Field(default=None, max_length=64)
    # Omitted means "not the default", except on the first account, where the
    # domain makes it primary regardless: a company with accounts and no default
    # is a company whose next invoice has nowhere to be paid.
    is_primary: bool = False


class UpdateBankAccountRequest(BaseModel):
    """Every field omitted is left alone.

    An empty ``label`` clears the name. An empty ``account`` is refused, because
    an account row with no number is not a thing that can exist — deleting it is.
    """

    account: str | None = Field(default=None, min_length=1, max_length=34)
    label: str | None = Field(default=None, max_length=64)
    # ``true`` promotes this account and demotes whichever held it. ``false`` is
    # accepted on any other account, so a form that resubmits every field it
    # shows goes on working, and refused on the primary itself with
    # ``primary_bank_account_required``.
    is_primary: bool | None = None


@router.post("/companies/{company_id}/bankkontoer", status_code=status.HTTP_201_CREATED)
async def add_bank_account(
    payload: CreateBankAccountRequest, context: CurrentCompany, db: DbSession
) -> CompanyResponse:
    """Add a kontonummer the company is paid into.

    ``ORGANIZATION_MANAGE`` and not ``LEDGER_WRITE``, for the same reason as the
    company's own details: this is not a bookkeeping entry, it is where every
    future invoice tells the customer to send money, and an issued
    salgsdokument can only be corrected with a kreditnota.

    Two refusals worth telling apart: 422 ``invalid_bank_account`` for a number
    that fails its check digit, and 409 ``duplicate_bank_account`` for one the
    company already holds.
    """
    require_permission(context.membership.role, Permission.ORGANIZATION_MANAGE)
    await ledger_service.add_bank_account(
        db,
        company=context.company,
        account=payload.account,
        label=payload.label,
        is_primary=payload.is_primary,
    )
    return _company_response(context.company, context.membership)


@router.patch("/companies/{company_id}/bankkontoer/{bank_account_id}")
async def update_bank_account(
    payload: UpdateBankAccountRequest,
    bank_account_id: int,
    context: CurrentCompany,
    db: DbSession,
) -> CompanyResponse:
    """Correct a number, rename an account, or make it the default.

    An account belonging to another company is a 404 and not a 403, like every
    other sub-resource here: these ids are sequential, and confirming which of
    them exist would turn the API into a way to count somebody else's accounts.
    """
    require_permission(context.membership.role, Permission.ORGANIZATION_MANAGE)
    await ledger_service.update_bank_account(
        db,
        company=context.company,
        bank_account_pk=bank_account_id,
        account=payload.account,
        label=payload.label,
        is_primary=payload.is_primary,
    )
    return _company_response(context.company, context.membership)


@router.delete("/companies/{company_id}/bankkontoer/{bank_account_id}")
async def delete_bank_account(
    bank_account_id: int, context: CurrentCompany, db: DbSession
) -> CompanyResponse:
    """Close an account. Issued documents keep the number they were issued with.

    Nothing is refused: a salgsdokument carries its own snapshot, and a draft
    pointed at this account falls back to the primary the next time it is saved.
    Deleting the primary promotes the oldest account that remains, so the
    company never ends up with several accounts and no default.
    """
    require_permission(context.membership.role, Permission.ORGANIZATION_MANAGE)
    await ledger_service.delete_bank_account(
        db, company=context.company, bank_account_pk=bank_account_id
    )
    return _company_response(context.company, context.membership)
