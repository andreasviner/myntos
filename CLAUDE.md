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
| [docs/agents-and-api.md](docs/agents-and-api.md) | **Any new route, page or feature.** Every feature is a public API feature and ships with a skill for agents and help articles for an AI helping a person; keys, what a key may never do, what waits for the person's approval, and why a pasted key must die on first use |
| [docs/agents/README.md](docs/agents/README.md) | **Writing or changing a skill or a help article**, which every route and every page change needs. The two halves of what agents read, the areas and their order, the exact shape of each file |
| [docs/landbruk.md](docs/landbruk.md) | Anything with `segment="landbruk"`, `company.landbruk` or a farm account or category, and any other industry add-on. Myntos is for everyone: an industry gets an add-on, never a different product. The farm VAT rates, why a settlement from Tine is a sale, and the farm's list of what is built and what the law still requires |
| [docs/anlegg.md](docs/anlegg.md) | Anything about driftsmidler, the anleggsregister, saldoavskrivning or the `AVSKR` journal. The rates are a year's, the saldo is derived and never stored, and the books follow the tax figures |
| [docs/gdpr.md](docs/gdpr.md) | Account deletion, deleting a company, the session list, or any column that points at `app_user`. What is the person's to erase and what is the books' to keep, why a deleted bookkeeper leaves a tombstone with their name, and why a deleted company's books stay locked for five years before the purge |
| [docs/frontend-conventions.md](docs/frontend-conventions.md) | Adding or changing a page. `hidden`, session and chrome, forms, the text rules |
| [docs/integrations/](docs/integrations/) | Maskinporten, ID-porten, Altinn, MVA, SAF-T, EHF, bank statements, BankID login (Idura), Landbrukets Dataflyt. One file per counterparty |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Deciding what to build next: what is being built, phase by phase |
| [docs/FEATURES.md](docs/FEATURES.md) | Choosing what to build after that: everything Fiken, Tripletex and the rest have that Myntos lacks, ranked, with sources |
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
   switched it on. See ARCHITECTURE.md § KI. Two exceptions, both written down:
   account mail (address confirmation, reset, invitations), which Cloudflare
   sends without an EEA promise and which therefore never carries accounting
   data (ARCHITECTURE.md § What the mail provider sees); and a company's own
   agent reading its books with a key the company chose to issue
   (docs/agents-and-api.md rule 8).
6. **Norwegian for the user, English for the code.** `.astro` comments are
   bokmål; `lib/*.ts` and the whole backend are English. Never "åpen
   kildekode": Myntos is source-available.
7. **Comments say why.** This codebase explains the decision and the failure it
   came from, not what the next line does. Match that; a change that removes a
   reason is a regression even when the code is identical.
8. **Every feature is an API feature, and every feature teaches agents, both
   ways.** It is an endpoint under `/api/v1` before it is a page, with an
   OpenAPI description written for someone who does not know Norwegian
   accounting, and in the same change it ships:
   - a **skill** (`docs/agents/skills/<feature>/SKILL.md`) for an agent
     working in the books through the API, and
   - **help articles** (`docs/agents/help/<area>/<topic>.md`, one per task)
     for an AI helping a person use the page, with the page's exact labels,

   both listed in `llms.txt` in the order docs/agents/README.md gives. Renaming
   a button or adding a step updates the help articles whose `pages:` name
   that page. `tests/test_agent_surface.py` fails a route without its skill, a
   page without a help article, a file `llms.txt` does not list and a dead link
   between them; the lists of older routes in it only shrink. Every route says
   what it is to a key: `openapi_extra=KEY_READ`, `key_write("…")` (undoable),
   `key_post("…")` (final), `KEY_PREVIEW` (stores nothing), or a reason in
   `_NOT_FOR_AGENTS`; undeclared is closed. The string in `key_write`/`key_post`
   is the bokmål a person reads when a key's request waits for their approval
   on `/godkjenning/` («Claude vil føre en betaling»). A key never files to
   Skatteetaten, moves money, changes a kontonummer, manages access or approves
   requests, whatever its scopes. See
   [docs/agents-and-api.md](docs/agents-and-api.md) and, for writing either
   kind of file, [docs/agents/README.md](docs/agents/README.md).

## Checks before you call something done

```
make test                            # both suites
make lint                            # ruff, ruff format --check, mypy, astro check
```

or one at a time:

```
cd backend && uv run pytest          # ~1900 tests, ~15 min. Reads no .env, so it
                                     # runs safely beside production's
cd frontend && pnpm test             # vitest, src/lib only
cd frontend && pnpm run check        # astro check, must be 0 errors
```

A schema change needs an Alembic migration in the same commit, with a docstring
that says what it infers and what it refuses to guess.
