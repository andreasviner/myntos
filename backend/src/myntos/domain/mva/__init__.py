"""MVA-håndtering: VAT calculation and the MVA-melding.

VAT is computed per transaction line using the standard SAF-T mva codes (the
MVA-melding has been code-based since 2022, not post-number-based). The
MVA-melding for a period is then an aggregation over those codes. Rates and
code lists live in versioned reference data, since they change by law.
See docs/integrations/mva.md.

- :mod:`.codes`: what each SAF-T code means to the melding (sale, deduction,
  or reverse charge reported on both sides).
- :mod:`.periods`: skattleggingsperioder, their XSD names and deadlines.
- :mod:`.melding`: the aggregation and the XML, pure functions.
- :mod:`.service`: the same against the database, plus the settlement voucher.
- :mod:`.models`: what was validated and filed, and the receipts.
"""
