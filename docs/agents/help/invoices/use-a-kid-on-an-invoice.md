---
title: Use a KID on an invoice
description: How the KID payment reference works on invoices in Myntos, set automatically at issue, typed by hand, or left off, on the Faktura page. Use when someone asks what a KID is, how to put their own KID on an invoice, or how to remove it.
pages: /faktura/
skill: myntos-faktura
---

# Use a KID on an invoice

A KID is the number the customer types into their bank together with the
amount. It comes back with the payment and tells the seller which invoice was
paid. Myntos makes one for every invoice by default, when the invoice is
issued: the customer number (five digits, zeros for a buyer who is not a
registered customer), the invoice number (six digits) and a check digit. Before
issue the form shows what it will probably be.

## They might ask

- «Hva er KID på fakturaen?»
- «Hvordan bruker jeg min egen KID?»
- «Kan jeg sende faktura uten KID?»
- "What is the KID number on a Norwegian invoice?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».

## Steps

1. Open «Faktura» in the top menu (`https://myntos.no/faktura/`) and find
   «Bruk KID på fakturaen» under «Levering og betaling».
2. **Automatic (the default):** leave it ticked. The grey «KID» field shows
   the number it will probably get, and the line under it explains the
   digits. It is set for good when the invoice is issued.
3. **Their own KID:** press «Endre selv» and type the number. «Bruk automatisk
   KID» goes back to the automatic one.
4. **No KID:** untick «Bruk KID på fakturaen».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «kontrollsifferet i KID-en … går ikke opp, verken etter mod10 eller mod11. …» in the review | The typed KID's check digit is wrong | Check the number, or press «Bruk automatisk KID». It is a warning, not a stop, but the customer's bank will probably refuse it |
| The preview number changes | Another invoice was issued first, or another customer was chosen | Nothing to do; the number is only fixed at issue |

## Good to know

- A KID only works with the bank if it matches the company's KID agreement
  with that bank (length and check digit). Myntos makes the common shape:
  twelve digits with a mod10 check digit.
- A credit note never gets a KID: nothing is paid on it.

## Related

- [Issue an invoice](https://myntos.no/llms/help/invoices/issue-an-invoice.md)
- [How invoice numbers work](https://myntos.no/llms/help/invoices/understand-invoice-numbers.md)
- An agent with a key can set the KID through the API:
  [myntos-faktura](https://myntos.no/llms/skills/myntos-faktura.md)
