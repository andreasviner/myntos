---
title: Understand the chart of accounts
description: What the Kontoplan page in Myntos shows - every account with its NS 4102 number, name, næringsspesifikasjon post and status (Aktiv, Deaktivert, Brukes av Myntos) - how to find the page and how to look at another year. Use when someone asks what the kontoplan is, where to find it, or what a column or status on it means.
pages: /kontoplan/
skill: myntos-kontoplan
---

# Understand the chart of accounts

The chart of accounts (kontoplan) is the list of accounts the company's
entries are booked to. Each account has a four-digit number from the
Norwegian standard NS 4102, where the first digit is the class: 1 assets
(eiendeler), 2 equity and liabilities (egenkapital og gjeld), 3 income
(inntekter), 4 to 7 costs (kostnader), 8 financial items. Myntos sets up a
starter kontoplan for every company. Each account is also placed, per year,
in a post of the næringsspesifikasjon, the itemised statement of the
business's figures that goes with the tax return (skattemelding); the same
post is written into the SAF-T file.

## They might ask

- «Hvor finner jeg kontoplanen i Myntos?»
- «Hva betyr «Brukes av Myntos» i kontoplanen?»
- «Hvilken post i næringsspesifikasjonen havner konto 6810 i?»
- "Where is the chart of accounts in Myntos?"

## Before they start

- Any role can look. Only «Administrator» and «Eier» can change it:
  [Who can change the chart of accounts](https://myntos.no/llms/help/chart-of-accounts/who-can-change-the-chart-of-accounts.md).

## Steps

1. Click the company's name at the top right and choose «Innstillinger for
   selskapet». In the «Selskapet» section, follow the link «kontoplanen»
   (`https://myntos.no/kontoplan/`).
2. The page heading is the company's name; the year shown is at the top
   right, with «‹» for the year before and «›» for the year after.
3. The table lists every account by number:
   - «Konto»: the number. It never changes.
   - «Navn»: the account's name.
   - «Post i næringsspesifikasjonen»: the post's code and name for the year
     shown. «Valgt for hånd» under it means a person chose it rather than
     Myntos. «Ingen post for året» means the account was added after that
     year was closed.
   - «Status»: «Aktiv»; «Deaktivert» (hidden from new entries, kept for the
     years that used it); or «Brukes av Myntos» (Myntos posts to it itself,
     such as the bank account 1920 and the VAT accounts, so it can never be
     switched off or deleted).
4. An «Administrator» or «Eier» also sees the box «Legg til konto» and, on
   each row, the buttons «Endre navn», «Endre post», «Deaktiver» or «Aktiver»,
   and «Slett».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «selskapet har ikke noe regnskapsår 2027 i Myntos» | That year has not started in the books yet | Press «‹» to go back to a year the company has |
| «Regnskapsåret er avsluttet. Næringsspesifikasjonen for det er levert med disse postene, så de endres ikke her. …» | The year is closed | Its posts are frozen: [Change an account's post](https://myntos.no/llms/help/chart-of-accounts/change-an-accounts-post.md) |
| «Skatteetaten har ikke publisert postene for … ennå. Valgene under er fra listen for …» | The tax authority has not published that year's list of posts yet | Nothing to do; Myntos uses the latest published list |
| No buttons, no «Legg til konto» | Their role is «Medlem» or «Leser» | Ask an «Administrator» or «Eier» |

## Good to know

- A kontoplan belongs to one company. An account added here does not exist
  in the person's other companies.
- Every account, deactivated ones too, stays in the list for good once
  anything has been posted to it.

## Related

- [Add an account](https://myntos.no/llms/help/chart-of-accounts/add-an-account.md)
- [Change an account's post](https://myntos.no/llms/help/chart-of-accounts/change-an-accounts-post.md)
- [Who can change the chart of accounts](https://myntos.no/llms/help/chart-of-accounts/who-can-change-the-chart-of-accounts.md)
- An agent with a key can read the kontoplan through the API:
  [myntos-kontoplan](https://myntos.no/llms/skills/myntos-kontoplan.md)
