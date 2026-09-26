/**
 * Client-side session state and the page chrome that depends on it.
 *
 * The site is prerendered, so the server that hands out the HTML has no idea
 * who is asking. The signed-out page is therefore what ships in the file, which is good
 * for search engines and correct when JavaScript never runs. The signed-in
 * view is revealed here.
 *
 * To avoid a flash of the wrong page for a returning user, a **hint** is kept in
 * `localStorage` and read by an inline script in the document head, before the
 * first paint. The hint is not a credential and grants nothing: the real answer
 * comes from `/auth/me` a moment later and overrules it either way. The session
 * itself lives in an httpOnly cookie no script can touch.
 */

import { ApiError, getSession, logout, type Company, type Session } from './api';
import {
  activeCompany,
  activeCompanyMovedElsewhere,
  clearActiveCompany,
  forgetActiveCompanyName,
  setActiveCompany as writeActiveCompany,
} from './tenant';

const HINT_KEY = 'myntos.signed-in';
const CHROME_KEY = 'myntos.chrome';

/** Fired on `document` once the session is known. */
export const SESSION_EVENT = 'myntos:session';

export type AuthState = 'inn' | 'ut';

function storage(): Storage | null {
  // Private-mode Safari and locked-down browsers throw on access, not on use.
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function rememberAuth(state: AuthState): void {
  const store = storage();
  if (store === null) return;
  if (state === 'inn') store.setItem(HINT_KEY, '1');
  else store.removeItem(HINT_KEY);
}

export function setAuthState(state: AuthState): void {
  document.documentElement.dataset.auth = state;
}

/**
 * Whether this browser was signed in last time it looked.
 *
 * The same hint the inline script in the head reads, exposed so a page can
 * start a request it is *probably* allowed to make instead of waiting for
 * /auth/me to confirm it. Never a permission: a wrong answer here costs one 401
 * and no more, because the API decides.
 */
export function signedInHint(): boolean {
  return storage()?.getItem(HINT_KEY) === '1';
}

let pending: Promise<Session | null> | null = null;

async function fetchSession(): Promise<Session | null> {
  try {
    return await getSession();
  } catch (error) {
    // 401 is the ordinary answer for a visitor, not a failure. Anything else,
    // such as the API being down or CORS being wrong, is also not a reason to claim the
    // user is signed in, so both land here.
    if (error instanceof ApiError && error.isUnauthenticated) return null;
    return null;
  }
}

/**
 * The current session, fetched once per page load.
 *
 * Every page shares this promise, so a page with three widgets still makes one
 * request.
 */
export function loadSession(force = false): Promise<Session | null> {
  if (pending === null || force) pending = fetchSession();
  return pending;
}

/** Reveal the right half of the page and fill in the header. */
export function applyChrome(session: Session | null): void {
  setAuthState(session === null ? 'ut' : 'inn');
  rememberAuth(session === null ? 'ut' : 'inn');

  if (session === null) {
    // Nothing to remember about a header nobody is looking at, and a stale hint
    // would paint the previous account's person and company on the next visit.
    // The company's *id* is kept (see `forgetActiveCompanyName`): /auth/me
    // answers the same way for an expired cookie and for an API that could not
    // be reached, and only one of those is a reason to forget which client you
    // were working on.
    forgetChrome();
    forgetActiveCompanyName();
  } else {
    for (const node of document.querySelectorAll<HTMLElement>('[data-session-name]')) {
      node.textContent = session.user.full_name || session.user.email;
    }
    // The header is company-first: the business is what the books are about,
    // and the person is who happens to be logged into them. The company takes a
    // second request, so something has to stand in the slot meanwhile.
    //
    // The stored choice is that stand-in, and it has already been painted by
    // the inline script in the head. Nothing to do here but leave it alone:
    // `currentCompany` confirms or corrects it the moment /companies answers,
    // and whatever is written there arrived with its own id (`lib/tenant.ts`),
    // so it cannot be another company's name.
    //
    // An account with no books has no company to name, so the slot is emptied
    // rather than filled with something else. It used to be filled with the
    // account's own name, which is exactly the habit this application had to
    // break: an account is routinely called after one of its companies, and a
    // name like that in the company slot is indistinguishable from the wrong
    // company being on screen. The header shows the person instead.
    //
    // Emptied and not left alone, because the pre-paint may have written a name
    // from a stored choice that no longer exists.
    if (activeCompany() === null) showCompanyName('');
  }
  document.documentElement.dataset.sessionReady = 'true';
  document.dispatchEvent(new CustomEvent<Session | null>(SESSION_EVENT, { detail: session }));
}

/** Wire up the shared header. Called once from the layout on every page. */
export function bootstrapChrome(): void {
  const signOutButton = document.querySelector<HTMLButtonElement>('[data-sign-out]');
  signOutButton?.addEventListener('click', () => {
    void signOut();
  });

  watchForForeignSwitch();
  void loadSession().then(applyChrome);
}

/**
 * Notice when another tab switches company, and say so rather than follow.
 *
 * A regnskapsfører with two clients open in two tabs is an ordinary Tuesday,
 * and switching company in one of them rewrites the choice both share. This
 * page does not re-read it (`lib/tenant.ts` freezes it per page load), so
 * everything on screen stays the company it was drawn for, which is the only
 * self-consistent answer. But «the company you are looking at» has quietly
 * stopped being «the company this site is set to», and the next click on a link
 * lands somewhere else. The honest response is a line saying so, with the way
 * to catch up.
 *
 * `storage` fires only in the *other* tabs, which is exactly the audience.
 */
function watchForForeignSwitch(): void {
  const notice = document.querySelector<HTMLElement>('[data-company-switched]');
  if (notice === null) return;
  notice.querySelector('[data-company-reload]')?.addEventListener('click', () => {
    window.location.reload();
  });
  window.addEventListener('storage', () => {
    if (!activeCompanyMovedElsewhere()) return;
    notice.hidden = false;
  });
}

export async function signOut(): Promise<void> {
  try {
    await logout();
  } finally {
    // Even if the call failed, the local hint must go: leaving it set would
    // show the signed-in shell to whoever opens the browser next.
    rememberAuth('ut');
    clearActiveCompany();
    forgetChrome();
    window.location.href = '/';
  }
}

/** Send a visitor to the login page, remembering where they were headed. */
export function redirectToLogin(): void {
  const target = window.location.pathname + window.location.search;
  window.location.href = `/logg-inn/?neste=${encodeURIComponent(target)}`;
}

/**
 * Where `?neste=` may send someone: our own pages, and nowhere else.
 *
 * Site-relative and not protocol-relative, so a crafted link cannot turn our
 * login or signup into an open redirect to somebody else's page that looks like
 * ours. Anything else is answered with the front page rather than refused: the
 * visitor did nothing wrong, and they are signed in either way.
 */
export function safeNext(value: string | null): string {
  if (value === null || !value.startsWith('/') || value.startsWith('//')) return '/';
  return value;
}

// --- Which company the user is looking at ---------------------------------------
//
// The choice itself lives in `lib/tenant.ts`, in one record with the name and
// the confirmation mark that go with it. What is here is the half that touches
// the page: writing the choice and painting it are the same call, so a screen
// naming one company while the requests name another is not a state this code
// can be left in. See the comment at the top of that module.

/**
 * The company being worked on: chosen, stored, and on screen, in one call.
 *
 * The name is the loud half of the header and the signed-in person is the quiet
 * half beneath it, because every page is about a set of books rather than about
 * an account: a regnskapsfører switching client has to see *which* client at a
 * glance, and their own name never changes.
 *
 * The «ubekreftet» mark is a link rather than a label. An unconfirmed company
 * is not broken (the books work, and nothing is hidden), it is a claim nobody
 * has evidenced yet, and the one thing worth offering is the way to evidence
 * it.
 *
 * Takes a `Company` and never an id: that is what stops the name on screen and
 * the id in the URL from being set by two different call sites, one of which
 * eventually forgets. `/nytt-selskap/` was that call site.
 */
export function setActiveCompany(company: Company): void {
  writeActiveCompany(company);
  paintCompany(company.name, company.verified);
}

/**
 * Put a name in the company slot without claiming it is a company.
 *
 * For the one account that has no books at all, where the organization's own
 * name is the only true thing to show. Nothing is stored, because nothing has
 * been chosen: the next page load must not read this back as a company.
 */
export function showCompanyName(name: string): void {
  paintCompany(name, true);
}

function paintCompany(name: string, verified: boolean): void {
  for (const node of document.querySelectorAll<HTMLElement>('[data-session-company]')) {
    node.textContent = name;
  }
  for (const mark of document.querySelectorAll<HTMLElement>('[data-session-unverified]')) {
    // Hidden when there is no company at all: there is nothing to confirm yet,
    // and «ubekreftet» about nothing reads as a fault.
    mark.hidden = verified || name === '';
  }
}

/** True when the caller's role in this organization carries the permission. */
export function can(session: Session | null, organizationId: number, permission: string): boolean {
  const membership = session?.memberships.find((m) => m.organization_id === organizationId);
  return membership?.permissions.includes(permission) ?? false;
}

/**
 * Whether the caller may do this to *these* books.
 *
 * The one to reach for. `can` above answers about an account, and an account
 * now carries exactly one decision worth asking about: whether a new company
 * may be added to it. Everything else is per company, because access is a row
 * per person per company: the same person can own their own books and merely
 * read a client's, and a page that asked the account would offer them the wrong
 * buttons on one of the two.
 *
 * Reads `permissions` and never `role`, so the role to permission table stays
 * in one place, which is the backend (`domain/identity/access.py`).
 */
export function canInCompany(company: Company | null | undefined, permission: string): boolean {
  return company?.permissions.includes(permission) ?? false;
}

// --- The chrome hint --------------------------------------------------------
//
// What the header said last time about the *account*, kept so it can say the
// same thing again before the first paint instead of a third of a second after
// it.
//
// The site is prerendered, so the file that arrives knows nothing about the
// reader; the header used to fill in only once /auth/me and /companies had both
// answered, which is two round trips to Frankfurt and back.
//
// Only account-level facts live here, and that is a rule and not an accident.
// Everything about the company on screen lives in the one record `lib/tenant.ts`
// owns, next to the id it belongs to. When the company's name and confirmation
// mark were kept *here* instead, they were written by different call sites than
// the id was, and a page that set one without the other left the next page load
// painting one company's name above another company's figures. There is no way
// to write half of that record now, and nothing company-shaped may be added to
// this one.
//
// Like the signed-in hint above it, this grants nothing: it is a cache of
// something that was on screen a moment ago, cleared on sign-out and overwritten
// by the truth as soon as the truth arrives.

/** What the page chrome showed last about the account, per browser. */
export interface ChromeHint {
  /** The signed-in person, as the header writes them. */
  user: string;
  /**
   * How many companies the account had.
   *
   * Read by the head script to decide whether a dashboard is coming, so the
   * overview can be laid out at its finished size instead of growing into it.
   */
  companies: number;
}

function isHint(value: unknown): value is ChromeHint {
  if (typeof value !== 'object' || value === null) return false;
  const hint = value as Record<string, unknown>;
  return typeof hint.user === 'string' && typeof hint.companies === 'number';
}

/**
 * The hint, or null when there is none and when there is nonsense.
 *
 * Validated rather than cast: the value is whatever an older version of this
 * site wrote, or whatever somebody typed into their own devtools, and a header
 * that throws is worse than a header that fills in late. A record left by the
 * version that kept the company here still validates, and its extra fields are
 * ignored rather than read.
 */
export function readChrome(): ChromeHint | null {
  const raw = storage()?.getItem(CHROME_KEY);
  if (raw === null || raw === undefined) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    return isHint(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/**
 * Record what the header is about to show about the account.
 *
 * Takes no company, on purpose: the company is written by `setActiveCompany`,
 * which cannot be called without one. A company switch therefore has one write
 * to get right instead of two to keep in step.
 */
export function rememberChrome(session: Session, companyCount: number): void {
  const hint: ChromeHint = {
    user: session.user.full_name || session.user.email,
    companies: companyCount,
  };
  try {
    storage()?.setItem(CHROME_KEY, JSON.stringify(hint));
  } catch {
    // A full or refused storage costs a returning visitor one flash. Not worth
    // an error anywhere.
  }
}

export function forgetChrome(): void {
  storage()?.removeItem(CHROME_KEY);
}
