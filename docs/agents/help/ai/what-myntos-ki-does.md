---
title: What Myntos's own KI does
description: What the built-in KI in Myntos does (reads uploaded receipts and suggests invoice lines, run by Løynd in Norway), what is sent with a receipt, and what it never does. Use when someone asks whether Myntos uses AI, where their receipts go, or whether the AI posts anything by itself.
pages: /ki/
---

# What Myntos's own KI does

Myntos has its own KI, separate from any AI assistant a person connects with
a key. It is run by **Løynd**, a Norwegian KI service from the company that
makes Myntos, on servers in Norway. Receipts are not stored there and train no
model.

It is used only when the person asks for it:

- reading a receipt or invoice they upload on «Betaling», to fill in the form;
- suggesting invoice lines from a description on «Faktura».

## They might ask

- «Bruker Myntos KI? Hvor blir kvitteringene mine av?»
- «Fører KI-en noe automatisk?»
- «Sendes regnskapet mitt til OpenAI?»

## What it never does

- It never writes an amount into the books: amounts are read from the
  document's text by Myntos itself.
- It posts nothing. The person sees and confirms every suggestion.
- It sends nothing outside Norway, and nothing to OpenAI, Anthropic or any
  other model provider.

## What is sent with a receipt

Besides the document: the company's registered purpose and activity from
Enhetsregisteret, its industry code and whether it is VAT-registered, and the
description the person typed in the form. Names, dates and currency are only
accepted when they appear in the document itself.

## Good to know

- The page «KI» (`https://myntos.no/ki/`) says the same, in Norwegian.
- An AI assistant the person connects with a key is different: it fetches the
  books itself, and its model decides where they go. See
  https://myntos.no/llms/help/ai/where-the-data-goes.md.

## Related

- [Turn Myntos's KI on or off](https://myntos.no/llms/help/ai/turn-myntos-ki-on-or-off.md)
