/**
 * What the operator's page, /drift/, needs that is worth a test (docs/operator.md):
 * keeping the operator key for the tab, telling the key from the digest pasted
 * by mistake, and writing a recorded failure so it reads at a glance.
 */

import type { ServerErrorEntry, ServerErrorFrame, ServerErrorStatus } from './api';

export const OPERATOR_KEY_PREFIX = 'myntos_op_';

// sessionStorage and not localStorage: the key opens every stranger's words,
// so it is kept for as long as the tab is open and no longer. Closing the tab,
// or «Lås», is the whole of signing out.
const STORAGE_KEY = 'myntos.operator-key';

function session(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    // Blocked storage (a private window, a strict setting): the page still
    // works, it asks for the key on every load.
    return null;
  }
}

export function storedKey(storage: Storage | null = session()): string | null {
  try {
    return storage?.getItem(STORAGE_KEY) ?? null;
  } catch {
    return null;
  }
}

export function keepKey(key: string, storage: Storage | null = session()): void {
  try {
    storage?.setItem(STORAGE_KEY, key);
  } catch {
    // As above: kept in memory for this load only.
  }
}

export function forgetKey(storage: Storage | null = session()): void {
  try {
    storage?.removeItem(STORAGE_KEY);
  } catch {
    // Nothing was kept, so nothing is left.
  }
}

export type KeyProblem = 'empty' | 'digest' | 'not-a-key';

/**
 * What is wrong with what was pasted, before it is sent anywhere.
 *
 * The digest is the likely mistake: it is the line in `.env`, the one the
 * operator saw last, and it looks just as random. Sending it would only get a
 * 401, which says nothing about why.
 */
export function keyProblem(input: string): KeyProblem | null {
  const key = input.trim();
  if (key === '') return 'empty';
  if (/^[0-9a-f]{64}$/i.test(key)) return 'digest';
  if (!key.startsWith(OPERATOR_KEY_PREFIX)) return 'not-a-key';
  return null;
}

export const KEY_PROBLEMS: Record<KeyProblem, string> = {
  empty: 'Lim inn driftsnøkkelen.',
  digest:
    'Dette er sjekksummen fra .env. Siden trenger selve nøkkelen, den som begynner med myntos_op_.',
  'not-a-key': 'En driftsnøkkel begynner med myntos_op_.',
};

export const ERROR_STATUS_LABELS: Record<ServerErrorStatus, string> = {
  open: 'Åpen',
  resolved: 'Løst',
};

export const SOURCE_LABELS: Record<'person' | 'agent', string> = {
  person: 'Person',
  agent: 'KI-agent',
};

/** `myntos.saft.jobs.ImportFailed` as `ImportFailed`: the module is in the frames anyway. */
export function shortType(qualified: string | null): string {
  if (qualified === null) return 'Logget uten unntak';
  return qualified.slice(qualified.lastIndexOf('.') + 1);
}

/** The frame to read first: the innermost one in Myntos's own code, else the innermost. */
export function ownFrame(frames: ServerErrorFrame[]): ServerErrorFrame | null {
  for (let index = frames.length - 1; index >= 0; index -= 1) {
    const frame = frames[index];
    if (frame !== undefined && frame.file.startsWith('myntos/')) return frame;
  }
  return frames.at(-1) ?? null;
}

export function frameLine(frame: ServerErrorFrame): string {
  return `${frame.file}:${frame.line} i ${frame.function}`;
}

export function timesLabel(count: number): string {
  return count === 1 ? '1 gang' : `${count.toLocaleString('nb-NO')} ganger`;
}

const MOMENT = new Intl.DateTimeFormat('nb-NO', { dateStyle: 'medium', timeStyle: 'short' });

/** An instant from the API, in the browser's own time. */
export function moment(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : MOMENT.format(parsed);
}

function utcMinute(epochMs: number): string {
  return new Date(epochMs).toISOString().slice(0, 16).replace('T', ' ');
}

/**
 * The command that finds the whole of the latest occurrence in the journal.
 *
 * The error log keeps the code and leaves the data out; the journal on the
 * machine has the message, the values and the request, so this is the way from
 * one to the other. UTC with the zone named, so the machine's own time zone
 * cannot shift the window.
 */
export function journalCommand(entry: Pick<ServerErrorEntry, 'last_seen_at'>): string {
  const last = new Date(entry.last_seen_at).getTime();
  const since = utcMinute(last - 60_000);
  const until = utcMinute(last + 120_000);
  return `journalctl -u myntos-backend --since "${since} UTC" --until "${until} UTC"`;
}
