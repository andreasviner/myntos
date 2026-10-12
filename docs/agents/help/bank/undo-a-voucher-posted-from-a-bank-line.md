---
title: Undo a voucher posted from a bank line
description: How a person corrects a voucher they posted from a bank line with Før som nytt bilag in Myntos, when the category, VAT or text was wrong - by correcting it under Betaling and moving the bank match - since a posted voucher cannot be deleted. Use when someone says they booked a bank transaction wrong from the Bankavstemming page.
pages: /bank/
skill: myntos-bank
---

# Undo a voucher posted from a bank line

A voucher posted from a bank line is an ordinary posted entry, and Norwegian
bookkeeping law (bokføringsloven) makes it permanent: it is never edited or
deleted. A mistake is corrected with a reversal (motpostering), which leaves
the original and its reversal in the books side by side. «Fjern
avstemmingen» on the bank page does not undo it; that only removes the link.

## They might ask

- «Jeg førte en banklinje med feil type, hvordan retter jeg det?»
- «Kan jeg slette bilaget jeg førte fra banksiden?»
- "I booked a bank transaction wrong in Myntos, how do I fix it?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- The voucher number, shown on the bank line as «Ført fra denne linjen som
  bilag …».

## Steps

1. Open «Betaling» in the top menu (`https://myntos.no/kontantstrom/`) and
   find the voucher number in the list.
2. If the money really moved and only the details were wrong (category, VAT
   code, text), press «Rediger» on that row, correct the form, and press
   «Lagre endringer». Myntos reverses the original and posts the corrected
   entry in its place.
3. Only if the payment should not be in the books at all, press «Reverser»
   instead and confirm. A movement that is on the bank statement nearly always
   belongs in the books, so this is rarely right for a bank line.
4. Go back to «Bankavstemming» (`https://myntos.no/bank/`), set «Vis» to
   «Avstemt», and find the line. It now says «Bilaget er reversert, så
   bevegelsen er ikke forklart lenger.»
5. Press «Fjern avstemmingen». The line goes back to «Ikke avstemt».
6. Press «Bekreft» next to the corrected voucher in the suggestions:
   [Match a bank line to a voucher](https://myntos.no/llms/help/bank/match-a-bank-line-to-a-voucher.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Rediger» or «Reverser» on the row | Their role is «Leser», or the voucher has already been reversed | Ask a «Medlem» or more; look for the corrected entry |
| «regnskapsåret … er avsluttet» | The voucher is in a closed year | A closed year cannot be changed; ask whoever closed it |

## Good to know

- All three vouchers (the original, its reversal and the corrected one) stay
  in the books. «Betaling» shows them as one line, the one that applies now.
- If the line was posted against the wrong bank line rather than with wrong
  details, remove that match first, then reconcile both lines properly.

## Related

- [Remove a bank match](https://myntos.no/llms/help/bank/remove-a-bank-match.md)
- [Post a voucher for a bank line](https://myntos.no/llms/help/bank/post-a-voucher-for-a-bank-line.md)
- An agent with a key can do this through the API:
  [myntos-bank](https://myntos.no/llms/skills/myntos-bank.md) and
  [myntos-kontantstrom](https://myntos.no/llms/skills/myntos-kontantstrom.md)
