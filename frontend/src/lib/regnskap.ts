/**
 * The accounts year by year (`/regnskap/`), and where the books stop.
 *
 * Both are here rather than in the pages because the mistakes they guard
 * against are quiet ones. A row pivoted from one year's lines alone drops an
 * account the other years have, and the columns stop adding up without
 * anything on screen saying so. And «where the books stop» is what tells a
 * company that brought only last year over that its «0,00» this year means
 * "not brought in yet", not "nothing happened"; got wrong, the overview calls
 * a refund paid out in February «til gode» in October.
 */

import type { ReportLine, ReportYear, Summary } from './api';

export type Statement = 'resultat' | 'balanse';

/** One account across the years, with an amount for each year that has it. */
export interface AccountRow {
  number: string;
  description: string;
  amounts: Map<number, string>;
}

/** One line of a statement across the years. */
export interface ReportRow {
  key: string;
  label: string;
  kind: ReportLine['kind'];
  amounts: Map<number, string>;
  accounts: AccountRow[];
}

/**
 * The statement's lines as rows, one amount per year.
 *
 * The backend lists the same lines in the same order in every year, so the
 * order is the first year's; the accounts under a line are the union of every
 * year's, by number, because an account used in one year and not another
 * still belongs in the row.
 */
export function rowsOf(years: ReportYear[], statement: Statement): ReportRow[] {
  const rows = new Map<string, ReportRow>();
  for (const year of years) {
    for (const line of year[statement]) {
      let row = rows.get(line.key);
      if (row === undefined) {
        row = { key: line.key, label: line.label, kind: line.kind, amounts: new Map(), accounts: [] };
        rows.set(line.key, row);
      }
      row.amounts.set(year.year, line.amount);
      for (const account of line.accounts) {
        let found = row.accounts.find((a) => a.number === account.account_number);
        if (found === undefined) {
          found = { number: account.account_number, description: account.description, amounts: new Map() };
          row.accounts.push(found);
        }
        found.amounts.set(year.year, account.amount);
      }
    }
  }
  for (const row of rows.values()) row.accounts.sort((a, b) => a.number.localeCompare(b.number));
  return [...rows.values()];
}

/**
 * The short word under a year's heading: what state the year is in.
 *
 * `formatDate` is passed in so this stays a pure function of its inputs.
 */
export function yearNote(year: ReportYear, formatDate: (iso: string) => string): string {
  if (year.last_entry === null) return 'ingen bilag';
  if (year.closed) return 'avsluttet';
  if (year.last_entry < year.end_date) return `ført til ${formatDate(year.last_entry)}`;
  return 'ikke avsluttet';
}

/** Where the books stop, when they stop far enough back that the overview must say so. */
export interface BooksBehind {
  /** The date of the last voucher in the books. */
  through: string;
  /** Nothing at all is posted in the year on screen. */
  emptyYear: boolean;
}

/**
 * Whether the overview's positions are as of an earlier day than today.
 *
 * The backend decides (`positions_as_of`): a VAT deadline has passed since the
 * last voucher. Not the turn of the year, which is what this used to compare:
 * on 1 January last year's final term is still open and its figure is real,
 * and the overview called a company that posts every week «behind» from the
 * first minute of the year.
 */
export function booksBehind(summary: Summary): BooksBehind | null {
  const through = summary.positions_as_of;
  if (through === null) return null;
  return { through, emptyYear: through < `${summary.financial_year}-01-01` };
}
