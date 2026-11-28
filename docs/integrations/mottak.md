# Mottatt — unified inbox for EHF e-invoices and email invoices

Companies receive supplier invoices and receipts through two channels:

1. **Peppol EHF** (structured UBL 2.1 XML via Ademico — [peppol.md](peppol.md)),
   which requires explicit opt-in in ELMA (`PUT …/peppol/mottak`) so Myntos
   never silently takes over an inbox hosted in another program. Activating
   EHF sending on `/faktura/` is **send-only** (`peppolRegistration: false`)
   and does not turn on receiving.
2. **Email bilagsmottak** (a dedicated `@myntos.no` address per company), so
   owners and suppliers can forward PDF invoices and receipt photos directly
   into Myntos before booking them.

Both channels land on one top-level tab — **«Mottatt»** (`/innkommende/`) —
with a clear segmented toggle (**Alle** / **Kun e-faktura (EHF)** / **Kun
e-postfaktura**), status filters (**Nye** / **Bokført** / **Avvist** /
**Alle**), and prominent sender verification on every email item.

## Per-company email address allocation

When a company is created (and backfilled for existing companies), Myntos
allocates a unique local-part under `INBOUND_EMAIL_DOMAIN` (default
`myntos.no`), stored in `company_inbox_address`:

1. Normalise the company name to lowercase ASCII alphanumeric:
   `æ → ae`, `ø → o`, `å → a`, strip diacritics and non-alphanumeric
   characters.
2. Split trailing Norwegian legal form tokens (`AS`, `ASA`, `ENK`, `DA`,
   `ANS`, `NUF`, `BA`, `SA`, `KS`, `STI`, `FLI`, `KF`, `IKS`, `BL`, `BRL`).
3. Candidate sequence (first unused, non-reserved local-part wins):
   - Candidate 1: stem without legal form (`"Test AS"` → `test@myntos.no`)
   - Candidate 2: full name with legal form (`"Test AS"` → `testas@myntos.no`)
   - Candidate 3+: numeric suffix (`testas2@myntos.no`, `testas3@myntos.no`, …)
   - Fallback when the normalised stem is shorter than 2 characters:
     `org<orgnr>@myntos.no`.
4. **Reserved local-parts** (system, RFC 2142, and Myntos addresses such as
   `kontakt`, `post`, `support`, `conduct`, `noreply`, `no-reply`, `admin`,
   `personvern`, `sikkerhet`, `faktura`, `efaktura`, `bilag`, `api`, `auth`,
   `dmarc`, `postmaster`, `abuse`, etc.) are never assigned to any customer
   company; e.g. `"Kontakt AS"` skips `kontakt` and receives
   `kontaktas@myntos.no`.
5. **Lifecycle:** Deleting an empty company cascades its `company_inbox_address`
   and frees the alias immediately. Closing a company with retained books sets
   `enabled = false` while keeping the row reserved during the five-year
   retention period so a stranger cannot register the same alias and receive
   late supplier invoices meant for the closed company.

## Transport: Cloudflare Email Routing → Email Worker → HMAC webhook

`myntos.no` already uses Cloudflare Email Routing (`route1/2/3.mx.cloudflare.net`).

1. Specific Cloudflare Email Routing rules (e.g. `kontakt@myntos.no`,
   `conduct@myntos.no`) take priority over the catch-all rule and stay
   untouched.
2. The catch-all rule routes all other `*@myntos.no` mail to a small
   Cloudflare Email Worker (`infra/cloudflare/email-worker/`).
3. The Worker checks total raw message size (≤ 25 MB), reads `message.from`,
   `message.to` and the raw RFC 5322 MIME bytes, computes an HMAC-SHA256 over
   `"<timestamp>." + raw_body` using `INBOUND_EMAIL_WEBHOOK_SECRET`, and POSTs
   to `POST /api/v1/webhooks/inbound-email` with headers:
   - `X-Myntos-Timestamp`: Unix seconds
   - `X-Myntos-Signature`: `sha256=<lowercase hex HMAC-SHA256>`
   - `X-Myntos-Envelope-From`: SMTP `MAIL FROM`
   - `X-Myntos-Envelope-To`: SMTP `RCPT TO`
