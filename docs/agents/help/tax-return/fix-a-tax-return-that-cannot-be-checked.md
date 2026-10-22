---
title: Fix a tax return that cannot be checked
description: Why the Skattemelding page in Myntos lists something in red and offers no «Kontroller hos Skatteetaten», and where each of those things is fixed - an open year, an account without a post, unposted depreciation, an unknown revisor, an enkeltpersonforetak. Use when someone asks why their skattemelding cannot be checked or sent from Myntos.
pages: /skattemelding/
skill: myntos-skattemelding
---

# Fix a tax return that cannot be checked

Before the tax return can be sent to Skatteetaten for checking, the books it
is built from have to be complete. Whatever stops it is listed in red at the
top of the Skattemelding page, and under «Kontroll og innsending» the page
says «Rett det som står øverst på siden. Da kan skattemeldingen
kontrolleres.» Each line has a link, «Gå til …», to the page where it is put
right.

## They might ask

- «Hvorfor kan jeg ikke kontrollere skattemeldingen?»
- «Hva betyr "har saldo, men ingen post i næringsspesifikasjonen"?»
- «Myntos vet ikke om selskapet har revisor. Hva gjør jeg?»
- "Why can't I check my tax return in Myntos?"

## Steps

1. Open `https://myntos.no/skattemelding/` and read the red lines at the top.
2. Follow the link on each line, fix it there, and come back. The page reads
   the books again when it is opened.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Avslutt … under Årsavslutning først. …» | The year is not closed | «Gå til Årsavslutning»: [Close a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/close-a-financial-year.md). Post the year's depreciation and year-end entries first |
| «Konto … har saldo, men ingen post i næringsspesifikasjonen. …» | An account has no post for the year | «Gå til Kontoplan»: [Change an account's post](https://myntos.no/llms/help/chart-of-accounts/change-an-accounts-post.md). A closed year has to be reopened first |
| «… står på post …, som ikke finnes i Skatteetatens liste …» or «Post … kan ikke brukes av et selskap med full regnskapsplikt …» | The account's post is wrong for an AS | Choose another post on «Kontoplan» |
| «Eiendelene (…) og gjeld og egenkapital (…) er ikke like store. …» | Something is on an account without a post, or the result was not moved to equity | «Gå til Årsavslutning», and read the balance under «Regnskap» |
| «Årets avskrivning er ikke ført under Anlegg. …» | The depreciation is not posted | «Gå til Anlegg og avskrivning»: [Post the year's depreciation](https://myntos.no/llms/help/opening-balance-and-year-end/post-the-years-depreciation.md) |
| «Myntos vet ikke om selskapet har revisor. …» | Myntos has no answer from Enhetsregisteret | «Gå til Selskapet» and fetch the company again: [Fetch from Enhetsregisteret again](https://myntos.no/llms/help/company-and-access/fetch-from-enhetsregisteret-again.md) |
| «Myntos lager ennå ikke næringsspesifikasjonen for et enkeltpersonforetak. …» | An ENK | Not sent from Myntos yet: [What the tax return from Myntos leaves out](https://myntos.no/llms/help/tax-return/what-the-tax-return-leaves-out.md) |
| «Myntos lager næringsspesifikasjonen bare for aksjeselskap foreløpig, …» | Another organisation form | Not sent from Myntos |

## Good to know

- Reopening a closed year to fix something is allowed, with a written reason,
  but only before the annual accounts are adopted:
  [Reopen a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/reopen-a-financial-year.md).
- «Myntos klarte ikke å lage skattemeldingen fra regnskapet. Det er en feil i
  Myntos; …» after pressing «Kontroller hos Skatteetaten» is Myntos's fault,
  not the books': report it under «Meld feil eller ønsker»
  ([Report a bug or ask for something](https://myntos.no/llms/help/feedback/report-a-bug-or-ask-for-something.md)).

## Related

- [See what the tax return will say](https://myntos.no/llms/help/tax-return/see-the-tax-return.md)
- [myntos-skattemelding](https://myntos.no/llms/skills/myntos-skattemelding.md)
