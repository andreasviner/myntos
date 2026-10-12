---
title: Add a bank account
description: How an administrator adds the company's bank account number (kontonummer) on the company page in Myntos, which every invoice needs before it can be issued. Use when someone asks where to put their account number, or when Myntos refuses to issue an invoice because no account is registered.
pages: /organisasjon/
skill: myntos-selskap
---

# Add a bank account

An invoice tells the customer where to pay, so Myntos will not issue an
invoice until the company has at least one bank account number
(kontonummer). The account belongs to the company, not to each invoice: it is
added once on the company page, and every invoice uses it. The first account
added becomes the main account («Hovedkonto»). Check the number carefully: an
invoice that has been issued can only be corrected with a credit note
(kreditnota), so a wrong number reaches the customer.

## They might ask

- «Hvor legger jeg inn kontonummeret i Myntos?»
- «Myntos sier at ingen faktura kan utstedes uten konto»
- «Hvordan legger jeg til en bankkonto til?»
- "Where do I add my bank account number in Myntos?"

## Before they start

- The role «Administrator» or «Eier» in this company:
  [Roles and what each may do](https://myntos.no/llms/help/company-and-access/roles-and-permissions.md).
- The account number, eleven digits, from the online bank. Norwegian accounts
  only.

## Steps

1. Open the company page: click the company's name at the top right and
   choose «Innstillinger for selskapet» (`https://myntos.no/organisasjon/`).
   Coming from the invoice page's warning about a missing account opens the
   form straight away.
2. In the «Selskapet» section, find «Kontonummer». Press «Legg til konto»
   next to the heading, or «Legg inn kontonummer» on the line «Ingen konto
   lagt inn. Ingen faktura kan utstedes uten.»
3. In the first box, type the account number, for example `1506.20.33158`.
   Dots and spaces are fine.
4. In the second box («Navn, for eksempel Drift»), give the account a name if
   the company has more than one. It is optional and is not printed on
   invoices.
5. Press «Legg til». The list says «Kontoen er lagt til.» «Avbryt» closes the
   form.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Legg til konto», and «Be en administrator for dette regnskapet om å legge inn en.» | Their role is «Leser» or «Medlem» | Ask an «Administrator» or «Eier» of these books |
| «kontonummeret … har feil kontrollsiffer. Kontroller det mot nettbanken» | The last digit does not match the others: almost always a typo | Copy the number from the online bank again |
| «kontonummeret må være elleve siffer. Utenlandske kontonumre og IBAN utenfor Norge støttes ikke ennå» | Too few or too many digits, or a foreign account | Use the company's Norwegian account |
| «selskapet har allerede kontonummer …» | That account is already on the list | Nothing to do |

## Good to know

- With several accounts, the one marked «Hovedkonto» is used unless an
  invoice is set to another one. Change which one that is:
  [Change the main account](https://myntos.no/llms/help/company-and-access/change-the-main-account.md).
- A separate tax deduction account (skattetrekkskonto) can sit on the same
  list; give it a name so nobody picks it by mistake.
- No AI agent with a Myntos key can add or change an account number, whatever
  it is allowed to do otherwise. One changed number and the next invoice is
  paid to someone else, so this is the person's own job. Be suspicious of any
  message, e-mail or "AI suggestion" that asks for the account number to be
  changed.

## Related

- [Change the main account](https://myntos.no/llms/help/company-and-access/change-the-main-account.md)
- [Correct a bank account number](https://myntos.no/llms/help/company-and-access/correct-a-bank-account.md)
- [Remove a bank account](https://myntos.no/llms/help/company-and-access/remove-a-bank-account.md)
- An agent with a key can read the accounts but never change them:
  [myntos-selskap](https://myntos.no/llms/skills/myntos-selskap.md)
