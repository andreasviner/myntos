# What is missing: features the other programs have

A ranked list of everything Fiken, Tripletex and the other Norwegian programs
(PowerOffice Go, Conta, Visma eAccounting, Uni Economy / DNB Regnskap, Duett,
Finago) offer that Myntos does not, compiled 2026-10-10 from their product
pages, price lists, help centres, release notes and API specs. Sources are at
the end.

**How this differs from [ROADMAP.md](ROADMAP.md).** The roadmap is what is
being built, phase by phase, and why it was built the way it was. This file is
the backlog behind it: the whole gap, most important first. When work on an
item starts, it gets a roadmap entry; when it ships, it leaves this file.

## How the order was decided

1. **The law, with a date on it.** Things a Norwegian AS or ENK must be able
   to do, where the alternative to the accounting program has been closed.
   Without these Myntos is not an accounting program for that company at all.
2. **What a business needs before it will switch.** Every competitor has it,
   the customer uses it every week, and its absence is the first thing they
   notice.
3. **Daily convenience.** Widely offered, saves real time, but a company can
   run its books without it.
4. **Where Myntos could be better, not just equal.** Things competitors
   charge extra for or do badly, and things Myntos's own rules (EEA, Løynd,
   immutable vouchers) make it good at.
5. **Niche and industry modules.** Real demand, but from a segment.

Inside a tier, items are ordered by how many customers they touch.

## What Myntos already has (not in the list)

Sign-in with password, Google, Microsoft or BankID (through a broker, built
but not yet switched on), address confirmation and
password reset, sessions and account deletion; one account with many
companies, roles, invitations; company setup from Enhetsregisteret and
ID-porten confirmation of who may act for it. The kontantstrøm register with a
type catalogue, gross-to-net VAT split, KI reading of bilag (PDF, photo, HEIC)
through Løynd, foreign-currency receipts at Norges Bank's rate. Customers and
suppliers. An append-only bilag archive with thumbnails. Invoicing with
§ 5-1 checks, gapless numbering, full kreditnota, EHF generated and validated
against the XSD and both Schematrons, PDF. MVA-melding built from the ledger,
validated live in TT02, settlement onto 2740, spesifikasjon (uttak, tap på
krav, justering, tilbakeføring), VAT registration with an effective date. SAF-T
1.30/1.40 export. Bank import from camt.053 with confirmed matching. Opening
balance (typed or saldobalanse CSV), year-end closing and reopening for AS and
ENK, an editable kontoplan.

---

## Tier 1 — The law, with a date on it

| # | Feature | What it means | Who has it | Why it is here |
|---|---|---|---|---|
| 1 | **Send EHF over Peppol** | Deliver the EHF Myntos already generates to the buyer's access point, look the buyer up in ELMA, show delivery status | Fiken (free), Tripletex (4 kr), PowerOffice, Conta, eAccounting | Mandatory for B2B sales from **2027-01-01**. Access point through invoice.be is being arranged (ROADMAP Phase 1d) |
| 2 | **Skattemelding for AS and ENK, with næringsspesifikasjon** | Build the næringsspesifikasjon from the ledger, fetch the draft, validate, submit with the person's ID-porten login; ENK business part plus hand-off to the personal part; correction after submission | Fiken (ENK 1 290 kr/yr, AS 1 590 kr/yr), Tripletex (990 kr/yr), Conta, eAccounting, PowerOffice (ENK only; AS "January 2027") | **Companies can no longer file on skatteetaten.no**; it must come from a program. Auth is proven (ROADMAP Phase 5); the envelope and UI are not built |
| 3 | **Aksjonærregisteroppgaven (RF-1086)** | An aksjeeierbok (shareholders, transfers, capital changes, dividends), the oppgave generated from it with checks, submitted via systembruker | Fiken, Tripletex, eAccounting, Uni | **Since June 2026 it can only be filed through an end-user system**, including corrections for earlier years. Every AS needs it every January |
| 4 | **Årsregnskap to Regnskapsregisteret** | Resultat and balanse from the closed year, mandatory small-company notes plus own notes, prior-year figures, board/general-meeting minutes templates, BankID signing by the board, submission via Altinn | Fiken (in the AS bundle), Tripletex (Penneo signing), Conta, eAccounting | Every AS files it after year-end. ROADMAP Phase 6 has the channel; nothing is built |
| 5 | **Statutory reports and specifications** | Resultatregnskap, balanse, saldobalanse, hovedbok, bilagsjournal, kunde- and leverandørspesifikasjon (open items, aged), MVA report; per period; PDF and Excel | All of them, in the base price | Bokføringsloven § 5 requires the bokføringsspesifikasjoner to be producible. Today only SAF-T and the overview totals exist |
| 6 | **Settle invoices against payments (kundereskontro)** | Register a payment on an issued invoice (full, partial, overpaid, refunded, written off), from the bank line or by hand; open items closed against each other | All of them | An issued invoice posts a receivable that nothing in Myntos ever clears. Bank import names the invoice by KID but does not settle it (docs/integrations/bank.md) |
| 7 | **Fixed assets and depreciation** | Asset register, monthly straight-line booking, tax depreciation by saldogruppe for the skattemelding, sale and write-down | Fiken (base), Tripletex (Smart), eAccounting (Smart), PowerOffice, Finago | Year-end and næringsspesifikasjon are wrong for any company that owns equipment |
| 8 | **Prove MVA filing in production** | A TT02 test company Skatteetaten's MVA register knows, the second `process/next`, then production | (all file today) | Validation answers in TT02; submission has never run (mva.md) |

