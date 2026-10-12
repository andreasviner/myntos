---
title: Enter an opening balance
description: How a person types in the company's opening balance (inngående balanse) line by line on the Inngående balanse page in Myntos, checks it and posts it. Use when someone asks how to enter the balances they brought from another accounting program or accountant, such as the bank balance, receivables, debts and equity at the start of the year.
pages: /inngaende-balanse/
skill: myntos-inngaende-balanse
---

# Enter an opening balance

The opening balance (inngående balanse) is the company's balance sheet on the
first day of its first year in Myntos, taken from the books it kept before.
Myntos posts it as one voucher dated 1 January. **Once posted it cannot be
edited or deleted**, because a posted voucher is permanent under Norwegian
bookkeeping law; a mistake is fixed by correcting it, which reverses the old
one and posts a new one. If the old program can export a saldobalanse file,
[importing it](https://myntos.no/llms/help/opening-balance-and-year-end/import-a-saldobalanse.md)
is easier than typing.

## They might ask

- «Hvordan fører jeg inngående balanse i Myntos?»
- «Hvor legger jeg inn saldoen på bankkontoen fra i fjor?»
- «Hvordan registrerer jeg aksjekapitalen og egenkapitalen ved oppstart i Myntos?»
- "How do I enter opening balances in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier». A «Leser» sees the page but no
  form.
- The balances at the end of the last year kept elsewhere, per account,
  preferably from the saldobalanse the previous accountant made **after** the
  year-end («saldobalansen etter årsoppgjøret»), so the year's result is
  already on equity.
- Every account they will use must exist in the company's chart of accounts
  («Kontoplan»).
- The company's first year in Myntos must still be open.

## Steps

1. Open «Oversikt» in the top menu (`https://myntos.no/`) and, in the note
   under «Største poster i år», click «Inngående balanse»
   (`https://myntos.no/inngaende-balanse/`).
2. In the card «Saldoer per 1. januar …», fill in one row per account:
   - «Konto»: the four-digit account number, such as `1920`.
   - «Tekst»: optional, for example «Bankinnskudd».
   - «Kunde/leverandør»: leave at «Ingen» unless they are splitting customers
     or suppliers per invoice:
     [Split customers and suppliers in the opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/split-customers-and-suppliers-in-the-opening-balance.md).
   - «Debet» or «Kredit»: the amount, on one side only. Assets (bank,
     receivables) are debit; debt, share capital and equity are credit.
     `152 340,00` is fine.
3. Press «Legg til linje» for more rows, and «Fjern» on a row to drop it.
4. Press «Kontroller». Nothing is posted yet: Myntos checks the lines and
   shows «Gjennomgå før føring».
5. Read the review: every line, the «Sum» of debit and credit (which must be
   equal), and any messages. Red messages must be fixed first; press
   «Tilbake til skjemaet» to do so. See
   [Review an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/review-an-opening-balance.md).
6. When it is right, press «Før inngående balanse». This cannot be undone.
   The page says «Inngående balanse er ført som bilag …» and the balance now
   shows under «Ført inngående balanse».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No form and no buttons, only the text at the top | Their role is «Leser» | Ask someone with «Medlem» or more |
| The card «Ført inngående balanse» instead of the form | A balance is already posted | Correct it: [Correct an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/correct-an-opening-balance.md) |
| «Beløpet på linje … kan ikke leses.» | An amount is not a number | Type digits with a comma for øre |
| «Før inngående balanse» cannot be pressed | The review found errors (in red) | Fix them via «Tilbake til skjemaet», then «Kontroller» again |
| «Regnskapsåret … er avsluttet, og inngående balanse kan bare føres i et åpent år. …» | The first year is closed | An «Administrator» or «Eier» reopens it first |
| «Selskapet har ikke noe regnskapsår i Myntos.» | The company has no books in Myntos yet; a year opens with the first entry | Enter the first payment or invoice, then come back |
| «selskapet har allerede en inngående balanse. …» | One was posted meanwhile | Use «Rett inngående balanse» |

## Good to know

- It is posted as one voucher in its own journal, dated the first day of the
  company's first year in Myntos.
- VAT that was not settled at the turn of the year goes on 2740, not 2700 or
  2710.
- The file or figures they typed from are not stored; keep the previous year's
  approved annual accounts and saldobalanse as the documentation.
- If they would rather have their own AI do it, they can invite it with
  «Inviter en KI» under «Selskapet» → «Tilgang»; never paste a password into a
  chat.

## Related

- [Import a saldobalanse](https://myntos.no/llms/help/opening-balance-and-year-end/import-a-saldobalanse.md)
- [Correct an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/correct-an-opening-balance.md)
- [Decide whether you need an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/decide-whether-you-need-an-opening-balance.md)
- An agent with a key can do this through the API:
  [myntos-inngaende-balanse](https://myntos.no/llms/skills/myntos-inngaende-balanse.md)
