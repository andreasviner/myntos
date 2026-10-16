---
title: Record that a fixed asset was sold or scrapped
description: How a person records in the anleggsregister on the Anlegg og avskrivning page in Myntos that a driftsmiddel was sold, taken out privately or scrapped, and how to take it back. Use when someone asks how to register the sale of a machine, vehicle or building («salg av driftsmiddel», «avgang», «kassere») in Myntos.
pages: /anlegg/
skill: myntos-anlegg
---

# Record that a fixed asset was sold or scrapped

When an asset leaves the business, the register has to know, because the sale
changes the saldo: in group a to d the price is taken off the group's saldo,
and for a building the gain or loss goes to the gevinst- og tapskonto. The
money itself is recorded separately, where it came in.

## They might ask

- «Jeg har solgt den gamle traktoren, hva gjør jeg i Myntos?»
- «Hvordan fører jeg at en maskin er kassert?»
- "How do I record the sale of an asset in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- Record the money under «Betaling» with the category «Salg av driftsmiddel»,
  on the same balance-sheet account as the asset.

## Steps

1. Open «Anlegg og avskrivning» (`https://myntos.no/anlegg/`).
2. Find the asset under «Driftsmidler» and press «Registrer avgang».
3. Fill in «Dato» (the day ownership passed) and «Salgssum». For an asset
   scrapped, 0; for one taken out for private use, what it would have sold
   for.
4. Press «Registrer avgang». The column «Avgang» shows the date and amount.
5. Recorded by mistake: press «Angre avgang» on the same row.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «avgangen kan ikke være før driftsmidlet ble levert» | The date is before «Levert» | Check the date |
| «avgangen er før …, det første året anleggsregisteret dekker» | The sale is already in the saldo brought from the old program | Nothing to record |
| «endringen ville endret saldoen for …, som er avsluttet» | The year is closed | Reopen it under «Årsavslutning» first |

## Good to know

- «Slett» is for an asset registered by mistake, not for one that was sold.
- The year's depreciation voucher clears what is left of a building that was
  sold; post it after the sale is registered.

## Related

- [Post the year's depreciation](https://myntos.no/llms/help/opening-balance-and-year-end/post-the-years-depreciation.md)
- [Register a fixed asset](https://myntos.no/llms/help/opening-balance-and-year-end/register-a-fixed-asset.md)
- An agent with a key can do this through the API:
  [myntos-anlegg](https://myntos.no/llms/skills/myntos-anlegg.md)
