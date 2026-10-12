---
title: Move to Myntos from DNB Regnskap, SpareBank 1, Visma or another program
description: How a person finds the SAF-T export in an accounting program Myntos has no specific guide for (DNB Regnskap, SpareBank 1 Regnskap, Visma, Luca and others), what else to download, and what to do with no SAF-T at all, following the Flytte fra et annet program guide. Use when someone asks how to move to Myntos from a program other than Fiken, Tripletex, PowerOffice Go or Conta.
pages: /kom-i-gang/
---

# Move to Myntos from another program

Every Norwegian accounting system must be able to deliver SAF-T Regnskap; it
is Skatteetaten's requirement, not a feature the vendor chooses. So even when
the menu looks nothing like the other guides, the file exists. Spreadsheets
and old desktop programs are the exception: they have no SAF-T, and the
saldobalanse plus the folder of bilag is what the person needs.

## They might ask

- «Hvordan flytter jeg fra DNB Regnskap til Myntos?»
- «Jeg bruker Visma, hvordan får jeg ut regnskapet?»
- «Jeg har ført i Excel, kan jeg begynne i Myntos?»
- "How do I move to Myntos from Visma?"

## Before they start

Read the general order first:
[Move your books to Myntos](https://myntos.no/llms/help/getting-started/move-your-books-to-myntos.md).
The guide is at `https://myntos.no/kom-i-gang/annet-program/`, headed «Flytte
fra et annet program til Myntos».

## Steps

As the guide «Hent dataene ut av et annet program» describes:

1. **«Let etter ordet SAF-T».** It is almost always in one of three places:
   under the settings («Eksport»), under the reports («Til Skatteetaten»), or
   on the chart of accounts. The vendor's help search is faster than the
   menus.
2. **«Velg riktig versjon hvis du blir spurt».** v1.30 for periods up to and
   including 2026; v1.40 is mandatory for periods from 1 January 2027. If
   they are not asked, the program chooses.
3. **«Last ned bilagsarkivet i tillegg».** SAF-T holds the entries, not
   necessarily the receipts. Look for a separate export of bilag,
   attachments or documents.
4. **«Får du den ikke ut, be om den skriftlig».** If there is no export in the
   program, ask the vendor or the accountant for the SAF-T file by e-mail.
   That gives both the file and a record of having asked.

Then in Myntos: create the account, set up the books, add customers and
suppliers, enter the opening balance and register payments from the
changeover date, as in the general article.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No SAF-T anywhere, in a spreadsheet or desktop program | Those have none. | Take the saldobalanse for the last closed year and the bilag folder, and enter the opening balance from the saldobalanse. |
| The accountant holds the books | Common for clients of an accounting firm. | Ask for the SAF-T file and the bilag in writing. The data is the company's. |

## Good to know

- **DNB Regnskap and SpareBank 1 Regnskap** are both built on Unimicro and
  have the same export menu.
- **What to keep** (the guide's «Ta vare på dette»): SAF-T Regnskap for each
  financial year; bilag, attachments and sent invoices as PDF; the customer
  and supplier list, preferably as CSV; payslips and the a-meldinger sent, if
  they ran payroll.
- **Myntos does not import the SAF-T file.** The opening balance is entered
  on «Inngående balanse»:
  [Enter an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/enter-an-opening-balance.md).

## Related

- [Move your books to Myntos from another program](https://myntos.no/llms/help/getting-started/move-your-books-to-myntos.md)
- [Move from Fiken](https://myntos.no/llms/help/getting-started/move-from-fiken.md)
- [Move from Tripletex](https://myntos.no/llms/help/getting-started/move-from-tripletex.md)
