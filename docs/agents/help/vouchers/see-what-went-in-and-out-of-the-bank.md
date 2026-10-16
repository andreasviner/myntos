---
title: See what went in and out of the bank
description: How a person sees every movement on the company's bank account (1920) or any other account in Myntos, with the balance carried after each voucher, on the Bilag page with «Inn og ut av banken (1920)» or «Konto». Use when someone asks where the payments in and out are, what the bank balance was on a date, or what is posted on an account, including vouchers brought over from another program.
pages: /bilag/
skill: myntos-bilag
---

# See what went in and out of the bank

On «Bilag», one account at a time is shown as its ledger: every voucher with a
line on it, how much went in and out, and the balance after each. On 1920
Bankinnskudd that is the company's money in and out of the bank, whatever
registered it: payments in Myntos, invoices paid, the opening balance, and the
vouchers brought over from the previous program.

## They might ask

- «Hvor finner jeg oversikten over inn- og utbetalinger?»
- «Hva sto på bankkontoen 1. april?»
- «Hva er ført på konto 4020 i år?»
- "Where do I see money in and out of the bank in Myntos?"

## Before they start

Any role, «Leser» included.

## Steps

1. Open `https://myntos.no/bilag/` (or «Inn og ut av banken» after an import).
2. Press «Inn og ut av banken (1920)». For another account, type its number
   in «Konto» and press «Vis».
3. The line above the table says the account, how many vouchers, and the
   balance before and after the period («Saldo før perioden …, etter …»).
4. The table has «Inn», «Ut» and «Saldo»: the balance after each voucher.
   Press a voucher's number to see its lines.
5. «Måned» narrows it to one month; the balance before the period is then the
   balance at the end of the month before.

## Good to know

- The opening balance is the first «Inn» or «Ut» of the first year in Myntos.
- An income or cost account starts every year at zero, as in the annual
  accounts. A balance-sheet account (the bank, customers, suppliers) carries
  its balance from year to year.
- A company whose old program kept the bank on another account (Tripletex
  often keeps a farm's driftskreditt on 2390) sees the money there unless the
  opening balance moved it to 1920. Choose that account in the list to follow
  it. Marked as a bank account on «Kontoplan», it is counted on «Oversikt»
  too: [Mark an account as a bank account](https://myntos.no/llms/help/chart-of-accounts/mark-an-account-as-a-bank-account.md).

## Related

- [See every voucher in the books](https://myntos.no/llms/help/vouchers/see-every-voucher.md)
- [Understand the bank reconciliation](https://myntos.no/llms/help/bank/understand-the-bank-reconciliation.md)
- An agent with a key can read the same through the API:
  [myntos-bilag](https://myntos.no/llms/skills/myntos-bilag.md)
