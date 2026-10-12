---
title: Match a bank line to a voucher
description: How a person confirms on the Bankavstemming page in Myntos that a movement on the bank account is the payment already entered as a voucher (bilag), using the Bekreft button next to a suggestion. Use when someone asks how to reconcile (avstemme) a bank transaction against an entry in Myntos.
pages: /bank/
skill: myntos-bank
---

# Match a bank line to a voucher

When a payment has already been entered (for example under «Betaling», with
the receipt), and the bank's statement shows the same movement, the two are
linked: the bank line is then reconciled («avstemt»). Myntos suggests which
voucher fits; the person confirms. Confirming only links them. The voucher is
not changed, and the link can be removed again.

## They might ask

- «Hvordan avstemmer jeg en banklinje i Myntos?»
- «Betalingen er ført, hvordan kobler jeg den til banken?»
- «Hva gjør «Bekreft» på banksiden?»
- "How do I match a bank transaction to an entry in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- The payment entered as a voucher, through the bank account (not as cash),
  with the same amount as the bank line.

## Steps

1. Open «Oversikt» in the top menu, then the link «Bankavstemming»
   (`https://myntos.no/bank/`).
2. Under «Bevegelser på kontoen», keep «Vis» on «Ikke avstemt».
3. Find the line. Under it, read the suggestions: «Trolig: bilag …» or
   «Mulig: bilag …», with the voucher's number, date and description, and the
   reasons it fits.
4. Check that it is the same payment, especially when there are two equal
   amounts close together.
5. Press «Bekreft» next to the right voucher. The line leaves the «Ikke
   avstemt» list; under «Avstemt» it says «Avstemt mot bilag …».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Ingen ført betaling har samme beløp i nærheten av datoen.» | Nothing entered has this exact amount within 14 days before to 7 days after the bank's date | If it was never entered: [Post a voucher for a bank line](https://myntos.no/llms/help/bank/post-a-voucher-for-a-bank-line.md). If it was entered with a wrong amount, correct it with «Rediger» under «Betaling», then come back |
| The right voucher is not among the suggestions | It was entered as cash, with another amount, far from the bank's date, or it is already matched to another line | Check the voucher under «Betaling». A payment registered as «Kontant» instead of «Bankkonto» is never suggested |
| «bilag … er allerede avstemt mot en annen banklinje» | That voucher already explains another movement | Look under «Avstemt» for the other line; remove that match if it was wrong |
| «banklinjen er allerede avstemt» | Someone matched the line meanwhile | Reload the page |
| «bilaget er reversert, …» | The voucher has been reversed and no longer explains anything | Choose the replacement voucher instead |
| No «Bekreft» button | Their role is «Leser» | Ask a «Medlem» or more |

## Good to know

- Matching is one line to one voucher: one voucher can explain only one
  movement.
- A wrong match is fixed by removing it:
  [Remove a bank match](https://myntos.no/llms/help/bank/remove-a-bank-match.md).

## Related

- [Understand the bank reconciliation](https://myntos.no/llms/help/bank/understand-the-bank-reconciliation.md)
- [Post a voucher for a bank line](https://myntos.no/llms/help/bank/post-a-voucher-for-a-bank-line.md)
- An agent with a key can do this through the API:
  [myntos-bank](https://myntos.no/llms/skills/myntos-bank.md)
