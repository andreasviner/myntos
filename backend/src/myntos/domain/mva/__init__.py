"""MVA-håndtering — VAT calculation and the MVA-melding.

VAT is computed per transaction line using the standard SAF-T mva codes (the
MVA-melding has been code-based since 2022, not post-number-based). The
MVA-melding for a period is then an aggregation over those codes. Rates and
code lists live in versioned reference data, since they change by law.
See docs/integrations/mva.md.
"""
