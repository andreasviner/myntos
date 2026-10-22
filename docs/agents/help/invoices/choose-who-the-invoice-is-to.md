---
title: Choose who the invoice is to
description: How a person picks the buyer on an invoice in Myntos, either a registered customer or one typed in, with name and address fetched from Enhetsregisteret, and how to invoice a private person. Use when someone asks how to invoice a new customer, a private person, or a customer who is not in the list.
pages: /faktura/
skill: myntos-faktura
---

# Choose who the invoice is to

The buyer on an invoice is either a customer already registered under
«Kunder og leverandører», or a buyer typed straight onto the invoice. Both are
fine. A customer who comes back belongs in the register: it saves typing, and
their customer number goes into the KID. A one-off private customer does not
need to be registered. Whichever it is, the law wants the buyer identified by
an address or an organisation number; a name alone is not enough.

## They might ask

- «Hvordan fakturerer jeg en ny kunde?»
- «Kan jeg sende faktura til en privatperson?»
- «Kunden min står ikke i listen»
- «Henter Myntos adressen fra Brønnøysund?»
- "How do I invoice someone who is not a customer yet?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».

## Steps

1. Open «Faktura» in the top menu (`https://myntos.no/faktura/`) and find
   «Kjøper» at the top of the form.
2. **A registered customer:** choose them in «Kunde». The line under it shows
   the orgnr and address Myntos has for them.
3. **Anyone else:** leave «Kunde» at «Skriv inn kjøperen selv».
   - For a business, type its nine-digit number in «Organisasjonsnummer». The
     page says «Slår opp i Enhetsregisteret …» and fills in «Navn»,
     «Adresse», «Postnummer» and «Poststed» from the register. The line under
     the number shows the registered name and form, and says «slettet fra
     Enhetsregisteret …», «registrert konkurs» or «under avvikling» if that is
     the case. A deleted company no longer exists to pay the invoice.
   - For a private person, type «Navn», «Adresse», «Postnummer» and
     «Poststed», and untick «Kjøperen er bokføringspliktig
     (næringsdrivende)».
   - Tick «Lagre som kunde, så står den i listen neste gang» to register the
     buyer as a customer when the draft is saved.
4. Carry on with the rest of the invoice:
   [Issue an invoice](https://myntos.no/llms/help/invoices/issue-an-invoice.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «… har verken organisasjonsnummer eller adresse. § 5-1-2 krever ett av dem.» under «Kunde» | The registered customer has neither | Type the buyer in instead, or have the customer corrected: [Correct a customer's or supplier's orgnr or address](https://myntos.no/llms/help/customers-and-suppliers/correct-an-orgnr-or-address.md) |
| A message ending «Du kan skrive navn og adresse selv.» under «Organisasjonsnummer» | The number is wrong, or Enhetsregisteret did not answer | Check the number, or type name and address by hand |
| The customer is not in «Kunde» | Only customers are listed, not suppliers | Register them as a customer, or tick «Lagre som kunde» |

## Good to know

- A business buyer needs its organisation number on the invoice: the
  electronic invoice (EHF) is addressed by it, and from 1 January 2027 an
  invoice to a business without one cannot be issued. Until then the review
  only warns.
- «Kjøperen er bokføringspliktig (næringsdrivende)» decides whether that rule
  applies. Choosing a customer with an organisation number ticks it; one
  without unticks it. Check it either way.
- Changing a registered customer's details later does not change invoices
  already issued.

## Related

- [Add a customer](https://myntos.no/llms/help/customers-and-suppliers/add-a-customer.md)
- [Issue an invoice](https://myntos.no/llms/help/invoices/issue-an-invoice.md)
- An agent with a key can do this through the API:
  [myntos-faktura](https://myntos.no/llms/skills/myntos-faktura.md)
