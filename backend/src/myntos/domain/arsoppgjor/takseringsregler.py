"""Skattedirektoratet's takseringsregler for the farm, as data, one entry per year.

Every December Skattedirektoratet publishes a regulation «om taksering av
formues-, inntekts- og fradragsposter mv. som må fastsettes ved skjønn» for the
income year that is ending. Two parts of it reach a farm's books:

- **§ 3-1-4** and **kapittel 5**: what the livestock and the stock of grain,
  potatoes and feed are worth at the turn of the year (the count,
  :mod:`.buskap`);
- **§ 3-2-3, § 3-2-6 and § 3-2-7**: what milk, potatoes, firewood and a horse's
  stall and feed taken out for the household are worth (private uttak,
  :mod:`.uttak`).

**A year is a data entry.** Adding 2026 is adding a :class:`YearRates` to
:data:`RATES` once FOR-2026-12-xx is published, with its Lovdata address; no code
changes. A year with no entry has no rates, and the review refuses a
``normalverdi`` line in it (``ingen_satser``) instead of quietly using last
year's: the values move every year (kyr tilført i året were 8 700 in 2024,
FOR-2024-11-28-2903, and 9 200 in 2025), and a count valued at the wrong
year's rates is a wrong balance sheet that looks right.

**What is deliberately not here.** Rein (formue only, skatteloven § 8-1 (1) a
says the rates do not touch income), pelsdyr skins, oppdrettsfisk, fish and
reindeer meat in the uttak norms (fishing and reindrift are not this add-on),
trav- and galopphester (antatt salgsverdi, which is a value the person states)
and kårbolig (§ 3-2-4: kår is paid as wages in kind and needs the a-melding,
which Myntos does not file).

Sources:

- 2025: FOR-2025-12-01-2404, Skattedirektoratet, kunngjort 03.12.2025,
  ikrafttredelse «Inntektsåret 2025»,
  <https://lovdata.no/dokument/SF/forskrift/2025-12-01-2404>.
  § 3-1-4 was amended by FOR-2025-12-11-2506; the figures here are the
  consolidated text as Lovdata showed it on 2026-10-15.
- The næringsspesifikasjon's own names for the totals (``buskap``,
  ``selvprodusertVareBenyttetIEgenProduksjon``,
  ``raavareOgInnkjoeptHalvfabrikata``) are from Skatteetaten's
  ``naeringsspesifikasjon_v7_ekstern.xsd`` (and v6),
  ``SpesifikasjonAvSkattemessigVarelager``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


class Kind(enum.StrEnum):
    """What a count line is, which decides its accounts."""

    #: Livestock. 1450 (NIBIO 1451-1458), change on 4690.
    BUSKAP = "buskap"
    #: Bought feed, fertiliser, seed, fuel: «innkjøpt varelager». 1400, change on 4090.
    INNKJOPT = "innkjopt"
    #: Grown on the farm: grain, potatoes, hay and silage. 1490 (NIBIO 1440-1448),
    #: change on 4290.
    EGENPRODUSERT = "egenprodusert"


class Valuation(enum.StrEnum):
    """How a line's unit value was arrived at."""

    #: The year's published value per head or per kilo.
    NORMALVERDI = "normalverdi"
    #: The average of the adult animals of the species in the opening count,
    #: kapittel 5 § 5-2 annet ledd. Computed, never typed.
    SNITT = "snitt"
    #: What it cost: bought stock (innkjøpspris med tillegg av frakt) and
    #: fôringsdyr bought for sale (innkjøpspris plus feed to the turn of the year).
    KOSTPRIS = "kostpris"
    #: A value the person states, with the reason in the description: a trav-
    #: or galopphest, an adult horse at the value it has stood at, a crop the
    #: farm can document at another tilvirkningsverdi.
    VERDSATT = "verdsatt"


@dataclass(frozen=True)
class CountCategory:
    """One row of § 3-1-4: a kind of animal or of stock, and what it is worth."""

    code: str
    kind: Kind
    label: str
    #: ``stk``, ``kg`` or ``FEm``.
    unit: str
    #: The published value per unit, or None when the regulation gives a rule
    #: instead of a figure (the average, kostpris, salgsverdi).
    value: Decimal | None
    #: How it is valued when the person does not say.
    valuation: Valuation
    #: For the average rule: the species, and whether a row of this category
    #: in the *opening* count is an adult animal whose value enters the average.
    species: str | None = None
    adult: bool = False
    #: NIBIO's product code, so the line reads the same to a farm accountant.
    nibio: str | None = None


