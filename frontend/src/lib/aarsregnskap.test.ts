import { describe, expect, it } from 'vitest';

import {
  awaitingSignature,
  documentTypeLabel,
  filingStatusLabel,
  filingStatusTone,
  formatBoardMembersText,
  formatWholeKr,
  isSubmitted,
  parseBoardMembersText,
  parseWholeKrInput,
  submissionReadinessText,
  yearFromHash,
} from './aarsregnskap';

describe('formatWholeKr and parseWholeKrInput', () => {
  it('formats positive, negative, zero, and missing whole NOK amounts', () => {
    expect(formatWholeKr(0)).toBe('0');
    expect(formatWholeKr(125000)).toBe('125 000');
    expect(formatWholeKr(-45200)).toBe('-45 200');
    expect(formatWholeKr(null)).toBe('-');
  });

  it('parses Norwegian spaced integers and refuses invalid strings', () => {
    expect(parseWholeKrInput('125 000')).toBe(125000);
    expect(parseWholeKrInput('-45 200')).toBe(-45200);
    expect(parseWholeKrInput('')).toBeNull();
    expect(parseWholeKrInput('125.50')).toBeNull();
    expect(parseWholeKrInput('abc')).toBeNull();
  });
});

describe('status labels and workflow predicates', () => {
  it('maps filing statuses to bokmål labels and tones', () => {
    expect(filingStatusLabel('laast')).toContain('klar for signering');
    expect(filingStatusLabel('mottatt')).toBe('Mottatt med kvittering');
    expect(filingStatusTone('mottatt')).toBe('ok');
    expect(filingStatusTone('laast')).toBe('warn');
    expect(filingStatusTone('feilet')).toBe('err');
    expect(documentTypeLabel('kvittering_pdf')).toBe('Kvittering fra Altinn (PDF)');
  });

  it('distinguishes filings waiting for signature from submitted filings', () => {
    expect(awaitingSignature({ status: 'laast' })).toBe(true);
    expect(awaitingSignature({ status: 'klargjort' })).toBe(true);
    expect(awaitingSignature({ status: 'signert' })).toBe(false);
    expect(isSubmitted({ status: 'signert' })).toBe(true);
    expect(isSubmitted({ status: 'mottatt' })).toBe(true);
    expect(isSubmitted({ status: 'laast' })).toBe(false);
  });
});

describe('yearFromHash and board members parsing', () => {
  it('reads a valid four-digit year from the URL hash', () => {
    expect(yearFromHash('#2025', 2024)).toBe(2025);
    expect(yearFromHash('2026', 2024)).toBe(2026);
    expect(yearFromHash('#invalid', 2024)).toBe(2024);
  });

  it('round-trips board member lines', () => {
    const parsed = parseBoardMembersText('Kari Nordmann, Styreleder\nOla Hansen\n');
    expect(parsed).toEqual([
      { name: 'Kari Nordmann', role: 'Styreleder' },
      { name: 'Ola Hansen', role: 'Styremedlem' },
    ]);
    expect(formatBoardMembersText(parsed)).toBe(
      'Kari Nordmann, Styreleder\nOla Hansen, Styremedlem',
    );
  });
});

describe('submissionReadinessText', () => {
  const base = {
    submission_available: true,
    systembruker_available: true,
    systembruker_approved: false,
    id_porten_available: true,
    id_porten_logged_in: false,
    blocked: false,
  };

  it('explains unconfigured, blocked, systembruker, and ID-porten states clearly', () => {
    expect(submissionReadinessText({ ...base, submission_available: false })).toContain(
      'ikke satt opp',
    );
    expect(submissionReadinessText({ ...base, blocked: true })).toContain('Rett feilene');
    expect(submissionReadinessText({ ...base, systembruker_approved: true })).toContain(
      'Altinn-systembruker er godkjent',
    );
    expect(submissionReadinessText({ ...base, id_porten_logged_in: true })).toContain(
      'logget inn med ID-porten',
    );
  });
});
