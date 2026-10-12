---
title: What closing a year locks
description: Explains what a closed financial year in Myntos stops (payments, invoices, corrections, VAT settlement, the opening balance and account groupings dated in that year), what it does not affect, and how to undo it. Use when someone asks what happens when they close the year, or why Myntos refuses an entry with «regnskapsåret … er avsluttet».
pages: /arsavslutning/
skill: myntos-arsavslutning
---

# What closing a year locks

Closing a year on «Årsavslutning» posts the result onto equity and then locks
the year, so the figures the next year starts from cannot drift. The lock is
for everyone with access to the company's books, not only the person who
closed it. It is not permanent: an «Administrator» or «Eier» can reopen the
year with a written reason, as long as the annual accounts have not been
adopted.

## They might ask

- «Hva skjer når jeg avslutter året i Myntos?»
- «Myntos sier "regnskapsåret 2025 er avsluttet" når jeg fører en kvittering»
- «Kan jeg fakturere med fjorårets dato etter årsavslutningen?»
- "What does closing the year lock in Myntos?"

## Before they start

Nothing; this explains what the lock means.

## Steps

1. To see whether a year is closed, open `https://myntos.no/arsavslutning/`
   and choose the year with «‹» and «›». A closed year shows «Regnskapsåret …
   er avsluttet» and the card «Året er avsluttet» with the date and time.
2. «Logg» lists every close and reopening of that year.
3. To post something in a closed year anyway, reopen it first:
   [Reopen a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/reopen-a-financial-year.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «regnskapsåret … er avsluttet» when saving a payment, invoice or correction | The date is in a closed year | Use a date in an open year if that is correct, or have the year reopened |
| On «Kontoplan»: «Regnskapsåret er avsluttet. Næringsspesifikasjonen for det er levert med disse postene, så de endres ikke her. …» | A closed year's account groupings are fixed | Reopen the year if a grouping was wrong |
| On «Inngående balanse»: «Regnskapsåret … er avsluttet, og inngående balanse kan bare føres i et åpent år. …» | The opening balance is in the first year, now closed | Reopen that year to correct the opening balance |

## Good to know

What a closed year stops, for everyone:

- Anything new dated in that year: payments and receipts, invoices and credit
  notes, vouchers for bank lines, VAT adjustments.
- Corrections (reversals) of the year's entries.
- Settling the year's VAT periods. Settle the last period before closing.
- Posting, correcting or withdrawing the opening balance, if it is the first
  year.
- Changing the year's account groupings (post i næringsspesifikasjonen) in
  «Kontoplan».

What it does not do:

- It does not file anything: the annual accounts (årsregnskap) and the tax
  return (skattemelding) are separate.
- It does not stop reading: the year's figures, reports and the SAF-T file are
  all still there.
- It does not touch the next year, which is opened at the same moment.

## Related

- [Close a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/close-a-financial-year.md)
- [Reopen a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/reopen-a-financial-year.md)
- An agent with a key reads which years are closed through the API:
  [myntos-arsavslutning](https://myntos.no/llms/skills/myntos-arsavslutning.md)
