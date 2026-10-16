/**
 * What the import page decides about the server's imports, kept out of the page
 * so it can be tested: which import is the one on screen, what the button says,
 * and the sentence that sums a review up.
 *
 * The figures themselves are the server's (`review`), never recomputed here.
 */

import type { MvaPeriodRow, SaftImport, SaftMoveReview } from './api';

/** Whether the server is working on it: the page polls until it is not. */
export function isWorking(job: SaftImport): boolean {
  return job.status === 'reading' || job.status === 'posting';
}

/**
 * The import the page is about: the newest one not yet posted, or else the
 * newest posted one, so a person coming back sees what they left.
 *
 * Only imports of the books; comparisons have their own card.
 */
export function currentImport(jobs: SaftImport[]): SaftImport | null {
  const imports = jobs.filter((job) => job.purpose === 'import');
  return imports.find((job) => job.status !== 'posted') ?? imports[0] ?? null;
}

export function currentComparison(jobs: SaftImport[]): SaftImport | null {
  return jobs.find((job) => job.purpose === 'sammenligning') ?? null;
}

/** «2025», «2024–2026». */
export function yearSpan(review: SaftMoveReview): string {
  const years = review.years.map((year) => year.year);
  if (years.length === 0) return '';
  const first = Math.min(...years);
  const last = Math.max(...years);
  return first === last ? String(first) : `${first}–${last}`;
}

/** What the post button says: the number, so the press says what it does. */
export function postLabel(review: SaftMoveReview): string {
  if (review.vouchers > 0) {
    return `Før ${review.vouchers.toLocaleString('nb-NO')} bilag`;
  }
  const opening = review.opening;
  if (opening !== null && (opening.action === 'post' || opening.action === 'replace')) {
    return 'Før inngående balanse';
  }
  return 'Ingenting nytt å føre';
}

function plural(count: number, one: string, many: string): string {
  return `${count.toLocaleString('nb-NO')} ${count === 1 ? one : many}`;
}

/** The review in one sentence: what comes over, from where, and where the books start. */
export function summary(review: SaftMoveReview): string {
  const software = review.years.find((year) => year.software)?.software;
  const from = software ? ` fra ${software}` : '';
  const parts = [
    `Filene${from} gir ${plural(review.vouchers, 'nytt bilag', 'nye bilag')} for ${yearSpan(review)}.`,
  ];
  if (review.first_year_before !== null && review.first_year_after < review.first_year_before) {
    parts.push(
      `Regnskapet i Myntos starter da i ${review.first_year_after}, ikke i ${review.first_year_before}.`,
    );
  }
  const already = review.years.reduce((sum, year) => sum + year.already_imported, 0);
  if (already > 0) parts.push(`${plural(already, 'bilag er', 'bilag er')} hentet før og hoppes over.`);
  return parts.join(' ');
}

/** «Ført 601 bilag for 2025–2026.» for a posted import. */
export function postedSummary(result: SaftMoveReview): string {
  const parts = [`Ført ${plural(result.vouchers, 'bilag', 'bilag')} for ${yearSpan(result)}.`];
  const opening = result.opening;
  if (opening !== null && (opening.action === 'post' || opening.action === 'replace')) {
    parts.push(`Inngående balanse er ført per 1. januar ${opening.year}.`);
  }
  const added = [
    result.accounts_to_add.length > 0 ? plural(result.accounts_to_add.length, 'konto', 'kontoer') : null,
    result.parties_to_add.length > 0
      ? plural(result.parties_to_add.length, 'kunde eller leverandør', 'kunder og leverandører')
      : null,
    result.dimensions_to_add.length > 0
      ? plural(result.dimensions_to_add.length, 'avdeling eller produkt', 'avdelinger og produkter')
      : null,
  ].filter((part): part is string => part !== null);
  if (added.length > 0) parts.push(`La til ${added.join(', ')}.`);
  return parts.join(' ');
}

/** The complete earlier years the import left open, for the link to Årsavslutning. */
export function yearsToClose(review: SaftMoveReview): number[] {
  const last = Math.max(...review.years.map((year) => year.year));
  return review.years.filter((year) => year.complete && year.year < last).map((year) => year.year);
}

/**
 * The VAT terms of the imported years to ask about: over, and neither filed
 * from Myntos nor already said to be filed elsewhere.
 *
 * Asked here because this is the moment a person knows the answer: they have
 * just brought the old program's books over. Left to the MVA page, every one
 * of those terms read as overdue and offered «Send», and a second melding for
 * a term replaces the first at Skatteetaten.
 */
export function termsToConfirm(periods: MvaPeriodRow[]): MvaPeriodRow[] {
  return periods.filter(
    (row) =>
      row.is_over && !row.filed_elsewhere && row.status !== 'sendt' && row.status !== 'mottatt',
  );
}

/**
 * The day recorded as the filing day when the person does not give one.
 *
 * The deadline once it has passed, today before then: the same default the
 * MVA page fills in, and always a day the backend accepts (after the term
 * ended, not in the future).
 */
export function defaultFiledOn(row: MvaPeriodRow, today: string): string {
  return today < row.deadline ? today : row.deadline;
}
