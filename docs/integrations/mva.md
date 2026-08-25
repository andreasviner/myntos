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

Auth is **ID-porten** (OIDC authorization-code; scopes `openid` and
`skatteetaten:mvameldingvalidering`), then exchanged for an Altinn token
(`…/authentication/api/v1/exchange/id-porten` — a raw ID-porten token against
the app 401s). Maskinporten is only accepted for the MVA read/information
APIs.

**Scope audit, 2026-08-24** (probed live, and checked against the portal's own
scope picker):

| Scope | ID-porten `5aa404aa` | Maskinporten `f8293b48` |
| --- | --- | --- |
| `skatteetaten:mvameldingvalidering` | **granted** | not registered |
| `skatteetaten:mvameldinginnsending` | **retired** | **retired** |

`mvameldinginnsending` is **gone, on Skatteetaten's own instruction**: the 2022
scope of that name "should no longer be used and should be replaced with
`altinn:instances.read` and `altinn:instances.write`". That is why it is missing
from Samarbeidsportalen's picker, and the code says why it was retired:
`MvaMeldingClient` makes exactly one Skatteetaten REST call, to
`/api/mva/grensesnittstoette/mva-melding/valider`. Everything after it -
instance, uploads, submission, feedback - goes through the Altinn app, which
authorizes on the exchanged Altinn token.

**So nothing needs to be applied for to submit an MVA-melding.**
`mvameldingvalidering` (granted) plus `altinn:instances.read`/`.write` (granted)
is the complete credential set for the documented flow. An earlier version of
this file claimed a second Skatteetaten scope was required; it was wrong, and the
replacement instruction is the reason.

What the picker does offer, none of it required today:

| Scope | For | Worth it |
| --- | --- | --- |
| `mvaregisteravgiftssubjekt` | MVA-register: who is registered and liable | yes, on **Maskinporten** - `registrertIMvaregisteret` decides whether output VAT is booked at all, and this is the authoritative source rather than Brreg's copy |
| `mvameldinginnsendingstatus` | "Innsendingsstatus og frister" | yes, on **Maskinporten** - drives which periods are due per client |
| `mvamelding` | the melding itself | later, when we add a fetch. There is no fetch method today |
| `mvameldingsopplysning`, `mvafastsetting` | meldingsopplysninger, assessment | no code path |
| `mvainnsendingsstatustest`, `mvameldinginnsendingsstatus` | test-suffixed and undescribed near-duplicates of the two above | no - prefer the described one of each pair |

The two "yes" rows belong on the **Maskinporten** client, not ID-porten: they are
lookups with no person in them. Validation and submission are the opposite, and
stay on ID-porten.

**Data types, read off the app on 2026-08-24** (not from documentation - the
app is the only authority, and `GET {app}/api/v1/applicationmetadata` returns
them):

| Purpose | dataType id |
| --- | --- |
| metadata | `no.skatteetaten.fastsetting.avgift.mva.mvameldinginnsending.v1.0` |
| the melding | `mvamelding` |
| attachments | `binaerVedlegg` |
| feedback | `valideringsresultat`, `betalingsinformasjon`, `kvittering` |

The app also declares `tidligereinnsendtvedlegg`, `miljo`, `utfyllingslogg`,
`signeringslogg`, `godkjenningslogg`, `attesteringslogg` and `portalLenker`,
none of which we use yet. The logs are the audit trail a bokforingspliktig may
want to keep.

Note the test app is `mva-melding-innsending-v1`, the **same name as
production** - `-etm2` does not exist on tt02 and answers 418. Environments are
separated by host, not by app name.

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
| Altinn 3 app | `https://skd.apps.tt02.altinn.no/skd/mva-melding-innsending-v1/instances` | `https://skd.apps.altinn.no/skd/mva-melding-innsending-v1/instances` |
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
