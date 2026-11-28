---
title: Send an invoice to the customer
description: How an invoice issued in Myntos reaches the customer, sent as EHF over Peppol by «Utsted og send som EHF» (or «Send som EHF» afterwards) or as a PDF the person sends themselves, how to see whether it arrived, and what the e-invoice requirement from 1 January 2027 means. Use when someone asks how to send an invoice or e-faktura from Myntos, whether Myntos e-mails invoices, or whether the customer got it.
pages: /faktura/
skill: myntos-faktura
---

# Send an invoice to the customer

An invoice to a business goes as an **EHF e-invoice over Peppol**, the network
Norwegian businesses receive e-invoices on, and in Myntos that is the default:
once the company is registered for e-invoicing, the review's main button for
a customer on Peppol is «Utsted og send som EHF», which issues the invoice and
sends it in one press. It goes to the customer's organisation number, straight
into their accounting system. An invoice issued without sending can be sent
later with «Send som EHF». **Myntos never e-mails an invoice**: a private
customer, or a business that cannot receive EHF, gets the PDF, which the
person downloads and sends themselves. An invoice that has gone cannot be
called back.

## They might ask

- «Hvordan sender jeg fakturaen til kunden fra Myntos?»
- «Hvordan sender jeg e-faktura / EHF?»
- «Har kunden fått fakturaen?»
- «Sender Myntos fakturaen på e-post?»
- "How do I send an invoice to my customer from Myntos?"

## Before they start

