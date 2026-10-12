---
title: Handle a submission that stopped partway
description: What a person does when sending the VAT return from Myntos stops partway («Stoppet underveis», «Innsendingen stoppet underveis»), so it is unclear whether Skatteetaten received it. Use when someone asks whether their mva-melding was sent after an error, or what «Stoppet underveis» means on the MVA page.
pages: /mva/
skill: myntos-mva
---

# Handle a submission that stopped partway

Sending goes through several steps in Altinn. If one of them fails, Myntos
stops, keeps a record of how far it got, and marks the melding «Stoppet
underveis». The melding may or may not have reached Altinn, so the first job
is to find out, not to press send again.

## They might ask

- «Det sto Innsendingen stoppet underveis. Er mva-meldingen sendt?»
- «Hva betyr Stoppet underveis?»
- "My VAT return failed halfway in Myntos. Was it filed?"

## Before they start

The role «Medlem» or above, and an ID-porten login, to check and send again.

## Steps

1. Open `https://myntos.no/mva/` and click «Se meldingen» on the termin.
2. Under «Meldinger for terminen», the melding is marked «Stoppet underveis»,
   with «Innsendingen stoppet underveis. Sjekk i Altinn om meldingen ligger der
   før du prøver igjen. Feilen: …».
3. Log in to Altinn (`altinn.no`) for the company and look for the
   MVA-melding for that termin.
4. If it is there and sent, it was filed. Do not send again.
5. If it is not there, or it is there unfinished, go back to the MVA page,
   press «Kontroller hos Skatteetaten» again, and send the new check:
   [Send the MVA-melding](https://myntos.no/llms/help/vat/send-the-vat-return.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «denne meldingen kan ikke sendes: … den er allerede sendt eller stoppet underveis; kontroller på nytt» | The stopped melding cannot be sent again as it is | Check again first; the new check can be sent |
| The same error again | Altinn or Skatteetaten has a problem | Wait and try later; the deadline is still the deadline |

## Good to know

- Myntos keeps the stopped attempt on purpose: if it did reach Altinn,
  somebody has to be able to find it.
- A message about «oppgjøret mot 2740» on a melding marked «Sendt» is
  different: the melding was sent, but the settlement voucher was not posted.
  See [Understand the VAT settlement voucher](https://myntos.no/llms/help/vat/understand-the-vat-settlement-voucher.md).

## Related

- [Send the MVA-melding](https://myntos.no/llms/help/vat/send-the-vat-return.md)
- [File the VAT return by uploading the XML](https://myntos.no/llms/help/vat/file-the-vat-return-by-uploading-the-xml.md)
- An agent with a key can see the status, but cannot send:
  [myntos-mva](https://myntos.no/llms/skills/myntos-mva.md)
