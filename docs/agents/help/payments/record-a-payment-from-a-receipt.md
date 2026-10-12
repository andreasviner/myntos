---
title: Record a payment from a receipt
description: How a person registers a payment in or out on the Betaling page in Myntos by uploading the receipt, invoice, photo or screenshots, letting the form fill itself in, checking it and pressing «Registrer betaling». Use when someone asks how to bokføre a receipt, record an expense or income, or "føre" a payment in Myntos.
pages: /kontantstrom/
skill: myntos-kontantstrom
---

# Record a payment from a receipt

Every payment that goes in or out of the company's bank account or cash box is
registered on the «Betaling» page, with the bilag (the receipt, invoice or
screenshots that document it). The person uploads the bilag first, Myntos
reads it and fills in the form, and the person checks the form and registers.
A registered payment is posted at once and can never be edited or deleted
afterwards, only corrected or reversed (Norwegian bookkeeping law,
bokføringsloven), so the check before pressing the button is the one that
counts.

## They might ask

- «Hvordan fører jeg en kvittering i Myntos?»
- «Hvordan bokfører jeg en faktura jeg har betalt?»
- «Hvor registrerer jeg at vi har fått betalt?»
- "How do I record an expense in Myntos?"

## Before they start

- They need the role «Medlem», «Administrator» or «Eier» in the company. A
  «Leser» sees the card «Du har lesetilgang» instead of the form.
- The company named at the top right of the screen is the one the payment is
  booked in. If it is the wrong one, switch first:
  [Switch to another company](https://myntos.no/llms/help/overview/switch-company.md).
- The bilag as a PDF or picture (PNG, JPEG, WebP, GIF or HEIC from an iPhone),
  at most 25 MB per file. No receipt at all:
  [Record a payment without a receipt](https://myntos.no/llms/help/payments/record-a-payment-without-a-receipt.md).

## Steps

1. Open «Betaling» in the top menu (`https://myntos.no/kontantstrom/`). The
   page is headed «Inn og ut», and the form on the left «Registrer en
   betaling».
2. Optional, and often worth it: type one sentence in the «Beskrivelse» box
   about what the payment was for, such as «Skjerm til kontoret». Myntos reads
   the bilag with that sentence in mind. See
   [Describe what a payment was for](https://myntos.no/llms/help/payments/describe-a-payment.md).
3. Click «Last opp faktura, kvittering eller skjermbilder», or drag the files
   onto that box. Choose every file that belongs to the payment: an order, the
   chat where the price was agreed and the bank line can all be one bilag. On
   a phone, «Ta bilde av kvitteringen» opens the camera.
4. Wait while it says «Leser bilaget …» (or «Leser 3 dokumenter … bilder tar
   litt tid.»). Pictures take longer than PDFs. The description is written
   into the box as it is read.
5. Check every field. A small tag next to a field says where its value came
   from: «Fra bilaget» (printed on the bilag), «Foreslått av KI» (Myntos's AI
   chose it; check it carefully) or «Regnet om» (converted from another
   currency). The fields:
   - «Penger inn» or «Penger ut».
   - «Hva gjaldt det?»: what the payment was for. The line under it explains
     what the choice assumes. See
     [Choose what a payment was for](https://myntos.no/llms/help/payments/choose-what-a-payment-was-for.md).
   - «Dato» and «Beløp inkl. mva»: the amount that actually moved, VAT
     included.
   - «Motpart (valgfritt)»: who was paid or who paid. Type a name or choose
     from the list; a new name is saved as a new customer or supplier when the
     payment is registered («Ny motpart. Navnet lagres når du fører.»).
   - «Mva-kode»: proposed from the type. Change it only if the receipt says
     otherwise.
   - «Konto», shown only when the company has other accounts of the same kind.
   - «Betalt via»: «Bankkonto» or «Kontant».
6. If questions appear above the description box, answer them in the box.
   The bilag is read again with the answer, by itself.
7. Press «Registrer betaling». The payment is posted, then the files are
   attached («Laster opp dokumentene … 2 av 3»).
8. The form says «Bilag 2026-0014 er ført.» (the bilag number), and the
   payment appears at the top of the register below, with its files under
   «Bilagsfil».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| The card «Du har lesetilgang» instead of the form | Their role is «Leser» | Ask an administrator for the role «Medlem» |
| «ikke lest» after a file | Nothing could be read from that file | Check the fields by hand, or try a sharper photo |
| «Vi fikk ikke lest tekst ut av bildene. Prøv et skarpere bilde, eller fyll inn feltene selv.» | The pictures were unreadable | Take a sharper photo, or fill in the fields |
| «KI er ikke brukt på dette bilaget, …» | The company has switched off Myntos's AI, or it was unavailable, so only the PDF's own text was read | Fill in what is missing. An administrator can switch AI back on under «KI» in the top menu |
| «PDF-en er passordbeskyttet, så vi fikk ikke lest innholdet.» | The PDF is locked | Upload an unlocked copy, or fill in the fields |
| «Vi fikk ikke lest bilaget: … Fyll ut skjemaet selv.» | The file is not a PDF or picture, is over 25 MB, or reading failed | Use a PDF or picture under 25 MB, or fill in the form |
| «Selskapet er ikke mva-registrert, så mvaen på bilaget kan ikke trekkes fra. …» | The company is not VAT-registered, so the whole amount is the cost | Nothing; this is correct |
| «Betalingen er ført, men disse ble ikke lagret: …» | The payment is posted but some files failed | Attach them from the register: [Attach a document to a payment](https://myntos.no/llms/help/payments/attach-a-document-to-a-payment.md). Do not register the payment again |
| «Vi fikk ikke lagt … inn som motpart, så betalingen er ikke ført. …» | The new counterparty could not be saved, so nothing was posted | Empty the «Motpart» box and register without one, or try again |
| «regnskapsåret 2025 er avsluttet» | That financial year has been closed (årsavslutning), and nothing more can be posted in it | Check the date. If the payment really belongs to that year, an administrator has to reopen the year under «Årsavslutning» first |
| «selskapets første regnskapsår i Myntos er …» | The date is before the company's books in Myntos begin | Check the date |

## Good to know

- A registered payment cannot be edited or deleted. A wrong detail is fixed
  with «Rediger» and a payment that should not be there with «Reverser»; both
  keep the original in the books.
- Files attached cannot be removed either. The law requires the bilag to be
  kept for five years, so attach only what documents this payment.
- The uploaded files are not stored until «Registrer betaling» is pressed.
  Removing a file with «Fjern», or leaving the page, leaves nothing behind.
- When Myntos's AI reads the bilag, it runs on Myntos's own servers in Norway
  (Løynd); nothing is sent to an outside AI provider.
- Picking a different «Penger inn» / «Penger ut» or editing the description
  reads the bilag again. Fields the person changed by hand are left alone.

## Related

- [Take a photo of a receipt](https://myntos.no/llms/help/payments/photograph-a-receipt.md)
- [Record a payment in a foreign currency](https://myntos.no/llms/help/payments/record-a-payment-in-a-foreign-currency.md)
- [Correct a payment](https://myntos.no/llms/help/payments/correct-a-payment.md)
- An agent with a key can do this through the API:
  [myntos-kontantstrom](https://myntos.no/llms/skills/myntos-kontantstrom.md).
  To let one, see [Invite an AI](https://myntos.no/llms/help/ai/invite-an-ai.md).
