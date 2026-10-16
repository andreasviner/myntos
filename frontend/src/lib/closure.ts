/**
 * Deleting a company: what /slett-selskap/ decides before it lets the button go.
 *
 * Kept out of the page so it can be tested. The rules themselves are the
 * backend's (`myntos.domain.identity.closure`); what is decided here is only
 * when the page has asked everything the backend will ask, so the button is
 * never pressable into a refusal the page could have seen coming.
 */

import type { ClosurePlan } from './api';

const MONTHS = [
  'januar',
  'februar',
  'mars',
  'april',
  'mai',
  'juni',
  'juli',
  'august',
  'september',
  'oktober',
  'november',
  'desember',
];

/**
 * «31. desember 2031» from `2031-12-31`.
 *
 * Read from the digits, never through `new Date(iso)`: that is midnight UTC,
 * which is still the day before anywhere west of Greenwich, and the day the
 * books are deleted must not move with the reader's clock.
 */
export function dayInWords(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (match === null) return iso;
  const [, year, month, day] = match;
  const name = MONTHS[Number(month) - 1];
  return name === undefined ? iso : `${Number(day)}. ${name} ${year}`;
}

export interface ClosureAnswers {
  /** The company's name, as it is. */
  companyName: string;
  /** What is typed in the name field. Always asked: it says which books are meant. */
  typedName: string;
  /** «Jeg har lastet ned SAF-T-filene». Asked only when there are years to download. */
  savedSaft: boolean;
  now: number;
}

const normalised = (name: string): string => name.split(/\s+/).filter(Boolean).join(' ').toLowerCase();

/**
 * Whether the typed name is the company's, ignoring case and spacing.
 *
 * The same leniency as `closure.require_name` in the backend, which has the
 * last word; this only decides when the button may be pressed.
 */
export function nameMatches(companyName: string, typed: string): boolean {
  return typed.trim() !== '' && normalised(typed) === normalised(companyName);
}

/** Whether the login is recent enough to count as proof, for an account without a password. */
export function loginIsRecent(plan: ClosurePlan, now: number): boolean {
  return plan.recent_login_until !== null && new Date(plan.recent_login_until).getTime() > now;
}

/** Whether the SAF-T box is on the page at all: only when there is a file to download. */
export function asksForSaft(plan: ClosurePlan): boolean {
  return plan.outcome === 'kept' && plan.years.length > 0;
}

/** Whether every question the deletion will ask has an answer on the page. */
export function readyToClose(plan: ClosurePlan, answers: ClosureAnswers): boolean {
  if (!nameMatches(answers.companyName, answers.typedName)) return false;
  if (asksForSaft(plan) && !answers.savedSaft) return false;
  // The page asks for a recent sign-in and nothing else, whichever way the
  // account signs in: there is no password field to show to somebody who
  // signed up with Google or BankID. The backend would take a password too
  // (`or_recent_login`), for API clients.
  return loginIsRecent(plan, answers.now);
}
