---
title: See how the business is going, month by month
description: How a person sees the company's income (inntekter), costs (kostnader) and result before tax (resultat før skatt) over time on the «Analyse» page in Myntos, per month, per account and, with KI on, by what the money went to, for this year, the last twelve months or an earlier year, against the same dates a year earlier. Use when someone asks how the business is going, whether they earn more than last year, which month was best, what the money went to, or why the figures there differ from «Inn i år» on the overview.
pages: /analyse/
skill: myntos-analyse
---

# See how the business is going, month by month

«Analyse» shows what the company earned, what it spent and the difference, for
a period the person chooses, against the same dates a year earlier: three
figures, a few sentences and four charts. Every number is read off the
books, the way the income statement on «Regnskapet år for år» reads them. The
one thing not to get wrong: **these are accounting figures, not money in the
bank.** They leave out VAT and count an invoice on the day it was issued, not
the day it was paid, so they will not match «Inn i år» on the overview. The
page only shows; nothing on it changes the books. With the company's KI on,
Myntos's KI reads the vouchers in the background to say what each cost and
income was for, so the rings can show «Drivstoff» or «Husleie og lokaler»
rather than an account number. It never makes or changes a figure. With KI
off, nothing on the page asks an AI and the rings are by account.

## They might ask

- «Hvordan går det i år?»
- «Tjener vi mer enn i fjor?»
- «Hvilken måned var best?»
- «Hva går pengene til?»
- «Hvorfor er inntektene på Analyse lavere enn «Inn i år» på forsiden?»
- "Where do I see income and costs per month in Myntos?"

## Before they start

Anyone with access to the company can see it, whatever their role. The page
shows the company that is chosen in the company menu at the top; to see
another company, switch there first
([Switch company](https://myntos.no/llms/help/overview/switch-company.md)).

## Steps

1. Press «Analyse» in the top menu (`https://myntos.no/analyse/`). The
   company's name is the heading, with the organisation number under it.
2. Choose the period with the buttons at the top:
   - «I år» (chosen at first): 1 January to today.
   - «Siste 12 måneder»: the twelve months up to today.
   - «I fjor»: all of last year. The button is there only when the company
     has last year in Myntos.
   - A year before that: choose it in the list «Eldre år» beside the buttons.
     The list is there only when the books have older years.
3. Leave «Sammenlign med året før» ticked to compare with the same dates a
   year earlier, or untick it to see the period on its own. The line under
   the buttons says which dates are shown and which they are compared with.
4. Read the three figures: «Inntekter», «Kostnader» and «Resultat før skatt».
   Under each, the change against the year before, such as «+12 % fra samme
   tid i fjor». The result's change is in kroner, not per cent.
5. Under them, a few sentences say the most important thing in words.
6. The charts:
   - «Inntekter per måned» and «Kostnader per måned»: one column per month,
     with the year before as paler columns behind.
   - «Inntekter per konto» and «Kostnader per konto»: a ring of the largest
     accounts, with the amount and share of each; «Andre» is the rest put
     together, not one account.
   - With KI on, once KI has read at least 90 % of the period's costs, the
     costs ring becomes «Hva kostnadene gikk til», split by what the money
     went to, with «Fordelt etter hva bilagene gjelder, lest av KI.» under
     it; the same for income, «Hva inntektene kom fra». What KI has not read
     yet is its own slice, «Ikke lest ennå». Each ring switches on its own,
     so one can be by what it was for and the other by account.
   - «Vis tallene» under a chart shows its figures as a table, to the øre.
7. While KI reads, a line above the rings says «KI leser bilagene for å
   fordele dem etter hva de gjelder (… av …). Siden oppdaterer seg selv.»
   Nothing needs doing: the page fills in the rings by itself when the
   reading is done. «Slik bruker Myntos KI» leads to what is sent and kept.
   The figures are right while it reads; only the split changes.

Myntos remembers the period and the comparison choice for each company on that
browser, so the page opens the same way next time.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Ingen inntekter eller kostnader er ført ennå.» | Nothing is booked in the company yet | Book the first voucher under «Betaling» ([Record a payment from a receipt](https://myntos.no/llms/help/payments/record-a-payment-from-a-receipt.md)), or bring the books over from the previous program |
| «Ingen bilag i perioden» | The books have no vouchers in the chosen dates | Choose another period; the card says the date of the last voucher |
| «Regnskapet i Myntos dekker ikke hele samme periode året før, så tallene sammenlignes ikke.» | The company was not in Myntos for all of the year before, so a percentage would compare a full period with part of one | Nothing is wrong. To compare, bring the earlier year over from the previous program |
| «Regnskapet i Myntos begynner i …, så månedene før står tomme.» under the monthly charts | The period starts before the company's first voucher in Myntos | Nothing is wrong: the empty months are outside the books, not months with nothing earned |
| «Det siste bilaget er datert …, så månedene etter står tomme til noe er ført.» | The period runs past the last booked voucher | Empty months are not booked yet, not zero. Book what is missing |
| «Med KI på fordeler Myntos kostnadene etter hva de gjelder, ikke bare etter konto.» under the costs ring | The company has KI off, so the rings are by account | Nothing is wrong. Someone who manages the company can switch KI on with «Slå på KI» ([Turn Myntos's KI on or off](https://myntos.no/llms/help/ai/turn-myntos-ki-on-or-off.md)); the line is shown only to them |
| The rings stay «per konto» with KI on | KI has read less than 90 % of that side, or is reading now, or Løynd did not answer just then | Wait, or open the page again later: each visit reads what is left, and Myntos tries again ten minutes after Løynd did not answer |

## Good to know

- **«Resultat før skatt»** leaves out the tax cost and what the result is set
  aside to, so it is not the year's result after tax. That is on «Regnskapet
  år for år».
- For a whole year, «Inntekter» less «Kostnader» is that year's result before
  tax on «Regnskapet år for år», exactly.
- A figure that looks wrong is corrected in the voucher behind it, not here.
  «Regnskapet år for år» leads from a line to the accounts and vouchers
  behind it.
- The sentences are written by fixed rules from the figures, not by AI, so the
  page works the same with KI switched off. With KI on, a sentence may name
  what KI read a cost to be, such as «Drivstoff har økt med 40 % fra samme
  tid i fjor.», but the amounts and percentages are still the books'.
- The KI reading is at «Gratis» and costs nothing of the company's KI plan
  ([What Myntos's KI does](https://myntos.no/llms/help/ai/what-myntos-ki-does.md)).

## Related

- [Run a deep KI analysis of a period](https://myntos.no/llms/help/analysis/run-a-deep-ki-analysis.md)
- [See the accounts for every year](https://myntos.no/llms/help/reports/see-the-accounts-for-every-year.md)
- [Find what is behind a figure in the accounts](https://myntos.no/llms/help/reports/find-what-is-behind-a-figure.md)
- [Read the overview](https://myntos.no/llms/help/overview/read-the-overview.md)
- An agent with a key can read the same figures and charts through the API:
  [myntos-analyse](https://myntos.no/llms/skills/myntos-analyse.md)
