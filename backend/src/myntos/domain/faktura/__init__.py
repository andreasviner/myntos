"""Fakturering — issuing salgsdokumenter that meet the law.

Two regimes apply at once, and the package is laid out around them.

**bokføringsforskriften § 5-1** says what a sales document must contain: a
machine-assigned number, the dokumentasjonsdato, both parties identified,
ytelsens art og omfang, leveringstidspunkt and -sted, vederlag and forfallsdato,
merverdiavgift in NOK, and — where it applies — the omvendt-avgiftsplikt
marking. § 5-1-5 additionally requires taxable, zero-rated and exempt sales to be
shown and summed separately. § 5-1-3 requires the numbering to be complete enough
to check, and § 5-2-7 makes a kreditnota the only way to correct an issued
document. All of it is checked in :mod:`.compliance`, at issue and nowhere else.

**Lov 19. juni 2026 nr. 39** (pliktig digital bokføring og e-fakturering) makes
documentation of sales to another bokføringspliktig issuable only in an
electronic invoice format — EHF, Norway's profile of Peppol BIS Billing 3.0.
Sending becomes mandatory 1 January 2027, receiving 1 January 2030. :mod:`.ehf`
produces the document; the gap this system does **not** close is transmission,
which needs a Peppol access point (see docs/integrations/efaktura.md).

The modules, in dependency order:

- :mod:`.models` — the document, its lines, and the number series
- :mod:`.vat` — our VAT codes as EN 16931 category codes
- :mod:`.catalogue` — what a line may say: sellable codes, income accounts, units
- :mod:`.totals` — line arithmetic and the § 5-1-5 breakdown
- :mod:`.compliance` — the legal checks, as data rather than exceptions
- :mod:`.ehf` — UBL 2.1 / Peppol BIS Billing 3.0 XML
- :mod:`.pdf` — the readable copy, written without a PDF library
- :mod:`.service` — draft, issue, credit; the only module that touches the database
"""
