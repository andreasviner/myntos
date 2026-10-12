## What does this PR do?

<!-- Short description. Link the issue if one exists. -->

## Checklist

- [ ] Tests cover the new/changed behavior
- [ ] `make lint` and `make test` pass locally (both suites; `make lint` runs ruff, `ruff format --check`, mypy and `astro check`)
- [ ] `pnpm build` passes locally (frontend changes)
- [ ] No secrets, credentials or personal data in the diff
- [ ] Changes touching bookkeeping rules reference the relevant regulation
- [ ] New or changed routes have an OpenAPI description, and the feature's skill (`docs/agents/skills/`) and `llms.txt` say what changed ([agents-and-api.md](../docs/agents-and-api.md) rule 2)
- [ ] New or changed pages (a new page, a renamed button, a new step) are reflected in the help articles that name them (`docs/agents/help/`, rule 14), and new articles are in `llms.txt`
- [ ] New routes declare `KEY_READ` / `key_write("…")` / `key_post("…")` / `KEY_PREVIEW`, `key_post` for anything that cannot be undone, with the bokmål a person approves; or are in `_NOT_FOR_AGENTS` with the reason
- [ ] Nothing here lets an API key file to Skatteetaten, move money, change a kontonummer, manage access or approve requests (rules 5 and 13)
