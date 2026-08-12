# Myntos backend

FastAPI application in a `src/` layout, managed with [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                        # install deps into .venv
uv run uvicorn myntos.main:app --reload        # dev server → http://localhost:8000/docs
uv run pytest                                  # tests
uv run ruff check . && uv run mypy src tests   # lint + types
```

Configuration comes from the repo-root `.env` (see [`../.env.example`](../.env.example))
or real environment variables.

## Package map

| Package | Responsibility |
| --- | --- |
| `myntos.api` | HTTP endpoints, versioned under `/api/v1` |
| `myntos.core` | Settings, security middleware, cross-cutting concerns |
| `myntos.auth` | Maskinporten machine-to-machine auth |
| `myntos.domain` | Accounting core: ledger, bilag, mva — pure business logic |
| `myntos.integrations` | Skatteetaten / Altinn API clients |
| `myntos.saft` | SAF-T Financial export |
| `myntos.db` | SQLAlchemy base + session handling (`migrations/` holds Alembic) |
| `myntos.storage` | Document storage backends for bilag attachments |

Dependency rule: `domain` imports nothing from `api`, `integrations` or
`storage`. Integrations depend on `auth` for tokens. Everything may depend on
`core`.
