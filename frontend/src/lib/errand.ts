/**
 * What a page was doing when it sent the browser to ID-porten, so it can finish
 * it on the way back.
 *
 * Fetching a receipt, checking a melding and sending one all go with the
 * person's ID-porten login, which ID-porten lets expire, and a page learnt it
 * had expired only when a press failed. Until 2026-11-02 that press ended in an
 * error, then a button to log in, then a round trip that ID-porten's single
 * sign-on often finished without asking anything, and then the same press
 * again. Now the press leaves a note here and goes to ID-porten, and the page
 * spends the note when the browser comes back: one press.
 *
 * `sessionStorage`, not the URL, for the reason organisasjon.astro gives for
 * its own note: it belongs to this tab and this errand, and a marker in the
 * address would replay on every reload and travel in any copied link. Spent
 * once, and only within the ten minutes a login attempt lives
 * (`ATTEMPT_LIFETIME` in api/v1/skatt.py): somebody who gave up on BankID and
 * came back to the page an hour later did not ask for it then.
 */

import { ApiError } from './api';

const KEY = 'myntos.etter-id-porten';
export const ERRAND_LIFETIME_MS = 10 * 60 * 1000;

export interface Errand {
  /** The page that left, and the only one that finishes it: `/mva/`. */
  page: string;
  companyId: number;
  /** What was pressed, in the page's own words: `kvittering`, `kontroll`, `send`. */
  action: string;
  /** What it was pressed on, read back only by the page that wrote it. */
  data: Record<string, string | number | null>;
  /** When the note was left, in milliseconds since the epoch. */
  at: number;
}

/**
 * Whether a refusal means «log in with ID-porten, again»: a 409 without a
 * code, which is how every route that spends the login says it
 * (`government_login_for` in api/v1/skatt.py). Every other 409 on those routes
 * carries a code.
 */
export function needsGovernmentLogin(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409 && error.code === null;
}

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    // Private-mode Safari throws on access. The press still works; it just
    // takes the second press it always took.
    return null;
  }
}

/** Leave the note. False when it could not be left, and the page should not count on it. */
export function rememberErrand(errand: Omit<Errand, 'at'>, now = Date.now()): boolean {
  try {
    const store = storage();
    if (store === null) return false;
    store.setItem(KEY, JSON.stringify({ ...errand, at: now }));
    return true;
  } catch {
    return false;
  }
}

/**
 * The note this page left for this company, cleared whatever it says, so that
 * it is attempted once. Null when there is none, when another page or company
 * left it, or when it is older than a login attempt lives.
 */
export function takeErrand(page: string, companyId: number, now = Date.now()): Errand | null {
  const store = storage();
  if (store === null) return null;
  let raw: string | null;
  try {
    raw = store.getItem(KEY);
    store.removeItem(KEY);
  } catch {
    return null;
  }
  if (raw === null) return null;
  let errand: Errand;
  try {
    errand = JSON.parse(raw) as Errand;
  } catch {
    return null;
  }
  if (errand.page !== page || errand.companyId !== companyId) return null;
  if (typeof errand.at !== 'number' || now - errand.at > ERRAND_LIFETIME_MS || now < errand.at) {
    return null;
  }
  return errand;
}

/** Drop any note, for a trip back from ID-porten that did not log in. */
export function forgetErrand(): void {
  try {
    storage()?.removeItem(KEY);
  } catch {
    // Nothing to forget, then.
  }
}
