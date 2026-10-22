---
title: Answer the questions about a receipt
description: How a person answers the questions Myntos asks after reading a bilag on the Betaling page, one at a time with «Neste», with the answer buttons or by writing the answer under the question, and what happens to the answers. Use when someone asks how to answer Myntos's AI, what «Spørsmål 1 av 2» or «Spurt av KI» means, or why the form changed after they answered.
pages: /kontantstrom/
skill: myntos-kontantstrom
---

# Answer the questions about a receipt

After Myntos has read an uploaded bilag, it sometimes asks about what the
paper does not say: whether money from a private person was the owner's
deposit or a sale, whether 31 000 kroner was one thing or several, which of two
categories a purchase belongs to. The questions appear one at a time in a
framed box at the top of the form on the «Betaling» page, above «Beskrivelse
(valgfritt)», and each is answered right under it. «Neste» brings the next
question; after the last, the answers go to Myntos's AI together, and the form
is filled in again with them in a few seconds, without the bilag being read
again. A question that has been answered is not asked again for that bilag.

## They might ask

- «Myntos spør meg om noe om kvitteringen, hvordan svarer jeg?»
- «Hvor svarer jeg KI-en i Myntos?»
- «Hva betyr «Spurt av KI»?»
- «Må jeg svare på spørsmålene?»
- "How do I answer the question Myntos asks about my receipt?"

## Before they start

They need the role «Medlem» or more, and a bilag uploaded on
`https://myntos.no/kontantstrom/`:
[Record a payment from a receipt](https://myntos.no/llms/help/payments/record-a-payment-from-a-receipt.md).
Questions only appear when something is unclear; most bilag get none.

## Steps

1. Look at the framed box at the top of the form. The small line above the
   question says «Spørsmål», or «Spørsmål 1 av 2» when there are more. «Spurt
   av KI» on the same line means the question was written by Myntos's AI
   rather than by the program's own reading.
2. Answer it right under the question:
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
     If none of the buttons fits, press «Skriv et annet svar» and write the
     answer in the line that appears.
   - **In their own words**, when the question has no buttons. Write the
     answer in the line under it («Skriv svaret ditt»), for example «Til
     selskapets drift, betalt med eget kort», and press the button beside it
     or Enter. The button says «Neste» while more questions follow, and «Send
     svar» on the last.
   - **The amount**, when Myntos could not find the total. Write what the
     account was actually charged in the line under the question and press
     the button beside it. It goes into «Beløp inkl. mva» at once, marked
     «Ditt svar». Typing it straight into «Beløp inkl. mva» answers the
     question too.
3. To leave a question unanswered, press «Hopp over». The next one appears,
   and the one passed over does not come back for this bilag.
4. After the last question the box says «Oppdaterer forslaget med svarene
   dine …» for a few seconds. Then the fields are filled in again and the box
   closes, or, if the answers raised something new, another question appears
   («Spørsmål 3 av 3»).
5. Check the fields and press «Registrer betaling» as usual.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Skriv beløpet i kroner, for eksempel 1 250,00.» under the amount line | What was written is not an amount | Write the number only, with a comma or a dot before the øre |
| «Vi fikk ikke oppdatert forslaget: …» | The answers could not be sent, or Myntos's AI did not answer | Check the fields and fill in what is missing by hand. The answers are kept and go with the next ones |
| The form reads the bilag again («Leser bilaget …») after an answer | More than ten minutes passed since the last answer, or KI was switched off in between, so Myntos no longer had the reading | Nothing; the answers given so far are sent again once it has read the bilag |

## Good to know

- Answering is optional. Every field can be filled in by hand instead, and
  the payment can be registered with questions left unanswered.
- Editing «Beskrivelse» while a question is open does not change the
  question. The sentence goes to Myntos's AI on its own, and the line under
  the files says «Oppdaterer forslaget med beskrivelsen din …» meanwhile.
- The answers go to Myntos's AI and are not stored. They are not written into
  «Beskrivelse», which is the text the payment is filed under. The AI may
  rewrite its own suggestion there to fit an answer; a description the person
  wrote themselves is kept as written.
- A field they choose by hand, or with an answer button, is not changed by
  later answers.
- Answers go only to Myntos's own AI (Løynd, on servers in Norway), never to
  an outside AI provider and never into a web search.
- Turning «Penger inn» / «Penger ut» around reads the bilag again; answers
  about the category are dropped, because they were about the other side.

## Related

- [Describe what a payment was for](https://myntos.no/llms/help/payments/describe-a-payment.md)
- [Choose what a payment was for](https://myntos.no/llms/help/payments/choose-what-a-payment-was-for.md)
- An agent with a key answers the same questions through the API:
  [myntos-kontantstrom](https://myntos.no/llms/skills/myntos-kontantstrom.md)
