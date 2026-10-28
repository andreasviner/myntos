"""Deleting a company from Myntos without deleting books the law says to keep.

An owner who is done with Myntos (the business wound up, or moved to another
program) wants the company gone. Bokføringsloven § 13 second paragraph says
the books may not be gone yet: the vouchers, the bilag, the filings and the
annual accounts are kept for five years after the end of the financial year
they belong to, and agreements and correspondence for three and a half. The
duty is the company's, and it outlives the business: aksjeloven § 16-10
fourth paragraph puts it on whoever winds a company up, and Skatteetaten's
principle statement on retention after winding-up and bankruptcy says there
is no general exemption. The reasoning is written out in docs/gdpr.md under
«Deleting a company»; the short version:

- **Nothing entered: deleted now.** Books holding only what setup seeded have
  nothing to keep, exactly as the account erasure decides (:func:`has_books`).
- **Anything entered: locked away, then deleted.** ``closed_at`` takes the
  company out of every list and every route (``get_company_for_user`` refuses
  it), everybody but its owners loses their membership, every key stops,
  every invitation goes, and the confirmed claim on the orgnr stops counting,
  so nobody confirming it later is warned about books nobody works in.
  ``retain_until`` is the last day it is kept. :func:`purge_expired` deletes
  it, stored files included, the day after.

The owner is told to download a SAF-T file for every year first, because that
is the company's own copy of the entries. It is not the whole of the books:
SAF-T carries no bilag. So the books stay here, locked, rather than going the
moment the owner asks, and an owner can open them again (:func:`reopen_company`)
for as long as they are kept, which is how the company produces a receipt for
a bokettersyn three years after it left.

Like the rest of the identity domain this module does not commit. The proof
that the owner is at the keyboard is the account erasure's
(:func:`myntos.domain.identity.service.confirm_identity`), asked by the route.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.core.clock import utc_now
from myntos.domain.bank.models import BankStatementFile
from myntos.domain.bilag.models import Attachment
from myntos.domain.data_sharing.models import DataSharingChoice, DataSharingLevel
from myntos.domain.faktura.models import SalesDocument
from myntos.domain.identity.erasure import has_books
from myntos.domain.identity.errors import (
    CompanyClosureBlockedError,
    CompanyNameMismatchError,
    ReopenBlockedError,
)
from myntos.domain.identity.models import (
    AgentApproval,
    AgentApprovalFile,
    AgentKey,
    AgentKeyStop,
    AgentToken,
    CompanyMembership,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    OrganizationRole,
)

# Models only, never services, for the reason erasure gives: deleting books
# means naming every table that holds them.
from myntos.domain.ledger.errors import CompanyNotFoundError
from myntos.domain.ledger.models import Company, Journal, Party, Transaction
from myntos.domain.mva.models import MvaFiling, MvaFilingDocument
from myntos.saft.models import ImportedDocument, ImportStatus, SaftImport, SaftImportFile
from myntos.skattemelding.models import SkattemeldingFiling, SkattemeldingFilingDocument
from myntos.storage.base import DocumentStorage
from myntos.storage.keys import KEY_PATTERN, thumbnail_key

logger = logging.getLogger(__name__)

#: Bokføringsloven § 13 second paragraph: five years after the end of the
#: financial year, for nr. 1 to 4 of the first paragraph (the annual accounts
#: and other mandatory reporting, specifications, documentation of what was
#: entered, the auditor's letters). Nr. 5 to 8 need three and a half, so five
#: covers everything Myntos holds. Unchanged by lov 19. juni 2026 nr. 39.
RETENTION_YEARS = 5

_OSLO = ZoneInfo("Europe/Oslo")


def today_in_norway() -> date:
    """The date in Norway, which is the date the financial year is reckoned in.

    Not the UTC date: a company deleted at half past midnight on 1 January,
    Norwegian time, has entered something in the new year, and UTC would
    still say the old one and end its retention a year early.
    """
    return utc_now().astimezone(_OSLO).date()


class ClosureOutcome(StrEnum):
    """What deleting the company does to its books."""

    # Nothing entered: gone at once, with the account erasure's reasoning.
    DELETED = "deleted"
    # Something entered: locked away until ``retain_until``, then gone.
    KEPT = "kept"


@dataclass(frozen=True)
class ClosurePlan:
    """What deleting this company would do, worked out before doing any of it.

    The same value answers the page and gates the deletion, as the account
    erasure's plan does, so the page cannot promise what the deletion would
    not do.
    """

    outcome: ClosureOutcome
    # The last day the books are kept. None when they are deleted at once.
    retain_until: date | None
    # The financial years with vouchers in them, oldest first: one SAF-T file
    # each, which is what the page asks the owner to download first.
    years: list[int]
    # People other than the caller whose access ends: everyone but the owners
    # leaves at once, and the owners keep only the right to open it again.
    members_leaving: int
    # Keys for agents that stop for good.
    live_keys: int


def _normalised(name: str) -> str:
    return " ".join(name.split()).casefold()


def require_name(company: Company, typed: str) -> None:
    """Refuse unless ``typed`` is the company's name, give or take case and spaces.

    Asked of everyone, whatever proof of identity they give: it says which
    books they meant, which a password does not. Lenient on case and spacing
    only, because «nordmann consulting as» is plainly the same answer and a
    refusal over a capital letter teaches nothing.
    """
    if _normalised(typed) != _normalised(company.name):
        raise CompanyNameMismatchError(
            f"skriv navnet på selskapet akkurat slik det står: {company.name}"
        )


def retention_end(last_year: int) -> date:
    """The last day books whose latest entry is in ``last_year`` must be kept.

    Financial years in Myntos are calendar years (myntos.saft.move), so
    "five years after the end of the financial year" is 31 December five
    years on.
    """
    return date(last_year + RETENTION_YEARS, 12, 31)


async def _years_with_vouchers(db: AsyncSession, company_id: int) -> list[int]:
    rows = await db.scalars(
        select(Transaction.financial_year)
        .join(Journal, Journal.id == Transaction.journal_id)
        .where(Journal.company_id == company_id)
        .distinct()
        .order_by(Transaction.financial_year)
    )
    return list(rows)


async def plan_closure(
    db: AsyncSession, company: Company, *, user_id: int, today: date | None = None
) -> ClosurePlan:
    """Work out what deleting ``company`` would do, without doing it.

    The retention runs from the later of this year and the last year with a
    voucher in it. This year, because anything entered up to today (a bilag,
    a draft, a filing) belongs to a year no later than this one; the last
    voucher year, because a voucher may be dated ahead, and its year is the one
    whose end the law counts from.
    """
    today = today or today_in_norway()
    years = await _years_with_vouchers(db, company.id)
    kept = await has_books(db, company.id)
    members_leaving = await db.scalar(
        select(func.count())
        .select_from(CompanyMembership)
        .where(
            CompanyMembership.company_id == company.id,
            CompanyMembership.user_id != user_id,
        )
    )
    live_keys = await db.scalar(
        select(func.count())
        .select_from(AgentKey)
        .where(AgentKey.company_id == company.id, AgentKey.revoked_at.is_(None))
    )
    return ClosurePlan(
        outcome=ClosureOutcome.KEPT if kept else ClosureOutcome.DELETED,
        retain_until=retention_end(max([today.year, *years])) if kept else None,
        years=years,
        members_leaving=members_leaving or 0,
        live_keys=live_keys or 0,
    )


async def close_company(
    db: AsyncSession, company: Company, *, user_id: int, today: date | None = None
) -> ClosurePlan:
    """Delete ``company`` from Myntos: at once when empty, after its retention otherwise.

    Re-plans rather than trusting the plan the page showed, for the reason
    the account erasure does: somebody may have posted something in between,
    and an empty company that stopped being empty must be kept, not deleted.
    """
    busy = await db.scalar(
        select(SaftImport.id).where(
            SaftImport.company_id == company.id,
            SaftImport.status.in_((ImportStatus.READING.value, ImportStatus.POSTING.value)),
        )
    )
    if busy is not None:
        raise CompanyClosureBlockedError(
            "en SAF-T-innlesing leses eller føres akkurat nå. Vent til den er ferdig, og prøv igjen"
        )
    plan = await plan_closure(db, company, user_id=user_id, today=today)
    # Anything this request already changed is written while the rows exist,
    # as in the erasure: the commit at the end must not chase deleted rows.
    await db.flush()

    if plan.outcome is ClosureOutcome.DELETED:
        # The database cascades take the seeded chart, VAT codes, bank
        # accounts, register snapshot, memberships, keys and invitations.
        await db.execute(delete(Company).where(Company.id == company.id))
        db.expunge(company)
        return plan

    company.closed_at = utc_now()
    company.retain_until = plan.retain_until
    # The switch goes off as well as every key stopping, so opening the books
    # again later does not bring an agent back with them (agents-and-api.md
    # rule 3: turning access off stops keys for good).
    company.agent_access_enabled = False
    # Sharing goes back to nothing for the same reason, and logged like any
    # other change: these books are no longer being worked in, so there is
    # nothing to improve Myntos with, and a reopening should not quietly resume
    # a choice made for books that were in use (docs/data-sharing.md).
    if company.data_sharing != DataSharingLevel.NONE.value:
        company.data_sharing = DataSharingLevel.NONE.value
        db.add(
            DataSharingChoice(
                company_id=company.id, level=DataSharingLevel.NONE.value, chosen_by_user_id=user_id
            )
        )
    await db.execute(
        update(AgentKey)
        .where(AgentKey.company_id == company.id, AgentKey.revoked_at.is_(None))
        .values(revoked_at=utc_now(), revoked_reason=AgentKeyStop.COMPANY_CLOSED)
        .execution_options(synchronize_session="fetch")
    )
    await db.execute(
        delete(AgentToken).where(
            AgentToken.key_id.in_(select(AgentKey.id).where(AgentKey.company_id == company.id))
        )
    )
    # A request waiting for approval can never run now. Its row stays as the
    # record of what was asked, as on the erasure; its files go.
    await db.execute(
        update(AgentApprovalFile)
        .where(
            AgentApprovalFile.approval_id.in_(
                select(AgentApproval.id).where(AgentApproval.company_id == company.id)
            )
        )
        .values(content=None)
    )
    # Everybody but the owners leaves. Their access was to books being worked
    # in, which these no longer are; the owners stay only so that one of them
    # can open the books again, and nothing shows them the books until then.
    await db.execute(
        delete(CompanyMembership).where(
            CompanyMembership.company_id == company.id,
            CompanyMembership.role != OrganizationRole.OWNER,
        )
    )
    # A pending invitation is a way in, to books nobody may enter, so it goes.
    # An accepted one leads nowhere any more and stays as what it was: the
    # record of who let whom in.
    await db.execute(
        delete(OrganizationInvitation).where(
            OrganizationInvitation.company_id == company.id,
            OrganizationInvitation.accepted_at.is_(None),
        )
    )
    # The uploaded files of an import are a working copy, let go of after 14
    # days when somebody next reads the import. Nobody will read it now. The
    # stored objects go with the next daily sweep (saft.jobs.sweep), which
    # deletes whatever no row points at.
    await db.execute(
        update(SaftImportFile)
        .where(
            SaftImportFile.import_id.in_(
                select(SaftImport.id).where(SaftImport.company_id == company.id)
            ),
            SaftImportFile.content.is_not(None) | SaftImportFile.storage_key.is_not(None),
        )
        .values(content=None, storage_key=None)
    )
    await db.flush()
    return plan


async def list_closed(db: AsyncSession, *, user_id: int) -> list[Company]:
    """The deleted companies this person owns, soonest gone first.

    Owners only, and only their own: the membership is what is left of their
    access, and it is not a way to learn about anybody else's books.
    """
    rows = await db.scalars(
        select(Company)
        .join(CompanyMembership, CompanyMembership.company_id == Company.id)
        .where(
            CompanyMembership.user_id == user_id,
            CompanyMembership.role == OrganizationRole.OWNER,
            Company.closed_at.is_not(None),
        )
        .order_by(Company.retain_until, Company.name)
    )
    return list(rows)


async def get_closed(
    db: AsyncSession, *, company_id: int, user_id: int
) -> tuple[Company, CompanyMembership]:
    """A deleted company this person owns, or the same 404 as any other id.

    The one lookup of a company outside ``get_company_for_user``, and it keeps
    that function's rule: an id that does not exist, one the caller is not an
    owner of, and one that is not deleted all get one answer.
    """
    row = (
        await db.execute(
            select(Company, CompanyMembership)
            .join(CompanyMembership, CompanyMembership.company_id == Company.id)
            .where(
                Company.id == company_id,
                CompanyMembership.user_id == user_id,
                CompanyMembership.role == OrganizationRole.OWNER,
                Company.closed_at.is_not(None),
            )
        )
    ).first()
    if row is None:
        raise CompanyNotFoundError("finner ikke selskapet")
    company, membership = row
    return company, membership


async def reopen_company(db: AsyncSession, company: Company) -> Company:
    """Open deleted books again, as they were, for the owners still on them.

    Refused while new books for the same orgnr have been set up in this
    account since. A confirmed claim made elsewhere in the meantime does not
    stop it: a second confirmed claim on an orgnr is allowed (migration
    c3e7a1f95d20), and these books were confirmed through Altinn before they
    were deleted.

    What closing took is not given back: the colleagues re-join by
    invitation, keys are made anew, and access for agents and sharing with
    Myntos are switched on again by somebody who reads what they mean. Only the
    books return.
    """
    # New books for the same orgnr set up in this account since: two open sets
    # is what uq_company_open_orgnr forbids, and the person decides which goes.
    newer = await db.scalar(
        select(Company.id).where(
            Company.organization_id == company.organization_id,
            Company.orgnr == company.orgnr,
            Company.closed_at.is_(None),
            Company.id != company.id,
        )
    )
    if newer is not None:
        raise ReopenBlockedError(
            f"det er satt opp et nytt regnskap for {company.orgnr} etter at dette ble slettet. "
            "Slett det nye først hvis du vil åpne dette igjen"
        )
    company.closed_at = None
    company.retain_until = None
    await db.flush()
    return company


async def _stored_keys(db: AsyncSession, company_id: int) -> list[str]:
    """Every object in the document store that belongs to these books."""
    keys: list[str] = []
    keys += await db.scalars(
        select(Attachment.storage_key).where(Attachment.company_id == company_id)
    )
    keys += await db.scalars(
        select(BankStatementFile.storage_key).where(BankStatementFile.company_id == company_id)
    )
    keys += await db.scalars(
        select(MvaFilingDocument.storage_key)
        .join(MvaFiling, MvaFiling.id == MvaFilingDocument.filing_id)
        .where(MvaFiling.company_id == company_id)
    )
    keys += await db.scalars(
        select(SkattemeldingFilingDocument.storage_key)
        .join(SkattemeldingFiling, SkattemeldingFiling.id == SkattemeldingFilingDocument.filing_id)
        .where(SkattemeldingFiling.company_id == company_id)
    )
    # The documents kept from the old program's export: the books'
    # documentation from before Myntos, purged with the rest of it.
    keys += await db.scalars(
        select(ImportedDocument.storage_key).where(ImportedDocument.company_id == company_id)
    )
    # A thumbnail may or may not have been made; deleting one that was not is
    # nothing (DocumentStorage.delete).
    thumbnails = [thumbnail_key(key) for key in keys if KEY_PATTERN.match(key)]
    return keys + thumbnails


async def _delete_books(db: AsyncSession, company_id: int) -> None:
    """Delete every row of these books, in an order no RESTRICT can stop.

    The company's foreign keys all cascade, but a few links inside the books
    are RESTRICT on purpose (an invoice's voucher, a voucher line's account,
    a party's balance account), so that nothing in the application can delete
    one half of a pair. One DELETE on the company would leave the order of the
    cascades to the database, and PostgreSQL checks RESTRICT at once. So the
    halves go explicitly, referrers first.
    """
    documents = SalesDocument.company_id == company_id
    # A kreditnota names the faktura it reverses, so kreditnotaer first.
    await db.execute(
        delete(SalesDocument).where(documents, SalesDocument.credits_document_pk.is_not(None))
    )
    await db.execute(delete(SalesDocument).where(documents))
    # Lines, their dimensions and attachments cascade from the voucher.
    await db.execute(
        delete(Transaction).where(
            Transaction.journal_id.in_(select(Journal.id).where(Journal.company_id == company_id))
        )
    )
    await db.execute(delete(Party).where(Party.company_id == company_id))
    organization_id = await db.scalar(
        select(Company.organization_id).where(Company.id == company_id)
    )
    await db.execute(delete(Company).where(Company.id == company_id))
    # An account left with nobody in it and nothing under it answered "whose
    # companies are these" and now answers nothing, as on the erasure. One
    # with members is somebody's account and stays.
    members = await db.scalar(
        select(func.count())
        .select_from(OrganizationMembership)
        .where(OrganizationMembership.organization_id == organization_id)
    )
    companies = await db.scalar(
        select(func.count()).select_from(Company).where(Company.organization_id == organization_id)
    )
    if not members and not companies:
        await db.execute(delete(Organization).where(Organization.id == organization_id))


async def due_for_purge(db: AsyncSession, *, today: date | None = None) -> list[int]:
    """The deleted companies whose last day of retention is behind us."""
    today = today or today_in_norway()
    return list(
        await db.scalars(
            select(Company.id).where(
                Company.closed_at.is_not(None),
                Company.retain_until.is_not(None),
                Company.retain_until < today,
            )
        )
    )


async def purge_expired(
    db: AsyncSession, storage: DocumentStorage, *, today: date | None = None
) -> list[int]:
    """Delete, for good, deleted companies whose retention has run out.

    The stored files go first and the rows after, each company in its own
    commit. A failure halfway leaves rows whose files are partly gone, which
    is harmless for books past their retention, and the next run finishes the
    job. The other order would leave files that no row names, holding
    personal data nothing would ever find to delete.

    Commits, unlike the rest of this module: it is called by the daily job in
    :mod:`myntos.main`, not by a request.
    """
    purged: list[int] = []
    for company_id in await due_for_purge(db, today=today):
        for key in await _stored_keys(db, company_id):
            await storage.delete(key)
        await _delete_books(db, company_id)
        await db.commit()
        logger.warning("Purged company %s: deleted from Myntos and past its retention", company_id)
        purged.append(company_id)
    return purged
