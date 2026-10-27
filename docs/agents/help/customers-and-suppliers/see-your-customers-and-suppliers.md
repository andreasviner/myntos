---
title: See your customers and suppliers
description: How a person finds the list of customers and suppliers on the «Kunder og leverandører» page in Myntos, and what the numbers and columns mean. Use when someone asks where their customers are in Myntos, how customer numbers work, or how to find a supplier.
pages: /parter/
skill: myntos-parter
---

# See your customers and suppliers

All the company's customers and suppliers are in one list on the «Kunder og
leverandører» page, sorted with customers first and then by name. Each has a
number Myntos gave it: from 10000 for customers and from 20000 for suppliers.
The number never changes, because entries in the books refer to it.

## They might ask

- «Hvor finner jeg kundene mine i Myntos?»
- «Hvilket kundenummer har Bygg AS?»
- «Hvor er leverandørlisten?»
- "Where is the customer list in Myntos?"

## Before they start

- Any role in the company, including «Leser».

## Steps

1. Open «Faktura» in the top menu and press «Kunder og leverandører» at the
   top right (`https://myntos.no/parter/`). From the «Betaling» page, «Se
   motpartene dine» goes to the same place.
2. The table shows «Nr.» (the number), «Navn», «Type» («Kunde» or
   «Leverandør»), «Orgnr» and «Sted» (postcode and town).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Ingen registrert ennå. …» | Nobody is registered yet | [Add a customer](https://myntos.no/llms/help/customers-and-suppliers/add-a-customer.md) or [add a supplier](https://myntos.no/llms/help/customers-and-suppliers/add-a-supplier.md) |
| The list is someone else's | Another company is chosen | Switch company with the company name at the top right |
| «Vi fikk ikke hentet listen.» | The list could not be loaded | Reload the page |

## Good to know

- The page is not in the top menu; it is reached from «Faktura» and
  «Betaling», where customers and suppliers are used.
- The list belongs to one company. Each company in Myntos has its own.

## Related

- [Rename a customer or supplier](https://myntos.no/llms/help/customers-and-suppliers/rename-a-customer-or-supplier.md)
- An agent with a key can read the list through the API:
  [myntos-parter](https://myntos.no/llms/skills/myntos-parter.md)
