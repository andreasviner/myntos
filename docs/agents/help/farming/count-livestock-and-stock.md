---
title: Count livestock and stock at year end
description: How a farm counts its animals, feed and grain on 31 December on the Buskap og lager page in Myntos, how the values are set by Skattedirektoratet's takseringsregler (normalverdi, the average for adult animals, kostpris), and how the change is posted as one voucher. Use when someone with a farm asks about dyretelling, varetelling, buskap i balansen or beholdningsendring.
pages: /buskap/ /kontoplan/
skill: myntos-landbruk-arsoppgjor
---

# Count livestock and stock at year end

A farm's animals, the hay, silage and grain grown for its own animals, and the
feed, fertiliser and seed bought and not yet used belong on the balance sheet
at what they are worth on 31 December. The farm counts them, Myntos values the
count by Skattedirektoratet's takseringsregler for the year, and posts the
change since last year as one voucher dated 31 December. A posted count cannot
be edited, only replaced (see
[Correct the livestock count](https://myntos.no/llms/help/farming/correct-the-livestock-count.md)).

## They might ask

- «Hvordan fører jeg dyretellingen i Myntos?»
- «Hvor fører jeg buskapen ved årsoppgjøret?»
- «Hva er normalverdien for kyr i år?»
- «Må jeg telle fôret på lageret?»
- "How do I record the year-end livestock count in Myntos?"

## Before they start

- The company needs the farming add-on, «Landbruk»
  ([Turn on farming](https://myntos.no/llms/help/farming/turn-on-farming.md)).
  Without it the page says «Landbruk er ikke slått på».
- Anyone can read the page; counting needs the role «Medlem» or more.
- The first year in Myntos they need last year's list: how many animals of
  each kind, and the value each stood at. From the second year Myntos takes
  it from last year's count.
- Skattedirektoratet publishes the year's values in December. Before that the
  page says the rules are not published, and lines at «Normalverdi» are refused.

## Steps

1. Open «Kontoplan», and in the «Landbruk» card choose «Buskap og lager»
   (`https://myntos.no/buskap/`). «Årsavslutning» links to it too.
2. Check «Regnskapsår» at the top. It opens on the year waiting for its count.
3. Under «Inngående 1. januar»: the first year, add a line per kind of animal
   or stock as it stood on last year's list, with «Antall» and «Verdi per
   enhet». Later years show last year's count and cannot be changed here.
4. Under «Telt 31. desember», one line per kind: choose «Hva» (grouped under
   «Buskap», «Egenproduserte varer (fôr, korn, poteter)» and «Innkjøpt fôr,
   gjødsel og såvarer», each with «Annet»), and fill in «Antall».
   - Cows, breeding pigs, sheep and goats that were there on 1 January: the
     categories that say «fra inngående balanse». Myntos gives them the average
     value of the adult animals in the opening lines.
   - New animals, calves and young stock, grain, potatoes and silage: the
     category's own value is used. Leave «Verdi per enhet» empty.
   - Bought feed, fertiliser and «Fôringsdyr kjøpt for salg»: fill in «Verdi
     per enhet» with what it cost.
   - «Verdsetting» can stay on «Etter reglene». «Oppgitt verdi» is for a value
     they can document, with the reason in «Beskrivelse».
   - If the page lists an account that «må telles», count what is on it,
     with 0 if it is gone.
5. Press «Kontroller». «Gjennomgå før føring» shows each line valued, «Per
   konto» (what the books say, what was counted and the change) and «Bilaget».
6. Read the red findings (they stop the button) and the grey ones, then press
   «Før tellingen». This posts the voucher and cannot be undone.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Skattedirektoratet har ikke publisert takseringsreglene …» | The year's values come in December | Wait, or count only what is valued at cost and the average now |
| «Konto … har en saldo …, men ingen linje i tellingen» | Something is on the balance sheet that was not counted | Add a line for it, with 0 if it is gone |
| «Inngående balanse har ingen voksne dyr av samme art …» | The average needs last year's adult animals | Add them under «Inngående 1. januar» |
| «En oppgitt verdi trenger en begrunnelse …» | A stated value must say why | Write the reason in «Beskrivelse» |
| No «Før tellingen» button at all | They are «Leser», or the year is closed | Ask an owner, or reopen the year on «Årsavslutning» |

## Good to know

- The voucher moves 1450 Buskap against 4690, 1490 (own feed and grain)
  against 4290, and 1400 (bought stock) against 4090. A herd that grew lowers
  the year's costs; one that shrank raises them.
- Adult animals keep the average value year after year, so the herd's value
  does not jump with each year's rates (takseringsreglene § 5-2).
- The count feeds the næringsspesifikasjon's «buskap» post in the ENK's
  skattemelding.

## Related

- [Correct the livestock count](https://myntos.no/llms/help/farming/correct-the-livestock-count.md)
- [Record private withdrawals](https://myntos.no/llms/help/farming/record-private-withdrawals.md)
- [Close a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/close-a-financial-year.md)
- An agent with a key can do this through the API:
  [myntos-landbruk-arsoppgjor](https://myntos.no/llms/skills/myntos-landbruk-arsoppgjor.md)
