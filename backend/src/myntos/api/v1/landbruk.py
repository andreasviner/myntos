"""The farming add-on: whether these books have it, and switching it.

Reading needs ``ledger:read``. Switching needs ``ledger:chart``, like changing
the kontoplan, because it is one: switched on it adds NIBIO's accounts, and it
changes which categories every colleague is offered. The rules are in
:mod:`myntos.domain.ledger.landbruk`; this module maps HTTP onto them.
"""

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from myntos.api.deps import KEY_READ, CurrentCompany, DbSession, key_write
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.ledger import landbruk
from myntos.domain.ledger.chart import LANDBRUK_ACCOUNTS
from myntos.domain.ledger.models import Account

router = APIRouter(tags=["landbruk"])


class LandbrukResponse(BaseModel):
    #: Whether the books have the add-on now.
    enabled: bool
    #: Who decided: ``valgt`` when a person switched it, ``naeringskode`` when
    #: the company's SN2007 næringskode did (01 jordbruk, 02 skogbruk on).
    source: str
    industry_code: str | None
    #: The add-on's accounts not in the kontoplan yet. They are added the first
    #: time a category needs one, or all at once by switching the add-on on.
    missing_accounts: list[str]
    #: Set only by a switch that added accounts: the numbers added now, and the
    #: ones the kontoplan already had under the same number with another name,
    #: which were left exactly as they were.
    added_accounts: list[str] = []
    kept_accounts: list[str] = []


class LandbrukRequest(BaseModel):
    #: ``true`` on, ``false`` off, ``null`` to let the næringskode decide again.
    enabled: bool | None


async def _response(
    db: DbSession, context: CurrentCompany, state: landbruk.LandbrukState
) -> LandbrukResponse:
    have = set(
        await db.scalars(
            select(Account.account_number).where(Account.company_id == context.company.id)
        )
    )
    return LandbrukResponse(
        enabled=state.enabled,
        source=state.source,
        industry_code=state.industry_code,
        missing_accounts=[seed.number for seed in LANDBRUK_ACCOUNTS if seed.number not in have],
        added_accounts=list(state.accounts.added) if state.accounts else [],
        kept_accounts=list(state.accounts.kept) if state.accounts else [],
    )


@router.get("/companies/{company_id}/landbruk", openapi_extra=KEY_READ)
async def get_landbruk(context: CurrentCompany, db: DbSession) -> LandbrukResponse:
    """Whether the company has the farming add-on (landbrukstillegget), and why.

    Myntos is for every kind of business. A farm gets an add-on on top:
    the accounts from NIBIO's Landbrukets kodesystem (melk 3071, slakt 3041,
    kraftfôr 4020, produksjonstilskudd 3410-3460, andeler i samvirke 1355,
    buskap 1450), the farm's categories in the register with the right VAT
    (milk, slaughter animals and grain to the buyer are 25 %, not food's 15 %),
    and a bilag reader that knows a settlement from Tine or Nortura is a sale
    the buyer wrote up, not a bill.

    ``enabled`` says whether the books have it and ``source`` who decided: the
    næringskode (``naeringskode``; SN2007 01 jordbruk and 02 skogbruk are on
    unless somebody switched it off) or a person (``valgt``).
    ``missing_accounts`` are the add-on's accounts the kontoplan does not have
    yet. Every role may read this, and every key.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    return await _response(db, context, landbruk.state_of(context.company))


@router.put(
    "/companies/{company_id}/landbruk",
    openapi_extra=key_write("slå landbrukstillegget av eller på"),
)
async def set_landbruk(
    payload: LandbrukRequest, context: CurrentCompany, db: DbSession
) -> LandbrukResponse:
    """Switch the farming add-on on or off, or hand it back to the næringskode.

    ``enabled`` is ``true`` (on), ``false`` (off) or ``null`` (let the
    company's næringskode decide again).

    On, whichever way, adds every account from the add-on the kontoplan lacks,
    for every open year, with Myntos's proposed næringsspesifikasjon post. An
    account the company already has under the same number is left exactly as
    it is and listed in ``kept_accounts``: it may mean something else in these
    books, so tell the person and let them decide on /kontoplan/.

    Off hides the farm categories from the register and from the bilag reader.
    It removes nothing: accounts stay (they may have history; deactivate them
    on /kontoplan/ if they are in the way), and entries already posted in a farm
    category stay as they are and keep their names.

    Undoable by calling it again. Needs the role Administrator or Eier, like
    every change to the kontoplan (403 otherwise).
    """
    require_permission(context.membership.role, Permission.LEDGER_CHART)
    state = await landbruk.set_enabled(db, company=context.company, enabled=payload.enabled)
    return await _response(db, context, state)
