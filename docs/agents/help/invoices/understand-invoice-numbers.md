---
title: How invoice numbers work
description: How Myntos numbers invoices and credit notes, when the number is given, why a draft has none, why the series cannot have gaps, and why the starting number cannot be chosen. Use when someone asks why their invoice has no number, how to choose the next invoice number, or why the numbering starts at 1.
pages: /faktura/
skill: myntos-faktura
---

# How invoice numbers work

An invoice gets its number at the moment it is issued, not before. Invoices
and credit notes share one unbroken series per company: 1, 2, 3 and on. The
series must have no gaps, because a missing number is what an auditor reads as
a deleted invoice. That is why a draft has no number, and why deleting a draft
leaves no gap.

## They might ask

- «Hvorfor har ikke fakturaen noe nummer?»
- «Hvordan velger jeg fakturanummer?»
- «Kan jeg fortsette nummerserien fra det gamle programmet?»
- «Hvorfor fikk kreditnotaen et fakturanummer?»
- "How are invoice numbers assigned in Myntos?"

## Before they start

- Nothing; this is how the page behaves for everyone.

## Steps

1. Open «Faktura» in the top menu (`https://myntos.no/faktura/`). In the list,
   drafts show «utkast» under «Nr.»; issued invoices and credit notes show
   their number.
2. To see what the next number will probably be, look at the grey «KID» field
   in the form: the six digits after the first five are the coming invoice
   number. The review shows the KID as «settes ved utstedelse, trolig …».
3. The number is fixed when «Utsted fakturaen» is pressed, and shown on the
   card «Faktura … er utstedt».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| The number is one higher than expected | Someone else issued an invoice or credit note first | Nothing; the preview is not a reservation |
| They want to start at another number | The series always starts at 1 and cannot be set by hand | It cannot be changed in Myntos. If the business continues from another program, ask an accountant how to keep the two series apart |

## Good to know

- A credit note takes the next number in the same series as the invoices.
- Numbers are never reused and never skipped.

## Related

- [Use a KID on an invoice](https://myntos.no/llms/help/invoices/use-a-kid-on-an-invoice.md)
- [Delete a draft invoice](https://myntos.no/llms/help/invoices/delete-a-draft-invoice.md)
- An agent with a key can preview the next number through the API:
  [myntos-faktura](https://myntos.no/llms/skills/myntos-faktura.md)
