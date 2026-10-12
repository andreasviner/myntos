# Synthetic test data (Tenor)

Exports from [Tenor Testdatasøk](https://www.skatteetaten.no/skjema/testdata/),
Skatteetaten's search over the synthetic registers behind the test environment.
Everything here is **synthetic**: the people and companies do not exist, and the
numbers cannot collide with real ones. That is what makes it safe to commit, and
it is also the only kind of data that may ever be sent to `*.sits.no` or TT02 —
anything submitted to test becomes searchable in Tenor by anyone.

A synthetic fødselsnummer carries **+80 on the month digits** (born in November
2024 → `0191…`), which is how one is recognised at a glance and how
`myntos.auth.idporten.testid` refuses a real number. A D-number adds 40 to the
*day* digits on top of that, so `60905701315` is both.

| File | Source | Records |
| --- | --- | --- |
| `tenor-personer.json` | Syntetisk Folkeregister | 800 people |
| `tenor-enheter.json` | Syntetisk Enhetsregister/Foretaksregister | 799 entities |
| `tenor-personer.csv`, `tenor-enheter.csv` | the same, ids only | 100 each |

Several exports merged and deduplicated by id. Every fødselsnummer verified
synthetic; every organisasjonsnummer verified against
`myntos.domain.ledger.orgnr` (mod-11).

## The records worth using

Tax data is **rare** — 4 of 800 people, 1 of 799 entities — and cannot be
filtered on in Tenor's UI, so these were found by mining the export. Existing in
a register is not the same as having a skattemelding: a person picked at random
answers empty, which looks exactly like a broken integration.

### People

| fødselsnummer | Skattemelding | Næringsspesifikasjon | Notes |
| --- | --- | --- | --- |
| `14884296650` | 2022–2025 | **2023, 2024** | **The best record we have.** Innehaver of ENK `312854465` — the personlig næringsdrivende case, filed on the *fødselsnummer* |
| `04829097694` | 2023–2025 | 2024 | Næringsspesifikasjon but no company in the register |
| `12869599229` | 2023–2025 | — | Plain lønnstaker; has arbeidsforhold |
| `18899099067` | 2022 only | — | Thin; the year is old enough to be awkward |

### Entities

| orgnr | Form | Year | What it has |
| --- | --- | --- | --- |
| `314067630` | AS | **2025** | `harSkattemeldingUtkast` — the only entity in 799 with one. Daglig leder `08866995401`, in Foretaksregisteret, **not** MVA-registered, has both revisor and regnskapsfører |
| `312854465` | ENK | — | Innehaver `14884296650` (above). In Foretaksregisteret, not MVA-registered |

Note the income years differ: the people have 2024, the AS only 2025. A check
run against the wrong year returns nothing and reads like a failure, which is
why `digdir_check --person` takes `--year`.

### Shapes for the non-tax paths

Company setup, VAT defaults and invoice rules need registered entities rather
than tax data. 46 ENK and 43 AS carry a named role holder; 53 are
MVA-registered and 53 are in Foretaksregisteret. Examples:

- MVA-registered ENK with innehaver: `311186191` / `08869698742`
- MVA-registered AS with daglig leder: `311124226` / `05849898878`

"MVA-registered" is Tenor's flag only. Skatteetaten's TT02 MVA register does
not hold either of them, so an MVA-melding validation for them fails on
`meldingskategori` whatever the period
([mva.md](../docs/integrations/mva.md#running-the-validation-live-and-where-it-stands)).

Both register flags drive real behaviour: `registrertIMvaregisteret` decides
whether output VAT is booked at all, and `registrertIForetaksregisteret` whether
a salgsdokument must carry the word "Foretaksregisteret" together with the
head-office address (bokføringsforskriften § 5-1-2).

## The combination that works

```
cd backend && uv run python -m myntos.auth.digdir_check \
    --person 08866995401 --org 314067630 --year 2025
```

`08866995401` is daglig leder of AS `314067630`, and that is what makes it work:
Skatteetaten checks the person's Altinn rights over the company, and a role in
Enhetsregisteret is such a right. No `digdir:skattemelding` is needed for it;
that was assumed until 2026-10-09, when the fetch ran without it and worked. The response is committed as
`skattemelding-314067630-2025.xml` - 714 bytes, the only real one we have.

Nobody's **own** melding has been served yet. Every person here answers
"Skattemeldingen er ikke tilgjengelig ennaa" on their own fodselsnummer, for
every year, so Tenor's `harSkattemeldingUtkast` evidently means the row exists
rather than that the API will serve it. The org path is unaffected.

No portal login per person is needed. That was a theory from Altinn's issue
tracker; it was tried and changed nothing. The cause was that the ID-porten
client carried no `altinn:*` scope at all, so its token had no audience Altinn
recognised. `altinn:enduser` is what was ticked on to fix it here, and it read
as the rule until production turned out to exchange tokens without it.

## Still missing

- **An organisation TT02's MVA register holds with an alminnelig filing duty.**
  The MVA path itself ran end to end on 2026-10-10 with `05849898878` for
  `311124226`, and the only finding was that the register does not know the
  company. Ask Skatteetaten's SBS support which test organisations it holds, or
  to register one, rather than trying more Tenor entities.
- **A `harSelskapsmeldingUtkast` entity.** Nothing in 799 has one.
- **Anything for 2026.** Every `testinnsendingSkattEnhet` row for 2026 is empty,
  and `manglendeGrunnlagsdata` on those rows lists what the synthetic population
  lacks (`BETALINGER_NAERINGSDRIVENDE`, `CRSFATCA`, `TILDELTE_OPSJONER`).
