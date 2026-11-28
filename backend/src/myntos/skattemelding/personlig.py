"""What Myntos computes and preserves for an enkeltpersonforetak's personal skattemelding.

An enkeltpersonforetak (ENK) is not a taxpayer of its own: its næringsspesifikasjon
is filed together with the owner's own personal skattemelding, on the owner's
fødselsnummer. Every personal item Skatteetaten prefilled or the owner added
(salary, bank accounts, loans, property, deductions) is kept untouched. Derived
fields (``skatt:erAvledet``) and non-overridden derived business summaries under
``<naering>`` are stripped on a re-filing so Skatteetaten recomputes them cleanly
from the new næringsspesifikasjon.

Unlike an aksjeselskap, an ENK computes **beregnet personinntekt** (skatteloven
§ 12-10 ff.): taxable business profit adjusted for capital income/costs, interest
on business debt (``foretaksgjeld``), and the shielding deduction
(``skjermingsfradrag``, computed from the average opening/closing tax values of
fixed assets, inventory, receivables, supplier debt, and business loans). This
module computes the exact figures Skatteetaten's published Kotlin rules derive
(``FordeltBeregnetPersoninntektBeregningFra2023.kt`` and
``BalanseFormueOgGjeld2025.kt``) so the owner sees them before checking, while
the XML writer never sends any derived field.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from myntos.domain.arsoppgjor.findings import Finding, merknad
from myntos.skattemelding import codes, schema, upersonlig, xmlwriter
from myntos.skattemelding.envelope import FetchedMelding, Innsendingstype
from myntos.skattemelding.naeringsspesifikasjon import (
    AccountFigures,
    EnkDocumentDetails,
    Naeringsspesifikasjon,
    Regnskapsplikt,
)

ZERO = Decimal("0.00")
_CENT = Decimal("0.01")
_TWO = Decimal("2")
_HUNDRED = Decimal("100")
_TWELVE = Decimal("12")

DEFAULT_NAERINGSTYPE = "annenNaering"
DEFAULT_MONTHS = 12
DEFAULT_OWNER_SHARE = Decimal("100")

# Saldogruppe -> skjermingsgrunnlagstype code in Skatteetaten's code list.
_SALDO_SKJERMING: dict[str, str] = {
    "a": "saldogruppeA",
    "b": "saldogruppeB",
    "c": "saldogruppeC",
    "d": "saldogruppeD",
    "e": "saldogruppeE",
    "f": "saldogruppeF",
    "g": "saldogruppeG",
    "h": "saldogruppeH",
    "i": "saldogruppeI",
    "j": "saldogruppeJ",
}

# Fallback balance post -> skjermingsgrunnlagstype when the company has fixed
# assets on balance accounts without anleggsregister saldi.
_POST_SKJERMING: dict[str, str] = {
    "1280": "saldogruppeA",
    "1080": "saldogruppeB",
    "1238": "saldogruppeC",
    "1205": "saldogruppeD",
    "1221": "saldogruppeE",
    "1225": "saldogruppeF",
    "1117": "saldogruppeG",
    "1115": "saldogruppeH",
    "1105": "saldogruppeI",
    "1120": "saldogruppeJ",
}

_DEBT_TYPES = frozenset({"leverandoergjeld", "foretaksgjeld"})

# Child elements under <naering> in skattemeldingPersonlig whose values are
# derived from the næringsspesifikasjon. On a re-filing of a Fastsatt personal
# return, stripping derived leaves inside <naeringsinntektMv> would remove its
# required-but-derived <naeringstype>; removing non-overridden occurrences
# lets Skatteetaten regenerate them cleanly without breaking the personal XSD.
_DERIVED_NAERING_ITEMS = frozenset({"naeringsinntektMv", "samletGjeldOgFormuesobjekterINaering"})


@dataclass(frozen=True)
class EnkAnswers:
    """What the ENK owner states before checking and filing."""

    naeringstype: str = DEFAULT_NAERINGSTYPE
    kommunenummer: str | None = None
    naeringsbeskrivelse: str | None = None
    innsendingstype: Innsendingstype = Innsendingstype.IKKE_KOMPLETT
    antall_maaneder: int = DEFAULT_MONTHS
    andel_innehaver: Decimal = DEFAULT_OWNER_SHARE


@dataclass(frozen=True)
class SkjermingsgrunnlagLine:
    """One row of ``spesifikasjonAvSkjermingsgrunnlag`` for ENK personinntekt."""

    type: str
    name: str
    opening: Decimal
    closing: Decimal
    average: Decimal
    is_debt: bool


@dataclass(frozen=True)
class Personinntekt:
    """Beregnet personinntekt preview (skatteloven § 12-10 ff.)."""

    skjermingsgrunnlag: tuple[SkjermingsgrunnlagLine, ...]
    sum_foer_gjeld: Decimal
    sum_etter_gjeld: Decimal
    skjermingsrente: Decimal
    skjermingsfradrag: Decimal
    kapitalinntekt: Decimal
    kapitalkostnad: Decimal
    rentekostnad_foretaksgjeld: Decimal
    antall_maaneder: int
    andel_innehaver: Decimal
    beregnet_personinntekt: Decimal
    tilordnet_innehaver: Decimal


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _kroner(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def suggest_naeringstype(*, industry_code: str | None, landbruk_enabled: bool) -> str:
    """Best guess for Skatteetaten's ``naeringstype`` from the farm add-on or NACE code."""
    if landbruk_enabled:
        return "jordbrukGartneriPelsdyrnaeringMv"
    prefix = (industry_code or "").strip()[:2]
    if prefix == "01":
        return "jordbrukGartneriPelsdyrnaeringMv"
    if prefix == "02":
        return "skogbruk"
    if prefix == "03":
        return "fiskeOgFangst"
    return DEFAULT_NAERINGSTYPE


