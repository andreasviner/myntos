# Frontend conventions

Astro, prerendered, no framework, no client-side router. Every page is a `.astro`
file with a `<script>` block that runs on load; shared logic lives in
`frontend/src/lib/`. This file is the short version of what those pages assume
about each other — read it before adding a page, and update it when one of these
rules changes.

For what the frontend *is* (rendering model, auth hint, page table), see
[ARCHITECTURE.md § Frontend](ARCHITECTURE.md#frontend).

---

## Visibility is the `hidden` attribute

Every script on the site shows and hides things by setting `element.hidden`
(usually through `show(node, visible)` in `lib/dom.ts`). Nothing toggles a class
and nothing writes `style.display`.

**This only works because `BaseLayout.astro` declares:**

```css
[hidden] {
  display: none !important;
}
```

The browser's own rule for `hidden` lives in the **user-agent stylesheet**, which
every author rule outranks regardless of specificity. So a single innocuous line
like `.field { display: block }` is enough to make `<label class="field" hidden>`
fully visible — with no error, no warning, and nothing in the markup to suggest
it. That bug shipped once: it left the "which organization?" picker on the
company-setup form visible for users who had exactly one organization, i.e. the
question the form had explicitly decided not to ask.

If you ever remove that rule, every `hidden` on the site becomes conditional on
what classes the element happens to carry.

**An id the layout already uses is the same bug wearing a different hat.**
`BaseLayout` gives `<main>` `id="innhold"`, because that is where the skip link
points, so a page that names its own wrapper `innhold` makes `el('#innhold')`
return the layout's `<main>` instead of the element the script meant. That
shipped too: `/invitasjon/` hid its loading message, never revealed the card
behind it, and on a dead token switched off the whole of `<main>` together with
the error it was trying to show. Both paths drew a blank page, with nothing in
the console and nothing in the markup to suggest why. An id has to be unique
against the layout, not only against the page.

## Session and chrome

- `lib/session.ts` owns the session. `loadSession()` fetches `/auth/me` **once
  per page load** and shares the promise, so a page with three widgets makes one
  request. Pass `force = true` only after something changed the session itself.
- `bootstrapChrome()` runs once from `BaseLayout` and sets `data-auth` on
  `<html>` to `inn` / `ut`. Markup marked `data-auth-only="inn"` or `"ut"` is
  shown or hidden by CSS from that attribute — this is separate from `hidden`
  and takes precedence over it.
- The `localStorage` hint (`myntos.signed-in`) exists only to avoid a flash of
  the wrong page before `/auth/me` answers. It is **not a credential** and grants
  nothing; the API's answer overrules it either way.
- The company being worked on is **one record** in `localStorage`
  (`myntos.company`), owned by `lib/tenant.ts`, and `setActiveCompany(company)`
  is the only way to change it. Read
  [company-isolation.md](company-isolation.md#the-consistency-wall-frontend)
  before touching anything company-scoped: the id, the name and the
  confirmation mark are deliberately one value, `lib/api.ts` refuses any request
  that names a different company, and `myntos.chrome` is for account-level facts
  only. Cleared on sign-out.
- `canInCompany(company, permission)` is the only way a page should decide what
  to show about a set of books. Never branch on `role`: the role to permission
  table lives in the backend (`domain/identity/access.py`) and the frontend
  reads the result. `can(session, organizationId, permission)` is the
  account-level sibling, and an account now carries one question worth asking
  (may this person set up a company at all), so anything company-shaped that
  reaches for it is asking the wrong thing.
- **Ask the permission where you draw it. Never keep the answer in a variable.**
  `/organisasjon/` held `mayAdmin` as a boolean assigned just after the first
  draw, which is one step too late: every ordinary visit opened the page
  read-only, so an owner got no «Legg til konto» button, no pencils, and an
  empty account list telling them they could not add an account. It righted
  itself only when a rename or a register fetch happened to draw the card a
  second time, which is why it read as a permissions bug rather than an ordering
  one. The company on screen also moves, on a switch, a rename or a
  confirmation, and a permission copied out before that move is a permission for
  the previous company. `/organisasjon/` asks through two small functions now
  (`mayAdmin()`, `mayManage()`), for the same reason `bokene()` on that page is
  a function rather than a captured value.

## API access

- `lib/api.ts` is the single place that knows URLs. Pages import named functions,
  never build a path.
- Every request is `credentials: 'include'` against another origin; writes carry
  the `Origin` header the backend's CSRF check requires.
- `ApiError` carries the backend's own message. Show `error.message` when it is
  an `ApiError` and a written fallback otherwise — never a raw `String(error)`.
- `error.isUnauthenticated` (401) is the ordinary answer for a visitor, not a
  failure. `redirectToLogin()` preserves where they were going in `?neste=`,
  which is validated as site-relative so it cannot become an open redirect.
- `pnpm test` runs Vitest over `src/lib/*.test.ts`. It covers the company wall
  and is where a new invariant in `lib/` belongs; the `.astro` pages have no
  harness, so logic worth asserting is worth moving into `lib/`.
- `request()` refuses, with `WrongCompanyError`, any path under
  `/companies/{id}/` whose id is not the company the page is about, and any
  answer carrying a different `company_id`. It subclasses `ApiError`, so the
  handling rule above already shows it. If you hit one, the bug is upstream: a
  stale `Company` held across a switch, not the guard.

## Forms

- `novalidate` on the form; the server has the last word, and its message is the
  one shown. HTML constraints are advisory only (`minLength` is set from
  `/auth/providers`, so a hint can never promise a rule the backend does not
  enforce).
- Disable the submit button while a request is in flight and re-enable it in
  `.catch()` / `.finally()`.
- One `.status` element per form, written through `setStatus(node, message,
  tone)`. It uses `textContent`, never `innerHTML` — some of these messages
  originate from the API and could one day contain something a user typed.
- An optional field is still a question. Anything the form can work without,
  and that the user may not know yet, is one more thing standing between them
  and the button: the invitation form dropped its "Navn (valgfritt)" for that
  reason, and keeps the name only when a board row already supplied one. Prefer
  filling such a value in from where it is known over asking for it.
- Only offer a choice when there is one. A `<select>` with a single option is
  worse than no select: it reads as a question the user is expected to have an
  opinion about. The company-setup form hides its organization picker below two
  options; do the same.

## Irreversible actions get a step, not a dialog

`window.confirm()` is fine for something a page can undo — deleting a draft,
removing a member. It is not fine for issuing a salgsdokument, which numbers the
document, posts it and can only be corrected with a kreditnota. `/faktura/`
shows a **review card** instead: the fields the buyer will receive, the account
it is paid to, and the compliance findings the backend returned, with the
confirm button disabled while anything blocks.

Two things follow from that:

- The findings shown are the backend's own (`violations` on the response, or
  `GET .../kontroll`). A second opinion computed in the browser could only ever
  disagree with the one that decides.
- Saving is not a question. A draft has to exist before the backend can check
  anything, so submitting the form saves it and opens the review — there is no
  "save draft" button beside "issue" asking the user to pick.

## A mark that is on every row is not a mark

The register says who entered a voucher, and it says it **only when the
register has more than one author** (`lib/authors.ts` owns that rule and is
tested on it). On a one-person set of books «Ført av Kari Nordmann» is on every
single row, answers nothing, and spends attention on the page a sole trader
uses most. The moment a colleague's name appears it is the fastest thing on the
row to read.

Two details that are the whole reason the rule is in `lib/` rather than in the
page:

- **"Not recorded" counts as an author.** A register where some rows are
  attributed and some are not has genuinely been written by more than one hand,
  and hiding the mark there would hide the one thing the reader needs: which
  rows nobody can be asked about. Such a row reads «ukjent» — never blank, and
  never a name.
- **Authors are counted by id, not by name.** Two people called Ola Nordmann on
  the same books are two authors, and telling them apart is the point.

The same shape applies to anything else the interface may one day attribute:
decide it once over the whole listing, not per row, because the mark is a
statement about the set of books and not about the entry.

## The company's own details live in one place

`/organisasjon/#selskap` is the only editor for the company's bank accounts,
`vat_registered`, `in_foretaksregisteret` and the address. It needs
`organization:manage` — whether the business is VAT-registered is a statement
about the entity that every future invoice repeats, not a bookkeeping entry, and
neither is where a customer is told to send money.

The accounts are the one **list** on a page of facts, and they are a list
because a company has as many as its bank gave it. The rules the page follows:

- The marked one («Hovedkonto») is where an invoice is paid when it names none
  itself, and it is marked rather than merely first — the order is a hint, the
  word is the answer.
- There is no way to un-mark it. Moving it is promoting another account, which
  is the only shape that cannot leave a company with accounts and no default;
  the backend refuses the other direction with `primary_bank_account_required`.
- An empty list is a *line with colour*, not a missing field. It is what stops
  `/faktura/` issuing anything, so it has to read as something to do.

Anything that refuses work because one of them is missing must **link there**,
not merely mention it. `setStatus()` writes `textContent`, so a notice that
needs a link is markup in the page with `show()` toggling it, not a status
string. This is load-bearing: an invoice with no kontonummer cannot be issued at
all, and for a while the invoice page said so while offering nowhere to fix it.

## Don't warn where you can decide

A banner saying "this company is not VAT-registered" is not a substitute for
selecting «Ikke mva-registrert» on the line and offering nothing else — which is
what the line editor does, mirroring the restriction the backend puts on
model-proposed lines. Reserve the banner for saying what has *already* been
done. The same rule is why the company-setup form sends
`vat_registered` / `in_foretaksregisteret` / `organisation_form` straight from
Enhetsregisteret instead of leaving them false and warning about it afterwards.

## Text

- Norwegian bokmål throughout, including code comments in `.astro` files (the
  `.ts` files under `lib/` are in English, as is the backend).
- Never "åpen kildekode" — Myntos is source-available, not open source.
- Label things by what they are to the user. "Sett opp regnskapet", not "opprett
  selskap": the company already exists, it is the books that are new.

## Every working page names its company

A page about a set of books says which set, in its own heading, and not only in
the shared header (which collapses behind the menu button on a narrow screen).
`/index.astro` is the pattern: the greeting is a small line above, the company
name is the `<h1>`, the orgnr sits under it, and «Bytt selskap» appears only
when the account holds more than one. The name goes in a
`[data-session-company]` slot so it is painted from the stored record before
first paint and corrected from `/companies` after, never assembled from a
different source than the figures on the page.

## The account is not on screen at all

**Selskap** (a set of books with an orgnr) is the only one of the two a user
ever sees. Members, invitations, the subscription and every figure belong to it.

**Organisasjon** is the account somebody signed up for. It owns the companies
and answers one question the interface still needs: whether this person may add
another one (`can(session, orgId, 'organization:manage')`, used by
`/nytt-selskap/` and the «Legg til selskap» link). It has a name in the
database; **no page may print it**, and there is no picker for it. An account is
routinely called after the first company set up under it, so such a name shown
near a different company is indistinguishable from a leak. It was reported as
one, which is why the Organisasjonen card, the account picker and the account
name on `/organisasjon/` are all gone. See
[company-isolation.md](company-isolation.md#the-account-is-not-a-company).

So on any page about a company:

- ask `canInCompany(company, permission)`, not `can(session, orgId, …)`. The
  role comes back on the company (`Company.role`, `Company.permissions`) because
  access is a row per person per company: the same person can own their own
  books and merely read a client's.
- members and invitations are `listMembers(companyId)` /
  `listInvitations(companyId)`. There is no account-level member list any more.
- say which books an action is about. «Medlemmer i DIALGA AS», not «Medlemmer»:
  the whole reason access moved is that "invite a colleague" used to hand over
  every client at once without saying so.

## Theme

`lib/chrome.ts`. Light is the default and is the **absence** of
`data-theme`, so the CSS has one palette in `:root` and one override rather than
two fighting. The first application happens in an inline `<head>` script, before
first paint; `chrome.ts` only handles the toggle afterwards. The choice is stored
only when the visitor actively makes one — writing on every load would turn the
default into a decision they never made, and make a later change of default
invisible to everyone who had ever loaded the site.
