# Handoff: Myntos «Analyse», for Gemini

Written 2026-11-27 by the agent that coordinated the work so far. You are
picking up a feature that several agents have built in this working tree over
two days. Nothing of it is committed. This file is temporary: delete it once
the work is merged, and do not commit it.

---

## 1. Read this first: it is an experiment

The owner's words: **this is a test, and things should be easy to change in
the future with clean, dynamic code, since we don't know what works yet.**

Nobody knows yet which charts people use, whether 90 % is the right threshold,
whether the tags are good enough, or whether the KI part survives at all.
Build so that every one of those can change in one place, without a hunt
through the code. In practice:

1. **Every knob is named and lives in one place.** A threshold, cap, limit or
   label is a module constant with a comment saying why it has that value, or
   a setting in `core/config.py` if it may differ between deployments. Never a
   bare `0.90` or `5000` inside a function. The code already does this; follow
   it:
   - `datasets.TOPP_DEFAULT`
   - `sentences.GROWTH_SHARE` and `sentences.GROWTH_KRONER`
   - `charts.MAX_DONUT_SLICES`
   - `freshness.PROMPT_VERSION` and `freshness.BACKOFF`
   - `items.RULES_VERSION`
   - `ki_analysis_concurrency`, `_batch_size`, `_answer_tokens`, `_read_timeout`
2. **Registries, not branches.** Each of these touches one place and its test:
   - a new chart is an entry in `datasets.DATASETS`;
   - a new view is a value in the chart spec;
   - a new sentence is a template in `sentences.py`;
   - a new facet or seed word is data in `vocabulary.py`.

   If adding one means editing an `if` chain in three files, restructure
   first.
3. **The model sits behind one seam.**
   - The domain never imports `integrations`.
   - Runs take a `jobs.Ki` (built by `api/analyse_ki.get_analysis_ki()`), and
     tests override that dependency.
   - Swapping the model, the level or the provider must not touch the domain.
4. **Old answers go stale by themselves.**
   - Every stored answer is stamped with the time, the level, the model, the
     input hash and the prompt version (docs/analyse.md, «The stamp»).
   - So changing a prompt means bumping `PROMPT_VERSION`, and `freshness.py`
     then knows what to ask again.
   - Never write a migration or a manual purge to "reset" answers.
5. **Wire formats are contracts.** These are validated at the boundary with
   Pydantic on the backend and typed in `lib/api.ts`:
   - the chart spec (`charts.py` ↔ `lib/charts.ts`);
   - the figures response and its `ki` object;
   - the model's answer JSON.

   Change them additively (new optional fields), and change them in
   docs/analyse.md in the same change.
6. **The experiment is removable.**
   - The page works with KI off.
   - Everything KI-made lives in the `ki_*` tables and `domain/analyse/`.
   - Nothing else in Myntos reads them. Keep it that way, so that dropping the
     KI part, if it fails, is a small change.
7. **Simple, not generic.** "Dynamic" means data-driven and in one place, not
   a plugin system or an abstraction for cases nobody asked for. Two similar
   functions are fine; extract when the third appears.
8. **Tests pin behaviour, not internals.** These are the things that must hold
   whatever the implementation:
   - the figures equal `/regnskap/`;
   - slices add up to the total to the øre;
   - another company's data never appears;
   - an unchanged voucher is never asked about twice;
   - KI off means nothing is asked and nothing stored is used.

   Test those, so the internals can be swapped freely.
9. **Write down what you tried.** When you choose between two designs because
   nobody knows which works, write in docs/analyse.md, where it applies and
   marked «Gemini»:
   - the choice;
   - what the other option was;
   - how to switch.

---

## 2. Where the rules live

Gemini CLI loads `GEMINI.md`, not `CLAUDE.md`, so open these yourself:

| File | Why |
| --- | --- |
| `CLAUDE.md` (repo root) | The project rules. They bind you exactly as they bind any other agent. |
| `CLAUDE.local.md` (repo root, gitignored) | This machine. Read «Testing» and «Løynd» before running anything. |
| `docs/analyse.md` | **The contract for this feature.** Tables, JSON shapes, rules 1 to 12, work packages, and «Where it stands», which is the live status. Read all of it once. |
| `docs/company-isolation.md` | Before any code that touches a company id. |
| `docs/agents-and-api.md`, `docs/agents/README.md` | Before any route or page. Every route needs a skill and every page a help article. |
| `docs/frontend-conventions.md` | Before touching a page. |
| `docs/gdpr.md`, `docs/data-sharing.md` | Before any table or text about what is kept. |

---

## 3. What the feature is

