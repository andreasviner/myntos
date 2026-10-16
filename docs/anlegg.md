# Anlegg: the fixed asset register and saldoavskrivning

Built 2026-10-15 (FEATURES #7, ROADMAP Phase L). Code in
`backend/src/myntos/domain/anlegg/`, routes in `api/v1/anlegg.py`, page
`/anlegg/`, skill `myntos-anlegg`, help in
`docs/agents/help/opening-balance-and-year-end/`. Migration `c7a4e2f91b38`.
Read this before touching any of them.

## What it is

A driftsmiddel is something kept for at least three years that cost 30 000
kroner or more (skatteloven § 14-40, raised from 15 000 by LOV-2023-12-20-98
from the income year 2024). Its cost goes onto a saldo for its saldogruppe
(§ 14-41: a to j), and each year at most the group's rate of the saldo is
deducted (§ 14-43). The register holds the assets; the saldo is worked out
from them; the year's depreciation is one voucher.

## The rules, and why

1. **The law is data, per year** (`rules.py`). Rates and limits are looked up
   by income year, from 2024. A year before it is refused rather than
   computed with figures nobody checked, and a year after `CONFIRMED_THROUGH`
   is computed with the latest figures and the review says so. The budget
   changes rates (group d had an extra 10 % in 2020 only); a constant would
   have been wrong the year it changed.
2. **The saldo is derived, never stored** (`saldo.py`). Opening + additions −
   grants − disposals = basis; basis − depreciation + income + what goes to
   the gevinst- og tapskonto = closing; rolled forward from the register's
   first year. A stored saldo would drift the first time an asset was
   corrected.
3. **The company's choice is stored beside the voucher.** Depreciation is
   «inntil» the rate (Skatte-ABC D-2-9.1), so next year's saldo depends on
   what was chosen. `fixed_asset_depreciation` keeps, per voucher, saldo and
   account, the basis it was chosen on (`grunnlag`) and what was taken. A row
   counts only while its voucher stands, so reversing the voucher takes the
   choice out of every later saldo with no second write to forget. The basis
   row is what lets the review say a posted year is out of date
   (`avskrivning_utdatert`) when the register changed under it, even where the
   voucher would come out the same.
4. **A year never posted took nothing.** Its saldo carries forward whole, and
   the next year's review says which years were never posted.
5. **The books follow the tax figures.** The voucher credits the asset account
   with the tax depreciation; there are no accumulated-depreciation accounts.
   Accepted practice for small companies (NOU 2014:13 p. 246 f.; Schwencke,
   Revisjon og Regnskap 5/2019: the saldo rates «vil som oftest tilfredsstille»
   the accounting requirement), and an ENK files from its tax figures anyway.
   Whether that holds for a given AS is a judgement for its accountant.
   Consequence: a sale in groups a–d and j reduces the saldo, so the books show
   no gain on it.
6. **Accounts**: 6000 buildings and installations (g–j), 6010 vehicles,
   ships, aircraft (c, e, f), 6015 machines and office machines (a, d), 6020
   goodwill and rights (b, linear); 3895 income from a negative saldo; 3880
   gain and 7880 loss when an asset in e–i leaves. 3880/7880 rather than
   3800/7800 because 3800 is «salgssum anleggsmidler» in other programs'
   charts, and a gain posted onto it would be counted twice. Created on first
   use (`chart.ANLEGG_ACCOUNTS`), never seeded.
7. **The gevinst- og tapskonto is never posted.** It is a tax balance (§ 14-45:
   at least 20 % of a gain balance is income, at most 20 % of a loss balance
   deducted, all of it under 30 000). Myntos applies the default each year and
   shows it; the næringsspesifikasjon reconciles it.
8. **Investeringstilskudd** reduce the cost (§ 14-42 (2) a), except
   distriktsrettet investeringsstøtte and Innovasjon Norge's tilskudd to faste
   anlegg i landbruket inside the area for distriktsrettet investeringsstøtte,
   which are tax free (§ 5-31 a) and leave the cost whole; sold within five
   years, such a grant counts as part of the price (§ 14-44 (1)). Which one a
   grant is, is the person's answer (`grant_reduces_cost`): the letter and the
   kommune decide it, not anything Myntos can read.
9. **Timing**: an asset enters on delivery (D-2-8.2.1); a full year's
   depreciation whatever the month (D-2-9.1); none in the year an e–i asset is
   realised (D-2-11); linear rights in twelfths the first year (D-4-2.2.3).
10. **Closed years are frozen.** A change to the register that would alter a
    closed year's saldo is refused (409 `register_year_closed`): that year's
    skattemelding was filed from it.
11. **Corrected only from its own routes.** The ledger's reversal refuses the
    `AVSKR` journal, and a year whose successor is posted cannot be reversed
    until the successor is (409 `later_year_depreciated`).
12. **The year-end review** (`closing.plan_closing`) adds the merknader
    `avskrivning_ikke_fort` and `avskrivning_utdatert`. Merknader, not feil: a
    company may keep its assets elsewhere.

`saldo.specification(db, *, company, year)` is what the næringsspesifikasjon
(FEATURES #2) reads. Keep its shape.

## What is not built

- A saldo per næring and per kommune (§ 14-41 (4)): Myntos keeps one per group
  per company.
- Monthly booking, accounting depreciation that differs from the tax figures,
  write-down (nedskrivning), and the § 14-44 (1) choice to take a sale price
  to income at once.
- Choosing more or less than the default on the gevinst- og tapskonto.
- Anything before 2024.
