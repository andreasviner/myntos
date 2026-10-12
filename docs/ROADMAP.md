# Roadmap

Phases are ordered by dependency, not calendar. Anything touching Skatteetaten
has a **manual onboarding lead time** (scopes are ordered via Skatteetaten's
contact form, ID-porten/Maskinporten integrations are configured in Digdir's
Samarbeidsportalen) — start phase 0b early, it gates everything after it.

The full gap against Fiken, Tripletex and the other Norwegian programs,
ranked, is in [FEATURES.md](FEATURES.md). An item moves from there to here
when work on it starts.

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
lifecycle all exist and are verified live (below; see
docs/integrations/maskinporten.md for the module map). The items below are
registration and access, which cannot be done from a repository.

- [x] Samarbeidsportalen access (test: self-signup with org-domain email).
      Clients are registered in both environments, and their scope lists were
      read off the portal (maskinporten.md, Which scope is where)
- [x] Register Maskinporten client (test), register the per-installation public
      JWK — `uv run python -m myntos.auth.digdir_key show maskinporten` prints
      it and the `kid` to put in `MASKINPORTEN_KID`. Test client `f8293b48`,
      key registered and issuing tokens by 2026-08-23 (maskinporten.md)
- [x] Skatteetaten API access for **test**, via their ekstern-Jira form plus
      the bruksvilkår. Verified 2026-08-23 by requesting each scope against
      test.maskinporten.no: `skatteetaten:formueinntekt/skattemelding` and
      `…/eiendom` both issue tokens on the test Maskinporten client, so the
      skattemelding read and validate paths are unblocked in test
- [x] The same access for **production**, which is a separate grant, and
      `skatteetaten:mvameldingvalidering` on the production ID-porten client.
      Verified 2026-10-06: production Maskinporten issues tokens to
      `0192:938193045` for `skatteetaten:mvameldingvalidering`,
      `skatteetaten:formueinntekt/skattemelding` (+`/eiendom`) and
      `altinn:instances.write`, and the production ID-porten client accepts
      `mvameldingvalidering` plus `altinn:instances.read`/`.write` in an
      authorize request. That is the only MVA scope:
      `skatteetaten:mvameldinginnsending` is retired in favour of
      `altinn:instances.read`/`.write` (docs/integrations/mva.md, scope audit).
      `digdir:skattemelding` is on the test clients only, and production does
      not need it (below)
- [x] ID-porten integration (test.idporten.no): client registered with
      `private_key_jwt` and its own key. Verified 2026-08-23 —
      `python -m myntos.auth.digdir_check` starts an authorization request and
      ID-porten accepts the client id and redirect URI. Note the redirect URI
      is the loopback **literal**
      (`http://127.0.0.1:<port>/api/v1/skatt/idporten/callback`): ID-porten
      follows RFC 8252 and rejects the name `localhost`
- [x] Tenor synthetic test users, in `test_data/` with a README recording which
      have tax data. TestID headless login verified end to end 2026-08-24:
      `python -m myntos.auth.digdir_check --person 12869599229` completes a real
      ID-porten login (`acr=idporten-loa-high`), which is what finally exercises
      the ID-porten `kid`
- [x] Scopes on the **ID-porten** client (`5aa404aa`), which had only `openid`.
      `altinn:enduser` fixed the Altinn token exchange (any `altinn:*` scope
      does - see maskinporten.md; production holds none of it) and
      `skatteetaten:formueinntekt/skattemelding` the person's own fetch, both
      verified 2026-08-24: the whole auth chain now completes for a machine and
      for a person, and the skattemelding API answers in Norwegian prose rather
      than an empty 403. Registering a scope is per client, which was the trap -
      the organisation-level grant had been there all along
