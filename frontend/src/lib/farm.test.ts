import { describe, expect, it } from 'vitest';

import type { FinancialYearStatus } from './api';
import { decimal, decodeCategory, encodeCategory, yearToOpen } from './farm';

function year(y: number, closed = false): FinancialYearStatus {
  return {
    company_id: 1,
    year: y,
    start_date: `${y}-01-01`,
    end_date: `${y}-12-31`,
    closed,
    closed_at: null,
    events: [],
  };
}

describe('the category select', () => {
  it('carries the kind only for «Annet»', () => {
    expect(encodeCategory('storfe_kyr_ib')).toBe('storfe_kyr_ib');
    expect(encodeCategory('annet', 'innkjopt')).toBe('annet:innkjopt');
    expect(decodeCategory('annet:egenprodusert')).toEqual({ category: 'annet', kind: 'egenprodusert' });
    expect(decodeCategory('korn')).toEqual({ category: 'korn', kind: null });
  });

  it('does not invent a kind the backend does not know', () => {
    expect(decodeCategory('annet:fisk')).toEqual({ category: 'annet', kind: null });
  });
});

describe('yearToOpen', () => {
  it('opens on the open year that is over, which is the one waiting for its count', () => {
    expect(yearToOpen([year(2025), year(2026)], '2026-02-10')).toBe(2025);
  });

  it('skips a closed year, and falls back to the running one', () => {
    expect(yearToOpen([year(2025, true), year(2026)], '2026-02-10')).toBe(2026);
    expect(yearToOpen([year(2025, true)], '2026-02-10')).toBe(2025);
    expect(yearToOpen([], '2026-02-10')).toBeNull();
  });
});

describe('decimal', () => {
  it('drops the scale the API pads with, and never the zeros of a whole number', () => {
    expect(decimal('9000')).toBe('9000');
    expect(decimal('9000.0000')).toBe('9000');
    expect(decimal('2.500')).toBe('2,5');
    expect(decimal('0.4500')).toBe('0,45');
  });
});
