---
title: Change an account's post in the næringsspesifikasjon
description: How an administrator chooses which post of the næringsspesifikasjon (the tax return's itemised business figures, also used in SAF-T) an account is reported under, with Endre post on the Kontoplan page in Myntos, and why a closed year cannot be changed. Use when someone asks where an account ends up in the skattemelding or wants to move it to another post.
pages: /kontoplan/
skill: myntos-kontoplan
---

# Change an account's post in the næringsspesifikasjon

The næringsspesifikasjon is the itemised statement of the business's figures
that goes with the tax return (skattemelding). Each account in the kontoplan
is reported under one of its posts, chosen from the tax authority's own list,
separately for each year; the SAF-T file carries the same choice. Myntos
proposes a post from the account's number, and a person can choose another.
Because this decides what the tax return says, a closed year cannot be
changed: its næringsspesifikasjon has been filed with the posts it had.

## They might ask

- «Hvilken post i næringsspesifikasjonen havner konto 6820 i?»
- «Hvordan endrer jeg posten for en konto?»
- «Kontoen havner feil i skattemeldingen»
- "How do I change where an account is reported in the tax return?"

## Before they start

- The role «Administrator» or «Eier»:
  [Who can change the chart of accounts](https://myntos.no/llms/help/chart-of-accounts/who-can-change-the-chart-of-accounts.md).
- The year must not be closed.
- If unsure which post is right, ask the company's accountant. A wrong post
  moves the amount to another line of the tax return.

## Steps

1. Open the kontoplan: the company's name at the top right, «Innstillinger
   for selskapet», then the link «kontoplanen» in «Selskapet»
   (`https://myntos.no/kontoplan/`).
2. Check the year at the top right; use «‹» and «›» to change it. The post is
   chosen per year.
3. On the account's row, press «Endre post».
4. Choose the post in the list. The posts are grouped by part of the
   statement, each with its code and name.
5. Press «Lagre». The page says «Lagret.», and «Valgt for hånd» shows under
   the post. «Avbryt» leaves it as it was.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Endre post», and «Regnskapsåret er avsluttet. Næringsspesifikasjonen for det er levert med disse postene, så de endres ikke her. En post som var feil, rettes ved å åpne året igjen først.» | The year is closed | Reopening the year comes first, under «Årsavslutning». It is a serious step; talk to the accountant first |
| No «Endre post», and «Ingen post for året» | The account was added after that year was closed | Nothing to choose: the account did not exist that year |
| No buttons at all | Their role is «Medlem» or «Leser» | Ask an «Administrator» or «Eier» |
| «Skatteetaten har ikke publisert postene for … ennå. Valgene under er fra listen for …» | That year's list is not published yet | Choose from the list shown; Myntos uses the latest one |

## Good to know

- A post chosen by hand carries into the next year when that year starts.
- New accounts get Myntos's proposal for every open year unless a post is
  chosen when adding them.

## Related

- [Understand the chart of accounts](https://myntos.no/llms/help/chart-of-accounts/understand-the-chart-of-accounts.md)
- [Add an account](https://myntos.no/llms/help/chart-of-accounts/add-an-account.md)
- An agent with a key can do this through the API:
  [myntos-kontoplan](https://myntos.no/llms/skills/myntos-kontoplan.md)
