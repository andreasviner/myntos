---
title: Correct a manual voucher
description: How a person corrects a posted manual voucher (manuelt bilag) in Myntos with «Rett», which reverses the old voucher and posts the corrected one in one step, linked. Use when someone says a manual voucher has the wrong amount, account or date, or asks how to change or edit one.
pages: /manuelt-bilag/
skill: myntos-manuelt-bilag
---

# Correct a manual voucher

A posted manual voucher cannot be edited or deleted: Norwegian bookkeeping law
keeps every posted voucher as it was. «Rett» opens it in the form; when the
corrected version is posted, Myntos reverses the old voucher in its own period
and posts the new one, linked to each other. **All three stay in the books**,
and the new one cannot be edited either, so check it before posting.

## They might ask

- «Jeg førte feil beløp på et manuelt bilag, hvordan retter jeg det?»
- «Kan jeg endre et manuelt bilag?»
- «Bilaget skulle vært på en annen konto»
- "How do I edit a journal entry in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- The old voucher's year must be open. If it is closed, post a new correcting
  voucher in the open year instead, with a «Begrunnelse» that names the old
  one, or have the year reopened:
  [Reopen a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/reopen-a-financial-year.md).

## Steps

1. Open `https://myntos.no/manuelt-bilag/` and choose the voucher's year with
   ‹ ›.
2. Under «Førte manuelle bilag i …», find the voucher and press «Rett». The
   form «Rett bilag …» opens with its lines as they were typed, VAT included.
3. Change what was wrong. Write in «Begrunnelse» what was wrong and why it is
   corrected; the old reason is not carried over.
4. Press «Kontroller». The review says «Dette erstatter bilag … Det gamle
   reverseres i sin egen periode, og dette føres datert …». Fix any red
   message with «Tilbake til skjemaet».
5. Press «Før rettingen». The page says «Bilag … er rettet. Reverseringen og
   det nye bilaget … står i listen.» In the list, the old one reads «Rettet
   med bilag …» and the new one «Retter bilag …».

«Avbryt rettingen» in the form goes back to a new, empty voucher without
changing anything.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Rett» button on the voucher | It is already reversed or corrected, it is itself a reversal, the year is closed, or their role is «Leser» | Correct the newest one; a reversal is not corrected, post what should stand as a new voucher |
| A red message that the original's year is closed | The old voucher cannot be reversed in a closed year | Post a correcting voucher in the open year, or reopen the year |

## Good to know

- The reversal is filed with the same reason and the new voucher's number,
  so the documentation of each refers to the other, as the law requires.
- Documents attached to the old voucher stay with it. Attach the new
  documents under «Dokumenter» before «Kontroller».

## Related

- [Reverse a manual voucher](https://myntos.no/llms/help/opening-balance-and-year-end/reverse-a-manual-voucher.md)
- [Post a manual voucher](https://myntos.no/llms/help/opening-balance-and-year-end/post-a-manual-voucher.md)
- An agent with a key can do this through the API:
  [myntos-manuelt-bilag](https://myntos.no/llms/skills/myntos-manuelt-bilag.md)
