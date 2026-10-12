---
title: Change the main account
description: How an administrator chooses which of the company's bank accounts is the main account («Hovedkonto») that invoices are paid into, on the company page in Myntos. Use when someone wants invoices to show a different account number.
pages: /organisasjon/
skill: myntos-selskap
---

# Change the main account

When a company has more than one bank account, the one marked «Hovedkonto» is
the one an invoice asks the customer to pay into, unless that invoice is set
to another. There is always exactly one main account while the company has
any accounts at all.

## They might ask

- «Hvordan bytter jeg hvilket kontonummer som står på fakturaen?»
- «Hvordan gjør jeg en annen konto til hovedkonto?»
- "How do I change the default bank account on invoices in Myntos?"

## Before they start

- The role «Administrator» or «Eier» in this company.
- The account must already be on the list:
  [Add a bank account](https://myntos.no/llms/help/company-and-access/add-a-bank-account.md).

## Steps

1. Open the company page: click the company's name at the top right and
   choose «Innstillinger for selskapet» (`https://myntos.no/organisasjon/`).
2. In «Selskapet», under «Kontonummer», find the account that should be the
   main one.
3. Press «Gjør til hovedkonto» on its line. The list says «… er nå
   hovedkonto.», the account moves to the top with the word «Hovedkonto», and
   the previous main account loses it.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Gjør til hovedkonto» on any line | Their role is «Leser» or «Medlem», or the company has only one account | Ask an «Administrator» or «Eier»; with one account, it is the main one already |

## Good to know

- Invoices already issued keep the account number they were issued with.
  Only invoices made from now on use the new main account.
- A single invoice can still name another of the company's accounts; that is
  chosen on the invoice itself.
- Removing the main account makes the oldest remaining account the main one.

## Related

- [Add a bank account](https://myntos.no/llms/help/company-and-access/add-a-bank-account.md)
- [Remove a bank account](https://myntos.no/llms/help/company-and-access/remove-a-bank-account.md)
- An agent with a key can read which account is the main one, but not change
  it: [myntos-selskap](https://myntos.no/llms/skills/myntos-selskap.md)
