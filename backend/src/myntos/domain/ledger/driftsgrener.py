"""NIBIO's codes for a farm's avdelinger and driftsgrener, as data.

Pure, like :mod:`.chart`: what the farming add-on offers when a farm sets up its
dimensions (:mod:`.dimensions`), and nothing else. A farm wants its result per
driftsgren, milk apart from beef apart from the forest, as Tripletex, Duett and
PowerOffice give it, and every farm accountant already uses NIBIO's codes for
that. Offering them means a farm moving from one of those programs keeps the
numbers it knows, and a Dataflyt import later maps one to one.

Read from NIBIO's «Landbrukets kodesystem», version 1.3.6 (December 2025), the
spreadsheet «Offentlig grunnlag Landbrukets_Kodesystem_Versjon-1.3.6.xlsm» at
<https://www.nibio.no/tema/landbruksokonomi/standarder-for-koder-i-landbruket>,
read on 2026-10-13. It has three areas, and two of them are here:

**Avdeling (sheet «Kontoplan», column «Avd»).** NIBIO's chart puts every account
in a department: 10 Finans, 20 Jordbruk, 30 Skogbruk, 50 Pelsdyr, 60 Annen
næring, 90 Privat, and writes it before the account (20.3071 is milk in
jordbruk). Tripletex's farm module writes it after the account instead
(``307120``), and carries the same department on each line as its own
avdeling. These are næringer more than driftsgrener: they separate the farm
from the forest and from the farmer's other business, which is what the
næringsspesifikasjon needs when the books hold two næringer. NIBIO is not
consistent about the last one: the chart says 60 «Annen næring», and the same
spreadsheet's posting table (sheet «Produkt», «Konteringstabell -
Avdelingskode=70») posts «Andre næringer» to 70, which is also what Tripletex
calls «Annen næring». The chart is the list, so 60 is offered; a file that uses
70 brings its own 70 along when it is imported (:mod:`myntos.saft.voucher_import`).

**Driftsgrein (sheet «Driftsgrein»).** The production lines, coded on four
hierarchical digits: 6000 Husdyrproduksjon, 6100 Storfeproduksjon, 6110
Melkeproduksjon storfe, 6111 the same without rearing. NIBIO says the codes are
meant first of all for analysis and planning per driftsgren, and that they «synes
nå som i overkant detaljerte». So the list below is not the whole sheet (373
lines) but the level a farm reports its result on: NIBIO's second level, and the
third where the second puts unlike productions together (milk, beef and ammeku
under storfe; milk and meat under geit; meat and eggs under fjørfe), with the
forest as one line, as a farm keeps it. The codes and names are NIBIO's, with
four changes: 1500 without the sheet's remark «(samlekode for EK)», 5500
shortened from «… - Utplantingsplanter (1. årige)», and 6510, 6520 and 6530
qualified with «Fjørfe», because out of their place in the hierarchy
«Kjøttproduksjon» and «Eggproduksjon» say nothing about which animal. Pelsdyr
(9100) is left out: fur farming has been forbidden in Norway since 1 February
2025.

**Not here: NIBIO's product codes** (sheet «Produkt», 4000 Husdyr, slakt …),
which say what a line on a settlement is rather than which driftsgren it belongs
to. The three digits Tripletex writes after the department (``145120512``: 512
«Kyr tilført i året») are its own list of the same kind, and a file that carries
them brings them as a «Produkt» dimension of its own.
"""

from __future__ import annotations

from dataclasses import dataclass

#: SAF-T ``AnalysisType`` for each kind Myntos knows by name. Nine characters at
#: most, which is what the schema allows (``SAFcodeType``); spelled out rather
#: than one letter, because the code is what an auditor reads in the file.
AVDELING = "AVDELING"
PROSJEKT = "PROSJEKT"
DRIFTSGREN = "DRIFTSGR"
PRODUKT = "PRODUKT"

#: The name each of those kinds goes by, which is also how a file's own analysis
#: type is recognised as one of them (by its description, case aside).
KIND_NAMES: dict[str, str] = {
    AVDELING: "Avdeling",
    PROSJEKT: "Prosjekt",
    DRIFTSGREN: "Driftsgren",
    PRODUKT: "Produkt",
}

#: Words that name a kind in another program's file, for :func:`kind_of`.
#: «driftsgrein» is NIBIO's and nynorsk's spelling of «driftsgren».
_KIND_WORDS: tuple[tuple[str, str], ...] = (
    ("avdeling", AVDELING),
    ("department", AVDELING),
    ("prosjekt", PROSJEKT),
    ("project", PROSJEKT),
    ("driftsgren", DRIFTSGREN),
    ("driftsgrein", DRIFTSGREN),
    ("produkt", PRODUKT),
    ("product", PRODUKT),
)


def kind_of(description: str) -> str | None:
    """The kind a dimension's name says it is: «Avdeling» is :data:`AVDELING`."""
    words = description.casefold().split()
    for word, kind in _KIND_WORDS:
        if word in words:
            return kind
    return None


@dataclass(frozen=True)
class Code:
    """One of NIBIO's codes, with the heading it is listed under."""

    code: str
    name: str
    group: str


#: NIBIO's departments (Landbrukets kodesystem 1.3.6, sheet «Kontoplan»).
AVDELINGER: tuple[Code, ...] = (
    Code("10", "Finans", "NIBIOs avdelinger"),
    Code("20", "Jordbruk", "NIBIOs avdelinger"),
    Code("30", "Skogbruk", "NIBIOs avdelinger"),
    Code("50", "Pelsdyr", "NIBIOs avdelinger"),
    Code("60", "Annen næring", "NIBIOs avdelinger"),
    Code("90", "Privat", "NIBIOs avdelinger"),
)

