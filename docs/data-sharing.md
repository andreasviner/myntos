# Sharing with Myntos, chosen per company

A company can let Myntos use some of what is in its books to make Myntos
better, at four levels from nothing to the bilag as they are. Nothing is the
default for every company. Built 2026-10-28 (migration `5e8b3c1f7a92`),
because the owner wanted a way for willing companies to help, with the top
level not anonymised and marked in red.

**Nothing collects anything yet.** The level is stored and logged so that
whatever collects later starts from a choice a company has already made. Until
a collector exists, every promise below is a rule for whoever writes it.

Read this before touching `domain/data_sharing/`, `Company.data_sharing`,
`PUT /api/v1/companies/{id}/datadeling`, the «Del data for å forbedre Myntos»
section of `/organisasjon/`, `/datadeling/`, `frontend/src/lib/datasharing.ts`,
or the personvernerklæring's section on it, and before writing anything that
collects.

## The levels

Lowest first; each shares everything the one before it does. What each covers
is written once, in `lib/datasharing.ts`, which both the choice and
`/datadeling/` are drawn from, so the page that asks and the page that explains
cannot disagree. The backend's `DataSharingLevel` (in
`domain/data_sharing/models.py`) is declared in the same order, and `allows()`
compares positions.

| Value | Label | Shares |
| --- | --- | --- |
| `none` | «Ingenting» | Nothing. The default |
| `usage` | «Bruksstatistikk» | Pages and buttons used, timings, failures **in the browser**. Nothing from the books. Not linked to the person or the company |
| `anonymised` | «Anonymiserte rettelser» | Usage, plus the category, account and VAT code Myntos proposed for a bilag against what was chosen, the kind of bilag, and the public næringskode. No names, orgnr, account numbers, amounts, text or documents. Not linked to the person or the company |
| `everything` | «Alt, også bilag» (red) | The bilag files, the text read off them, Myntos's proposal, and the entry as booked, with every name, address, amount and account number on them. **Not anonymised**, and linked to the company |

**The server's error log is not a level.** `server_error`
([operator.md](operator.md)) records what failed on the server for every
company, at every level, `none` included, and that is said on `/datadeling/`
and in the personvernerklæring. It can, because it keeps the code and never
the data: no company, no person, no value. The failures `usage` covers are the
ones only the browser sees (a script that threw, a page that never drew),
which is why the level's text says «i nettleseren». So a collector for
`usage` does not repeat the server's failures, and `server_error` never gains
a column because a company chose to share: the moment it names a company, it
is no longer something every company can be given without asking.

## The promises

Each is said to the person on `/datadeling/` or in the personvernerklæring, so
breaking one breaks what a company agreed to.

1. **Nothing until a person says so.** `none` for every company, including
   every company that existed before the choice did: the migration backfills
   `none` and writes no log row, because a row would claim a decision nobody
   made. `ki_enabled` is not read as a yes. It lets Myntos send a bilag to a
   model to answer the company's own question, and says nothing about Myntos
   keeping it.
2. **A level may only narrow without asking again.** Collecting anything a
   level's text on `/datadeling/` does not name means asking every company on
   that level again, and until it answers its old choice stands. Narrowing a
   level's text is always allowed. Widening it in `lib/datasharing.ts` is the
   one edit to that file that needs this rule read first.
3. **From the day it is chosen.** Nothing booked before the choice is shared
   after the fact. `data_sharing_choice.chosen_at` is what a collector compares
   against; that is why the log exists rather than just the column.
4. **Lowering applies at once.** A collector calls
   `domain.data_sharing.service.allows(company, level)` every time it takes
   something, never once at startup or per job. What was kept under
   `everything` is deleted when the company goes below it, and when the
   company is deleted (closing sets the level to `none`, below).
5. **Anonymous means not linked.** What `usage` and `anonymised` collect is
   stored without a `company_id`, a `user_id`, an orgnr or anything else that
   leads back, like `feedback` and `server_error`. The price, said on the page:
   it cannot be found again to delete it when the company lowers its level. If
   a collector needs to link it, it is not anonymous, and it belongs under
   `everything`.
6. **The top level stays in the EEA, is read only by the people who build
   Myntos, and trains no model.** Its use is testing that Myntos reads bilag
   correctly: a real bilag beside the entry a person made of it is exactly the
   fixture `regnaskaps_tests/` is, at scale. Training a model on it would
   break both this promise and the one on `/ki/` («trener ingen modell»), and
   would need asking again (rule 2).
7. **Nothing is sold or passed on**, at any level.

## Who chooses

`organization:manage`, owners and admins, the same as the KI switch: it is a
statement about the books, made by the people responsible for them, and it has
to hold whoever is signed in. Everyone else in the company sees the level and
cannot change it. Not owner-only, because an admin already decides whether
bilag reach a model at all; an owner who wants it narrower controls who is
admin.

**Never a key.** `PUT …/datadeling` is in `_NOT_FOR_AGENTS`: what leaves the
books for Myntos's own use is the company's decision about its tools, not one
its tools may make. A key reads the level on the company (`data_sharing`) like
every other field, so an agent can answer what the company shares.

The choice is a dropdown near the bottom of `/organisasjon/`, just above
«Slett selskapet»: it is made once and seldom changed, and four rows of
explanation took room from what people come to that page for. The sentence
under the list is the chosen level's `summary`, so nobody has to open the list
to see what is chosen. Picking `everything` turns the list red and shows a
small «Takk :)» for a moment; that is thanks for the offer, not a
confirmation. The page asks for a tick («Jeg forstår at bilagene deles slik de
er, og vil dele dem.») before «Lagre» saves `everything`, and only on the way
up to it.

## The log, deletion and erasure

- `data_sharing_choice` is a row per change (`company_id`, `level`,
  `chosen_at`, `chosen_by_user_id`), never edited. Saving the level a company
  already has writes nothing.
- **Deleting the company** (`closure.close_company`) sets the level to `none`
  and logs it with who deleted the company, like it switches agent access off.
  Reopening does not restore it: somebody reads what it means and chooses
  again. A purge or an outright deletion takes the log with the company
  (`ON DELETE CASCADE`).
- **Deleting the person** keeps a tombstone when they chose a level above
  `none` (`erasure._is_named_by_records`): Myntos keeps what it was given on
  that choice and must be able to say who made it. The company's level stands;
  it was the company's choice. Choosing `none` names nobody worth keeping.
  See [gdpr.md](gdpr.md).

## What the law makes of it

At `none` Myntos is the company's databehandler and nothing more
(personvernerklæringen § 3d). From `usage` up, Myntos uses something for a
purpose of its own, and for that use it is the controller
(behandlingsansvarlig). For `usage` and `anonymised` that is a small matter
while promise 5 holds: anonymous data is not personal data. For `everything`
it is not small: the bilag carry personal data about the company's customers,
suppliers and employees, who were never asked. The basis is Myntos's legitimate
interest in software that reads their bilag correctly, on the company's
choice to share; the page tells the company to choose it only if it can stand
behind sharing them. Before the first collector for `everything` is built,
that balance deserves a written assessment, and the personvernerklæring's
section on it is the place the outcome is promised.