## Tier 2 — What a business needs before it will switch

| # | Feature | What it means | Who has it | Why it is here |
|---|---|---|---|---|
| 9 | **Live bank integration** | Daily transactions and balances from the bank via an aggregator (Aritma, ZTL), not a camt.053 download; consent renewal every 90 days | Fiken 69 kr/mo (80+ banks via Aritma), Tripletex 49 kr/mo (ZTL), PowerOffice (~40 banks), Uni (all banks via Aritma), Conta | The first question every switcher asks is "does it talk to my bank". camt upload works but nobody does it monthly |
| 10 | **Supplier invoices and unpaid purchases (leverandørreskontro)** | Register a purchase as owed with a due date, see what is unpaid, pay it later, supplier credit notes and prepayments | All of them | The register only records money that has moved. A company with 30-day supplier terms cannot keep its payables in Myntos |
| 11 | **Pay bills from the program** | Send supplier invoices, salary, tax and VAT to the bank for payment; approve in the program (RGB) or in the bank; status back | Fiken (bank add-on), Tripletex (4 kr/payment), PowerOffice (double approval), Uni, Duett | Pairs with 9 and 10; removes retyping KID and account numbers |
| 12 | **Inbox for incoming documents** | One place where photos, PDFs, forwarded e-mails (own address per company) and EHF land, waiting to be booked | Fiken (Innboks, Dropbox sync), Tripletex (Bilagsmottak), PowerOffice | Today a bilag must be booked the moment it is uploaded |
| 13 | **Receive EHF** | Register in ELMA, receive EHF invoices and credit notes into the inbox, pre-fill the purchase from the XML | Fiken (free), Tripletex (4 kr), PowerOffice, eAccounting | Mandatory to receive from 2030; every competitor already does, and suppliers already send it |
| 14 | **Payroll and a-melding** | Employees and employments, tax cards from Skatteetaten, payroll run, a-melding to Altinn, AGA by zone, feriepenger, OTP, payslips, annual summary, NAV refunds | Fiken 69 kr/user, Tripletex 65 kr/user, PowerOffice 195 kr + 35 kr/employee, Conta 49 kr/employee, Finago 49 kr/payslip | Any company with one employee, including the owner of an AS taking salary, files a-melding monthly |
| 15 | **Automatic reminders and debt collection** | Purring plans (dates, fees, interest at the statutory rate), manual reminder and rentenota, hand-off to Kravia/Kredinor/Intrum | All of them | Getting paid is the reason small businesses invoice from a program |
| 16 | **KID on invoices and OCR matching** | KID per invoice, an OCR/Nets agreement, incoming payments booked on the invoice automatically | Fiken 69 kr/mo, Tripletex 4 kr/payment, Conta Smart, eAccounting Smart | Makes 6 automatic |
| 17 | **Import from the old program** | SAF-T import (vouchers, opening balance, open items, accounts), customer/supplier/product import, invoice-number continuation; switching guides per competitor | Fiken, Tripletex (SAF-T 1.2–1.4, reversible), Conta and DNB (free switching help) | Opening balance exists; a whole-history move does not. Switching cost is the main barrier |
| 18 | **Mobile receipt capture** | An app or installable web app: photo of a receipt to the inbox, crop, several pages, offline queue | Every competitor, free | The web camera button exists; people expect an app icon |
| 19 | **Two-factor authentication** | TOTP or passkey on password accounts; mandatory for accountants | Fiken (SMS/app), Tripletex (mandatory for accountants from 2026-09-15) | Accounting data plus filing rights; BankID login (built 2026-10-10, not yet switched on) covers only those who choose it |
| 20 | **Accruals (periodisering)** | Spread an income or cost over months from the invoice or purchase | Fiken, Tripletex, eAccounting Smart, PowerOffice | Prepaid rent and annual subscriptions are common even in micro companies |
| 21 | **Period locking and an event log** | Lock months after MVA filing or reconciliation; unlock with history; who did what and when, exportable | Fiken, Tripletex | Myntos locks years; auditors and accountants expect month locks and a log |
| 22 | **Deadline reminders** | MVA, a-melding, skattemelding, årsregnskap, aksjonærregister, forskuddsskatt: on the front page, by e-mail, as a calendar feed | Fiken (calendar URL), Tripletex (client deadline list) | Missed deadlines cost tilleggsskatt and gebyr; this is what "ro i regnskapet" means |
| 23 | **Accountant and auditor access** | A read-only revisor role; a regnskapsfører seeing all clients at once with status, deadlines and outstanding bilag; client list from Altinn (`altinn:clientdelegations/myclients.read`) | Fiken (partners), Tripletex (Byrå account), PowerOffice, Conta (free) | Half of small companies use an accountant; the accountant picks the program |

