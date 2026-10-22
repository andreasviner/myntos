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
- The department is a **dimension**, not part of the account: NIBIO's
  avdelinger (10 Finans, 20 Jordbruk, 30 Skogbruk …) are offered as values of
  «Avdeling» on `/avdelinger/`, beside the driftsgrener (rule 7). Splitting the
  næringsspesifikasjon by næring from it is not built; see
  [Not built yet](#not-built-yet).

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
VAT.

An investeringstilskudd is not income in the year either, but which of two
rules applies depends on the grant, and this paragraph used to give only the
first (corrected 2026-10-15, read in Lovdata):

- **The rule: it reduces the asset's cost** (skatteloven § 14-42 (2) a, a grant
  from the state, a kommune or a publicly supported company), so the saldo and
  every later year's depreciation are smaller.
- **The exception: tax free, and the cost stays whole.** Inside the
  geographic area for distriktsrettet investeringsstøtte, two grants are not
  income (§ 5-31 a) and are *not* deducted from the cost (§ 14-42 (2) a, last
  sentences): distriktsrettet investeringsstøtte, and Innovasjon Norge's
  tilskudd to faste anlegg og tilhørende produksjonsutstyr under forskrift
  2014-12-19-1816 (investering og bedriftsutvikling i landbruket). Sold within
  five years, the grant counts as part of the price (§ 14-44 (1)).

The grant letter and the farm's kommune decide which it is, so Myntos asks:
the asset in the anleggsregister has a grant and a tick for «Tilskuddet er
skattefritt og trekkes ikke fra kostprisen» ([anlegg.md](anlegg.md)). The note
on «Annet tilskudd» says the grant is not booked as income there.

### 6. The VAT return

Primærnæring files once a year, due 10 April (skatteforvaltningsforskriften
§§ 8-3-7, 8-3-10). Built before the add-on (`domain/mva/periods.py`, setting
«Primærnæring (jord, skog, fiske)»). Binæring up to 30 000 kr ex. VAT may go in
the same return (mval § 15-1 (1)); above that the company files an alminnelig
return as well. Myntos files both since 2026-10-15: the card «Den andre
meldingen» on `/mva/` sets up the second, and a line goes in the melding of its
**account** (the other business keeps accounts of its own, as NIBIO's
department 60 annen næring already has it). See
[integrations/mva.md](integrations/mva.md) «Two returns».

**Known limit: a cost both businesses use.** How the input VAT on diesel, a
telephone or a machine shared by the farm and the other business is divided
between the two meldinger is not settled (nothing found from Skatteetaten on
splitting between two meldinger of one company; only the proportional split
for fellesanskaffelser between taxable and exempt use). Myntos applies no
rule: the whole line goes to the melding of the account it is booked on, and an
account in neither list goes to the primærnæring melding. The person decides,
with their accountant, by the account or by two lines on two accounts; the page
says so where the accounts are chosen. Settle this with Skatteetaten before
Myntos proposes any split.

Terms the previous program filed are marked «Sendt fra et annet program» on
`/mva/` (proposed from Tripletex's own settlement vouchers in the SAF-T
import), so the parallel run never sends a term twice
([integrations/mva.md](integrations/mva.md) «Filed elsewhere»).

### 7. Driftsgrener are a dimension everyone has, with the farm's codes

