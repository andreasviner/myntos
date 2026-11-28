---
title: What Myntos's own KI does
description: What the built-in KI in Myntos does (reads uploaded receipts, fills in invoices and suggests invoice lines, reads the vouchers to split the costs on «Analyse», run by Løynd inside the EEA), its three levels «Gratis», «Medium» and «Smart», what is sent with a receipt, and what it never does. Use when someone asks whether Myntos uses AI, where their receipts go, whether the AI costs anything, or whether the AI posts anything by itself.
pages: /ki/about/
skill: myntos-ki
---

# What Myntos's own KI does

Myntos has its own KI, separate from any AI assistant a person connects with
a key. It is run by **Løynd**, a Norwegian KI service that Myntos uses, inside
the EEA. Nothing is stored there, and nothing trains a model.

It is used when the person asks for it:

- answering questions about the company's books in **KI** chat
  (`https://myntos.no/ki/`);
- reading a receipt or invoice they upload on «Betaling», to fill in the form;
- filling in an invoice from the customer's e-mail, and suggesting invoice
  lines from a description, on «Faktura»;
- turning a screenshot or photo into text in a KI box, with «Legg ved bilde»;
- and, on «Analyse», reading the company's vouchers in the background to say
  what each cost and income was for, and writing a «Dyp KI-analyse» report when
  someone presses the button (see «What is sent for «Analyse»» below).

## Three levels

Under every KI box, «KI-nivå» offers three levels:

- «Gratis»: Løynd's own model, on Løynd's machines in Norway. Every company has
  it, at no cost and with no limit.
- «Medium» and «Smart»: larger models, for a company with a KI plan
  ([Buy a KI plan](https://myntos.no/llms/help/ai/buy-a-ki-plan.md)). They run
  on Løynd's own machines in Norway when they are free, and otherwise on extra
  KI capacity in the EU that Løynd uses.

The same things are sent at every level
([Choose the KI level](https://myntos.no/llms/help/ai/choose-a-ki-level.md)).

## They might ask

- «Bruker Myntos KI? Hvor blir kvitteringene mine av?»
- «Fører KI-en noe automatisk?»
- «Sendes regnskapet mitt til OpenAI?»
- «Koster KI-en noe?»
- «Kjører KI-en i Norge?»
- «Leser KI-en hele regnskapet mitt?»

## What it never does

- It never writes an amount into the books: amounts are read from the
  document's text by Myntos itself.
- It never makes a figure on «Analyse». It only says which part a line
  belongs in; every amount there is the books'.
- It posts nothing. The person sees and confirms every suggestion.
- It sends nothing outside the EEA, and nothing to OpenAI, Anthropic or any
  other model provider outside it.
- It never searches the web with what it is given.

## What is sent with a receipt

Besides the document: the company's registered purpose and activity from
Enhetsregisteret, its industry code and whether it is VAT-registered, and the
description the person typed in the form. Names, dates and currency are only
accepted when they appear in the document itself.

Pictures, the bilag's included, are always read on Løynd's own machines in
Norway, whatever the level. Only the text read from them goes on to «Medium»
or «Smart».

## What is sent for «Analyse»

With KI on, opening «Analyse» has Myntos read the period's vouchers in the
background: their dates, amounts, accounts, descriptions and counterparties.
This reading is always «Gratis», on Løynd's own machines in Norway, and costs
nothing of the company's KI plan. Myntos keeps a short summary of each
voucher in its own database, so it does not ask about the same voucher again.

When someone with write access presses «Dyp KI-analyse» on «Analyse», Myntos
sends a compiled summary of the period's figures, splits, top counterparties,
recurring costs and summaries of the largest vouchers (never PDF or image
files) at the KI level chosen («Gratis», «Medium» or «Smart»). Only the
analysis step itself counts against a KI plan. The finished report is stored in
Myntos's database in the EEA so asking again for the same unchanged period and
level uses no quota. Both summaries and reports are deleted with the company.
Switching KI off stops the reading and any running analysis, prevents starting
a new one, and nothing stored before is used while KI is off.

## Good to know

- The «Om KI og KI-plan» page (`https://myntos.no/ki/about/`) says the same in
  Norwegian, and is where the owner manages a monthly KI plan and downloads KI
  invoices. It is reached from «Om KI og KI-plan» in the company menu (the
  company's name at the top right), from «KI i Myntos» in the «Funksjoner» menu
  before signing in, from «KI og KI-planer» in the footer of every page, and
  from the link under any KI level picker. The on/off switch for KI sits in
  company settings (`https://myntos.no/organisasjon/#ki`).
- An AI assistant the person connects with a key is different: it fetches the
  books itself, and its model decides where they go. See
  https://myntos.no/llms/help/ai/where-the-data-goes.md.

## Related

- [Turn Myntos's KI on or off](https://myntos.no/llms/help/ai/turn-myntos-ki-on-or-off.md)
- [Choose the KI level](https://myntos.no/llms/help/ai/choose-a-ki-level.md)
- [Buy a KI plan](https://myntos.no/llms/help/ai/buy-a-ki-plan.md)
