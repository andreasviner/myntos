---
title: Rename a customer or supplier
description: How a person changes the name of a customer or supplier with «Endre navn» on the «Kunder og leverandører» page in Myntos. Use when someone asks how to change or correct a customer's or supplier's name, or says a customer has changed its name.
pages: /parter/
skill: myntos-parter
---

# Rename a customer or supplier

A customer's or supplier's name can be corrected at any time, for a typo or
because the business changed its name. Invoices already issued keep the name
they were issued with. The number and the type («Kunde» or «Leverandør»)
cannot be changed.

## They might ask

- «Hvordan endrer jeg navnet på en kunde?»
- «Leverandøren har byttet navn»
- «Jeg skrev feil navn på kunden»
- "How do I rename a customer in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».

## Steps

1. Open «Faktura» in the top menu and press «Kunder og leverandører» at the
   top right (`https://myntos.no/parter/`).
2. Find the row in the list and press «Endre navn» at the end of it.
3. The browser asks «Nytt navn», with the current name filled in. Type the new
   name and press OK.
4. The list shows the new name.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Endre navn» | Their role is «Leser» | Ask an «Administrator» or «Eier» |
| «Endringen feilet.» or another message at the top | The change could not be saved | Reload the page and try again |

## Good to know

- «Endre navn» changes only the name. The organisation number and address
  cannot be changed on this page:
  [Correct a customer's or supplier's orgnr or address](https://myntos.no/llms/help/customers-and-suppliers/correct-an-orgnr-or-address.md).
- A draft invoice that was saved before the change still shows the old name
  until it is opened with «Endre» and saved again.

## Related

- [See your customers and suppliers](https://myntos.no/llms/help/customers-and-suppliers/see-your-customers-and-suppliers.md)
- An agent with a key can correct names through the API:
  [myntos-parter](https://myntos.no/llms/skills/myntos-parter.md)
