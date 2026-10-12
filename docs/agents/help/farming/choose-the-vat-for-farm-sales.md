---
title: Choose the VAT for farm sales
description: Which VAT rate a Norwegian farm's sales and purchases carry in Myntos - milk to the dairy, slaughter animals, live animals and grain at 25 %, food-quality potatoes, vegetables, fruit, berries and eggs at 15 %, feed at 25 %, subsidies and land rent without VAT, hunting rights at 25 % - and which VAT return a farm files. Use when someone asks what VAT milk, meat, grain, hay, jordleie or tilskudd have, or which mva-kode to choose on a farm.
pages: /kontantstrom/
skill: myntos-landbruk
---

# Choose the VAT for farm sales

The rates on a farm are not the rates in a shop. Milk is food on a shop shelf
(15 %), but milk delivered to the dairy is not food yet, so the farmer sells it
at **25 %**. The same goes for slaughter animals, live animals and grain. The
farm types in Myntos propose the right code, and their note under «Hva gjaldt
det?» says why; change «Mva-kode» only when the settlement or the bill says
something else.

## They might ask

- «Hvilken mva er det på melk til Tine?»
- «Er det 15 eller 25 % mva på slakt?»
- «Har jordleie mva?»
- «Er det mva på produksjonstilskudd?»

## Before they start

- The farm types are there when the farming add-on is on:
  [Turn on farming](https://myntos.no/llms/help/farming/turn-on-farming.md).

## Steps

1. Open «Betaling» (`https://myntos.no/kontantstrom/`) and choose the type
   under «Hva gjaldt det?». «Mva-kode» fills in with the type's code.
2. Compare it with the table below and with the bilag. Change «Mva-kode» only
   if the bilag says otherwise.

| Sold or bought | Rate | Mva-kode |
| --- | --- | --- |
| Milk to the dairy, slaughter animals, live animals, grain and seed | 25 % | 3 |
| Hay, straw and silage sold; wool and skins; timber, firewood, chips | 25 % | 3 |
| Contract work for others (leiekjøring), hunting and fishing rights, renting out milk quota | 25 % | 3 |
| Potatoes, vegetables, fruit, berries and eggs sold as food | 15 % | 31 |
| Seed potatoes, potatoes for feed, hatching eggs | 25 % | 3 |
| Renting out land or grazing (jordleie), unless voluntarily registered | exempt | 6 |
| Produksjonstilskudd, avløsertilskudd, compensation for crop damage | none | 0 |
| Feed, fertiliser, seed, vet, medicine, insemination bought | 25 % deductible | 1 |
| Land or grazing rented | none | 0 |

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «… så mva-kode 3 kan ikke brukes …» | The company was not VAT-registered on that date | Use code 0; an unregistered farm charges and deducts no VAT |

## Good to know

- A farm in primærnæring files its VAT return (mva-melding) once a year, by
  10 April the year after. Choose «Primærnæring (jord, skog, fiske)» under
  «Meldingstype» on the Mva-melding page:
  [Change the VAT period or category](https://myntos.no/llms/help/vat/change-the-vat-period-or-category.md).
- Other business up to 30 000 kr a year (ex. VAT) can go in the same yearly
  return. Above that it needs a return of its own, which Myntos cannot file
  beside the primærnæring one yet.

## Related

- [Book a settlement from the dairy or slaughterhouse](https://myntos.no/llms/help/farming/book-a-settlement.md)
- [Book production subsidies](https://myntos.no/llms/help/farming/book-production-subsidies.md)
- An agent with a key can do this through the API:
  [myntos-landbruk](https://myntos.no/llms/skills/myntos-landbruk.md)
