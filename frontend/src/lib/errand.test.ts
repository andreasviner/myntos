/**
 * The note a page leaves before ID-porten: spent once, by the page and company
 * that left it, and only while a login attempt could still be coming back.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from './api';
import { ERRAND_LIFETIME_MS, forgetErrand, needsGovernmentLogin, rememberErrand, takeErrand } from './errand';

class MemoryStorage implements Storage {
  private items = new Map<string, string>();
  get length(): number {
    return this.items.size;
  }
  clear(): void {
    this.items.clear();
  }
  getItem(key: string): string | null {
    return this.items.get(key) ?? null;
  }
  key(index: number): string | null {
    return [...this.items.keys()][index] ?? null;
  }
  removeItem(key: string): void {
    this.items.delete(key);
  }
  setItem(key: string, value: string): void {
    this.items.set(key, value);
  }
}

const NOW = 1_800_000_000_000;
const RECEIPT = { page: '/mva/', companyId: 8, action: 'kvittering', data: { filing: 3 } };

beforeEach(() => {
  vi.stubGlobal('window', { sessionStorage: new MemoryStorage() });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('the errand through ID-porten', () => {
  it('comes back once, to the page and company that left it', () => {
    expect(rememberErrand(RECEIPT, NOW)).toBe(true);
    expect(takeErrand('/mva/', 8, NOW + 30_000)).toEqual({ ...RECEIPT, at: NOW });
    expect(takeErrand('/mva/', 8, NOW + 31_000)).toBeNull();
  });

  it('is not for another company or another page, and is gone after asking', () => {
    rememberErrand(RECEIPT, NOW);
    expect(takeErrand('/mva/', 9, NOW)).toBeNull();
    expect(takeErrand('/mva/', 8, NOW)).toBeNull();
    rememberErrand(RECEIPT, NOW);
    expect(takeErrand('/skattemelding/', 8, NOW)).toBeNull();
  });

  it('is too old once a login attempt could not still be coming back', () => {
    rememberErrand(RECEIPT, NOW);
    expect(takeErrand('/mva/', 8, NOW + ERRAND_LIFETIME_MS + 1)).toBeNull();
  });

  it('is dropped when the trip back did not log in', () => {
    rememberErrand(RECEIPT, NOW);
    forgetErrand();
    expect(takeErrand('/mva/', 8, NOW)).toBeNull();
  });

  it('says so when the browser will not keep it', () => {
    vi.stubGlobal('window', {
      get sessionStorage(): Storage {
        throw new Error('SecurityError');
      },
    });
    expect(rememberErrand(RECEIPT, NOW)).toBe(false);
    expect(takeErrand('/mva/', 8, NOW)).toBeNull();
  });
});

describe('needsGovernmentLogin', () => {
  it('is a 409 without a code, and no other refusal', () => {
    expect(needsGovernmentLogin(new ApiError(409, 'logg inn med ID-porten', null))).toBe(true);
    expect(needsGovernmentLogin(new ApiError(409, 'sendt', 'nothing_new_to_file'))).toBe(false);
    expect(needsGovernmentLogin(new ApiError(502, 'svarte ikke', null))).toBe(false);
    expect(needsGovernmentLogin(new Error('nettverk'))).toBe(false);
  });
});
