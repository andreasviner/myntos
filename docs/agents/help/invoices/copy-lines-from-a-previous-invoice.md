---
title: Copy the lines from a previous invoice
description: How a person reuses the lines of the last invoice to the same customer on the Faktura page in Myntos, with the button «Kopier linjene fra faktura …». Use when someone asks how to send the same invoice again, invoice a customer monthly, or copy an old invoice.
pages: /faktura/
skill: myntos-faktura
---

# Copy the lines from a previous invoice

When a customer has been invoiced before, the form offers to copy the lines
from their most recent invoice or draft. It is the quickest way to send the
same thing again, such as a monthly fee. It copies the lines and the buyer's
reference, nothing else: the new invoice gets its own number, dates and KID.

## They might ask

- «Hvordan sender jeg samme faktura igjen?»
- «Kan jeg kopiere en gammel faktura?»
- «Jeg fakturerer samme kunde hver måned»
- "How do I duplicate an invoice in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- An earlier invoice or draft with lines to the same customer, or to the same
  organisation number if the buyer was typed in.

## Steps

1. Open «Faktura» in the top menu (`https://myntos.no/faktura/`).
2. Under «Kjøper», choose the customer in «Kunde», or type the buyer's
   «Organisasjonsnummer».
3. Under «Linjer», press «Kopier linjene fra faktura …» (it says «Kopier
   linjene fra forrige utkast» when the latest one is a draft). The lines in
   the form are replaced with the copied ones, and «Deres referanse» is filled
   in if it was empty.
4. Check every line, and check «Leveringsdato»: it must be the date of this
   delivery, not the last one. The page reminds them: «Linjene er kopiert.
   Kontroller beskrivelse, antall og pris, og at leveringsdatoen gjelder denne
   leveransen.»
5. Carry on with «Se over fakturaen»:
   [Issue an invoice](https://myntos.no/llms/help/invoices/issue-an-invoice.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No copy button | No earlier invoice to this buyer with lines, or the buyer was typed without an organisation number | Type the lines, or choose the registered customer |

## Good to know

- Only the most recent invoice or draft to that buyer is offered.
- Credit notes are never copied.
- Myntos has no automatic repeating invoices; each one is issued by a person.

## Related

- [Issue an invoice](https://myntos.no/llms/help/invoices/issue-an-invoice.md)
- An agent with a key can copy lines from an earlier invoice through the API:
  [myntos-faktura](https://myntos.no/llms/skills/myntos-faktura.md)
