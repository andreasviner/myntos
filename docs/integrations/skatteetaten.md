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
(confirm, then submit; **once** for a company whose revisor confirms, see
below) → fetch the answer after a processing delay. Persist this as an
explicit workflow state machine. The validation endpoint wants the opposite
content type: `application/xml`, and 415 for `text/xml`.

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

An agent with a Myntos key may ask for the check and the filing, but each
request waits for its person on `/godkjenning/` and runs only when they
approve it, with their own ID-porten login (`key_send(…, id_porten=True)`,
docs/agents-and-api.md rule 5). That is the same checkpoint: the person
logged in with ID-porten says yes to exactly what is sent.

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

## Filing from Myntos (built 2026-10-22)

An aksjeselskap's skattemelding and næringsspesifikasjon are built from a
closed year, checked, filed and answered from the Skattemelding page
(`/skattemelding/`). The code is `backend/src/myntos/skattemelding/` (its
README says which of Skatteetaten's files each module reads), the client is
`integrations/skatteetaten/client.py`, the routes `api/v1/skattemelding.py`.

| Step | Route | Who |
| --- | --- | --- |
| Read the year: the posts, the saldo groups, the equity reconciliation, the formue, what stops it | `GET /companies/{id}/skattemelding?aar=` | a person, or a key with `read` |
| Fetch gjeldende, complete it, build the envelope, `/valider` it, keep both | `POST …/skattemelding/kontroll` | the person, on their ID-porten login |
| Create the instance, upload, `process/next` | `POST …/innsendinger/{id}/send` | the person |
| Fetch Skatteetaten's answer | `POST …/innsendinger/{id}/tilbakemelding` | the person |
| One filing, and its stored documents | `GET …/innsendinger/{id}`, `GET …/innsendinger/{id}/dokument?type=` | a person, or a key with `read` |

The three in the middle are closed to keys (`_NOT_FOR_AGENTS`, reason
`_GOVERNMENT`). An agent asks with `POST …/godkjenninger/innsending`
`{"type": "skattemelding", "aar": …}`, which puts a link to the page on
«Godkjenning» and turns `done` when a filing for the year is sent.

What it does, and the reason for each:

- **The fetch is gjeldende** (`GET /api/skattemelding/v2/<år>/<orgnr>`), not
  `utkast`: the first time it is the utkast, and after a filing it is the
  fastsatt melding, which is what a correction must start from. The fetch
  gives `partsnummer`, which the næringsspesifikasjon must carry, and the
  melding must be `upersonlig`, or the check stops.
- **Myntos sends nothing Skatteetaten derives** (sums, results, the computed
  depreciation), because the XSDs mark those and Skatteetaten refuses or
  overwrites them. The validation answer's `avvik` of type `mangler…` are
  exactly those values, filled in by Skatteetaten, and are expected on an
  accepted check.
- **The check keeps the envelope it sent**, byte for byte, with a sha256 of
  the næringsspesifikasjon. Sending files that stored envelope, and only
  while the books still build the same næringsspesifikasjon (409
  `skattemelding_changed` otherwise), so what Skatteetaten checked is what is
  filed. The person confirms the `skattemessig_resultat` they were shown.
- **A revisor confirms the næringsspesifikasjon** when Enhetsregisteret names
  one (role `REVI` on the company's snapshot). The instance is then created
  with `dataValues` `{"inntektsaar": "2025", "skalBekreftesAvRevisor": "true"}`
  (both strings, set when the instance is created), and the person's one `process/next` leaves it at `Task_2Revisor`. The revisor
  confirms in Skatteetaten's viewer,
  `https://skatt.skatteetaten.no/web/skattemelding-visning/altinn?appId=skd/formueinntekt-skattemelding-v2&instansId=<party>/<guid>`
  (test: `skatt-test.sits.no`), and the filing reads «Sendt, venter på
  revisor» until then. Without a snapshot Myntos does not know, and the check
  stops (`revisor_ukjent`) rather than guess: a wrong guess either files
  without the confirmation the law needs or waits for a revisor who does not
  exist. This path is Skatteetaten's documented flow and is covered by tests
  against stubs; it has **not** been run in TT02, because the synthetic AS has
  no revisor.
- **The upload is named**: `Content-Disposition: attachment;
  filename=skattemelding.xml`, unquoted, as Skatteetaten's examples send it.
- **The answer is the data element `tilbakemelding`**, the same document type
  as the validation answer (`skattemeldingOgNaeringsspesifikasjonResponse`),
  computed again on what was filed. Myntos stores it and takes the computed
  tax from it. Altinn's own `status.substatus` carries the words a person
  reads («Mottatt» / «Din skattemelding er mottatt…»), and those are stored as
  the outcome. A miss is ordinary: Skatteetaten answers a while after Altinn
  accepts, so `…/tilbakemelding` fetches it later.
- **A submission that stops halfway** keeps the filing as `feilet` with the
  step that failed, and the person checks Altinn before trying again: the
  instance may exist.

XSDs, vendored in `skattemelding/xsd/` with their hashes in the README: the
næringsspesifikasjon v6 (2025) and v7 (2026), the upersonlig skattemelding
v5 (2025) and v6 (2026), and the request/response envelope v2. A year with no
entry is refused, never written with a neighbour's schema.

**Proven in TT02** with the synthetic AS `314067630` (daglig leder
`08866995401`), through `digdir_check` in `../myntos-tt02`:

- 2026-10-21: `--skattemelding 314067630 --year 2025` built and validated the
  pair: `validertOK`, computed tax 83 820, every `avvik` a derived value.
- 2026-10-22: `--submit` filed it (create, upload, `process/next` twice) and
  polled the answer: Altinn's status «Mottatt: Din skattemelding er mottatt»,
  instance `51889108/21800e12-17db-4e38-931d-d154aa24ce3d`. The validation and
  the answer are in `backend/tests/fixtures/skattemelding/`.

`--submit` is the one thing `digdir_check` sends rather than reads, and it
refuses any app URL without `.tt02.` in it.

**Production needs three settings in `.env`**, and the page says filing is
not available until all three are there:

```
SKATTEETATEN_API_BASE_URL=https://api.skatteetaten.no
SKATTEETATEN_IDPORTEN_API_BASE_URL=https://idporten.api.skatteetaten.no
ALTINN_SKATTEMELDING_APP_URL=https://skd.apps.altinn.no/skd/formueinntekt-skattemelding-v2
```

Production had the first two from 2026-10-09 (for the MVA validation) and
got the third on 2026-10-22. The scopes are already on the production
ID-porten client and in `IDPORTEN_SCOPES`
(`skatteetaten:formueinntekt/skattemelding`, `altinn:instances.read`/`.write`).

### A dissolved company's last year: forhåndsfastsetting (built 2026-10-22)

An aksjeselskap dissolved during a year files that year's skattemelding before
it is deleted, not the spring after (skatteforvaltningsloven § 8-2, with
tvangsmulkt when it is missing), together with any earlier year still owed.
In Myntos the person ends the books on the company's last day under
Årsavslutning (`company.wound_up_on`, `domain/arsoppgjor/winding_up.py`), so
the year's `end_date` is that day and the year closes the day after; the check
and the filing are then the routes above, with what follows. Read from
`docs/api-v2` in Skatteetaten's repository («Forhåndsfastsetting») and proven
in TT02 on 2026-10-22 with `314067630` for 2026: prepared, checked
`validertOK`, filed, answered «Mottatt» (instance
`51889108/99c4e6f2-099a-4a6d-96f8-74caf309b3cf`).

- **There is no melding to fetch until Myntos asks for one.** `GET` gjeldende
  for the running year answers **403 «Skatteplikt ikke registrert»**. `POST
  /api/skattemelding/v2/klargjoerforhaandsfastsetting/<år>/<orgnr>` (ID-porten
  host, the skattemelding scope, JSON) answers `{"status": "OK"}`, and the
  utkast appears **about 20 seconds later** (403 «Skattemeldingen er ikke
  tilgjengelig ennå» meanwhile). The check fetches first, prepares only when
  there is nothing or only an ordinary utkast while the year is running, then
  waits up to 30 seconds and otherwise asks the person to press again
  (`skattemelding_not_available`).
- **Its refusals are 400 with the same JSON**: `PART_HAR_GJELDENDE` (a
  gjeldende exists; also what 2025 answered for the company),
  `FORHAANDSFASTSETTING_ER_IKKE_STOETTET_FOR_INNTEKTSAAR` (2024 was refused),
  `PART_TYPE_ER_IKKE_STOETTET` (a person), `PART_HAR_UGYLDIG_STATUS_I_ER`
  (Enhetsregisteret does not mark the company dissolved, deleted, bankrupt or
  under tvangsoppløsning; **switched off in test**, which is why TT02 prepared
  a live synthetic company and production will not). Calling it again after
  `OK`, even after filing, answered `OK` and left the filed melding alone.
- **The utkast says which format to file in**:
  `gjelderForhaandsfastsetting/innsendingsformat/forhaandsfastsettingsformattype`,
  `aaretsSkattemelding` (the year's own XSDs; what TT02 served for 2026 in
  October, in the v6 namespace) or `fjoraaretsSkattemelding` (last year's,
  before the year's model is published). Myntos writes the first and refuses
  the second by name: it would mean writing the næringsspesifikasjon for
  year N with year N−1's schema and code lists, which is not built.
- **What is added**: `opplysningOmSkattesubjekt/erFulltAvvikletIInntektsaaret`
  (`boolsk` true) and `verdsettingAvAksje/likvidasjonsvederlagVedForhaandsfastsetting`
  (the payout to the shareholders, whole kroner, stated by the person and
  suggested from the equity that left other than through the result); the
  marker Skatteetaten wrote stays. Each envelope `dokument` gets
  `navneromVedForhaandsfastsetting`, the namespace of that document's own
  root. The næringsspesifikasjon's `regnskapsperiode` ends on the last day,
  and the payout is the equity change «annenNegativEndringIEgenkapital»;
  Skatteetaten has no type of its own for it and took that one.
- **The Altinn instance is the ordinary one**, `dataValues.inntektsaar` the
  running year; nothing marks the forhåndsfastsetting there.
- **A deleted company's roles are gone from Enhetsregisteret** (its
  `/roller` answers 404, which reads as no roles), so «no revisor» would be a
  guess: the check asks the person instead (`revisor`), and the overview says
  `revisor_unknown`. Enhetsregisteret answers a deleted unit with a 200 and
  `respons_klasse: SlettetEnhet` (it used to be 410); Myntos shows it as
  deleted (`deleted_on`) and still lets books be set up for it.

**A second filing for a year already filed (a correction) strips what
Skatteetaten derives.** Once a year is filed, the melding served is the
fastsatt one, and it carries Skatteetaten's own figures
(`inntektOgUnderskudd/samletUnderskudd`, `inntektsfradrag/underskudd`,
`inntektFoerFradragForEventueltAvgittKonsernbidrag`, `fremfoerbartUnderskudd…`,
all `skatt:erAvledet`). Sent back as served, TT02 refused the pair
(`validertMedFeil`, `avvikSkattemelding` on exactly those four, 2026-10-22)
as soon as the books differed from the first filing. `xmlwriter.merge` now
takes every derived element out of the fetched melding, and a section left
empty by that, before it adds Myntos's sections; the same check then answered
`validertOK`. A derived figure carrying `erOverstyrt` true stays: that is the
person having overridden Skatteetaten's figure on skatteetaten.no, and it is
theirs. What is still kept as served is a non-derived section the fetched
melding already has (`formueOgGjeld`, `opplysningOmSkattesubjekt`,
`verdsettingAvAksje`): a correction does not replace them with fresh figures
from the books, so a changed formue or payout in a correction has to be put
right on skatteetaten.no. The review says so (`…_fra_skatteetaten`).

Not tried in production yet: whether Altinn creates an instance for a deleted
party, and whether a former daglig leder still holds the right to file for
it there. The first real case is the owner's own AS, deleted 2026-07-21.

**Not built**, and what each means for a user:

- **An enkeltpersonforetak.** Its næringsspesifikasjon goes with the owner's
  personal skattemelding, on fødselsnummer; the review says `enk_ikke_ferdig`
  and the owner files on skatteetaten.no. ANS, DA and the other forms are
  refused the same way.
- **Real estate and shares in the formue**: their formue value is not their
  book value, so they are left out and named in the findings; the person adds
  them on skatteetaten.no after filing.
- **Saldo groups beyond a–j** (time-limited rights, non-depreciable assets,
  `anlegg_ikke_stottet`), and attachments (PDF `skattemelding-vedlegg`).
- **The async job API** (`…/v2/jobb/…`), mandatory above 10 MB of base64
  næringsspesifikasjon. A small company's is a few kilobytes (the TT02 one was
  6 KB); one that large would be refused by Altinn at upload, not
  truncated.
- **Klage and endringsanmodning**: only `egenfastsetting` is sent. A
  correction before the deadline is a new egenfastsetting from the same page.
- **A forhåndsfastsetting in last year's format** (`fjoraaretsSkattemelding`),
  above; refused by name, filed on skatteetaten.no.

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
  and the repo; fellesmøter are ~quarterly, announced there.
