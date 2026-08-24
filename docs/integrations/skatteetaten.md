# Skattemelding & næringsspesifikasjon (+ årsregnskap)

Researched and verified against official sources 2026-07-18.

**The docs ARE the GitHub repo:** `github.com/Skatteetaten/skattemeldingen`
(Apache-2.0 — XSDs and examples may be vendored here with NOTICE
attribution). `docs/api-v2` is the current API description; also
`docs/api-asynk`, `docs/informasjonsmodell`, `docs/test`, `docs/kontroller`.
There is no github.io site for it. Beware the name collision: the
"Skattemelding API" / "Næringsspesifikasjon API" on
`skatteetaten.github.io/api-dokumentasjon` are **read-only data-sharing
APIs** (require legal basis) — *not* the SBS submission APIs.

## Environments

| | Test | Production |
| --- | --- | --- |
| Skatteetaten API (ID-porten) | `idporten-api-test.sits.no` | `idporten.api.skatteetaten.no` |
| Skatteetaten API (Maskinporten) | `api-test.sits.no` | `api.skatteetaten.no` |
| Altinn 3 app | `skd.apps.tt02.altinn.no/skd/formueinntekt-skattemelding-v2/` | `skd.apps.altinn.no/skd/formueinntekt-skattemelding-v2/` |
| Altinn token exchange | `platform.tt02.altinn.no/authentication/api/v1/exchange/id-porten` | `platform.altinn.no/…` |

## Two API sets, one lifecycle

**Skatteetatens-API** (REST, XML): `GET /api/skattemelding/v2/<år>/<id>`
(gjeldende), `GET …/v2/{utkast|fastsatt}/<år>/<id>`, `POST
…/v2/valider/<år>/<id>` (returns validation result **and beregnet skatt**),
plus PDF, avregning, eiendom, skatteplikt endpoints. An async job API
(`…/v2/jobb/…`) is mandatory when the base64 næringsspesifikasjon exceeds
10 MB.

**Altinn3-API** (submission): create instance on
`skd/formueinntekt-skattemelding-v2` (instanceOwner + `dataValues.inntektsaar`)
→ upload the XML envelope as `dataType=skattemeldingOgNaeringsspesifikasjon`
— **`Content-Type: text/xml`, not `application/xml`** (Altinn's own docs say
otherwise; Skatteetaten explicitly flags that as wrong) → optional PDF
attachments (`skattemelding-vedlegg`) → `PUT …/process/next` **twice**
(confirm, then submit) → fetch receipt after a processing delay. Persist this
as an explicit workflow state machine.

## Formats

