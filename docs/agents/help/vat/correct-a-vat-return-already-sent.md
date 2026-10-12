---
title: Correct a VAT return already sent
description: How a person corrects an MVA-melding that has already been sent from Myntos, by correcting the entries and sending a new melding for the same period, and what «Endret etter innsending» means. Use when someone found a mistake after sending the mva-melding, asks how to send a corrected (korrigert) mva-melding, or sees «Endret etter innsending».
pages: /mva/
skill: myntos-mva
---

# Correct a VAT return already sent

A sent melding cannot be withdrawn or edited. It is corrected by sending a new
melding for the same termin, which replaces the earlier one at Skatteetaten.
In Myntos the new melding comes from the books, so the order is: correct the
entries, then check and send the termin again.

## They might ask

- «Jeg har sendt feil mva-melding. Hvordan retter jeg den?»
- «Hvordan sender jeg en korrigert mva-melding?»
- «Hva betyr Endret etter innsending?»
- "How do I correct a VAT return I already filed in Myntos?"

## Before they start

- The role «Medlem» or above, ID-porten, and the right to report VAT for the
  company in Altinn, as for any sending.
- They know which entry was wrong.

## Steps

1. Correct the entry in the books, in the termin it belongs to: a payment on
   «Betaling» (correct or reverse it), an invoice with a credit note, a VAT
   adjustment by reversing it and entering it again. A reversal lands in the
   same termin as the entry it reverses.
2. Open `https://myntos.no/mva/`. The termin is marked «Endret etter
   innsending», and in the card: «Regnskapet for terminen er endret etter at
   meldingen ble sendt. Tallene under er slik de er nå. Send en ny melding for
   terminen, så erstatter den den forrige.»
3. Click «Se meldingen» and read the new lines and total.
4. Log in with ID-porten, press «Kontroller hos Skatteetaten», tick the box
   and send: [Send the MVA-melding](https://myntos.no/llms/help/vat/send-the-vat-return.md).
5. Both meldinger stay listed under «Meldinger for terminen»; the newest is
   the one that counts.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «regnskapsåret … er avsluttet» when correcting the entry | The year is closed | An administrator must reopen the year under «Årsavslutning» first |
| «Endret etter innsending» on a termin they did not mean to change | Something dated in that termin was entered or reversed later | Find the entry; if it belongs there, send a new melding; if not, correct its date |

## Good to know

- The new melding replaces the old one entirely; it is not a difference.
- Myntos's settlement against 2740 moves only the difference, so the books
  stay right without anything posted by hand.
- If the correction means more to pay, the extra amount is paid like the
  first; the new payment details come with the new receipt.

## Related

- [Get the receipt and payment details](https://myntos.no/llms/help/vat/get-the-receipt-and-payment-details.md)
- [Reverse a VAT adjustment](https://myntos.no/llms/help/vat/reverse-a-vat-adjustment.md)
- An agent with a key can find what changed since the melding was sent, but
  cannot send: [myntos-mva](https://myntos.no/llms/skills/myntos-mva.md)
