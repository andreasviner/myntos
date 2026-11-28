/**
 * The deadline list (`/frister/`, and the card on the overview): what each row
 * says, and which rows the card picks.
 *
 * Here and not in the pages because the mistake it guards against is a quiet
 * one. `done: null` means Myntos cannot know (a payment, or a report filed from
 * another program), and a page that reads it as «not done» tells a company it
 * is late with something it sent in January. Only the backend's `is_overdue`
 * says late, and only `done: false` says not sent.
 */

import type { Frist } from './api';

/** How a row is coloured: missed, met, still to do, or not Myntos's to know. */
export type FristTone = 'over' | 'done' | 'open' | 'unknown';

export function toneOf(frist: Frist): FristTone {
  if (frist.is_overdue) return 'over';
  if (frist.done === true) return 'done';
  if (frist.done === false) return 'open';
  return 'unknown';
}

/** The status as the page writes it; empty when Myntos cannot know. */
export function statusText(frist: Frist): string {
  if (frist.is_overdue) return 'Over fristen';
  if (frist.done === true) {
    if (frist.done_by === 'annet_program') return 'Sendt fra et annet program';
    if (frist.done_by === 'regnskapsregisteret') return 'Mottatt i Regnskapsregisteret';
    return 'Sendt fra Myntos';
  }
  if (frist.done === false) return 'Ikke sendt ennå';
  return '';
}

function utcDay(iso: string): number {
  const [year, month, day] = iso.split('-').map(Number);
  return Date.UTC(year ?? 1970, (month ?? 1) - 1, day ?? 1);
}

/**
 * How far off the deadline is, from the backend's `today`.
 *
 * The backend's day and not the browser's: the list was decided against
 * Norway's date, and a laptop in another timezone would otherwise call
 * yesterday's deadline «i dag» beside a badge that says it has passed.
 */
export function distance(deadline: string, today: string): string {
  const days = Math.round((utcDay(deadline) - utcDay(today)) / 86_400_000);
  if (days === 0) return 'i dag';
  if (days === 1) return 'i morgen';
  if (days === -1) return 'i går';
  return days > 0 ? `om ${days} dager` : `${-days} dager siden`;
}

/** Where in Myntos the row is done, or null when it is done somewhere else. */
export function placeFor(frist: Frist): { href: string; text: string } | null {
  if (frist.kind === 'mva_melding') return { href: '/mva/', text: 'Til mva-meldingen' };
  if (frist.kind === 'skattemelding' && frist.files_from_myntos) {
    return { href: '/skattemelding/', text: 'Til skattemeldingen' };
  }
  if (frist.kind === 'aksjonaerregisteroppgave' && frist.files_from_myntos) {
    return { href: '/aksjonaer/', text: 'Til aksjonærregisteroppgaven' };
  }
  if (frist.kind === 'arsregnskap' && frist.files_from_myntos) {
    return { href: '/aarsregnskap/', text: 'Til årsregnskapet' };
  }
  return null;
}

/**
 * The rows the overview card shows: what is missed first, then what is still
 * to do, soonest first, and nothing already met.
 *
 * Missed first because it is the one thing on the list that already costs
 * money; a card that showed three upcoming dates above it would bury it.
 */
export function nextUp(frister: readonly Frist[], limit: number): Frist[] {
  const missed = frister.filter((frist) => frist.is_overdue);
  const ahead = frister.filter((frist) => !frist.is_overdue && frist.done !== true);
  return [...missed, ...ahead].slice(0, limit);
}
