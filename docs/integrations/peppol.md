# Sending and receiving EHF over Peppol (Ademico)

From 1 January 2027 a sale to another bokføringspliktig is documented by an
e-invoice, and an e-invoice is only delivered when it travels over Peppol
(receiving becomes mandatory from 1 January 2030). Myntos writes outgoing EHF
XML and freezes it at issue ([efaktura.md](efaktura.md)); this is how the
frozen file reaches the customer and how supplier EHF invoices arrive in Myntos.

Peppol is not something a program joins on its own: every sender and every
receiver goes through an access point certified by OpenPeppol. This deployment
uses **Ademico Software** (Belgium, EEA), through its Peppol REST API
(`https://peppol-api1.ademico-software.com/domibus` for Basic Auth,
`https://peppol-api.ademico-software.com` for OAuth2), chosen on 2026-11-25 in
place of Recommand (which was built 2026-11-01 as send-only).

Code:
- `backend/src/myntos/integrations/ademico.py` (the Ademico REST client)
- `backend/src/myntos/integrations/peppol_smp.py` (direct public OpenPeppol BDXL SML DNS NAPTR + SMP XML lookup for pre-send recipient checks)
- `backend/src/myntos/integrations/peppol_sync.py` (Ademico's webhook and the sweep behind it, for all companies at once: every push read back from Ademico before it is believed; the pull queue drained and Ademico's own document list checked every 15 minutes; and the `show`/`on`/`off` command that tells Ademico where to push)
- `backend/src/myntos/domain/peppol/` (`models.py`, `service.py`, `errors.py`, `ubl_reader.py`, `incoming_pdf.py`; makes no network calls)
- `backend/src/myntos/api/v1/peppol.py` (registration, receiving opt-in/out, recipient lookup, sending, delivery status, Ademico's webhook, sync, and incoming inbox routes)
- Migrations: `8d7b599c0754` (initial send tables) and `c7e3b91a4f20` (Ademico receiving fields + `peppol_incoming_document`)

Frontend:
- «Utsted og send som EHF», «Send som EHF» and delivery status on `/faktura/`
- «E-faktura» card (sending registration + explicit «Mottak av e-faktura» opt-in/out) on `/organisasjon/`
- «Innkommende e-faktura» inbox and booking page on `/innkommende/`
- Shared labels and copy helpers in `frontend/src/lib/peppol.ts`

Tests: `backend/tests/test_peppol.py`, against an Ademico stand-in modeled directly from live Ademico TEST recordings (2026-11-09 / 2026-11-25).

## Core decisions

### Peppol only, never e-mail

Myntos never asks Ademico to send invoices by e-mail and sends no company
e-mail address or phone number when registering a legal entity. A customer who
cannot receive over Peppol is refused (`recipient_not_on_peppol`) with a
message telling the user to send the PDF themselves.

### The frozen XML, byte for byte

`POST /api/peppol/v1/invoices/ubl-submissions` sends the stored `ehf_xml`
unchanged as `multipart/form-data`. Ademico validates XSD/Schematron and queues
the AS4 delivery. Nothing is regenerated from JSON.

### Send-only by default; required before first business invoice

E-invoicing is the default in Myntos, and from 1 January 2027 it is mandatory
for B2B sales to other bokføringspliktige. Whenever this deployment has Ademico
configured (`available: true`), a company must activate EHF sending before
issuing its first business invoice (`buyer_is_business: true`):

- On `/faktura/`, companies with `can_send: false` see the «Sett opp e-faktura
  (EHF) før første faktura» card ahead of the form, with one-click inline
  activation («Aktiver e-faktura (EHF)») once confirmed through Altinn, or a
  direct link to ID-porten confirmation. Private-consumer (B2C) invoicing stays
  reachable through «Skriv faktura uten e-faktura».
- In the API (`GET …/kontroll` and `POST …/faktura/{id}/utsted`), business
  drafts carry a blocking violation (`field: "peppol_registration"`) until
  `can_send` is true. Private-consumer drafts (`buyer_is_business: false`) are
  not blocked.

Registering a company (`POST …/peppol`) creates (or adopts) the legal entity at
Ademico with `peppolRegistration: false` and `publishInPeppolDirectory: false`.
That enables **sending** immediately without touching ELMA or taking over any
inbox the company currently receives supplier invoices in.

Enabling **receiving** (`PUT …/peppol/mottak` with `{"enabled": true}`) is a
separate, explicit admin action with an on-screen confirmation warning:
1. It checks that no other active Myntos company already holds receiving for
   that `orgnr` (`uq_peppol_registration_receiving_orgnr`, 409
   `receiving_already_claimed`).
2. It updates the Ademico registration to `peppolRegistration: true` and
   `publishInPeppolDirectory: true`, publishing `0192:<orgnr>` in SML/SMP
   (ELMA) for `PEPPOL_BIS_BILLING_UBL_INVOICE_V3` and
   `PEPPOL_BIS_BILLING_UBL_CREDIT_NOTE_V3`.
3. If the orgnr is already registered to receive at another access point (e.g.
   Fiken or Tripletex), Ademico returns `PARTICIPANT_ERROR`, which Myntos
   translates into 409 `receiving_conflict` telling the admin to switch off
   receiving in the previous program first.
4. Turning receiving off (`{"enabled": false}`) sets `peppolRegistration: false`
   at Ademico, freeing the orgnr in SML/SMP while keeping sending active.

### Who may act as the company

Ademico accepts requests for any legal entity under the deployment's
credentials, so Myntos enforces ownership before Ademico is called:
1. **Altinn confirmation required** (`Company.verified_at`). Unconfirmed
   companies are refused with 409 `company_not_confirmed`.
2. **Admins and owners only** (`ORGANIZATION_MANAGE`) may register or toggle
   receiving, and **no agent key** may call either route (`_NOT_FOR_AGENTS`).
3. **One receiving inbox per orgnr** across all active companies in Myntos.
   Closing a company (`domain/identity/closure.py`) clears its
   `receiving_enabled` flag so locked books cannot steal incoming invoices.

### Direct public Peppol SML/SMP recipient lookup

Ademico's REST API has no participant-lookup endpoint. Instead,
`GET …/peppol/mottaker` queries OpenPeppol's public BDXL SML DNS `NAPTR` record
(`<base32(sha256("0192:<orgnr>"))>.iso6523-actorid-upis.participant.sml.prod.tech.peppol.org`)
and reads the participant's public SMP XML ServiceGroup (e.g.
`https://smp.elma-smp.no/iso6523-actorid-upis::0192%3A<orgnr>`) to verify
support for BIS Billing 3.0 Invoice or CreditNote. No credentials or accounting
data leave Myntos.

