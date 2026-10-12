---
title: Decide whether you need an opening balance
description: Explains when a company in Myntos needs an opening balance (inngående balanse) and when it does not, and what happens if one is missing. Use when someone asks whether they have to enter an opening balance, has just moved their books from another program, or sees a note about a missing opening balance when closing the year.
pages: /inngaende-balanse/
skill: myntos-inngaende-balanse
---

# Decide whether you need an opening balance

The opening balance (inngående balanse) is what the company owned and owed on
the first day it kept its books in Myntos: money in the bank, customers who
owed it money, suppliers it owed, share capital and the equity earlier years
built up. A company that kept its books somewhere else before Myntos needs
one, or its bank balance, its customers and its equity will all start at zero
and be wrong from the first day. It is entered once, for the company's first
year in Myntos only.

## They might ask

- «Må jeg føre inngående balanse i Myntos?»
- «Vi har byttet fra Fiken, hva gjør jeg med tallene fra i fjor?»
- «Selskapet ble startet i år, trenger vi inngående balanse?»
- "Do I need an opening balance in Myntos?"

## Before they start

Nothing. This article only helps them decide.

## Steps

1. Ask whether the company kept accounts anywhere before its first year in
   Myntos (another program, an accountant, a spreadsheet).
   - **Yes**: it needs an opening balance, taken from the old books as they
     stood at the end of the last year kept there. Go on with
     [Import a saldobalanse](https://myntos.no/llms/help/opening-balance-and-year-end/import-a-saldobalanse.md)
     (easiest) or
     [Enter an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/enter-an-opening-balance.md).
   - **No**, the company started its life in Myntos: it does not need one.
     Everything that happens, the share capital paid in included, is entered
     as it happens.
2. To see whether one has already been entered, open
   `https://myntos.no/inngaende-balanse/` (from «Oversikt», the link
   «Inngående balanse» in the note under «Største poster i år»). If a card
   called «Ført inngående balanse» is there, it has been.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| On «Årsavslutning»: «Det er ikke ført noen inngående balanse for …» | The first year is being closed without an opening balance | If the company existed before Myntos, enter the opening balance before closing the year |
| «Regnskapsåret … er avsluttet, og inngående balanse kan bare føres i et åpent år. …» | The first year is already closed | An «Administrator» or «Eier» reopens it first: [Reopen a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/reopen-a-financial-year.md) |

## Good to know

- Only the company's **first** year in Myntos takes an opening balance. Every
  later year starts from the year before's closing balance automatically.
- It does not have to be entered before anything else. Receipts and invoices
  can be entered from January, and the opening balance added in March when the
  old accountant's final figures arrive, as long as the year is still open.
- Keep the previous year's approved annual accounts (årsregnskap) and
  saldobalanse: they are the documentation for the opening balance.

## Related

- [Import a saldobalanse](https://myntos.no/llms/help/opening-balance-and-year-end/import-a-saldobalanse.md)
- [Enter an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/enter-an-opening-balance.md)
- An agent with a key can do this through the API:
  [myntos-inngaende-balanse](https://myntos.no/llms/skills/myntos-inngaende-balanse.md)
