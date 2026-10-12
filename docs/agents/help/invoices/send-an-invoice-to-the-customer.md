---
title: Send an invoice to the customer
description: How an invoice issued in Myntos reaches the customer, why Myntos does not send it yet, and what the e-invoice (EHF) requirement from 1 January 2027 means. Use when someone asks whether Myntos e-mails invoices, how to send e-faktura or EHF, or whether they must send electronic invoices.
pages: /faktura/
skill: myntos-faktura
---

# Send an invoice to the customer

Myntos writes, checks, numbers and books the invoice, and makes both a PDF and
an EHF file, but **it does not send anything to the customer yet**. Sending
over the Peppol network needs an access point, which is not connected, and
Myntos does not e-mail invoices. The person downloads the file and sends it
themselves.

## They might ask

- «Sender Myntos fakturaen til kunden?»
- «Hvordan sender jeg e-faktura / EHF fra Myntos?»
- «Må jeg sende EHF?»
- "Does Myntos e-mail the invoice to my customer?"

## Before they start

- An issued invoice: [Issue an invoice](https://myntos.no/llms/help/invoices/issue-an-invoice.md).

## Steps

1. Open «Faktura» in the top menu (`https://myntos.no/faktura/`).
2. Download the file: «PDF» or «EHF» on the invoice's row, or «Last ned PDF»
   and «Last ned EHF» right after issuing.
3. Send it: the PDF by e-mail to a private customer, or to a business that
   accepts PDF; the EHF file to a business through whatever channel the
   customer uses for e-invoices.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No send button anywhere | Sending is not built yet | Download and send the file as above |

## Good to know

- The card «Om e-faktura» on the page says it too: from 1 January 2027, sales
  to other bokføringspliktige (businesses obliged to keep accounts) must be
  invoiced electronically (EHF over Peppol). Myntos makes the EHF file for
  every issued invoice already.
- An EHF invoice is addressed by the buyer's organisation number, which is
  why the review asks for it on invoices to businesses.
- Private customers are not covered by the requirement; a PDF is fine.

## Related

- [Download an invoice](https://myntos.no/llms/help/invoices/download-an-invoice.md)
- An agent with a key can fetch the files through the API, but cannot send
  them either: [myntos-faktura](https://myntos.no/llms/skills/myntos-faktura.md)
