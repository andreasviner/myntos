import { describe, expect, it } from 'vitest';

import { normaliseAmount } from './amount';

describe('normaliseAmount', () => {
  it('reads Norwegian amounts as decimal strings without making numbers of them', () => {
    expect(normaliseAmount('1 234,56')).toBe('1234.56');
    expect(normaliseAmount('1 234,5')).toBe('1234.5');
    expect(normaliseAmount('1.234,56')).toBe('1234.56');
    expect(normaliseAmount('1,234.56')).toBe('1234.56');
    expect(normaliseAmount('30000')).toBe('30000');
    expect(normaliseAmount('-500,00')).toBe('-500.00');
  });

  it('treats an empty field as nothing on that side', () => {
    expect(normaliseAmount('  ')).toBe('0');
  });

  it('refuses rather than guesses', () => {
    expect(normaliseAmount('12a')).toBeNull();
    expect(normaliseAmount('1,2,3')).toBeNull();
    // An øre has two decimals; a third is a typo, not a rounding job.
    expect(normaliseAmount('1,005')).toBeNull();
  });

  it('takes more decimals when asked, for kilos and values per kilo', () => {
    expect(normaliseAmount('2 500,125', 3)).toBe('2500.125');
    expect(normaliseAmount('0,45', 4)).toBe('0.45');
    expect(normaliseAmount('1,00005', 4)).toBeNull();
  });
});
