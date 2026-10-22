---
title: Get Skatteetaten's answer to the tax return
description: How a person fetches and reads Skatteetaten's answer to a tax return (skattemelding) sent from the Skattemelding page in Myntos, downloads what was sent, and follows a return that waits for the revisor. Use when someone asks whether their skattemelding was received, where the answer from Skatteetaten is, or what «Sendt, venter på revisor» means.
pages: /skattemelding/
skill: myntos-skattemelding
---

# Get Skatteetaten's answer to the tax return

Skatteetaten processes a skattemelding a while after it is sent (after the
revisor has confirmed it, for a company with one) and leaves an answer on it
in Altinn. Myntos fetches the answer when it is sent, if it is ready, and
otherwise on request.

## They might ask

- «Er skattemeldingen mottatt?»
- «Hva svarte Skatteetaten?»
- «Hva betyr "Sendt, venter på revisor"?»
- "Did Skatteetaten receive my tax return?"

## Steps

1. Open `https://myntos.no/skattemelding/` and the year, and look under
   «Skattemeldinger for året».
2. «Sendt og mottatt»: the line «Svar fra Skatteetaten: …» is Skatteetaten's
   own words, and «Svaret fra Skatteetaten» downloads the answer.
3. «Sendt, venter på svar» or «Sendt, venter på revisor»: press «Hent svar fra
   Skatteetaten» (after «Logg inn med ID-porten for å hente svaret» if the
   login has run out). The page says whether it arrived, «Skatteetaten har
   ikke svart ennå. Prøv igjen senere.», or that it still waits for the
   revisor.
4. Every filing has «Næringsspesifikasjonen (XML)», «Skattemeldingen (XML)»
   and «Kontrollresultatet»: exactly what was checked and sent.

## Good to know

- **Waiting for the revisor**: a company whose revisor is registered in
  Enhetsregisteret files a næringsspesifikasjon the revisor confirms. The
  revisor does it in Skatteetaten's own viewer: «Åpne hos Skatteetaten» on the
  filing opens it, and the revisor can reach it from the company's Altinn
  inbox. Nothing more is done in Myntos.
- A correction is a new check and a new send for the same year, which
  replaces the earlier one at Skatteetaten.

## Related

- [Send the tax return](https://myntos.no/llms/help/tax-return/send-the-tax-return.md)
- [myntos-skattemelding](https://myntos.no/llms/skills/myntos-skattemelding.md)
