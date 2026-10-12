---
title: Deactivate or reactivate an account
description: How an administrator hides an account from new entries with Deaktiver on the Kontoplan page in Myntos, or brings it back with Aktiver, and what happens to the entries already on it. Use when someone wants to stop using an account, hide accounts they do not need, or a payment is refused because an account is deactivated.
pages: /kontoplan/
skill: myntos-kontoplan
---

# Deactivate or reactivate an account

An account that has been used can never be deleted, because its entries are
part of the books. Deactivating it is the alternative: it is no longer
offered for new entries, while everything already booked to it stays and is
still reported for the years that used it. It can be switched back on at any
time. Accounts Myntos uses itself («Brukes av Myntos») cannot be deactivated.

## They might ask

- «Hvordan skjuler jeg en konto jeg ikke bruker?»
- «Hvordan deaktiverer jeg en konto?»
- «Myntos sier at kontoen er deaktivert, hvordan slår jeg den på igjen?»
- "How do I deactivate an account in Myntos?"

## Before they start

- The role «Administrator» or «Eier»:
  [Who can change the chart of accounts](https://myntos.no/llms/help/chart-of-accounts/who-can-change-the-chart-of-accounts.md).

## Steps

1. Open the kontoplan: the company's name at the top right, «Innstillinger
   for selskapet», then the link «kontoplanen» in «Selskapet»
   (`https://myntos.no/kontoplan/`).
2. On the account's row, press «Deaktiver». The page says «Deaktivert.
   Kontoen tilbys ikke i nye føringer.», and the status becomes
   «Deaktivert».
3. To switch it back on, press «Aktiver» on the same row. The page says
   «Aktivert.»

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Deaktiver» on the row, status «Brukes av Myntos» | Myntos posts to that account itself (bank, cash, VAT, customers, suppliers, equity, year-end); switched off, every payment would be refused | Leave it on. It can still be renamed |
| No buttons at all | Their role is «Medlem» or «Leser» | Ask an «Administrator» or «Eier» |
| Under «Betaling» or «Bankavstemming»: «konto … er deaktivert. Aktiver den i kontoplanen, eller velg en annen konto» | A payment category books to an account that has been switched off | Press «Aktiver» on that account here, or choose another category |

## Good to know

- A payment category whose account is deactivated disappears from the list on
  «Betaling».
- Deactivating changes nothing already posted, and the account still shows in
  the years that used it.

## Related

- [Delete an account](https://myntos.no/llms/help/chart-of-accounts/delete-an-account.md)
- [Understand the chart of accounts](https://myntos.no/llms/help/chart-of-accounts/understand-the-chart-of-accounts.md)
- An agent with a key can do this through the API:
  [myntos-kontoplan](https://myntos.no/llms/skills/myntos-kontoplan.md)
