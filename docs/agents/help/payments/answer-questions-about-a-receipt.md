---
title: Answer the questions about a receipt
description: How a person answers the questions Myntos asks after reading a bilag on the Betaling page, with the answer buttons, in the «Beskrivelse» box or in the amount field, and what happens to the answer. Use when someone asks how to answer Myntos's AI, what the questions above the description mean, or why a question went away.
pages: /kontantstrom/
skill: myntos-kontantstrom
---

# Answer the questions about a receipt

After Myntos has read an uploaded bilag, it sometimes asks about what the
paper does not say: whether money from a private person was the owner's
deposit or a sale, whether 31 000 kroner was one thing or several, which of two
categories a purchase belongs to. The questions appear in a framed box at the
top of the form on the «Betaling» page, together with the «Beskrivelse» box,
and each one is answered right there. The answer goes to Myntos's AI and the
form is filled in again with it, in a few seconds, without the bilag being read
again. A question that has been answered is not asked again for that bilag.

## They might ask

- «Myntos spør meg om noe om kvitteringen, hvordan svarer jeg?»
- «Hvor svarer jeg KI-en i Myntos?»
- «Hva betyr «Spurt av KI»?»
- "How do I answer the question Myntos asks about my receipt?"

## Before they start

They need the role «Medlem» or more, and a bilag uploaded on
`https://myntos.no/kontantstrom/`:
[Record a payment from a receipt](https://myntos.no/llms/help/payments/record-a-payment-from-a-receipt.md).
Questions only appear when something is unclear; most bilag get none.

## Steps

1. Look at the box above «Beskrivelse (valgfritt)». Each question starts with
   a «?». One marked «Spurt av KI» under it was written or chosen by Myntos's
   AI rather than by the program's own reading.
2. Answer each question the way it offers:
   - **With a button**, when the question lists its answers. For example
     «Innskudd fra eier», «Tilbakebetalt utlegg», «Et lån» or «Et salg» for
     money in from someone who is not a customer; «Én ting som skal brukes i
     tre år eller mer», «Én ting som skal brukes kortere enn tre år» or
     «Flere ting, hver under 30 000 kr» for a purchase over the driftsmiddel
     limit; «Alt på 25 %» (or the other rate shown) or «Ulike deler, hver med
     sin sats» when the receipt shows two VAT rates; or one of the two
     category names when Myntos is unsure between them. A button that decides
     a field fills it in at once, and the field is marked «Ditt svar».
     «Ulike deler, hver med sin sats» opens «Linjer», one line per part:
     [Split a payment into several lines](https://myntos.no/llms/help/payments/split-a-payment-into-lines.md).
   - **In the description**, when the question has no buttons. Press «Svar i
     beskrivelsen under» to jump to the «Beskrivelse» box, and write the
     answer as a sentence about the payment, for example «Skjermkort til
     kontoret, betalt med eget kort». The answer is sent when they stop
     typing or click outside the box.
   - **In the amount field**, when Myntos could not find the total. Press
     «Fyll inn beløpet» to jump to «Beløp inkl. mva», and type what the
     account was actually charged.
3. While Myntos takes the answer in, the box says «Oppdaterer forslaget med
   svaret ditt …». The answered question then disappears, and the fields are
   filled in again. Another question may take its place.
4. Check the fields and press «Registrer betaling» as usual.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Vi fikk ikke oppdatert forslaget: …» | The answer could not be sent, or Myntos's AI did not answer | Check the fields and fill in what is missing by hand. The answer is kept and goes with the next one |
| The form reads the bilag again («Leser bilaget …») after an answer | More than ten minutes passed since the last answer, or KI was switched off in between, so Myntos no longer had the reading | Nothing; the answers given so far are sent again once it has read the bilag |

## Good to know

- A field they choose by hand, or with an answer button, is not changed by
  later answers.
- The «Beskrivelse» box is also the text the payment is filed under in the
  books, so an answer written there is stored and posted with the payment.
  Write it as they would want an auditor to read it. Answers given with a
  button are sent to Myntos's AI and not stored.
- Answers go only to Myntos's own AI (Løynd, on servers in Norway), never to
  an outside AI provider and never into a web search.
- Turning «Penger inn» / «Penger ut» around reads the bilag again; answers
  about the category are dropped, because they were about the other side.

## Related

- [Describe what a payment was for](https://myntos.no/llms/help/payments/describe-a-payment.md)
- [Choose what a payment was for](https://myntos.no/llms/help/payments/choose-what-a-payment-was-for.md)
- An agent with a key answers the same questions through the API:
  [myntos-kontantstrom](https://myntos.no/llms/skills/myntos-kontantstrom.md)
