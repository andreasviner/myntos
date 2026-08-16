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
- `rememberCompany()` / `rememberedCompany()` keep the selected company across
  pages in `localStorage`. Cleared on sign-out.
- `can(session, organizationId, permission)` is the only way a page should decide
  what to show. Never branch on `role` — the role → permission table lives in the
  backend (`domain/identity/access.py`) and the frontend reads the result.

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
- Only offer a choice when there is one. A `<select>` with a single option is
  worse than no select: it reads as a question the user is expected to have an
  opinion about. The company-setup form hides its organization picker below two
  options; do the same.

## Text

- Norwegian bokmål throughout, including code comments in `.astro` files (the
  `.ts` files under `lib/` are in English, as is the backend).
- Never "åpen kildekode" — Myntos is source-available, not open source.
- Label things by what they are to the user. "Sett opp regnskapet", not "opprett
  selskap": the company already exists, it is the books that are new.

## Organization vs. company

The distinction confuses people, so the UI has to carry it rather than assume it:

- **Organisasjon** = the Myntos account. Owns members, invitations and the
  subscription.
- **Selskap** = a set of books with an orgnr.

For a sole trader these are the same thing named twice, which is why signup
invents an organization name and the first company quietly replaces it — see
[ARCHITECTURE.md § Where the organization's name comes from](ARCHITECTURE.md#where-the-organizations-name-comes-from).
Anywhere both words appear on screen together, say which is which in a
`.field-hint`. `organization_name_is_placeholder` on the membership tells you
when the name is one Myntos made up, so `/organisasjon/` can ask for a real one
instead of presenting the invented one as settled.

## Theme

`lib/chrome.ts`. Light is the default and is the **absence** of
`data-theme`, so the CSS has one palette in `:root` and one override rather than
two fighting. The first application happens in an inline `<head>` script, before
first paint; `chrome.ts` only handles the toggle afterwards. The choice is stored
only when the visitor actively makes one — writing on every load would turn the
default into a decision they never made, and make a later change of default
invisible to everyone who had ever loaded the site.
