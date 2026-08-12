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
"""