#: The names of NIBIO's departments by code, for an account id that carries one
#: (``291610``) in a file whose own analysis table does not name it. 70 is the
#: posting table's «Andre næringer», which Tripletex calls «Annen næring».
DEPARTMENT_NAMES: dict[str, str] = {code.code: code.name for code in AVDELINGER} | {
    "70": "Annen næring"
}

_PLANTE = "Planteproduksjon jordbruk"
_GRONT = "Potet- og grønnsakproduksjon"
_FRUKT = "Frukt- og bærproduksjon"
_BLOMST = "Blomsterproduksjon"
_HUSDYR = "Husdyrproduksjon"
_SKOG = "Skogbruksproduksjon"
_ANNEN = "Tilleggsnæring og annen næring"

#: NIBIO's driftsgreinkoder (Landbrukets kodesystem 1.3.6, sheet «Driftsgrein»),
#: at the level a farm reports its result on. See the module docstring.
DRIFTSGRENER: tuple[Code, ...] = (
    Code("1100", "Kornproduksjon til mat og fôrkorn", _PLANTE),
    Code("1200", "Såkornproduksjon", _PLANTE),
    Code("1300", "Oljefrø- og belgvekstproduksjon", _PLANTE),
    Code("1400", "Gras- og kløverfrøproduksjon", _PLANTE),
    Code("1500", "Grovfôrproduksjon", _PLANTE),
    Code("1900", "Annen planteproduksjon i jordbruket", _PLANTE),
    Code("2100", "Potetproduksjon", _GRONT),
    Code("2200", "Kålvekstproduksjoner", _GRONT),
    Code("2500", "Gulrotproduksjon (rotvekst)", _GRONT),
    Code("2600", "Andre rotvekstproduksjoner", _GRONT),
    Code("2800", "Salat- og bladgrønnsakproduksjoner", _GRONT),
    Code("3100", "Løk- og purrevekstproduksjoner", _GRONT),
    Code("3200", "Stengelvekstproduksjoner", _GRONT),
    Code("3300", "Agurkproduksjon (grønnsaksfrukt)", _GRONT),
    Code("3400", "Tomat- og annen grønnsaksproduksjon", _GRONT),
    Code("3600", "Frø- og belgvekstproduksjon", _GRONT),
    Code("3700", "Urteproduksjon", _GRONT),
    Code("3800", "Sopproduksjon", _GRONT),
    Code("4100", "Fruktproduksjon", _FRUKT),
    Code("4500", "Bærproduksjon", _FRUKT),
    Code("5100", "Produksjon av potteplanter", _BLOMST),
    Code("5300", "Produksjon av snittblomster", _BLOMST),
    Code("5500", "Produksjon av sommerplanter (utplantingsplanter)", _BLOMST),
    Code("5700", "Produksjon av planteskolevarer", _BLOMST),
    Code("6110", "Melkeproduksjon storfe", _HUSDYR),
    Code("6120", "Kjøttproduksjon, storfe", _HUSDYR),
    Code("6130", "Ammekuproduksjon", _HUSDYR),
    Code("6140", "Oppdrett, produksjon av livdyr storfe", _HUSDYR),
    Code("6200", "Griseproduksjon", _HUSDYR),
    Code("6300", "Saueproduksjon", _HUSDYR),
    Code("6410", "Melkeproduksjon geit", _HUSDYR),
    Code("6420", "Kjøttproduksjon geit", _HUSDYR),
    Code("6510", "Fjørfe, kjøttproduksjon", _HUSDYR),
    Code("6520", "Fjørfe, oppdrett og livdyr", _HUSDYR),
    Code("6530", "Fjørfe, eggproduksjon", _HUSDYR),
    Code("6910", "Hest, husdyrproduksjon", _HUSDYR),
    Code("6920", "Hjort, husdyrproduksjon", _HUSDYR),
    Code("6930", "Bier, husdyrproduksjon", _HUSDYR),
    Code("6990", "Produksjon andre dyreslag", _HUSDYR),
    Code("7000", "Skogbruksproduksjon", _SKOG),
    Code("8100", "Utleie", _ANNEN),
    Code("8200", "Maskinkjøring", _ANNEN),
    Code("8300", "Foredling/vareproduksjon", _ANNEN),
    Code("8400", "Utmarksnæring", _ANNEN),
    Code("8500", "Landbruksrelatert reiseliv (turisme)", _ANNEN),
    Code("8600", "Annen tjenesteyting", _ANNEN),
    Code("8700", "Annen biologisk produksjon", _ANNEN),
    Code("8800", "Annen næring", _ANNEN),
)

#: What the farming add-on offers, per kind.
FARM_SETS: dict[str, tuple[Code, ...]] = {
    DRIFTSGREN: DRIFTSGRENER,
    AVDELING: AVDELINGER,
}

#: Where the codes come from, for the page and the API to say so.
SOURCE = "NIBIO, Landbrukets kodesystem 1.3.6 (desember 2025)"

__all__ = [
    "AVDELING",
    "AVDELINGER",
    "DEPARTMENT_NAMES",
    "DRIFTSGREN",
    "DRIFTSGRENER",
    "FARM_SETS",
    "KIND_NAMES",
    "PRODUKT",
    "PROSJEKT",
    "SOURCE",
    "Code",
    "kind_of",
]
