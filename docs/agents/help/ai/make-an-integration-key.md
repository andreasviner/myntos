---
title: Make an integration key for a server
description: How a company owner makes an integration key in Myntos, so the company's own server or online shop can make, issue and send invoices as EHF by itself, from the IP addresses the owner gives it. Use when someone asks how to let a system send invoices from Myntos automatically, without approving each one, or how to connect a webshop or subscription system to Myntos invoicing.
pages: /organisasjon/
skill: myntos-integrasjon
---

# Make an integration key for a server

An integration key lets a server the company uses (a webshop, a subscription
system, any program running on a machine) make customers, make and issue
invoices and send them as EHF in Myntos **on its own, without anybody
approving each one**. That is what makes it different from a key for an AI
assistant, where everything that leaves Myntos waits for the person. So it is
narrow: only an owner makes one, it works only from the IP addresses the
owner writes in, and it can do nothing but invoicing. It cannot make credit
notes, post other vouchers, move money or decide who has access.

**The secret must never be pasted into an AI assistant** (ChatGPT, Claude or
any other): with it, the assistant could send invoices without asking anybody.
It goes into the server's settings, and nowhere else.

## They might ask

- «Hvordan kobler jeg nettbutikken til fakturaene i Myntos?»
- «Kan et system sende fakturaer fra Myntos uten at jeg godkjenner hver?»
- «Hvor lager jeg en integrasjonsnøkkel?»
- "How do I let my server send invoices through Myntos automatically?"

## Before they start

- They must be an owner («Eier») of the company. Administrators and members
  do not see the section:
  [Roles and what each may do](https://myntos.no/llms/help/company-and-access/roles-and-permissions.md).
- They need the IP address or addresses the server calls Myntos from. The
  person who runs the server knows them. A server on the same machine as
  Myntos calls from `127.0.0.1`.
- An account that signs in with BankID cannot make keys yet.

## Steps

1. Open the company page: click the company's name at the top right and
   choose «Innstillinger for selskapet» (`https://myntos.no/organisasjon/`).
2. Scroll to «Integrasjoner», right under «Tilgang», and press «Lag
   integrasjonsnøkkel» on the card «Integrasjonsnøkler».
3. In the window, fill in «Navn»: what they will know the server by, for
   example «Nettbutikken».
4. Fill in «IP-adresser»: the addresses the server calls from, one per line,
   at most 10. A range is written like `203.0.113.0/24`.
5. «Formål» is fixed: «Fakturering: lage, utstede og sende fakturaer».
6. Press «Lag nøkkelen».
7. The secret is shown once, starting with `myntos_ik_`. Press «Kopier» and
   paste it into the server's settings, then press «Ferdig». The window says:
   «Lim den inn i innstillingene til serveren som skal bruke den. Lim den
   aldri inn i en KI-assistent: den sender fakturaer uten å spørre deg.»
8. The server also needs the number after «Gi serveren også selskapets ID i
   Myntos:». It is not secret, and it stays on the card as «Selskapets ID i
   Myntos, som serveren også trenger:».

The key then appears in the list with «Navn», «IP-adresser», «Laget» (date
and who made it), «Sist brukt» and «Status» («I bruk»).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Integrasjoner» section | They are not an owner of this company | Ask an owner to make the key |
| «… er ikke en IP-adresse eller et adresseområde …» | A line is not an address | Write addresses like `203.0.113.7` or `2001:db8::1`, one per line |
| «… er for mange adresser for en nøkkel …» | A range wider than /16 (IPv4) or /48 (IPv6) | Write the server's own address, or a small range around it |
| «Skriv minst en IP-adresse serveren kaller Myntos fra …» | The address field is empty | Ask whoever runs the server for its address |
| «… logger inn med BankID kan ikke lage nøkler ennå …» | BankID accounts make no keys yet | Another owner who signs in with e-mail and password can make it |

## Good to know

- The secret is shown only once. Myntos keeps only a fingerprint of it, so a
  lost secret means making a new key and stopping the old one.
- The key has no expiry. It works until somebody stops it, or until the owner
  who made it stops being an owner of the company.
- It works with the role of the owner who made it, and every invoice it
  issues is marked with the key's name in the books.
- The switch for AI keys under «Tilgang» does not affect it, either way.
- The owner gets an e-mail when a key is made and when one stops. The e-mail
  never contains the secret.

## Related

- [Stop an integration key](https://myntos.no/llms/help/ai/stop-an-integration-key.md)
- [What an AI's key can never do](https://myntos.no/llms/help/ai/what-a-key-can-never-do.md)
- The server's side, for the developer:
  [myntos-integrasjon](https://myntos.no/llms/skills/myntos-integrasjon.md)
