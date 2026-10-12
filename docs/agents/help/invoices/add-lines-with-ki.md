---
title: Let KI write the lines
description: How a person describes a sale in their own words on the Faktura page in Myntos and gets the invoice lines proposed by KI, and why the box may be missing. Use when someone asks whether Myntos can fill in invoice lines for them, or where the KI box on the invoice is.
pages: /faktura/
skill: myntos-faktura
---

# Let KI write the lines

Above the invoice lines there is a box where the person writes what they sold
in a sentence, and Myntos's own KI sets the lines up: description, quantity,
unit, price and VAT rate. It only fills in the form. Nothing is saved or
issued until the person has checked the lines and issued the invoice
themselves, as with lines they typed.

## They might ask

- «Kan Myntos fylle ut fakturalinjene for meg?»
- «Hvor er KI-feltet på fakturaen?»
- «Hvordan bruker jeg KI til å lage faktura?»
- "Can Myntos write the invoice lines from a description?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- KI switched on for the company. It is on unless someone ticked «Slå av KI
  for …» on the «KI» page (`https://myntos.no/ki/`).

## Steps

1. Open «Faktura» in the top menu (`https://myntos.no/faktura/`).
2. Under «Linjer», find «Beskriv salget, så setter vi opp linjene» and write
   what was sold, how much and at what price, for example «10 timer rådgivning
   i mars til 1 500 kroner timen, og to kursmapper à 450».
3. Press «Foreslå linjer». The page says «Setter opp linjene …».
4. The lines appear marked «Foreslått av KI», and a message says how many were
   added. If the form already had lines, the new ones are added below them.
5. Check every line: «Hva er solgt?», «Antall», the unit, «Pris» and the VAT
   rate. Change or «Fjern» what is wrong. Then carry on:
   [Issue an invoice](https://myntos.no/llms/help/invoices/issue-an-invoice.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No box at all | KI is switched off for the company, Myntos has no model available, or their role is «Leser» | Write the lines by hand, or have KI switched on again on the «KI» page |
| «Vi fikk ikke svar fra Løynd nå. Skriv linjene selv, eller prøv igjen.» | The model did not answer | Try again in a moment, or type the lines |
| «Vi fikk ikke laget fakturalinjer av beskrivelsen. …» | The description could not be turned into lines | Say what was sold, how much and the price, or type the lines |
| «Beskrivelsen nevner priser inkludert mva. …» | Lines are priced without VAT | Check every «Pris»: it must be the price before VAT |
| «Mva-satsen på minst én linje er satt til … fordi forslaget ikke var tydelig.» | The VAT rate was a guess | Choose the right rate on each line |

## Good to know

- The description goes to Løynd, Myntos's own model, which runs in Norway.
  «Hva ser modellen?» next to the button explains what it sees.
- Prices on invoice lines are always without VAT. The VAT is added at the
  bottom.
- A company outside the VAT register only gets lines with «Ikke
  mva-registrert».
- The person issuing the invoice is responsible for what it says, whoever
  wrote the lines.

## Related

- [Choose the VAT rate on a line](https://myntos.no/llms/help/invoices/choose-the-vat-rate-on-a-line.md)
- [Copy the lines from a previous invoice](https://myntos.no/llms/help/invoices/copy-lines-from-a-previous-invoice.md)
- An agent with a key can ask for the same proposal through the API:
  [myntos-faktura](https://myntos.no/llms/skills/myntos-faktura.md)