- [x] A company's skattemelding read by a person, **verified 2026-08-24**:
      a real skattemelding fetched for AS `314067630` (2025) as its daglig leder
      `08866995401`, via `digdir_check --person 08866995401 --org 314067630
      --year 2025`. Response saved as
      test_data/skattemelding-314067630-2025.xml. The auth work is done: every
      remaining 403 is a data or representation answer, not a credential one.
      That token also held `digdir:skattemelding`, which was credited for it;
      re-run without it on 2026-10-09 and the same melding came back, so it is
      not required (maskinporten.md, Representing a taxpayer)
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
- [x] The MVA validation call, live, **answered 2026-10-10** by
      `digdir_check --person 05849898878 --mva 311124226 --save <dir>` from the
      TT02 copy: the melding Myntos built passes, and the one finding is about
      test data, not the melding. The run, the finding and the hosts are in
      mva.md (Running the validation live); what is left is the Phase 4 item
      **Live in TT02** below
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
- [x] Tests against Skatteetaten's SAF-T schemas and example files
      (`tests/fixtures/saft/`): the examples validate as a control, and the
      exporter's output validates against both v1.30 and v1.40, from a
      hand-built snapshot and from real postings through the API. Not a byte
      comparison with the examples, which are generated data with fields
      Myntos does not hold

## Phase 1b — Accounts, organizations and access control

- [x] Company-as-tenant model: a `CompanyMembership` row per person per
      company is what grants access, and `plan` is on the company, so a
      subscription attaches to a set of books rather than to a user or an
      account. `Organization` only groups the companies set up together and
      says who may add another. Since 2026-09-03, migration `d1a7f2c6b483`;
      before that the organization was the tenant (`3d7c15ba90e4`).
      `company.orgnr` is unique per organization, not globally — two firms may
      keep books for the same client
- [x] Login with email/password (Argon2id, per-account lockout) or a federated
      provider — **Google and Microsoft (Entra ID)** over one OIDC
      implementation, listed by `/auth/providers` only when configured. One
      account can hold several, and a federated identity is linked to an
      *existing* account only on a provider-**verified** email (takeover
      defence; Microsoft needs the `xms_edov` optional claim for that). Vipps
      Login was built and removed on 2026-10-10 without ever being configured:
      it costs from 300 kr a month, and Vipps stops giving out the
      fødselsnummer in Norway from 2027-01-01
- [x] **BankID** sign-in through an OIDC broker (Idura by default; any broker is
      configuration), `private_key_jwt` client authentication, keyed on the
      broker's `sub`, no fødselsnummer asked for. BankID has no address, so it
      reaches an existing account only by linking from `/konto/`, and a BankID
      signup asks for an address and refuses one that already has an account.
      Migration `7a3e5c1d9b24`; see
      [docs/integrations/bankid.md](integrations/bankid.md)
- [x] **A BankID account signs in with BankID only** (2026-10-10). BankID's
      terms forbid ID-veksling without a paid permission, so no password, Google
      or Microsoft beside it: linking BankID is a switch that removes them
      (password required first), and leaving BankID goes through a mailed link
      that sets the password. No migration
- [ ] Ask Stø, through Idura, to confirm the switch from BankID to e-mail, and
      the other two open questions in
      [docs/integrations/bankid.md](integrations/bankid.md), when the agreement
      is ordered
- [ ] Release an address squatted by a BankID signup: a reset no longer reaches
      such an account, so the real owner of an unconfirmed address has no way to
      free it
- [ ] Turn BankID on: an Idura sandbox tenant and application, then production
      (Norwegian BankID ordered from Stø through Idura's dashboard, 10–13
      working days). Steps in [docs/integrations/bankid.md](integrations/bankid.md)
- [ ] Prune `oauth_login_attempt`: rows are tiny and dead after 15 minutes, and
      link attempts now carry the account that started them
- [x] Revocable server-side sessions (opaque token, stored hashed, httpOnly
      cookie or bearer), idle + absolute expiry, origin-checked writes
- [x] Roles (owner/admin/member/viewer) in one permission table; only an owner
      may create or remove owners, and the last owner cannot leave
