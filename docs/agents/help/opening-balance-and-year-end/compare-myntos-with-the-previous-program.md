---
title: Compare Myntos with the previous program
description: How a person who books in both Myntos and their old accounting program for a while (a parallel run) checks whether the two agree, by uploading a fresh SAF-T file from the old program on the page «Hent regnskapet fra et annet program» and reading the differences per account, per customer and supplier, and per VAT term. Use when someone asks whether Myntos and Tripletex (or Fiken, PowerOffice, Visma) agree, or why their figures differ.
pages: /tidligere-program/
skill: myntos-tidligere-program
---

# Compare Myntos with the previous program

During a parallel run («parallellkjøring») the person enters every new bilag in
both Myntos and the old program, and wants to know whether the two books agree.
Myntos answers from a fresh **SAF-T file** from the old program: it compares
what each program has booked from 1 January, account by account, customer and
supplier by customer and supplier, and VAT term by VAT term, and says in which
months an account differs. The file is read on Myntos's server and deleted once
compared; nothing in either program changes.

## They might ask

- «Stemmer Myntos med Tripletex?»
- «Hvorfor er mva-en ulik i Myntos og Fiken?»
- «Hvordan sjekker jeg parallellkjøringen?»
- "How do I check that Myntos and my old program agree?"

## Before they start

- Any role, «Leser» included.
- A fresh SAF-T file from the old program for the year, from 1 January, as ZIP
  or XML, in one part.
- For the comparison to mean anything, the year so far must be in Myntos:
  [Bring your books over from the previous program's SAF-T files](https://myntos.no/llms/help/opening-balance-and-year-end/bring-your-books-over-from-saf-t-files.md).

## Steps

1. Open `https://myntos.no/tidligere-program/`.
2. In the card «Sammenlign med det gamle programmet», choose the file under «Ny
   SAF-T-fil fra det gamle programmet». Leave «Til og med (valgfritt)» empty to
   compare up to the last voucher in the file, or pick a day, such as the end of
   a VAT term.
3. Press «Last opp og sammenlign». It shows «Laster opp …» while the file goes
   up, then what the server is doing; the answer appears in the same card. The
   last comparison stays there when they come back to the page.
4. Read the answer:
   - «Alt stemmer fra … til …: …» means both programs have booked the same.
   - «De to programmene skiller lag fra … til …: …» is followed by the tables
     «Kontoer» (with «I filen», «I Myntos», «Forskjell» and «Måneder»),
     «Kunder og leverandører» and «Mva per termin». «Hva som ble sammenlignet»
     opens what the comparison covered.

## Reading the differences

- **Look at the months.** An account that differs only in «oktober» points at
  a bilag from October entered in one program and not the other, or on another
  account or another day.
- **«Forskjell» is Myntos less the old program**, debit positive: a cost
  account with a positive difference has more cost in Myntos.
- **The VAT accounts are one row**, «2740 Merverdiavgift, alle mva-kontoer
  samlet». Each program moves 2700 and 2710 to 2740 only when it files the
  return itself, so comparing them one by one would always differ. Whether the
  VAT is the same is in «Mva per termin», per VAT code.
- **A supplier can differ while the cost and the bank agree.** The old program
  books an invoice to «Leverandørgjeld» (2400) and the payment against it;
  Myntos books a paid purchase straight from the bank, without 2400.
- **The opening balance is left out** of both sides, so the decisions made
  there (last year's result on 2050, the driftskreditt on 1920) do not show as
  differences.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «filen er regnskapet til org.nr. …» | Another company's file | Choose the right company at the top, or export this company's file |
| «filen gjelder … Sammenligningen trenger en fil fra 1. januar …» | The file does not start on 1 January | Export the whole year |
| «SAF-T-filen er delt i … deler …» | The program split the file | Export a shorter period, from 1 January |
| «filen sier at den har … bilag, men Myntos fant …» | The download was cut short | Download it again |

## Related

- [Bring your books over from the previous program's SAF-T files](https://myntos.no/llms/help/opening-balance-and-year-end/bring-your-books-over-from-saf-t-files.md)
- [See what is owed for a VAT period](https://myntos.no/llms/help/vat/see-what-is-owed-for-a-termin.md)
- An agent with a key can do this through the API:
  [myntos-tidligere-program](https://myntos.no/llms/skills/myntos-tidligere-program.md)
