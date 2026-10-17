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

## A page's `<style>` never reaches what its script builds

Astro scopes a page's `<style>` by stamping `data-astro-cid-*` onto the
elements in the template and adding the same attribute to every selector. A
node from `document.createElement` never gets the stamp, so a rule like
`.dropzone-files li { … }` compiles to a selector no generated row matches, and
is dead without a warning. Each time it shipped, it was found from a screenshot:
the register's thumbnails rendered at full size, because their 3rem rule was on
`/kontantstrom/`, so ten screenshots made one entry taller than the screen; and
the upload list ran its text together as «(184 kB)Fjernikke lest». The third
time, on 2026-10-15, it was eight pages at once: `/konto/`'s session list read
«Chrome på WindowsDenne enhetenLogget inn …» on one line, and `/mva/`,
`/kontoplan/`, `/saft/`, `/arsavslutning/`, `/inngaende-balanse/` and the
question and party lists on `/kontantstrom/` had rules that matched nothing.
A rule whose last part is a bare tag or attribute (`.filings > li`,
`tr[data-open] td`, `[role='option']`) is the easy one to miss, because there
is no class name to grep for.

Two ways out, both in use. Anchor the rule on an element that *is* in the
markup and reach the generated children with `:global()` (`/faktura/`'s line
editor, the upload list on `/kontantstrom/`); or, for anything more than one
page builds, put it in `BaseLayout` (the register's rows, the detail line and
its files).

**A colour that does not exist is not a fallback.** `border: 1px solid
var(--line)` with no `--line` anywhere is not a thin grey line: a `var()` that
resolves to nothing makes the whole declaration invalid, the property falls
back to its initial value, and a later `border-style: dashed` then draws the
browser's own border, three pixels and black. That was the heavy black frame
around the model's question on `/kontantstrom/` until 2026-10-17, and nothing
in `astro check` or the console says so. The tokens are the ones `BaseLayout`
defines (`--border`, `--accent-line`, `--muted`, …); check a new one there
before using it.

**An id the layout already uses is the same bug wearing a different hat.**
`BaseLayout` gives `<main>` `id="innhold"`, because that is where the skip link
points, so a page that names its own wrapper `innhold` makes `el('#innhold')`
return the layout's `<main>` instead of the element the script meant. That
shipped too: `/invitasjon/` hid its loading message, never revealed the card
behind it, and on a dead token switched off the whole of `<main>` together with
the error it was trying to show. Both paths drew a blank page, with nothing in
the console and nothing in the markup to suggest why. An id has to be unique
against the layout, not only against the page.

## A line break in the markup is a space

`astro.config.mjs` sets `compressHTML: true`. Astro 7 changed the default to
`'jsx'`, React's whitespace rules: a run of whitespace that holds a line break,
at the edge of a text node, is deleted. Prose here wraps wherever the line gets
long, which is often right before a link, a `<strong>` or a `{/* … */}`, so
under the default the front page read «ser du underMva-melding.SAF-T-filen
Skatteetaten …» with every space beside a link gone, and the same on every
page with a link in a sentence. Nothing fails and `astro check` is clean; it
shows only on screen. `true` is Astro's lossless mode: whitespace is still
compressed, but a break between two words stays a space. Do not drop the line
to "use the default".

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

- **A page is the API's first client, not its owner**
  ([agents-and-api.md](agents-and-api.md) rule 1). It calls documented
  `/api/v1` routes and nothing else; there is no frontend-only endpoint. When a
  page needs something the API does not offer, the route comes first, with its
  OpenAPI description and its skill, and the page follows. Anything a page can
  do, an agent or a developer's own frontend must be able to do the same way,
  except what rule 5 keeps away from keys.
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
- A token in a link Myntos mails (address confirmation, password reset) is in
  the **fragment**, read with `tokenFromFragment(location.hash)` from
  `lib/links.ts` and posted in a body. A fragment never reaches a server, so
  the token is in no access log and no Referer. Take it off the address bar
  with `forgetFragment()` once it is spent, not before: a reload halfway
  through typing a new password must not cost the person a fresh mail. The
  invitation link predates this and still uses `?token=`.
- Anything that only works when the deployment sends mail asks
  `getProviders().email_delivery` first and stays `hidden` otherwise.
- A federated flow says what it is for: `providerStartUrl(code, redirectTo,
  intent)`. `/registrer/` passes `signup` and `/konto/` passes `link`; leaving
  it out means `login`, which for BankID refuses an identity linked to nothing
  instead of asking for an address. Every flow that fails comes back to the page
  it started from with `?error=<code>`, and `CALLBACK_ERRORS` in
  `lib/providers.ts` owns the wording for all of them. The held BankID signup
  arrives at `/fullfor-registrering/` with its token in the fragment, like a
  mailed link.
