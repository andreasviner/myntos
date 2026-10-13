---
title: See every voucher in the books
description: How a person sees every posted voucher (bilag) in Myntos on the Bilag page, whatever made it (payments, invoices, VAT settlements, the opening balance, vouchers brought over from Tripletex or another program, manual vouchers, the year-end), opens one to see its lines, and narrows the list by month, where it came from, account or text. Use when someone cannot find their vouchers, asks whether an import from the old program came over, or wants to look a voucher up.
pages: /bilag/
skill: myntos-bilag
---

# See every voucher in the books

«Bilag» lists every voucher posted in the books for a year, oldest first, no
matter what made it. «Betaling» shows only the payments registered in Myntos,
so this is the page for everything else too: the opening balance, the vouchers
brought over from the previous accounting program, manual vouchers and the
year-end. It only shows; a posted voucher is never changed or deleted
(bokføringsloven), and a correction is made where the voucher was made.

## They might ask

- «Jeg har importert fra Tripletex, men finner ikke bilagene»
- «Hvor ser jeg alle bilagene?»
- «Hva står på bilag 2026-240?»
- "Where can I see all the entries in Myntos?"

## Before they start

Any role, «Leser» included.

## Steps

1. Open `https://myntos.no/bilag/`: «Se alle bilag →» under «Siste bevegelser»
   on «Oversikt», or «Se bilagene» after an import on «Hent regnskapet fra et
   annet program».
2. Choose «År», and if they want, «Måned».
3. «Ført fra» narrows to where the vouchers came from, each with how many
   there are in the year: «Betaling», «Faktura», «Mva-oppgjør», «Inngående
   balanse», «Tidligere program» (brought over from the old program),
   «Manuelt bilag», «Årsavslutning».
4. «Konto» narrows to vouchers with a line on one account, and «Søk» to text
   or a voucher number. Press «Vis». «Nullstill» clears the choices.
5. Each row has «Dato», «Bilag» (the number), «Tekst», «Ført fra» and «Beløp».
   A voucher from the old program shows under its text where its documentation
   is («Tripletex bilag 240-2026»). Press the number to open the voucher's
   lines: account, text, customer or supplier, VAT code, avdeling, debit and
   credit, and who registered it.
6. «Vis flere» loads the next 100.

## Good to know

- A struck-through row has been reversed; the reversal is its own row.
- The choices stay in the page's address, so the view can be bookmarked or
  shared with a colleague who has access to the same books.
- To see the money in and out of the bank with the balance:
  [See what went in and out of the bank](https://myntos.no/llms/help/vouchers/see-what-went-in-and-out-of-the-bank.md).

## Related

- [Bring your books over from the previous program's SAF-T files](https://myntos.no/llms/help/opening-balance-and-year-end/bring-your-books-over-from-saf-t-files.md)
- [Read the payment register](https://myntos.no/llms/help/payments/read-the-payment-register.md)
- An agent with a key can read the same through the API:
  [myntos-bilag](https://myntos.no/llms/skills/myntos-bilag.md)
