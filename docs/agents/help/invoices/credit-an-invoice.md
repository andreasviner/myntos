---
title: Credit an invoice
description: How a person reverses an issued invoice with a credit note (kreditnota) using «Krediter» on the Faktura page in Myntos, and what that does in the books. Use when someone asks how to make a credit note, cancel an issued invoice, or take back an invoice sent by mistake.
pages: /faktura/
skill: myntos-faktura
---

# Credit an invoice

An issued invoice cannot be changed or deleted. It is reversed with a credit
note (kreditnota): a document of its own, with its own number in the same
series, that cancels the **whole** invoice and reverses its entry in the
books. There is no partial credit. **A credit note is issued the moment it is
confirmed and cannot be undone.**

## They might ask

- «Hvordan lager jeg en kreditnota?»
- «Hvordan kansellerer jeg en faktura?»
- «Jeg sendte fakturaen ved en feil»
- "How do I credit an invoice in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- The invoice is issued and not credited before.

## Steps

1. Open «Faktura» in the top menu (`https://myntos.no/faktura/`).
2. In the list on the right, find the invoice (status «Utstedt») and press
   «Krediter».
3. Read the question «Kreditere faktura …?», which says a credit note
   reversing the whole invoice will be issued and cannot be undone, and press
   OK.
4. The page says «Kreditnota … er utstedt.» The credit note appears in the
   list with status «Kreditnota», and the invoice's status changes to
   «Kreditert».
5. Send the credit note to the customer the same way as the invoice: «PDF»
   (and «EHF» for a business customer) on its row.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Krediter» on the row | The invoice is a draft, already credited, is itself a credit note, or their role is «Leser» | A draft is deleted instead: [Delete a draft invoice](https://myntos.no/llms/help/invoices/delete-a-draft-invoice.md) |
| «fakturaen er allerede kreditert» | It already has a credit note | Nothing more to do |
| «Krediteringen feilet.» or another message at the top | It could not be issued | Reload the page and try again |

## Good to know

- The credit note is dated today, not on the invoice's date, so a VAT period
  already reported is not changed.
- It carries no KID: nothing is paid on it.
- If the customer has already paid, the money has to be paid back separately;
  the credit note does not move money.
- To fix a wrong amount, credit the invoice and write a new one:
  [Correct an issued invoice](https://myntos.no/llms/help/invoices/correct-an-issued-invoice.md).

## Related

- [Download an invoice](https://myntos.no/llms/help/invoices/download-an-invoice.md)
- An agent with a key can issue a credit note through the API, if the person
  allowed it: [myntos-faktura](https://myntos.no/llms/skills/myntos-faktura.md)
