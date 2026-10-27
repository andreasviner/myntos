---
title: Bring your books over from the previous program's SAF-T files
description: How a person moving to Myntos from Tripletex, Fiken, PowerOffice, Visma or another program uploads that program's SAF-T files, one per year, on the page «Hent regnskapet fra et annet program», lets Myntos read them on the server, checks what will be posted and posts it, earlier years and the unfinished current year alike. Use when someone asks how to import SAF-T, whether earlier years or this year's vouchers can come over, why the import is slow, or whether they can leave the page while it runs.
pages: /tidligere-program/
skill: myntos-tidligere-program
---

# Bring your books over from the previous program's SAF-T files

Every Norwegian accounting program exports **SAF-T files**, one per financial
year, with the chart of accounts, every balance, the customers and suppliers and
every voucher. Myntos reads them and brings **every year in them** over in one
go: the opening balance (inngående balanse) from the earliest file, the
accounts, customers, suppliers and avdelinger it needs, and each year's vouchers
under the old program's own numbers. Earlier years are fine, as far back as the
files go, and so is the year the person is in (its file runs to the day it was
taken). The reading and the posting happen on Myntos's server: once the files
are uploaded the person may close the page. Once posted, the vouchers cannot be
edited, only corrected in the old program. The receipts and invoices behind
them (the PDFs) are not in SAF-T. From Tripletex they come over afterwards from
its printout of the vouchers:
[Bring the documents behind the vouchers over from Tripletex](https://myntos.no/llms/help/opening-balance-and-year-end/bring-the-documents-over-from-tripletex.md).
From other programs they stay in the old program's archive, which the person
must keep.

## They might ask

- «Kan Myntos hente inn regnskapet fra Tripletex?»
- «Kan jeg ta med tidligere år også?»
- «Jeg har lastet opp SAF-T-filene, men finner ikke bilagene»
- «Må jeg holde siden åpen mens filene leses?»
- "Can I import my SAF-T files into Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier». When the files need accounts
  the chart of accounts does not have, only «Administrator» or «Eier» can post.
- The SAF-T files from the old program: one per year, each from 1 January,
  the years one after another with none missing, every year but the last one
  whole. ZIP or XML, at most 2 GB in all, each not split in parts. From Fiken
  the complete export (`fiken-komplett-….zip`) can be uploaded as it is: it has
  the SAF-T file for every year in it, and Myntos keeps the documents in it
  too. Where to find them in each program:
  [Move your books to Myntos](https://myntos.no/llms/help/getting-started/move-your-books-to-myntos.md).
- Nothing registered in Myntos yet for the months the files cover. If there is,
  they choose a date before the first of those entries under «Flere valg» →
  «Til og med (valgfritt)».

## Steps

1. Open `https://myntos.no/tidligere-program/`: the button «Hent regnskapet» on
   «Oversikt» (shown while the books are empty), on «Inngående balanse», on
   the move guides under «Kom i gang», or the link «Hent regnskapet fra et
   annet program» in the note at the bottom of «Oversikt». A move guide's
   «Hent regnskapet» opens it with that program chosen.
2. Optionally, choose the program under «Programmet du kommer fra». It adds a
   link to the move guide for finding the SAF-T files in that program, says
   what becomes of the documents behind the vouchers, for «Tripletex» shows the
   card for its printout further down (step 11), and for «Fiken» says under
   the upload box that the complete export can be uploaded as it is. Myntos
   chooses the program by itself when a file names it (the SAF-T header,
   or Fiken's export by its name) and says so: «Filene er fra Fiken, så Fiken
   er valgt som programmet du kommer fra.»
3. In the card «Last opp SAF-T-filene», drag the files into the box «SAF-T-filer
   fra det gamle programmet», or press it to choose them. They are listed with
   their size; «Fjern» takes one out. A file in the wrong box is moved where it
   belongs, with a line saying so: Tripletex's printout of the vouchers (a PDF)
   goes to «Vedlegg fra Tripletex», and a SAF-T file dropped there comes up
   here. A file that is neither (a spreadsheet, a bank statement, an invoice)
   is left out, with a line saying what it is.
4. Press «Last opp og les filene». A bar shows the upload; keep the page open
   until it says «Lastet opp. Myntos leser filene nå.»
5. The card «Myntos leser filene» shows how far it has come («Leser «2025.zip»
   (1 av 2)»). **They may close the page now**: the work is on the server, and
   the result is there when they come back (the overview shows «SAF-T-filene
   er lest» with «Se over og før»).
6. «Dette blir ført» shows the review:
   - one sentence summing it up («Filene fra Tripletex gir 601 nye bilag for
     2025–2026. Regnskapet i Myntos starter da i 2025, ikke i 2026.»);
   - a row per year, with the period, the number of vouchers, the months, and
     marks «Nytt år i Myntos», «Ikke ferdig» or «… hentet før»;
   - what happens to the opening balance;
   - under «Legges til i Myntos», tiles with how many accounts, customers and
     suppliers, and avdelinger and products will be added. Each opens to a
     short list, with «Søk i listen» when it is long;
   - for Fiken's complete export, «Dokumentene i eksporten»: how many files
     will be kept and their size, a tile «dokumenter tas vare på» that opens
     to the export's folders, and «filer tas ikke vare på» with the reason for
     each (locked with a password, over 100 MB);
   - anything in red stops the post; «… merknader å lese» opens the notes.
7. Press «Før … bilag» (it names the number). This cannot be undone. The card
   shows «Myntos fører regnskapet», then for an export «Myntos tar vare på
   dokumentene» with a count, and may be closed again.
8. «Regnskapet er hentet» says what was posted, and for an export how many
   documents were kept. From there: «Se bilagene»
   (the vouchers on «Bilag»), «Inn og ut av banken», and «Avslutt …» for an
   earlier year. Every year's result and balance sheet is on «Regnskapet år
   for år» on the overview.
9. For a VAT-registered company, «Mva-meldingene det gamle programmet
   leverte» lists the VAT terms of the imported years that are over and not
   yet filed or marked. Tick the ones the old program filed and press «Merk
   som levert fra det gamle programmet». Terms where the files carry the old
   program's own VAT settlement are ticked already; the others are left for
   the person to decide. Each ticked term is marked «Sendt fra et annet
   program» on the MVA page, with the deadline as the day it was sent, so it is
   no longer overdue and Myntos will not send a melding that would replace it.
   It can be undone per term on the MVA page:
   [Mark a VAT period as filed from another program](https://myntos.no/llms/help/vat/mark-a-termin-filed-in-another-program.md).
10. Check the running year with a fresh file:
    [Compare Myntos with the previous program](https://myntos.no/llms/help/opening-balance-and-year-end/compare-myntos-with-the-previous-program.md).
11. From Tripletex, bring the receipts and invoices over in the card «Vedlegg
    fra Tripletex» further down the same page, shown when «Tripletex» is
    chosen under «Programmet du kommer fra»:
    [Bring the documents behind the vouchers over from Tripletex](https://myntos.no/llms/help/opening-balance-and-year-end/bring-the-documents-over-from-tripletex.md).
    From Fiken's complete export they are kept already, under «Dokumenter fra
    eksporten» on the same page:
    [Find the documents from Fiken's complete export](https://myntos.no/llms/help/opening-balance-and-year-end/find-the-documents-from-fikens-export.md).

«Forkast» throws away files read by mistake, before anything is posted.

## The notes Myntos may show

| The note starts | What it means |
| --- | --- |
| «Konto … er bankkontoen …, og er merket som bankkonto …» | The old program kept the company's bank on another account than 1920 (Tripletex keeps a farm's driftskreditt on 2390), and the file says so. The account is marked as a bank account, so «Oversikt» counts the money on it. Nothing is moved; it can be undone with «Ikke bankkonto» on «Kontoplan»: [Mark an account as a bank account](https://myntos.no/llms/help/chart-of-accounts/mark-an-account-as-a-bank-account.md) |
| «Konto … er bankkontoen …. Be en administrator eller eier merke den …» | The same, but the person may not change the kontoplan | An «Administrator» or «Eier» presses «Merk som bankkonto» on «Kontoplan» |
| «Regnskapet i Myntos starter i …, ikke i …» | The files go further back than the books in Myntos did; those years are added |
| «Filen for … har ingen saldoer per 1. januar …» | The books began in that year in the old program (the farm's Tripletex books began in June). Nothing to post as opening balance; everything is in the vouchers |
| «Inngående balanse for … (bilag …) reverseres, og en ny føres per 1. januar …» | An opening balance posted for a later year is replaced by one from the earliest file, so nothing counts twice. Accounts the person had moved in it (the driftskreditt to 1920) are moved in the new one too |
| «Årene … er ferdige i det gamle programmet, men åpne i Myntos.» | Close them under «Årsavslutning», oldest first: [What closing a year locks](https://myntos.no/llms/help/opening-balance-and-year-end/what-closing-a-year-locks.md) |
| «Bilagene har mva, og Mva-siden i Myntos regner dem med …» | The old program filed those VAT returns. **Never send them again from Myntos**; tick them under «Mva-meldingene det gamle programmet leverte» once the books are posted (step 9), or mark them one by one on the MVA page ([how](https://myntos.no/llms/help/vat/mark-a-termin-filed-in-another-program.md)) |
| «… bilag i filen har linjer på flere datoer …» | Tripletex keeps an invoice and its payment in one voucher. Myntos posts each date as its own voucher, with the date after the number (2026-1-0109) |
| «Filen fører mva på 2711 …» | Myntos keeps output VAT on 2700 and input VAT on 2710; the line's text keeps the old account |
| «Saldoene i filen går ikke i null: differansen … er underskudd for …» | The old program had not moved the year before's result to equity; it is put on 2050 |
| «Filen grupperer … som …, som ikke passer …» | A new account got Myntos's own post in the næringsspesifikasjon; the accountant can look at it under «Kontoplan» |

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Filene kunne ikke leses» with «… slutter …. Bare det siste året kan være uferdig» | A year before the last is cut short | Export that year whole, and upload again |
| «filen for … mangler. Årene må komme etter hverandre …» | A year missing between two files | Upload the missing year too |
| «… og … gjelder begge …» | Two files for one year | Upload one per year |
| «filen er regnskapet til org.nr. …» | Another company's file | Choose the right company at the top, or export this company's |
| «filen sier at den har … bilag, men Myntos fant …» | The download was cut short | Download the file again |
| «SAF-T-filen er delt i … deler …» | The program split a large file | Export a shorter period |
| «… har ingen SAF-T-fil i seg», «… er et regneark …», «… har dokumenter, men ingen SAF-T-fil» | A ZIP or workbook without the SAF-T files: from Fiken, «fiken-bilag-…», «fiken-dokumenter-…» or the Excel workbook | Upload the SAF-T file for each year, or Fiken's complete export |
| «… er en PDF, ikke en SAF-T-fil» | A printout or a receipt sent as a SAF-T file | Tripletex's printout goes in «Vedlegg fra Tripletex»; from other programs the PDFs stay in the old program's archive |
| «… har mer enn 300 000 bilagslinjer …» or «… er mer enn 1 GB XML …» | One year is more than Myntos reads from a file | Export a shorter period from the old program |
| «Myntos har allerede … bilag fra … til … som ikke kom fra filen.» (in red) | Payments already registered in Myntos for those months | «Forkast», upload again with «Til og med» the day before the first of them |
| «Ingenting ble ført: regnskapet har endret seg siden filene ble lest.» | Something was registered between reading and posting | Read the new review, and post again if it is right |
| «Før …» is greyed out, with «Bare en administrator eller eier kan legge til kontoer …» | The person is «Medlem» and the files need new accounts | Ask an administrator or owner to post it |
| «Regnskapsåret … er avsluttet i Myntos …» | A year in the files is closed in Myntos | Reopen it under «Årsavslutning», or leave its file out |
| «Regnskapet er ført, men ikke alle dokumentene i eksporten ble tatt vare på. …» | The books are posted; storing the export's documents stopped halfway | Press «Ta vare på resten av dokumentene»; what is kept already is not kept twice |

## Good to know

- The vouchers are not listed under «Betaling», which shows payments
  registered in Myntos. They are under «Bilag», where «Ført fra» → «Tidligere
  program» picks them out:
  [See every voucher in the books](https://myntos.no/llms/help/vouchers/see-every-voucher.md).
  They count in every balance, the MVA page, the year-end and the SAF-T export.
- Uploading the same files again brings only what is new; vouchers already
  brought over are skipped. «Tidligere innlesinger» lists what was posted, and
  when.
- Myntos keeps the uploaded files only until they are posted or thrown away,
  and at most 14 days. From Fiken's complete export it keeps every document
  for good once the books are posted, on no voucher. From other programs the
  documents stay in the old program's archive; keep it, with the SAF-T files:
  they are the documentation of those years.
- An opening balance on its own, without vouchers, is entered on «Inngående
  balanse»:
  [Import the opening balance from a SAF-T file](https://myntos.no/llms/help/opening-balance-and-year-end/import-a-saf-t-file.md).

## Related

- [Find the documents from Fiken's complete export](https://myntos.no/llms/help/opening-balance-and-year-end/find-the-documents-from-fikens-export.md)
- [Bring the documents behind the vouchers over from Tripletex](https://myntos.no/llms/help/opening-balance-and-year-end/bring-the-documents-over-from-tripletex.md)
- [Compare Myntos with the previous program](https://myntos.no/llms/help/opening-balance-and-year-end/compare-myntos-with-the-previous-program.md)
- [See every voucher in the books](https://myntos.no/llms/help/vouchers/see-every-voucher.md)
- [Move your books to Myntos](https://myntos.no/llms/help/getting-started/move-your-books-to-myntos.md)
- An agent with a key can do this through the API:
  [myntos-tidligere-program](https://myntos.no/llms/skills/myntos-tidligere-program.md)
