"""The tax rules for driftsmidler as data: groups, rates and limits, per year, with their source.

Pure data and pure arithmetic: nothing here reads the database, so the whole
law Myntos applies can be read in one screen and tested without a company.

**Where it comes from** (read 2026-10-13):

- Skatteloven §§ 14-40 to 14-48, 14-50 and 14-53
  (<https://lovdata.no/dokument/NL/lov/1999-03-26-14/KAPITTEL_14>), as amended
  by LOV-2023-12-20-98, which raised both beløpsgrenser from 15 000 to 30 000
  kroner with effect from inntektsåret 2024.
- Skatte-ABC 2025/2026, D-2 «Driftsmiddel: avskrivning på/inntektsføring av
  saldo» (D-2-8.2.1 when an asset enters the saldo, D-2-8.6 realisation,
  D-2-9.1 to D-2-9.3 the rates and the rest under 30 000, D-2-10 negative
  saldo, D-2-11 when depreciation may be taken, D-2-15.1 the specification
  per saldo), D-4-2.2.3 (linear depreciation in twelfths), G-4 (gevinst- og
  tapskonto), J-2-8.5 and J-2-8.31 (plansiloer, gjødselkummer, husdyrbygg),
  J-2-8.43 (melkekvote) and T-11-4 (investeringstilskudd).

**Rates are a year's, not constants.** The law changes them (group d had an
extra 10 % for 2020 only, § 14-43 femte ledd, and both beløpsgrenser doubled
in 2024), so every rate is looked up by year in :data:`_YEARS`. The table
starts in 2024, the first year whose limits Myntos has read in force; a year
before it is refused rather than computed with rates nobody checked, and a year
after
:data:`CONFIRMED_THROUGH` is computed with the latest rates and says so.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

ZERO = Decimal("0.00")
_KRONE = Decimal("1")
_CENT = Decimal("0.01")


class Saldogruppe(enum.StrEnum):
    """Skatteloven § 14-41 første ledd, plus the two kinds of asset outside the saldo system."""

    A = "a"  # kontormaskiner o.l.
    B = "b"  # ervervet forretningsverdi
    C = "c"  # vogntog, lastebiler, busser, varebiler, drosjebiler
    D = "d"  # personbiler, traktorer, maskiner, redskap, instrumenter, inventar
    E = "e"  # skip, fartøyer, rigger
    F = "f"  # fly, helikopter
    G = "g"  # anlegg for overføring og distribusjon av elektrisk kraft
    H = "h"  # bygg og anlegg, hoteller, losjihus, bevertningssteder
    I = "i"  # noqa: E741 - the law's own letter; forretningsbygg
    J = "j"  # fast teknisk installasjon i bygninger
    # Tidsbegrensede rettigheter: like store årlige beløp over levetiden
    # (§ 14-50), in twelfths in the year of acquisition (Skatte-ABC D-4-2.2.3).
    LINEAER = "lineaer"
    # Capitalised and never depreciated: land, a farm's bought milk quota
    # (Skatte-ABC J-2-8.43), art. Its cost is deducted only when it is sold,
    # and the gain or loss goes to the gevinst- og tapskonto (§ 14-53).
    IKKE_AVSKRIVBAR = "ikke_avskrivbar"


#: What each group is, in the words of § 14-41 første ledd, for the page and the API.
GROUP_LABELS: dict[Saldogruppe, str] = {
    Saldogruppe.A: "Saldogruppe a: kontormaskiner o.l.",
    Saldogruppe.B: "Saldogruppe b: ervervet forretningsverdi",
    Saldogruppe.C: "Saldogruppe c: varebiler, lastebiler, busser, vogntog, drosjer",
    Saldogruppe.D: "Saldogruppe d: personbiler, traktorer, maskiner, redskap, inventar",
    Saldogruppe.E: "Saldogruppe e: skip, fartøyer, rigger",
    Saldogruppe.F: "Saldogruppe f: fly, helikopter",
    Saldogruppe.G: "Saldogruppe g: anlegg for overføring og distribusjon av elektrisk kraft",
    Saldogruppe.H: "Saldogruppe h: bygg og anlegg",
    Saldogruppe.I: "Saldogruppe i: forretningsbygg",
    Saldogruppe.J: "Saldogruppe j: fast teknisk installasjon i bygninger",
    Saldogruppe.LINEAER: "Lineær avskrivning: tidsbegrenset rettighet",
    Saldogruppe.IKKE_AVSKRIVBAR: "Ikke avskrivbar: tomt, melkekvote o.l.",
}

#: Avskrives under ett: one saldo for the whole group (§ 14-41 fjerde ledd).
#: The law says per næring and per kommune; Myntos keeps one per company
#: (docs/anlegg.md, What is not built).
COLLECTIVE = frozenset({Saldogruppe.A, Saldogruppe.B, Saldogruppe.C, Saldogruppe.D})
#: Hvert driftsmiddel på egen saldo (§ 14-41 fjerde ledd).
PER_ASSET = frozenset({Saldogruppe.E, Saldogruppe.F, Saldogruppe.G, Saldogruppe.H, Saldogruppe.I})
#: Under ett for hver bygning (§ 14-41 fjerde ledd siste punktum).
PER_BUILDING = frozenset({Saldogruppe.J})
#: Sale proceeds reduce the saldo (§ 14-44 annet ledd), so no gain arises.
SALE_REDUCES_SALDO = COLLECTIVE | PER_BUILDING
#: A negative saldo is taken to income, at least at the group's rate (§ 14-46).
#: Group b's negative saldo goes to the gevinst- og tapskonto instead
#: (§ 14-44 tredje ledd annet punktum).
NEGATIVE_INCOME = frozenset({Saldogruppe.A, Saldogruppe.C, Saldogruppe.D, Saldogruppe.J})
#: The groups a building may belong to, for a j saldo to hang off.
BUILDING_GROUPS = frozenset({Saldogruppe.H, Saldogruppe.I})
#: Depreciated at all, by a rate on a saldo.
SALDO_GROUPS = COLLECTIVE | PER_ASSET | PER_BUILDING


class Satsvariant(enum.StrEnum):
    """The raised rates the law allows inside group h, chosen per asset."""

    # § 14-43 tredje ledd: husdyrbygg i jord- og skogbruk, at least half the
    # floor used for the animals (fôrlager in the same building counts; a
    # free-standing fôrlager does not), with a life of more than 20 years.
    HUSDYRBYGG = "husdyrbygg"
    # § 14-43 annet ledd: bygg av så enkel konstruksjon at brukstiden er 20 år
    # eller mindre, and anlegg with the same life. Frittstående plansiloer and
    # gjødselkummer are here (Skatte-ABC J-2-8.5).
    KORT_LEVETID = "kort_levetid"
    # § 14-43 tredje ledd: fruktfelt 10 %, bærfelt 20 %.
    FRUKTFELT = "fruktfelt"
    BAERFELT = "baerfelt"


VARIANT_LABELS: dict[Satsvariant, str] = {
    Satsvariant.HUSDYRBYGG: "Husdyrbygg i jord- og skogbruk",
    Satsvariant.KORT_LEVETID: "Bygg eller anlegg med brukstid på 20 år eller mindre",
    Satsvariant.FRUKTFELT: "Fruktfelt",
    Satsvariant.BAERFELT: "Bærfelt",
}


@dataclass(frozen=True)
class YearRules:
    """The figures that apply to one income year."""

    from_year: int
    #: Maximum rate per group, § 14-43 første ledd.
    rates: dict[Saldogruppe, Decimal]
    #: The raised rates inside group h, § 14-43 annet og tredje ledd.
    variant_rates: dict[Satsvariant, Decimal]
    #: Below this cost an asset may be deducted at once instead of capitalised
    #: (§ 14-40 første ledd: «betydelig» is a cost of this or more).
    capitalisation_limit: Decimal
    #: A positive saldo under this may be deducted whole, a negative one or a
    #: gevinstsaldo under it must be taken to income whole (§ 14-47).
    rest_limit: Decimal
    #: At least this share of a gevinstsaldo is income each year, and at most
    #: this share of a tapssaldo is deducted (§ 14-45 fjerde ledd).
    gain_loss_rate: Decimal
    #: Varig: a useful life of at least this many years (§ 14-40 første ledd).
    minimum_life_years: int
    source: str


_RATES_FROM_2024: dict[Saldogruppe, Decimal] = {
    Saldogruppe.A: Decimal("0.30"),
    Saldogruppe.B: Decimal("0.20"),
    Saldogruppe.C: Decimal("0.24"),
    Saldogruppe.D: Decimal("0.20"),
    Saldogruppe.E: Decimal("0.14"),
    Saldogruppe.F: Decimal("0.12"),
    Saldogruppe.G: Decimal("0.05"),
    Saldogruppe.H: Decimal("0.04"),
    Saldogruppe.I: Decimal("0.02"),
    Saldogruppe.J: Decimal("0.10"),
}

_YEARS: tuple[YearRules, ...] = (
    YearRules(
        from_year=2024,
        rates=_RATES_FROM_2024,
        variant_rates={
            Satsvariant.HUSDYRBYGG: Decimal("0.06"),
            Satsvariant.KORT_LEVETID: Decimal("0.10"),
            Satsvariant.FRUKTFELT: Decimal("0.10"),
            Satsvariant.BAERFELT: Decimal("0.20"),
        },
        capitalisation_limit=Decimal("30000.00"),
        rest_limit=Decimal("30000.00"),
        gain_loss_rate=Decimal("0.20"),
        minimum_life_years=3,
        source=(
            "Skatteloven §§ 14-40, 14-43, 14-45 og 14-47 slik de lyder fra inntektsåret 2024 "
            "(LOV-2023-12-20-98); Skatte-ABC 2025/2026 D-2-9.2"
        ),
    ),
)

#: The last year whose figures somebody has read in force. A later year is
#: computed with the latest entry, and the review says that it has not been
#: checked; the day a budget changes a rate, a new entry goes in above.
CONFIRMED_THROUGH = 2026


def rules_for(year: int) -> YearRules | None:
    """The rules for ``year``, or None before the first year Myntos has read."""
    found: YearRules | None = None
    for entry in _YEARS:
        if entry.from_year <= year:
            found = entry
    return found


def earliest_year() -> int:
    return _YEARS[0].from_year


def rate_for(
    rules: YearRules, group: Saldogruppe, variant: Satsvariant | None = None
) -> Decimal | None:
    """The maximum rate for a saldo, or None for an asset outside the saldo system."""
    if group not in SALDO_GROUPS:
        return None
    if variant is not None and group is Saldogruppe.H:
        return rules.variant_rates[variant]
    return rules.rates[group]


# --- Rounding ------------------------------------------------------------------------
#
# The skattemelding's saldo specification is in whole kroner. A depreciation is
# a maximum («inntil»), so it is rounded *down*: a figure that is a krone short
# is a choice the law allows, one a krone over is not. An income the law sets
# a minimum for («minst») is rounded *up* for the same reason. The amounts the
# rules take whole (a rest under 30 000) are taken exactly, øre and all, so a
# saldo can reach zero.


def max_share(amount: Decimal, rate: Decimal) -> Decimal:
    """``amount * rate``, down to whole kroner: the most a maximum allows."""
    return (amount * rate).quantize(_KRONE, rounding=ROUND_FLOOR).quantize(_CENT)


def min_share(amount: Decimal, rate: Decimal) -> Decimal:
    """``amount * rate``, up to whole kroner: the least a minimum allows."""
    return (amount * rate).quantize(_KRONE, rounding=ROUND_CEILING).quantize(_CENT)


def started_months(start: date, year: int) -> int:
    """Påbegynte måneder an asset is owned in ``year``, from ``start`` (D-4-2.2.3)."""
    if start.year < year:
        return 12
    if start.year > year:
        return 0
    return 12 - start.month + 1


# --- Accounts the depreciation voucher posts to ---------------------------------------
#
# NS 4102's numbers, which Tripletex's and NIBIO's farm charts keep at the top
# level too (6000 bygninger, 6010 transportmidler, 6015 maskiner, 6020
# immaterielle). The gain and loss accounts are 3880 and 7880 rather than 3800
# and 7800, because 3800 is «salgssum anleggsmidler» in more than one program's
# chart, and a gain posted onto another program's sales sum would be counted
# twice; 3880, 3895 and 7880 are the næringsspesifikasjon's own post numbers,
# so the number says what the account is. All are created the first time a
# voucher needs them (chart.ANLEGG_ACCOUNTS).

DEPRECIATION_ACCOUNT: dict[Saldogruppe, str] = {
    Saldogruppe.A: "6015",
    Saldogruppe.B: "6020",
    Saldogruppe.C: "6010",
    Saldogruppe.D: "6015",
    Saldogruppe.E: "6010",
    Saldogruppe.F: "6010",
    Saldogruppe.G: "6000",
    Saldogruppe.H: "6000",
    Saldogruppe.I: "6000",
    Saldogruppe.J: "6000",
    Saldogruppe.LINEAER: "6020",
}
GAIN_ACCOUNT = "3880"
NEGATIVE_SALDO_INCOME_ACCOUNT = "3895"
LOSS_ACCOUNT = "7880"
