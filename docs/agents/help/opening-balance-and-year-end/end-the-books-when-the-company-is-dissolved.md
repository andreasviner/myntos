---
title: End the books when the company is dissolved
description: How an owner or administrator ends a dissolved aksjeselskap's books on its last day on the Årsavslutning page in Myntos («Er selskapet oppløst?», «Avslutt regnskapet ved oppløsningen»), so the last year can be closed at once and its tax return sent early as a forhåndsfastsetting. Use when someone says the company has been wound up, dissolved, liquidated or deleted (oppløst, avviklet, slettet) and asks what to do with the books.
pages: /arsavslutning/
skill: myntos-arsavslutning
---

# End the books when the company is dissolved

When an aksjeselskap has been dissolved (oppløst), its debts paid and what
was left paid out to the shareholders, its last financial year ends on the
day the last thing happened, not on 31 December. Myntos needs that day: the
year can then be closed the day after, and the tax return for the year is
sent straight away, before the company is deleted, as a
**forhåndsfastsetting** (skatteforvaltningsloven § 8-2), not the spring
after. Nothing can be posted after the day, and no new year is opened.

## They might ask

- «Selskapet er oppløst. Hva gjør jeg med regnskapet i Myntos?»
- «Hvordan avslutter jeg regnskapet når selskapet legges ned?»
- «Vi har avviklet aksjeselskapet, hvordan leverer vi siste skattemelding?»
- "The company has been wound up; how do I close the books?"

## Before they start

- The role «Administrator» or «Eier».
- Everything that happened in the company is posted, **including the payout
  to the shareholders**, usually as a manual voucher (debit the equity,
  credit the bank):
  [Post a manual voucher](https://myntos.no/llms/help/opening-balance-and-year-end/post-a-manual-voucher.md).
  Nothing can be dated after the last day.
- A company set up in Myntos only for this, after Brønnøysund deleted it,
  first needs the opening balance for the year:
  [Enter an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/enter-an-opening-balance.md).

## Steps

1. Open «Årsavslutning» (`https://myntos.no/arsavslutning/`) on the year the
   company was dissolved. Use «‹» and «›» beside the year if it opens on
   another.
2. At the bottom, in the card «Oppløsning», open «Er selskapet oppløst?».
3. Under «Siste dag i regnskapet», choose the day the last thing happened in
   the company, usually the payout to the shareholders. It is not
   necessarily the day Brønnøysundregistrene deleted the company, which is
   often weeks later.
4. Tick «Selskapet er oppløst.» and press «Avslutt regnskapet ved
   oppløsningen».
5. The page says «Regnskapet slutter …. Avslutt året, og send så
   skattemeldingen som forhåndsfastsetting.», the card says «Selskapet er
   oppløst, og regnskapet slutter …», and «Logg» lists it.
6. Close the year as usual, the day after the last day at the earliest:
   [Close a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/close-a-financial-year.md).
   The postings are dated the last day, and a note says this is the
   company's last year. If the note «Et oppløst selskap skal ikke eie eller
   skylde noe. Disse kontoene har fortsatt saldo: …» appears, something is
   still on the bank or another account: usually the payout is not posted
   yet.
7. Then the tax return:
   [File the tax return when the company is dissolved](https://myntos.no/llms/help/tax-return/file-the-tax-return-when-the-company-is-dissolved.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| The card «Oppløsning» is not there | The year shown is closed, a later year exists, or their role is «Medlem» or «Leser» | Go to the latest open year; ask an «Administrator» or «Eier» |
| «Velg siste dag i regnskapet.» / «Bekreft at selskapet er oppløst.» | The date or the tick is missing | Fill in both |
| «… bilag er datert etter …, det første …. Regnskapet kan ikke slutte før det siste som er ført» | Something is posted after the chosen day | Choose a later day, or correct the entry that is dated wrong |
| «siste dag i regnskapet kan ikke være etter i dag. …» | The day is in the future | Wait, or choose the right day |
| «regnskapet kan ikke slutte …, årets første dag. …» | 1 January was chosen | A company that did nothing in the year ended in the one before: choose 31 December of that year |
| «selskapet har et regnskapsår … i Myntos, etter …» | A later year has been opened, with entries in it | The company did not end in this year; check the date |
| «regnskapsåret … er avsluttet. Gjenåpne det …» | The year is already closed | [Reopen a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/reopen-a-financial-year.md), then end the books |
| «selskapet er oppløst, og regnskapet sluttet …; det går ikke an å føre etter det» (on another page) | Someone tried to post after the last day | Date it on or before the last day, or undo the ending if the day was wrong |

## Good to know

- **A day chosen by mistake can be undone** with «Angre avslutningen ved
  oppløsning» in the same card, while the year is open and before the
  forhåndsfastsetting has been sent. The year then runs to 31 December
  again. To pick another day, undo and choose again.
- **Myntos does not dissolve the company.** The resolution, the notice to
  the creditors and the deletion are reported to Brønnøysundregistrene, not
  from Myntos. Skatteetaten only accepts a forhåndsfastsetting once
  Enhetsregisteret has registered the company as dissolved or deleted.
- **The books must still be kept** for five years after the last year
  (bokføringsloven § 13), even though the company is gone.

## Related

- [File the tax return when the company is dissolved](https://myntos.no/llms/help/tax-return/file-the-tax-return-when-the-company-is-dissolved.md)
- [Close a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/close-a-financial-year.md)
- An agent with a key can do this through the API:
  [myntos-arsavslutning](https://myntos.no/llms/skills/myntos-arsavslutning.md)
