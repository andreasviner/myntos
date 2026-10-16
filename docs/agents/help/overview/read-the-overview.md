---
title: Read the overview
description: What the signed-in front page («Oversikt») in Myntos shows: the company's name and organisasjonsnummer, the year's «Inn i år», «Ut i år» and «Netto», the VAT owed now under «Merverdiavgift», «Siste bevegelser», «Største poster i år», and the links to the VAT return, SAF-T, bank reconciliation, opening balance and year-end. Use when someone asks what the numbers on the Myntos front page mean, why they show zero after importing books, or where to start after signing in.
pages: /
skill: myntos-selskap
---

# Read the overview

After signing in, Myntos opens on «Oversikt»: one company's year at a glance.
It shows which company the figures are about, the money in and out this year,
the VAT owed now, the latest payments, and the largest kinds of income and
cost. The figures are read off the books: the payments registered under
«Betaling», and the vouchers brought over from another program. They are not
a VAT return and not finished accounts, and the page says so at the bottom.

## They might ask

- «Hva betyr tallene på forsiden i Myntos?»
- «Hva er «Netto» på oversikten?»
- «Hvorfor står det «anslått å betale» under merverdiavgift?»
- «Hvorfor står det «Inn i år 0,00» når jeg har hentet regnskapet fra Tripletex?»
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
   On the right, «Regnskapet år for år» opens the result and balance sheet for
   every year, earlier years included
   ([See the accounts for every year](https://myntos.no/llms/help/reports/see-the-accounts-for-every-year.md)),
   and «Registrer betaling» goes to the payment form.
3. The four cards:
   - «Inn i år» and «Ut i år»: money in and out of the bank accounts and cash
     box this calendar year. That is 1920 and 1900, and any account marked as
     a bank account on «Kontoplan».
   - «Netto»: in minus out, with the number of entries under it («14 føringer
     i 2026»).
   - «Merverdiavgift»: the VAT owed now, not this year's total: the periods
     not yet reported, plus the ones reported but not yet paid. «anslått å
     betale» means owed to Skatteetaten, «anslått til gode» means Skatteetaten
     owes the company. «herav … levert og ikke ført betalt» after it is the
     part already filed whose payment has not been registered. When nothing
     has been posted since a VAT deadline passed, the card shows «–» and «ikke
     kjent: regnskapet slutter …»: the payment or refund due then is missing,
     so no figure for today can be read off the books. «Mva-meldingen →» opens
     the VAT return.
4. «Siste bevegelser» lists the latest payments: «Dato», «Beskrivelse»,
   «Type», «Beløp». A corrected payment is one line with «Rettet. 2 eldre
   bilag.» under it; a reversed one is struck through with «Motpostert …
   Går i null.». «Se hele kontantstrømmen →» opens the full register, and
   «Se alle bilag →» every voucher in the books, including those brought over
   from another program, which the payments list never shows.
5. «Største poster i år» lists the largest types of income and cost this
   year, each marked «Inn» or «Ut», with «Antall» and «Sum».
6. The note at the bottom links to the pages that are not in the top menu:
   «Mva-melding», «SAF-T», «Bankavstemming», «Regnskapet år for år», «Hent
   regnskapet fra et annet program», «Inngående balanse» and «Årsavslutning»:
   [Find the pages that are not in the menu](https://myntos.no/llms/help/overview/find-the-pages-not-in-the-menu.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «ubekreftet» next to the company's name | Nobody has yet proved through Altinn that they may act for the company. The books work as before | Click it; it leads to the company page, where an administrator confirms the company with ID-porten |
| «Bekreft e-postadressen din.» | The account's email address is not confirmed | Click the link in the email, or «Send lenken på nytt»: [Confirm your email address](https://myntos.no/llms/help/getting-started/confirm-your-email-address.md) |
| «Ingen betalinger registrert i Myntos ennå.» | No payment registered in Myntos this year; vouchers from another program are on «Bilag» | [Record a payment from a receipt](https://myntos.no/llms/help/payments/record-a-payment-from-a-receipt.md), or [See every voucher in the books](https://myntos.no/llms/help/vouchers/see-every-voucher.md) |
| The wrong company's figures | Another company is selected | [Switch to another company](https://myntos.no/llms/help/overview/switch-company.md) |
| «Ingenting er ført i … ennå» (or «Regnskapet er ført til …») above the cards | Nothing has been posted since a VAT deadline passed, typically after importing only last year from the old program. It does not appear just because a new year began: last year's final term counts until its deadline (10 February for two-monthly terms) | «Se regnskapet år for år» shows the years that are there. To catch up, «Hent fra det gamle programmet» imports the rest, or register the payments and import the bank statements under «Bankavstemming» |
| «–» and «ikke kjent: regnskapet slutter …» under «Merverdiavgift» | Same cause: the VAT payment or refund after that date is not in the books, so the balance would be the old day's | Bring the rest of the year in as above. Once the payment or refund is posted, the card shows a figure again: [Record the VAT payment or refund](https://myntos.no/llms/help/vat/record-the-vat-payment-or-refund.md) |
| «Inn i år 0,00» and «Ut i år 0,00» although the books brought over from another program have payments | The old program kept the bank on another account than 1920, such as a driftskreditt on 2390, and that account is not marked as a bank account | An «Administrator» or «Eier» presses «Merk som bankkonto» on that account under «Kontoplan»: [Mark an account as a bank account](https://myntos.no/llms/help/chart-of-accounts/mark-an-account-as-a-bank-account.md) |

## Good to know

- The VAT figure is read off the books, across all years. A period that has
  been reported still counts until its payment is registered under
  «Betaling». The VAT return itself is per period and is under
  «Mva-meldingen →».
- The overview is one year. Earlier years, also those brought over from
  another program, are under «Regnskapet år for år».
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
