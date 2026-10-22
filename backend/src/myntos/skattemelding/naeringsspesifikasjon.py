"""The næringsspesifikasjon, built from the books: a projection, like the SAF-T file.

Every account in Myntos carries, per financial year, the næringsspesifikasjon
post it belongs to (``AccountGrouping``: SAF-T's ``GroupingCategory`` and
``GroupingCode`` are the næringsspesifikasjon's own sections and posts). So the
result and the balance are the SAF-T snapshot's accounts summed per post, and
the saldo specification is the anleggsregister's
(:func:`myntos.domain.anlegg.saldo.specification`). Nothing here is a second
reading of the ledger: the figures come from :mod:`myntos.saft.service`, which
already decides what an opening balance is and what an ``IB`` voucher counts as.

**Inputs only, never what Skatteetaten derives.** Sums, the year's result,
the skattemessig resultat, a saldo's closing value and its year's depreciation
are all computed by Skatteetaten from what is sent, and a sent value that
differs from its own is ``avvikNaeringsopplysninger`` and a refused melding.
:mod:`myntos.skattemelding.xmlwriter` refuses to write one (``erAvledet`` in
the XSD). Myntos still computes them, the way Skatteetaten's published
calculations do (``github.com/Skatteetaten/skattemeldingen``, ``src/kotlin``),
for two reasons: the person is shown them before anything is sent, and one of
them must be repeated as an input (``fordeltSkattemessigResultat``, which
Skatteetaten refuses when it is more than 1 000 kroner from its own figure).

**The year's depreciation is a rate.** The saldo specification has no field
for an amount depreciated: Skatteetaten computes ``grunnlag x sats``. The law
says «inntil» the group's rate, and a company that took less is saying so by
sending a lower rate. So the rate sent is ``depreciation ÷ grunnlag x 100``,
which the XSD lets carry ten decimals, enough to come back to the same øre.

**Signs.** Each post's amount is sent as the books read it on its own side
(income and liabilities as credit, costs and assets as debit), turned round for
a post Skatteetaten subtracts (``kategori`` ``negativ``: 3300 særavgifter, 2080
udekket tap, 7099). So 2080 with a debit of 5 000 is sent as 5 000, as the
examples send it.

**What is built so far.** An aksjeselskap with full regnskapsplikt, which is
every AS. An enkeltpersonforetak files on different terms (most have no
regnskapsplikt, and then Skatteetaten makes several posts itself from
specifications Myntos does not send yet), and is refused by name rather than
sent half right; docs/integrations/skatteetaten.md says what it still needs.
"""

from __future__ import annotations

import enum
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import cast

from myntos.domain.anlegg.rules import Saldogruppe
from myntos.domain.anlegg.saldo import SaldoSpecification, SaldoYear
from myntos.domain.arsoppgjor.findings import Finding, Severity, feil, merknad
from myntos.skattemelding import codes
from myntos.skattemelding.xmlwriter import Node

ZERO = Decimal("0.00")
_CENT = Decimal("0.01")
_HUNDRED = Decimal("100")
# Ten decimals of a percentage: what the XSD's Prosent allows, and enough that
# grunnlag x sats comes back to the øre for any saldo under a billion kroner.
_RATE = Decimal("0.0000000001")

# The id of the one share of the skattemessig resultat an AS has.
FORDELT_ID = "fordeltBeregnetNaeringsinntekt"

# Skatteetaten's tolerance between fordeltSkattemessigResultat and its own
# skattemessig resultat (N_AVVIK_SKATTEMESSIG_RESULTAT). Myntos sends its own
# figure exactly; this is what the review compares with when it explains a
# finding that came back.
FORDELT_TOLERANCE = Decimal("1000")

# Above both, an AS is not «små foretak» (regnskapsloven § 1-6: two of three,
# and the third, 50 årsverk, is not in the books).
SMALL_COMPANY_REVENUE = Decimal("70000000")
SMALL_COMPANY_BALANCE = Decimal("35000000")

