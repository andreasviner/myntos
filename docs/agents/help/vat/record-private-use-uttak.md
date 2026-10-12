---
title: Record private use (uttak)
description: How a person records in Myntos that goods or services were taken out of the business for the owner's private use or given away (uttak), so the VAT is reported as if they were sold, on the page «Uttak og justering». Use when someone asks how to record uttak, goods the owner took home, or a gift from the business, with VAT in Myntos.
pages: /mva-justeringer/
skill: myntos-mva-justering
---

# Record private use (uttak)

When goods or services are taken out of a VAT-registered business, for the
owner or as a gift, VAT is due as though they had been sold at their ordinary
value (merverdiavgiftsloven kapittel 3, «uttak»). Myntos records it as a sale
of that value with VAT, against the owner's private account (or a cost account
for a gift), and reports it on its own line in the MVA-melding. The entry is
posted at once and cannot be edited afterwards, only reversed.

## They might ask

- «Jeg tok med meg varer fra butikken hjem. Hvordan fører jeg det?»
- «Hvordan fører jeg uttak av varer med mva?»
- «Vi ga bort varer til en kunde, skal det mva på?»
- "How do I record goods the owner took for private use in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier». A «Leser» sees only the table.
- The company was VAT-registered on the date.
- The value: what the goods or services would have cost if sold, without VAT.

## Steps

1. Open `https://myntos.no/mva-justeringer/`, or on the MVA page
   (`https://myntos.no/mva/`) click «på en egen side» in the text at the top.
   The page is headed «Mva · Uttak og justering».
2. In «Før en mva-justering», under «Hva gjelder det?», choose «Uttak: varer
   eller tjenester tatt ut av virksomheten».
3. «Dato»: the day it happened. It decides which termin's melding it goes in.
4. «Mva-kode»: the code for the rate the goods would be sold at: 3 (25 %),
   31 (15 %, food) or 33 (12 %).
5. «Verdi uten mva»: the value without VAT. The VAT is calculated and added.
6. «Motkonto»: leave it empty when the owner took it (it goes to 2060
   Privatkonto). For a gift, type the cost account from the chart of accounts.
7. «Tekst»: what was taken, for example «Varer tatt hjem, oktober».
8. Press «Før». The page says «Ført som bilag …», and the entry appears in the
   table with «Uttak» under «Hva».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No form, only the table | Their role is «Leser» | Ask a «Medlem» or above |
| «selskapet var ikke mva-registrert …, og da finnes det ingen mva-melding å føre dette i» | Not VAT-registered on that date | Check the date; an unregistered company reports no uttak |
| «beløpet må være større enn null» or «beløpet kan ikke ha brøkdeler av et øre» | The amount is empty, zero or has more than two decimals | Type the amount, for example 1250,00 |
| «kontoplanen har ingen konto …» or «konto … kan ikke være motkonto her» | The account in «Motkonto» does not exist, or is 2700, 2710 or 1500 | Leave it empty, or use another account |
| «regnskapsåret … er avsluttet» | That year is closed | Check the date |

## Good to know

- Uttak is reported as a sale: it adds to the VAT to pay for the termin.
- If the termin is already sent, the MVA page marks it «Endret etter
  innsending», and a corrected melding is due:
  [Correct a VAT return already sent](https://myntos.no/llms/help/vat/correct-a-vat-return-already-sent.md).
- Money the owner simply takes out of the bank is not uttak with VAT; that is
  «Uttak til eier» on «Betaling».

## Related

- [Reverse a VAT adjustment](https://myntos.no/llms/help/vat/reverse-a-vat-adjustment.md)
- [See what is owed for a VAT period](https://myntos.no/llms/help/vat/see-what-is-owed-for-a-termin.md)
- An agent with a key can record it through the API:
  [myntos-mva-justering](https://myntos.no/llms/skills/myntos-mva-justering.md)