def formue(nsp: Naeringsspesifikasjon) -> tuple[upersonlig.Formue, list[Finding]]:
    """Business wealth and debt Skatteetaten transfers from an ENK's balance sheet.

    Mirrors ``BalanseFormueOgGjeld2025.kt``: only posts marked ``overfoeresTilSME``
    in Skatteetaten's code list are summed into business wealth and debt. Bank
    deposits (1920), bank loans (2220), real estate, shares, and tax-exempt
    self-produced farm feed (1401) are intentionally excluded because banks and
    property valuations populate the owner's personal tax return directly.
    """
    posts = codes.posts(nsp.year)
    with_discount = without_discount = debt = ZERO
    left_out: dict[str, list[str]] = {"eiendom": [], "aksjer": []}
    for post in nsp.posts:
        if post.result_side:
            continue
        entry = posts[(post.category, post.code)]
        if post.category in ("kortsiktigGjeld", "langsiktigGjeld"):
            if entry.transfers_to_sme:
                debt += post.natural
            continue
        if not post.category.startswith("balanseverdi"):
            continue
        if post.code in upersonlig.REAL_ESTATE:
            left_out["eiendom"].append(f"{post.code} {post.name}")
            continue
        if post.code in upersonlig.SHARES:
            left_out["aksjer"].append(f"{post.code} {post.name}")
            continue
        if not entry.transfers_to_sme:
            continue
        if post.natural < 0:
            debt -= post.natural
        elif entry.gives_discount:
            with_discount += post.natural
        else:
            without_discount += post.natural

    findings: list[Finding] = []
    if left_out["eiendom"]:
        findings.append(
            merknad(
                "formue_eiendom_utelatt",
                "Fast eiendom overføres ikke fra næringsspesifikasjonen til formuen ("
                + ", ".join(left_out["eiendom"])
                + "). Formuesverdien fastsettes på skatteetaten.no under eiendom i din "
                "personlige skattemelding.",
            )
        )
    if left_out["aksjer"]:
        findings.append(
            merknad(
                "formue_aksjer_utelatt",
                "Aksjer og fondsandeler overføres ikke fra næringsspesifikasjonen ("
                + ", ".join(left_out["aksjer"])
                + "). Kontroller at de står riktig i din personlige skattemelding på "
                "skatteetaten.no.",
            )
        )
    findings.append(
        merknad(
            "formue_enk_overfores",
            "Skatteetaten overfører næringsformue og næringsgjeld fra balansen (driftsmidler, "
            "varelager, kundefordringer, leverandørgjeld og skyldig mva) til din personlige "
            "skattemelding. Bankinnskudd (1920) og banklån (2220) er allerede forhåndsutfylt fra "
            "banken i din personlige skattemelding og overføres derfor ikke herfra.",
        )
    )
    return upersonlig.Formue(
        _kroner(with_discount), _kroner(without_discount), _kroner(debt)
    ), findings


