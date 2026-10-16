---
title: Correct the livestock count
description: How a person replaces or withdraws a year-end count of livestock and stock that is already posted, on the Buskap og lager page in Myntos. Use when someone counted wrong, forgot an animal or a kind of feed, or posted the count in the wrong year.
pages: /buskap/
skill: myntos-landbruk-arsoppgjor
---

# Correct the livestock count

A posted count is never edited. Correcting it reverses its voucher and posts
the new count in the same step, so both stand in the books with a link between
them, as bokføringsloven asks.

## They might ask

- «Jeg glemte to kviger i tellingen, hvordan retter jeg?»
- «Kan jeg slette dyretellingen?»
- "How do I fix a wrong livestock count in Myntos?"

## Before they start

- The role «Medlem» or more.
- The year must be open. If it is closed, it is reopened on «Årsavslutning»
  first ([Reopen a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/reopen-a-financial-year.md)).

## Steps

1. Open «Buskap og lager» (`https://myntos.no/buskap/`) and choose the year in
   «Regnskapsår».
2. In «Ført telling», press «Rett tellingen». The form opens with the posted
   lines.
3. Change the lines, press «Kontroller», read «Gjennomgå før føring», and press
   «Før tellingen». The old voucher is reversed and the new one posted.
4. To remove the count without a new one, press «Trekk den tilbake» and confirm.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Rett tellingen» button | They are «Leser», or the year is closed | Ask an owner, or reopen the year |
| The opening lines cannot be changed | They are last year's count, unchanged | Correct last year's count instead |

## Good to know

- Next year's opening is this year's count. Correcting this year changes what
  next year starts from.
- The voucher cannot be reversed from «Bilag» or «Betaling»; this page is its
  only way out.

## Related

- [Count livestock and stock at year end](https://myntos.no/llms/help/farming/count-livestock-and-stock.md)
- An agent with a key can do this through the API:
  [myntos-landbruk-arsoppgjor](https://myntos.no/llms/skills/myntos-landbruk-arsoppgjor.md)