### A webhook, with a sweep behind it

Ademico reports events for **every** legal entity under this deployment's
credentials: deliveries (`DOCUMENT_SENT`, `DOCUMENT_SEND_FAILED`), a buyer's
answer (`INVOICE_RESPONSE_RECEIVED`, `MLR_RECEIVED`) and received documents
(`DOCUMENT_RECEIVED`). Until 2026-11-25 Myntos asked for them every minute
(`GET /api/peppol/v1/notifications`). Now Ademico pushes each event to
`POST /api/v1/webhooks/ademico` as it happens (`peppol_sync.handle_push`), and
a slow sweep catches whatever no push brought.

**What Ademico is told.** `PUT /api/peppol/v1/settings/webhooks` with
`active: true`, the URL in `ADEMICO_WEBHOOK_URL`, Basic Auth with user
`ademico` and password `ADEMICO_WEBHOOK_SECRET`, and `bulk: false`: one event
per request, so a retry repeats only the event that could not be confirmed,
and each request stays well inside Ademico's timeout (a received invoice costs
Myntos two calls back to Ademico). `PUT` rather than `PATCH`, so nothing set by
hand in Ademico's portal survives beside it. From `backend/`:
`uv run python -m myntos.integrations.peppol_sync show|on|off`. It never prints
the secret.

