"""What each SAF-T VAT code means to the MVA-melding.

The ledger knows a code, a rate, a basis and an amount per line. The melding
needs to know something the ledger does not: which *kind* of line the code
produces. Three kinds, and they are reported differently enough that mixing them
up is a melding Skatteetaten rejects:

**Utgående** (3, 31, 32, 33, 5, 6, 51, 52). A sale. Reported with ``grunnlag``,
``sats`` and a positive ``merverdiavgift``.

**Inngående** (1, 11, 12, 13, 14, 15). A deduction. Reported with
``merverdiavgift`` only, negative, and no basis or rate: Skatteetaten's own
examples (``mvakode1.xml``, ``mvakode14.xml``) carry neither, and the validation
compares a basis against a rate only where one is given.

**Omvendt avgiftsplikt** (81 to 92). A purchase on which the buyer calculates
the VAT, because the seller could not: an import, a service bought from abroad,
gold. Reported as an *output* line with basis and rate and, when the purchase
carries a right to deduct, a second *input* line with the same amount negated
(``mvakode81.xml``). A deductible import therefore nets to zero and still
appears twice, which is the whole point of reporting it.

The table is Skatteetaten's ``kodelister/mvaKodeSAFT.xml``, read on 2026-10-06,
and the column that decides the kind is its ``kodeBrukesTilSpesifiseringAv``.
Codes 0, 7, 20, 21 and 22 are absent on purpose: they never reach a melding
(:data:`~myntos.domain.ledger.models.NON_REPORTABLE_STANDARD_TAX_CODES`).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class CodeKind(enum.StrEnum):
    UTGAAENDE = "utgaaende"
    INNGAAENDE = "inngaaende"
    OMVENDT = "omvendt"


class Meldingskategori(enum.StrEnum):
    """The ``meldingskategori`` values the XSD accepts.

    Only the first two are filed by Myntos today. ``kompensasjon`` is for
    municipalities and non-profits claiming compensation rather than deducting,
    and reads the same codes with a different meaning; ``omvendtAvgiftsplikt`` is
    for a business that is *not* registered and bought services from abroad;
    ``eHandel`` is VOEC, for foreign sellers. Each is a melding with rules of its
    own, and filing one of them through the ordinary path would be a melding that
    validates and says something untrue.
    """

    ALMINNELIG = "alminnelig"
    PRIMAERNAERING = "primaernaering"
    KOMPENSASJON = "kompensasjon"
    OMVENDT_AVGIFTSPLIKT = "omvendtAvgiftsplikt"
    EHANDEL = "eHandel"


SUPPORTED_CATEGORIES = frozenset({Meldingskategori.ALMINNELIG, Meldingskategori.PRIMAERNAERING})


@dataclass(frozen=True)
class MvaCode:
    code: str
    kind: CodeKind
    # Only meaningful for OMVENDT: whether the second, negative line is
    # reported. The no-deduction variants (82, 84, 87, 89, 92) are a cost to the
    # buyer and report the output side alone.
    deductible: bool = False


MVA_CODES: dict[str, MvaCode] = {
    entry.code: entry
    for entry in (
        MvaCode("1", CodeKind.INNGAAENDE),
        MvaCode("11", CodeKind.INNGAAENDE),
        MvaCode("12", CodeKind.INNGAAENDE),
        MvaCode("13", CodeKind.INNGAAENDE),
        MvaCode("14", CodeKind.INNGAAENDE),
        MvaCode("15", CodeKind.INNGAAENDE),
        MvaCode("3", CodeKind.UTGAAENDE),
        MvaCode("31", CodeKind.UTGAAENDE),
        MvaCode("32", CodeKind.UTGAAENDE),
        MvaCode("33", CodeKind.UTGAAENDE),
        MvaCode("5", CodeKind.UTGAAENDE),
        MvaCode("6", CodeKind.UTGAAENDE),
        MvaCode("51", CodeKind.UTGAAENDE),
        MvaCode("52", CodeKind.UTGAAENDE),
        MvaCode("81", CodeKind.OMVENDT, deductible=True),
        MvaCode("82", CodeKind.OMVENDT),
        MvaCode("83", CodeKind.OMVENDT, deductible=True),
        MvaCode("84", CodeKind.OMVENDT),
        # 85 is zero-rated: there is nothing to deduct, so a second line would
        # be a zero reported for its own sake.
        MvaCode("85", CodeKind.OMVENDT),
        MvaCode("86", CodeKind.OMVENDT, deductible=True),
        MvaCode("87", CodeKind.OMVENDT),
        MvaCode("88", CodeKind.OMVENDT, deductible=True),
        MvaCode("89", CodeKind.OMVENDT),
        MvaCode("91", CodeKind.OMVENDT, deductible=True),
        MvaCode("92", CodeKind.OMVENDT),
    )
}

# The order the lines appear in, in the melding and on screen: sales first,
# then deductions, then everything bought from abroad. Skatteetaten does not
# require an order. A person reading the melding before confirming it does, and
# this is the order Skatteetaten's own portal shows the themes in.
DISPLAY_ORDER: tuple[str, ...] = (
    "3", "31", "33", "32", "5", "6", "51", "52",
    "1", "11", "13", "12", "14", "15",
    "81", "82", "83", "84", "85", "86", "87", "88", "89", "91", "92",
)  # fmt: skip


class Spesifikasjon(enum.StrEnum):
    """The ``mvaSpesifikasjon`` code list, spelled exactly as it is published.

    The spelling is the point. ``tapPåKrav`` and
    ``tilbakeføringAvInngåendeMerverdiavgift`` carry the Norwegian letters, and an
    earlier note in docs/integrations/mva.md wrote them ``tapPaKrav`` and
    ``tilbakefoering``, which Skatteetaten does not know.

    The ledger records the first four on a line's ``vat_specification``
    (:mod:`myntos.domain.mva.adjustments` writes them) and the aggregator groups
    by it. The last is for the ``kompensasjon`` category, which Myntos does not
    file, and is listed only so the list is whole.
    """

    UTTAK = "uttak"
    TAP_PAA_KRAV = "tapPåKrav"
    JUSTERING = "justering"
    TILBAKEFOERING = "tilbakeføringAvInngåendeMerverdiavgift"
    KOMPENSASJONSRETT = "kompensasjonsrett"


#: Which codes each spesifikasjon may be reported under, as far as Myntos
#: records it.
#:
#: Read from ``mvaKodeSAFT.xml``, whose per-code flags (``uttak``,
#: ``tapPaaKrav``, ``justering``, ``tilbakefoeringAvInngaaendeMerverdiavgift``)
#: say where each may appear; ``tests/test_mva_spesifikasjon.py`` holds this
#: table to the vendored list so it cannot claim a combination Skatteetaten does
#: not. It is a **subset** on purpose, in three places:
#:
#: - uttak under 5 (zero-rated) is allowed by the list, and left out because the
#:   starter chart has no zero-rated domestic income account to credit and
#:   choosing one for the person would be a guess;
#: - justering and tilbakeføring under 81 (import with deduction) are allowed,
#:   and left out because Skatteetaten publishes no example of the shape such a
#:   line takes next to 81's own pair of lines;
#: - tap på krav is allowed under 1, 11, 12 and 13, and all four are here.
#:
#: Skatteetaten's ``eksempelMedAlleTilfeller.xml`` is the shape each takes:
#: uttak as a sale (grunnlag, sats, positive merverdiavgift), the other three as
#: a deduction line (merverdiavgift alone, negative for tap på krav, positive
#: for tilbakeføring, either sign for justering).
SPESIFIKASJON_CODES: dict[Spesifikasjon, frozenset[str]] = {
    Spesifikasjon.UTTAK: frozenset({"3", "31", "33"}),
    Spesifikasjon.TAP_PAA_KRAV: frozenset({"1", "11", "12", "13"}),
    Spesifikasjon.JUSTERING: frozenset({"1"}),
    Spesifikasjon.TILBAKEFOERING: frozenset({"1"}),
}
