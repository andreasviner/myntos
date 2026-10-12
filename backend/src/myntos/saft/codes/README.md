# Skatteetaten's SAF-T code lists, copied verbatim

From <https://github.com/Skatteetaten/saf-t>, commit `05179521e435`, fetched
2026-10-09. Read at runtime by `myntos.saft.codes`, which is why they live in the
package and not under `tests/`.

- `naeringsspesifikasjon_2025_2026.csv`: `Grouping Category Code 2025-2026/CSV/
  naeringsspesifikasjon.csv`, the `GroupingCategory`/`GroupingCode` pairs an
  account may carry for financial years 2025 and 2026. UTF-8 with a BOM, `;`
  separated, as published.
- `standard_tax_codes.csv`: `Standard Tax Codes/CSV/Standard_Tax_Codes.csv`, the
  `StandardTaxCode` values a TaxTable may map to.

The lists are versioned by year. When Skatteetaten publishes the one for 2027, it
goes in beside this one under its own name and gets its own entry in
`codes.GROUPING_LISTS`; the 2025-2026 file stays, because a 2026 export must keep
being checked against the 2026 list.
