---
title: Change the VAT period or category
description: How an administrator changes how often the company files its VAT return (terminlengde: monthly, every two months or yearly) and the kind of return (meldingstype) on the Mva-melding page in Myntos. Use when someone has been granted monthly or yearly VAT periods by Skatteetaten, is in primærnæring, or asks how to change the mva termin in Myntos.
pages: /mva/
skill: myntos-mva
---

# Change the VAT period or category

How often a company files the MVA-melding is decided by Skatteetaten, not by
the company: every two months is the rule, and monthly or yearly terms are
granted on application. Myntos needs to know which, so that it builds the
right periods with the right deadlines. Change it only when Skatteetaten has
said yes.

## They might ask

- «Vi har fått månedlig mva-termin. Hvordan endrer jeg det?»
- «Hvordan setter jeg årlig mva i Myntos?»
- «Vi driver landbruk, hvilken meldingstype skal vi ha?»
- "How do I switch to yearly VAT returns in Myntos?"

## Before they start

- The role «Administrator» or «Eier». Others see the setting as text, not as
  a choice.
- A decision from Skatteetaten for a monthly or yearly term.

## Steps

1. Open `https://myntos.no/mva/` (from «Oversikt», the card «Merverdiavgift»,
   «Mva-meldingen →»).
2. In the box at the top, under «Terminlengde», choose «Månedlig»,
   «Annenhver måned (vanlig)» or «Årlig».
3. Under «Meldingstype», choose «Alminnelig næring» or «Primærnæring (jord,
   skog, fiske)».
4. There is no save button: the choice is saved as soon as it is made, and the
   page says «Lagret.» The table of termins is rebuilt for the new length.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| The setting is text, not a list | Their role is «Leser» or «Medlem» | Ask an «Administrator» or «Eier» |
| «Vi fikk ikke lagret.» or a message from the server | The change was refused | Try again; the message says what was wrong |

## Good to know

- The page's own hint: «Annenhver måned er det vanlige. Månedlig og årlig
  termin får man etter søknad hos Skatteetaten, så velg det bare når de har
  sagt ja.»
- Changing the length renumbers the termins of every year, and the page then
  only lists meldinger sent under the new length. Meldinger already sent are
  kept, but do not switch back and forth.
- Myntos files only «Alminnelig næring» and «Primærnæring». Other kinds of
  melding (kompensasjon, omvendt avgiftsplikt, VOEC) are not supported.
- These settings are the main melding's. A farm with other business above
  30 000 kroner files a second, alminnelig melding beside it, set up in the
  card «Den andre meldingen»
  ([File two VAT returns](https://myntos.no/llms/help/vat/file-two-vat-returns.md)).
  While both have anything sent, «Meldingstype» cannot be changed: it would
  swap which melding is which.

## Related

- [Find the VAT deadline](https://myntos.no/llms/help/vat/find-the-vat-deadline.md)
- An agent with a key and the right scope can change it through the API:
  [myntos-mva](https://myntos.no/llms/skills/myntos-mva.md)