- [x] Invitations: single-use hashed token, addressed to an email and naming
      one company, which is all it grants — `myntos.domain.identity`
- [x] Frontend: login/signup/invitation pages; every company- and ledger-scoped
      endpoint resolves the caller's membership before answering, and any
      company the caller has no `CompanyMembership` on is a 404 rather than a
      403 (`api/deps.py`)
- [x] Email delivery (Cloudflare Email Service): address confirmation,
      invitation mails, password reset, each link single-use and hashed. Reset
      never reveals whether an address has an account. Unconfirmed accounts
      keep every right they had; they only lose Reply-To on invitations they
      send — `domain/identity/account_mail.py`, migration `c5e8a17d3f42`
- [ ] Pre-hijack defence: a verified Google/Microsoft login landing on an
      unconfirmed password account with the same address should clear the
      password and end its sessions. Deferred because it also hits today's
      legitimate unconfirmed users
- [x] Member-management UI (`/organisasjon/`): invite with a role, change roles,
      remove or leave. The invite link is shown once, to copy and pass on
- [x] Self-service session list ("sign out this device") and account deletion
      (GDPR erasure) at `/konto/`. The person is erased; what the books must keep
      names them through a tombstone row (id and name only). See docs/gdpr.md —
      migration `c3e8a5d1f720`
- [ ] Expire tombstone names once nothing retained names them any more, and
      keep a restore from Neon point-in-time backups from bringing an erased
      account back

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
- [x] The starter chart's `GroupingCategory`/`GroupingCode` pairs checked
      against Skatteetaten's 2025-2026 list. Half did not exist (`lonnskostnad`,
      a post 4000, `NA` on every balance account); corrected in the chart and
      for existing companies by migration `b7d40e2c9a15`, and a test now checks
      every seeded pair against the vendored list
- [ ] Check the chart against the 2027 grouping list when Skatteetaten
      publishes it (procedure at the end of docs/integrations/saf-t.md)
- [x] Editing the kontoplan (`/kontoplan/`): add, rename, deactivate, choose a
      næringsspesifikasjon post from the published list, refused in a closed
      year. VAT registration with an effective date, read on each entry's own
      date; an unregistered company books gross to cost — migration
      `c5a2e7d94b13`
- [ ] `vat_registered_until`, so a company that deregisters keeps its deduction
      codes for corrections dated while it was registered
- [x] Customers and suppliers (SAF-T `Party`) with balance accounts derived from
      the type, optional on an entry, carried on the basis line and preserved
      through a reversal — `myntos.domain.ledger.parties`, `/parter/`
- [x] Bank import from camt.053 (`/bank/`): statements verified against their
      own arithmetic, archived, deduplicated on the bank's reference;
      suggested matches the user confirms, or a new voucher from a line through
      the register's own path. See docs/integrations/bank.md — migration
      `c3e8a5f1d207`
- [ ] PSD2 feed, foreign-currency accounts, one-to-many matches, and posting a
      KID payment against kundefordringer

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
      `organisation_form` and a bank account — what § 5-1-2 requires a
      salgsdokument to be able to state about the seller
- [x] Frontend `/faktura/`: line editor, a review step that names the paragraph
      for every finding before anything is issued, PDF and EHF linked from the
      result rather than only from the list
- [x] Frontend `/organisasjon/#selskap`: the company's own details — kontonumre,
      mva-registrering, Foretaksregisteret, address. Until it existed the fields
      § 5-1-2 requires could only be set at company creation, and were not set
      there either
- [x] An invoice cannot be issued without a kontonummer, and the number is
      mod-11 checked when it is entered (`domain/ledger/bankkonto.py`). Not a
      § 5-1 requirement — it blocks because the alternative is a numbered,
      posted, archived invoice with nowhere to pay it
