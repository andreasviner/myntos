---
title: Choose the account an invoice is paid to
description: How a person chooses which of the company's bank accounts an invoice tells the customer to pay into, with «Betales til» on the Faktura page in Myntos. Use when someone asks how to put another account number on an invoice, or which account number the invoice shows.
pages: /faktura/
skill: myntos-faktura
---

# Choose the account an invoice is paid to

The account number on an invoice is always one of the company's own accounts,
registered on the company page. It is never typed on the invoice itself.
A company with one account gets that account on every invoice and sees no
choice. A company with more than one gets the field «Betales til», set to the
main account until someone picks another.

## They might ask

- «Hvordan endrer jeg kontonummeret på fakturaen?»
- «Hvilket kontonummer står på fakturaen?»
- «Kan jeg velge en annen konto for denne fakturaen?»
- "How do I change the bank account on an invoice in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- At least two accounts on the company for the choice to appear. Accounts are
  added by an «Administrator» or «Eier»:
  [Add a bank account](https://myntos.no/llms/help/company-and-access/add-a-bank-account.md).

## Steps

1. Open «Faktura» in the top menu (`https://myntos.no/faktura/`).
2. Under «Levering og betaling», choose the account in «Betales til». Each
   choice shows the number and the account's name.
3. Press «Se over fakturaen» and check «Betales til» in the review before
   issuing.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Betales til» field | The company has only one account (or none) | With one, that one is used. With none: [Add a bank account](https://myntos.no/llms/help/company-and-access/add-a-bank-account.md) |
| The account number itself is wrong | It is wrong on the company page | An «Administrator» or «Eier» corrects it there: [Correct a bank account](https://myntos.no/llms/help/company-and-access/correct-a-bank-account.md) |

## Good to know

- An issued invoice keeps the account number it was issued with, even if the
  company's accounts change later.
- Which account is the main one is set on the company page:
  [Change the main account](https://myntos.no/llms/help/company-and-access/change-the-main-account.md).
- An AI agent with a key can choose among the company's accounts, but can
  never add or change an account number.

## Related

- [Issue an invoice](https://myntos.no/llms/help/invoices/issue-an-invoice.md)
- An agent with a key can choose the account through the API:
  [myntos-faktura](https://myntos.no/llms/skills/myntos-faktura.md)
