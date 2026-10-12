---
title: Send SAF-T to Skatteetaten
description: How a person hands the SAF-T file from Myntos to Skatteetaten when asked, by uploading it themselves in Altinn on form RF-1363. Use when someone has a letter from Skatteetaten asking for SAF-T, or is in a tax audit (bokettersyn) and asks how to send the file.
pages: /saft/
skill: myntos-saft
---

# Send SAF-T to Skatteetaten

Skatteetaten asks for the SAF-T file when it wants to look at the books,
usually at a tax audit (bokettersyn). The file is made in Myntos and then
**uploaded by the person themselves** in Altinn, on form RF-1363, logged in
with their own ID-porten. Myntos does not send it, and no AI with a Myntos key
can either.

## They might ask

- «Skatteetaten ber om SAF-T-filen, hvordan sender jeg den?»
- «Hvor i Altinn laster jeg opp SAF-T?»
- «Hva er RF-1363?»
- "How do I send SAF-T to Skatteetaten?"

## Before they start

- The year Skatteetaten asks for, from their letter.
- Someone who may act for the company in Altinn (usually the daglig leder,
  the chair, or someone they have given the right to).
- The file, downloaded from Myntos:
  [Export a SAF-T file](https://myntos.no/llms/help/saf-t/export-a-saf-t-file.md).

## Steps

1. In Myntos, open `https://myntos.no/saft/`, pick the year with «‹» and «›»,
   and press «Last ned SAF-T-filen».
2. Do not rename the file. Zipping it is recommended by Skatteetaten,
   especially if it is large.
3. Log in to Altinn (`https://www.altinn.no/`) and choose to act for the
   company.
4. Find form RF-1363 (search for «RF-1363» or «SAF-T»), fill in what it asks,
   and attach the file. Each attachment may be up to 200 MB.
5. Send the form, and keep Altinn's receipt.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Last ned SAF-T-filen» in Myntos | The check found errors | [Check a SAF-T file](https://myntos.no/llms/help/saf-t/check-a-saf-t-file.md) |
| The year asked for is before 2025, or before the company came to Myntos | Myntos has no books, or no file format, for it | Get that year's file from the program the books were in then |
| They cannot act for the company in Altinn | They lack the right in Altinn | Ask whoever holds it, usually the daglig leder or chair; this is Altinn's, not Myntos's |

## Good to know

- One file holds one financial year. If several years are asked for, download
  each.
- The file holds personal data about customers and suppliers. Send it only
  through Altinn, not by e-mail, unless Skatteetaten asks otherwise.
- If their own AI helps, it can check and download the file with a key, but
  the upload in Altinn is always theirs.

## Related

- [Export a SAF-T file](https://myntos.no/llms/help/saf-t/export-a-saf-t-file.md)
- [What SAF-T is for](https://myntos.no/llms/help/saf-t/what-saf-t-is-for.md)
- An agent with a key can prepare the file, never send it:
  [myntos-saft](https://myntos.no/llms/skills/myntos-saft.md)