- [x] **Several kontonummer per company** (`CompanyBankAccount`), one of them
      the primary, and a per-invoice choice between them
      (`SalesDocument.bank_account_id`). The column it replaced could hold one
      account, so a business keeping its skattetrekk apart — skattebetalingsloven
      § 5-12 — had nowhere to put the second one
- [ ] **Peppol access point.** Myntos produces the EHF file but does not send
      it. This is the remaining blocker for the 2027 requirement — everything
      else is in place. Either contract an access point or become one
- [ ] Foreign-currency invoicing: needs the § 5-1-1 nr. 6 restatement of VAT in
      NOK plus `TaxCurrencyCode` and a second `TaxTotal` in the XML. Refused
      outright until then, rather than issued without it
- [ ] Partial kreditnota, document-level discounts, and recurring invoices
- [x] Validate generated EHF against the UBL 2.1 XSD, CEN EN 16931 1.3.16 and
      Peppol BIS Billing 3.0.21 Schematron in the test suite, with the official
      examples as a control. Every document Myntos produced failed at least one
      fatal rule before this; all fixed (docs/integrations/efaktura.md)
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
- [x] Accept images as well as PDF, including HEIC, and generate thumbnails.
      Originals byte for byte; derived copies stripped of EXIF/GPS and decoded
      in a sandboxed child process. No PDF thumbnails: that needs a native
      renderer, which was decided against
- [ ] Unique filenames per melding and the other Altinn attachment rules, when
      bilag start travelling with a submission

## Phase 3 — SAF-T Financial export (legal requirement)

- [x] Version-parameterized SAF-T export: **v1.30** (usable until
      2026-12-31) and **v1.40** (mandatory for periods from 2027-01-01),
      selected by financial year; vendored XSDs + official examples as
      fixtures (github.com/Skatteetaten/saf-t, which carries no licence file);
      own cross-reference validation that blocks the download (Altinn only
      checks the XSD) — `myntos.saft`, `/saft/`, docs/integrations/saf-t.md
- [x] Opening-balance import (`/inngaende-balanse/`, typed or saldobalanse
      CSV) and årsavslutning (`/arsavslutning/`) for AS and ENK, with a year
      lock and a logged reopening. SAF-T takes opening balances from them —
      migration `c3f9a2d71e58`
- [ ] An accountant's review of the disposition rules, and of leaving the
      opening voucher out of SAF-T's entries. Dividends (8920/2800), deferred
      tax, and archiving the old system's saldobalanse as a bilag
- [ ] `Owners` in MasterFiles (from Enhetsregisteret's roles or the
      aksjonærregister), once there is a reason to store them
- [ ] § 7-5 compliance features: notification-letter generator,
      readable/printable-in-Norway guarantee, full-data repatriation export

## Phase 4 — MVA (VAT)

- [x] MVA calculation per SAF-T code; aggregation to MvaSpesifikasjonslinje
      (`domain/mva/melding`), with the exclusion list, each code reported the
      way Skatteetaten's examples report it (sales with basis and rate,
      deductions negative and bare, reverse charge on both sides), and the XML
      validated against the published XSD in the test suite
- [x] The official code list, checked against `mvaKodeSAFT.xml`: 33 is 12 %
      and 32 is fish, which the seed had backwards (migration `e1cb264f0c44`
      renames in place). Added 12, 14, 15, 32, 51, 84, 85, 88, 89, 91, 92, and a
      payment category for border VAT on a tollpass (`mva_innforsel`, code 14)
- [x] Periods and deadlines (`domain/mva/periods`): two-monthly, monthly,
      yearly; alminnelig and primærnæring; a setting per company
- [x] ID-porten OIDC flow (`auth/idporten`) + Altinn token exchange
      (`integrations/altinn/tokens`): PKCE, `private_key_jwt` client
      authentication, and the `acr` claim checked rather than merely requested