- **BankID is never a `link`.** An account that signs in with BankID signs in
  with nothing else (BankID's terms; see
  [integrations/bankid.md](integrations/bankid.md)), so both directions on
  `/konto/` are a switch that takes the other way in away, and each is a step
  with the warning, a ticked box and a button, not a link. To BankID:
  `startBankidLink(password, redirectTo)` POSTs, and the page sends the browser
  to the URL it answers with; a GET to `/start` is refused. From BankID:
  `requestSwitchToEmail()` mails a link to `/bytt-til-epost/#token=…`, read like
  any mailed link. `isBankidAccount` and `describeSignIn` in `lib/signin.ts`
  decide which of the two a page offers, and nothing offers «Koble til Google»
  to a BankID account. Anything next to a BankID button that changes how the
  account will sign in says so before the press (`renderProviderButtons(…,
  hints)`), not after.
- `request()` refuses, with `WrongCompanyError`, any path under
  `/companies/{id}/` whose id is not the company the page is about, and any
  answer carrying a different `company_id`. It subclasses `ApiError`, so the
  handling rule above already shows it. If you hit one, the bug is upstream: a
  stale `Company` held across a switch, not the guard.
- **A file shown inside a page is fetched, never framed from the API.** Every
  API answer carries `X-Frame-Options: DENY`, and the file routes send
  `Content-Disposition: attachment`, so an `<iframe src>` pointing at the API
  shows nothing, and a link to it downloads. `/faktura/` showed the draft's
  PDF only as a download, and a review step whose document has to be
  downloaded to be read gets passed without anyone reading it. Fetch the file
  through a `lib/api.ts` function that runs the company check first
  (`fetchFakturaPdf`, `fetchThumbnail`) and frame the object URL. That function
  returns null unless the answer has the type it expects: an object URL takes
  the page's origin, so an HTML page framed through one would run as
  myntos.no. Ask `navigator.pdfViewerEnabled` before offering a PDF frame,
  because Chrome on Android leaves it blank, and keep the download link beside
  it either way.

## Forms

- `novalidate` on the form; the server has the last word, and its message is the
  one shown. HTML constraints are advisory only (`minLength` is set from
  `/auth/providers`, so a hint can never promise a rule the backend does not
  enforce).
- Disable the submit button while a request is in flight and re-enable it in
  `.catch()` / `.finally()`.
- When a press is more than one request, the button says which one it is
  waiting on («Fører betalingen …», «Laster opp dokumentene … 4 av 10»). A
  disabled button that says nothing for eight seconds reads as a page that
  froze, and that was the report: posting a ten-file bilag sent the files one
  at a time, each a dozen round trips on the server. They go four at a time now
  (`settleLimited` in `lib/parallel.ts`), under the database pool of five, so
  the archive order is the order they finished in, not the order they were
  picked.
- Once the thing the user asked for has happened, the button is theirs again.
  Reloading a list afterwards is the page's business, and a failure there must
  not be reported as a failure of the post: «Vi fikk ikke ført betalingen»
  about a payment that was posted is an invitation to post it twice.
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
  opinion about. The company-setup form (`nytt-selskap.astro`) has no account
  picker at all: a person holds exactly one account that may add companies
  (signup makes one, and nobody joins another), so there is nothing to ask.

## Work the server finishes after answering

`/tidligere-program/` is the first page whose work outlives its request: the
SAF-T files go up once and are read and posted on the server
(ARCHITECTURE.md § Work done after the answer). The shape, for the next one:

- **Show the upload, then let go.** The upload is the only part that needs the
  tab, and it gets a bar (`uploadSaftFiles` in `lib/api.ts` uses
  XMLHttpRequest, because fetch cannot report upload progress) and says to keep
  the page open until it is done. After that the page says, in so many words,
  that it can be closed.
- **Poll the record, never hold it.** The page asks for the import every 1.5 s
  while it is `reading` or `posting`, and a lost answer is not an error: the
  next poll asks again. On load it finds the import to show from the list
  (`currentImport` in `lib/saftimport.ts`), so a person coming back lands on
  the same state they left.
- **Long lists fold.** What an import adds was a bullet list on the page, and
  62 parties made the review four screens long. Counts first, as tiles; the
  list behind each in a `<details>` with a fixed height and a filter once it is
  long; notes folded under «… merknader å lese», errors never folded.

## A window is a `<dialog>`, and only for something you came to do

«Inviter en KI» on `/organisasjon/` is the first popup on the site, and the
shape it set is the one to copy: a `<dialog>` in the page's markup, opened with
`showModal()` (which keeps focus inside, makes the rest of the page inert, and
closes on Escape), with its states as sibling blocks shown one at a time with
`show()`. Anything secret it showed (the connection key) is emptied on the
dialog's `close` event, not on a button, because Escape and the backdrop close
it too.

It is a window because the owner asked for the consent text to stop standing on
the page for every visitor: the text a person must read before keys are switched
on is read once, in the window, at the moment they asked for a key, and the
page shows the list of keys only once there are any. That is the test for
another popup: something a person came to do, with something they must read
first. Information that is always true belongs on the page; an irreversible
action belongs in a review step (below), not in a window.

## Help articles name this page's labels

