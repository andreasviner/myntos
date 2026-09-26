/**
 * The one company a page is about, and the wall around it.
 *
 * Every working page in Myntos is about *a* company: one set of books, one
 * orgnr, one row of figures. Which one is a choice the account made, kept in
 * `localStorage` rather than in the URL (see `lib/company.ts` for why), and
 * that choice is the single most dangerous piece of state on the client. A
 * company name from one set of books printed above another's numbers is
 * indistinguishable, to the person reading it, from the API having handed over
 * somebody else's data. The server never does: `get_company_context` resolves
 * every `{company_id}` through the caller's memberships, and every service call
 * filters on `company_id` besides, both asserted route by route in
 * `backend/tests/test_company_boundary.py` and its sibling file. So the only
 * way a wrong company can reach a screen is for this side to put it there.
 *
 * It used to be able to. The name shown before the API answered lived in one
 * `localStorage` record (`myntos.chrome`) and the id every request was built
 * from lived in another (`myntos.company`), written by different call sites at
 * different moments. Setting up a second company wrote the id and forgot the
 * name, so the next page load painted the first company's name over the second
 * company's figures until the request came back.
 *
 * This module exists so that cannot be written again:
 *
 * 1. **One record.** The id, the name and the confirmation mark are one value,
 *    written only from a whole `Company`. There is no way to set the id without
 *    also setting the name, so the two cannot drift apart.
 * 2. **Frozen per page load.** The choice is read once. A second tab switching
 *    company cannot move this page's answer out from under a request that is
 *    already in flight, and a page that has painted a name keeps that name for
 *    as long as it is on screen.
 * 3. **Asserted on every request.** `request()` in `lib/api.ts` is the one
 *    place every API call passes through, and it checks the company in the URL
 *    against `activeCompanyId()` on the way out and against the company in the
 *    answer on the way back. A request naming a company other than the one on
 *    screen fails loudly instead of quietly filling the page with the wrong
 *    figures.
 *
 * None of this is a security boundary and none of it is trusted: the record is
 * a cache of something the user was already looking at, and a tampered value
 * gains nothing, because the API answers 404 for books the caller cannot reach.
 * It is a *consistency* boundary, which is the failure that actually happens.
 */

import type { Company } from './api';

const ACTIVE_KEY = 'myntos.company';

/**
 * The active company, as much of it as is painted before the API answers.
 *
 * Deliberately three fields and not a whole `Company`: what is stored is what
 * the chrome draws, and a stale address or bank account cached here would be a
 * second copy of something no page reads from storage anyway.
 */
export interface ActiveCompany {
  id: number;
  name: string;
  /** Whether anyone has proved in Altinn that they may act for it. */
  verified: boolean;
}

function storage(): Storage | null {
  // Private-mode Safari and locked-down browsers throw on access, not on use.
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/**
 * `undefined` until the first read of the page load, then the answer forever.
 *
 * `null` is a real answer, meaning no company has been chosen in this browser,
 * so the two cannot share a value.
 */
let frozen: ActiveCompany | null | undefined;

function parse(raw: string): ActiveCompany | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  // Before this module the key held a bare id, so an existing browser is
  // holding `12` where a record is now expected. (It parses as a number rather
  // than throwing, which is why this is a branch and not a catch.) The id is
  // still the right company and worth keeping, so the choice survives the
  // upgrade; the name is not in it, and an empty name paints nothing rather
  // than something wrong. `currentCompany` completes the record on the first
  // page load that asks /companies who is who.
  if (typeof value === 'number') {
    return Number.isInteger(value) && value > 0
      ? { id: value, name: '', verified: false }
      : null;
  }
  if (typeof value !== 'object' || value === null) return null;
  const record = value as Record<string, unknown>;
  // Validated rather than cast: this is whatever an older version of the site
  // wrote or whatever somebody typed into their own devtools, and a header that
  // throws is worse than a header that fills in late.
  if (!Number.isInteger(record.id) || (record.id as number) <= 0) return null;
  if (typeof record.name !== 'string') return null;
  return {
    id: record.id as number,
    name: record.name,
    verified: record.verified === true,
  };
}

/**
 * The company this page is about, decided once and then unchanging.
 *
 * Everything on screen is drawn for the answer this gives, so it must not
 * change while the page is up unless the page itself changes it through
 * `setActiveCompany`. Reading storage every time would let another tab's switch
 * arrive halfway through rendering, which is exactly how one company's name
 * ends up over another's figures.
 */
export function activeCompany(): ActiveCompany | null {
  if (frozen === undefined) {
    const raw = storage()?.getItem(ACTIVE_KEY);
    frozen = raw === null || raw === undefined ? null : parse(raw);
  }
  return frozen;
}

/** The id every company-scoped request on this page must carry. */
export function activeCompanyId(): number | null {
  return activeCompany()?.id ?? null;
}

/**
 * Choose the company, in storage and for the rest of this page load.
 *
 * Takes a whole `Company` and not an id, and that is the point: the name the
 * next page load paints and the id its requests are built from come out of the
 * same object, in one write. Callers in `lib/session.ts` wrap this so the
 * chrome on screen is repainted in the same breath.
 */
export function setActiveCompany(company: Company): ActiveCompany {
  const active: ActiveCompany = {
    id: company.id,
    name: company.name,
    verified: company.verified,
  };
  frozen = active;
  try {
    storage()?.setItem(ACTIVE_KEY, JSON.stringify(active));
  } catch {
    // A full or refused storage costs a returning visitor one flash of an
    // unnamed header. Not worth an error anywhere.
  }
  return active;
}

/** Forget the choice. Sign-out only: the next account must not inherit it. */
export function clearActiveCompany(): void {
  frozen = null;
  storage()?.removeItem(ACTIVE_KEY);
}

/**
 * Keep the choice, drop the name: for when the session turns out to be gone.
 *
 * A record with a name in it is a licence to paint that name before the API has
 * said anything, and the moment /auth/me answers "nobody" that licence has to
 * go. Otherwise the next person to sign in on this browser sees the previous
 * account's company in the header for a round trip, which is the same failure
 * this module exists to prevent, one account further out.
 *
 * The id stays, because /auth/me answers "nobody" for a network failure and an
 * expired cookie alike, and a regnskapsfører with a dozen clients should not be
 * dropped back to the first one every time the API hiccups. An id alone paints
 * nothing: `currentCompany` either finds it in the account's list or falls back
 * to the first company, which is what it does for a deleted company anyway.
 */
export function forgetActiveCompanyName(): void {
  const here = activeCompany();
  if (here === null || here.name === '') return;
  const demoted: ActiveCompany = { id: here.id, name: '', verified: false };
  frozen = demoted;
  try {
    storage()?.setItem(ACTIVE_KEY, JSON.stringify(demoted));
  } catch {
    // See setActiveCompany.
  }
}

/**
 * Whether storage now names a different company than this page is drawing.
 *
 * True only for the second tab case: someone switched client in another tab, so
 * the site's choice has moved on while this page is still, correctly, showing
 * what it loaded. The page does not follow, because half a page of one
 * company's figures under another's name is the failure worth avoiding; it says
 * so instead, and offers a reload. See `bootstrapChrome`.
 */
export function activeCompanyMovedElsewhere(): boolean {
  const here = activeCompany();
  if (here === null) return false;
  const raw = storage()?.getItem(ACTIVE_KEY);
  const there = raw === null || raw === undefined ? null : parse(raw);
  return there !== null && there.id !== here.id;
}