# Posts the controls refuse with a minus sign (N_FEIL_NEGATIV_*). Others may be
# negative: a bank account overdrawn at year end is a negative 1920.
_NEVER_NEGATIVE = frozenset({"2080", "6998", "7099"})


class Regnskapsplikt(enum.StrEnum):
    FULL = "fullRegnskapsplikt"
    BEGRENSET = "begrensetRegnskapsplikt"
    INGEN = "ingenRegnskapsplikt"

    @property
    def text(self) -> str:
        """In a sentence a person reads: «full regnskapsplikt»."""
        return {
            Regnskapsplikt.FULL: "full regnskapsplikt",
            Regnskapsplikt.BEGRENSET: "begrenset regnskapsplikt",
            Regnskapsplikt.INGEN: "ingen regnskapsplikt",
        }[self]


class Virksomhetstype(enum.StrEnum):
    ENKELTPERSONFORETAK = "enkeltpersonforetak"
    # Every company that is not one of the special kinds (banks, insurers,
    # samvirkeforetak, NOKUS, SDF): an AS is «øvrig selskap».
    OEVRIG_SELSKAP = "oevrigSelskap"


SMALL_COMPANY_RULES = "regnskapslovensReglerForSmaaForetak"
GENERAL_RULES = "regnskapslovensAlminneligeRegler"


@dataclass(frozen=True)
class _Section:
    category: str
    #: Where its lines go, from the root, and the element each line is.
    path: tuple[str, ...]
    item: str
    #: Read as credit less debit: income, liabilities, equity.
    credit_side: bool
    #: On the result side; otherwise the balance.
    result: bool


SECTIONS: dict[str, _Section] = {
    section.category: section
    for section in (
        _Section(
            "salgsinntekt",
            ("resultatregnskap", "driftsinntekt", "salgsinntekt"),
            "inntekt",
            True,
            True,
        ),
        _Section(
            "annenDriftsinntekt",
            ("resultatregnskap", "driftsinntekt", "annenDriftsinntekt"),
            "inntekt",
            True,
            True,
        ),
        _Section(
            "varekostnad",
            ("resultatregnskap", "driftskostnad", "varekostnad"),
            "kostnad",
            False,
            True,
        ),
        _Section(
            "loennskostnad",
            ("resultatregnskap", "driftskostnad", "loennskostnad"),
            "kostnad",
            False,
            True,
        ),
        _Section(
            "annenDriftskostnad",
            ("resultatregnskap", "driftskostnad", "annenDriftskostnad"),
            "kostnad",
            False,
            True,
        ),
        _Section("finansinntekt", ("resultatregnskap", "finansinntekt"), "inntekt", True, True),
        _Section("finanskostnad", ("resultatregnskap", "finanskostnad"), "kostnad", False, True),
        _Section("skattekostnad", ("resultatregnskap", "skattekostnad"), "kostnad", False, True),
        _Section(
            "balanseverdiForAnleggsmiddel",
            ("balanseregnskap", "anleggsmiddel", "balanseverdiForAnleggsmiddel"),
            "balanseverdi",
            False,
            False,
        ),
        _Section(
            "balanseverdiForOmloepsmiddel",
            ("balanseregnskap", "omloepsmiddel", "balanseverdiForOmloepsmiddel"),
            "balanseverdi",
            False,
            False,
        ),
        _Section(
            "langsiktigGjeld",
            ("balanseregnskap", "gjeldOgEgenkapital", "langsiktigGjeld"),
            "gjeld",
            True,
            False,
        ),
        _Section(
            "kortsiktigGjeld",
            ("balanseregnskap", "gjeldOgEgenkapital", "kortsiktigGjeld"),
            "gjeld",
            True,
            False,
        ),
        _Section(
            "egenkapital",
            ("balanseregnskap", "gjeldOgEgenkapital", "egenkapital"),
            "kapital",
            True,
            False,
        ),
    )
}

# The categories on the result side whose amounts reduce the result.
_COST_CATEGORIES = frozenset(
    s.category for s in SECTIONS.values() if s.result and not s.credit_side
)
_EQUITY = "egenkapital"
_TAX = "skattekostnad"


