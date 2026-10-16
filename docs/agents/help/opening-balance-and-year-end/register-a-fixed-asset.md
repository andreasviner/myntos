---
title: Register a fixed asset
description: How a person registers a driftsmiddel (a machine, vehicle, building, computer) in the anleggsregister on the Anlegg og avskrivning page in Myntos, including a public investment grant and an asset brought from the previous program. Use when someone asks how to register a purchase that is to be depreciated, «aktivere», «føre inn i anleggsregisteret» or «registrere driftsmiddel» in Myntos.
pages: /anlegg/
skill: myntos-anlegg
---

# Register a fixed asset

Something bought to keep for at least three years that cost 30 000 kroner or
more is a driftsmiddel (skatteloven § 14-40, from 2024). It is not a cost in
the year it is bought: it goes into the anleggsregister, and part of it is
deducted each year as depreciation. Registering it posts nothing; the payment
is recorded on its own, normally under «Betaling» with the category «Større
anskaffelse (driftsmiddel)».

## They might ask

- «Hvordan registrerer jeg en traktor i anleggsregisteret?»
- «Hvor fører jeg inn en maskin som skal avskrives?»
- «Vi fikk tilskudd fra Innovasjon Norge til fjøset, hvordan fører jeg det?»
- "How do I add a fixed asset in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- The account the asset stands on (1000 to 1299) must be in the kontoplan; if
  it is not: [Add an account](https://myntos.no/llms/help/chart-of-accounts/add-an-account.md).

## Steps

1. Open «Anlegg og avskrivning» (`https://myntos.no/anlegg/`). It is linked
   from the note at the bottom of «Oversikt» and from «Årsavslutning».
2. Press «Registrer driftsmiddel» on the card «Driftsmidler».
3. Fill in «Beskrivelse» and choose the «Saldogruppe». Tractors, machines,
   cars and furniture are group d; vans and lorries c; computers a; buildings
   h. For a livestock building choose group h and «Husdyrbygg i jord- og
   skogbruk» under «Forhøyet sats».
4. Fill in «Konto», «Levert» (the day it was delivered, not ordered or paid)
   and «Kostpris» (without the VAT the company gets back).
5. A public grant goes in «Offentlig tilskudd». If it is Innovasjon Norge's
   grant to fixed farm installations or distriktsrettet investeringsstøtte,
   tick «Tilskuddet er skattefritt og trekkes ikke fra kostprisen …»; the
   grant letter says which it is.
6. If the asset was delivered before the first year the register covers, and
   is a building or another asset with its own saldo, the form asks for
   «Skattemessig verdi ved registerets start»: its value in last year's saldo
   specification.
7. Press «Lagre». The asset appears in the list; «Endre» changes it later.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «konto … finnes ikke i kontoplanen» | The account is not set up | Add it under «Kontoplan», or use one that exists |
| «konto … er ikke en konto for anleggsmidler» | Driftsmidler stand on 1000-1299 | Choose an asset account |
| «tilskuddet kan ikke være større enn kostprisen» | The grant is larger than the price | Check both amounts |
| «forhøyet sats … gjelder bare saldogruppe h» | A raised rate in another group | Choose group h, or «Vanlig sats» |
| «endringen ville endret saldoen for …, som er avsluttet» | The year it was delivered is closed | Reopen the year under «Årsavslutning» if it really belongs there |

## Good to know

- Under 30 000 kroner it may be expensed at once instead; Myntos says so in
  the review but does not refuse it.
- Group a to d have one saldo for the whole group; a building has its own.

## Related

- [Record that a fixed asset was sold or scrapped](https://myntos.no/llms/help/opening-balance-and-year-end/record-the-sale-of-a-fixed-asset.md)
- [Post the year's depreciation](https://myntos.no/llms/help/opening-balance-and-year-end/post-the-years-depreciation.md)
- An agent with a key can do this through the API:
  [myntos-anlegg](https://myntos.no/llms/skills/myntos-anlegg.md)
