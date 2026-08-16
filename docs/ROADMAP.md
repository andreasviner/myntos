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

- [x] SAF-T-aligned schema: kontoplan with **per-financial-year
      GroupingCategory/GroupingCode mapping**, customers/suppliers with
      balance accounts, **date-effective VAT-code + rate tables** (mapped to
      StandardTaxCode), analysis dimensions, journal/ledger with immutable
      posted entries and debit/credit amount pairs (Alembic migrations)
      — `myntos.domain.ledger.models`, migration `9f2a1c4b7e30`
- [x] Double-entry posting engine (exact decimals, balanced-entry invariant)
      — `myntos.domain.ledger.posting`; corrections go through
      `build_reversal`, never through edits. Closing balances are derived,
      not stored, so they cannot drift from the postings
- [ ] Golden tests against official SAF-T example files — blocked on the
      exporter itself (phase 3): the fixtures from `Skatteetaten/saf-t` are
      XML output, and there is nothing yet to compare them against. Schema
      and posting invariants are covered by `tests/test_ledger_schema.py`
      and `tests/test_posting.py` in the meantime

## Phase 1b — Accounts, organizations and access control

- [x] Organization-as-tenant model: `Organization` owns members *and* companies,
      so a subscription attaches to the organization rather than to a user;
      `company.orgnr` is unique per organization, not globally — two firms may
      keep books for the same client. Migration `3d7c15ba90e4`
- [x] Login with email/password (Argon2id, per-account lockout) or a federated
      provider — **Google, Microsoft (Entra ID) and Vipps** over one OIDC
      implementation, listed by `/auth/providers` only when configured. One
      account can hold several, and a federated identity is linked to an
      *existing* account only on a provider-**verified** email (takeover
      defence; Microsoft needs the `xms_edov` optional claim for that)
- [ ] Verify the Vipps discovery URL against current Vipps documentation, and
      replace the placeholder Vipps mark with their supplied button asset,
      before offering Vipps login publicly
- [x] Revocable server-side sessions (opaque token, stored hashed, httpOnly
      cookie or bearer), idle + absolute expiry, origin-checked writes
- [x] Roles (owner/admin/member/viewer) in one permission table; only an owner
      may create or remove owners, and the last owner cannot leave
- [x] Invitations: single-use hashed token, addressed to an email, so several
      accounts share one organization — `myntos.domain.identity`
- [x] Frontend: login/signup/invitation pages; every company- and ledger-scoped
      endpoint resolves the caller's membership before answering, and a company
      in another organization is a 404 rather than a 403
- [ ] Email delivery (Cloudflare Email Service): address verification, invitation
      mails, password reset. Until then the invite link is returned once to the
      inviter to pass on, and no address is verified by us
- [x] Member-management UI (`/organisasjon/`): invite with a role, change roles,
      remove or leave. The invite link is shown once, to copy and pass on
- [ ] Self-service session list ("sign out this device") and account deletion
      (GDPR erasure)

## Phase 1c — Kontantstrøm (the first thing a user can do)

- [x] Company setup that seeds a starter kontoplan, VAT codes with
      date-effective rates, the first financial year and its per-year account
      groupings — so a new user's first action can be a payment rather than
      building a chart of accounts. Orgnr is mod-11 checked
- [x] Cash-flow type catalogue mapping each category to an account (and through
      it a næringsspesifikasjon post) and a default VAT code, with the
      assumption stated on every type and overridable per entry —
      `myntos.domain.ledger.cashflow`
- [x] Recording in/out from the **gross** amount, split into net and VAT so the
      voucher balances exactly (`posting.split_gross`); posted immediately, and
      corrected by reversal with `reverses_transaction_pk` recording which entry
      was undone. Migration `5c81ea2d4f16`
- [x] Frontend: two-state front page (marketing when signed out, overview with
      totals when signed in) and the `/kontantstrom/` register
- [ ] The starter chart's `GroupingCategory`/`GroupingCode` pairs follow the
      documented shape but must be checked against Skatteetaten's year-versioned
      code lists before the SAF-T export ships — see the note in
      `myntos.domain.ledger.chart`
