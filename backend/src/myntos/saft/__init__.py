"""SAF-T Financial export.

Generates the SAF-T Financial XML that bookkeeping-obligated entities must be
able to hand to Skatteetaten on demand (bokføringsforskriften). The export
must validate against Skatteetaten's official XSD. Because the ledger is
SAF-T-aligned by design, this package is a serializer, not a converter.
See docs/integrations/saf-t.md.
"""
