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

**Manual vouchers** (`/manuelt-bilag/`, built 2026-10-15) are their own
journal, `JournalID` `MANUELT` and `Type` `manuelt` (the standard caps them at
18 and 9 characters), exported like every other journal with the bilagstekst
as the transaction's `Description`. The written reason each one carries is
not in the file, which has no field for it: it stays in Myntos beside the
voucher, as the documentation bokføringsforskriften § 5-12 asks for, and is
produced from there on request.

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

## Reading another program's file (built 2026-10-12)

The other direction: a company moving to Myntos brings the SAF-T file its old
program writes, and the **opening balance** is read from it. Built for the farm
pilot, whose Tripletex files were the first ones read (docs/landbruk.md), and
general: every rule below is something that file did.

`POST /companies/{id}/inngaende-balanse/saft` (`KEY_PREVIEW`, `ledger:write`)
takes the XML or the ZIP and answers with the review the saldobalanse upload
gives, plus `accounts_to_add` and `parties_to_add`. The page adds those through
the ordinary kontoplan and party routes and reads the file again; posting is
the existing `POST …/inngaende-balanse`. Nothing is stored, the file included.

| Module | Does |
| --- | --- |
| `saft.reader` | Pure. `read`: the header and the balances, and nothing after: parsing stops where `GeneralLedgerEntries` starts, so a year of vouchers costs nothing, and at most 64 MB is read before that. `read_entries`: the vouchers too, each `Transaction` built as a small tree, converted and dropped, at most 1 GB of XML and 300 000 lines; the file's own `NumberOfEntries` and `TotalDebit` are checked against what was read, so a download cut short is refused rather than read as a shorter year. Expat with every DTD refused, as `domain/bank/camt.py` does. A ZIP is opened (one XML, or the `_1_n` part; for the vouchers, a file in several parts is refused) |
| `saft.opening_import` | What the balances become in these books, with findings |
| `saft.voucher_import` | The year's vouchers as vouchers of the `IMPORT` journal: the review, and the post |
| `saft.compare` | The old program's year so far against Myntos's, per account, party and VAT term |

**What it refuses outright:** another company's file (`saft_wrong_company`,
by organisasjonsnummer; nothing of it is shown), and a file that is neither the
company's first year in Myntos (its opening balances are read) nor the whole
year before it (its closing balances, with the income statement summed into
the result). Both readings of the farm's files give the same voucher.

**The decisions, each with its reason in the module:**

- **Account ids: the first four digits.** Tripletex's farm module writes
  NIBIO's department and product after them (`145120512`); the rest goes into
  the line's text, and several ids on one account stay separate lines.
- **New accounts take the file's grouping only when it is usable:** in the
  year's list, of a category the account's class can hold, and not an IFRS
  post. Tripletex grouped a driftskreditt as «Positiv egenkapital» and an ENK's
  cash deposit on «Avsatt utbytte - IFRS» (account 2055, post 2055); both get
  Myntos's default by number, with a `merknad`. A pair that passes every rule
  and is still wrong (NIBIO's 118x installations on post 1180
  «Investeringseiendommer») is the accountant's to see, which is why every
  difference between the file's grouping and Myntos's is reported.
- **The undisposed result goes on 2050**, and only when the file proves that is
  what the gap is: a first-year file whose own movements balance, or a
  year-before file whose gap equals its income statement. Tripletex leaves the
  year's result off equity in the next year's opening, so a file read straight
  does not balance.
- **Open items per party** only when the file's sub-ledger adds up to the
  account exactly; parties are matched by organisasjonsnummer, then name, and
  new ones keep the old program's number when it is free.
- **VAT accounts** (2700, 2710, anything the file groups as «Skyldig
  merverdiavgift») go on 2740, as the review already requires.
- **The company's own bank account** found in an account's text on another
  account than 1920 is a `merknad`, not a move. The merknad asks the person to
  mark that account as a bank account on `/kontoplan/` (this route stores
  nothing, so it cannot mark it itself), or to move the line to 1920 if the
  accountant prefers. It goes away once the account is marked.

**Checked against the real file.** `regnaskaps_tests/saft_pilot.py`
(gitignored, beside the file) reads the farm's 2026 and 2025 files through the
API, adds what they ask for, posts, exports Myntos's own 2026 file, validates it
against the 1.40 XSD and compares every account's and party's opening balance
with Tripletex's: no differences, from either file. `tests/test_saft_import.py`
is the same flow on a synthetic file in Tripletex's shape.