@dataclass(frozen=True)
class UttakProduct:
    """One kind of produce taken out for the household, and its norm."""

    code: str
    label: str
    #: ``husstandsmedlem`` for the norms per person, ``løs m³``, ``hest``, or
    #: ``kg`` / ``stk`` for products valued at omsetningsverdi.
    unit: str
    #: The norm per unit, or None for omsetningsverdi (§ 3-2-2): the person
    #: states what the product would have fetched.
    value: Decimal | None
    #: NIBIO's uttak account for it (3161-3166).
    account: str
    #: Whether it is a næringsmiddel at the 15 % rate, which matters only when
    #: the uttak falls in an alminnelig melding (merverdiavgiftsloven § 5-2).
    #: Raw milk, live or unbutchered animals, firewood and feed are not
    #: (Merverdiavgiftshåndboken M-5-2.4; docs/landbruk.md rule 3).
    food: bool
    note: str = ""


@dataclass(frozen=True)
class YearRates:
    year: int
    #: The regulation, as Lovdata names it.
    source: str
    url: str
    categories: tuple[CountCategory, ...]
    products: tuple[UttakProduct, ...]

    def category(self, code: str) -> CountCategory | None:
        return next((c for c in self.categories if c.code == code), None)

    def product(self, code: str) -> UttakProduct | None:
        return next((p for p in self.products if p.code == code), None)


#: The category every count accepts in every year, rates or not: anything the
#: table does not name, at kostpris or a stated value.
ANNET = "annet"
#: The product every uttak list accepts: anything else, at omsetningsverdi.
ANNET_UTTAK = "annet"

_N = Decimal
_S = Valuation.NORMALVERDI
_A = Valuation.SNITT
_K = Valuation.KOSTPRIS
_V = Valuation.VERDSATT
_B = Kind.BUSKAP
_E = Kind.EGENPRODUSERT

