---
title: Claim back VAT on a customer who never paid
description: How a person records a bad debt (tap på krav) in Myntos so the VAT on a sale the customer never paid is taken back in the MVA-melding, on the page «Uttak og justering». Use when a customer has gone bankrupt or will never pay an invoice, or someone asks how to record tap på krav with VAT in Myntos.
pages: /mva-justeringer/
skill: myntos-mva-justering
---

# Claim back VAT on a customer who never paid

The VAT on a sale was reported and paid when the invoice was sent. If the
customer never pays and the loss is final, that VAT can be taken back
(merverdiavgiftsloven § 4-7, «tap på krav»). Myntos records the loss as a cost
on 7830 Tap på fordringer, takes the VAT back as a deduction, and writes the
claim off the customers' account 1500. The entry is posted at once and cannot
be edited afterwards, only reversed.

## They might ask

- «Kunden har gått konkurs og betaler aldri. Hvordan fører jeg det?»
- «Hvordan fører jeg tap på krav med mva?»
- «Kan jeg få tilbake mvaen på en faktura som aldri ble betalt?»
- "How do I write off an unpaid invoice and get the VAT back in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- The loss is final: bankruptcy closed, or collection has failed. A customer
  who is only late does not qualify; ask an accountant when unsure.
- The amount the customer owed, with VAT, and the VAT rate on the invoice.

## Steps

1. Open `https://myntos.no/mva-justeringer/`, or on the MVA page click «på en
   egen side» in the text at the top.
2. In «Før en mva-justering», under «Hva gjelder det?», choose «Tap på krav:
   en kunde som aldri betalte».
3. «Dato»: the day the loss became final. It decides which termin's melding
   it goes in.
4. «Mva-kode»: the code for the rate the sale had: 1 (25 %), 11 (15 %),
   13 (12 %) or 12 (11,11 %, fish).
5. «Kravet som er tapt, med mva»: what the customer owed, VAT included. The
   VAT in it is worked out and taken back.
6. «Motkonto»: leave it empty (7830 Tap på fordringer), or type another cost
   account.
7. «Tekst»: which invoice and customer, for example «Tap på krav, faktura
   1042, Kunde AS konkurs».
8. Press «Før». The page says «Ført som bilag …», and the entry appears in the
   table with «Tap på krav» and a negative amount under «Mva».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «tap på krav føres på en kostnadskonto, som 7830» | The account in «Motkonto» is not a cost account | Leave it empty, or choose a cost account |
| «beløpet er for lite til å inneholde mva» | The amount is too small to contain any VAT | Check the amount |
| «selskapet var ikke mva-registrert …» | Not VAT-registered on that date | Check the date |
| «regnskapsåret … er avsluttet» | That year is closed | Check the date |

## Good to know

- It shows in the melding as a deduction, so it lowers the VAT to pay.
- The page does not ask which customer it was. The amount comes off the
  customers' account 1500 as a whole, so name the customer and invoice in
  «Tekst». An agent working through the API can name the customer.
- If the customer pays after all, reverse the entry and record the payment:
  [Reverse a VAT adjustment](https://myntos.no/llms/help/vat/reverse-a-vat-adjustment.md).

## Related

- [See what is owed for a VAT period](https://myntos.no/llms/help/vat/see-what-is-owed-for-a-termin.md)
- An agent with a key can record it through the API:
  [myntos-mva-justering](https://myntos.no/llms/skills/myntos-mva-justering.md)
