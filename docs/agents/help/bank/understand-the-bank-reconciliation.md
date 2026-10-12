---
title: Understand the bank reconciliation
description: What the Bankavstemming page in Myntos shows - the movements on the account, which are reconciled (avstemt) and which are not, the suggested vouchers marked Trolig and Mulig, and the imported statements. Use when someone asks whether their books agree with the bank, what is missing, or what a line on the bank page means.
pages: /bank/
skill: myntos-bank
---

# Understand the bank reconciliation

Bank reconciliation (bankavstemming) means checking every movement on the
bank account against the books. On the page «Bankavstemming» each movement
from an imported statement is either reconciled («avstemt»), meaning a voucher
(bilag) in the books explains it, or not («ikke avstemt»). A movement that is
not reconciled is usually a payment nobody has entered yet, or one entered
with another amount. Reconciling only links the bank's line to the voucher;
it never changes what has been entered.

## They might ask

- «Stemmer regnskapet med banken?»
- «Hva mangler i regnskapet?»
- «Hva betyr «Trolig» og «Mulig» på banksiden?»
- "How do I see which payments are missing from my books?"

## Before they start

- At least one imported statement:
  [Import a bank statement](https://myntos.no/llms/help/bank/import-a-bank-statement.md).
- Any role can look. Only «Medlem», «Administrator» and «Eier» see the
  buttons that change anything.

## Steps

1. Open «Oversikt» in the top menu, and under the year's figures follow the
   link «Bankavstemming» (`https://myntos.no/bank/`).
2. Under «Bevegelser på kontoen», «Vis» chooses what is listed: «Ikke
   avstemt» (the default: what still needs attention), «Avstemt» or «Alle».
   The newest movements come first.
3. Each line shows who paid or was paid (or the bank's text, or «Uten tekst
   fra banken»), the date the bank booked it, the payment message and any KID
   (a Norwegian payment reference), and the amount, with + for money in and
   − for money out.
4. Under a line that is not reconciled, Myntos lists up to three vouchers
   that could be its entry:
   - «Trolig: bilag 2026-0004, …» is a likely match, «Mulig: …» a possible
     one. Below each, the reasons: «Samme beløp og retning», «Samme dato»,
     «Navnet i banken stemmer med bilaget», «Teksten i banken går igjen i
     bilaget», «KID-en står i beskrivelsen».
   - «Ingen ført betaling har samme beløp i nærheten av datoen.» means
     nothing in the books fits.
   What to do next:
   [Match a bank line to a voucher](https://myntos.no/llms/help/bank/match-a-bank-line-to-a-voucher.md)
   or
   [Post a voucher for a bank line](https://myntos.no/llms/help/bank/post-a-voucher-for-a-bank-line.md).
5. A reconciled line says «Avstemt mot bilag 2026-0004» (someone confirmed an
   existing voucher) or «Ført fra denne linjen som bilag 2026-0004» (the
   voucher was entered from the bank line).
6. «Importerte kontoutskrifter» at the bottom lists each imported statement:
   «Konto», «Periode», the bank's opening and closing balance («Inngående»,
   «Utgående»), «Nye linjer» («12 av 15» when 3 were already imported), and
   «Fil», the original file.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Ingen bevegelser å vise. Importer en kontoutskrift først.» | Nothing imported yet, or nothing under this filter | Import a statement, or choose «Alle» under «Vis» |
| «Alt som er importert, er avstemt.» | Every imported movement is explained | Nothing to do until the next statement |
| «Bilaget er reversert, så bevegelsen er ikke forklart lenger.» | The voucher it was matched to has been reversed since | Remove the match and match or enter the right one: [Remove a bank match](https://myntos.no/llms/help/bank/remove-a-bank-match.md) |
| «KID-en hører til faktura … til …. Innbetaling på faktura føres ikke herfra ennå.» | A customer paid an invoice the company issued | [Handle an invoice payment in the bank](https://myntos.no/llms/help/bank/handle-an-invoice-payment-in-the-bank.md) |
| No buttons on the lines, no import box | Their role is «Leser» | Ask a «Medlem» or more to do it |

## Good to know

- A suggestion always has exactly the same amount and direction, was paid
  through the bank account, and is dated from 14 days before to 7 days after
  the bank's date. «Trolig» is still a guess: two equal payments close
  together look the same, so the person decides.
- Matching is one bank line to one voucher. A single transfer that paid
  several invoices cannot be matched yet.
- The page does not yet compare the statement's closing balance with the
  books' bank balance.

## Related

- [Import a bank statement](https://myntos.no/llms/help/bank/import-a-bank-statement.md)
- [Match a bank line to a voucher](https://myntos.no/llms/help/bank/match-a-bank-line-to-a-voucher.md)
- [Post a voucher for a bank line](https://myntos.no/llms/help/bank/post-a-voucher-for-a-bank-line.md)
- An agent with a key can read and reconcile through the API:
  [myntos-bank](https://myntos.no/llms/skills/myntos-bank.md)
