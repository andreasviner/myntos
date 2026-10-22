"""Deleting an account (GDPR art. 17) without deleting anybody's books.

Two laws pull in opposite directions here, and the line between them is the
whole design. The reasoning is written out in docs/gdpr.md; the short version:

- **The person is theirs to erase.** Credentials, linked Google, Microsoft
  and BankID identities, the ID-porten link and its fødselsnummer, the government login
  attempts that carry a pid and a sealed token, every session with its IP and
  user agent, every membership, and every invitation addressed to them.
- **The books are the company's to keep.** A posted voucher, a bilag, an
  MVA-melding: bokføringsloven § 13 makes the company keep them for five years,
  and art. 17(3)(b) exempts exactly that. Part of what the company must keep is
  *who registered each entry*; that is what ``created_by_user_id`` and the SAF-T
  ``SourceID`` built from it are for.

So the account row has two possible ends. When nothing the books keep points at
it, it is deleted outright. When something does, it stays as a **tombstone**:
the same id (so every voucher, bilag and SAF-T file keeps naming the same
person), the name (so the register can still say who), and nothing else. No
address, no password, no identities, no way in. Pseudonymising the name instead
("Slettet bruker 41") was considered and rejected: it would satisfy the letter
of keeping an actor while defeating its purpose, because the company could no
longer answer an auditor asking who booked an entry, and the books are the
company's record, not ours to make less true.

Erasure is refused, rather than half-done, while the person is the last owner
of open books that somebody still needs: books other people work in (they would be
left with no owner, which nothing else in Myntos allows either), or books with
anything entered in them (they must stay readable for five years, and only an
owner can hand them on). Books with nothing in them and nobody else on them are
deleted with the account, because there is nothing to keep and an orphaned
confirmed claim on an orgnr would stop its real owner setting up books. Books
an owner already deleted from Myntos (myntos.domain.identity.closure) block
nothing: they are kept until their retention ends whoever owns them, and the
owner decided what happens to them when they deleted the company.

Like the rest of the identity domain this module does not commit. Proving that
the owner is at the keyboard comes first, and lives in
:func:`myntos.domain.identity.service.confirm_identity` because the switch to
BankID asks the same question; that function is the one that does commit, so a
wrong password is counted even though the request then fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.core.clock import utc_now
from myntos.domain.arsoppgjor.models import FinancialYearEvent
from myntos.domain.bank.models import BankStatementFile, BankStatementLine
from myntos.domain.bilag.models import Attachment
from myntos.domain.faktura.models import SalesDocument
from myntos.domain.identity.errors import ErasureBlockedError
from myntos.domain.identity.models import (
    AgentApproval,
    AgentApprovalFile,
    AgentKey,
    AgentKeyStop,
    AgentToken,
    CompanyMembership,
    GovernmentLoginAttempt,
    OAuthLoginAttempt,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    OrganizationRole,
    User,
    UserEmailToken,
    UserGovernmentIdentity,
    UserIdentity,
    UserSession,
)
from myntos.domain.identity.service import _unique_slug, normalise_email

# Models only, never services, for the reason identity.service gives for
# importing Company: whether an account may go is a question about which books
# still point at it, so this module has to be able to name those tables.
from myntos.domain.ledger.models import (
    Account,
    AccountGrouping,
    AccountOpeningBalance,
    Company,
    Journal,
    Party,
    PartyOpeningBalance,
    Transaction,
)
from myntos.domain.mva.models import MvaFiling, MvaFilingElsewhere
from myntos.skattemelding.models import SkattemeldingFiling

# The address a tombstone is left with. ``.invalid`` is reserved (RFC 2606), so
# it can never be delivered to, and pydantic's EmailStr refuses it, so nobody
# can register it ahead of us and make an erasure collide on the unique index.
# The id makes it unique; the original address is freed for a new signup.
TOMBSTONE_EMAIL = "deleted-{id}@erased.invalid"


class CompanyFate(StrEnum):
    """What deleting the account does to one set of books the person is on."""

    # Their membership goes; the books carry on with the people still on them.
    LEAVES = "leaves"
    # Nothing entered and nobody else on them: deleted with the account.
    DELETED = "deleted"
    # Already deleted from Myntos and kept until its retention ends. The
    # membership goes, and with it the last way to open the books again.
    CLOSED = "closed"
    # They are the last owner, and somebody still needs the books.
    BLOCKS_MEMBERS = "blocks_members"
    BLOCKS_BOOKS = "blocks_books"


@dataclass(frozen=True)
class CompanyOutcome:
    company_id: int
    company_name: str
    fate: CompanyFate

    @property
    def blocks(self) -> bool:
        return self.fate in (CompanyFate.BLOCKS_MEMBERS, CompanyFate.BLOCKS_BOOKS)


@dataclass(frozen=True)
class ErasurePlan:
    """What deleting this account would do, worked out before doing any of it.

    The same value answers the confirmation page and gates the deletion, so the
    page can never promise an outcome the deletion would not deliver.
    """

    companies: list[CompanyOutcome]
    # True when retained records name this person, so the row stays as a
    # tombstone with their name on it rather than disappearing.
    keeps_name: bool

    @property
    def blocked(self) -> bool:
        return any(outcome.blocks for outcome in self.companies)


async def has_books(db: AsyncSession, company_id: int) -> bool:
    """Whether anything has been entered in these books that must be kept.

    Broad on purpose. A voucher and a bilag are the obvious ones; an issued or
    even a draft salgsdokument, an MVA or skattemelding filing, a party, an opening balance, an
    imported bank statement and a closed year are also somebody's bookkeeping,
    and deleting books that held any of them would need a judgement about which
    ones mattered. A company with none of
    them holds only what setup seeded (the chart, the VAT codes, the register
    snapshot, bank accounts), which nobody is obliged to keep.

    Deleting a company asks the same question (myntos.domain.identity.closure),
    because it is the same question: whether there is anything to keep.
    """
    checks = (
        exists().where(Journal.company_id == company_id, Transaction.journal_id == Journal.id),
        exists().where(Attachment.company_id == company_id),
        exists().where(SalesDocument.company_id == company_id),
        exists().where(MvaFiling.company_id == company_id),
        exists().where(SkattemeldingFiling.company_id == company_id),
        exists().where(Party.company_id == company_id),
        exists().where(
            Account.company_id == company_id, AccountOpeningBalance.account_id == Account.id
        ),
        exists().where(Party.company_id == company_id, PartyOpeningBalance.party_id == Party.id),
        # An imported bank statement is archived primary documentation, and a
        # closed or reopened year is a record of what was done to the books.
        exists().where(BankStatementFile.company_id == company_id),
        exists().where(FinancialYearEvent.company_id == company_id),
    )
    for check in checks:
        if await db.scalar(select(check)):
            return True
    return False


async def _is_named_by_records(db: AsyncSession, user_id: int) -> bool:
    """Whether anything the books keep still points at this account.

    Across every company, not only the ones the person is on today: somebody
    removed from a client's books last year still entered the vouchers they
    entered, and those still need their name.
    """
    checks = (
        exists().where(Transaction.created_by_user_id == user_id),
        exists().where(Attachment.uploaded_by_user_id == user_id),
        exists().where(
            or_(MvaFiling.created_by_user_id == user_id, MvaFiling.submitted_by_user_id == user_id)
        ),
        exists().where(
            or_(
                SkattemeldingFiling.created_by_user_id == user_id,
                SkattemeldingFiling.submitted_by_user_id == user_id,
            )
        ),
        # Who took back a «sendt fra et annet program» statement: the term's
        # history is who said it was filed elsewhere and who changed their mind.
        exists().where(MvaFilingElsewhere.withdrawn_by_user_id == user_id),
        # The confirmation of who a company belongs to is what its filings rest
        # on, so whoever made it is part of that record too.
        exists().where(Company.verified_by_user_id == user_id),
        # Who imported a bank statement, who agreed that a voucher explains a
        # movement, and who closed or reopened a year: each is part of the
        # audit trail, and SET NULL on deletion would leave it saying nobody.
        exists().where(BankStatementFile.uploaded_by_user_id == user_id),
        exists().where(BankStatementLine.matched_by_user_id == user_id),
        exists().where(FinancialYearEvent.user_id == user_id),
        # Who chose a næringsspesifikasjon post: the skattemelding is filed from
        # it, and an account regrouped by "nobody" is one an auditor cannot ask
        # about.
        exists().where(AccountGrouping.chosen_by_user_id == user_id),
    )
    for check in checks:
        if await db.scalar(select(check)):
            return True
    return False


async def plan_erasure(db: AsyncSession, user: User) -> ErasurePlan:
    """Work out what deleting ``user`` would do, without doing it."""
    rows = await db.execute(
        select(CompanyMembership, Company)
        .join(Company, Company.id == CompanyMembership.company_id)
        .where(CompanyMembership.user_id == user.id)
        .order_by(Company.name, Company.id)
    )
    outcomes: list[CompanyOutcome] = []
    for membership, company in rows.tuples():
        outcomes.append(
            CompanyOutcome(
                company_id=company.id,
                company_name=company.name,
                fate=await _fate(db, membership, company),
            )
        )
    keeps_name = await _is_named_by_records(db, user.id)
    return ErasurePlan(companies=outcomes, keeps_name=keeps_name)


async def _fate(db: AsyncSession, membership: CompanyMembership, company: Company) -> CompanyFate:
    if company.closed_at is not None:
        return CompanyFate.CLOSED
    if membership.role != OrganizationRole.OWNER:
        return CompanyFate.LEAVES
    owners, members = (
        await db.execute(
            select(
                func.count().filter(CompanyMembership.role == OrganizationRole.OWNER),
                func.count(),
            ).where(CompanyMembership.company_id == membership.company_id)
        )
    ).one()
    if owners > 1:
        return CompanyFate.LEAVES
    if members > 1:
        return CompanyFate.BLOCKS_MEMBERS
    if await has_books(db, membership.company_id):
        return CompanyFate.BLOCKS_BOOKS
    return CompanyFate.DELETED


def _refusal(plan: ErasurePlan) -> str:
    """The prose the refusal carries: each company, and what to do about it."""
    sentences = []
    for outcome in plan.companies:
        if outcome.fate is CompanyFate.BLOCKS_MEMBERS:
            sentences.append(
                f"Du er eneste eier av {outcome.company_name}, og andre har tilgang til "
                "regnskapet. Gjør en av dem til eier først."
            )
        elif outcome.fate is CompanyFate.BLOCKS_BOOKS:
            sentences.append(
                f"Du er eneste eier av {outcome.company_name}, og regnskapet har bilag som "
                "skal oppbevares i fem år. Inviter den som skal ta over regnskapet som eier "
                "først."
            )
    return " ".join(sentences)


async def erase_account(db: AsyncSession, user: User) -> ErasurePlan:
    """Delete ``user``'s personal data, keeping only what the books must.

    Re-plans rather than trusting a plan the caller holds: membership can
    change between the confirmation page and the button, and the refusal has to
    be about the state the deletion would actually act on.
    """
    plan = await plan_erasure(db, user)
    if plan.blocked:
        raise ErasureBlockedError(_refusal(plan))

    # Anything the request already changed (the session's sliding window, at
    # least) is written now, while the rows still exist. Otherwise the commit
    # at the end would try to update a session deleted below and fail on it.
    await db.flush()
    user_id = user.id

    # Books nobody else is on and nothing was entered in. The database cascades
    # take the seeded chart, VAT codes, bank accounts, register snapshot and
    # any pending invitations with them.
    empty = [o.company_id for o in plan.companies if o.fate is CompanyFate.DELETED]
    if empty:
        await db.execute(delete(Company).where(Company.id.in_(empty)))

    # Invitations addressed to this person, pending or taken up. The address is
    # theirs, and the access it led to is going anyway. Invitations they *sent*
    # stay: each was given on behalf of a company to somebody else, and that
    # person's way in is not the sender's to withdraw by leaving. The link to
    # the sender is cut on the pending ones, so a stranger opening the link is
    # not told the name of somebody who has left.
    await db.execute(
        delete(OrganizationInvitation).where(
            or_(
                OrganizationInvitation.email == normalise_email(user.email),
                OrganizationInvitation.accepted_by_user_id == user_id,
            )
        )
    )
    await db.execute(
        update(OrganizationInvitation)
        .where(
            OrganizationInvitation.invited_by_user_id == user_id,
            OrganizationInvitation.accepted_at.is_(None),
        )
        .values(invited_by_user_id=None)
    )

    # Every key the person made stops, and its tokens go. The key rows stay,
    # with the name the person gave them and nothing that identifies a device,
    # because a voucher registered through a key says which one, and that is
    # part of the books' record. On an account deleted outright the rows go
    # with it by cascade, and no voucher can be pointing at them: one made
    # through a key names the key's person too, who would be a tombstone.
    await db.execute(
        update(AgentKey)
        .where(AgentKey.user_id == user_id, AgentKey.revoked_at.is_(None))
        .values(revoked_at=utc_now(), revoked_reason=AgentKeyStop.ACCOUNT_DELETED)
    )
    await db.execute(
        update(AgentKey).where(AgentKey.user_id == user_id).values(last_used_client=None)
    )
    await db.execute(
        delete(AgentToken).where(
            AgentToken.key_id.in_(select(AgentKey.id).where(AgentKey.user_id == user_id))
        )
    )
    # What those keys were waiting to have approved can never run now, so the
    # files sent with it go too. The requests stay as a record of what was
    # asked, like the keys; a request that ran left its files in the bilag
    # archive, which is where the books keep them.
    await db.execute(
        update(AgentApprovalFile)
        .where(
            AgentApprovalFile.approval_id.in_(
                select(AgentApproval.id)
                .join(AgentKey, AgentKey.id == AgentApproval.key_id)
                .where(AgentKey.user_id == user_id)
            )
        )
        .values(content=None)
    )

    # Everything that is the person rather than the books. Sessions go
    # entirely, not merely revoked: "when was I logged out, and from where" is
    # a question only the person could ask, and the person is leaving.
    for table, column in (
        (UserSession, UserSession.user_id),
        # Confirmation and reset links carry the address they were sent to. The
        # foreign key cascades, but a tombstone keeps the user row, so without
        # this the old address would outlive the account in the token table.
        (UserEmailToken, UserEmailToken.user_id),
        # Every provider alike, BankID included: the row is the person's way
        # in, and for BankID it is also the broker's pseudonym for them.
        (UserIdentity, UserIdentity.user_id),
        # A link to a provider started from /konto/ and never finished still
        # names the account. Cascades on a deleted row, but not on a tombstone.
        (OAuthLoginAttempt, OAuthLoginAttempt.user_id),
        (UserGovernmentIdentity, UserGovernmentIdentity.user_id),
        (GovernmentLoginAttempt, GovernmentLoginAttempt.user_id),
        (CompanyMembership, CompanyMembership.user_id),
    ):
        await db.execute(delete(table).where(column == user_id))

    organization_ids = list(
        await db.scalars(
            select(OrganizationMembership.organization_id).where(
                OrganizationMembership.user_id == user_id
            )
        )
    )
    await db.execute(
        delete(OrganizationMembership).where(OrganizationMembership.user_id == user_id)
    )
    await _settle_organizations(db, organization_ids)

    if plan.keeps_name:
        _leave_tombstone(user)
    else:
        await db.execute(delete(User).where(User.id == user_id))
        db.expunge(user)
    await db.flush()
    return plan


async def _settle_organizations(db: AsyncSession, organization_ids: list[int]) -> None:
    """Tidy the accounts the person belonged to, now that they do not.

    An account with no members and no companies is deleted: it answered "whose
    companies are these", and the answer is now nobody's and nothing. One that
    still holds companies stays, because those companies have owners of their
    own; its placeholder name was made from this person's name at signup
    ("Kari Nordmann (privat)"), so that is replaced. A name somebody chose is
    left alone, because nothing here knows whose it is.
    """
    for organization_id in organization_ids:
        members = await db.scalar(
            select(func.count())
            .select_from(OrganizationMembership)
            .where(OrganizationMembership.organization_id == organization_id)
        )
        companies = await db.scalar(
            select(func.count())
            .select_from(Company)
            .where(Company.organization_id == organization_id)
        )
        if not members and not companies:
            await db.execute(delete(Organization).where(Organization.id == organization_id))
            continue
        organization = await db.scalar(
            select(Organization).where(Organization.id == organization_id)
        )
        if organization is not None and organization.name_is_placeholder:
            # The slug was made from the same name, so it goes too. Through
            # _unique_slug, because "konto-12" is also what a person who named
            # their account "Konto 12" would have.
            organization.name = f"Konto {organization_id}"
            organization.slug = await _unique_slug(db, organization.name)


def _leave_tombstone(user: User) -> None:
    """Strip ``user`` down to what the books need: the id and the name."""
    user.email = TOMBSTONE_EMAIL.format(id=user.id)
    user.email_verified_at = None
    user.password_hash = None
    user.is_active = False
    user.last_login_at = None
    user.failed_login_attempts = 0
    user.locked_until = None
    user.deleted_at = utc_now()
