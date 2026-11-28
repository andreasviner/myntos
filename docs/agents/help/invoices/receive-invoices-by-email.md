---
title: Receive invoices by e-mail
description: How to find your company's @myntos.no inbox address, filter between e-invoices (EHF) and e-mail invoices on the Mottatt page, verify who sent an e-mail invoice, manage approved senders, and book or dismiss attachments. Use when someone asks for their Myntos bilag e-mail address, how to email receipts into Myntos, or how to restrict who can send e-mail invoices.
pages: /innkommende/
skill: myntos-innkommende-efaktura
---

# Receive invoices by e-mail

Every company in Myntos automatically gets its own `@myntos.no` address (for
example, «Test AS» gets `test@myntos.no`, or `testas@myntos.no` if `test` is
already taken). You or your suppliers can send PDF invoices and receipt photos
to that address, and they show up under **Mottatt** alongside EHF e-invoices.

Because ordinary e-mail is easier to spoof than Peppol EHF, Myntos shows **who
sent every e-mail invoice**, checks SPF, DKIM and DMARC, flags mismatches
between `From`, SMTP envelope sender and `Reply-To`, and lets an administrator
restrict the inbox to approved senders only.

## They might ask

- «Hva er e-postadressen jeg sender bilag og fakturaer til i Myntos?»
- «Hvordan ser jeg bare e-postfakturaer eller bare EHF i Mottatt?»
- «Hvordan sjekker jeg hvem som sendte en e-postfaktura?»
- «Hvordan godkjenner jeg en avsender eller sperrer ukjente avsendere?»
- "Can I forward PDF invoices to an e-mail address in Myntos?"

## Before they start

- Anyone with access to the company can view the `@myntos.no` address and open
  received attachments.
- The role «Medlem», «Administrator» or «Eier» is needed to book, dismiss or
  reopen an e-mail invoice.
- Only an «Administrator» or «Eier» can turn e-mail intake on/off, switch
  between «Alle, med avsendervarsel» and «Kun godkjente avsendere», or manage
  the list of approved senders.

## Steps

1. Open **Mottatt** from the top menu (`https://myntos.no/innkommende/`).
2. In the **E-postfaktura** card at the top, copy your company's `@myntos.no`
   address with **Kopier**.
3. Send or forward an e-mail with a PDF or image attachment (PDF, PNG, JPEG,
   WebP, GIF or HEIC, up to 25 MB) to that address.
4. Use the source toggle above the table to switch between **Alle**, **Kun
   e-faktura (EHF)**, and **Kun e-postfaktura**, and the status buttons
   (**Nye**, **Bokført**, **Avvist**, **Alle**) to narrow the list.
5. Click a row to inspect it on the right. Check the **Avsender og sikkerhet**
   box first:
   - **Egen bruker** (green): sent from a verified member of your company.
   - **Godkjent avsender** (green): matches your company's approved-sender list.
   - **Ukjent avsender** (amber): sender is not recognised yet — verify the
     invoice and bank account number before paying.
   - **Sikkerhetsvarsel** (red): SPF, DKIM or DMARC failed, or the technical
     sender / Reply-To domain disagrees with `From`.
6. Under **Bokfør som bilag**, optionally choose «Gratis», «Medium» or «Smart»
   at **KI-nivå** (when KI is enabled for the company; «Medium» and «Smart»
   require an active KI plan with weekly quota left) and press **Les vedlegg med KI**
   to pre-fill date, amount, category and VAT code, check every field, and press
   **Bokfør bilag**. The voucher is posted (irreversible) and the PDF/image is
   filed in the bilag archive.

To clear spam, duplicates or non-invoices without deleting the audit record,
press **Avvis**. You can bring it back later under **Avvist** → **Åpne på nytt**.

## Tighten sender security

Administrators and owners can open **Sikkerhet og godkjente avsendere** inside
the **E-postfaktura** card (or use the one-click **Godkjenn …** buttons on any
received e-mail):

- Choose **Alle, med avsendervarsel** (default) to accept mail from anyone with
  clear warnings on unknown or suspicious senders.
- Choose **Kun godkjente avsendere** to automatically reject mail from anyone
  other than verified company members and addresses/domains in your approved
  list (`faktura@leverandor.no` or `@leverandor.no`).
- Untick **Ta imot fakturaer og kvitteringer på denne e-postadressen** to turn
  e-mail intake off completely while keeping EHF receiving active.

## Good to know

- Ordinary internet e-mail (SMTP) and Cloudflare Email Routing do not guarantee
  EEA-only transit. Once received by Myntos, attachments and metadata are stored
  solely in the EEA. If you need EEA-only transit end to end, use **e-faktura
  (EHF over Peppol)** or upload receipts directly under **Betaling**.
- Unsupported attachments (such as `.zip`, `.html`, `.svg` or executables) are
  automatically ignored; HTML e-mail bodies are never rendered.

## Related

- [Receive and book an e-invoice (EHF)](https://myntos.no/llms/help/invoices/receive-and-book-an-e-invoice.md)
- [Register for e-invoicing](https://myntos.no/llms/help/company-and-access/register-for-e-invoicing.md)
- An agent with a key can inspect sender security and book received e-mail
  invoices through the API:
  [myntos-innkommende-efaktura](https://myntos.no/llms/skills/myntos-innkommende-efaktura.md)
