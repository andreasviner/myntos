# Contributing to Myntos

Myntos is developed in the open — the source is public so anyone can read and
audit it — but it is **source-available, not open source**: the license is
proprietary while the long-term licensing model is being decided (see
[LICENSE](LICENSE) and [docs/ROADMAP.md](docs/ROADMAP.md)).

Bug reports and feature discussion are very welcome. **Code contributions are
not being accepted yet** — not for lack of interest, but because merging
someone else's copyrighted code before the license is settled would lock the
choice permanently. Once the license lands, this will open up.

This document describes the development setup and the conventions the codebase
follows.

## Development setup

Prerequisites: Python ≥ 3.12, [uv](https://docs.astral.sh/uv/), Node ≥ 22,
[pnpm](https://pnpm.io). See [README.md](README.md#getting-started) for the
full quickstart.

```bash
cp .env.example .env

cd backend && uv sync
cd ../frontend && pnpm install

# Install the git hooks (formatting, lint, secret scanning) once:
cd .. && uv run --directory backend pre-commit install
```

## Quality gates

CI must be green. Run the same checks locally:

| Area | Command |
| --- | --- |
| Backend lint/format | `uv run ruff check .` / `uv run ruff format .` (in `backend/`) |
| Backend types | `uv run mypy src tests` |
| Backend tests | `uv run pytest` |
| Frontend types | `pnpm check` (in `frontend/`) |
| Frontend build | `pnpm build` |

## Guidelines

- **Small, focused PRs** with a clear description beat big ones.
- **Tests accompany behavior.** Accounting logic especially — ledger, MVA and
  SAF-T code needs tests before it merges.
- **No secrets, ever.** Not in code, not in tests, not in fixtures.
- **Domain language:** we use the Norwegian domain terms (bilag, hovedbok,
  mva, skattemelding) in the domain layer — they are precise legal concepts —
  and English for everything else (infrastructure, comments, docs).
- **Commit messages:** imperative mood, explain the *why* in the body when it
  isn't obvious. [Conventional Commits](https://www.conventionalcommits.org)
  prefixes (`feat:`, `fix:`, `docs:` …) are appreciated but not enforced yet.
- **Copyright in contributions:** until the license is settled, any merged code
  must be covered by a signed CLA or copyright assignment. This is what keeps
  relicensing possible — a DCO-only flow permanently fixes the terms of
  contributed code. See [docs/ROADMAP.md](docs/ROADMAP.md).
- **Legal requirements are not negotiable.** Changes touching bookkeeping
  rules (immutability of posted entries, retention, SAF-T mapping) must
  reference the relevant regulation in the PR description.
- **KI goes through Løynd, or it does not go.** AI is welcome anywhere in the
  product where it earns its place, but every prompt leaves through
  `backend/src/myntos/integrations/loynd.py` — never OpenAI, Anthropic, Google
  or a second endpoint "just for this feature". Løynd runs in Norway and stores
  nothing, which is the only reason user documents may reach a model at all.
  A model's answer is never posted to the ledger without a person confirming it,
  and a new use must be described on [/ki](https://myntos.no/ki/) and in the
  personvernerklæring in the same PR. See
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#ki-anywhere-in-the-product-one-provider-only).

## Reporting bugs and proposing features

Open a GitHub issue. For security problems, **do not** open an issue — follow
[SECURITY.md](SECURITY.md).
