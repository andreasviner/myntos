import { describe, expect, it } from 'vitest';

import type { TaxCode } from './api';
import { fitsSide, percent, taxCodeGroups, taxCodeLabel } from './taxcodes';

const CODES: TaxCode[] = [
  { code: '0', description: 'Ingen merverdiavgiftsbehandling', rate_percent: '0.000000', kind: null },
  { code: '1', description: 'Inngående mva, høy sats (25 %)', rate_percent: '25.000000', kind: 'inngaaende' },
  {
    code: '12',
    description: 'Inngående mva, kjøp av fisk og marine ressurser (11,11 %)',
    rate_percent: '11.110000',
    kind: 'inngaaende',
  },
  { code: '3', description: 'Utgående mva, høy sats (25 %)', rate_percent: '25.000000', kind: 'utgaaende' },
  { code: '5', description: 'Avgiftsfri omsetning (0 %)', rate_percent: '0.000000', kind: 'utgaaende' },
  { code: '6', description: 'Omsetning utenfor merverdiavgiftsloven', rate_percent: '0.000000', kind: 'utgaaende' },
  {
    code: '86',
    description: 'Tjenester kjøpt fra utlandet, høy sats (25 %), med fradragsrett',
    rate_percent: '25.000000',
    kind: 'omvendt',
  },
];

describe('the label', () => {
  it('puts the rate right after the code, where a narrow select still shows it', () => {
    expect(taxCodeLabel(CODES[1])).toBe('1: 25 % – Inngående mva, høy sats');
    expect(taxCodeLabel(CODES[2])).toBe('12: 11,11 % – Inngående mva, kjøp av fisk og marine ressurser');
    expect(taxCodeLabel(CODES[6])).toBe(
      '86: 25 % – Tjenester kjøpt fra utlandet, høy sats, med fradragsrett',
    );
  });

  it('keeps code 0 as the help articles quote it', () => {
    expect(taxCodeLabel(CODES[0])).toBe('0: Ingen merverdiavgiftsbehandling');
  });

  it('says 0 % for a zero-rated code but not for one outside VAT', () => {
    expect(taxCodeLabel(CODES[4])).toBe('5: 0 % – Avgiftsfri omsetning');
    expect(taxCodeLabel(CODES[5])).toBe('6: Omsetning utenfor merverdiavgiftsloven');
  });

  it('shows the rate even when the description does not', () => {
    expect(
      taxCodeLabel({ code: '3', description: 'Utgående mva høy sats', rate_percent: '25.00' }),
    ).toBe('3: 25 % – Utgående mva høy sats');
  });

  it('is the bare code for one kept only so a correction does not lose it', () => {
    expect(taxCodeLabel({ code: '20', description: '', rate_percent: '' })).toBe('20');
  });

  it('writes the rate the Norwegian way without making a number of it', () => {
    expect(percent('25.000000')).toBe('25');
    expect(percent('11.110000')).toBe('11,11');
    expect(percent('15')).toBe('15');
    expect(percent('0.000000')).toBe('0');
  });
});

describe('which side a code goes with', () => {
  it('gives a sale the output codes and a purchase the rest', () => {
    const sale = CODES.filter((code) => fitsSide(code.kind, 'inn')).map((code) => code.code);
    const purchase = CODES.filter((code) => fitsSide(code.kind, 'ut')).map((code) => code.code);
    expect(sale).toEqual(['0', '3', '5', '6']);
    expect(purchase).toEqual(['0', '1', '12', '86']);
  });

  it('offers everything before a category is chosen', () => {
    expect(CODES.every((code) => fitsSide(code.kind, null))).toBe(true);
  });

  it('fits a code without a kind to either side', () => {
    expect(fitsSide(undefined, 'inn') && fitsSide(null, 'ut')).toBe(true);
  });
});

describe('the groups', () => {
  it('puts 0 above them and keeps the melding order', () => {
    const groups = taxCodeGroups(CODES);
    expect(groups.map((group) => group.label)).toEqual([
      null,
      'Salg',
      'Kjøp',
      'Import og omvendt avgiftsplikt',
    ]);
    expect(groups[0].codes.map((code) => code.code)).toEqual(['0']);
    expect(groups[2].codes.map((code) => code.code)).toEqual(['1', '12']);
  });

  it('leaves out a group with nothing in it', () => {
    // What a company that is not VAT-registered gets: 0 and the reverse-charge
    // codes without a deduction.
    const groups = taxCodeGroups([CODES[0], { ...CODES[6], code: '87' }]);
    expect(groups.map((group) => group.label)).toEqual([null, 'Import og omvendt avgiftsplikt']);
  });
});
