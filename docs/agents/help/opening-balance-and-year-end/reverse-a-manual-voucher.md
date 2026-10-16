---
title: Reverse a manual voucher
description: How a person reverses a posted manual voucher (manuelt bilag) in Myntos with «Reverser» and a written reason, leaving both the voucher and its mirror image in the books. Use when someone posted a manual voucher twice, on the wrong company, or one that should never have been posted, or asks how to delete one.
pages: /manuelt-bilag/
skill: myntos-manuelt-bilag
---

# Reverse a manual voucher

A manual voucher that should never have been posted (entered twice, meant for
another company) is reversed: Myntos posts its mirror image in the same
period, so the two cancel out. Nothing is deleted; both stay in the books,
linked. A reversal cannot be undone. If only the figures or the account are
wrong, correct it instead, which leaves the right figures in the same step.

## They might ask

- «Jeg førte det samme bilaget to ganger, hvordan sletter jeg det ene?»
- «Hvordan reverserer jeg et manuelt bilag?»
- «Kan jeg slette et manuelt bilag?»
- "How do I delete a journal entry in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- The voucher's year must be open. If it is closed, post a correcting voucher
  in the open year, or have the year reopened:
  [Reopen a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/reopen-a-financial-year.md).

## Steps

1. Open `https://myntos.no/manuelt-bilag/` and choose the voucher's year with
   ‹ ›.
2. Under «Førte manuelle bilag i …», find the voucher and press «Reverser».
3. Write in «Begrunnelse for reverseringen» why it is reversed, at least ten
   characters: «Ført to ganger ved en feil.»
4. Press «Reverser bilaget». The page says «Bilag … er reversert med bilag
   …». In the list, the voucher reads «Reversert med bilag …» and the
   reversal «Reverserer bilag …».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Reverser» button on the voucher | It is already reversed or corrected, it is itself a reversal, the year is closed, or their role is «Leser» | A reversal is not reversed again: post what should stand as a new voucher |
| A red message about the reason | No reason, or under ten characters | Write why it is reversed |
| A message that the year is closed | The voucher's year was closed after it was posted | Post a correcting voucher in the open year, or reopen the year |

## Good to know

- Manual vouchers are reversed here and only here; the reversal in the
  payments register refuses them.
- The reason is kept with the reversal as its documentation and cannot be
  changed afterwards.

## Related

- [Correct a manual voucher](https://myntos.no/llms/help/opening-balance-and-year-end/correct-a-manual-voucher.md)
- [Post a manual voucher](https://myntos.no/llms/help/opening-balance-and-year-end/post-a-manual-voucher.md)
- An agent with a key can do this through the API:
  [myntos-manuelt-bilag](https://myntos.no/llms/skills/myntos-manuelt-bilag.md)
