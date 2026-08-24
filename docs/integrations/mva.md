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

**Scope audit, 2026-08-24** (probed live, not read off a page):

| Scope | ID-porten client `5aa404aa` | Maskinporten `f8293b48` |
| --- | --- | --- |
| `skatteetaten:mvameldingvalidering` | **granted** | not registered |
| `skatteetaten:mvameldinginnsending` | **not registered** | not registered |

So validation is reachable today and submission is not. Neither belongs on the
Maskinporten client: both steps require a person, so a machine token would have
nowhere to use them. Getting `mvameldinginnsending` is the one portal action
left for this path, and the note further down about multi-week grant handling
applies to it - it is not obviously a self-serve checkbox.

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

Test logins use **TestID** with **synthetic** people from Tenor Testdatasøk
(`skatteetaten.no/skjema/testdata/`, logged into with a real ID-porten login of
your own). A synthetic fødselsnummer carries **+80 on the month digits** — born
in November 2024 gives `0191…` — which is how one is recognised, and why it can
never collide with a real number. Digdir now recommends TestID for everyone
rather than the older password-based test users; it has no password and lets the
caller pick the assurance level.

**Headless login is supported in test, and it is worth building against.**
ID-porten's `/authorize` accepts

    login_hint=testid:<synthetic pid>_idporten-loa-high

and authenticates without a person or a browser, redirecting straight back with
a code. The result is a full token — refreshable, introspectable, and carrying
`amr=TestID` — so the whole submission chain (ID-porten token → Altinn exchange
→ instance → `process/next` twice → receipt) is automatable end to end. Test
only; production has no equivalent, and the `amr` claim is how a token from it
is told apart. Verified against docs.digdir.no 2026-08-24.

Getting scopes and an ID-porten integration is partly manual with multi-week
lead time; it gates any end-to-end milestone (see ROADMAP phase 0b). After
Altinn 2 shuts down (June 2026), customers must delegate the new MVA
tilgangspakker (available since 2026-04-15) or on-behalf submissions fail
authorization.
