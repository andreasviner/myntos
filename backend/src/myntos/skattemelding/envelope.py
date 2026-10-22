"""The envelope a skattemelding travels in, both ways.

Out: ``skattemeldingOgNaeringsspesifikasjonRequest``, which holds the
skattemelding and the næringsspesifikasjon as base64 documents, names the
skattemelding it answers (``dokumentreferanseTilGjeldendeDokument``, the id the
fetch gave), and is posted to validation and uploaded to Altinn byte for byte
the same. In: the fetch's answer, whose document is the utkast Myntos sends
back, and validation's, which says whether Skatteetaten would accept it and
what it computed.

**The skattemelding goes back as it came, plus two sections.** For an AS the
fields Skatteetaten needs from the books (næringsinntekt, samlet inntekt, the
loss carried forward) are derived from the næringsspesifikasjon by Skatteetaten
itself, and are never written. Two sections it cannot derive, and refuses the
pair without (the formue and two facts about the company), are added by
:mod:`myntos.skattemelding.upersonlig` before the melding reaches this module;
anything Skatteetaten already wrote in the utkast is left as it is. The rest
(konsernbidrag and the like) is not in the books, comes back from validation
as a merknad, and the person completes it on skatteetaten.no.

**What validation's verdict means.** ``validertOK`` may be sent;
``validertMedFeil`` would be refused, and ``aarsakTilValidertMedFeil`` says
why. A ``veiledning`` whose ``betjeningsstrategi`` is ``faktiskFeil`` is an
error the person has to fix; the ``merknad…`` ones are advice.
"""

from __future__ import annotations

import base64
import binascii
import enum
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from myntos.skattemelding import schema, xmlwriter

# The name a submitting system gives itself in ``opprettetAv``.
CREATED_BY = "Myntos"

# What the fetch calls a melding, and what the request calls the same melding:
# the fetch adds whether it is the utkast or the fastsatt one.
_FETCH_SUFFIXES = ("Utkast", "Fastsatt")

# The two formats a forhåndsfastsetting may be filed in
# (``forhaandsfastsettingsformattype``, Skatteetaten's code list): the income
# year's own schema once Skatteetaten has published it, or last year's before.
# Skatteetaten decides, and says which in the melding it serves.
THIS_YEARS_FORMAT = "aaretsSkattemelding"
LAST_YEARS_FORMAT = "fjoraaretsSkattemelding"


class EnvelopeError(Exception):
    """An answer from Skatteetaten that is not what its own schema says it is."""


class Innsendingstype(enum.StrEnum):
    #: Final. The only kind an AS may send (``kunKomplettTillatt``).
    KOMPLETT = "komplett"
    #: For a person, who finishes on skatteetaten.no.
    IKKE_KOMPLETT = "ikkeKomplett"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(element: ET.Element, name: str) -> ET.Element | None:
    for child in element:
        if _local(child.tag) == name:
            return child
    return None


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if _local(child.tag) == name]


def _text(element: ET.Element | None, name: str) -> str | None:
    if element is None:
        return None
    found = _child(element, name)
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def _parse(content: bytes, what: str) -> ET.Element:
    # The stdlib parser, for the reason myntos.domain.mva.melding gives: this
    # comes from Skatteetaten over TLS on a token we hold.
    try:
        return ET.fromstring(content)  # noqa: S314
    except ET.ParseError as error:
        raise EnvelopeError(f"{what} er ikke lesbar XML") from error


def _decoded(element: ET.Element, what: str) -> bytes:
    raw = _text(element, "content")
    if raw is None:
        raise EnvelopeError(f"{what} har ikke noe innhold")
    try:
        return base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError) as error:
        raise EnvelopeError(f"{what} er ikke gyldig base64") from error


# --- What the fetch gives -------------------------------------------------------------------


