---
title: See the result per avdeling or prosjekt
description: How a person reads income, costs and result per avdeling, prosjekt or driftsgren for a year, and per month, on the Avdelinger page in Myntos. Use when someone asks what one department, project or production line earned, or for a farm's driftsgrenregnskap.
pages: /avdelinger/
skill: myntos-avdelinger
---

# See the result per avdeling or prosjekt

Once payments are put on avdelinger, prosjekter or driftsgrener, the page
«Avdelinger» shows what each earned and cost in a year: income, costs and the
result, without VAT, and the same per month. Everything nobody put on a value
is one row of its own, so the rows always add up to the year's result.

## They might ask

- «Hva tjente butikken i fjor?»
- «Hvordan ser jeg resultatet per prosjekt?»
- «Hva sitter vi igjen med på melkeproduksjonen?»
- "How do I see profit per department in Myntos?"

## Before they start

Any role may read it. The company needs at least one avdeling, prosjekt or
driftsgren set up, with payments on it:
[Set up avdelinger and prosjekter](https://myntos.no/llms/help/departments/set-up-departments-and-projects.md).

## Steps

1. Open «Avdelinger» from the link in the note at the bottom of «Oversikt»
   (`https://myntos.no/avdelinger/`).
2. Choose the year with ‹ and › beside the year at the top.
3. The box «Resultat per avdeling» (or «Resultat per prosjekt», «Resultat per
   driftsgren») lists «Verdi», «Inntekter», «Kostnader» and «Resultat» for
   each value. When the company has more than one dimension, choose which at
   «Dimensjon».
4. The row «Uten avdeling» (or «Uten prosjekt» …) is what no value carries,
   and «Sum» is the year's result.
5. Open «Resultat per måned» under the table for the result month by month.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Ingen avdelinger, prosjekter eller driftsgrener ennå» | Nothing is set up | Set one up, or ask an «Administrator» or «Eier» |
| Everything is under «Uten avdeling» | The payments were posted without a value | Put new payments on one; an old one is moved by correcting it under «Betaling» |

## Good to know

- Income and costs are without VAT, from the income and cost accounts
  (classes 3 to 8).
- The opening balance and the year-end closing are left out. Everything else
  posted in the year counts: payments, invoices, manual vouchers, vouchers
  brought from another program, and reversals, which cancel what they
  reverse.
- A switched-off value is shown with «(deaktivert)» in the years it has
  figures.

## Related

- [Put a payment on an avdeling or prosjekt](https://myntos.no/llms/help/payments/put-a-payment-on-an-avdeling.md)
- An agent with a key can read this through the API:
  [myntos-avdelinger](https://myntos.no/llms/skills/myntos-avdelinger.md)
