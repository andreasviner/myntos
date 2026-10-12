---
title: Find out why the VAT return cannot be sent
description: Why the Mva-melding page in Myntos shows no button to check or send the VAT return, or shows red messages above the lines, and what to do about each. Use when someone asks why they cannot send the mva-melding, why the button is missing, or what a red message on the MVA page means.
pages: /mva/
skill: myntos-mva
---

# Find out why the VAT return cannot be sent

The MVA page shows the buttons for checking and sending only when a melding
can actually be sent. When something is in the way, it says what above the
lines in red, or the buttons are simply not there. Each cause has its own fix.

## They might ask

- «Hvorfor kan jeg ikke sende mva-meldingen?»
- «Jeg finner ikke knappen for å sende mva»
- «Hva betyr at meldingen og mva-kontoene ikke er enige?»
- "Why is there no send button on the VAT page in Myntos?"

## Steps

1. Open `https://myntos.no/mva/` and click «Se meldingen» on the termin.
2. Read the red lines above the table, if any, and find them below.
3. If there are none and still no button, check the role and the other causes
   in the table.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Terminen slutter …, og meldingen kan sendes fra dagen etter.» | The termin has not ended | Wait until the day after it ends |
| «Selskapet er ikke merket som registrert i Merverdiavgiftsregisteret. …» | The company is marked as not VAT-registered, and an unregistered company sends no melding | If wrong: [Set the VAT registration](https://myntos.no/llms/help/company-and-access/set-vat-registration.md) |
| «Regnskapet har en mva-kode som ikke kan rapporteres ennå.» | An entry in the termin has a VAT code the melding cannot carry; the line table is empty | Find that entry and correct it with a reversal and a new entry |
| «Meldingen og mva-kontoene 2700 og 2710 er ikke enige: de skiller seg med …» | Something was booked to a VAT account without a VAT code | Find that entry before sending; it does not stop the button, but the melding would be wrong |
| «Innsending fra Myntos er ikke satt opp her. …» | This installation cannot file | [File the VAT return by uploading the XML](https://myntos.no/llms/help/vat/file-the-vat-return-by-uploading-the-xml.md) |
| No buttons, no red lines | Their role is «Leser» | Someone with «Medlem» or above must send it |
| «Logg inn med ID-porten» instead of «Kontroller hos Skatteetaten» | Not logged in with ID-porten | [Log in with ID-porten to file the VAT return](https://myntos.no/llms/help/vat/log-in-with-id-porten-for-the-vat-return.md) |
| The box «Send inn» was there and has gone | Something was entered in the termin after the check | Press «Kontroller hos Skatteetaten» again |

## Good to know

- The melding can be read and downloaded («Last ned XML») whatever stops it
  being sent.
- A termin with nothing in it is not a reason to skip it: it is sent with
  zero.

## Related

- [See what is owed for a VAT period](https://myntos.no/llms/help/vat/see-what-is-owed-for-a-termin.md)
- [Send the MVA-melding](https://myntos.no/llms/help/vat/send-the-vat-return.md)
- An agent with a key sees the same causes through the API:
  [myntos-mva](https://myntos.no/llms/skills/myntos-mva.md)
