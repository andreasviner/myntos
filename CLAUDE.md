# Working on Myntos

Myntos is a Norwegian accounting program: FastAPI + SQLAlchemy (async) behind a
prerendered Astro site, filing to Skatteetaten through Altinn. Norwegian
bokkeeping law is a hard constraint, not a feature area.

**Read the doc for the area you are about to touch before you touch it, and
update it in the same change.** The docs are small and split by subject on
purpose: a rule that has moved out of the file that states it is worse than no
file. Every one of them exists because something went wrong once, and the
paragraph explaining why is the part worth keeping.

## Where the rules live

| Doc | Read it before |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Anything structural. Tenancy model, the two auth planes, deployment, the data-model constraints that are legal rather than technical |
| [docs/company-isolation.md](docs/company-isolation.md) | **Any code that touches a company id, on either side.** One account holds many companies; the walls that keep them apart are built twice, differently, and the second one is easy to breach by accident |
| [docs/register-access.md](docs/register-access.md) | Anything that joins Enhetsregisteret's board list to the member list, or that touches the ID-porten identity link. The join has no key: it is a name match, and the mark has to say how much the match is worth |
| [docs/frontend-conventions.md](docs/frontend-conventions.md) | Adding or changing a page. `hidden`, session and chrome, forms, the text rules |
| [docs/integrations/](docs/integrations/) | Maskinporten, ID-porten, Altinn, MVA, SAF-T, EHF. One file per counterparty |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Deciding what to build next |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Commits, style, running the checks |
| `CLAUDE.local.md` (gitignored, if present) | Anything about *this machine*: what runs where, deployment, live integration state |

## The rules that survive every refactor

1. **One company at a time, and never the wrong name.** Access is a row per
   person per company (`CompanyMembership`), so an invitation, a role and a
   subscription all belong to one set of books. Company-scoped routes go through
   `CurrentCompany`; every sub-resource is looked up with its `company_id`; the
   frontend keeps the active company as one record and refuses requests that
   disagree with it. The account owns companies and is never shown, and nothing
   may copy a company's name onto it. See
   [docs/company-isolation.md](docs/company-isolation.md).
2. **Permissions come from the backend.** `domain/identity/access.py` owns the
   role to permission table. The frontend calls `canInCompany(company, perm)`
   for anything about a set of books, and never branches on a role name.
3. **A posted voucher is history.** No edits, no deletes: corrections are
   reversals plus a replacement, linked. The bilag archive is append-only,
   because bokføringsloven requires five years of primary documentation.
4. **Money is `Decimal` end to end.** No floats anywhere near an amount.
5. **Nothing leaves the EEA**, and nothing reaches a model unless the company
   switched it on. See ARCHITECTURE.md § KI.
6. **Norwegian for the user, English for the code.** `.astro` comments are
   bokmål; `lib/*.ts` and the whole backend are English. Never "åpen
   kildekode": Myntos is source-available.
7. **Comments say why.** This codebase explains the decision and the failure it
   came from, not what the next line does. Match that; a change that removes a
   reason is a regression even when the code is identical.

## Checks before you call something done

```
make test                            # both suites
make lint                            # ruff, ruff format --check, mypy, astro check
```

or one at a time:

```
cd backend && uv run pytest          # 906 tests. Move the repo .env aside first,
                                     # or ~212 fail on production config
cd frontend && pnpm test             # vitest, src/lib only: the company wall
cd frontend && pnpm run check        # astro check, must be 0 errors
```

A schema change needs an Alembic migration in the same commit, with a docstring
that says what it infers and what it refuses to guess.
