---
title: Delete a company
description: How an owner deletes a company from Myntos on the Slett selskapet page, downloading the SAF-T file for every year first, and why books with anything entered are kept locked for five years (bokføringsloven § 13) before they are deleted for good. Use when someone is done with Myntos for a company, the business has wound up, they moved to another program, or they ask how to delete a company or what happens to its accounts.
pages: /slett-selskap/ /organisasjon/
skill: myntos-selskap
---

# Delete a company

An owner can delete a company from Myntos. What happens depends on whether
anything was ever entered:

- **Nothing entered** (no voucher, bilag, invoice or draft, VAT filing,
  customer or supplier, opening balance): the company is deleted at once and
  cannot be brought back.
- **Anything entered**: the company disappears from Myntos for everyone, but
  the books are **not** deleted yet. Bokføringsloven § 13 says the company
  must keep its accounts for five years after the end of each financial year,
  even after the business has wound up. So Myntos keeps them locked until
  31 December five years after the last year with entries, and deletes them
  for good the day after. Until then an owner can open the company again.

Before deleting, the person should download the SAF-T file for every year:
that is the company's own copy of its accounts.

## They might ask

- «Hvordan sletter jeg et selskap i Myntos?»
- «Vi har lagt ned firmaet, hva gjør jeg med regnskapet i Myntos?»
- «Vi har byttet regnskapsprogram, kan vi slette selskapet?»
- «Hvor lenge tar Myntos vare på regnskapet etter at jeg har slettet det?»
- "How do I delete a company from Myntos?"

## Before they start

- They must be «Eier» of the company. Nobody else sees the button or can use
  the page. See
  [Roles and what each may do](https://myntos.no/llms/help/company-and-access/roles-and-permissions.md).
- **A sign-in within the last ten minutes**, however they sign in (BankID,
  Google, Microsoft or e-mail and password). The page asks for no password.
  If the sign-in is older, the page offers «Logg inn på nytt», which signs
  them out and back in and returns them to the page.
- If a year's SAF-T check reports errors, the file for that year cannot be
  downloaded until they are fixed:
  [Check a SAF-T file](https://myntos.no/llms/help/saf-t/check-a-saf-t-file.md).
- If a SAF-T import from a previous program is being read or posted, wait
  until it has finished.

## Steps

1. Make sure the company to delete is the one shown at the top. If not, switch
   with «Bytt selskap».
2. Open the company's settings page (`https://myntos.no/organisasjon/`) and
   scroll to the bottom. The section «Slett selskapet» explains in two short
   paragraphs what deleting does; press the red «Slett selskapet …» button
   under them. It opens `https://myntos.no/slett-selskap/`, and nothing is
   deleted yet.
3. **«1. Last ned SAF-T-filene først»** (shown when there are entries): one row
   per financial year, «Regnskapsåret 2025» and so on. Press
   «Last ned SAF-T for …» on each. It opens the SAF-T page for that year in a
   new tab; press «Last ned SAF-T-filen» there and keep the file.
4. **«Dette skjer»** lists what will happen: the company disappears for
   everyone, how many others lose their access, how many AI keys stop, the
   date the accounts are kept until, that the company can be opened again
   under «Slettede selskaper» on the account page until then, and that a
   confirmed ownership in Altinn is released. For a company with nothing
   entered it says it is deleted at once.
5. If they signed in more than ten minutes ago, the page shows only «Det er
   mer enn ti minutter siden du logget inn. Logg inn på nytt for å bekrefte at
   det er deg.» and the button «Logg inn på nytt», with no form under it.
   Pressing it signs them out and sends them to the login page; after signing
   in the usual way they come back to this page with the form showing.
6. Tick «Jeg har lastet ned SAF-T-filene selskapet trenger.» (only shown when
   there are files to download).
7. Type the company's name in the field «Skriv … for å bekrefte», which shows
   the name to type. Upper and lower case do not matter.
8. Press «Slett selskapet». («Avbryt» goes back without doing anything.)
9. «Selskapet er slettet» says what was kept and until when. «Til forsiden»
   continues with another company if they have one.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Bare en eier av selskapet kan slette det. …» | They are not «Eier». | Ask an owner, listed under «Medlemmer». |
| «Slett selskapet» stays grey | The SAF-T box is not ticked, or the name does not match. | Fill in what is missing. |
| No form and no «Slett selskapet», only «Logg inn på nytt» | They signed in more than ten minutes ago. | Press «Logg inn på nytt» and come back. |
| «skriv navnet på selskapet akkurat slik det står: …» | The typed name is not the company's. | Type it as shown. |
| «en SAF-T-innlesing leses eller føres akkurat nå. …» | An import from the previous program is running. | Wait a few minutes and press again. |
| «Filen lages ikke før feilene over er rettet. …» on the SAF-T page | That year's check found errors. | [Check a SAF-T file](https://myntos.no/llms/help/saf-t/check-a-saf-t-file.md) |

## Good to know

- **The company must keep its accounts itself.** The law puts the five years
  on the company, and on whoever winds it up, not on Myntos. The SAF-T files
  are its copy. Myntos keeping the locked books is extra safety, not a
  replacement for that copy.
- **SAF-T does not contain the bilag** (the receipts and invoices as PDF or
  image). They stay in Myntos with the locked books. To fetch one later, open
  the company again:
  [Open a deleted company again](https://myntos.no/llms/help/your-account/open-a-deleted-company-again.md).
  A download of all bilag at once is not available yet.
- **Everybody but the owners loses access at once**, pending invitations are
  withdrawn, and every key for an AI agent or program stops for good.
  Opening the company again does not bring them back.
- **The day after the date shown, everything is deleted for good**, including
  the stored bilag files, and cannot be recovered.
- **Starting over works.** The same organisation number can be set up again
  straight away, with fresh, empty books, for instance after trying Myntos
  with the wrong number. The deleted books stay locked beside them until
  their date. To open the old ones again, the new ones have to be deleted
  first.
- Deleting the company does not delete the person's account. To leave Myntos
  completely, delete the account afterwards:
  [Delete your account](https://myntos.no/llms/help/your-account/delete-your-account.md).

## Related

- [Move your books to another program](https://myntos.no/llms/help/saf-t/move-your-books-to-another-program.md)
- [Export a SAF-T file](https://myntos.no/llms/help/saf-t/export-a-saf-t-file.md)
- [Open a deleted company again](https://myntos.no/llms/help/your-account/open-a-deleted-company-again.md)
- No AI key can delete a company; the owner does it on this page.
