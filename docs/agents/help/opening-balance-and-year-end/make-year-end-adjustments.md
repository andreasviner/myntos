---
title: Make the year-end adjustments
description: How a person posts the year-end adjustments (årsoppgjørsføringer) in Myntos as manual vouchers dated 31 December, before closing the year: change in stock or livestock, prepaid and accrued costs, depreciation, moving the driftskreditt. Use when someone asks how to do the year-end entries, «endring varelager», «periodisering» or «avskrivning» in Myntos.
pages: /manuelt-bilag/
skill: myntos-manuelt-bilag
---

# Make the year-end adjustments

Before a year is closed, the accountant adjusts the books for what no payment
shows: how much stock or livestock is left, costs paid this year that belong
to next year (and the other way round), depreciation, and balances on the
wrong account. In Myntos each adjustment is a manual voucher dated 31
December, posted on the «Manuelt bilag» page, except the depreciation of the
assets in the anleggsregister, which is posted from «Anlegg og avskrivning»:
[Post the year's depreciation](https://myntos.no/llms/help/opening-balance-and-year-end/post-the-years-depreciation.md). **Do them before «Årsavslutning»**:
a closed year takes no new vouchers. Myntos does not calculate these figures;
the person or their accountant decides them.

## They might ask

- «Hvordan fører jeg endring i varelager ved årsslutt?»
- «Hvor periodiserer jeg forsikringen som gjelder neste år?»
- «Hvordan fører jeg avskrivning i Myntos?»
- «Regnskapsføreren har sendt meg årsoppgjørsposteringene, hvor legger jeg dem inn?»
- "How do I do the year-end adjustments in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- The year must still be open. If it is already closed, see
  [Reopen a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/reopen-a-financial-year.md).
- The figures, from a stock count, a depreciation schedule, a loan statement
  or the accountant. The account numbers below are the standard ones; the
  company's kontoplan may differ.

## Steps

1. Open `https://myntos.no/manuelt-bilag/` and choose the year with ‹ ›. For
   a past year, «Dato» is filled in as 31 December.
2. Post one manual voucher per adjustment, as in
   [Post a manual voucher](https://myntos.no/llms/help/opening-balance-and-year-end/post-a-manual-voucher.md),
   with a «Begrunnelse» that says how the figure was arrived at. The usual
   lines:

   | Adjustment | «Debet» | «Kredit» |
   | --- | --- | --- |
   | Stock went up | 1400 Varelager | 4090 Beholdningsendring |
   | Stock went down | 4090 | 1400 |
   | Cost paid this year that belongs to next year | 1700 | the cost account |
   | Cost of this year, invoiced next year | the cost account | 2900 |
   | Depreciation of an asset that is not in «Anlegg og avskrivning» | 6000–6020 | the asset's account (1200 and on) |
   | Driftskreditt shown on 2390, moved to the bank account | 2390 | 1920, or the other way round by the balance's sign |

3. Attach the stock count list, the schedule or the statement under
   «Dokumenter» before pressing «Kontroller», or afterwards with «Legg ved
   dokumenter» on the voucher in the list. A stock count must show what was
   counted, by whom and when.
4. When every adjustment is posted, close the year on «Årsavslutning»:
   [Close a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/close-a-financial-year.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Regnskapsåret … er avsluttet og tar ikke imot nye bilag. Det gjenåpnes under Årsavslutning.» | The year was closed before the adjustments | An «Administrator» or «Eier» reopens it, if the annual accounts are not yet adopted |
| A red message about 8300, 8800, 8960 or 8990 | The tax cost and the year's result are posted by closing the year | Leave them out; the tax is entered on «Årsavslutning» |
| A red message that an account is not in the kontoplan | The company has not got 1400, 4090 or the like yet | Add it: [Add an account](https://myntos.no/llms/help/chart-of-accounts/add-an-account.md) |

## Good to know

- Next year, the stock and the prepaid costs are reversed or adjusted again
  with new figures; Myntos does not do that automatically.
- A wrong adjustment is corrected, not edited:
  [Correct a manual voucher](https://myntos.no/llms/help/opening-balance-and-year-end/correct-a-manual-voucher.md).

## Related

- [Post a manual voucher](https://myntos.no/llms/help/opening-balance-and-year-end/post-a-manual-voucher.md)
- [Close a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/close-a-financial-year.md)
- An agent with a key can do this through the API:
  [myntos-manuelt-bilag](https://myntos.no/llms/skills/myntos-manuelt-bilag.md)