## Tier 3 — Daily convenience

| # | Feature | What it means | Who has it |
|---|---|---|---|
| 24 | **Products register** | Products with number, price, VAT, cost price, unit; used on invoices and quotes | All |
| 25 | **Recurring and mass invoicing, customer groups** | Subscriptions sent automatically; one invoice to many customers; groups | All (ROADMAP Phase 1d lists recurring) |
| 26 | **Quotes and order confirmations** | Tilbud with reply deadline and acceptance, ordrebekreftelse, pakkseddel, convert to invoice | All (eAccounting as add-on) |
| 27 | **Partial kreditnota, discounts, invoice in English, logo** | Credit part of an invoice; line and document discounts; English language per customer; company logo | All (partial kreditnota and discounts are in ROADMAP Phase 1d) |
| 28 | **Foreign-currency invoicing** | Invoice in EUR/USD/…, rate fetched, agio/disagio at payment, IBAN/SWIFT shown | Fiken (30+ currencies), Tripletex, eAccounting (ROADMAP Phase 1d; refused today) |
| 29 | **eFaktura, AvtaleGiro, SMS and letter delivery** | Invoices to consumers' nettbank or Vipps; SMS notice; printed letter | Fiken (5/3/25 kr), Tripletex (4/4/30 kr), PowerOffice |
| 30 | **Customer invoice page** | A web page behind the e-mail link with amount, account, KID, PDF, paid status; read receipt for the seller | Fiken |
| 31 | **Bank rules and posting suggestions** | Rules that book bank fees, interest and internal transfers on their own; "book and match" for unmatched lines | Fiken (Superføring), Tripletex, PowerOffice (rule engine with auto-run) |
| 32 | **Payment-service settlements** | Import Vipps, Klarna, Stripe, Zettle, SumUp, Shopify Payments settlements with fees split out | Fiken (daily for Vipps/Klarna/Stripe), Tripletex (Vipps/Klarna) |
| 33 | **Daily cash settlement (Z-report)** | Enter the till's day by VAT rate and payment method, check it balances | Fiken, Tripletex |
| 34 | **Travel, expenses and kjørebok** | Reiseregning with diett at state rates, kilometre allowance with supplements and tolls, employee outlays, approval, payout with salary; kjørebok for ENK | Fiken (69 kr/user), Tripletex (65 kr/user), PowerOffice, Finago |
| 35 | **Budget and budget vs. actual** | Per year by account or group, monthly spread, from last year ±%; shown against the result | Fiken (base), Tripletex (Smart), eAccounting (add-on) |
| 36 | **Approval flow (attestering)** | Send a purchase or payment to an approver; rules by amount, department, supplier; double approval of payments | Tripletex (Smart), eAccounting Smart, PowerOffice, Duett; Fiken in beta |
| 37 | **Departments and dimensions** | Tag postings with department, project or free dimensions; reports filtered by them | Tripletex, PowerOffice, Conta Smart (the SAF-T schema already has analysis dimensions) |
| 38 | **Time tracking, projects and re-invoicing** | Hours per project and activity, project accounting and reports, mark costs and hours for re-invoicing with a markup | Fiken (69 kr each), Tripletex (Komplett), PowerOffice |
| 39 | **Credit check and register warnings** | Credit score on a customer; warning when a customer is under bankruptcy; warning when a supplier charging VAT is not VAT-registered | Fiken (15–19 kr per check; warnings free), Tripletex (29 kr) |
| 40 | **Customs declarations from Altinn** | Fetch the tolldeklarasjon and book import VAT with codes 81–85 | Fiken, Tripletex |
| 41 | **Forskuddsskatt and skatteoppgjør** | Record advance tax; fetch the skatteoppgjør, book restskatt or refund, correct the tax provision | Fiken, Tripletex |
| 42 | **Loans, owner accounts, shares and dividends** | Loan register with interest and instalments; ENK private withdrawals; securities; dividend provision and payout | Fiken |
| 43 | **Reconciliation module** | Payroll vs. A07, holiday pay vs. list, VAT vs. last return, receivables vs. ledger, Skatteetaten's "Krav og betalinger" vs. the books; documentation ZIP | Fiken (base), Tripletex (accounting firms) |
| 44 | **Full export** | ZIP of every bilag, generated document, payslip and per-year Excel, for leaving or for the five-year retention | Fiken, Tripletex (and § 7-5 access after termination; ROADMAP Phase 3) |
| 45 | **Dashboard and cash forecast** | Front page with tasks, deadlines and amounts owed; balance forecast from invoices, VAT, salary and tax | Tripletex (bank dashboard, 3 months), Fiken (front page) |

