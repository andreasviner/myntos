---
title: Where the books go when an AI reads them
description: Where a company's data goes when an AI assistant reads the books in Myntos with a key (the model provider decides), how that differs from Myntos's own KI, and who is responsible. Use when someone asks whether it is GDPR-safe or legal to let ChatGPT or Claude read their books in Myntos.
pages: /organisasjon/ /ki/about/
---

# Where the books go when an AI reads them

Myntos runs in Norway and sends a company's books to no model provider. With
a key, the AI assistant **fetches** the books itself, so they go wherever the
model behind the assistant sends them:

- a model the person runs on their own machine keeps them there;
- ChatGPT sends them to OpenAI and Claude to Anthropic, possibly outside the
  EEA, under those companies' terms.

Myntos does not know or check which model holds a key. The company chose the
tool, so the company is responsible for that choice, as it would be for an
export it sent somewhere. The window that switches keys on says this before
anybody can make a key.

## They might ask

- «Er det lov å la ChatGPT lese regnskapet?»
- «Hvor havner opplysningene når Claude leser regnskapet mitt?»
- «Følger dette GDPR?»

## Good to know

- The books contain personal data about customers, suppliers and employees.
  An AI with a key can read all of it.
- The AI is told to read only what the task needs.
- Myntos's own KI (reading receipts, filling in invoices, reading the
  vouchers for «Analyse») is different: Løynd, inside the EEA, and nothing
  stored there
  (https://myntos.no/llms/help/ai/what-myntos-ki-does.md). The card
  «KI-agenter og API-nøkler» and the window «Inviter en KI eller et program»
  on the company page say so too, with the link «Les mer om KI i Myntos» to
  the «Om KI» page (`https://myntos.no/ki/about/`).
- Myntos's privacy statement (`https://myntos.no/personvern/`) says the same.

## Related

- [Invite an AI](https://myntos.no/llms/help/ai/invite-an-ai.md)
- [What a key can never do](https://myntos.no/llms/help/ai/what-a-key-can-never-do.md)
