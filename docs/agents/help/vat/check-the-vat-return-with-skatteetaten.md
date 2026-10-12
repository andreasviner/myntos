---
title: Check the VAT return with Skatteetaten
description: How a person has Skatteetaten check (kontrollere) the VAT return on the Mva-melding page in Myntos before sending it, what a note (merknad) is for, and how to read the answer. Use when someone asks how to check or validate the mva-melding, what «Kontroller hos Skatteetaten» does, or whether checking sends anything.
pages: /mva/
skill: myntos-mva
---

# Check the VAT return with Skatteetaten

Before a melding can be sent, Myntos has Skatteetaten check it. The check
sends nothing in: Skatteetaten only says whether it would accept the melding,
and Myntos keeps exactly what was checked. Only that checked melding can then
be sent.

## They might ask

- «Hvordan kontrollerer jeg mva-meldingen?»
- «Sender jeg inn meldingen når jeg trykker Kontroller?»
- «Hva skal jeg skrive i merknad til Skatteetaten?»
- "How do I validate the VAT return in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- The termin has ended, and nothing on the page stops it (no red lines above
  the table).
- They are logged in with ID-porten:
  [Log in with ID-porten to file the VAT return](https://myntos.no/llms/help/vat/log-in-with-id-porten-for-the-vat-return.md).
- They have looked through the lines and the total:
  [See what is owed for a VAT period](https://myntos.no/llms/help/vat/see-what-is-owed-for-a-termin.md).

## Steps

1. Open `https://myntos.no/mva/` and click «Se meldingen» on the termin.
2. Optional: write in «Merknad til Skatteetaten (valgfritt)». Only when a
   figure needs explaining; the check says so when it wants one.
3. Press «Kontroller hos Skatteetaten». The page says «Sender meldingen til
   kontroll …».
4. A box «Send inn» appears with Skatteetaten's answer:
   - «Skatteetaten fant ingenting å utsette på meldingen.»: it can be sent.
   - «Skatteetaten sier «avvikende skattemelding». Meldingen kan sendes, men
     den kan bli fulgt opp.»: it can be sent, and Skatteetaten may ask about
     it later. The points it found are listed.
   - «Skatteetaten ville avvist denne meldingen. Rett det kontrollen peker på,
     og kontroller på nytt.»: it cannot be sent as it is:
     [Fix a VAT return Skatteetaten would reject](https://myntos.no/llms/help/vat/fix-a-vat-return-skatteetaten-would-reject.md).
5. The check is listed under «Meldinger for terminen» as «Kontrollert, ikke
   sendt» or «Avvist i kontrollen».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Skatteetatens kontroll av mva-meldingen svarte ikke. Prøv igjen om litt.» | Skatteetaten did not answer | Try again in a while |
| «logg inn med ID-porten for å kontrollere og sende mva-meldingen» | The ID-porten login has run out | Reload the page and log in again |
| «terminen slutter …; meldingen kan sendes fra dagen etter» | The termin has not ended | Wait until the day after |

## Good to know

- Nothing is sent by checking. The person sees the answer before deciding.
- If anything is entered or reversed in the termin after the check, the
  checked melding can no longer be sent; check again.
- A check can be repeated as often as needed.

## Related

- [Send the MVA-melding](https://myntos.no/llms/help/vat/send-the-vat-return.md)
- An agent with a key cannot run the check, but can prepare and review the
  melding first: [myntos-mva](https://myntos.no/llms/skills/myntos-mva.md)
