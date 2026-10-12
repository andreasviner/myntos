/**
 * The arithmetic of a composite entry: what its lines add up to at the bank.
 *
 * A settlement from the dairy is milk in, less the levy, the share and the feed
 * out, and the page shows what that nets to while it is being typed, so the
 * person can hold it against the bank statement before posting. The backend
 * decides what is posted; this only has to agree with it to the øre.
 *
 * In whole øre as BigInt, never as a float, for the reason lib/amount.ts gives:
 * 0.1 + 0.2 is not 0.3, and a sum shown an øre off from the bank line reads as
 * a settlement that does not add up. Amounts arrive as the strings the inputs
 * hold and leave as the strings the API reads.
 */

import { normaliseAmount } from './amount';

export type Direction = 'inn' | 'ut';

export interface Line {
  /** What the person typed, Norwegian or English decimal mark. */
  amount: string;
  /** The direction of the line's category; null while none is chosen. */
  direction: Direction | null;
}

export interface Net {
  /** The bank movement, unsigned, as a decimal string ("6800.00"). */
  amount: string;
  /** Which way it goes, or null when the lines cancel out. */
  direction: Direction | null;
  /** Lines whose amount could not be read, by index. */
  unreadable: number[];
}

function toOre(text: string): bigint {
  const [kroner = '0', ore = ''] = text.split('.');
  return BigInt(kroner) * 100n + BigInt(ore.padEnd(2, '0'));
}

function fromOre(ore: bigint): string {
  const whole = ore / 100n;
  const rest = ore % 100n;
  return `${whole.toString()}.${rest.toString().padStart(2, '0')}`;
}

/** What the lines net to: money in adds, money out subtracts. */
export function netOf(lines: Line[]): Net {
  let total = 0n;
  const unreadable: number[] = [];
  lines.forEach((line, index) => {
    if (line.amount.trim() === '' || line.direction === null) return;
    const normalised = normaliseAmount(line.amount);
    if (normalised === null || normalised.startsWith('-')) {
      unreadable.push(index);
      return;
    }
    const ore = toOre(normalised);
    total += line.direction === 'inn' ? ore : -ore;
  });
  const direction: Direction | null = total > 0n ? 'inn' : total < 0n ? 'ut' : null;
  return { amount: fromOre(total < 0n ? -total : total), direction, unreadable };
}
