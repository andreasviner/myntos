import { describe, expect, it } from 'vitest';

import { netOf } from './lines';

describe('netOf', () => {
  it('nets a milk settlement to what the bank received', () => {
    const net = netOf([
      { amount: '12 500,00', direction: 'inn' },
      { amount: '100', direction: 'ut' },
      { amount: '600,00', direction: 'ut' },
      { amount: '5000.00', direction: 'ut' },
    ]);
    expect(net).toEqual({ amount: '6800.00', direction: 'inn', unreadable: [] });
  });

  it('is exact to the øre where a float is not', () => {
    const net = netOf([
      { amount: '0,10', direction: 'inn' },
      { amount: '0,20', direction: 'inn' },
    ]);
    expect(net.amount).toBe('0.30');
  });

  it('goes out of the bank when the deductions are larger', () => {
    const net = netOf([
      { amount: '1000', direction: 'inn' },
      { amount: '2500', direction: 'ut' },
    ]);
    expect(net).toEqual({ amount: '1500.00', direction: 'ut', unreadable: [] });
  });

  it('says when the lines cancel, which the backend refuses', () => {
    expect(netOf([
      { amount: '500', direction: 'inn' },
      { amount: '500,00', direction: 'ut' },
    ]).direction).toBeNull();
  });

  it('skips a line not filled in yet and names one it cannot read', () => {
    const net = netOf([
      { amount: '100', direction: 'inn' },
      { amount: '', direction: 'ut' },
      { amount: '12,345', direction: 'ut' },
      { amount: '50', direction: null },
    ]);
    expect(net).toEqual({ amount: '100.00', direction: 'inn', unreadable: [2] });
  });
});
