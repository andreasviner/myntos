---
title: Export a SAF-T file
description: How a person downloads the SAF-T file for a financial year from the SAF-T page in Myntos with «Last ned SAF-T-filen». Use when someone asks how to make, export or download SAF-T from Myntos, for Skatteetaten, an auditor or another accounting program.
pages: /saft/
skill: myntos-saft
---

# Export a SAF-T file

The SAF-T file holds the company's whole accounts for one financial year in
the format Skatteetaten set. Myntos checks the year first and makes the file
only when the check finds no errors. Downloading changes nothing in the books,
and anyone with access to the company may do it.

## They might ask

- «Hvordan lager jeg SAF-T-fil i Myntos?»
- «Hvor laster jeg ned SAF-T?»
- «Revisor vil ha SAF-T for 2025»
- "How do I export SAF-T from Myntos?"

## Before they start

- Any role in the company, «Leser» included.
- Which year is wanted; one file holds one year. Myntos makes files for 2025
  and later.

## Steps

1. Open «Oversikt» in the top menu (`https://myntos.no/`) and, in the note
   under «Største poster i år», click «SAF-T» (`https://myntos.no/saft/`).
2. The page opens on the current year. Use «‹» and «›» beside the year to pick
   the one asked for, usually last year.
3. Read the summary: «Bilag i året», «Sum debet og kredit» and «Versjon».
   Leave the version as it is unless they were asked for a particular one:
   [Choose a SAF-T version](https://myntos.no/llms/help/saf-t/choose-a-saf-t-version.md).
4. Read the check's result. «Kontrollen fant ingenting å utsette på året.»
   means all is well; red messages must be fixed first:
   [Check a SAF-T file](https://myntos.no/llms/help/saf-t/check-a-saf-t-file.md).
5. Press «Last ned SAF-T-filen». The browser saves a file named like
   `SAF-T Financial_<organisasjonsnummer>_<date and time>_1_1.xml`. Keep that
   name: it is the one Skatteetaten prescribes.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Filen lages ikke før feilene over er rettet. …» and no download button | The check found errors | [Check a SAF-T file](https://myntos.no/llms/help/saf-t/check-a-saf-t-file.md) |
| «Selskapet har ikke noe regnskap for … i Myntos.» | No books for that year in Myntos | Pick another year; earlier years come from the program used then |
| «SAF-T for … skal leveres i et eldre format enn Myntos lager; Myntos eksporterer fra 2025» | A year before 2025 | That file must come from the program the books were in then |

## Good to know

- The file contains personal data: names and addresses of customers and
  suppliers, and the name of the person who made it. Send it only to whoever
  asked for it.
- Myntos does not send the file anywhere. For Skatteetaten, the person
  uploads it themselves:
  [Send SAF-T to Skatteetaten](https://myntos.no/llms/help/saf-t/send-saf-t-to-skatteetaten.md).
- A year does not have to be closed to be exported, but if an earlier year was
  not closed in Myntos the check says so in a grey note.

## Related

- [Check a SAF-T file](https://myntos.no/llms/help/saf-t/check-a-saf-t-file.md)
- [Send SAF-T to Skatteetaten](https://myntos.no/llms/help/saf-t/send-saf-t-to-skatteetaten.md)
- [What SAF-T is for](https://myntos.no/llms/help/saf-t/what-saf-t-is-for.md)
- An agent with a key can do this through the API:
  [myntos-saft](https://myntos.no/llms/skills/myntos-saft.md)