# --- What goes in ------------------------------------------------------------------------


@dataclass(frozen=True)
class AccountFigures:
    """One account in one year, as the SAF-T snapshot has it. Debit less credit."""

    number: str
    name: str
    #: The næringsspesifikasjon post (SAF-T grouping) for the year; None when
    #: the account has none.
    category: str | None
    code: str | None
    opening: Decimal
    closing: Decimal


@dataclass(frozen=True)
class Grunnlag:
    """Everything the næringsspesifikasjon is built from. Plain figures, no database."""

    year: int
    start: date
    end: date
    organisation_form: str | None
    #: Whether the year is closed in Myntos: the tax and the disposition are
    #: posted, so the equity on the balance includes the year's result.
    year_closed: bool
    #: Whether Enhetsregisteret names a revisor for the company.
    has_auditor: bool
    accounts: tuple[AccountFigures, ...]
    saldo: SaldoSpecification | None = None
    #: The company's last year: it was wound up, and ``end`` is the last day
    #: of its books (myntos.domain.arsoppgjor.winding_up).
    wound_up: bool = False


# --- What comes out ---------------------------------------------------------------------


@dataclass(frozen=True)
class Post:
    """One line of the result or the balance: a post, its amount, and the accounts behind it."""

    category: str
    code: str
    name: str
    #: As sent: the books' own side, turned round where Skatteetaten subtracts.
    amount: Decimal
    #: ``kategori`` ``negativ``: Skatteetaten subtracts it within its section.
    subtracted: bool
    #: As the books read it on the post's own side (income as credit, costs as
    #: debit), before any turning round. What the result is computed from.
    natural: Decimal
    accounts: tuple[str, ...]

    @property
    def result_side(self) -> bool:
        return SECTIONS[self.category].result


@dataclass(frozen=True)
class SaldoLine:
    """One saldo of the specification, with the rate that carries the year's choice."""

    key: str
    saldogruppe: str
    label: str
    opening: Decimal
    additions: Decimal
    grants: Decimal
    disposals: Decimal
    basis: Decimal
    depreciation: Decimal
    income: Decimal
    to_gain_loss_account: Decimal
    closing: Decimal
    #: The percentage sent, from which Skatteetaten computes the depreciation
    #: (or a negative saldo's income) of the year.
    rate_percent: Decimal
    physical: bool


@dataclass(frozen=True)
class GainLossLine:
    opening: Decimal
    gains: Decimal
    losses: Decimal
    income: Decimal
    deduction: Decimal
    closing: Decimal
    rate_percent: Decimal


@dataclass(frozen=True)
class PermanentDifference:
    type: str
    name: str
    amount: Decimal
    adds: bool


@dataclass(frozen=True)
class EquityChange:
    type: str
    name: str
    amount: Decimal
    adds: bool


@dataclass(frozen=True)
class Naeringsspesifikasjon:
    year: int
    start: date
    end: date
    regnskapsplikt: Regnskapsplikt
    business_type: Virksomhetstype
    rules: str | None
    has_auditor: bool
    posts: tuple[Post, ...]
    saldi: tuple[SaldoLine, ...]
    gain_loss: GainLossLine | None
    permanent: tuple[PermanentDifference, ...]
    equity_opening: Decimal
    equity_changes: tuple[EquityChange, ...]
    equity_closing: Decimal
    # As Skatteetaten computes them from the inputs above.
    result: Decimal
    additions: Decimal
    deductions: Decimal
    temporary_change: Decimal
    taxable_result: Decimal
    findings: tuple[Finding, ...] = field(default=())
    #: The company's last year (see :class:`Grunnlag`).
    wound_up: bool = False

    @property
    def blocked(self) -> bool:
        return any(finding.severity is Severity.FEIL for finding in self.findings)

    @property
    def assets(self) -> Decimal:
        return sum((p.natural for p in self.posts if p.category.startswith("balanseverdi")), ZERO)

    @property
    def claims(self) -> Decimal:
        return sum(
            (
                p.natural
                for p in self.posts
                if not p.result_side and SECTIONS[p.category].credit_side
            ),
            ZERO,
        )


