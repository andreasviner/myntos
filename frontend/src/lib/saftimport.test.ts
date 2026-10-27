/**
 * Which import the page shows, and the words it uses about one.
 *
 * A person who leaves the page while the server reads their files comes back
 * to whatever the newest import is, so «which one is current» decides what
 * they find: the half-done one if there is one, never a finished one hiding it.
 */

import { describe, expect, it } from 'vitest';

import type { MvaPeriodRow, SaftArchive, SaftImport, SaftMoveReview } from './api';
import {
  archiveLine,
  currentComparison,
  currentImport,
  defaultFiledOn,
  isWorking,
  keepingDocuments,
  keptSummary,
  postLabel,
  postedSummary,
  skipReason,
  summary,
  termsToConfirm,
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
    archives: [],
    ...overrides,
  };
}

function archive(overrides: Partial<SaftArchive> = {}): SaftArchive {
  return {
    file_name: 'fiken-komplett-gard.zip',
    documents: 1204,
    bytes: 512 * 1024 * 1024,
    folders: [{ name: 'Bilag', documents: 1204 }],
    skipped: [],
    skipped_count: 0,
    kept: null,
    already_kept: null,
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

describe('the documents in an old program’s export', () => {
  it('are on the button when they are all that is left, the books being in Myntos', () => {
    const left = review({ postable: false, vouchers: 0, opening: null, archives: [archive()] });
    expect(postLabel(left)).toBe('Ta vare på 1\u00a0204 dokumenter');
    // With books to post, the button still says what is posted.
    expect(postLabel(review({ archives: [archive()] }))).toBe('Før 600 bilag');
  });

  it('are counted in the review, and what is not kept says why', () => {
    expect(archiveLine(archive())).toBe(
      '«fiken-komplett-gard.zip» har 1\u00a0204 dokumenter (512.0 MB) som tas vare på i Myntos.',
    );
    expect(skipReason('encrypted')).toBe('låst med passord');
    expect(skipReason('broken')).toBe('kunne ikke pakkes ut');
  });

  it('are summed up once kept, and not before', () => {
    expect(keptSummary([archive()])).toBeNull();
    expect(keptSummary([])).toBeNull();
    expect(keptSummary([archive({ kept: 1200, already_kept: 4, skipped_count: 1 })])).toBe(
      'Tok vare på 1\u00a0200 dokumenter fra eksporten. 4 dokumenter var tatt vare på fra før. ' +
        '1 fil i eksporten ble ikke tatt vare på.',
    );
  });

  it('are all a post of the documents alone says it did', () => {
    const alone = review({ postable: false, vouchers: 0, archives: [archive({ kept: 3, already_kept: 0 })] });
    expect(postedSummary(alone)).toBe('Tok vare på 3 dokumenter fra eksporten.');
  });

  it('are being kept once the books are posted, and only then has the bar a count', () => {
    expect(keepingDocuments(job(1, { status: 'posting' }))).toBe(false);
    expect(keepingDocuments(job(1, { status: 'posting', posted_at: '2026-10-27T10:00:00Z' }))).toBe(true);
    expect(keepingDocuments(job(1, { status: 'posted', posted_at: '2026-10-27T10:00:00Z' }))).toBe(false);
  });
});

describe('the VAT terms the old program filed', () => {
  function term(termin: number, overrides: Partial<MvaPeriodRow> = {}): MvaPeriodRow {
    return {
      year: 2025,
      termin,
      label: `${termin}. termin 2025`,
      name: '',
      start: '2025-01-01',
      end: '2025-02-28',
      deadline: '2025-04-10',
      is_over: true,
      fastsatt_merverdiavgift: '100.00',
      line_count: 1,
      status: null,
      filed_fastsatt: null,
      changed_since_filed: false,
      is_overdue: true,
      filed_elsewhere: false,
      filed_elsewhere_amount: null,
      elsewhere_difference: null,
      elsewhere_proposal: null,
      ...overrides,
    };
  }

  it('asks about the terms that are over and not filed or marked', () => {
    const asked = termsToConfirm([
      term(1),
      term(2, { status: 'mottatt' }),
      term(3, { filed_elsewhere: true, status: 'annet_program' }),
      term(4, { is_over: false }),
      term(5, { status: 'validert' }),
    ]);
    expect(asked.map((t) => t.termin)).toEqual([1, 5]);
  });

  it('records the deadline once it has passed, and today before then', () => {
    expect(defaultFiledOn(term(1), '2026-10-15')).toBe('2025-04-10');
    expect(defaultFiledOn(term(1), '2025-03-20')).toBe('2025-03-20');
  });
});
