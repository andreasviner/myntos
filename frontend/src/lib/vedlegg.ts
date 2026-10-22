/**
 * What the import page decides about the uploads of the old program's
 * printouts: which upload is on screen, what the button says, and the sentence
 * that sums a review up. Kept out of the page so it can be tested.
 *
 * The figures are the server's (`review`), never counted again here.
 */

import type { DocumentImport, DocumentReview } from './api';

/**
 * The upload the card is about: the newest one not yet archived, or else the
 * newest archived one, so a person coming back sees what they left.
 */
export function currentDocumentImport(jobs: DocumentImport[]): DocumentImport | null {
  return jobs.find((job) => job.status !== 'posted') ?? jobs[0] ?? null;
}

function plural(value: number, one: string, many: string): string {
  return `${count(value)} ${value === 1 ? one : many}`;
}

function count(value: number): string {
  return value.toLocaleString('nb-NO');
}

/** What the button says: the number, so the press says what it does. */
export function archiveLabel(review: DocumentReview): string {
  if (review.documents === 0) return 'Ingenting å legge i bilagsarkivet';
  return `Legg ${plural(review.documents, 'dokument', 'dokumenter')} i bilagsarkivet`;
}

/** The review in a sentence or three: what goes in, and what does not and why. */
export function documentsSummary(review: DocumentReview): string {
  const parts = [
    `Utskriften har ${plural(review.vouchers_in_files, 'bilag', 'bilag')} fra ${review.software}.`,
  ];
  if (review.to_attach > 0) {
    const onto =
      review.documents === review.to_attach
        ? ''
        : `, som legges på ${plural(review.documents, 'bilag', 'bilag')} i Myntos`;
    parts.push(`${count(review.to_attach)} av dem har vedlegg${onto}.`);
  }
  if (review.no_attachments > 0) {
    parts.push(
      `${count(review.no_attachments)} har bare ${review.software}-siden med posteringene og får ingenting.`,
    );
  }
  if (review.already_attached > 0) {
    parts.push(`${count(review.already_attached)} har fått dokumentet før.`);
  }
  if (review.not_in_books > 0) {
    parts.push(`${count(review.not_in_books)} finnes ikke blant bilagene som er hentet.`);
  }
  return parts.join(' ');
}

/** «Lagt 37 dokumenter i bilagsarkivet, fra 34 bilag i Tripletex.» for an archived upload. */
export function archivedSummary(result: DocumentReview): string {
  return (
    `Lagt ${plural(result.documents, 'dokument', 'dokumenter')} i bilagsarkivet, fra ` +
    `${plural(result.to_attach, 'bilag', 'bilag')} i ${result.software}.`
  );
}

/**
 * The year to open the bilagsjournal on after archiving: the latest one a
 * document went into, read off the voucher numbers Myntos gave them («2026-240»).
 */
export function documentsYear(result: DocumentReview): number | null {
  const years = result.vouchers
    .filter((voucher) => voucher.action === 'attach')
    .flatMap((voucher) => voucher.targets.map((target) => Number(target.voucher_no.slice(0, 4))))
    .filter((year) => Number.isInteger(year) && year > 1900);
  return years.length > 0 ? Math.max(...years) : null;
}
