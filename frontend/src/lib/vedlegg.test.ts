/**
 * Which upload of printouts the page shows, and the words it uses about one.
 *
 * The button names the number of documents because pressing it puts them in an
 * archive nothing leaves; the sentence says what stays out, and why, before
 * anybody presses it.
 */

import { describe, expect, it } from 'vitest';

import type { DocumentImport, DocumentReview, PrintedVoucher } from './api';
import {
  archiveLabel,
  archivedSummary,
  currentDocumentImport,
  documentsSummary,
  documentsYear,
} from './vedlegg';

function voucher(number: string, overrides: Partial<PrintedVoucher> = {}): PrintedVoucher {
  return {
    number,
    file_name: 'Bilag_September_2026.pdf',
    first_page: 1,
    pages: 2,
    attachment_pages: 1,
    printed_on: '2026-09-30',
    action: 'attach',
    sha256: 'a'.repeat(64),
    byte_size: 48454,
    printed_debit: '443642.88',
    booked_debit: '443642.88',
    targets: [{ voucher_id: 7, voucher_no: `2026-${number.split('-')[0]}`, already: false }],
    ...overrides,
  };
}

function review(overrides: Partial<DocumentReview> = {}): DocumentReview {
  return {
    kind: 'vedlegg',
    postable: true,
    software: 'Tripletex',
    files: [{ file_name: 'Bilag_September_2026.pdf', pages: 110, vouchers: 51, loose_pages: 0 }],
    vouchers_in_files: 51,
    to_attach: 34,
    documents: 37,
    already_attached: 0,
    no_attachments: 17,
    not_in_books: 0,
    too_large: 0,
    repeated: 0,
    vouchers: [],
    findings: [],
    ...overrides,
  };
}

function job(id: number, overrides: Partial<DocumentImport> = {}): DocumentImport {
  return {
    company_id: 1,
    id,
    status: 'ready',
    created_at: '2026-10-22T10:00:00Z',
    updated_at: '2026-10-22T10:00:00Z',
    posted_at: null,
    step: null,
    done: 1,
    total: 1,
    running: false,
    files_kept: true,
    files: [{ filename: 'Bilag_September_2026.pdf', size: 15938883 }],
    review: review(),
    result: null,
    error: null,
    error_code: null,
    may_post: true,
    ...overrides,
  };
}

describe('currentDocumentImport', () => {
  it('is the newest upload not yet archived, even behind an archived one', () => {
    const jobs = [job(3, { status: 'posted' }), job(2, { status: 'ready' })];
    expect(currentDocumentImport(jobs)?.id).toBe(2);
  });

  it('is the newest archived one when nothing waits', () => {
    expect(currentDocumentImport([job(5, { status: 'posted' }), job(4, { status: 'posted' })])?.id).toBe(5);
    expect(currentDocumentImport([])).toBeNull();
  });
});

describe('archiveLabel', () => {
  it('names how many documents go in', () => {
    expect(archiveLabel(review())).toBe('Legg 37 dokumenter i bilagsarkivet');
    expect(archiveLabel(review({ documents: 1 }))).toBe('Legg 1 dokument i bilagsarkivet');
    expect(archiveLabel(review({ documents: 0 }))).toBe('Ingenting å legge i bilagsarkivet');
  });
});

describe('documentsSummary', () => {
  it('says what goes in, and on how many vouchers when the import split some', () => {
    expect(documentsSummary(review())).toBe(
      'Utskriften har 51 bilag fra Tripletex. 34 av dem har vedlegg, som legges på 37 bilag i ' +
        'Myntos. 17 har bare Tripletex-siden med posteringene og får ingenting.',
    );
  });

  it('says what is already there and what Myntos does not have', () => {
    expect(
      documentsSummary(
        review({ to_attach: 2, documents: 2, no_attachments: 0, already_attached: 30, not_in_books: 3 }),
      ),
    ).toBe(
      'Utskriften har 51 bilag fra Tripletex. 2 av dem har vedlegg. 30 har fått dokumentet før. ' +
        '3 finnes ikke blant bilagene som er hentet.',
    );
  });
});

describe('archivedSummary', () => {
  it('counts the documents and the printed vouchers they came from', () => {
    expect(archivedSummary(review())).toBe('Lagt 37 dokumenter i bilagsarkivet, fra 34 bilag i Tripletex.');
  });
});

describe('documentsYear', () => {
  it('is the latest year a document went into, from the numbers Myntos gave the vouchers', () => {
    const result = review({
      vouchers: [
        voucher('196-2025', { targets: [{ voucher_id: 1, voucher_no: '2025-196', already: false }] }),
        voucher('240-2026'),
        voucher('999-2027', { action: 'not_in_books', targets: [] }),
      ],
    });
    expect(documentsYear(result)).toBe(2026);
    expect(documentsYear(review())).toBeNull();
  });
});
