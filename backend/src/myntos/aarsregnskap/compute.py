"""Deterministic computation of årsregnskap (RR-0002 / RR-0002-U) from the ledger.

Pure functions over loaded ledger balances, company metadata, draft settings,
optional shareholder register (aksjeeierbok), and optional fixed-asset register
(anleggsregister). No database queries or network calls live here so every
calculation, sign convention, rounding rule, note, and compliance check is
directly unit-testable.

Sign conventions follow Brønnøysundregistrene's official ``Oppstillingsplan``
for RR-0002-U:

- Resultatregnskap: income and cost lines are positive in their natural sign.
  ``driftsresultat = driftsinntekter - driftskostnader``;
  ``netto_finans = finansinntekter - finanskostnader``;
  ``aarsresultat = resultat_for_skatt - skattekostnad``.
- Overføringer og disponeringer: sum equals ``aarsresultat``. A profit moved to
  ``annen_egenkapital`` is positive; a loss carried to ``udekket_tap`` is
  negative on ``overfoeringer/udekketTap`` (ORID 185) so the sum matches a
  negative ``aarsresultat``.
- Balanse: assets, equity, and liabilities are positive in their natural sign,
  EXCEPT ``udekket_tap`` (ORID 249), which is stored as a **positive** integer
  and **subtracted** in ``sumOpptjentEgenkapital`` (ORID 9702), matching
  Brønnøysund's formula ``27579 + 23857 + 3274 - 249 + 7734``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from myntos.domain.arsoppgjor.findings import Finding, Severity, feil, merknad
from myntos.domain.ledger.models import AccountType, Company

ZERO = Decimal("0.00")
ONE_KR = Decimal("1")

# Organisation forms that file årsregnskap from Myntos.
SUPPORTED_FORMS = frozenset({"AS", "ASA"})

# Minimum statutory share capital (aksjeloven § 3-1, allmennaksjeloven § 3-1).
MIN_SHARE_CAPITAL_AS = 30_000
MIN_SHARE_CAPITAL_ASA = 1_000_000

# Statutory audit opt-out thresholds (aksjeloven § 7-6, forskrift 2023).
AUDIT_THRESHOLD_REVENUE = 7_000_000
AUDIT_THRESHOLD_ASSETS = 27_000_000
AUDIT_THRESHOLD_FTE = Decimal("10")

# Allowed keys for manual prior-year overrides (every leaf line in P&L,
# disposition, balance sheet, and off-balance-sheet items).
RESULT_LEAF_KEYS: tuple[str, ...] = (
    "salgsinntekt",
    "annen_driftsinntekt",
    "beholdningsendring_varer",
    "varekostnad",
    "lonnskostnad",
    "avskrivning",
    "nedskrivning",
    "annen_driftskostnad",
    "finansinntekt_konsern",
    "finansinntekt_andre_inv",
    "renteinntekt_konsern",
    "annen_renteinntekt",
    "annen_finansinntekt",
    "verdireduksjon_fin_instr",
    "nedskrivning_fin_eiendeler",
    "rentekostnad_konsern",
    "annen_rentekostnad",
    "annen_finanskostnad",
    "skattekostnad",
    "disp_fond",
    "disp_utbytte",
    "disp_konsernbidrag_mottatt",
    "disp_konsernbidrag_avgitt",
    "disp_udekket_tap",
    "disp_annen_egenkapital",
)

BALANCE_LEAF_KEYS: tuple[str, ...] = (
    "utvikling",
    "konsesjoner_rettigheter",
    "utsatt_skattefordel",
    "goodwill",
    "tomter_bygninger",
    "maskiner_anlegg",
    "skip_rigg_fly",
    "driftslosore_inventar",
    "inv_datterselskap",
    "inv_samme_konsern",
    "laan_samme_konsern",
    "inv_tilknyttet",
    "laan_tilknyttet",
    "inv_aksjer_andeler",
    "obligasjoner",
    "andre_langsiktige_fordringer",
    "varer",
    "kundefordringer",
    "andre_kortsiktige_fordringer",
    "konsernfordringer",
    "markedsbaserte_aksjer",
    "andre_fin_instrumenter",
    "bankinnskudd_kontanter",
    "selskapskapital",
    "egne_aksjer",
    "overkurs",
    "annen_innskutt_egenkapital",
    "fond",
    "avsatt_utbytte",
    "annen_egenkapital",
    "udekket_tap",
    "pensjonsforpliktelser",
    "utsatt_skatt",
    "andre_avsetninger",
    "konvertible_laan_lang",
    "obligasjonslaan",
    "gjeld_kredittinst_lang",
    "langsiktig_konserngjeld",
    "ansvarlig_laanekapital",
    "ovrig_langsiktig_gjeld",
    "gjeld_kredittinst_kort",
    "leverandorgjeld",
    "betalbar_skatt",
    "skyldige_offentlige_avgifter",
    "utbytte_gjeld",
    "kortsiktig_konserngjeld",
    "annen_kortsiktig_gjeld",
)

OFF_BALANCE_LEAF_KEYS: tuple[str, ...] = (
    "garantistillelser",
    "pantstillelser",
)

ALL_OVERRIDE_KEYS: frozenset[str] = frozenset(
    (*RESULT_LEAF_KEYS, *BALANCE_LEAF_KEYS, *OFF_BALANCE_LEAF_KEYS)
)


@dataclass(frozen=True)
class LedgerAccountBalance:
    """One GL account's movement or closing balance (debit minus credit)."""

    account_number: str
    description: str
    account_type: AccountType
    net: Decimal  # debit minus credit


@dataclass(frozen=True)
class StatementLine:
    """One rendered line in resultatregnskap or balanse."""

    key: str
    label: str
    kind: str  # "post" or "sum"
    section: str
    orid_current: int | None
    orid_prior: int | None
    current: int
    prior: int | None
    exact_current: Decimal
    note_ref: str | None = None
    editable_prior: bool = True


@dataclass(frozen=True)
class ShareholderSummary:
    """Summary of one shareholder for Note 3 (Aksjekapital og aksjonærer)."""

    name: str
    shares: int
    ownership_percent: Decimal
    share_class: str = "Ordinære aksjer"


@dataclass(frozen=True)
class FixedAssetNoteSummary:
    """Summary of tangible/intangible fixed assets for Note 4."""

    tangible_opening_cost: int = 0
    tangible_additions: int = 0
    tangible_disposals: int = 0
    tangible_closing_cost: int = 0
    tangible_year_depreciation: int = 0
    tangible_accumulated_depreciation: int = 0
    tangible_carrying_value: int = 0
    intangible_opening_cost: int = 0
    intangible_additions: int = 0
    intangible_disposals: int = 0
    intangible_closing_cost: int = 0
    intangible_year_depreciation: int = 0
    intangible_accumulated_depreciation: int = 0
    intangible_carrying_value: int = 0


@dataclass(frozen=True)
class DraftInput:
    """Normalized user-editable settings and notes for one annual accounts year."""

    small_company: bool = True
    is_parent: bool = False
    consolidated: bool = False
    winding_up_accounts: bool = False
    auditor_elected_out: bool = True
    prepared_by_external_accountant: bool = False
    external_accountant_assistance: bool = False
    approval_date: date | None = None
    confirming_representative: str | None = None
    contact_email: str | None = None
    board_meeting_date: date | None = None
    board_place: str | None = None
    board_members: tuple[dict[str, Any], ...] = ()
    general_meeting_date: date | None = None
    general_meeting_place: str | None = None
    general_meeting_chair: str | None = None
    general_meeting_cosigner: str | None = None
    dividend_proposed: Decimal = ZERO
    prior_year_overrides: dict[str, int] = field(default_factory=dict)
    notes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedNotes:
    """Resolved small-company notes ready for UI, PDF, and RR-0002-U XML."""

    regnskapsprinsipper: str
    usikkerhet_fortsatt_drift: bool
    usikkerhet_fortsatt_drift_tekst: str
    antall_aarsverk: Decimal
    har_otp_plikt: bool
    otp_tekst: str
    lonn_aarets: int
    lonn_fjoraarets: int | None
    arbeidsgiveravgift_aarets: int
    arbeidsgiveravgift_fjoraarets: int | None
    pensjon_aarets: int
    pensjon_fjoraarets: int | None
    andre_ytelser_aarets: int
    andre_ytelser_fjoraarets: int | None
    sum_lonnskostnad_aarets: int
    sum_lonnskostnad_fjoraarets: int | None
    lonnskostnad_fritekst: str
    aksjekapital_tekst: str
    antall_aksjer: int | None
    paalydende: Decimal | None
    aksjonaerer: tuple[ShareholderSummary, ...]
    egne_aksjer_antall: int
    anleggsmidler: FixedAssetNoteSummary
    anleggsmidler_fritekst: str
    bundne_bankinnskudd: int
    gjeld_over_fem_aar: int
    pantstillelser_gjeld: int
    pantsatte_eiendeler_verdi: int
    garantiforpliktelser: int
    gjeld_fritekst: str
    laan_til_styret: int
    laan_til_daglig_leder: int
    sikkerhet_styret: int
    sikkerhet_daglig_leder: int
    laan_rentesats_styret: Decimal | None
    laan_rentesats_daglig_leder: Decimal | None
    laan_vilkaar_styret: str
    laan_vilkaar_daglig_leder: str
    laan_fritekst: str
    egenkapital_ib: int
    egenkapital_aarsresultat: int
    egenkapital_utbytte: int
    egenkapital_andre_endringer: int
    egenkapital_ub: int
    skatt_tekst: str
    ytterligere_opplysninger: str
    egendefinerte_noter: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class ComputedAarsregnskap:
    """Complete computed annual accounts ready for UI, XML, PDF, and filing."""

    year: int
    start_date: date
    end_date: date
    year_closed: bool
    organisation_form: str | None
    has_prior_year_in_books: bool
    uses_prior_year_overrides: bool
    result_lines: tuple[StatementLine, ...]
    disposition_lines: tuple[StatementLine, ...]
    balance_asset_lines: tuple[StatementLine, ...]
    balance_equity_liability_lines: tuple[StatementLine, ...]
    off_balance_lines: tuple[StatementLine, ...]
    current_values: dict[str, int]
    prior_values: dict[str, int | None]
    driftsinntekter: int
    driftsresultat: int
    resultat_for_skatt: int
    skattekostnad: int
    aarsresultat: int
    sum_anleggsmidler: int
    sum_omlopsmidler: int
    sum_eiendeler: int
    sum_innskutt_egenkapital: int
    sum_opptjent_egenkapital: int
    sum_egenkapital: int
    sum_langsiktig_gjeld: int
    sum_kortsiktig_gjeld: int
    sum_gjeld: int
    sum_egenkapital_og_gjeld: int
    balance_diff: int
    rounding_adjustment: int
    notes: ResolvedNotes
    draft: DraftInput
    findings: tuple[Finding, ...]
    content_hash: str

    @property
    def blocked(self) -> bool:
        return any(f.severity is Severity.FEIL for f in self.findings)


