---
title: Import the opening balance from a SAF-T file
description: How a person brings the balances from their previous accounting program (Tripletex, Fiken, PowerOffice Go, Conta, Visma and others) into Myntos by uploading its SAF-T file on the Inngående balanse page, adds the accounts and customers and suppliers the file names, checks the result and posts it. Use when someone has moved their books to Myntos and has the SAF-T export, or asks whether Myntos can import SAF-T.
pages: /inngaende-balanse/
skill: myntos-inngaende-balanse
---

# Import the opening balance from a SAF-T file

Every Norwegian accounting program can export a SAF-T file: the chart of
accounts with each account's balance at the start and end of the year, and the
customers and suppliers with what they owed. Myntos reads **the balances** from
it into the opening balance form (inngående balanse), splits customers and
suppliers per party, and lists the accounts and parties Myntos does not have
yet. This page reads **the balances** only. To bring the books over with their
vouchers, earlier years included, the person uploads the SAF-T files on «Hent
regnskapet fra et annet program» instead, which posts the opening balance
itself. Nothing is posted until the person has checked the result
and pressed «Før inngående balanse», and once posted it cannot be edited, only
corrected with a reversal and a new balance.

## They might ask

- «Kan Myntos importere SAF-T?»
- «Jeg har SAF-T-filen fra Tripletex, hvordan får jeg den inn?»
- «Hvordan får jeg saldoene fra Fiken over til Myntos?»
- "Can I import a SAF-T file into Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier». Adding accounts to the chart of
  accounts needs «Administrator» or «Eier».
