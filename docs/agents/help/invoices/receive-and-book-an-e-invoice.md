---
title: Receive and book an e-invoice (EHF)
description: How to view, download, book, dismiss or reopen incoming supplier e-invoices (innkommende e-faktura / EHF) received over Peppol on the Mottatt page in Myntos. Use when someone asks where received EHF invoices show up, why an e-invoice has not arrived yet, or how to book a received e-invoice.
pages: /innkommende/
skill: myntos-innkommende-efaktura
---

# Receive and book an e-invoice (EHF)

When Peppol receiving is switched on for a company, supplier invoices and
credit notes sent as EHF over Peppol arrive automatically in Myntos under
«Mottatt» (alongside any invoices emailed to the company's `@myntos.no`
address). Note that turning on EHF on the Faktura page activates **sending
only**; receiving EHF is switched on separately under «Innstillinger for
selskapet» → «E-faktura». A new e-invoice normally appears automatically within
seconds of the access point receiving it, and Myntos also checks by itself
every 15 minutes as a backup. Booking an incoming EHF posts a voucher in the
books and archives a readable PDF in the bilag archive, while keeping the
original received EHF XML unchanged under bokføringsloven § 13.

## They might ask

- «Hvor finner jeg mottatte e-fakturaer i Myntos?»
- «Hvordan bokfører jeg en innkommende EHF-faktura?»
- «Hvorfor har ikke leverandørfakturaen kommet inn ennå?»
- «Kan jeg avvise en e-faktura som jeg allerede har ført?»
- "How do I receive and book supplier EHF invoices in Myntos?"

## Before they start

- Peppol receiving turned on for the company under «Innstillinger for
  selskapet» → «E-faktura» → «Slå på mottak av e-faktura»:
  [Register for e-invoicing](https://myntos.no/llms/help/company-and-access/register-for-e-invoicing.md).
- The role «Medlem», «Administrator» or «Eier» to book, dismiss or reopen an
  invoice. A «Leser» can view and download PDF and XML.

## Steps

1. Open «Mottatt» in the top menu (`https://myntos.no/innkommende/`).
2. Use the source toggle (**Alle**, **Kun e-faktura (EHF)**, **Kun
   e-postfaktura**) and status buttons (**Nye**, **Bokført**, **Avvist**,
   **Alle**) to choose which invoices are listed.
3. Click a row in the list to open its details on the right: supplier name and
   orgnr, dates, KID, bank account number, invoice lines, VAT breakdown, and
   links to «Last ned PDF» and «Last ned EHF (XML)».
4. Under «Bokfør som bilag», check and fill in:
   - «Hva gjaldt fakturaen?» (the cost/purchase category, or a refund category
     for a credit note)
   - «Dato» (pre-filled with the invoice date; change to the payment date if
     needed)
   - «Beløp i NOK (inkl. mva)» (pre-filled for NOK invoices; for foreign
     currency invoices, type the NOK amount paid)
   - «Mva-kode» (pre-selected from the EHF's VAT rate)
   - «Betalt via» («Bank (1920)» or «Kontant (1900)»)
   - «Beskrivelse» and whether to tick «Knytt til leverandør automatisk (opprett
     hvis den ikke finnes)»
5. Press «Bokfør bilag». The voucher is posted (irreversible; correct mistakes
   by reversing the voucher) and the PDF is filed in the bilag archive
   automatically.

To clear an invoice that was already booked from a PDF or sent in error, press
«Avvis». Nothing is deleted; switch to «Avvist» and press «Åpne på nytt» if you
ever need to book it later.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Mottak av e-faktura er ikke slått på for dette selskapet ennå.» | Receiving is still off | Follow «Slå på mottak under Innstillinger for selskapet» ([Register for e-invoicing](https://myntos.no/llms/help/company-and-access/register-for-e-invoicing.md)) |
| «Fakturaen er i EUR. Skriv inn beløpet i norske kroner (NOK) …» | Foreign currency invoice | Enter the NOK amount actually paid from the bank in «Beløp i NOK (inkl. mva)» |
| No «Bokfør som bilag» form | Their role is «Leser», or the invoice is already «Bokført» / «Avvist» | Ask someone with write access, or press «Åpne på nytt» if it was dismissed |
| «E-faktura over Peppol er ikke satt opp i denne installasjonen av Myntos.» | The installation has no Peppol access point configured | Upload the supplier's PDF under «Betaling» instead |

## Good to know

- E-invoices arrive automatically as soon as the access point receives them,
  with an automatic backup check every 15 minutes. If a supplier invoice has not
  appeared yet, verify that receiving is turned on under «Innstillinger for
  selskapet» → «E-faktura» and check again shortly.
- Booking a received EHF posts a voucher in the books — it does **not** pay the
  supplier. Pay the bill in your online bank using the KID and kontonummer
  shown on the invoice.
- If the supplier embedded a PDF inside the EHF file, «Last ned PDF» and the
  archived bilag use that PDF; otherwise Myntos renders a clean A4 summary from
  the frozen EHF XML.
- Received EHF files are never deleted so they remain available for five years
  under bokføringsloven § 13.

## Related

- [Receive invoices by e-mail](https://myntos.no/llms/help/invoices/receive-invoices-by-email.md)
- [Register for e-invoicing](https://myntos.no/llms/help/company-and-access/register-for-e-invoicing.md)
- [Record a payment from a receipt](https://myntos.no/llms/help/payments/record-a-payment-from-a-receipt.md)
- An agent with a key can sync, inspect and book incoming EHFs through the API:
  [myntos-innkommende-efaktura](https://myntos.no/llms/skills/myntos-innkommende-efaktura.md)