4. The backend fails closed (503) if `INBOUND_EMAIL_WEBHOOK_SECRET` is unset,
   rejects invalid or stale (> 5 min) signatures with 401 (`hmac.compare_digest`),
   resolves the recipient local-part against active `company_inbox_address` rows,
   enforces a per-company hourly rate limit (`INBOUND_EMAIL_MAX_PER_HOUR`,
   default 60), parses the MIME safely with Python stdlib `email`, and stores
   each valid attachment.

### EEA / privacy note

As documented in [ARCHITECTURE.md](../ARCHITECTURE.md#what-the-mail-provider-sees),
standard internet SMTP and Cloudflare Email Routing do not guarantee EEA-only
transit before mail reaches Myntos. Once ingested by the backend, attachments
and metadata are stored exclusively in Postgres and EU-jurisdiction R2
(`jurisdiction=eu`), and optional AI reading goes only through Løynd (Norway)
when the company has `ki_enabled` turned on and the user clicks «Les med KI».
Suppliers who can send Peppol EHF should use EHF as the primary EEA-end-to-end
channel.

## Email security model

Unlike Peppol EHF (which authenticates senders through certified AS4 access
points), email can be sent by anyone on the internet. Myntos therefore adds
explicit sender visibility and policy controls on mail invoices:

1. **Sender identity and authentication on every mail item:**
   - Displays `From:` name and address, SMTP `envelope_from` (highlighted if
     different from `From:`), `Reply-To:` (highlighted if different from
     `From:` — a classic BEC invoice-fraud indicator), and `SPF`/`DKIM`/`DMARC`
     results parsed from `Authentication-Results`.
   - Classifies every mail item into a live trust level:
     - **`egen_bruker` («Egen bruker»):** sender matches a verified email of a
       current member of this company, with no authentication failure.
     - **`godkjent` («Godkjent avsender»):** sender matches an address
       (`faktura@leverandor.no`) or domain (`@leverandor.no`) on the company's
       allowlist, with no authentication failure.
     - **`ukjent` («Ukjent avsender»):** external sender not on the allowlist.
     - **`advarsel` («Sikkerhetsvarsel»):** SPF, DKIM or DMARC failed, or
       `From:` / `envelope_from` / `Reply-To:` disagree.
2. **Per-company sender policy (`sender_policy`, admin-only):**
   - `alle_med_varsel` (default): accepts mail from anyone, tags trusted vs
     unknown senders clearly, shows warnings on unverified/mismatched mail,
     and offers a one-click **«Godkjenn avsender»** button for admins.
   - `kun_godkjente`: strict allowlist mode — only accepts emails from the
     company's verified members and explicit allowlist; anything else is
     rejected at ingest before any file is stored.
3. **Attachment & content safety:**
   - Only attachments whose **magic bytes** pass
     `myntos.domain.bilag.service.validate_document` (PDF, PNG, JPEG, WebP,
     GIF, HEIC; max 25 MB per file, max 10 attachments per email) are stored.
     HTML files, executables, archives, scripts and macros are ignored.
   - Untrusted HTML email bodies are never rendered; only a short plain-text
     excerpt is stored and displayed via `textContent`.
   - Deduplication on `(company_id, message_id, attachment_index)` makes
     webhook retries idempotent.
4. **Append-only bookkeeping retention (bokføringsloven § 13):**
   - Received mail documents are never deleted while the company's books exist
     (only marked `bokfort` or `avvist`, and `avvist` can be reopened).
   - Booking (`POST …/mottak/epost/{mail_id}/bokfor`) locks the row
     `FOR UPDATE`, posts an immutable voucher via
     `ledger_service.record_cash_flow`, and files the original PDF/image into
     the append-only `bilag_attachment` archive.

## Configuration

Backend `.env`:
```
INBOUND_EMAIL_DOMAIN=myntos.no
INBOUND_EMAIL_WEBHOOK_SECRET=<random 32+ byte hex secret>
# INBOUND_EMAIL_MAX_PER_HOUR=60
```

Cloudflare Email Worker (`infra/cloudflare/email-worker/`):
1. Deploy the worker: `cd infra/cloudflare/email-worker && wrangler deploy`
2. Set the matching secret on the worker:
   `wrangler secret put INBOUND_EMAIL_WEBHOOK_SECRET`
3. In Cloudflare Dashboard → `myntos.no` → Email → Email Routing → Routing
   rules → Catch-all address: set action to **Send to a Worker** →
   `myntos-inbound-email` and enable it. (Keep existing specific routes like
   `kontakt@myntos.no` above it.)
