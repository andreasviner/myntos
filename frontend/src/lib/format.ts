/**
 * Norwegian formatting for money and dates.
 *
 * Amounts arrive from the API as decimal *strings* and are only turned into
 * numbers here, at the last possible moment, purely to be displayed. Nothing in
 * this file ever computes with them: a ledger total recalculated in JavaScript
 * would be a second source of truth, and a float one at that.
 */

const KRONER = new Intl.NumberFormat('nb-NO', {
  style: 'currency',
  currency: 'NOK',
  minimumFractionDigits: 2,
});

const DATE = new Intl.DateTimeFormat('nb-NO', { day: '2-digit', month: 'short', year: 'numeric' });

export function kroner(amount: string): string {
  const value = Number(amount);
  return Number.isFinite(value) ? KRONER.format(value) : `${amount} kr`;
}

/** Formats an ISO date (`2026-03-14`) without letting the timezone shift it. */
export function isoDate(value: string): string {
  const [year, month, day] = value.split('-').map(Number);
  if (year === undefined || month === undefined || day === undefined) return value;
  return DATE.format(new Date(year, month - 1, day));
}

/** Today as `yyyy-mm-dd`, in the browser's own calendar. */
export function todayIso(): string {
  const now = new Date();
  const month = `${now.getMonth() + 1}`.padStart(2, '0');
  const day = `${now.getDate()}`.padStart(2, '0');
  return `${now.getFullYear()}-${month}-${day}`;
}

/**
 * A Norwegian account number as it is read: `1506.20.33158`.
 *
 * Stored and sent as eleven bare digits — the grouping is presentation only, and
 * it is the same grouping the invoice PDF uses. Anything that is not eleven
 * digits comes back untouched: a value stored before the format was enforced
 * should be shown as it is rather than cut into a shape it does not have.
 */
export function kontonummer(value: string): string {
  const digits = value.replace(/\D+/g, '');
  if (digits.length !== 11) return value;
  return `${digits.slice(0, 4)}.${digits.slice(4, 6)}.${digits.slice(6)}`;
}

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} kB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
