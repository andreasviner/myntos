/**
 * Which company the user is currently looking at.
 *
 * One Myntos account keeps books for as many companies as it likes: a sole
 * trader has one, a regnskapsfører has one per client. Every working page is
 * therefore about *a* company rather than about the account, and they all have
 * to agree on which one. Otherwise the invoice form and the cash-flow register
 * end up on different sets of books without saying so.
 *
 * The answer lives in `localStorage` rather than in the URL, because it is a
 * preference and not a location: a link to /faktura/ sent to a colleague should
 * open *their* current company, not the sender's. It is not a permission
 * either. Every request still names the company id, and the backend answers 404
 * for one the caller cannot reach, so a tampered value gains nothing.
 *
 * The record itself, and the wall that keeps one company's data off another
 * company's page, are in `lib/tenant.ts`. This module is the part that decides
 * *which* company that record should name: the remembered one, the first one,
 * or the one just picked from the menu.
 */

import { listApprovals, listCompanies, type Company, type Session } from './api';
import { isWaiting, waitingBadge } from './approvals';
import { closeMenus } from './chrome';
import { orgnummer } from './format';
import { loadSession, rememberChrome, setActiveCompany, signedInHint } from './session';
import { activeCompanyId } from './tenant';

let pending: Promise<Company[]> | null = null;

/**
 * The companies on the account, fetched once per page load.
 *
 * Shared for the same reason `loadSession` is: the header needs the current
 * company's name, and so does the page under it, and two requests for one
 * answer is one request too many. `force` is for after a company is created or
 * renamed, where the cached list is now behind what the user just did.
 */
export function loadCompanies(force = false): Promise<Company[]> {
  if (pending === null || force) pending = listCompanies();
  return pending;
}

/**
 * Ask for the companies now, without waiting to hear who is asking.
 *
 * /companies needs the session cookie, which the browser attaches by itself; it
 * does not need /auth/me to have answered first. Waiting for it anyway made
 * every signed-in page start with two full round trips in single file before a
 * single figure could be requested — and on this deployment one round trip is a
 * TLS hop to the API plus half a dozen queries to a database in Frankfurt.
 *
 * Gated on the signed-in hint rather than fired unconditionally, because a
 * visitor reading /priser/ should not collect a 401 in their console for a list
 * they were never going to see. A wrong hint costs one wasted request and
 * nothing else: `loadCompanies` hands the same promise to whoever asks next,
 * and its rejection is caught wherever it is finally awaited.
 */
export function prefetchCompanies(): void {
  if (!signedInHint()) return;
  // Swallowed here only so a rejection with no listener yet is not reported as
  // unhandled; the memoised promise is kept, and the real caller still sees it.
  void loadCompanies().catch(() => undefined);
}

/**
 * Put the current company in the page header, on every page.
 *
 * Gated on the session rather than on the request failing: an anonymous visitor
 * reading /priser/ should not cause a 401 in their console, and the marketing
 * pages have no header to fill.
 */
export function bootstrapHeaderCompany(): void {
  void loadSession()
    .then(async (session) => {
      if (session === null) return;
      const companies = await loadCompanies();
      // currentCompany fills the header name as a side effect; the answer is
      // needed here too, to mark the row the menu is already showing.
      const current = currentCompany(companies);
      // Now that the header is right, record what it says about the account.
      // The company half was already written by `currentCompany`, in one record
      // with its id; this is the person and the number of companies, which is
      // what the next page load paints and lays itself out from.
      rememberChrome(session, companies.length);
      mountChromeCompanyMenu(companies, current, session);
      mountApprovalsLink(current);
    })
    // Silent: the header still carries whatever was stored the last time this
    // browser looked, which is the company it is about to be about anyway, and
    // an error banner about a subtitle would be louder than the thing it
    // describes.
    .catch(() => undefined);
}