- [ ] Editing the kontoplan, adding accounts, and choosing a VAT registration
      status that switches the sensible defaults for a whole company
- [x] Customers and suppliers (SAF-T `Party`) with balance accounts derived from
      the type, optional on an entry, carried on the basis line and preserved
      through a reversal — `myntos.domain.ledger.parties`, `/parter/`
- [ ] Bank import (CAMT.053 / PSD2) so the register is reconciled against the
      account rather than typed twice

## Phase 1d — Fakturering og e-faktura (legal deadline: 2027-01-01)

Lov 19. juni 2026 nr. 39 (pliktig digital bokføring og e-fakturering) makes
documentation of sales to another bokføringspliktig issuable only in an
electronic invoice format. **Sending is mandatory from 2027-01-01**, receiving
and full digital bokføring from 2030-01-01. See docs/integrations/efaktura.md.

- [x] Salgsdokument per bokføringsforskriften § 5-1, checked at issue and not
      before, with each finding naming the paragraph it comes from — a draft is
      allowed to be half-written, § 5-1 describes what a document must contain
      when it is *issued* — `myntos.domain.faktura.compliance`
- [x] Gapless machine-assigned numbering (§ 5-1-3): a draft has **no number at
      all**, so discarding one leaves no hole. Counter row plus a unique
      constraint, not `MAX(...) + 1`. Migration `7b4e0d92a1c8`
- [x] Issue posts the double entry (kundefordringer / salgsinntekt / utgående
      mva) into its own `SALG` journal, with tax information on the basis lines;
      the ledger's VAT agrees with the document's by construction, because both
      group by the same VAT code
- [x] Kreditnota (§ 5-2-7) as the only correction, reversing through the
      ledger's own `posting.build_reversal` so it stays legible as a reversal.
      One per faktura, enforced by a unique index
- [x] EHF / Peppol BIS Billing 3.0 (UBL 2.1, EN 16931) written directly, frozen
      at issue and stored — from 2027 the XML *is* the sales document for a
      business customer — `myntos.domain.faktura.ehf`
- [x] PDF written without a PDF library (base-14 Helvetica, WinAnsi), filed into
      the append-only bilag archive at issue so the document stays readable for
      the retention period — `myntos.domain.faktura.pdf`
- [x] Company gains `vat_registered`, `in_foretaksregisteret`,
      `organisation_form` and `bank_account` — what § 5-1-2 requires a
      salgsdokument to be able to state about the seller
- [x] Frontend `/faktura/`: line editor, live compliance panel that names the
      paragraph, issue-with-confirmation, PDF and EHF download
- [ ] **Peppol access point.** Myntos produces the EHF file but does not send
      it. This is the remaining blocker for the 2027 requirement — everything
      else is in place. Either contract an access point or become one
- [ ] Foreign-currency invoicing: needs the § 5-1-1 nr. 6 restatement of VAT in
      NOK plus `TaxCurrencyCode` and a second `TaxTotal` in the XML. Refused
      outright until then, rather than issued without it
- [ ] Partial kreditnota, document-level discounts, and recurring invoices
- [ ] Validate generated EHF against the official XSD and the Peppol
      Schematron, as golden fixtures — same approach as SAF-T in phase 3
- [ ] Fill `in_foretaksregisteret` / `organisation_form` from Enhetsregisteret
      during company setup instead of defaulting them to "not registered"
- [ ] Watch the exemption rules from the 2026 law (turnover threshold, transition
      relief). They were signalled in the hearing but are **not settled**, so
      nothing is implemented against them

## Phase 2 — Bilagsføring

- [x] PDF upload attached to a posted voucher, append-only archive
      (oppbevaringsplikt): magic-number validation, 25 MB cap, generated storage
      keys, SHA-256 stored, and no delete path anywhere in the application
- [x] R2 storage backend with `jurisdiction=eu` in the endpoint, SigV4 signed
      over httpx (no boto3), verified against AWS's published test vector;
      blank or missing credentials fail at startup, not at the first upload
- [ ] Accept images as well as PDF (receipts are usually photographed), and
      generate thumbnails
- [ ] Unique filenames per melding and the other Altinn attachment rules, when
      bilag start travelling with a submission

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
