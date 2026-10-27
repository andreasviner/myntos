---
title: Bring the documents behind the vouchers over from Tripletex
description: How a person who has brought their books over from Tripletex prints the vouchers with their attachments in Tripletex as a PDF, chooses Tripletex under «Programmet du kommer fra» on the page «Hent regnskapet fra et annet program», uploads the PDF under «Vedlegg fra Tripletex», lets Myntos put each voucher's receipts and invoices in the bilag archive on the voucher with the same number, and finds them on «Bilag». Use when someone asks whether the attachments, receipts, invoices or PDFs can come over from Tripletex, says Tripletex has no ZIP of the attachments, cannot find the card, or cannot find a voucher's document after archiving.
pages: /tidligere-program/ /bilag/
skill: myntos-tidligere-program
---

# Bring the documents behind the vouchers over from Tripletex

The SAF-T files bring the books over, but not the receipts and invoices behind
the vouchers. Tripletex has no export of those, but it prints them: one PDF
where every voucher starts with a page Tripletex draws itself («Bilag nummer
240-2026») and its attachments follow. Myntos reads that printout, cuts it at
each voucher, and puts each voucher's pages, as one PDF named «Tripletex bilag
240-2026.pdf», on the voucher it brought over under the same number. It goes
by the number alone and never guesses from what an invoice says. Whatever is
put in the bilag archive stays there: bokføringsloven requires the
documentation to be kept for five years after the year, so nothing can be
taken out again.

## They might ask

- «Kan jeg få med vedleggene fra Tripletex?»
- «Tripletex sier de ikke har ZIP-eksport av vedleggene. Hva gjør jeg?»
- «Hvor er kvitteringene til bilagene jeg hentet fra Tripletex?»
- «Jeg har lastet opp PDF-ene, men ser dem ikke»
- "Can I import my receipts and invoices from Tripletex into Myntos?"

## Before they start