- **`/analyse/`** replaces «KI» in the signed-in top bar. It shows a company's
  income and costs over time and how they split, with a few sentences about
  them. Every number comes from the books, on an accounting basis like
  `/regnskap/`: IB and AVS are out, and accounts 83 to 89 are out. It asks no
  model, and works with KI off.
- **With KI on**, Myntos reads the vouchers an analysis needs, at «Gratis»
  (free), in the background. Each voucher gets:
  - a short summary;
  - tags in three facets (`hva`, `motpart`, `flagg`), from one vocabulary per
    company.

  **Tags are never shown.** They are only used to split the pies by what the
  money was for, once at least 90 % of a side is read (D5).
- **«Dyp KI-analyse» and «KI-revisor»** are two buttons for a deeper look at a
  paid level. They are not built yet (WP6, WP7).
- **Charts** all go through one kit (`lib/charts.ts`). The backend and the
  model send a chart spec, never colours or markup.

### Rule 12, settled by the owner on 2026-11-27

It took three steps the same day; this is where it landed:

- **The per-voucher understanding is cached, so it is never asked for twice.**
  That means the summary and tags, and the reports built on them.
- It is **read when an analysis needs it**: a visit to `/analyse/` reads its
  period and the comparison period, and Dyp and Revisor read their period
  first.
- **There is no sweep** that reads every voucher ahead of time. One was
  designed and dropped: ten years of one company's books would take about
  12 hours of both GPU slots.
- **Not kept:** the text read off a bilag, the register's proposals and
  invoice drafts. /ki/ promises the bilag text lives in memory for at most ten
  minutes, and that promise stands.

### Decisions already made (don't reopen them)

- **D2:** KI on is enough to start the free background reading. The first
  `GET analyse` with KI on starts it.
- **D5:** the 90 % rule per side above, with «Ikke lest ennå» (not read yet)
  as its own slice.
- **D7:** accounting basis.
- **Rule 12** as above.
- **Løynd is the only AI.** Use it only through `integrations/loynd.py`, always
  with `"search": false`. The levels are `free` / `medium` / `thinking`.

### Open, for the owner; don't decide them

| Question | What to do |
| --- | --- |
| **D1**: keep the name «KI-revisor»? | Must be settled before WP7. |
| Privacy text | The personvern page's sentence about what is stored in the browser is out of date. |
| Rounding | The result tile can differ by 1 kr from income minus costs, because each figure is rounded on its own. |

If you reach one of these, build the default written in docs/analyse.md and
say so in your report.

---

## 4. Where it stands (2026-11-27, 15:00)

| Package | State |
| --- | --- |
| WP1 `/ki/` linked from more places | Done, uncommitted |
| WP2 the figures (backend, three `GET` routes) | Done, uncommitted |
| WP3 the chart kit | Done, uncommitted |
| WP4 the page, and «Analyse» in the top bar | Done, uncommitted |
| WP5 the KI data layer (tables, tagging, runs) | Done, uncommitted |
| WP5b the page with tags | **Stopped halfway**, see below |
| WP5c no reading twice | **Stopped before its last check**, see below |
| WP6 «Dyp KI-analyse» | After WP5b |
| WP7 «KI-revisor» | After WP6, and after D1 |
| WP8 afterwards | Later |

**WP5b and WP5c were cut off by a usage limit at about 11:08.** Their changes
are in the tree, unfinished, and **nobody has reviewed them**. Treat them as a
draft: read each change before you build on it. No other agent is writing in
the tree now.

### WP5c: no reading twice (small; do it first)

What it reported before it stopped:
- **It found a real gap:** a `merking` run that stopped after an answered
  batch stored none of it. On its test, the old code stores 0 vouchers where
  the fix stores 2.
  - The fix is in `domain/analyse/{jobs,tagging,items}.py`.
  - The test is in `tests/test_analyse_ki.py`.
- **It reverted the background sweep it had started** after the owner dropped
  it: the module `domain/analyse/sweep.py` is deleted, and its two settings in
  `core/config.py` and the line in `tests/conftest.py` are gone. Checked at
  15:00: no sweep is left.
- By its account, ruff, ruff format and mypy are clean. The full suite was
  running when it stopped, and the result is unknown.

To finish:
1. Review the diff in those three files against rule 12 and the WP5c entry in
   docs/analyse.md.
2. Run `tests/test_analyse_ki.py`, then the full suite.
3. Write the WP5c row in «Where it stands».

### WP5b: the page with tags (the main job)

The spec:
- the WP5b entry in docs/analyse.md;
- «The Analyse page», points 4, 6 and 6b;
- «Datasets», the WP5b rows;
- «Runs», «The API», «Privacy».

