---
title: Close a financial year
description: How an owner or administrator closes a financial year (årsavslutning) on the Årsavslutning page in Myntos, which moves the year's result onto equity, locks the year and opens the next. Use when someone asks how to close last year, finish the year in Myntos, or move the result to equity.
pages: /arsavslutning/
skill: myntos-arsavslutning
---

# Close a financial year

When a year is over, its result (income minus costs) is moved onto the
company's equity and the year is locked, so the next year starts with the
right balance. Myntos does this on «Årsavslutning»: it shows exactly what it
will post, and the person confirms. How the result is placed depends on
whether the company is an aksjeselskap (AS) or an enkeltpersonforetak (ENK),
and Myntos reads that from Enhetsregisteret. **After closing, nothing can be
posted in that year by anyone in the company** until an owner or administrator
reopens it with a written reason. Closing does **not** send the annual accounts
(årsregnskap) or the tax return (skattemelding) anywhere.

## They might ask

- «Hvordan avslutter jeg regnskapsåret i Myntos?»
- «Hvordan fører jeg årsresultatet over på egenkapitalen?»
- «Må jeg låse fjoråret?»
- "How do I close the financial year in Myntos?"

## Before they start

- The role «Administrator» or «Eier»
  ([Roles and what each may do](https://myntos.no/llms/help/company-and-access/roles-and-permissions.md)).
  Others see the figures and «Det er eier eller administrator av selskapet som
  avslutter et regnskapsår.»
- The year must be over (closing is possible from 1 January of the next year),
  and every earlier year in Myntos must be closed already.
- Everything for the year should be entered: the last invoices, receipts and
  bank lines, and the VAT for the last period settled.
- For an AS: the year's tax cost (betalbar skatt), usually from the
  accountant. Myntos does not calculate it.
- If the company kept books elsewhere before its first year in Myntos, the
  opening balance should be entered first.

## Steps

1. Open «Oversikt» in the top menu (`https://myntos.no/`) and, in the note
   under «Største poster i år», click «Årsavslutning»
   (`https://myntos.no/arsavslutning/`).
2. The page opens on the first year that is not closed. Use «‹» and «›» beside
   the year to change it.
3. Read the figures: «Inntekter», «Kostnader», «Resultat før skatt»,
   «Årsresultat» and «Organisasjonsform».
4. For an AS, type the tax under «Betalbar skatt for året» and leave the
   field; the figures and the postings update:
   [Enter the tax when closing a year](https://myntos.no/llms/help/opening-balance-and-year-end/enter-the-tax-when-closing-a-year.md).
5. Read the messages (red ones stop the close) and the table «Dette blir ført
   per 31. desember» with «Konto», «Tekst», «Debet» and «Kredit». If the
   result is zero it says «Resultatet er null, så ingenting føres. Året låses
   likevel.»
6. Tick «Jeg har gått gjennom tallene og posteringene over. Etter
   avslutningen kan ingenting føres i året, for noen av dem som har tilgang
   til regnskapet.»
7. Press «Avslutt …» (for example «Avslutt 2025»). The page says
   «Regnskapsåret … er avsluttet med bilag …, og … er åpnet.», and the close
   is listed under «Logg».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Det er eier eller administrator av selskapet som avslutter et regnskapsår.» | Their role is «Medlem» or «Leser» | Ask an «Administrator» or «Eier» |
| «Avslutt …» cannot be pressed | A red message stands, or the box is not ticked | [Fix a year that will not close](https://myntos.no/llms/help/opening-balance-and-year-end/fix-a-year-that-will-not-close.md) |
| «Selskapet har ikke noe regnskapsår … i Myntos.» | That year is not kept in Myntos | Use «‹» or «›» to the right year |
| «Skattebeløpet kan ikke leses.» | The tax field holds something that is not an amount | Type digits, with a comma for øre |

## Good to know

- For an AS, a profit first covers any uncovered loss from earlier years
  (udekket tap) and the rest goes to other equity; a loss is covered by other
  equity as far as it goes. For an ENK, the result and the owner's private
  drawings go straight to equity, and no tax is posted. Other company forms
  (ANS, DA, SA, NUF) cannot be closed in Myntos yet.
- What exactly is locked:
  [What closing a year locks](https://myntos.no/llms/help/opening-balance-and-year-end/what-closing-a-year-locks.md).
- The annual accounts to Brønnøysund and the tax return to Skatteetaten are
  separate tasks; closing in Myntos does not file them.
- If they want their own AI to prepare this, they can invite it with «Inviter
  en KI» under «Selskapet» → «Tilgang». If the AI's closing waits for them,
  they approve it on «Godkjenning»:
  [Approve what an AI asks](https://myntos.no/llms/help/ai/approve-what-an-ai-asks.md).

## Related

- [Enter the tax when closing a year](https://myntos.no/llms/help/opening-balance-and-year-end/enter-the-tax-when-closing-a-year.md)
- [Fix a year that will not close](https://myntos.no/llms/help/opening-balance-and-year-end/fix-a-year-that-will-not-close.md)
- [Reopen a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/reopen-a-financial-year.md)
- An agent with a key can do this through the API:
  [myntos-arsavslutning](https://myntos.no/llms/skills/myntos-arsavslutning.md)
