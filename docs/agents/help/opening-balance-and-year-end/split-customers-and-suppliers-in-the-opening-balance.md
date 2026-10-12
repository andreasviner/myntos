---
title: Split customers and suppliers in the opening balance
description: How a person enters unpaid customer and supplier invoices (åpne poster) one by one on accounts 1500 and 2400 in the opening balance in Myntos, instead of one total. Use when someone asks how to bring over who owed the company money, or whom it owed, at the start of the year, so later payments can be matched.
pages: /inngaende-balanse/
skill: myntos-inngaende-balanse
---

# Split customers and suppliers in the opening balance

Account 1500 (kundefordringer) holds what customers owe the company and 2400
(leverandørgjeld) what it owes suppliers. In the opening balance each can be
one total, or one line per unpaid invoice with the customer or supplier named
(åpne poster). Splitting is optional, but it means each customer's balance is
right from the first day, a payment received in January can be matched to the
invoice it pays, and the SAF-T file carries an opening balance per customer and
supplier. **It is all or nothing per account**: either every line on 1500 names
a customer, or none does.

## They might ask

- «Hvordan fører jeg kundefordringer i inngående balanse?»
- «Tre kunder skyldte oss penger ved nyttår, hvordan legger jeg dem inn?»
- «Hva er åpne poster i inngående balanse?»
- "How do I enter open customer invoices in the opening balance?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- A list of the unpaid invoices at the turn of the year, per customer and per
  supplier, from the old program's customer and supplier ledger
  (kundereskontro, leverandørreskontro). The lines must add up to the 1500 and
  2400 balances.
- Every customer and supplier on the list registered in Myntos, on
  `https://myntos.no/parter/` («Kunder og leverandører»).

## Steps

1. Open `https://myntos.no/inngaende-balanse/` and fill in or load the opening
   balance as usual:
   [Enter an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/enter-an-opening-balance.md)
   or [Import a saldobalanse](https://myntos.no/llms/help/opening-balance-and-year-end/import-a-saldobalanse.md).
2. Find the row for 1500. Replace it with one row per unpaid customer invoice:
   press «Legg til linje», type `1500` under «Konto», the invoice number under
   «Tekst», pick the customer under «Kunde/leverandør», and put the amount
   under «Debet». Press «Fjern» on the original 1500 total.
3. Do the same for 2400 with each unpaid supplier invoice, the supplier picked
   under «Kunde/leverandør» and the amount under «Kredit».
4. Press «Kontroller» and check that «Sum» still balances and that no red
   message names 1500 or 2400.
5. Press «Før inngående balanse» when it is right. This cannot be undone.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Konto 1500 er delvis fordelt på kunder eller leverandører. …» | Some 1500 lines name a customer and some do not | Give every line on that account a customer, or remove all of them |
| «Linje … (konto …): … avregnes mot en annen konto enn …» | A supplier was picked on 1500, or a customer on 2400 | Customers on 1500, suppliers on 2400 |
| «Debet … og kredit … går ikke i null …» | The invoice lines do not add up to the old total | Compare them with the customer or supplier ledger from the old program |
| The customer is not in the list | Not registered in Myntos yet | Add it under «Kunder og leverandører», then reload the opening balance page. Reloading empties the form, so register everyone before typing |
| A grey note «Saldoen på 1500 er ikke fordelt på kunder. …» | 1500 is one total | Allowed; split it if they want payments matched to invoices |

## Good to know

- A loaded saldobalanse never fills in customers or suppliers: the file does
  not say who owes what.
- The invoices themselves are not recreated in Myntos; only the amounts owed
  are carried over.

## Related

- [Enter an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/enter-an-opening-balance.md)
- [Review an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/review-an-opening-balance.md)
- An agent with a key can do this through the API:
  [myntos-inngaende-balanse](https://myntos.no/llms/skills/myntos-inngaende-balanse.md)
