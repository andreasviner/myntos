---
title: Remove a bank account
description: How an administrator removes a bank account the company no longer uses from the company page in Myntos, and what happens to invoices that named it. Use when someone has closed an account or added one by mistake.
pages: /organisasjon/
skill: myntos-selskap
---

# Remove a bank account

An account the company no longer uses can be removed from the list. Invoices
already issued keep the number printed on them, so nothing that has gone out
changes. A removed account can be added again later.

## They might ask

- «Hvordan sletter jeg en bankkonto i Myntos?»
- «Vi har byttet bank, hvordan fjerner jeg den gamle kontoen?»
- "How do I remove an old bank account in Myntos?"

## Before they start

- The role «Administrator» or «Eier» in this company.
- If the company has changed bank, add the new account first, so invoices
  always have somewhere to be paid:
  [Add a bank account](https://myntos.no/llms/help/company-and-access/add-a-bank-account.md).

## Steps

1. Open the company page: click the company's name at the top right and
   choose «Innstillinger for selskapet» (`https://myntos.no/organisasjon/`).
2. In «Selskapet», under «Kontonummer», press «Fjern» on the account's line.
3. The browser asks «Fjerne …? Fakturaer som alt er utstedt beholder nummeret
   sitt.» Press OK. The list says «Kontoen er fjernet.»

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Fjern» | Their role is «Leser» or «Medlem» | Ask an «Administrator» or «Eier» |
| «Ingen konto lagt inn. Ingen faktura kan utstedes uten.» after removing | It was the last account | Add the account that should be used: [Add a bank account](https://myntos.no/llms/help/company-and-access/add-a-bank-account.md) |

## Good to know

- Removing the main account («Hovedkonto») makes the oldest remaining account
  the main one. Check that it is the right one afterwards.
- A draft invoice that pointed at the removed account switches to the main
  account the next time it is saved.
- With no account at all, no invoice can be issued.

## Related

- [Add a bank account](https://myntos.no/llms/help/company-and-access/add-a-bank-account.md)
- [Change the main account](https://myntos.no/llms/help/company-and-access/change-the-main-account.md)
- An agent with a key can read the accounts but never change them:
  [myntos-selskap](https://myntos.no/llms/skills/myntos-selskap.md)
