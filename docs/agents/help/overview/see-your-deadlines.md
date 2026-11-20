---
title: See your deadlines
description: Where a person in Myntos sees the company's deadlines with Skatteetaten and Brønnøysundregistrene for the coming year («Frister»): each VAT return, the skattemelding, the årsregnskap, the aksjonærregisteroppgave and forskuddsskatt, which of them are met, and which are «Over fristen». Use when someone asks what is due, when the next deadline is, whether they have missed one, or why a deadline shows no status.
pages: /frister/ /
skill: myntos-frister
---

# See your deadlines

«Frister» lists the company's deadlines with Skatteetaten and
Brønnøysundregistrene for the coming year: the VAT return for each period,
the skattemelding, the årsregnskap, the aksjonærregisteroppgave and the
forskuddsskatt terms. Myntos works the dates out from the law and the
company's settings, and says beside each one what it knows: sent from Myntos,
sent from another program, received by Regnskapsregisteret, not sent, or
nothing at all where it cannot know.

## They might ask

- «Hvilke frister har vi det neste året?»
- «Når er neste mva-frist?»
- «Har vi glemt å sende noe?»
- «Hvorfor står det ingen status på forskuddsskatten?»
- "Where do I see my tax deadlines in Myntos?"

## Before they start

Anyone with access to the company sees its deadlines, whatever their role.
Which deadlines appear depends on the company's organisation form and whether
it is registered for VAT, both on the company page.

## Steps

1. On «Oversikt», the card «Neste frister» shows the next three: anything
   «Over fristen» first, then what is still to do, soonest first. Each has its
   date, what it is, and how far off it is («om 34 dager»).
2. Click «Alle frister →» under the card, or «Frister» in the note at the
   bottom of the overview, or open `https://myntos.no/frister/`. When more
   than three are over the deadline, the link says so: «Alle frister, 5 over
   fristen →».
3. The table has a row per deadline, soonest first:
   - «Frist»: the date, and how far off it is.
   - «Hva»: what is due, such as «Mva-melding for 6. termin 2026
     (november-desember)», «Skattemeldingen for 2026» or «Forskuddsskatt for
     2026, 1. termin». Under some, a line in grey says what Myntos cannot do
     or know about it.
   - «Status»: «Over fristen», «Ikke sendt ennå», «Sendt fra Myntos», «Sendt
     fra et annet program» or «Mottatt i Regnskapsregisteret». Empty means
     Myntos cannot know, not that it is missed.
   - A link where the work is done in Myntos: «Til mva-meldingen →» or «Til
     skattemeldingen →». Rows already met have none.
4. Met deadlines from the past are left out; a missed one stays at the top
   for a year.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Over fristen» on a VAT return they sent from another program | The period is inside the books in Myntos and nothing was filed from Myntos | Record it on the VAT return page, and the row goes: [Mark a VAT period as filed from another program](https://myntos.no/llms/help/vat/mark-a-termin-filed-in-another-program.md) |
| No status on a VAT return, with «Perioden er fra før regnskapet i Myntos begynner» | The period is from before the books in Myntos, so the old program may have sent it | Nothing, if it was sent. To have it shown as met, mark it the same way |
| No status on the forskuddsskatt | Myntos does not see payments to Skatteetaten, and the amount is Skatteetaten's | Check the amount and the KID in Skatteetaten's letter or on skatteetaten.no. If the company has no forskuddsskatt, there is nothing to pay |
| «Over fristen» on «Årsregnskapet for …» | Enhetsregisteret had not received the year's accounts when Myntos last asked, after the deadline. The date it asked is under the table | Send the accounts to Regnskapsregisteret. The row goes once the register lists them |
| «Myntos viser foreløpig bare mva-fristene for organisasjonsformen …» | Myntos lists the yearly deadlines only for AS, ASA and ENK so far | The skattemelding and other yearly deadlines for that form are not here; check them with Skatteetaten |
| «Selskapet er ikke registrert i Merverdiavgiftsregisteret, så det har ingen mva-frister.» | The company page says the company is not registered for VAT | If it is registered, an «Administrator» or «Eier» corrects it on the company page |
| «Regnskapet slutter …» | The company is being wound up | The last skattemelding has no fixed date and is not in the list; it is sent under «Skattemelding» |
| «Fristene kunne ikke hentes nå.» on the overview | The list did not load | Reload the page |

## Good to know

- The dates are never later than the law's. A VAT deadline on a Saturday or
  Sunday is already moved to the Monday; a public holiday is not, so the real
  deadline can be a day or two later than the date shown, never earlier.
- Not in the list: the a-melding (the 5th of every month for a company that
  pays wages) and any deadline Skatteetaten has moved for this company. The
  page says so at the bottom.
- The year in a row is the year the duty is about: «Skattemeldingen for
  2026» is due 31 May 2027.
- An ENK's skattemelding and forskuddsskatt are the owner's own. Myntos shows
  the dates; the owner sends and pays them as a person.

## Related

- [Read the overview](https://myntos.no/llms/help/overview/read-the-overview.md)
- [Find the VAT deadline](https://myntos.no/llms/help/vat/find-the-vat-deadline.md)
- [Send the VAT return](https://myntos.no/llms/help/vat/send-the-vat-return.md)
- An agent with a key can read the same list through the API:
  [myntos-frister](https://myntos.no/llms/skills/myntos-frister.md)