def compute_personinntekt(
    nsp: Naeringsspesifikasjon,
    *,
    accounts: Sequence[AccountFigures],
    answers: EnkAnswers | None = None,
) -> tuple[Personinntekt, list[Finding]]:
    """Compute ENK personinntekt and skjermingsgrunnlag per Skatteetaten's Kotlin rules."""
    chosen = answers or EnkAnswers()
    labels = codes.by_code(nsp.year, codes.SKJERMINGSGRUNNLAG)

    opening_by_type: dict[str, Decimal] = defaultdict(lambda: ZERO)
    closing_by_type: dict[str, Decimal] = defaultdict(lambda: ZERO)

    # 1. Fixed assets: from anleggsregister saldi when present, else from balance accounts.
    if nsp.saldi:
        for saldo in nsp.saldi:
            stype = _SALDO_SKJERMING.get(saldo.saldogruppe)
            if stype is None:
                continue
            opening_by_type[stype] += max(ZERO, saldo.opening)
            closing_by_type[stype] += max(ZERO, saldo.closing)
    else:
        for account in accounts:
            if (
                account.category == "balanseverdiForAnleggsmiddel"
                and account.code in _POST_SKJERMING
            ):
                stype = _POST_SKJERMING[account.code]
                opening_by_type[stype] += account.opening
                closing_by_type[stype] += account.closing

    # 2. Inventory (1400, 1401), customer receivables (1500), supplier debt (2400),
    # and long-term business debt (foretaksgjeld, e.g. 2220).
    for account in accounts:
        if account.category == "balanseverdiForOmloepsmiddel":
            if account.code in ("1400", "1401"):
                opening_by_type["varelager"] += account.opening
                closing_by_type["varelager"] += account.closing
            elif account.code == "1500":
                opening_by_type["kundefordring"] += account.opening
                closing_by_type["kundefordring"] += account.closing
        elif account.category == "kortsiktigGjeld" and account.code == "2400":
            # Debt is a credit balance, stored negative in AccountFigures.
            opening_by_type["leverandoergjeld"] += -account.opening
            closing_by_type["leverandoergjeld"] += -account.closing
        elif account.category == "langsiktigGjeld":
            opening_by_type["foretaksgjeld"] += -account.opening
            closing_by_type["foretaksgjeld"] += -account.closing

    # Build ordered skjermingsgrunnlag lines, clamping negative debt/asset totals to zero
    # (N_SKJERMINGSGRUNNLAG_GJELD_NEGATIVT warns against negative debt values).
    ordered_types = [
        *_SALDO_SKJERMING.values(),
        "varelager",
        "kundefordring",
        "leverandoergjeld",
        "foretaksgjeld",
    ]
    lines: list[SkjermingsgrunnlagLine] = []
    for stype in ordered_types:
        opening = _money(max(ZERO, opening_by_type.get(stype, ZERO)))
        closing = _money(max(ZERO, closing_by_type.get(stype, ZERO)))
        if opening == 0 and closing == 0:
            continue
        entry = labels.get(stype)
        lines.append(
            SkjermingsgrunnlagLine(
                type=stype,
                name=entry.name if entry is not None else stype,
                opening=opening,
                closing=closing,
                average=_money((opening + closing) / _TWO),
                is_debt=stype in _DEBT_TYPES,
            )
        )

    asset_avg = sum((line.average for line in lines if not line.is_debt), ZERO)
    supplier_avg = sum((line.average for line in lines if line.type == "leverandoergjeld"), ZERO)
    business_debt_avg = sum((line.average for line in lines if line.type == "foretaksgjeld"), ZERO)

    sum_foer_gjeld = _money(max(ZERO, asset_avg - supplier_avg))
    sum_etter_gjeld = _money(max(ZERO, sum_foer_gjeld - business_debt_avg))
    rate = codes.skjermingsrente(nsp.year)
    faktor = Decimal(chosen.antall_maaneder) / _TWELVE
    skjermingsfradrag = _money(sum_etter_gjeld * faktor * rate / _HUNDRED)

    # Capital income and costs (skatteloven § 12-11):
    # - finansinntekt is subtracted (not personal effort income)
    # - finanskostnad is added back in full
    # - interest cost (8150) on foretaksgjeld is then subtracted when the business holds
    #   foretaksgjeld on the balance sheet.
    kapitalinntekt = _money(
        max(ZERO, sum((p.natural for p in nsp.posts if p.category == "finansinntekt"), ZERO))
    )
    kapitalkostnad = _money(
        max(ZERO, sum((p.natural for p in nsp.posts if p.category == "finanskostnad"), ZERO))
    )
    interest_8150 = _money(
        max(
            ZERO,
            sum(
                (
                    p.natural
                    for p in nsp.posts
                    if p.category == "finanskostnad" and p.code == "8150"
                ),
                ZERO,
            ),
        )
    )
    has_foretaksgjeld = any(line.type == "foretaksgjeld" for line in lines)
    rentekostnad_foretaksgjeld = interest_8150 if has_foretaksgjeld else ZERO

    findings: list[Finding] = []
    if interest_8150 > 0 and not has_foretaksgjeld:
        findings.append(
            merknad(
                "rentekostnad_uten_foretaksgjeld",
                f"Regnskapet har {interest_8150} kr på post 8150 Rentekostnad, men ingen "
                "langsiktig foretaksgjeld i balansen. Rentekostnaden er derfor ikke trukket fra "
                "i beregnet personinntekt. Gjelder den et næringslån, kan du legge den inn som "
                "rentekostnad på foretaksgjeld på skatteetaten.no.",
            )
        )

    beregnet = _money(
        nsp.taxable_result
        - rentekostnad_foretaksgjeld
        - kapitalinntekt
        + kapitalkostnad
        - skjermingsfradrag
    )
    tilordnet = _money(beregnet * chosen.andel_innehaver / _HUNDRED)

    return (
        Personinntekt(
            skjermingsgrunnlag=tuple(lines),
            sum_foer_gjeld=sum_foer_gjeld,
            sum_etter_gjeld=sum_etter_gjeld,
            skjermingsrente=rate,
            skjermingsfradrag=skjermingsfradrag,
            kapitalinntekt=kapitalinntekt,
            kapitalkostnad=kapitalkostnad,
            rentekostnad_foretaksgjeld=rentekostnad_foretaksgjeld,
            antall_maaneder=chosen.antall_maaneder,
            andel_innehaver=chosen.andel_innehaver,
            beregnet_personinntekt=beregnet,
            tilordnet_innehaver=tilordnet,
        ),
        findings,
    )