The full brief the previous agent had may still be at
`/tmp/claude-1000/-home-pcviner-production/b802edaa-cda9-401a-81bf-a0fbc3c56858/scratchpad/wp5b-brief-full.md`.

**What is in the tree.** Verify each item; don't trust this list.

Backend:
- the datasets `inntekter_per_hva`, `kostnader_per_hva` and
  `kostnader_per_motpart`;
- `dekning` (coverage) in `figures.py`;
- `overview.split_for`;
- the two `hva` sentences (`sentences.GROWTH_*`);
- `GET analyse` starting `jobs.start_merking`;
- `GET analyse/kjoringer/{id}`;
- the `myntos-analyse` and `myntos-ki` skills.

Tests: the new `tests/test_analyse_tags.py` (18 tests). The agent was fixing
it when it stopped, so expect failures.

Frontend:
- `analyse.astro` has the caption under a pie split by `hva`.
- It has the KI-off line with «Slå på KI».
- It has the quiet line while KI reads, with polling (`getAnalysisRun` in
  `lib/api.ts`).
- `lib/analyse.ts` and its tests were touched.

**Not done:**
- `/ki/`: the section «Hva som følger med en analyse» with `id="analyse"`.
  `analyse.astro` already links to `/ki/#analyse`, so `pnpm check:seo` fails
  until the section exists.
- `/ki/`: the one sentence under «Slå av KI» saying that switching off stops
  the reading too.
- `personvern.astro`: the KI part.
- `docs/data-sharing.md`: one sentence saying an analysis kept for the
  company's own use is not data sharing.
- `docs/gdpr.md`: check that the tables and the purge by cascade are covered.
- `funksjoner.astro#analyse`: check its `/ki/` link.
- The WP5b box in `docs/ROADMAP.md` «Phase A».
- The WP5b row in «Where it stands».
- The visual check: light and dark themes, at 360 px and at 1280 px. Cover
  KI off, KI reading, and KI read.
- All the checks in section 7.

**The /ki/ and personvern text is legal text the owner reads before
deploying.**
- The reading must not ship without it (docs/analyse.md, «Privacy»).
- Write it minimal and factual, in bokmål. Say that with KI on, Myntos reads
  the company's vouchers in the background **when someone opens «Analyse»**,
  not after posting: there is no sweep (rule 12).
- Mark the change in `personvern.astro` with a bokmål comment.
- Quote it in full in your report.
- «Hva som følger med bilaget» does not change.

### Your task, in order

1. Finish and verify WP5c.
2. Finish WP5b.
3. WP6 «Dyp KI-analyse», only when the owner says so. Build it as its entry
   in docs/analyse.md says.

---

## 5. The code

### Backend (`backend/src/myntos/`)

**`domain/analyse/`:**

| File | What it does |
| --- | --- |
| `figures.py` | The numbers, from the ledger |
| `datasets.py` | The registry of charts, `DATASETS` |
| `charts.py` | The chart spec and its validation |
| `overview.py` | What the default page shows |
| `sentences.py` | The text, as templates |
| `errors.py` | Errors |
| `models.py` | The `ki_*` tables |
| `vocabulary.py` | Facets and seed words |
| `items.py` | What a voucher sends, and its input hash |
| `freshness.py` | When to ask again |
| `tagging.py` | Prompt, parse, validate, store |
| `jobs.py` | Runs, resume, the global limit |

**Elsewhere:**
- `api/v1/analyse.py`: the routes.
- `api/analyse_ki.py`: the real Løynd for a run.
- `migrations/versions/20261126_659e80f0c3f5_ki_analysis.py`: the tables.

**Jobs API**, for your routes. All functions are in
`myntos.domain.analyse.jobs`:
- `start_merking(...)` returns the active run, a new run, or None. It returns
  None when:
  - KI is off;
  - no model is configured;
  - there is nothing to read;
  - Løynd failed less than ten minutes ago.

  It commits.
- `get_run(db, company_id, run_id)`
- `active_run(db, company_id, kind)`
- `cancel(db, company_id, run_id)`

Every lookup carries the `company_id`.

**Tests:**
- `backend/tests/test_analyse_figures.py`
- `backend/tests/test_analyse_ki.py`: the fakes `FakeModel`, `fake_ki`,
  `answer_all`, `tagged` and `vouchers_in`. Override the model with
  `client.app.dependency_overrides[get_analysis_ki] = lambda: fake_ki(model)`.

### Frontend (`frontend/src/`)

- `pages/analyse.astro`
- `lib/analyse.ts` and its tests
- `lib/charts.ts`, `lib/charts.fixtures.ts`, `lib/charts.test.ts`
- the `Analysis*` types in `lib/api.ts`
- the chart tokens in `layouts/BaseLayout.astro`'s global sheet

