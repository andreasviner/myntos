# Roadmap

Phases are ordered by dependency, not calendar. Anything touching Skatteetaten
has a **manual onboarding lead time** (scopes are ordered via Skatteetaten's
contact form, ID-porten/Maskinporten integrations are configured in Digdir's
Samarbeidsportalen) — start phase 0b early, it gates everything after it.

## Phase 0 — Foundation ✅ (this scaffold)

- [x] Monorepo layout, uv + pnpm, lockfiles, CI (ruff/mypy/pytest, astro
  check/build, gitleaks), Dependabot, pre-commit, SHA-pinned actions
- [x] FastAPI app factory, settings, security headers, health endpoint, tests
- [x] Astro 7 + Cloudflare adapter v14, Workers deploy config
- [x] Disclaimer (DISCLAIMER.md, README banner, site footer + /ansvar page)
- [x] Architecture + integration research (verified against official sources)

## Phase 0b — Government onboarding (parallel to everything, slow, manual)

- [ ] Samarbeidsportalen access (test: self-signup with org-domain email)
- [ ] Register Maskinporten client (test), generate per-installation JWK
- [ ] Apply for Skatteetaten API access via their ekstern-Jira form and
      accept the bruksvilkår — required **for test as well as production**;
      until granted, `skatteetaten:*` scopes show "Tilgang mangler" and are
      unusable. Covers `skatteetaten:formueinntekt/skattemelding`,
      `skatteetaten:mvameldingvalidering`, `skatteetaten:mvameldinginnsending`
- [ ] ID-porten integration (test.idporten.no) + Tenor synthetic test users
- [ ] TT02/Altinn test setup (`skd.apps.tt02.altinn.no`)

## Phase 1 — Accounting core

- [ ] SAF-T-aligned schema: kontoplan with **per-financial-year
      GroupingCategory/GroupingCode mapping**, customers/suppliers with
      balance accounts, **date-effective VAT-code + rate tables** (mapped to
      StandardTaxCode), analysis dimensions, journal/ledger with immutable
      posted entries and debit/credit amount pairs (Alembic migrations)
- [ ] Double-entry posting engine (exact decimals, balanced-entry invariant)
- [ ] Golden tests against official SAF-T example files

## Phase 2 — Bilagsføring

- [ ] PDF upload, storage backends (local dev, R2 `jurisdiction=eu` prod)
- [ ] Bilag ↔ journal-entry linking, append-only archive (oppbevaringsplikt)
- [ ] Attachment validation (type/size — Altinn caps at 25 MB, unique names)

## Phase 3 — SAF-T Financial export (legal requirement)

- [ ] Version-parameterized SAF-T export: **v1.30** (usable until
      2026-12-31) and **v1.40** (mandatory for periods from 2027-01-01),
      selected by financial year; vendored XSDs + official examples as golden
      fixtures (github.com/Skatteetaten/saf-t, Apache-2.0); own
      cross-reference validation (Altinn only checks the XSD)
- [ ] § 7-5 compliance features: notification-letter generator,
      readable/printable-in-Norway guarantee, full-data repatriation export

## Phase 4 — MVA (VAT)

- [ ] MVA calculation per SAF-T code; aggregation to MvaSpesifikasjonslinje
      (exclusion list: codes 0, 7, 20, 21, 22 must never be reported)
- [ ] ID-porten OIDC flow (`auth/idporten`) + Altinn token exchange
- [ ] Validation API → Altinn 3 instance flow → feedback (persist
      valideringsresultat, kvittering PDF, betalingsinformasjon/KID with the
      period's bilag)
- [ ] Review-and-confirm UI before any submission (user sees the full melding)

## Phase 5 — Skattemelding + næringsspesifikasjon

- [ ] Build næringsspesifikasjon from the ledger (formats per
      github.com/Skatteetaten/skattemeldingen, api-v2)
- [ ] Fetch current skattemelding (Maskinporten), validate + submit,
      receipts. Systembruker may fetch/validate/create instance/upload, but
      the **final submit requires an ID-porten-logged-in person** — the
      review-and-sign UI step is the mandatory checkpoint
- [ ] Regnskapsfører/multi-client: `consumer_org` delegation now; Altinn 3
      systembruker + tilgangspakker as they land (Altinn 2 shuts down
      June 2026; old role delegations die then)

## Phase 6 — Årsregnskap til Regnskapsregisteret

"Regnskapsmelding" is not a live API (July 2026); the real channel is
Brønnøysundregistrene's **maskinell innrapportering** — see
docs/integrations/skatteetaten.md.

- [ ] Altinn 3 client for the brreg årsregnskap apps
      (`aarsregnskap-vanlig-202406` et al.) — reuses our Maskinporten/Altinn
      plumbing, only needs `altinn:instances.read/write`
- [ ] Fill-with-systembruker → **sign with ID-porten** (signing auto-submits;
      a personnummer is legally attached to the signature)

## Continuous / before first public release

- [ ] Threat model review; independent security review of auth + storage
- [ ] OpenSSF Scorecard action + bestpractices.dev badge; CODEOWNERS
- [ ] Code of Conduct (Contributor Covenant 3.0)
- [ ] License: **deliberately undecided.** Reverted from AGPL-3.0-or-later to
      proprietary / all-rights-reserved (2026-08-12) to keep every option open.
      The source stays **public and readable** — this is source-available, not
      closed. Rationale: publishing under any OSI license frees that snapshot
      permanently — you can relicense future versions, but the released commit
      stays forkable forever. Nothing forces the decision yet: sole copyright
      holder, never licensed to anyone, and no copyleft in the dependency tree
      (FastAPI/SQLAlchemy/Alembic/PyJWT MIT, uvicorn/httpx BSD,
      asyncpg/cryptography Apache-2.0, Astro/Wrangler MIT).
      Decide before first public release. Options in view:
      - **AGPL-3.0-or-later** — blocks closed-source SaaS forks (Firefly III's
        reasoning), but forecloses a commercial hosted offering of your own
        unless every contributor signed a CLA.
      - **FSL / BSL 1.1** — source-available, no competing use, auto-converts
        to Apache-2.0 after a change date.
      - **Stay proprietary** — required if a paid hosted service is the plan.
      **The real deadline is the first external PR, not the release** — and a
      public repo means PRs can arrive any day. Without a CLA or copyright
      assignment, contributed code fixes its own terms and relicensing dies.
      CONTRIBUTING.md therefore says code contributions are closed for now.
      Expect pushback on "public but not open source"; the README says so
      plainly rather than implying otherwise.
      If the Maskinporten client or SAF-T exporter are ever extracted as
      standalone libraries, MIT/Apache-2.0 for those remains sensible — they
      carry no business value and adoption helps other Norwegian projects.
- [ ] Dialogporten integration for receipts/correspondence (post-June-2026
      Altinn pattern)
- [ ] Legal review of DISCLAIMER.md before offering any hosted service
