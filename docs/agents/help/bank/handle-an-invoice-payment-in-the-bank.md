---
title: Handle an invoice payment in the bank
description: How a person records a customer's payment of an invoice straight from the bank line on the Bankavstemming page in Myntos, with «Før som innbetaling på faktura …» when the line's KID names the invoice, and why it must not be entered as a new sale. Use when someone asks how to register that a customer paid an invoice from the bank statement, or what «KID-en hører til faktura …» means.
pages: /bank/
skill: myntos-bank
---

# Handle an invoice payment in the bank

When a customer pays an invoice with its KID (the payment reference printed
on the invoice), the bank line on «Bankavstemming» names the invoice. The
sale itself was already entered in the books when the invoice was issued, so
the payment is not new income: it settles what the customer owed (account
1500). «Før som innbetaling på faktura …» on the line posts exactly that,
with the date and amount from the bank, and reconciles the line in the same
press. The thing to avoid is entering the payment as a sale, which would count
the same income twice.

## They might ask

- «Kunden har betalt fakturaen, hvordan fører jeg det?»
- «Hva betyr «KID-en hører til faktura …»?»
- «Skal jeg føre innbetalingen fra kunden som salg?»
- "A customer paid my invoice, how do I record it in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- A bank line under «Ikke avstemt» that says «KID-en hører til faktura … til
  ….».
- If they already entered the payment as a manual voucher or a sale, reverse
  that first, or the money counts twice:
  [Register a payment on an invoice](https://myntos.no/llms/help/invoices/register-a-payment-on-an-invoice.md)
  says how.

## Steps

1. Open «Bankavstemming» (`https://myntos.no/bank/`).
2. Find the line. Under its text it says «KID-en hører til faktura … til
   ….», followed by «Den er ikke betalt.» or «Den er delvis betalt, og det
   står … igjen.»
3. Press «Før som innbetaling på faktura …».
4. The browser asks «Føre … fra … som innbetaling på faktura … til …?».
   Press OK.
5. The line moves to reconciled and says «Ført fra denne linjen som bilag …».
   On «Faktura», the invoice's status is now «Betalt», or «Delvis betalt» if
   the customer paid part.

Do not press «Før som nytt bilag» on that line, and do not register the
payment as «Salg med 25 % mva» or another sale under «Betaling».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Den er betalt fra før. Er det denne innbetalingen, bekrefter du bilaget den ble ført på.» | The payment was registered on the invoice by hand before the statement was imported | Press «Bekreft» on the suggested voucher: [Match a bank line to a voucher](https://myntos.no/llms/help/bank/match-a-bank-line-to-a-voucher.md) |
| «Den er kreditert, så kunden skylder ingenting på den.» | The invoice was cancelled with a credit note | The money is the customer's: post the line as what it is with «Før som nytt bilag», or ask an accountant |
| «det står … kr igjen på faktura …, og innbetalingen er på … kr» | The customer paid more than is left | The line stays as it is. Register what is left on the invoice and decide what the rest is |
| The customer paid without the KID, so no invoice is named | The bank line carries no KID, or a mistyped one | Register the payment on the invoice with the date and amount from the line: [Register a payment on an invoice](https://myntos.no/llms/help/invoices/register-a-payment-on-an-invoice.md). Then press «Bekreft» on the line's suggestion |
| No «Før som innbetaling på faktura …» button | The line is money going out, it is already reconciled, the invoice is paid or credited, or their role is «Leser» | See the line's note |
| «Vi fikk ikke ført innbetalingen.» | Something went wrong, and nothing was posted | Reload the page and try again |

## Good to know

- To undo it, press «Reverser» beside the payment under «Innbetalinger» on
  the invoice, or on the voucher under «Betaling», then «Fjern avstemmingen»
  on the line:
  [Undo a voucher posted from a bank line](https://myntos.no/llms/help/bank/undo-a-voucher-posted-from-a-bank-line.md).
  The invoice is then owed that amount again.
- One transfer that paid several invoices cannot be split from the bank page:
  a line is matched to one voucher. Register each invoice's share on
  «Faktura» with «Registrer innbetaling», and leave the line unreconciled.

## Related

- [Understand the bank reconciliation](https://myntos.no/llms/help/bank/understand-the-bank-reconciliation.md)
- [Use a KID on an invoice](https://myntos.no/llms/help/invoices/use-a-kid-on-an-invoice.md)
- An agent with a key does the same through the API:
  [myntos-bank](https://myntos.no/llms/skills/myntos-bank.md)