# --- Building it ------------------------------------------------------------------------


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT)


def _form(grunnlag: Grunnlag) -> tuple[Regnskapsplikt, Virksomhetstype] | Finding:
    form = (grunnlag.organisation_form or "").upper()
    if form == "AS":
        return Regnskapsplikt.FULL, Virksomhetstype.OEVRIG_SELSKAP
    if form == "ENK":
        return feil(
            "enk_ikke_ferdig",
            "Myntos lager ennå ikke næringsspesifikasjonen for et enkeltpersonforetak. Den sendes "
            "sammen med innehaverens egen skattemelding, og Skatteetaten lager flere poster selv "
            "fra spesifikasjoner Myntos ikke sender ennå. Fyll den ut på skatteetaten.no, med "
            "tallene fra Regnskap i Myntos.",
        )
    return feil(
        "organisasjonsform_ikke_stottet",
        f"Myntos lager næringsspesifikasjonen bare for aksjeselskap foreløpig, ikke for "
        f"organisasjonsformen «{grunnlag.organisation_form or 'ukjent'}».",
    )


def _posts(
    grunnlag: Grunnlag,
    regnskapsplikt: Regnskapsplikt,
    business_type: Virksomhetstype,
    findings: list[Finding],
) -> list[Post]:
    known = codes.posts(grunnlag.year)
    sums: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    behind: dict[tuple[str, str], list[str]] = defaultdict(list)
    for account in grunnlag.accounts:
        if account.opening == 0 and account.closing == 0:
            continue
        category, code = account.category, account.code
        if category == codes.SAFT_ONLY_CATEGORY:
            # The disposition of the result (8800 and its kind): in SAF-T, not here.
            continue
        if category is None or code is None or (category, code) == ("NA", "NA"):
            findings.append(
                feil(
                    "konto_uten_post",
                    f"Konto {account.number} {account.name} har saldo, men ingen post i "
                    "næringsspesifikasjonen. Velg posten under Kontoplan.",
                )
            )
            continue
        section = SECTIONS.get(category)
        if section is None:
            findings.append(
                feil(
                    "post_ikke_stottet",
                    f"Konto {account.number} {account.name} står på en post for {category}, "
                    "som Myntos ikke sender i næringsspesifikasjonen. Den er for banker, "
                    "forsikringsselskaper og liknende.",
                )
            )
            continue
        if (category, code) not in known:
            findings.append(
                feil(
                    "ukjent_post",
                    f"Konto {account.number} {account.name} står på post {code}, som ikke "
                    f"finnes i Skatteetatens liste for {grunnlag.year}. Velg en annen post "
                    "under Kontoplan.",
                )
            )
            continue
        net = account.closing - account.opening if section.result else account.closing
        sums[(category, code)] += -net if section.credit_side else net
        behind[(category, code)].append(account.number)

    posts: list[Post] = []
    for (category, code), natural in sorted(sums.items(), key=lambda item: item[0][1]):
        natural = _money(natural)
        if natural == 0:
            continue
        entry = known[(category, code)]
        subtracted = entry.kategori == "negativ"
        amount = -natural if subtracted else natural
        if not entry.allows(regnskapsplikt=regnskapsplikt.value, business_type=business_type.value):
            findings.append(
                feil(
                    "post_ikke_tillatt",
                    f"Post {code} {entry.name} kan ikke brukes av et selskap med "
                    f"{regnskapsplikt.text} (kontoene {', '.join(behind[(category, code)])}). "
                    "Skatteetaten avviser den. Velg en annen post under Kontoplan.",
                )
            )
        if code in _NEVER_NEGATIVE and amount < 0:
            findings.append(
                feil(
                    "post_negativ",
                    f"Post {code} {entry.name} kan ikke være negativ, og er {amount} i regnskapet.",
                )
            )
        posts.append(
            Post(
                category=category,
                code=code,
                name=entry.name,
                amount=amount,
                subtracted=subtracted,
                natural=natural,
                accounts=tuple(behind[(category, code)]),
            )
        )
    return posts


