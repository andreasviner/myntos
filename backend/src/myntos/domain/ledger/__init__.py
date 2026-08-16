"""Double-entry ledger (hovedbok).

Design constraints (driving the schema, see docs/ARCHITECTURE.md):

- SAF-T-aligned from day one: every account carries a per-financial-year
  GroupingCategory/GroupingCode mapping (næringsspesifikasjon post numbers —
  the pre-2025 standard-kontoplan mapping is obsolete), and every VAT
  treatment maps to a standard tax code, so the SAF-T export is a projection
  of the ledger rather than a translation layer. See docs/integrations/saf-t.md.
- Posted journal entries are immutable — corrections are reversals, never
  edits or deletions (bokføringsloven sporbarhetskrav).
- Amounts are exact decimals; floating point never touches money.

The schema lives in :mod:`myntos.domain.ledger.models` and the rules that
enforce the above in :mod:`myntos.domain.ledger.posting`.
"""

from myntos.domain.ledger.models import (
    GROUPING_NOT_APPLICABLE,
    NON_REPORTABLE_STANDARD_TAX_CODES,
    Account,
    AccountGrouping,
    AccountOpeningBalance,
    AccountType,
    Company,
    DimensionType,
    DimensionValue,
    FinancialYear,
    Journal,
    Party,
    PartyOpeningBalance,
    PartyType,
    TaxCode,
    TaxRate,
    Transaction,
    TransactionLine,
    TransactionLineAnalysis,
)
from myntos.domain.ledger.posting import (
    AmbiguousTaxRateError,
    ImmutablePostingError,
    PostingError,
    TaxRateNotFoundError,
    UnbalancedEntryError,
    apply_tax,
    assert_mutable,
    build_reversal,
    compute_tax,
    derive_closing_balance,
    post,
    quantize_money,
    rate_on,
    totals,
    validate,
)

__all__ = [
    "GROUPING_NOT_APPLICABLE",
    "NON_REPORTABLE_STANDARD_TAX_CODES",
    "Account",
    "AccountGrouping",
    "AccountOpeningBalance",
    "AccountType",
    "AmbiguousTaxRateError",
    "Company",
    "DimensionType",
    "DimensionValue",
    "FinancialYear",
    "ImmutablePostingError",
    "Journal",
    "Party",
    "PartyOpeningBalance",
    "PartyType",
    "PostingError",
    "TaxCode",
    "TaxRate",
    "TaxRateNotFoundError",
    "Transaction",
    "TransactionLine",
    "TransactionLineAnalysis",
    "UnbalancedEntryError",
    "apply_tax",
    "assert_mutable",
    "build_reversal",
    "compute_tax",
    "derive_closing_balance",
    "post",
    "quantize_money",
    "rate_on",
    "totals",
    "validate",
]
