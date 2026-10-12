---
title: Record a payment without a receipt
description: How a person registers a payment on the Betaling page in Myntos by filling in the form by hand, with «Ingen bilag? Fyll inn manuelt», when there is no receipt or invoice to upload. Use when someone asks how to record a cash expense, a bank fee or any payment they have no document for, or how to type a payment in themselves.
pages: /kontantstrom/
skill: myntos-kontantstrom
---

# Record a payment without a receipt

Most payments are registered by uploading the bilag and letting the form fill
itself in. When there is nothing to upload (a bank fee, a small cash purchase
whose receipt is lost, money the owner put in), the form can be filled in by
hand. Bokføringsloven § 10 still requires every entry to be documented, so the
receipt should be attached later if it turns up. Like every payment, this one
is posted at once and cannot be edited or deleted afterwards, only corrected
or reversed.

## They might ask

- «Hvordan fører jeg en betaling uten kvittering?»
- «Kan jeg skrive inn en betaling manuelt i Myntos?»
- «Jeg har mistet kvitteringen, hva gjør jeg?»
- "How do I record a payment by hand in Myntos?"

## Before they start

- They need the role «Medlem», «Administrator» or «Eier». A «Leser» sees «Du
  har lesetilgang» instead of the form.
- Check that the company named at the top right of the screen is the right
  one.

## Steps

1. Open «Betaling» in the top menu (`https://myntos.no/kontantstrom/`).
2. Under the upload box, click «Ingen bilag? Fyll inn manuelt». The fields
   open.
3. Choose «Penger inn» or «Penger ut». Nothing is chosen in advance.
4. In «Hva gjaldt det?», choose what the payment was for. The line under it
   says what the choice assumes about VAT. See
   [Choose what a payment was for](https://myntos.no/llms/help/payments/choose-what-a-payment-was-for.md).
5. Fill in «Dato» (the day the money moved) and «Beløp inkl. mva» (the amount
   that left or reached the account, VAT included).
6. Optional: «Motpart (valgfritt)». Type a name or choose one from the list.
   «Føringen går fint uten.»
7. Leave «Mva-kode» as proposed unless they know it should be different.
8. Choose «Betalt via»: «Bankkonto» or «Kontant».
9. Optional: write what it was for in «Beskrivelse» at the top of the form.
   Left empty, the payment is filed under the name of the type, such as
   «Bank- og kortgebyr».
10. Press «Registrer betaling». The form says «Bilag 2026-0015 er ført.»

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Velg penger inn eller ut først» in the type list | No direction chosen yet | Choose «Penger inn» or «Penger ut» |
| «regnskapsåret 2025 er avsluttet» | That year has been closed | Check the date; an administrator must reopen the year under «Årsavslutning» to post in it |
| «selskapet var ikke mva-registrert …, så mva-kode … kan ikke brukes …» | The company was not VAT-registered on that date, so it cannot deduct or charge VAT | Choose «0: Ingen merverdiavgiftsbehandling» in «Mva-kode» |

## Good to know

- Once the receipt turns up, attach it from the register with «Legg ved
  bilag»: [Attach a document to a payment](https://myntos.no/llms/help/payments/attach-a-document-to-a-payment.md).
  The overview beside the form counts the payments still missing one.
- The payment cannot be deleted afterwards. A mistake is fixed with «Rediger»
  or «Reverser» in the register.

## Related

- [Record a payment from a receipt](https://myntos.no/llms/help/payments/record-a-payment-from-a-receipt.md)
- [Correct a payment](https://myntos.no/llms/help/payments/correct-a-payment.md)
- An agent with a key can do this through the API:
  [myntos-kontantstrom](https://myntos.no/llms/skills/myntos-kontantstrom.md)
