/**
 * `?neste=` is read after a login, so a value that leaves the site sends a
 * person who has just signed in on the real page to a look-alike asking them to
 * sign in again.
 */

import { describe, expect, it } from 'vitest';

import { safeNext } from './session';

describe('safeNext', () => {
  it('keeps a page of our own, with its query and anchor', () => {
    expect(safeNext('/bilag/')).toBe('/bilag/');
    expect(safeNext('/invitasjon/?token=abc#svar')).toBe('/invitasjon/?token=abc#svar');
  });

  it('sends anything else to the front page', () => {
    expect(safeNext(null)).toBe('/');
    expect(safeNext('')).toBe('/');
    expect(safeNext('https://evil.example/')).toBe('/');
    expect(safeNext('//evil.example/')).toBe('/');
    expect(safeNext('javascript:alert(1)')).toBe('/');
  });

  it('is not fooled by what the URL parser reads as a second slash', () => {
    // Each of these starts with a single slash and still lands on evil.example.
    expect(safeNext('/\\evil.example/')).toBe('/');
    expect(safeNext('/\\/evil.example/')).toBe('/');
    expect(safeNext('/\t/evil.example/')).toBe('/');
    expect(safeNext('/\n/evil.example/')).toBe('/');
    expect(safeNext('/\r/evil.example/')).toBe('/');
  });
});

/**
 * Every page's source, as Vite reads it (see indexing.test.ts for the same
 * grep). `?raw` hands over the text without compiling the `.astro`.
 */
const pageSources = import.meta.glob<string>('../pages/**/*.astro', {
  query: '?raw',
  import: 'default',
  eager: true,
});

describe('can', () => {
  it('is asked only whether a company may be added to an account', () => {
    // «Faktura», «Kontakter» and «Betaling» asked the account whether the caller
    // could write in the books, and a colleague invited to one company holds no
    // role in the account that owns it: an «Eier» was shown «Du har lesetilgang»
    // on all three (2026-10-23). Anything about a set of books is canInCompany.
    const calls = Object.entries(pageSources).flatMap(([file, source]) =>
      [...source.matchAll(/\bcan\(\s*[^,]+,[^,]+,\s*'([^']+)'\s*\)/g)].map(
        (match) => `${file}: ${match[1]}`,
      ),
    );
    // If this ever finds nothing, the grep is broken, not the pages.
    expect(calls.length).toBeGreaterThan(0);
    expect(calls.filter((call) => !call.endsWith(': organization:manage'))).toEqual([]);
  });
});