def _rate(part: Decimal, whole: Decimal) -> Decimal:
    return (part / whole * _HUNDRED).quantize(_RATE).normalize() if whole else ZERO


def _saldi(
    spec: SaldoSpecification | None, findings: list[Finding]
) -> tuple[list[SaldoLine], GainLossLine | None]:
    if spec is None or not spec.saldi:
        return [], None
    if not spec.posted:
        findings.append(
            feil(
                "avskrivning_ikke_fort",
                "Årets avskrivning er ikke ført under Anlegg. Næringsspesifikasjonen sender "
                "saldoene med den avskrivningen regnskapet har, så før den først.",
            )
        )
    lines: list[SaldoLine] = []
    for saldo in spec.saldi:
        if saldo.saldogruppe in (Saldogruppe.LINEAER, Saldogruppe.IKKE_AVSKRIVBAR):
            findings.append(
                feil(
                    "anlegg_ikke_stottet",
                    f"«{saldo.label}» er en tidsbegrenset rettighet eller et anleggsmiddel som "
                    "ikke avskrives. Myntos sender ennå bare saldogruppene a til j i "
                    "næringsspesifikasjonen.",
                )
            )
            continue
        lines.append(_saldo_line(saldo))
    gtk = spec.gain_loss_account
    gain_loss = None
    if any((gtk.opening, gtk.gains, gtk.losses, gtk.closing)):
        base = gtk.basis
        taken = gtk.income if base > 0 else gtk.deduction
        gain_loss = GainLossLine(
            opening=_money(gtk.opening),
            gains=_money(gtk.gains),
            losses=_money(gtk.losses),
            income=_money(gtk.income),
            deduction=_money(gtk.deduction),
            closing=_money(gtk.closing),
            rate_percent=_rate(abs(taken), abs(base)),
        )
    return lines, gain_loss


def _saldo_line(saldo: SaldoYear) -> SaldoLine:
    basis = _money(saldo.basis)
    if basis > 0:
        rate = _rate(saldo.depreciation, basis)
    elif basis < 0:
        rate = _rate(saldo.income, -basis)
    else:
        rate = saldo.rate * _HUNDRED if saldo.rate is not None else ZERO
    return SaldoLine(
        key=saldo.key,
        saldogruppe=saldo.saldogruppe.value,
        label=saldo.label,
        opening=_money(saldo.opening),
        additions=_money(saldo.additions),
        grants=_money(saldo.grants),
        disposals=_money(saldo.disposals),
        basis=basis,
        depreciation=_money(saldo.depreciation),
        income=_money(saldo.income),
        to_gain_loss_account=_money(saldo.to_gain_loss_account),
        closing=_money(saldo.closing),
        rate_percent=rate,
        # An asset still owned at year end. A collective group (a-d) is a pool
        # of things still owned unless the saldo is gone; a single asset (e-i)
        # that left in the year is not.
        physical=saldo.closing != 0 or saldo.disposals == 0,
    )