## Tier 4 — Where Myntos could be better

Each of these is something competitors charge for, do partially, or do in a
way Myntos's own rules would not allow.

| # | Feature | What it means | Competitor state | The Myntos angle |
|---|---|---|---|---|
| 46 | **Automatic posting of incoming invoices** | Per-supplier rules, then model suggestions, then automatic checks; auto-post only when everything passes; line-level coding of EHF; a confidence threshold; an explanation when it did not auto-post; AI-coded postings marked and filterable | Tripletex add-on 50–2 650 kr/mo ("95%"), PowerOffice (dimensions only, off by default), Duett (rules→AI→checks), Uni | Myntos already reads bilag with Løynd, on hardware in Norway. Competitors send data to US model providers (Tripletex names Claude, Gemini, GPT). Must stay off by default per company (ARCHITECTURE § KI) |
| 47 | **Anomaly detection** | Wrong VAT code for the counterparty's VAT status, likely duplicate, unusual amount or supplier, new activity on an account, missing project; a prioritised list at period close | Tripletex, 49 kr per client per month | Myntos already holds the register data (VAT status, orgnr) that the first two checks need |
| 48 | **MVA reconciliation with deviation detection** | Before filing, compare the melding with the ledger and last period's patterns, and explain each deviation | PowerOffice (since 2026-09-23) | Fits the existing review-and-confirm step on `/mva/` |
| 49 | **Year-end and tax return included, not an add-on** | AS skattemelding + årsregnskap + aksjonærregister in the subscription | Fiken 1 590 kr/yr, Tripletex 990 kr/yr, Conta 1 499 kr, PowerOffice AS only from January 2027 | A pricing decision, but the cheapest moat available while PowerOffice cannot file for an AS |
| 50 | **Standalone AS year-end** | Year-end and filings for a company whose books are kept elsewhere (from an imported SAF-T) | Conta (1 499 kr) | SAF-T export and import, opening balance and year-end already exist; this is mostly packaging |
| 51 | **Assistant inside the program** | Ask about the books and have it prepare invoices, entries and reports, nothing posted until confirmed, every action logged | Tripletex (on external US models, plus an MCP server) | Only through Løynd, so it stays in the EEA; the confirm-before-posting rule already exists |
| 52 | **Start a company** | Guided AS or ENK formation: founding documents signed with BankID, share capital, Brønnøysund registration, then books opened with the right opening balance and aksjeeierbok | Fiken (free), Tripletex (via Stiftemaskinen) | The cheapest customer acquisition there is: the books start in the program that founded the company |
| 53 | **Wind up an AS** | Guided avvikling: resolution, final filings, forhåndsfastsatt skattemelding, deletion | Fiken (1 590 kr) | Closes the lifecycle 52 opens |
| 54 | **Public API, agent keys and webhooks** ([agents-and-api.md](agents-and-api.md), ROADMAP Phase 7) | REST API with OAuth, webhooks on invoice and voucher events, a test environment | Fiken (99 kr/mo, no webhooks), Tripletex (552 paths, webhooks, MCP server) | Integrations (webshops, POS, payroll) are built by others once an API exists |
| 55 | **Help in the product** | Plain-language explanations of accounts and terms, "which account?" helper, context help per page | Fiken (Kontohjelp, Ordbok, webinars), Tripletex (Kontohjelp, Sticos) | Myntos already writes its findings with the paragraph they come from; extend that voice to help |

