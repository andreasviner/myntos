---
title: File what your AI prepared
description: What to do when an AI assistant in Myntos asks to send the MVA-melding (VAT return) or the skattemelding (tax return) to Skatteetaten, or to have it checked there: why it always waits for the person, and how they approve it on «Godkjenning» with their own ID-porten login; and what to do when the AI instead asks the person to file it themselves. Use when someone asks whether their AI can send the VAT return or skattemelding, or what a request about Skatteetaten on «Godkjenning» means.
pages: /godkjenning/ /mva/ /skattemelding/
skill: myntos-godkjenning
---

# File what your AI prepared

An AI can prepare the MVA-melding or the skattemelding, have Skatteetaten
check it and file it, but **nothing it sends to Skatteetaten goes until the
person approves it**, every time, even when its key may post on its own
(«Selv»). Each request waits on «Godkjenning» and goes with the person's own
ID-porten login when they press «Godkjenn», as if they had pressed the button
on the MVA or Skattemelding page themselves, because Skatteetaten requires
the person's own login.

## They might ask

- «Kan KI-en sende mva-meldingen for meg?»
- «Agenten vil sende skattemeldingen. Hva gjør jeg?»
- «Agenten ber meg sende mva-meldingen. Hva gjør jeg?»
- «Kan agenten levere skattemeldingen?»

## Steps

1. Open «Godkjenning» (`https://myntos.no/godkjenning/`). A request the AI
   wants to send reads, for example, ««<key name>» vil sende skattemeldingen
   til kontroll hos Skatteetaten», ««<key name>» vil sende skattemeldingen
   til Skatteetaten» or ««<key name>» vil sende mva-meldingen til
   Skatteetaten», with the line «Når du godkjenner, sendes dette til
   Skatteetaten med din egen ID-porten-innlogging, som om du trykket på
   knappen selv.»
2. Read «Agentens forklaring:» and «Det som blir sendt». For a filing, the
   amount in it (`fastsatt_merverdiavgift` for the MVA-melding,
   `skattemessig_resultat` for the skattemelding) is the figure they confirm
   by approving. «Se MVA» or «Se Skattemelding» opens the page with the
   figures.
3. If the card shows «Logg inn med ID-porten», press it and log in with
   BankID or another ID-porten method; it comes back to the card.
4. Press «Godkjenn». The card says «Utført.» when Skatteetaten has the check
   or the filing, or «Myntos avviste det: …» with the reason. The result also
   shows on the MVA or Skattemelding page.

**If the AI asks the person to file it themselves** (its key may not post),
the request reads ««<key name>» ber deg om at du sender mva-meldingen for
<n>. termin <year>» or ««<key name>» ber deg om at du sender skattemeldingen
for <year>». Press «Gå til MVA-siden» or «Gå til skattemeldingen», and check
and send it there as described in
[Send the MVA-melding](https://myntos.no/llms/help/vat/send-the-vat-return.md)
or [Send the tax return](https://myntos.no/llms/help/tax-return/send-the-tax-return.md).
The request turns «Utført» by itself once the termin or the year has been
sent.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Logg inn med ID-porten» instead of «Godkjenn» | They are not logged in with ID-porten | Log in, then «Godkjenn» |
| «Myntos avviste det: … endret seg etter kontrollen …» | The books changed after the check | Ask the AI to ask for the check again |
| «Myntos avviste det: …» with something else | Skatteetaten or Myntos refused it | Tell the AI; the reason is in the message |

## Good to know

- If the person does not want it sent, «Avvis» removes the request.
- A filed melding cannot be withdrawn, only replaced by a newer one.
- The skattemelding has one question no AI can answer for the person, about
  benefits between the company and its shareholders. The AI asks them, and
  the answer is in «Det som blir sendt» (`ytelser_til_aksjonaerer`).

## Related

- [Approve what an AI asks to do](https://myntos.no/llms/help/ai/approve-what-an-ai-asks.md)
- [Let an AI prepare the VAT return](https://myntos.no/llms/help/vat/let-an-ai-prepare-the-vat-return.md)
- [What a key can never do](https://myntos.no/llms/help/ai/what-a-key-can-never-do.md)