def build(grunnlag: Grunnlag) -> Naeringsspesifikasjon:
    """The year's næringsspesifikasjon and what stops it, from plain figures."""
    findings: list[Finding] = []
    decided = _form(grunnlag)
    if isinstance(decided, Finding):
        findings.append(decided)
        regnskapsplikt, business_type = Regnskapsplikt.FULL, Virksomhetstype.OEVRIG_SELSKAP
    else:
        regnskapsplikt, business_type = decided
    if not grunnlag.year_closed:
        findings.append(
            feil(
                "aret_er_apent",
                f"Avslutt {grunnlag.year} under Årsavslutning først. Næringsspesifikasjonen lages "
                "fra et avsluttet år, fordi det er avslutningen som fører skattekostnaden og "
                "flytter årets resultat til egenkapitalen.",
            )
        )

    posts = _posts(grunnlag, regnskapsplikt, business_type, findings)
    # Årsresultat as Skatteetaten sums it: each section with its own sign,
    # income added and costs (the tax among them) taken away.
    result = _money(
        sum(
            (
                -p.natural if p.category in _COST_CATEGORIES else p.natural
                for p in posts
                if p.result_side
            ),
            ZERO,
        )
    )

    assets = sum((p.natural for p in posts if p.category.startswith("balanseverdi")), ZERO)
    claims = sum(
        (p.natural for p in posts if not p.result_side and SECTIONS[p.category].credit_side), ZERO
    )
    if assets != claims and grunnlag.year_closed:
        findings.append(
            feil(
                "balansen_gar_ikke_opp",
                f"Eiendelene ({_money(assets)}) og gjeld og egenkapital ({_money(claims)}) er "
                "ikke like store. Det betyr at noe står på en konto uten post, eller at årets "
                "resultat ikke er flyttet til egenkapitalen.",
            )
        )

    saldi, gain_loss = _saldi(grunnlag.saldo, findings)

    # The tax cost is not deductible, and is reversed as a permanent difference
    # for exactly the amount on the posts (N_AVVIK_TILBAKEFØRT_SKATTEKOSTNAD
    # refuses more than 10 kroner off).
    tax = sum((p.natural for p in posts if p.category == _TAX), ZERO)
    permanent: list[PermanentDifference] = []
    types = codes.by_code(grunnlag.year, codes.PERMANENT_DIFFERENCE)
    if tax:
        name = "positivSkattekostnad" if tax > 0 else "negativSkattekostnad"
        entry = types[name]
        permanent.append(
            PermanentDifference(
                type=name,
                name=entry.name,
                amount=_money(abs(tax)),
                adds=entry.kategori == "tillegg",
            )
        )
    additions = sum((d.amount for d in permanent if d.adds), ZERO)
    deductions = sum((d.amount for d in permanent if not d.adds), ZERO)
    temporary_change = ZERO
    taxable_result = _money(result + additions - deductions + temporary_change)

    equity = [a for a in grunnlag.accounts if a.category == _EQUITY]
    equity_opening = _money(-sum((a.opening for a in equity), ZERO))
    equity_closing = _money(-sum((a.closing for a in equity), ZERO))
    changes = _equity_changes(
        grunnlag.year, result, equity_opening, equity_closing, findings, wound_up=grunnlag.wound_up
    )

    rules: str | None = None
    if regnskapsplikt is Regnskapsplikt.FULL:
        revenue = sum((p.natural for p in posts if p.category == "salgsinntekt"), ZERO)
        rules = SMALL_COMPANY_RULES
        if revenue > SMALL_COMPANY_REVENUE and assets > SMALL_COMPANY_BALANCE:
            rules = GENERAL_RULES
            findings.append(
                merknad(
                    "ikke_sma_foretak",
                    "Salgsinntektene og balansesummen er begge over grensene for små foretak "
                    "(regnskapsloven § 1-6), så næringsspesifikasjonen sier at årsregnskapet "
                    "følger regnskapslovens alminnelige regler.",
                )
            )

    return Naeringsspesifikasjon(
        year=grunnlag.year,
        start=grunnlag.start,
        end=grunnlag.end,
        regnskapsplikt=regnskapsplikt,
        business_type=business_type,
        rules=rules,
        has_auditor=grunnlag.has_auditor,
        posts=tuple(posts),
        saldi=tuple(saldi),
        gain_loss=gain_loss,
        permanent=tuple(permanent),
        equity_opening=equity_opening,
        equity_changes=tuple(changes),
        equity_closing=equity_closing,
        result=result,
        additions=_money(additions),
        deductions=_money(deductions),
        temporary_change=temporary_change,
        taxable_result=taxable_result,
        findings=tuple(findings),
        wound_up=grunnlag.wound_up,
    )


