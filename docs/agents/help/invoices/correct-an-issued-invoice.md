---
title: Correct an issued invoice
description: How a person fixes a mistake on an invoice that is already issued in Myntos (wrong amount, customer, lines or date) by crediting it and issuing a new one on the Faktura page. Use when someone asks how to edit or change an invoice they have already sent.
pages: /faktura/
skill: myntos-faktura
---

# Correct an issued invoice

An issued invoice cannot be edited, by anyone. That is Norwegian bookkeeping
law (bokføringsforskriften § 5-2-7): the customer has received it and it is in
the books. A mistake is corrected in two steps: a credit note (kreditnota)
that reverses the wrong invoice in full, and a new invoice that is right.

## They might ask

- «Hvordan endrer jeg en faktura som er sendt?»
- «Fakturaen har feil beløp»
- «Jeg sendte faktura til feil kunde»
- "How do I edit an invoice I already sent in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».

## Steps

1. Open «Faktura» in the top menu (`https://myntos.no/faktura/`).
2. Credit the wrong invoice: «Krediter» on its row, then OK.
   [Credit an invoice](https://myntos.no/llms/help/invoices/credit-an-invoice.md)
   has the details.
3. Write the correct invoice. If it is to the same customer, «Kopier linjene
   fra faktura …» brings the old lines back to correct:
   [Copy the lines from a previous invoice](https://myntos.no/llms/help/invoices/copy-lines-from-a-previous-invoice.md).
4. Press «Se over fakturaen», check it, and press «Utsted fakturaen».
5. Send the customer both: the credit note and the new invoice.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Endre» on an issued invoice | Issued invoices cannot be changed | Follow the steps above |
| No «Krediter» either | It is already credited, or their role is «Leser» | If credited, only the new invoice is left to write |

## Good to know

- The invoice still has a draft? Then it was never issued, and it can simply
  be changed: [Change a draft invoice](https://myntos.no/llms/help/invoices/change-a-draft-invoice.md).
- Both the credit note and the new invoice get their own numbers, so the
  series stays unbroken and an auditor can follow what happened.

## Related

- [Credit an invoice](https://myntos.no/llms/help/invoices/credit-an-invoice.md)
- [Issue an invoice](https://myntos.no/llms/help/invoices/issue-an-invoice.md)
- An agent with a key can do both steps through the API:
  [myntos-faktura](https://myntos.no/llms/skills/myntos-faktura.md)
