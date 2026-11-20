---
title: Send the MVA-melding
description: How a person sends (files) the VAT return, the mva-melding, to Skatteetaten from the Mva-melding page in Myntos, from ID-porten login through the check to the confirmation, and what happens afterwards. Use when someone asks how to send, file or deliver the mva-melding in Myntos, or when their AI has asked them to file it.
pages: /mva/
skill: myntos-mva
---

# Send the MVA-melding

Sending the MVA-melding files it with Skatteetaten through Altinn. It is two
steps on one page: have Skatteetaten check the melding, then confirm the
amount and send. Both go with the person's ID-porten login, which the first
press asks for if it is needed. **A sent melding cannot be
withdrawn.** It can only be replaced, by sending a new melding for the same
termin.

## They might ask

- «Hvordan sender jeg mva-meldingen i Myntos?»
- «Hvordan leverer jeg mva for 5. termin?»
- «KI-en min ba meg sende mva-meldingen. Hva gjør jeg?»
- "How do I file the VAT return from Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier», and the right to report VAT
  for the company in Altinn.
- BankID, Buypass or Commfides for ID-porten.
- The termin has ended, and the figures are right:
  [See what is owed for a VAT period](https://myntos.no/llms/help/vat/see-what-is-owed-for-a-termin.md).

## Steps

1. Open `https://myntos.no/mva/` (from «Oversikt», the card «Merverdiavgift»,
   «Mva-meldingen →») and click «Se meldingen» on the termin. If an AI asked
   them to file, the request under «Godkjenning» leads to the same place.
2. Read the lines and the total, «Å betale» or «Til gode».
3. Press «Kontroller hos Skatteetaten». Nothing is sent yet. If they are not
   logged in with ID-porten, the press goes through ID-porten first and the
   check runs when they are back: [Log in with ID-porten to file the VAT return](https://myntos.no/llms/help/vat/log-in-with-id-porten-for-the-vat-return.md).
   See [Check the VAT return with Skatteetaten](https://myntos.no/llms/help/vat/check-the-vat-return-with-skatteetaten.md).
4. In the box «Send inn», read Skatteetaten's answer.
5. Tick the box «Jeg har gått gjennom meldingen og vil sende den inn for …»,
   which names the company, its organisation number, the termin and the
   amount. Check all four.
6. Press «Send inn mva-meldingen (…)», which shows the amount again. This is
   the step that cannot be undone. If the login ran out since the check, the
   press goes through ID-porten and sends when they are back.
7. The page says «Meldingen er sendt, og kvitteringen er lagret.» or
   «Meldingen er sendt. Kvitteringen kommer litt senere.» The termin's status
   becomes «Sendt og mottatt» or «Sendt, venter på kvittering».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No box «Send inn» after the check | Skatteetaten would reject it, or something was entered in the termin after the check | Read the box, or press «Kontroller hos Skatteetaten» again |
| «regnskapet for terminen er endret etter kontrollen; kontroller meldingen på nytt» | The books changed between the check and sending | Check again, read the new figures, then send |
| «bekreft beløpet i meldingen du kontrollerte før den sendes» | The box was not ticked for the checked amount | Reload, check again, tick and send |
| «Innsendingen stoppet underveis. …» | Altinn stopped partway | [Handle a submission that stopped partway](https://myntos.no/llms/help/vat/handle-a-submission-that-stopped-partway.md) |
| «Meldingen for terminen er sendt. …» where the button was | The termin is already sent, and the books still say the same | Nothing to send. A new melding is only for a change: [Correct a VAT return already sent](https://myntos.no/llms/help/vat/correct-a-vat-return-already-sent.md) |
| «meldingen for … er sendt, og regnskapet sier det samme som da. …» | A check from before the termin was sent, sent afterwards | Nothing to send; the termin is filed |
| «Du er logget inn, men meldingen ble ikke sendt: den står ikke lenger klar. Se over terminen.» | The press went through ID-porten, and meanwhile the books changed or the melding was sent from another tab | Read the termin again, check again if needed, then send |
| «logg inn med ID-porten for å kontrollere og sende mva-meldingen» | The login did not take, even after the trip through ID-porten | Reload and press again |

## Good to know

- What is sent is exactly the melding that was checked, never a newer one.
- When it is sent, Myntos moves the termin's VAT onto account 2740 in a
  voucher of its own:
  [Understand the VAT settlement voucher](https://myntos.no/llms/help/vat/understand-the-vat-settlement-voucher.md).
- Paying is separate, from the bank: [Get the receipt and payment details](https://myntos.no/llms/help/vat/get-the-receipt-and-payment-details.md).
  Recording it in the books is one button, «Før betalingen»:
  [Record the VAT payment or refund](https://myntos.no/llms/help/vat/record-the-vat-payment-or-refund.md).
- An AI can prepare the melding and ask, but never send it:
  [Let an AI prepare the VAT return](https://myntos.no/llms/help/vat/let-an-ai-prepare-the-vat-return.md).

## Related

- [Correct a VAT return already sent](https://myntos.no/llms/help/vat/correct-a-vat-return-already-sent.md)
- [Approve what an AI asks](https://myntos.no/llms/help/ai/approve-what-an-ai-asks.md)
- An agent with a key can review the melding and ask the person to send it:
  [myntos-mva](https://myntos.no/llms/skills/myntos-mva.md)