Every page is explained to AI assistants by the help articles in
`docs/agents/help/` whose frontmatter names it (`pages: /faktura/`), quoting
its labels exactly (docs/agents/README.md). **Renaming a button, a heading or
a menu item, or adding or removing a step, means updating those articles in
the same change.** `grep -rl "«Old label»" docs/agents/` finds them. A new page
needs at least one article, or `test_every_page_has_a_help_article` fails.

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

`/inngaende-balanse/` and `/arsavslutning/` follow the same shape without a
draft: the form asks the backend for a review (`.../kontroll`, the closing
plan), and the page shows the lines that will be posted, the sums and the
findings exactly as returned. What is posted is the reviewed lines, not the form
as it looks after «Tilbake». Closing a year adds a checkbox above the button,
because it locks the year for every colleague; reopening asks for a written
reason and is the one button there that the backend refuses without one.
Amounts typed into these forms go through `lib/amount.ts`, which turns
«1 234,56» into `"1234.56"` without ever making a number of it.

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

## A register row is one line

How many files a bilag has says nothing about how much the entry matters, so
it must not decide how much room the entry takes. The register on
`/kontantstrom/` shows **one badge per entry**: a single file is its badge
(«PDF 37 kB») and links straight to it; several are «10 FILER», which opens the
detail line where every file is listed with its thumbnail. Thumbnails are
fetched only when that line is opened (an `IntersectionObserver` never sees a
hidden row), so loading the register costs no pictures at all. It used to show
every file with its thumbnail in the row, and one AliExpress order of ten
screenshots and invoices filled the screen.

## The company's own details live in one place

`/organisasjon/#selskap` is the only editor for the company's bank accounts,
`vat_registered` and the date it took effect (`vat_registered_from`),
`in_foretaksregisteret` and the address. It needs
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

The MVA settings are the one exception, and a narrow one. How long a VAT period
is and which kind of melding the company files are edited on `/mva/`, beside the
periods they change, with the same `organization:manage`. They mean nothing
anywhere else, and a select on `/organisasjon/` would change the page somebody
is not looking at.

The kontoplan is the other place the books are *set up* rather than written in,
and it has a permission of its own, `ledger:chart`, held by owners and admins:
a member who posts payments does not thereby regroup the accounts every other
member's entries land on. `/kontoplan/` asks `canInCompany(company,
'ledger:chart')` each time it draws, and the cash-flow form loads the
**company's** catalogue (`getCompanyCatalogue`), never the shared one, because
the VAT code a category proposes follows that company's registration and a
deactivated account's category is not offered.

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

## What search engines and AI assistants see

A page is indexed only when `lib/indexing.ts` names it. Everything else gets
`<meta name="robots" content="noindex, follow">` from `BaseLayout` and is left
out of the sitemap, because both read that one list. Until 2026-10-16 the
sitemap kept pages out with a hand list in `astro.config.mjs` instead, written
when there were five application pages. Every later one went in by default, and
the sitemap offered Google eighteen pages that send a signed-out visitor (and
Google's renderer, which runs the script) straight to `/logg-inn/`. The list now
runs the other way, so a new page is private until somebody decides it is
public, and `indexing.test.ts` fails if a page that calls `redirectToLogin` is
ever listed.

Nothing is refused in `robots.txt`. Google never reads the noindex of a page it
may not fetch, and indexes the bare URL from any link to it instead; that is
what the old `Disallow` lines did for `/kontantstrom/` and three others.

The rest of what a crawler reads lives in `BaseLayout`'s head:

- **Structured data** is one schema.org `@graph`: the organisation, the site,
  the application (with `offers` at 0 NOK and what is not built yet in its
  description, because answer engines quote it) and a `WebPage` node per page.
  A page adds its own nodes with the `schema` prop, built from the same data it
  renders: the questions on `/priser/` as `FAQPage`, each moving guide as
  `BreadcrumbList` and `HowTo` from `data/flytting.ts`. Never write the text a
  second time for the markup; Google ignores structured data that says
  something the page does not, and an assistant repeats it anyway.
- **Every `name` on the home page is «Myntos»**, for the Google OAuth brand
  review described at `APP_NAME`. That is why the `WebPage` node has none.
- **`lang`** is `nb` except on `/about/`, which passes `lang="en"`. The header,
  footer and skip link are marked `lang="nb"` on such a page, since they stay
  bokmål.
- **The share image** is `public/og-image.png`, 1200×630, drawn from the logo
  by `pnpm og-image`. Regenerate it when the mark or the tagline changes.

`/404.html` is served for any address that matches no file, with status 404,
through `not_found_handling` in `wrangler.jsonc`. A page kept out of the index
carries no canonical link, since the 404 page is served at whatever address
missed and would name the wrong one.

For assistants that read rather than crawl, `docs/agents/llms.txt` opens with
«About Myntos»: what it is, the price, what is not built yet, and where the data
is, each line linked to the public page that says it. Change it with the page:
see [agents/README.md](agents/README.md).
