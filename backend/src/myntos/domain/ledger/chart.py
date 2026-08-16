"""The starter chart of accounts and VAT-code table for a new company.

Pure data. :mod:`myntos.domain.ledger.service` turns it into rows; nothing here
touches a database, so the whole table is inspectable in a test and in a REPL.

Two things are deliberately modest:

**The chart is a starting point, not a complete kontoplan.** It holds the
accounts the cash-flow register actually posts to (:mod:`.cashflow`) plus the
handful the double entry needs on the other side — bank, cash, the two VAT
accounts, equity. A business with payroll, inventory or fixed assets will add
accounts; the point of seeding is that a brand-new company can record its first
payment without first being asked to build a kontoplan.

**The næringsspesifikasjon mapping is provisional.** Since SAF-T v1.30 every
account carries a ``GroupingCategory``/``GroupingCode`` pair instead of the old
``StandardAccountID``, and the code lists are **year-versioned** by
Skatteetaten (``github.com/Skatteetaten/saf-t``). The pairs below follow the
documented shape (category name plus post number) and are wired through the
whole model, but they must be checked against the published list for the
financial year before any SAF-T export or næringsspesifikasjon goes out the
door. The mapping being *present and per-year* is the part that is expensive to
retrofit; the exact codes are cheap to correct.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from myntos.domain.ledger.models import (
    GROUPING_NOT_APPLICABLE,
    NON_REPORTABLE_STANDARD_TAX_CODES,
    AccountType,
)

# Accounts the posting logic refers to by name rather than by user choice.
BANK_ACCOUNT = "1920"
CASH_ACCOUNT = "1900"
OUTPUT_VAT_ACCOUNT = "2700"
INPUT_VAT_ACCOUNT = "2710"

# SAF-T BalanceAccountStructure: the GL account a party's sub-ledger settles
# against. Required on every customer and supplier, which is why the two are
# named here rather than chosen per party.
RECEIVABLES_ACCOUNT = "1500"
PAYABLES_ACCOUNT = "2400"

# The journal every cash-flow entry lands in. SAF-T wants entries grouped in
# journals; one is enough until there is more than one source of vouchers.
CASH_JOURNAL_ID = "KONTANT"
CASH_JOURNAL_DESCRIPTION = "Inn- og utbetalinger"
CASH_JOURNAL_TYPE = "bank"

# Issued sales documents land in their own journal rather than in the cash one.
# They are a different event: an invoice is revenue and a receivable on the day
# it is issued, while a payment is money moving, and the two are weeks apart. A
# shared journal would also make the bilagsnummer series interleave two sources.
SALES_JOURNAL_ID = "SALG"
SALES_JOURNAL_DESCRIPTION = "Utgående fakturaer"
SALES_JOURNAL_TYPE = "salg"

# VAT code meaning "no VAT treatment" — the default for wages, interest,
# insurance and everything else outside the VAT system. Excluded from the
# MVA-melding by NON_REPORTABLE_STANDARD_TAX_CODES.
NO_VAT_CODE = "0"


@dataclass(frozen=True)
class SeedAccount:
    """One general ledger account, with its per-year grouping mapping."""

    number: str
    description: str
    account_type: AccountType
    # Balance-sheet accounts have no næringsspesifikasjon income post; SAF-T
    # prescribes "NA" rather than an empty element.
    grouping_category: str = GROUPING_NOT_APPLICABLE
    grouping_code: str = GROUPING_NOT_APPLICABLE


@dataclass(frozen=True)
class SeedTaxCode:
    """One internal VAT code, its standard code, and the rate it starts at."""

    code: str
    description: str
    standard_tax_code: str
    rate: Decimal

    @property
    def is_reportable(self) -> bool:
        """False for the codes that must never reach the MVA-melding."""
        return self.standard_tax_code not in NON_REPORTABLE_STANDARD_TAX_CODES


# NS 4102-style numbering, trimmed to what the cash-flow register uses.
STANDARD_ACCOUNTS: tuple[SeedAccount, ...] = (
    # --- Balance sheet ---
    SeedAccount("1500", "Kundefordringer", AccountType.EIENDEL),
    SeedAccount("1900", "Kontanter", AccountType.EIENDEL),
    SeedAccount("1920", "Bankinnskudd", AccountType.EIENDEL),
    SeedAccount("2050", "Annen egenkapital", AccountType.EGENKAPITAL),
    SeedAccount("2060", "Privatkonto", AccountType.EGENKAPITAL),
    SeedAccount("2400", "Leverandørgjeld", AccountType.GJELD),
    SeedAccount("2700", "Utgående merverdiavgift", AccountType.GJELD),
    SeedAccount("2710", "Inngående merverdiavgift", AccountType.EIENDEL),
    # --- Income ---
    SeedAccount(
        "3000", "Salgsinntekt, avgiftspliktig høy sats", AccountType.INNTEKT, "salgsinntekt", "3000"
    ),
    SeedAccount(
        "3010",
        "Salgsinntekt, avgiftspliktig middels sats",
        AccountType.INNTEKT,
        "salgsinntekt",
        "3000",
    ),
    SeedAccount(
        "3020", "Salgsinntekt, avgiftspliktig lav sats", AccountType.INNTEKT, "salgsinntekt", "3000"
    ),
    SeedAccount(
        "3100",
        "Salgsinntekt, utenfor merverdiavgiftsloven",
        AccountType.INNTEKT,
        "salgsinntekt",
        "3100",
    ),
    SeedAccount(
        "3120", "Salgsinntekt, utførsel (avgiftsfri)", AccountType.INNTEKT, "salgsinntekt", "3100"
    ),
    SeedAccount("3900", "Annen driftsinntekt", AccountType.INNTEKT, "annenDriftsinntekt", "3900"),
    SeedAccount("8050", "Renteinntekt", AccountType.INNTEKT, "finansinntekt", "8050"),
    # --- Expenses ---
    SeedAccount("4000", "Varekjøp", AccountType.KOSTNAD, "varekostnad", "4000"),
    SeedAccount("5000", "Lønn til ansatte", AccountType.KOSTNAD, "lonnskostnad", "5000"),
    SeedAccount("5400", "Arbeidsgiveravgift", AccountType.KOSTNAD, "lonnskostnad", "5400"),
    SeedAccount("6300", "Leie av lokaler", AccountType.KOSTNAD, "annenDriftskostnad", "6300"),
    SeedAccount("6340", "Lys, varme og strøm", AccountType.KOSTNAD, "annenDriftskostnad", "6340"),
    SeedAccount("6500", "Verktøy og inventar", AccountType.KOSTNAD, "annenDriftskostnad", "6500"),
    SeedAccount(
        "6700",
        "Fremmed tjeneste (regnskap, revisjon, juridisk)",
        AccountType.KOSTNAD,
        "annenDriftskostnad",
        "6700",
    ),
    SeedAccount("6800", "Kontorrekvisita", AccountType.KOSTNAD, "annenDriftskostnad", "6800"),
    SeedAccount(
        "6810", "Datakostnad og programvare", AccountType.KOSTNAD, "annenDriftskostnad", "6800"
    ),
    SeedAccount("6900", "Telefon og internett", AccountType.KOSTNAD, "annenDriftskostnad", "6900"),
    SeedAccount(
        "7000", "Drivstoff og bilkostnad", AccountType.KOSTNAD, "annenDriftskostnad", "7000"
    ),
    SeedAccount("7140", "Reisekostnad", AccountType.KOSTNAD, "annenDriftskostnad", "7140"),
    SeedAccount(
        "7320", "Reklame og markedsføring", AccountType.KOSTNAD, "annenDriftskostnad", "7320"
    ),
    SeedAccount("7500", "Forsikringspremie", AccountType.KOSTNAD, "annenDriftskostnad", "7500"),
    SeedAccount("7770", "Bank- og kortgebyr", AccountType.KOSTNAD, "annenDriftskostnad", "7770"),
    SeedAccount("7790", "Annen driftskostnad", AccountType.KOSTNAD, "annenDriftskostnad", "7790"),
    SeedAccount("8150", "Rentekostnad", AccountType.KOSTNAD, "finanskostnad", "8150"),
)

ACCOUNTS_BY_NUMBER: dict[str, SeedAccount] = {a.number: a for a in STANDARD_ACCOUNTS}

# Internal code == standard code. Keeping them equal costs nothing and removes
# a translation step that would otherwise be wrong in exactly one place.
# Rates as of 2026 (docs/integrations/mva.md); they are seeded date-effective
# from the company's first financial year, never as constants.
STANDARD_TAX_CODES: tuple[SeedTaxCode, ...] = (
    SeedTaxCode("0", "Ingen merverdiavgiftsbehandling", "0", Decimal("0.000000")),
    SeedTaxCode("1", "Inngående mva, høy sats (25 %)", "1", Decimal("0.250000")),
    SeedTaxCode("11", "Inngående mva, middels sats (15 %)", "11", Decimal("0.150000")),
    SeedTaxCode("13", "Inngående mva, lav sats (12 %)", "13", Decimal("0.120000")),
    SeedTaxCode("3", "Utgående mva, høy sats (25 %)", "3", Decimal("0.250000")),
    SeedTaxCode("31", "Utgående mva, middels sats (15 %)", "31", Decimal("0.150000")),
    SeedTaxCode("32", "Utgående mva, lav sats (12 %)", "32", Decimal("0.120000")),
    SeedTaxCode("5", "Avgiftsfri omsetning (0 %)", "5", Decimal("0.000000")),
    SeedTaxCode("6", "Omsetning utenfor merverdiavgiftsloven", "6", Decimal("0.000000")),
    SeedTaxCode("52", "Utførsel av varer og tjenester (0 %)", "52", Decimal("0.000000")),
)

TAX_CODES_BY_CODE: dict[str, SeedTaxCode] = {t.code: t for t in STANDARD_TAX_CODES}
