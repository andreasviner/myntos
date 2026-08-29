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

The code this onboarding feeds is in place — Maskinporten's JWT grant,
ID-porten's authorization-code flow, the Altinn token exchange and the instance
lifecycle all exist and are tested against stubs (see
docs/integrations/maskinporten.md for the module map). What remains below is
registration and access, which cannot be done from a repository.

- [ ] Samarbeidsportalen access (test: self-signup with org-domain email)
- [ ] Register Maskinporten client (test), register the per-installation public
      JWK — `uv run python -m myntos.auth.digdir_key show maskinporten` prints
      it and the `kid` to put in `MASKINPORTEN_KID`
- [x] Skatteetaten API access for **test**, via their ekstern-Jira form plus
      the bruksvilkår. Verified 2026-08-23 by requesting each scope against
      test.maskinporten.no: `skatteetaten:formueinntekt/skattemelding` and
      `…/eiendom` both issue tokens on the test Maskinporten client, so the
      skattemelding read and validate paths are unblocked in test
- [ ] The same access for **production**, which is a separate grant — and the
      two MVA scopes (`skatteetaten:mvameldingvalidering`,
      `skatteetaten:mvameldinginnsending`), which are ID-porten scopes and
      therefore untestable until the ID-porten client exists
- [x] ID-porten integration (test.idporten.no): client registered with
      `private_key_jwt` and its own key. Verified 2026-08-23 —
      `python -m myntos.auth.digdir_check` starts an authorization request and
      ID-porten accepts the client id and redirect URI. Note the redirect URI
      is the loopback **literal** (`http://127.0.0.1:8000/...`): ID-porten
      follows RFC 8252 and rejects the name `localhost`
- [x] Tenor synthetic test users, in `test_data/` with a README recording which
      have tax data. TestID headless login verified end to end 2026-08-24:
      `python -m myntos.auth.digdir_check --person 12869599229` completes a real
      ID-porten login (`acr=idporten-loa-high`), which is what finally exercises
      the ID-porten `kid`
- [x] Scopes on the **ID-porten** client (`5aa404aa`), which had only `openid`.
      `altinn:enduser` fixed the Altinn token exchange and
      `skatteetaten:formueinntekt/skattemelding` the person's own fetch, both
      verified 2026-08-24: the whole auth chain now completes for a machine and
      for a person, and the skattemelding API answers in Norwegian prose rather
      than an empty 403. Registering a scope is per client, which was the trap -
      the organisation-level grant had been there all along
- [x] `digdir:skattemelding` on both clients, granted and **verified 2026-08-24**:
      a real skattemelding fetched for AS `314067630` (2025) as its daglig leder
      `08866995401`, via `digdir_check --person 08866995401 --org 314067630
      --year 2025`. Response saved as
      test_data/skattemelding-314067630-2025.xml. The auth work is done: every
      remaining 403 is a data or representation answer, not a credential one
- [x] Retrieval **without a person present**, via Altinn 3 systembruker.
      **Verified end to end 2026-08-28**: a client company's skattemelding
      fetched 200 on the machine plane, where the same call without a system
      user answers 400 `SME-EKSTERN-005`. The exchange accepts a systembruker
      token too, so the instance APIs are reachable this way as well.
      - System registered as `938193045_myntos`
        (`integrations/altinn/systemregister`), visible in Altinn's catalogue
      - System-user request and approval polling
        (`integrations/altinn/systemuser`)
      - RFC 9396 `authorization_details` in the JWT grant, customer in the cache
        key (`auth/maskinporten/client`)
      - `externalRef` is part of the system user's identity, not a label: omit it
        at token time and Maskinporten answers 404
        `invalid_altinn_customer_configuration`. Required by the module for that
        reason
      - This also outlives the 2026-12-01 change, since it does not rest on
        Altinn roles
- [ ] Use it: put the fetch/validate/upload steps on a schedule now that they no
      longer need somebody logged in. Submission still requires a person, by
      Skatteetaten policy, so the human checkpoint stays exactly where it was
- [ ] **2026-12-01: Altinn roles stop counting in access checks** (tilgangspakker
      replace them). The proven fetch works because daglig leder is a role, so
      re-verify the whole org path before that date
- [x] Altinn test platform: the Maskinporten-to-Altinn token exchange against
      `platform.tt02.altinn.no` works (verified 2026-08-23)
- [x] A first real instance on both TT02 apps, **verified 2026-08-24**:
      `skd/formueinntekt-skattemelding-v2` as daglig leder `08866995401` for AS
      `314067630`, and `skd/mva-melding-innsending-v1` as `05849898878` for
      `311124226`. Created and read back only - `process/next` was deliberately
      not called, since the second call submits. It found three things unit tests
      against stubs could not:
      - `ALTINN_MVA_APP_URL` pointed at `mva-melding-innsending-etm2`, which
        **does not exist** on tt02 (418). The test app carries the same `-v1`
        name as production; environments differ by host, not by app name
      - `METADATA_DATA_TYPE` was `mvaMeldingInnsending`; the app declares
        `no.skatteetaten.fastsetting.avgift.mva.mvameldinginnsending.v1.0`
      - `ATTACHMENT_DATA_TYPE` was `mvamelding-vedlegg`; the app declares
        `binaerVedlegg`
      Each would have been a 400 at upload time. Every data-type constant in
      both clients is now read off `GET {app}/api/v1/applicationmetadata`
