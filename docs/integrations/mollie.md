# Mollie: paying for a KI plan

Mollie takes the money for a month of a KI plan, and nothing else. The
accounting program has no price, and «Gratis» has none either; the one thing
Myntos sells is a month of the paid KI levels («Medium» and «Smart»). What
the levels and plans are, and why a request is never refused for want of
quota, is in
[ARCHITECTURE.md § KI levels](../ARCHITECTURE.md#ki-levels-and-the-plans-that-pay-for-two-of-them).

It is the same provider and the same design as Løynd's own checkout
(`loynd-backend/app/mollie.py`), so the owner runs one way of taking money.

Code: `backend/src/myntos/integrations/mollie.py` (the client),
`domain/ki/plans.py` (prices), `domain/ki/service.py` (`start_purchase`,
`settle`), `api/v1/ki.py` (the routes). Frontend: `/ki/` and
`frontend/src/lib/kiplan.ts`. Tests: `backend/tests/test_ki_purchase.py`.

## The flow

1. The owner presses «Kjøp KI Start|Pluss|Pro» on `/ki/`.
2. `POST /api/v1/companies/{id}/ki/betaling` writes an `open` `ki_period` row
   with a reference of its own (`secrets.token_urlsafe(18)`), asks Mollie for a
   payment of the plan's price with VAT, and hands the browser Mollie's hosted
   `checkout_url`.
3. The person pays on Mollie's page. Card or account details are typed in
   there and never pass through Myntos.
4. Mollie sends the browser back to `/ki/?betaling=<reference>`, and the page
   calls `GET …/ki/betaling/{reference}`, which asks Mollie and settles the row.
5. Mollie also POSTs `id=tr_…` to `POST /api/v1/webhooks/mollie`. Either of 4
   and 5 may come first, or only one of them; `settle` is idempotent, so a
   month opens exactly once.

## Nothing Mollie is told is from the books

Myntos sends an amount, a description naming the plan («Myntos KI Pluss, en
måned», which is what the bank statement shows), the return URL and
`{"reference", "plan"}` as metadata. No company name, no organisation number,
nothing from the books. Mollie B.V. is in Amsterdam and processes in the EEA,
so the payer's name and payment details it collects stay inside rule 5.
`personvern.astro` names Mollie as a recipient.

## Why the webhook is only a hint

The webhook is the one route anybody on the internet can call, with any body.
So its `id` is checked against Mollie's shape (`tr_` and letters and digits)
before it goes into a URL, and then only used to ask Mollie, with Myntos's own
key, what the payment's status is. A forged webhook can at most make Myntos ask
Mollie a question.

What Mollie answers is still checked against the row before a month opens:
the payment id, the amount, the currency and the reference in the metadata.
A payment opens exactly the month it was started for, or nothing
(`PaymentMismatchError`, logged at ERROR by reference only, so it reaches
`/drift/`).

The webhook answers:

| Case | Status | Why |
| --- | --- | --- |
| Not a Mollie id, or not one of ours, or plans are not sold here | 200 | Mollie stops retrying something that will never be found |
| A payment that does not match its purchase | 200 | No retry can mend it; the ERROR line is what someone acts on |
| Mollie could not be reached when asked | 503 | Mollie retries, which is how a payment made while its API was down still opens its month without the browser coming back |
| Settled, whatever the outcome | 200 | |

## Modes and settings

| Setting | Default | What it does |
| --- | --- | --- |
| `MOLLIE_MODE` | `test` | `test`, `live` or `off`. Picks which key is used |
| `MOLLIE_TEST_KEY` | none | Mollie's `test_…` key. Payments in test mode move no money, and `/ki/` says so («testmodus») |
| `MOLLIE_LIVE_KEY` | none | Mollie's `live_…` key, used only when `MOLLIE_MODE=live` |
| `MOLLIE_WEBHOOK_URL` | `https://api.myntos.no/api/v1/webhooks/mollie` | Sent with each payment only when it is public HTTPS |
| `MOLLIE_TIMEOUT_SECONDS` | `15` | One request to Mollie |

Plans are on sale only when the mode in use has a key **and** Løynd is
configured (`plans_on_sale` in `GET …/ki`); a copy of Myntos without either
sells nothing and shows no buy buttons. `live` with only a test key is off, so
a test key never takes real money because somebody wrote «live» next to it.

Mollie refuses to create a payment whose webhook it cannot reach, so a local
or self-hosted copy sets `MOLLIE_WEBHOOK_URL=` (empty) and relies on the
browser's return. A payment made there and then abandoned before the browser
came back stays `open` until someone opens `/ki/?betaling=<reference>` again.

No `method` is sent, so Mollie offers every way of paying switched on in its
dashboard. That is why no page in Myntos promises a card: which methods appear
is the dashboard's decision, not the code's.

## Setting it up on the production box

1. Back up `.env` (`secrets/.env.bak-<date>-mollie`).
2. Add `MOLLIE_TEST_KEY=` (and `MOLLIE_MODE=test`), restart
   (`./update.sh --restart-only`), and buy a month in test mode on a company
   of our own. `/ki/` should say the month is paid in test mode, and
   `GET …/ki` should show it as `current`.
3. For real money: add `MOLLIE_LIVE_KEY=`, set `MOLLIE_MODE=live`, back up
   first and restart again.

The Løynd key Myntos uses must have prepaid credit for the paid models (402
otherwise), and must not be one Løynd keeps in Norway (403 for the paid
models, which may run in the EU). Either refusal makes every paid answer fall
back to «Gratis» with a warning, so a misconfigured key shows up as companies
that paid getting «Den betalte modellen svarte ikke akkurat nå…» on every
answer.

## Not built

- **A salgsdokument for each purchase.** Myntos is the seller, and
  bokføringsforskriften kapittel 5 requires a sales document to the buyer.
  Mollie's receipt is not one. Until it is built, the owner has to issue them
  by hand from the `ki_period` rows (`status = 'paid'`, `mollie_mode = 'live'`).
- **Refunds and angrerett.** No refund route exists, and the terms do not yet
  say what happens to an unused month. The buyer is a company, but the policy
  is the owner's to decide and write into «Vilkår».
- **Recurring payments.** Deliberately not: a plan is a month paid once and
  never renewed, so there is nothing to cancel.