/**
 * «Godkjenning» in the menu, for a company whose keys are switched on.
 *
 * Without keys there is nothing that could wait for approval, so the link is
 * not shown at all. With them, one request for what waits, and the count only
 * when it is more than nothing: a badge that always says 0 is a badge nobody
 * reads (docs/frontend-conventions.md, a mark on every row is not a mark).
 * Silent on failure, like the rest of the header.
 */
function mountApprovalsLink(current: Company | undefined): void {
  const link = document.querySelector<HTMLElement>('[data-approvals]');
  const badge = document.querySelector<HTMLElement>('[data-approvals-count]');
  if (link === null || badge === null) return;
  link.hidden = current?.agent_access_enabled !== true;
  if (current === undefined || link.hidden) return;
  void listApprovals(current.id, 'pending')
    .then((found) => showWaitingCount(found.filter(isWaiting).length))
    .catch(() => undefined);
}

/**
 * Set the count beside «Godkjenning» to what waits now.
 *
 * Exported for /godkjenning/, which knows the moment a request is decided. The
 * header reads the count once, when the page loads, so approving the only
 * request left the (1) standing beside the menu item while the page under it
 * said nothing waited.
 */
export function showWaitingCount(count: number): void {
  const badge = document.querySelector<HTMLElement>('[data-approvals-count]');
  if (badge === null) return;
  badge.textContent = waitingBadge(count);
  badge.hidden = badge.textContent === '';
}

/** The permission /organisasjon/ requires before it will set up a company. */
const MANAGE = 'organization:manage';

/**
 * Show «Legg til selskap» to the people who could act on it.
 *
 * An account can hold several organizations with a different role in each, and
 * the link is offered as soon as *one* of them would take it: /organisasjon/
 * decides which organization the books land in, and gating on the current one
 * would leave the owner of the other with no way in. Read from the cached
 * session rather than passed in, so a caller that has just created a company
 * can redraw the menu without carrying the session around.
 */
function showAddLink(): void {
  const add = document.querySelector<HTMLElement>('[data-company-add]');
  if (add === null) return;
  void loadSession().then((session) => {
    add.hidden = !(session?.memberships ?? []).some((membership) =>
      membership.permissions.includes(MANAGE),
    );
  });
}

/**
 * The company switcher in the header.
 *
 * Every company on the account, on every page, because switching client is
 * something a regnskapsfører does between two glances at the same screen and
 * not an errand worth a detour through a settings page. The name in the header
 * was already the answer to "which books am I in"; this makes it the answer to
 * "and how do I get to the others".
 *
 * This is now the only company switcher on the site; the five per-page
 * `<select>`s that used to duplicate it are gone (see the note further down).
 *
 * Picking a company reloads rather than re-deriving the page: the invoice
 * draft, the party list and the catalogue on screen all belong to the company
 * that was showing, and half of one company's state surviving into another's is
 * the failure worth spending a page load to avoid. The choice is stored first,
 * so it is the new company that comes back.
 */
export function mountChromeCompanyMenu(
  companies: Company[],
  current: Company | undefined,
  session: Session | null = null,
): void {
  const list = document.querySelector<HTMLElement>('[data-company-list]');
  if (list === null) return;
  showAddLink();

  list.replaceChildren();
  if (companies.length === 0) {
    // Not an error and not empty: an account whose books have not been set up
    // yet, with the one thing left to do sitting right below this line.
    const empty = document.createElement('p');
    empty.className = 'nav-menu-tom';
    empty.textContent = 'Ingen selskaper ennå.';
    list.append(empty);
    return;
  }

  // Alphabetical, because the order the API happens to return is creation
  // order, and a list of a dozen clients is searched by eye.
  const sorted = [...companies].sort((a, b) => a.name.localeCompare(b.name, 'nb'));
  for (const company of sorted) {
    list.append(companyRow(company, company.id === current?.id, session, companies.length));
  }
}

