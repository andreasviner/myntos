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
