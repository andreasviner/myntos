---
title: Handle an invoice payment in the bank
description: What a person should do when the Bankavstemming page in Myntos says a bank line's KID belongs to an invoice the company issued - a customer paying - and why it must not be entered as a new sale. Use when someone asks how to register that a customer paid an invoice, or sees «Innbetaling på faktura føres ikke herfra ennå».
pages: /bank/
skill: myntos-bank
---

# Handle an invoice payment in the bank

When a customer pays an invoice with its KID (the payment reference printed
on the invoice), the bank line on «Bankavstemming» names the invoice. The
sale itself was already entered in the books when the invoice was issued, so
the payment is not new income. Myntos cannot yet record from the bank page
that the invoice has been paid; the page says so. The thing to avoid is
entering the payment as a sale, which would count the same income twice.

## They might ask

- «Kunden har betalt fakturaen, hvordan fører jeg det?»
- «Hva betyr «Innbetaling på faktura føres ikke herfra ennå»?»
- «Skal jeg føre innbetalingen fra kunden som salg?»
- "A customer paid my invoice, how do I record it in Myntos?"

## Before they start

- A bank line that says «KID-en hører til faktura … til ….».

## Steps

1. Do not press «Før som nytt bilag» on that line, and do not register the
   payment as «Salg med 25 % mva» or another sale under «Betaling».
2. Leave the line as it is. It stays under «Ikke avstemt», with the note
   naming the invoice, so it is easy to find again.
3. The line tells the person which invoice was paid. They can check it under
   «Faktura» in the top menu.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| The customer paid without the KID, so no invoice is named | The bank line carries no KID, or a mistyped one | Treat it the same way if the person knows it is an invoice payment: do not enter it as a sale |

## Good to know

- Settling an invoice from the bank line, so that the invoice counts as paid
  in the books, is planned but not built.
- One transfer that paid several invoices cannot be matched either: matching
  is one bank line to one voucher.

## Related

- [Understand the bank reconciliation](https://myntos.no/llms/help/bank/understand-the-bank-reconciliation.md)
- [Post a voucher for a bank line](https://myntos.no/llms/help/bank/post-a-voucher-for-a-bank-line.md)
- An agent with a key reads the same note through the API:
  [myntos-bank](https://myntos.no/llms/skills/myntos-bank.md)
