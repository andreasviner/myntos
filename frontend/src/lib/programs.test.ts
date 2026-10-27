/**
 * Which program /tidligere-program/ starts with, and what choosing one adds.
 *
 * The Tripletex card is the reason the choice exists, so the cases that matter
 * are the ones where it would be missing: a person sent from the Tripletex
 * guide, a printout already uploaded, books already brought over from it.
 */

import { describe, expect, it } from 'vitest';

import { guider } from '../data/flytting';
import type { DocumentImport, SaftImport, SaftImportYear, SaftMoveReview } from './api';
import {
  OTHER,
  PROGRAMS,
  documentsNote,
  exportNote,
  guideLink,
  initialProgram,
  programBySlug,
  programFromImport,
  programFromSoftware,
  showsPrintouts,
  switchedNote,
} from './programs';

function year(software: string | null): SaftImportYear {
  return {
    year: 2026,
    file_name: '2026.zip',
    software,
    start_date: '2026-01-01',
    end_date: '2026-10-12',
    complete: false,
    through: '2026-10-12',
    opened: false,
    vouchers: 596,
    lines: 2000,
    first_date: '2026-01-02',
    last_date: '2026-10-12',
    already_imported: 0,
    later: 0,
    months: [],
  };
}

function imported(software: string | null, overrides: Partial<SaftImport> = {}): SaftImport {
  const review = {
    kind: 'import',
    postable: true,
    vouchers: 596,
    first_year_before: null,
    first_year_after: 2026,
    years_opened: [],
    years: [year(software)],
    opening: null,
    accounts_to_add: [],
    parties_to_add: [],
    dimensions_to_add: [],
    findings: [],
    archives: [],
  } as SaftMoveReview;
  return {
    company_id: 8,
    id: 4,
    purpose: 'import',
    status: 'posted',
    til: null,
    created_at: '2026-10-15T19:56:01Z',
    updated_at: '2026-10-15T19:57:09Z',
    posted_at: '2026-10-15T19:57:09Z',
    step: null,
    done: 1,
    total: 2,
    running: false,
    files_kept: false,
    files: [],
    review,
    result: review,
    error: null,
    error_code: null,
    may_post: false,
    ...overrides,
  };
}

function printouts(status: DocumentImport['status']): DocumentImport {
  return {
    company_id: 8,
    id: 6,
    status,
    created_at: '2026-10-22T20:41:29Z',
    updated_at: '2026-10-22T20:44:08Z',
    posted_at: status === 'posted' ? '2026-10-22T20:44:08Z' : null,
    step: null,
    done: 1,
    total: 1,
    running: false,
    files_kept: status !== 'posted',
    files: [{ filename: 'Bilag_September_2026.pdf', size: 15938883 }],
    review: null,
    result: null,
    error: null,
    error_code: null,
    may_post: status === 'ready',
  };
}

describe('the program the page starts with', () => {
  it('is the one the move guide sent the person from', () => {
    const chosen = initialProgram({ requested: 'tripletex', printouts: [], imports: [] });
    expect(chosen?.slug).toBe('tripletex');
  });

  it('is what was asked for, ahead of what the files say', () => {
    const chosen = initialProgram({ requested: 'fiken', printouts: [], imports: [imported('Tripletex')] });
    expect(chosen?.slug).toBe('fiken');
  });

  it('is Tripletex once a printout has been uploaded', () => {
    const chosen = initialProgram({ requested: null, printouts: [printouts('posted')], imports: [] });
    expect(chosen?.slug).toBe('tripletex');
  });

  it('is the program the SAF-T files already brought over were made by', () => {
    const chosen = initialProgram({ requested: null, printouts: [], imports: [imported('Tripletex')] });
    expect(chosen?.slug).toBe('tripletex');
  });

  it('leaves an unknown ?fra= to what the books say', () => {
    const chosen = initialProgram({ requested: 'nope', printouts: [], imports: [imported('Fiken AS')] });
    expect(chosen?.slug).toBe('fiken');
  });

  it('is nothing when nothing says, rather than a guess', () => {
    expect(initialProgram({ requested: null, printouts: [], imports: [] })).toBeNull();
    const visma = imported('Visma Software AS');
    expect(initialProgram({ requested: null, printouts: [], imports: [visma] })).toBeNull();
  });

  it('is not read from a comparison, which is not books brought over', () => {
    const comparison = imported('Tripletex', { purpose: 'sammenligning' });
    expect(initialProgram({ requested: null, printouts: [], imports: [comparison] })).toBeNull();
  });
});

