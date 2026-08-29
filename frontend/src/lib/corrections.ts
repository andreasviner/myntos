/**
 * Reading a register back as events rather than as vouchers.
 *
 * Correcting a posted entry leaves three vouchers where one payment happened:
 * the original, the reversal that cancels it, and the replacement. Reversing an
 * entry outright leaves two. All of them stay in the ledger permanently, which
 * is bokføringsloven working as intended, but a register that lists them side
 * by side makes one purchase look like three, and shows the reversal of a
 * purchase as money coming in.
 *
 * So the listing folds them: one row per event, with the vouchers it consumed
 * kept underneath and one click away. Nothing is hidden from the record and
 * nothing is posted differently. It is only the table that stops counting the
 * same payment three times.
 *
 * The links come from the database, never from matching dates and amounts.
 * ``corrects_id`` runs from a replacement to what it replaced, and
 * ``reversed_by_id`` from an entry to the voucher that undid it. A guess from
 * dates and sums would eventually fold two genuine payments to the same
 * supplier on the same day into a correction that never happened, and then the
 * register would be lying about the books rather than merely cluttered.
 */

import type { CashFlowEntry } from './api';

/** One line in the register, and the vouchers folded underneath it. */
export interface RegisterRow {
  /** The entry that stands: the correction, or the entry that was reversed. */
  lead: CashFlowEntry;
  /** What it consumed, newest first. Empty for an ordinary payment. */
  history: CashFlowEntry[];
}

/**
 * Group a register listing into one row per event.
 *
 * Order is preserved: a row appears where its lead appeared. A correction is
 * newer than what it replaced, so folding lifts the whole group to the top of
 * the list and leaves nothing behind further down.
 *
 * Only vouchers present in ``entries`` are folded. A listing cut off at 200
 * rows can hold a reversal whose original fell outside it, and that reversal
 * stays as its own line rather than vanishing into a group that is not shown.
 */
export function foldCorrections(entries: CashFlowEntry[]): RegisterRow[] {
  const byId = new Map(entries.map((entry) => [entry.id, entry]));
  const history = new Map<number, CashFlowEntry[]>();
  const folded = new Set<number>();

  const reversalOf = (entry: CashFlowEntry): CashFlowEntry | undefined =>
    entry.reversed_by_id === null ? undefined : byId.get(entry.reversed_by_id);

  // 1. Corrections, following corrects_id back through however many rounds of
  //    editing there have been. Each step takes the entry that was replaced
  //    and the reversal that cancelled it.
  for (const entry of entries) {
    if (entry.corrects_id === null) continue;
    const trail: CashFlowEntry[] = [];
    // Not paranoia alone: a link that pointed in a circle would otherwise hang
    // the tab, and a loop that cannot stop is worse than a row without history.
    const seen = new Set<number>([entry.id]);
    let at = byId.get(entry.corrects_id);
    while (at !== undefined && !seen.has(at.id)) {
      seen.add(at.id);
      trail.push(at);
      const undo = reversalOf(at);
      if (undo !== undefined && !seen.has(undo.id)) {
        seen.add(undo.id);
        trail.push(undo);
      }
      at = at.corrects_id === null ? undefined : byId.get(at.corrects_id);
    }
    if (trail.length === 0) continue;
    history.set(entry.id, trail);
    for (const older of trail) folded.add(older.id);
  }

  // 2. Reversals no correction accounted for. Two cases reach here and both
  //    want the same thing: a payment reversed outright because it should
  //    never have been recorded, and an edit made before the correction link
  //    existed, whose replacement therefore has nothing pointing back. Either
  //    way the entry and its reversal are one event and cancel to nothing.
  for (const entry of entries) {
    if (folded.has(entry.id)) continue;
    const undo = reversalOf(entry);
    if (undo === undefined || folded.has(undo.id)) continue;
    history.set(entry.id, [undo, ...(history.get(entry.id) ?? [])]);
    folded.add(undo.id);
  }

  const rows: RegisterRow[] = [];
  for (const entry of entries) {
    if (folded.has(entry.id)) continue;
    rows.push({ lead: entry, history: history.get(entry.id) ?? [] });
  }
  return rows;
}

/** Every voucher a row stands for, the visible one included. */
export function vouchersIn(row: RegisterRow): CashFlowEntry[] {
  return [row.lead, ...row.history];
}

/**
 * Whether the payment a row stands for is documented.
 *
 * The bilag may hang off any voucher in the group, and after an edit it
 * usually hangs off the one that was reversed: the archive is append-only, so
 * correcting an entry cannot move the PDF onto the replacement. It documents
 * the purchase either way, and the group is what the purchase is.
 */
export function hasDocument(row: RegisterRow): boolean {
  return vouchersIn(row).some((entry) => entry.attachments.length > 0);
}

/**
 * The sentence under a folded row, or null when there is nothing to explain.
 *
 * Two different things get folded and they do not mean the same thing. A
 * correction leaves a payment that stands; a bare reversal leaves one that
 * does not, and saying so is the difference between a row you can trust and a
 * row you have to open to understand.
 */
export function foldNote(row: RegisterRow): string | null {
  if (row.history.length === 0) return null;
  const reversal = row.history.find((entry) => entry.is_reversal);
  if (row.lead.reversed_by_id !== null) {
    return reversal === undefined
      ? 'Motpostert. Går i null.'
      : `Motpostert av bilag ${reversal.voucher_no}. Går i null.`;
  }
  // "Bilag" is the same in the singular and the plural, so no count agreement.
  // The triangle already says there is something to open, so the sentence only
  // has to say what happened and how much of it there is.
  return `Rettet. ${row.history.length} eldre bilag.`;
}
