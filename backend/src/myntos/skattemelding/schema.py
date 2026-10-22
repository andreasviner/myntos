"""Skatteetaten's XSDs, read for the three things they decide.

The næringsspesifikasjon is a deep document (some 240 complex types) whose
element order is an ``xsd:sequence`` everywhere, so an element in the wrong
place is a schema error, and the order changes between income years: v7 for
2026 moved ``sumEgenkapital`` after the lists it sums. A writer with the order
typed into it is right for the year it was written in. So the order is read
from the year's own XSD, vendored beside this module (``README.md``), and so
are the two other facts that decide what may be written:

- **cardinality**, so a missing required element or a repeated single one is
  this module's error and not a 400 from Skatteetaten;
- **what Skatteetaten derives itself** (``skatt:erAvledet="true"``): sums,
  results, the saldo's closing value, the year's depreciation. Sending one is
  worse than useless. Skatteetaten recomputes it, and where the two disagree
  validation answers ``avvikNaeringsopplysninger`` and refuses the melding, so
  a derived element is something :mod:`myntos.skattemelding.xmlwriter` refuses
  to write at all.

Only what these XSDs use is read: named complex types with a sequence, a
choice inside one, and ``complexContent/extension`` of a base type. Anything
else raises, so a construct a future year adds is found by the tests rather
than silently skipped.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path

_DIR = Path(__file__).parent / "xsd"

_XSD = "{http://www.w3.org/2001/XMLSchema}"
_DERIVED = "{http://www.skatteetaten.no/xsd}erAvledet"

# Income year -> the næringsspesifikasjon XSD for it. A year with no entry is
# refused rather than written with a neighbouring year's schema, which would be
# valid against nothing Skatteetaten accepts. See README.md for adding a year.
NAERINGSSPESIFIKASJON: dict[int, str] = {
    2025: "naeringsspesifikasjon_v6_ekstern.xsd",
    2026: "naeringsspesifikasjon_v7_ekstern.xsd",
}

# Income year -> the upersonlig skattemelding (an AS's). Read to add the
# sections only the books or the person can fill to the utkast Skatteetaten
# serves (myntos.skattemelding.upersonlig), in the place the XSD puts them.
SKATTEMELDING_UPERSONLIG: dict[int, str] = {
    2025: "skattemeldingUpersonlig_v5_ekstern.xsd",
    2026: "skattemeldingUpersonlig_v6_ekstern.xsd",
}

# The envelope, one version for every year.
REQUEST = "skattemeldingognaeringsspesifikasjonrequest_v2.xsd"
RESPONSE = "skattemeldingognaeringsspesifikasjonresponse_v2.xsd"


class SchemaError(Exception):
    """The XSD uses something this reader does not understand, or a year has none."""


@dataclass(frozen=True)
class Child:
    """One element a complex type may contain, in the place the XSD puts it."""

    name: str
    #: The element's type, without a prefix. A simple type when the schema has
    #: no complex type by that name (``xsd:string`` and the like included).
    type_name: str
    min_occurs: int
    #: None for ``unbounded``.
    max_occurs: int | None
    #: ``skatt:erAvledet``: computed by Skatteetaten, never sent.
    derived: bool
    #: The ``xsd:choice`` this element belongs to, numbered within its type;
    #: at most one element of a choice may be present.
    choice: int | None = None


@dataclass(frozen=True)
class Schema:
    namespace: str
    root: str
    root_type: str
    types: Mapping[str, tuple[Child, ...]]

    def children(self, type_name: str) -> tuple[Child, ...] | None:
        """The elements a type holds, in order; ``None`` for a simple type."""
        return self.types.get(type_name)


def _local(name: str) -> str:
    return name.rsplit(":", 1)[-1]


def _occurs(value: str | None, default: int) -> int | None:
    if value is None:
        return default
    return None if value == "unbounded" else int(value)


def _particles(group: ET.Element, *, choice: int | None, counter: list[int]) -> list[Child]:
    found: list[Child] = []
    for particle in group:
        tag = particle.tag
        if tag == f"{_XSD}element":
            name = particle.get("name")
            type_name = particle.get("type")
            if name is None or type_name is None:
                raise SchemaError(f"an element without a name or a type: {particle.attrib}")
            min_occurs = _occurs(particle.get("minOccurs"), 1)
            found.append(
                Child(
                    name=name,
                    type_name=_local(type_name),
                    min_occurs=0 if min_occurs is None else min_occurs,
                    max_occurs=_occurs(particle.get("maxOccurs"), 1),
                    derived=particle.get(_DERIVED) == "true",
                    choice=choice,
                )
            )
        elif tag == f"{_XSD}choice":
            counter[0] += 1
            found += _particles(particle, choice=counter[0], counter=counter)
        elif tag == f"{_XSD}sequence":
            found += _particles(particle, choice=choice, counter=counter)
        elif tag == f"{_XSD}annotation":
            continue
        else:
            raise SchemaError(f"unsupported particle {tag}")
    return found


def _complex_types(root: ET.Element) -> dict[str, ET.Element]:
    types: dict[str, ET.Element] = {}
    for element in root.findall(f"{_XSD}complexType"):
        name = element.get("name")
        if name is not None:
            types[name] = element
    return types


def _resolve(
    name: str, raw: Mapping[str, ET.Element], done: dict[str, tuple[Child, ...]]
) -> tuple[Child, ...]:
    if name in done:
        return done[name]
    element = raw[name]
    counter = [0]
    children: list[Child] = []
    for part in element:
        if part.tag == f"{_XSD}sequence":
            children += _particles(part, choice=None, counter=counter)
        elif part.tag == f"{_XSD}complexContent":
            extension = part.find(f"{_XSD}extension")
            if extension is None or extension.get("base") is None:
                raise SchemaError(f"{name}: only complexContent/extension is supported")
            children += _resolve(_local(extension.get("base") or ""), raw, done)
            # The base's choices are numbered in its own type; the extension's
            # continue from where they stopped, so the two never share a number.
            counter[0] = max((c.choice or 0) for c in children) if children else 0
            for group in extension:
                if group.tag == f"{_XSD}sequence":
                    children += _particles(group, choice=None, counter=counter)
                elif group.tag != f"{_XSD}annotation":
                    raise SchemaError(f"{name}: unsupported {group.tag} in an extension")
        elif part.tag in (f"{_XSD}annotation", f"{_XSD}attribute"):
            continue
        else:
            raise SchemaError(f"{name}: unsupported {part.tag}")
    done[name] = tuple(children)
    return done[name]


@cache
def load(filename: str) -> Schema:
    """One vendored XSD, read once per process."""
    # The stdlib parser, for the reason myntos.domain.mva.melding gives: these
    # files are vendored in the package, not input.
    root = ET.parse(_DIR / filename).getroot()  # noqa: S314
    namespace = root.get("targetNamespace")
    top = root.find(f"{_XSD}element")
    if namespace is None or top is None or top.get("name") is None or top.get("type") is None:
        raise SchemaError(f"{filename}: no target namespace or root element")
    raw = _complex_types(root)
    done: dict[str, tuple[Child, ...]] = {}
    for name in raw:
        _resolve(name, raw, done)
    return Schema(
        namespace=namespace,
        root=top.get("name") or "",
        root_type=_local(top.get("type") or ""),
        types=done,
    )


def skattemelding_upersonlig(year: int) -> Schema:
    """The upersonlig skattemelding schema for an income year, or :class:`SchemaError`."""
    filename = SKATTEMELDING_UPERSONLIG.get(year)
    if filename is None:
        raise SchemaError(f"Myntos har ikke Skatteetatens skjema for skattemeldingen for {year}")
    return load(filename)


def naeringsspesifikasjon(year: int) -> Schema:
    """The næringsspesifikasjon schema for an income year, or :class:`SchemaError`."""
    filename = NAERINGSSPESIFIKASJON.get(year)
    if filename is None:
        raise SchemaError(
            f"Myntos har ikke Skatteetatens skjema for næringsspesifikasjonen for {year}"
        )
    return load(filename)
