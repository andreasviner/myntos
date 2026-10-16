/**
 * The accounts pivoted into rows, and the overview's "where do the books stop".
 *
 * See regnskap.ts for why these are asserted at all: both fail without a sound.
 */

import { describe, expect, it } from 'vitest';

import type { ReportLine, ReportYear, Summary } from './api';
import { booksBehind, rowsOf, yearNote } from './regnskap';

function line(key: string, amount: string, accounts: [string, string][] = []): ReportLine {
  return {
    key,
    label: key,
    kind: accounts.length === 0 ? 'sum' : 'gruppe',
    amount,
    accounts: accounts.map(([number, value]) => ({
      account_number: number,
      description: `Konto ${number}`,
      amount: value,
    })),
  };
}

function year(value: number, resultat: ReportLine[], extra: Partial<ReportYear> = {}): ReportYear {
  return {
    year: value,
    start_date: `${value}-01-01`,
    end_date: `${value}-12-31`,
    closed: false,
    voucher_count: 1,
    last_entry: `${value}-12-31`,
    resultat,
    balanse: [],
    ...extra,
  };
}

describe('rowsOf', () => {
  it('keeps an account that only one year has', () => {
    const rows = rowsOf(
      [
        year(2026, [line('salgsinntekter', '100.00', [['3000', '100.00']])]),
        year(2025, [
          line('salgsinntekter', '250.00', [
            ['3000', '200.00'],
            ['3100', '50.00'],
          ]),
        ]),
      ],
      'resultat',
    );
    expect(rows).toHaveLength(1);
    const [sales] = rows;
    expect(sales?.amounts.get(2026)).toBe('100.00');
    expect(sales?.amounts.get(2025)).toBe('250.00');
    expect(sales?.accounts.map((a) => a.number)).toEqual(['3000', '3100']);
    expect(sales?.accounts[1]?.amounts.has(2026)).toBe(false);
    expect(sales?.accounts[1]?.amounts.get(2025)).toBe('50.00');
  });

  it('keeps the order the backend gave', () => {
    const rows = rowsOf(
      [year(2025, [line('driftsinntekter', '1'), line('driftsresultat', '1'), line('arsresultat', '1')])],
      'resultat',
    );
    expect(rows.map((r) => r.key)).toEqual(['driftsinntekter', 'driftsresultat', 'arsresultat']);
  });
});

describe('yearNote', () => {
  const format = (iso: string): string => iso;

  it('says a year is empty, closed, or how far it goes', () => {
    expect(yearNote(year(2026, [], { last_entry: null }), format)).toBe('ingen bilag');
    expect(yearNote(year(2024, [], { closed: true }), format)).toBe('avsluttet');
    expect(yearNote(year(2026, [], { last_entry: '2026-10-14' }), format)).toBe('ført til 2026-10-14');
    expect(yearNote(year(2025, []), format)).toBe('ikke avsluttet');
  });
});

describe('booksBehind', () => {
  function summary(financialYear: number, through: string | null, asOf: string | null): Summary {
    return {
      financial_year: financialYear,
      total_in: '0.00',
      total_out: '0.00',
      net: '0.00',
      entry_count: 0,
      output_vat: '0.00',
      input_vat: '0.00',
      vat_position: '-303471.00',
      vat_settled: '-303471.00',
      cash_position: '0.00',
      books_through: through,
      positions_as_of: asOf,
      by_type: [],
    };
  }

  it('says nothing on 1 January, when last year still counts', () => {
    // The books end on New Year's Eve; the backend has not called them behind.
    expect(booksBehind(summary(2027, '2026-12-31', null))).toBeNull();
  });

  it('says where the books stop once the backend calls them behind', () => {
    expect(booksBehind(summary(2026, '2025-12-31', '2025-12-31'))).toEqual({
      through: '2025-12-31',
      emptyYear: true,
    });
    expect(booksBehind(summary(2026, '2026-03-14', '2026-03-14'))).toEqual({
      through: '2026-03-14',
      emptyYear: false,
    });
  });

  it('is nothing for empty books', () => {
    expect(booksBehind(summary(2026, null, null))).toBeNull();
  });
});
