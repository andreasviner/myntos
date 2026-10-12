"""Skatteetaten's published SAF-T code lists, read from the files they publish.

Two lists matter to the export. The grouping list says which
``GroupingCategory``/``GroupingCode`` pairs an account may carry in a given
financial year, and the standard tax code list says what a ``StandardTaxCode``
may be. Neither is checked by the XSD, which declares both as free strings, so a
file with an invented pair is schema-valid and wrong. This module is how the
export finds out before the auditor does.

The files are vendored verbatim (``codes/README.md`` says from where) rather
than retyped. The starter chart was once written from the list's documented
shape without the list itself, and half its pairs did not exist; reading the
published file is the only way to be sure the comparison is with what
Skatteetaten actually says.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import cache
from pathlib import Path

_DIR = Path(__file__).parent / "codes"

# Financial year -> the published grouping list that covers it. Skatteetaten
# publishes one list for a span of years ("2025-2026") and replaces it when the
# næringsspesifikasjon changes. A year with no entry here has no published list
# we know of, and is reported as unchecked rather than checked against a
# neighbouring year's list: a post that was retired would pass silently.
GROUPING_LISTS: dict[int, str] = {
    2025: "naeringsspesifikasjon_2025_2026.csv",
    2026: "naeringsspesifikasjon_2025_2026.csv",
}

# The placeholder the list prescribes for an account with genuinely no post.
NOT_APPLICABLE = ("NA", "NA")


def _rows(name: str) -> list[dict[str, str]]:
    # utf-8-sig: the grouping list is published with a byte-order mark, which
    # would otherwise become part of the first column's name.
    with (_DIR / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


@cache
def _grouping_list(name: str) -> frozenset[tuple[str, str]]:
    return frozenset((row["GroupingCategory"], row["GroupingCode"]) for row in _rows(name))


def grouping_pairs(year: int) -> frozenset[tuple[str, str]] | None:
    """Every valid (category, code) pair for ``year``, or ``None`` if unpublished."""
    name = GROUPING_LISTS.get(year)
    return None if name is None else _grouping_list(name)


def is_valid_grouping(year: int, category: str, code: str) -> bool | None:
    """Whether the pair is in the year's list; ``None`` when there is no list."""
    if (category, code) == NOT_APPLICABLE:
        return True
    pairs = grouping_pairs(year)
    return None if pairs is None else (category, code) in pairs


@cache
def standard_tax_codes() -> frozenset[str]:
    return frozenset(row["Code"] for row in _rows("standard_tax_codes.csv"))


@dataclass(frozen=True)
class GroupingEntry:
    """One post in the published list, with the words a person picks it by."""

    category: str
    code: str
    description: str


@cache
def _grouping_entries(name: str) -> tuple[GroupingEntry, ...]:
    return tuple(
        GroupingEntry(row["GroupingCategory"], row["GroupingCode"], row["CodeDescriptionNOB"])
        for row in _rows(name)
    )


def latest_published_year(year: int) -> int:
    """The year whose list stands in for ``year`` when *choosing* a pair.

    Only for offering and accepting a choice on the kontoplan page, never for
    the export check, which must keep saying "unchecked" for a year with no
    list (see :data:`GROUPING_LISTS`). The difference is who is asking: the
    check certifies a file, and the page asks a person to pick from something.
    Refusing every pick for 2027 until Skatteetaten publishes would leave a new
    account with no grouping at all, which the check calls an error rather than
    a note, so the nearest published list is the better of two imperfect
    answers. The check still marks the year as unchecked.
    """
    published = sorted(GROUPING_LISTS)
    earlier = [candidate for candidate in published if candidate <= year]
    return earlier[-1] if earlier else published[0]


def grouping_entries(year: int) -> tuple[GroupingEntry, ...]:
    """The posts a person may choose from for ``year``, in the list's order."""
    return _grouping_entries(GROUPING_LISTS[latest_published_year(year)])


def is_choosable_grouping(year: int, category: str, code: str) -> bool:
    """Whether a person may put this pair on an account for ``year``."""
    if (category, code) == NOT_APPLICABLE:
        return True
    return any(
        entry.category == category and entry.code == code for entry in grouping_entries(year)
    )
