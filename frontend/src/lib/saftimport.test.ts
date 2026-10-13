/**
 * Which import the page shows, and the words it uses about one.
 *
 * A person who leaves the page while the server reads their files comes back
 * to whatever the newest import is, so «which one is current» decides what
 * they find: the half-done one if there is one, never a finished one hiding it.
 */

import { describe, expect, it } from 'vitest';

import type { SaftImport, SaftMoveReview } from './api';
import {
  currentComparison,
  currentImport,
  isWorking,
  postLabel,
  postedSummary,
  summary,
  yearsToClose,
} from './saftimport';

function review(overrides: Partial<SaftMoveReview> = {}): SaftMoveReview {
  const year = (value: number, complete: boolean) => ({
    year: value,
    file_name: `${value}.zip`,
    software: 'Tripletex',
    start_date: `${value}-01-01`,
    end_date: complete ? `${value}-12-31` : `${value}-09-30`,
    complete,
    through: complete ? `${value}-12-31` : `${value}-09-30`,
    opened: true,
    vouchers: 300,
    lines: 900,
    first_date: `${value}-01-03`,
    last_date: `${value}-09-30`,
    already_imported: 0,
    later: 0,
    months: [],
  });
  return {
    kind: 'import',
    postable: true,
    vouchers: 600,
    first_year_before: 2026,
    first_year_after: 2025,
    years_opened: [2025],
    years: [year(2025, true), year(2026, false)],
    opening: { year: 2025, action: 'post', replaces: null, replaces_year: null, lines: 24, total: '1000.00' },
    accounts_to_add: [],
    parties_to_add: [],
    dimensions_to_add: [],
    findings: [],
    ...overrides,
  };
}

function job(id: number, overrides: Partial<SaftImport> = {}): SaftImport {
  return {
    company_id: 1,
    id,
    purpose: 'import',
    status: 'ready',
    til: null,
    created_at: '2026-10-13T10:00:00Z',
    updated_at: '2026-10-13T10:00:00Z',
    posted_at: null,
    step: null,
    done: 0,
    total: 1,
    running: false,
    files_kept: true,
    files: [],
    review: null,
    result: null,
    error: null,
    error_code: null,
    may_post: false,
    ...overrides,
  };
}

describe('the import on screen', () => {
  it('is the newest one not posted, ahead of a posted one', () => {
    const jobs = [job(3, { status: 'posted' }), job(2, { status: 'ready' }), job(1)];
    expect(currentImport(jobs)?.id).toBe(2);
  });

  it('is the newest posted one when every import is done', () => {
    expect(currentImport([job(5, { status: 'posted' }), job(4, { status: 'posted' })])?.id).toBe(5);
  });

  it('leaves comparisons to their own card', () => {
    const jobs = [job(7, { purpose: 'sammenligning' }), job(6, { status: 'posted' })];
    expect(currentImport(jobs)?.id).toBe(6);
    expect(currentComparison(jobs)?.id).toBe(7);
  });

  it('is polled while the server reads or posts it', () => {
    expect(isWorking(job(1, { status: 'reading' }))).toBe(true);
    expect(isWorking(job(1, { status: 'posting' }))).toBe(true);
    expect(isWorking(job(1, { status: 'failed' }))).toBe(false);
  });
});

describe('the words about a review', () => {
  it('names the number on the button, or the balance when there are no vouchers', () => {
    expect(postLabel(review())).toBe('Før 600 bilag');
    expect(postLabel(review({ vouchers: 0 }))).toBe('Før inngående balanse');
    expect(postLabel(review({ vouchers: 0, opening: null }))).toBe('Ingenting nytt å føre');
  });

  it('says where the books start when the files reach before them', () => {
    expect(summary(review())).toBe(
      'Filene fra Tripletex gir 600 nye bilag for 2025–2026. ' +
        'Regnskapet i Myntos starter da i 2025, ikke i 2026.',
    );
  });

  it('sums up what a post did', () => {
    expect(
      postedSummary(review({ accounts_to_add: [{} as never, {} as never] })),
    ).toBe(
      'Ført 600 bilag for 2025–2026. Inngående balanse er ført per 1. januar 2025. La til 2 kontoer.',
    );
  });

  it('points at the earlier years that are whole, and not the running one', () => {
    expect(yearsToClose(review())).toEqual([2025]);
  });
});
