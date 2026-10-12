---
title: See what is owed for a VAT period
description: How a person sees, on the Mva-melding page in Myntos, what the VAT return (mva-melding) for a period (termin) comes to, line by line, and whether it is money to pay or a refund. Use when someone asks how much VAT they owe, what goes into the mva-melding, or where to find the VAT figures in Myntos.
pages: /mva/
skill: myntos-mva
---

# See what is owed for a VAT period

A VAT-registered company reports its VAT to Skatteetaten once per period, a
«termin», in the MVA-melding (the VAT return). Myntos builds it from what has
been entered in the books, VAT code by VAT code, so there is nothing to type
in: the page shows what the melding will say. To change a figure, the entry
behind it is corrected; the melding is never edited directly.

## They might ask

- «Hvor mye mva skal jeg betale denne terminen?»
- «Hvor ser jeg mva-meldingen i Myntos?»
- «Får vi penger tilbake på mvaen?»
- "How much VAT do I owe this period in Myntos?"

## Before they start

- Any role can read the page («Leser» and up).
- The company named at the top of the page is the one shown; check it is the
  right one.

## Steps

1. Open «Oversikt» in the top menu (`https://myntos.no/`), and in the card
   «Merverdiavgift» click «Mva-meldingen →». Or go straight to
   `https://myntos.no/mva/`.
2. The table lists every termin of the year: «Termin», «Frist», «Beløp»,
   «Status». «Beløp» is followed by «å betale» (owed to Skatteetaten) or
   «til gode» (a refund to the company). Use ‹ and › at the top right to
   change the year.
3. Click «Se meldingen» on the termin they want. A card opens below with the
   termin's dates and «Frist for melding og betaling».
4. The table in the card has one line per VAT code: «Kode», «Hva»,
   «Grunnlag» (the amount the VAT was calculated on), «Sats» and «Mva».
   Lines marked «(fradrag)» are VAT on purchases, which is deducted.
5. Under it: «Utgående mva» (VAT on sales), «Inngående mva» (VAT on
   purchases), and the result, «Å betale» or «Til gode».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Selskapet er ikke merket som registrert i Merverdiavgiftsregisteret, og da sendes det ingen mva-melding.» | The company is marked as not VAT-registered | If that is wrong: [Set the VAT registration](https://myntos.no/llms/help/company-and-access/set-vat-registration.md) |
| «Ingenting å rapportere i terminen. Meldingen skal likevel sendes, med null.» | Nothing with VAT was entered in the termin | The melding must still be sent, with zero |
| «Meldingen og mva-kontoene 2700 og 2710 er ikke enige: …» | Something was booked to a VAT account without a VAT code | Find that entry before sending: [Find out why the VAT return cannot be sent](https://myntos.no/llms/help/vat/find-out-why-the-vat-return-cannot-be-sent.md) |

## Good to know

- The figures follow the books as they are now. An entry posted or reversed
  later changes them, also for a termin already sent; the page then marks it
  «Endret etter innsending».
- An entry counts in the termin of its posting date.
- Uttak, tap på krav and the other VAT adjustments come in on lines of their
  own; they are entered on a separate page, linked in the text at the top
  («på en egen side»).

## Related

- [Find the VAT deadline](https://myntos.no/llms/help/vat/find-the-vat-deadline.md)
- [Send the MVA-melding](https://myntos.no/llms/help/vat/send-the-vat-return.md)
- An agent with a key can read the melding through the API:
  [myntos-mva](https://myntos.no/llms/skills/myntos-mva.md)