/** One row: the name, the orgnr under it, and a tick when it is the one showing. */
function companyRow(
  company: Company,
  isCurrent: boolean,
  session: Session | null,
  companyCount: number,
): HTMLButtonElement {
  const row = document.createElement('button');
  row.type = 'button';
  row.className = 'nav-selskap-rad';
  // aria-current rather than a disabled button: the row is still worth reading
  // and worth reaching from the keyboard, it just has nowhere to take you.
  if (isCurrent) row.setAttribute('aria-current', 'true');

  const text = document.createElement('span');
  text.className = 'nav-selskap-tekst';

  const name = document.createElement('span');
  name.className = 'nav-selskap-navn';
  const label = document.createElement('span');
  label.textContent = company.name;
  name.append(label);
  // The same dot the header carries, meaning the same thing: nobody has proved
  // in Altinn that they may act for this company. Repeated per row because the
  // point of the list is comparing companies.
  if (!company.verified) {
    const dot = document.createElement('span');
    dot.className = 'nav-dot';
    dot.setAttribute('role', 'img');
    dot.setAttribute('aria-label', 'Ikke bekreftet');
    dot.title = 'Ingen har bekreftet i Altinn at de kan opptre for dette selskapet ennå.';
    name.append(dot);
  }

  const orgnr = document.createElement('span');
  orgnr.className = 'nav-selskap-orgnr';
  orgnr.textContent = orgnummer(company.orgnr);

  text.append(name, orgnr);
  row.append(text);

  if (isCurrent) {
    row.append(tick());
    // Nothing to switch to, so the only honest response is the one a menu
    // gives when its work is done.
    row.addEventListener('click', closeMenus);
    return row;
  }

  row.addEventListener('click', () => {
    // Written *before* the reload, and this is the whole fix for the switch
    // flicker: the page that comes back paints its header from what is stored,
    // and a record left naming the company being left behind is precisely what
    // showed the old company for a third of a second. One call, so the id and
    // the name cannot be left disagreeing.
    setActiveCompany(company);
    if (session !== null) rememberChrome(session, companyCount);
    window.location.reload();
  });
  return row;
}

function tick(): SVGSVGElement {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'nav-selskap-hake');
  svg.setAttribute('viewBox', '0 0 16 16');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', 'M3 8.5 6.2 12 13 4.5');
  path.setAttribute('fill', 'none');
  path.setAttribute('stroke', 'currentColor');
  path.setAttribute('stroke-width', '2');
  path.setAttribute('stroke-linecap', 'round');
  path.setAttribute('stroke-linejoin', 'round');
  svg.append(path);
  return svg;
}

/**
 * The company to open a page with: the remembered one, or the first.
 *
 * The choice is stamped back into storage, so the first page load after a
 * signup settles the question for every page that follows.
 */
export function currentCompany(companies: Company[]): Company | undefined {
  const remembered = activeCompanyId();
  // A remembered id can outlive what it points at: a company deleted, or a
  // membership withdrawn. Falling back to the first is what keeps the page
  // usable instead of empty.
  const current = companies.find((company) => company.id === remembered) ?? companies[0];
  // Every page settles on its company here, so this is the one place that knows
  // what the header should say, including on pages that never touch the header
  // themselves. Writing it back on every page load is what keeps the stored
  // record honest through the two cases that outrun it: a company renamed or
  // confirmed elsewhere, and a remembered one that no longer exists, where the
  // fallback to the first company is the real answer.
  if (current !== undefined) setActiveCompany(current);
  return current;
}

// Here lived fillCompanyPicker and mountCompanyPicker, a `<select>` of the
// account's companies that five pages each mounted for themselves.
//
// They are gone because the question they asked now has one place to be
// answered: the company menu in the header, on every page. Five controls for
// one fact is five controls that can disagree, and on a page like /ki/ — where
// the answer decides whether documents leave the building — disagreeing is not
// a cosmetic problem. It also read wrong: a settings page opening with "which
// company?" asks you to choose before it lets you act, when you had already
// chosen by being on the page.
//
// What the pickers did carefully is what the header menu still does: remember
// the choice, update the chrome hint *before* reloading so the page that comes
// back does not paint the company being left, and reload rather than re-derive,
// because /faktura/ holds a draft and a catalogue that both belong to the
// company that was showing. See `companyRow` above.
