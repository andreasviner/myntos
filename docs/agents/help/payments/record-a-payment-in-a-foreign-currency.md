---
title: Record a payment in a foreign currency
description: How a person records a receipt or invoice in euro, dollars, pounds or another foreign currency on the Betaling page in Myntos, what the «Regnet om» amount is, and why the amount to enter is what the bank actually charged in kroner. Use when someone asks how to book a foreign receipt, which exchange rate to use, or why the amount field is empty or converted.
pages: /kontantstrom/
skill: myntos-kontantstrom
---

# Record a payment in a foreign currency

The books in Myntos are kept in Norwegian kroner. A receipt in euro, dollars
or pounds is still registered on the «Betaling» page as usual, but the amount
entered is what actually left the bank account in kroner, with the bank's
exchange rate and any card fee included. Myntos helps by converting the
receipt's total at Norges Bank's rate, but that is an estimate: the bank
statement has the real figure.

## They might ask

- «Hvordan fører jeg en kvittering i euro?»
- «Hvilken kurs skal jeg bruke på en faktura i dollar?»
- «Hvorfor er beløpsfeltet tomt for denne kvitteringen?»
- "How do I record a receipt in a foreign currency in Myntos?"

## Before they start

- They need the role «Medlem» or more.
- The bank statement line for the payment, to see the amount in kroner.

## Steps

1. Upload the receipt as in
   [Record a payment from a receipt](https://myntos.no/llms/help/payments/record-a-payment-from-a-receipt.md).
2. Read the sentence under the form. Either:
   - «Bilaget er i EUR. Vi har regnet om … med Norges Banks kurs … til … kr.
     Sjekk mot kontoutskriften før du bokfører: …», and «Beløp inkl. mva» is
     filled in and tagged «Regnet om»; or
   - «Bilaget er i EUR, ikke kroner … Hovedboken føres i kroner, så
     beløpsfeltet står tomt med vilje: …», and «Beløp inkl. mva» is empty.
3. Look up the payment on the bank statement and type that amount in kroner
   in «Beløp inkl. mva», replacing the converted one if it differs.
4. Check the other fields and press «Registrer betaling».

## Good to know

- The converted amount is usually a little lower than what was charged,
  because card issuers add a currency mark-up and sometimes a fee. What was
  charged is what belongs in the books.
- A small difference between the invoice and the payment because the rate
  moved is not a purchase: if it ever has to be booked on its own, the types
  are «Valutatap» and «Valutagevinst».
- A purchase that becomes more than 15 000 kroner once converted may be a
  large purchase that is written off over several years; Myntos says so under
  the form when it is.

## Related

- [Choose what a payment was for](https://myntos.no/llms/help/payments/choose-what-a-payment-was-for.md)
- [Correct a payment](https://myntos.no/llms/help/payments/correct-a-payment.md)
- An agent with a key can do this through the API:
  [myntos-kontantstrom](https://myntos.no/llms/skills/myntos-kontantstrom.md)
