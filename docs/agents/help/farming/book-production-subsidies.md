---
title: Book production subsidies
description: How a farmer registers produksjonstilskudd, avløsertilskudd and other farm subsidies from Landbruksdirektoratet on the Betaling page in Myntos - which year they belong to, that they carry no VAT, and how to split the payment by subsidy type with the bunnfradrag as its own line. Use when someone asks how to book tilskudd, produksjonstilskudd or avløsertilskudd.
pages: /kontantstrom/
skill: myntos-landbruk
---

# Book production subsidies

Produksjonstilskudd and avløsertilskudd from Landbruksdirektoratet are not
sales: they carry no VAT, and they are income in the year they are **paid
out**. The subsidies for 2025, paid in February 2026, are 2026 income. The
letter (tilskuddsbrev) splits the payment by type of subsidy, and Myntos has a
type for each, so the books show what the farm received for what.

## They might ask

- «Hvordan fører jeg produksjonstilskuddet?»
- «Hvilket år hører tilskuddet hjemme i?»
- «Er det mva på tilskudd?»
- «Hvordan fører jeg bunnfradraget?»

## Before they start

- The farming add-on must be on: [Turn on farming](https://myntos.no/llms/help/farming/turn-on-farming.md).
- They need the role «Medlem», «Administrator» or «Eier».
- The tilskuddsbrev, and the payment in the bank.

## Steps

1. Open «Betaling» in the top menu (`https://myntos.no/kontantstrom/`).
2. Upload the tilskuddsbrev, or click «Ingen bilag? Fyll inn manuelt». Choose
   «Penger inn», and set «Dato» to the day the money arrived.
3. With one type of subsidy, choose it under «Hva gjaldt det?»
   («Produksjonstilskudd: areal», «Produksjonstilskudd: husdyr»,
   «Avløsertilskudd» …) and press «Registrer betaling».
4. With several, click «Del opp i flere linjer» and add one line per type on
   the letter: «Produksjonstilskudd: areal», «Produksjonstilskudd:
   kulturlandskap», «Produksjonstilskudd: husdyr», «Produksjonstilskudd:
   driftstilskudd», «Produksjonstilskudd: dyr på beite», «Avløsertilskudd»,
   «Miljøtilskudd (RMP, økologisk)», «Annet tilskudd».
5. Add the bunnfradrag, or any avkorting, as its own line: «Bunnfradrag eller
   avkorting i tilskudd».
6. Check that «Til sammen … inn på kontoen» matches the bank, and press
   «Registrer betaling».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No subsidy types | The farming add-on is off | [Turn on farming](https://myntos.no/llms/help/farming/turn-on-farming.md) |
| «Mva-kode» shows something other than 0 | Changed by hand | Subsidies carry no VAT: choose «0: Ingen merverdiavgiftsbehandling» |

## Good to know

- Distriktstilskudd paid by the dairy or the slaughterhouse with the price is
  not on this letter: it is a line of the settlement, with VAT, as
  [Book a settlement](https://myntos.no/llms/help/farming/book-a-settlement.md)
  describes.
- An investment grant from Innovasjon Norge is not income here: it reduces
  what the thing it paid for cost.
- Subsidies count in the basis for the jordbruksfradrag, which the tax
  return works out; Myntos does not file the tax return yet.

## Related

- [Split a payment into several lines](https://myntos.no/llms/help/payments/split-a-payment-into-lines.md)
- [Book a settlement from the dairy or slaughterhouse](https://myntos.no/llms/help/farming/book-a-settlement.md)
- An agent with a key can do this through the API:
  [myntos-landbruk](https://myntos.no/llms/skills/myntos-landbruk.md)