- [x] The HTTP routes, and where a government token lives between the callback
      and the submission (`api/v1/skatt`, `auth/idporten/vault`,
      `government_login_attempt`). What was decided, and why:
      - **A row, not process memory.** The token must survive a restart or a
        deploy between the callback and the submission (`./update.sh` restarts
        the unit), the same reasoning `OAuthLoginAttempt` records
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
- [x] Validation API → Altinn 3 instance flow → feedback, wired end to end
      (`api/v1/mva`): check, file exactly the checked bytes, refuse when the
      ledger moved, record a failure halfway instead of rolling it back, store
      the three documents and read the KID out of betalingsinformasjon. The
      envelope is now PUT into the element the app creates, which the client
      used to POST beside it
- [x] Settlement: the period's 2700 and 2710 moved onto 2740 on filing; a
      correction settles only the difference
- [x] Review-and-confirm UI (`/mva/`): every line, the backend's blockers,
      Skatteetaten's findings, and a confirmation naming the company, the
      period and the amount
- [ ] **Live in TT02**: validation answers (2026-10-10) and refuses only on
      test data, a company TT02's MVA register does not hold; the host is `idporten-api-test.sits.no`, since
      `idporten-api-sbstest` still resets connections; the envelope without a namespace and
      the PUT are per Skatteetaten's examples and guide, unproven against the
      running app. Then the production grant
- [x] `spesifikasjon` in the ledger (`/mva-justeringer/`): uttak, tap på krav,
      justering and tilbakeføring, shaped as in Skatteetaten's
      eksempelMedAlleTilfeller.xml and validated against the XSD
- [ ] The Maskinporten lookups: `mvaregisteravgiftssubjekt` (is the company
      registered) and `mvameldinginnsendingstatus` (what is due, per client)
- [ ] The other meldingskategorier: `omvendtAvgiftsplikt` (unregistered
      businesses buying services from abroad) first, `kompensasjon` if asked
- [ ] Public holidays in deadlines

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
      systembruker is built (phase 0b above). Altinn 2 shut down 2026-06-19,
      and tilgangspakker replace roles in access checks from 2026-12-01

## Phase 6 — Årsregnskap til Regnskapsregisteret

"Regnskapsmelding" is not a live API (July 2026); the real channel is
Brønnøysundregistrene's **maskinell innrapportering** — see
docs/integrations/skatteetaten.md.

- [ ] Altinn 3 client for the brreg årsregnskap apps
      (`aarsregnskap-vanlig-202406` et al.) — reuses our Maskinporten/Altinn
      plumbing, only needs `altinn:instances.read/write`
- [ ] Fill-with-systembruker → **sign with ID-porten** (signing auto-submits;
      a personnummer is legally attached to the signature)

## Phase 7 — Agents and the public API

Rules decided 2026-10-10 in [agents-and-api.md](agents-and-api.md); they apply
to every feature from now on, not only to this phase.

- [x] The rules: one public API, a skill per feature, keys per person per
      company, single-use connection keys exchanged for rotating refresh
      tokens, what a key may never do, two-step irreversible writes
