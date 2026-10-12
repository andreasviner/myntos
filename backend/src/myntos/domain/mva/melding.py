"""The MVA-melding: built from ledger lines, written as Skatteetaten's XML.

Pure functions over plain values, like :mod:`myntos.domain.ledger.posting`: no
session and no I/O, so the rules that decide what a company tells Skatteetaten it
owes are testable without a database and live in one place.

**Aggregation.** The melding is ``GROUP BY (mvaKode, spesifikasjon, sats)`` over
the period's taxed lines (:mod:`.codes` says how each kind of code is reported).
A reversal mirrors its original's tax amounts onto the other side, so the pair
nets to zero here without any special-casing; the same goes for a kreditnota.
What is *not* done is rounding. The ledger holds øre and the melding reports
øre: Skatteetaten compares ``grunnlag * sats`` to ``merverdiavgift`` under its
own tolerance, and rounding to kroner first is how a correct melding ends up
outside it.

**Two documents.** The melding itself (``mvaMeldingDto``, namespaced, from
``no.skatteetaten.fastsetting.avgift.mva.skattemeldingformerverdiavgift.v1.0.xsd``)
and the envelope Altinn reads first (``mvaMeldingInnsending``). The envelope is
written **without** a namespace, because that is what Skatteetaten's examples
do and what the app itself stored when it read one back
(``example_files/feedback/exampleSuccessfulFeedback17062021/konvolutt.xml``).
The XSD declares a target namespace for it all the same; if the app ever starts
insisting, the 409 on ``process/next`` names the element.

**Two answers.** The validation result and the payment information come back as
XML and are parsed here, namespace-agnostically, because the validation result
has been published in two shapes and the user has to be shown what it says in
either.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from myntos.domain.mva.codes import (
    DISPLAY_ORDER,
    MVA_CODES,
    SPESIFIKASJON_CODES,
    CodeKind,
    Meldingskategori,
    Spesifikasjon,
)
from myntos.domain.mva.periods import Termin

MELDING_NAMESPACE = "no:skatteetaten:fastsetting:avgift:mva:skattemeldingformerverdiavgift:v1.0"

# What Skatteetaten is told wrote the melding. The name is the product's; the
# version is the backend package's, so a melding can be traced to the code that
# built it.
SYSTEM_NAME = "Myntos"

# The filename the melding is uploaded under, and the vedlegg entry in the
# envelope that has to name it: Altinn refuses process/next when the envelope's
# list of files and the instance's disagree.
MELDING_FILENAME = "mva-melding.xml"

ZERO = Decimal("0.00")
_ORE = Decimal("0.01")


class MeldingError(ValueError):
    """The ledger holds something the melding cannot express."""


@dataclass(frozen=True)
class TaxedLine:
    """One ledger line carrying tax information, as the aggregator needs it.

    ``base`` is unsigned, like ``tax_base`` on the ledger line, and ``is_debit``
    says which side it sits on. The tax amounts are the two SAF-T columns as
    stored; exactly one of them is non-zero on any real line.
    """

    code: str
    rate: Decimal
    is_debit: bool
    base: Decimal
    debit_tax: Decimal
    credit_tax: Decimal
    # ``vat_specification`` off the ledger line: uttak, tapPåKrav, justering or
    # tilbakeføringAvInngåendeMerverdiavgift, and None on every ordinary line.
    spesifikasjon: str | None = None


@dataclass(frozen=True)
class Spesifikasjonslinje:
    mva_kode: str
    merverdiavgift: Decimal
    # Absent on an input (deduction) line, present on every output line.
    grunnlag: Decimal | None = None
    sats: Decimal | None = None
    spesifikasjon: str | None = None

    @property
    def is_deduction(self) -> bool:
        return self.grunnlag is None


@dataclass(frozen=True)
class Melding:
    termin: Termin
    category: Meldingskategori
    orgnr: str
    lines: tuple[Spesifikasjonslinje, ...]

    @property
    def fastsatt_merverdiavgift(self) -> Decimal:
        """What the period comes to: positive is owed, negative is refunded."""
        return sum((line.merverdiavgift for line in self.lines), start=ZERO)

    @property
    def output_vat(self) -> Decimal:
        return sum((ln.merverdiavgift for ln in self.lines if not ln.is_deduction), start=ZERO)

    @property
    def input_vat(self) -> Decimal:
        return -sum((ln.merverdiavgift for ln in self.lines if ln.is_deduction), start=ZERO)


# --- Aggregation ------------------------------------------------------------------


@dataclass
class _Sum:
    grunnlag: Decimal = ZERO
    merverdiavgift: Decimal = ZERO


def aggregate(lines: Iterable[TaxedLine]) -> tuple[Spesifikasjonslinje, ...]:
    """Group the period's taxed lines into specification lines.

    Codes the melding never carries must already be filtered out by the caller
    (``TaxCode.is_reportable``); a code that is reportable and still unknown
    here raises, naming it. Reporting it as *something* would be a guess on a
    document the company signs.
    """
    outputs: dict[tuple[str, Decimal, str | None], _Sum] = {}
    inputs: dict[tuple[str, str | None], _Sum] = {}

    for line in lines:
        known = MVA_CODES.get(line.code)
        if known is None:
            raise MeldingError(
                f"mva-kode {line.code} kan ikke rapporteres i mva-meldingen ennå; "
                "ta kontakt med oss før du sender"
            )
        spes = line.spesifikasjon
        if spes is not None:
            # Refused rather than reported as written. A spesifikasjon on a code
            # the list does not pair it with is a melding Skatteetaten rejects,
            # and dropping the word would report an uttak as an ordinary sale.
            try:
                allowed = SPESIFIKASJON_CODES.get(Spesifikasjon(spes), frozenset())
            except ValueError:
                allowed = frozenset()
            if line.code not in allowed:
                raise MeldingError(
                    f"spesifikasjonen «{spes}» kan ikke rapporteres med mva-kode {line.code}"
                )
        signed_base = -line.base if line.is_debit else line.base
        # Credit is the side output VAT is owed on. A sale credits revenue; a
        # purchase, including an import, debits a cost.
        credit_tax = line.credit_tax - line.debit_tax

        if known.kind is CodeKind.UTGAAENDE:
            bucket = outputs.setdefault((line.code, line.rate, spes), _Sum())
            bucket.grunnlag += signed_base
            bucket.merverdiavgift += credit_tax
        elif known.kind is CodeKind.INNGAAENDE:
            # A tap på krav, a justering and a tilbakeføring are reported in the
            # shape of a deduction, merverdiavgift alone, and the sign is the
            # tax side as posted: a tilbakeføring is VAT owed back, so positive.
            inputs.setdefault((line.code, spes), _Sum()).merverdiavgift += credit_tax
        else:
            # The buyer owes the VAT on a purchase, so the signs flip: a debit
            # (the purchase) is a positive basis and a positive amount owed.
            bucket = outputs.setdefault((line.code, line.rate, spes), _Sum())
            bucket.grunnlag -= signed_base
            bucket.merverdiavgift -= credit_tax
            if known.deductible:
                inputs.setdefault((line.code, spes), _Sum()).merverdiavgift += credit_tax

    result: list[Spesifikasjonslinje] = []
    for (code, rate, spes), total in outputs.items():
        if total.grunnlag == ZERO and total.merverdiavgift == ZERO:
            # A sale and its kreditnota in the same period. Reporting a line of
            # zeros adds nothing and invites a question about it.
            continue
        result.append(
            Spesifikasjonslinje(
                mva_kode=code,
                grunnlag=total.grunnlag,
                sats=rate,
                merverdiavgift=total.merverdiavgift,
                spesifikasjon=spes,
            )
        )
    for (code, spes), total in inputs.items():
        if total.merverdiavgift == ZERO:
            continue
        result.append(
            Spesifikasjonslinje(
                mva_kode=code, merverdiavgift=total.merverdiavgift, spesifikasjon=spes
            )
        )

    order = {code: position for position, code in enumerate(DISPLAY_ORDER)}
    result.sort(
        key=lambda ln: (
            order.get(ln.mva_kode, len(order)),
            # The output side of a reverse-charge code before its deduction.
            ln.is_deduction,
            # The ordinary line of a code before its specified ones, which is
            # where Skatteetaten's example puts them too.
            ln.spesifikasjon is not None,
            ln.spesifikasjon or "",
            -(ln.sats or ZERO),
        )
    )
    return tuple(result)


def build(
    lines: Iterable[TaxedLine],
    *,
    termin: Termin,
    category: Meldingskategori,
    orgnr: str,
) -> Melding:
    return Melding(termin=termin, category=category, orgnr=orgnr, lines=aggregate(lines))


# --- Formatting -------------------------------------------------------------------


def format_amount(value: Decimal) -> str:
    """An amount as the XSD's decimal, in øre and never in exponent form."""
    return format(value.quantize(_ORE), "f")


