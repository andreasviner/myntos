---
title: Check a SAF-T file
description: Explains the check Myntos runs on the SAF-T page before it makes the file, what each red error and grey note means, and how to fix the errors that stop the download. Use when someone asks why they cannot download SAF-T, why «Last ned SAF-T-filen» is missing, or what a message on the SAF-T page means.
pages: /saft/
skill: myntos-saft
---

# Check a SAF-T file

Altinn only checks that a SAF-T file has the right shape. An auditor checks
more: that every account, customer, supplier and VAT code an entry points to
is in the file, that every entry balances, that the opening balance adds up to
zero, and that every account has its reporting code. Myntos runs those checks
on the «SAF-T» page before it makes the file, and **will not make the file
while any red error stands**, because a file that passes Altinn and fails at
the audit is worse than none. Grey notes do not stop the download.

## They might ask

- «Hvorfor kan jeg ikke laste ned SAF-T-filen?»
- «Hva betyr "har ingen næringsspesifikasjonskode"?»
- «Myntos sier at filen ikke lages før feilene er rettet»
- "Why won't Myntos let me download the SAF-T file?"

## Before they start

They are on `https://myntos.no/saft/` with the year chosen. Reading the check
needs any role; fixing what it finds may need more (see the table).

## Steps

1. Read the red messages under the summary, then the grey ones.
2. Fix each red one as the table says. Most come from the chart of accounts:
   open «kontoplanen» from the link in the text at the top of the SAF-T page
   (`https://myntos.no/kontoplan/`).
3. Come back to the SAF-T page, or press «‹» and «›» to the year again, and
   the check runs again.
4. When «Kontrollen fant ingenting å utsette på året.» or only grey notes
   remain, «Last ned SAF-T-filen» appears.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Konto … har ingen næringsspesifikasjonskode for ….» | An account is missing its post in the business specification (næringsspesifikasjon) for that year | On «Kontoplan», choose the year, press «Endre post» on the account, pick a post and «Lagre». Needs «Administrator» or «Eier» |
| «Konto … er knyttet til …, som ikke finnes i Skatteetatens liste for ….» | The account's post is not in that year's official list | Choose another post with «Endre post». A closed year's posts are locked; it must be reopened first |
| «Inngående balanse for … går ikke i null (differanse …).» | The year's opening balance does not add up | Check the opening balance on «Inngående balanse» |
| «… er lengre enn 35 tegn.» or «… er for lang for SAF-T.» | A number or reference is longer than the format allows; Myntos refuses rather than cut it | Shorten it where it was typed (a customer number, a document reference); otherwise contact Myntos |
| «Bilag … går ikke i null …», «Hovedboken går ikke i null …», or a message that an entry points at something «som ikke er i» the file | Something is inconsistent in the books | Contact Myntos with the message; this should not happen |
| Grey: «Resultatet for tidligere år er ikke disponert i Myntos, …» | An earlier year was not closed in Myntos | The file is still fine; closing that year on «Årsavslutning» places the result properly |
| Grey: «Inngående balanse for … kommer fra åpningsbilaget …» | The opening balance appears as opening balances, not among the entries | Nothing to do; it explains the file to whoever reads it |
| Grey: «Skatteetaten har ikke publisert en grupperingsliste for … som Myntos kjenner til, …» | The official list for that year is not out yet | Nothing to do now |
| Grey: «… er ført som positiv egenkapital, men har debetsaldo ved årets slutt. …» | Equity is negative | Mention it to the accountant; they may change the post |

## Good to know

- The check runs again at the moment of download, so the file always matches
  the books as they are then.
- Changing a post in «Kontoplan» does not touch any entry; it only decides
  where the account is reported.

## Related

- [Export a SAF-T file](https://myntos.no/llms/help/saf-t/export-a-saf-t-file.md)
- [Change an account's post](https://myntos.no/llms/help/chart-of-accounts/change-an-accounts-post.md)
- [What SAF-T is for](https://myntos.no/llms/help/saf-t/what-saf-t-is-for.md)
- An agent with a key can read the same check through the API:
  [myntos-saft](https://myntos.no/llms/skills/myntos-saft.md)
