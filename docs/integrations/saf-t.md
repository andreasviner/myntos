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
availability 3.5 years. § 7-5 (since 2025-01-27, forskrift 27. januar 2025
nr. 79, in force at once; checked on Lovdata 2026-10-10): storage allowed in
EEA/UK/CH with written notification to skattekontoret; outside that,
individual dispensation. § 7-4: bookkeeping performed abroad must be
transferred to Norway within one month after the annual accounts are adopted,
at latest 7 months after year-end. Export/print access must survive
subscription termination, and the SAF-T file plus a full bilag export are how
that is met (the SAF-T file is built; the bilag export is not yet, ROADMAP
phase 3). Self-hosting is not the answer: the licence is proprietary (settled
2026-10-10), so a customer leaving keeps their data, not a copy of the
program.

## What Myntos does (built 2026-10-09)

`backend/src/myntos/saft/`, `/saft/` in the frontend, linked from the note under
the overview's figures (not the menu: the file is made when Skatteetaten asks,
perhaps never).

| Module | Does |
| --- | --- |
| `saft.service` | Reads one company's financial year into an `AuditFile` snapshot. Every query is scoped to the company; vouchers reach it through `Journal.company_id`, so that join is the wall |
| `saft.export` | Writes the snapshot as XML, pure, like `mva.melding`. Version chosen per file |
| `saft.check` | The cross-reference check Altinn does not do. `feil` blocks the download, `merknad` is shown |
| `saft.codes` | Skatteetaten's grouping list and standard tax code list, vendored verbatim in `saft/codes/` and read at runtime |

Routes: `GET /companies/{id}/saft/kontroll?aar=&versjon=` (findings, no file) and
`GET /companies/{id}/saft/fil?aar=&versjon=` (the file, named
`SAF-T Financial_<orgnr>_<yyyymmddhhmmss>_1_1.xml` in Oslo time, or a 422
`saft_not_exportable` carrying the blocking findings). Both need only
`ledger:read`: the person most likely to want the file is a revisor with read
access.

**Versions.** v1.30 by default up to and including 2026, v1.40 offered beside it;
from 2027 only v1.40. Years before 2025 are refused rather than written in a
format whose StandardAccountID regime Myntos never had data for. Every amount is
carried in NOK with any foreign amount alongside, so v1.40's NOK tax structure
never applies and the two files differ only in `AuditFileVersion` (a test pins
that).

**The grouping mapping was wrong until this was built.** The seeded chart had
been written from the list's documented shape without the list, and half its
pairs did not exist: `lonnskostnad` for `loennskostnad`, a post 4000 that is
4005, seven cost posts the list does not have, and `NA` on every balance-sheet
account although the list has a post for each. The XSD accepts all of that,
since it declares the category as a string. Corrected in
`domain/ledger/chart.py` and, for rows already seeded, by migration
`b7d40e2c9a15`, which rewrites only rows still holding the old seed's exact pair.
`tests/test_saft_export.py` now checks every seeded pair against the vendored
list, so this cannot drift back. Two choices in there are judgement, not lookup:
2060 Privatkonto maps to `egenkapital/2050` (the list has no drawings post;
drawings reduce equity), and 2710 input VAT maps to `kortsiktigGjeld/2740` like
2700, as Skatteetaten's own example file does.

**Opening balances come from the ledger, and the opening voucher is not a
transaction.** Nothing writes `AccountOpeningBalance`. A company that brought its
books from another system posts its inngående balanse as one voucher in the `IB`
journal (`domain/arsoppgjor/opening.py`, built 2026-10-09). For the year that
voucher is in, the export adds the `IB` journal's lines to
`OpeningDebitBalance`/`OpeningCreditBalance`, per account and per customer and
supplier (open items on 1500/2400 carry the party), and leaves the journal out
of `GeneralLedgerEntries`. Both at once would count the balance twice: the
file's closing balance has to be its opening plus its transactions, and that is
the first thing anyone reading the file checks. This is our reading of the
standard, not a sentence quoted from Skatteetaten's documentation; a `merknad`
(`inngaende_balanse_fra_bilag`) says in the file's check that it was done, so
nobody has to guess why voucher `IB 2025-0001` is missing from the entries. For
every later year the voucher is simply one of the earlier years' postings.