**Not read:** the documents behind any voucher, which stay in the old
program's bilag archive. Earlier years' vouchers are read since 2026-10-13,
below.

### The year so far, and the parallel run (built 2026-10-12)

A company that moves in October needs January to September in Myntos, and one
that books in both programs for a while (the farm pilot's parallel run) needs
that and a way to see whether the two agree. Both read the same file, uploaded
with every call and kept by nobody (`api/v1/saft_import.py`):

| Route | Key | Does |
| --- | --- | --- |
| `POST …/saft/bilag/kontroll` | `KEY_PREVIEW`, `ledger:write` | What the import would post, per month, with `accounts_to_add`, `parties_to_add` and findings |
| `POST …/saft/bilag` | `key_post`, `ledger:write` | Posts it, all or nothing, into the `IMPORT` journal |
| `POST …/saft/sammenligning` | `KEY_READ`, `ledger:read` | The file's year so far against Myntos's books |

The page is `/tidligere-program/`.

**The import's decisions** (`saft.voucher_import`, each with its reason there):

- **The opening balance first, and nothing else in the period.** Any posted
  voucher outside `IB` and `IMPORT` between 1 January and `til` is
  `bilag_i_perioden`: the same trade would be booked twice. Once a parallel
  run has started, the import cannot run over it, which is the point; the
  comparison takes over.
- **One voucher per date.** Tripletex adds a payment to the invoice's voucher,
  each line dated by `ValueDate`. Split by date when every date balances on its
  own (the farm's 467 vouchers became 596, all balanced); taken whole on
  `GLPostingDate` otherwise.
- **The old program's numbers:** `2026-240`, and `2026-1-0109` for a later
  date. Unique in the journal, so a second import skips what is there.
  `SourceDocumentID` points at the old archive («Tripletex bilag 240-2026»).
- **Accounts** as the opening balance placed them: the first four digits, unless
  the standing `IB` voucher moved that file account (its lines' text starts with
  the file's id), as when the accountant has the driftskreditt put on 1920.
  270x goes on 2700 and 271x on 2710, because Myntos's MVA page and settlement
  read those two (Tripletex keeps 15 % input VAT on 2711).
- **VAT copied, not recomputed:** code (the file's `StandardTaxCode`), rate,
  basis and both amounts. `UTTAK-`, `TAP-`, `JUST-` and `TILB-` codes become
  the melding's spesifikasjon; `-K` compensation codes are refused.
- **The old program's settlements stay settlements.** A voucher of VAT accounts
  against 2740 with only øre beside it gets the settlement's voucher type
  (`mva_avregning`), and `ledger.service.not_a_settlement()` leaves it out of
  the year's VAT figures and the MVA page's reconciliation as it does Myntos's
  own. Without it every term the old program filed would reconcile to nothing.
- **Not in the register**, which lists what was registered in Myntos and cannot
  read another program's voucher back as a category; the register refuses to
  reverse one (`cannot_reverse_here`). It counts everywhere else.

**The comparison** (`saft.compare`) compares *movements* from 1 January to the
file's last voucher (or `til`), the opening balance left out of both sides, so
the decisions made there (VAT on 2740, the result on 2050, the bank on 1920) are
not differences. All VAT accounts are one row, because each program settles a
term into 2740 only when it files it; whether the VAT agrees is compared per
term and code instead. Parties are compared on 1500 and 2400 only, since the
register names the party on the cost line too. Each differing account says in
which months it parts.

**Checked against the real file.** `saft_pilot.py` takes the farm's 2026 file
through all of it: opening balance, 61 accounts and 62 parties added, 596
vouchers posted, a second read finding nothing new, the comparison finding no
difference in 93 accounts, 81 parties and 5 terms, each term's melding in Myntos
equal to the settlement Tripletex posted when it filed it (terms 1-4), Myntos's
own 2026 file valid against the 1.40 XSD, and every closing balance per account
and party equal to the opening plus Tripletex's movement.
`tests/test_saft_voucher_import.py` is the same on a synthetic file.

**Not done:** the PDFs (not in SAF-T). The terms the old program filed are
marked «Sendt fra et annet program» on the MVA page since 2026-10-15, proposed
from the imported `mva_avregning` vouchers and then refused a check and a
submission ([mva.md](mva.md) «Filed elsewhere»).

## The books brought over, on the server (built 2026-10-13)

The farm pilot's farmer uploaded his Tripletex files himself and wrote back
that the accounts had come over and he could not tell whether the vouchers had,
and could find no overview of the payments. Three things were wrong, and this
is what each became:

- **Earlier years were refused.** The vouchers could only be read for the
  company's first year in Myntos, and the files he had were the years before
  it as well. Now every file comes over: `saft.move` takes one file per year,
  the years in a row and only the last unfinished, posts the opening balance
  from the earliest file's opening balances and every year's vouchers, and
  opens the years before Myntos's first one to take them
  (`ledger.service.open_earlier_years`). There is no floor on how far back.
  The seeded VAT rates, which claim only the first year, move back only for a
  code whose every line in the files had today's rate: the low rate was 6 % for
  much of 2020-2021, and a rate nobody proved is not claimed. Another code in
  such a year has no rate, and a new entry with it there is refused
  (`tax_rate_not_found`, now a 422 and not a crash).
- **It was slow, and the page had to stay open.** The page uploaded the file to
  read it, added 120 accounts and parties one request at a time, uploaded it
  again to read it again, and a third time to post; the avdelinger a farm file
  needs had no button at all, so its import stopped at `dimensjoner_mangler`.
  Now the files are uploaded once (`saft_import`, `saft_import_file`) and read
  and posted on the server (`saft.jobs`, see ARCHITECTURE.md § Work done after
  the answer): accounts, parties and dimension values are added by the post
  itself, in the same transaction as the vouchers. The review is that posting,
  rolled back, so the two cannot disagree; the post runs it again on the books
  as they then are, and posts nothing if they changed (`review_changed`).
- **Imported vouchers were nowhere to be seen.** The register lists payments
  registered in Myntos, and the overview's «Siste bevegelser» is the register.
  `/bilag/` (`GET …/bilag`, `domain/ledger/vouchers.py`) lists every voucher of
  every journal, and narrowed to an account carries its balance: 1920 is the
  bank's ins and outs.

| Route | Key | Does |
| --- | --- | --- |
| `POST …/saft/innlesing` | `KEY_PREVIEW`, `ledger:write` (`ledger:read` with `sammenlign=true`) | Upload one file per year, or one to compare; 202, read in the background |
| `GET …/saft/innlesing`, `GET …/saft/innlesing/{import_id}` | `KEY_READ` | The imports, their progress, the review, the result |
| `POST …/saft/innlesing/{import_id}/foring` | `key_post`, `ledger:write` (+ `ledger:chart` when accounts are added) | Post it, all or nothing; 202 |
| `DELETE …/saft/innlesing/{import_id}` | `key_write` | Throw away one not posted |

The upload is a preview for a key: it changes nothing in the books, and holding
a 200 MB file for approval would not fit the approval's 30 MB anyway. The post
that waits for approval is a request with no body.

**The opening balance.** Posted from the earliest file when none stands; kept
when one stands for that year (the person may have checked it line by line);
**replaced** when one stands for a later year, reversed and posted anew in one
step, or that year's balance would count twice, once from the postings and
once from the voucher. An account the person had moved in the replaced one
(the driftskreditt onto 1920) is moved in the new one too, read off its lines'
text the way the vouchers already follow it.

**Year-end of an imported year.** A complete earlier year comes over open, and
`closing.plan_closing` takes the tax cost and disposition its file carries as
booked: Tripletex's own year-end vouchers stand, and Myntos disposes only what
is left (`avsluttet_i_tidligere_program`). They used to be refused as
`allerede_disponert`, which would have kept such a year, and every later one,
open for good.

**The bank where the files say it is** (2026-10-15). An account whose text
carries one of the files' own bank account numbers (the header's
`BankAccount`), other than 1920 and 1900, is marked as a bank account
(`account.is_bank_account`, migration `f3a9c6e2b814`) with the `merknad`
`bankkonto_merket`; without `ledger:chart` it is the `merknad`
`bankkonto_utenfor_1920` asking an administrator to do it. The farm's only bank
account is a driftskreditt Tripletex keeps on 2390, and the overview, which
counted money only on 1920 and 1900, read «Inn i år 0,00» for a year of bank
lines. Marking moves nothing (the vouchers stay where Tripletex put them, and
new payments still go to 1920) and is undone on `/kontoplan/`. A flag on the
ledger account rather than a link from `company_bank_account`, because that
table is where invoices are paid to and no import or key may add to it
(agents-and-api.md rule 5). Not yet changed: the bank reconciliation matches a
statement against vouchers on 1920 only, so a camt.053 for the driftskreditt
finds no candidates among the vouchers on 2390.

**The single-file routes stay** (`…/inngaende-balanse/saft`, `…/saft/bilag`,
`…/saft/sammenligning`): an agent may want an answer in the same call, and
`/api/v1` is additive. They still bring only the first year.

**Checked against:** `tests/test_saft_innlesing.py`, an earlier year with a
sale and Tripletex's year-end and the running year cut at February, brought
over in one go, closed, compared, and listed. Not yet run on the farm's real
files: `regnaskaps_tests/saft_pilot.py` drives the single-file routes and needs
a variant for the import.

## Dimensions: the file's analysis, both ways (built 2026-10-15)

A line's `Analysis` is a dimension in Myntos (`domain/ledger/dimensions.py`,
`TransactionLineAnalysis`): an avdeling, a prosjekt, a farm's driftsgren. The
`AnalysisType` is the dimension's code (Myntos's own are `AVDELING`,
`PROSJEKT`, `DRIFTSGR`, `PRODUKT`, nine characters at most, which is what
`SAFcodeType` allows) and the `AnalysisID` the value's.

**Out.** `saft.service` writes every dimension's values in the
`AnalysisTypeTable`, with `Status` `Active` or `Closed` (switched off, the
value or its whole dimension; after `AnalysisIDDescription`, where the
schema's sequence puts it, since Myntos keeps no `StartDate`), and each line's
allocation as `Analysis` with `DebitAnalysisAmount` or `CreditAnalysisAmount`
on the line's side, for the line's whole amount. Only the category's income or
cost line carries it, never the bank or VAT lines. Validated against both XSDs
in `tests/test_dimensions_api.py`.

**In.** `saft.reader` reads the `AnalysisTypeTable` and each line's
`Analysis`. `saft.voucher_import` keeps a line's analysis when the file's name
for the type is one Myntos knows (`driftsgrener.kind_of`: «Avdeling»,
«Prosjekt», «Driftsgren», «Produkt», English too), and leaves the rest with a
note (`dimensjoner_utelatt`): Tripletex's `B` «Bilagsart» repeats the voucher
type, and its `E` «Ansatte» names employees, which a second program has no
business copying. A value the company lacks blocks the one-file route
(`dimensjoner_mangler`, listed in `dimensions_to_add`), because a posted line
cannot be given a dimension afterwards; the server import adds them itself in
the same transaction, if the person may set up dimensions (`ledger:chart`,
else `dimensjoner_krever_administrator`). Each line gets the value for its
whole amount: Tripletex writes `DebitAnalysisAmount` on credit lines too, so
the file's own amount says nothing the line does not.

**A farm's account id.** In books with the farming add-on, a six- or nine-digit
Tripletex id (`402020`, `145120512`) whose fifth and sixth digits are a NIBIO
department gives the line that avdeling, and the last three a «Produkt» named
from the account's parenthesis («Kyr tilført i året»). The line's own
`Analysis` wins where the two disagree: in the pilot's file they do on about a
hundred balance lines and on two result lines in 2025, and the line is what
Tripletex reported on. Outside the add-on six digits stay only a sub-account.
The opening balance carries no dimension: it touches no income or cost
account, which is all the result per value reads.

**Checked against:** `tests/test_dimensions_api.py` and the dimension tests in
`tests/test_saft_voucher_import.py` (a line's «Avdeling» kept, «Bilagsart» and
«Ansatte» dropped; a farm's account id; six digits outside the add-on), and
the farm's real 2026 file through `regnaskaps_tests/saft_pilot.py`
(2026-10-15): eight values added (three avdelinger, five products), the
comparison and every VAT term still equal to Tripletex's, the result per
avdeling adding up to the year's, and Myntos's file valid against the XSD.

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
