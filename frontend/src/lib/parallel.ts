/**
 * Run a task over a list, a few at a time, and keep every outcome.
 *
 * Written for attaching a bilag's files after the entry is posted. Those went
 * one at a time, and ten screenshots of an AliExpress order took eight seconds
 * behind a button that said nothing: each upload is a dozen round trips on the
 * server (session, membership, voucher, the R2 write, the insert), so the
 * wait was latency stacked ten deep, not bandwidth. Four at once overlaps it.
 *
 * Settled rather than failed-fast, because one rejected file must not stop the
 * others from being archived, and the page has to be able to name exactly
 * which ones did not stick.
 */

/**
 * Call `task` for every item, never more than `limit` in flight, and resolve
 * with each outcome at the index of the item it belongs to.
 *
 * `onSettled` is told how many have finished, whichever finished, so a
 * progress line can count up without caring about order.
 */
export async function settleLimited<T, R>(
  items: readonly T[],
  limit: number,
  task: (item: T, index: number) => Promise<R>,
  onSettled?: (done: number) => void,
): Promise<PromiseSettledResult<R>[]> {
  const results: PromiseSettledResult<R>[] = new Array<PromiseSettledResult<R>>(items.length);
  let next = 0;
  let done = 0;

  const worker = async (): Promise<void> => {
    while (next < items.length) {
      const index = next;
      next += 1;
      try {
        results[index] = { status: 'fulfilled', value: await task(items[index] as T, index) };
      } catch (reason: unknown) {
        results[index] = { status: 'rejected', reason };
      }
      done += 1;
      onSettled?.(done);
    }
  };

  // At least one worker, or a limit of zero would resolve with nothing done.
  const workers = Math.min(Math.max(1, Math.floor(limit)), items.length);
  await Promise.all(Array.from({ length: workers }, worker));
  return results;
}