XML only, per-income-year XSD sets in `src/resources/xsd` (2024:
`skattemelding_v12` + `naeringsspesifikasjon_v5`; v13/v6 and v14/v7 exist for
2025/2026 — the README's mapping table lags, verify in the repo). Everything
travels in one base64 envelope (`skattemeldingognaeringsspesifikasjonrequest_v2.xsd`)
with a required `innsendingsformål` (egenfastsetting | klage |
endringsanmodning). Expect a new XSD major **every year** (0.9 model
~30 Nov, freeze ~15 Jan).

## Auth, scopes, and the human-in-the-loop rule

Scopes: `skatteetaten:formueinntekt/skattemelding` (+ `…/eiendom`), plus
`altinn:instances.read` / `altinn:instances.write`. ID-porten tokens: 8 h
access / 90-day refresh. Scope checks are not yet enforced on all tokens but
**will be turned on** — request scopes properly from day one.

**The skattemelding scope belongs on both clients**, and this is easy to get
wrong because the two failures look nothing alike (verified 2026-08-24):

- On the **Maskinporten** client it is what a systembruker fetch authenticates
  with. Ours has it, and it works — but a fetch for a person still fails
  `400 SME-EKSTERN-005` ("ikke mulig å sjekke om pålogget bruker har nødvendige
  rettigheter til å representere skattepliktig"), because a machine with no
  delegation has no authority over that taxpayer.
- On the **ID-porten** client it is what a person's own fetch needs. Without it
  the token carries only `openid`, and Skatteetaten answers **403 with an empty
  body** — no mention of a scope anywhere. Adding it to `IDPORTEN_SCOPES` before
  it is registered on the client is at least honest about the cause: ID-porten
  refuses the authorization with `invalid_scope — Scope
  skatteetaten:formueinntekt/skattemelding not registered on client`.

Organization-level access is granted once and covers both; registering the scope
on each client is a separate step per client.

**As of June 2026:** a systembruker (Maskinporten, no human) may fetch,
validate, create the instance and upload — but the **final submission
requires an ID-porten-logged-in person** (Altinn resource:
`app_skd_formueinntekt-skattemelding-v2`). The UI's review-and-submit step is
the mandatory human checkpoint, not a nicety.

## What a fetch actually returns

From the first real response (test_data/skattemelding-314067630-2025.xml, an AS
for 2025), because the shape is not obvious and costs an afternoon to discover:

```
<skattemeldingOgNaeringsspesifikasjonforespoerselResponse>
  <dokumenter><skattemeldingdokument>
    <id>SKI:755:32858</id>
    <encoding>utf-8</encoding>
    <content>PD94bWwg...</content>      <-- base64, and the real document
    <type>skattemeldingUpersonligUtkast</type>
  </skattemeldingdokument></dokumenter>
```

The envelope is XML wrapping a **base64-encoded** inner XML document; a parser
that reads the envelope's namespace and stops has found nothing. Decoded, the
inner document is `urn:no:skatteetaten:fastsetting:formueinntekt:skattemelding:
upersonlig:ekstern:v5` - note `upersonlig`, which is the schema family for a
company, against `personlig` for a fodselsnummer. `type` names both the group
and the variant (`skattemeldingUpersonligUtkast`).

The utkast for a synthetic company is nearly empty - `partsnummer` and
`inntektsaar` and nothing else - so it proves the transport, not the parsing.

## Taxpayer groups (2026)

All groups are supported via SBS: lønnstakere/pensjonister (P), personlig
næringsdrivende/ENK (**always filed on fødselsnummer, never orgnr**),
selskap med deltakerfastsetting (SDF), and upersonlige (AS etc.) — for whom
**SBS is the only channel**. Altinn 2 shut down 2026-06-19; Altinn roles work
in access checks only until **2026-12-01** (then tilgangspakker).

## Årsregnskap to Regnskapsregisteret ("regnskapsmelding")

"Regnskapsmelding" is **not** an official live API as of July 2026 — don't
scaffold against that name. The real machine channel is Brønnøysundregistrene's
**maskinell innrapportering** of årsregnskap: Altinn 3 apps
(`aarsregnskap-vanlig-202406` and nine siblings for other company types),
documented at `brreg.github.io/docs/apidokumentasjon/regnskapsregisteret/`.
Needs only `altinn:instances.read/write`; flow = instantiate → fill
hovedskjema/underskjema → attachments → lock → **sign** (auto-submits).
Same pattern: systembruker can fill, **signing requires ID-porten** (a
personnummer is legally attached). Reuses our Maskinporten/Altinn plumbing
wholesale. Support: `#annual-accounts-systeminnsending` on
digdir-samarbeid.slack.com.

## Onboarding (manual, slow — ROADMAP phase 0b)

1. Digdir Samarbeidsportalen account; ID-porten + Maskinporten integrations
   with the skattemelding scope + altinn:instances.read/write
2. Apply for test **and** prod API access via Skatteetaten's ekstern-Jira
   form (`eksternjira.sits.no/plugins/servlet/desk/site/global`), accept
   bruksvilkår. On the form:
   - *Tjenesteområder*: **Skattemeldingen** and **MVA-melding** only. The
     others are payroll (A-meldingen, Skattekort), shareholder register,
     third-party reporting or debt collection — not in scope, and asking for
     unused areas invites review delay. You can apply again later.
   - *Tjenester*: pick the **validering** and **innsending** services. Do
     **not** pick the deling/sharing read APIs ("Skattemelding API",
     "Næringsspesifikasjon API" on api-dokumentasjon) — those consume
     reported data and require a legal basis (behandlingsgrunnlag), which is
     a different application entirely.
   - *Leverandør for hvilken virksomhet*: not industry-specific — state
     bookkeeping-obligated businesses generally rather than leaving it blank,
     so it doesn't come back as a clarifying question.
   - Årsregnskap is **not** on this form: that is Brønnøysundregistrene and
     needs only the Altinn scopes you already have.
3. Altinn systembruker against `app_skd_formueinntekt-skattemelding-v2`
4. Tenor synthetic test users; full flow against `…test.sits.no` + TT02

## Gotchas

- Everything submitted to test becomes **searchable in Tenor** — never send
  real data to test.
- The shortest-lived scope in a combined token governs the whole token's
  lifetime.
- Validation errors include myndighetsfastsatt locks
  (`KanIkkeOverskriveMyndighetsfastsattVerdi`) — surface them in the UI;
  UP skatteplikt info arrives in the *valider* response, not in *hent*.
- Skatteetaten support is effectively unstaffed weeks 28–32 (Norwegian
  summer) — plan onboarding around it.
- Follow `skatteetaten.no/samarbeidspartnere/sluttbrukersystemer/sbs-nyheter/`
  and the repo; fellesmøter are ~quarterly (next: 2026-09-08).
