# Bank: camt.053 import and reconciliation

The register used to be typed twice: once by the bank and once by the person
reading the bank's screen into Myntos. The bank's half is now data. A camt.053
file is read, checked to add up, archived whole, and each of its lines is set
beside the vouchers already posted, so every movement of money is either
explained by an entry in the books or visibly not.

Code: `backend/src/myntos/domain/bank/` (`camt.py` the parser, `matching.py`
the ranking, `service.py` the database), `api/v1/bank.py`, `/bank/` in the
frontend. Built 2026-10-09.

## The format

ISO 20022 **camt.053** ("BankToCustomerStatement"), the end-of-day statement.
DNB, SpareBank 1, Nordea and Handelsbanken all export it from the nettbank,
mostly as `camt.053.001.02` and increasingly as `.001.08`.

- **Both versions are read by one parser.** The one structural difference that
  reaches the fields we use is the related party: `.02` has `Dbtr/Nm`, the 2019
  versions wrap it as `Dbtr/Pty/Nm`. Both shapes are read, so every
  `camt.053.001.NN` is accepted. Fixtures exist for `.02` and `.08` only
  (`backend/tests/fixtures/bank/`, hand-built, no real bank data); a bank that
  sends another version and breaks something is a fixture to add.
- **camt.052 and camt.054 are refused by name.** camt.052 is an intraday report
  whose entries can still change; camt.054 notifies single movements with no
  balances to check. The refusal says which one the file is and asks for the
  statement instead.
- **Only booked entries are imported** (`Sts` = `BOOK`, or `Sts/Cd` = `BOOK`
  from 2019 on). Pending ones are left out and counted in the answer.
- What is read per entry: booking date, value date, amount and `CdtDbtInd`
  (stored as a positive amount plus `inn`/`ut`, the register's own shape),
  currency, counterparty (`Dbtr` for money in, `Cdtr` for money out, `Ultmt*`
  as fallback; none for a batch whose transactions name different parties),
  every `Ustrd`, the structured `CdtrRefInf/Ref` (the KID), `EndToEndId`
  (`NOTPROVIDED` read as none), `AcctSvcrRef`, and `AddtlNtryInf`.
- The account is `Acct/Id/IBAN` or `Acct/Id/Othr/Id`, normalised through
  `validate_bank_account`, so a Norwegian IBAN and the bare BBAN are the same
  eleven digits. A foreign account is refused.

## What the import refuses

All before anything is written. One request is one import, so a file is all of
its statements or none of them.

| Refusal | Code | Why |
| --- | --- | --- |
| A DTD anywhere in the file | `invalid_bank_statement` | See below |
| Not camt.053, malformed, a missing balance or date | `invalid_bank_statement` | |
| Opening + booked entries ≠ closing | `unbalanced_bank_statement` | A file that disagrees with itself is truncated, edited or filtered, and reconciling against it would mark entries matched against an account that never looked like that |
| `TxsSummry` sums ≠ the entries | `unbalanced_bank_statement` | Catches a file cut off at a line boundary whose closing balance was cut with it |
| Any amount, balance or the account not in NOK | `unsupported_statement_currency` | The bank line carries only the charged amount; a conversion would be a figure no document states. Refused whole, because a statement without its currency lines does not add up |
| An account not registered on the company | `unknown_statement_account` | Which accounts are the company's is set on `/organisasjon/#selskap` with `organization:manage`. An uploaded file is not evidence that an account is theirs, and the wrong client's statement is exactly the file that arrives by mistake. The page links there |
| A known bank reference with a different amount, date or direction | `bank_statement_conflict` (409) | The reference is the same booking; one that changed is a bank rewriting history or two files mixed up, and keeping whichever came first without saying so is not an answer |

The opening balance is `OPBD`, or `PRCD` (the previous day's close, which
several banks send instead). Available balances (`OPAV`, `CLAV`, `ITAV`) are
never what is checked: they include reservations.

### No DTD, ever

The file comes from a download folder, not from the bank over a channel we
authenticate, so it is untrusted input to an XML parser: the home of entity
expansion bombs and external entities that read local files. A bank statement
never carries a DTD, so the parser refuses the document the moment it meets
`<!DOCTYPE`, before any entity can be declared. That is stricter than
`defusedxml` (which tolerates a DTD and refuses its entities) and needs no new
dependency: the stdlib's expat is driven directly, every declaration handler set
to refuse, parameter entities off. The MVA module's note on `ElementTree` is
about XML from Skatteetaten over TLS; it does not apply here.

## Idempotency: the dedup key

`bank_statement_line` is unique on `(bank_account_id, dedup_key)`, and that
constraint is the whole mechanism.

**`ref:` + `AcctSvcrRef` when the bank gave one.** The account servicer's
reference is the bank's own identifier for the booking, unique per account at
that bank, and the same in every file the booking appears in whatever period
the export covered. It is the one key under which a re-import and an
overlapping import are both recognised for what they are. The entry's own
`AcctSvcrRef` is used, or the transaction's when the entry has none and exactly
one transaction; with several, any one reference names a part, not the booking.
`NtryRef` is not used: banks number it within the file.

**`fp:` + SHA-256 of the movement's fields + an ordinal when it did not.**
Booking and value date, direction, amount, currency, counterparty, message,
KID, end-to-end id and the bank's free text, hashed. The ordinal keeps two
genuinely identical bookings apart (two 25-krone card fees on one day): within
a statement they are the first and second of that fingerprint, and an
overlapping statement covering the same day numbers them the same way, because
a statement always covers whole booking days. What the fingerprint cannot do is
tell a booking the bank silently changed from a new one, which is why the
reference wins whenever it exists.

Rejected alternatives: the statement's `Id` (banks reuse it across re-exports);
the file's hash alone (catches the same file, not an overlapping one; it is
still used for that, below); `EndToEndId` (set by the payer, often
`NOTPROVIDED`, not unique).

