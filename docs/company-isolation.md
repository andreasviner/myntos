# One company at a time: the wall, and why it is built twice

One Myntos account keeps books for as many companies as it likes. A sole trader
has one; a regnskapsfører has one per client. So **every working page is about
one company**, and the single worst thing this application can do is show one
company's data, or even one company's *name*, while claiming to be about
another.

That failure has two completely different causes, and they need two completely
different walls:

| | Where it would happen | What stops it | Tested by |
| --- | --- | --- | --- |
| **Access** | The API hands out books the caller may not read | `get_company_context` resolves `{company_id}` through the caller's **company** memberships; every service call filters on `company_id` as well | [`test_company_boundary.py`](../backend/tests/test_company_boundary.py), [`test_sibling_company_boundary.py`](../backend/tests/test_sibling_company_boundary.py), [`test_company_members_api.py`](../backend/tests/test_company_members_api.py) |
| **Consistency** | The API answers correctly and the *page* puts the answer under the wrong name | One stored record in [`lib/tenant.ts`](../frontend/src/lib/tenant.ts), asserted on every request in `lib/api.ts` | [`tenant.test.ts`](../frontend/src/lib/tenant.test.ts), [`api.test.ts`](../frontend/src/lib/api.test.ts) (`pnpm test`) |

Read both halves before changing anything that touches a company id.

---

## Access is per company, not per account

**`CompanyMembership` is the only thing that grants access to a set of books**:
one row per person per company, carrying the role held over *that* company. The
account (`OrganizationMembership`) says only whose companies these are and who
may add another.

It used to be account-wide, and that was the bug behind the change on
2026-09-03. One membership let a person into every set of books on the account,
so a regnskapsfører could not say "help me with this one client", and the
invitation form that handed over everything sat under the account where nothing
said so. Now:

- an invitation names a company; accepting it grants that company and nothing
  else, and no membership of the sender's account;
- `plan` (the subscription) is a column on `company`, because a set of books is
  the thing that has a size;
- `/companies` lists what you may open, with your `role` and `permissions` on
  each, so the frontend asks the company and never the account.

Creating a company grants **the creator alone**. Sharing is something you do,
not something that happens: see `create_company`.

## The access wall (backend)

Two dependencies in `api/deps.py`, and nothing else is allowed to resolve a
company:

```python
async def get_company_context(company_id: int, user: CurrentUser, db: DbSession) -> CompanyContext
```

- It returns the company **and** the caller's membership as one frozen value,
  because that is exactly the pair a missing check separates.
- A company that does not exist and a company belonging to somebody else's
  organization get the **same 404**. A 403 would confirm the id is real, turning
  the API into a way to enumerate other people's books.

Inside a route body the membership check has already passed, which is only half
the job: **two companies in the same account both pass it.** So every service
function takes `company: Company` and filters on `company.id`:

```python
document = await db.scalar(
    _document_query().where(
        SalesDocument.id == document_pk, SalesDocument.company_id == company.id
    )
)
```

There is exactly one `db.get(...)` by primary key in the whole backend, and it
is for the session's user. Adding a second for a company-scoped row is how this
wall gets a hole in it.

### The one query that reads a row by id on purpose

`ledger.service._author_names` resolves `{user id: full name}` for the people
who entered a page of vouchers, and it does **not** filter on the company. That
looks like the hole described above and is not one, for a reason worth stating
so nobody has to re-derive it while auditing:

- The ids it is handed come off `gl_transaction.created_by_user_id` on this
  company's own vouchers. They are not from a path, a payload or a query
  string, so there is no id a caller can choose.
- Every name it can therefore return belongs to somebody who was writing in
  these books. That includes a person since removed from them, which is the
  answer that is true: they did make the entry, and dropping the name with the
  access would leave the register unable to say who booked the row somebody is
  asking about.

What would turn it into a hole is passing it anything a caller supplied. If a
future endpoint wants "entries by person", the person is a `CompanyMembership`
on `context.company` first and a `user_id` second — resolve the membership, then
use its `user_id`. Never the other way round.

### Rules for a new endpoint

1. Company-scoped routes live under `/companies/{company_id}/...` and take
   `context: CurrentCompany`. Never read `company_id` out of the payload.
2. Every sub-resource id is looked up **with** the company: `WHERE id = :id AND
   company_id = :company`. Not found rather than forbidden.
3. Both boundary tests read the route list out of the generated OpenAPI schema,
   so a new route is covered the moment it exists. `test_sibling_company_boundary`
   **fails** rather than skips when it meets a path parameter it does not know
   how to build under a second company. Teach it; do not skip it.

---

## The consistency wall (frontend)

Which company you are looking at is a **preference, not a location**: it lives
in `localStorage`, not in the URL, so a link to `/faktura/` sent to a colleague
opens *their* current company. That choice is the most dangerous state on the
client, because nothing about it is validated by the server: the server answers
truthfully about whichever company was asked for.