_CATEGORIES_2025: tuple[CountCategory, ...] = (
    # --- Storfe. "Voksne" for the average are storfe over two years: the cows. ---
    CountCategory(
        "storfe_kyr_ib", _B, "Kyr fra inngående balanse", "stk", None, _A, "storfe", True, "511"
    ),
    CountCategory(
        "storfe_kyr_tilfort",
        _B,
        "Kyr tilført i året (har kalvet)",
        "stk",
        _N("9200"),
        _S,
        "storfe",
        True,
        "512",
    ),
    CountCategory(
        "storfe_kviger_over_12",
        _B,
        "Kviger over 12 måneder (ikke kalvet)",
        "stk",
        _N("6400"),
        _S,
        "storfe",
        nibio="514",
    ),
    CountCategory(
        "storfe_kviger_under_12",
        _B,
        "Kviger under 12 måneder",
        "stk",
        _N("2800"),
        _S,
        "storfe",
        nibio="515",
    ),
    CountCategory(
        "storfe_okser_over_12",
        _B,
        "Okser over 12 måneder",
        "stk",
        _N("6400"),
        _S,
        "storfe",
        nibio="516",
    ),
    CountCategory(
        "storfe_okser_under_12",
        _B,
        "Okser under 12 måneder",
        "stk",
        _N("2800"),
        _S,
        "storfe",
        nibio="517",
    ),
    # --- Gris. Adults: avlsgriser over six months. ---
    CountCategory(
        "gris_avl_ib", _B, "Avlsgriser fra inngående balanse", "stk", None, _A, "gris", True, "531"
    ),
    CountCategory(
        "gris_avl_tilfort",
        _B,
        "Avlsgriser tilført i året (over 6 måneder)",
        "stk",
        _N("3100"),
        _S,
        "gris",
        True,
        "532",
    ),
    CountCategory(
        "gris_slakteferdig", _B, "Slakteferdige griser", "stk", _N("2200"), _S, "gris", nibio="535"
    ),
    CountCategory(
        "gris_slakt_4", _B, "Slaktegriser ca. 4 måneder", "stk", _N("1800"), _S, "gris", nibio="536"
    ),
    CountCategory(
        "gris_slakt_3", _B, "Slaktegriser ca. 3 måneder", "stk", _N("1300"), _S, "gris", nibio="537"
    ),
    CountCategory(
        "gris_smagris_2", _B, "Smågriser ca. 2 måneder", "stk", _N("900"), _S, "gris", nibio="538"
    ),
    CountCategory(
        "gris_smagris_under_2",
        _B,
        "Smågriser under 2 måneder",
        "stk",
        _N("440"),
        _S,
        "gris",
        nibio="534",
    ),
    # --- Småfe. Adults: sauer over one year, geiter over nine months. ---
    CountCategory("sau_ib", _B, "Sauer fra inngående balanse", "stk", None, _A, "sau", True, "541"),
    CountCategory(
        "sau_tilfort",
        _B,
        "Sauer tilført i året (over 12 måneder)",
        "stk",
        _N("800"),
        _S,
        "sau",
        True,
        "542",
    ),
    CountCategory(
        "sau_under_12", _B, "Sauer under 12 måneder", "stk", _N("480"), _S, "sau", nibio="543"
    ),
    CountCategory(
        "geit_ib", _B, "Geiter fra inngående balanse", "stk", None, _A, "geit", True, "551"
    ),
    CountCategory(
        "geit_tilfort",
        _B,
        "Geiter tilført i året (over 9 måneder)",
        "stk",
        _N("500"),
        _S,
        "geit",
        True,
        "552",
    ),
    CountCategory(
        "geit_under_9", _B, "Geiter under 9 måneder", "stk", _N("150"), _S, "geit", nibio="553"
    ),
    # --- Fjørfe: slakteverdi per head (FSFIN § 14-5-3), never the average. ---
    CountCategory("fjorfe_verpehoner", _B, "Verpehøner", "stk", _N("4"), _S, nibio="561"),
    CountCategory(
        "fjorfe_kyllinger",
        _B,
        "Kyllinger som skal settes inn i eggproduksjon",
        "stk",
        _N("4"),
        _S,
        nibio="562",
    ),
    CountCategory(
        "fjorfe_oppforing",
        _B,
        "Kyllinger kjøpt inn til oppfôring",
        "stk",
        None,
        _K,
        nibio="565",
    ),
    # --- Hester av eget oppal i næringen. An adult horse keeps its value until
    # it is sold, so «over 3 år» is the value it was entered at, and a later
    # year's count states it (verdsatt). ---
    CountCategory("hest_fjording_1", _B, "Fjording inntil 1 år", "stk", _N("2100"), _S, "hest"),
    CountCategory("hest_fjording_2", _B, "Fjording inntil 2 år", "stk", _N("5000"), _S, "hest"),
    CountCategory("hest_fjording_3", _B, "Fjording inntil 3 år", "stk", _N("7900"), _S, "hest"),
    CountCategory("hest_fjording_eldre", _B, "Fjording over 3 år", "stk", _N("9900"), _S, "hest"),
    CountCategory("hest_dolahest_1", _B, "Dølahest inntil 1 år", "stk", _N("2800"), _S, "hest"),
    CountCategory("hest_dolahest_2", _B, "Dølahest inntil 2 år", "stk", _N("7000"), _S, "hest"),
    CountCategory("hest_dolahest_3", _B, "Dølahest inntil 3 år", "stk", _N("11000"), _S, "hest"),
    CountCategory("hest_dolahest_eldre", _B, "Dølahest over 3 år", "stk", _N("13800"), _S, "hest"),
    CountCategory("bifolk", _B, "Bifolk (uten kube)", "stk", _N("460"), _S),
    # Fôringsdyr bought for sale as slaughter or livdyr: what they cost plus the
    # feed until the turn of the year (§ 5-2, the paragraph after the rates).
    CountCategory("foringsdyr", _B, "Fôringsdyr kjøpt for salg", "stk", None, _K),
    # --- Korn, poteter, fôr: tilvirkningsverdi per kilo or per FEm (b-d).
    # «Dersom ikke noe annet kan dokumenteres» (§ 5-2), so a documented
    # tilvirkningsverdi is stated as verdsatt. ---
    CountCategory("korn", _E, "Korn (hvete, rug, rughvete, bygg, havre)", "kg", _N("1.70"), _S),
    CountCategory("oljefro", _E, "Oljefrø", "kg", _N("3.80"), _S),
    CountCategory("matpoteter", _E, "Matpoteter", "kg", _N("2.00"), _S),
    CountCategory("fabrikkpoteter", _E, "Fabrikkpoteter", "kg", _N("1.30"), _S),
    CountCategory("halm_ammoniakk", _E, "Halm, ammoniakkbehandlet", "kg", _N("1.30"), _S),
    CountCategory("grassurfor", _E, "Grassurfôr", "kg", _N("0.45"), _S),
    # Hay has a figure of its own per kilo (d, «Avlingslager til oppfôring på
    # egen buskap»); only what d does not name is valued per fôrenhet.
    CountCategory("hoy", _E, "Høy", "kg", _N("1.50"), _S),
    CountCategory("andre_formidler", _E, "Andre fôrmidler", "FEm", _N("1.60"), _S),
    # --- Bought stock: innkjøpspris med tillegg av frakt (§ 5-1-2). ---
    CountCategory("innkjopt_kraftfor", Kind.INNKJOPT, "Kraftfôr", "kg", None, _K),
    CountCategory("innkjopt_gjodsel", Kind.INNKJOPT, "Gjødsel og kalk", "kg", None, _K),
    CountCategory("innkjopt_savarer", Kind.INNKJOPT, "Såvarer og settepoteter", "kg", None, _K),
    CountCategory("innkjopt_drivstoff", Kind.INNKJOPT, "Drivstoff", "liter", None, _K),
    CountCategory("innkjopt_annet", Kind.INNKJOPT, "Annet innkjøpt", "stk", None, _K),
)

