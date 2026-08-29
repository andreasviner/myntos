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

const HINT_KEY = 'myntos.signed-in';
const COMPANY_KEY = 'myntos.company';

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

  const name = document.querySelector<HTMLElement>('[data-session-name]');
  if (name !== null && session !== null) {
    name.textContent = session.user.full_name || session.user.email;
  }
  // The header is company-first: the business is what the books are about, and
  // the person is who happens to be logged into them. The company is not known
  // yet at this point — it takes a second request — so the organization name
  // stands in meanwhile. It is the closest true answer (the account's own
  // name), and setChromeCompany replaces it the moment the books are known,
  // rather than leaving the slot blank for a beat.
  if (session !== null) {
    const organization = session.memberships[0]?.organization_name ?? '';
    setChromeOrganization(organization);
    setChromeCompany(null, organization);
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

  void loadSession().then(applyChrome);
}

export async function signOut(): Promise<void> {
  try {
    await logout();
  } finally {
    // Even if the call failed, the local hint must go: leaving it set would
    // show the signed-in shell to whoever opens the browser next.
    rememberAuth('ut');
    storage()?.removeItem(COMPANY_KEY);
    window.location.href = '/';
  }
}

/** Send a visitor to the login page, remembering where they were headed. */
export function redirectToLogin(): void {
  const target = window.location.pathname + window.location.search;
  window.location.href = `/logg-inn/?neste=${encodeURIComponent(target)}`;
}

// --- Which company the user is looking at ---------------------------------------

export function rememberCompany(companyId: number): void {
  storage()?.setItem(COMPANY_KEY, String(companyId));
}

export function rememberedCompany(): number | null {
  const raw = storage()?.getItem(COMPANY_KEY);
  if (raw === null || raw === undefined) return null;
  const parsed = Number(raw);
  return Number.isInteger(parsed) ? parsed : null;
}

/** Put an organization's name in the page eyebrow. */
export function setChromeOrganization(name: string): void {
  const node = document.querySelector<HTMLElement>('[data-session-organization]');
  if (node !== null) node.textContent = name;
}

/**
 * Put the company being worked on at the top of the header, and say whether
 * anyone has proved it is theirs.
 *
 * The name is the loud half and the signed-in person is the quiet half beneath
 * it, because every page is about a set of books rather than about an account:
 * a regnskapsfører switching client has to see *which* client at a glance, and
 * their own name never changes.
 *
 * The «ubekreftet» mark is a link rather than a label. An unconfirmed company
 * is not broken — the books work, and nothing is hidden — it is a claim nobody
 * has evidenced yet, and the one thing worth offering is the way to evidence
 * it. `fallback` covers an account with no books yet, where the organization's
 * own name is the only true thing to show.
 */
export function setChromeCompany(company: Company | null, fallback = ''): void {
  const node = document.querySelector<HTMLElement>('[data-session-company]');
  if (node !== null) node.textContent = company?.name ?? fallback;
  const mark = document.querySelector<HTMLElement>('[data-session-unverified]');
  // Hidden for an account with no books at all: there is nothing to confirm
  // yet, and «ubekreftet» about nothing reads as a fault.
  if (mark !== null) mark.hidden = company === null || company.verified;
}

/** The name of one of the caller's organizations, or an empty string. */
export function organizationName(session: Session | null, organizationId: number): string {
  const membership = session?.memberships.find((m) => m.organization_id === organizationId);
  return membership?.organization_name ?? '';
}

/** True when the caller's role in this organization carries the permission. */
export function can(session: Session | null, organizationId: number, permission: string): boolean {
  const membership = session?.memberships.find((m) => m.organization_id === organizationId);
  return membership?.permissions.includes(permission) ?? false;
}