- An issued invoice: [Issue an invoice](https://myntos.no/llms/help/invoices/issue-an-invoice.md).
- For EHF: the company set up for e-invoicing, once, by an
  «Administrator» or «Eier». Before the first business invoice, the «Faktura»
  page prompts them to set up e-faktura right on the page («Aktiver e-faktura
  (EHF)» once confirmed with ID-porten, or «Bekreft selskapet med ID-porten»
  first), and it can also be managed under «Innstillinger for selskapet» →
  «E-faktura»:
  [Register for e-invoicing](https://myntos.no/llms/help/company-and-access/register-for-e-invoicing.md).
- For EHF: the customer's organisation number on the invoice. A customer
  without one gets the PDF.
- The role «Medlem», «Administrator» or «Eier». A «Leser» sees how each
  invoice stands but has no button.

## Steps

**As EHF, to a business, when issuing:**

1. Write the invoice and press «Se over fakturaen»:
   [Issue an invoice](https://myntos.no/llms/help/invoices/issue-an-invoice.md).
2. On the review, the main button says «Utsted og send som EHF», and the text
   under it says «Når den er utstedt, sendes den som EHF over Peppol til …
   (…)», naming the customer and their organisation number. Check it is the
   right customer.
3. Press «Utsted og send som EHF». There is no extra question: the review is
   the confirmation.
4. The card «Faktura … er utstedt» says «Sender EHF-en over Peppol …», then
   «EHF-en er sendt over Peppol. Vi venter på kvittering fra kundens
   system.», and the invoice's status in the list becomes «Utstedt · Sendt
   som EHF».
5. When the customer's side has received it, the status says «Utstedt ·
   Levert som EHF». The page asks again each time it is opened, so to check,
   open «Faktura» again later.

**As EHF, an invoice issued earlier:** one issued with «Utsted uten å sende»,
or one whose send failed.

1. Press «Send som EHF» on the card right after issuing, or on the invoice's
   row in the list «Fakturaer» («Send EHF på nytt» if a send failed).
2. A window asks «Sende faktura … som EHF til … (…)?», naming the customer
   and their organisation number, and says it cannot be called back. Check
   it is the right customer and press «OK».
3. The status moves as in steps 4 and 5 above.

A credit note («Kreditnota») for an invoice that went as EHF is sent as EHF
by itself when «Krediter» is confirmed:
[Credit an invoice](https://myntos.no/llms/help/invoices/credit-an-invoice.md).

**As a PDF, to anyone else:**

1. Download it: «PDF» on the invoice's row, or «Last ned PDF» right after
   issuing.
2. Send it to the customer by e-mail or post.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Sett opp e-faktura (EHF) før første faktura» on «Faktura», or the review stops with «selskapet må være satt opp for e-faktura (EHF) før du kan utstede faktura til en bedrift …» | E-invoicing is the default in Myntos and required before issuing business invoices | Confirm the company with ID-porten if prompted, then press «Aktiver e-faktura (EHF)» (or ask an «Administrator» / «Eier»): [Register for e-invoicing](https://myntos.no/llms/help/company-and-access/register-for-e-invoicing.md). For a private customer, click «Skriv faktura uten e-faktura» |
| The review says «Utsted fakturaen», with «… kan ikke ta imot EHF over Peppol, så fakturaen sendes ikke herfra.» | The customer is not on Peppol for this kind of document | Issue it and send the PDF |
| Neither «Utsted og send som EHF» nor «Send som EHF» anywhere, and «Om e-faktura» at the bottom says «Denne installasjonen av Myntos har ikke et tilgangspunkt for Peppol» | This Myntos cannot send EHF | Send the PDF |
| No «Send som EHF» on one invoice | It is a draft, the customer has no organisation number, or it has already been sent | Issue it first; send the PDF to a customer without an organisation number; a sent one is not sent again |
| «kunden kan ikke motta EHF over Peppol, så ingenting ble sendt. Send PDF-en til kunden selv» | The customer is not on Peppol | Nothing went. Send the PDF |
| «EHF kom ikke fram» on the row, and «Send EHF på nytt» | The customer's side did not take it; the page quotes what the access point said | Read the reason; press «Send EHF på nytt», or send the PDF |
| «tilgangspunktet svarte ikke i tide, så vi vet ikke om fakturaen gikk. Spør kunden om de har fått den før du sender den igjen» | No answer in time | **Do not send again yet.** Ask the customer whether it arrived; an invoice sent twice can be paid twice |
| «fakturaen er allerede sendt som EHF …» | Someone already sent it, perhaps from another tab | Nothing to do; the list shows how it stands |
| «fikk ikke kontakt med tilgangspunktet for e-faktura. Prøv igjen om litt» | The service that carries it could not be reached | Nothing went. Try again later |
| «tilgangspunktet avviste fakturaen: …» | The access point found something wrong in the file | The invoice cannot be changed. Send the PDF, and tell Myntos through «Meld feil eller ønsker» at the bottom of the page |

## Good to know

- From 1 January 2027, sales to other bokføringspliktige (businesses obliged
  to keep accounts) must be invoiced electronically, as EHF over Peppol.
  Private customers are not covered; a PDF is fine for them.
- What goes is the EHF file made when the invoice was issued, unchanged, the
  same file «EHF» on the row downloads.
- An EHF invoice is addressed by the buyer's organisation number, which is
  why the review asks for it on invoices to businesses. To know whether the
  customer can receive it, Myntos asks the access point when the review
  opens, sending the customer's organisation number and nothing else.
- Issuing and sending are two steps under one button. If the send fails, the
  invoice is still issued and in the books, and the card says what went
  wrong.
- Setting up e-invoicing for sending does not change where supplier invoices
  arrive. Receiving incoming supplier EHF invoices in Myntos is a separate
  opt-in under «Innstillinger for selskapet» → «E-faktura»:
  [Receive and book an e-invoice](https://myntos.no/llms/help/invoices/receive-and-book-an-e-invoice.md).

## Related

- [Register for e-invoicing](https://myntos.no/llms/help/company-and-access/register-for-e-invoicing.md)
- [Download an invoice](https://myntos.no/llms/help/invoices/download-an-invoice.md)
- [Credit an invoice](https://myntos.no/llms/help/invoices/credit-an-invoice.md)
- An agent with a key can ask to send an invoice, and the person approves each
  send under «Godkjenning»: [myntos-faktura](https://myntos.no/llms/skills/myntos-faktura.md)
