---
title: Buy a KI plan for a company
description: How the owner of a company activates, switches or cancels a monthly KI plan («KI Start», «KI Pluss» or «KI Pro») on «Om KI og KI-plan» (/ki/about/), how weekly percentage quotas work and reset every Monday, and how monthly section 5-1 invoices are sent via EHF and PDF e-mail. Use when someone asks what the KI plans cost, how weekly quotas work, how invoicing works, or how to cancel a KI plan.
pages: /ki/about/
skill: myntos-ki
---

# Buy a KI plan for a company

Myntos's own KI has three levels: «Gratis», which every company has at no cost
and with no limit, and «Medium» and «Smart», larger models that require a KI
plan. Each plan is a monthly subscription with a **weekly quota** shown as
remaining percentage (0 to 100 %) that resets every Monday at 00:00 Norwegian
time, billed monthly by invoice with no lock-in period.

| Plan | A month, excl. VAT | With 25 % VAT | Weekly quota |
| --- | --- | --- | --- |
| «KI Start» | 49 kr | 61,25 kr | Standard ukekvote |
| «KI Pluss» | 149 kr | 186,25 kr | 3× ukekvote |
| «KI Pro» | 399 kr | 498,75 kr | 8× ukekvote |

The accounting program itself stays free whether or not the company has a plan,
and so does «Gratis».

## They might ask

- «Hva koster KI-planene?»
- «Hvordan får jeg «Smart»?»
- «Hvordan fungerer ukekvoten?»
- «Hvordan faktureres KI-planen?»
- «Hvordan avslutter jeg KI-abonnementet?»
- «Hvor finner jeg fakturaene for KI-planen?»

## Before they start

- The role «Eier» in the company. Only an owner can activate, switch, or cancel
  a plan; other roles can read the status, weekly usage, and invoices.
- KI switched on for the company under «Innstillinger for selskapet» →
  «KI i Myntos» (`https://myntos.no/organisasjon/#ki`)
  ([Turn Myntos's KI on or off](https://myntos.no/llms/help/ai/turn-myntos-ki-on-or-off.md)).

## Steps

1. Signed in with the right company chosen at the top right, click the company's
   name and choose **Om KI og KI-plan** (`https://myntos.no/ki/about/#planer`).
   The link under any KI level picker («Se KI-planene», «Bytt plan» or «Om
   KI-nivåene») also goes straight there.
2. Under «Tre nivåer, og KI-planene», check the company's current plan and
   weekly quota meter (%).
3. Optionally enter or update «E-postadresse for kopi av månedsfaktura (PDF)».
4. Press **Velg KI Start**, **Velg KI Pluss** or **Velg KI Pro** (or **Bytt til
   …** to switch plans).
5. The plan and its weekly quota activate immediately. Myntos issues a
   Norwegian section 5-1 monthly invoice from Lindeman Technologies AS, sends
   it via **EHF over Peppol** when the company can receive EHF and sends a PDF
   copy by e-mail. Every invoice also appears under **Fakturaer for KI-plan**
   with a **Last ned PDF** link.
6. To stop renewal, press **Avslutt abonnement**. Paid levels remain active
   until the end of the current billing period, and no further monthly invoice
   is issued.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No plan buttons, and «Bare eieren av … kan velge eller endre en KI-plan.» | Their role is not «Eier» | Ask an owner of the company to choose or change the plan |
| «KI er slått av for …. Slå på KI under Innstillinger for selskapet før du velger en plan.» | KI is switched off for the company | Turn KI on under `https://myntos.no/organisasjon/#ki` first |
| «KI-planer tilbys ikke i denne installasjonen av Myntos.» | This installation has no KI billing or paid models configured | «Gratis» still works with no limit |

## Good to know

- Weekly quotas reset every Monday at 00:00 `Europe/Oslo`. Unused weekly quota
  does not roll over to the next week.
- «Smart» uses a larger model than «Medium» and therefore uses a larger share of
  the weekly percentage per answer.
- When the weekly quota reaches 0 %, «Gratis» answers automatically until Monday
  (or until the owner upgrades to a larger plan).
- Switching plans mid-month applies the new weekly quota right away without
  double-billing; the new monthly price appears on the next monthly invoice.
- Only «Medium» and «Smart» count against the weekly quota. «Gratis», reading a
  picture with «Legg ved bilde», and background voucher tagging on «Analyse»
  never count.
- Full terms are on «Vilkår» (`https://myntos.no/vilkar/#ki-planer`) and
  privacy details on «Personvern» (`https://myntos.no/personvern/#ki-planer`).
- An AI assistant connected with a key can inspect the plan, weekly usage, and
  download KI invoices, but can never activate, switch, or cancel a plan.

## Related

- [Ask Myntos KI anything about your books](https://myntos.no/llms/help/ai/ask-ki-about-your-books.md)
- [Choose the KI level, and add a picture to a KI box](https://myntos.no/llms/help/ai/choose-a-ki-level.md)
- [What Myntos's own KI does](https://myntos.no/llms/help/ai/what-myntos-ki-does.md)
- An agent with a key reads plans, weekly usage, and KI invoices through
  [myntos-ki](https://myntos.no/llms/skills/myntos-ki.md)