A farm wants its result per driftsgren: the milk apart from the sheep apart
from the forest, as Duett, Tripletex and PowerOffice give it. That is not a
farm feature. Dimensions (avdelinger, prosjekter, a sorting of one's own) are
for every company (`domain/ledger/dimensions.py`, `/avdelinger/`, the skill
`myntos-avdelinger`), and the add-on only offers **NIBIO's codes** to start
from (`domain/ledger/driftsgrener.py`, `GET …/dimensjoner/forslag`, empty
without the add-on): Landbrukets kodesystem 1.3.6's driftsgreinkoder at the
level a farm reports on (6110 Melkeproduksjon storfe, 6300 Saueproduksjon,
7000 Skogbruksproduksjon …; not all 373, which NIBIO itself calls «i overkant
detaljerte»; no pelsdyr, banned since 2025-02-01) and its avdelinger. The
module docstring says which codes were taken and why.

A line carries one value per dimension for its whole amount, chosen on the
register and per line of a settlement, so the milk goes to 6110 and the feed
deducted from it to whichever driftsgren ate it. The result per driftsgren,
per month, excludes the opening balance and the year-end closing, and the
lines nobody marked are a row of their own («Uten driftsgren») so the rows add
up to the year's result.

From a Tripletex file, the line's own «Avdeling» comes along, and in books with
the add-on so does the department and product written into a six- or
nine-digit account id (`145120512`: avdeling 20, Tripletex's product 512 as a
«Produkt» dimension). Tripletex's «Bilagsart» and «Ansatte» (people's names)
stay behind. See [integrations/saf-t.md](integrations/saf-t.md).

### 8. The year end: the count and private uttak

Two lists every farm hands its accountant in January, each stored as it was
posted (`domain/arsoppgjor/landbruk_models.py`, never updated; a correction is
a new list naming the one it replaces) behind **one voucher** in a journal of
its own. The register's reversal refuses both journals: reversed from there,
the voucher would be gone while its list still claimed to stand.

**The count** (`domain/arsoppgjor/buskap.py`, `/buskap/`, journal `TELLING`,
dated 31 December). Each line is a kind (`buskap` on 1450, the farm's own feed
and grain `egenprodusert` on 1490, bought feed, fertiliser and seed `innkjopt`
on 1400), a category from the year's takseringsregler, a quantity and a
valuation. The voucher brings each account to the counted value against 4690,
4290 or 4090, compared with the books on 31 December *without* this year's
count, so a correction's review shows the change from the books. The rules,
read in FOR-2025-12-01-2404 on Lovdata 2026-10-15:

- **Adults already adult on 1 January and still there: the average of all
  adult animals of the species in the opening balance**, rounded to 10 kroner
  (kapittel 5 § 5-2 annet ledd and femte ledd). Computed, never typed. Adult is
  storfe over 2 years, avlsgriser over ½ year, geiter over 9 months, sauer over
  a year; last year's «tilført i året» rows count as adults in this year's
  opening.
- **New animals of own breeding, and animals bought to renew or expand the
  herd: the year's enhetspriser** (§ 3-1-4 a, § 5-2 tredje to sjette ledd);
  the young at the regulation's percentages, which the published figures
  already are. Verpehøner and kyllinger at slakteverdi (FSFIN § 14-5-3).
- **Fôringsdyr bought for sale, chicks for fattening, bought stock: kostpris**
  (innkjøpspris, plus feed to the turn of the year for animals; plus frakt
  for bought stock, § 5-1-2).
- **Grain, potatoes, hay, silage, other feed: tilvirkningsverdi per kg or FEm**
  (§ 3-1-4 b-d), «dersom ikke noe annet kan dokumenteres»; a documented value
  is `verdsatt`, with the reason.
- **The opening is last year's count, unchanged** («Den årlige sluttbalansen
  … blir uten endringer å regne som åpningsbalanse», § 5-2 last paragraph).
  Copied from Myntos's own count when there is one; typed in the first year.
- **An account in the ranges (1400-1459, 1490) with a balance and no line is
  refused.** Otherwise the pilot's Tripletex opening on 1451 would stand beside
  a count on 1450, and the herd would be on the balance sheet twice. Counting
  0 writes it down.
- **A year without published rates refuses `normalverdi`** (`ingen_satser`);
  the average and kostpris need none. Rates are data, one entry per year in
  `domain/arsoppgjor/takseringsregler.py` with its Lovdata address: 2026 is
  added when Skattedirektoratet publishes it in December, and nothing else
  changes.

