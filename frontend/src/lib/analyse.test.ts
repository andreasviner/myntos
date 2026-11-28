import { describe, expect, it } from 'vitest';

import {
  DEEP_POLL_MS,
  DEFAULT_CHOICE,
  against,
  changeText,
  deepProgress,
  endsAfterBooks,
  olderYears,
  osloToday,
  periodHasBooks,
  queryFor,
  READING_POLL_GIVE_UP,
  readingText,
  rememberChoice,
  rememberedChoice,
  reportStampText,
  runErrorText,
  staleReportText,
  startsBeforeBooks,
  toOre,
  usableChoice,
  watchReading,
  yearEarlier,
  type Timers,
  type Visibility,
} from './analyse';
import type { AnalysisRun } from './api';

const NBSP = ' ';
const MINUS = '−';

describe('osloToday', () => {
  it('is already the new year in Oslo when it is still the old one in UTC', () => {
    expect(osloToday(new Date('2026-12-31T23:30:00Z'))).toBe('2027-01-01');
  });

  it('is the date in Oslo in summer time too', () => {
    expect(osloToday(new Date('2026-06-30T22:30:00Z'))).toBe('2026-07-01');
    expect(osloToday(new Date('2026-06-30T21:30:00Z'))).toBe('2026-06-30');
  });
});

describe('queryFor', () => {
  const today = '2026-11-27';

  it('sends no dates for «I år», so the backend reads its own Oslo year', () => {
    expect(queryFor({ periode: 'i-ar', sammenlign: true }, today)).toEqual({ sammenlign: true });
  });

  it('reads «Siste 12 måneder» from the day after this date a year ago', () => {
    expect(queryFor({ periode: 'siste-12', sammenlign: false }, today)).toEqual({
      fra: '2025-11-28',
      til: '2026-11-27',
      sammenlign: false,
    });
  });

  it('turns the year boundary and 29 February the way the backend does', () => {
    expect(queryFor({ periode: 'siste-12', sammenlign: true }, '2026-12-31').fra).toBe('2026-01-01');
    expect(queryFor({ periode: 'siste-12', sammenlign: true }, '2028-02-29').fra).toBe('2027-03-01');
    expect(yearEarlier('2028-02-29')).toBe('2027-02-28');
  });

  it('reads «I fjor» and an older year as whole calendar years', () => {
    expect(queryFor({ periode: 'i-fjor', sammenlign: true }, today)).toEqual({
      fra: '2025-01-01',
      til: '2025-12-31',
      sammenlign: true,
    });
    expect(queryFor({ periode: 2022, sammenlign: true }, today)).toMatchObject({
      fra: '2022-01-01',
      til: '2022-12-31',
    });
  });
});

describe('the year choices', () => {
  const today = '2026-11-27';

  it('offers the years before last year, newest first', () => {
    expect(olderYears([2022, 2023, 2024, 2025, 2026], today)).toEqual([2024, 2023, 2022]);
    expect(olderYears([2025, 2026], today)).toEqual([]);
  });

  it('falls back to «I år» when a remembered choice has no year to show', () => {
    expect(usableChoice({ periode: 'i-fjor', sammenlign: false }, [2026], today)).toEqual({
      periode: 'i-ar',
      sammenlign: false,
    });
    expect(usableChoice({ periode: 2021, sammenlign: true }, [2024, 2025, 2026], today).periode).toBe('i-ar');
    // Last year picked from the list in an older session is «I fjor» now, not a list entry.
    expect(usableChoice({ periode: 2025, sammenlign: true }, [2025, 2026], today).periode).toBe('i-ar');
    expect(usableChoice({ periode: 2024, sammenlign: true }, [2024, 2025, 2026], today).periode).toBe(2024);
    expect(usableChoice({ periode: 'i-fjor', sammenlign: true }, [2025, 2026], today).periode).toBe('i-fjor');
  });
});

describe('against', () => {
  it('names the comparison as the backend sentences do', () => {
    expect(against({ fra: '2026-01-01', til: '2026-11-27' }, '2026-11-27').from).toBe('fra samme tid i fjor');
    expect(against({ fra: '2025-01-01', til: '2025-12-31' }, '2026-11-27')).toEqual({
      from: 'fra 2024',
      like: 'som i 2024',
    });
    expect(against({ fra: '2025-11-28', til: '2026-11-27' }, '2026-11-27').from).toBe('fra samme tid året før');
  });

  it('reads the year so far first on New Year’s Eve, when it is also a calendar year', () => {
    expect(against({ fra: '2026-01-01', til: '2026-12-31' }, '2026-12-31').from).toBe('fra samme tid i fjor');
  });
});

