---
title: Enter the opening saldi from the previous program
description: How a person enters the tax saldi of each saldogruppe and the gevinst- og tapskonto, brought from the previous accounting program, under Inngående saldo on the Anlegg og avskrivning page in Myntos. Use when someone moving to Myntos asks where the saldoene for avskrivning go, or how to bring the anleggsregister over.
pages: /anlegg/
skill: myntos-anlegg
---

# Enter the opening saldi from the previous program

A company that depreciated its assets in another program carries a saldo for
each group. Those saldi are tax figures, and they do not travel with the
balance sheet. They are entered once, for the first year the register covers.

## They might ask

- «Hvor legger jeg inn saldoene fra Tripletex?»
- «Hvordan får jeg med meg avskrivningsgrunnlaget fra fjoråret?»
- "Where do I enter the depreciation balances from my old system?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- Last year's saldo specification from the skattemelding
  (næringsspesifikasjonen) or the accountant: the column «utgående saldo». Not
  the balance on the account, which can differ.

## Steps

1. Open «Anlegg og avskrivning» (`https://myntos.no/anlegg/`) and go to the
   card «Inngående saldo».
2. In «Gjelder fra år», the first year in Myntos (from 2024).
3. Press «Legg til linje» for each saldo: choose «Saldogruppe a» to «d» or
   «j», fill in the account the group stands on, and the amount (negative for
   a negative saldo). A gevinst- og tapskonto is one line «Gevinst- og
   tapskonto», without an account.
4. Press «Lagre inngående saldo».
5. Buildings and other assets in group e to i are registered one by one, each
   with «Skattemessig verdi ved registerets start»:
   [Register a fixed asset](https://myntos.no/llms/help/opening-balance-and-year-end/register-a-fixed-asset.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «inngående saldo føres for saldogruppe a, b, c, d og j …» | Group e to i has a saldo per asset | Register the asset with its own value |
| «oppgi kontoen saldoen står på» | A group line without an account | Fill in the account |
| «selskapet har ikke noe regnskapsår … i Myntos» | The year is not in Myntos | Choose the company's first year |
| «avskrivningene for … er ført fra anleggsregisteret …» | Depreciation is already posted for an earlier year | Reverse it first, if the register really starts later |

## Good to know

- Saving replaces all the lines; removing every line and saving removes them.
- The balance sheet itself comes in through «Inngående balanse»:
  [Enter an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/enter-an-opening-balance.md).

## Related

- [Post the year's depreciation](https://myntos.no/llms/help/opening-balance-and-year-end/post-the-years-depreciation.md)
- An agent with a key can do this through the API:
  [myntos-anlegg](https://myntos.no/llms/skills/myntos-anlegg.md)
