---
title: Fix a VAT return Skatteetaten would reject
description: What a person does in Myntos when Skatteetaten's check of the VAT return says it would reject it («Avvist i kontrollen»), or accepts it with remarks («avvikende skattemelding»). Use when someone asks why their mva-melding was rejected or «avvist», or what a finding from Skatteetaten's check means.
pages: /mva/
skill: myntos-mva
---

# Fix a VAT return Skatteetaten would reject

When Skatteetaten's check finds something it would refuse, the melding is
marked «Avvist i kontrollen» and cannot be sent. Nothing has been filed. The
melding is built from the books, so the fix is in the entries behind it, never
in the melding itself; then it is checked again.

## They might ask

- «Mva-meldingen ble avvist i kontrollen. Hva gjør jeg?»
- «Hva betyr avvikende skattemelding?»
- «Skatteetaten sier noe om kode 1, hva er galt?»
- "Skatteetaten rejected my VAT return in Myntos, what now?"

## Before they start

The role «Medlem» or above, to correct entries and check again.

## Steps

1. Open `https://myntos.no/mva/` and click «Se meldingen» on the termin.
2. The box «Send inn» says «Skatteetaten ville avvist denne meldingen. Rett
   det kontrollen peker på, og kontroller på nytt.», with a list of what the
   check found. A point about one VAT code starts «Kode …:».
3. Find the entries behind that code in the termin. A payment entered with the
   wrong VAT code is corrected on «Betaling» (correct or reverse it, then
   enter it again); an invoice with a credit note; a VAT adjustment by
   reversing it.
4. Back on the MVA page, the lines and totals have changed. Press
   «Kontroller hos Skatteetaten» again (log in with ID-porten again if the
   button «Logg inn med ID-porten» is back).
5. When the box says the melding can be sent, send it:
   [Send the MVA-melding](https://myntos.no/llms/help/vat/send-the-vat-return.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| The same finding after a new check | The entries behind it are unchanged | Look again at the code it names, or ask an accountant |
| A finding that the company is not registered for this kind of melding | Skatteetaten's VAT register does not have the company with this filing duty | Check the company's registration with Skatteetaten, and «Meldingstype» on the MVA page |
| A finding asking for an explanation | A figure is unusual | Write it in «Merknad til Skatteetaten (valgfritt)» and check again |

## Good to know

- «Avvikende skattemelding» is not a rejection: the melding may be sent, and
  Skatteetaten may follow it up. The box says so and offers sending.
- Every check is kept under «Meldinger for terminen», with «Kontrollresultat»
  (Skatteetaten's answer as a file) once the melding is sent.
- Posted entries are never edited away: corrections are reversals and new
  entries, which is what bokføringsloven requires.

## Related

- [Check the VAT return with Skatteetaten](https://myntos.no/llms/help/vat/check-the-vat-return-with-skatteetaten.md)
- [Reverse a VAT adjustment](https://myntos.no/llms/help/vat/reverse-a-vat-adjustment.md)
- An agent with a key can read the findings and help find the entries:
  [myntos-mva](https://myntos.no/llms/skills/myntos-mva.md)