describe('changeText', () => {
  const measured = against({ fra: '2026-01-01', til: '2026-11-27' }, '2026-11-27');

  it('gives income and costs in per cent, rounded half away from zero', () => {
    expect(changeText('inntekter', '412000.00', '368000.00', measured)).toBe(`+12${NBSP}% fra samme tid i fjor`);
    expect(changeText('kostnader', '95.00', '100.00', measured)).toBe(`${MINUS}5${NBSP}% fra samme tid i fjor`);
    // 0,5 % exactly goes up, as ROUND_HALF_UP does in sentences.py; −0,5 % goes down.
    expect(changeText('inntekter', '100.50', '100.00', measured)).toBe(`+1${NBSP}% fra samme tid i fjor`);
    expect(changeText('inntekter', '99.50', '100.00', measured)).toBe(`${MINUS}1${NBSP}% fra samme tid i fjor`);
  });

  it('says a change that rounds to nothing in words', () => {
    expect(changeText('inntekter', '100.40', '100.00', measured)).toBe('Omtrent som samme tid i fjor');
    expect(changeText('resultat', '10.00', '10.20', measured)).toBe('Omtrent som samme tid i fjor');
  });

  it('gives kroner when there was nothing to take a percentage of', () => {
    expect(changeText('inntekter', '12000.00', '0.00', measured)).toBe(`+12${NBSP}000${NBSP}kr fra samme tid i fjor`);
    expect(changeText('kostnader', '50.00', '-20.00', measured)).toBe(`+70${NBSP}kr fra samme tid i fjor`);
  });

  it('gives the result in kroner, because it crosses zero', () => {
    expect(changeText('resultat', '110500.00', '77900.00', measured)).toBe(`+32${NBSP}600${NBSP}kr fra samme tid i fjor`);
    expect(changeText('resultat', '-1500.50', '2000.00', measured)).toBe(
      `${MINUS}3${NBSP}501${NBSP}kr fra samme tid i fjor`,
    );
  });

  it('groups large percentages and leaves out what it cannot read', () => {
    expect(changeText('inntekter', '1250000.00', '1000.00', measured)).toBe(
      `+124${NBSP}900${NBSP}% fra samme tid i fjor`,
    );
    expect(changeText('inntekter', 'mye', '1000.00', measured)).toBeNull();
  });
});

describe('toOre', () => {
  it('reads decimal strings without a float', () => {
    expect(toOre('412000.40')).toBe(41200040n);
    expect(toOre('-0.5')).toBe(-50n);
    expect(toOre('7')).toBe(700n);
    expect(toOre('9007199254740993.01')).toBe(900719925474099301n);
    expect(toOre('1.005')).toBe(101n);
    expect(toOre('')).toBeNull();
  });
});

describe('rememberedChoice', () => {
  function memory(): Storage {
    const items = new Map<string, string>();
    return {
      getItem: (key: string) => items.get(key) ?? null,
      setItem: (key: string, value: string) => void items.set(key, value),
      removeItem: (key: string) => void items.delete(key),
      clear: () => items.clear(),
      key: () => null,
      get length() {
        return items.size;
      },
    };
  }

  it('keeps a choice per company', () => {
    const storage = memory();
    rememberChoice(storage, 4, { periode: 'i-fjor', sammenlign: false });
    rememberChoice(storage, 5, { periode: 2023, sammenlign: true });
    expect(rememberedChoice(storage, 4)).toEqual({ periode: 'i-fjor', sammenlign: false });
    expect(rememberedChoice(storage, 5)).toEqual({ periode: 2023, sammenlign: true });
    expect(rememberedChoice(storage, 6)).toEqual(DEFAULT_CHOICE);
  });

  it('is the default when storage is missing, broken or throws', () => {
    expect(rememberedChoice(null, 4)).toEqual(DEFAULT_CHOICE);
    const broken = memory();
    broken.setItem('myntos.analyse.4', '{nei');
    expect(rememberedChoice(broken, 4)).toEqual(DEFAULT_CHOICE);
    broken.setItem('myntos.analyse.4', '{"periode":"neste-ar","sammenlign":"ja"}');
    expect(rememberedChoice(broken, 4)).toEqual(DEFAULT_CHOICE);
    const throwing = {
      getItem: () => {
        throw new Error('blocked');
      },
      setItem: () => {
        throw new Error('full');
      },
    };
    expect(rememberedChoice(throwing, 4)).toEqual(DEFAULT_CHOICE);
    expect(() => rememberChoice(throwing, 4, DEFAULT_CHOICE)).not.toThrow();
  });
});

