# SAF-T Financial

Researched and verified against official sources (Lovdata, Skatteetaten,
the official GitHub repo) 2026-07-18. Official repo:
`github.com/Skatteetaten/saf-t` — XSDs (`SAF-T_Financial_1.3` and `…_1.4`),
year-versioned code lists, example files (golden test fixtures),
Skatteetaten's XML validator.

## Versions — the exporter must be version-parameterized

| Version | Status |
| --- | --- |
| v1.30 | Valid for periods from 2025-01-01, usable **until 2026-12-31** |
| v1.40 | Published 2026-04-30, usable **now**, the **only** valid format for periods starting 2027-01-01 |

v1.40 changes: bigger monetary datatype (38 digits), multiple owner account
IDs, higher-precision currency/exchange-rate fields, optional
virtual-currency and country fields, and **NOK debit/credit tax amounts
required when the transaction currency isn't NOK** (VAT is fixed in NOK,
bokføringsforskriften § 4-2). Select the schema by financial year; keep both.
v2.0 is in active design (SourceDocuments — invoices/payments — targeted
around 2027): keep bilag metadata structured (parties, amounts, dates,
invoice numbers), not just PDF blobs.

## Who is in scope, and when files are sent

Legal chain: bokføringsforskriften **§ 7-8** (standardized export, format set
by Skattedirektoratet) → bokføringsloven **§ 13b** (electronic availability,
3.5 years). The < 5 MNOK exemption lives in bokføringsforskriften
**§ 7-7 tredje ledd**; the 600-bilag rule is a separate provision about
spreadsheet bookkeeping. None of it matters for us: **anyone whose records
are electronically available is in scope** (§ 7-8 second sentence re-captures
them) — i.e. *every* Myntos user. Never market the exemptions.

There is **no periodic SAF-T filing**: the file is produced **on demand at an
audit**, uploaded via Altinn form **RF-1363**. Limits: 200 MB/attachment,
2 GB per XML file, max 10 attachments, one financial year per file, naming
`SAF-T Financial_<orgnr>_<yyyymmddhhmiss>_<n>_<total>.xml`, ZIP recommended.

## Account mapping — the old standard kontoplan is DEAD

Since v1.30 (periods from 2025-01-01), the 2-/4-digit `StandardAccountID`
mapping is gone. Each GL account instead carries a mandatory
**`GroupingCategory` + `GroupingCode`** — næringsspesifikasjon
income-statement post numbers (e.g. `salgsinntekt`/3000), `NA` for
unmappable interim accounts. The code lists are **year-versioned** (repo
folders "Grouping Category Code 2024", "… 2025-2026") and the mapping is
**per financial year with history retained** — a single static column on the
account table is insufficient. Ignore older articles (and the repo's legacy
standard-accounts folder) describing the pre-2025 regime.

## VAT

Internal VAT codes are free-form, but the TaxTable must map every internal
code to a Norwegian **`StandardTaxCode`** (mandatory since v1.30) — the same
code dimension that feeds the MVA-melding. Classic exporter bug:
`TaxInformation` belongs on the **VAT basis line**, not the VAT-account line
(exception: reverse-charge codes 14/15). `DebitTaxAmount`/`CreditTaxAmount`
are mandatory whenever TaxInformation is present (`0.00` allowed).

## What the database must capture (drives the Phase 1 schema)

- **Header:** company orgnr/name/address/contact, default currency,
  SelectionCriteria (mandatory since v1.30), software info
- **MasterFiles:** GL accounts (ID, description, per-year grouping mapping,
  opening/closing debit/credit balances); customers & suppliers (IDs, orgnr,
  addresses, balance account + opening/closing balances —
  `BalanceAccountStructure`); TaxTable; analysis/dimension types
  (department, project, cost centre…); owners
- **GeneralLedgerEntries:** journal → transaction (ID, period, dates:
  TransactionDate / SystemEntryDate / GLPostingDate, voucher type) → line
  (RecordID, AccountID, Customer/SupplierID, **SourceDocumentID → FK to the
  bilag**, description, debit/credit amounts with currency, TaxInformation,
  repeatable Analysis allocations)
- Store amounts as **debit/credit pairs**, not signed values; keep NOK
  amounts alongside foreign-currency amounts (v1.40 requirement)

Two sharp edges: XSD-"optional" elements become **mandatory for us** for any
data our DB actually holds (official rule); and mandatory-but-missing values
use prescribed placeholders (`NA`/`NotAvailable`, `0001-01-01`, `00:00:00`,
`0.00`) — empty elements fail validation.

## Validation strategy

1. XSD validation in CI (vendored schemas)
2. **Own cross-reference validation** — Altinn only checks the XSD; broken
   AccountID/CustomerID/TaxCode references surface at audit otherwise
3. Skatteetaten's `Norwegian_SAF-T_XML_Validator` + their test-submission
   scheme for system suppliers

## Storage & retention (see also ARCHITECTURE.md)

Bokføringsloven § 13: primary material stored **5 years** after year-end
(secondary 3.5), readable and printable throughout; § 13b: electronic
availability 3.5 years. § 7-5 (since 2025-01-27): storage allowed in
EEA/UK/CH with written notification to skattekontoret; outside that,
individual dispensation. § 7-4: bookkeeping performed abroad must be
transferred to Norway within one month after the annual accounts are adopted,
at latest 7 months after year-end. Export/print access must survive
subscription termination — a natural fit for the public-source/self-host story.