@dataclass(frozen=True)
class FetchedMelding:
    """The skattemelding Skatteetaten has for the year, as the fetch returned it."""

    #: ``dokumentidentifikator``: what the request refers to (``SKI:755:32858``).
    id: str
    #: As the fetch names it: ``skattemeldingUpersonligUtkast`` and the like.
    type: str
    #: The melding itself, decoded.
    content: bytes

    @property
    def request_type(self) -> str:
        """The same melding's name in a request: the fetch's, without Utkast or Fastsatt."""
        for suffix in _FETCH_SUFFIXES:
            if self.type.endswith(suffix):
                return self.type.removesuffix(suffix)
        return self.type

    @property
    def personal(self) -> bool:
        return self.request_type == "skattemeldingPersonlig"

    @property
    def forhaandsfastsetting_format(self) -> str | None:
        """The format Skatteetaten asks a forhåndsfastsetting in, or None for an ordinary melding.

        Set on the utkast Skatteetaten serves after
        ``klargjoerforhaandsfastsetting`` (``gjelderForhaandsfastsetting/
        innsendingsformat/forhaandsfastsettingsformattype``); TT02 served
        ``aaretsSkattemelding`` for 2026 on 2026-10-22. A melding without it is
        on the ordinary assessment.
        """
        marker = _child(_parse(self.content, "skattemeldingen"), "gjelderForhaandsfastsetting")
        if marker is None:
            return None
        return _text(_child(marker, "innsendingsformat"), "forhaandsfastsettingsformattype")

    @property
    def partsnummer(self) -> int | None:
        """Skatteetaten's number for the taxpayer, which the næringsspesifikasjon repeats.

        ``partsnummer`` in an upersonlig melding, ``partsreferanse`` in a
        personlig one; the same number under two names.
        """
        root = _parse(self.content, "skattemeldingen")
        value = _text(root, "partsnummer") or _text(root, "partsreferanse")
        try:
            return int(value) if value is not None else None
        except ValueError:
            return None


def parse_fetch(content: bytes) -> FetchedMelding:
    """The skattemelding in a fetch's answer, or :class:`EnvelopeError` when there is none."""
    root = _parse(content, "svaret fra Skatteetaten")
    documents = _child(root, "dokumenter")
    found = _child(documents, "skattemeldingdokument") if documents is not None else None
    if found is None:
        raise EnvelopeError("Skatteetaten svarte uten noen skattemelding for året")
    identifier, kind = _text(found, "id"), _text(found, "type")
    if identifier is None or kind is None:
        raise EnvelopeError("skattemeldingen fra Skatteetaten mangler id eller type")
    return FetchedMelding(id=identifier, type=kind, content=_decoded(found, "skattemeldingen"))


# --- What is sent ---------------------------------------------------------------------------


def request_xml(
    *,
    year: int,
    melding: FetchedMelding,
    naeringsspesifikasjon: bytes,
    innsendingstype: Innsendingstype,
    tin: str | None = None,
    forhaandsfastsetting: bool = False,
) -> bytes:
    """The envelope with the melding and the næringsspesifikasjon, for validation and Altinn alike.

    ``tin`` is the taxpayer's number as Skatteetaten's own examples give it (the
    orgnr for an AS). ``innsendingsformaal`` is always ``egenfastsetting``:
    Myntos files the taxpayer's own melding, not a klage or an
    endringsanmodning, and Skatteetaten requires the field from 2024 although
    the XSD does not.

    ``forhaandsfastsetting`` names, on each document, the namespace it is
    written in (``navneromVedForhaandsfastsetting``), which Skatteetaten asks
    for on a forhåndsfastsetting because its format may be last year's
    (docs/api-v2, "Forhåndsfastsetting"). Read off each document's own root
    rather than passed in, so it can never name a schema the document is not
    written in. Validated and filed that way in TT02 on 2026-10-22.
    """

    def document(kind: str, content: bytes) -> dict[str, object]:
        entry: dict[str, object] = {
            "type": kind,
            "encoding": "utf-8",
            "content": base64.b64encode(content).decode("ascii"),
        }
        if forhaandsfastsetting:
            root = _parse(content, kind)
            entry["navneromVedForhaandsfastsetting"] = root.tag[1:].split("}", 1)[0]
        return entry

    information: dict[str, object] = {
        "innsendingstype": innsendingstype.value,
        "opprettetAv": CREATED_BY,
        "innsendingsformaal": "egenfastsetting",
    }
    if tin:
        information["tin"] = tin
    tree: dict[str, object] = {
        "dokumenter": {
            "dokument": [
                document(melding.request_type, melding.content),
                document("naeringsspesifikasjon", naeringsspesifikasjon),
            ]
        },
        "dokumentreferanseTilGjeldendeDokument": [
            {"dokumenttype": melding.request_type, "dokumentidentifikator": melding.id}
        ],
        "inntektsaar": year,
        "innsendingsinformasjon": information,
    }
    return xmlwriter.write(schema.load(schema.REQUEST), tree)