def format_sats(rate: Decimal) -> str:
    """A fraction as the code list writes the rate: ``25``, ``15``, ``11,11``.

    With a comma. ``sats.xml`` spells 11,11 % that way and the examples follow
    it, and the field is a string in the XSD, so ``11.11`` would be a different
    value rather than a different format.
    """
    percent = (rate * 100).quantize(_ORE)
    text = format(percent.normalize(), "f")
    return text.replace(".", ",")


def _sub(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    child = ET.SubElement(parent, tag)
    if text is not None:
        child.text = text
    return child


def _period(parent: ET.Element, termin: Termin, tag: str) -> None:
    period = _sub(parent, tag)
    _sub(_sub(period, "periode"), termin.period_type.element, termin.name)
    _sub(period, "aar", str(termin.year))


def melding_xml(
    melding: Melding,
    *,
    reference: str,
    system_version: str,
    merknad: str | None = None,
) -> bytes:
    """The ``mvaMeldingDto`` document, element order as the XSD's sequence.

    ``reference`` is ``regnskapssystemsreferanse``, unique per submission. The
    filing's own id is used, so a melding seen in Altinn can be found again here.
    """
    ns = f"{{{MELDING_NAMESPACE}}}"
    ET.register_namespace("", MELDING_NAMESPACE)
    root = ET.Element(f"{ns}mvaMeldingDto")

    innsending = ET.SubElement(root, f"{ns}innsending")
    ET.SubElement(innsending, f"{ns}regnskapssystemsreferanse").text = reference
    system = ET.SubElement(innsending, f"{ns}regnskapssystem")
    ET.SubElement(system, f"{ns}systemnavn").text = SYSTEM_NAME
    ET.SubElement(system, f"{ns}systemversjon").text = system_version

    grunnlag = ET.SubElement(root, f"{ns}skattegrunnlagOgBeregnetSkatt")
    periode = ET.SubElement(grunnlag, f"{ns}skattleggingsperiode")
    ET.SubElement(
        ET.SubElement(periode, f"{ns}periode"), f"{ns}{melding.termin.period_type.element}"
    ).text = melding.termin.name
    ET.SubElement(periode, f"{ns}aar").text = str(melding.termin.year)
    ET.SubElement(grunnlag, f"{ns}fastsattMerverdiavgift").text = format_amount(
        melding.fastsatt_merverdiavgift
    )
    for line in melding.lines:
        element = ET.SubElement(grunnlag, f"{ns}mvaSpesifikasjonslinje")
        ET.SubElement(element, f"{ns}mvaKode").text = line.mva_kode
        if line.spesifikasjon is not None:
            ET.SubElement(element, f"{ns}spesifikasjon").text = line.spesifikasjon
        if line.grunnlag is not None:
            ET.SubElement(element, f"{ns}grunnlag").text = format_amount(line.grunnlag)
        if line.sats is not None:
            ET.SubElement(element, f"{ns}sats").text = format_sats(line.sats)
        ET.SubElement(element, f"{ns}merverdiavgift").text = format_amount(line.merverdiavgift)

    # Required, and empty: the KID inside it is for refunds paid by KID, which
    # is not how a company receives one. The refund goes to the account
    # Skatteetaten holds for the orgnr.
    ET.SubElement(root, f"{ns}betalingsinformasjon")
    skattepliktig = ET.SubElement(root, f"{ns}skattepliktig")
    ET.SubElement(skattepliktig, f"{ns}organisasjonsnummer").text = melding.orgnr
    ET.SubElement(root, f"{ns}meldingskategori").text = melding.category.value
    if merknad:
        ET.SubElement(ET.SubElement(root, f"{ns}merknad"), f"{ns}beskrivelse").text = merknad[:4000]
    return bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))


