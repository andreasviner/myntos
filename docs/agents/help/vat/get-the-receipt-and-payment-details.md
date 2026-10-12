---
title: Get the receipt and payment details
description: Where a person finds Skatteetaten's receipt (kvittering) for a sent VAT return in Myntos, and the payment details (amount, account number, KID and due date) for paying the VAT. Use when someone asks for the mva-melding receipt, which KID or account to pay the VAT to, or how to fetch the kvittering.
pages: /mva/
skill: myntos-mva
---

# Get the receipt and payment details

After a melding is sent, Skatteetaten sends back three documents: the receipt
(«kvittering»), the payment information, and its final check result. Myntos
fetches them, keeps them with the melding, and reads out what to pay, to which
account, with which KID and by when.

## They might ask

- «Hvor finner jeg kvitteringen for mva-meldingen?»
- «Hvilket KID-nummer skal jeg bruke når jeg betaler mva?»
- «Hvilket kontonummer betaler jeg mvaen til?»
- "Where is the receipt for my VAT return in Myntos?"

## Before they start

- Any role can see and download what has been fetched.
- Fetching a receipt that has not arrived needs «Medlem» or above and an
  ID-porten login.

## Steps

1. Open `https://myntos.no/mva/` and click «Se meldingen» on the termin.
2. Under «Meldinger for terminen», the sent melding shows «Sendt og mottatt»
   or «Sendt, venter på kvittering».
3. The payment details are in a line «Beløp …, frist …, til konto …, KID …».
   Pay that amount to that account with that KID by that date, from the bank.
4. The documents are links under it: «Kvittering (PDF)», «Betalingsinformasjon»
   and «Kontrollresultat». «Meldingen (XML)» is the melding exactly as sent.
5. If it says «Sendt, venter på kvittering», press «Hent kvittering». If the
   link says «Logg inn med ID-porten for å hente kvitteringen», log in first;
   they come back to the same termin.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Hent kvittering» | Their role is «Leser», or everything has been fetched already | Ask a «Medlem» or above, or use the links that are there |
| «Vi fikk ikke hentet kvitteringen.» or a message from Altinn | Skatteetaten has not produced it yet, or Altinn did not answer | Try again later |
| No payment line | The payment information has not been fetched yet | Press «Hent kvittering» |

## Good to know

- The line under the payment details says it: «Betalingen føres som «Oppgjør
  av mva-melding til Skatteetaten».» See
  [Record the VAT payment or refund](https://myntos.no/llms/help/vat/record-the-vat-payment-or-refund.md).
- The receipt is evidence that the melding was filed. Keep it with the books;
  Myntos already does.
- Myntos does not pay anything. The payment is made from the bank.

## Related

- [Send the MVA-melding](https://myntos.no/llms/help/vat/send-the-vat-return.md)
- An agent with a key can read the payment details and download the documents
  Myntos has already fetched: [myntos-mva](https://myntos.no/llms/skills/myntos-mva.md)