describe('what there is to draw', () => {
  it('tells a period with no books from one that came to zero', () => {
    expect(periodHasBooks([null, null, null])).toBe(false);
    expect(periodHasBooks([null, '0.00', null])).toBe(true);
  });

  it('notices a period that runs past the last voucher', () => {
    const periode = { fra: '2026-01-01', til: '2026-11-27' };
    expect(endsAfterBooks('2026-03-14', periode)).toBe(true);
    expect(endsAfterBooks('2026-11-20', periode)).toBe(false);
    expect(endsAfterBooks(null, periode)).toBe(false);
  });

  it('names the first month with books only when the period starts before it', () => {
    const labels = ['2026-09', '2026-10', '2026-11'];
    expect(startsBeforeBooks(labels, [null, null, '18450.00'])).toBe('2026-11');
    expect(startsBeforeBooks(labels, [null, '0.00', '18450.00'])).toBe('2026-10');
    expect(startsBeforeBooks(labels, ['0.00', null, null])).toBeNull();
    expect(startsBeforeBooks(labels, [null, null, null])).toBeNull();
  });
});

describe('readingText', () => {
  it('says how far KI has come, with the numbers grouped the Norwegian way', () => {
    expect(readingText({ done: 340, total: 1200 })).toBe(
      `KI leser bilagene for å fordele dem etter hva de gjelder (340 av 1${NBSP}200). Siden oppdaterer seg selv.`,
    );
  });

  it('leaves the count out before the run has counted, and never shows more done than there is', () => {
    expect(readingText({ done: 0, total: 0 })).toBe(
      'KI leser bilagene for å fordele dem etter hva de gjelder. Siden oppdaterer seg selv.',
    );
    expect(readingText({ done: 12, total: 10 })).toContain('(10 av 10)');
  });

  it('has no em dash', () => {
    expect(readingText({ done: 1, total: 2 })).not.toContain('\u2014');
  });
});

type Run = Pick<AnalysisRun, 'status' | 'done' | 'total'>;

