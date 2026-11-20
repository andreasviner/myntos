---
title: Register a payment on an invoice
description: How a person records in Myntos that a customer has paid an issued invoice, in full or in part, with «Registrer innbetaling» on the Faktura page; what the statuses «Ubetalt», «Delvis betalt», «Betalt» and «Forfalt» mean; and how to undo a payment registered by mistake. Use when someone asks how to mark an invoice as paid, why an invoice still shows as unpaid, or why «Inn i år» on the overview is lower than the income.
pages: /faktura/
skill: myntos-faktura
---

# Register a payment on an invoice

An issued invoice is income in the books the day it is issued, and an amount
the customer owes (account 1500, kundefordringer). When the money comes, that
is a second entry: the bank account goes up and 1500 goes down. Myntos never
does this by itself on the due date, because the due date is when the money
should come, not when it came: a customer who pays late, pays part or never
pays would show as having paid. The person registers the payment when they
see it on the bank statement, with the date and amount the statement shows.

If the bank statement is imported under «Bankavstemming» and the customer
paid with the invoice's KID, the line there is the quicker way, and it
reconciles the line at the same time:
[Handle an invoice payment in the bank](https://myntos.no/llms/help/bank/handle-an-invoice-payment-in-the-bank.md).

## They might ask

- «Hvordan markerer jeg en faktura som betalt?»
- «Kunden har betalt, men fakturaen står fortsatt som ubetalt»
- «Kunden betalte bare en del av fakturaen»
- «Hvorfor viser «Inn i år» mindre enn inntektene?»
- "How do I mark an invoice as paid in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- The invoice is issued, not a draft and not credited.
- **If they already entered the payment some other way, that entry must be
  reversed first**, or the money counts twice. Before this was built, a
  payment was often entered as a manual voucher (debit 1920, credit 1500) on
  «Manuelt bilag», or as a sale under «Betaling». Reverse it:
  [Reverse a manual voucher](https://myntos.no/llms/help/opening-balance-and-year-end/reverse-a-manual-voucher.md)
  or [Reverse a payment](https://myntos.no/llms/help/payments/reverse-a-payment.md).
  An entry recorded as a sale counted the income twice as well, so reversing
  it also corrects the income.

## Steps

1. Open «Faktura» in the top menu (`https://myntos.no/faktura/`).
2. In the list «Fakturaer», find the invoice. Its status says whether it is
   paid: «Ubetalt», «Delvis betalt» or «Betalt». An unpaid invoice past its
   due date says «Forfalt» in red, and a part-paid one «Delvis betalt,
   forfalt».
3. Press «Registrer innbetaling» on its row. A card opens above the list,
   titled «Innbetaling på faktura … til …», showing «Å betale»,
   «Forfallsdato», «Betalt» and «Står igjen».
4. Fill in:
   - «Dato»: the day the money came, as the bank statement shows it. It
     cannot be after today.
   - «Beløp»: filled in with what is left. Change it if the customer paid
     less; anything less is a part payment, and the rest stays open.
   - «Betalt til»: «Bankkontoen (1920)» for a transfer, «Kontanter (1900)» for
     cash.
5. Press «Registrer innbetalingen». The page says «Faktura … er betalt.», or
   for a part payment «Innbetalingen er registrert. Det står … igjen.» The
   payment appears under «Innbetalinger» on the card, and the invoice's
   status in the list changes.
6. Press «Lukk» to close the card.

## Undo a payment registered by mistake

A registered payment is a posted voucher and cannot be edited or deleted; it
is reversed. On a paid invoice the row has «Innbetalinger» instead of
«Registrer innbetaling».

1. Press «Registrer innbetaling» or «Innbetalinger» on the invoice's row.
2. Under «Innbetalinger», press «Reverser» beside the wrong payment.
3. The browser asks «Reversere innbetalingen på … fra …?». Press OK.
4. The page says «Innbetalingen er reversert.» The invoice is owed that
   amount again; register the right payment as above.

The same payment can be reversed with «Reverser» in the register under
«Betaling», where it is listed as «Innbetaling på faktura». «Rediger» is not
offered there for it, because a corrected entry would no longer be linked to
the invoice.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Registrer innbetaling» on the row | The invoice is a draft, a credit note, credited or paid, or their role is «Leser» | A draft is issued first: [Issue an invoice](https://myntos.no/llms/help/invoices/issue-an-invoice.md) |
| «det står … kr igjen på faktura …, og innbetalingen er på … kr» | The amount is more than is left | Register what is left. If the customer paid too much, the rest is not a payment of this invoice; the person decides what it is (another invoice, or money to pay back) |
| «faktura … er allerede betalt» | Nothing is left to pay | Check «Innbetalinger» on the card: it may have been registered already |
| «datoen … er fram i tid» | The date is after today | Use the date the money came |
| «regnskapsåret … er avsluttet» | The payment's year is closed | An administrator must reopen the year under «Årsavslutning» first, or the payment is dated in the open year it actually came in |
| «Skriv beløpet som kom inn, for eksempel 1 250,00.» | «Beløp» is empty, zero or not a number | Type the amount |
| «Innbetalingen ble ikke registrert.» | Something went wrong, and nothing was posted | Reload the page and try again |

## Good to know

- The payment is a voucher of type «Innbetaling på faktura», debit the bank
  (or cash), credit 1500 with the customer, and no VAT: the VAT was counted
  when the invoice was issued.
- It counts in «Inn i år» on «Oversikt», which counts money in, not income.
  Until an invoice is paid, its amount is income but not «Inn i år».
- One invoice may be paid in as many instalments as the customer likes. Each
  is its own voucher.
- An invoice credited after it was paid shows «Kreditert», and the card still
  shows what was paid. Paying the customer back is a separate entry, and it
  is not linked to the invoice yet:
  [Credit an invoice](https://myntos.no/llms/help/invoices/credit-an-invoice.md).

## Related

- [Use a KID on an invoice](https://myntos.no/llms/help/invoices/use-a-kid-on-an-invoice.md)
- [Read the overview](https://myntos.no/llms/help/overview/read-the-overview.md)
- An agent with a key can register a payment through the API, if the person
  allowed it: [myntos-faktura](https://myntos.no/llms/skills/myntos-faktura.md)
