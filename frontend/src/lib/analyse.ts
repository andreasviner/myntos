/**
 * The Analyse page's small decisions: which dates a period choice means, what
 * the line under each tile says, and what the page remembers between visits.
 * docs/analyse.md, «The Analyse page», points 2 and 3.
 *
 * Every figure comes from the backend. The one thing computed here is the
 * change from the comparison under each tile, and it is computed exactly, on
 * whole øre as BigInt, with the rounding `domain/analyse/sentences.py` uses
 * (half away from zero), so «+12 %» on a tile and «12 % mer» in the sentence
 * under it can never disagree by a rounding.
 *
 * With the company's KI on, the page also watches KI read the books in the
 * background (WP5b, point 6b): what the line above the pies says, and the
 * polling that tells the page when to ask for the figures again.
 */

import type { AnalysisPeriod, AnalysisQuery, AnalysisReading, AnalysisRun } from './api';
import { isKiLevel, KI_LEVEL_NAMES } from './kiinput';

/**
 * What the person picked. `i-ar` and `i-fjor` are relative on purpose: a
 * choice of «I fjor» remembered in December still means last year in
 * January. A number is one older year, picked from the year list.
 */
export type PeriodChoice = 'i-ar' | 'siste-12' | 'i-fjor' | number;

export interface AnalysisChoice {
  periode: PeriodChoice;
  sammenlign: boolean;
}

export const DEFAULT_CHOICE: AnalysisChoice = { periode: 'i-ar', sammenlign: true };

// --- Dates ---------------------------------------------------------------------------

const OSLO_DAY = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Europe/Oslo',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

/**
 * Today in Norway, as `YYYY-MM-DD`. Not the browser's calendar: the backend
 * reads «I år» in Oslo, and a laptop on another clock on New Year's Eve would
 * otherwise ask for one year and be answered with the other.
 */