/** Timers the test moves by hand, and a page it hides and shows. */
function harness(answers: Array<Run | Error>) {
  let pending: Array<{ id: number; callback: () => void }> = [];
  let next = 1;
  const timers: Timers = {
    set: (callback) => {
      const id = next++;
      pending.push({ id, callback });
      return id;
    },
    clear: (handle) => {
      pending = pending.filter((entry) => entry.id !== handle);
    },
  };
  let hidden = false;
  const listeners = new Set<() => void>();
  const visibility: Visibility = {
    hidden: () => hidden,
    onChange: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
  const asked: number[] = [];
  const progress: Run[] = [];
  const ended: Array<Run['status'] | null> = [];
  const fetchRun = async (): Promise<Run> => {
    asked.push(asked.length);
    const answer = answers.shift();
    if (answer === undefined) throw new Error('asked once too often');
    if (answer instanceof Error) throw answer;
    return answer;
  };
  const stop = watchReading({
    fetchRun,
    onProgress: (run) => progress.push(run),
    onEnd: (status) => ended.push(status),
    timers,
    visibility,
  });
  return {
    stop,
    asked,
    progress,
    ended,
    listeners,
    pending: () => pending.length,
    /** Fires the timer that is due and lets its request answer. */
    async fire() {
      const due = pending.shift();
      due?.callback();
      await new Promise((resolve) => setTimeout(resolve, 0));
    },
    async setHidden(value: boolean) {
      hidden = value;
      for (const listener of [...listeners]) listener();
      await new Promise((resolve) => setTimeout(resolve, 0));
    },
  };
}

describe('watchReading', () => {
  const going = (done: number): Run => ({ status: 'running', done, total: 10 });

  it('asks every few seconds while the run goes, then stops for good and says how it ended', async () => {
    const page = harness([going(3), going(7), { status: 'done', done: 10, total: 10 }]);
    expect(page.asked).toHaveLength(0);
    await page.fire();
    await page.fire();
    expect(page.progress.map((run) => run.done)).toEqual([3, 7]);
    await page.fire();
    expect(page.ended).toEqual(['done']);
    expect(page.pending()).toBe(0);
    expect(page.listeners.size).toBe(0);
  });

  it('ends on a failed run without asking again', async () => {
    const page = harness([{ status: 'failed', done: 0, total: 10 }]);
    await page.fire();
    expect(page.ended).toEqual(['failed']);
    expect(page.pending()).toBe(0);
  });

  it('pauses while the page is hidden and asks at once when it is shown', async () => {
    const page = harness([going(1), going(2)]);
    await page.setHidden(true);
    expect(page.pending()).toBe(0);
    expect(page.asked).toHaveLength(0);
    await page.setHidden(false);
    expect(page.asked).toHaveLength(1);
    expect(page.pending()).toBe(1);
    await page.fire();
    expect(page.progress.map((run) => run.done)).toEqual([1, 2]);
    page.stop();
  });

  it('gives up quietly after several failed requests in a row', async () => {
    const page = harness(Array.from({ length: READING_POLL_GIVE_UP }, () => new Error('offline')));
    for (let i = 0; i < READING_POLL_GIVE_UP; i++) await page.fire();
    expect(page.ended).toEqual([null]);
    expect(page.pending()).toBe(0);
  });

  it('a request that fails once is asked again, and the count starts over', async () => {
    const page = harness([new Error('blip'), going(4), { status: 'done', done: 10, total: 10 }]);
    await page.fire();
    expect(page.ended).toEqual([]);
    await page.fire();
    await page.fire();
    expect(page.ended).toEqual(['done']);
  });

  it('stopped from outside, it asks nothing more and calls nothing', async () => {
    const page = harness([going(1)]);
    page.stop();
    expect(page.pending()).toBe(0);
    await page.setHidden(false);
    expect(page.asked).toHaveLength(0);
    expect(page.ended).toEqual([]);
  });

  it('passes the final run payload to onEnd so a deep run can open its report_id', async () => {
    type DeepRun = Pick<AnalysisRun, 'status' | 'done' | 'total' | 'report_id' | 'notices'>;
    const pending: Array<() => void> = [];
    const ended: Array<{ status: AnalysisRun['status'] | null; run?: DeepRun }> = [];
    watchReading<DeepRun>({
      interval: DEEP_POLL_MS,
      fetchRun: async () => ({
        status: 'done',
        done: 4,
        total: 4,
        report_id: 99,
        notices: ['Fallback-merknad'],
      }),
      onProgress: () => undefined,
      onEnd: (status, run) => ended.push({ status, run }),
      timers: {
        set: (cb) => {
          pending.push(cb);
          return pending.length;
        },
        clear: () => {
          pending.length = 0;
        },
      },
      visibility: { hidden: () => false, onChange: () => () => undefined },
    });
    expect(pending).toHaveLength(1);
    pending.shift()?.();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(ended).toHaveLength(1);
    expect(ended[0]?.status).toBe('done');
    expect(ended[0]?.run?.report_id).toBe(99);
    expect(ended[0]?.run?.notices).toEqual(['Fallback-merknad']);
  });
});

describe('WP6 deep analysis helpers', () => {
  it('formats the D6 report stamp in Oslo time and maps level names to bokmål', () => {
    expect(reportStampText('2026-11-26T10:15:00Z', 'thinking')).toBe('Laget 26. nov. med Smart');
    expect(reportStampText('2026-11-26T23:30:00Z', 'free')).toBe('Laget 27. nov. med Gratis');
    expect(reportStampText('2026-03-04T12:00:00Z', 'medium')).toBe('Laget 4. mars med Medium');
  });

  it('formats stale report voucher counts in singular, plural and grouped thousands', () => {
    expect(staleReportText(1)).toBe('Laget før 1 nytt bilag');
    expect(staleReportText(14)).toBe('Laget før 14 nye bilag');
    expect(staleReportText(1250)).toBe(`Laget før 1${NBSP}250 nye bilag`);
  });

  it('returns determinate fraction while reading vouchers and indeterminate for other steps', () => {
    expect(deepProgress({ step: null, done: 0, total: 0 })).toEqual({
      label: 'Venter på tur',
      fraction: null,
    });
    expect(deepProgress({ step: 'Leser bilagene', done: 340, total: 1200 })).toEqual({
      label: `Leser bilagene (340 av 1${NBSP}200)`,
      fraction: 340 / 1200,
    });
    expect(deepProgress({ step: 'Leser bilagene', done: 15, total: 10 })).toEqual({
      label: 'Leser bilagene (10 av 10)',
      fraction: 1,
    });
    expect(deepProgress({ step: 'Spør KI-en (Smart)', done: 0, total: 0 })).toEqual({
      label: 'Spør KI-en (Smart)',
      fraction: null,
    });
  });

  it('turns run error codes into plain bokmål without em dashes', () => {
    for (const code of ['ki_turned_off', 'ki_unavailable', 'internal_error', null]) {
      const text = runErrorText(code);
      expect(text.length).toBeGreaterThan(10);
      expect(text).not.toContain('\u2014');
    }
    expect(runErrorText('ki_turned_off')).toContain('slått av');
    expect(runErrorText('ki_unavailable')).toContain('Løynd');
  });
});
