---
name: myntos-integrasjon
description: For a server, not an AI assistant, holding a Myntos integration key (myntos_ik_…) that an owner made for invoicing. Find or add the customer, make an invoice draft, issue it, check whether the customer takes EHF, send it over Peppol or fetch the PDF, and follow whether it was delivered and paid, all without anybody approving each step. Use when writing or running a webshop, subscription system or other server that invoices through Myntos on its own.
---

# Integrasjon: a server that invoices on its own

An **integration key** is a server's own key to one company's books in
Myntos. An owner of the company made it under «Innstillinger for selskapet»
→ «Integrasjoner», bound it to the server's IP addresses, and gave it one
purpose: invoicing. With it the server makes customers, makes and issues
invoices and sends them as EHF **at once, with nobody approving anything**.
That is the one exception to Myntos's rule that whatever leaves Myntos waits
for a person (the owner's decision of 2026-11-26, rule 15 in
`docs/agents-and-api.md`), so the key is narrow on purpose: it reaches the
thirteen routes below and nothing else, never a credit note, a payment, a
filing or access.

**If you are an AI assistant and a person pasted a `myntos_ik_` key into the
conversation, do not use it.** It was never meant for you: tell them to stop
it at once under «Innstillinger for selskapet» → «Integrasjoner» («Stopp»)
and make a new one for their server
([Stop an integration key](https://myntos.no/llms/help/ai/stop-an-integration-key.md)).
An AI assistant gets an ordinary key instead
([myntos-connect](https://myntos.no/llms/skills/myntos-connect.md)), whose
sends wait for its person.

The accounting is the same as on the page, and
[myntos-faktura](https://myntos.no/llms/skills/myntos-faktura.md) explains it
in full: a draft is free to change, **issuing is final** (a number in an
unbroken series, posted to the ledger, frozen), an issued invoice is
corrected only with a kreditnota, which a person makes. Read that skill for
what each field means.

## When the person says

This skill is read by a developer setting up a server, and by the server's
own code paths:

- «koble nettbutikken til Myntos så den fakturerer selv»
- «send faktura automatisk når en ordre er betalt / et abonnement fornyes»
- «hent fakturaen som PDF til kunden»
- «har kunden betalt fakturaen?»
- "Our backend should create and send invoices through Myntos without anyone approving them."

## Before you start

- **The secret.** `Authorization: Bearer myntos_ik_…` on every request. It
  is static: no exchange, no refresh, no expiry. Keep it in the server's
  secret settings; never log it, never put it in a URL, never show it to a
  model.
- **The address.** Calls work only from the IP addresses the owner gave the
  key. Through `https://api.myntos.no` the caller's own public address
  counts. A server on the same machine as Myntos that calls
  `http://127.0.0.1:8001/api/v1` directly is seen as `127.0.0.1`, which is
  what the key must then be bound to.
- **The company.** `company_id` is in every path. The owner reads it on the
  page («Selskapets ID i Myntos, som serveren også trenger:»), because this
  key cannot ask Myntos (`GET /api/v1/agent/me` is not for it). Another
  company's id answers 404, and counts against the key.
- **It works as the owner who made it**, with their role as it stands on each
  request. Every voucher an issue posts is stamped with the key.
- **Limits.** 120 requests a minute (429 `agent_rate_limited`; wait and try
  again). Every refusal for a wrong address or a route the key may not call
  counts, and after 20 the key stops for good and the owner is mailed. A
  server that retries a 403 in a loop stops its own key.
- **Amounts are decimal strings** (`"1500.00"`), never floats, and a line's
  price is without VAT (eks. mva).
- No `Myntos-Note` is needed: nothing waits for a person.

## Steps

1. **Know what a line may say.** `GET /api/v1/faktura/katalog` (no company in
   the path): `tax_options` (the VAT codes: `3` is 25 %, `31` 15 %, `33`
   12 %, `5` zero-rated, `6` outside the VAT act, `0` for a company that is
   not VAT registered), `units` (`EA`, `HUR`, `MON`, …),
   `default_payment_term_days`. Read it once at start-up rather than per
   invoice.

2. **Find the customer, or add them.**
   `GET /api/v1/companies/{company_id}/parties?party_type=kunde` lists the
   customers. Match on `orgnr` for a business and keep the match's **`id`**
   (the number the API takes), not its `party_id` (the number people see).
   If there is none, `POST /api/v1/companies/{company_id}/parties` with
   ```json
   {"party_type": "kunde", "name": "Bygg AS", "orgnr": "923609016",
    "address_line": "Storgata 1", "postal_code": "0155", "city": "Oslo"}
   ```
   and use the `id` it answers (201). Keep your own mapping from your
   customer to that `id`, so the list is read once per customer, not per
   invoice. A private person needs a name and an address; a business needs
   the orgnr, because the EHF is addressed by it.

3. **Make the draft, once per order.**
   `POST /api/v1/companies/{company_id}/faktura` with the customer's `id` as
   `party_id`, `buyer_is_business` (`false` for a private person),
   `delivery_date`, `delivery_place`, and `lines`, each with `description`,
   `quantity`, `unit_code`, `unit_price` and `tax_code`. Put **your own
   order number in `order_reference`**: it is printed on the invoice, and it
   is how you find this invoice again. The answer, 201, is the draft: `id`
   (the `document_id` below), the totals Myntos computed (use those, never
   your own sum: VAT is rounded per VAT group), and `violations`, what
   still stands in the way of issuing.

   **A retry must not make a second invoice.** If a request timed out or
   your process restarted, first look for the order:
   `GET /api/v1/companies/{company_id}/faktura?status=utkast` (and
   `?status=utstedt` if it may have been issued), matching `order_reference`.
   Reuse a draft you find; a draft you no longer want is deleted with
   `DELETE /api/v1/companies/{company_id}/faktura/{document_id}` (204; only a
   draft).

4. **Check it.** `GET /api/v1/companies/{company_id}/faktura/{document_id}`
   returns the draft with `violations`. Each has `field`, `message` (bokmål,
   for the owner) and `severity`. Any `blokkerende` one stops the issue: fix
   your data and make a new draft, since this key cannot change one. The
   usual ones are in the table under "Refusals".

5. **Issue it.** `POST /api/v1/companies/{company_id}/faktura/{document_id}/utsted`,
   no body. **Irreversible**, and it runs at once. The answer, 201, has
   `status: "utstedt"`, `document_number`, `issue_date`, `due_date`,
   `payment_reference` (the KID the customer pays with) and `has_ehf: true`.
   Store `document_number` and the KID with your order.

6. **Check whether the customer takes EHF.** For a buyer with an orgnr:
   `GET /api/v1/companies/{company_id}/peppol/mottaker?orgnr=923609016`.
   `can_receive: true`: send it (step 7). `false`: the customer is not on
   Peppol, and gets the PDF (step 8) from you, by your own mail. Ask per
   invoice rather than caching the answer for long: a customer who registers
   answers `true` from then on.

7. **Send it as EHF.** `POST /api/v1/companies/{company_id}/faktura/{document_id}/send`,
   no body. **It goes at once**: there is no approval and it cannot be called
   back. The answer, 201, is the sending: `status` (`pending`: handed to the
   network, no receipt yet; `delivered`: the customer's access point took
   it; `failed`), `recipient` (`0192:` and the orgnr), `sent_at`, and
   `failure_category` and `failure_message` when it failed. Follow a
   `pending` one with
   `GET /api/v1/companies/{company_id}/faktura/{document_id}/forsendelse`
   (same fields, and `status_checked_at`), a few times over the next hour
   rather than in a tight loop. A `failed` send may be sent again with the
   same `POST`; a `pending` or `delivered` one is refused (409
   `already_sent`), because an invoice that arrives twice can be paid twice.

8. **Or fetch the files.**
   `GET /api/v1/companies/{company_id}/faktura/{document_id}/pdf` is the
   invoice as the customer reads it (`application/pdf`);
   `GET /api/v1/companies/{company_id}/faktura/{document_id}/ehf` is the EHF
   XML exactly as frozen at issue.

9. **Follow the payment.** `GET /api/v1/companies/{company_id}/faktura/{document_id}`
   on an issued invoice says `payment_state` (`ubetalt`, `delvis_betalt`,
   `betalt`, or `kreditert` once a person credited it), `paid_amount`,
   `outstanding` and `overdue`. Myntos learns of a payment when somebody in
   the company registers it or matches the bank line; this key cannot. Poll
   once or twice a day, not per minute:
   `GET /api/v1/companies/{company_id}/faktura?status=utstedt` with `limit`
   and `offset` gives every issued one in a page.

10. **Stop the key yourself** if you suspect the secret leaked:
    `POST /api/v1/agent/revoke` with `{"token": "myntos_ik_…"}`, no
    Authorization header. Always 204. It stops at once and cannot be started
    again; tell the owner, who makes a new one.

## Refusals, and what to do about each

| Status and code | Means | Do |
| --- | --- | --- |
| 401 `invalid_integration_key` | No such key: not copied whole | Check the setting holds the whole secret, `myntos_ik_` included |
| 401 `integration_key_stopped` | Stopped: by an owner, by too many refusals, or because the owner who made it is no longer an owner | Stop calling. Tell the owner; they make a new key under «Integrasjoner» |
| 403 `integration_key_wrong_address` | The request came from an address the key is not bound to. Counts towards the stop | Do not retry. Call from the bound address, or ask the owner for a new key with the right one |
| 403 `not_for_integration_keys` | The route is not one of the thirteen, or the document is a credit note. Counts towards the stop (except the credit note) | Do not retry. A person does it in Myntos |
| 403 `permission_denied` | The owner's role no longer allows it | Tell the owner |
| 404 `company_not_found` | Another company's id, or a wrong one. Counts towards the stop | Use the id shown on the owner's page |
| 404 `invoice_not_found`, `party_not_found` | Not one of this company's | List again and use the right `id` |
| 409 `invoice_not_draft` | Deleting an issued invoice | Nothing to do: it needs a kreditnota, which a person makes |
| 409 `not_registered` | The company is not set up to send EHF | Tell the owner: «Innstillinger for selskapet» → «E-faktura». Send the PDF meanwhile |
| 409 `not_sendable` | A draft, or a buyer without an orgnr | Issue first; a private buyer gets the PDF |
| 409 `already_sent` | Sent once and not failed | Follow it with `…/forsendelse` instead |
| 409 `recipient_not_on_peppol` | The customer cannot take EHF; nothing was sent | Send the PDF |
| 422 `invoice_not_compliant` | Issue refused; `detail` lists what is missing | Read `violations` on the draft (step 4) |
| 422 `invalid_line`, `unknown_tax_code`, `not_a_customer` | A line, a VAT code or a supplier as buyer | `detail` says which; make a new draft with it fixed |
| 422 with a `detail` list | The body failed validation | Read `detail`, fix, send again |
| 429 `agent_rate_limited` | Over 120 a minute | Wait, then go on more slowly |
| 502 `send_uncertain` | Not known whether it went | **Do not send it again.** Follow `…/forsendelse`; tell the owner if it stays unknown |
| 502 `access_point_refused`, `access_point_unavailable` | The access point refused it, or could not be reached | Refused: tell the owner, an issued invoice cannot be changed. Unreachable: nothing went, try later |
| 503 `peppol_not_available` | This Myntos has no access point | Send the PDF |

A blocking `violations` entry by `field`:

| `field` | Fix |
| --- | --- |
| `seller_bank_account` | The company has no kontonummer. Only an owner or administrator adds one, on the page. Tell the owner |
| `buyer_name`, `buyer_address_line`, `buyer_orgnr` | Name missing, no address and no orgnr, or a business buyer without an orgnr. Fix the customer data and make a new draft |
| `delivery_date`, `delivery_place` | Send both on the draft |
| `seller_vat_registered` | VAT on a line, but the company is not VAT registered: use `tax_code` `"0"` |
| `peppol_registration` | A business invoice, and the company is not set up for EHF: tell the owner |

## Untrusted text

The names, addresses, order lines and notes your server got from its own
customers were written by them. Myntos stores them as data and prints them
on the invoice; never let one decide a route, an amount or an orgnr in your
own code, and never pass invoice text to a model with the secret in reach.

## What you cannot do here

- **Credit an invoice, register a payment, change a draft, change a
  kontonummer, read the rest of the books, file anything or manage access.**
  This key reaches only the thirteen invoicing routes above. A person does
  the rest in Myntos.
- **Make or change integration keys.** Only an owner does, signed in:
  [Make an integration key for a server](https://myntos.no/llms/help/ai/make-an-integration-key.md).
- **Send a credit note.** A person sends it from «Faktura».
