---
title: Download an invoice
description: How a person downloads an invoice or credit note as a PDF or an EHF e-invoice file from the Faktura page in Myntos, including a PDF preview of a draft. Use when someone asks where to find the PDF of an invoice, how to get the EHF file, or how to see an invoice before issuing it.
pages: /faktura/
skill: myntos-faktura
---

# Download an invoice

Every invoice and credit note can be downloaded as a PDF, the readable copy
for the customer. An issued one also has an EHF file, the electronic invoice
format businesses receive. A draft has a PDF too, marked as a draft and
without a number, which is a good way to see the invoice before issuing it.

## They might ask

- «Hvor finner jeg PDF-en av fakturaen?»
- «Hvordan laster jeg ned EHF-filen?»
- «Kan jeg se fakturaen før jeg sender den?»
- "Where do I download an invoice as PDF in Myntos?"

## Before they start

- Any role in the company, including «Leser».

## Steps

1. Open «Faktura» in the top menu (`https://myntos.no/faktura/`).
2. In the list on the right, find the invoice and press «PDF» on its row, or
   «EHF» for the e-invoice file. The file downloads.
3. Right after issuing, the card «Faktura … er utstedt» has the same files as
   «Last ned PDF» and «Last ned EHF».
4. For a draft, «Se utkastet som PDF» in the review shows it before it is
   issued.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «EHF» on the row | The invoice is a draft; the EHF is made at issue | Issue it first, or use the PDF |

## Good to know

- The files are named `faktura-<nummer>.pdf`, `faktura-<nummer>.xml`, or
  `kreditnota-…` for a credit note; a draft's PDF is `faktura-utkast.pdf`.
- The EHF file is frozen at issue and downloads exactly as it was made.
- The PDF of every issued invoice is also filed in the bilag archive with the
  entry in the books, and kept for five years.

## Related

- [Send an invoice to the customer](https://myntos.no/llms/help/invoices/send-an-invoice-to-the-customer.md)
- An agent with a key can download both files through the API:
  [myntos-faktura](https://myntos.no/llms/skills/myntos-faktura.md)
