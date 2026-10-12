"""What an invoice line may say: the sellable VAT codes and the units.

Pure data, validated at import against the seeded kontoplan — the same guard
:mod:`myntos.domain.ledger.cashflow` applies, and for the same reason: a typo in
an account number here would otherwise surface as a 500 the first time somebody
picks that particular VAT code, long after the deploy that introduced it.

The VAT code decides two things at once, which is why they live in one row:

1. **Which income account** the revenue lands on, and through that account which
   næringsspesifikasjon post it reaches. 25 % goes to 3000, 15 % to 3010, and
   omsetning outside the VAT act to 3100 — the split exists because the
   skattemelding wants it, not because the accounts look tidy.
2. **How the line is presented** on the document and in the e-invoice
   (:mod:`myntos.domain.faktura.vat`).

Units are UN/ECE Recommendation 20 codes, which EN 16931 requires. The list is
short on purpose: it covers what a Norwegian small business actually invoices,
and a longer list would mostly offer people new ways to pick the wrong one.
"""

from __future__ import annotations

from dataclasses import dataclass

from myntos.domain.ledger.chart import ACCOUNTS_BY_NUMBER, TAX_CODES_BY_CODE


@dataclass(frozen=True)
class SalesTaxOption:
    """One VAT treatment a line may be sold under."""

    tax_code: str
    label: str
    income_account: str
    note: str


SALES_TAX_OPTIONS: tuple[SalesTaxOption, ...] = (
    SalesTaxOption(
        tax_code="3",
        label="25 % (alminnelig sats)",
        income_account="3000",
        note="Vanlig avgiftspliktig salg av varer og tjenester.",
    ),
    SalesTaxOption(
        tax_code="31",
        label="15 % (næringsmidler)",
        income_account="3010",
        note="Mat og drikke, og vann og avløp fra 1. juli 2025.",
    ),
    SalesTaxOption(
        tax_code="33",
        label="12 % (persontransport, overnatting)",
        income_account="3020",
        note="Persontransport, overnatting, kino, museer og idrettsarrangement.",
    ),
    SalesTaxOption(
        tax_code="5",
        label="0 % (avgiftsfritt salg)",
        income_account="3100",
        note=(
            "Fritatt etter merverdiavgiftsloven kapittel 6, for eksempel bøker og "
            "aviser. Du har fortsatt fradragsrett for inngående mva."
        ),
    ),
    SalesTaxOption(
        tax_code="6",
        label="Unntatt merverdiavgift",
        income_account="3100",
        note=(
            "Utenfor merverdiavgiftsloven: helsetjenester, undervisning, "
            "finansielle tjenester. Kan ikke stå på samme faktura som "
            "avgiftspliktig salg."
        ),
    ),
    SalesTaxOption(
        tax_code="52",
        label="Utførsel til utlandet",
        income_account="3120",
        note="Avgiftsfri utførsel av varer og tjenester. Skal med i mva-meldingen.",
    ),
    SalesTaxOption(
        tax_code="0",
        label="Ikke mva-registrert",
        income_account="3100",
        note=(
            "For virksomhet under registreringsgrensen. Fakturaen viser da ingen "
            "merverdiavgift, og det står hvorfor."
        ),
    ),
)

SALES_TAX_OPTIONS_BY_CODE: dict[str, SalesTaxOption] = {
    option.tax_code: option for option in SALES_TAX_OPTIONS
}


@dataclass(frozen=True)
class Unit:
    """A UN/ECE Rec 20 unit code and what to call it in Norwegian."""

    code: str
    label: str


UNITS: tuple[Unit, ...] = (
    Unit("EA", "stk"),
    Unit("HUR", "time"),
    Unit("DAY", "dag"),
    Unit("WEE", "uke"),
    Unit("MON", "måned"),
    Unit("ANN", "år"),
    Unit("KGM", "kg"),
    Unit("TNE", "tonn"),
    Unit("LTR", "liter"),
    Unit("MTR", "meter"),
    Unit("MTK", "kvadratmeter"),
    Unit("MTQ", "kubikkmeter"),
    Unit("KMT", "kilometer"),
    Unit("SET", "sett"),
    Unit("PR", "par"),
)

UNIT_CODES: frozenset[str] = frozenset(unit.code for unit in UNITS)


def income_account_for(tax_code: str) -> str:
    """Which income account a line with ``tax_code`` posts to."""
    option = SALES_TAX_OPTIONS_BY_CODE.get(tax_code)
    if option is None:
        raise KeyError(tax_code)
    return option.income_account


def _validate_catalogue() -> None:
    """Fail at import if an option names an account or VAT code we never seed."""
    for option in SALES_TAX_OPTIONS:
        if option.income_account not in ACCOUNTS_BY_NUMBER:
            raise AssertionError(
                f"{option.tax_code}: konto {option.income_account} finnes ikke i chart"
            )
        if option.tax_code not in TAX_CODES_BY_CODE:
            raise AssertionError(f"mva-koden {option.tax_code} finnes ikke i chart")


_validate_catalogue()