**A push is a hint, never a fact.** Ademico cannot sign a webhook body; Basic
Auth is all it can send. So a push only says *which document to look at*.
Before anything is stored, that document is read back from Ademico with the
deployment's own credentials (`GET …/documents?transmissionId=…&direction=…`),
and only Ademico's answer counts:
- Its `receiver` decides whose inbox. Routing by the push's own `receiver`
  would let one forged field put one company's supplier invoice in another
  company's inbox.
- Its `documentStatus` decides delivery. A push claiming a failure loses to a
  record that says delivered. The push's `details` are only used as the
  reason the seller reads for a failure Ademico itself reports.
- A delivery event for a send Myntos never made is not looked up at all.

Mollie's webhook takes the same stance (`api/v1/ki.py`).

**What the webhook answers.**
- 503 when no secret or no access point is configured.
- 401 without the exact credentials, logged at WARNING (anyone can knock; a
  wrong secret at Ademico shows up as the sweep's ERROR instead).
- 503 when Ademico cannot confirm an event yet, so Ademico pushes it again: a
  received document its list does not show yet, a `DOCUMENT_SENT` or
  `DOCUMENT_SEND_FAILED` whose record still reads as pending, or Ademico not
  answering.
- 200 otherwise, including for an event Myntos does not act on and for a body
  it cannot read. That last one is logged at ERROR with the body's type and
  key names only, never its values: Ademico does not document what it pushes,
  so this is the line that says its format is not what Myntos expects.
- A handled event is also deleted from the pull queue, in case Ademico keeps a
  copy there. A 404 there means it does not.

**The sweep is not optional.** Ademico documents neither the body it pushes,
nor whether it retries a push that failed, nor whether a pushed event also
stays in the queue, and every `./update.sh` is a window in which a push meets
a closed port. So every `PEPPOL_SWEEP_INTERVAL_SECONDS` (900), and on
`POST …/peppol/synkroniser` (API fallback),
`peppol_sync.sync_once`:
1. drains `GET …/notifications` as the old poller did, which finds everything
   if Ademico keeps pushed events there and nothing if it does not;
2. lists the invoices and credit notes Ademico received in the last 7 days
   (`GET …/documents?direction=RECEIVING&startDateTime=…Z`) and stores any
   Myntos has not;
3. asks Ademico about every send still `pending` that is between 2 minutes and
   30 days old, at most 100 a sweep.

Once a webhook secret is set, a background sweep that finds anything at all
logs it at ERROR («Peppol sweep found N received documents and M delivery
updates that no webhook brought»), which reaches `/drift/`
([operator.md](../operator.md)): the webhook is off at Ademico, its secret
differs from `.env`, or the backend was down when a push came and Ademico did
not try again. Without a secret the sweep is the only channel, and says what
it found at INFO.

**Storing a received document** is the same whichever channel names it. The
single active Myntos company receiving for that `0192:<orgnr>` is found, the
raw UBL XML is downloaded via `GET …/invoices/{transmissionId}/ubl`, parsed
with an Expat DTD-refusing parser (`ubl_reader.py`), and stored as the frozen
XML with its SHA-256 digest, parsed lines and VAT breakdown. The
`transmissionId` is checked across all companies before anything is
downloaded, every commit completes **before** `DELETE …/notifications/{id}`,
and `uq_peppol_incoming_company_transmission` keeps one copy when a push and
the sweep store the same document at the same moment. So the push, the queue
and the sweep may all see one document, and it is stored once.

### Booking received EHFs

Received supplier invoices and credit notes appear under `/innkommende/` with
status `ny`, `bokfort` or `avvist`. Nothing is ever deleted (bokføringsloven
§ 13):
- `POST …/peppol/innkommende/{incoming_id}/bokfor` posts an immutable voucher
  through `ledger_service.record_cash_flow`, automatically matches or creates
  the supplier party by `seller_orgnr` (when `create_supplier=true`), archives
  a readable PDF in the append-only bilag store (the supplier's embedded PDF
  when present, otherwise a rendered A4 summary from `incoming_pdf.py`), and
  locks the row `FOR UPDATE` so an incoming invoice can never be booked twice.
- Foreign-currency EHFs require the user to supply the paid NOK amount.
- Dismissing (`…/avvis`) moves an unbooked invoice out of `ny` without deleting
  it; `…/gjenapne` returns it to `ny`.

## Configuration

```
ADEMICO_USER=lindeman_technologies
ADEMICO_PASSWORD=…
ADEMICO_TOKEN=…
ADEMICO_WEBHOOK_SECRET=…     # openssl rand -hex 32
# ADEMICO_WEBHOOK_URL=https://api.myntos.no/api/v1/webhooks/ademico
# ADEMICO_API_BASE_URL=https://peppol-api1.ademico-software.com/domibus
# PEPPOL_SML_ZONE=participant.sml.prod.tech.peppol.org
# PEPPOL_SWEEP_INTERVAL_SECONDS=900
```

All three credentials or nothing: a blank user, password or token counts as
unconfigured (`available: false`). As with every setting, editing `.env` takes
effect only after restarting `myntos-backend`.

Without `ADEMICO_WEBHOOK_SECRET` the webhook answers 503 and the sweep is the
only channel, so invoices arrive within the sweep interval rather than at once.
Switching the webhook on goes in this order, because Ademico starts pushing
the moment it is told to and a backend without the secret refuses every push:
1. Back up `.env`, add `ADEMICO_WEBHOOK_SECRET`, restart (`./update.sh`).
2. `uv run python -m myntos.integrations.peppol_sync on` from `backend/`, which
   prints what Ademico now has.

Switching it off is the reverse: `peppol_sync off`, then remove the secret.
Changing the secret is a restart and `on` again, with the pushes in between
refused and left to the sweep.

## Before the first external customer

1. **Test run end to end against Ademico TEST** (`secrets/ademico-test.env`,
   `https://test-peppol-api1.ademico-software.com/domibus`) from TT02 or a
   scratch run: register a company, send an invoice and credit note, receive it
   through the webhook and through the sweep, and book it. The webhook needs a
   public URL in front of a backend configured for TEST: pointed at
   production's, every TEST push is read back from PROD, not found, and
   answered 503. Look at the body Ademico actually pushes while there (see
   Known gaps).
2. **One real invoice in PROD** between two companies the owner controls:
   verify sending, delivery status update, receiving into `/innkommende/`, and
   booking with archived PDF.
3. **Signed DPA (*databehandleravtale*) with Ademico Software** and EEA hosting
   confirmation before serving third-party customer companies (required by
   `/personvern/` § 6 and CLAUDE.md rule 5).
4. **Optional hardening:** request PROD OAuth2 (`client_id`/`client_secret`)
   credentials from Ademico Support on ticket `PEPPOLS-7735` if moving off
   Basic Auth.

## Known gaps

- **Ademico's webhook is half documented.** The settings are; the body it
  pushes, whether and how often it retries a push that failed, how long it
  waits for an answer, and whether a pushed event also stays in the pull
  queue are not. Myntos accepts a single notification, a JSON array of them,
  or `{"notifications": [...]}`, and the sweep covers the rest. Asked on
  ticket `PEPPOLS-7735`; narrow `ademico.notifications_from_push` once
  answered.
- **The sweep looks back 7 days for received documents.** One that no push,
  no queue entry and no sweep brought within a week (a backend down that
  long) is the operator's to find in Ademico's portal.
- **Timeout during send:** a send cut off by `_SEND_TIMEOUT` (90 s) returns
  502 `send_uncertain` without storing a row so the user asks the customer
  before retrying.
- **Norwegian buyers/receivers only (`0192:<orgnr>`).** Foreign Peppol schemes
  are not yet supported on outgoing sends or incoming routing.
- **Single-line booking:** booking an incoming EHF posts a single cash-flow
  entry (like `/kontantstrom/`); mixed-VAT supplier bills can be split via
  composite entries or manual vouchers if needed.
