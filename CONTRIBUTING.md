# Contributing to Myntos

Myntos is developed in the open — the source is public so anyone can read and
audit it — but it is **source-available, not open source**: the license is
proprietary, and that is settled (see [LICENSE](LICENSE) and
[docs/ROADMAP.md](docs/ROADMAP.md)).

Bug reports and feature discussion are very welcome. **Code contributions are
not accepted** without a signed copyright assignment or CLA. Myntos is offered
as a paid hosted service, and code somebody else holds the copyright to could
not be shipped in it on those terms.

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
- **Copyright in contributions:** any merged code must be covered by a signed
  CLA or copyright assignment. A DCO-only flow would leave the contributor
  holding the copyright, and the proprietary license could not cover their
  code. See [docs/ROADMAP.md](docs/ROADMAP.md).
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
  This is about what Myntos sends. A company's own agent reading its books with
  a key the company issued is not Myntos sending anything
  ([docs/agents-and-api.md](docs/agents-and-api.md) rule 8).
- **Every feature is an API feature, and teaches agents, both ways.** The
  route under `/api/v1` comes first and the page is its first client. Every
  route has an OpenAPI description (its docstring) written for someone who has
  never kept Norwegian books. In the same PR the feature has a skill in
  `docs/agents/skills/` (for an agent working through the API) and help
  articles in `docs/agents/help/` (for an AI helping a person use the page,
  with its exact labels), all listed in `docs/agents/llms.txt`. A renamed
  button or a new step on a page updates the articles that name that page.
  Every route also declares what a key may do with it (`openapi_extra=KEY_READ`,
  `key_write("…")`, `key_post("…")` or `KEY_PREVIEW` from `myntos.api.deps`;
  `key_post` for anything that cannot be undone, with the bokmål a person
  approves when the key may only ask), or is listed in `_NOT_FOR_AGENTS` with
  the reason no key may. A route that says nothing is closed to keys.
  `backend/tests/test_agent_surface.py` fails a route, or a page, without all
  of this. Nothing a key could call may file to Skatteetaten, move money,
  change a kontonummer, manage access or approve requests. See
  [docs/agents-and-api.md](docs/agents-and-api.md) and
  [docs/agents/README.md](docs/agents/README.md).

## Reporting bugs and proposing features

Open a GitHub issue. For security problems, **do not** open an issue — follow
[SECURITY.md](SECURITY.md).

## Conduct

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md)
(Contributor Covenant 3.0). Report a problem to conduct@myntos.no.
