---
title: Find the documents from Fiken's complete export
description: How a person who brought their books over from Fiken's complete export (fiken-komplett-….zip) finds and downloads the receipts, invoices and other files Myntos kept from it, under «Dokumenter fra eksporten» on the page «Hent regnskapet fra et annet program», why they are not on the vouchers, and what to do when not all of them were kept. Use when someone asks where the documents from Fiken went, whether Myntos kept the Fiken export, how to find a receipt from before Myntos, or sees «ikke alle dokumentene i eksporten ble tatt vare på».
pages: /tidligere-program/
skill: myntos-tidligere-program
---

# Find the documents from Fiken's complete export

Fiken's complete export is a ZIP with the SAF-T file for every year and,
beside them, every receipt and invoice uploaded to Fiken, the invoices Fiken
made and a spreadsheet per year. When that ZIP is uploaded on «Hent
regnskapet fra et annet program» and the books are posted, Myntos keeps every
file in it, under the folder it was in. They are **not put on the vouchers**:
the export does not say which voucher each document is behind, and anything
put in the bilag archive stays there, so Myntos does not guess. They are kept
with the books instead, and can be searched and downloaded. Nothing kept can be
deleted: bokføringsloven requires the documentation to be kept for five years
after the year.

## They might ask

- «Hvor ble det av kvitteringene fra Fiken?»
- «Tok Myntos vare på dokumentene i Fiken-eksporten?»
- «Jeg trenger fakturaen fra Felleskjøpet fra i fjor, den lå i Fiken»
- «Hvorfor ligger ikke dokumentene på bilagene?»
- "Where are the receipts from my Fiken export?"

## Before they start

- The books brought over from Fiken's complete export, not from the SAF-T
  files alone:
  [Bring your books over from the previous program's SAF-T files](https://myntos.no/llms/help/opening-balance-and-year-end/bring-your-books-over-from-saf-t-files.md).
  Books brought over before Myntos kept the documents can have them added: upload the
  complete export again, and the review shows nothing new to post and
  «Dokumentene i eksporten»; press «Ta vare på … dokumenter».
- Any role in the company can see and download them. Uploading and keeping
  them needs «Medlem», «Administrator» or «Eier».

## Steps

1. Open `https://myntos.no/tidligere-program/` («Hent regnskapet fra et annet
   program»).
2. The card «Dokumenter fra eksporten» lists every kept file by its folder and
   name, with its size, a hundred at a time; «Vis flere» shows the next.
3. Type in «Søk i mapper og filnavn» to find one by any part of the folder or
   file name Fiken gave it (a supplier, a year, an invoice number).
4. «Last ned» downloads the file as it was in the export.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No card «Dokumenter fra eksporten» | Nothing is kept: the books came from the SAF-T files alone, or the export has not been posted yet | Upload Fiken's complete export as under «Before they start» |
| «Regnskapet er ført, men ikke alle dokumentene i eksporten ble tatt vare på. …» | The books are posted, but storing the documents stopped halfway | Press «Ta vare på resten av dokumentene». What is kept already is not kept twice |
| The same, saying the export was deleted after 14 days | Myntos keeps an upload at most 14 days | Upload the complete export again; only what is missing is kept |
| «Ingen dokumenter passer søket.» | Nothing in a folder or file name matches | Try a shorter word; the search looks in names, not inside the documents |
| A file listed under «filer tas ikke vare på» in the review | Locked with a password, over 100 MB, or damaged in the ZIP | Keep that file from Fiken's export themselves |

## Good to know

- The search looks in the folders and file names the export had, not in the
  documents' contents.
- Uploading the same export twice keeps nothing twice.
- The files are downloaded, never opened in the page, whatever their name
  says they are.

## Related

- [Bring your books over from the previous program's SAF-T files](https://myntos.no/llms/help/opening-balance-and-year-end/bring-your-books-over-from-saf-t-files.md)
- [Move from Fiken to Myntos](https://myntos.no/llms/help/getting-started/move-from-fiken.md)
- An agent with a key can do this through the API:
  [myntos-tidligere-program](https://myntos.no/llms/skills/myntos-tidligere-program.md)
