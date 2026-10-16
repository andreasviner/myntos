# MVA-melding (VAT return)

Verified against official sources 2026-07-18; the code lists, XSDs and the
submission sequence re-read from `github.com/Skatteetaten/mva-meldingen` on
2026-10-06, when the melding itself was built. Canonical docs:
`skatteetaten.github.io/api-dokumentasjon/anvendelsesomraader/mvamelding`
(note: the older `skatteetaten.github.io/mva-meldingen` site carries a
"SIDEN ER UTDATERT" banner but still hosts the implementation guide, XSDs and
code lists via `github.com/Skatteetaten/mva-meldingen`).

## Model: SAF-T codes, not form posts

Since 2022-01-01 the MVA-melding is a **dynamic list of
`MvaSpesifikasjonslinje`** keyed on (simplified) SAF-T standard VAT codes —
the old numbered posts (RF-0002/0004) are gone; ignore any doc that mentions
them. Root element `mvaMeldingDto`; categories (`meldingskategori`):
`alminnelig`, `primaernaering`, `kompensasjon`, `omvendtAvgiftsplikt`,
`eHandel`.

Each line: `mvaKode`, optional `spesifikasjon`, optional
`mvaKodeRegnskapsystem` (your internal code; one "s", as the XSD spells it),
`grunnlag`, `sats`, `merverdiavgift`, optional `merknad`. Amounts are decimals
(øre allowed): **don't round**; validation compares `grunnlag × sats` vs
`merverdiavgift` with tolerance rules. `sats` is a *string* from
`kodelister/sats.xml` and is written with a comma: `25`, `15`, `12`, `11,11`.

`spesifikasjon` values, spelled exactly as `kodelister/mvaSpesifikasjon.xml`
spells them (an earlier version of this file had ASCII guesses, which
Skatteetaten does not know): `uttak`, `tapPåKrav`, `justering`,
`tilbakeføringAvInngåendeMerverdiavgift`, `kompensasjonsrett`,
`justeringMerverdiavgiftskompensasjonFastEiendom`.

### The codes, and how each is reported

