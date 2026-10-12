---
title: Add an account
description: How an administrator adds a new account to the company's chart of accounts (kontoplan) on the Kontoplan page in Myntos, with its NS 4102 number, name and næringsspesifikasjon post. Use when someone needs an account the books do not have, such as a separate account for hosting, leasing or a loan.
pages: /kontoplan/
skill: myntos-kontoplan
---

# Add an account

The starter kontoplan covers the usual payments. When a business needs to
keep something apart (hosting apart from other software, one product line
apart from another), it adds an account. The number is four digits from the
Norwegian standard NS 4102, and **it can never be changed afterwards**, so
choose it next to similar accounts: hosting beside 6810 «Datakostnad og
programvare» as 6820, for instance. If the company has an accountant, ask
them which number to use.

## They might ask

- «Hvordan legger jeg til en konto i kontoplanen?»
- «Jeg trenger en egen konto for hosting»
- «Kan jeg lage mine egne kontoer i Myntos?»
- "How do I add an account to the chart of accounts in Myntos?"

## Before they start

- The role «Administrator» or «Eier»:
  [Who can change the chart of accounts](https://myntos.no/llms/help/chart-of-accounts/who-can-change-the-chart-of-accounts.md).
- The number must not be in the list already. If it is there as
  «Deaktivert», switch it back on instead:
  [Deactivate or reactivate an account](https://myntos.no/llms/help/chart-of-accounts/deactivate-an-account.md).

## Steps

1. Click the company's name at the top right, choose «Innstillinger for
   selskapet», and in «Selskapet» follow the link «kontoplanen»
   (`https://myntos.no/kontoplan/`).
2. In the box «Legg til konto», type the number at «Kontonummer» (four
   digits, 1000 to 8999).
3. At «Navn», type the account's name.
4. At «Post i næringsspesifikasjonen», Myntos fills in a proposal once the
   four digits are typed: «Foreslått ut fra nummeret: …». It is a proposal,
   not a decision; choose another post in the list if it fits better.
5. Press «Legg til». The page says «Kontoen er lagt til.», and the account
   appears in the table as «Aktiv».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Legg til konto» box | Their role is «Medlem» or «Leser» | Ask an «Administrator» or «Eier» |
| «kontoplanen har allerede konto …» | The number is taken, perhaps by a deactivated account | Use that account, or press «Aktiver» on it |
| «kontoklasse 1 til 8 kan brukes; klasse 9 er til intern bruk …» | Numbers starting with 9 are not used in the books | Choose a number in class 1 to 8 |
| «kontonummeret må være fire siffer, som i NS 4102» or «String should have at least 4 characters» | Not four digits | Type four digits |
| «String should have at least 1 character» | «Navn» is empty | Give the account a name |

## Good to know

- The account's type (asset, liability, income, cost) follows from the first
  digit, so the class decides where the account lands in the accounts.
- Without a choice of post, every open year gets the post Myntos proposes for
  the number. A post chosen by hand is used for every open year and carried
  into later ones. Closed years get none: the account did not exist then.
- To use the new account on a payment, register it under «Betaling» and pick
  the account in the field «Konto», which appears when the kontoplan has more
  than one account of the same kind as the category.
- An account added by mistake can be deleted as long as nothing has been
  posted to it:
  [Delete an account](https://myntos.no/llms/help/chart-of-accounts/delete-an-account.md).

## Related

- [Understand the chart of accounts](https://myntos.no/llms/help/chart-of-accounts/understand-the-chart-of-accounts.md)
- [Change an account's post](https://myntos.no/llms/help/chart-of-accounts/change-an-accounts-post.md)
- An agent with a key can do this through the API:
  [myntos-kontoplan](https://myntos.no/llms/skills/myntos-kontoplan.md)
