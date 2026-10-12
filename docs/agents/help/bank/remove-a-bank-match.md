---
title: Remove a bank match
description: How a person takes away the link between a bank line and a voucher on the Bankavstemming page in Myntos, with the button Fjern avstemmingen, and what that does and does not undo. Use when someone matched a bank transaction to the wrong voucher, or a matched voucher has been reversed.
pages: /bank/
skill: myntos-bank
---

# Remove a bank match

A match between a bank line and a voucher is Myntos's note that the two are
the same payment. If it is wrong, it can be removed, and the line goes back
to «Ikke avstemt». The voucher itself stays exactly as it was: removing a
match never deletes or reverses anything in the books.

## They might ask

- «Jeg avstemte mot feil bilag, hvordan angrer jeg?»
- «Hvordan fjerner jeg en avstemming i Myntos?»
- "How do I undo a bank match in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».

## Steps

1. Open «Oversikt» in the top menu, then the link «Bankavstemming»
   (`https://myntos.no/bank/`).
2. Under «Bevegelser på kontoen», set «Vis» to «Avstemt».
3. Find the line. It says «Avstemt mot bilag …» or «Ført fra denne linjen som
   bilag …».
4. Press «Fjern avstemmingen». The line moves back to «Ikke avstemt», with
   suggestions again.
5. Match it to the right voucher, or enter one:
   [Match a bank line to a voucher](https://myntos.no/llms/help/bank/match-a-bank-line-to-a-voucher.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Fjern avstemmingen» button | Their role is «Leser» | Ask a «Medlem» or more |
| «Vi fikk ikke fjernet avstemmingen.» | The request failed | Reload the page and try again |

## Good to know

- If the line says «Ført fra denne linjen som bilag …», the voucher was
  entered from the bank line. Removing the match does not remove that
  voucher; it stays in the books and will show up as a suggestion for the
  same line. If the entry itself was wrong, it has to be reversed:
  [Undo a voucher posted from a bank line](https://myntos.no/llms/help/bank/undo-a-voucher-posted-from-a-bank-line.md).
- «Bilaget er reversert, så bevegelsen er ikke forklart lenger.» on a
  reconciled line means the voucher has been reversed since. Remove the match,
  then match the line to the corrected voucher.

## Related

- [Understand the bank reconciliation](https://myntos.no/llms/help/bank/understand-the-bank-reconciliation.md)
- [Match a bank line to a voucher](https://myntos.no/llms/help/bank/match-a-bank-line-to-a-voucher.md)
- An agent with a key can do this through the API:
  [myntos-bank](https://myntos.no/llms/skills/myntos-bank.md)
