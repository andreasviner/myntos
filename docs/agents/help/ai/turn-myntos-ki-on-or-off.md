---
title: Turn Myntos's KI on or off
description: How an owner or administrator switches Myntos's built-in KI (KI chat, reading receipts, filling in invoices and «Analyse») off or on for one company under Innstillinger for selskapet (/organisasjon/#ki), and what changes when it is off. Use when someone wants no documents or books sent to any AI, or asks how to turn KI off in Myntos.
pages: /organisasjon/
---

# Turn Myntos's KI on or off

## They might ask

- «Hvordan slår jeg av KI i Myntos?»
- «Jeg vil ikke at kvitteringene skal leses av KI.»
- «Hvor finner jeg bryteren for å slå av KI?»
- «Hvordan får jeg kostnadene fordelt etter hva de gjelder på Analyse?»

## Steps

1. Signed in with the right company chosen at the top right, open company
   settings via the gear icon («Innstillinger») or the company menu →
   **Innstillinger for selskapet** (`https://myntos.no/organisasjon/#ki`).
2. Scroll to **KI i Myntos**.
3. Tick **Slå av KI for <selskapsnavn>** to turn KI off. The box is unticked
   while KI is on; ticking it turns all built-in KI off for this company.
4. Untick the box to turn KI back on.

## What changes when it is off

- No documents, chat prompts, or accounting data are sent to Løynd, at any
  level.
- **KI** chat (`/ki/`) shows that KI is switched off and cannot send messages.
- PDFs uploaded as receipts are still read by Myntos's own local text reader,
  so forms still fill in where the PDF contains text.
- Photos cannot be read at all, but are still stored in the bilag archive as
  before.
- «KI-nivå» and «Legg ved bilde» disappear from the KI boxes.
- Myntos stops reading vouchers for «Analyse», any running analysis stops,
  «Dyp KI-analyse» cannot be started, and previously stored tags are not used.
- A running KI subscription does not renew after its current period while KI is
  switched off, and a new plan cannot be activated while KI is off
  ([Buy a KI plan](https://myntos.no/llms/help/ai/buy-a-ki-plan.md)).

## Good to know

- Only an «Eier» or «Administrator» can change this setting.
- It applies to one company only: the one shown at the top right.
- It does not affect external AI assistants connected with a key. Those are
  switched off under «Innstillinger for selskapet» → «Tilgang»:
  https://myntos.no/llms/help/ai/turn-off-keys-for-the-company.md.
- No key can change this switch; only a person can.

## Related

- [What Myntos's own KI does](https://myntos.no/llms/help/ai/what-myntos-ki-does.md)
- [Ask Myntos KI anything about your books](https://myntos.no/llms/help/ai/ask-ki-about-your-books.md)
