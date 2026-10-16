import { describe, expect, it } from 'vitest';

import type { ClosurePlan } from './api';
import { asksForSaft, dayInWords, nameMatches, readyToClose } from './closure';

const NOW = Date.parse('2026-10-15T12:00:00Z');

function plan(overrides: Partial<ClosurePlan> = {}): ClosurePlan {
  return {
    outcome: 'kept',
    retain_until: '2031-12-31',
    years: [2025, 2026],
    members_leaving: 0,
    live_keys: 0,
    requires_password: true,
    recent_login_until: '2026-10-15T12:05:00Z',
    ...overrides,
  };
}

const ALL = {
  companyName: 'Nordmann Consulting AS',
  typedName: 'Nordmann Consulting AS',
  savedSaft: true,
  now: NOW,
};

describe('dayInWords', () => {
  it('reads the date from its digits, so no time zone can move it', () => {
    expect(dayInWords('2031-12-31')).toBe('31. desember 2031');
    expect(dayInWords('2032-01-01')).toBe('1. januar 2032');
  });

  it('leaves anything else as it came', () => {
    expect(dayInWords('i morgen')).toBe('i morgen');
  });
});

describe('readyToClose', () => {
  it('asks for the SAF-T files only when there are some to download', () => {
    expect(asksForSaft(plan())).toBe(true);
    expect(asksForSaft(plan({ years: [] }))).toBe(false);
    expect(asksForSaft(plan({ outcome: 'deleted', retain_until: null, years: [] }))).toBe(false);
    expect(readyToClose(plan(), { ...ALL, savedSaft: false })).toBe(false);
    expect(readyToClose(plan({ years: [] }), { ...ALL, savedSaft: false })).toBe(true);
  });

  it('always asks for the company name, whatever the proof', () => {
    expect(readyToClose(plan(), { ...ALL, typedName: '' })).toBe(false);
    expect(readyToClose(plan(), { ...ALL, typedName: 'Nordmann Holding AS' })).toBe(false);
    expect(readyToClose(plan(), ALL)).toBe(true);
  });

  it('needs a sign-in in the last ten minutes, with or without a password', () => {
    const fresh = '2026-10-15T12:05:00Z';
    const stale = '2026-10-15T11:55:00Z';
    for (const requires_password of [true, false]) {
      expect(readyToClose(plan({ requires_password, recent_login_until: fresh }), ALL)).toBe(true);
      expect(readyToClose(plan({ requires_password, recent_login_until: stale }), ALL)).toBe(false);
    }
  });
});

describe('nameMatches', () => {
  it('ignores case and spacing, as the backend does, and nothing else', () => {
    expect(nameMatches('Nordmann Consulting AS', '  nordmann   consulting as ')).toBe(true);
    expect(nameMatches('Nordmann Consulting AS', 'Nordmann Consulting')).toBe(false);
    expect(nameMatches('Nordmann Consulting AS', '   ')).toBe(false);
  });
});
