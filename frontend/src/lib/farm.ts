/**
 * What the two farm year-end pages (/buskap/, /private-uttak/) share and can be
 * tested on: the words for the backend's codes, and which year a page opens on.
 *
 * Nothing here values anything. The values, the average of the adult animals,
 * the VAT and the voucher all come from the backend's `…/kontroll`, for the
 * reason lib/format.ts gives about sums computed in the browser.
 */

import type { CountKind, CountValuation, FinancialYearStatus } from './api';

export const KIND_LABELS: Record<CountKind, string> = {
  buskap: 'Buskap',
  egenprodusert: 'Egenproduserte varer (fôr, korn, poteter)',
  innkjopt: 'Innkjøpt fôr, gjødsel og såvarer',
};

export const VALUATION_LABELS: Record<CountValuation, string> = {
  normalverdi: 'Normalverdi',
  snitt: 'Gjennomsnitt av voksne dyr',
  kostpris: 'Kostpris',
  verdsatt: 'Oppgitt verdi',
};

export const VAT_REASON_LABELS: Record<string, string> = {
  fritatt: 'Fritatt (primærnæring, årlig melding)',
  avgiftspliktig: 'Avgiftspliktig',
  uregistrert: 'Ikke mva-registrert',
};

/**
 * The category select's value: the category code, or `annet:<kind>` for
 * «Annet», which the backend takes as `category: 'annet'` with a `kind`.
 * One select instead of two, because «Annet» is the only category whose kind
 * the person has to say.
 */
export function encodeCategory(category: string, kind: CountKind | null = null): string {
  return category === 'annet' && kind !== null ? `annet:${kind}` : category;
}

export function decodeCategory(value: string): { category: string; kind: CountKind | null } {
  const [category, kind] = value.split(':');
  if (category === 'annet' && (kind === 'buskap' || kind === 'egenprodusert' || kind === 'innkjopt')) {
    return { category, kind };
  }
  return { category: category ?? value, kind: null };
}

/**
 * The year a year-end page opens on: the earliest open year that is over, since
 * that is the one waiting for its count; otherwise the earliest open year;
 * otherwise the last one.
 */
export function yearToOpen(years: FinancialYearStatus[], today: string): number | null {
  const open = years.filter((y) => !y.closed);
  const over = open.find((y) => y.end_date < today);
  return over?.year ?? open[0]?.year ?? years[years.length - 1]?.year ?? null;
}

/**
 * A quantity or a value per unit as a Norwegian reads it: «2,5», «0,45», «9000».
 * Text to text, never through a number. The API sends `"2.500"` and `"1.7000"`
 * (the columns' scale), and trailing zeros after the decimal mark say nothing.
 */
export function decimal(value: string): string {
  const trimmed = value.includes('.') ? value.replace(/0+$/, '').replace(/\.$/, '') : value;
  return trimmed.replace('.', ',');
}
