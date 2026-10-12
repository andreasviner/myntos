---
title: File the VAT return by uploading the XML
description: How a person downloads the MVA-melding from Myntos as an XML file («Last ned XML») and uploads it in Skatteetaten's own portal, for when sending from Myntos is not set up or not working. Use when someone sees «Innsending fra Myntos er ikke satt opp her», wants the mva-melding as a file, or wants to file it themselves at Skatteetaten.
pages: /mva/
skill: myntos-mva
---

# File the VAT return by uploading the XML

Myntos can give the melding as a file in the format Skatteetaten reads.
Skatteetaten's own portal for the MVA-melding accepts it as an upload from an
accounting system. That is the way to file when this installation cannot send
it itself, and a way to look at exactly what the melding contains.

## They might ask

- «Det står at innsending ikke er satt opp. Hvordan sender jeg da?»
- «Kan jeg laste ned mva-meldingen som fil?»
- «Kan jeg sende mva-meldingen selv hos Skatteetaten?»
- "How do I download the VAT return file from Myntos?"

## Before they start

- Any role can download the file.
- Uploading it is done at Skatteetaten, with their own login there.

## Steps

1. Open `https://myntos.no/mva/` and click «Se meldingen» on the termin.
2. Press «Last ned XML» at the top right of the card. The file is named
   `mva-melding-<orgnr>-<år>-<termin>.xml`.
3. Open Skatteetaten's page for the MVA-melding (skatteetaten.no), log in,
   choose to upload a melding from an accounting system, and upload the file.
4. Check the figures Skatteetaten shows against the MVA page in Myntos before
   sending there.

## Good to know

- The file is the melding as the books are at the moment of download. If
  anything is entered in the termin afterwards, download it again.
- A melding sent this way is not listed under «Meldinger for terminen» in
  Myntos, and Myntos does not post the settlement against 2740 for it.
- For a melding already sent from Myntos, «Meldingen (XML)» under «Meldinger
  for terminen» is the exact file that was sent.

## Related

- [See what is owed for a VAT period](https://myntos.no/llms/help/vat/see-what-is-owed-for-a-termin.md)
- An agent with a key can fetch the same file: [myntos-mva](https://myntos.no/llms/skills/myntos-mva.md)