def payout(nsp: Naeringsspesifikasjon) -> Decimal | None:
    """What the books say a wound-up company paid its shareholders, or None.

    The equity that left the company in its last year other than through the
    result: for a dissolved company that is the payout (likvidasjonsvederlag),
    and it is only a suggestion, because a correction against equity in the
    same year would count as well. The person confirms it on the page.
    """
    if not nsp.wound_up:
        return None
    return next(
        (c.amount for c in nsp.equity_changes if c.type == "annenNegativEndringIEgenkapital"),
        ZERO,
    )


def _equity_changes(
    year: int,
    result: Decimal,
    opening: Decimal,
    closing: Decimal,
    findings: list[Finding],
    *,
    wound_up: bool = False,
) -> list[EquityChange]:
    """The year's result, and whatever else moved the equity, as Skatteetaten's types.

    For a wound-up company the rest is, as a rule, the payout to the
    shareholders, and is sent as «andre negative endringer» like any other:
    Skatteetaten has no type of its own for it, and took it so in TT02 on
    2026-10-22. The payout itself is stated in the skattemelding
    (myntos.skattemelding.upersonlig).
    """
    types = codes.by_code(year, codes.EQUITY_CHANGE)
    changes: list[EquityChange] = []

    def add(name: str, amount: Decimal) -> None:
        entry = types[name]
        changes.append(
            EquityChange(
                type=name,
                name=entry.name,
                amount=_money(abs(amount)),
                adds=entry.kategori == "tillegg",
            )
        )

    if result > 0:
        add("aaretsOverskudd", result)
    elif result < 0:
        add("aaretsUnderskudd", result)
    other = _money(closing - opening - result)
    if other < 0 and wound_up:
        add("annenNegativEndringIEgenkapital", other)
        findings.append(
            merknad(
                "utbetaling_ved_opplosning",
                f"Egenkapitalen ble redusert med {-other} utover årets resultat. For et oppløst "
                "selskap er det normalt utbetalingen til aksjonærene, og den står som «andre "
                "negative endringer i egenkapital». Beløpet aksjonærene fikk, oppgir du når du "
                "kontrollerer skattemeldingen.",
            )
        )
    elif other:
        # A capital increase, a dividend Myntos did not book as one, a
        # correction against equity: Myntos cannot tell which, so it says
        # so, and the person can name it on skatteetaten.no.
        add(
            "annenPositivEndringIEgenkapital" if other > 0 else "annenNegativEndringIEgenkapital",
            other,
        )
        findings.append(
            merknad(
                "annen_endring_i_egenkapital",
                f"Egenkapitalen endret seg med {other} utover årets resultat. Den står som «andre "
                "endringer i egenkapital». Var det en kapitalforhøyelse eller et utbytte, kan "
                "typen rettes på skatteetaten.no etter innsendingen.",
            )
        )
    return changes


# --- The document ---------------------------------------------------------------------------


def _beloep(amount: Decimal) -> Node:
    return {"beloep": {"beloep": _money(amount)}}


def _lines(tree: dict[str, object], path: Iterable[str], item: str) -> list[object]:
    """The list of ``item`` elements at ``path``, made on the way down if it is not there.

    Only this module puts anything at these paths, and only dicts and lists, so
    the casts say what was put there rather than guess at it.
    """
    node = tree
    for name in path:
        node = cast(dict[str, object], node.setdefault(name, {}))
    return cast(list[object], node.setdefault(item, []))