Profit and loss opens at zero. **Årsavslutning** (`domain/arsoppgjor/closing.py`)
posts the disposition in the `AVS` journal: 8960 against 2050 (and 8990 against
2080 for udekket tap) for an AS, 8800 against 2050 for an ENK, all grouped
`resultatDisponeringForSAF-T`/8800. That brings the year's income and cost
accounts to zero, so for a closed year the derivation finds nothing undisposed.
Where an earlier year in Myntos has *not* been closed, the result it carried is
still placed on 2050 so the opening balance sums to zero, with the
`udisponert_resultat` merknad saying so and pointing at Årsavslutning. The
disposition vouchers *are* transactions of the closed year and appear in its
`GeneralLedgerEntries`, which is what an auditor expects to find there.

**What the check blocks:** an account with no grouping for the year or a pair
not in the year's list; a line on an account, customer, supplier, tax code or
dimension the master files do not hold; an unbalanced voucher or ledger; an
opening balance that does not sum to zero; a duplicate voucher or line number;
an identifier longer than the schema allows (refused, never truncated: two ids
differing past the limit would merge). **What it notes:** a year with no
published grouping list (2027 today: the list is year-versioned, and checking
2027 against 2026's would pass a retired post silently), negative equity under
the positive-equity post, TaxInformation on a VAT account line, a period that
disagrees with the date, and the derived result above.

**Not exported yet:** `Owners` (Myntos does not store them), `Assets`,
`TaxDeclarationPeriod`, `ValueDate`. All optional in the schema, and none of them
is data the database holds, so the "optional becomes mandatory when you hold
it" rule does not bite. `Header/Company/Contact` is mandatory and names the
person who produced the file.

**Tests.** The XSDs and Skatteetaten's example files are in
`backend/tests/fixtures/saft/` (see its README). The examples are validated first
as a control; our output is validated against both schemas with `xmllint`, from a
hand-built snapshot (`test_saft_export.py`) and from real postings through the API
(`test_saft_api.py`), including a second year's derived opening balance and that
another company's vouchers never reach the file. `test_arsoppgjor_api.py` adds
the opening voucher arriving as opening balance (account and customer) and not
as an entry, and a closed year's file and the next year's, both validated
against both schemas. The examples are not compared
byte for byte: they are generated test data with fields Myntos does not hold, so
a diff would test the example and not the exporter.

**When Skatteetaten publishes the 2027 grouping list:** add the file to
`saft/codes/`, an entry to `codes.GROUPING_LISTS`, run
`test_every_seeded_account_is_grouped_under_a_published_post`, and if it fails,
correct the chart and write a migration for the 2027 rows. **Not one exactly
like `b7d40e2c9a15`:** that one inferred that a row holding the old seed's pair
was the seed's, because nothing let a person edit a grouping. Since the
kontoplan became editable that is false, and the new migration must also
require `account_grouping.chosen_at IS NULL`, which exists for exactly this.

## The kontoplan is editable (built 2026-10-09)

`domain/ledger/kontoplan.py`, `/kontoplan/`, routes under
`/companies/{id}/kontoplan` (reading needs `ledger:read`, every change
`ledger:chart`, held by owners and admins: see `domain/identity/access.py`).

- **Accounts** are four-digit NS 4102 numbers in classes 1-8; the type follows
  from the class and may only be one the class can hold. The number never
  changes. An account with postings, an opening balance or a party settling
  against it is never deleted, only deactivated, which hides it from new
  entries and nothing else. The accounts the code refers to by number (1900,
  1920, 1500, 2400, 2700, 2710, 2740, 2050) cannot be deactivated at all.
- **Groupings** are chosen from the published list per year
  (`codes.grouping_entries`), and a pair not in it is refused, so the export
  check keeps passing. A year with no list yet takes its choices from the
  nearest published one (`codes.latest_published_year`); the check still marks
  that year as unchecked.
- **A closed year's grouping is refused** (409 `closed_financial_year`). Its
  næringsspesifikasjon has been filed from those groupings, and a SAF-T file for
  the year that disagreed with the filing would have nothing in it to say why.
  Correcting a filed mapping starts with reopening the year, which is the
  explicit step. Myntos does not record the skattemelding itself, so "closed"
  stands in for "filed".
- **A new account** is grouped for every open year, by the person's choice or by
  `kontoplan.default_grouping` (the seed's pair; the list's post with the same
  number; the seeded neighbour in the same NS 4102 group; the nearest post
  below; never a petroleum, group or reinvoicing post). Closed years get
  nothing, and the export leaves an account created after a year's end, with no
  grouping and no balance in it, out of that year's file.
- **Who chose** is `chosen_at`/`chosen_by_user_id`. A chosen pair carries forward
  when the next year opens; an unchosen seeded account takes the seed's current
  pair, which is how a corrected seed reaches the next year.

The cash-flow register may book a category to another active account of the
**same type** (`account_number` on the entry), which is how an added account is
used; a type mismatch, a locked account or a deactivated one is refused.
