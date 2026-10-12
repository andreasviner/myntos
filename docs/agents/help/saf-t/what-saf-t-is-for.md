---
title: What SAF-T is for
description: Explains what a SAF-T file is, who needs one, when Skatteetaten asks for it and what it is used for (a tax audit, an auditor, moving to another accounting program), and that Myntos never sends it on its own. Use when someone asks what SAF-T is, whether they must send it every year, or where it is in Myntos.
pages: /saft/
skill: myntos-saft
---

# What SAF-T is for

SAF-T Financial is a standard file format, set by Skatteetaten (the Norwegian
Tax Administration), that holds a company's whole accounts for one financial
year: the chart of accounts with opening and closing balances, customers and
suppliers, VAT codes, and every entry. Every Norwegian company whose books are
kept electronically must be able to produce it. **It is not sent in on a
schedule**: the company makes the file when someone asks for it, and in Myntos
that takes a few seconds on the «SAF-T» page.

## They might ask

- «Hva er SAF-T?»
- «Må jeg sende SAF-T til Skatteetaten hvert år?»
- «Hvor finner jeg SAF-T i Myntos?»
- "What is a SAF-T file and do I need to send it?"

## Before they start

Nothing; this explains what the file is.

## Steps

1. The file is made on `https://myntos.no/saft/`. It is not in the top menu:
   open «Oversikt» (`https://myntos.no/`) and, in the note under «Største
   poster i år», click «SAF-T».
2. When they need it, follow
   [Export a SAF-T file](https://myntos.no/llms/help/saf-t/export-a-saf-t-file.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| Nothing to do yet | Nobody has asked for the file | Nothing; there is no deadline and no yearly filing |

## Good to know

When a company needs the file:

- **Skatteetaten asks for it**, usually at a tax audit (bokettersyn). The
  person downloads it and uploads it in Altinn on form RF-1363:
  [Send SAF-T to Skatteetaten](https://myntos.no/llms/help/saf-t/send-saf-t-to-skatteetaten.md).
- **An auditor (revisor) or accountant asks for it.** Someone with only the
  «Leser» role can download it.
- **The books move to another program.** Most Norwegian accounting programs
  can read it:
  [Move your books to another program](https://myntos.no/llms/help/saf-t/move-your-books-to-another-program.md).

Also:

- Myntos makes files for years from 2025 on.
- Myntos checks the file before making it, beyond what Altinn checks, and
  refuses to make a file that would be rejected at an audit:
  [Check a SAF-T file](https://myntos.no/llms/help/saf-t/check-a-saf-t-file.md).
- The file contains personal data (names and addresses of customers and
  suppliers). Send it only to whoever asked for it.

## Related

- [Export a SAF-T file](https://myntos.no/llms/help/saf-t/export-a-saf-t-file.md)
- [Send SAF-T to Skatteetaten](https://myntos.no/llms/help/saf-t/send-saf-t-to-skatteetaten.md)
- An agent with a key can check and download it through the API:
  [myntos-saft](https://myntos.no/llms/skills/myntos-saft.md)
