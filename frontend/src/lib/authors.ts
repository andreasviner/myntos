/**
 * Who entered a voucher, and when that is worth saying.
 *
 * Access to a set of books is a row per person per company, so a register is
 * frequently written by more than one hand: an owner books what they paid and a
 * regnskapsfører books everything else, or two people in the same office split
 * the month. The backend records the author on every voucher it posts
 * (`gl_transaction.created_by_user_id`), and this file decides what the table
 * does with it.
 *
 * The rule is that the mark appears **only when the register has more than one
 * author**. On a one-person set of books "Ført av Kari Nordmann" is on every
 * single row, says nothing, and costs a column's worth of attention on the
 * page a sole trader uses most. The moment a second name shows up it is the
 * fastest thing on the row to read, so it earns its place then and not before.
 *
 * "Not recorded" counts as one of the authors, deliberately. A register where
 * some entries are attributed and some are not has genuinely been written by
 * more than one hand, and hiding the mark there would be hiding the one thing
 * the reader needs: which rows nobody can be asked about.
 */

import type { CashFlowEntry } from './api';

/** What an unattributed entry is called. Never a name, and never blank. */
export const UNKNOWN_AUTHOR = 'Ukjent';

/**
 * Distinct authors in a listing, counting "not recorded" as one of them.
 *
 * Grouped by id and not by name: two people called Ola Nordmann on the same
 * books are two authors, and the whole point of the mark is telling them apart.
 *
 * A person and a key they made count as two hands. Kari typing an entry and
 * Kari's agent posting one are different answers to "who did this", and a
 * register written by both is exactly where the reader needs to tell which
 * rows came from the agent (docs/agents-and-api.md rule 6).
 */
export function authorCount(entries: CashFlowEntry[]): number {
  const seen = new Set<string>();
  for (const entry of entries) seen.add(`${entry.created_by_id ?? ''}|${entry.created_via ?? ''}`);
  return seen.size;
}

/** Whether the register should carry the mark at all. See the file comment. */
export function namesAuthors(entries: CashFlowEntry[]): boolean {
  return authorCount(entries) > 1;
}

/** The name to print for one entry. */
export function authorOf(entry: CashFlowEntry): string {
  // An empty string is treated as missing on purpose: a name is required at
  // signup, so a blank one means something went wrong upstream, and printing
  // nothing at all would read as though the row had no mark.
  const name = entry.created_by_name?.trim();
  const who = name === undefined || name === '' ? UNKNOWN_AUTHOR : name;
  // The person first and the key after, because the person is the one who
  // registered the entry in the law's sense; the key is how.
  const via = entry.created_via?.trim();
  return via === undefined || via === '' ? who : `${who} via «${via}»`;
}
