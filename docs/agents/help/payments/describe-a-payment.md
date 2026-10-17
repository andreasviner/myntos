---
title: Describe what a payment was for
description: How a person uses the «Beskrivelse» box on the Betaling page in Myntos to say what a payment was for, answer the questions Myntos asks about a bilag, and have the form filled in again with that sentence. Use when someone asks what to write in the description, why Myntos asks questions about a receipt, or how to explain a payment the receipt does not explain.
pages: /kontantstrom/
skill: myntos-kontantstrom
---

# Describe what a payment was for

At the top of the form on the «Betaling» page is one box, «Beskrivelse
(valgfritt)», with the hint «Hva gjaldt betalingen?». It does two jobs. It is
the text the payment is filed under in the books, and it is what Myntos's AI
reads the bilag with. Some bilag cannot be understood from the paper alone:
3 000 kroner from a private account looks the same whether it is the owner
putting money in, a loan being repaid or a sale. One sentence from the person
who knows settles it.

## They might ask

- «Hva skal jeg skrive i beskrivelsen?»
- «Myntos spør meg om noe om kvitteringen, hvor svarer jeg?»
- «Kvitteringen sier ikke hva det var til, hvordan forklarer jeg det?»
- "What is the description box for in Myntos?"

## Before they start

They need the role «Medlem» or more, and the form open on
`https://myntos.no/kontantstrom/`.

## Steps

1. Before uploading, or after: write in «Beskrivelse» what the payment was
   for, in their own words, for example «Kjøpte strømforsyning til kontoret,
   la ut 3 000 privat». Written before the upload, the bilag is read with it
   from the start.
2. Left empty, Myntos writes a description itself while the bilag is read.
   The box is locked with three dots while that happens, and the tag next to
   the label says «Skriver …».
3. If questions appear in a framed box together with the description, they
   are what Myntos could not settle from the bilag. One marked «Spurt av KI»
   was composed by Myntos's AI. A question with buttons is answered by
   pressing one; a question without is answered by writing in the box («Svar
   i beskrivelsen under» jumps there). See
   [Answer the questions about a receipt](https://myntos.no/llms/help/payments/answer-questions-about-a-receipt.md).
4. When they stop typing, or click outside the box, what they wrote goes to
   Myntos's AI, and the form is filled in again with it, without the bilag
   being read again (with no files uploaded there is nothing to fill in from).
   The tag says «Din tekst», and their sentence is kept as it is; the other
   fields may change to fit the answer.
5. Check the fields and press «Registrer betaling». The sentence in the box is
   what the payment is filed under.

## Good to know

- A field they changed by hand is not overwritten when the bilag is read
  again; only the fields they left alone are filled in anew.
- If the box is empty when they register, the payment is filed under the name
  of the type they chose, such as «Varekjøp».
- At most 255 characters.
- The sentence is shown only to Myntos's own AI (on Myntos's servers in
  Norway) and stored as the payment's text. Write it as you would want an
  auditor to read it.
- After the payment is registered, the description can only be changed with
  «Rediger», which keeps the original in the books:
  [Correct a payment](https://myntos.no/llms/help/payments/correct-a-payment.md).

## Related

- [Answer the questions about a receipt](https://myntos.no/llms/help/payments/answer-questions-about-a-receipt.md)
- [Record a payment from a receipt](https://myntos.no/llms/help/payments/record-a-payment-from-a-receipt.md)
- [Choose what a payment was for](https://myntos.no/llms/help/payments/choose-what-a-payment-was-for.md)
- An agent with a key can send the same sentence through the API:
  [myntos-kontantstrom](https://myntos.no/llms/skills/myntos-kontantstrom.md)
