---
title: Delete a payment
description: Why a registered payment, or a receipt attached to it, cannot be deleted in Myntos (bokføringsloven), and what a person does instead on the Betaling page: «Rediger» for a wrong detail, «Reverser» for a payment that should not be there. Use when someone asks how to delete, remove or erase a payment, a bilag or a wrong receipt in Myntos.
pages: /kontantstrom/
skill: myntos-kontantstrom
---

# Delete a payment

There is no delete button, on purpose. Norwegian bookkeeping law
(bokføringsloven) requires a posted entry to stay in the books, and a
correction to be visible as a correction, so that an auditor can see both
the mistake and what was done about it. The bilag archive is the same: a file
attached to a payment must be kept for five years after the financial year
ends. What the person can do instead depends on what is wrong. The page
explains this itself under «Hvorfor kan ingenting slettes?» in the «Oversikt»
card.

## They might ask

- «Hvordan sletter jeg en betaling i Myntos?»
- «Hvordan fjerner jeg en feil kvittering?»
- «Jeg la inn noe feil, kan jeg slette det?»
- "How do I delete a transaction in Myntos?"

## Before they start

They need the role «Medlem», «Administrator» or «Eier» to correct or reverse
anything.

## Steps

1. Open «Betaling» in the top menu (`https://myntos.no/kontantstrom/`) and
   find the payment in the register.
2. Decide which of the two it is:
   - **The payment happened, but something about it is wrong** (amount, date,
     type, VAT code, counterparty, description): click «Rediger», fix it and
     press «Lagre endringer». See
     [Correct a payment](https://myntos.no/llms/help/payments/correct-a-payment.md).
   - **The payment should not be there at all** (it did not happen, or it was
     entered twice): click «Reverser» and confirm. See
     [Reverse a payment](https://myntos.no/llms/help/payments/reverse-a-payment.md).
3. In both cases the register folds the entries into one line, so it does not
   look like several payments.

## Good to know

- A wrong file attached to a payment cannot be removed, and the page offers
  «Legg ved bilag» only on payments with no file. To get the right file in,
  correct the payment with «Rediger» and choose the right file in the form
  before «Lagre endringer»: it is attached to the corrected entry, while the
  wrong one stays on the original, folded underneath.
- Files chosen in the form are not stored until «Registrer betaling» is
  pressed. Before that, «Fjern» or «Fjern alle» takes them out and leaves
  nothing behind.
- Nobody can delete an entry, administrators and owners included.

## Related

- [Correct a payment](https://myntos.no/llms/help/payments/correct-a-payment.md)
- [Reverse a payment](https://myntos.no/llms/help/payments/reverse-a-payment.md)
- An agent with a key can correct or reverse through the API, but cannot
  delete either: [myntos-kontantstrom](https://myntos.no/llms/skills/myntos-kontantstrom.md)