- The vouchers must already be in Myntos, brought over from the SAF-T files:
  [Bring your books over from the previous program's SAF-T files](https://myntos.no/llms/help/opening-balance-and-year-end/bring-your-books-over-from-saf-t-files.md).
  A voucher that is not there yet gets nothing.
- The role «Medlem», «Administrator» or «Eier». A «Leser» sees the result
  but cannot upload.
- The printout from Tripletex, made like this (Tripletex's own help article
  «Hvordan laster jeg ned regnskapsdataene mine?»):
  1. In Tripletex, open «Bilag» → «Bilagsoversikt».
  2. Choose the period, for example one month.
  3. Tick the box in the «Dato» column, so every voucher is chosen.
  4. Choose «Skriv ut valgte bilag».
  5. Download what is shown as a PDF with the arrow in the menu bar.
  For a long period Tripletex itself advises one at a time; a month is
  usually 10 to 20 MB. At most 24 files and 200 MB in one upload; a larger
  year goes up in more than one round.

## Steps

1. Open `https://myntos.no/tidligere-program/` («Hent regnskapet fra et annet
   program»).
2. Under «Programmet du kommer fra», choose «Tripletex». It is chosen already
   when they came from the Tripletex move guide's «Hent regnskapet», once
   SAF-T files from Tripletex are brought over, and once a printout has been
   uploaded. The card «Vedlegg fra Tripletex» then shows below the SAF-T upload.
3. In the card «Vedlegg fra Tripletex», drag the PDFs into the box
   «Utskrift av bilag med vedlegg», or press it to choose them. They are listed
   with their size; «Fjern» takes one out. A PDF dropped on the SAF-T box above
   is moved here, and «Tripletex» chosen, with a line saying so; a SAF-T file
   dropped here is moved up to the SAF-T files.
4. Press «Last opp og les utskriftene». Keep the page open until it says
   «Lastet opp. Myntos leser utskriftene nå.»
5. «Myntos leser utskriftene» shows how far it has come. **They may close the
   page now**: the work is on Myntos's server, and the result is there when
   they come back.
6. «Dette legges i bilagsarkivet» shows the review:
   - a sentence summing it up («Utskriften har 51 bilag fra Tripletex. 34 av
     dem har vedlegg, som legges på 48 bilag i Myntos. 17 har bare
     Tripletex-siden med posteringene og får ingenting.»). More vouchers in
     Myntos than in the printout is normal: Myntos split a voucher whose
     payment came on a later day into one voucher per date, and the document
     goes on each;
   - tiles that open to the voucher numbers: «bilag får vedleggene sine» (with
     the vouchers in Myntos each goes on), «bilag har ingen vedlegg», «bilag
     finnes ikke i Myntos», «bilag har dokumentet fra før» and «bilag er for
     store»;
   - «… merknader å lese» opens the notes.
7. Press «Legg … dokumenter i bilagsarkivet» (it names the number). **This
   cannot be undone.** «Myntos legger dokumentene i bilagsarkivet» shows while
   it works.
8. «Vedleggene er lagt i bilagsarkivet» says how many. «Se bilagene med
   dokument» opens «Bilag» with «Bare bilag med dokument» ticked, so only the
   vouchers that got a document are listed; each says «1 dokument» under its
   text, and pressing the voucher number shows the file to open. Without that
   box the whole year is listed, newest first, a hundred at a time, so an
   earlier month's vouchers are several «Vis flere» down. They are never on
   «Oversikt» or «Betaling», which show payments registered in Myntos.
9. For the next month, upload the next printout the same way.

«Forkast» throws away printouts uploaded by mistake, before anything is put in
the archive.

## The notes Myntos may show

| The note starts | What it means |
| --- | --- |
| «… bilag i utskriften finnes ikke blant bilagene hentet fra Tripletex …» | Those vouchers are not in Myntos (posted in Tripletex after the SAF-T file was taken, say). Bring them over with a fresh SAF-T file, then upload the printout again: what is already on is skipped |
| «… bilag har bare Tripletex' egen side …» | Vouchers with no attachment in Tripletex, such as bank payments. Nothing to put on |
| «Bilag …: Tripletex-siden sier … i debet, Myntos har …» | The voucher was probably changed in Tripletex after the SAF-T file was taken. The document goes on anyway; a fresh SAF-T file and [Compare Myntos with the previous program](https://myntos.no/llms/help/opening-balance-and-year-end/compare-myntos-with-the-previous-program.md) shows where they part |
| «… bilag ble delt på dato da de ble hentet …» | The document goes on every part of the voucher |
| «… bilag har fått det samme dokumentet før …» | Uploaded before; skipped |
| «Bilag … er … MB med vedleggene …» | Over 25 MB for one voucher; left out, so it stays in Tripletex |

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Utskriftene kunne ikke leses» with «fant ingen bilag i …» | The PDF is not Tripletex's printout of vouchers (an invoice, a report, an export from another program) | Make the printout as under «Before they start» |
| «… er bilagene til org.nr. …, ikke til dette selskapet …» | Another company's vouchers | Choose the right company at the top, or print this company's |
| «… er ikke en PDF», «… er passordbeskyttet» | The wrong file, or one closed with a password | Download the printout again from Tripletex |
| «Ingenting ble lagt i bilagsarkivet: bilagene i Myntos eller arkivet har endret seg …» | Vouchers were brought over or a document attached between reading and archiving | Read the new review, and press the button again if it is right |
| No card «Vedlegg fra Tripletex» on the page | Another program, or none, is chosen under «Programmet du kommer fra» | Choose «Tripletex» |
| «Vedleggene er lagt i bilagsarkivet», but no documents on «Bilag» | The list starts in January and shows a hundred at a time | Tick «Bare bilag med dokument» on «Bilag», or choose the month |
| The button is greyed out and says «Ingenting å legge i bilagsarkivet» | Everything in the printout is already on, or not in Myntos | Read the summary; nothing to do, or bring the missing vouchers over first |

## Good to know

- Only Tripletex's printout is read. Fiken's complete export has the
  documents in it, and Myntos keeps them when the books are brought over
  ([Find the documents from Fiken's complete export](https://myntos.no/llms/help/opening-balance-and-year-end/find-the-documents-from-fikens-export.md)).
  From PowerOffice, Conta and other programs they stay in that program's
  archive.
- What lands in Myntos is what Tripletex printed: Tripletex's page with the
  postings first, then the attachments as printed. An EHF invoice is
  Tripletex's rendering of it, and a photographed receipt is the picture.
- Myntos keeps the uploaded printouts only until the documents are in the
  archive or the upload is thrown away, and at most 14 days. Keep the PDFs
  and the SAF-T files. Tripletex also offers read access for five years after
  cancelling.

## Related

- [Bring your books over from the previous program's SAF-T files](https://myntos.no/llms/help/opening-balance-and-year-end/bring-your-books-over-from-saf-t-files.md)
- [See every voucher in the books](https://myntos.no/llms/help/vouchers/see-every-voucher.md)
- [Move from Tripletex to Myntos](https://myntos.no/llms/help/getting-started/move-from-tripletex.md)
- An agent with a key can do this through the API:
  [myntos-tidligere-program](https://myntos.no/llms/skills/myntos-tidligere-program.md)
