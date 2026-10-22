---
title: Move from Tripletex to Myntos
description: How a person exports their books from Tripletex (Regnskapseksport SAF-T, the vouchers with attachments printed as PDF, customer and supplier lists as CSV) following the Flytte fra Tripletex guide on Myntos, and starts in Myntos. Use when someone asks how to switch from Tripletex to Myntos, where the SAF-T export is in Tripletex, or how to get the attachments out of Tripletex.
pages: /kom-i-gang/
---

# Move from Tripletex to Myntos

Tripletex keeps the SAF-T export on the chart of accounts rather than in the
settings, which is easy to miss. The SAF-T file has the books but not the
receipts and invoices behind the vouchers: Tripletex has no export of those
(it says so itself), but prints them as one PDF from the voucher overview, and
Myntos reads that printout. Download everything before cancelling, and after
the period is closed: anything posted after the export means exporting again.

## They might ask

- «Hvordan flytter jeg fra Tripletex til Myntos?»
- «Hvor finner jeg SAF-T-eksporten i Tripletex?»
- «Hvordan får jeg vedleggene ut av Tripletex?»
- "How do I export my books from Tripletex?"

## Before they start

Read the general order first:
[Move your books to Myntos](https://myntos.no/llms/help/getting-started/move-your-books-to-myntos.md).
The guide is at `https://myntos.no/kom-i-gang/tripletex/`, headed «Flytte fra
Tripletex til Myntos».

## Steps

In Tripletex, as the guide «Hent dataene ut av Tripletex» describes:

1. **«Finn regnskapseksporten».** It is on the chart of accounts: «Regnskap →
   Kontoplan → Regnskapseksport (SAF-T)». Signed in as an accountant, choose
   the right client first.
2. **«Velg periode og last ned».** Give the from and to dates and choose «Last
   ned regnskap». Without the tick for document reception, the file goes
   straight to the downloads folder.
3. **«Vent på ZIP-en».** Closing the tab too early means starting again;
   there is no queue that carries on in the background.
4. **«Skriv ut bilagene med vedlegg».** «Bilag → Bilagsoversikt → Skriv ut
   valgte bilag»: choose the period, tick the box in the «Dato» column so every
   voucher is chosen, choose «Skriv ut valgte bilag» and download what is shown
   as a PDF. A month at a time if there is much.
5. **«Ta med listene».** The customer and supplier lists are exported
   separately as CSV.

Then in Myntos: create the account, set up the books, add customers and
suppliers, enter the opening balance and register payments from the
changeover date, as in the general article.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| The export is not where the guide says | Tripletex moved it. The guide was checked on 13 August 2026. | Use the guide's «Kilder»: Tripletex's help articles on exporting SAF-T, downloading the data, and exporting the customer list. |
| The ZIP never finished | The tab was closed, or the year is large. | Start the export again and leave the tab open. |

## Good to know

- **What to keep** (the guide's «Ta vare på dette»): the SAF-T export (ZIP)
  for each year in Tripletex; the printout of the vouchers with attachments,
  as PDF, for each year; the customer and supplier list as CSV; any reports
  they rely on, as PDF, because those are not in SAF-T. Tripletex also offers
  read access for five years after cancelling.
- **Timesheets, projects and payroll** in Tripletex are outside SAF-T
  Regnskap. Export what they need from those modules separately.
- **Myntos brings the books over from the SAF-T files, earlier years too.** On
  «Hent regnskapet fra et annet program», upload one file per year, as far
  back as they want the books in Myntos, and the running year's file up to
  today. Myntos reads them on its server (the page can be closed meanwhile),
  shows what will be posted, and posts the opening balance from the earliest
  file and every year's vouchers, under Tripletex's own voucher numbers. In a
  parallel run, a fresh file later shows whether the two agree:
  [Bring your books over from the previous program's SAF-T files](https://myntos.no/llms/help/opening-balance-and-year-end/bring-your-books-over-from-saf-t-files.md).
  The documents behind the vouchers (the PDFs) are not in SAF-T; afterwards
  the printout is uploaded in the card «Vedlegg fra Tripletex» on the same
  page, shown with «Tripletex» chosen under «Programmet du kommer fra» (this
  guide's «Hent regnskapet» opens the page with it chosen), and Myntos puts
  each voucher's pages on the voucher with the same number:
  [Bring the documents behind the vouchers over from Tripletex](https://myntos.no/llms/help/opening-balance-and-year-end/bring-the-documents-over-from-tripletex.md).

## Related

- [Move your books to Myntos from another program](https://myntos.no/llms/help/getting-started/move-your-books-to-myntos.md)
- [Set up the books for your company](https://myntos.no/llms/help/getting-started/set-up-the-books-for-your-company.md)