def envelope_xml(
    melding: Melding,
    *,
    created_by: str,
    created_at: datetime,
) -> bytes:
    """The ``mvaMeldingInnsending`` document Altinn reads before the melding.

    Everything in it has to agree with the melding and the instance, and Altinn
    checks: the orgnr against the instance owner, the category against the
    melding's, and the ``vedlegg`` list against the files actually uploaded.
    ``instansstatus`` is optional in the XSD and required by the app, which
    answers 409 without it; its value is not read by anything, and Skatteetaten's
    own examples put "Deprecated" there.
    """
    timestamp = created_at.isoformat(timespec="seconds")
    root = ET.Element("mvaMeldingInnsending")
    _sub(_sub(root, "norskIdentifikator"), "organisasjonsnummer", melding.orgnr)
    _period(root, melding.termin, "skattleggingsperiode")
    _sub(root, "meldingskategori", melding.category.value)
    _sub(root, "innsendingstype", "komplett")
    _sub(root, "instansstatus", "Deprecated")
    _sub(root, "opprettetAv", created_by[:4000])
    _sub(root, "opprettingstidspunkt", timestamp)
    vedlegg = _sub(root, "vedlegg")
    _sub(vedlegg, "vedleggstype", "mva-melding")
    _sub(vedlegg, "kildegruppe", "sluttbrukersystem")
    _sub(vedlegg, "opprettetAv", created_by[:4000])
    _sub(vedlegg, "opprettingstidspunkt", timestamp)
    fil = _sub(vedlegg, "vedleggsfil")
    _sub(fil, "filnavn", MELDING_FILENAME.removesuffix(".xml"))
    _sub(fil, "filekstensjon", "xml")
    _sub(fil, "filinnhold", "mva-melding")
    return bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))


