# Sending EHF over Peppol (Recommand)

From 1 January 2027 a sale to another bokføringspliktig is documented by an
e-invoice, and an e-invoice is only delivered when it travels over Peppol.
Myntos writes the EHF and freezes it at issue ([efaktura.md](efaktura.md));
this is how the frozen file reaches the customer. Peppol is not something a
program joins on its own: every sender and every receiver goes through an
access point certified by OpenPeppol. This deployment uses **Recommand** (BRBX
BV, Peer, Belgium), through its REST API at `https://app.recommand.eu`, chosen
on 2026-11-01 in place of invoice.be, the earlier plan.

Code: `backend/src/myntos/integrations/recommand.py` (the client, and the only
file that knows Recommand's API), `domain/peppol/` (who may register and send,
the two tables; it makes no request), `api/v1/peppol.py` (the five routes,
which call the access point between the domain's checks and its storing, as
ARCHITECTURE.md's dependency rule has it), migration `8d7b599c0754`.
Frontend: «Utsted og send som EHF», «Send som EHF» and the delivery status on
`/faktura/`, the «E-faktura» card on `/organisasjon/`, `src/lib/peppol.ts`.
Tests: `backend/tests/test_peppol.py`, against a stand-in written from
Recommand's published OpenAPI document. Built 2026-11-01; **nothing has been
sent to a real access point yet** (see «Before the first customer»).

## The four decisions

### Peppol only, never e-mail

Recommand will e-mail a document instead of, or beside, a Peppol transmission,
if the request carries an `email` field. Its mail goes through **Postmark, in
the USA** (Recommand's own list of subprocessors, effective 2026-10-14). An
invoice is accounting data, which never leaves the EEA (CLAUDE.md rule 5), so
no request from Myntos carries `email`, and a company is registered with no
contact details at all. A customer who cannot receive over Peppol is refused
(422 from Recommand, `recipient_not_on_peppol` from Myntos) with a message
telling the person to send the PDF themselves. A silent fallback to mail would
have been the friendlier button and the breach of rule 5.

The test `test_the_frozen_xml_goes_as_it_is_and_never_by_email` holds this:
no request body may contain the key.

### The frozen XML, as it is

Recommand can build a UBL document from its own JSON model, and can detect the
document type of XML it is given. Both were refused. Building from JSON would
make Recommand the author of the sales document, which from 2027 *is* the
legal document for a business customer, and it could quietly differ from the
EHF Myntos validated and froze. Detecting would let a change in Recommand's
guesswork change what goes out. So the send is `documentType: "xml"` with the
stored `ehf_xml` unchanged, and `doctypeId` and `processId` named: Peppol BIS
Billing 3.0 invoice or credit note, by `SalesDocument.is_credit_note`.

### Send only

Each company is created with `isSmpRecipient` off. Registering a Norwegian
organisasjonsnummer to **receive** puts it in ELMA, Norway's Peppol registry,
and a participant can be registered with only one access point: it would take
over whatever inbox the company receives its supplier invoices in today, in
another program, from an access point Myntos does not run. Myntos also has
nowhere to put an incoming invoice yet. Receiving is in «Det Myntos ikke gjør
ennå» on `/funksjoner/`; building it means a place for incoming documents, a
webhook from Recommand, and an explicit «move my inbox here» step that says
what it takes over.

### Who may send as the company

The access point sends as whichever company Myntos names under the
deployment's key. So every check of *who may send as this business* is
Myntos's, before Recommand is asked:

1. **The company must be confirmed through Altinn** (`Company.verified_at`,
   stamped only by `confirm_company_ownership`) before it can be registered.
   Without that, a new account that typed somebody else's orgnr would be one
   click from sending invoices in their name, and the link to the access
   point's identity check (step 3) would be in the wrong person's hands.
2. **Only an «Administrator» or «Eier»** registers (`ORGANIZATION_MANAGE`),
   and no key may (`_NOT_FOR_AGENTS` in `tests/test_agent_surface.py`): it
   decides which business invoices go out in the name of.
3. **Recommand's own identity check.** Registering answers with a
   `verificationUrl`; somebody who signs for the company proves who they are
   on Recommand's page (the KYC is done by Didit, Spain, and kept in the EU).
   Nothing is sent until Recommand says `isVerified`. Myntos never stores the
   link: it is a credential while it lives, and asking again gives a fresh one.
   `GET …/peppol` asks Recommand on every read until the check is done, and
   stores `verified_at` once it is, so the page knows when the person comes
   back from the other tab.

An existing Recommand company with the same orgnr is **adopted** rather than
duplicated (`find_company`). Peppol names the sender by orgnr, so two
companies at the access point for one orgnr would be two senders claiming to
be the same business. This happens when two sets of books in Myntos are both
confirmed for one company (ARCHITECTURE.md § Confirmed ownership allows it,
after asking): they share one registration, and one identity check counts for
both. Both passed Altinn, so neither is a stranger.

## Sending

### Sent by default, in the press that issues

From 1 January 2027 an invoice to another bokføringspliktig is only delivered
when it travels as EHF, so on `/faktura/` sending is not a second step. When
the company can send and the buyer has an orgnr, the review asks the access
point whether that customer can receive (`GET …/peppol/mottaker`), and if so
its main button is «Utsted og send som EHF», with a sentence under it naming
the customer and the orgnr the invoice goes to. «Utsted uten å sende» beside
it is for an invoice to be sent later. The first version had a separate «Send
som EHF» on the issued invoice, and that left room for an invoice issued,
booked and then never sent, which from 2027 is an invoice the customer has
not lawfully received. `delivery()` in `src/lib/peppol.ts` decides it.

Why the questions are where they are:

- **The lookup is made before the press, not after it.** The review is the
  only confirmation an EHF send gets: no `confirm()` follows, so the review
  has to say where the invoice goes before the button that sends it can be
  pressed, and the buttons wait for the answer. A customer who cannot receive
  gets «Utsted fakturaen» and a sentence telling the person to send the PDF,
  rather than an invoice issued and booked and then a refusal
  (`recipient_not_on_peppol`).
- **A lookup that fails is not a no.** The send then goes and says itself if
  the customer cannot take it. Holding back every invoice while the lookup
  is down would make the PDF the default again for as long as it lasted.
- **Issuing and sending stay two requests.** Issuing numbers, books and
  freezes the document in one transaction in our database; handing a file to
  an access point abroad cannot be part of that transaction, and an invoice
  must not fail to be issued because Belgium did not answer. The page sends
  right after the issue commits, and a send that fails leaves the invoice
  issued, with the reason and «Send som EHF» on the card.
- **A credit note follows its invoice.** «Krediter» on an invoice that went
  as EHF (pending or delivered, not failed) sends the credit note as EHF in
  the same confirmed press, so the customer's system stops saying they owe
  it. The `confirm()` names the customer and orgnr. An invoice that went as a
  PDF is credited as a PDF.

The API keeps the two requests apart (`POST …/utsted`, then
`POST …/send`), because an agent's send is `key_send` and waits for a person,
while its issue may not (the skill says so step by step).

### One send per document

`POST …/faktura/{document_id}/send` takes the document row `FOR UPDATE`
first, so two presses in two tabs cannot both pass the check for an earlier
send; the second waits for the first to commit and then finds it. The partial
unique index `uq_peppol_transmission_live` (one non-failed transmission per
document) holds the same rule where a lock cannot: a second process, a future
code path. A `failed` transmission may be followed by another; a `pending` or
`delivered` one may not (`already_sent`), because an invoice that arrives
twice can be paid twice.

What each answer from Recommand leaves behind:

| Recommand | Myntos stores | The person is told |
| --- | --- | --- |
| 200, Peppol delivery `pending`/`delivered`/`failed` | A `peppol_transmission` row with that status | «EHF-en er sendt over Peppol …» or the failure, quoted |
| 422, recipient not reachable | Nothing | `recipient_not_on_peppol`: send the PDF |
| 400, document refused | Nothing | `access_point_refused`, with Recommand's findings |
| Timeout during the send | Nothing | `send_uncertain`: ask the customer before sending again |
| 401, any other error, no answer | Nothing | `access_point_unavailable`: try again |

**The timeout is the one honest gap.** Recommand's send has no idempotency key,
and a send cut off by Myntos's own timeout (90 seconds, `_SEND_TIMEOUT`, long
because Recommand hands the document over AS4 before it answers) may or may
not have gone. Storing it as sent would block a resend that might be needed;
storing nothing lets the person press again. Myntos stores nothing and says so
in words that make them ask the customer first. The fix, when it matters:
after a timeout, look for the document in Recommand's list of the company's
sent documents before answering, and store what it finds.

A 401 is logged as a warning (a rotated or mistyped key), and nothing that
crosses the wire is logged at all: the request is an invoice and the answer
may quote it.

### Delivery status

`pending` means handed to the network with no receipt from the customer's
side. `GET …/forsendelse` asks Recommand again while it is pending and stores
the answer with `status_checked_at`; the invoice page does that for every
pending row after the list is drawn. An access point that cannot be reached
leaves the stored status, which is still the last true thing known.

There is **no webhook** yet. Recommand can call one on delivery, which would
make `pending` resolve without anybody opening the page, but it needs a
public endpoint that verifies Recommand's signature, and asking on read is
enough while nothing else depends on the status.

### Agents

Sending is `key_send("sende fakturaen som EHF til kunden")`: a key's request
always waits for its person on `/godkjenning/`, whatever its scopes, because an
invoice that has gone cannot be called back (CLAUDE.md rule 8). The approval
names the invoice through the `document_id` target in
`auth/approval_targets.py`. Reading where the company stands and following a
send are `KEY_READ`. Registering is closed to keys (above).

## Where the data goes

Recommand states that it hosts in the EEA, on AWS, Google Cloud, DigitalOcean
and Hetzner, all listed as «EU», and that identity verification data is
processed and stored in the EU. Two entries on its list of subprocessors
matter here, and both are why something above is the way it is:

- **Postmark (USA)** sends Recommand's mail. Myntos never asks it to mail
  anything (above).
- **Cloudflare (listed «EU/Global»)** terminates TLS in front of Recommand's
  API, so the invoice is decrypted at a Cloudflare edge before it reaches
  Recommand. Myntos calls from Norway and lands on a Nordic edge in practice,
  but Cloudflare promises no location for it: the same caveat as Myntos's own
  site, and written down for the same reason.

What Recommand receives: at registration, the company's name, orgnr, business
address and (if VAT registered) `NO<orgnr>MVA`. When a review opens for a
buyer with an orgnr, that orgnr as a Peppol address and the document type
(`POST /api/v1/verify-document-support`), and nothing else: no amount, no name,
nothing Myntos stores. With each send, the EHF, which
holds the buyer's name, orgnr and address, the lines and the amounts, and any
reference typed on the invoice. From there it goes to the access point the
customer chose, which may be anywhere on the Peppol network, the way a
recipient's mail server is wherever they chose.

Recommand is a sub-processor of the companies' accounting data, listed on
`/personvern/` (§ 3f2 and the table in § 6), which name the lookup as well.

## Configuration

```
RECOMMAND_API_KEY=…
RECOMMAND_API_SECRET=…
# RECOMMAND_API_BASE_URL=https://app.recommand.eu   (the default)
```

Both halves or nothing: a blank half counts as no key, and then `GET …/peppol`
answers `available: false`, the «E-faktura» card is hidden and the invoice
page offers the files to download as before. One key for the whole
deployment; each Myntos company is a company under it. Like every setting,
an edited `.env` does nothing until the backend restarts.

## Before the first customer

The stand-in in `test_peppol.py` answers the way the OpenAPI document says
Recommand does. It is not a recording. One answer has been checked against the
live API (2026-11-01, read-only, production's key): `GET /api/v1/companies`
lists companies with `id`, `enterpriseNumber`, `isVerified` and
`isSmpRecipient` among their fields, which is what `find_company` reads. The
rest is owed before a customer relies on it:

1. **A test run against a separate Recommand team** with its own key, from
   the TT02 copy, never production's key: register a company, finish the
   identity check, send an invoice and a credit note, and compare each answer
   with the stand-in. Correct the stand-in where it differs.
2. **One real invoice between two companies the owner controls**, the second
   receiving through any access point: check that it arrives, that its
   status in Myntos moves from «Sendt som EHF» to «Levert som EHF», and how
   the receiving program shows the sender.
3. **A data processing agreement with Recommand.** `/personvern/` § 6 says
   every supplier in its table «har databehandleravtale med oss», and the
   table now names Recommand. Accept theirs (or have one signed) before the
   page goes out.
4. **Read the privacy page's new wording** (§ 3f2 and the table) with the eyes
   of the companies, who are the controllers: adding a sub-processor of their
   accounting data is the kind of change `/personvern/` § 12 promises to
   announce by e-mail.

## Known gaps

- **The timeout** (above): a send cut off by our own timeout is not looked
  up afterwards.
- **No webhook** (above): `pending` resolves only when somebody looks.
- **No receiving** (above).
- **The lookup answers for a document type, not a promise.** A customer who
  is on Peppol when the review opens can still refuse the document; that
  comes back as a failed transmission, quoted, with «Send EHF på nytt».
- **Norwegian buyers only.** The recipient is `0192:<orgnr>`. A foreign buyer
  needs their own Peppol participant id on the party, the same gap as in
  [efaktura.md](efaktura.md#known-gaps).
