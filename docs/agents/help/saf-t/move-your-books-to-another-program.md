---
title: Move your books to another program
description: How a person takes their accounts out of Myntos with the SAF-T file, one file per financial year, to import into another Norwegian accounting program or hand to a new accountant, and what the file does not include. Use when someone is leaving Myntos, changing accounting program, or asks how to get their data out.
pages: /saft/
skill: myntos-saft
---

# Move your books to another program

The accounts belong to the company, and Myntos gives them back in the
standard format every Norwegian accounting program knows: SAF-T. One file per
financial year holds the chart of accounts with balances, customers and
suppliers, VAT codes and every entry, so the new program can start from the
right figures. The receipts and invoices themselves (bilag) are not in the
file; they stay in Myntos's archive.

## They might ask

- «Hvordan tar jeg med meg regnskapet fra Myntos til et annet program?»
- «Kan jeg eksportere regnskapet mitt fra Myntos?»
- «Vi skal bytte til Tripletex, hva trenger de fra Myntos?»
- "How do I export all my accounting data from Myntos?"

## Before they start

- Any role in the company, «Leser» included.
- Which years the new program or accountant wants. Often only the last closed
  year is needed to start, plus the current year so far.

## Steps

1. Open `https://myntos.no/saft/` (from «Oversikt», the link «SAF-T» in the
   note under «Største poster i år»).
2. Pick a year with «‹» and «›», and press «Last ned SAF-T-filen». Repeat for
   each year needed.
3. If the button is missing, fix what the check reports first:
   [Check a SAF-T file](https://myntos.no/llms/help/saf-t/check-a-saf-t-file.md).
4. Import the files in the new program, using its own instructions for
   importing SAF-T, or give them to the new accountant.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Filen lages ikke før feilene over er rettet. …» | The check found errors | [Check a SAF-T file](https://myntos.no/llms/help/saf-t/check-a-saf-t-file.md) |
| «Selskapet har ikke noe regnskap for … i Myntos.» | No books for that year in Myntos | Only years kept in Myntos can be exported |

## Good to know

- Closing the last full year on «Årsavslutning» before exporting gives the
  new program a clean starting point.
- The bilag (PDFs and images of receipts and invoices) are not part of SAF-T.
  Bokføringsloven requires them to be kept for five years; Myntos keeps them
  in its archive, and a bulk export of them is not available yet.
- The files contain personal data about customers and suppliers. Hand them
  only to the new program or accountant.

## Related

- [Export a SAF-T file](https://myntos.no/llms/help/saf-t/export-a-saf-t-file.md)
- [What SAF-T is for](https://myntos.no/llms/help/saf-t/what-saf-t-is-for.md)
- An agent with a key can download the files through the API:
  [myntos-saft](https://myntos.no/llms/skills/myntos-saft.md)
