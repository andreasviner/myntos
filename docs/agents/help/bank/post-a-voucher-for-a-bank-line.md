---
title: Post a voucher for a bank line
description: How a person enters a payment straight from a bank line that nothing in the books explains, with Før som nytt bilag on the Bankavstemming page in Myntos, taking the amount and date from the bank. Use when someone asks how to book a bank fee, interest or another transaction they see in the bank but have not entered.
pages: /bank/
skill: myntos-bank
---

# Post a voucher for a bank line

Some movements on the account were never entered: a bank fee, interest, a
payment someone forgot. From the bank line, Myntos can enter (post) the
voucher directly, with the amount, date and direction the bank states, and
link the two at once. **Posting is final.** Norwegian bookkeeping law makes a
posted voucher permanent; a mistake is corrected afterwards with a reversal,
never by editing or deleting it. Check the category and text before pressing
the button: there is no confirmation step.

## They might ask

- «Hvordan fører jeg et bankgebyr fra kontoutskriften?»
- «Det står en betaling i banken som ikke er ført, hvordan fører jeg den?»
- «Hva gjør «Før som nytt bilag»?»
- "How do I book a bank transaction that isn't in my books yet?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- Be sure the payment is not already entered with another amount or date. If
  it is, correct that entry instead, or the payment is booked twice.
- If the line says «KID-en hører til faktura …», stop: it is a customer
  paying an invoice, and must not be entered as a new sale:
  [Handle an invoice payment in the bank](https://myntos.no/llms/help/bank/handle-an-invoice-payment-in-the-bank.md).

## Steps

1. Open «Oversikt» in the top menu, then the link «Bankavstemming»
   (`https://myntos.no/bank/`).
2. Under «Bevegelser på kontoen», with «Vis» on «Ikke avstemt», find the line.
3. Press «Før som nytt bilag» under it. A small form opens.
4. At «Hva var dette?», choose what the payment was, for example «Bank- og
   kortgebyr». Only categories going the same way as the money are offered.
   The hint under the field explains the chosen category.
5. At «Beskrivelse», check the text. It is filled in from what the bank wrote;
   change it so it says what the payment was.
6. Read «Beløp, dato og retning kommer fra banken: …». Those cannot be
   changed here.
7. Press «Før bilaget». The voucher is posted, and the line says «Ført fra
   denne linjen som bilag …».
8. If there is a receipt or invoice for it, attach it to the new voucher
   under «Betaling» in the top menu: find the voucher number in the list and
   choose «Legg ved bilag».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Før som nytt bilag» | Their role is «Leser», or the line is already reconciled | Ask a «Medlem» or more; check «Vis» |
| «String should have at least 1 character» | «Beskrivelse» is empty | Write what the payment was |
| «konto … er deaktivert. Aktiver den i kontoplanen, eller velg en annen konto» | The category's account has been switched off in the chart of accounts | Choose another category, or ask an «Administrator» to reactivate the account: [Deactivate or reactivate an account](https://myntos.no/llms/help/chart-of-accounts/deactivate-an-account.md) |
| «selskapet var ikke mva-registrert …» | The category assumes VAT the company could not deduct on that date | Choose a category without VAT, or enter it under «Betaling» with mva-kode 0 |
| «regnskapsåret … er avsluttet» | The bank's date is in a closed year | Nothing can be posted into a closed year |
| «selskapets første regnskapsår i Myntos er …» | The date is before the books in Myntos start | It belongs to the opening balance, not to Myntos's books |
| «banklinjen er allerede avstemt» | Someone reconciled the line meanwhile | Reload the page |

## Good to know

- The form uses the category's usual VAT code and account, and names no
  customer or supplier. To choose another mva-kode, book to an account the
  company added to its chart of accounts, or name the supplier, enter the
  payment under «Betaling» instead («Registrer betaling», with «Betalt via»
  «Bankkonto», the bank's date and exactly the bank's amount), then come back
  and press «Bekreft» on the suggestion.
- A posted voucher cannot be edited or deleted. If it was wrong:
  [Undo a voucher posted from a bank line](https://myntos.no/llms/help/bank/undo-a-voucher-posted-from-a-bank-line.md).
- If an AI with a key posts for the person, the voucher may wait for their
  approval under «Godkjenning» first:
  [Approve what an AI asks](https://myntos.no/llms/help/ai/approve-what-an-ai-asks.md).

## Related

- [Match a bank line to a voucher](https://myntos.no/llms/help/bank/match-a-bank-line-to-a-voucher.md)
- [Understand the bank reconciliation](https://myntos.no/llms/help/bank/understand-the-bank-reconciliation.md)
- An agent with a key can do this through the API:
  [myntos-bank](https://myntos.no/llms/skills/myntos-bank.md)
