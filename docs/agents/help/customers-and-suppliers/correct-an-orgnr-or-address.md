---
title: Correct a customer's or supplier's orgnr or address
description: What a person can do when a registered customer or supplier in Myntos has a wrong or missing organisation number or address, which the «Kunder og leverandører» page cannot edit. Use when someone asks how to change a customer's address or orgnr, or when the invoice form says a customer has neither.
pages: /parter/ /faktura/
skill: myntos-parter
---

# Correct a customer's or supplier's orgnr or address

The «Kunder og leverandører» page can change a name, but not the
organisation number or the address. That matters most when the invoice form
says a customer has neither, because an invoice needs one of them. There are
two ways round it today: type the buyer onto the invoice by hand, or let an AI
agent with a key correct the customer.

## They might ask

- «Hvordan endrer jeg adressen til en kunde?»
- «Det står at kunden verken har organisasjonsnummer eller adresse»
- «Kunden har feil organisasjonsnummer»
- "How do I change a customer's address in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».

## Steps

For the invoice in front of them:

1. Open «Faktura» (`https://myntos.no/faktura/`). Under «Kjøper», set «Kunde»
   to «Skriv inn kjøperen selv».
2. Type the «Organisasjonsnummer» (name and address fill in from
   Enhetsregisteret), or type «Navn», «Adresse», «Postnummer» and
   «Poststed».
3. Carry on with «Se over fakturaen». The invoice is then not linked to the
   registered customer.

To correct the customer itself:

1. Invite an AI agent to the books and ask it to correct the customer; an
   agent with a key that may write can change the organisation number and
   address: [Invite an AI](https://myntos.no/llms/help/ai/invite-an-ai.md).
2. Afterwards, open any draft invoice to that customer with «Endre» and save
   it again with «Se over fakturaen», so it picks up the new details.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «… har verken organisasjonsnummer eller adresse. § 5-1-2 krever ett av dem. Fyll det inn under Kunder og leverandører før du utsteder.» | The customer has neither, and the page it points to can only change the name | Type the buyer in by hand as above |
| Only «Endre navn» on the row | That is all the page offers | Use one of the ways above |

## Good to know

- Ticking «Lagre som kunde» while typing the buyer in registers a **new**
  customer with a new number; the old one stays in the list, because
  customers cannot be deleted.
- A postcode alone is enough address for the invoice check, so a customer
  with «Postnummer» filled in can be invoiced even though the form's note may
  still appear.
- Invoices already issued keep the details they were issued with.

## Related

- [Rename a customer or supplier](https://myntos.no/llms/help/customers-and-suppliers/rename-a-customer-or-supplier.md)
- [Choose who the invoice is to](https://myntos.no/llms/help/invoices/choose-who-the-invoice-is-to.md)
- [myntos-parter](https://myntos.no/llms/skills/myntos-parter.md), the skill
  an agent uses for this
