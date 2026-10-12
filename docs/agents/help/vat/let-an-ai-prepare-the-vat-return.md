---
title: Let an AI prepare the VAT return
description: What a person's own AI (Claude, ChatGPT or another) can and cannot do with the MVA-melding in Myntos: it can read and review the VAT return and ask the person to file, but only the person can send it, with their own ID-porten login. Use when someone asks whether their AI can send the mva-melding, or what to do when their AI asks them to file it.
pages: /mva/
skill: myntos-mva
---

# Let an AI prepare the VAT return

A person can let their own AI work in the books with a key made in Myntos.
For the MVA-melding the AI can go through the termin, check the figures
against the books, explain them, and then ask the person to file. It can
never check the melding with Skatteetaten or send it: Skatteetaten requires
the person's own ID-porten login for that, so the last step is always the
person's.

## They might ask

- «Kan KI-en min sende mva-meldingen for meg?»
- «KI-en ba meg sende mva-meldingen. Hvor gjør jeg det?»
- «Hvordan gir jeg Claude tilgang til regnskapet?»
- "Can my AI file the VAT return in Myntos?"

## Before they start

- To give an AI access: «Inviter en KI» under «Selskapet» → «Tilgang»
  (`https://myntos.no/organisasjon/#tilgang`). Never give an AI a password or
  a BankID.
- To send: the role «Medlem» or above, ID-porten, and the right to report VAT
  for the company in Altinn.

## Steps

1. Ask the AI to go through the MVA-melding for the termin. It reads the same
   figures as the MVA page and should show them, with the total «Å betale» or
   «Til gode».
2. When the figures are right, the AI asks the person to file. The request
   appears on «Godkjenning» (`https://myntos.no/godkjenning/`) and leads to
   the termin on the MVA page.
3. On the MVA page, the person logs in with ID-porten, presses «Kontroller hos
   Skatteetaten», reads the answer, ticks the box and presses «Send inn
   mva-meldingen (…)»: [Send the MVA-melding](https://myntos.no/llms/help/vat/send-the-vat-return.md).
4. Afterwards the AI can read the receipt and the payment details from Myntos
   and remind the person to pay.

## Good to know

- Sending cannot be undone. The person should read the figures on the MVA
  page themselves, not only the AI's summary.
- The AI cannot pay the VAT and cannot change whether the company is
  VAT-registered.
- An AI may also ask to record VAT adjustments (uttak, tap på krav); those can
  be set to wait for the person's approval too.

## Related

- [Approve what an AI asks](https://myntos.no/llms/help/ai/approve-what-an-ai-asks.md)
- [Log in with ID-porten to file the VAT return](https://myntos.no/llms/help/vat/log-in-with-id-porten-for-the-vat-return.md)
- What the AI itself follows: [myntos-mva](https://myntos.no/llms/skills/myntos-mva.md)
