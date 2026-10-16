---
title: Record private withdrawals from the farm
description: How a farm lists what the household took from the farm during the year (milk, potatoes, meat, eggs, firewood, a horse's stall and hay) on the Private uttak page in Myntos, how it is valued by the takseringsregler norms, and when it carries VAT. Use when someone asks about privat uttak, uttak av melk og poteter, ved til eget bruk or kjøtt til fryseren.
pages: /private-uttak/ /kontoplan/
skill: myntos-landbruk-arsoppgjor
---

# Record private withdrawals from the farm

What the household takes from the farm is income to the farm and a
withdrawal by the owner, and bokføringsforskriften asks for a list of it.
Myntos values the list, decides the VAT, and posts it as one voucher against
the privatkonto 2060. A posted list cannot be edited, only replaced.

## They might ask

- «Hvordan fører jeg privat uttak av melk og poteter?»
- «Vi tok en gris til eget bruk, hvordan fører jeg det?»
- «Er det mva på privat uttak på gården?»
- "How do I record farm produce used by my household in Myntos?"

## Before they start

- The company needs the farming add-on «Landbruk»
  ([Turn on farming](https://myntos.no/llms/help/farming/turn-on-farming.md)),
  and it must be an enkeltpersonforetak. An AS is refused: its owner's
  withdrawals are salary, a loan or a dividend.
- The role «Medlem» or more.
- How many are in the household, and for meat, eggs and vegetables what they
  would have cost to buy.

## Steps

1. Open «Kontoplan», and in the «Landbruk» card choose «Private uttak»
   (`https://myntos.no/private-uttak/`). «Årsavslutning» links to it too.
2. Check «Regnskapsår», and «Dato for bilaget» (31 December unless they have
   reason to choose another; the date decides the VAT term).
3. One line per product: choose «Produkt» and fill in «Mengde».
   - «Melk» and «Poteter» are per household member a year: 1 per person, 0,5
     for someone over 70, and for potatoes 0,5 for a child under 10.
   - «Ved fra egen skog» in loose cubic metres.
   - «Kjøtt og slakt», «Egg» and «Grønnsaker, frukt og bær» have no norm:
     fill in «Verdi per enhet» with what it would have cost.
4. Press «Kontroller». «Gjennomgå før føring» shows each line's value and
   «Mva»: «Fritatt (primærnæring, årlig melding)», «Avgiftspliktig» or «Ikke
   mva-registrert».
5. Press «Før uttakene». This posts the voucher and cannot be undone. To
   change it later, press «Rett uttakene»; to remove it, «Trekk dem tilbake».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Et aksjeselskap har ingen privatkonto …» | Private uttak is for an ENK | The accountant books it as salary, loan or dividend |
| «… har ingen norm, og verdien er det varen ville fått ved salg …» | Meat, eggs and vegetables are valued at what they would fetch | Fill in «Verdi per enhet» |
| «Skattedirektoratet har ikke publisert normene …» | The norms come in December | Wait, or record only products without a norm now |

## Good to know

- A farm on the yearly primærnæring VAT return takes its own produce out VAT
  free (merverdiavgiftsloven § 6-20). On a two-monthly or alminnelig return the
  uttak is taxed as a sale: 15 % for food, 25 % for raw milk, firewood and a
  horse's hay.
- Kår (føderåd) to the previous owner is not here; it goes through the
  a-melding. Gifts of produce are not private use either; they are booked on
  «Mva-justeringer».

## Related

- [Count livestock and stock at year end](https://myntos.no/llms/help/farming/count-livestock-and-stock.md)
- [Close a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/close-a-financial-year.md)
- An agent with a key can do this through the API:
  [myntos-landbruk-arsoppgjor](https://myntos.no/llms/skills/myntos-landbruk-arsoppgjor.md)
