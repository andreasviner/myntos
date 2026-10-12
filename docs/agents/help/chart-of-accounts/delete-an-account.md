---
title: Delete an account
description: How an administrator deletes an account that was added to the chart of accounts (kontoplan) by mistake, with Slett on the Kontoplan page in Myntos, and why an account that has been used can only be deactivated. Use when someone wants to remove an account from the kontoplan.
pages: /kontoplan/
skill: myntos-kontoplan
---

# Delete an account

Deleting is only for an account added by mistake that has never been used:
nothing posted to it, no opening balance on it, and no customer or supplier
settled against it. Every other account is part of the books' history, which
the company may have to show for years, and is deactivated instead. Accounts
Myntos uses itself can be neither deleted nor deactivated.

## They might ask

- «Hvordan sletter jeg en konto i kontoplanen?»
- «Jeg la til feil konto, hvordan fjerner jeg den?»
- «Hvorfor finnes det ingen slett-knapp på kontoen?»
- "How do I delete an account in Myntos?"

## Before they start

- The role «Administrator» or «Eier»:
  [Who can change the chart of accounts](https://myntos.no/llms/help/chart-of-accounts/who-can-change-the-chart-of-accounts.md).

## Steps

1. Open the kontoplan: the company's name at the top right, «Innstillinger
   for selskapet», then the link «kontoplanen» in «Selskapet»
   (`https://myntos.no/kontoplan/`).
2. On the account's row, press «Slett». The button is only there for an
   account nothing has been posted to.
3. The browser asks «Slette konto … ?». Press OK to delete it. This cannot be
   undone, but the account can be added again with the same number.
4. The page says «Slettet.»

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Slett» on the row | Something has been posted to the account, or its status is «Brukes av Myntos» | Deactivate it instead: [Deactivate or reactivate an account](https://myntos.no/llms/help/chart-of-accounts/deactivate-an-account.md) |
| «konto … kan ikke slettes fordi den har en inngående balanse. Deaktiver den i stedet, …» | The account carries an opening balance | Deactivate it instead |
| «konto … kan ikke slettes fordi en kunde eller leverandør avregnes mot den. …» | A customer or supplier is settled against it | Deactivate it instead |
| No buttons at all | Their role is «Medlem» or «Leser» | Ask an «Administrator» or «Eier» |

## Good to know

- A deactivated account disappears from new entries just as well, and keeps
  the history readable.

## Related

- [Deactivate or reactivate an account](https://myntos.no/llms/help/chart-of-accounts/deactivate-an-account.md)
- [Add an account](https://myntos.no/llms/help/chart-of-accounts/add-an-account.md)
- An agent with a key can do this through the API:
  [myntos-kontoplan](https://myntos.no/llms/skills/myntos-kontoplan.md)
