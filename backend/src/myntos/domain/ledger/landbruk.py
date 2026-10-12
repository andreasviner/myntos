"""The farming add-on: whether a set of books has it, and switching it on.

Myntos is an accounting program for every business, not a farm program. A farm
still needs things nobody else does: NIBIO's accounts (milk on 3071, kraftfôr
on 4020, the share in Tine on 1355), categories that know milk to the dairy is
25 % and a settlement from Tine is a sale, and a model told the same. So they
come as an add-on, and nothing in it changes what a company without it sees
(docs/landbruk.md).

**Who has it.** :attr:`~myntos.domain.ledger.models.Company.landbruk` says,
when a person has said. Until then the næringskode decides: SN2007 division 01
is jordbruk, 02 skogbruk, and a company registered under either is a farm in
every sense the add-on cares about. Fishing (03) is primærnæring too, but its
products, rates and settlements are different, and it gets nothing from a
farm's catalogue; it is not covered by this.

**Switching it on adds the accounts.** A farm expects to open the kontoplan and
find its accounts there, and an opening balance brought from Duett or
Tripletex carries buskap on 1450 and the Tine share on 1355 before anything has
been posted. So :func:`ensure_accounts` adds every one that is missing, for
every open year, as Myntos's own proposal (``chosen_at`` empty, so a corrected
grouping in a later release still reaches it). An account the company already
has under the same number is left exactly as it is, and reported, because it
may mean something else in these books, and the person is the one to decide.

Posting a farm category in books without the add-on is refused rather than
quietly switching it on: an agent with a key must not be able to change what
the whole company sees by booking one payment.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.ledger import kontoplan
from myntos.domain.ledger.chart import LANDBRUK_ACCOUNTS
from myntos.domain.ledger.errors import SegmentNotEnabledError
from myntos.domain.ledger.models import Account, Company

#: The add-on's name, as categories carry it in ``segment``.
SEGMENT = "landbruk"

#: SN2007 divisions that are farms: 01 jordbruk og tjenester tilknyttet
#: jordbruk, jakt og viltstell; 02 skogbruk og tjenester tilknyttet skogbruk.
FARM_DIVISIONS: tuple[str, ...] = ("01", "02")


def by_industry(industry_code: str | None) -> bool:
    """Whether a næringskode is in a farming division."""
    if industry_code is None:
        return False
    return industry_code.strip()[:2] in FARM_DIVISIONS


def is_enabled(company: Company) -> bool:
    """Whether these books have the add-on: the person's word, else the næringskode."""
    if company.landbruk is not None:
        return company.landbruk
    return by_industry(company.industry_code)


def segments_for(company: Company) -> frozenset[str]:
    """The add-ons whose categories this company is offered."""
    return frozenset({SEGMENT}) if is_enabled(company) else frozenset()


def require_segment(company: Company, segment: str | None) -> None:
    """Refuse a category from an add-on these books do not have."""
    if segment is None:
        return
    if segment == SEGMENT and is_enabled(company):
        return
    raise SegmentNotEnabledError(
        "typen hører til landbrukstillegget, og det er ikke slått på for dette selskapet. "
        "Slå det på under Kontoplan, eller velg en annen type"
    )


@dataclass(frozen=True)
class AccountsAdded:
    """What :func:`ensure_accounts` did to the kontoplan."""

    #: Numbers added now.
    added: tuple[str, ...]
    #: Numbers the company already had, left as they were. Each may mean
    #: something else in these books; the person decides.
    kept: tuple[str, ...]


async def ensure_accounts(db: AsyncSession, *, company: Company) -> AccountsAdded:
    """Add every farm account the kontoplan lacks, and say which were already there."""
    existing = set(
        await db.scalars(select(Account.account_number).where(Account.company_id == company.id))
    )
    added: list[str] = []
    kept: list[str] = []
    for seed in LANDBRUK_ACCOUNTS:
        if seed.number in existing:
            # Kept only when it means something else; one Myntos added before
            # is simply there.
            if not await _is_ours(db, company=company, number=seed.number):
                kept.append(seed.number)
            continue
        await kontoplan.ensure_seeded_account(db, company=company, number=seed.number)
        added.append(seed.number)
    return AccountsAdded(added=tuple(added), kept=tuple(kept))


async def _is_ours(db: AsyncSession, *, company: Company, number: str) -> bool:
    """Whether the company's account under ``number`` is the add-on's own, by name."""
    description = await db.scalar(
        select(Account.description).where(
            Account.company_id == company.id, Account.account_number == number
        )
    )
    seed = next(seed for seed in LANDBRUK_ACCOUNTS if seed.number == number)
    return description == seed.description


@dataclass(frozen=True)
class LandbrukState:
    """Where the add-on stands for one company, as the page shows it."""

    enabled: bool
    #: ``"valgt"`` when a person decided, ``"naeringskode"`` when the register
    #: did, so the page can say why it is on (or off) without being asked.
    source: str
    industry_code: str | None
    accounts: AccountsAdded | None = None


def state_of(company: Company, accounts: AccountsAdded | None = None) -> LandbrukState:
    return LandbrukState(
        enabled=is_enabled(company),
        source="valgt" if company.landbruk is not None else "naeringskode",
        industry_code=company.industry_code,
        accounts=accounts,
    )


async def set_enabled(db: AsyncSession, *, company: Company, enabled: bool | None) -> LandbrukState:
    """Switch the add-on on or off, or hand the decision back to the næringskode.

    On, by either route, adds the accounts. Off leaves every account where it
    is: they may have history, and a kontoplan is never shrunk behind somebody's
    back (the person can deactivate them on /kontoplan/). Posted entries in a
    farm category stay as they were and keep reading back by their names.
    """
    company.landbruk = enabled
    await db.flush()
    accounts = await ensure_accounts(db, company=company) if is_enabled(company) else None
    return state_of(company, accounts)
