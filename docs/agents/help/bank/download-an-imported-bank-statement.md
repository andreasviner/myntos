---
title: Download an imported bank statement
description: How a person finds and downloads, from the Bankavstemming page in Myntos, the original bank statement file they imported earlier, and why it cannot be deleted. Use when someone asks where their uploaded kontoutskrift went, which periods are imported, or wants to remove one.
pages: /bank/
skill: myntos-bank
---

# Download an imported bank statement

Every imported bank statement file is kept in Myntos exactly as it was
uploaded, because it is part of the documentation the books must keep for at
least five years under Norwegian bookkeeping law. It can be downloaded again
at any time, by anyone with access to the books. It cannot be deleted.

## They might ask

- «Hvor finner jeg kontoutskriften jeg lastet opp?»
- «Hvilke perioder har jeg importert fra banken?»
- «Kan jeg slette en kontoutskrift jeg lastet opp?»
- "Where can I find the bank statement I imported in Myntos?"

## Before they start

- Any role in the company.

## Steps

1. Open «Oversikt» in the top menu, then the link «Bankavstemming»
   (`https://myntos.no/bank/`).
2. Scroll to «Importerte kontoutskrifter». Each row is one statement:
   «Konto», «Periode», the opening and closing balance («Inngående»,
   «Utgående»), and «Nye linjer».
3. In the column «Fil», click the file name. The file downloads as it was
   uploaded.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Importerte kontoutskrifter» section | Nothing has been imported yet | [Import a bank statement](https://myntos.no/llms/help/bank/import-a-bank-statement.md) |

## Good to know

- «Nye linjer» shows how many of the statement's movements were new, for
  example «12 av 15»: the other 3 were already imported from an earlier,
  overlapping statement.
- A statement imported by mistake cannot be removed, but it does no harm: it
  can only be for one of the company's own registered accounts (any other is
  refused), and its movements are only listed beside the books. Nothing is
  entered from them until someone presses «Bekreft» or «Før bilaget».
- Comparing the periods in the list shows whether a month is missing.

## Related

- [Import a bank statement](https://myntos.no/llms/help/bank/import-a-bank-statement.md)
- [Understand the bank reconciliation](https://myntos.no/llms/help/bank/understand-the-bank-reconciliation.md)
- An agent with a key can list and download statements through the API:
  [myntos-bank](https://myntos.no/llms/skills/myntos-bank.md)
