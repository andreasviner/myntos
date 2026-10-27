# Faktura og e-faktura

What a Norwegian sales document must contain, what changes in 2027, and what
this system does and does not do about it.

Sources are linked inline. Where a rule is quoted, it is quoted rather than
paraphrased — the wording is sometimes prescribed.

## The two regimes

**bokføringsforskriften kapittel 5-1 and 5-2** — in force now. Defines what a
salgsdokument must contain, when it must be issued, and how it is corrected.

**Lov 19. juni 2026 nr. 39** — *Lov om endringer i bokføringsloven og enkelte
andre lover på finansmarkedsområdet (pliktig digital bokføring og e-fakturering
mv.)*, [LTI/lov/2026-06-19-39](https://lovdata.no/dokument/LTI/lov/2026-06-19-39).
Documentation of sales of goods and services to another bokføringspliktig must
be issued in an electronic invoice format:

> Dokumentasjon for salg av varer og tjenester til andre bokføringspliktige skal
> utstedes i elektronisk fakturaformat

Dates, from Prop. 62 LS and the departmental transitional rules:

| Date | What |
| --- | --- |
| 2027-01-01 | **Sending** e-invoices to bokføringspliktige buyers becomes mandatory |
| 2030-01-01 | **Receiving** e-invoices becomes mandatory, and all bokføring must be done in an electronic system |

Exemptions were signalled during the hearing for very small entities (turnover
below ~50 000 kroner without bookkeeping or VAT obligations) and possibly for
businesses already on other electronic formats. **The exemption list is not
settled** — do not implement one from this document.

Consumers (B2C) are outside the requirement entirely. That is why
`SalesDocument.buyer_is_business` is asked rather than inferred: a missing orgnr
is evidence of nothing.

## § 5-1-1 — what a salgsdokument must contain

Implemented in `myntos.domain.faktura.compliance`, one check per numbered
requirement, each carrying its own rule reference so the interface can say *why*.

1. **Nummer og dokumentasjonsdato.** Assigned by `service.issue()` from the
   company's `DocumentSeries` counter. Never on a draft — see § 5-1-3 below.
2. **Partene** (§ 5-1-2, detail below).
3. **Ytelsens art og omfang.** Every line needs a description and a quantity.
4. **Leveringstidspunkt og leveringssted.** Both mandatory. In EHF these become
   `cac:Delivery/cbc:ActualDeliveryDate` and `cac:DeliveryLocation`.
5. **Vederlag og forfallsdato.** A due date in the past is *lawful* — an invoice
   issued late with terms counted from delivery produces one — so it warns rather
   than blocks, and there is deliberately no database constraint on it.
6. **Merverdiavgift i norske kroner.** VAT must be stated in NOK even on a
   foreign-currency invoice. This system is **NOK-only** for now and refuses
   another currency outright rather than issuing a document without the NOK
   restatement. Lifting that is the main known gap.
7. **Omvendt avgiftsplikt.** When it applies, the document must be marked. The
   prescribed wording is in `vat.REVERSE_CHARGE_MARKING`; it appears in the PDF
   and as a `cbc:Note` and `TaxExemptionReason` in the XML.

### § 5-1-2 — the parties

**Buyer**: name, plus address *or* organisasjonsnummer. The name alone is not
enough.

**Seller**: name and orgnr. When VAT-registered, the orgnr must be followed by
the letters **MVA** — hence `company.vat_registered`, which also gates whether
output VAT may be charged at all (charging it unregistered means owing it
anyway). Aksjeselskap, allmennaksjeselskap and NUF must additionally show the
word **Foretaksregisteret** and the head-office address — hence
`company.in_foretaksregisteret` and `company.organisation_form`, both of which
the signup flow takes from Enhetsregisteret (`registrertIForetaksregisteret`)
rather than asking for. All three are editable afterwards at
`/organisasjon/#selskap`, which is also the only place they can be edited.

### Kontonummer — not in § 5-1, and blocking anyway

A kontonummer is not something the regulation asks for. Having **none** blocks
an issue regardless (`compliance._check_payment_account`), because an invoice
without it is a demand for money with no account on it: the PDF omits the line,
and `cac:PaymentMeans` is left out of the XML **entirely** rather than emitted
incomplete — an empty one fails BR-49 — so the KID goes with it. There is no
edit path afterwards; the only correction is a kreditnota and a second invoice.

**A company has as many accounts as its bank gave it**, so this is a table
(`CompanyBankAccount`) and not a column: a driftskonto to invoice against, a
skattetrekkskonto that skattebetalingsloven § 5-12 requires be kept apart from
the operating money, a currency account. It used to be `company.bank_account`,
one column, and everyone with two put the second one nowhere.

Three consequences worth keeping straight, because they are the ones an invoice
depends on:

- **Exactly one account is `is_primary`** for as long as the company has any,
  enforced by a partial unique index and not only by the service. It is where an
  invoice is paid when it names no account itself.
- **A document names its own account** (`SalesDocument.bank_account_id`), and
  that is the choice. `/faktura/` offers the picker **only** when the company has
  more than one account — a select with one option is a question nobody was
  asked — so a one-account company behaves exactly as it did before.
- **`seller_bank_account` on the document is still the snapshot**, and it is what
  the PDF and the XML read. The key is `ON DELETE SET NULL`, so closing a bank
  account cannot change what an issued invoice said, and cannot be blocked by a
  draft from March: the draft falls back to the primary the next time it is
  saved, the issued document beside it notices nothing.

The number is eleven digits with a mod-11 check digit over the first ten,
weighted 2, 3, 4, 5, 6, 7 from the right — the same arithmetic as a MOD11 KID,
which is why `domain/ledger/bankkonto.py` borrows `kid.mod11_check_digit`
instead of writing it twice. It is validated when entered, not at issue: a
transposed pair is caught by the person typing it, and discovered otherwise by a
customer whose payment went nowhere. A Norwegian IBAN is reduced to the eleven
digits inside it; a foreign one is refused, because neither renderer can present
it correctly yet.

### § 5-1-3 — numbering

> forhåndsnummerert på trykte blanketter eller ved maskinelt tildelte nummer

The series must be complete enough to check. Two consequences in the code:

- A **draft has no number at all.** Numbering drafts is how a series acquires
  gaps, and a gap reads to an auditor as a deleted invoice. The
  `number_iff_issued` check constraint enforces the pairing in the database
  rather than trusting every code path that writes a status.
- Numbers come from a **counter row** (`sales_document_series`), not from
  `MAX(...) + 1`. The unique constraint on `(company_id, document_number)` is
  the backstop that turns a concurrent double-issue into one failed request.

Faktura and kreditnota share one series, so completeness stays provable by
counting.

### KID — and why it can only exist after numbering

A KID is not a legal requirement; nothing in § 5-1 asks for one. It is what makes
an incoming payment reconcilable, and it does that by carrying the number of the
invoice it pays:

```
  1 3 7   0
  └─┬─┘   ┬
  fakturanr  kontrollsiffer
              (mod10)
```

The **invoice number** identifies the document, and through it the customer:
the series is one per company and never reused, so a KID names exactly one
invoice in the books. The **check digit** is what gets a mistyped KID rejected
at the keyboard instead of reconciled against somebody else's invoice.

**No customer number and no zero-padding, since 2026-10-23.** The KID used to be
a five-digit customer number and a six-digit invoice number, both zero-padded,
so a typed-in buyer's first invoice read `000000000018`. People copy a KID off a
paper invoice into their bank, and nobody counts nine zeros right. The customer
number had been justified as what lands a mismatched payment (a partial payment,
two invoices in one transfer, a number off an old reminder) on the right
customer, but every one of those still carries some invoice's KID, and that
invoice names its buyer. The fixed width had been justified by the KID-avtale,
the bank's OCR agreement that fixes the length; Myntos does not model that
agreement, and a seller who has one types their own KID (manual, below). Reading
a KID back is a lookup of `payment_reference` on the issued documents, not a
parse by position, so nothing needed the widths. Invoices issued before the
change keep the KID they were issued with.

The invoice number puts all of this downstream of § 5-1-3: a draft has no number,
so a draft has no KID either. `myntos.domain.faktura.kid` builds it and
`service.issue()` calls that in the same breath as assigning the number — before
the EHF and the PDF are rendered, since a payment reference the documents do not
show is one nobody can pay by.

Three states, carried by two columns (`payment_reference`,
`payment_reference_auto`):

| State | Columns | Meaning |
| --- | --- | --- |
| automatic | `auto = true`, reference `NULL` until issue | the default; issue assigns it |
| manual | `auto = false`, reference set | the seller's own, from their bank agreement |
| none | `auto = false`, reference `NULL` | no KID on this invoice |

`GET /faktura/neste-nummer` previews the number and KID the next issue would
produce, so the form can show it. It **reserves nothing**: reserving on sight
would let an abandoned draft take a number out of the series, which is the gap
§ 5-1-3 is about, so the interface labels what it shows as a preview.

We generate **MOD10** (Luhn); `kid.is_valid` accepts MOD10 **or** MOD11, because
which one applies is agreed between the seller and their bank and this system is
not told. A hand-typed KID failing both is an `advarsel`, never a refusal.

A kreditnota gets no KID: it reverses a payment rather than asking for one.

**Known gap:** a KID-avtale (the bank's OCR agreement) may fix the length and
the scheme, and Myntos does not model that agreement. When it does, the length
belongs in `kid.build()`, padded from the agreement rather than always. Until
then, a seller whose bank agreed a fixed format types their own.

### § 5-1-5 — separate presentation

Taxable, zero-rated and exempt sales must each appear and be summed *for
themselves*, and each rate separately. This is one grouping — by VAT category
and rate — and it is the same structure EN 16931 wants as `TaxSubtotal`, so
`myntos.domain.faktura.totals.breakdown` serves the regulation and the standard
with one computation instead of two that could disagree.

## § 5-2 — when, and how it is corrected

- **§ 5-2-2**: issue *snarest mulig og senest en måned etter levering*. Past the
  deadline this system **warns** and issues anyway: the document is late either
  way, and refusing would leave the sale undocumented, which is the worse breach.
- **§ 5-2-7**: a sales document being replaced is reversed by a **kreditnota**.
  There is therefore no edit and no delete path for an issued document. The
  credit note is issued immediately (never a draft), copies the original's lines
  at the original's rates, and its ledger side goes through the ledger's own
  `posting.build_reversal` so `reverses_transaction_pk` is set and the correction
  is legible to SAF-T and to an auditor.

Crediting is **all-or-nothing**. Partial credit notes are not implemented; a
partial correction would need its own lines, and pretending a full reversal
covers it would leave the paper and the ledger disagreeing.

## EHF / Peppol BIS Billing 3.0

EHF is Norway's profile of Peppol BIS Billing 3.0, itself a CEN **EN 16931**
implementation on **OASIS UBL 2.1**. `myntos.domain.faktura.ehf` writes it
directly, for the same reason `myntos.storage.sigv4` signs its own requests.

Fixed identifiers:

```
CustomizationID  urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0
ProfileID        urn:fdc:peppol.eu:2017:poacc:billing:01:1.0
```

Norwegian orgnr is Peppol scheme **0192**; that identifier is what an access
point routes on, which is why a B2B invoice without the buyer's orgnr cannot be
delivered and the compliance check says so.

Things that bite:

- **UBL is `xsd:sequence`.** Right elements in the wrong order is an invalid
  document. `Invoice` and `CreditNote` do not share an order.
- **Root namespace.** ElementTree's `default_namespace=` cannot be used: it
  rejects unqualified *attribute* names, and `currencyID`, `schemeID` and
  `unitCode` are all unqualified. The root is built with a literal `xmlns`
  attribute instead.
- **Zero-rate categories need a reason** (BR-E-10, BR-G-10, BR-AE-10) —
  **except `Z`, which must not have one** (BR-Z-10): a zero rate is a rate, not
  an exemption. The PDF prints the reason for `Z` anyway; the XML leaves it out.
- **Category `O` cannot be mixed** (BR-O-11). Norwegian *unntatt* omsetning is
  therefore mapped to `E`, which is mixable, not to `O`.
- **One `cbc:Note`** (PEPPOL-EN16931-R002). The markings (Foretaksregisteret,
  omvendt avgiftsplikt, Kreditnota) and the free text are joined into it, one
  per line. A kreditnota for an AS used to carry three and would have been
  refused.
- **A buyer reference or an order reference is mandatory** (PEPPOL-EN16931-R003),
  though nothing in § 5-1 asks for either and most small buyers never give one.
  When the document has neither, `BuyerReference` says `Ikke oppgitt` — not the
  buyer's name or our invoice number, because BT-10 is the *buyer's* routing key
  and a plausible value that is not theirs would be routed on as if it were.
- **Foretaksregisteret is a tax registration, to Peppol.** NO-R-002 reads the
  word from `PartyTaxScheme` with scheme `TAX` (BT-32), not from a note. It is
  written there and in the note.
- **A seller outside Merverdiavgiftsregisteret states its orgnr as BT-32.** Its
  lines are category `E`, and BR-E-02 refuses an `E` line from a seller with no
  tax identifier at all. The orgnr is the identifier Skatteetaten knows it by;
  XRechnung does the same for the German Kleinunternehmer with the Steuernummer.
  A seller in Foretaksregisteret already has the marking in that slot, and
  UBL-SR-13 allows one.
- **No buyer orgnr, no Peppol document** (PEPPOL-EN16931-R010). The buyer's
  `EndpointID` is mandatory, and for a buyer without a Norwegian orgnr — a
  consumer, or typically a foreign business — there is nothing honest to put in
  it. The XML is still produced (it is the frozen record of what was issued),
  but it fails R010, and the compliance check is what stops it being sent to a
  business: a warning now, blocking from 2027-01-01. The validation test pins
  both halves, so the refusal cannot be loosened by making the XML look valid.

### Validation against the published rule sets

`backend/tests/test_faktura_ehf_validation.py` runs what the real code path
produces — company, draft, issue and kreditnota over HTTP, then the frozen XML
off the download endpoint — through the three validators an access point runs:

| Layer | Artefact | Version |
| --- | --- | --- |
| Syntax | OASIS UBL 2.1 XSD (lxml) | UBL 2.1 OS |
| Semantic model | CEN EN 16931 UBL Schematron, CEN's own compiled XSLT (Saxon-HE) | 1.3.16 |
| Peppol + Norwegian rules | Peppol BIS Billing 3.0 Schematron, compiled with SchXslt (Saxon-HE) | 3.0.21 |

Vendored unmodified under `backend/tests/fixtures/ehf/`, with sources,
licences and checksums in the README there. SAF-T output is validated the same
way, against vendored schemas with the official examples as a control
([saf-t.md](saf-t.md#what-myntos-does-built-2026-10-09), **Tests**).

Shapes covered: every rate and zero category on one invoice, zero-rated alone,
fractional quantities with a four-decimal price and a discount, reverse charge,
a kreditnota referencing its invoice, payment means with and without KID, a
seller outside the VAT register, a seller in Foretaksregisteret, and the
buyer-without-orgnr case above.

Two controls keep the test honest. Every official Peppol example (and the
Norwegian national example) must pass — if one fails, the validator is broken,
not the generator. And a deliberately broken document must fail at each layer
(BR-CO-16, PEPPOL-EN16931-R010, an XSD sequence error), because a validator
that passes everything is worse than none.

**When Peppol publishes a new release** (twice a year, May and November), drop
the new `.sch` and examples in a new versioned directory, point the test at it,
and fix what fires. The release notes say which CEN version it adopts.

### Warnings left standing

Fatal findings are all fixed. One warning is accepted, and the test fails on
any other:

- **NO-R-002** ("most invoice issuers are required to append
  Foretaksregisteret") fires for every seller *not* in Foretaksregisteret.
  § 5-1-2 requires the word only of the forms registered there (AS, ASA, NUF);
  an enkeltpersonforetak outside the register that wrote it would be stating
  something untrue on its own invoice.

The XML is **frozen at issue** and stored on the row. From 2027 it *is* the sales
document for a business customer; a document rebuilt on every download is one
that can quietly differ from what was sent.

### Not implemented: transmission

Myntos **produces** the EHF file. It does not send it. Sending over Peppol
requires an access point — a commercial service, or running one and being
certified — and is the remaining work before the 2027 requirement is actually
met rather than merely prepared for. Until then the file is downloadable and can
be handed to whatever channel the business already uses.

## The PDF

`myntos.domain.faktura.pdf` writes the readable copy with no PDF library: a
fixed layout in the base-14 Helvetica, which every conforming reader has, so
nothing is embedded. It is filed into the append-only bilag archive at issue,
because bokføringsloven § 13 wants the document readable for five years and XML
is not what a person opens in 2031.

Text is **WinAnsi (cp1252)**, not UTF-8 — a base-14 Type1 font maps single
bytes, and UTF-8 produces mojibake on exactly æ, ø and å.

## Known gaps

- **No Peppol transmission** (above). The blocking item for 2027.
- **NOK only.** Foreign currency needs the § 5-1-1 nr. 6 NOK restatement of VAT
  and a `TaxCurrencyCode` + second `TaxTotal` in the XML.
- **No partial kreditnota.**
- **The KID-avtale is not modelled** (above), so the generated KID may not match
  the format the seller's bank agreed. A seller in that position types their own.
- **No document-level discounts or charges**; `tax_exclusive` is kept distinct
  from `line_total` in `DocumentTotals` so adding them is not a schema change.
- **A buyer without a Norwegian orgnr cannot be addressed in Peppol** (above).
  Foreign buyers need their own Peppol participant id (another EAS scheme) on
  the party before their EHF can validate or be sent.
- **Exemption thresholds from the 2026 law are not implemented**, because they
  are not settled.
