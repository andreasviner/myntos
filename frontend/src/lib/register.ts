/**
 * What Enhetsregisteret says about a unit's standing, in the words a page shows.
 *
 * Three pages describe a unit in one line (setting up books, the invoice's
 * buyer, the company page) and each kept its own copy of "konkurs" and "under
 * avvikling". A deleted unit was missing from all three, and when the register
 * started answering one as an ordinary 200 (2026-10-22) it read as a live
 * company with no address. One list, in the order a reader should meet it:
 * gone first, because nothing else about a deleted unit matters as much.
 */

import type { RegisteredUnit } from './api';
import { isoDate } from './format';

type Standing = Pick<
  RegisteredUnit,
  'bankrupt' | 'under_liquidation' | 'under_forced_liquidation' | 'deleted_on'
>;

/** «slettet …», «registrert konkurs», «under avvikling», «under tvangsavvikling»: those that hold. */
export function standing(unit: Standing): string[] {
  const marks: string[] = [];
  if (unit.deleted_on) marks.push(`slettet fra Enhetsregisteret ${isoDate(unit.deleted_on)}`);
  if (unit.bankrupt) marks.push('registrert konkurs');
  if (unit.under_liquidation) marks.push('under avvikling');
  if (unit.under_forced_liquidation) marks.push('under tvangsavvikling');
  return marks;
}

/** The year the register deleted the unit, or null for a live one. */
export function deletedYear(unit: Pick<RegisteredUnit, 'deleted_on'>): number | null {
  const year = Number(unit.deleted_on?.slice(0, 4));
  return Number.isInteger(year) && year > 0 ? year : null;
}
