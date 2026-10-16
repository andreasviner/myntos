---
title: Switch off or delete an avdeling
description: How an administrator renames, switches off, switches on or deletes an avdeling, prosjekt or driftsgren, or a whole dimension, on the Avdelinger page in Myntos, and why one with postings can only be switched off. Use when a department closes, a project is over, a farm gives up a production, or a value was added by mistake.
pages: /avdelinger/
skill: myntos-avdelinger
---

# Switch off or delete an avdeling

A project ends, a shop closes, a farm stops keeping sheep. The value is then
switched off: it is no longer offered on new payments, while every payment
already on it keeps it and the result still shows it. **Only a value nothing
has been posted to can be deleted**, because a posted voucher is history.

## They might ask

- «Prosjektet er ferdig, hvordan fjerner jeg det?»
- «Vi har sluttet med sau, kan jeg slette driftsgrenen?»
- «Jeg la til feil avdeling»
- "How do I delete a department in Myntos?"

## Before they start

The role «Administrator» or «Eier».

## Steps

1. Open «Avdelinger» (`https://myntos.no/avdelinger/`) and find the value in
   the box «Sett opp».
2. To rename it, press «Endre navn», type the new name and press «Lagre». The
   code stays.
3. To switch it off, press «Deaktiver». The page says «Deaktivert. Den tilbys
   ikke i nye føringer.», and «Status» reads «Deaktivert». «Aktiver» switches
   it back on.
4. To delete one added by mistake, press «Slett» and confirm. «Slett» is
   shown only while «Brukt» says «Ikke brukt».
5. The buttons beside the dimension's heading do the same for the whole
   dimension: «Deaktiver» there stops all its values on new payments, and
   «Slett» is there only while none of its values is used.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Slett» button | Something is posted to it | Press «Deaktiver» instead |
| On «Betaling»: «… er deaktivert og brukes ikke i nye føringer» | The value or its dimension is switched off | Choose another, leave it at «Ingen», or switch it on again here |

## Related

- [Set up avdelinger and prosjekter](https://myntos.no/llms/help/departments/set-up-departments-and-projects.md)
- An agent with a key can do this through the API:
  [myntos-avdelinger](https://myntos.no/llms/skills/myntos-avdelinger.md)
