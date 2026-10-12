---
title: Repay the VAT deduction on a car or property
description: How a person records that the VAT deducted on a passenger car or a property must be paid back in full (tilbakeføring av inngående merverdiavgift), on the page «Uttak og justering» in Myntos. Use when someone asks how to record tilbakeføring of mva on a car or building, for example after the car was taken into private use or the property changed use.
pages: /mva-justeringer/
skill: myntos-mva-justering
---

# Repay the VAT deduction on a car or property

In some cases the VAT deducted on a passenger car or a property has to be
paid back in full rather than adjusted year by year («tilbakeføring»,
merverdiavgiftsloven kapittel 9). Myntos records the amount as owed and
reports it on its own line in the MVA-melding. Myntos does not work out the
figure. The entry is posted at once and cannot be edited afterwards, only
reversed.

## They might ask

- «Hvordan tilbakefører jeg mva-fradraget på bilen?»
- «Bilen er tatt over privat, må jeg betale tilbake mvaen?»
- «Hvordan fører jeg tilbakeføring av inngående mva i Myntos?»
- "How do I repay the VAT deduction on a company car in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- The amount to pay back, worked out already. Ask an accountant whether it is
  a tilbakeføring or a yearly adjustment if unsure.

## Steps

1. Open `https://myntos.no/mva-justeringer/`, or on the MVA page click «på en
   egen side» in the text at the top.
2. In «Før en mva-justering», under «Hva gjelder det?», choose «Tilbakeføring
   for personbil eller fast eiendom».
3. «Dato»: the date it applies from. It decides which termin's melding it
   goes in.
4. «Mva-kode»: 1, the only choice.
5. «Mva-beløpet»: the deduction to pay back.
6. «Motkonto»: leave it empty (7790) unless their accountant names another
   account.
7. «Tekst»: what it concerns, for example «Tilbakeføring mva, bil EL 12345».
8. Press «Før». The page says «Ført som bilag …», and the entry appears in the
   table with «Tilbakeføring».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «beløpet må være større enn null» | No amount, or a negative one | Type the amount without a minus sign |
| «selskapet var ikke mva-registrert …» | Not VAT-registered on that date | Check the date |
| «regnskapsåret … er avsluttet» | That year is closed | Check the date |

## Good to know

- It adds to the VAT to pay for the termin.
- A yearly adjustment on a capital good is the choice «Justering for
  kapitalvare»:
  [Adjust the VAT deduction on a capital good](https://myntos.no/llms/help/vat/adjust-the-deduction-on-a-capital-good.md).

## Related

- [Reverse a VAT adjustment](https://myntos.no/llms/help/vat/reverse-a-vat-adjustment.md)
- An agent with a key can record it through the API:
  [myntos-mva-justering](https://myntos.no/llms/skills/myntos-mva-justering.md)
