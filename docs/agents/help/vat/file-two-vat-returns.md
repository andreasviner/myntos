---
title: File two VAT returns (a farm with other business)
description: How a farm, forest or fishery with other business above 30 000 kroner a year (snow ploughing, a farm shop, machine hire) sets up its second VAT return, the alminnelig melding beside the primærnæring one, on the Mva-melding page in Myntos, chooses which accounts go in it, and switches between the two. Use when someone asks about two mva-meldinger, annen næring, or how to report other business beside the farm.
pages: /mva/
skill: myntos-mva
---

# File two VAT returns (a farm with other business)

A farm, forest or fishery files the primærnæring melding, usually once a year.
Other business beside it may go in that melding only while its sales stay at
or under 30 000 kroner a year. Above that the company files an alminnelig
melding as well, with its own terms, usually every two months. Myntos files
both, and decides which melding a line goes in **by the account it is booked
on**.

## They might ask

- «Vi har brøyting ved siden av gården. Hvordan leverer vi to mva-meldinger?»
- «Hvordan skiller jeg den andre næringen fra gårdsdriften i mva?»
- «Hvilken melding går dieselen i når traktoren brukes til begge deler?»
- "How do I file a second VAT return for my farm's other business?"

## Before they start

- The role «Administrator» or «Eier».
- «Meldingstype» at the top of the page set to «Primærnæring (jord, skog,
  fiske)» ([Change the VAT period or category](https://myntos.no/llms/help/vat/change-the-vat-period-or-category.md)).
- Skatteetaten has registered both kinds of melding for the company. A melding
  of a kind Skatteetaten does not hold is refused there.
- Accounts of their own for the other business, added under «Kontoplan»
  (`https://myntos.no/kontoplan/`): one for its sales, and one for each kind
  of cost that belongs to it alone.

## Steps

1. Open `https://myntos.no/mva/`.
2. Find the card «Den andre meldingen» below the settings.
3. Under «Terminlengde», choose what Skatteetaten decided for the other
   business (usually «Annenhver måned (vanlig)»).
4. Under «Fra og med», the first day the second melding covers.
5. Under «Kontoer», the account numbers the other business's sales and costs
   are booked on, separated by commas (for example «3550, 4060»).
6. Press «Lagre den andre meldingen». The page says «Lagret.», and buttons
   with the two meldinger's names appear above the table of terms. Press one
   to see that melding's terms; each is checked and sent on its own, exactly
   as one melding is ([Send the MVA-melding](https://myntos.no/llms/help/vat/send-the-vat-return.md)).

From then on, book the other business's sales and costs on its own accounts
(an entry on «Kontantstrøm» can pick another account of the same kind).

## Costs both businesses use

**Myntos does not divide a shared cost.** How the input VAT on diesel, a
telephone or a machine used by both businesses is to be split between the two
meldinger is not settled, so Myntos applies no rule of its own: the whole line
goes in the melding of the account it is booked on, and an account not in the
list goes in the main (primærnæring) melding. The person decides, preferably
with their accountant: book the cost on an account of the melding it belongs
to, or book it as two lines on two accounts in the split they settle on. The
page says the same under «Den andre meldingen».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «kontoplanen har ikke konto …» | The account does not exist yet | Add it under «Kontoplan» first |
| «konto 2700 er en mva-konto …» | 2700, 2710 and 2740 hold the VAT itself, not what it was charged on | Choose the accounts the sales and costs are booked on |
| «endringen ville flyttet mva på konto …» | The change would move VAT into or out of a term already sent | Leave the setup. Correct a wrongly booked entry with a reversal and a new entry on the right account |
| No card «Den andre meldingen» | The role is «Leser» or «Medlem», or «Meldingstype» is «Alminnelig næring» | Ask an «Administrator» or «Eier», or change the meldingstype first |

## Good to know

- «Fjern den andre meldingen» goes back to one melding, but only while nothing
  has been checked, sent or marked for the second one.
- Each melding is settled in its own voucher onto 2740, whose text names the
  melding.
- Yearly primærnæring is due 10 April; the alminnelig melding has its own
  deadlines. The table shows each.

## Related

- [Find the VAT deadline](https://myntos.no/llms/help/vat/find-the-vat-deadline.md)
- An agent with a key can set this up and read both meldinger through the API:
  [myntos-mva](https://myntos.no/llms/skills/myntos-mva.md)
