---
title: Mark an account as a bank account
description: How an administrator marks an account other than 1920 as one of the company's bank accounts with «Merk som bankkonto» on the Kontoplan page in Myntos, so the overview counts the money on it, and how to undo it with «Ikke bankkonto». Use when the overview shows «Inn i år 0,00» although money moved, or the bank from another program (often a driftskreditt on 2390) is missing from «Kasse og bank».
pages: /kontoplan/
skill: myntos-kontoplan
---

# Mark an account as a bank account

Myntos posts every new payment on 1920 Bankinnskudd (or 1900 Kontanter), and
the overview counts the money there. Books brought over from another program
may keep the company's bank somewhere else: Tripletex keeps a farm's operating
credit line (driftskreditt) on 2390. Until that account is marked as a bank
account, the overview shows no money in or out on it. Marking moves nothing
and posts nothing, and it can be undone.

## They might ask

- «Oversikten viser «Inn i år 0,00», men det har gått penger inn og ut»
- «Banken vår står på 2390 driftskreditt, hvordan får jeg den med på oversikten?»
- «Hvordan merker jeg en konto som bankkonto?»
- "Myntos shows no money in or out after I imported my books"

## Before they start

- The role «Administrator» or «Eier»:
  [Who can change the chart of accounts](https://myntos.no/llms/help/chart-of-accounts/who-can-change-the-chart-of-accounts.md).
- Books brought in with «Hent regnskapet» on «Tidligere program» are usually
  marked already: when the file names the company's kontonummer in an
  account's name, the import marks that account and says so in the list of
  notes. Check the row's status first.

## Steps

1. Open the kontoplan: the company's name at the top right, «Innstillinger
   for selskapet», then the link «kontoplanen» in «Selskapet»
   (`https://myntos.no/kontoplan/`).
2. Find the account the money is on, often one whose name holds the bank's
   kontonummer. Its status says «Aktiv», or «Aktiv, bankkonto» when it is
   marked already.
3. Press «Merk som bankkonto» on its row. The page says «Merket som
   bankkonto. Oversikten regner kontoen med i banken.», and the status
   becomes «Aktiv, bankkonto».
4. To undo it, press «Ikke bankkonto» on the same row. The page says «Ikke
   lenger bankkonto. Oversikten regner ikke kontoen med i banken.»

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Merk som bankkonto» on the row | Only accounts in class 1 (assets) and 2 (debt, such as a credit line) can hold the company's money, and 1920 and 1900 («Brukes av Myntos») always count | Nothing to do for 1920 and 1900. An income or cost account is not a bank account |
| No buttons at all | Their role is «Medlem» or «Leser» | Ask an «Administrator» or «Eier» |

## Good to know

- After marking, «Inn i år», «Ut i år» and «Netto» on «Oversikt», and «Kasse
  og bank» beside the register on «Betaling», include the account. A credit
  line that is drawn shows as a negative bank balance, which is true.
- New payments still go to 1920. Moving the balance from 2390 to 1920 instead
  is a manual voucher, and a question for the accountant:
  [Make the year-end adjustments](https://myntos.no/llms/help/opening-balance-and-year-end/make-year-end-adjustments.md).
- Marking adds no kontonummer anywhere: which account an invoice is paid to
  is set under «Innstillinger for selskapet».

## Related

- [Read the overview](https://myntos.no/llms/help/overview/read-the-overview.md)
- [Understand the chart of accounts](https://myntos.no/llms/help/chart-of-accounts/understand-the-chart-of-accounts.md)
- An agent with a key can do this through the API:
  [myntos-kontoplan](https://myntos.no/llms/skills/myntos-kontoplan.md)
