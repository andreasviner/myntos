"""Skatteetaten's code lists for the næringsspesifikasjon, read from the files it publishes.

Vendored byte for byte (``README.md`` says from where, and gzipped), for the
reason ``myntos.saft.codes`` gives: a list typed from its documentation is a
list somebody remembered. Four of them decide something here:

- ``resultatregnskapOgBalanse``: every post of the result and the balance,
  under the same category names as SAF-T's ``GroupingCategory``, with its
  ``kategori`` (``positiv`` is added, ``negativ`` subtracted when Skatteetaten
  sums a section), and the kinds of business and regnskapsplikt that may use
  it. Skatteetaten refuses a post outside its regnskapsplikt
  (``N_FEIL_*TYPE``), so Myntos looks first.
- ``permanentForskjellstype`` and ``egenkapitalendringstype``: the types of
  permanent difference and of change in equity, each ``tillegg`` or
  ``fradrag``.
- ``midlertidigForskjellstype``, for the same.

A year with no vendored list is refused, never read with a neighbouring year's:
a post retired in between would pass silently.
"""

from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import cache
from pathlib import Path

_DIR = Path(__file__).parent / "codes"
_NS = "{urn:no:skatteetaten:informasjonsforvaltning:kodeliste:v2}"

YEARS = frozenset({2025, 2026})

RESULT_AND_BALANCE = "resultatregnskapOgBalanse"
PERMANENT_DIFFERENCE = "permanentForskjellstype"
EQUITY_CHANGE = "egenkapitalendringstype"
TEMPORARY_DIFFERENCE = "midlertidigForskjellstype"
ANNUAL_ACCOUNTS_RULES = "regeltypeForAarsregnskap"

# SAF-T's disposition of the result (8800 Årsresultat and its kind). In the
# grouping list an account may carry, and not a post of the næringsspesifikasjon:
# the result is computed there, not disposed of.
SAFT_ONLY_CATEGORY = "resultatDisponeringForSAF-T"


class CodeListError(Exception):
    """No list for the year, or a list Myntos cannot read."""


@dataclass(frozen=True)
class Code:
    #: The sub-list the code is in (a result or balance category), or the
    #: list's own name for a list without sub-lists.
    list_name: str
    code: str
    #: The name as Skatteetaten shows it in bokmål.
    name: str
    #: ``positiv``/``negativ`` for a post, ``tillegg``/``fradrag`` for a
    #: difference or a change in equity.
    kategori: str | None
    #: Empty means every kind; the same for the next.
    business_types: frozenset[str]
    regnskapsplikt: frozenset[str]
    #: A balance post whose value gets the verdsettingsrabatt in the formue
    #: (``BalansekontoGirVerdsettingsrabatt``): driftsmidler, shares.
    gives_discount: bool = False

    def allows(self, *, regnskapsplikt: str, business_type: str) -> bool:
        """Whether a business of this kind and regnskapsplikt may use the code."""
        return (not self.regnskapsplikt or regnskapsplikt in self.regnskapsplikt) and (
            not self.business_types or business_type in self.business_types
        )


def _text(element: ET.Element, name: str) -> str | None:
    found = element.find(f"{_NS}{name}")
    return found.text.strip() if found is not None and found.text else None


def _display_name(kode: ET.Element) -> str:
    for text in kode.iterfind(f"{_NS}visningsnavn/{_NS}spraakTekst"):
        if _text(text, "spraak") == "nb_NO":
            return _text(text, "tekst") or ""
    for text in kode.iterfind(f"{_NS}beskrivelse/{_NS}spraakTekst"):
        return _text(text, "tekst") or ""
    return ""


def _code(list_name: str, kode: ET.Element) -> Code:
    extra = kode.find(f"{_NS}kodetillegg")
    values: dict[str, list[str]] = {}
    if extra is not None:
        for item in extra:
            if item.text:
                values.setdefault(item.tag.removeprefix(_NS), []).append(item.text.strip())
    return Code(
        list_name=list_name,
        code=_text(kode, "tekniskNavn") or "",
        name=_display_name(kode),
        kategori=next(iter(values.get("kategori", ())), None),
        business_types=frozenset(values.get("virksomhetstype", ())),
        regnskapsplikt=frozenset(values.get("regnskapspliktstype", ())),
        gives_discount=values.get("BalansekontoGirVerdsettingsrabatt") == ["true"],
    )


@cache
def codes(year: int, list_name: str) -> tuple[Code, ...]:
    """Every code in one of the year's lists."""
    if year not in YEARS:
        raise CodeListError(f"Myntos har ikke Skatteetatens kodelister for {year}")
    path = _DIR / f"{year}_{list_name}.xml.gz"
    if not path.is_file():
        raise CodeListError(f"kodelisten {list_name} for {year} mangler")
    # The stdlib parser: a vendored file, as in myntos.skattemelding.schema.
    root = ET.fromstring(gzip.decompress(path.read_bytes()))  # noqa: S314
    found: list[Code] = []
    subs = root.findall(f"{_NS}underkodeliste")
    if subs:
        for sub in subs:
            name = _text(sub, "tekniskNavn") or ""
            found += [_code(name, kode) for kode in sub.findall(f"{_NS}kode")]
    else:
        found += [_code(list_name, kode) for kode in root.findall(f"{_NS}kode")]
    return tuple(found)


@cache
def posts(year: int) -> dict[tuple[str, str], Code]:
    """The result and balance posts, by (category, code) as SAF-T pairs them."""
    return {(code.list_name, code.code): code for code in codes(year, RESULT_AND_BALANCE)}


@cache
def by_code(year: int, list_name: str) -> dict[str, Code]:
    """A list without categories, by code."""
    return {code.code: code for code in codes(year, list_name)}