_PRODUCTS_2025: tuple[UttakProduct, ...] = (
    UttakProduct(
        "melk",
        "Melk",
        "husstandsmedlem",
        _N("410"),
        "3163",
        food=False,
        note="Per husstandsmedlem og år; halv sats (0,5) for personer over 70 år.",
    ),
    UttakProduct(
        "poteter",
        "Poteter",
        "husstandsmedlem",
        _N("130"),
        "3161",
        food=True,
        note="Per husstandsmedlem og år; halv sats (0,5) for barn under 10 og personer over 70 år.",
    ),
    UttakProduct(
        "ved",
        "Ved fra egen skog",
        "løs m³",
        _N("750"),
        "3164",
        food=False,
        note="750 kr per løs m³, som er 2 300 kr per favn.",
    ),
    UttakProduct("flis", "Flis fra egen skog", "løs m³", _N("270"), "3164", food=False),
    UttakProduct(
        "hest_trav",
        "Stallplass og grovfôr, voksen trav- eller galopphest",
        "hest",
        _N("21700"),
        "3166",
        food=False,
        note="Per hest og år. Settes ned skjønnsmessig for føll og unghester.",
    ),
    UttakProduct(
        "hest_hobby",
        "Stallplass og grovfôr, hobbyhest",
        "hest",
        _N("14900"),
        "3166",
        food=False,
    ),
    UttakProduct(
        "hest_ponni", "Stallplass og grovfôr, ponni", "hest", _N("9800"), "3166", food=False
    ),
    # No norm: omsetningsverdi, what the product would have fetched (§ 3-2-2).
    UttakProduct(
        "kjott",
        "Kjøtt og slakt",
        "kg",
        None,
        "3162",
        food=True,
        note="Ingen norm: verdien er det kjøttet ville fått ved salg.",
    ),
    UttakProduct(
        "egg",
        "Egg",
        "kg",
        None,
        "3163",
        food=True,
        note="Ingen norm: verdien er det eggene ville fått ved salg.",
    ),
    UttakProduct(
        "gront",
        "Grønnsaker, frukt og bær",
        "kg",
        None,
        "3161",
        food=True,
        note="Ingen norm: verdien er det varene ville fått ved salg.",
    ),
)

RATES: dict[int, YearRates] = {
    2025: YearRates(
        year=2025,
        source="FOR-2025-12-01-2404 (takseringsreglene for inntektsåret 2025)",
        url="https://lovdata.no/dokument/SF/forskrift/2025-12-01-2404",
        categories=_CATEGORIES_2025,
        products=_PRODUCTS_2025,
    ),
}

#: The fallback product, any year: valued at omsetningsverdi, at 25 % if it
#: ever falls in an alminnelig melding.
ANNET_PRODUCT = UttakProduct(
    ANNET_UTTAK,
    "Annet uttak",
    "stk",
    None,
    "3161",
    food=False,
    note="Verdien er det varen ville fått ved salg.",
)


def for_year(year: int) -> YearRates | None:
    """The year's rates, or None when Skattedirektoratet has not published them (yet)."""
    return RATES.get(year)


def known_years() -> list[int]:
    return sorted(RATES)


def round_average(value: Decimal) -> Decimal:
    """The average of the adults, rounded to the nearest 10 kroner (§ 5-2 femte ledd, last
    sentence: «Ved beregning av ny enhetspris for dyr fra åpningsbalansen … nærmeste kr 10»)."""
    return (value / 10).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * 10
