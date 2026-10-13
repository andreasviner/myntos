---
title: Import a saldobalanse
description: How a person loads the saldobalanse CSV exported from their previous accounting program (Tripletex, Fiken, Visma, PowerOffice, Uni and others) into the opening balance form in Myntos, checks it and posts it. Use when someone has moved their books to Myntos and asks how to bring last year's balances over from a file.
pages: /inngaende-balanse/
skill: myntos-inngaende-balanse
---

# Import a saldobalanse

A saldobalanse (trial balance) is the list of every account and its balance
that any accounting program can export. Taken at the end of the last year kept
in the old program, it is exactly the opening balance (inngående balanse)
Myntos needs. Loading the file fills in the form; **nothing is posted until the
person has checked it and pressed «Før inngående balanse»**, and once posted
it cannot be edited, only corrected with a reversal and a new balance.

## They might ask

- «Kan jeg importere saldobalansen fra Tripletex til Myntos?»
- «Hvordan får jeg inn tallene fra Fiken?»
- «Hvilket filformat vil Myntos ha for saldobalansen?»
- "How do I import my trial balance into Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- The saldobalanse from the old program **as a CSV file**, exported **after**
  the old year was closed («saldobalanse etter årsoppgjøret»), so that the
  result is already moved to equity. A PDF or Excel file will not load; most
  programs can export the saldobalanse report as CSV, and an Excel sheet can
  be saved as CSV.
- The file needs a heading row, one heading «Konto» or «Kontonr», and the
  balance either in a column called «Utgående saldo», «UB» or «Saldo», or in
  two columns «Debet» and «Kredit».
- The company's first year in Myntos must still be open.

## Steps

1. Open `https://myntos.no/inngaende-balanse/` (from «Oversikt», the link
   «Inngående balanse» in the note under «Største poster i år»).
2. In the card «Saldoer per 1. januar …», use «Last inn SAF-T-fil eller
   saldobalanse» and choose the file. (A SAF-T file is read differently:
   [Import the opening balance from a SAF-T file](https://myntos.no/llms/help/opening-balance-and-year-end/import-a-saf-t-file.md).)
3. Myntos fills the rows and says «Leste … kontoer fra filen. Se over linjene,
   fordel 1500 og 2400 på kunder og leverandører hvis du vil, og kontroller.»
   The file itself is not stored.
4. Look over the rows. Positive balances are put under «Debet» and negative
   under «Kredit»; if assets have landed under «Kredit», the file uses the
   opposite sign and the figures must be swapped.
5. Optionally split 1500 (customers) and 2400 (suppliers) per unpaid invoice:
   [Split customers and suppliers in the opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/split-customers-and-suppliers-in-the-opening-balance.md).
6. Press «Kontroller», read «Gjennomgå før føring», and fix anything in red
   with «Tilbake til skjemaet»:
   [Review an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/review-an-opening-balance.md).
7. Press «Før inngående balanse». This cannot be undone. The page says
   «Inngående balanse er ført som bilag …».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «fant ingen kolonne for kontonummer. …» | No heading called «Konto» or «Kontonr» | Rename the account column's heading in the file, or check the first row is headings |
| «fant ingen kolonne for saldo. …» | No balance column Myntos recognises | Rename the closing-balance column to «Utgående saldo», or use «Debet» and «Kredit» |
| «fant ingen kolonner i filen; den må være CSV» | Not a CSV file | Export or save the report as CSV |
| «linje … (konto …): beløpet kan ikke leses …» | An amount on that line is not a number | Fix that cell in the file |
| «fant ingen kontoer med saldo i filen» | Every balance is zero, or no account numbers were found | Check it is the right report and year |
| «filen er for stor til å være en saldobalanse» | Over 2 MB | Export only the saldobalanse, not the general ledger |
| In the review: «… er en resultatkonto. …» | The file was taken before the old year was closed, so income and cost accounts are in it | Ask the old accountant for the saldobalanse after the year-end close |

## Good to know

- Lines on 2700 and 2710 (VAT) are refused; VAT not settled at the turn of the
  year goes on 2740 instead.
- Accounts the company's chart of accounts lacks are flagged in the review;
  add them under «Kontoplan» or move the balance to the matching account.
- Keep the old year's approved annual accounts and the saldobalanse itself:
  they are the documentation for the opening balance, and Myntos does not keep
  the file.

## Related

- [Enter an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/enter-an-opening-balance.md)
- [Review an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/review-an-opening-balance.md)
- [Move your books to Myntos](https://myntos.no/llms/help/getting-started/move-your-books-to-myntos.md)
- An agent with a key can do this through the API:
  [myntos-inngaende-balanse](https://myntos.no/llms/skills/myntos-inngaende-balanse.md)
