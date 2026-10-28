---
title: Share data to improve Myntos
description: How an owner or administrator chooses what a company shares with Myntos to make Myntos better, under «Del data for å forbedre Myntos» on the company settings page, and what each of the four levels shares, from nothing to the bilag themselves, not anonymised. Use when someone asks whether Myntos uses their data, wants to help improve Myntos, or wants to stop sharing.
pages: /organisasjon/ /datadeling/
skill: myntos-selskap
---

# Share data to improve Myntos

A company can let Myntos use some of what is in its books to make Myntos
better. It shares **nothing** until an owner or administrator chooses
otherwise, and each company is chosen for separately. The highest level,
«Alt, også bilag», shares the receipts and invoices as they are, **not
anonymised**, with the names and amounts of customers, suppliers and
employees on them. The page shows it in red.

Myntos does not collect any of this yet. The choice is stored, so it applies
from the day collection starts.

## They might ask

- «Bruker Myntos regnskapet mitt til noe?»
- «Hvordan deler jeg data med Myntos?»
- «Hvordan slutter jeg å dele data med Myntos?»
- «Hva betyr Alt, også bilag?»

## Before they start

They need to be an owner («Eier») or administrator («Administrator») in the
company. Everyone else can see what is chosen but not change it; see
[Roles and what each may do](https://myntos.no/llms/help/company-and-access/roles-and-permissions.md).

## Steps

1. Open «Innstillinger» (the gear at the top) or go to
   `https://myntos.no/organisasjon/#datadeling`, with the right company
   chosen at the top.
2. Find «Del data for å forbedre Myntos», near the bottom of the page, just
   above «Slett selskapet» if they are an owner. The line at the top says what
   the company shares now, with its name, for example «Nordmann AS deler
   ingenting med Myntos.»
3. Choose a level in the list «Hva selskapet deler». The sentence under the
   list says what the chosen level shares:
   - «Ingenting»: the default. Nothing is used.
   - «Bruksstatistikk»: which pages and buttons are used, how long things
     take and what fails in the browser. Nothing from the books, and not
     linked to the person or the company.
   - «Anonymiserte rettelser»: usage, plus which category, account and VAT
     code Myntos proposed for a bilag and what was chosen instead, and the
     company's public næringskode. No names, organisation numbers, account
     numbers, amounts, text or documents, and not linked to the person or the
     company.
   - «Alt, også bilag» (red): the bilag files, the text Myntos read from them,
     its proposal, and the entry as it was booked, with every name, address,
     amount and account number on them.
4. For «Alt, også bilag», the list turns red, a small «Takk :)» shows for a
   moment, and a red box explains what it means. Tick «Jeg forstår at
   bilagene deles slik de er, og vil dele dem.» Nothing is saved yet: the
   «Takk :)» is not a confirmation.
5. Press «Lagre». The line at the top changes to say what is shared now.

To stop sharing, choose «Ingenting» and press «Lagre».

«Hva deles på hvert nivå?» opens `https://myntos.no/datadeling/`, which lists
everything each level shares and does not share. It can be read without
signing in.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| The list is greyed out and «Bare eiere og administratorer kan endre hva selskapet deler.» | Their role is «Medlem» or «Leser» | Ask an owner or administrator |
| «Lagre» stays grey | Nothing changed, or «Alt, også bilag» is chosen and the box is not ticked | Choose a different level, or tick the box |
| «Fikk ikke lagret. Prøv igjen.» | The save failed | Try again; the choice went back to what is saved |

## Good to know

- It applies only from the day it is saved. Nothing already booked is shared
  after the fact.
- Choosing a lower level applies at once. What was shared under «Alt, også
  bilag» is deleted then. What was shared anonymously cannot be traced back to
  the company, so it cannot be deleted.
- What was shared under «Alt, også bilag» is read only by the people who
  build Myntos, stored in the EEA, and never used to train an AI model.
  Nothing is sold or passed on.
- If Myntos wants to use more than a level says, it asks again; until the
  company answers, its earlier choice stands.
- Deleting the company from Myntos sets it back to «Ingenting», and opening
  the books again does not bring the old choice back.
- Failures on Myntos's server are logged for every company whatever it
  chose, «Ingenting» included: what failed in the code, how often and when,
  never who it concerned, the company or anything from the books. That is
  operation, not sharing, and it is in the privacy policy (section 8 of
  `https://myntos.no/personvern/`).
- This is not the KI switch. Whether bilag are read by Myntos's KI is
  chosen on the KI page; see
  [Turn Myntos's KI on or off](https://myntos.no/llms/help/ai/turn-myntos-ki-on-or-off.md).
- No key can change it, whatever its rights: only a person can.

## Related

- [Roles and what each may do](https://myntos.no/llms/help/company-and-access/roles-and-permissions.md)
- [Turn Myntos's KI on or off](https://myntos.no/llms/help/ai/turn-myntos-ki-on-or-off.md)
- An agent with a key can read the level (`data_sharing` on the company) but
  never change it: [myntos-selskap](https://myntos.no/llms/skills/myntos-selskap.md)
