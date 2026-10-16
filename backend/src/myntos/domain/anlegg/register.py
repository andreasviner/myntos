"""The anleggsregister itself: adding, changing and disposing of assets, and the opening saldi.

**What is checked, and why here.** The register is what the saldo is rolled
forward from (:mod:`.saldo`), so an asset that cannot be placed in a saldo is
refused when it is entered rather than found missing in December:

- the account is an asset account in the company's kontoplan (10xx to 12xx,
  NS 4102's anleggsmidler), because the depreciation voucher credits it;
- a grant is no larger than the cost it is a grant towards;
- a raised rate (husdyrbygg, kort levetid, frukt- and bærfelt) belongs to
  group h only, which is where § 14-43 annet and tredje ledd put them;
- a group j installation hangs off a building (an h or i asset) of the same
  company, or off no building at all;
- an asset in a group with a saldo per asset (e to i), a time-limited right or
  a non-depreciable asset, delivered before the register's first year, brings
  its tax value at the start of that year (``opening_value``). Its cost is
  history the old program depreciated, and without the value it would be
  missing from every saldo. An asset in a collective group (a to d) or in j
  never does: it is already inside its group's opening saldo
  (:class:`~myntos.domain.anlegg.models.FixedAssetOpening`), and a value of its
  own would count it twice.

**A closed year is frozen.** Any change that would alter a saldo in a closed
year (the year an asset entered, the year it left, the year its opening value
belongs to) is refused with :class:`RegisterYearClosedError`. That year's
skattemelding was built from the saldo as it stood; the register behind it
must keep saying the same thing, as the ledger behind a closed year does.

**The purchase voucher fills in what it can.** An asset may name the voucher
it was bought on (``transaction_pk``), looked up through the company so a
sibling company's voucher is simply not found. When the account, the cost or
the date is left out, they are read off that voucher's one debit on an asset
account: the person registered the purchase once already, and typing it again
is how the two come to disagree.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.anlegg import saldo
from myntos.domain.anlegg.errors import (
    FixedAssetNotFoundError,
    InvalidFixedAssetError,
    InvalidOpeningSaldoError,
    RegisterYearClosedError,
)
from myntos.domain.anlegg.models import FixedAsset, FixedAssetDepreciation, FixedAssetOpening
from myntos.domain.anlegg.rules import (
    BUILDING_GROUPS,
    COLLECTIVE,
    PER_BUILDING,
    ZERO,
    Saldogruppe,
    Satsvariant,
    rules_for,
)
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.models import Account, AccountType, Company, FinancialYear, Transaction

_CENT = Decimal("0.01")

#: NS 4102's anleggsmidler: immaterielle (10), tomter og bygninger (11),
#: transportmidler, maskiner og inventar (12). Class 13 is financial assets,
#: which are not driftsmidler and are not depreciated.
ASSET_ACCOUNT_PREFIXES = ("10", "11", "12")

#: The groups whose opening saldo is brought per group (or per building for j),
#: and the gevinst- og tapskonto.
OPENING_GROUPS = frozenset({g.value for g in COLLECTIVE | PER_BUILDING} | {saldo.GTK})


@dataclass(frozen=True)
class AssetInput:
    """An asset as a person or an agent describes it. None means "not given"."""

    description: str
    saldogruppe: str
    account_number: str | None = None
    acquired_on: date | None = None
    cost: Decimal | None = None
    grant: Decimal = ZERO
    grant_reduces_cost: bool = True
    rate_variant: str | None = None
    transaction_pk: int | None = None
    building_id: int | None = None
    opening_value: Decimal | None = None
    useful_life_years: int | None = None
    note: str | None = None


@dataclass(frozen=True)
class OpeningInput:
    """One opening saldo: a group (a to d, j, or ``gtk``), its account, and the signed amount."""

    saldogruppe: str
    amount: Decimal
    account_number: str | None = None
    building_id: int | None = None


def as_input(asset: FixedAsset) -> AssetInput:
    """The stored asset as an input, so a partial change can be merged onto it."""
    return AssetInput(
        description=asset.description,
        saldogruppe=asset.saldogruppe,
        account_number=asset.account_number,
        acquired_on=asset.acquired_on,
        cost=asset.cost,
        grant=asset.grant,
        grant_reduces_cost=asset.grant_reduces_cost,
        rate_variant=asset.rate_variant,
        transaction_pk=asset.transaction_pk,
        building_id=asset.building_id,
        opening_value=asset.opening_value,
        useful_life_years=asset.useful_life_years,
        note=asset.note,
    )


# --- Reading ----------------------------------------------------------------------------


async def get_asset(db: AsyncSession, *, company: Company, asset_id: int) -> FixedAsset:
    """One asset of ``company``'s. Another company's is the same 404 as one that never was."""
    asset = await db.scalar(
        select(FixedAsset).where(FixedAsset.id == asset_id, FixedAsset.company_id == company.id)
    )
    if asset is None:
        raise FixedAssetNotFoundError("finner ikke driftsmidlet i anleggsregisteret")
    return asset


async def list_assets(db: AsyncSession, *, company: Company) -> list[FixedAsset]:
    return list(
        await db.scalars(
            select(FixedAsset)
            .where(FixedAsset.company_id == company.id)
            .order_by(FixedAsset.acquired_on, FixedAsset.id)
        )
    )


async def list_openings(db: AsyncSession, *, company: Company) -> list[FixedAssetOpening]:
    return list(
        await db.scalars(
            select(FixedAssetOpening)
            .where(FixedAssetOpening.company_id == company.id)
            .order_by(FixedAssetOpening.saldogruppe, FixedAssetOpening.id)
        )
    )


# --- Shared checks ------------------------------------------------------------------------


def _money(value: Decimal, what: str, *, signed: bool = False) -> Decimal:
    if value != value.quantize(_CENT):
        raise InvalidFixedAssetError(f"{what} har brøkdeler av et øre")
    if not signed and value < ZERO:
        raise InvalidFixedAssetError(f"{what} kan ikke være negativ")
    return value.quantize(_CENT)


async def _closed_years(db: AsyncSession, company: Company) -> set[int]:
    return {
        int(year)
        for year in await db.scalars(
            select(FinancialYear.year).where(
                FinancialYear.company_id == company.id, FinancialYear.closed_at.is_not(None)
            )
        )
    }


def _touched_years(asset: AssetInput | FixedAsset, start: int | None) -> set[int]:
    """The years whose saldo this asset changes: entering, its opening value, and leaving."""
    years: set[int] = set()
    if asset.acquired_on is not None and (start is None or asset.acquired_on.year >= start):
        years.add(asset.acquired_on.year)
    if isinstance(asset, FixedAsset):
        if asset.opening_year is not None:
            years.add(asset.opening_year)
        if asset.disposed_on is not None:
            years.add(asset.disposed_on.year)
    return years


def _refuse_closed(touched: set[int], closed: set[int]) -> None:
    hit = sorted(touched & closed)
    if hit:
        years = ", ".join(str(y) for y in hit)
        raise RegisterYearClosedError(
            f"endringen ville endret saldoen for {years}, som er avsluttet. Skattemeldingen for "
            "et avsluttet år er levert på saldoen slik den sto; gjenåpne året under "
            "Årsavslutning hvis registeret må rettes"
        )


async def _asset_account(
    db: AsyncSession, company: Company, number: str, error: type[Exception]
) -> Account:
    account = await db.scalar(
        select(Account).where(Account.company_id == company.id, Account.account_number == number)
    )
    if account is None:
        raise error(f"konto {number} finnes ikke i kontoplanen")
    if account.account_type is not AccountType.EIENDEL or not number.startswith(
        ASSET_ACCOUNT_PREFIXES
    ):
        raise error(
            f"konto {number} er ikke en konto for anleggsmidler. Driftsmidler står på "
            "kontoene 1000 til 1299 i kontoplanen"
        )
    return account


async def _building(
    db: AsyncSession, company: Company, building_id: int, own_id: int | None, error: type[Exception]
) -> FixedAsset:
    building = await db.scalar(
        select(FixedAsset).where(FixedAsset.id == building_id, FixedAsset.company_id == company.id)
    )
    if building is None:
        raise FixedAssetNotFoundError("finner ikke bygningen i anleggsregisteret")
    if building.id == own_id or Saldogruppe(building.saldogruppe) not in BUILDING_GROUPS:
        raise error(
            "en fast teknisk installasjon hører til en bygning i saldogruppe h eller i "
            "(skatteloven § 14-41 første ledd j)"
        )
    return building


# --- Assets -------------------------------------------------------------------------------


async def _resolve(
    db: AsyncSession, company: Company, data: AssetInput, *, own_id: int | None
) -> tuple[AssetInput, int | None]:
    """Check ``data`` and fill in what the purchase voucher says. Raises on the first problem.

    Returns the checked input and the year its opening value belongs to, which
    nobody types: it is the register's first year.
    """
    description = data.description.strip()
    if not description:
        raise InvalidFixedAssetError("driftsmidlet må ha en beskrivelse")
    try:
        group = Saldogruppe(data.saldogruppe)
    except ValueError:
        raise InvalidFixedAssetError(f"ukjent saldogruppe {data.saldogruppe!r}") from None
    variant: str | None = None
    if data.rate_variant:
        try:
            variant = Satsvariant(data.rate_variant).value
        except ValueError:
            raise InvalidFixedAssetError(f"ukjent satsvariant {data.rate_variant!r}") from None
        if group is not Saldogruppe.H:
            raise InvalidFixedAssetError(
                "forhøyet sats (husdyrbygg, kort levetid, frukt- og bærfelt) gjelder bare "
                "saldogruppe h (skatteloven § 14-43 annet og tredje ledd)"
            )

    account_number = data.account_number.strip() if data.account_number else None
    cost = data.cost
    acquired_on = data.acquired_on
    if data.transaction_pk is not None:
        voucher = await ledger_service.get_voucher(
            db, company=company, voucher_pk=data.transaction_pk
        )
        debits: dict[str, Decimal] = {}
        for line in voucher.lines:
            number = line.account.account_number
            if number.startswith(ASSET_ACCOUNT_PREFIXES) and line.debit > ZERO:
                debits[number] = debits.get(number, ZERO) + line.debit
        if account_number is None:
            if len(debits) != 1:
                raise InvalidFixedAssetError(
                    f"bilag {voucher.transaction_id} har "
                    + ("ingen" if not debits else "flere")
                    + " debetposteringer på anleggskontoer; oppgi konto og kostpris"
                )
            account_number = next(iter(debits))
        if cost is None:
            cost = debits.get(account_number)
        if acquired_on is None:
            acquired_on = voucher.transaction_date
    if account_number is None:
        raise InvalidFixedAssetError("oppgi kontoen driftsmidlet står på")
    if acquired_on is None:
        raise InvalidFixedAssetError("oppgi datoen driftsmidlet ble levert")
    if cost is None or cost <= ZERO:
        raise InvalidFixedAssetError("oppgi kostprisen (inngangsverdien), større enn null")
    cost = _money(cost, "kostprisen")
    grant = _money(data.grant, "tilskuddet")
    if grant > cost:
        raise InvalidFixedAssetError("tilskuddet kan ikke være større enn kostprisen")
    await _asset_account(db, company, account_number, InvalidFixedAssetError)

    building_id = data.building_id
    if building_id is not None:
        if group is not Saldogruppe.J:
            raise InvalidFixedAssetError("bare saldogruppe j (tekniske installasjoner) har bygning")
        await _building(db, company, building_id, own_id, InvalidFixedAssetError)

    life = data.useful_life_years
    if group is Saldogruppe.LINEAER:
        if life is None or not 1 <= life <= 100:
            raise InvalidFixedAssetError(
                "en tidsbegrenset rettighet avskrives lineært over levetiden (skatteloven "
                "§ 14-50); oppgi hvor mange år den varer"
            )
    else:
        life = None

    start = await saldo.start_year(db, company)
    brought = start is not None and acquired_on.year < start
    opening_value = data.opening_value
    opening_year: int | None = None
    if group in COLLECTIVE | PER_BUILDING:
        if opening_value is not None:
            raise InvalidFixedAssetError(
                f"saldogruppe {group.value} avskrives under ett. Verdien ved registerets start "
                "føres som inngående saldo for gruppen, ikke per driftsmiddel"
            )
    elif brought:
        if opening_value is None:
            raise InvalidFixedAssetError(
                f"driftsmidlet ble levert før {start}, det første året anleggsregisteret dekker. "
                f"Oppgi skattemessig verdi (saldoverdi) ved inngangen til {start}"
            )
        opening_value = _money(opening_value, "inngående verdi")
        opening_year = start
    elif opening_value is not None:
        raise InvalidFixedAssetError(
            "inngående verdi gjelder bare driftsmidler levert før anleggsregisterets første år"
        )

    note = (data.note or "").strip()[:1000] or None
    resolved = AssetInput(
        description=description[:255],
        saldogruppe=group.value,
        account_number=account_number,
        acquired_on=acquired_on,
        cost=cost,
        grant=grant,
        grant_reduces_cost=data.grant_reduces_cost,
        rate_variant=variant,
        transaction_pk=data.transaction_pk,
        building_id=building_id,
        opening_value=opening_value,
        useful_life_years=life,
        note=note,
    )
    return resolved, opening_year


def _apply(asset: FixedAsset, data: AssetInput, opening_year: int | None) -> None:
    asset.description = data.description
    asset.saldogruppe = data.saldogruppe
    asset.rate_variant = data.rate_variant
    asset.account_number = data.account_number or ""
    # _resolve refuses an asset without either, so this is for the type
    # checker; raised rather than asserted so it holds under ``python -O``.
    if data.acquired_on is None or data.cost is None:
        raise InvalidFixedAssetError("oppgi datoen driftsmidlet ble levert og kostprisen")
    asset.acquired_on = data.acquired_on
    asset.cost = data.cost
    asset.grant = data.grant
    asset.grant_reduces_cost = data.grant_reduces_cost
    asset.transaction_pk = data.transaction_pk
    asset.building_id = data.building_id
    asset.opening_value = data.opening_value
    asset.opening_year = opening_year
    asset.useful_life_years = data.useful_life_years
    asset.note = data.note


async def create_asset(
    db: AsyncSession, *, company: Company, data: AssetInput, created_by_user_id: int | None = None
) -> FixedAsset:
    resolved, opening_year = await _resolve(db, company, data, own_id=None)
    start = await saldo.start_year(db, company)
    touched = _touched_years(resolved, start)
    if opening_year is not None:
        touched.add(opening_year)
    _refuse_closed(touched, await _closed_years(db, company))
    asset = FixedAsset(company_id=company.id, created_by_user_id=created_by_user_id)
    _apply(asset, resolved, opening_year)
    db.add(asset)
    await db.flush()
    return asset


async def update_asset(
    db: AsyncSession, *, company: Company, asset_id: int, data: AssetInput
) -> FixedAsset:
    """Replace what the register says about an asset, while every year it touches is open."""
    asset = await get_asset(db, company=company, asset_id=asset_id)
    resolved, opening_year = await _resolve(db, company, data, own_id=asset.id)
    start = await saldo.start_year(db, company)
    touched = _touched_years(asset, start) | _touched_years(resolved, start)
    if opening_year is not None:
        touched.add(opening_year)
    _refuse_closed(touched, await _closed_years(db, company))
    if (
        asset.disposed_on is not None
        and resolved.acquired_on is not None
        and asset.disposed_on < resolved.acquired_on
    ):
        raise InvalidFixedAssetError("driftsmidlet kan ikke være levert etter at det gikk ut")
    if resolved.saldogruppe not in BUILDING_GROUPS and await _has_installations(db, asset):
        raise InvalidFixedAssetError(
            "bygningen har tekniske installasjoner (saldogruppe j) knyttet til seg, og må "
            "forbli i saldogruppe h eller i"
        )
    _apply(asset, resolved, opening_year)
    await db.flush()
    return asset


async def _has_installations(db: AsyncSession, asset: FixedAsset) -> bool:
    return bool(
        await db.scalar(
            select(func.count()).select_from(FixedAsset).where(FixedAsset.building_id == asset.id)
        )
    ) or bool(
        await db.scalar(
            select(func.count())
            .select_from(FixedAssetOpening)
            .where(FixedAssetOpening.building_id == asset.id)
        )
    )


async def delete_asset(db: AsyncSession, *, company: Company, asset_id: int) -> None:
    """Take an asset out of the register, for one entered by mistake.

    Not how an asset leaves the business: that is a disposal
    (:func:`dispose`), which the saldo has to see. Refused for a building
    other rows hang off, and for an asset whose years include a closed one.
    """
    asset = await get_asset(db, company=company, asset_id=asset_id)
    start = await saldo.start_year(db, company)
    _refuse_closed(_touched_years(asset, start), await _closed_years(db, company))
    if await _has_installations(db, asset):
        raise InvalidFixedAssetError(
            "bygningen har tekniske installasjoner eller en inngående saldo for saldogruppe j "
            "knyttet til seg. Fjern dem først"
        )
    await db.delete(asset)
    await db.flush()


async def dispose(
    db: AsyncSession,
    *,
    company: Company,
    asset_id: int,
    disposed_on: date,
    proceeds: Decimal,
) -> FixedAsset:
    """Record that the asset left: sold, taken out privately (uttak) or scrapped (``proceeds`` 0).

    Uttak is valued at market value (skatteloven § 5-2), so ``proceeds`` is
    then what the asset would have sold for. The money itself is posted where
    it came in (the register's «Salg av driftsmiddel»); this tells the saldo.
    """
    asset = await get_asset(db, company=company, asset_id=asset_id)
    amount = _money(proceeds, "salgssummen")
    if disposed_on < asset.acquired_on:
        raise InvalidFixedAssetError("avgangen kan ikke være før driftsmidlet ble levert")
    start = await saldo.start_year(db, company)
    if start is not None and disposed_on.year < start:
        raise InvalidFixedAssetError(
            f"avgangen er før {start}, det første året anleggsregisteret dekker. Den er "
            "allerede med i saldoen som ble tatt med fra det gamle programmet"
        )
    touched = _touched_years(asset, start) | {disposed_on.year}
    _refuse_closed(touched, await _closed_years(db, company))
    asset.disposed_on = disposed_on
    asset.disposal_proceeds = amount
    await db.flush()
    return asset


async def undo_disposal(db: AsyncSession, *, company: Company, asset_id: int) -> FixedAsset:
    """Take back a disposal recorded by mistake, while its year is open."""
    asset = await get_asset(db, company=company, asset_id=asset_id)
    if asset.disposed_on is None:
        raise InvalidFixedAssetError("driftsmidlet har ingen registrert avgang")
    start = await saldo.start_year(db, company)
    _refuse_closed(_touched_years(asset, start), await _closed_years(db, company))
    asset.disposed_on = None
    asset.disposal_proceeds = None
    await db.flush()
    return asset


# --- Opening saldi --------------------------------------------------------------------------


async def set_openings(
    db: AsyncSession,
    *,
    company: Company,
    year: int,
    rows: Sequence[OpeningInput],
    created_by_user_id: int | None = None,
) -> list[FixedAssetOpening]:
    """Replace the opening saldi: the tax values the old program carried at the start of ``year``.

    ``year`` becomes the register's first year. The saldi are tax figures from
    last year's skattemelding (RF-1084/næringsspesifikasjonen's saldo
    specification, the column «utgående saldo»), not the balance on the
    account: the two part the first time anything was written down differently
    in the books. An empty list removes them.
    """
    financial_year = await db.scalar(
        select(FinancialYear).where(
            FinancialYear.company_id == company.id, FinancialYear.year == year
        )
    )
    if financial_year is None:
        raise InvalidOpeningSaldoError(f"selskapet har ikke noe regnskapsår {year} i Myntos")
    if rules_for(year) is None:
        raise InvalidOpeningSaldoError(
            "Myntos har satsene og beløpsgrensene fra inntektsåret 2024; inngående saldo kan "
            "ikke føres for et tidligere år"
        )
    existing = await list_openings(db, company=company)
    closed = await _closed_years(db, company)
    _refuse_closed({year} | {row.year for row in existing}, closed)

    earlier = await db.scalar(
        select(func.min(FixedAssetDepreciation.year))
        .join(Transaction, Transaction.id == FixedAssetDepreciation.transaction_pk)
        .where(
            FixedAssetDepreciation.company_id == company.id,
            FixedAssetDepreciation.year < year,
            *saldo.standing_transaction_filter(),
        )
    )
    if earlier is not None:
        raise InvalidOpeningSaldoError(
            f"avskrivningene for {int(earlier)} er ført fra anleggsregisteret, så registeret "
            f"begynner før {year}. Reverser dem først hvis registeret skal begynne i {year}"
        )
    brought = await db.scalar(
        select(func.min(FixedAsset.opening_year)).where(
            FixedAsset.company_id == company.id,
            FixedAsset.opening_year.is_not(None),
            FixedAsset.opening_year != year,
        )
    )
    if brought is not None:
        raise InvalidOpeningSaldoError(
            f"driftsmidler i registeret har inngående verdi ved inngangen til {int(brought)}. "
            "Alle inngående verdier må gjelde samme år; rett dem først"
        )

    seen: set[tuple[str, int | None, str | None]] = set()
    created: list[FixedAssetOpening] = []
    for number, row in enumerate(rows, start=1):
        where = f"linje {number}"
        if row.saldogruppe not in OPENING_GROUPS:
            raise InvalidOpeningSaldoError(
                f"{where}: inngående saldo føres for saldogruppe a, b, c, d og j og for "
                "gevinst- og tapskontoen; driftsmidler i gruppe e til i får sin verdi hver for seg"
            )
        if row.amount != row.amount.quantize(_CENT):
            raise InvalidOpeningSaldoError(f"{where}: beløpet har brøkdeler av et øre")
        account = row.account_number.strip() if row.account_number else None
        if row.saldogruppe == saldo.GTK:
            if account is not None or row.building_id is not None:
                raise InvalidOpeningSaldoError(
                    f"{where}: gevinst- og tapskontoen er en skattemessig saldo uten konto i "
                    "regnskapet"
                )
        else:
            if account is None:
                raise InvalidOpeningSaldoError(f"{where}: oppgi kontoen saldoen står på")
            await _asset_account(db, company, account, InvalidOpeningSaldoError)
        if row.building_id is not None:
            if row.saldogruppe != Saldogruppe.J.value:
                raise InvalidOpeningSaldoError(f"{where}: bare saldogruppe j hører til en bygning")
            await _building(db, company, row.building_id, None, InvalidOpeningSaldoError)
        key = (row.saldogruppe, row.building_id, account)
        if key in seen:
            raise InvalidOpeningSaldoError(f"{where}: samme saldo og konto står to ganger")
        seen.add(key)
        created.append(
            FixedAssetOpening(
                company_id=company.id,
                year=year,
                saldogruppe=row.saldogruppe,
                building_id=row.building_id,
                account_number=account,
                amount=row.amount.quantize(_CENT),
                created_by_user_id=created_by_user_id,
            )
        )
    for old in existing:
        await db.delete(old)
    await db.flush()
    db.add_all(created)
    await db.flush()
    return created
