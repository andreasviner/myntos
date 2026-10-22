---
title: Let an AI prepare the VAT return
description: What a person's own AI (Claude, ChatGPT or another) can and cannot do with the MVA-melding in Myntos: it can read and review the VAT return, have Skatteetaten check it and send it, but each of those waits for the person to approve it on «Godkjenning» with their own ID-porten login. Use when someone asks whether their AI can send the mva-melding, or what to do when their AI asks them to file it.
pages: /mva/
skill: myntos-mva
---

# Let an AI prepare the VAT return

A person can let their own AI work in the books with a key made in Myntos.
For the MVA-melding the AI can go through the termin, check the figures
against the books, explain them, and ask to have Skatteetaten check the
melding and to send it. Neither goes before the person approves it on
«Godkjenning», every time, whatever the key may do on its own: it then goes
with the person's own ID-porten login, which Skatteetaten requires, so the
last step is always the person's.

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
  for the company in Altinn. For the AI to ask to send, its key needs «Selv»
  or «Spør meg først» for «Bokføre».


## Steps

1. Ask the AI to go through the MVA-melding for the termin. It reads the same
   figures as the MVA page and should show them, with the total «Å betale» or
   «Til gode».
2. When the figures are right, the AI asks for the check. On «Godkjenning»
   (`https://myntos.no/godkjenning/`) the card reads ««<key name>» vil sende
   mva-meldingen til kontroll hos Skatteetaten». The person logs in with
   ID-porten if the card asks, and presses «Godkjenn».
3. The AI reads Skatteetaten's answer and, if the person wants it sent, asks
   to send it: ««<key name>» vil sende mva-meldingen til Skatteetaten». The
   amount in «Det som blir sendt» (`fastsatt_merverdiavgift`) is what they
   confirm by pressing «Godkjenn»:
   [File what your AI prepared](https://myntos.no/llms/help/ai/file-what-an-ai-asks-you-to-file.md).
4. An AI whose key may not post asks the person to file it themselves
   instead; that request leads to the termin on the MVA page:
   [Send the MVA-melding](https://myntos.no/llms/help/vat/send-the-vat-return.md).
5. Afterwards the AI can read the receipt and the payment details from Myntos
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
