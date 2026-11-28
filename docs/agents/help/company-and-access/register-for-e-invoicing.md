---
title: Register for e-invoicing
description: How an administrator registers a company in Myntos for sending and optionally receiving invoices as EHF over Peppol, under «E-faktura» on the company page. Use when someone asks how to start sending or receiving e-faktura or EHF in Myntos, what «Registrer for e-faktura», «Slå på mottak av e-faktura» or «Slå av mottak av e-faktura» does, or why invoices are not sent as EHF.
pages: /organisasjon/
skill: myntos-faktura
---

# Register for e-invoicing

Before Myntos can send a company's invoices as EHF over Peppol, an
«Administrator» or «Eier» registers the company once under «E-faktura» on the
company page. Registering enables **sending only**, so any inbox where the
company currently receives supplier e-invoices is untouched. On the same card,
an administrator can also explicitly switch **receiving** on or off in
Peppol (ELMA) so supplier e-invoices arrive directly under «Innkommende
e-faktura».

## They might ask

- «Hvordan begynner jeg å sende e-faktura fra Myntos?»
- «Hvordan slår jeg på mottak av EHF i Myntos?»
- «Hvorfor har jeg ingen "Send som EHF"-knapp?»
- «Hvordan slår jeg av mottak av e-faktura når jeg bytter regnskapsprogram?»
- "How do I set up e-invoicing in Myntos?"

## Before they start

- The role «Administrator» or «Eier» in this company. Others see «Bare eiere
  og administratorer kan registrere selskapet for e-faktura eller endre
  mottak.»
- The company confirmed with ID-porten, so only people who may act for it
  can send and receive invoices in its name:
  [Confirm the company with ID-porten](https://myntos.no/llms/help/company-and-access/confirm-the-company.md).
- The company's business address, which Myntos has from Enhetsregisteret.
- **If turning on receiving:** if the company already receives EHF in another
  accounting program (such as Fiken or Tripletex), switch off EHF receiving
  there first — a Norwegian organisation number can only be registered to
  receive through one Peppol access point at a time.

## Steps: enable sending

1. Open the company page: click the company's name at the top right and
   choose «Innstillinger for selskapet» (`https://myntos.no/organisasjon/`).
   The link «Sett opp e-faktura under Selskap» in the yellow notice at the top
   of the «Faktura» page leads straight to the card.
2. Find the card «E-faktura», below «Tilgang».
3. If it says «For å sende eller motta EHF må selskapet først bekreftes med
   ID-porten …», press «Bekreft eierskapet» and confirm the company first, then
   come back.
4. Press «Registrer for e-faktura».
5. The card updates to «Selskapet er registrert for sending. Fakturaer til
   kunder på Peppol sendes som EHF når de utstedes på Faktura-siden.»

## Steps: turn receiving on or off

1. On the same «E-faktura» card, find «Mottak av e-faktura».
2. To turn receiving **on**: tick «Jeg vet at dette publiserer
   organisasjonsnummeret i Peppol-nettverket (ELMA) gjennom Myntos, og at
   mottak i et annet regnskapsprogram må være slått av først.», then press
   «Slå på mottak av e-faktura».
3. Once enabled, follow «Åpne innkommende e-faktura →» to open the inbox
   ([Receive and book an e-invoice](https://myntos.no/llms/help/invoices/receive-and-book-an-e-invoice.md)).
4. To turn receiving **off** (for example before moving to another program),
   press «Slå av mottak av e-faktura» and confirm. Sending stays active, and
   the organisation number is freed in Peppol (ELMA).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «E-faktura» card | This Myntos has no access point configured | Invoices are sent as PDF |
| «Bare eiere og administratorer kan registrere selskapet for e-faktura eller endre mottak.» | Their role is «Leser» or «Medlem» | Ask an «Administrator» or «Eier» |
| «tilgangspunktet trenger selskapets forretningsadresse. Hent opplysningene fra Enhetsregisteret på nytt under Selskap» | Myntos has no business address for the company | [Fetch the company from Enhetsregisteret again](https://myntos.no/llms/help/company-and-access/fetch-from-enhetsregisteret-again.md), then try again |
| «organisasjonsnummeret er allerede registrert for mottak av e-faktura hos et annet tilgangspunkt …» | Another accounting system still holds the company's Peppol/ELMA registration | Turn off EHF receiving in the previous program, then press «Slå på mottak av e-faktura» again |
| «dette organisasjonsnummeret tar allerede imot e-faktura i et annet regnskap i Myntos …» | Another set of books in Myntos already receives EHF for the same orgnr | Turn off receiving on that company first |
| «fikk ikke kontakt med tilgangspunktet for e-faktura. Prøv igjen om litt» | The access point could not be reached | Try again later |

## Good to know

- Registering for sending is once per company. Each invoice to a business on
  Peppol is then sent as it is issued with «Utsted og send som EHF», or later
  with «Send som EHF»:
  [Send an invoice to the customer](https://myntos.no/llms/help/invoices/send-an-invoice-to-the-customer.md).
- Receiving is separate on purpose so registering to send never takes over an
  existing supplier-invoice inbox by surprise.
- An AI agent with a key cannot register the company or turn receiving on or
  off. Once enabled, it can inspect and book incoming EHFs and ask to send
  outgoing ones (each send waits for approval under «Godkjenning»).

## Related

- [Send an invoice to the customer](https://myntos.no/llms/help/invoices/send-an-invoice-to-the-customer.md)
- [Receive and book an e-invoice](https://myntos.no/llms/help/invoices/receive-and-book-an-e-invoice.md)
- [Confirm the company with ID-porten](https://myntos.no/llms/help/company-and-access/confirm-the-company.md)
- An agent with a key can read where the company stands (`GET …/peppol`), but
  not register it or change receiving: [myntos-faktura](https://myntos.no/llms/skills/myntos-faktura.md)
