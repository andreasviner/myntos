---
title: Take a photo of a receipt
description: How a person photographs a paper receipt with their phone on the Betaling page in Myntos («Ta bilde av kvitteringen») or uploads photos and screenshots they already have, and what happens to the picture. Use when someone asks whether they can photograph receipts, use their phone, or upload screenshots or iPhone photos in Myntos.
pages: /kontantstrom/
skill: myntos-kontantstrom
---

# Take a photo of a receipt

A receipt does not have to be a PDF. On the «Betaling» page a phone can take
the picture straight into the form, and photos and screenshots already on the
phone or computer can be uploaded like any other file. Myntos reads the
picture and fills in the form, and the original picture is kept in the bilag
archive when the payment is registered.

## They might ask

- «Kan jeg ta bilde av kvitteringen med mobilen?»
- «Går det an å laste opp skjermbilder som bilag?»
- «Tar Myntos imot bilder fra iPhone?»
- "Can I photograph receipts in Myntos?"

## Before they start

- They need the role «Medlem» or more in the company.
- The picture must be readable: flat, in focus, the whole receipt in the
  frame.
- Reading pictures uses Myntos's AI. If the company has switched it off,
  pictures are still kept as bilag, but the form is not filled in from them.

## Steps

1. Open «Betaling» in the top menu (`https://myntos.no/kontantstrom/`) on the
   phone.
2. Click «Ta bilde av kvitteringen» under the upload box. The camera opens.
   The link only shows on a touch screen; on a computer, use «Last opp
   faktura, kvittering eller skjermbilder» to choose saved pictures instead.
3. Take the picture and accept it. Myntos starts reading it at once («Leser
   bilaget …»).
4. To add another picture to the same payment (a long receipt, or the order
   and the bank line), tap «Ta bilde av kvitteringen» again or «Legg til flere
   filer». Every picture is one bilag together.
5. Check the fields that were filled in, and press «Registrer betaling», as in
   [Record a payment from a receipt](https://myntos.no/llms/help/payments/record-a-payment-from-a-receipt.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «ikke lest» after the picture | Nothing could be read from it | Take a sharper picture, or fill in the fields |
| «Vi fikk ikke lest tekst ut av bildene. Prøv et skarpere bilde, eller fyll inn feltene selv.» | None of the pictures could be read | Retake in better light, or fill in the fields |
| «KI er ikke brukt på dette bilaget, …» | The company's AI is off, so pictures cannot be read | Fill in the fields by hand; the picture is still attached when they register. An administrator can switch AI on under «KI» |
| No «Ta bilde av kvitteringen» link | They are on a computer | Upload the saved picture with «Last opp faktura, kvittering eller skjermbilder» |

## Good to know

- Accepted pictures are PNG, JPEG, WebP, GIF and HEIC (the iPhone's own
  format), at most 25 MB each.
- An iPhone photo in HEIC is read through a JPEG copy with its location data
  removed. The archive always keeps the original picture exactly as it was
  uploaded.
- The AI runs on Myntos's own servers in Norway; the picture is not sent to an
  outside AI provider.
- A picture cannot be removed once the payment is registered, so check that it
  is the right receipt before pressing «Registrer betaling». Before that,
  «Fjern» next to the file takes it out.

## Related

- [Record a payment from a receipt](https://myntos.no/llms/help/payments/record-a-payment-from-a-receipt.md)
- [See the documents for a payment](https://myntos.no/llms/help/payments/see-the-documents-for-a-payment.md)
- An agent with a key can do this through the API:
  [myntos-kontantstrom](https://myntos.no/llms/skills/myntos-kontantstrom.md)
