# Myntos

**Norwegian accounting with direct submission to Skatteetaten — developed in
the open.**

Myntos is a regnskapsprogram (accounting program) built to file the
skattemelding, næringsspesifikasjon and MVA-melding directly through
Skatteetaten's APIs, with the legally required SAF-T Financial export and a
proper bilag (voucher) archive. The full source is public: accounting software
handles your books and your identity, and you should be able to read exactly
what it does.

> **⚠️ Use at your own risk — på eget ansvar.** Myntos comes with **no
> warranty and no liability whatsoever**. It is not accounting, tax or legal
> advice; you are solely responsible for everything you record and everything
> you submit to Skatteetaten or any other authority. Read
> [DISCLAIMER.md](DISCLAIMER.md) before using the software.

> **Status: pre-alpha.** Accounts, organizations, customers and suppliers, and
> the cash-flow register with its bilag archive work end to end. The
> government integrations authenticate and read: Maskinporten and ID-porten
> tokens, the Altinn exchange, fetching a skattemelding, and creating an
> Altinn 3 instance are all verified against Skatteetaten's test
> environment. **Nothing has been submitted**, and the SAF-T export does not
> exist. Nothing here is ready for production bookkeeping. See
> [docs/ROADMAP.md](docs/ROADMAP.md).

## Features

