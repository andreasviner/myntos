---
title: Read the payment register
description: What the register on the Betaling page in Myntos shows: the columns «Dato», «Bilag», «Beskrivelse», «Beløp», «Bilagsfil», the details behind «Vis info», greyed-out reversed payments, folded corrections with «Rettet» and «Motpostert», and how many entries it lists. Use when someone asks how to see what has been booked, what a line in the register means, or how to find a payment.
pages: /kontantstrom/
skill: myntos-kontantstrom
---

# Read the payment register

Below the form on the «Betaling» page is the register: every payment in and
out of the company's bank account and cash box, newest first. Nothing ever
disappears from it, because a posted entry stays in the books by law. A
corrected or reversed payment therefore leaves several entries behind, and the
register folds them into one line so one payment does not look like three.

## They might ask

- «Hvor ser jeg alt jeg har ført?»
- «Hva betyr «Rettet. 2 eldre bilag.»?»
- «Hvorfor er en betaling gjennomstreket?»
- «Hvilken mva-kode ble brukt på denne betalingen?»
- "Where do I see all my transactions in Myntos?"

## Before they start

Anyone with access to the company can read the register, whatever their
role. It is the register of the company named at the top right of the screen.

## Steps

1. Open «Betaling» in the top menu (`https://myntos.no/kontantstrom/`) and
   scroll below the form. The overview's «Se hele kontantstrømmen →» leads
   here too.
2. Each line is one payment:
   - «Dato»: the day the money moved.
   - «Bilag»: the bilag number, such as «2026-0014», counted from 0001 each
     year.
   - «Beskrivelse»: what it was for.
   - «Beløp»: the amount, VAT included, coloured for money in and money out.
   - «Bilagsfil»: the attached files, or «Legg ved bilag» when there are none.
3. Click «Vis info» at the end of a line for the rest: «Motpart», «Type»,
   «Konto», «Netto», «Mva», «Mva-kode», «Betalt via» («Bankkonto» or
   «Kontant»), «Ført av», and the list of files. «Skjul info» closes it.
4. A line with a small triangle in front of the date stands for several
   entries. The sentence under its description says which kind:
   - «Rettet. 2 eldre bilag.»: the payment was corrected. The line shows the
     corrected payment; the triangle shows the original and its reversal.
   - «Motpostert av bilag 2026-0022. Går i null.»: the payment was reversed.
     The line is greyed out and struck through, and with its reversal it comes
     to zero.

## Good to know

- There is no search or filter on the page. The register lists the 200 most
  recent entries, across all years; older ones are still in the books.
- A greyed-out, struck-through line is still part of the books. It is shown,
  not hidden, because the law wants the record to show what was undone.
- The payments here are the ones that moved money through the bank account or
  cash box. An invoice issued under «Faktura» is not listed: it is booked when
  it is issued and moves no money by itself.
- «Rediger» and «Reverser» appear only for «Medlem» and above, and only on a
  payment that still stands.

## Related

- [Understand the figures beside the register](https://myntos.no/llms/help/payments/understand-the-payment-figures.md)
- [See who recorded a payment](https://myntos.no/llms/help/payments/see-who-recorded-a-payment.md)
- [See the documents for a payment](https://myntos.no/llms/help/payments/see-the-documents-for-a-payment.md)
- An agent with a key can read the register through the API:
  [myntos-kontantstrom](https://myntos.no/llms/skills/myntos-kontantstrom.md)
