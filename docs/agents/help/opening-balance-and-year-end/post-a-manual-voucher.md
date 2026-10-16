---
title: Post a manual voucher
description: How a person posts a manual voucher (manuelt bilag) in Myntos, typed as accounts with debit and credit and a written reason, on the «Manuelt bilag» page. Use when someone needs a journal entry with no payment behind it, a memorialbilag, a reclassification between accounts or a posting their accountant asked for.
pages: /manuelt-bilag/
skill: myntos-manuelt-bilag
---

# Post a manual voucher

A manual voucher is one voucher (bilag) typed as lines: an account and an
amount in «Debet» or «Kredit» on each, with no bank line. It is for what no
payment describes: year-end adjustments, moving a balance from one account to
another, and corrections. **Once posted it cannot be edited or deleted**; a
wrong one is corrected or reversed, and every voucher stays in the books. Every
manual voucher needs a written reason, because the law requires documentation
of why it is posted.

## They might ask

- «Hvordan fører jeg et manuelt bilag i Myntos?»
- «Hvor lager jeg et memorialbilag / en postering?»
- «Regnskapsføreren ba meg flytte et beløp fra én konto til en annen»
- "How do I make a journal entry in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier». A «Leser» sees the list but
  no form.
- The year the voucher is dated in must be open. A closed year is reopened on
  «Årsavslutning» by an «Administrator» or «Eier»:
  [Reopen a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/reopen-a-financial-year.md).
- The accounts used must be in the kontoplan:
  [Add an account](https://myntos.no/llms/help/chart-of-accounts/add-an-account.md).
- A payment that went through the bank is not a manual voucher: it is
  registered under «Kontantstrøm».

## Steps

1. Open `https://myntos.no/manuelt-bilag/` (from «Oversikt», the link
   «Manuelt bilag» in the note under «Største poster i år»; or from the line
   above the figures on «Årsavslutning»). The arrows ‹ › at the top choose the
   year.
2. In the card «Nytt manuelt bilag», fill in «Dato», «Bilagstekst» (a short
   text such as «Omklassifisering driftskreditt») and «Begrunnelse»: why it is
   posted, at least ten characters. The reason is kept with the voucher and
   cannot be changed later.
3. Fill in one row per account: «Konto» (typing suggests the kontoplan's
   accounts), «Tekst» if the line needs its own, «Kunde/leverandør» on lines
   on 1500 or 2400, «Mva-kode» only if the posting carries VAT, and the amount
   in «Debet» or «Kredit». «Legg til linje» adds a row, «Fjern» removes one.
   Debit and credit must add up to the same.
4. If there is a document behind it (a count list, a calculation, a
   statement), choose it under «Dokumenter».
5. Press «Kontroller». The card «Gjennomgå før føring» shows every line as it
   will be posted, the sums, the reason and any messages. Red messages must
   be fixed with «Tilbake til skjemaet»; grey ones are worth reading.
6. Press «Før bilaget». This cannot be undone. The page says «Bilaget er ført
   som …» with the number, and the voucher appears under «Førte manuelle
   bilag i …».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No form, and «Du kan se de manuelle bilagene, men rollen din kan ikke føre dem.» | Their role is «Leser» | Someone with «Medlem» or more posts it |
| «Regnskapsåret … er avsluttet og tar ikke imot nye bilag. Det gjenåpnes under Årsavslutning.» | The year is closed | Post in the open year, or have an «Administrator» or «Eier» reopen it |
| «Før bilaget» cannot be pressed | Red messages in the review | Read each one; the usual ones are that debit and credit differ, the reason is missing or too short, or an account is not in the kontoplan |
| A red message about 2700 or 2710 | VAT is never typed as its own line | Choose the «Mva-kode» on the line the VAT belongs to, with the amount including VAT |
| A red message about 8300, 8800, 8960 or 8990 | Those are posted by closing the year | Leave them out; see [Close a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/close-a-financial-year.md) |
| «Bilaget er ført som …» followed by «Disse dokumentene ble ikke lagret: …» | The voucher is posted; a file did not upload | Do not post it again. Press «Legg ved dokumenter» on the voucher in the list and choose the file again |

## Good to know

- With a «Mva-kode», the amount on the line is the amount including VAT, and
  Myntos adds the VAT line itself, marked «(mva, lagt til av Myntos)» in the
  review. A voucher with VAT changes that period's VAT return.
- Manual vouchers are numbered in their own series per year and are also
  listed under «Bilag» with everything else that is posted.
- Myntos does not calculate the figures. For an estimate (a provision, a
  write-down), say in «Begrunnelse» how the amount was arrived at.

## Related

- [Make the year-end adjustments](https://myntos.no/llms/help/opening-balance-and-year-end/make-year-end-adjustments.md)
- [Correct a manual voucher](https://myntos.no/llms/help/opening-balance-and-year-end/correct-a-manual-voucher.md)
- [Reverse a manual voucher](https://myntos.no/llms/help/opening-balance-and-year-end/reverse-a-manual-voucher.md)
- An agent with a key can do this through the API:
  [myntos-manuelt-bilag](https://myntos.no/llms/skills/myntos-manuelt-bilag.md)
