---
title: Choose a SAF-T version
description: Explains the «Versjon» choice on the SAF-T page in Myntos (1.30 or 1.40), which one Myntos picks for each year, and when to change it. Use when someone asks which SAF-T version to choose, or has been asked for a specific version.
pages: /saft/
skill: myntos-saft
---

# Choose a SAF-T version

Skatteetaten publishes the SAF-T format in versions. For 2025 and 2026 both
1.30 and 1.40 are accepted; from 2027 only 1.40 is. Myntos picks the right one
for the year by itself, so **most people never need to change it**. For the
data Myntos holds, the two versions contain the same thing and differ only in
the version number written in the file.

## They might ask

- «Hvilken SAF-T-versjon skal jeg velge?»
- «Revisor ber om SAF-T 1.40»
- «Hva er forskjellen på 1.30 og 1.40?»
- "Which SAF-T version should I pick in Myntos?"

## Before they start

They are on `https://myntos.no/saft/` with the year chosen.

## Steps

1. Look at «Versjon» in the summary. For 2025 and 2026 it is a choice that
   starts at «1.30»; from 2027 it just shows «1.40».
2. Leave it, unless whoever asked for the file named a version.
3. To change it, pick «1.40» in «Versjon». The check runs again for that
   version.
4. Press «Last ned SAF-T-filen».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No choice, only «1.40» | From 2027 only 1.40 is accepted | Nothing to do |
| «SAF-T for … må leveres i versjon …» | That version is not accepted for the year | Use the version the message names |

## Good to know

- Changing the year resets the choice to Myntos's default for that year.

## Related

- [Export a SAF-T file](https://myntos.no/llms/help/saf-t/export-a-saf-t-file.md)
- An agent with a key can choose the version through the API:
  [myntos-saft](https://myntos.no/llms/skills/myntos-saft.md)
