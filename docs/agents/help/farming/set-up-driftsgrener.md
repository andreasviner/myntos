---
title: Set up driftsgrener from NIBIO's list
description: How a farm with the farming add-on picks its driftsgrener (melkeproduksjon, sau, skog …) and avdelinger (Jordbruk, Skogbruk) from NIBIO's Landbrukets kodesystem on the Avdelinger page in Myntos, to see the result per driftsgren. Use when a farmer asks for driftsgrenregnskap, the result per production, or NIBIO's driftsgrein codes.
pages: /avdelinger/
skill: myntos-avdelinger
---

# Set up driftsgrener from NIBIO's list

A farm usually wants to know what each production earned: the milk apart from
the sheep apart from the forest. Those are driftsgrener, and NIBIO's
Landbrukets kodesystem gives them codes every farm accountant knows (6110
Melkeproduksjon storfe, 6300 Saueproduksjon, 7000 Skogbruksproduksjon). With
the farming add-on, the page «Avdelinger» offers that list, and NIBIO's
avdelinger (10 Finans, 20 Jordbruk, 30 Skogbruk …), to tick.

## They might ask

- «Hvordan får jeg driftsgrenregnskap i Myntos?»
- «Kan jeg se hva melka tjener alene?»
- «Bruker Myntos NIBIOs driftsgreinkoder?»
- "How do I track results per farm enterprise in Myntos?"

## Before they start

- The farming add-on switched on:
  [Turn on farming](https://myntos.no/llms/help/farming/turn-on-farming.md).
- The role «Administrator» or «Eier».

## Steps

1. Open «Avdelinger» (`https://myntos.no/avdelinger/`), from the note at the
   bottom of «Oversikt».
2. In the box «Driftsgrener fra NIBIO», tick what the farm has under
   «Driftsgrener», and under «Avdelinger» if the farm wants Jordbruk apart
   from Skogbruk too. Those already added are ticked and marked «(lagt til)».
3. Press «Legg til valgte». The page says «Lagt til.», and the box «Sett opp»
   shows the dimension «Driftsgren (DRIFTSGR)» with the codes ticked.
4. Under «Betaling», a list «Driftsgren» now appears on the form and on each
   line of a settlement: put the milk on 6110, the feed deducted on the
   driftsgren that ate it.
5. «Resultat per driftsgren» at the top of «Avdelinger» shows the result.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No box «Driftsgrener fra NIBIO» | The farming add-on is off, or their role is «Medlem» or «Leser» | Switch farming on under «Kontoplan», or ask an «Administrator» or «Eier» |

## Good to know

- The names can be changed afterwards with «Endre navn»; the codes cannot.
- A farm moving from Tripletex brings its avdelinger along with the SAF-T
  files: the avdeling in Tripletex's six-digit accounts (402020 is 4020 on
  avdeling 20) is put on each line.

## Related

- [See the result per avdeling or prosjekt](https://myntos.no/llms/help/departments/see-the-result-per-department.md)
- [Book a settlement](https://myntos.no/llms/help/farming/book-a-settlement.md)
- An agent with a key can do this through the API:
  [myntos-avdelinger](https://myntos.no/llms/skills/myntos-avdelinger.md)
