---
title: Register for e-invoicing
description: How an administrator registers a company in Myntos for sending invoices as EHF over Peppol, under «E-faktura» on the company page, and how the person who signs for the company finishes the access point's identity check. Use when someone asks how to start sending e-faktura or EHF from Myntos, what «Registrer for e-faktura» or «Fullfør identitetskontrollen» does, or why invoices are not sent as EHF («Utsted og send som EHF» and «Send som EHF» missing).
pages: /organisasjon/
skill: myntos-faktura
---

# Register for e-invoicing

Before Myntos can send a company's invoices as EHF over Peppol, the company
is registered once with the access point, the service that carries the
invoices onto the Peppol network. An «Administrator» or «Eier» does it on the
company page under «E-faktura». It ends with an **identity check on the
access point's own page**, which has to be done by someone who signs for the
company (daglig leder, a board member with signing rights, or prokura). After
that, an invoice to a customer on Peppol is sent as EHF the moment it is
issued: the review's main button becomes «Utsted og send som EHF». The
company is registered to **send** only: e-invoices it receives today keep
arriving where they arrive.

## They might ask

- «Hvordan begynner jeg å sende e-faktura fra Myntos?»
- «Hvorfor har jeg ingen "Send som EHF"-knapp?»
- «Hva betyr den gule meldingen om e-faktura på Faktura-siden?»
- «Hva er identitetskontrollen for e-faktura?»
- "How do I set up e-invoicing in Myntos?"

## Before they start

- The role «Administrator» or «Eier» in this company. Others see «Bare eiere
  og administratorer kan registrere selskapet og hente lenken til
  identitetskontrollen.»
- The company confirmed with ID-porten, so only people who may act for it
  can send invoices in its name:
  [Confirm the company with ID-porten](https://myntos.no/llms/help/company-and-access/confirm-the-company.md).
- The company's business address, which Myntos has from Enhetsregisteret.
- Someone who signs for the company, ready to prove who they are. It may be
  the same person, or someone they send the link to.

## Steps

1. Open the company page: click the company's name at the top right and
   choose «Innstillinger for selskapet» (`https://myntos.no/organisasjon/`).
   The link «Sett opp e-faktura under Selskap» in the yellow notice at the top
   of the «Faktura» page leads straight to the card.
2. Find the card «E-faktura», below «Tilgang».
3. If it says «For å sende EHF må selskapet først bekreftes med ID-porten …»,
   press «Bekreft eierskapet» and confirm the company first, then come back.
4. Press «Registrer for e-faktura».
5. A link appears under «Åpne lenken selv hvis du signerer for selskapet,
   eller send den til den som gjør det.» Press «Åpne» to do the check
   yourself, or «Kopier» and send the link to whoever signs for the company.
6. The access point's page opens in a new tab. The person who signs for the
   company follows its steps to prove who they are.
7. Back in Myntos, the card updates by itself when the tab is opened again,
   and says «Selskapet er registrert. Fakturaer til kunder på Peppol sendes
   som EHF når de utstedes på Faktura-siden.»

If the link is lost or has run out, the card says «Selskapet er registrert.
Før det kan sende, må noen som signerer for selskapet … fullføre
identitetskontrollen hos tilgangspunktet.» Press «Fullfør identitetskontrollen»
for a fresh link.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «E-faktura» card | This Myntos has no access point, so there is nothing to register | Invoices are sent as PDF |
| «Bare eiere og administratorer kan registrere selskapet og hente lenken til identitetskontrollen.» | Their role is «Leser» or «Medlem» | Ask an «Administrator» or «Eier» |
| «tilgangspunktet trenger selskapets forretningsadresse. Hent opplysningene fra Enhetsregisteret på nytt under Selskap» | Myntos has no business address for the company | [Fetch the company from Enhetsregisteret again](https://myntos.no/llms/help/company-and-access/fetch-from-enhetsregisteret-again.md), then press «Registrer for e-faktura» again |
| «fikk ikke kontakt med tilgangspunktet for e-faktura. Prøv igjen om litt» | The access point could not be reached | Try again later |
| «tilgangspunktet avviste registreringen: …» | The access point refused, and says why | Read the reason; if it is not something they can fix, tell Myntos through «Meld feil eller ønsker» at the bottom of the page |
| The card still asks for the identity check after it was done | The access point has not finished checking, or the check was not completed | Reload the page in a little while. If it still asks, press «Fullfør identitetskontrollen» for a fresh link and do it again |

## Good to know

- Registering is once per company. Each invoice is then sent as it is
  issued, with «Utsted og send som EHF», or later with «Send som EHF»:
  [Send an invoice to the customer](https://myntos.no/llms/help/invoices/send-an-invoice-to-the-customer.md).
- Why only to send: registering a Norwegian organisation number to *receive*
  on Peppol takes over whichever inbox the company receives its supplier
  e-invoices in today, so Myntos does not do it.
- The identity check is the access point's, on their page. Myntos does not
  see or keep what is shown there.
- An AI agent with a key cannot register the company or do the identity
  check. It can ask to send an invoice once the company is registered, and the
  person approves each send under «Godkjenning».

## Related

- [Send an invoice to the customer](https://myntos.no/llms/help/invoices/send-an-invoice-to-the-customer.md)
- [Confirm the company with ID-porten](https://myntos.no/llms/help/company-and-access/confirm-the-company.md)
- An agent with a key can read where the company stands (`GET …/peppol`), but
  not register it: [myntos-faktura](https://myntos.no/llms/skills/myntos-faktura.md)
