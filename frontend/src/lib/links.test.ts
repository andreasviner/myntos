/**
 * The token in a mailed link lives in the fragment, and reading the query
 * string instead would make every link look invalid without an error anywhere.
 */

import { describe, expect, it } from 'vitest';

import { tokenFromFragment } from './links';

describe('tokenFromFragment', () => {
  it('reads the token from the fragment', () => {
    expect(tokenFromFragment('#token=abc_DEF-123')).toBe('abc_DEF-123');
  });

  it('accepts a fragment without its hash sign', () => {
    expect(tokenFromFragment('token=abc')).toBe('abc');
  });

  it('answers null for no fragment, an empty token and an unrelated fragment', () => {
    expect(tokenFromFragment('')).toBeNull();
    expect(tokenFromFragment('#token=')).toBeNull();
    expect(tokenFromFragment('#innhold')).toBeNull();
  });

  it('trims whitespace a mail client wrapped onto the link', () => {
    expect(tokenFromFragment('#token=abc%20')).toBe('abc');
  });
});