`buskap.year_count()` is what the ENK's næringsspesifikasjon reads: the
standing count with its opening and closing totals per kind, under the
element names of `SpesifikasjonAvSkattemessigVarelager` in Skatteetaten's
næringsspesifikasjon XSD v6/v7 (`buskap`,
`selvprodusertVareBenyttetIEgenProduksjon`, `raavareOgInnkjoeptHalvfabrikata`).
Årsavslutning gives a merknad (`ingen_telling_buskap`) for a farm year with no
count; not a feil, because a farm that sold its last animal has nothing to count.

**Private uttak** (`domain/arsoppgjor/uttak.py`, `/private-uttak/`, journal
`UTTAK`): 2060 debited, NIBIO's uttak accounts 3161-3166 credited, 2700 when
there is VAT. Bokføringsforskriften § 3-1 første ledd nr. 5 asks for the list.
Values (FOR-2025-12-01-2404): milk 410 and potatoes 130 kr per household
member a year, halved for those over 70 and, for potatoes, children under 10
(§ 3-2-3); firewood 750 kr per løs m³, flis 270 (§ 3-2-6); stall and hay for a
horse used outside the farm 21 700 / 14 900 / 9 800 (§ 3-2-7); everything else
at omsetningsverdi (§ 3-2-2). The VAT, per line, by the melding the line falls
in (`naering.of_line`):

- **Primærnæring filed once a year: fritatt, code 5.** Merverdiavgiftsloven
  § 6-20 første ledd exempts uttak «fra eget fiske, skogbruk eller jordbruk med
  binæringer … dersom produktene skal brukes privat», and FMVA § 6-20-1 første
  ledd limits it to those who «skal levere skattemelding en gang i året etter
  skatteforvaltningsforskriften § 8-3-7 første ledd». Both read on Lovdata
  2026-10-15. A primærnæring melding on two-monthly terms (§ 8-3-8) is
  therefore *not* exempt. The `uttak` spesifikasjon is not written on code 5,
  because `SPESIFIKASJON_CODES` does not list it yet (mva/codes.py says why);
  code 5 is itself «omsetning og uttak fritatt».
- **Otherwise, registered: taxed as a sale** (§ 3-21, basis omsetningsverdi
  § 4-9), code 31 at 15 % for food and 3 at 25 % for raw milk, firewood and a
  horse's hay, spesifikasjon `uttak`. A merknad says the norms are the tax
  rules' values and the VAT basis is what the produce would fetch.
- **Not registered: no code.**

An AS is refused (its owner's uttak is salary, a loan or a dividend); ANS and
DA get a merknad. Kår (føderåd) is a benefit in kind through the a-melding and
is not here; gifts of produce are not private use and go on
`/mva-justeringer/`.

### Where Myntos's farm accounts differ from NIBIO's

Found reading the pilot's Tripletex chart (NIBIO's numbers) while building the
count. **None is changed**: companies already have these accounts seeded, and
renumbering an account with postings would move history. Written down so an
import or a farm accountant is not surprised.

| Account | NIBIO (jordbruk) | Myntos | Consequence |
| --- | --- | --- | --- |
| 1451-1458 | Buskap per species | 1450 Buskap, one account | The count accepts 1451-1458 as livestock accounts; a pilot's opening on 1451 is counted there |
| 1490 | «Biologiske eiendeler» (post 1490); own feed is 1447 (post 1401), own grain 1406 | Egenproduserte varer til eget bruk (post 1401) | The count treats 1440-1449 and 1490 as own produce. An import carrying NIBIO's 1490 lands on Myntos's own-feed account |
| 3094 | Høy, egenprodusert | Skogsvirke (timber, ved) | Rule 2: NIBIO's forestry department reuses 3010/3030 |
| 3910 | Leieverdi kårbolig | Skogfond frigitt | Kår is not built; a NIBIO 3910 from an import needs the accountant |
| 4690 grouping | Post 4295, endring i beholdning av egenproduserte varer | Post 4005, varekostnad | The total is the same; the split between the two posts in the næringsspesifikasjon differs. A person may move it on /kontoplan/ |
| 3161-3166 grouping | Tripletex puts uttak on 3100 | 3100 «Salg og uttak med 0 prosent mva» | Right for the yearly primærnæring melding (§ 6-20 is a fritak); a farm on the alminnelig melding moves them to 3000 |