export function osloToday(now: Date = new Date()): string {
  const parts = Object.fromEntries(OSLO_DAY.formatToParts(now).map((part) => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function ymd(iso: string): [number, number, number] {
  const [year, month, day] = iso.split('-').map(Number);
  return [year ?? 0, month ?? 1, day ?? 1];
}

function iso(year: number, month: number, day: number): string {
  return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

function daysIn(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/** The same date a year earlier, with 29 February as 28 February, as the backend does. */
export function yearEarlier(date: string): string {
  const [year, month, day] = ymd(date);
  return iso(year - 1, month, Math.min(day, daysIn(year - 1, month)));
}

function dayAfter(date: string): string {
  const [year, month, day] = ymd(date);
  if (day < daysIn(year, month)) return iso(year, month, day + 1);
  if (month < 12) return iso(year, month + 1, 1);
  return iso(year + 1, 1, 1);
}

export function yearOf(date: string): number {
  return ymd(date)[0];
}

/**
 * The request for a choice. «I år» sends no dates, so the backend's own Oslo
 * year so far is what is read; the others are the periods the route's
 * description names, «Siste 12 måneder» being the day after today's date a
 * year ago up to today.
 */
export function queryFor(choice: AnalysisChoice, today: string): AnalysisQuery {
  const { periode, sammenlign } = choice;
  if (periode === 'i-ar') return { sammenlign };
  if (periode === 'siste-12') return { fra: dayAfter(yearEarlier(today)), til: today, sammenlign };
  const year = periode === 'i-fjor' ? yearOf(today) - 1 : periode;
  return { fra: iso(year, 1, 1), til: iso(year, 12, 31), sammenlign };
}

/**
 * Whether a remembered choice can still be shown: «I fjor» needs last year
 * in the books and an older year needs that year, or the page would open on
 * a button that is not there.
 */
export function usableChoice(choice: AnalysisChoice, years: number[], today: string): AnalysisChoice {
  const lastYear = yearOf(today) - 1;
  const { periode } = choice;
  if (periode === 'i-fjor' && !years.includes(lastYear)) return { ...choice, periode: 'i-ar' };
  if (typeof periode === 'number' && !(periode < lastYear && years.includes(periode))) {
    return { ...choice, periode: 'i-ar' };
  }
  return choice;
}

/** The years before last year, newest first: the ones the year list offers. */
export function olderYears(years: number[], today: string): number[] {
  const lastYear = yearOf(today) - 1;
  return [...new Set(years)].filter((year) => year < lastYear).sort((a, b) => b - a);
}

// --- What the change is measured against ------------------------------------------

/**
 * How the tiles name the comparison, the way the backend's sentences do:
 * «samme tid i fjor» for the year so far, the year itself for a whole
 * calendar year, «samme tid året før» for anything else.
 */
export interface Against {
  /** «fra samme tid i fjor», «fra 2025». */
  from: string;
  /** «som samme tid i fjor», «som i 2025», for a change that rounds to nothing. */
  like: string;
}

export function against(periode: AnalysisPeriod, today: string): Against {
  const year = yearOf(periode.fra);
  if (periode.fra === iso(yearOf(today), 1, 1) && periode.til === today) {
    return { from: 'fra samme tid i fjor', like: 'som samme tid i fjor' };
  }
  if (periode.fra === iso(year, 1, 1) && periode.til === iso(year, 12, 31)) {
    return { from: `fra ${year - 1}`, like: `som i ${year - 1}` };
  }
  return { from: 'fra samme tid året før', like: 'som samme tid året før' };
}

// --- The change under a tile --------------------------------------------------------

const NBSP = ' ';
const MINUS = '−';

/** Øre as BigInt, from a decimal string; null for anything that is not one. */
export function toOre(value: string): bigint | null {
  const match = /^\s*([+-])?(\d+)(?:\.(\d*))?\s*$/.exec(value);
  if (match === null) return null;
  const frac = (match[3] ?? '').padEnd(3, '0');
  // A third decimal never comes from the ledger, but if it did it is rounded
  // half away from zero rather than cut, like everything else here.
  let ore = BigInt(match[2] ?? '0') * 100n + BigInt(frac.slice(0, 2));
  if ((frac[2] ?? '0') >= '5') ore += 1n;
  return match[1] === '-' ? -ore : ore;
}

/** `numerator / denominator` rounded half away from zero, denominator above zero. */
function roundedDivision(numerator: bigint, denominator: bigint): bigint {
  const negative = numerator < 0n;
  const magnitude = negative ? -numerator : numerator;
  let quotient = magnitude / denominator;
  if ((magnitude % denominator) * 2n >= denominator) quotient += 1n;
  return negative ? -quotient : quotient;
}

function grouped(whole: bigint): string {
  const digits = (whole < 0n ? -whole : whole).toString();
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
}

function signed(whole: bigint): string {
  if (whole > 0n) return `+${grouped(whole)}`;
  if (whole < 0n) return `${MINUS}${grouped(whole)}`;
  return '0';
}

export type Tile = 'inntekter' | 'kostnader' | 'resultat';

/**
 * The line under a tile: «+12 % fra samme tid i fjor».
 *
 * Income and costs change in per cent, when the comparison had any; from
 * nothing, or from a negative amount, a percentage means nothing, so it is the
 * difference in kroner instead. The result is always in kroner, because it
 * crosses zero, and «+300 %» from a small loss to a small profit says the
 * opposite of what happened. A change that rounds to nothing says so in words
 * rather than as «0 %».
 */
export function changeText(tile: Tile, current: string, previous: string, measured: Against): string | null {
  const now = toOre(current);
  const before = toOre(previous);
  if (now === null || before === null) return null;
  const difference = now - before;
  if (tile !== 'resultat' && before > 0n) {
    const percent = roundedDivision(difference * 100n, before);
    if (percent === 0n) return `Omtrent ${measured.like}`;
    return `${signed(percent)}${NBSP}% ${measured.from}`;
  }
  const kroner = roundedDivision(difference, 100n);
  if (kroner === 0n) return `Omtrent ${measured.like}`;
  return `${signed(kroner)}${NBSP}kr ${measured.from}`;
}

// --- What the page remembers --------------------------------------------------------

const STORAGE_PREFIX = 'myntos.analyse.';

function storageKey(companyId: number): string {
  return `${STORAGE_PREFIX}${companyId}`;
}

function encodeChoice(periode: PeriodChoice): string {
  return typeof periode === 'number' ? String(periode) : periode;
}

function decodeChoice(value: unknown): PeriodChoice | null {
  if (value === 'i-ar' || value === 'siste-12' || value === 'i-fjor') return value;
  if (typeof value === 'string' && /^\d{4}$/.test(value)) return Number(value);
  return null;
}

/**
 * The choice last made for this company, or the default. Per company, because
 * a bookkeeper comparing last year for one client should not open the next
 * one on last year. A convenience only: anything unreadable, a private window
 * included, is the default.
 */
export function rememberedChoice(storage: Pick<Storage, 'getItem'> | null, companyId: number): AnalysisChoice {
  try {
    const raw = storage?.getItem(storageKey(companyId));
    if (raw === null || raw === undefined) return DEFAULT_CHOICE;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== 'object' || parsed === null) return DEFAULT_CHOICE;
    const { periode, sammenlign } = parsed as { periode?: unknown; sammenlign?: unknown };
    return {
      periode: decodeChoice(periode) ?? DEFAULT_CHOICE.periode,
      sammenlign: typeof sammenlign === 'boolean' ? sammenlign : DEFAULT_CHOICE.sammenlign,
    };
  } catch {
    return DEFAULT_CHOICE;
  }
}

export function rememberChoice(
  storage: Pick<Storage, 'setItem'> | null,
  companyId: number,
  choice: AnalysisChoice,
): void {
  try {
    storage?.setItem(
      storageKey(companyId),
      JSON.stringify({ periode: encodeChoice(choice.periode), sammenlign: choice.sammenlign }),
    );
  } catch {
    // Full or blocked storage: the choice still holds until the page is left.
  }
}

// --- Whether there is anything to draw ----------------------------------------------

/**
 * Whether any month of the period has books. The time charts carry `null` for
 * a month outside the books, so a period with no month at all is one the
 * company has no books for, and its charts would be empty frames.
 */
export function periodHasBooks(values: Array<string | null>): boolean {
  return values.some((value) => value !== null);
}

/**
 * Whether the period runs into months after the last voucher. Those months are
 * empty because nothing is booked yet, not because nothing happened, and the
 * page says so rather than let a short column pass for a quiet month.
 */
export function endsAfterBooks(bokfortTil: string | null, periode: AnalysisPeriod): boolean {
  if (bokfortTil === null) return false;
  return bokfortTil.slice(0, 7) < periode.til.slice(0, 7);
}

/**
 * The first month with books, as `YYYY-MM`, when the period starts before it;
 * otherwise null. A company that came to Myntos in November has empty columns
 * from January to October, and without a word those read as ten months of
 * nothing earned. `labels` and `values` are a time chart's months and its main
 * series, in the same order.
 */
export function startsBeforeBooks(labels: string[], values: Array<string | null>): string | null {
  const first = values.findIndex((value) => value !== null);
  return first > 0 ? (labels[first] ?? null) : null;
}

// --- While KI reads (WP5b) ----------------------------------------------------------

function wholeCount(n: number): string {
  return grouped(BigInt(Math.max(0, Math.trunc(n))));
}

/**
 * The quiet line above the pies while KI reads: «KI leser bilagene for å
 * fordele dem etter hva de gjelder (340 av 1 200). Siden oppdaterer seg
 * selv.» Without the count while the run has not yet counted its vouchers,
 * since «(0 av 0)» reads as if there were nothing to read.
 */
export function readingText(reading: Pick<AnalysisReading, 'done' | 'total'>): string {
  const progress =
    reading.total > 0
      ? ` (${wholeCount(Math.min(reading.done, reading.total))} av ${wholeCount(reading.total)})`
      : '';
  return `KI leser bilagene for å fordele dem etter hva de gjelder${progress}. Siden oppdaterer seg selv.`;
}

/** How often the page asks how far the reading has come: a few seconds, as the contract says. */
export const READING_POLL_MS = 3000;

/**
 * Consecutive failed polls before the page stops asking. A network that is
 * gone, or a run that is suddenly not there, is no reason to keep a timer
 * going for as long as the tab is open, and nothing about it is alarming
 * enough to say: the figures on the page are still right.
 */
export const READING_POLL_GIVE_UP = 5;

export function isGoing(status: AnalysisRun['status']): boolean {
  return status === 'queued' || status === 'running';
}

export interface Visibility {
  hidden(): boolean;
  /** Calls `listener` whenever the page is hidden or shown again; returns how to stop. */
  onChange(listener: () => void): () => void;
}

export interface Timers {
  set(callback: () => void, ms: number): unknown;
  clear(handle: unknown): void;
}

export function documentVisibility(doc: Document = document): Visibility {
  return {
    hidden: () => doc.visibilityState === 'hidden',
    onChange: (listener) => {
      doc.addEventListener('visibilitychange', listener);
      return () => doc.removeEventListener('visibilitychange', listener);
    },
  };
}

const windowTimers: Timers = {
  set: (callback, ms) => window.setTimeout(callback, ms),
  clear: (handle) => window.clearTimeout(handle as number),
};

export interface WatchOptions<
  TRun extends Pick<AnalysisRun, 'status' | 'done' | 'total'> = Pick<
    AnalysisRun,
    'status' | 'done' | 'total'
  >,
> {
  /** `GET …/analyse/kjoringer/{id}` for the run being watched. */
  fetchRun: () => Promise<TRun>;
  /** Each answer while the run is still going. */
  onProgress: (run: TRun) => void;
  /**
   * Once, when the run has ended: its status (or null when the page gave up
   * asking) and the final run payload if one arrived.
   */
  onEnd: (status: AnalysisRun['status'] | null, run?: TRun) => void;
  interval?: number;
  timers?: Timers;
  visibility?: Visibility;
}

/**
 * Polls a KI run every few seconds until it ends, and returns how to stop.
 *
 * Paused while the page is hidden, because a tab left open in the background
 * for a day should not ask the server every three seconds for it; asked at
 * once when the page is shown again, since that is when someone looks. One
 * request at a time: a slow answer is waited for, never overtaken.
 */
export function watchReading<
  TRun extends Pick<AnalysisRun, 'status' | 'done' | 'total'> = Pick<
    AnalysisRun,
    'status' | 'done' | 'total'
  >,
>(options: WatchOptions<TRun>): () => void {
  const interval = options.interval ?? READING_POLL_MS;
  const timers = options.timers ?? windowTimers;
  const visibility = options.visibility ?? documentVisibility();
  let timer: unknown = null;
  let stopped = false;
  let inFlight = false;
  let errors = 0;

  function clearTimer(): void {
    if (timer !== null) timers.clear(timer);
    timer = null;
  }

  function schedule(): void {
    clearTimer();
    if (stopped || visibility.hidden()) return;
    timer = timers.set(() => void tick(), interval);
  }

  function end(status: AnalysisRun['status'] | null, run?: TRun): void {
    stop();
    options.onEnd(status, run);
  }

  async function tick(): Promise<void> {
    timer = null;
    // Hidden: the visibility listener asks again when the page is shown.
    if (stopped || inFlight || visibility.hidden()) return;
    inFlight = true;
    try {
      const run = await options.fetchRun();
      errors = 0;
      if (stopped) return;
      if (!isGoing(run.status)) {
        end(run.status, run);
        return;
      }
      options.onProgress(run);
    } catch {
      if (stopped) return;
      errors += 1;
      if (errors >= READING_POLL_GIVE_UP) {
        end(null);
        return;
      }
    } finally {
      inFlight = false;
    }
    schedule();
  }

  const unsubscribe = visibility.onChange(() => {
    if (stopped) return;
    clearTimer();
    if (!visibility.hidden()) void tick();
  });

  function stop(): void {
    stopped = true;
    clearTimer();
    unsubscribe();
  }

  schedule();
  return stop;
}

// --- «Dyp KI-analyse» progress, stamps and staleness (WP6) --------------------------

/** How often the page polls a deep analysis run a person just started (docs/analyse.md). */
export const DEEP_POLL_MS = 1500;

const OSLO_SHORT_DATE = new Intl.DateTimeFormat('nb-NO', {
  day: 'numeric',
  month: 'short',
  timeZone: 'Europe/Oslo',
});

/** «Laget 26. nov. med Smart» (D6). Never exposes per-voucher stamps. */
export function reportStampText(createdAt: string, niva: string): string {
  const dateText = OSLO_SHORT_DATE.format(new Date(createdAt));
  const levelLabel = isKiLevel(niva) ? KI_LEVEL_NAMES[niva] : niva;
  return `Laget ${dateText} med ${levelLabel}`;
}

/** «Laget før 14 nye bilag» / «Laget før 1 nytt bilag». */
export function staleReportText(nyeBilag: number): string {
  return nyeBilag === 1 ? 'Laget før 1 nytt bilag' : `Laget før ${wholeCount(nyeBilag)} nye bilag`;
}

export interface DeepProgress {
  /** Current step label shown beside the progress bar. */
  label: string;
  /** Fraction 0..1 while counting vouchers, or null for indeterminate steps. */
  fraction: number | null;
}

/** Step text and bar fraction for a queued or running «Dyp KI-analyse». */
export function deepProgress(run: Pick<AnalysisRun, 'step' | 'done' | 'total'>): DeepProgress {
  const step = run.step ?? 'Venter på tur';
  if (step === 'Leser bilagene' && run.total > 0) {
    const done = Math.min(Math.max(0, run.done), run.total);
    return {
      label: `${step} (${wholeCount(done)} av ${wholeCount(run.total)})`,
      fraction: done / run.total,
    };
  }
  return { label: step, fraction: null };
}

/** Plain bokmål message when a deep analysis run did not finish with a report. */
export function runErrorText(errorCode: string | null): string {
  if (errorCode === 'ki_turned_off') {
    return 'KI ble slått av underveis, så analysen ble stoppet.';
  }
  if (errorCode === 'ki_unavailable') {
    return 'Løynd svarte ikke akkurat nå. Prøv igjen om litt.';
  }
  return 'Analysen kunne ikke fullføres. Prøv igjen om litt.';
}