## Tier 5 — Niche and industry modules

| # | Feature | Who has it | Segment |
|---|---|---|---|
| 56 | Stock and logistics (warehouses, purchase orders, goods receipt, stock counts, barcode) | Tripletex (from 248 kr/mo; Rackbeat), eAccounting (149 kr), Fiken (count only) | Retail, wholesale |
| 57 | Webshop and POS integrations (Shopify, WooCommerce, Zettle, PCKasse, …) | Fiken and Tripletex via partners | Retail (needs 54) |
| 58 | Primary industry (næringstyper, primærnæring melding, skogfond, Landbrukets Dataflyt) | Fiken, Tripletex | Farming, fishing |
| 59 | Associations (kontingent, momskompensasjon) | Fiken | Lag og foreninger |
| 60 | Group companies (intercompany ledgers, consolidation, group view) | Tripletex (consolidation planned) | Konsern |
| 61 | DA/ANS deltakerfastsetting, NUF, SA and other company forms in year-end | Fiken, Tripletex | The year-end refuses them today by name |
| 62 | SkatteFUNN, konsernbidrag, rentebegrensning in the tax return | Fiken (SkatteFUNN), Tripletex | Specific AS cases |
| 63 | OTP provider integration (Duvi and others) | Fiken | Payroll customers (needs 14) |
| 64 | Factoring, overdraft (Aprila), business account (Folio) | Tripletex, Fiken, eAccounting, Conta | Embedded finance; partner revenue |
| 65 | Electrical and plumbing package (wholesaler files, Nelfo forms, Boligmappa) | Tripletex (from 699 kr/mo) | Trades |
| 66 | Control forms and checklists | Tripletex (Komplett) | Trades, construction |
| 67 | Capacity and resource planning | Tripletex (Komplett) | Consultancies |
| 68 | Climate accounting (spend-based emissions) | Tripletex via partners, PowerOffice (1 195 kr/yr), Finago | Reporting duty for larger firms |

---

## Things competitors do that Myntos will not copy

