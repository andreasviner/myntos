"""Write a document in the order its XSD says, and refuse what the XSD says not to send.

The builder in :mod:`myntos.skattemelding.naeringsspesifikasjon` produces a
plain tree: a mapping from element name to a value, a nested mapping, or a list
of either for a repeated element. Nothing in it knows the element order; this
module takes it from :mod:`myntos.skattemelding.schema` and refuses, with the
path, an element the type does not have, a required one that is missing, more
than one of a choice, more than the XSD allows, and above all **an element
Skatteetaten derives** (``skatt:erAvledet``): the reason is in the schema
module's docstring, and it is the one mistake that turns a correct melding
into a refused one.

Amounts are given as :class:`~decimal.Decimal` and written exactly as given,
never through a float. Rounding them to øre is the builder's decision, because
it is the builder that knows which numbers are amounts (a ``prosent`` has ten
decimals).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from myntos.skattemelding.schema import Child, Schema

# A value is a str, int, bool, Decimal or date; a structure is a mapping of
# element names; a repeated element is a list of either. Typed loosely on
# purpose, because the builders assemble their trees a key at a time, and
# checked here, element by element, against the schema instead.
Node = Mapping[str, object]


class DocumentError(Exception):
    """The tree does not fit the schema. A bug in the builder, never the person's doing."""


def _text(value: object, path: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise DocumentError(f"{path}: {value} is not an amount")
        # "f", so 1E+3 is written 1000 and never in exponent form.
        return format(value, "f")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return value
    raise DocumentError(f"{path}: {type(value).__name__} is not a value")


def _as_list(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, (Mapping, str, int, bool, Decimal, date)):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    raise DocumentError(f"{type(value).__name__} is neither a value nor a list of them")


def _fill(
    schema: Schema, element: ET.Element, children: tuple[Child, ...], node: Node, path: str
) -> None:
    names = {child.name for child in children}
    unknown = sorted(set(node) - names)
    if unknown:
        raise DocumentError(f"{path}: no such element here: {unknown}")
    chosen: dict[int, str] = {}
    for child in children:
        values = _as_list(node.get(child.name))
        where = f"{path}/{child.name}"
        if not values:
            if child.min_occurs > 0 and child.choice is None:
                raise DocumentError(f"{where}: required, and missing")
            continue
        if child.derived:
            raise DocumentError(
                f"{where}: Skatteetaten derives this (skatt:erAvledet) and refuses a melding "
                "whose figure differs from its own; it is never sent"
            )
        if child.choice is not None:
            if child.choice in chosen:
                raise DocumentError(f"{where}: only one of {chosen[child.choice]} and this")
            chosen[child.choice] = child.name
        if child.max_occurs is not None and len(values) > child.max_occurs:
            raise DocumentError(f"{where}: at most {child.max_occurs}, got {len(values)}")
        nested = schema.children(child.type_name)
        for value in values:
            sub = ET.SubElement(element, f"{{{schema.namespace}}}{child.name}")
            if nested is None:
                if isinstance(value, Mapping):
                    raise DocumentError(f"{where}: a value, not a structure")
                sub.text = _text(value, where)
            else:
                if not isinstance(value, Mapping):
                    raise DocumentError(f"{where}: a structure, not a value")
                _fill(schema, sub, nested, value, where)


def write(schema: Schema, document: Node) -> bytes:
    """``document`` as XML in ``schema``'s namespace and order, UTF-8 with a declaration."""
    children = schema.children(schema.root_type)
    if children is None:
        raise DocumentError(f"{schema.root}: the root type is not a structure")
    ET.register_namespace("", schema.namespace)
    root = ET.Element(f"{{{schema.namespace}}}{schema.root}")
    _fill(schema, root, children, document, schema.root)
    ET.indent(root, space="  ")
    return bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _overridden(element: ET.Element) -> bool:
    """A derived figure the person set themselves on skatteetaten.no (``erOverstyrt``)."""
    for child in element:
        if _local(child.tag) == "erOverstyrt":
            return "".join(child.itertext()).strip() == "true"
    return False


def _strip_derived(schema: Schema, element: ET.Element, type_name: str) -> None:
    children = schema.children(type_name)
    if children is None:
        return
    specs = {child.name: child for child in children}
    for child in list(element):
        spec = specs.get(_local(child.tag))
        if spec is None:
            continue
        if spec.derived and not _overridden(child):
            element.remove(child)
            continue
        had_children = len(child) > 0
        _strip_derived(schema, child, spec.type_name)
        # A section that held only derived figures goes too, rather than
        # going back as an empty element Skatteetaten never wrote.
        if had_children and len(child) == 0 and spec.min_occurs == 0:
            element.remove(child)


def merge(schema: Schema, content: bytes, additions: Node) -> tuple[bytes, tuple[str, ...]]:
    """Add top-level sections to a document Skatteetaten wrote, and leave the rest as it is.

    For the skattemelding fetched for the year, which Myntos sends back with
    what only the books or the person can say. A section the document already
    has is kept as Skatteetaten wrote it (it may be prefilled, or carry ids
    Skatteetaten matches on), and not in the names returned. A new section goes
    where the XSD puts it among the root's children.

    **Except what Skatteetaten derives**, which is taken out wherever it is.
    An utkast has none, but once a year is filed the melding served is the
    fastsatt one, carrying Skatteetaten's own figures (samlet inntekt, the
    underskudd), and a correction sent back with them is refused
    (``avvikSkattemelding``) as soon as the books have moved: TT02 did exactly
    that on 2026-10-22, to a second forhåndsfastsetting for a year already
    filed. Skatteetaten derives them again from what is sent. A figure the
    person overrode on skatteetaten.no (``erOverstyrt``) is theirs and stays.
    """
    # The stdlib parser: a document Skatteetaten served us, as in envelope.py.
    root = ET.fromstring(content)  # noqa: S314
    if root.tag != f"{{{schema.namespace}}}{schema.root}":
        raise DocumentError(
            f"{_local(root.tag)} in {root.tag} is not a {schema.root} for this schema"
        )
    _strip_derived(schema, root, schema.root_type)
    children = schema.children(schema.root_type) or ()
    order = {child.name: index for index, child in enumerate(children)}
    specs = {child.name: child for child in children}
    present = {_local(child.tag) for child in root}
    added: list[str] = []
    for name, value in additions.items():
        spec = specs.get(name)
        if spec is None:
            raise DocumentError(f"{schema.root}/{name}: no such element here")
        if name in present or not _as_list(value):
            continue
        holder = ET.Element("holder")
        _fill(schema, holder, (spec,), {name: value}, schema.root)
        position = len(root)
        for index, child in enumerate(root):
            if order.get(_local(child.tag), len(order)) > order[name]:
                position = index
                break
        for offset, element in enumerate(list(holder)):
            root.insert(position + offset, element)
        added.append(name)
    ET.register_namespace("", schema.namespace)
    return bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True)), tuple(added)
