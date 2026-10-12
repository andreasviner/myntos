---
title: Rename an account
description: How an administrator changes the name of an account in the chart of accounts (kontoplan) on the Kontoplan page in Myntos with Endre navn, and why the number cannot be changed. Use when someone wants an account to have a different name, or asks whether they can change an account number.
pages: /kontoplan/
skill: myntos-kontoplan
---

# Rename an account

Any account can be renamed, also one that has entries and the ones Myntos
uses itself. The name is just a label: the entries point at the account, and
the SAF-T file uses the current name for every year. The number cannot be
changed, ever, because every entry and every export refers to it.

## They might ask

- «Hvordan endrer jeg navnet på en konto?»
- «Kan jeg endre kontonummeret på en konto?»
- "How do I rename an account in Myntos?"

## Before they start

- The role «Administrator» or «Eier»:
  [Who can change the chart of accounts](https://myntos.no/llms/help/chart-of-accounts/who-can-change-the-chart-of-accounts.md).

## Steps

1. Open the kontoplan: the company's name at the top right, «Innstillinger
   for selskapet», then the link «kontoplanen» in «Selskapet»
   (`https://myntos.no/kontoplan/`).
2. On the account's row, press «Endre navn».
3. Change the text in the box.
4. Press «Lagre». The page says «Lagret.» «Avbryt» leaves the name as it was.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Endre navn» | Their role is «Medlem» or «Leser», or another row is open for editing | Ask an «Administrator» or «Eier»; or finish the open row first |
| «String should have at least 1 character» | The box is empty | Write a name |

## Good to know

- Only one row can be edited at a time; the other rows' buttons come back
  after «Lagre» or «Avbryt».
- If the account is in the wrong class (a cost among the assets, say), a new
  name does not fix it. Add the right account and deactivate the old one:
  [Add an account](https://myntos.no/llms/help/chart-of-accounts/add-an-account.md),
  [Deactivate or reactivate an account](https://myntos.no/llms/help/chart-of-accounts/deactivate-an-account.md).

## Related

- [Understand the chart of accounts](https://myntos.no/llms/help/chart-of-accounts/understand-the-chart-of-accounts.md)
- An agent with a key can do this through the API:
  [myntos-kontoplan](https://myntos.no/llms/skills/myntos-kontoplan.md)
