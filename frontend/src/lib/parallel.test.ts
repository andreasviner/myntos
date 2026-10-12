/**
 * The helper that attaches a bilag's files after the entry is posted.
 *
 * What is asserted is what the page relies on: outcomes come back in the
 * order the files were chosen even though they finish in any order, one
 * failure does not cost the rest, and the limit is a limit.
 */

import { describe, expect, it } from 'vitest';

import { settleLimited } from './parallel';

/** A promise that resolves when told to, so a test decides the finishing order. */
function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe('settleLimited', () => {
  it('keeps the order of the items, not the order they finished in', async () => {
    const gates = [deferred<string>(), deferred<string>(), deferred<string>()];
    const running = settleLimited([0, 1, 2], 3, (index) => gates[index]!.promise);
    gates[2]!.resolve('c');
    gates[0]!.resolve('a');
    gates[1]!.resolve('b');
    const results = await running;
    expect(results.map((result) => result.status === 'fulfilled' && result.value)).toEqual([
      'a',
      'b',
      'c',
    ]);
  });

  it('never has more than the limit in flight', async () => {
    let inFlight = 0;
    let peak = 0;
    await settleLimited(Array.from({ length: 10 }, (_, i) => i), 4, async () => {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      await new Promise((tick) => setTimeout(tick, 1));
      inFlight -= 1;
    });
    expect(peak).toBe(4);
  });

  it('carries on past a failure and says which one failed', async () => {
    const results = await settleLimited(['a.pdf', 'b.png', 'c.pdf'], 2, async (name) => {
      if (name === 'b.png') throw new Error('rejected');
      return name;
    });
    expect(results.map((result) => result.status)).toEqual(['fulfilled', 'rejected', 'fulfilled']);
  });

  it('counts every item that finishes, failed or not', async () => {
    const seen: number[] = [];
    await settleLimited(
      [1, 2, 3],
      2,
      async (n) => {
        if (n === 2) throw new Error('rejected');
      },
      (done) => seen.push(done),
    );
    expect(seen).toEqual([1, 2, 3]);
  });

  it('treats a limit below one as one, rather than doing nothing', async () => {
    const results = await settleLimited([1, 2], 0, async (n) => n * 2);
    expect(results).toEqual([
      { status: 'fulfilled', value: 2 },
      { status: 'fulfilled', value: 4 },
    ]);
  });

  it('resolves at once with nothing to do', async () => {
    expect(await settleLimited([], 4, async () => 1)).toEqual([]);
  });
});