Read from `kodelister/mvaKodeSAFT.xml`. Three kinds, and they are reported
differently (Skatteetaten's `example_files/melding/mvakode*.xml` show each):

| Kind | Codes | Reported as |
| --- | --- | --- |
| Utgående (sales) | 3 (25 %), 31 (15 %), **33 (12 %)**, **32 (fish, 11,11 %)**, 5 (zero-rated), 6 (outside the law), 51 (klimakvoter and gold sold to a business), 52 (export) | `grunnlag`, `sats`, positive `merverdiavgift` |
| Inngående (deductions) | 1, 11, 13, 12 (fish purchase), **14, 15 (VAT paid at the border, on a tollpass)** | `merverdiavgift` only, **negative**; no `grunnlag`, no `sats` |
| Omvendt avgiftsplikt | 81–85 (goods import), 86–89 (services from abroad), 91, 92 (klimakvoter and gold bought) | an output line with `grunnlag`, `sats` and positive amount, **plus** an input line with the same amount negated when the code carries deduction (81, 83, 86, 88, 91). 82, 84, 87, 89, 92 report the output line alone; 85 is zero-rated |

**33 is 12 %, 32 is fish.** Myntos seeded 32 as "lav sats (12 %)" until
migration `e1cb264f0c44`, which renames that row to 33 in place (its id, and so
every posting under it, is unchanged). A 12 % sale reported under 32 fails
validation with a message about basis and rate, not about the code.

- **Never report codes 0, 7, 20, 21, 22.** `NON_REPORTABLE_STANDARD_TAX_CODES`
  in `domain/ledger/models.py`, denormalised as `TaxCode.is_reportable`.
- A reportable code the melding has no meaning for (one somebody added by
  hand) is refused by name rather than reported as a guess.

The ledger carries a SAF-T VAT code on every transaction line; the melding is
`GROUP BY (mvaKode, spesifikasjon, sats)` over the `skattleggingsperiode`
(two-monthly default; enum also has monthly, quarterly, half-yearly, weekly,
half-month and `aarlig`). Myntos files two-monthly, monthly and yearly, the
three an ordinary business can have; the others belong to arrangements it does
not file.

## Rates (date-effective!)

2026: **25 %** standard · **15 %** food *and* water/wastewater (cut from 25 %
effective **2025-07-01** — same SAF-T code 31 as food, so one-rate-per-code
tables misvalidate 2025 periods straddling the change) · **12 %** passenger
transport, accommodation, cinema, broadcasting, museums, sport events ·
**11.11 %** wild marine resources · **0 %** export. Rate list is versioned
upstream: `Skatteetaten/mva-meldingen … kodelister/sats.xml`.

## Submission flow (sluttbrukersystem)

Auth is **ID-porten** (OIDC authorization-code; scopes `openid` and
`skatteetaten:mvameldingvalidering`), then exchanged for an Altinn token
(`…/authentication/api/v1/exchange/id-porten` — a raw ID-porten token against
the app 401s). Maskinporten is only accepted for the MVA read/information
APIs.

**Scope audit, 2026-08-24** (probed live, and checked against the portal's own
scope picker):

| Scope | ID-porten `5aa404aa` | Maskinporten `f8293b48` |
| --- | --- | --- |
| `skatteetaten:mvameldingvalidering` | **granted** | not registered |
| `skatteetaten:mvameldinginnsending` | **retired** | **retired** |

`mvameldinginnsending` is **gone, on Skatteetaten's own instruction**: the 2022
scope of that name "should no longer be used and should be replaced with
`altinn:instances.read` and `altinn:instances.write`". That is why it is missing
from Samarbeidsportalen's picker, and the code says why it was retired:
`MvaMeldingClient` makes exactly one Skatteetaten REST call, to
`/api/mva/grensesnittstoette/mva-melding/valider`. Everything after it -
instance, uploads, submission, feedback - goes through the Altinn app, which
authorizes on the exchanged Altinn token.

**So nothing needs to be applied for to submit an MVA-melding.**
`mvameldingvalidering` (granted) plus `altinn:instances.read`/`.write` (granted)
is the complete credential set for the documented flow. An earlier version of
this file claimed a second Skatteetaten scope was required; it was wrong, and the
replacement instruction is the reason.

What the picker does offer, none of it required today:

| Scope | For | Worth it |
| --- | --- | --- |
| `mvaregisteravgiftssubjekt` | MVA-register: who is registered and liable | yes, on **Maskinporten** - `registrertIMvaregisteret` decides whether output VAT is booked at all, and this is the authoritative source rather than Brreg's copy |
| `mvameldinginnsendingstatus` | "Innsendingsstatus og frister" | yes, on **Maskinporten** - drives which periods are due per client |
| `mvamelding` | the melding itself | later, when we add a fetch. There is no fetch method today |
| `mvameldingsopplysning`, `mvafastsetting` | meldingsopplysninger, assessment | no code path |
| `mvainnsendingsstatustest`, `mvameldinginnsendingsstatus` | test-suffixed and undescribed near-duplicates of the two above | no - prefer the described one of each pair |

The two "yes" rows belong on the **Maskinporten** client, not ID-porten: they are
lookups with no person in them. Validation and submission are the opposite, and
stay on ID-porten.

**Data types, read off the app on 2026-08-24** (not from documentation - the
app is the only authority, and `GET {app}/api/v1/applicationmetadata` returns
them):

| Purpose | dataType id |
| --- | --- |
| metadata | `no.skatteetaten.fastsetting.avgift.mva.mvameldinginnsending.v1.0` |
| the melding | `mvamelding` |
| attachments | `binaerVedlegg` |
| feedback | `valideringsresultat`, `betalingsinformasjon`, `kvittering` |

The app also declares `tidligereinnsendtvedlegg`, `miljo`, `utfyllingslogg`,
`signeringslogg`, `godkjenningslogg`, `attesteringslogg` and `portalLenker`,
none of which we use yet. The logs are the audit trail a bokforingspliktig may
want to keep.

Note the test app is `mva-melding-innsending-v1`, the **same name as
production** - `-etm2` does not exist on tt02 and answers 418. Environments are
separated by host, not by app name.

### The sequence

From Skatteetaten's implementation guide (`docs/documentation/api/index.md`)
and their own reference script (`test_with_python_script_files/`), both read on
2026-10-06. The parts marked **sharp edge** each turn into a 4xx with a
misleading message when missed.

1. `POST {validation}/api/mva/grensesnittstoette/mva-melding/valider` with the
   ID-porten token and `Content-Type: application/xml`. Answers a
   `valideringsresultat` (namespace `…:valideringsresultat:v1`):
   `avvikVedMeldingslevering` is `ingen avvik`, `avvikende skattemelding` (may
   be sent, may be followed up), `mangelfull skattemelding` or
   `ugyldig skattemelding` (will be refused), plus `avvik` entries. The app
   answers a refused `process/next` with an older shape
   (`status` / `valideringsfeil` / `valideringsDetaljer`); read both.
2. `POST {app}/instances` with `{"instanceOwner": {"organisationNumber": …}}`
   on the exchanged Altinn token. **Sharp edge:** the instance is created
   *with* an empty `mvaMeldingInnsending` data element.
3. `PUT {app}/instances/{party}/{guid}/data/{that element's id}` with the
   envelope, `Content-Type: application/xml`. **Sharp edge:** PUT into the
   existing element, not POST a new one; the data type allows one. Myntos POSTed
   until 2026-10-06 and every stub accepted it. The envelope:
   - is written **without** a namespace, as every Skatteetaten example is and as
     the app stores it when read back (`exampleSuccessfulFeedback17062021/
     konvolutt.xml`), although the XSD declares a target namespace;
   - must carry `instansstatus` (optional in the XSD, required by the app: 409
     "instansstatus er påkrevd"); its value is not read, Skatteetaten's examples
     put `Deprecated` there;
   - must list every uploaded file under `vedlegg` (the melding as
     `vedleggstype` `mva-melding`, `kildegruppe` `sluttbrukersystem`), or
     `process/next` refuses;
   - must agree with the instance on the orgnr and with the melding on the
     `meldingskategori`.
4. `POST {app}/instances/…/data?dataType=mvamelding`, `Content-Type: text/xml`,
   `Content-Disposition: attachment; filename=mva-melding.xml`. Content-
   Disposition is required on every upload; filenames must be unique within
   the instance.
5. Optional attachments (`binaerVedlegg`): max 57 files, 25 MB each.
6. `PUT {instance}/process/next` **twice**: complete filling, then submit.
   The first one runs the validation again and answers 409 with the result.
7. `GET {instance}/feedback/status` (`isFeedbackProvided`), then the instance's
   data elements `valideringsresultat` (XML), `betalingsinformasjon` (XML:
   KID, `beloep`, `betalingsfrist`, `betalesTilKonto`) and `kvittering` (PDF).
   `betalingsinformasjon` is produced while the submission completes; the
   receipt can lag.

UI requirement: the user must see the full aggregated melding (all lines +
`fastsattMerverdiavgift`) and actively confirm before submission.

## What Myntos does

`domain/mva` builds the melding; `api/v1/mva.py` is the lifecycle;
`integrations/skatteetaten/mva.py` is the wire. `/mva/` is the page.

- **Building.** `melding.aggregate` over every posted line with a reportable
  code whose `gl_posting_date` falls in the period. Reversals keep the
  original's posting date and mirror its tax amounts, so they net out without
  special-casing. Pure functions, tested against Skatteetaten's XSD
  (`backend/tests/fixtures/mva/`, Apache 2.0, copied verbatim).
- **Period and category** are settings on the company (`vat_period_type`,
  `vat_category`; `toMaaneder` and `alminnelig` by default), changed on `/mva/`
  with `organization:manage`. Deadlines follow skatteforvaltningsforskriften
  § 8-3-1, moved off weekends but **not** off public holidays, so the date shown
  is never later than the real one.
- **Check, then file the same bytes.** `POST …/termin/kontroll` validates and
  stores an `mva_filing` row with the exact XML and a hash of the lines.
  `POST …/innsendinger/{id}/send` refuses unless the person confirms the
  checked figure exactly, the check did not block, and the ledger still hashes
  the same (`melding_changed`, 409, otherwise). The XML sent is the stored one.
- **Failure halfway is recorded**, not rolled back: a 502 *returned* with the
  filing marked `feilet`, the Altinn instance id kept, because the instance may
  now exist and someone has to be able to find it.
- **Settlement.** On submission, the period's 2700 and 2710 are moved onto 2740
  in a voucher of its own (journal `MVA`, dated the last day of the period).
  The sums include earlier settlements, so a correction moves only the
  difference. The year summary leaves the `MVA` journal out, so a settled
  period still shows the VAT it had.
- **Receipts** are stored through `myntos.storage` and listed in
  `mva_filing_document`; the KID, amount, due date and account are read out of
  `betalingsinformasjon` onto the filing.
- **The government login is not spent** by validation, submission or the receipt
  fetch; the receipt needs the same login after the submission, and ID-porten's
  own expiry is what limits it.
- **Download.** `GET …/termin/xml` gives the melding as XML at any time, which
  Skatteetaten's portal accepts as an upload: the way to file while this
  installation cannot.

Not built: the `kompensasjon`, `omvendtAvgiftsplikt` and `eHandel` categories,
attachments, the Maskinporten lookups above (`mvaregisteravgiftssubjekt`,
`mvameldinginnsendingstatus`), public holidays in deadlines, a line-level
`merknad`, and tilbakegående avgiftsoppgjør (§ 8-6) for purchases made before
registration.

### Spesifikasjon: uttak, tap på krav, justering, tilbakeføring (built 2026-10-09)

`domain/mva/adjustments.py`, `/mva-justeringer/`, routes
`/companies/{id}/mva/spesifikasjoner` (list, record; `ledger:write`) and
`…/{adjustment_id}/reversering`. Each is an ordinary posted voucher in journal
`MVAJ`; the line carrying the tax information also carries
`gl_transaction_line.vat_specification`, and `melding.aggregate` groups on it.
A reversal copies the column (`posting._reverse_line`), so an undone tap på
krav cancels inside its own melding line and not against ordinary deductions.

Shapes, matching Skatteetaten's `example_files/melding/eksempelMedAlleTilfeller.xml`
(vendored in `backend/tests/fixtures/mva/`, with `kodelister/mvaKodeSAFT.xml` and
`mvaSpesifikasjon.xml`, from commit `05c8b14e`):

| Spesifikasjon | Codes Myntos accepts | Entered as | Posted | Reported |
| --- | --- | --- | --- | --- |
| `uttak` | 3, 31, 33 | the value, ex. VAT | D contra (2060 by default) / K 3000·3010·3020 with tax info / K 2700 | grunnlag, sats, positive mva |
| `tapPåKrav` | 1, 11, 12, 13 | the claim written off, VAT incl. | D 7830 net with tax info / D 2710 / K 1500 (the customer) | mva only, negative |
| `justering` | 1 | the VAT, signed: + owed back, − deducted | contra (7790) and 2710, tax info on the contra with the tax side set by the VAT's direction | mva only, either sign |
| `tilbakeføringAvInngåendeMerverdiavgift` | 1 | the VAT, owed | D contra / K 2710, as above | mva only, positive |

The accepted codes are a **subset** of what `mvaKodeSAFT.xml` allows, and a test
holds them to it: uttak under 5 is allowed there and left out (no zero-rated
domestic income account to credit without guessing), and justering and
tilbakeføring under 81 are allowed and left out (no published example of how
such a line sits beside 81's own pair). A spesifikasjon on any other code is
refused by the aggregator by name rather than reported.

For justering and tilbakeføring the amount *is* the tax, as on a tollpass, so
the basis written to SAF-T is derived (amount ÷ rate), and the tax information
sits on the contra line rather than on 2710, so the SAF-T check's "tax on the VAT
line" note does not fire for a correct entry. Recording any of them is refused
for a company not VAT-registered on the date (409 `not_vat_registered`): there is
no melding to put them in. Skatteetaten's example puts
`<utvalgtMerknad>personkjøretøy</utvalgtMerknad>` on its tilbakeføring line;
Myntos sends no merknad yet, and if validation ever asks for one the `avvik`
will name it.

## Two returns (built 2026-10-15)

A farm, forest or fishery files the **primærnæring** melding, and may put other
business in it only while that business's taxable sales and uttak stay at or
under 30 000 kroner a year (merverdiavgiftsloven § 15-1 (1)). Above that it
files an **alminnelig** melding as well (Merverdiavgiftshåndboken 15-1.3), each
with its own terms: primærnæring yearly, due 10 April
(skatteforvaltningsforskriften § 8-3-7, § 8-3-10 (3)), or two-monthly when
Skatteetaten agreed (§ 8-3-8 (3)-(4)); alminnelig two-monthly (§ 8-3-1), or
yearly under a million kroner (§ 8-3-3, where (2) waives the twelve months'
qualification for a § 8-3-7 filer's other business). Each melding carries its
`meldingskategori`, and Skatteetaten holds the duty per category.

`domain/mva/naering.py`. The **main melding** is the company's own two columns,
untouched, so a company with one melding is exactly what it was. The **second**
is one `mva_second_naering` row (period length, `valid_from`), whose category
is always the other of the two Myntos files, with its accounts in
`mva_second_naering_account`.

- **A line goes in the melding of its account.** A line on one of the second
  melding's accounts dated on or after `valid_from` is the second melding's;
  every other line is the main one's. Read when the melding is built, not
  stamped on the line, so it reaches invoices, imports and manual vouchers
  without any of them knowing. Why by account and not by category or per line:
  the module docstring.
- **The past is held by a guard, not by stamps.** `naering.guard` refuses any
  change (an account moved, `valid_from` moved, the second melding removed, the
  categories swapped) that would move a taxed line into or out of a term filed
  or recorded as filed elsewhere: 409 `filed_lines_would_move`.
- **Routes:** `naering` (a category) on the overview, the termin, its XML, the
  check and an agent's request to file; `PUT`/`DELETE …/mva/andre-melding`
  (`organization:manage`).
- **Settlement:** each melding in its own voucher onto 2740, its text naming the
  melding; split mode settles the melding's own output and input less earlier
  settlements of the same category and term, not the account balances, which
  hold both. The reconciliation on the term page compares 2700/2710 with both
  meldinger's lines for the term's dates.

**Known limit: input VAT on costs shared by both businesses.** How the
deduction on a cost both businesses use (diesel, a telephone, a machine) is to
be divided between the two meldinger is **not settled**. The only guidance
found (2026-10-13) is on fellesanskaffelser between taxable and exempt use,
where a proportional split is accepted; nothing found says how Skatteetaten
wants it between two meldinger of one company, nor whether the two meldinger's
payments and refunds are netted. So Myntos applies **no rule**: the whole line
goes to the melding of its account, and an account in neither list to the main
melding. The person chooses, by the account they book the cost on, or by
booking it as two lines on two accounts in the split they and their accountant
settle on. The MVA page says so beside the account list and on every term of a
company with two meldinger; the skill tells agents not to invent a split. To
close: get Skatteetaten's answer (SBS support), then decide whether Myntos
should propose a split.

Also not built: a second melding cannot end (no `valid_to`); invoice lines post
to fixed income accounts (3000/3010/3020), so the second business's invoice
sales need those accounts assigned or a separate account chosen; only
alminnelig and primærnæring can be paired.

## Filed elsewhere (built 2026-10-15)

A company that moves to Myntos during a year, or runs it beside the old program
(the farm pilot's parallel run, saf-t.md), has terms the old program filed.
Myntos computes them from the imported vouchers and, knowing nothing else,
offered them as not sent. Sending one would not be a harmless duplicate: **a
second melding for a term replaces the first** at Skatteetaten.

`domain/mva/elsewhere.py`. The person records it: an `mva_filing` row with
status `annet_program` (no XML, no answer; Myntos's own figures at the time as
the snapshot) and its detail in `mva_filing_elsewhere` (the day, optionally the
amount, the imported settlement voucher it came from).

- `POST …/mva/termin/annet-program` records it (`ledger:write`, like filing; a
  key `key_write`: undoable and files nothing). Refused for a term Myntos filed
  (`already_filed`), already marked (`filed_elsewhere`), not over, or with a
  `filed_on` before the term ended or in the future (`invalid_filing_date`).
- While it stands the term is closed (`FilingStatus.closes_term`): not overdue,
  check and send refused with 409 `filed_elsewhere`, as is an agent's request
  to file, and a waiting request is marked done.
- `POST …/innsendinger/{id}/angre` takes it back: status `opphevet`, kept with
  who and when, because the term's history is who said it was filed elsewhere
  and who changed their mind. That is also how a correction for such a term is
  filed from Myntos.
- **Proposed from the import:** the old program's settlement voucher
  (`IMPORT` journal, voucher type `mva_avregning`) whose output and input are
  exactly a term's melding, within 115 days after the term ends, is shown as
  `elsewhere_proposal`. With one melding a voucher dated on a term's last day
  is proposed even when the amounts differ; with two, only an exact match. A
  proposal is never recorded on its own.
- The comparison: Myntos's figure minus the recorded amount; under a krone is
  agreement, since Tripletex rounds the settlement to kroner (øre on 7740).
- **Asked on the import too** (2026-10-15): the finished import on
  `/tidligere-program/` lists every term of the imported years that is over
  and neither filed nor marked, the proposed ones ticked, and marks the ticked
  ones through the same route, one request each, `filed_on` the deadline (the
  default `/mva/` fills in) and the proposal's amount and voucher when there is
  one. That is the moment the person knows the answer; left to `/mva/`, every
  such term read as overdue with «Send» beside it.

**The overview's VAT card is not the term list.** It is the balance on 2700,
2710 and 2740, and marking a term filed elsewhere does not move it: filed is
not paid. A company that brought only last year over shows last year's final
term there (on 2740 once the old program's settlement came across) until the
payment, a voucher in a year Myntos does not have yet, is posted. The summary
therefore carries `books_through`, `vat_settled` and `positions_as_of`
(`domain/mva/naering.books_behind`): once the deadline of the term the books
stop in has passed with nothing posted since, the balance is that day's, and
the card shows «–» and «ikke kjent: regnskapet slutter <dato>» instead of an
amount. The first version kept the amount with the date under it, and
«−303 471,00 kr» was still read as a refund owed in October for one paid out
in February. The deadline and not New Year decides, because on 1 January last
year's final term is open and its figure is real. Zeroing the card on the
person's say-so was considered and refused: it would be a payment the books do
not hold, and the bank balance beside it would still be last year's.

## Registration status (built 2026-10-09)

`company.vat_registered` says whether the company is in Merverdiavgiftsregisteret
now; `company.vat_registered_from` (nullable) says from which day.
`domain/ledger/vatstatus.registered_on(company, date)` is the only reader, and
it is always asked with the **entry's own date**: a purchase made before the
registration took effect carried no deduction, whenever it is typed in.

What the status decides, for *new* entries only:

- **Registered:** the catalogue's defaults, any code the company has.
- **Not registered:** code 0 for every category, so the whole amount goes to
  the category's account and nothing to 2700 or 2710; only code 0 and the
  non-deductible reverse-charge codes (82, 84, 87, 89, 92: remote services
  bought from abroad are owed without registration) are accepted
  (`tax_code_not_allowed`, 422, otherwise); `mva_innforsel` is refused, because
  border VAT is part of what the goods cost. The company catalogue
  (`GET /companies/{id}/cashflow/types`) and the bilag proposal follow the same
  rule, so the form never offers what the register would refuse.

**Posted vouchers are never touched.** Each line carries the code and rate it
was posted with; flipping the flag or the date changes what is proposed next,
and a test compares every posted line before and after a switch. Before this,
an unregistered company had every purchase split with 25 % onto 2710, a
receivable no melding would ever claim.

Known gap: a company that is deregistered (flag set to false) is then treated as
unregistered on every date, so correcting an entry from its registered years
cannot use a deduction code. A `vat_registered_until` would close it.

## Endpoints

| | Test | Production |
| --- | --- | --- |
| Validation | `https://idporten-api-test.sits.no/api/mva/grensesnittstoette/mva-melding/valider`¹ | `https://idporten.api.skatteetaten.no/api/mva/grensesnittstoette/mva-melding/valider` |
| Altinn 3 app | `https://skd.apps.tt02.altinn.no/skd/mva-melding-innsending-v1/instances` | `https://skd.apps.altinn.no/skd/mva-melding-innsending-v1/instances` |
| ID-porten | `https://test.idporten.no/.well-known/openid-configuration` | `https://idporten.no/.well-known/openid-configuration` |

¹ **The documented test validation host does not answer.** Skatteetaten
documents `idporten-api-sbstest.sits.no`, which resets every connection
straight after the TLS handshake (2026-10-06, and again 2026-10-09 10:10 UTC:
`curl: (56) Recv failure: Connection reset by peer`). `idporten-api-test.sits.no`
answers the same path, and is what validated the 2026-10-10 run, so the table
gives it and the TT02 copy points `SKATTEETATEN_MVA_API_BASE_URL` there. Switch
back once Skatteetaten fixes the documented one.

The Altinn **host differs between environments**; the MVA app is `…-v1` in
both (`…-etm2` is in older docs and answers 418 on tt02). Configuration values,
never constants. Ignore legacy `oidc-ver2.difi.no` hosts in older docs.

## Testing

Test logins use **TestID** with **synthetic** people from Tenor Testdatasøk
(`skatteetaten.no/skjema/testdata/`, logged into with a real ID-porten login of
your own). A synthetic fødselsnummer carries **+80 on the month digits** — born
in November 2024 gives `0191…` — which is how one is recognised, and why it can
never collide with a real number. Digdir now recommends TestID for everyone
rather than the older password-based test users; it has no password and lets the
caller pick the assurance level.

**Headless login is supported in test, and it is worth building against.**
ID-porten's `/authorize` accepts

    login_hint=testid:<synthetic pid>_idporten-loa-high

and authenticates without a person or a browser, redirecting straight back with
a code. The result is a full token — refreshable, introspectable, and carrying
`amr=TestID` — so the whole submission chain (ID-porten token → Altinn exchange
→ instance → `process/next` twice → receipt) is automatable end to end. Test
only; production has no equivalent, and the `amr` claim is how a token from it
is told apart. Verified against docs.digdir.no 2026-08-24.

### Running the validation live, and where it stands

    cd backend && uv run python -m myntos.auth.digdir_check \
        --person 05849898878 --mva 311124226 --save <dir>

run from the TT02 copy, not the repo (CLAUDE.local.md), so it reads the test
client's `.env`. It logs the synthetic daglig leder in through TestID, builds a
melding with `domain.mva.melding` from one 25 % sale and one deduction for the
last finished two-month period, and posts it to the validation endpoint. It
stops there: no Altinn instance and no `process/next`, because the first is a
filing in progress somebody has to clean up and the second is a filing.
`311124226` is used because it is MVA-registered; `05849898878` is its daglig
leder.

**2026-10-09: not yet answered, and the cause is ours.** The melding it builds
validates against Skatteetaten's XSD, and the test host answers, but the login
never produces a token: `IDPORTEN_KID` is blank in the TT02 `.env`, and the
key's RFC 7638 thumbprint (what `digdir_key` prints) is not the kid the portal
holds it under. ID-porten said so in as many words, `invalid_client` "Invalid
JWT. Key identifier (kid) reference not found", which the code exchange now
reports instead of a bare 401. The kid is on the client's Nøkler tab in
Samarbeidsportalen; once it is in the TT02 `.env`, the command above is the
whole proof.

**2026-10-10: answered, and the one finding is about test data.** With the kid
in place the whole chain ran: TestID login, Altinn exchange, validation. The
melding Myntos built passed every structural check, and Skatteetaten returned
a single `ugyldig skattemelding` finding:

    MVA_PLIKT_OPPGITT_MELDINGSKATEGORI_ALMINNELIG_NÆRING_FINNES_IKKE
    //meldingskategori: Virksomheten er ikke registrert i
    Merverdiavgiftsregisteret med plikt til å levere mva-melding for
    alminnelig næring.

That is a lookup in TT02's own MVA register, which is not Enhetsregisteret:
Tenor marks `311124226` (AS) and `311186191` (ENK) `registrertIMvaregisteret`,
and both get this answer, for 4. termin 2026, 3. termin 2025 and 6. termin
2024 alike. So it is not the period and not our melding. What is still
missing is a TT02 company that Skatteetaten's test MVA register holds with an
alminnelig filing duty. Ask Skatteetaten's SBS support which test
organisations those are, or to register one, rather than trying more Tenor
entities at random. The answers are saved under
`myntos-tt02/mva-validation-2026-10-10*`.

The scopes and both Digdir clients are in place (ROADMAP, phase 0b). What
remains is a TT02 test organisation the MVA register holds (above) and the
production run. Since Altinn 2 shut down (2026-06-19), customers must delegate
the new MVA tilgangspakker (available since 2026-04-15) or on-behalf
submissions fail authorization.