| Feature | Notes | Status |
| --- | --- | --- |
| Accounts and organizations | Email/password or Google sign-in; colleagues invited into an organization with roles; a subscription will attach to the organization, never to a person | Working |
| Cash-flow register | Money in and out with a category that decides account and VAT code, gross-to-net split, immediate posting, corrections by reversal | Working |
| Fakturering | Salgsdokument checked against bokføringsforskriften § 5-1 before it is issued, with each finding naming its paragraph; gapless machine numbering (a draft has no number, so discarding one leaves no hole); posted and archived on issue; corrected only by kreditnota | Working |
| E-faktura (EHF / Peppol BIS Billing 3.0) | Generated and frozen at issue, ahead of the 2027-01-01 requirement in lov 19. juni 2026 nr. 39. **Transmission is not built** — that needs a Peppol access point; see [docs/integrations/efaktura.md](docs/integrations/efaktura.md) | Partly |
| Double-entry ledger (hovedbok) | SAF-T-aligned data model from day one | Working |
| Bilagsføring with PDF archive | PDFs attached to a posted voucher; append-only, validated by magic number, digest stored | Working |
| Faktura read into the entry form | Upload the PDF first: date, amount, VAT, invoice number and counterparty are read out of the text layer; the category is chosen from a numbered menu by a small model hosted in Norway ([Løynd](https://loynd.no)), optional and off without a key. Nothing posts until the user confirms — see [/ki](https://myntos.no/ki/) | Working |
| An invoice that fills itself in | The buyer comes from Enhetsregisteret on the organisasjonsnummer (name, address, and the number an EHF invoice is addressed by); the KID is assigned at issue as kundenummer + fakturanummer + kontrollsiffer, so the bank's OCR file posts to the right kundereskontro; the lines can be copied from the last invoice to the same customer, or set up from a sentence describing the sale by the same Norwegian model. Every amount is still computed by the ledger, and § 5-1 is still checked at issue | Working |
| Customers and suppliers | SAF-T parties with balance accounts, optional on any payment | Working |
| Document storage | Local filesystem for development, Cloudflare R2 (`jurisdiction=eu`) for production | Working |
| MVA-håndtering | VAT calculation on the standard SAF-T mva codes, automatic MVA-melding | Planned |
| SAF-T Financial export | Required by law (bokføringsforskriften) for electronic bookkeeping | Planned |
| Skattemelding + næringsspesifikasjon | Validate and submit via Skatteetaten's sluttbrukersystem APIs | Planned |
| Årsregnskap to Regnskapsregisteret | Machine filing via Brønnøysundregistrene's Altinn 3 apps (system fills, user signs with ID-porten) | Planned |
| Maskinporten + ID-porten auth | The JWT grant, the ID-porten authorization-code flow, the Altinn token exchange and the Altinn 3 instance lifecycle are implemented and tested against stubs. Maskinporten covers retrieval APIs; **submission legally requires an ID-porten login** (Skatteetaten policy, 2026), which is why `submit` takes a person's token as a required argument. Unused until a client is registered with Digdir — see [docs/integrations/maskinporten.md](docs/integrations/maskinporten.md) | Working, not provisioned |

## Tech stack

- **Frontend:** [Astro](https://astro.build) 7 + TypeScript on Cloudflare Workers — [`frontend/`](frontend/)
- **Backend:** Python 3.12 + [FastAPI](https://fastapi.tiangolo.com) + SQLAlchemy, containerized (target: Cloudflare Containers, EU jurisdiction) — [`backend/`](backend/)
- **Database:** PostgreSQL in an EEA region (migrations via Alembic) — see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the legal storage rules
- **Package managers:** [uv](https://docs.astral.sh/uv/) (Python), [pnpm](https://pnpm.io) (Node)

## Repository layout

```
Myntos/
├── backend/                 # Python API (FastAPI, src layout)
│   ├── src/myntos/
│   │   ├── api/             # HTTP endpoints (versioned)
│   │   ├── auth/            # Myntos login (passwords, sessions, Google) + Maskinporten/ID-porten
│   │   ├── core/            # Configuration, security, cross-cutting concerns
│   │   ├── db/              # SQLAlchemy base, sessions (+ migrations/)
│   │   ├── domain/          # Accounting core: ledger, bilag, faktura, mva, identity
│   │   ├── integrations/    # Skatteetaten / Altinn / Brreg / Løynd API clients
│   │   ├── saft/            # SAF-T Financial export
│   │   └── storage/         # Document storage for bilag (local / R2)
│   ├── migrations/          # Alembic migrations
│   └── tests/
├── frontend/                # Astro app (pnpm) — conventions in docs/frontend-conventions.md
├── docs/                    # Architecture, roadmap, integration guides
├── infra/                   # Deployment (Cloudflare)
└── .github/                 # CI, dependabot, PR template
```

## Getting started

Prerequisites: **Python ≥ 3.12**, **[uv](https://docs.astral.sh/uv/getting-started/installation/)**,
**Node ≥ 22**, **[pnpm](https://pnpm.io/installation)**. Docker is optional
(local PostgreSQL); the test suite runs without it.

```bash
# 1. Configuration — copy the example env file and adjust as needed
cp .env.example .env

# 2. Backend
cd backend
uv sync                                        # create venv + install deps
uv run uvicorn myntos.main:app --reload  # http://localhost:8000/docs

# 3. Frontend (separate terminal)
cd frontend
cp .env.example .env                           # points the site at the API
pnpm install
pnpm dev                                       # http://localhost:4321

# Local PostgreSQL — the backend needs a database to do anything
docker compose up -d db
cd backend && uv run alembic upgrade head
```

Run the quality gates locally before pushing (CI runs the same):

```bash
cd backend
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests
uv run pytest

cd ../frontend
pnpm check && pnpm build
```

## Configuration

All configuration is environment-driven (12-factor). Copy
[.env.example](.env.example) to `.env` and fill in your values — the file
documents every variable. **Never commit `.env` or key material**; the
`.gitignore` and the gitleaks pre-commit hook guard against it, but the
responsibility is yours.

Maskinporten client registration (required for any real Skatteetaten API
calls) is done in Digdir's Samarbeidsportalen and is deliberately kept out of
this repo — see [docs/integrations/maskinporten.md](docs/integrations/maskinporten.md).

## Security

This program will handle accounting data and government API credentials, so
security is a design constraint, not an afterthought:

- No secrets in the repo — enforced by `.gitignore` + gitleaks in pre-commit and CI.
- Maskinporten private keys live outside the repo (or in the gitignored `secrets/`).
- Dependencies are locked (`uv.lock`, `pnpm-lock.yaml`) and watched by Dependabot.
- Security headers and least-privilege API scopes by default.

Found a vulnerability? Please report it responsibly — see [SECURITY.md](SECURITY.md).

## Legal context

Two Norwegian legal requirements shape the architecture:

1. **SAF-T Financial** — entities with bookkeeping obligations that keep
   electronic records must be able to export their accounts in the SAF-T
   format on demand from Skatteetaten. The database schema is therefore
   SAF-T-aligned from the start (standard kontoplan mapping, standard VAT
   codes, immutable journal).
2. **Oppbevaringsplikt** — vouchers and records must stay retrievable for the
   full retention period, which drives the append-only bilag archive and the
   choice of storage location. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Contributing

Myntos is not currently open to outside contributions. See
[CONTRIBUTING.md](CONTRIBUTING.md) for development setup and the conventions
used in this codebase.

## License

**Source-available, not open source.** The code is public so anyone can read,
audit and learn from it, but the license is proprietary — copyright © 2026
Andreas Lindeman, all rights reserved (see [LICENSE](LICENSE)). No right to
use, copy, modify or redistribute is granted, and cloning the repository does
not convey one.

This is deliberate and temporary. An OSI license is irreversible for every
snapshot it is applied to, so the choice is deferred until the shape of the
project — and whether a hosted service is offered — is settled. The intent is
to land on an open-source or source-available license (AGPL, BSL or FSL) before
the first public release; see [docs/ROADMAP.md](docs/ROADMAP.md). Until then,
please don't describe Myntos as open source — it isn't yet.