def document_details(
    nsp: Naeringsspesifikasjon,
    *,
    answers: EnkAnswers,
    personinntekt: Personinntekt,
    formue_values: upersonlig.Formue,
) -> EnkDocumentDetails:
    """Build the ENK-specific XML inputs passed to ``naeringsspesifikasjon.document``."""
    include_municipality_formue = (
        nsp.regnskapsplikt is Regnskapsplikt.FULL or answers.andel_innehaver < DEFAULT_OWNER_SHARE
    )
    share = answers.andel_innehaver / _HUNDRED
    return EnkDocumentDetails(
        naeringstype=answers.naeringstype,
        kommunenummer=answers.kommunenummer,
        naeringsbeskrivelse=answers.naeringsbeskrivelse,
        andel_innehaver=answers.andel_innehaver,
        antall_maaneder=answers.antall_maaneder,
        skjermingsgrunnlag=tuple(
            (line.type, line.opening, line.closing) for line in personinntekt.skjermingsgrunnlag
        ),
        # Send skjermingsrente whenever skjermingsgrunnlag after debt is positive
        # (avoids Skatteetaten's N_SKJERMINGSRENTE_MANGLER merknad).
        skjermingsrente=(
            personinntekt.skjermingsrente if personinntekt.sum_etter_gjeld > 0 else None
        ),
        rentekostnad_foretaksgjeld=personinntekt.rentekostnad_foretaksgjeld,
        kapitalinntekt=personinntekt.kapitalinntekt,
        kapitalkostnad=personinntekt.kapitalkostnad,
        formue_with_discount=(
            _money(Decimal(formue_values.with_discount) * share)
            if include_municipality_formue
            else None
        ),
        formue_without_discount=(
            _money(Decimal(formue_values.without_discount) * share)
            if include_municipality_formue
            else None
        ),
        formue_debt=(
            _money(Decimal(formue_values.debt) * share) if include_municipality_formue else None
        ),
    )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _has_override(element: ET.Element) -> bool:
    for node in element.iter():
        if _local(node.tag) == "erOverstyrt" and "".join(node.itertext()).strip() == "true":
            return True
    return False


def _strip_derived_naering(content: bytes) -> bytes:
    """Remove non-overridden business summary nodes regenerated from the næringsspesifikasjon.

    A previously filed (Fastsatt) personal skattemelding carries ``naeringsinntektMv``
    and ``samletGjeldOgFormuesobjekterINaering`` under ``<naering>``. Inside
    ``naeringsinntektMv``, ``naeringstype`` is required (minOccurs=1) AND derived
    (``skatt:erAvledet="true"``), so stripping derived leaves alone would leave an
    invalid stub. Removing the whole non-overridden item lets Skatteetaten regenerate
    it cleanly from the new næringsspesifikasjon while preserving every other personal
    section (salary, banks, loans, deductions, property, and oppgavegiver).
    """
    root = ET.fromstring(content)  # noqa: S314 - document served by Skatteetaten
    for child in list(root):
        if _local(child.tag) != "naering":
            continue
        for item in list(child):
            if _local(item.tag) in _DERIVED_NAERING_ITEMS and not _has_override(item):
                child.remove(item)
        if len(child) == 0:
            root.remove(child)
    ns = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else ""
    if ns:
        ET.register_namespace("", ns)
    return bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))


def complete(melding: FetchedMelding, nsp: Naeringsspesifikasjon) -> tuple[bytes, list[Finding]]:
    """Return the owner's personal skattemelding with all personal posts preserved.

    Strips Skatteetaten-derived fields (so re-filing a Fastsatt return does not
    fail with ``avvikSkattemelding``) while leaving every personal post untouched.
    """
    cleaned = _strip_derived_naering(melding.content)
    content, _ = xmlwriter.merge(schema.skattemelding_personlig(nsp.year), cleaned, {})
    return content, []
