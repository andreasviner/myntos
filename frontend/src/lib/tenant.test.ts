/**
 * The record that decides which company every page is about.
 *
 * These are the tests the frontend did not have when one company's name was
 * painted above another company's figures. Each one names the failure it is
 * standing in front of, because the module is small enough that a test which
 * only restates the implementation would be worth nothing.
 *
 * `lib/tenant.ts` decides its answer once per page load and caches it, so every
 * test imports the module fresh: `vi.resetModules()` plus a dynamic import is
 * what a new page load looks like from inside the module.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Company } from './api';

/** A `localStorage` a test can read, write and break on purpose. */
function fakeStorage(initial: Record<string, string> = {}) {
  const items = new Map(Object.entries(initial));
  return {
    items,
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => void items.set(key, value),
    removeItem: (key: string) => void items.delete(key),
    clear: () => items.clear(),
    key: (index: number) => [...items.keys()][index] ?? null,
    get length() {
      return items.size;
    },
  };
}

type Store = ReturnType<typeof fakeStorage>;

/** A fresh module, the way a page load gets one, over the given storage. */
async function load(store: Store) {
  vi.stubGlobal('window', { localStorage: store });
  vi.resetModules();
  return import('./tenant');
}

/** Only the fields the record is built from; the rest never reaches storage. */
function company(id: number, name: string, verified = true): Company {
  return { id, name, verified } as Company;
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe('reading the stored choice', () => {
  it('reads back a record it wrote', async () => {
    const store = fakeStorage();
    const tenant = await load(store);
    tenant.setActiveCompany(company(12, 'DIALGA AS'));

    const reloaded = await load(store);
    expect(reloaded.activeCompany()).toEqual({ id: 12, name: 'DIALGA AS', verified: true });
    expect(reloaded.activeCompanyId()).toBe(12);
  });

  it('keeps the id but paints no name for a value written before the record existed', async () => {
    // The key used to hold a bare id. It parses as a *number* rather than
    // throwing, which is why this is a branch in `parse` and not a catch, and
    // it was a real bug in the first draft of that function: the choice was
    // silently dropped and every existing browser jumped to its first company.
    const tenant = await load(fakeStorage({ 'myntos.company': '12' }));
    expect(tenant.activeCompany()).toEqual({ id: 12, name: '', verified: false });
  });

  it('refuses nonsense rather than throwing on it', async () => {
    // Whatever an older version wrote, or whatever somebody typed into their
    // own devtools. A header that fills in late beats a header that throws.
    for (const raw of ['not json', 'null', '{}', '{"id":0,"name":"x"}', '{"id":3}']) {
      const tenant = await load(fakeStorage({ 'myntos.company': raw }));
      expect(tenant.activeCompany(), raw).toBeNull();
    }
  });

  it('survives a browser that refuses storage', async () => {
    // Private-mode Safari throws on access, not on use.
    vi.stubGlobal('window', {
      get localStorage(): Storage {
        throw new Error('nope');
      },
    });
    vi.resetModules();
    const tenant = await import('./tenant');
    expect(tenant.activeCompany()).toBeNull();
    expect(() => tenant.setActiveCompany(company(1, 'Lindeman Technologies AS'))).not.toThrow();
  });
});

describe('the id and the name are one value', () => {
  it('writes both from one Company, so neither can be set without the other', async () => {
    // The bug this module exists for: /nytt-selskap/ stored the new company's
    // id and left the previous company's name in place, so the next page load
    // painted one company's name above another company's figures. There is no
    // longer an entry point that takes an id.
    const store = fakeStorage();
    const tenant = await load(store);
    tenant.setActiveCompany(company(7, 'Klient To AS', false));

    const stored: unknown = JSON.parse(store.getItem('myntos.company') ?? 'null');
    expect(stored).toEqual({ id: 7, name: 'Klient To AS', verified: false });
  });

  it('drops the name but keeps the id when the session turns out to be gone', async () => {
    // /auth/me answers the same way for an expired cookie and an unreachable
    // API. Keeping the name would show the previous account's company to the
    // next person to sign in; dropping the id would send a regnskapsfører back
    // to their first client every time the API hiccups.
    const store = fakeStorage();
    const tenant = await load(store);
    tenant.setActiveCompany(company(12, 'DIALGA AS'));

    tenant.forgetActiveCompanyName();

    expect(tenant.activeCompany()).toEqual({ id: 12, name: '', verified: false });
    const reloaded = await load(store);
    expect(reloaded.activeCompany()).toEqual({ id: 12, name: '', verified: false });
  });

  it('forgets everything on sign-out', async () => {
    const store = fakeStorage();
    const tenant = await load(store);
    tenant.setActiveCompany(company(12, 'DIALGA AS'));

    tenant.clearActiveCompany();

    expect(tenant.activeCompany()).toBeNull();
    expect(store.getItem('myntos.company')).toBeNull();
  });
});

describe('a second tab', () => {
  it('cannot move this page onto another company', async () => {
    // Two clients open in two tabs is an ordinary Tuesday. The page that is
    // already drawn keeps the company it was drawn for: following the other
    // tab would leave half of one company's figures under the other's name.
    const store = fakeStorage();
    const tenant = await load(store);
    tenant.setActiveCompany(company(12, 'DIALGA AS'));

    store.setItem(
      'myntos.company',
      JSON.stringify({ id: 99, name: 'Lindeman Technologies AS', verified: true }),
    );

    expect(tenant.activeCompanyId()).toBe(12);
    expect(tenant.activeCompany()?.name).toBe('DIALGA AS');
  });

  it('is noticed, so the page can say so instead of pretending', async () => {
    const store = fakeStorage();
    const tenant = await load(store);
    tenant.setActiveCompany(company(12, 'DIALGA AS'));
    expect(tenant.activeCompanyMovedElsewhere()).toBe(false);

    store.setItem('myntos.company', JSON.stringify({ id: 99, name: 'Annet AS', verified: true }));
    expect(tenant.activeCompanyMovedElsewhere()).toBe(true);

    // A rename of the same company is not a switch, and must not raise a notice
    // telling the reader to reload for no reason.
    store.setItem('myntos.company', JSON.stringify({ id: 12, name: 'DIALGA AS', verified: false }));
    expect(tenant.activeCompanyMovedElsewhere()).toBe(false);
  });
});
