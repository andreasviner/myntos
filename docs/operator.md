# The operator's page and the error log

What Myntos keeps that belongs to no company, in one place for whoever runs
the deployment: every stranger's feedback in their own words
([feedback.md](feedback.md)), and what failed on the server. The page is
`/drift/`, behind `/api/v1/drift/*`; the failures are the table
`server_error`, filled by a logging handler. Built 2026-10-28 (migration
`9c4f2e7a1b35`), the day a customer wrote that he got «Noe gikk galt hos oss»
importing from Fiken, and the only way to find out what had happened was to
ssh in and grep the journal, which names the client's address next to every
traceback.

Read this before touching `domain/server_errors/`, `auth/operator.py`,
`api/v1/operator.py`, `pages/drift.astro`, `lib/drift.ts`, **any log call at
ERROR** (what it logs ends up here, minus its values), or the
personvernerklæring's bullet on server errors.

## Setting it up

```
cd backend
uv run python -m myntos.auth.operator
```

prints a new key, `myntos_op_…`, for the password manager, and the line
`OPERATOR_KEY_SHA256=…` for `.env`. Put the line in, restart the backend, open
`/drift/` and paste the key. The key is not stored anywhere on the machine; a
lost key is replaced by running the command again, and the old one stops
working at the restart that reads the new digest.

## The key, and why it is not a role

The objection to an operator page was always that reading every stranger's
words would be the most attractive route in Myntos, guarded by whatever an
operator role turned out to be ([feedback.md](feedback.md)). So it is not a
role, and not anything a person or a company has:

- **One key per deployment, 256 random bits behind a prefix**, stored as its
  SHA-256 digest like every other credential (ARCHITECTURE.md, Security
  model). Compared in constant time. No lockout, because guessing it is not a
  way in. The prefix lets a secret scanner, and a person, recognise it.
- **A bearer token, never a cookie.** The page sends it with
  `credentials: 'omit'`, so the session cookie stays home, and a cross-site
  request cannot add the header, so there is no CSRF to guard. No session, no
  membership and no company's key opens the routes: they answer 401
  `not_the_operator` for all of them, as for a wrong key.
- **Off unless set.** Without `OPERATOR_KEY_SHA256` every route answers 404
  `operator_page_off`, which is what a self-hosted deployment, the test suite
  and CI get. A value that is not a digest (the key pasted where its digest
  goes, the likely mistake) also leaves the page off, says so in the 404, and
  logs a warning at startup; it does **not** stop the backend from starting,
  because a typo in an optional setting must not close the books on the next
  restart.
- **A leaked key can do little.** The routes read, set a feedback item's
  status and mark a failure resolved, and nothing else
  (`test_a_leaked_key_can_change_a_status_and_nothing_else`). Replying reaches
  the sender, publishing reaches the public list and deleting is for good, so
  all three stay in the shell (`python -m myntos.domain.feedback.triage`).
  A route that does any of them must not go under `/drift`.
- **Never for agents.** All four routes are in `_NOT_FOR_AGENTS`
  (tests/test_agent_surface.py) and the page in `_PAGES_WITHOUT_HELP`. An agent
  in the operator's terminal can use the triage command like the operator.
- The key stays in the tab's `sessionStorage` until the tab closes or «Lås» is
  pressed, never `localStorage`. Everything a stranger wrote is put on the page
  with `textContent`.

## The error log: the code, never the data

It runs for every company, whatever it chose under «Del data for å forbedre
Myntos»: what it keeps names no company, so it is operation and not sharing
([data-sharing.md](data-sharing.md)). Anything that would make a row point at
a company breaks that, as well as what follows.

Every record logged at ERROR or above under the `myntos` logger is also boiled
down (`recorder.boil_down`) and counted in memory. A lifespan task writes the
counts every ten seconds, and once more when the process stops. One row per
*kind* of failure, with a count and the first and last time, so a page that
polls a broken route every two seconds is one row and not forty thousand.

What the row keeps:

- the exception's qualified type, and up to three types it was raised from;
- the latest traceback's frames: file (relative to `site-packages/` or `src/`,
  never the machine's own paths), line, function, and the source line read
  from the file. `traceback.extract_tb` without locals, so no value from the
  time is in it;
- the logger and the log line's **template**, before its arguments were put in;
- the route as its **template**, `POST /api/v1/companies/{company_id}/…`, set
  by `unhandled_error_middleware` through `extra`, or none for background work.

What it does not keep, by design, and `test_the_table_has_no_column_that_could_name_anybody`
lists the columns so that adding one sends you here:

- no user, company, IP address or user agent, and no foreign key;
- **no exception message.** `KeyError: 'Ola Nordmann AS'`, a database error
  quoting the row it refused and a parser quoting the bilag are all messages.
  The type is kept, the words are not;
- **no log arguments.** This is why ruff's `G` rules are on: an f-string or a
  `%` inside the log call would put the values into the template, and the
  template is kept. `logger.exception("SAF-T-innlesing %s feilet", import_pk)`,
  never `logger.exception(f"SAF-T-innlesing {import_pk} feilet")`. A record
  whose message is not a string at all keeps only its type (`<dict>`);
- **not the path asked for**, which names the company. The template does not.

Two failures are the same kind when the logger, the template, the route, the
types and the (file, function) of Myntos's own frames match. Line numbers are
left out of that, so a deploy that moves code does not split a failure in two;
the stored frames are the latest occurrence's, so the line numbers shown are
the running code's.

The journal still has the whole of it: the message, the values, the request
line with the client's address. Each failure on the page carries the
`journalctl` command for the minutes around its last occurrence, in UTC. That
is the way from the page to the details, and it needs the machine.

A source line can still hold a literal someone wrote into the code. That is
code, not data: a test that raises `RuntimeError("Ola Nordmann AS")` sees the
name in its frame, and the tests raise from a variable for that reason.

### What is caught, and what is not

Only `myntos.*` loggers: a library that logs an error it then handled has not
failed, and a request it broke is caught anyway, by the middleware. Below
ERROR is not caught. The recorder's own complaint, when the database is down,
is a WARNING for that reason: an ERROR there would be recorded, fail to be
stored, and be recorded again. Meanwhile the counts are put back and tried
with the next write, at most 200 kinds held at once; past that the journal has
them.

A failure the operator marked resolved opens again by itself when it happens
again after that moment. Kinds not seen for `SERVER_ERROR_RETENTION_DAYS` (90)
are deleted by the daily purge in `main.py`, resolved or not.

## Tests

`tests/test_server_errors.py` holds what is kept and what is not, grouping,
reopening, storing after a failed write and the purge.
`tests/test_operator.py` holds the key: off until set, a session or a company's
key is not it, the digest is not it, and what the routes may change. The
recorder is process-wide, so `conftest.an_empty_error_recorder` empties it
around every test; without that, a failure one test logs on purpose would be
written into the next test's database by its lifespan.

## Gaps

- **Nothing tells the operator that something failed.** The page has to be
  opened. A mail per new kind of failure would be the obvious next step, and
  it may carry only the type and the route template, for the reason feedback's
  notice carries no text (feedback.md, rule 4).
- **The frontend's own failures are not here.** A page that throws in the
  browser leaves nothing on the server. Sending those would need the same
  boiling down in the browser, and a public route that takes them without
  becoming a way to fill the table.
- **One key, one person.** There is no second operator and no record of who
  looked, because there is nobody else to tell apart.
