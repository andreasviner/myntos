---
title: Read the overview
description: What the signed-in front page («Oversikt») in Myntos shows: the company's name and organisasjonsnummer, the year's «Inn i år», «Ut i år», «Netto» and «Merverdiavgift», «Siste bevegelser», «Største poster i år», and the links to the VAT return, SAF-T, bank reconciliation, opening balance and year-end. Use when someone asks what the numbers on the Myntos front page mean or where to start after signing in.
pages: /
skill: myntos-selskap
---

# Read the overview

After signing in, Myntos opens on «Oversikt»: one company's year at a glance.
It shows which company the figures are about, the money in and out this year,
the VAT building up, the latest payments, and the largest kinds of income and
cost. The figures summarise the payments registered under «Betaling». They are
not a VAT return and not finished accounts, and the page says so at the
bottom.

## They might ask

- «Hva betyr tallene på forsiden i Myntos?»
- «Hva er «Netto» på oversikten?»
- «Hvorfor står det «anslått å betale» under merverdiavgift?»
- "What does the Myntos dashboard show?"

## Before they start

Anyone with access to a company sees its overview, whatever their role. An
account with no company yet sees «Ingen regnskap ennå» instead:
[See why the overview is empty](https://myntos.no/llms/help/overview/see-why-the-overview-is-empty.md).

## Steps

1. Click «Oversikt» in the top menu, or open `https://myntos.no/`.
2. At the top: «Hei, …» with their name, then the company's name as the
   heading and its organisasjonsnummer under it. With access to more than one
   company, «Bytt selskap» is next to the number:
   [Switch to another company](https://myntos.no/llms/help/overview/switch-company.md).
   The button «Registrer betaling» on the right goes to the payment form.
3. The four cards are for this calendar year:
   - «Inn i år» and «Ut i år»: money in and out of the bank account and cash
     box.
   - «Netto»: in minus out, with the number of entries under it («14 føringer
     i 2026»).
   - «Merverdiavgift»: the VAT charged on sales minus the VAT that may be
     deducted on purchases. «anslått å betale» means owed to Skatteetaten,
     «anslått til gode» means Skatteetaten owes the company. «Mva-meldingen →»
     opens the VAT return.
4. «Siste bevegelser» lists the latest payments: «Dato», «Beskrivelse»,
   «Type», «Beløp». A corrected payment is one line with «Rettet. 2 eldre
   bilag.» under it; a reversed one is struck through with «Motpostert …
   Går i null.». «Se hele kontantstrømmen →» opens the full register.
5. «Største poster i år» lists the largest types of income and cost this
   year, each marked «Inn» or «Ut», with «Antall» and «Sum».
6. The note at the bottom links to the pages that are not in the top menu:
   «Mva-melding», «SAF-T», «Bankavstemming», «Inngående balanse» and
   «Årsavslutning»:
   [Find the pages that are not in the menu](https://myntos.no/llms/help/overview/find-the-pages-not-in-the-menu.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «ubekreftet» next to the company's name | Nobody has yet proved through Altinn that they may act for the company. The books work as before | Click it; it leads to the company page, where an administrator confirms the company with ID-porten |
| «Bekreft e-postadressen din.» | The account's email address is not confirmed | Click the link in the email, or «Send lenken på nytt»: [Confirm your email address](https://myntos.no/llms/help/getting-started/confirm-your-email-address.md) |
| «Ingenting ført ennå. Registrer den første betalingen.» | No payments in this company yet | [Record a payment from a receipt](https://myntos.no/llms/help/payments/record-a-payment-from-a-receipt.md) |
| The wrong company's figures | Another company is selected | [Switch to another company](https://myntos.no/llms/help/overview/switch-company.md) |

## Good to know

- The VAT figure is an estimate for the whole year, read off the payments. The
  VAT return itself is per period and is under «Mva-meldingen →».
- A reversed payment and its reversal are left out of the figures, and a
  corrected payment counts once.
- An invoice issued under «Faktura» is booked when it is issued, but it is not
  a payment, so it does not appear in «Siste bevegelser».

## Related

- [Understand the figures beside the register](https://myntos.no/llms/help/payments/understand-the-payment-figures.md)
- [Read the payment register](https://myntos.no/llms/help/payments/read-the-payment-register.md)
- An agent with a key can read the same figures through the API:
  [myntos-selskap](https://myntos.no/llms/skills/myntos-selskap.md) and
  [myntos-kontantstrom](https://myntos.no/llms/skills/myntos-kontantstrom.md)
