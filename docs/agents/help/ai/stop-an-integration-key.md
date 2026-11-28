---
title: Stop an integration key
description: How a company owner sees the integration keys that let servers send invoices from Myntos on their own, when each was last used, and how to stop one at once. Use when someone wants to disconnect a webshop or server from Myntos invoicing, thinks an integration key has leaked, or asks why a server stopped sending invoices.
pages: /organisasjon/
skill: myntos-integrasjon
---

# Stop an integration key

An integration key lets a server send invoices from Myntos without anybody
approving them, so stopping it is the one thing to do at once if its secret
may have leaked, or if the server should no longer invoice in the company's
name.

## They might ask

- «Hvordan stopper jeg nettbutikken fra å sende fakturaer?»
- «Jeg tror integrasjonsnøkkelen har lekket. Hva gjør jeg?»
- «Hvorfor sender ikke serveren fakturaer lenger?»

## Steps

1. Open the company page: click the company's name at the top right and
   choose «Innstillinger for selskapet» (`https://myntos.no/organisasjon/`).
2. Scroll to «Integrasjoner». The card «Integrasjonsnøkler» lists every key
   with «Navn», «IP-adresser», «Laget», «Sist brukt» and «Status».
3. Press «Stopp» on the key's row. It stops at once: the server's next request
   is refused, and the page says «Integrasjonsnøkkelen «…» er stoppet.».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Integrasjoner» section | Only owners see it | Ask an owner of the company to stop the key |
| No «Stopp» on the row | The key is already stopped; «Status» says why | Nothing to do |

## Good to know

- Any owner can stop any of the company's integration keys. The owner who
  made it gets an e-mail.
- A stopped key cannot be started again. To connect the server again, make a
  new key and put the new secret in the server's settings:
  https://myntos.no/llms/help/ai/make-an-integration-key.md.
- Myntos stops a key by itself, and «Status» says why: «Stoppet: personen er
  ikke lenger eier» when the owner who made it lost the owner role,
  «Stoppet: personen har ikke tilgang lenger» when they left the company,
  «Stoppet: ble avvist for mange ganger» when it kept calling from the wrong
  address or asking for what it may not do, and also when the owner's
  password is reset or their account deleted.
- Invoices the server already issued or sent stay in the books. A wrong one is
  corrected with a credit note on «Faktura», as always.

## Related

- [Make an integration key for a server](https://myntos.no/llms/help/ai/make-an-integration-key.md)
- [See and stop an AI's key](https://myntos.no/llms/help/ai/see-and-stop-keys.md)