def request_documents(content: bytes) -> dict[str, bytes]:
    """The documents in an envelope :func:`request_xml` wrote, decoded, by type."""
    root = _parse(content, "konvolutten")
    holder = _child(root, "dokumenter")
    documents: dict[str, bytes] = {}
    for item in _children(holder, "dokument") if holder is not None else []:
        kind = _text(item, "type")
        if kind is not None:
            documents[kind] = _decoded(item, kind)
    return documents


# --- What validation answers ----------------------------------------------------------------


@dataclass(frozen=True)
class Avvik:
    """A figure Skatteetaten read differently, or a field it says is missing."""

    #: ``avvikVedValidering`` or ``avvikEtterBeregning``.
    stage: str
    type: str
    path: str | None
    received: str | None
    computed: str | None
    occurrence: str | None
    information: str | None


@dataclass(frozen=True)
class Veiledning:
    type: str
    text: str | None
    #: ``faktiskFeil`` stops the melding; ``merknad…`` is advice.
    strategy: str | None
    path: str | None
    occurrence: str | None

    @property
    def is_error(self) -> bool:
        return self.strategy == "faktiskFeil"


@dataclass(frozen=True)
class Validering:
    result: str
    reasons: tuple[str, ...]
    avvik: tuple[Avvik, ...]
    veiledninger: tuple[Veiledning, ...]
    #: The documents Skatteetaten returned, decoded, by type.
    documents: dict[str, bytes] = field(default_factory=dict)
    #: The tax Skatteetaten computed, in whole kroner, when it computed any.
    computed_tax: Decimal | None = None

    @property
    def accepted(self) -> bool:
        """``validertOK``: Skatteetaten would take it as it is."""
        return self.result == "validertOK"

    @property
    def errors(self) -> tuple[Veiledning, ...]:
        return tuple(item for item in self.veiledninger if item.is_error)


def _amount(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _computed_tax(documents: dict[str, bytes]) -> Decimal | None:
    """``beregnetSkatt``, or the sum of the taxes when that one is absent."""
    content = documents.get("beregnetSkattUpersonlig") or documents.get("beregnetSkattPersonlig")
    if content is None:
        return None
    root = _parse(content, "den beregnede skatten")
    direct = _amount(_text(root, "beregnetSkatt"))
    if direct is not None:
        return direct
    return _amount(_text(_child(root, "skatt"), "sumSkatt"))


def parse_validation(content: bytes) -> Validering:
    root = _parse(content, "valideringssvaret")
    result = _text(root, "resultatAvValidering")
    if result is None:
        raise EnvelopeError("valideringssvaret mangler resultatAvValidering")
    avvik: list[Avvik] = []
    for stage in ("avvikVedValidering", "avvikEtterBeregning"):
        holder = _child(root, stage)
        for item in _children(holder, "avvik") if holder is not None else []:
            avvik.append(
                Avvik(
                    stage=stage,
                    type=_text(item, "avvikstype") or "",
                    path=_text(item, "sti"),
                    received=_text(item, "mottattVerdi") or _text(item, "mottattTekst"),
                    computed=_text(item, "beregnetVerdi") or _text(item, "beregnetTekst"),
                    occurrence=_text(item, "forekomstidentifikator"),
                    information=_text(item, "oevrigInformasjon"),
                )
            )
    guidance = _child(root, "veiledningEtterKontroll")
    veiledninger = [
        Veiledning(
            type=_text(item, "veiledningstype") or "",
            text=_text(item, "hjelpetekst"),
            strategy=_text(item, "betjeningsstrategi"),
            path=_text(item, "sti"),
            occurrence=_text(item, "forekomstidentifikator"),
        )
        for item in (_children(guidance, "veiledning") if guidance is not None else [])
    ]
    documents: dict[str, bytes] = {}
    holder = _child(root, "dokumenter")
    for item in _children(holder, "dokument") if holder is not None else []:
        kind = _text(item, "type")
        if kind is not None:
            documents[kind] = _decoded(item, kind)
    return Validering(
        result=result,
        reasons=tuple(
            (element.text or "").strip() for element in _children(root, "aarsakTilValidertMedFeil")
        ),
        avvik=tuple(avvik),
        veiledninger=tuple(veiledninger),
        documents=documents,
        computed_tax=_computed_tax(documents),
    )
