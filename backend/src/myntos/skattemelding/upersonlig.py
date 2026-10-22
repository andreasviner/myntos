"""What Myntos adds to an aksjeselskap's own skattemelding before it goes back.

An AS files two documents together: the næringsspesifikasjon, which is the
books, and the upersonlig skattemelding, which Skatteetaten serves as an utkast
for the year. Most of what the skattemelding holds about income is derived by
Skatteetaten from the næringsspesifikasjon (næringsinntekt, samlet inntekt, the
loss carried forward), and is never written here. Two sections are not:

- **``formueOgGjeld``**: the company's assets and debt at their value for
  formue, which is what the value of its shares is worked out from for every
  shareholder's own formue. Myntos fills it from the closing balance, sorted the
  way Skatteetaten's own calculation sorts an enkeltpersonforetak's balance
  (``BalanseFormueOgGjeld2025.kt``): the posts the code list marks
  ``BalansekontoGirVerdsettingsrabatt`` as «driftsmidler mv.», the rest of the
  assets without the discount, and every liability as debt. Two kinds of asset
  are left out, because their value for formue is not their book value and
  Myntos cannot know it: **real estate**, whose formuesverdi Skatteetaten's
  property calculator sets, and **shares**, valued from the issuer's
  aksjonærregister or the market. The review says so for each, and the person
  adds them on skatteetaten.no.
- **``opplysningOmSkattesubjekt``**: facts about the company only a person can
  state. Myntos asks one question (``harYtelseMellomAksjonaer…``, whether
  anything passed between the company and a shareholder or someone close to
  one) and states one assumption, that the company is not listed on a stock
  exchange, which no company keeping its books in Myntos is.

A **wound-up company** (``company.wound_up_on``) says two things more, which
Skatteetaten took in TT02 on 2026-10-22: that it was fully wound up in the
year (``erFulltAvvikletIInntektsaaret``), and, on a forhåndsfastsetting, what
its shareholders were paid when it was dissolved
(``verdsettingAvAksje/likvidasjonsvederlagVedForhaandsfastsetting``), which is
what each shareholder's gain or loss on the shares is worked out from. The
person states the payout; the review suggests it from the books.

Without either section Skatteetaten refuses the pair outright
(``UP_HAR_NÆRINGSSPESIFIKASJON_MANGLER_SKATTEMELDING``: "Selskapet mangler
skattemelding"), which is what TT02 answered on 2026-10-21 to a synthetic
company's empty utkast sent back as it came.

A section the utkast already has is kept as Skatteetaten wrote it
(:func:`myntos.skattemelding.xmlwriter.merge`).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from myntos.domain.arsoppgjor.findings import Finding, merknad
from myntos.skattemelding import codes, schema, xmlwriter
from myntos.skattemelding.envelope import FetchedMelding
from myntos.skattemelding.naeringsspesifikasjon import Naeringsspesifikasjon

ZERO = Decimal("0")

# Fast eiendom: valued for formue by Skatteetaten's own rules (formuesverdi,
# the property calculator), never at book value.
REAL_ESTATE = frozenset({"1105", "1115", "1120", "1140", "1150", "1160", "1180"})
# Aksjer and fund units: valued at the issuer's formuesverdi or the market.
SHARES = frozenset({"1350", "1800", "1810"})

WITH_DISCOUNT = "formuesobjektOmfattetAvVerdsettingsrabattDriftsmidlerMv"
WITHOUT_DISCOUNT = "formuesobjektIkkeOmfattetAvVerdsettingsrabatt"


@dataclass(frozen=True)
class Answers:
    """What the person states about the company, on the page, before the check."""

    #: «Skal selskapet rapportere opplysninger om ytelser mellom selskap og
    #: personlig aksjonær, deltaker eller nærstående til denne», in
    #: Skatteetaten's own words (docs/tekster in its repository).
    benefits_with_shareholders: bool
    #: The company was wound up in the year (its last year in the books).
    wound_up: bool = False
    #: What the shareholders were paid in all when the company was dissolved,
    #: in whole kroner. Sent only on a forhåndsfastsetting, where the schema
    #: has the field (``likvidasjonsvederlagVedForhaandsfastsetting``).
    liquidation_payout: int | None = None


@dataclass(frozen=True)
class Formue:
    """The company's assets and debt for formue, in whole kroner."""

    with_discount: int
    without_discount: int
    debt: int


