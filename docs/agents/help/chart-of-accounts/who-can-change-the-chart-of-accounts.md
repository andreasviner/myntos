---
title: Who can change the chart of accounts
description: Which roles in Myntos may add, rename, deactivate, delete and regroup accounts on the Kontoplan page (only Administrator and Eier), why an ordinary member who posts payments cannot, and what to do instead. Use when someone sees no buttons on the Kontoplan page or asks who may change the kontoplan.
pages: /kontoplan/
skill: myntos-kontoplan
---

# Who can change the chart of accounts

Everyone with access to the books can see the kontoplan, but only an
«Administrator» or «Eier» can change it. A «Medlem» can post payments every
day and still not touch the kontoplan, on purpose: an account changed here
changes what everybody's entries can be booked to, and which post of the tax
return the figures land in. Someone invited to keep the books should not be
able to move a cost account to another post by accident.

## They might ask

- «Hvorfor ser jeg ingen knapper i kontoplanen?»
- «Hvem kan legge til kontoer i Myntos?»
- «Jeg er medlem, hvorfor kan jeg ikke endre kontoplanen?»
- "Why can't I edit the chart of accounts in Myntos?"

## Before they start

- Nothing; this is about roles.

## Steps

1. Open the kontoplan: the company's name at the top right, «Innstillinger
   for selskapet», then the link «kontoplanen» in «Selskapet»
   (`https://myntos.no/kontoplan/`).
2. If the box «Legg til konto» and the buttons on the rows are missing, the
   person's role in this company is «Medlem» or «Leser».
3. Ask an «Administrator» or «Eier» of the company to make the change, or to
   give the person the role «Administrator». Roles are set under «Tilgang» on
   the company page (`https://myntos.no/organisasjon/#tilgang`).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| The kontoplan is visible but has no buttons | Role «Medlem» or «Leser» | As in step 3 |
| The buttons are there in one company and not in another | Roles are per company | Check the company named in the page heading |

## Good to know

| Role | Sees the kontoplan | Changes it |
| --- | --- | --- |
| «Leser» | Yes | No |
| «Medlem» | Yes | No |
| «Administrator» | Yes | Yes |
| «Eier» | Yes | Yes |

- An AI the person invites with a key acts with the person's own role, so an
  AI invited by a «Medlem» cannot change the kontoplan either:
  [Invite an AI](https://myntos.no/llms/help/ai/invite-an-ai.md).

## Related

- [Understand the chart of accounts](https://myntos.no/llms/help/chart-of-accounts/understand-the-chart-of-accounts.md)
- [Add an account](https://myntos.no/llms/help/chart-of-accounts/add-an-account.md)
- An agent with a key, invited by an administrator or owner, can do this
  through the API:
  [myntos-kontoplan](https://myntos.no/llms/skills/myntos-kontoplan.md)
