---
title: Attach a receipt to a payment already registered
description: How a person adds the bilag (receipt, invoice, photo) to a payment that was registered without one, with «Legg ved bilag» in the register on the Betaling page in Myntos, and how to find the payments still missing one («… føringer mangler bilag»). Use when someone asks how to add a receipt afterwards, attach a document to an existing payment, or which payments lack documentation.
pages: /kontantstrom/
skill: myntos-kontantstrom
---

# Attach a receipt to a payment already registered

Norwegian bookkeeping law (bokføringsloven § 10) requires every payment to be
documented by a bilag. A payment registered without one, because the receipt
came later or the upload failed, gets its bilag from the register on the
«Betaling» page. The archive is append-only: a file attached can never be
removed again, because the law requires the bilag to be kept for five years.
Check that it is the right file first.

## They might ask

- «Hvordan legger jeg ved kvitteringen i etterkant?»
- «Jeg glemte å legge ved bilaget, hvordan fikser jeg det?»
- «Hvilke betalinger mangler bilag?»
- "How do I attach a receipt to a payment I already recorded?"

## Before they start

- They need the role «Medlem», «Administrator» or «Eier».
- The file as a PDF or picture (PNG, JPEG, WebP, GIF, HEIC), at most 25 MB.

## Steps

1. Open «Betaling» in the top menu (`https://myntos.no/kontantstrom/`).
2. In the «Oversikt» card beside the form, a yellow line says how many
   payments lack a bilag, for example «3 føringer mangler bilag. Legg ved
   PDF-en i registeret under.» No line means none are missing.
3. In the register below, find the payment. In the «Bilagsfil» column, a
   payment without a file shows the link «Legg ved bilag».
4. Click «Legg ved bilag» and choose the file, or several files at once if
   the bilag is more than one.
5. The register reloads, and the column now shows the file (for example «PDF
   184 kB», or «3 FILER» for several). The yellow line counts one fewer.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Legg ved bilag» on the payment | The payment already has a file, it has been reversed, or their role is «Leser» | See Good to know |
| «filen er større enn 25 MB» | The file is too large | Use a smaller copy, or a PDF |
| «vi tar imot .pdf, .png, .jpg, …» | Not a PDF or a supported picture | Save it as PDF or take a screenshot |
| «filen er tom» | The file has no content | Choose the file again |

## Good to know

- «Legg ved bilag» is offered only on a payment with no file at all. The page
  cannot add a second file to a payment that already has one.
- A payment that was corrected with «Rediger» keeps its file on the original
  entry, folded under the corrected one. It counts as documented and is not
  in the «mangler bilag» count.
- A reversed payment needs no bilag of its own and shows no «Legg ved bilag».
- Files cannot be removed or replaced after they are attached, by anyone.

## Related

- [See the documents for a payment](https://myntos.no/llms/help/payments/see-the-documents-for-a-payment.md)
- [Record a payment from a receipt](https://myntos.no/llms/help/payments/record-a-payment-from-a-receipt.md)
- An agent with a key can attach documents through the API:
  [myntos-kontantstrom](https://myntos.no/llms/skills/myntos-kontantstrom.md)