The new accounts (2026-10-15): 4090 and 4290 for the count's other two
changes, and NIBIO's uttak accounts 3161-3164 and 3166, so the add-on has 69.

## What a farm needs and where Myntos stands

Roald's list from Reime Gard, with the competitors' answer beside it. **Law**
means the farm cannot do without it; **wish** means it can, with more work.

| Area | Law or wish | Myntos 2026-10-12 | Missing |
| --- | --- | --- | --- |
| Farm chart of accounts | Wish (the law asks for bookkeeping, not NIBIO) | **Built**: 69 NIBIO accounts, grouped | Product codes and quantities per line |
| Correct VAT | Law | **Built**: farm rates as defaults; primærnæring yearly return; two returns in one company, by account (2026-10-15); terms filed from the previous program marked | A rule for input VAT on costs shared by both returns (not settled; the person chooses the account) |
| Milk, slaughter, grain, timber income | Law (documented sales) | **Built**: settlements as composite entries, read as sales | Import from Landbrukets Dataflyt ([integrations/landbrukets-dataflyt.md](integrations/landbrukets-dataflyt.md)) |
| Produksjons- and avløsertilskudd | Law (income, payment year) | **Built**: a category per tilskudd, bunnfradrag apart | Tilskudd settlement through Dataflyt |
| Livestock valuation | Law (skatteloven § 8-1 (4), FSFIN § 14-5-3, takseringsregler) | **Built** (rule 8): the year-end count on `/buskap/`, the average for adults, the year's enhetspriser, one voucher | 2026's rates (December); `buskap` in the næringsspesifikasjon (reads `buskap.year_count`) |
| Feed and other inventory | Law (varelager; post 1401 is not formue) | **Built** (rule 8): in the same count, own feed and grain at the takseringsregler values on 1490, bought stock at cost on 1400 | |
| Machinery and depreciation | Law (saldo, skatteloven § 14-40 ff.) | **Built** 2026-10-15: the anleggsregister and the year's saldoavskrivning on `/anlegg/` ([anlegg.md](anlegg.md)) | A saldo per næring and kommune; accounting depreciation that differs from tax |
| Buildings and investments | Law (saldogruppe h, husdyrbygg 6 %, § 14-43 (3); grants reduce cost unless tax free, §§ 14-42, 5-31 a) | **Built** 2026-10-15: group h with husdyrbygg 6 %, kort levetid 10 %, frukt- and bærfelt; both kinds of investeringstilskudd | Whether a building is a husdyrbygg (half its floor for animals) is the person's answer |
| Milk quota | Law (capitalised, not depreciated) | **Built**: 1010, 3675, 6475 | Gain on sale (gevinst- og tapskonto) |
| Jordbruksfradrag | Law (personal skattemelding; Skatteetaten computes it from the basis) | Nothing | The basis per FSFIN § 8-1-11, and an estimate (2025: 95 800 + 38 %, max 200 850; 2026: 99 600, max 208 900) |
| Jordbrukskonto (§ 14-82, from 2025) and tømmerkonto (§ 14-81) | Law once chosen; a deficit must go on | Nothing | Tax-only balances per kommune: 85 % back in 2025, 80 % from 2026; 20 % for tømmerkonto; ENK only |
| Næringsspesifikasjon and skattemelding for the ENK | Law (only from a program) | Groupings only | FEATURES #2, with the farm cards (jordbrukskonto, skogOgToemmerkonto, buskap) |
| Driftsgrenregnskap (melk, storfe, sau) | Wish | **Built** (2026-10-15): dimensions with NIBIO's driftsgrener and avdelinger, the result per driftsgren by month, carried in and out of SAF-T (rule 7) | Quantities (litres, kg, head) per line |
| Private uttak and kår | Law (a list of uttak, bokføringsforskriften § 3-1 nr. 5; kår via a-melding, § 6-52) | **Built** for uttak (rule 8): `/private-uttak/` at the norms, VAT free on the yearly primærnæring melding | Kår needs payroll (FEATURES #14) |
| EHF in through Peppol | Law from 2030, expected now | Nothing | FEATURES #13; access point through invoice.be |
| SAF-T 1.40 | Law on request | **Built**, and the opening balance and every year's vouchers are read from the old program's SAF-T files (Tripletex's farm accounts included), with a comparison for the parallel run; the documents behind them from Tripletex's printout of the vouchers (2026-10-22, all 51 of the pilot's September matched) | The documents from programs other than Tripletex (FEATURES #17) |
| Bank reconciliation | Wish | **Built** (camt.053) | Live bank feed (FEATURES #9) |
| Approval and payment | Wish | Nothing | FEATURES #11, #36 |
| Backup to the farm's own OneDrive or SharePoint | Wish | Nothing | Full export (FEATURES #44). The farm's own Microsoft 365 is its choice of storage; Myntos's own archive stays in R2 `eu` |
| Production data and analysis per driftsgren | Wish, later | The result per driftsgren only | Quantities, and key figures per cow or per decare, on the dimensions |

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
  account and the rest is kept as the line's text in the opening balance; the
  year's vouchers carry it as an avdeling and a product since 2026-10-15
  (rule 7). Read through Myntos and
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
  new payment on 1920, and the overview counted money only there, so after
  the server-side import the overview read «Inn i år 0,00». Since 2026-10-15
  the import marks 2390 as a bank account (its text carries the kontonummer
  the file's header names), and the overview counts it; nothing is moved. The
  move to 1920 is still the accountant's call: if the opening balance moves
  it, the year's vouchers follow. The farm's books already in production are
  marked by hand on `/kontoplan/` («Merk som bankkonto»).
- **The VAT card showed the year, not what was owed.** With four terms filed
  and settled in Tripletex, the year's output less input VAT read as a large
  amount «til gode» while the open term was to be paid. The card is now the
  balance on 2700, 2710 and 2740 across every year: open terms plus filed
  terms not yet paid.
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

In the order the pilot will hit them: the split of input VAT on costs shared
by the two VAT returns (rule 6; the returns themselves are built); the 2026
takseringsregler as data when they are published (rule 8; the count is built);
fixed assets; the
næringsspesifikasjon with the farm cards (split by næring from the avdeling);
jordbrukskonto; quantities per driftsgren; Dataflyt. Each is in [FEATURES.md](FEATURES.md) or
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
- Takseringsreglene for 2025, FOR-2025-12-01-2404 (§§ 3-1-4, 3-2-2 to 3-2-7,
  kapittel 5), read 2026-10-15:
  <https://lovdata.no/dokument/SF/forskrift/2025-12-01-2404>
- Merverdiavgiftsloven §§ 3-21, 4-9, 5-2, 6-20:
  <https://lovdata.no/dokument/NL/lov/2009-06-19-58>; FMVA § 6-20-1:
  <https://lovdata.no/dokument/SF/forskrift/2009-12-15-1540>
- Bokføringsforskriften § 3-1 første ledd nr. 5 (the list of uttak):
  <https://lovdata.no/forskrift/2004-12-01-1558/§3-1>
- Skatteetaten, næringsspesifikasjon XSD v7, `SpesifikasjonAvSkattemessigVarelager`:
  <https://github.com/Skatteetaten/skattemeldingen/tree/master/src/resources/xsd>
- NIBIO, Landbrukets kodesystem 1.3.6:
  <https://www.nibio.no/tema/landbruksokonomi/standarder-for-koder-i-landbruket>
- Tripletex landbruk: <https://hjelp.tripletex.no/hc/no/articles/20568206668433>;
  Fiken for bønder: <https://hjelp.fiken.no/fiken-for-boender>; Duett:
  <https://duett.no/bransjer/landbruk/>
- Landbruksdirektoratet, utbetaling 2026:
  <https://www.landbruksdirektoratet.no/nb/nyhetsrom/nyhetsarkiv/betaler-ut-naermere-18-7-milliarder-kroner-til-norske-bonder>
