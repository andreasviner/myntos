---
title: Review an opening balance
description: Explains the «Gjennomgå før føring» step of the opening balance in Myntos, what each red error and grey note means, and how to fix each before posting. Use when someone has pressed «Kontroller» on the Inngående balanse page and asks why they cannot post, or what a message in the review means.
pages: /inngaende-balanse/
skill: myntos-inngaende-balanse
---

# Review an opening balance

Before an opening balance (inngående balanse) is posted, Myntos checks it and
shows the result under «Gjennomgå før føring»: every line as it will be
posted, the totals, and what is wrong with it. Nothing is posted by the check.
**Red messages block posting**; grey ones are worth reading but may be right.
Once «Før inngående balanse» is pressed, the balance cannot be edited, only
corrected with a reversal and a new one, so this is the moment to look.

## They might ask

- «Hvorfor kan jeg ikke trykke Før inngående balanse?»
- «Myntos sier at debet og kredit ikke går i null»
- «Hva betyr "er en resultatkonto" i inngående balanse?»
- "Why won't Myntos let me post my opening balance?"

## Before they start

They have filled in the form or loaded a file and pressed «Kontroller» on
`https://myntos.no/inngaende-balanse/`.

## Steps

1. Read the table: «Konto», «Tekst», «Kunde/leverandør», «Debet», «Kredit».
   An account shown as «(ukjent konto)» is not in the company's chart of
   accounts.
2. Check the «Sum» row: debit and credit must be the same.
3. Read the messages under the table, red first, using the table below.
4. To change anything, press «Tilbake til skjemaet», fix the rows, and press
   «Kontroller» again. The form keeps what they typed.
5. When no red message is left, «Før inngående balanse» can be pressed. It
   posts exactly the lines shown in the review.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Debet … og kredit … går ikke i null (differanse …). …» | A balance sheet always balances, so an account is missing or on the wrong side | Compare line by line with the old saldobalanse; the difference often equals one account |
| «Linje … (konto …): … er en resultatkonto. …» | An income or cost account; the old year's result must already be on equity | Use the saldobalanse taken after the old year was closed |
| «Linje … (konto …): mva som ikke var gjort opp ved årsskiftet føres på 2740 …» | VAT on 2700 or 2710 | Move the amount to 2740 |
| «Linje … (konto …): kontoen finnes ikke i kontoplanen. …» | The account does not exist in Myntos | Add it under «Kontoplan» («Legg til konto»), or use the account it belongs with |
| «Linje … (konto …): et beløp kan ikke være negativt; før det på motsatt side i stedet.» | A minus sign | Put the amount, without the minus, in the other column |
| «… en linje har beløp enten i debet eller i kredit, ikke begge.» | Both columns filled on one row | Keep one side |
| «… linjen har ikke noe beløp.» | An account with no amount | Fill it in, or «Fjern» the row |
| «Konto … er delvis fordelt på kunder eller leverandører. …» | Some lines on 1500 or 2400 name a party, some do not | All or none: [Split customers and suppliers in the opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/split-customers-and-suppliers-in-the-opening-balance.md) |
| «… avregnes mot en annen konto enn …» | A customer on 2400 or a supplier on 1500 | Customers on 1500, suppliers on 2400 |
| «Inngående balanse har ingen linjer.» | The form is empty | Fill it in or load a file |
| Grey: «Saldoen på 1500 er ikke fordelt på kunder. …» (or 2400, leverandører) | One total instead of per invoice | Allowed; split it if they want payments matched to invoices |

## Good to know

- The review is Myntos's own check, run again when they press «Før inngående
  balanse», so the button and the rule never disagree.
- If the review says this replaces a posted balance («Dette erstatter den
  førte balansen. …»), posting reverses the old one and posts the new one
  together.

## Related

- [Enter an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/enter-an-opening-balance.md)
- [Import a saldobalanse](https://myntos.no/llms/help/opening-balance-and-year-end/import-a-saldobalanse.md)
- An agent with a key can run the same check through the API:
  [myntos-inngaende-balanse](https://myntos.no/llms/skills/myntos-inngaende-balanse.md)