def _kroner(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def formue(nsp: Naeringsspesifikasjon) -> tuple[Formue, list[Finding]]:
    """The formue the balance gives, and what was left out of it."""
    posts = codes.posts(nsp.year)
    with_discount = without_discount = debt = ZERO
    left_out: dict[str, list[str]] = {"eiendom": [], "aksjer": []}
    for post in nsp.posts:
        if post.result_side:
            continue
        if post.category in ("kortsiktigGjeld", "langsiktigGjeld"):
            debt += post.natural
            continue
        if not post.category.startswith("balanseverdi"):
            continue
        if post.code in REAL_ESTATE:
            left_out["eiendom"].append(f"{post.code} {post.name}")
            continue
        if post.code in SHARES:
            left_out["aksjer"].append(f"{post.code} {post.name}")
            continue
        if post.natural < 0:
            # An asset below zero (a bank account overdrawn at year end) is
            # owed, not owned.
            debt -= post.natural
        elif posts[(post.category, post.code)].gives_discount:
            with_discount += post.natural
        else:
            without_discount += post.natural
    findings: list[Finding] = []
    if left_out["eiendom"]:
        findings.append(
            merknad(
                "formue_eiendom_utelatt",
                "Fast eiendom er ikke med i formuen Myntos sender ("
                + ", ".join(left_out["eiendom"])
                + "). Formuesverdien settes etter Skatteetatens egne regler, ikke etter "
                "regnskapsverdien. Legg eiendommen til under «Faste eiendommer» på "
                "skatteetaten.no etter innsendingen.",
            )
        )
    if left_out["aksjer"]:
        findings.append(
            merknad(
                "formue_aksjer_utelatt",
                "Aksjer og fondsandeler er ikke med i formuen Myntos sender ("
                + ", ".join(left_out["aksjer"])
                + "). Verdien for formue er selskapets formuesverdi eller kursen, ikke "
                "kostprisen. Legg dem til på skatteetaten.no etter innsendingen.",
            )
        )
    findings.append(
        merknad(
            "formue_fra_regnskapet",
            "Formuen i skattemeldingen er satt til verdiene i balansen. For driftsmidler "
            "følger regnskapet i Myntos skatteverdiene, og for bank, fordringer og varer er "
            "det normalt det samme. Er en verdi en annen for skatt, rett den på "
            "skatteetaten.no etter innsendingen.",
        )
    )
    return Formue(_kroner(with_discount), _kroner(without_discount), _kroner(debt)), findings


def additions(
    nsp: Naeringsspesifikasjon, answers: Answers
) -> tuple[dict[str, object], list[Finding]]:
    """The sections Myntos adds to the utkast, and what the review should say about them."""
    values, findings = formue(nsp)
    objects = [
        {
            "id": kind,
            "formuesobjekttype": {"formuesobjekttype": kind},
            "verdiFoerEventuellVerdsettingsrabatt": {"beloepSomHeltall": amount},
        }
        for kind, amount in (
            (WITH_DISCOUNT, values.with_discount),
            (WITHOUT_DISCOUNT, values.without_discount),
        )
        if amount > 0
    ]
    formue_og_gjeld: dict[str, object] = {}
    if objects:
        formue_og_gjeld["formuesobjekt"] = objects
    if values.debt > 0:
        formue_og_gjeld["gjeld"] = {"annenGjeld": {"beloepSomHeltall": values.debt}}
    about: dict[str, object] = {
        "erBoersnotert": False,
        "harYtelseMellomAksjonaerEllerNaerstaaendeOgSelskapEllerSelskapetsDatterselskap": (
            answers.benefits_with_shareholders
        ),
    }
    if answers.wound_up:
        about["erFulltAvvikletIInntektsaaret"] = {"boolsk": True}
    sections: dict[str, object] = {"opplysningOmSkattesubjekt": about}
    if formue_og_gjeld:
        sections["formueOgGjeld"] = formue_og_gjeld
    if answers.liquidation_payout is not None:
        sections["verdsettingAvAksje"] = {
            "likvidasjonsvederlagVedForhaandsfastsetting": {
                "beloepSomHeltall": answers.liquidation_payout
            }
        }
    return sections, findings


def complete(
    melding: FetchedMelding, nsp: Naeringsspesifikasjon, answers: Answers
) -> tuple[bytes, list[Finding]]:
    """The utkast with Myntos's sections added, and the review's notes about them."""
    sections, findings = additions(nsp, answers)
    content, added = xmlwriter.merge(
        schema.skattemelding_upersonlig(nsp.year), melding.content, sections
    )
    for name in sections:
        if name not in added:
            findings.append(
                merknad(
                    f"{name}_fra_skatteetaten",
                    f"Skattemeldingen fra Skatteetaten har allerede «{name}». Myntos lar den stå "
                    "som den er.",
                )
            )
    return content, findings
