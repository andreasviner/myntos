# MVA-melding (VAT return)

Verified against official sources 2026-07-18. Canonical docs:
`skatteetaten.github.io/api-dokumentasjon/anvendelsesomraader/mvamelding`
(note: the older `skatteetaten.github.io/mva-meldingen` site carries a
"SIDEN ER UTDATERT" banner but still hosts the implementation guide, XSDs and
code lists via `github.com/Skatteetaten/mva-meldingen`).

## Model: SAF-T codes, not form posts

Since 2022-01-01 the MVA-melding is a **dynamic list of
`MvaSpesifikasjonslinje`** keyed on (simplified) SAF-T standard VAT codes —
the old numbered posts (RF-0002/0004) are gone; ignore any doc that mentions
them. Root element `mvaMeldingDto`; categories (`meldingskategori`):
`alminnelig`, `primaernaering`, `kompensasjon`, `omvendtAvgiftsplikt`,
`eHandel`.

Each line: `mvaKode`, optional `spesifikasjon` (`uttak`, `tapPaKrav`,
`justering`, `tilbakefoering`, `kjoepMedKompensasjonsrett`), optional
`mvaKodeRegnskapssystem` (your internal code), `grunnlag`, `sats`,
`merverdiavgift`, optional `merknad`. Amounts are decimals (øre allowed) —
**don't round**; validation compares `grunnlag × sats` vs `merverdiavgift`
with tolerance rules.

- Reportable codes: 3, 31, 33, 5, 6, 32, 51 (domestic sales), 52 (export),
  1, 11, 12, 13 (purchase deductions), 81–85, 14, 15 (goods import),
  86–89 (services import), 91, 92 (allowances/gold).
- **Never report codes 0, 7, 20, 21, 22** — the aggregator needs an explicit
  exclusion list.

The ledger carries a SAF-T VAT code on every transaction line; the melding is
`GROUP BY (mvaKode, spesifikasjon, sats)` over the `skattleggingsperiode`
(two-monthly default; enum also has monthly, quarterly, half-yearly, weekly,
half-month and `aarlig`).

## Rates (date-effective!)

2026: **25 %** standard · **15 %** food *and* water/wastewater (cut from 25 %
effective **2025-07-01** — same SAF-T code 31 as food, so one-rate-per-code
tables misvalidate 2025 periods straddling the change) · **12 %** passenger
transport, accommodation, cinema, broadcasting, museums, sport events ·
**11.11 %** wild marine resources · **0 %** export. Rate list is versioned
upstream: `Skatteetaten/mva-meldingen … kodelister/sats.xml`.

## Submission flow (sluttbrukersystem)

Auth is **ID-porten** (OIDC authorization-code; scopes `openid`,
`skatteetaten:mvameldingvalidering`, `skatteetaten:mvameldinginnsending` —
ordered manually from Skatteetaten), then exchanged for an Altinn token
(`…/authentication/api/v1/exchange/id-porten` — a raw ID-porten token against
the app 401s). Maskinporten is only accepted for the MVA read/information
APIs.

1. POST the mva-melding XML to the **validation API** (always validate first)
2. Create an instance in Skatteetaten's Altinn 3 app
3. PUT the `mvaMeldingInnsending` metadata XML (orgnr/fnr, period, category,
   `innsendingstype`)
4. POST the mva-melding XML (`datatype=mvamelding`)
5. Optional attachments: max 57 files, 25 MB each, PDF/Office/images/XML,
   duplicate filenames rejected
6. `PUT {instance}/process/next` **twice** (complete filling → submit)
7. Poll `GET …/feedback/status`; fetch and **persist** `valideringsresultat`
   (XML), `betalingsinformasjon` (XML, contains the payment KID),
   `kvittering` (PDF) — store them with the period's bilag

UI requirement: the user must see the full aggregated melding (all lines +
`fastsattMerverdiavgift`) and actively confirm before submission.

## Endpoints

| | Test | Production |
| --- | --- | --- |
| Validation | `https://idporten-api-sbstest.sits.no/api/mva/grensesnittstoette/mva-melding/valider` | `https://idporten.api.skatteetaten.no/api/mva/grensesnittstoette/mva-melding/valider` |
| Altinn 3 app | `https://skd.apps.tt02.altinn.no/skd/mva-melding-innsending-etm2/instances` | `https://skd.apps.altinn.no/skd/mva-melding-innsending-v1/instances` |
| ID-porten | `https://test.idporten.no/.well-known/openid-configuration` | `https://idporten.no/.well-known/openid-configuration` |

The Altinn **app id differs between environments** (`…-etm2` vs `…-v1`) —
configuration values, never constants. Ignore legacy `oidc-ver2.difi.no`
hosts in older docs.

## Testing

Only **synthetic Tenor Testdatasøk users** work for ID-porten/Altinn test
login — ordinary Digdir test users don't. Getting scopes + an ID-porten
integration is partly manual with multi-week lead time; it gates any
end-to-end milestone (see ROADMAP phase 0b). After Altinn 2 shuts down
(June 2026), customers must delegate the new MVA tilgangspakker (available
since 2026-04-15) or on-behalf submissions fail authorization.
