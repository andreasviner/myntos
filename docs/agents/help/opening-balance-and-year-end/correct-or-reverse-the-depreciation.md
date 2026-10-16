---
title: Correct or reverse the year's depreciation
description: How a person corrects or reverses depreciation already posted from the anleggsregister on the Anlegg og avskrivning page in Myntos, for example after registering an asset that was missing. Use when someone says the avskrivninger are wrong, out of date, or should be taken back.
pages: /anlegg/
skill: myntos-anlegg
---

# Correct or reverse the year's depreciation

A posted voucher is never edited. When the register changes after the
depreciation is posted (an asset added, a sale recorded), the page says the
voucher is out of date, and it is corrected: the old voucher is reversed and
a new one posted, linked, in one step.

## They might ask

- «Jeg glemte en maskin, hvordan retter jeg avskrivningene?»
- «Kan jeg angre avskrivningene for i fjor?»
- "How do I fix depreciation I already posted in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- The year must be open. If it is closed, it is reopened under «Årsavslutning»
  first.

## Steps

1. Open «Anlegg og avskrivning» (`https://myntos.no/anlegg/`) and choose the
   year. The card's heading says which voucher it was posted with.
2. Make the change in the register first, if that is the reason.
3. Adjust «Årets avskrivning» if needed and press «Kontroller».
4. Press «Rett avskrivningene». The old voucher and its reversal stay in the
   books beside the new one, listed under the card.
5. To take it back without a new one, press «Reverser avskrivningene» and
   confirm.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «avskrivningene for … er ført, og saldoen de bygger på kommer fra …» | A later year's depreciation rests on this year | Reverse the later year first |
| «avskrivningene rettes og reverseres under Anlegg …» | The voucher was reversed from the voucher list | Use this page |

## Related

- [Post the year's depreciation](https://myntos.no/llms/help/opening-balance-and-year-end/post-the-years-depreciation.md)
- [Reopen a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/reopen-a-financial-year.md)
- An agent with a key can do this through the API:
  [myntos-anlegg](https://myntos.no/llms/skills/myntos-anlegg.md)