# --- Reading Skatteetaten's answers -----------------------------------------------


# _parse_note: the stdlib parser, on purpose. What it reads comes from
# Skatteetaten over TLS on a token we hold, not from a user; ElementTree does
# not resolve external entities, and the expat it ships with since Python 3.12
# refuses the entity-expansion bombs bandit's S314 is about. defusedxml would be
# a dependency added to guard against a sender we already have to trust with
# the filing itself.


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(element: ET.Element, name: str) -> ET.Element | None:
    for child in element:
        if _local(child.tag) == name:
            return child
    return None


def _text(element: ET.Element | None, name: str) -> str | None:
    if element is None:
        return None
    found = _find(element, name)
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if _local(child.tag) == name]


# The two verdicts that mean "this will not be accepted". The older shape spells
# them in capitals with underscores.
_BLOCKING = frozenset(
    {
        "ugyldig skattemelding",
        "mangelfull skattemelding",
        "ugyldig_skattemelding",
        "mangelfull_skattemelding",
    }
)


@dataclass(frozen=True)
class Avvik:
    sti: str
    melding: str
    alvorlighet: str | None = None
    kode: str | None = None
    mva_kode: str | None = None
    detaljer: str | None = None


@dataclass(frozen=True)
class Valideringsresultat:
    """What the validation said, in a shape a person can be shown.

    ``status`` is Skatteetaten's own word for the melding as a whole. ``ingen
    avvik`` may be sent; ``avvikende skattemelding`` may be sent and will be
    looked at, which the user is told; ``mangelfull`` and ``ugyldig`` will be
    refused, so they block. An answer that could not be read blocks too: the
    alternative is submitting on a verdict nobody saw.
    """

    status: str
    avvik: tuple[Avvik, ...] = field(default_factory=tuple)
    readable: bool = True

    @property
    def blocks_submission(self) -> bool:
        return not self.readable or self.status.lower() in _BLOCKING

    @property
    def has_findings(self) -> bool:
        return bool(self.avvik) or self.status.lower() != "ingen avvik"


