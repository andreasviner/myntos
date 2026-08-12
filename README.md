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

> **Status: pre-alpha.** The repository structure, tooling and security
> baseline are in place; the accounting core and government integrations are
> under active development. Nothing here is ready for production bookkeeping
> yet. See [docs/ROADMAP.md](docs/ROADMAP.md).

## Planned features

| Feature | Notes | Status |
| --- | --- | --- |
| Double-entry ledger (hovedbok) | SAF-T-aligned data model from day one | Planned |
| Bilagsføring with PDF archive | Upload and attach vouchers to journal entries | Planned |
| MVA-håndtering | VAT calculation on the standard SAF-T mva codes, automatic MVA-melding | Planned |
| SAF-T Financial export | Required by law (bokføringsforskriften) for electronic bookkeeping | Planned |
| Skattemelding + næringsspesifikasjon | Validate and submit via Skatteetaten's sluttbrukersystem APIs | Planned |
| Årsregnskap to Regnskapsregisteret | Machine filing via Brønnøysundregistrene's Altinn 3 apps (system fills, user signs with ID-porten) | Planned |
| Maskinporten + ID-porten auth | Maskinporten covers retrieval APIs; **submission legally requires an ID-porten login** (Skatteetaten policy, 2026) | Interfaces in place |

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
│   │   ├── auth/            # Maskinporten machine-to-machine auth
│   │   ├── core/            # Configuration, security, cross-cutting concerns
│   │   ├── db/              # SQLAlchemy base, sessions (+ migrations/)
│   │   ├── domain/          # Accounting core: ledger, bilag, mva
│   │   ├── integrations/    # Skatteetaten / Altinn API clients
│   │   ├── saft/            # SAF-T Financial export
│   │   └── storage/         # Document storage for bilag (local / R2)
│   ├── migrations/          # Alembic migrations
│   └── tests/
├── frontend/                # Astro app (pnpm)
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
pnpm install
pnpm dev                                       # http://localhost:4321

# Optional: local PostgreSQL (needed once the ledger lands)
docker compose up -d db
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
