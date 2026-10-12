---
title: Adjust the VAT deduction on a capital good
description: How a person records the yearly VAT adjustment (justering) on a capital good, such as a building or an expensive machine whose use has changed, on the page «Uttak og justering» in Myntos. Use when someone asks how to record justering av inngående mva, a kapitalvare adjustment, or a change in how much of a building is used for VAT-liable business.
pages: /mva-justeringer/
skill: myntos-mva-justering
---

# Adjust the VAT deduction on a capital good

The VAT deducted on a capital good (kapitalvare: a building, or machinery
and equipment of some size) assumes it stays in the same use. When the share
used in VAT-liable business changes, the deduction is adjusted year by year
(merverdiavgiftsloven kapittel 9), either paid back or increased. Myntos does
not work out the figure; the person, or their accountant, does. Myntos records
it and reports it on its own line. The entry is posted at once and cannot be
edited afterwards, only reversed.

## They might ask

- «Hvordan fører jeg justering av inngående mva på bygget?»
- «Vi bruker lokalet til noe annet nå, hva med mva-fradraget?»
- «Hvor fører jeg kapitalvarejustering i Myntos?»
- "How do I record a VAT adjustment on a capital good in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- The adjustment amount, worked out already: how much VAT is paid back, or
  how much more may be deducted. Ask an accountant if it has not been
  calculated.

## Steps

1. Open `https://myntos.no/mva-justeringer/`, or on the MVA page click «på en
   egen side» in the text at the top.
2. In «Før en mva-justering», under «Hva gjelder det?», choose «Justering for
   kapitalvare».
3. «Dato»: the date of the adjustment. It decides which termin's melding it
   goes in.
4. «Mva-kode»: 1, the only choice.
5. «Retning»: «Fradrag tilbakeføres (du skylder)» when deduction is paid
   back, «Mer fradrag (til gode)» when more may be deducted.
6. «Mva-beløpet»: the VAT amount itself, without basis, and without a minus
   sign: «Retning» decides the sign.
7. «Motkonto»: leave it empty (7790) unless their accountant names another
   account.
8. «Tekst»: what it concerns, for example «Justering 2026, bygget i
   Storgata 1».
9. Press «Før». The page says «Ført som bilag …», and the entry appears in the
   table with «Justering».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «beløpet må være større enn null» | No amount | Type the amount |
| «selskapet var ikke mva-registrert …» | Not VAT-registered on that date | Check the date |
| «konto … er deaktivert. …» | The account in «Motkonto» is switched off | Leave it empty, or activate it under «Kontoplan» |
| «regnskapsåret … er avsluttet» | That year is closed | Check the date |

## Good to know

- «Fradrag tilbakeføres» adds to the VAT to pay; «Mer fradrag» lowers it.
- A deduction that must be paid back in full, on a car or a property, is a
  different choice:
  [Repay the VAT deduction on a car or property](https://myntos.no/llms/help/vat/repay-the-deduction-on-a-car-or-property.md).

## Related

- [Reverse a VAT adjustment](https://myntos.no/llms/help/vat/reverse-a-vat-adjustment.md)
- An agent with a key can record it through the API:
  [myntos-mva-justering](https://myntos.no/llms/skills/myntos-mva-justering.md)