### The record

`lib/tenant.ts` owns one `localStorage` key, `myntos.company`:

```json
{ "id": 12, "name": "DIALGA AS", "verified": true }
```

Three properties, and each one is load-bearing:

1. **One record.** The id every request is built from and the name painted in
   the header are the *same value*, written only from a whole `Company` object.
   There is no way to set the id without the name, so they cannot drift.
2. **Frozen per page load.** `activeCompany()` reads storage once and caches.
   Another tab switching company cannot move this page's answer out from under a
   request in flight. `BaseLayout` shows a notice (`[data-company-switched]`)
   when that happens, rather than following.
3. **Asserted on every request.** `request()` in `lib/api.ts` reads the company
   out of the URL and refuses, with `WrongCompanyError`, if it is not the active
   one, and refuses again if the *answer* carries a different `company_id`.
4. **The name never outlives the session.** When `/auth/me` says nobody is
   signed in, `forgetActiveCompanyName()` blanks the name and keeps the id: the
   next person to sign in on this browser must not see the previous account's
   company in the header, and an API hiccup (indistinguishable from an expired
   cookie here) must not drop a regnskapsfører back to their first client.

`myntos.chrome` is the other hint record and holds **account-level facts only**
(the signed-in person, how many companies exist). Nothing company-shaped may be
added to it. That rule exists because it was broken: the company's name lived
there while the id lived in `myntos.company`, written by different call sites,
and `/nytt-selskap/` set the id without the name. The next page load painted the
first company's name above the second company's figures.

### Rules for a page

1. Get the company from `currentCompany(companies)` (`lib/company.ts`). It
   settles the choice *and* writes it, so every page agrees.
2. Change the choice only through `setActiveCompany(company)`
   (`lib/session.ts`), which writes the record and repaints the chrome in one
   call. There is no id-only setter, and there must not be one.
3. Never cache anything company-specific under a key without the company id in
   the value. `myntos.bekreft-selskap` is the one such value and it stores the
   id it is about.
4. Switching company **reloads the page**. A draft invoice, a party list and a
   catalogue all belong to the company that was showing, and half of one
   company's state surviving into another's is worth a page load to avoid.
5. Paint the company name from `window.myntosCompany` (the record) and never
   from the account hint. Slots are `[data-session-company]` and
   `[data-session-unverified]`; a page below the header paints its own, because
   the inline script in `BaseLayout` runs before the page is parsed.

### What the user must always be able to see

Every working page names the company it is about, in its own heading and in the
header. `/index.astro` (the dashboard) is the strongest case: the heading is the
company, the orgnr sits under it, and «Bytt selskap» appears when the account
holds more than one. It used to greet the user by name and say nothing about
which books were on screen.

---

## The account is not a company

An **organisasjon** (the account) and a **selskap** (a set of books) are
different things, and until 2026-09-03 an account could quietly end up wearing
one of its companies' names: setting up the first set of books renamed a
placeholder account after that company.

That made the settings page show `Lindeman Technologies` on the account card
while the header said `DIALGA AS`: one company's name on a page about another,
which is indistinguishable from a leak. It was reported as one.

Both halves are fixed, and both are rules now:

1. **No code copies a company's name onto an account.** The adoption is gone
   (see the note where `adopt_name_if_placeholder` used to be, in
   `domain/identity/service.py`); migrations `c4d7e81f2a90` and `d1a7f2c6b483`
   gave back the names already taken, the second matching case-insensitively
   and ignoring a trailing organisasjonsform.
2. **No screen shows an account name, and the API does not send one.**
   `MembershipResponse` carries `organization_id`, `role` and `permissions` and
   nothing else. The Organisasjonen card, the account picker on
   `/organisasjon/`, the account picker on `/nytt-selskap/` and the account
   eyebrow on the dashboard are all gone. Signup still invents a name
   ("Kari Nordmann (privat)") because a nameless row is harder to talk about in
   a support conversation, and `GET /organizations` still answers with it, but
   no page reads that.

A provisional name that is obviously the account's own is worth more than a real
one that is true of something else, and a name nobody displays cannot be
mistaken for anything at all.

---

## Gaps

- **The frontend tests reach `src/lib/` and no further.** `pnpm test` runs
  Vitest over [`tenant.test.ts`](../frontend/src/lib/tenant.test.ts) and
  [`api.test.ts`](../frontend/src/lib/api.test.ts), which cover the record and
  both halves of the request wall (all six wall assertions were confirmed to
  fail with the guards removed). Nothing tests the `.astro` pages: that needs a
  browser, and a half-trusted browser harness is worse than none. So the *slots*
  a page paints into are still only asserted by reading the page.
- **The choice is not in the URL.** It cannot be shared or bookmarked, and two
  tabs on two companies is a notice rather than a supported state. Putting the
  company in the path (`/selskap/{id}/faktura/`) would make the whole
  consistency wall unnecessary, at the cost of touching every page and link.
