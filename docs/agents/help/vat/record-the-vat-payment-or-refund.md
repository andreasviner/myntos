---
title: Record the VAT payment or refund
description: How a person records in Myntos that the VAT from a sent mva-melding has been paid to Skatteetaten, or that a VAT refund has come in, with «Før betalingen» on the MVA page or by hand on the Betaling page. Use when someone asks how to book the payment of the mva, which category to use for paying the VAT, how to record money back from Skatteetaten, or what «Før betalingen» does.
pages: /mva/ /kontantstrom/
skill: myntos-mva
---

# Record the VAT payment or refund

When the MVA-melding is sent, Myntos moves the termin's VAT onto account 2740,
which then holds what is owed to (or by) Skatteetaten. Paying it, or receiving
a refund, is recorded on «Betaling» like any other payment, with a category of
its own. It is not a cost and not an income, and it must not go into a new
melding. The MVA page has a button that fills the form in.

## They might ask

- «Hvordan fører jeg betalingen av mva?»
- «Hvilken kategori bruker jeg når jeg betaler mva-meldingen?»
- «Vi fikk mva tilbake fra Skatteetaten. Hvordan fører jeg det?»
- «Hva gjør knappen Før betalingen?»
- "How do I record paying the VAT in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- The melding is sent, and the money has actually moved.
- The amount and date from the bank. The details Skatteetaten gave are on the
  MVA page: [Get the receipt and payment details](https://myntos.no/llms/help/vat/get-the-receipt-and-payment-details.md).

## Steps

The quick way, from the MVA page:

1. Open `https://myntos.no/mva/`. On the termin's row, under «Se meldingen»,
   press «Før betalingen» («Før tilbakebetalingen» for a refund). The same
   button is on the sent melding under «Meldinger for terminen».
2. «Betaling» opens with the form filled in: «Penger ut» (or «Penger inn»),
   the category, the amount, Skatteetaten as the other party, a description
   with the termin and the KID, and Skatteetaten's receipt attached as the
   bilag. The fields from the melding are marked «Fra mva-meldingen», and the
   page says «Fylt ut fra mva-meldingen, …».
3. Change «Dato» to the day the money left (or arrived). It starts as today.
4. Press «Registrer betaling».

By hand:

1. Open «Betaling» in the top menu (`https://myntos.no/kontantstrom/`).
2. In «Registrer en betaling», upload the bank's payment confirmation, or
   click «Ingen bilag? Fyll inn manuelt».
3. Choose «Penger ut» for a payment to Skatteetaten, or «Penger inn» for a
   refund.
4. In «Hva gjaldt det?», choose «Oppgjør av mva-melding til Skatteetaten»
   (paid) or «Utbetaling av mva-melding fra Skatteetaten» (refund).
5. Fill in «Dato» and «Beløp inkl. mva»: the amount that left or reached the
   account.
6. Leave «Mva-kode» as proposed: the payment carries no VAT of its own.
7. Press «Registrer betaling».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| The payment was already recorded under another category, as a cost | A different category was chosen | Correct the payment so it uses the MVA category; otherwise the VAT is counted as a cost and 2740 never clears |
| «regnskapsåret … er avsluttet» | That year has been closed | Check the date |
| «Betalingen for … er allerede ført, som bilag …» | The payment is in the books already | Nothing to do. If that voucher is wrong, correct it |
| «Denne meldingen for … har ingen betaling å føre.» | Nothing is owed, or a later melding for the termin replaced this one | Use the button on the newest melding |
| «Fylt ut fra mva-meldingen. Kvitteringen er ikke hentet, …» | The receipt has not been fetched from Skatteetaten | Attach the bank's confirmation, or fetch the receipt first with «Hent kvittering» |
| No «Før betalingen» | Paid already («Betalt» on the termin), nothing owed, or the role is «Leser» | If the payment is not in the books, record it by hand as below |

## Good to know

- Both categories go to account 2740, where the melding's settlement voucher
  put the amount. After the payment, 2740 is back to zero for that termin.
- The page under the payment details on the MVA page says the same: «Betalingen
  føres som «Oppgjør av mva-melding til Skatteetaten».»
- Myntos recognises the payment by its amount: a voucher on 2740 after the
  termin ended with exactly what is owed. Then the melding says «Betalt …,
  bilag …», the termin is marked «Betalt», and the button goes away. A
  payment split into two parts is not recognised, and the button stays.
- After a correction of a melding already paid, only the difference is owed,
  and that is what the button fills in.
- Paying too little or too late is between the company and Skatteetaten;
  Myntos only records what happened.

## Related

- [Understand the VAT settlement voucher](https://myntos.no/llms/help/vat/understand-the-vat-settlement-voucher.md)
- [Record a payment without a receipt](https://myntos.no/llms/help/payments/record-a-payment-without-a-receipt.md)
- [Record a payment from a receipt](https://myntos.no/llms/help/payments/record-a-payment-from-a-receipt.md)
- An agent with a key can record the payment once the person has paid:
  [myntos-kontantstrom](https://myntos.no/llms/skills/myntos-kontantstrom.md)
