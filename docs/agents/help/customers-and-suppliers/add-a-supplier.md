---
title: Add a supplier
description: How a person registers a supplier (leverandør) on the «Kunder og leverandører» page in Myntos, so payments can be recorded against them. Use when someone asks how to add or create a supplier or vendor in Myntos.
pages: /parter/
skill: myntos-parter
---

# Add a supplier

A supplier is someone the company buys from. Registering one lets the
payments to them be recorded against a named counterparty, which is what an
auditor asks for and what the SAF-T export carries as the supplier ledger.
Myntos gives each supplier a number from 20000 and up. **Whether it is a
customer or a supplier, and its number, can never be changed afterwards**, and
a supplier cannot be deleted, so check the list first.

## They might ask

- «Hvordan legger jeg til en leverandør?»
- «Hvor registrerer jeg leverandørene mine?»
- "How do I add a supplier in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».

## Steps

1. Open the page `https://myntos.no/parter/`. It is reached with «Kunder og
   leverandører» at the top right of the «Faktura» page, or with «Se
   motpartene dine» under the counterparty field on the «Betaling» page.
2. Look in the list on the right that the supplier is not there already.
3. In «Legg til», choose «Leverandør».
4. Fill in «Navn», and «Organisasjonsnummer (valgfritt)» for a Norwegian
   business. Fill in «Postnummer» and «Poststed» if they like.
5. Press «Lagre». The form says «… er lagret som nr. …».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Du har lesetilgang» and no form | Their role is «Leser» | Ask an «Administrator» or «Eier» for a role that may write |
| «organisasjonsnummeret har feil kontrollsiffer» or «organisasjonsnummer må være ni siffer» | The number is mistyped | Check it in Enhetsregisteret, or leave it empty for a foreign supplier |

## Good to know

- Recording a payment works without a supplier too; the page says «Føringen
  går fint uten.» A supplier makes the entry traceable.
- Myntos keeps no bank account number for suppliers. Paying them happens in
  the bank, not in Myntos.
- A supplier cannot be chosen as the buyer on an invoice; register them as a
  customer as well if the company also sells to them.

## Related

- [Add a customer](https://myntos.no/llms/help/customers-and-suppliers/add-a-customer.md)
- [See your customers and suppliers](https://myntos.no/llms/help/customers-and-suppliers/see-your-customers-and-suppliers.md)
- An agent with a key can add suppliers through the API:
  [myntos-parter](https://myntos.no/llms/skills/myntos-parter.md)
