---
title: Add a customer
description: How a person registers a customer (kunde) on the «Kunder og leverandører» page in Myntos, or straight from the invoice form with the details fetched from Enhetsregisteret. Use when someone asks how to add or create a customer in Myntos.
pages: /parter/ /faktura/
skill: myntos-parter
---

# Add a customer

A customer is registered once and then chosen from a list on every invoice.
Myntos gives each customer a number, from 10000 and up, which also goes into
the KID on their invoices. **Whether it is a customer or a supplier, and its
number, can never be changed afterwards**, and a customer cannot be deleted,
so check the list first.

## They might ask

- «Hvordan legger jeg til en kunde i Myntos?»
- «Hvordan oppretter jeg en ny kunde?»
- «Hvor registrerer jeg kundene mine?»
- "How do I add a customer in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier». A «Leser» sees the list but
  not the form.

## Steps

On the customer page:

1. Open «Faktura» in the top menu and press «Kunder og leverandører» at the
   top right (`https://myntos.no/parter/`). The page is headed «Hvem du handler
   med».
2. Look in the list on the right that the customer is not there already.
3. In «Legg til», choose «Kunde».
4. Fill in «Navn», and «Organisasjonsnummer (valgfritt)» if it is a business.
   Fill in «Postnummer» and «Poststed».
5. Press «Lagre». The form says «… er lagret som nr. …», and the customer
   appears in the list.

Or from the invoice form, which fetches the details from Enhetsregisteret and
also stores the street address:

1. Open «Faktura» (`https://myntos.no/faktura/`). Under «Kjøper», leave
   «Kunde» at «Skriv inn kjøperen selv».
2. Type the «Organisasjonsnummer»; «Navn», «Adresse», «Postnummer» and
   «Poststed» fill in from the register. For a private person, type them.
3. Tick «Lagre som kunde, så står den i listen neste gang». The customer is
   registered when the invoice is saved with «Se over fakturaen».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Du har lesetilgang» and no form | Their role is «Leser» | Ask an «Administrator» or «Eier» for a role that may write |
| «organisasjonsnummeret har feil kontrollsiffer» or «organisasjonsnummer må være ni siffer» | The number is mistyped | Check it in Enhetsregisteret, or leave it empty for a private person or a foreign company |
| «Vi fikk ikke lagret.» | The save failed | Try again |

## Good to know

- To be invoiced, a customer needs an organisation number or an address;
  a name alone does not satisfy the rules for invoices. A business customer
  needs the organisation number, because electronic invoices are addressed by
  it.
- The «Kunder og leverandører» page has no field for the street address, and
  does not look anything up in Enhetsregisteret. The invoice form does both.
- The page's own form changes only the name afterwards; see
  [Correct a customer's or supplier's orgnr or address](https://myntos.no/llms/help/customers-and-suppliers/correct-an-orgnr-or-address.md).

## Related

- [Add a supplier](https://myntos.no/llms/help/customers-and-suppliers/add-a-supplier.md)
- [Choose who the invoice is to](https://myntos.no/llms/help/invoices/choose-who-the-invoice-is-to.md)
- An agent with a key can add customers through the API:
  [myntos-parter](https://myntos.no/llms/skills/myntos-parter.md)