**The same file twice** is caught earlier, by `(company_id, sha256)` on
`bank_statement_file`, and answered with the first import (`already_imported`)
rather than refused: pressing the button twice is not an error. Per company, so
the ENK and the AS that took over its account can each import the same file.

## The archive

The file is primary documentation: the bank's own account of what moved. It is
stored through `myntos.storage` like every bilag (R2, `jurisdiction=eu`, in
production), with its SHA-256, its size and the camt version in
`bank_statement_file`, and there is no delete path. It is downloadable byte for
byte from `/bank/kontoutskrifter/{id}/fil`.

It is not a row in `bilag_attachment`, whose `transaction_pk` is NOT NULL
because an attachment documents one voucher. A statement documents every voucher
on the account for a period, including the ones nobody has entered yet. It
follows `MvaFilingDocument` instead: same storage, same digest, same rule.

Deleting a `CompanyBankAccount` cascades to its statements and lines, which are
the reconciliation work. The file hangs off the company, not the account, so the
bank's record stays in the archive and can be imported again.

## Reconciliation

**A match is a link, never a posting.** `bank_statement_line.transaction_pk`
points at the voucher that books the movement. Setting it touches nothing in the
ledger; a posted voucher is history, and the bank agreeing with it is no reason
to change it. Clearing the link (`DELETE .../avstemming`) is allowed for the
same reason: it was our claim, not the ledger's.

**Suggestions** (`matching.py`) are ranked, never applied:

- Requirements, not points: the same amount to the øre, the same direction, the
  voucher posted through 1920 (a cash payment is a coincidence, not a match),
  still standing (not a reversal, not reversed), not already linked to another
  line, and dated within 14 days before to 7 days after the booking date.
  Lopsided because a card purchase is entered on the receipt's date and booked
  by the bank days later.
- Points: the date (same day, within 3, within 7), the bank's counterparty name
  sharing a word with the voucher's party or description (company forms such as
  `AS` and `NORGE` do not count), the bank's free text doing the same (weaker),
  and the KID appearing in the description. Each comes with the sentence that
  earned it, and the page shows those rather than a number.

**Confirming** re-checks every requirement, because the voucher id arrives from
the browser and a suggestion is not a promise. The voucher is looked up through
the company (`get_voucher`), so one from the company next door is a 404. One
voucher explains at most one line (unique on `transaction_pk`): two lines on one
voucher would make the register look reconciled while the account held twice
the difference.

**Posting from a line** (`POST .../bilag`) goes through `record_cash_flow`, the
register's own path, so the type catalogue decides the account and the VAT
exactly as on `/kontantstrom/`. The amount, the date (booking date) and the
direction are the bank's and are not in the request; the type must go the same
way as the money. The link is recorded as `opprettet` rather than `bekreftet`,
because to an auditor "the entry was made from this movement" and "a person
agreed this entry explains this movement" are different statements.

A matched voucher that is reversed later keeps its link, and the line says
`voucher_reversed`: the link was true when made, and the reversal is the event
worth showing.

**KID → invoice.** When an incoming line's KID equals an issued invoice's
`payment_reference`, the line names the invoice. Settling the receivable from
the line is not built: there is no voucher type for a customer paying 1500 yet.

## Not done

- **PSD2** (account information through the bank's API, so nothing is
  downloaded by hand). Needs a licensed AISP or an aggregator inside the EEA,
  consent renewal every 180 days, and the same import underneath; the parser and
  the dedup key are written so a PSD2 feed can produce `ParsedEntry` values and
  reuse everything after it. Not started.
- Foreign-currency accounts and entries.
- One bank line settling several vouchers, or one voucher split across lines
  (a batch payment of three invoices). Matching is one to one.
- Settling a kundefordring (1500) from an incoming payment with an invoice KID.
- Comparing the statement's closing balance with the ledger's 1920 balance:
  every bank account posts to 1920 today, so per-account balances are not in
  the books to compare against.
- Reconciling an account marked as a bank account in the kontoplan
  (`account.is_bank_account`, such as a driftskreditt on 2390 brought over
  from Tripletex). The overview counts it, but a suggestion still needs a
  voucher through 1920, and nothing links a `company_bank_account` to the
  ledger account its money is booked on. That link is what reconciling it
  would need first.
