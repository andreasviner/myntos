---
title: Set up avdelinger and prosjekter
description: How an administrator sets up dimensions (avdelinger, prosjekter, or a sorting of their own) and their values on the Avdelinger page in Myntos, so payments can be put on them and the result seen per department or project. Use when someone asks how to add a department, a project or a cost centre in Myntos.
pages: /avdelinger/
skill: myntos-avdelinger
---

# Set up avdelinger and prosjekter

The account says what an amount was; an avdeling (department) or a prosjekt
(project) says which part of the business it belongs to. Set one up, add its
values (the shop, the workshop; project P-104 «Nytt tak»), and each payment
can be put on one of them, so the result can be read per department or
project. Most companies need none. **A value's code can never be changed
afterwards**, because it is written into the SAF-T file.

## They might ask

- «Hvordan lager jeg avdelinger i Myntos?»
- «Kan jeg føre på prosjekt?»
- «Jeg vil skille butikken fra verkstedet i regnskapet»
- "How do I add a department or a project in Myntos?"

## Before they start

- The role «Administrator» or «Eier». A «Medlem» or «Leser» sees «Bare en
  administrator eller eier kan sette opp avdelinger, prosjekter og
  driftsgrener.» instead of the box «Sett opp».
- A farm with the farming add-on can start from NIBIO's codes instead:
  [Set up driftsgrener from NIBIO's list](https://myntos.no/llms/help/farming/set-up-driftsgrener.md).

## Steps

1. Open «Oversikt» and follow the link «Avdelinger» in the note at the bottom,
   or follow «Avdelinger» in the text at the top of «Kontoplan»
   (`https://myntos.no/avdelinger/`).
2. In the box «Sett opp», under «Ny dimensjon», choose at «Hva vil du sortere
   etter?»: «Avdeling», «Prosjekt», or «Noe annet». For «Noe annet», fill in
   «Kode» (up to nine letters or digits, never changed) and «Navn».
3. Press «Legg til dimensjon». The page says «Lagt til. Legg til verdiene
   under den.», and the dimension appears with its name and code as a heading.
4. Under that heading, type the value's code in the box «Kode» (a number like
   «20», or «P-104») and its name in «Navn», and press «Legg til». Repeat for
   each department or project.
5. Each value is listed with «Kode», «Navn», «Brukt» («Ikke brukt» until
   something is posted to it) and «Status».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No box «Sett opp» | Their role is «Medlem» or «Leser» | Ask an «Administrator» or «Eier» |
| «selskapet har allerede en dimensjon …», or ««Avdeling» har allerede 20. Er den deaktivert, slå den på igjen» | The dimension, or the value in it, exists, perhaps «Deaktivert» | Use it, or press «Aktiver» on it |
| «koden må være 1 til 9 bokstaver eller tall …» or «koden kan ha høyst 35 tegn …» | The code has a space or a character SAF-T cannot carry, or is too long | Letters, digits, «-» and «_» only |

## Good to know

- After this, «Betaling» shows a list for the dimension on the form, with
  «Ingen» first: [Put a payment on an avdeling or prosjekt](https://myntos.no/llms/help/payments/put-a-payment-on-an-avdeling.md).
- Payments posted before the value existed stay on no value; they are shown
  as «Uten avdeling» in the result. A posted payment is moved by correcting it.
- Names can be changed at any time with «Endre navn»; the code cannot.

## Related

- [See the result per avdeling or prosjekt](https://myntos.no/llms/help/departments/see-the-result-per-department.md)
- [Switch off or delete an avdeling](https://myntos.no/llms/help/departments/switch-off-or-delete-an-avdeling.md)
- An agent with a key can do this through the API:
  [myntos-avdelinger](https://myntos.no/llms/skills/myntos-avdelinger.md)
