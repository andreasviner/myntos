---
title: Split a payment into several lines
description: How a person registers one payment that is several things at once - a settlement with deductions, a Vipps or Stripe payout less fees, a receipt with both 15 % and 25 % VAT, a loan instalment that is interest and repayment - with «Del opp i flere linjer» on the Betaling page in Myntos. Use when someone asks how to split a payment, book a settlement or oppgjør, or register two VAT rates on one receipt.
pages: /kontantstrom/
skill: myntos-kontantstrom
---

# Split a payment into several lines

Some payments are several things netted into one bank line: a settlement
(oppgjør) is a sale less what the buyer deducted, a payout from Vipps or Stripe
is the day's sales less the fee, one receipt from a shop has food at 15 % VAT
and other goods at 25 %, an instalment on a loan is interest and repayment.
Registered under one type, each of these is wrong somewhere. «Del opp i flere
linjer» registers the payment as one bilag with one line per thing, each with
its own type, amount and VAT code, and Myntos works out what they add up to.
It is for every business, not only farms.

## They might ask

- «Hvordan fører jeg et oppgjør med trekk i Myntos?»
- «Vipps-utbetalingen er salg minus gebyr, hvordan fører jeg den?»
- «Kvitteringen har både mat og andre varer, med to mva-satser»
- «Kan jeg dele opp en betaling i flere linjer?»
- "How do I split one payment into several lines in Myntos?"

## Before they start

- They need the role «Medlem», «Administrator» or «Eier».
- The bilag (the settlement, the payout report or the receipt) at hand, with
  the amount of every line. Each amount is entered **with** VAT, as printed.

## Steps

1. Open «Betaling» in the top menu (`https://myntos.no/kontantstrom/`).
2. Upload the bilag as usual and let Myntos read it, or click «Ingen bilag?
   Fyll inn manuelt». The date, «Motpart» and «Beskrivelse» apply to the whole
   payment.
3. Under «Hva gjaldt det?», click «Del opp i flere linjer». The type, the
   amount and the VAT code already chosen become the first line, and an empty
   second line is added under the heading «Linjer».
4. For each line, choose «Hva gjaldt linjen?», fill in «Beløp inkl. mva» and
   check «Mva-kode». The type list has both «Penger inn» and «Penger ut»: in a
   settlement the sale is money in and each deduction money out. Each line's
   «Mva-kode» shows only the codes for its own type's side, sale or purchase.
   When a type
   can go to another account, a «Konto» list appears on that line. When the
   company has avdelinger, prosjekter or driftsgrener, each line also has a
   list for each of them («Avdeling», «Driftsgren» …), so the milk can go to
   one driftsgren and the feed deducted to another; «Ingen» leaves the line on
   none.
5. Click «Legg til linje» for each further line, and «Fjern» to take one away.
6. Read the sum under the lines: «Til sammen 6 800,00 kr inn på kontoen. Sjekk
   at det stemmer med det banken viser.» It must match the bank statement.
7. Press «Registrer betaling». The payment is posted at once as one bilag, and
   cannot be deleted afterwards.
8. To go back to one type and one amount before posting, click «Tilbake til én
   linje».

In the register the payment is one row with the amount that moved. «Vis info»
on it shows «Sammensatt bilag, 4 linjer» and, under «Linjer», each line with its
account, VAT code and amount, + for money in and − for money out.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Linjene går i null. Et bilag må flytte penger inn eller ut.» | The lines cancel each other out, so no money moved | Check the amounts against the bilag and the bank |
| «Velg hva linje 2 gjaldt.» | A line has no type | Choose one, or click «Fjern» on that line |
| «Skriv beløpet på linje 2, større enn null.» | A line has no amount, or a negative one | Enter the amount without a minus; the type decides whether it adds or subtracts |
| «Et oppdelt bilag trenger minst to linjer.» | Only one line is left | Add a line, or click «Tilbake til én linje» |
| «typen hører til landbrukstillegget, og det er ikke slått på for dette selskapet …» | A farm type was chosen in a company without the farming add-on | Choose another type, or [turn on farming](https://myntos.no/llms/help/farming/turn-on-farming.md) |

## Good to know

- Every amount is positive. A «Penger ut» type subtracts from the payment, a
  «Penger inn» type adds to it, so a sale of 12 500 kr with 600 kr deducted is
  two lines, 12 500 and 600, and the bank line is 11 900 kr in.
- Each line's VAT goes where its type says. The fee on a Vipps payout and the
  feed set off on a settlement are purchases; the deductions marked «Trekk:»
  reduce the sale itself.
- A payment registered as one type can be split afterwards: click «Rediger» on
  its row, then «Del opp i flere linjer», and «Lagre endringer». As with any
  correction, the books keep the original, its reversal and the new bilag.

## Related

- [Record a payment from a receipt](https://myntos.no/llms/help/payments/record-a-payment-from-a-receipt.md)
- [Correct a payment](https://myntos.no/llms/help/payments/correct-a-payment.md)
- [Book a settlement from the dairy or slaughterhouse](https://myntos.no/llms/help/farming/book-a-settlement.md)
- An agent with a key can do this through the API:
  [myntos-kontantstrom](https://myntos.no/llms/skills/myntos-kontantstrom.md)