def document(nsp: Naeringsspesifikasjon, *, partsreferanse: int) -> dict[str, object]:
    """The tree :func:`myntos.skattemelding.xmlwriter.write` turns into the XML.

    ``partsreferanse`` is Skatteetaten's number for the taxpayer, read off the
    skattemelding fetched for the year (``partsnummer`` in an upersonlig one).
    """
    tree: dict[str, object] = {"partsreferanse": partsreferanse, "inntektsaar": nsp.year}
    for post in nsp.posts:
        section = SECTIONS[post.category]
        _lines(tree, section.path, section.item).append(
            {
                "id": post.code,
                "beloep": _beloep(post.amount),
                "type": {"resultatOgBalanseregnskapstype": post.code},
            }
        )

    if nsp.saldi or nsp.gain_loss:
        spec: dict[str, object] = {}
        if nsp.saldi:
            spec["saldoavskrevetAnleggsmiddel"] = [_saldo_node(line) for line in nsp.saldi]
        if nsp.gain_loss:
            spec["gevinstOgTapskonto"] = [_gain_loss_node(nsp.gain_loss)]
        tree["spesifikasjonAvAnleggsmiddel"] = spec

    if nsp.business_type is Virksomhetstype.OEVRIG_SELSKAP:
        tree["beregnetNaeringsinntekt"] = {
            "fordeltBeregnetNaeringsinntektForUpersonligSkattepliktig": [
                {"id": FORDELT_ID, "fordeltSkattemessigResultat": _beloep(nsp.taxable_result)}
            ]
        }

    if nsp.permanent:
        tree["forskjellMellomRegnskapsmessigOgSkattemessigVerdi"] = {
            "permanentForskjell": [
                {
                    "id": difference.type,
                    "permanentForskjellstype": {"permanentForskjellstype": difference.type},
                    "beloep": _beloep(difference.amount),
                }
                for difference in nsp.permanent
            ]
        }

    virksomhet: dict[str, object] = {
        "regnskapspliktstype": {"regnskapspliktstype": nsp.regnskapsplikt.value},
        "regnskapsperiode": {"start": {"dato": nsp.start}, "slutt": {"dato": nsp.end}},
        "virksomhetstype": {"virksomhetstype": nsp.business_type.value},
    }
    if nsp.rules is not None:
        virksomhet["regeltypeForAarsregnskap"] = {"regeltypeForAarsregnskap": nsp.rules}
    tree["virksomhet"] = virksomhet

    tree["egenkapitalavstemming"] = {
        "inngaaendeEgenkapital": _beloep(nsp.equity_opening),
        "egenkapitalendring": [
            {
                "id": change.type,
                "egenkapitalendringstype": {"egenkapitalendringstype": change.type},
                "beloep": _beloep(change.amount),
            }
            for change in nsp.equity_changes
        ],
    }
    tree["skalBekreftesAvRevisor"] = nsp.has_auditor
    return tree


def _saldo_node(line: SaldoLine) -> Node:
    node: dict[str, object] = {
        "id": f"saldo-{line.key}",
        "objektidentifikator": {"tekst": line.key},
        "objektbeskrivelse": {"tekst": line.label},
        "saldogruppe": {"saldogruppe": line.saldogruppe},
        "avskrivningssats": {"prosent": line.rate_percent},
        "erDetFysiskAnleggsmiddelIUtgaaendeVerdi": {"boolsk": line.physical},
    }
    for name, amount in (
        ("inngaaendeVerdi", line.opening),
        ("nyanskaffelse", line.additions),
        ("offentligTilskudd", line.grants),
        ("vederlagVedRealisasjonOgUttak", line.disposals),
    ):
        if amount:
            node[name] = _beloep(amount)
    if line.to_gain_loss_account > 0:
        node["gevinstOverfoertTilGevinstOgTapskonto"] = _beloep(line.to_gain_loss_account)
    elif line.to_gain_loss_account < 0:
        node["tapOverfoertTilGevinstOgTapskonto"] = _beloep(-line.to_gain_loss_account)
    return node


def _gain_loss_node(line: GainLossLine) -> Node:
    node: dict[str, object] = {
        "id": "gevinstOgTapskonto",
        "gevinstOgTapskontoIdentifikator": {"tekst": "gevinst- og tapskonto"},
        "satsForInntektsfoeringOgInntektsfradrag": {"prosent": line.rate_percent},
    }
    if line.opening:
        node["inngaaendeVerdi"] = _beloep(line.opening)
    if line.gains:
        node["sumGevinstVedRealisasjonOgUttak"] = _beloep(line.gains)
    if line.losses:
        node["sumTapVedRealisasjonOgUttak"] = _beloep(line.losses)
    return node
