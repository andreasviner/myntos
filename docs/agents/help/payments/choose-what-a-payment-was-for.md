---
title: Choose what a payment was for
description: How a person picks the right type in «Hva gjaldt det?» on the Betaling page in Myntos, including refunds, credit notes, money the owner puts in or takes out, loans, VAT settlements and large purchases, and what the type decides (account and VAT code). Use when someone asks which category to choose, how to record a refund, an owner's deposit or withdrawal, or why the VAT code changed.
pages: /kontantstrom/
skill: myntos-kontantstrom
---

# Choose what a payment was for

The bank statement says how much moved; it does not say why. The type chosen
in «Hva gjaldt det?» is that answer, and it decides two things the person
would otherwise have to know: which account the amount is booked to (and
through that, where it lands in the tax return), and which VAT code applies
(and so what goes into the VAT return, the MVA-melding). The line under the
list explains what each type assumes. A wrong type is fixed afterwards with
«Rediger», never by deleting.

## They might ask

- «Hvilken type skal jeg velge for denne betalingen?»
- «Hvordan fører jeg at jeg fikk pengene tilbake fra en leverandør?»
- «Hvordan fører jeg penger jeg har satt inn i selskapet selv?»
- «Hvordan fører jeg at jeg tok ut penger til meg selv?»
- "How do I record a refund in Myntos?"

## Before they start

They need the role «Medlem» or more, and the form open on
`https://myntos.no/kontantstrom/`, either with a bilag uploaded or after
«Ingen bilag? Fyll inn manuelt».

## Steps

1. Choose «Penger inn» or «Penger ut» first. Until then the list says «Velg
   penger inn eller ut først».
2. Open «Hva gjaldt det?» and choose the type. Refunds and credit notes are in
   their own group at the bottom of the list (see below).
3. Read the line under the list. It says what the type assumes, for example
   that the purchase carries 25 % VAT that may be deducted.
4. Check «Mva-kode». It is set from the type («Foreslått ut fra typen. Endre
   hvis fakturaen sier noe annet.»). Change it only if the receipt shows a
   different VAT rate, or none.
5. If a «Konto» field appears, the company has added accounts of the same
   kind to its chart of accounts, and one of those can be chosen instead of
   the type's own. Otherwise the field does not show.

## Common situations

| The payment | Direction | Choose |
| --- | --- | --- |
| A customer paid for goods or services, with no invoice issued in Myntos | «Penger inn» | «Salg med 25 % mva» (or 15 %, 12 %, «Salg uten mva», «Salg til utlandet (utførsel)») |
| A supplier refunded a purchase | «Penger inn» | In the group «Penger tilbake på et kjøp», the same type the purchase was booked as |
| The company paid money back to a customer | «Penger ut» | In the group «Kreditnota til en kunde», the same type the sale was booked as |
| The owner put their own money in | «Penger inn» | «Innskudd fra eier» |
| The owner took money out for themselves | «Penger ut» | «Uttak til eier» |
| A loan was paid out to the company | «Penger inn» | «Nytt lån» |
| A loan instalment was paid | «Penger ut» | «Avdrag på lån» (the interest part as «Renter og lånekostnad») |
| The VAT return was paid to Skatteetaten | «Penger ut» | «Oppgjør av mva-melding til Skatteetaten» |
| Skatteetaten paid out VAT | «Penger inn» | «Utbetaling av mva-melding fra Skatteetaten» |
| A purchase from 15 000 kr that will last three years or more (a machine, a computer) | «Penger ut» | «Større anskaffelse (driftsmiddel)» |
| Bank or card fees | «Penger ut» | «Bank- og kortgebyr» |
| Something that fits nowhere else | either | «Annen inntekt» or «Annen driftskostnad», and check «Mva-kode» yourself |

## Good to know

- **A customer paying an invoice issued in Myntos is not a new sale.** The
  invoice already booked the sale and its VAT when it was issued under
  «Faktura». Registering the payment here as «Salg med 25 % mva» books both a
  second time. Myntos has no type for «the customer paid our invoice» yet, so
  ask an accountant before registering such a payment here.
- Money back is not income, and money given back is not a cost. Booking a
  supplier's refund as «Annen inntekt» leaves the cost and the VAT deduction
  standing; the refund type reverses both.
- The owner's own money is neither income nor cost, and is not taxed as
  either. «Innskudd fra eier» and «Uttak til eier» keep it out of the
  company's result.
- A large purchase booked as a «Større anskaffelse (driftsmiddel)» is not a
  cost this year: it is written off over the years it lasts. When a bilag
  shows an expense over 15 000 kroner, Myntos says so under the form.
- A company that is not registered for VAT cannot deduct VAT. Myntos then
  proposes «0: Ingen merverdiavgiftsbehandling» for every type, and does not
  offer «Mva betalt ved innførsel (tollpass)» at all.
- Myntos's AI may have proposed the type («Foreslått av KI» next to the
  field). It is a proposal; the person decides.

## Related

- [Record a payment from a receipt](https://myntos.no/llms/help/payments/record-a-payment-from-a-receipt.md)
- [Correct a payment](https://myntos.no/llms/help/payments/correct-a-payment.md)
- An agent with a key can do this through the API:
  [myntos-kontantstrom](https://myntos.no/llms/skills/myntos-kontantstrom.md)