def round_kr(value: Decimal) -> int:
    """Round a Decimal NOK amount to the nearest whole krone (half up)."""
    return int(value.quantize(ONE_KR, rounding=ROUND_HALF_UP))


def _parse_decimal(raw: Any, default: Decimal = ZERO) -> Decimal:
    if raw is None or raw == "":
        return default
    if isinstance(raw, Decimal):
        return raw
    try:
        return Decimal(str(raw).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return default


def _parse_int(raw: Any, default: int = 0) -> int:
    if raw is None or raw == "":
        return default
    try:
        return int(round_kr(_parse_decimal(raw, Decimal(default))))
    except (ValueError, InvalidOperation):
        return default


def _parse_optional_decimal(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _account_int(number: str) -> int | None:
    digits = "".join(ch for ch in number if ch.isdigit())
    if len(digits) < 4:
        return None
    return int(digits[:4])


def _classify_result_account(account_number: str, account_type: AccountType) -> tuple[str, str]:
    """Return ``(leaf_key, side)`` where side is ``income``, ``cost``, or ``disposition``."""
    num = _account_int(account_number)
    if num is not None:
        if 3000 <= num <= 3299:
            return "salgsinntekt", "income"
        if 3300 <= num <= 3999:
            return "annen_driftsinntekt", "income"
        if 4290 <= num <= 4299:
            return "beholdningsendring_varer", "cost"
        if 4000 <= num <= 4999:
            return "varekostnad", "cost"
        if 5000 <= num <= 5999:
            return "lonnskostnad", "cost"
        if 6000 <= num <= 6049:
            return "avskrivning", "cost"
        if 6050 <= num <= 6099:
            return "nedskrivning", "cost"
        if 6100 <= num <= 7999:
            return "annen_driftskostnad", "cost"
        if 8000 <= num <= 8019:
            return "finansinntekt_konsern", "income"
        if 8020 <= num <= 8039:
            return "finansinntekt_andre_inv", "income"
        if 8040 <= num <= 8049:
            return "renteinntekt_konsern", "income"
        if 8050 <= num <= 8059:
            return "annen_renteinntekt", "income"
        if 8060 <= num <= 8099:
            return "annen_finansinntekt", "income"
        if 8100 <= num <= 8109:
            return "verdireduksjon_fin_instr", "cost"
        if 8110 <= num <= 8129:
            return "nedskrivning_fin_eiendeler", "cost"
        if 8130 <= num <= 8149:
            return "rentekostnad_konsern", "cost"
        if 8150 <= num <= 8159:
            return "annen_rentekostnad", "cost"
        if 8160 <= num <= 8199:
            return "annen_finanskostnad", "cost"
        if 8200 <= num <= 8299:
            if account_type is AccountType.INNTEKT:
                return "annen_finansinntekt", "income"
            return "annen_finanskostnad", "cost"
        if 8300 <= num <= 8799:
            return "skattekostnad", "cost"
        if 8900 <= num <= 8919:
            return "disp_fond", "disposition"
        if 8920 <= num <= 8929:
            return "disp_utbytte", "disposition"
        if 8930 <= num <= 8939:
            return "disp_konsernbidrag_avgitt", "disposition"
        if 8990 <= num <= 8999:
            return "disp_udekket_tap", "disposition"
        if 8800 <= num <= 8989:
            return "disp_annen_egenkapital", "disposition"

    if account_type is AccountType.INNTEKT:
        return "annen_driftsinntekt", "income"
    return "annen_driftskostnad", "cost"


def _classify_balance_account(account_number: str, account_type: AccountType) -> tuple[str, str]:
    """Return ``(leaf_key, side)`` where side is ``asset``, ``claim``, or ``loss``."""
    num = _account_int(account_number)
    if num is not None:
        if 1000 <= num <= 1019:
            return "utvikling", "asset"
        if 1020 <= num <= 1069:
            return "konsesjoner_rettigheter", "asset"
        if 1070 <= num <= 1079:
            return "utsatt_skattefordel", "asset"
        if 1080 <= num <= 1099:
            return "goodwill", "asset"
        if 1100 <= num <= 1199:
            return "tomter_bygninger", "asset"
        if 1200 <= num <= 1219:
            return "maskiner_anlegg", "asset"
        if 1220 <= num <= 1229:
            return "skip_rigg_fly", "asset"
        if 1230 <= num <= 1299:
            return "driftslosore_inventar", "asset"
        if 1300 <= num <= 1309:
            return "inv_datterselskap", "asset"
        if 1310 <= num <= 1319:
            return "inv_samme_konsern", "asset"
        if 1320 <= num <= 1329:
            return "laan_samme_konsern", "asset"
        if 1330 <= num <= 1339:
            return "inv_tilknyttet", "asset"
        if 1340 <= num <= 1349:
            return "laan_tilknyttet", "asset"
        if 1350 <= num <= 1369:
            return "inv_aksjer_andeler", "asset"
        if 1370 <= num <= 1379:
            return "obligasjoner", "asset"
        if 1380 <= num <= 1399:
            return "andre_langsiktige_fordringer", "asset"
        if 1400 <= num <= 1499:
            return "varer", "asset"
        if 1500 <= num <= 1549:
            return "kundefordringer", "asset"
        if 1550 <= num <= 1569:
            return "konsernfordringer", "asset"
        if 1570 <= num <= 1799:
            return "andre_kortsiktige_fordringer", "asset"
        if 1800 <= num <= 1829:
            return "markedsbaserte_aksjer", "asset"
        if 1830 <= num <= 1899:
            return "andre_fin_instrumenter", "asset"
        if 1900 <= num <= 1999:
            return "bankinnskudd_kontanter", "asset"
        if 2000 <= num <= 2009:
            return "selskapskapital", "claim"
        if 2010 <= num <= 2019:
            return "egne_aksjer", "claim"
        if 2020 <= num <= 2029:
            return "overkurs", "claim"
        if 2030 <= num <= 2039:
            return "annen_innskutt_egenkapital", "claim"
        if 2040 <= num <= 2049:
            return "fond", "claim"
        if 2050 <= num <= 2079:
            return "annen_egenkapital", "claim"
        if 2080 <= num <= 2099:
            # Udekket tap (ORID 249): positive when debit balance exists,
            # subtracted in sumOpptjentEgenkapital per Brreg formula.
            return "udekket_tap", "loss"
        if 2100 <= num <= 2119:
            return "pensjonsforpliktelser", "claim"
        if 2120 <= num <= 2129:
            return "utsatt_skatt", "claim"
        if 2130 <= num <= 2199:
            return "andre_avsetninger", "claim"
        if 2200 <= num <= 2209:
            return "konvertible_laan_lang", "claim"
        if 2210 <= num <= 2219:
            return "obligasjonslaan", "claim"
        if 2220 <= num <= 2249:
            return "gjeld_kredittinst_lang", "claim"
        if 2250 <= num <= 2269:
            return "langsiktig_konserngjeld", "claim"
        if 2270 <= num <= 2279:
            return "ansvarlig_laanekapital", "claim"
        if 2280 <= num <= 2299:
            return "ovrig_langsiktig_gjeld", "claim"
        if 2300 <= num <= 2399:
            return "gjeld_kredittinst_kort", "claim"
        if 2400 <= num <= 2499:
            return "leverandorgjeld", "claim"
        if 2500 <= num <= 2599:
            return "betalbar_skatt", "claim"
        if 2600 <= num <= 2799:
            return "skyldige_offentlige_avgifter", "claim"
        if 2800 <= num <= 2809:
            return "utbytte_gjeld", "claim"
        if 2810 <= num <= 2899:
            return "kortsiktig_konserngjeld", "claim"
        if 2900 <= num <= 2999:
            return "annen_kortsiktig_gjeld", "claim"

    if account_type is AccountType.EIENDEL:
        return "andre_kortsiktige_fordringer", "asset"
    if account_type is AccountType.EGENKAPITAL:
        return "annen_egenkapital", "claim"
    return "annen_kortsiktig_gjeld", "claim"


def _is_result_account(account_number: str, account_type: AccountType) -> bool:
    num = _account_int(account_number)
    if num is not None:
        return num >= 3000
    return account_type in (AccountType.INNTEKT, AccountType.KOSTNAD)


def _aggregate_year(
    movements: list[LedgerAccountBalance],
    cumulative: list[LedgerAccountBalance],
) -> tuple[dict[str, Decimal], dict[str, Decimal], dict[str, Decimal]]:
    """Aggregate one year's P&L, balance sheet, and payroll breakdown in exact Decimal."""
    exact: dict[str, Decimal] = dict.fromkeys(
        (*RESULT_LEAF_KEYS, *BALANCE_LEAF_KEYS, *OFF_BALANCE_LEAF_KEYS), ZERO
    )
    payroll = {
        "lonn": ZERO,
        "aga": ZERO,
        "pensjon": ZERO,
        "andre": ZERO,
    }
    extras = {
        "bundne_bankinnskudd": ZERO,
    }

    # --- P&L (movements within the financial year) ---
    for item in movements:
        if not _is_result_account(item.account_number, item.account_type):
            continue
        key, side = _classify_result_account(item.account_number, item.account_type)
        amount = -item.net if side == "income" else item.net
        exact[key] += amount

        num = _account_int(item.account_number)
        if num is not None and 5000 <= num <= 5999:
            if 5000 <= num <= 5399:
                payroll["lonn"] += item.net
            elif 5400 <= num <= 5419:
                payroll["aga"] += item.net
            elif (5420 <= num <= 5499) or (5940 <= num <= 5949):
                payroll["pensjon"] += item.net
            else:
                payroll["andre"] += item.net

    # --- Balance sheet (cumulative posted lines through end of year) ---
    vat_net = ZERO  # debit - credit across 2700..2749
    undisposed = ZERO  # cumulative credit - debit across all P&L accounts
    for item in cumulative:
        if _is_result_account(item.account_number, item.account_type):
            undisposed -= item.net
            continue
        num = _account_int(item.account_number)
        if num is not None and 2700 <= num <= 2749:
            vat_net += item.net
            continue
        if num is not None and 1950 <= num <= 1959:
            extras["bundne_bankinnskudd"] += item.net
        key, side = _classify_balance_account(item.account_number, item.account_type)
        if side in ("asset", "loss"):
            exact[key] += item.net
        else:
            exact[key] -= item.net

    # Net VAT across 2700-2749: credit balance is short-term tax liability;
    # debit balance (Skatteetaten owes VAT refund) is a short-term receivable.
    if vat_net > ZERO:
        exact["andre_kortsiktige_fordringer"] += vat_net
    elif vat_net < ZERO:
        exact["skyldige_offentlige_avgifter"] += -vat_net

    # If the financial year is still open, undisposed result sits on P&L
    # accounts instead of equity. Fold it into annen_egenkapital / udekket_tap
    # so the preview balance sheet balances cleanly while the closing finding
    # still blocks submission until årsavslutning is run.
    if undisposed != ZERO:
        if undisposed > ZERO:
            if exact["udekket_tap"] > ZERO:
                cover = min(undisposed, exact["udekket_tap"])
                exact["udekket_tap"] -= cover
                exact["annen_egenkapital"] += undisposed - cover
            else:
                exact["annen_egenkapital"] += undisposed
        else:
            loss = -undisposed
            if exact["annen_egenkapital"] > ZERO:
                cover = min(loss, exact["annen_egenkapital"])
                exact["annen_egenkapital"] -= cover
                exact["udekket_tap"] += loss - cover
            else:
                exact["udekket_tap"] += loss

    # Normalize negative annen_egenkapital into udekket_tap (or negative
    # udekket_tap into annen_egenkapital) so presentation matches regnskapsloven.
    if exact["annen_egenkapital"] < ZERO:
        exact["udekket_tap"] += -exact["annen_egenkapital"]
        exact["annen_egenkapital"] = ZERO
    if exact["udekket_tap"] < ZERO:
        exact["annen_egenkapital"] += -exact["udekket_tap"]
        exact["udekket_tap"] = ZERO

    return exact, payroll, extras


def _compute_totals(values: dict[str, int]) -> dict[str, int]:
    """Compute every RR-0002-U subtotal from whole-krone leaf values."""
    v = dict(values)

    v["sum_driftsinntekter"] = v["salgsinntekt"] + v["annen_driftsinntekt"]
    v["sum_driftskostnader"] = (
        v["beholdningsendring_varer"]
        + v["varekostnad"]
        + v["lonnskostnad"]
        + v["avskrivning"]
        + v["nedskrivning"]
        + v["annen_driftskostnad"]
    )
    v["driftsresultat"] = v["sum_driftsinntekter"] - v["sum_driftskostnader"]

    v["sum_finansinntekter"] = (
        v["finansinntekt_konsern"]
        + v["finansinntekt_andre_inv"]
        + v["renteinntekt_konsern"]
        + v["annen_renteinntekt"]
        + v["annen_finansinntekt"]
    )
    v["sum_finanskostnader"] = (
        v["verdireduksjon_fin_instr"]
        + v["nedskrivning_fin_eiendeler"]
        + v["rentekostnad_konsern"]
        + v["annen_rentekostnad"]
        + v["annen_finanskostnad"]
    )
    v["netto_finans"] = v["sum_finansinntekter"] - v["sum_finanskostnader"]
    v["resultat_for_skatt"] = v["driftsresultat"] + v["netto_finans"]
    v["aarsresultat"] = v["resultat_for_skatt"] - v["skattekostnad"]

    v["sum_overfoeringer"] = (
        v["disp_fond"]
        + v["disp_utbytte"]
        - v["disp_konsernbidrag_mottatt"]
        + v["disp_konsernbidrag_avgitt"]
        + v["disp_udekket_tap"]
        + v["disp_annen_egenkapital"]
    )

    v["sum_immaterielle"] = (
        v["utvikling"] + v["konsesjoner_rettigheter"] + v["utsatt_skattefordel"] + v["goodwill"]
    )
    v["sum_varige_driftsmidler"] = (
        v["tomter_bygninger"]
        + v["maskiner_anlegg"]
        + v["skip_rigg_fly"]
        + v["driftslosore_inventar"]
    )
    v["sum_finansielle_anleggsmidler"] = (
        v["inv_datterselskap"]
        + v["inv_samme_konsern"]
        + v["laan_samme_konsern"]
        + v["inv_tilknyttet"]
        + v["laan_tilknyttet"]
        + v["inv_aksjer_andeler"]
        + v["obligasjoner"]
        + v["andre_langsiktige_fordringer"]
    )
    v["sum_anleggsmidler"] = (
        v["sum_immaterielle"] + v["sum_varige_driftsmidler"] + v["sum_finansielle_anleggsmidler"]
    )

    v["sum_varer"] = v["varer"]
    v["sum_fordringer"] = (
        v["kundefordringer"] + v["andre_kortsiktige_fordringer"] + v["konsernfordringer"]
    )
    v["sum_investeringer"] = v["markedsbaserte_aksjer"] + v["andre_fin_instrumenter"]
    v["sum_bankinnskudd"] = v["bankinnskudd_kontanter"]
    v["sum_omlopsmidler"] = (
        v["sum_varer"] + v["sum_fordringer"] + v["sum_investeringer"] + v["sum_bankinnskudd"]
    )
    v["sum_eiendeler"] = v["sum_anleggsmidler"] + v["sum_omlopsmidler"]

    v["sum_innskutt_egenkapital"] = (
        v["selskapskapital"] + v["egne_aksjer"] + v["overkurs"] + v["annen_innskutt_egenkapital"]
    )
    # Brreg formula (ORID 9702): 27579 + 23857 + 3274 - 249 + 7734.
    v["sum_opptjent_egenkapital"] = (
        v["fond"] + v["avsatt_utbytte"] + v["annen_egenkapital"] - v["udekket_tap"]
    )
    v["sum_egenkapital"] = v["sum_innskutt_egenkapital"] + v["sum_opptjent_egenkapital"]

    v["sum_avsetninger"] = v["pensjonsforpliktelser"] + v["utsatt_skatt"] + v["andre_avsetninger"]
    v["sum_annen_langsiktig_gjeld"] = (
        v["konvertible_laan_lang"]
        + v["obligasjonslaan"]
        + v["gjeld_kredittinst_lang"]
        + v["langsiktig_konserngjeld"]
        + v["ansvarlig_laanekapital"]
        + v["ovrig_langsiktig_gjeld"]
    )
    v["sum_langsiktig_gjeld"] = v["sum_avsetninger"] + v["sum_annen_langsiktig_gjeld"]

    v["sum_kortsiktig_gjeld"] = (
        v["gjeld_kredittinst_kort"]
        + v["leverandorgjeld"]
        + v["betalbar_skatt"]
        + v["skyldige_offentlige_avgifter"]
        + v["utbytte_gjeld"]
        + v["kortsiktig_konserngjeld"]
        + v["annen_kortsiktig_gjeld"]
    )
    v["sum_gjeld"] = v["sum_langsiktig_gjeld"] + v["sum_kortsiktig_gjeld"]
    v["sum_egenkapital_og_gjeld"] = v["sum_egenkapital"] + v["sum_gjeld"]
    return v


def default_accounting_principles_text(year: int, *, small_company: bool = True) -> str:
    """Standard Norwegian bokmål regnskapsprinsipper boilerplate (regnskapsloven § 7-35)."""
    rules_line = (
        "Årsregnskapet er satt opp i samsvar med regnskapsloven og god regnskapsskikk for "
        "små foretak."
        if small_company
        else "Årsregnskapet er satt opp i samsvar med regnskapsloven og god regnskapsskikk."
    )
    return (
        f"{rules_line}\n\n"
        "Driftsinntekter: Inntektsføring ved salg av varer og tjenester skjer på "
        "leveringstidspunktet.\n"
        "Skatt: Skattekostnaden i resultatregnskapet omfatter både periodens betalbare skatt "
        "og endring i utsatt skatt. Utsatt skatt beregnes med 22 % på grunnlag av midlertidige "
        "forskjeller mellom regnskapsmessige og skattemessige verdier.\n"
        "Klassifisering og vurdering av balanseposter: Eiendeler bestemt til varig eie eller "
        "bruk klassifiseres som anleggsmidler. Omløpsmidler og kortsiktig gjeld omfatter "
        "poster som forfaller til betaling innen ett år etter balansedagen. Omløpsmidler "
        "vurderes til laveste av anskaffelseskost og virkelig verdi.\n"
        "Varige driftsmidler: Varige driftsmidler balanseføres og avskrives lineært eller etter "
        "saldo over driftsmidlets forventede økonomiske levetid.\n"
        "Fordringer: Kundefordringer og andre fordringer oppføres til pålydende etter fradrag "
        "for avsetning til forventet tap.\n"
        f"Årsregnskapet for {year} er avlagt under forutsetning om fortsatt drift."
    )


def _build_notes(
    *,
    year: int,
    draft: DraftInput,
    current: dict[str, int],
    prior: dict[str, int | None],
    has_prior: bool,
    payroll_curr: dict[str, Decimal],
    payroll_prior: dict[str, Decimal] | None,
    extras_curr: dict[str, Decimal],
    shareholders: list[ShareholderSummary],
    share_count: int | None,
    par_value: Decimal | None,
    fixed_assets: FixedAssetNoteSummary,
) -> ResolvedNotes:
    raw = draft.notes or {}

    regnskapsprinsipper = str(raw.get("regnskapsprinsipper") or "").strip()
    if not regnskapsprinsipper:
        regnskapsprinsipper = default_accounting_principles_text(
            year, small_company=draft.small_company
        )

    usikkerhet_drift = bool(raw.get("usikkerhet_fortsatt_drift", False))
    usikkerhet_tekst = str(raw.get("usikkerhet_fortsatt_drift_tekst") or "").strip()

    antall_aarsverk = _parse_decimal(raw.get("antall_aarsverk"), Decimal("0"))
    har_otp = bool(raw.get("har_otp_plikt", False))
    otp_tekst = str(raw.get("otp_tekst") or "").strip()
    if not otp_tekst:
        if har_otp:
            otp_tekst = (
                "Selskapet er pliktig til å ha tjenestepensjonsordning etter lov om obligatorisk "
                "tjenestepensjon. Selskapets pensjonsordning tilfredsstiller kravene i denne loven."
            )
        else:
            otp_tekst = (
                "Selskapet er ikke pliktig til å ha tjenestepensjonsordning etter lov om "
                "obligatorisk tjenestepensjon."
            )

    lonn_aarets = round_kr(payroll_curr["lonn"])
    aga_aarets = round_kr(payroll_curr["aga"])
    pensjon_aarets = round_kr(payroll_curr["pensjon"])
    andre_aarets = round_kr(payroll_curr["andre"])
    sum_lonn_aarets = lonn_aarets + aga_aarets + pensjon_aarets + andre_aarets
    # Align payroll note total with rounded P&L lonnskostnad if rounding differed by 1 kr.
    if (
        current["lonnskostnad"] != sum_lonn_aarets
        and abs(current["lonnskostnad"] - sum_lonn_aarets) <= 2
    ):
        andre_aarets += current["lonnskostnad"] - sum_lonn_aarets
        sum_lonn_aarets = current["lonnskostnad"]

    lonn_fjor: int | None = None
    aga_fjor: int | None = None
    pensjon_fjor: int | None = None
    andre_fjor: int | None = None
    sum_lonn_fjor: int | None = None
    if payroll_prior is not None:
        lonn_fjor = round_kr(payroll_prior["lonn"])
        aga_fjor = round_kr(payroll_prior["aga"])
        pensjon_fjor = round_kr(payroll_prior["pensjon"])
        andre_fjor = round_kr(payroll_prior["andre"])
        sum_lonn_fjor = lonn_fjor + aga_fjor + pensjon_fjor + andre_fjor
    elif has_prior and prior.get("lonnskostnad") is not None:
        sum_lonn_fjor = prior["lonnskostnad"]

    lonn_fritekst = str(raw.get("lonnskostnad_fritekst") or "").strip()
    if not lonn_fritekst:
        lonn_fritekst = otp_tekst

    # Note 3: Aksjekapital og aksjonærer.
    aksjekapital_tekst = str(raw.get("aksjekapital_tekst") or "").strip()
    if not aksjekapital_tekst:
        if share_count is not None and par_value is not None and shareholders:
            par_str = f"{par_value.quantize(Decimal('0.01'))}".replace(".", ",")
            lines = [
                f"Aksjekapitalen utgjør kr {current['selskapskapital']:,}".replace(",", " ")
                + f" fordelt på {share_count:,}".replace(",", " ")
                + f" aksjer pålydende kr {par_str}. Alle aksjer har like rettigheter."
            ]
            for sh in shareholders[:20]:
                pct = f"{sh.ownership_percent.quantize(Decimal('0.01'))}".replace(".", ",")
                lines.append(f"- {sh.name}: {sh.shares} aksjer ({pct} %)")
            aksjekapital_tekst = "\n".join(lines)
        elif current["selskapskapital"] > 0:
            cap_str = f"{current['selskapskapital']:,}".replace(",", " ")
            aksjekapital_tekst = f"Selskapets aksjekapital per 31.12.{year} er kr {cap_str}."

    egne_aksjer_antall = _parse_int(raw.get("egne_aksjer_antall"), 0)

    # Note 4: Varige driftsmidler og immaterielle eiendeler.
    anlegg_fritekst = str(raw.get("anleggsmidler_fritekst") or "").strip()
    if not anlegg_fritekst and (
        fixed_assets.tangible_carrying_value > 0 or current["sum_varige_driftsmidler"] > 0
    ):
        anlegg_fritekst = (
            "Varige driftsmidler avskrives planmessig over forventet økonomisk levetid."
        )

    # Bundne bankinnskudd (skattetrekkskonto 1950 + optional user override).
    auto_bundne = max(0, round_kr(extras_curr["bundne_bankinnskudd"]))
    bundne = _parse_int(raw.get("bundne_bankinnskudd"), auto_bundne)
    gjeld_over_fem = _parse_int(raw.get("gjeld_over_fem_aar"), 0)
    pant_gjeld = _parse_int(raw.get("pantstillelser_gjeld"), 0)
    pantsatte_verdi = _parse_int(raw.get("pantsatte_eiendeler_verdi"), 0)
    garantier = _parse_int(raw.get("garantiforpliktelser"), 0)
    gjeld_fritekst = str(raw.get("gjeld_fritekst") or "").strip()
    if not gjeld_fritekst and bundne > 0:
        bundne_str = f"{bundne:,}".replace(",", " ")
        gjeld_fritekst = f"Bundne skattetrekksmidler utgjør kr {bundne_str} per 31.12.{year}."

    laan_styret = _parse_int(raw.get("laan_til_styret"), 0)
    laan_dl = _parse_int(raw.get("laan_til_daglig_leder"), 0)
    sikk_styret = _parse_int(raw.get("sikkerhet_styret"), 0)
    sikk_dl = _parse_int(raw.get("sikkerhet_daglig_leder"), 0)
    rente_styret = _parse_optional_decimal(raw.get("laan_rentesats_styret"))
    rente_dl = _parse_optional_decimal(raw.get("laan_rentesats_daglig_leder"))
    vilkaar_styret = str(raw.get("laan_vilkaar_styret") or "").strip()
    vilkaar_dl = str(raw.get("laan_vilkaar_daglig_leder") or "").strip()
    laan_fritekst = str(raw.get("laan_fritekst") or "").strip()

    # Egenkapital note (IB + årsresultat - utbytte + andre endringer = UB).
    ek_ub = current["sum_egenkapital"]
    prior_ek = prior.get("sum_egenkapital") if has_prior else None
    ek_ib = prior_ek if prior_ek is not None else 0
    ek_res = current["aarsresultat"]
    ek_utbytte = current["disp_utbytte"] or round_kr(draft.dividend_proposed)
    ek_andre = ek_ub - (ek_ib + ek_res - ek_utbytte)

    skatt_tekst = str(raw.get("skatt_tekst") or "").strip()
    if not skatt_tekst:
        skatt_str = f"{current['skattekostnad']:,}".replace(",", " ")
        bet_str = f"{current['betalbar_skatt']:,}".replace(",", " ")
        skatt_tekst = (
            f"Årets skattekostnad utgjør kr {skatt_str}. Betalbar skatt i balansen per "
            f"31.12.{year} er kr {bet_str}."
        )

    custom_raw = raw.get("egendefinerte_noter")
    custom_notes: list[dict[str, str]] = []
    if isinstance(custom_raw, list):
        for entry in custom_raw:
            if isinstance(entry, dict):
                title = str(entry.get("tittel") or "").strip()
                text = str(entry.get("tekst") or "").strip()
                if title or text:
                    custom_notes.append({"tittel": title[:120], "tekst": text[:4000]})

    ytterligere = str(raw.get("ytterligere_opplysninger") or "").strip()

    return ResolvedNotes(
        regnskapsprinsipper=regnskapsprinsipper,
        usikkerhet_fortsatt_drift=usikkerhet_drift,
        usikkerhet_fortsatt_drift_tekst=usikkerhet_tekst,
        antall_aarsverk=antall_aarsverk,
        har_otp_plikt=har_otp,
        otp_tekst=otp_tekst,
        lonn_aarets=lonn_aarets,
        lonn_fjoraarets=lonn_fjor,
        arbeidsgiveravgift_aarets=aga_aarets,
        arbeidsgiveravgift_fjoraarets=aga_fjor,
        pensjon_aarets=pensjon_aarets,
        pensjon_fjoraarets=pensjon_fjor,
        andre_ytelser_aarets=andre_aarets,
        andre_ytelser_fjoraarets=andre_fjor,
        sum_lonnskostnad_aarets=sum_lonn_aarets,
        sum_lonnskostnad_fjoraarets=sum_lonn_fjor,
        lonnskostnad_fritekst=lonn_fritekst,
        aksjekapital_tekst=aksjekapital_tekst,
        antall_aksjer=share_count,
        paalydende=par_value,
        aksjonaerer=tuple(shareholders),
        egne_aksjer_antall=egne_aksjer_antall,
        anleggsmidler=fixed_assets,
        anleggsmidler_fritekst=anlegg_fritekst,
        bundne_bankinnskudd=bundne,
        gjeld_over_fem_aar=gjeld_over_fem,
        pantstillelser_gjeld=pant_gjeld,
        pantsatte_eiendeler_verdi=pantsatte_verdi,
        garantiforpliktelser=garantier,
        gjeld_fritekst=gjeld_fritekst,
        laan_til_styret=laan_styret,
        laan_til_daglig_leder=laan_dl,
        sikkerhet_styret=sikk_styret,
        sikkerhet_daglig_leder=sikk_dl,
        laan_rentesats_styret=rente_styret,
        laan_rentesats_daglig_leder=rente_dl,
        laan_vilkaar_styret=vilkaar_styret,
        laan_vilkaar_daglig_leder=vilkaar_dl,
        laan_fritekst=laan_fritekst,
        egenkapital_ib=ek_ib,
        egenkapital_aarsresultat=ek_res,
        egenkapital_utbytte=ek_utbytte,
        egenkapital_andre_endringer=ek_andre,
        egenkapital_ub=ek_ub,
        skatt_tekst=skatt_tekst,
        ytterligere_opplysninger=ytterligere,
        egendefinerte_noter=tuple(custom_notes),
    )


def compute_aarsregnskap(
    *,
    company: Company,
    year: int,
    start_date: date,
    end_date: date,
    year_closed: bool,
    current_movements: list[LedgerAccountBalance],
    current_cumulative: list[LedgerAccountBalance],
    prior_movements: list[LedgerAccountBalance] | None,
    prior_cumulative: list[LedgerAccountBalance] | None,
    draft: DraftInput,
    shareholders: list[ShareholderSummary] | None = None,
    share_count: int | None = None,
    par_value: Decimal | None = None,
    fixed_assets: FixedAssetNoteSummary | None = None,
    today: date | None = None,
) -> ComputedAarsregnskap:
    """Compute the complete årsregnskap (RR-0002 / RR-0002-U) and compliance checks."""
    form = (company.organisation_form or "").strip().upper() or None
    exact_curr, payroll_curr, extras_curr = _aggregate_year(current_movements, current_cumulative)

    curr_leaf: dict[str, int] = {key: round_kr(val) for key, val in exact_curr.items()}
    # Off-balance-sheet items come from draft notes (garantier / pantstillelser).
    raw_notes = draft.notes or {}
    curr_leaf["garantistillelser"] = _parse_int(raw_notes.get("garantiforpliktelser"), 0)
    curr_leaf["pantstillelser"] = _parse_int(raw_notes.get("pantstillelser_gjeld"), 0)

    curr = _compute_totals(curr_leaf)

    # Check exact Decimal balance vs rounded balance: if the exact Decimal books
    # balance (as double-entry guarantees) and rounding multiple lines to whole
    # NOK produced a small 1-5 kr rounding difference, absorb it cleanly so
    # sumEiendeler == sumEgenkapitalGjeld in Brreg's RR-0002-U validation.
    exact_assets = sum(
        (exact_curr[k] for k in BALANCE_LEAF_KEYS[:23]),
        ZERO,
    )
    exact_eq_liab = (
        sum((exact_curr[k] for k in BALANCE_LEAF_KEYS[23:] if k != "udekket_tap"), ZERO)
        - exact_curr["udekket_tap"]
    )
    exact_diff = abs(exact_assets - exact_eq_liab)
    rounding_adj = 0
    if exact_diff < Decimal("0.05") and curr["sum_eiendeler"] != curr["sum_egenkapital_og_gjeld"]:
        diff = curr["sum_eiendeler"] - curr["sum_egenkapital_og_gjeld"]
        if abs(diff) <= 5:
            rounding_adj = diff
            if curr_leaf["annen_egenkapital"] != 0 or curr_leaf["udekket_tap"] == 0:
                curr_leaf["annen_egenkapital"] += diff
            else:
                curr_leaf["udekket_tap"] -= diff
            curr = _compute_totals(curr_leaf)

    # Ensure rounded disposition sum matches rounded aarsresultat when closed
    # (or when draft has a closing disposition), absorbing any 1-2 kr rounding residual.
    has_disp = any(
        curr_leaf[k] != 0
        for k in (
            "disp_fond",
            "disp_utbytte",
            "disp_konsernbidrag_mottatt",
            "disp_konsernbidrag_avgitt",
            "disp_udekket_tap",
            "disp_annen_egenkapital",
        )
    )
    if has_disp and curr["sum_overfoeringer"] != curr["aarsresultat"]:
        disp_diff = curr["aarsresultat"] - curr["sum_overfoeringer"]
        if abs(disp_diff) <= 2:
            if curr_leaf["disp_annen_egenkapital"] != 0 or curr_leaf["disp_udekket_tap"] == 0:
                curr_leaf["disp_annen_egenkapital"] += disp_diff
            else:
                curr_leaf["disp_udekket_tap"] += disp_diff
            curr = _compute_totals(curr_leaf)
    elif not has_disp and curr["aarsresultat"] != 0:
        # Preview disposition when year is still open so user sees how result will allocate.
        if curr["aarsresultat"] > 0:
            curr_leaf["disp_annen_egenkapital"] = curr["aarsresultat"]
        else:
            curr_leaf["disp_udekket_tap"] = curr["aarsresultat"]
        curr = _compute_totals(curr_leaf)

    # --- Prior-year comparative figures ---
    has_prior_books = prior_movements is not None and prior_cumulative is not None
    payroll_prior: dict[str, Decimal] | None = None
    prior_raw_overrides = {
        k: int(v) for k, v in (draft.prior_year_overrides or {}).items() if k in ALL_OVERRIDE_KEYS
    }
    uses_overrides = bool(prior_raw_overrides)
    has_prior = has_prior_books or uses_overrides

    prior_values: dict[str, int | None] = {}
    if has_prior:
        if has_prior_books and prior_movements is not None and prior_cumulative is not None:
            exact_prior, payroll_prior, _ = _aggregate_year(prior_movements, prior_cumulative)
            prior_leaf = {key: round_kr(val) for key, val in exact_prior.items()}
        else:
            prior_leaf = dict.fromkeys(
                (*RESULT_LEAF_KEYS, *BALANCE_LEAF_KEYS, *OFF_BALANCE_LEAF_KEYS), 0
            )
        # Manual overrides always win per line (lets companies whose prior year
        # was kept outside Myntos enter exact comparative figures).
        for key, val in prior_raw_overrides.items():
            prior_leaf[key] = val
        prior_totals = _compute_totals(prior_leaf)
        prior_values = dict(prior_totals)
    else:
        for key in curr:
            prior_values[key] = None

    # --- Notes ---
    resolved_notes = _build_notes(
        year=year,
        draft=draft,
        current=curr,
        prior=prior_values,
        has_prior=has_prior,
        payroll_curr=payroll_curr,
        payroll_prior=payroll_prior,
        extras_curr=extras_curr,
        shareholders=list(shareholders or ()),
        share_count=share_count,
        par_value=par_value,
        fixed_assets=fixed_assets or FixedAssetNoteSummary(),
    )

    # --- Structured statement rows ---
    def _row(
        key: str,
        label: str,
        section: str,
        orid_curr: int | None,
        orid_prev: int | None,
        *,
        kind: str = "post",
        note_ref: str | None = None,
    ) -> StatementLine:
        return StatementLine(
            key=key,
            label=label,
            kind=kind,
            section=section,
            orid_current=orid_curr,
            orid_prior=orid_prev,
            current=curr[key],
            prior=prior_values.get(key) if has_prior else None,
            exact_current=exact_curr.get(key, Decimal(curr[key])),
            note_ref=note_ref,
            editable_prior=(kind == "post" and key in ALL_OVERRIDE_KEYS),
        )

    def _keep(line: StatementLine, *, always: bool = False) -> bool:
        if always or line.kind == "sum":
            return True
        return line.current != 0 or (line.prior is not None and line.prior != 0)

    raw_result = [
        _row("salgsinntekt", "Salgsinntekt", "driftsinntekter", 1340, 7965),
        _row("annen_driftsinntekt", "Annen driftsinntekt", "driftsinntekter", 7709, 7966),
        _row("sum_driftsinntekter", "Sum driftsinntekter", "driftsinntekter", 72, 6972, kind="sum"),
        _row(
            "beholdningsendring_varer",
            "Endring i beholdning av varer under tilvirkning og ferdig varer",
            "driftskostnader",
            16924,
            16925,
        ),
        _row("varekostnad", "Varekostnad", "driftskostnader", 101, 6977),
        _row("lonnskostnad", "Lønnskostnad", "driftskostnader", 81, 6979, note_ref="2"),
        _row(
            "avskrivning",
            "Avskrivning på varige driftsmidler og immaterielle eiendeler",
            "driftskostnader",
            2139,
            10181,
            note_ref="4",
        ),
        _row(
            "nedskrivning",
            "Nedskrivning av varige driftsmidler og immaterielle eiendeler",
            "driftskostnader",
            2354,
            10180,
        ),
        _row("annen_driftskostnad", "Annen driftskostnad", "driftskostnader", 82, 7023),
        _row(
            "sum_driftskostnader",
            "Sum driftskostnader",
            "driftskostnader",
            17126,
            17127,
            kind="sum",
        ),
        _row("driftsresultat", "Driftsresultat", "driftsresultat", 146, 7026, kind="sum"),
        _row(
            "finansinntekt_konsern",
            "Inntekt på investering i datterselskap og tilknyttet selskap",
            "finans",
            27934,
            27935,
        ),
        _row(
            "finansinntekt_andre_inv",
            "Inntekt på andre investeringer",
            "finans",
            27936,
            27937,
        ),
        _row(
            "renteinntekt_konsern",
            "Renteinntekt fra foretak i samme konsern",
            "finans",
            149,
            7029,
        ),
        _row("annen_renteinntekt", "Annen renteinntekt", "finans", 150, 7030),
        _row("annen_finansinntekt", "Annen finansinntekt", "finans", 152, 7032),
        _row("sum_finansinntekter", "Sum finansinntekter", "finans", 153, 7993, kind="sum"),
        _row(
            "verdireduksjon_fin_instr",
            "Verdireduksjon finansielle instrumenter",
            "finans",
            25004,
            25005,
        ),
        _row(
            "nedskrivning_fin_eiendeler",
            "Nedskrivning av finansielle eiendeler",
            "finans",
            7035,
            7036,
        ),
        _row(
            "rentekostnad_konsern",
            "Rentekostnad til foretak i samme konsern",
            "finans",
            7037,
            7038,
        ),
        _row("annen_rentekostnad", "Annen rentekostnad", "finans", 2216, 7039),
        _row("annen_finanskostnad", "Annen finanskostnad", "finans", 156, 7041),
        _row("sum_finanskostnader", "Sum finanskostnader", "finans", 17130, 17131, kind="sum"),
        _row("netto_finans", "Netto finans", "finans", 158, 7999, kind="sum"),
        _row(
            "resultat_for_skatt",
            "Resultat før skattekostnad",
            "resultat",
            167,
            7042,
            kind="sum",
        ),
        _row("skattekostnad", "Skattekostnad", "resultat", 11835, 11836, note_ref="5"),
        _row("aarsresultat", "Årsresultat", "resultat", 172, 7054, kind="sum"),
    ]
    always_result = {"salgsinntekt", "annen_driftskostnad", "skattekostnad"}
    result_lines = tuple(r for r in raw_result if _keep(r, always=r.key in always_result))

    raw_disp = [
        _row("disp_fond", "Overføring til/fra fond", "disponering", 7055, 7056),
        _row("disp_utbytte", "Ordinært utbytte", "disponering", 6112, 9952),
        _row("disp_konsernbidrag_mottatt", "Mottatt konsernbidrag", "disponering", 6113, 10288),
        _row("disp_konsernbidrag_avgitt", "Avgitt konsernbidrag", "disponering", 36636, 36637),
        _row("disp_udekket_tap", "Udekket tap", "disponering", 185, 7070),
        _row(
            "disp_annen_egenkapital",
            "Overføring til/fra annen egenkapital",
            "disponering",
            7065,
            7066,
        ),
        _row(
            "sum_overfoeringer",
            "Sum overføringer og disponeringer",
            "disponering",
            7071,
            7072,
            kind="sum",
        ),
    ]
    disposition_lines = tuple(
        r for r in raw_disp if _keep(r, always=r.key == "disp_annen_egenkapital")
    )

    raw_assets = [
        _row("utvikling", "Utvikling", "immaterielle", 7073, 7074, note_ref="4"),
        _row(
            "konsesjoner_rettigheter",
            "Konsesjoner, patenter, lisenser og rettigheter",
            "immaterielle",
            205,
            7075,
            note_ref="4",
        ),
        _row("utsatt_skattefordel", "Utsatt skattefordel", "immaterielle", 202, 7076),
        _row("goodwill", "Goodwill", "immaterielle", 206, 7077, note_ref="4"),
        _row(
            "sum_immaterielle",
            "Sum immaterielle eiendeler",
            "immaterielle",
            2400,
            8006,
            kind="sum",
        ),
        _row(
            "tomter_bygninger",
            "Tomter, bygninger og annen fast eiendom",
            "varige_driftsmidler",
            1976,
            8007,
            note_ref="4",
        ),
        _row(
            "maskiner_anlegg", "Maskiner og anlegg", "varige_driftsmidler", 1975, 8008, note_ref="4"
        ),
        _row("skip_rigg_fly", "Skip, rigger, fly og lignende", "varige_driftsmidler", 1348, 7084),
        _row(
            "driftslosore_inventar",
            "Driftsløsøre, inventar, verktøy og kontormaskiner",
            "varige_driftsmidler",
            7725,
            8009,
            note_ref="4",
        ),
        _row(
            "sum_varige_driftsmidler",
            "Sum varige driftsmidler",
            "varige_driftsmidler",
            47,
            8010,
            kind="sum",
        ),
        _row(
            "inv_datterselskap",
            "Investering i datterselskap",
            "finansielle_anleggsmidler",
            9686,
            10289,
        ),
        _row(
            "inv_samme_konsern",
            "Investering i annet foretak i samme konsern",
            "finansielle_anleggsmidler",
            7727,
            8012,
        ),
        _row(
            "laan_samme_konsern",
            "Lån til foretak i samme konsern",
            "finansielle_anleggsmidler",
            6500,
            7093,
        ),
        _row(
            "inv_tilknyttet",
            "Investeringer i tilknyttet selskap",
            "finansielle_anleggsmidler",
            7726,
            8013,
        ),
        _row(
            "laan_tilknyttet", "Lån til tilknyttet selskap", "finansielle_anleggsmidler", 7098, 7099
        ),
        _row(
            "inv_aksjer_andeler",
            "Investeringer i aksjer og andeler",
            "finansielle_anleggsmidler",
            7100,
            7101,
        ),
        _row("obligasjoner", "Obligasjoner", "finansielle_anleggsmidler", 27583, 27584),
        _row(
            "andre_langsiktige_fordringer",
            "Andre langsiktige fordringer",
            "finansielle_anleggsmidler",
            203,
            27585,
        ),
        _row(
            "sum_finansielle_anleggsmidler",
            "Sum finansielle anleggsmidler",
            "finansielle_anleggsmidler",
            5267,
            8014,
            kind="sum",
        ),
        _row("sum_anleggsmidler", "Sum anleggsmidler", "anleggsmidler", 217, 7108, kind="sum"),
        _row("varer", "Varer", "omlopsmidler", 326, 797),
        _row("kundefordringer", "Kundefordringer", "omlopsmidler", 116, 6921),
        _row("andre_kortsiktige_fordringer", "Andre fordringer", "omlopsmidler", 282, 7112),
        _row("konsernfordringer", "Konsernfordringer", "omlopsmidler", 7128, 6946),
        _row("sum_fordringer", "Sum fordringer", "omlopsmidler", 80, 8015, kind="sum"),
        _row("markedsbaserte_aksjer", "Markedsbaserte aksjer", "omlopsmidler", 7117, 7118),
        _row(
            "andre_fin_instrumenter", "Andre finansielle instrumenter", "omlopsmidler", 6429, 7123
        ),
        _row("sum_investeringer", "Sum investeringer", "omlopsmidler", 6601, 8018, kind="sum"),
        _row(
            "bankinnskudd_kontanter",
            "Bankinnskudd, kontanter og lignende",
            "omlopsmidler",
            786,
            8019,
            note_ref="7",
        ),
        _row("sum_omlopsmidler", "Sum omløpsmidler", "omlopsmidler", 194, 7126, kind="sum"),
        _row("sum_eiendeler", "SUM EIENDELER", "eiendeler", 219, 7127, kind="sum"),
    ]
    always_assets = {"kundefordringer", "bankinnskudd_kontanter"}
    hide_zero_subtotals = {
        "sum_immaterielle",
        "sum_varige_driftsmidler",
        "sum_finansielle_anleggsmidler",
        "sum_investeringer",
    }
    balance_asset_lines = tuple(
        r
        for r in raw_assets
        if (
            _keep(r, always=r.key in always_assets)
            and (
                r.key not in hide_zero_subtotals
                or r.current != 0
                or (r.prior is not None and r.prior != 0)
            )
        )
    )

    raw_eq_liab = [
        _row("selskapskapital", "Aksjekapital", "innskutt_egenkapital", 20488, 20489, note_ref="3"),
        _row("egne_aksjer", "Beholdning av egne aksjer", "innskutt_egenkapital", 7131, 7132),
        _row("overkurs", "Overkurs", "innskutt_egenkapital", 2585, 7135),
        _row(
            "annen_innskutt_egenkapital",
            "Annen innskutt egenkapital",
            "innskutt_egenkapital",
            17110,
            17111,
        ),
        _row(
            "sum_innskutt_egenkapital",
            "Sum innskutt egenkapital",
            "innskutt_egenkapital",
            3730,
            9984,
            kind="sum",
        ),
        _row("fond", "Fond", "opptjent_egenkapital", 27579, 27580),
        _row("avsatt_utbytte", "Avsatt utbytte", "opptjent_egenkapital", 23857, 23858),
        _row(
            "annen_egenkapital",
            "Annen egenkapital",
            "opptjent_egenkapital",
            3274,
            7140,
            note_ref="6",
        ),
        _row(
            "udekket_tap", "Udekket tap (fratrekk)", "opptjent_egenkapital", 249, 7141, note_ref="6"
        ),
        _row(
            "sum_opptjent_egenkapital",
            "Sum opptjent egenkapital",
            "opptjent_egenkapital",
            9702,
            9985,
            kind="sum",
        ),
        _row(
            "sum_egenkapital", "Sum egenkapital", "egenkapital", 250, 7142, kind="sum", note_ref="6"
        ),
        _row("pensjonsforpliktelser", "Pensjonsforpliktelser", "langsiktig_gjeld", 238, 6685),
        _row("utsatt_skatt", "Utsatt skatt", "langsiktig_gjeld", 237, 7143),
        _row(
            "andre_avsetninger",
            "Andre avsetninger for forpliktelser",
            "langsiktig_gjeld",
            7157,
            7146,
        ),
        _row(
            "konvertible_laan_lang", "Konvertible lån (langsiktig)", "langsiktig_gjeld", 7147, 7148
        ),
        _row("obligasjonslaan", "Obligasjonslån", "langsiktig_gjeld", 6091, 7149),
        _row(
            "gjeld_kredittinst_lang",
            "Gjeld til kredittinstitusjoner (langsiktig)",
            "langsiktig_gjeld",
            7150,
            7151,
        ),
        _row("langsiktig_konserngjeld", "Langsiktig konserngjeld", "langsiktig_gjeld", 2256, 7152),
        _row("ansvarlig_laanekapital", "Ansvarlig lånekapital", "langsiktig_gjeld", 243, 9989),
        _row("ovrig_langsiktig_gjeld", "Øvrig langsiktig gjeld", "langsiktig_gjeld", 242, 7155),
        _row(
            "sum_langsiktig_gjeld",
            "Sum langsiktig gjeld",
            "langsiktig_gjeld",
            86,
            7156,
            kind="sum",
        ),
        _row(
            "gjeld_kredittinst_kort",
            "Gjeld til kredittinstitusjoner (kortsiktig)",
            "kortsiktig_gjeld",
            10926,
            13203,
        ),
        _row("leverandorgjeld", "Leverandørgjeld", "kortsiktig_gjeld", 220, 7162),
        _row("betalbar_skatt", "Betalbar skatt", "kortsiktig_gjeld", 2483, 10293, note_ref="5"),
        _row(
            "skyldige_offentlige_avgifter",
            "Skyldige offentlige avgifter",
            "kortsiktig_gjeld",
            225,
            7170,
        ),
        _row("utbytte_gjeld", "Utbytte", "kortsiktig_gjeld", 235, 7171),
        _row("kortsiktig_konserngjeld", "Kortsiktig konserngjeld", "kortsiktig_gjeld", 2255, 7175),
        _row("annen_kortsiktig_gjeld", "Annen kortsiktig gjeld", "kortsiktig_gjeld", 236, 7182),
        _row(
            "sum_kortsiktig_gjeld",
            "Sum kortsiktig gjeld",
            "kortsiktig_gjeld",
            85,
            7183,
            kind="sum",
        ),
        _row("sum_gjeld", "Sum gjeld", "gjeld", 1119, 7184, kind="sum"),
        _row(
            "sum_egenkapital_og_gjeld",
            "SUM EGENKAPITAL OG GJELD",
            "egenkapital_og_gjeld",
            251,
            7185,
            kind="sum",
        ),
    ]
    always_eq_liab = {"selskapskapital", "annen_egenkapital", "leverandorgjeld"}
    balance_equity_liability_lines = tuple(
        r for r in raw_eq_liab if _keep(r, always=r.key in always_eq_liab)
    )

    off_balance_lines = tuple(
        r
        for r in (
            _row(
                "garantistillelser",
                "Garantistillelser",
                "poster_utenom",
                16920,
                16921,
                note_ref="7",
            ),
            _row("pantstillelser", "Pantstillelser", "poster_utenom", 16922, 16923, note_ref="7"),
        )
        if _keep(r)
    )

    # --- Compliance checks (feil = blocks filing, merknad = advisory) ---
    findings: list[Finding] = []
    if form not in SUPPORTED_FORMS:
        findings.append(
            feil(
                "organisasjonsform_ikke_stottet",
                f"Myntos sender årsregnskap for AS og ASA. Selskapet er registrert som "
                f"{form or 'ukjent organisasjonsform'}.",
            )
        )
    if not year_closed:
        findings.append(
            feil(
                "aar_ikke_avsluttet",
                f"Regnskapsåret {year} er ikke avsluttet ennå. Fullfør årsavslutningen på "
                "Årsoppgjør-siden før årsregnskapet sendes til Regnskapsregisteret.",
            )
        )
    if today is not None and end_date > today:
        findings.append(
            feil(
                "periode_ikke_over",
                f"Regnskapsperioden slutter {end_date.strftime('%d.%m.%Y')}, som er fram i tid. "
                "Brønnøysundregistrene godtar ikke sluttdato fram i tid.",
            )
        )

    balance_diff = curr["sum_eiendeler"] - curr["sum_egenkapital_og_gjeld"]
    if balance_diff != 0:
        findings.append(
            feil(
                "balanse_stemmer_ikke",
                f"Sum eiendeler ({curr['sum_eiendeler']} kr) stemmer ikke med sum egenkapital "
                f"og gjeld ({curr['sum_egenkapital_og_gjeld']} kr). Differanse: {balance_diff} kr.",
            )
        )
    if (
        curr["sum_eiendeler"] == 0
        and curr["sum_egenkapital_og_gjeld"] == 0
        and curr["sum_driftsinntekter"] == 0
    ):
        findings.append(
            merknad(
                "tomt_regnskap",
                f"Regnskapet for {year} har ingen bokførte tall ennå.",
            )
        )

    if draft.approval_date is None:
        findings.append(
            feil(
                "mangler_fastsettelsedato",
                "Oppgi dato for fastsettelse av årsregnskapet (generalforsamlingsdato) før "
                "innsending til Regnskapsregisteret.",
            )
        )
    elif draft.approval_date < end_date:
        findings.append(
            feil(
                "fastsettelsedato_for_tidlig",
                f"Fastsettelsesdato ({draft.approval_date.strftime('%d.%m.%Y')}) kan ikke være "
                f"før regnskapsårets slutt ({end_date.strftime('%d.%m.%Y')}).",
            )
        )

    rep = (draft.confirming_representative or "").strip()
    if len(rep) < 3:
        findings.append(
            feil(
                "mangler_bekreftende_representant",
                "Oppgi fullt navn på representant (minst 3 tegn) som bekrefter at "
                "årsregnskapet er fastsatt.",
            )
        )

    if resolved_notes.antall_aarsverk < ZERO:
        findings.append(
            feil(
                "negativt_aarsverk",
                "Antall årsverk kan ikke være et negativt tall.",
            )
        )
    if (
        resolved_notes.usikkerhet_fortsatt_drift
        and not resolved_notes.usikkerhet_fortsatt_drift_tekst
    ):
        findings.append(
            feil(
                "mangler_fortsatt_drift_beskrivelse",
                "Når det er oppgitt usikkerhet om fortsatt drift, må begrunnelsen beskrives i "
                "noten for regnskapsprinsipper.",
            )
        )

    if form == "AS" and curr["selskapskapital"] < MIN_SHARE_CAPITAL_AS:
        findings.append(
            merknad(
                "lav_aksjekapital",
                f"Aksjekapital i balansen er {curr['selskapskapital']} kr. Et AS har lovpålagt "
                "minstekrav på 30 000 kr i aksjekapital (aksjeloven § 3-1).",
            )
        )
    elif form == "ASA" and curr["selskapskapital"] < MIN_SHARE_CAPITAL_ASA:
        findings.append(
            merknad(
                "lav_aksjekapital",
                f"Aksjekapital i balansen er {curr['selskapskapital']} kr. Et ASA har lovpålagt "
                "minstekrav på 1 000 000 kr i aksjekapital.",
            )
        )

    if draft.auditor_elected_out and (
        curr["sum_driftsinntekter"] >= AUDIT_THRESHOLD_REVENUE
        or curr["sum_eiendeler"] >= AUDIT_THRESHOLD_ASSETS
        or resolved_notes.antall_aarsverk > AUDIT_THRESHOLD_FTE
    ):
        findings.append(
            merknad(
                "revisjonsplikt_terskel",
                "Selskapet passerer en eller flere terskelverdier for fravalg av revisjon "
                "(7 mill. kr i driftsinntekter, 27 mill. kr i balansesum eller over 10 årsverk). "
                "Kontroller om revisjonsberetning må legges ved.",
            )
        )

    if not draft.auditor_elected_out:
        findings.append(
            merknad(
                "revisjonsberetning_kreves",
                "Når årsregnskapet skal revideres, må revisjonsberetning fra revisor legges ved "
                "innsendingen i Altinn før signering.",
            )
        )

    if not has_prior:
        findings.append(
            merknad(
                "mangler_sammenligningstall",
                f"Det finnes ingen tall for {year - 1} i Myntos. Hvis selskapet eksisterte i "
                f"{year - 1}, bør du fylle inn sammenligningstall (fjorårstall) manuelt før "
                "innsending (regnskapsloven § 6-6).",
            )
        )

    if curr["sum_egenkapital"] <= 0 and curr["sum_eiendeler"] > 0:
        findings.append(
            merknad(
                "negativ_egenkapital",
                "Sum egenkapital er null eller negativ. Husk styrets handleplikt etter "
                "aksjeloven § 3-5 og vurder om det er usikkerhet om fortsatt drift.",
            )
        )

    # Deterministic content hash over everything sent in RR-0002 + RR-0002-U.
    hash_payload = {
        "orgnr": company.orgnr,
        "year": year,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "current": curr,
        "prior": prior_values,
        "small_company": draft.small_company,
        "is_parent": draft.is_parent,
        "consolidated": draft.consolidated,
        "winding_up_accounts": draft.winding_up_accounts,
        "auditor_elected_out": draft.auditor_elected_out,
        "prepared_by_external_accountant": draft.prepared_by_external_accountant,
        "external_accountant_assistance": draft.external_accountant_assistance,
        "approval_date": draft.approval_date.isoformat() if draft.approval_date else None,
        "confirming_representative": rep,
        "contact_email": (draft.contact_email or "").strip(),
        "notes": {
            "regnskapsprinsipper": resolved_notes.regnskapsprinsipper,
            "usikkerhet_fortsatt_drift": resolved_notes.usikkerhet_fortsatt_drift,
            "usikkerhet_fortsatt_drift_tekst": resolved_notes.usikkerhet_fortsatt_drift_tekst,
            "antall_aarsverk": str(resolved_notes.antall_aarsverk),
            "lonnskostnad_fritekst": resolved_notes.lonnskostnad_fritekst,
            "aksjekapital_tekst": resolved_notes.aksjekapital_tekst,
            "anleggsmidler_fritekst": resolved_notes.anleggsmidler_fritekst,
            "bundne_bankinnskudd": resolved_notes.bundne_bankinnskudd,
            "gjeld_over_fem_aar": resolved_notes.gjeld_over_fem_aar,
            "pantstillelser_gjeld": resolved_notes.pantstillelser_gjeld,
            "pantsatte_eiendeler_verdi": resolved_notes.pantsatte_eiendeler_verdi,
            "garantiforpliktelser": resolved_notes.garantiforpliktelser,
            "gjeld_fritekst": resolved_notes.gjeld_fritekst,
            "laan_til_styret": resolved_notes.laan_til_styret,
            "laan_til_daglig_leder": resolved_notes.laan_til_daglig_leder,
            "ytterligere_opplysninger": resolved_notes.ytterligere_opplysninger,
            "egendefinerte_noter": list(resolved_notes.egendefinerte_noter),
        },
    }
    content_hash = hashlib.sha256(
        json.dumps(hash_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    return ComputedAarsregnskap(
        year=year,
        start_date=start_date,
        end_date=end_date,
        year_closed=year_closed,
        organisation_form=form,
        has_prior_year_in_books=has_prior_books,
        uses_prior_year_overrides=uses_overrides,
        result_lines=result_lines,
        disposition_lines=disposition_lines,
        balance_asset_lines=balance_asset_lines,
        balance_equity_liability_lines=balance_equity_liability_lines,
        off_balance_lines=off_balance_lines,
        current_values=curr,
        prior_values=prior_values,
        driftsinntekter=curr["sum_driftsinntekter"],
        driftsresultat=curr["driftsresultat"],
        resultat_for_skatt=curr["resultat_for_skatt"],
        skattekostnad=curr["skattekostnad"],
        aarsresultat=curr["aarsresultat"],
        sum_anleggsmidler=curr["sum_anleggsmidler"],
        sum_omlopsmidler=curr["sum_omlopsmidler"],
        sum_eiendeler=curr["sum_eiendeler"],
        sum_innskutt_egenkapital=curr["sum_innskutt_egenkapital"],
        sum_opptjent_egenkapital=curr["sum_opptjent_egenkapital"],
        sum_egenkapital=curr["sum_egenkapital"],
        sum_langsiktig_gjeld=curr["sum_langsiktig_gjeld"],
        sum_kortsiktig_gjeld=curr["sum_kortsiktig_gjeld"],
        sum_gjeld=curr["sum_gjeld"],
        sum_egenkapital_og_gjeld=curr["sum_egenkapital_og_gjeld"],
        balance_diff=balance_diff,
        rounding_adjustment=rounding_adj,
        notes=resolved_notes,
        draft=draft,
        findings=tuple(findings),
        content_hash=content_hash,
    )
