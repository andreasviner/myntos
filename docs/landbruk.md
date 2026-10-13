# Landbruk: the farming add-on

Myntos is an accounting program for every business. It is not a farm program,
and nothing in this file may change what a company without the add-on sees. A
farm still needs things nobody else does, so it gets an **add-on**: NIBIO's
accounts, the farm's categories in the register, and a bilag reader that knows
how a farm's paperwork works. Read this before touching anything with
`segment="landbruk"`, `company.landbruk`, `LANDBRUK_ACCOUNTS` or a farm
category.

Why it exists: on 2026-10-12 a farmer told the owner that Tripletex supports
farming badly, and the owner asked for Myntos to have everything a farm needs,
on the condition that Myntos stays a program for everyone. The same day Roald at
Reime Gard (an ENK) offered the farm as a pilot, to be run beside its current
system with its accountant checking the books and the year-end. The mapping he
asked for, law against wish, is [below](#what-a-farm-needs-and-where-myntos-stands).

Research behind this file, all from 2026-10-12: Skatteetaten's handbooks
(Skatte-ABC 2025/26, Merverdiavgiftshåndboken), Lovdata, NIBIO's Landbrukets
kodesystem 1.3.6 (December 2025), the help centres of Tripletex, Fiken,
PowerOffice, Duett and DNB Regnskap, and Landbrukets Dataflyt's price list and
service description. [Sources](#sources) at the end.

## The rules

### 1. An add-on, never a kind of company

- Every farm category carries `segment="landbruk"` (`domain/ledger/cashflow.py`)
  and is offered, proposed by the model and accepted for posting only when
  `landbruk.is_enabled(company)`. `types_for()` without segments is the
  catalogue every business has always seen, in the same order; a test holds it.
- The farm accounts (`chart.LANDBRUK_ACCOUNTS`) are never seeded into a new
  company. They are added when the add-on is switched on, when the register
  says the company is a farm, or the first time a farm category needs one.
- **Who has it.** `company.landbruk` is three-valued. NULL means nobody has
  said, and the næringskode decides: SN2007 division 01 (jordbruk) and 02
  (skogbruk) are on. True and False are a person's answer and outrank the
  register, because farms registered under another code exist, and so does a
  01 company that wants none of it. Fishing (03) is primærnæring too, but its
  products, rates and settlements are different, and it gets nothing from this
  add-on.
- **Switching needs `ledger:chart`** (Administrator, Eier), like the
  kontoplan, because it adds accounts and changes what every colleague is
  offered. Posting a farm category in books without the add-on is refused
  (409 `segment_not_enabled`) rather than switching it on: one entry, perhaps
  an agent's, must not change what the whole company sees.
- **Switching off removes nothing.** Accounts may have history; posted entries
  keep their category and read back by name.

*Why:* Tripletex's landbruk module replaces the chart of accounts and cannot be
undone, and PowerOffice's the same. A program for everyone cannot make that
trade, and does not need to.

### 2. NIBIO's numbers, in NS 4102's four digits

NIBIO's Landbrukets kodesystem is the chart every farm accountant, every farm
program (Duett, Tripletex, PowerOffice, DNB Regnskap, Eika) and Landbrukets
Dataflyt use. NIBIO writes `department.account` (20.3071 is milk in jordbruk;
departments 10 finans, 20 jordbruk, 30 skogbruk, 50 pelsdyr, 60 annen næring,
90 privat). Myntos keeps NS 4102's four digits and the **jordbruk department's
leaf accounts**, so 3071 is milk to anyone who has kept farm books, and a future
Dataflyt import maps one to one.

- **Leaf accounts only, never NIBIO's headings.** 3010 is NIBIO's heading for
  potatoes and vegetables and Myntos's «Salgsinntekt, avgiftspliktig middels
  sats»; the two must never meet.
- **Forestry** could not keep NIBIO's department 30 numbers, which reuse 3010
  and 3030. Timber and firewood go on 3094 («Skogsvirke», jordbruk
  department); the skogfond accounts keep NIBIO's 7761 and 3910.
- **Every account has a pair in Skatteetaten's næringsspesifikasjon list**
  (tested). Proposed, not chosen: `chosen_at` stays empty, so a corrected pair
  in a later release reaches books that never chose one.
- The department becomes a **næring dimension** when the books need two
  næringer (jordbruk and annen næring); see [Not built yet](#not-built-yet).

### 3. The VAT rates are not the shop's

Milk delivered to the dairy, slaughter animals, live animals and grain are **25
%**: none of them is a næringsmiddel until the dairy, the slaughterhouse or the
mill has processed it (Merverdiavgiftshåndboken M-5-2.4). Booking the dairy's
milk at 15 % because milk is food on a shelf is the mistake this add-on exists
to prevent, and NIBIO's own table codes 3071 O_S (25 %).

| Supply | Rate | Category |
| --- | --- | --- |
| Milk to the dairy, slaughter, live animals, grain and seed | 25 % | `landbruk_melk`, `_slakt`, `_livdyr`, `_korn` |
| Distriktstilskudd paid by the dairy or slaughterhouse with the price | 25 % (NIBIO O_S) | `landbruk_distriktstilskudd_melk`, `_slakt` |
| Hay, straw, silage; wool and skins; timber, firewood | 25 % | `landbruk_grovfor_salg`, `_ull`, `_skogsvirke` |
| Potatoes, vegetables, fruit, berries, eggs sold as food | 15 % | `landbruk_poteter`, `_gront`, `_egg` |
| Seed potatoes, feed potatoes, hatching eggs | 25 % | the same categories, code 3 (the note says so) |
| Leiekjøring, hunting and fishing rights, milk quota rented out | 25 % | `landbruk_leiekjoring`, `_jakt`, `_kvote_inn` |
| Land and grazing rented out (jordleie) | exempt, mval § 3-11 (1) | `landbruk_jordleie_inn`, code 6 |
| Produksjons- and avløsertilskudd, compensation | outside VAT | `landbruk_tilskudd_*`, `_erstatning`, code 0 |
| Feed, fertiliser, seed, vet, medicine bought | 25 % deductible | `landbruk_kraftfor` … |

There is no flat-rate or compensation scheme for farmers in Norway.

### 4. A settlement is the farm's sale

Tine, Nortura, Felleskjøpet, the mills and the timber buyers write the sales
document for the farmer: bokføringsforskriften § 5-2-1 tredje ledd lets
cooperatives and approved buyers issue it. The name at the top is the
**customer**. Two things follow:

- **The bilag reader reads it as money in.** `interpretation.issued_by_buyer`
  recognises self-billing for everyone («selvfaktura», «utstedt av kjøper»)
  and, only in farm books, the trade's names for it (melkeoppgjør,
  slakteoppgjør, kornoppgjør, tømmeroppgjør …), since «oppgjør» alone is also
  the word on a power bill, and an «avregning» that pays out («Utbetalingsdato»,
  «Utbetalt»): Fatland calls its slaughter settlement «Avregning nr.» and
  nothing else, while a power company's avregning says what is owed and when
  it falls due. It outranks the party register, because
  Felleskjøpet is the feed supplier and the grain buyer on one orgnr. The
  model is told the same in a fixed sentence (`context._FARM_SAID`). Without
  this a melkeoppgjør came back as a purchase from Tine, and since the
  category menu is narrowed by direction, «Melkeoppgjør» was never even on it.
- **Each post on the settlement is a line of a composite entry**
  (ARCHITECTURE.md, The cash-flow register). The milk at its gross price with
  its 25 %; the deductions as their own lines:

  | Deduction | Booked | Why |
  | --- | --- | --- |
  | Omsetningsavgift, forskningsavgift | `landbruk_trekk_avgift`: against the product's sales account (3071, or moved to 3041/3001), with the sale's 25 %, which it gives back | NIBIO codes it against the product (tillegg/trekk 33002, 43004), and the buyer computes the VAT on the price after it (below) |
  | Andelsinnskudd | `landbruk_trekk_andel`: 1355, balance sheet | The share is the farmer's money; a cost would deduct money the farmer still has |
  | Kraftfôr set off | `landbruk_kraftfor`: a purchase with its own 25 % deduction | Each supply carries its own VAT; netting them would hide both |
  | Skogfond on a timber settlement | `landbruk_trekk_skogfond`: 7761, a deductible cost | Skatte-ABC S-17-14.27 |
  | Bunnfradrag on produksjonstilskudd | `landbruk_trekk_tilskudd`: 3499, against the tilskudd | Keeps the granted tilskudd visible |

  A deduction that reduces the sale (`deducts_from_sale=True`) **books as the
  sale's side**: if the settlement shows VAT on it, the VAT is debited to 2700
  and reduces the output VAT. Booked as an ordinary money-out category it would
  have claimed a deduction on 2710 for tax nobody charged the farm. The
  catalogue refuses a deduction that is not money out onto an income account.

- **The levy comes off the VAT basis, because the settlement takes it off.**
  This said the opposite until the pilot's first settlement arrived: that the
  levy is the producer's own debt the buyer collects, not a price reduction,
  so the sale's VAT basis stays the gross price. Q-Meieriene's avregningsoppgave
  for September 2026 sums the price posts, the supplements and «Omsetningsavgift»
  to «Netto avregnet» and prints «25,0% merverdiavgift av» that net; Landbrukets
  Dataflyt's posting of the same settlement put the levy into 3071 under the
  milk's own code 3. Whatever the theory, the settlement is the farm's sales
  document (§ 5-2-1), and the output VAT in the books must be the VAT printed on
  it: booked without VAT, the levy left 25 % of itself as output VAT the dairy
  never charged, and the lines no longer added up to the bank. The category's
  default is 25 % now, and a buyer that computes VAT before the levy is
  answered by setting the line to 0.
- **Posts below the VAT line are their own lines.** The same settlement prints
  the membership in husdyrkontrollen after «Beløp inkl. mva.», with its own
  25 %: a purchase set off (`landbruk_husdyrkontroll`), not a reduction of
  the sale.
- **Etterbetaling** from a cooperative is income in the year the årsmøte
  decides it (Skatte-ABC J-2-7.1), with VAT as part of the price; it goes on
  the product's account (`landbruk_melk` for Tine).

### 5. Tilskudd are income in the year they are paid

«Årlige tilskudd til driften skattlegges i utbetalingsåret» (Skatte-ABC
J-2-8.63.2). Produksjonstilskudd for 2025 paid on 11 February 2026 is 2026
income; it is booked on the day it arrived, and no accrual is offered. Outside
VAT. Investeringstilskudd (Innovasjon Norge) is not income: it reduces the
asset's cost (skatteloven § 14-42 (2) a), and the note on «Annet tilskudd»
says so.

### 6. The VAT return

Primærnæring files once a year, due 10 April (skatteforvaltningsforskriften
§§ 8-3-7, 8-3-10). Built before the add-on (`domain/mva/periods.py`, setting
«Primærnæring (jord, skog, fiske)»). Binæring up to 30 000 kr ex. VAT may go in
the same return (mval § 15-1 (1)); above that the company files an alminnelig
return as well, which Myntos cannot do yet (one `vat_category` per company).

## What a farm needs and where Myntos stands

Roald's list from Reime Gard, with the competitors' answer beside it. **Law**
means the farm cannot do without it; **wish** means it can, with more work.

| Area | Law or wish | Myntos 2026-10-12 | Missing |
| --- | --- | --- | --- |
| Farm chart of accounts | Wish (the law asks for bookkeeping, not NIBIO) | **Built**: 62 NIBIO accounts, grouped | Product codes and quantities per line |
| Correct VAT | Law | **Built**: farm rates as defaults; primærnæring yearly return | Two returns in one company (primærnæring + alminnelig over 30 000 kr) |
| Milk, slaughter, grain, timber income | Law (documented sales) | **Built**: settlements as composite entries, read as sales | Import from Landbrukets Dataflyt ([integrations/landbrukets-dataflyt.md](integrations/landbrukets-dataflyt.md)) |
| Produksjons- and avløsertilskudd | Law (income, payment year) | **Built**: a category per tilskudd, bunnfradrag apart | Tilskudd settlement through Dataflyt |
| Livestock valuation | Law (skatteloven § 8-1 (4), FSFIN § 14-5-3, takseringsregler) | Accounts 1450/4690 only | Year-end count with Skattedirektoratet's yearly rates; `buskap` in the næringsspesifikasjon |
| Feed and other inventory | Law (varelager; post 1401 is not formue) | Accounts 1400/1490 only | Year-end count; own-produced feed at takseringsregler values |
| Machinery and depreciation | Law (saldo, skatteloven § 14-40 ff.) | Capitalising to 1200/1230 only | Asset register and saldoavskrivning (FEATURES #7) |
| Buildings and investments | Law (saldogruppe h, husdyrbygg 6 %, § 14-43 (3); grants reduce cost, § 14-42) | Account 1100 only | As above, with the 6 % rule and investeringstilskudd |
| Milk quota | Law (capitalised, not depreciated) | **Built**: 1010, 3675, 6475 | Gain on sale (gevinst- og tapskonto) |
| Jordbruksfradrag | Law (personal skattemelding; Skatteetaten computes it from the basis) | Nothing | The basis per FSFIN § 8-1-11, and an estimate (2025: 95 800 + 38 %, max 200 850; 2026: 99 600, max 208 900) |
| Jordbrukskonto (§ 14-82, from 2025) and tømmerkonto (§ 14-81) | Law once chosen; a deficit must go on | Nothing | Tax-only balances per kommune: 85 % back in 2025, 80 % from 2026; 20 % for tømmerkonto; ENK only |
| Næringsspesifikasjon and skattemelding for the ENK | Law (only from a program) | Groupings only | FEATURES #2, with the farm cards (jordbrukskonto, skogOgToemmerkonto, buskap) |
| Driftsgrenregnskap (melk, storfe, sau) | Wish | Nothing; the schema has SAF-T analysis dimensions | Departments and dimensions (FEATURES #37) |
| Private uttak and kår | Law (a list of uttak, bokføringsforskriften § 3-1 nr. 5; kår via a-melding, § 6-52) | VAT uttak on /mva-justeringer/ only | Uttak at takseringsregler rates; kår needs payroll (FEATURES #14) |
| EHF in through Peppol | Law from 2030, expected now | Nothing | FEATURES #13; access point through invoice.be |
| SAF-T 1.40 | Law on request | **Built**, and the opening balance and the year's vouchers are read from the old program's SAF-T file (Tripletex's farm accounts included), with a comparison for the parallel run | Earlier years' vouchers and the documents (FEATURES #17) |
| Bank reconciliation | Wish | **Built** (camt.053) | Live bank feed (FEATURES #9) |
| Approval and payment | Wish | Nothing | FEATURES #11, #36 |
| Backup to the farm's own OneDrive or SharePoint | Wish | Nothing | Full export (FEATURES #44). The farm's own Microsoft 365 is its choice of storage; Myntos's own archive stays in R2 `eu` |
| Production data and analysis per driftsgren | Wish, later | Nothing | After dimensions |

## What the pilot's first files taught (2026-10-12)

Roald sent the farm's Tripletex SAF-T files for 2025 and 2026, Tripletex's
postings of two vouchers, and three bilag. They live in the gitignored
`regnaskaps_tests/` (`_reime_gard/` for the source material, a folder per
bilag, and `FASIT.md` with what each should produce, read from the documents
and checked against how Tripletex booked them). `regnaskaps_tests/saft_pilot.py`
takes the SAF-T file through Myntos end to end. What changed because of them:

- **The opening balance is read from the SAF-T file**
  (`POST …/inngaende-balanse/saft`, [integrations/saf-t.md](integrations/saf-t.md)).
  Tripletex's farm module writes NIBIO's department and product code after the
  four digits (`145120512` is 1451, jordbruk, kyr), so the four digits are the
  account and the rest is kept as the line's text. Read through Myntos and
  exported again, the farm's own 2026 SAF-T file validates against the 1.40
  XSD and every account's and party's opening balance equals Tripletex's.
- **Tripletex carries the balance sheet into the new year and leaves the
  result off equity.** The farm's 2026 opening did not sum to zero; the
  difference is 2025's result, and it goes on 2050.
- **Tripletex's groupings need the accountant.** Three kinds were wrong in the
  farm's file: the driftskreditt (2390) grouped as «Positiv egenkapital», the
  ENK's «Innskudd kontanter» (2055) on «Avsatt utbytte - IFRS» because the
  numbers match, and NIBIO's faste tekniske installasjoner (1180-1182) on post
  1180 «Investeringseiendommer», for the same reason. The import refuses the
  first two (class and IFRS) and proposes Myntos's default; the third looks
  right to any rule and is on the accountant's list in `FASIT.md`.
- **The farm's bank account is a driftskreditt on 2390.** Myntos books every
  payment on 1920, so the import says so and leaves the move to the
  accountant. If the opening balance moves it, the year's vouchers follow.
- **The parallel run starts from 1 January 2026.** The year so far comes from
  the same file (`POST …/saft/bilag`, page `/tidligere-program/`): 467
  Tripletex vouchers became 596, because Tripletex keeps an invoice and its
  payment in one voucher on two dates. Compared with the file afterwards,
  nothing differs in 93 accounts, 81 parties and 5 VAT terms, and Myntos's
  melding for each term Tripletex has filed equals the settlement Tripletex
  posted. From the export day the farm enters every bilag in both, Tripletex
  files every VAT return, and a fresh file is compared after each term.
- **The settlement's VAT is on the net** (rule 4 above), and the bilag reader
  had four bugs a farmer's paperwork found at once: a kontonummer printed after
  the amount was read as the amount, the register's name in front of an
  orgnr («Foretaksregisteret: NO…») was read as the sender's name, the dairy's
  «Avregningsoppgave» was not a settlement word, and «utbetalingsdato» was
  read as the date of the sale because it contains «betalingsdato».

## What the slaughter settlement taught (2026-10-13)

Roald sent Fatland Jæren's slakteavregning for September 2026 (seven young
bulls, 255 835,31 paid out) as a Tripletex print-out, with Landbrukets
Dataflyt's posting on page 1. Fatland's pages are `regnaskaps_tests/fatland/`;
the print-out is in `_reime_gard/`. It came back as a purchase of 255 kroner
with 913 251 342 kroner of VAT from «Foretagsreg», and every one of those was
the parser's:

- **A total wrapped in its cell.** «Total 255» over «835,31», and the same for
  «Sum brutto». An amount whose cell broke at the thousands space is put back
  on one line before anything is read.
- **The orgnr as VAT.** «Foretagsreg: NO 913 251 342 MVA» carries the word
  «MVA», and the spaced number is an amount to the pattern. «Foretaksregisteret»
  had been kept off VAT lines by name; now any valid orgnr with «MVA» behind
  it is never an amount, whatever the label says.
- **The register's label as the name**, again, in a new spelling; and the
  name in two lines, because the logo sets «FATLAND» over «Jæren AS».
- **No settlement word.** See rule 4: an «avregning» that pays out.
- **Varenr and skrottnr as an orgnr.** «162060 341» passed the check digit
  for two carcasses in seven. An orgnr's separators are now the same twice
  or absent.

How it is booked: every post carries the same 25 % («Moms» on «Sum brutto»),
so one `landbruk_slakt` entry for the amount paid out gives the settlement's
VAT to the øre, which is what Dataflyt posted (all on 3041). Splitting it is
also right, with the levy line moved to 3041. The slaughterhouse's supplements
(innmelding, puljetillegg, ny bruker) are price, on 3041 in NIBIO's coding too.
**«Kvalitetstilskudd storfe»** is a state tilskudd the slaughterhouse pays
with the price, at 25 %; NIBIO codes it 45004 to 3048 «Offentlige tilskudd
utbetalt via varemottaker», which Myntos does not have. It stays in the
slaughter line until someone asks for 3048: the parallel run compares
account by account with Tripletex, which has it on 3041.

## Not built yet

In the order the pilot will hit them: two VAT returns in one company; a manual
journal entry for the accountant's year-end adjustments (livestock, inventory,
accruals); the year-end livestock and feed count; fixed assets; the
næringsspesifikasjon with the farm cards; jordbrukskonto; dimensions for
driftsgrener; Dataflyt. Each is in [FEATURES.md](FEATURES.md) or
[ROADMAP.md](ROADMAP.md) Phase L.

## Sources

- Merverdiavgiftshåndboken, M-5-2.4 (næringsmidler; råmelk, slaktedyr, korn):
  <https://www.skatteetaten.no/rettskilder/type/handboker/merverdiavgiftshandboken/gjeldende/M-5/M-5-2/M-5-2.4/>
- Bokføringsforskriften § 5-2-1 (buyer-issued sales documents):
  <https://lovdata.no/forskrift/2004-12-01-1558/§5-2-1>
- Skatte-ABC J-2 (jordbruk), J-4 (jordbruksfradrag), S-17 (skogbruk):
  <https://www.skatteetaten.no/rettskilder/type/handboker/skatte-abc/gjeldende/>
- Skatteloven §§ 8-1, 8-2, 14-42, 14-43, 14-81, 14-82:
  <https://lovdata.no/dokument/NL/lov/1999-03-26-14>; 2025 and 2026 amounts in
  LOV-2024-12-20-86 and LOV-2025-12-22-119
- Skatteforvaltningsforskriften §§ 8-3-7, 8-3-10 (yearly primærnæring return):
  <https://lovdata.no/forskrift/2016-11-23-1360>
- NIBIO, Landbrukets kodesystem 1.3.6:
  <https://www.nibio.no/tema/landbruksokonomi/standarder-for-koder-i-landbruket>
- Tripletex landbruk: <https://hjelp.tripletex.no/hc/no/articles/20568206668433>;
  Fiken for bønder: <https://hjelp.fiken.no/fiken-for-boender>; Duett:
  <https://duett.no/bransjer/landbruk/>
- Landbruksdirektoratet, utbetaling 2026:
  <https://www.landbruksdirektoratet.no/nb/nyhetsrom/nyhetsarkiv/betaler-ut-naermere-18-7-milliarder-kroner-til-norske-bonder>