- **Deleting a posted voucher.** Tripletex lets the last voucher in a series be
  deleted, permanently. Myntos corrects by reversal only (CLAUDE.md rule 3).
- **Sending books to non-EEA model providers.** Tripletex's Assistant runs on
  Claude, Gemini and GPT. Myntos uses Løynd or nothing (ARCHITECTURE § KI).
- **ID-porten as the product login.** Digdir's terms for private customers do
  not allow it; BankID comes through a broker instead.
- **A payslip PDF whose password is the employee's fødselsnummer.** Fiken's
  default. Anyone who has the number has the payslip; payroll (item 14) should
  not repeat it.

## Price points, for planning

| | Fiken | Tripletex | PowerOffice Go | Conta | eAccounting |
|---|---|---|---|---|---|
| Base | 219 kr/mo, one plan | 199 / 299 / 479 / 649 kr/mo (Basis/Smart/Pro/Komplett), 500 vouchers/yr | 425 kr/mo + per voucher; micro 1 195–2 495 kr/yr | 209 / 339 kr/mo | 159 / 279 kr/mo (Faktura free 12 months) |
| Bank | 69 kr/mo | 49 kr/mo + 4 kr/payment | included | included | Smart only |
| Payroll | 69 kr/user/mo | 65 kr/user/mo | 195 kr + 35 kr/employee | 49 kr/employee | — |
| Year-end, AS | 1 590 kr/yr | 990 kr/yr | January 2027 | 1 499 kr | 109 kr/mo |
| EHF out | free | 4 kr | included | from 3.38 kr | 4 kr |
| Users | unlimited, free | per user | unlimited | — | 89 kr/extra user |

## Sources

- Fiken: [fiken.no/priser](https://fiken.no/priser), help centre index
  [hjelp.fiken.no/llms.txt](https://hjelp.fiken.no/llms.txt) (about 130
  articles read), [API spec](https://api.fiken.no/api/v2/docs/swagger.yaml).
- Tripletex: [tripletex.no/priser](https://www.tripletex.no/priser/),
  [transaksjonspriser](https://www.tripletex.no/transaksjonspriser/), help
  centre (1 714 articles indexed, about 90 read), release notes
  [produktnyheter](https://www.tripletex.no/produktnyheter/) (255 items,
  2024-01 to 2026-09), [OpenAPI spec](https://tripletex.no/v2/openapi.json).
- PowerOffice Go: [priser](https://poweroffice.no/priser),
  [årsoppgjør](https://poweroffice.no/arsoppgjor),
  [help centre](https://hjelpesenter.poweroffice.no/).
- Conta: [priser](https://conta.no/priser),
  [årsoppgjør](https://www.conta.no/regnskap/arsoppgjor).
- Visma eAccounting: [priser](https://www.eaccounting.no/priser/),
  [årsoppgjør](https://www.eaccounting.no/funksjoner/arsoppgjor/).
- Finago: [priser](https://finago.no/priser). Uni Economy:
  [byråpriser](https://unimicro.no/byrapriser). Duett:
  [regnskapsprogram](https://www.duett.no/regnskapsprogram).
- The legal dates: aksjonærregisteroppgaven via system only from June 2026
  ([Skatteetaten RF-1086](https://www.skatteetaten.no/en/forms/aksjonarregisteroppgaven));
  companies' skattemelding only from a program
  ([Revisorforeningen](https://www.revisorforeningen.no/revisornytt/skattemeldingen---selskaper/));
  the duty to receive e-invoices from 2030-01-01
  ([regjeringen.no, 2026-09-25](https://www.regjeringen.no/no/aktuelt/fastsetter-overgangsregel-om-plikten-til-a-motta-e-faktura-og-skattedirektoratet-delegeres-myndighet/id3173725/));
  sending from 2027-01-01 per docs/integrations/efaktura.md.

Prices are as published in October 2026, excluding VAT. Fiken's own pages
disagree on three prices (AS year-end 1 490/1 590 kr, credit check 15/19 kr,
payroll per user or per employee); the price page's figure or the range is
shown.
