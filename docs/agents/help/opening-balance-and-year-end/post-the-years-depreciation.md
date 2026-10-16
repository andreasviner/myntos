---
title: Post the year's depreciation
description: How a person checks and posts the year's depreciation (avskrivninger) from the anleggsregister on the Anlegg og avskrivning page in Myntos, choosing less than the maximum if they want, before closing the year. Use when someone asks how to do avskrivning, saldoavskrivning or årets avskrivninger in Myntos.
pages: /anlegg/
skill: myntos-anlegg
---

# Post the year's depreciation

Each year, part of each saldo is deducted as depreciation, at most the
group's rate (20 % for machines and tractors, 4 % for buildings, 6 % for a
livestock building). Myntos works the saldi out from the register and posts
one voucher dated 31 December. Do it after the year is over and before it is
closed: a closed year takes no new vouchers.

## They might ask

- «Hvordan fører jeg avskrivninger i Myntos?»
- «Kan jeg avskrive mindre enn 20 % i år?»
- «Hvor ser jeg saldoen på saldogruppe d?»
- "How do I post depreciation in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier» to post; a «Leser» sees the
  figures only.
- The year's purchases and sales registered:
  [Register a fixed asset](https://myntos.no/llms/help/opening-balance-and-year-end/register-a-fixed-asset.md).

## Steps

1. Open «Anlegg og avskrivning» (`https://myntos.no/anlegg/`) and choose the
   year with ‹ ›. The card is called «Avskrivninger» and the year.
2. Read the table: per saldo «Inngående», «Tilgang», «Avgang», «Grunnlag»,
   «Årets avskrivning» and «Utgående». Myntos fills in the highest amount the
   law allows.
3. To take less, write another amount in «Årets avskrivning» and press
   «Kontroller». For a negative saldo, the amount is what is taken to income,
   at least the minimum.
4. Read the red messages (they stop the button) and the grey ones (worth
   checking, for example an account whose balance differs from the register,
   or a purchase on an asset account that is not registered).
5. Check the lines under «Dette blir ført per 31. desember», then press «Før
   avskrivningene for» and the year. The voucher cannot be edited afterwards,
   only corrected or reversed.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Regnskapsåret … varer til …» | The year is not over | Wait until the new year |
| «Regnskapsåret … er avsluttet …» | The year is closed | Reopen it under «Årsavslutning» first |
| «Myntos har satsene og beløpsgrensene fra inntektsåret 2024 …» | Earlier years are not computed | Those years belong to the old program |
| «… ble levert før …, og har ingen skattemessig verdi …» | An asset from before the register lacks its value | Press «Endre» on it and fill in the value |
| «… høyst … kan avskrives …» | The amount is above the maximum | Lower it |

## Good to know

- The gevinst- og tapskonto under the table is a tax figure only; it is not
  posted.
- The year-end review on «Årsavslutning» reminds the person if the
  depreciation is not posted.

## Related

- [Correct or reverse the year's depreciation](https://myntos.no/llms/help/opening-balance-and-year-end/correct-or-reverse-the-depreciation.md)
- [Close a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/close-a-financial-year.md)
- An agent with a key can do this through the API:
  [myntos-anlegg](https://myntos.no/llms/skills/myntos-anlegg.md)