- The SAF-T file from the old program, as the ZIP it downloads as or the XML
  inside it, at most 200 MB. Either the file for the year they start in Myntos
  (from 1 January), or the file for the whole year before; both give the same
  balance. Where to find it in each program:
  [Move your books to Myntos](https://myntos.no/llms/help/getting-started/move-your-books-to-myntos.md).
- The file must be this company's: Myntos checks the organisasjonsnummer.
- The company's first year in Myntos must still be open.

## Steps

1. Open `https://myntos.no/inngaende-balanse/` (from «Oversikt», the link
   «Inngående balanse» in the note under «Største poster i år»).
2. In the card «Saldoer per 1. januar …», use «Last inn SAF-T-fil eller
   saldobalanse» and choose the file.
3. If the file has balances on accounts the chart of accounts does not have,
   or open items on customers and suppliers Myntos does not know, the box
   «Dette mangler i Myntos før balansen kan føres» lists them. Press «Legg til
   kontoer og parter». The button counts as it goes («Legger til konto 1451 …
   3 av 24»). Accounts get the name and the grouping from the file, customers
   and suppliers their name, organisasjonsnummer and the number the old
   program gave them. Myntos then reads the file again by itself.
4. The page says «Leste … linjer fra SAF-T-filen fra … (inngående saldoer for
   …). Se over linjene og kontroller.» Read the notes in grey under the box
   (table below), and look over the rows. Each row's «Tekst» is the account
   number and name in the old program.
5. Press «Kontroller», read «Gjennomgå før føring», and fix anything in red
   with «Tilbake til skjemaet»:
   [Review an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/review-an-opening-balance.md).
6. Press «Før inngående balanse». This cannot be undone. The page says
   «Inngående balanse er ført som bilag …».

## The notes Myntos may show

| The note starts | What it means | What to do |
| --- | --- | --- |
| «Saldoene i filen går ikke i null: differansen … er underskudd for …» (or «overskudd») | The old program had not moved last year's result to equity. Myntos puts it on 2050 Annen egenkapital | Usually right. For an AS with a loss, ask the accountant whether part of it belongs on 2080 Udekket tap |
| «Filen fører samme konto på flere avdelinger eller produktkoder …» | The old program used longer account numbers (Tripletex's farm accounts carry a department and a product code). Myntos has four digits, so they share one account, as separate rows | Nothing; the code stays in each row's text |
| «… er bankkontoen …. Merk konto … som bankkonto under Kontoplan …» | The company's bank account was on another account in the old program, often a credit line (driftskreditt) on 2390, and Myntos does not yet count that account as the bank | Either mark the account with «Merk som bankkonto» on «Kontoplan», which moves nothing and makes «Oversikt» count the money there ([how](https://myntos.no/llms/help/chart-of-accounts/mark-an-account-as-a-bank-account.md)), or, if the accountant prefers, change that row's account to 1920 before «Kontroller» |
| «Filen grupperer … som …, som ikke passer en konto i …» | The old program's næringsspesifikasjon grouping for that account was wrong for its kind of account, or an IFRS post | Myntos used its own; let the accountant look at it under «Kontoplan» |
| «Konto … er gruppert som … i Myntos og som … i filen» | Myntos and the old program put the account on different posts in the næringsspesifikasjon | Choose the right one under «Kontoplan», with the accountant |
| «… er mva som ikke var gjort opp ved årsskiftet …» | VAT left on 2700 or 2710 | Nothing; it is on 2740, where the payment clears it |
| «Kunde- og leverandørsaldoene i filen summerer ikke til saldoen på …» | The file's customer or supplier list does not add up to the account | The account stays one row; split it by hand if needed: [Split customers and suppliers in the opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/split-customers-and-suppliers-in-the-opening-balance.md) |

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «filen er regnskapet til org.nr. … (…), ikke til …» | The file is another company's | Choose the right company at the top of the page, or export the file for this one |
| «filen gjelder … til …. Inngående balanse i Myntos er per 1. januar …» | The file is for a year that says nothing about this opening balance | Export the file for the year named in the message, or the whole year before it |
| «filen er ikke gyldig XML. …» or «filen er ikke en SAF-T Financial-fil» | Damaged, or not a SAF-T file | Download the SAF-T export again from the old program |
| «Filen er en PDF …», «… en kontoutskrift fra banken …», «… en EHF-faktura …» | Another kind of file, named by what it is | Upload the SAF-T file instead |
| «ZIP-filen har … SAF-T-filer, en for hvert år. …» | A ZIP with several years in it, such as Fiken's complete export | Unpack it and upload the XML for the right year, or bring every year over at once on «Hent regnskapet fra et annet program»: [Bring your books over from the previous program's SAF-T files](https://myntos.no/llms/help/opening-balance-and-year-end/bring-your-books-over-from-saf-t-files.md) |
| «ZIP-filen har ingen SAF-T-fil i seg …», «… er et regneark …» or «… har dokumenter, men ingen SAF-T-fil» | A ZIP or workbook without the SAF-T file | Upload the SAF-T file for the year |
| The button «Legg til kontoer og parter» is greyed out, with «Bare en administrator eller eier kan legge til kontoer i kontoplanen.» | The person is «Medlem» | Ask an administrator or owner to do this step |

## Good to know

- Only the balances are read here. The vouchers, this year's and earlier
  years', come over with the opening balance on «Hent regnskapet fra et annet
  program» (the box «Flytter du fra et annet regnskapsprogram?» at the top of
  this page links there):
  [Bring your books over from the previous program's SAF-T files](https://myntos.no/llms/help/opening-balance-and-year-end/bring-your-books-over-from-saf-t-files.md).
  The documents behind the vouchers stay in the old program's bilag archive,
  which must be kept for five years.
- Keep the SAF-T file itself: Myntos does not store it.

## Related

- [Import a saldobalanse](https://myntos.no/llms/help/opening-balance-and-year-end/import-a-saldobalanse.md)
- [Review an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/review-an-opening-balance.md)
- [Move your books to Myntos](https://myntos.no/llms/help/getting-started/move-your-books-to-myntos.md)
- [Bring your books over from the previous program's SAF-T files](https://myntos.no/llms/help/opening-balance-and-year-end/bring-your-books-over-from-saf-t-files.md)
- An agent with a key can do this through the API:
  [myntos-inngaende-balanse](https://myntos.no/llms/skills/myntos-inngaende-balanse.md)