def parse_validation(content: bytes) -> Valideringsresultat:
    try:
        root = ET.fromstring(content)  # noqa: S314 - see _parse_note
    except ET.ParseError:
        return Valideringsresultat(status="uleselig svar", readable=False)

    status = _text(root, "avvikVedMeldingslevering") or _text(root, "status")
    findings: list[Avvik] = []
    # The published XSD: avvik / avviksinformasjon.
    for avvik in _children(root, "avvik"):
        for info in _children(avvik, "avviksinformasjon"):
            findings.append(
                Avvik(
                    sti=_text(avvik, "stiTilAvvik") or "",
                    mva_kode=_text(avvik, "mvaKode"),
                    melding=_text(info, "begrunnelse") or _text(info, "regelDefinisjon") or "",
                    alvorlighet=_text(info, "avvikstype"),
                    kode=_text(info, "avvikKode"),
                    detaljer=_text(info, "detaljer"),
                )
            )
    # The shape the app answers a failed process/next with:
    # valideringsfeil / valideringsDetaljer.
    for feil in _children(root, "valideringsfeil"):
        for detalj in _children(feil, "valideringsDetaljer"):
            findings.append(
                Avvik(
                    sti=_text(feil, "stiTilFeil") or "",
                    melding=_text(detalj, "feilmelding") or "",
                    alvorlighet=_text(detalj, "alvorlighetsgrad"),
                    kode=_text(detalj, "avvikKode"),
                    detaljer=_text(detalj, "informasjon"),
                )
            )
    if status is None:
        return Valideringsresultat(status="uleselig svar", avvik=tuple(findings), readable=False)
    return Valideringsresultat(status=status, avvik=tuple(findings))


@dataclass(frozen=True)
class Betalingsinformasjon:
    """Where and when to pay, as Skatteetaten worded it after the filing."""

    kid: str | None
    beloep: Decimal | None
    betalingsfrist: date | None
    kontonummer: str | None
    betale_til: str | None


def parse_payment(content: bytes) -> Betalingsinformasjon:
    try:
        root = ET.fromstring(content)  # noqa: S314 - see _parse_note
    except ET.ParseError as error:
        raise MeldingError("betalingsinformasjonen fra Skatteetaten kunne ikke leses") from error

    amount_text = _text(root, "beloep")
    try:
        amount = Decimal(amount_text) if amount_text is not None else None
    except InvalidOperation:
        amount = None
    due_text = _text(root, "betalingsfrist")
    try:
        due = date.fromisoformat(due_text[:10]) if due_text else None
    except ValueError:
        due = None
    return Betalingsinformasjon(
        kid=_text(root, "kundeidentifikasjonsnummer"),
        beloep=amount,
        betalingsfrist=due,
        kontonummer=_text(_find(root, "betalesTilKonto"), "norskKontonummer"),
        betale_til=_text(root, "betaleTil"),
    )