- [x] Keys and the exchange (2026-10-11, not deployed; migration
      `9d2b6f4e1a37`): the company's switch, connection keys,
      `POST /api/v1/agent/token`, rotation with reuse detection, revocation,
      the gate that holds every route to a declared key scope, the key recorded
      on vouchers, the Tilgang card, the mails, `llms.txt` and the
      `myntos-connect` skill. Gaps in [agents-and-api.md](agents-and-api.md#gaps)
- [x] The test that holds every new route to the rules
      (`backend/tests/test_agent_surface.py`) and the skill template
      (`docs/agents/README.md`); the routes that predate it stand on two lists
      that only shrink
- [ ] The front door: empty those lists (a description on every older route,
      and a skill for each route a key may call), the published OpenAPI
      document, the feature skills
- [ ] OAuth 2.1 with PKCE and a remote MCP server, so agents connect without a
      pasted key
- [ ] Developers: webhooks, sandbox companies, a developer page

## Phase L — Landbruk, an add-on for farms (pilot: Reime Gard)

Started 2026-10-12, after a farmer told the owner that Tripletex handles farms
badly. Myntos stays a program for everyone: a farm gets an add-on on top, and
nothing in it changes what other companies see. The rules are in
[landbruk.md](landbruk.md); the rest of the gap is the Landbruk section of
[FEATURES.md](FEATURES.md). Reime Gard (ENK) has offered to run Myntos beside
its current system, with its accountant checking the books and the year-end.

- [x] Composite entries for everyone (`POST …/cashflow/sammensatt`, the
      «Del opp i flere linjer» editor on `/kontantstrom/`): one payment, one
      line per trade, each with its own category and VAT. Migration
      `cddd50896de9` (`gl_transaction_line.entry_type`)
- [x] The add-on and its switch (`company.landbruk`, on by næringskode 01/02
      unless a person says otherwise; `GET`/`PUT …/landbruk`, the «Landbruk»
      card on `/kontoplan/`)
- [x] 62 accounts from NIBIO's Landbrukets kodesystem 1.3.6, each with its
      næringsspesifikasjon post; farm categories with the farm VAT rates (milk,
      slaughter, live animals and grain at 25 %), the settlement deductions,
      a category per tilskudd, skogfond
- [x] Settlements read as sales: `issued_by_buyer` in the bilag reader, and
      the model told so for farm books
- [x] Skill `myntos-landbruk`, help area `farming`
- [ ] **The pilot.** Reime Gard's opening balance (from its current system),
      a month of settlements booked in both, the accountant's review of the
      groupings and of booking omsetningsavgift against the sale
- [ ] Two VAT returns in one company (FEATURES 23b)
- [ ] Manual journal entries for the year-end adjustments (FEATURES 23a), then
      the livestock and feed count at the takseringsregler rates
- [ ] Fixed assets with the farm rules (FEATURES 7), the næringsspesifikasjon
      with the jordbrukskonto and tømmerkonto cards (FEATURES 2)
- [ ] The decision on Landbrukets Dataflyt (about 325 000 kr a year;
      [integrations/landbrukets-dataflyt.md](integrations/landbrukets-dataflyt.md))
- [ ] Bank import: split a bank line straight into a composite entry, rather
      than registering the settlement first and matching it after

## Continuous / before first public release

- [ ] Threat model review; independent security review of auth + storage
- [x] CODEOWNERS
- [ ] OpenSSF Scorecard action + bestpractices.dev badge
- [x] Code of Conduct (Contributor Covenant 3.0), reports to conduct@myntos.no
- [x] License: **proprietary, decided 2026-10-10.** Myntos is to be a paid
      hosted service, and of the options weighed only this one leaves that
      intact: AGPL forecloses a commercial hosted offering without a CLA from
      every contributor, and FSL/BSL would convert each release to Apache-2.0
      after the change date. The source stays **public and readable** —
      source-available, not open source, and the README says so plainly.
      Consequences that stay in force:
      - Code contributions need a signed CLA or copyright assignment
        (CONTRIBUTING.md). Without one the contributor keeps the copyright and
        the license cannot cover their code.
      - No copyleft in what is *distributed*. The dependency tree is MIT, BSD
        and Apache-2.0, plus LGPL-3.0 in libheif/libde265 (bundled by pi-heif
        for HEIC). Those run on our server and are not distributed, which LGPL
        permits; re-check if Myntos is ever shipped for self-hosting.
      - If the Maskinporten client or SAF-T exporter are ever extracted as
        standalone libraries, MIT/Apache-2.0 for those remains sensible — they
        carry no business value and adoption helps other Norwegian projects.
- [ ] Dialogporten integration for receipts/correspondence (post-June-2026
      Altinn pattern)
- [ ] Legal review of DISCLAIMER.md before offering any hosted service