describe('the printout card', () => {
  const tripletex = programBySlug('tripletex');
  const fiken = programBySlug('fiken');

  it('is on the page for Tripletex and not for the others', () => {
    expect(showsPrintouts(tripletex)).toBe(true);
    expect(showsPrintouts(fiken)).toBe(false);
    expect(showsPrintouts(null)).toBe(false);
  });

  it('comes back by itself for an upload waiting for its button', () => {
    const waiting = initialProgram({ requested: null, printouts: [printouts('ready')], imports: [] });
    expect(showsPrintouts(waiting)).toBe(true);
  });
});

describe('the list', () => {
  it('has a program for every move guide, so each guide can preselect its own', () => {
    for (const guide of guider) expect(programBySlug(guide.slug)?.guide).toBe(guide.slug);
  });

  it('points every program at a guide that exists', () => {
    const slugs = new Set(guider.map((guide) => guide.slug));
    for (const program of PROGRAMS) expect(slugs.has(program.guide)).toBe(true);
  });

  it('reads the program from the SAF-T header where the name is its own', () => {
    expect(programFromSoftware('Tripletex')?.slug).toBe('tripletex');
    expect(programFromSoftware('PowerOffice AS')?.slug).toBe('poweroffice-go');
    expect(programFromSoftware(null)).toBeNull();
  });
});

describe('the words under the dropdown', () => {
  it('sends Tripletex to the card, Fiken to its export, and the rest to their own archive', () => {
    expect(documentsNote(programBySlug('tripletex')!)).toContain('«Vedlegg fra Tripletex»');
    expect(documentsNote(programBySlug('fiken')!)).toContain('den komplette eksporten fra Fiken');
    expect(documentsNote(programBySlug(OTHER)!)).toContain('bilagsarkivet fra det gamle programmet');
  });

  it('links each program to its guide, or to the one for every other program', () => {
    expect(guideLink(programBySlug('fiken')!)).toEqual({
      href: '/kom-i-gang/fiken/',
      text: 'Slik tar du SAF-T-filene ut av Fiken',
    });
    expect(guideLink(programBySlug('xledger')!).href).toBe(`/kom-i-gang/${OTHER}/`);
  });
});

describe('Fiken’s complete export', () => {
  it('is mentioned under the upload for Fiken and for no other program', () => {
    expect(exportNote(programBySlug('fiken'))).toContain('den komplette eksporten (fiken-komplett-….zip)');
    // Kept since 2026-10-27: the line used to tell the person to keep the export themselves.
    expect(exportNote(programBySlug('fiken'))).toContain('tar Myntos vare på');
    expect(exportNote(programBySlug('fiken'))).not.toContain('ta vare på eksporten selv');
    for (const program of PROGRAMS.filter((p) => p.slug !== 'fiken')) {
      expect(exportNote(program)).toBeNull();
    }
    expect(exportNote(null)).toBeNull();
  });
});

describe('the files correcting the choice', () => {
  it('reads the program from an import once its files are read', () => {
    expect(programFromImport(imported('Fiken AS'))?.slug).toBe('fiken');
    expect(programFromImport(imported(null))).toBeNull();
    expect(programFromImport(imported('Fiken AS', { purpose: 'sammenligning' }))).toBeNull();
    expect(switchedNote(programBySlug('fiken')!)).toBe(
      'Filene er fra Fiken, så Fiken er valgt som programmet du kommer fra.',
    );
  });
});