### What agents read

- `docs/agents/skills/myntos-analyse/` and `myntos-ki/`
- `docs/agents/help/analysis/`
- `llms.txt`

---

## 6. Hard rules (each one exists because something went wrong once)

**Data and safety:**
- **Production data never enters your context.** Never run anything against
  the production database.
  - The test suite reads no `.env` and is safe.
  - Booting the app outside pytest needs `DATABASE_URL` and storage
    overrides; see CLAUDE.local.md «Testing».
  - Your context goes to a model provider outside the EEA, which the
    company's promises do not cover.
- **Tests use fake models only**, never the live Løynd.
- **Nothing is committed, pushed or deployed.** A production deploy is the
  owner's call.
- **The tree holds the owner's many uncommitted changes.** Don't revert,
  reformat or "tidy" anything you didn't write. Edit shared files narrowly.
- **Never generate or print the operator key.**

**Product rules:**
- **One company at a time.** Every sub-resource is looked up with its
  `company_id`. Another company's run or report answers 404, the same as a
  missing one.
- **Permissions come from the backend.** The frontend uses
  `canInCompany(company, perm)`, never a role name.
- **Every route is an API feature.** It needs:
  - an OpenAPI description for someone who doesn't know Norwegian accounting;
  - a skill;
  - help articles with the page's exact labels;
  - its entry in `llms.txt`;
  - a key declaration (`KEY_READ`, `key_write`, `key_post`, `key_send`,
    `KEY_PREVIEW`).

  `tests/test_agent_surface.py` enforces this.

**Code conventions:**
- **Money is `Decimal` end to end.** The frontend uses `Number()` only for
  geometry.
- **Never use the em dash character (U+2014)**: not in code, comments, docs or
  UI text, and check the built HTML:
  `grep -rcP '\x{2014}' frontend/dist/client | grep -v ':0'` must print nothing.
- **Norwegian bokmål for users and in `.astro` comments; English in
  `lib/*.ts` and the backend.**
- **Comments say why**, not what the next line does. Removing a reason is a
  regression.
- **A page's `<style>` doesn't reach DOM its script builds.** Use `:global()`
  or the global sheet.

**Logging and wording:**
- **Logs are templates with ids and counts.** Never log a prompt, an answer
  or a voucher's text (docs/operator.md).
- **Don't name subcontractors or cloud providers** on pages. OVHcloud is named
  only on /trust, personvern and the DPA's Annex IV; elsewhere it's "extra EU
  inference capacity".

**Schema:** a schema change needs an Alembic migration with a docstring that
says what it infers and what it refuses to guess.

---

## 7. Checks before you call anything done

```
cd backend
uv run pytest tests/test_analyse_figures.py tests/test_analyse_ki.py tests/test_agent_surface.py tests/test_ledger_schema.py -q
uv run pytest tests/test_company_boundary.py tests/test_sibling_company_boundary.py -q   # slow, give it time
uv run pytest -q -p no:cacheprovider        # full suite, about 30 minutes, run it in the background
uv run ruff check . && uv run ruff format --check <your files> && uv run mypy src tests

cd ../frontend
pnpm test && pnpm run check                 # astro check must be 0 errors
pnpm build && pnpm check:seo
```

For anything on a page, look at it: light and dark themes, at 360 px and at
1280 px wide.

---

## 8. Known issues that are not yours

| Issue | Status |
| --- | --- |
| `tests/test_saft_innlesing.py` hangs about one run in ten. `_sweep` calls `asyncio.run` beside the test client's event loop. | It predates this work. If it is the only failure, re-run that file alone and say so. |
| `/personvern/` scrolls 2 px sideways at 360 px, because of a wide table. | Pre-existing. |
| A `motpart` tag can be a private person's name, for a sole trader or a person paid. | It is deleted with the company by cascade, but docs/gdpr.md should say so explicitly. Raise it; don't decide it. |
| Capacity: reading at «Gratis» shares Løynd's two GPUs with everyone, and 2 000 vouchers take about 30 minutes, two at a time. | Keep the global limit. Never read ahead of what an analysis needs (rule 12). |

---

## 9. When you finish

- Update your row in «Where it stands» in docs/analyse.md, including what you
  left out.
- Report to the owner:
  - the files created and changed;
  - the commands you ran and their results, as counts rather than logs;
  - deviations from docs/analyse.md, each marked there;
  - any user-facing or legal text you wrote, quoted in full, for the owner to
    read;
  - what you found but did not fix;
  - what the next package needs from you.