- [x] Upload a data element and advance the process, **verified live 2026-08-24**
      on `skd/formueinntekt-skattemelding-v2`: create -> upload
      `skattemeldingOgNaeringsspesifikasjon` -> `process/next` all succeed. It
      found a bug no stub could: Altinn requires **Content-Disposition on every
      upload**, and we only sent it when a caller passed a filename - so the
      envelope and the MVA metadata, both uploaded unnamed, would always have
      failed with 400 "The request must include a Content-Disposition header"
- [ ] The **second** `process/next`, which submits. Everything before it is
      proven. Needs a real envelope rather than the minimal one used to prove
      the transport, since a submission is a filing even in TT02
- [ ] The MVA validation call, live. `idporten-api-sbstest.sits.no` resolves but
      resets the connection (three attempts, while its sibling hosts answer), so
      this is blocked on Skatteetaten's test environment rather than on us. The
      URL and path match their documentation. While looking, found that the
      endpoint documents `Content-Type: application/xml` where we were sending
      `text/xml` - fixed
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
- [x] Frontend `/faktura/`: line editor, a review step that names the paragraph
      for every finding before anything is issued, PDF and EHF linked from the
      result rather than only from the list
- [x] Frontend `/organisasjon/#selskap`: the company's own details — kontonummer,
      mva-registrering, Foretaksregisteret, address. Until it existed the fields
      § 5-1-2 requires could only be set at company creation, and were not set
      there either
- [x] An invoice cannot be issued without a kontonummer, and the number is
      mod-11 checked when it is entered (`domain/ledger/bankkonto.py`). Not a
      § 5-1 requirement — it blocks because the alternative is a numbered,
      posted, archived invoice with nowhere to pay it
- [ ] **Peppol access point.** Myntos produces the EHF file but does not send
      it. This is the remaining blocker for the 2027 requirement — everything
      else is in place. Either contract an access point or become one
- [ ] Foreign-currency invoicing: needs the § 5-1-1 nr. 6 restatement of VAT in
      NOK plus `TaxCurrencyCode` and a second `TaxTotal` in the XML. Refused
      outright until then, rather than issued without it
- [ ] Partial kreditnota, document-level discounts, and recurring invoices
- [ ] Validate generated EHF against the official XSD and the Peppol
      Schematron, as golden fixtures — same approach as SAF-T in phase 3
- [x] Fill `vat_registered` / `in_foretaksregisteret` / `organisation_form`
      from Enhetsregisteret during company setup instead of defaulting them to
      "not registered" — `registrertIForetaksregisteret` is in the register's
      answer, so none of it is a guess
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
- [x] ID-porten OIDC flow (`auth/idporten`) + Altinn token exchange
      (`integrations/altinn/tokens`): PKCE, `private_key_jwt` client
      authentication, and the `acr` claim checked rather than merely requested
- [x] The HTTP routes, and where a government token lives between the callback
      and the submission (`api/v1/skatt`, `auth/idporten/vault`,
      `government_login_attempt`). What was decided, and why:
      - **A row, not process memory.** The backend sleeps between requests, so
        the same reasoning `OAuthLoginAttempt` records applies here
      - **A separate table from the sign-in flow.** One table would let a
        government callback complete a sign-in, or the reverse. A Myntos session
        is never evidence of anything to Skatteetaten, so the two never share a
        row
      - **Encrypted with Fernet, and no key means the feature is off** rather
        than the token being stored readable. It authorizes filing in a named
        person's name
      - **Bound to the user who started the flow**, expiring at ID-porten's own
        `exp`, single use. A leaked callback URL cannot attach a government
        identity to another account
      - The `acr` check happens once, at the callback, so nothing downstream has
        to remember to verify what it is trusting
- [ ] Validation API → Altinn 3 instance flow → feedback: the channel exists
      (`integrations/skatteetaten/mva`), including fetching the three documents
      as a set. What remains is building the melding and persisting
      valideringsresultat, kvittering PDF and betalingsinformasjon/KID with the
      period's bilag
- [ ] Review-and-confirm UI before any submission (user sees the full melding)

## Phase 5 — Skattemelding + næringsspesifikasjon

- [ ] Build næringsspesifikasjon from the ledger (formats per
      github.com/Skatteetaten/skattemeldingen, api-v2)
- [ ] Fetch current skattemelding (Maskinporten), validate + submit,
      receipts. The client is written (`integrations/skatteetaten/client`) and
      `submit` takes the person's Altinn token as a required argument, so the
      rule that a systembruker may fetch/validate/create/upload but **the final
      submit requires an ID-porten-logged-in person** is visible in the
      signature. What remains is the envelope, the async job API for
      næringsspesifikasjoner over 10 MB, and the review-and-sign UI step
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
