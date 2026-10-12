# Myntos backend

FastAPI application in a `src/` layout, managed with [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                        # install deps into .venv
uv run uvicorn myntos.main:app --reload        # dev server → http://localhost:8000/docs
uv run pytest                                  # tests
uv run ruff check . && uv run mypy src tests   # lint + types
```

Configuration comes from the repo-root `.env` (see [`../.env.example`](../.env.example))
or real environment variables. The database needs the migrations applied before
the API can do anything:

```bash
uv run alembic upgrade head
```

## Package map

| Package | Responsibility |
| --- | --- |
| `myntos.api` | HTTP endpoints, versioned under `/api/v1` |
| `myntos.core` | Settings, security middleware, cross-cutting concerns |
| `myntos.auth` | Two planes: Myntos login (passwords, sessions, federated OIDC login) and government auth (Maskinporten, ID-porten) |
| `myntos.domain` | Accounting core (ledger and the cash-flow register, bilag, faktura, bank, mva, arsoppgjor) plus accounts and organizations — business logic |
| `myntos.integrations` | Skatteetaten / Altinn / Brreg / Løynd / Norges Bank API clients |
| `myntos.saft` | SAF-T Financial export |
| `myntos.db` | SQLAlchemy base + session handling (`migrations/` holds Alembic) |
| `myntos.storage` | Document storage backends for bilag attachments |

Dependency rule: `domain` imports nothing from `api`, and from `integrations`
and `storage` only types and pure functions, never a client. The named
exceptions are the `storage.base.DocumentStorage` protocol (bilag, faktura and
bank services are handed a backend, never choose one), `storage.keys.thumbnail_key`
(a pure function, in the bilag service), and `integrations.brreg.RegisteredUnit`
(a data type, in the ledger service); the reasons are in
[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md#backend-layout-backendsrcmyntos).
Integrations depend on `auth` for tokens. Everything may depend on `core`.
