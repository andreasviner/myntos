/**
 * The levels of sharing with Myntos, as the choice and the explaining page show them.
 *
 * The order and the red mark are what a person relies on: the levels must run
 * from nothing to everything, the default must be nothing, and the one level
 * that is not anonymised must be the one drawn in red.
 */

import { describe, expect, it } from 'vitest';

import { LEVELS, currentText, levelOf } from './datasharing';

describe('LEVELS', () => {
  it('runs from nothing to everything, in the order the backend compares them', () => {
    expect(LEVELS.map((level) => level.value)).toEqual([
      'none',
      'usage',
      'anonymised',
      'everything',
    ]);
  });

  it('marks only the level that shares the bilag themselves as dangerous', () => {
    expect(LEVELS.filter((level) => level.danger).map((level) => level.value)).toEqual([
      'everything',
    ]);
  });

  it('says for every level what it shares and what it keeps', () => {
    for (const level of LEVELS.slice(1)) {
      expect(level.shares.length).toBeGreaterThan(0);
      expect(level.keeps.length).toBeGreaterThan(0);
    }
    expect(LEVELS[0]?.shares).toEqual([]);
  });

  it('shares only the failures the browser sees, since the server keeps its own anyway', () => {
    const usage = LEVELS.find((level) => level.value === 'usage');
    expect(usage?.shares.some((line) => line.includes('feiler i nettleseren'))).toBe(true);
  });

  it('never promises the top level trains a model', () => {
    const top = LEVELS.at(-1);
    expect(top?.keeps.some((line) => line.includes('ikke til å trene'))).toBe(true);
  });
});

describe('levelOf', () => {
  it('reads an unknown or missing level as nothing', () => {
    expect(levelOf(undefined).value).toBe('none');
    expect(levelOf('something-retired').value).toBe('none');
    expect(levelOf('usage').label).toBe('Bruksstatistikk');
  });
});

describe('currentText', () => {
  it('names the company and says what it shares', () => {
    expect(currentText('Nordmann AS', 'none')).toBe('Nordmann AS deler ingenting med Myntos.');
    expect(currentText('Nordmann AS', 'usage')).toBe(
      'Nordmann AS deler bruksstatistikk med Myntos.',
    );
    expect(currentText('Nordmann AS', 'everything')).toContain('også bilagene');
  });
});
