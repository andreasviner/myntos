---
title: Understand the VAT settlement voucher
description: What the voucher «Mva-oppgjør» that Myntos posts when a VAT return is sent does, moving accounts 2700 and 2710 onto the settlement account 2740, and why nobody should post it by hand. Use when someone asks what the mva-oppgjør voucher is, why 2700 and 2710 went to zero, what account 2740 is, what the øre on 7740 Øreavrunding are, or whether to book the VAT settlement themselves.
pages: /mva/
skill: myntos-mva
---

# Understand the VAT settlement voucher

Through a termin, VAT on sales collects on account 2700 (utgående mva) and
VAT on purchases on 2710 (inngående mva). Once the melding is sent, those two
become one amount owed to or by Skatteetaten. Myntos then posts one voucher by
itself, «Mva-oppgjør» followed by the termin, that moves the termin's 2700 and
2710 onto 2740, the VAT settlement account. The payment to Skatteetaten (or
the refund) then clears 2740. Nobody needs to post this, and nobody should.

The melding is filed in whole kroner, because Skatteetaten does not accept
øre in it. So 2740 gets the amount in whole kroner, exactly what Skatteetaten
asks for, and the øre left over go to account 7740 «Øreavrunding» in the same
voucher. That is usually under a krone or two.

## They might ask

- «Hva er bilaget Mva-oppgjør?»
- «Må jeg føre mva-oppgjøret selv?»
- «Hvorfor står det et beløp på konto 2740?»
- «Hva er de få kronene på konto 7740 Øreavrunding?»
- «Hvorfor er mva-meldingen noen øre annerledes enn mva-kontoene?»
- "What is the VAT settlement entry Myntos made?"

## Before they start

Nothing: it happens on its own when a melding is sent.

## Steps

1. Send the melding: [Send the MVA-melding](https://myntos.no/llms/help/vat/send-the-vat-return.md).
   The voucher is posted at the same moment, dated the last day of the
   termin, in a journal of its own («Mva-oppgjør»).
2. Record the payment or refund with its own category:
   [Record the VAT payment or refund](https://myntos.no/llms/help/vat/record-the-vat-payment-or-refund.md).
   2740 is then back to zero for the termin.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| Under «Meldinger for terminen», a melding marked «Sendt» with a text ending «Meldingen er sendt, men oppgjøret mot 2740 ble ikke ført: …» | The melding was filed, but the settlement voucher could not be posted (for example because the year is closed) | The filing stands. Write to kontakt@myntos.no with the message; do not post the settlement by hand and do not send the melding again for this |

## Good to know

- A corrected melding for the same termin settles only the difference, so a
  correction does not move the VAT twice. The same goes for the øre on 7740.
- Each line of the melding is rounded to the nearest krone, so the amount to
  pay can be a krone or two away from 2700 less 2710. The page does not count
  that as a disagreement.
- The year's figures on «Oversikt» leave this voucher out, so a settled termin
  still shows the VAT it had.
- Do not post anything to 2700, 2710, 2740 or 7740 by hand. An entry on 2700 or 2710
  without a VAT code makes the MVA page say «Meldingen og mva-kontoene 2700 og
  2710 er ikke enige».

## Related

- [See what is owed for a VAT period](https://myntos.no/llms/help/vat/see-what-is-owed-for-a-termin.md)
- An agent with a key can read which voucher settled a melding:
  [myntos-mva](https://myntos.no/llms/skills/myntos-mva.md)
