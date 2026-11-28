/**
 * The chart kit: one `Chart` spec from the backend in, one `<figure>` out.
 *
 * No library. The frontend has one runtime dependency, Astro, and a chart
 * library would outweigh every page it is drawn on; the six views here are a
 * few hundred lines of SVG (docs/analyse.md, «The renderer»).
 *
 * Three rules hold everywhere in this file:
 *
 * - **Every figure a person reads is the backend's decimal string**, run
 *   through `kroner()` or a string formatter below. `Number()` is used for
 *   pixel geometry and for choosing which of the backend's figures to name,
 *   never for a figure on screen and never for a sum (CLAUDE.md rule 4). The
 *   tick labels are the one exception, and they are not data: they are round
 *   numbers this file chose to draw grid lines at.
 * - **Nothing is parsed as markup.** Titles, labels and captions may one day be
 *   written by a model, and a label is a counterparty name a person typed.
 *   Every string goes in through `textContent`.
 * - **The table is the chart.** Every figure in the SVG is also in the
 *   `<details>` table under it, which is what a screen reader, a person who
 *   cannot tell the colours apart and an AI helping the person all read. The
 *   SVG only has to be right to look at; the `<title>` tooltips are a
 *   convenience, never the only place a number is.
 *
 * The styles are in BaseLayout's global sheet under `.chart`, because a page's
 * scoped `<style>` never reaches nodes its script builds
 * (docs/frontend-conventions.md), and the colours are the `--chart-*` tokens
 * there, so both themes come from CSS and nothing here knows which is on.
 */

import type { Chart, ChartAxis, ChartSeries, ChartSlice, ChartUnit, ChartView } from './api';
import { kroner } from './format';

const SVG_NS = 'http://www.w3.org/2000/svg';
const MINUS = '−';
const NBSP = ' ';

/**
 * The width a chart is laid out at before the page has measured it, and in
 * tests. On the page a ResizeObserver redraws it at the measured width before
 * the first paint, so the viewBox is the box and 12px text stays 12px: a
 * chart scaled down from 480 to a phone's 280 would have 7px labels.
 */
export const DEFAULT_WIDTH = 480;

/**
 * The advance of one character of the 12px chart text, generously: SVG text
 * cannot be measured before it is in a laid-out document, and these layouts
 * are made before that. Too wide wastes a few pixels; too narrow overlaps.
 */
const CHAR = 6.8;

/** Month labels closer than this are thinned to every second, third, … month. */
const MIN_LABEL_SPACING = 36;

/** kolonner and linje: the plot's vertical frame, in px of the viewBox. */
const SERIES_HEIGHT = 236;
const PLOT_TOP = 10;
const X_BAND = 38;

/**
 * stolper: one row is a label line over a bar. The names are what is read
 * there, not axis ticks, so they are 13px (`.chart-label`), and measured as such.
 */
const BAR_ROW = 40;
const BAR_THICKNESS = 12;
const BAR_TOP = 20;
const BAR_CHAR = 7.4;

/**
 * Node's and the browsers' `Intl` disagree on the short months («jan» against
 * «jan.»), and the doc fixes the dotted form, so they are written out.
 */
const MONTHS_SHORT = ['jan.', 'feb.', 'mars', 'apr.', 'mai', 'juni', 'juli', 'aug.', 'sep.', 'okt.', 'nov.', 'des.'];
const MONTHS_LONG = [
  'januar', 'februar', 'mars', 'april', 'mai', 'juni',
  'juli', 'august', 'september', 'oktober', 'november', 'desember',
];

// --- Months -------------------------------------------------------------------------

function parseMonth(label: string): { year: string; month: number } | null {
  const match = /^(\d{4})-(\d{2})$/.exec(label);
  if (match === null) return null;
  const month = Number(match[2]) - 1;
  return month >= 0 && month < 12 ? { year: match[1] ?? '', month } : null;
}

/** `'2026-03'` as an axis label: «mars». A label that is not a month comes back as it is. */
export function monthShort(label: string): string {
  const parsed = parseMonth(label);
  return parsed === null ? label : (MONTHS_SHORT[parsed.month] ?? label);
}

/** `'2026-03'` as the table and the tooltips name it: «mars 2026». */
export function monthLong(label: string): string {
  const parsed = parseMonth(label);
  return parsed === null ? label : `${MONTHS_LONG[parsed.month] ?? label} ${parsed.year}`;
}

function xName(axis: ChartAxis, index: number): string {
  const label = axis.labels[index] ?? '';
  return axis.kind === 'maned' ? monthLong(label) : label;
}

// --- Figures as text ----------------------------------------------------------------

interface Decimal {
  negative: boolean;
  int: string;
  frac: string;
}

function parseDecimal(value: string): Decimal | null {
  const match = /^\s*([+-])?(\d*)(?:\.(\d*))?\s*$/.exec(value);
  if (match === null) return null;
  const int = (match[2] ?? '').replace(/^0+(?=\d)/, '');
  const frac = match[3] ?? '';
  if (int === '' && frac === '') return null;
  return { negative: match[1] === '-', int: int === '' ? '0' : int, frac };
}

function groupDigits(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
}

/** Adds one to a string of digits, so rounding never goes through a float. */
function incrementDigits(digits: string): string {
  const out = digits.split('');
  for (let i = out.length - 1; i >= 0; i--) {
    if (out[i] !== '9') {
      out[i] = String(Number(out[i]) + 1);
      return out.join('');
    }
    out[i] = '0';
  }
  return `1${out.join('')}`;
}

/**
 * Fractions as percentages, one decimal at most, by moving the decimal point
 * in the string: `'0.125'` is «12,5 %». The backend's shares already sum to
 * exactly 1 (it lets «Andre» take the rounding), so these are its figures
 * shown, not new ones. A list shares its decimals, so a legend reads
 * «38,0 %» above «22,4 %» rather than «38 %», and «62 %» throughout when no
 * share has a tenth.
 */
export function percentTexts(values: string[]): string[] {
  const parts = values.map((value) => {
    const parsed = parseDecimal(value);
    if (parsed === null) return null;
    const frac = parsed.frac.padEnd(3, '0');
    let whole = `${parsed.int}${frac.slice(0, 2)}`;
    let tenth = frac.slice(2, 3);
    if ((frac[3] ?? '0') >= '5') {
      const bumped = incrementDigits(`${whole}${tenth}`);
      whole = bumped.slice(0, -1);
      tenth = bumped.slice(-1);
    }
    whole = whole.replace(/^0+(?=\d)/, '');
    return { negative: parsed.negative, whole, tenth };
  });
  const tenths = parts.some((part) => part !== null && part.tenth !== '0');
  return parts.map((part, index) => {
    if (part === null) return values[index] ?? '';
    const zero = /^0*$/.test(part.whole) && part.tenth === '0';
    const sign = part.negative && !zero ? MINUS : '';
    return `${sign}${groupDigits(part.whole)}${tenths ? `,${part.tenth}` : ''}${NBSP}%`;
  });
}

/** A count, grouped the Norwegian way, without going through a float. */
function countText(value: string): string {
  const parsed = parseDecimal(value);
  if (parsed === null) return value;
  const frac = parsed.frac.replace(/0+$/, '');
  const zero = /^0*$/.test(parsed.int) && frac === '';
  return `${parsed.negative && !zero ? MINUS : ''}${groupDigits(parsed.int)}${frac === '' ? '' : `,${frac}`}`;
}

/**
 * Kroner rounded to whole kroner, half away from zero, on the string.
 *
 * What is drawn (a hover title, a legend, a bar's label, the big figure) is
 * read at a glance, and «114 570,00 kr» beside every slice is noise that hides
 * the number. The «Vis tallene» table keeps the øre: it is the exact version,
 * the one an accountant or an AI helping the person reads.
 */
function wholeKroner(value: string): string {
  const parsed = parseDecimal(value);
  if (parsed === null) return kroner(value);
  const int = (parsed.frac[0] ?? '0') >= '5' ? incrementDigits(parsed.int) : parsed.int;
  const zero = /^0*$/.test(int);
  return `${parsed.negative && !zero ? MINUS : ''}${groupDigits(int)}${NBSP}kr`;
}

/**
 * One figure as a person reads it, in the chart's unit. `whole` drops the øre
 * from kroner for what is drawn; tables leave it false.
 */
export function valueText(unit: ChartUnit, value: string, whole = false): string {
  if (unit === 'kr') return whole ? wholeKroner(value) : kroner(value);
  if (unit === 'prosent') return percentTexts([value])[0] ?? value;
  return countText(value);
}

/**
 * A column of figures: percentages share their decimals; kroner have two in a
 * table and none when `whole` (what is drawn, see wholeKroner).
 */
function columnTexts(unit: ChartUnit, values: Array<string | null>, whole = false): Array<string | null> {
  if (unit !== 'prosent') return values.map((value) => (value === null ? null : valueText(unit, value, whole)));
  const present = values.filter((value): value is string => value !== null);
  const texts = percentTexts(present);
  let next = 0;
  return values.map((value) => (value === null ? null : (texts[next++] ?? value)));
}

const NO_BOOKS = 'Ingen bilag';

// What a chart with nothing in it says instead of drawing an empty ring or a
// table with no rows, which read as broken rather than as «nothing here».
const NOTHING_TO_SHOW = 'Ingen tall å vise for perioden.';

// --- The value axis -----------------------------------------------------------------

function niceStep(rough: number, integer: boolean): number {
  const power = 10 ** Math.floor(Math.log10(rough));
  const fraction = rough / power;
  // 2,5 is a nice step for money and percentages, but not for a count of
  // things below ten: «2,5 bilag» is a tick nobody can have.
  const steps = integer && power < 10 ? [1, 2, 5, 10] : [1, 2, 2.5, 5, 10];
  const multiple = steps.find((step) => fraction <= step + 1e-9) ?? 10;
  return integer ? Math.max(1, multiple * power) : multiple * power;
}

/** Strips float noise (`3 * 0.1`) and negative zero from a tick. */
function cleanTick(value: number): number {
  const clean = Number(value.toPrecision(12));
  return clean === 0 ? 0 : clean;
}

/**
 * Grid lines at 1, 2, 2,5 or 5 × 10ⁿ, about `count` intervals, always
 * including zero: a column chart that does not start at zero draws a small
 * difference as a large one, and a loss has to be seen to cross the line.
 * Nothing but zeros gives the single tick 0.
 */
export function niceTicks(min: number, max: number, count = 5, integer = false): number[] {
  const lo = Math.min(0, min);
  const hi = Math.max(0, max);
  if (!(hi > lo)) return [0];
  const step = niceStep((hi - lo) / count, integer);
  const first = Math.floor(lo / step + 1e-9);
  const last = Math.ceil(hi / step - 1e-9);
  const ticks: number[] = [];
  for (let i = first; i <= last; i++) ticks.push(cleanTick(i * step));
  return ticks;
}

const TICK = new Intl.NumberFormat('nb-NO', { maximumFractionDigits: 2 });
const TICK_PERCENT = new Intl.NumberFormat('nb-NO', { style: 'percent', maximumFractionDigits: 1 });

/**
 * Tick labels: «40 000», or «1,2 mill.» for the whole axis once its largest
 * tick reaches a million, so one axis never mixes the two. These are the
 * round numbers the grid is drawn at, not figures from the books, which is why
 * they may go through `Intl` as numbers.
 */
export function tickTexts(ticks: number[], unit: ChartUnit): string[] {
  const largest = Math.max(0, ...ticks.map((tick) => Math.abs(tick)));
  return ticks.map((tick) => {
    if (tick === 0) return '0';
    if (unit === 'prosent') return TICK_PERCENT.format(tick);
    if (largest >= 1e6) return `${TICK.format(tick / 1e6)}${NBSP}mill.`;
    return TICK.format(tick);
  });
}

// --- Colours ------------------------------------------------------------------------

/**
 * `hoved` is always `--chart-1`, `sammenligning` the quiet `--chart-compare`,
 * and every other series the next slot `hoved` has not taken. The classes set
 * `--c` in BaseLayout, which every mark fills or strokes with, because an SVG
 * presentation attribute cannot hold a `var()`.
 */
function seriesColours(series: ChartSeries[]): string[] {
  const taken = new Set<number>(series.some((one) => one.role === 'hoved') ? [1] : []);
  return series.map((one) => {
    if (one.role === 'sammenligning') return 'chart-cc';
    if (one.role === 'hoved') return 'chart-c1';
    let slot = 1;
    while (taken.has(slot) && slot < 6) slot++;
    taken.add(slot);
    return `chart-c${slot}`;
  });
}

function sliceColour(index: number): string {
  return `chart-c${(index % 6) + 1}`;
}

// --- Geometry -----------------------------------------------------------------------

function amount(value: string | null): number | null {
  if (value === null) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function px(value: number): string {
  return String(Math.round(value * 10) / 10);
}

/**
 * A column or bar with its data end rounded (4px, or less when it is
 * thinner) and its baseline end square, so it reads as standing on the axis.
 * `base` and `end` run along the value axis; `from` and `size` across it.
 */
function barPath(vertical: boolean, from: number, size: number, base: number, end: number): string {
  const length = Math.abs(end - base);
  const r = Math.min(4, size / 2, length);
  const toward = end < base ? -1 : 1;
  const sweep = vertical ? (toward < 0 ? 1 : 0) : toward > 0 ? 1 : 0;
  const corner = end - toward * r;
  if (vertical) {
    return [
      `M${px(from)} ${px(base)}`,
      `V${px(corner)}`,
      `A${px(r)} ${px(r)} 0 0 ${sweep} ${px(from + r)} ${px(end)}`,
      `H${px(from + size - r)}`,
      `A${px(r)} ${px(r)} 0 0 ${sweep} ${px(from + size)} ${px(corner)}`,
      `V${px(base)}Z`,
    ].join('');
  }
  return [
    `M${px(base)} ${px(from)}`,
    `H${px(corner)}`,
    `A${px(r)} ${px(r)} 0 0 ${sweep} ${px(end)} ${px(from + r)}`,
    `V${px(from + size - r)}`,
    `A${px(r)} ${px(r)} 0 0 ${sweep} ${px(corner)} ${px(from + size)}`,
    `H${px(base)}Z`,
  ].join('');
}

function truncate(text: string, room: number, char = CHAR): string {
  const chars = Math.max(1, Math.floor(room / char));
  return text.length <= chars ? text : `${text.slice(0, Math.max(1, chars - 1)).trimEnd()}…`;
}

export interface SeriesLayout {
  width: number;
  height: number;
  plotLeft: number;
  plotRight: number;
  plotTop: number;
  plotBottom: number;
  /** Where zero is: the axis every column stands on or hangs from. */
  zeroY: number;
  ticks: Array<{ value: number; y: number; text: string }>;
  slots: Array<{ cx: number; x: number; width: number; label: string[] | null; labelX: number; title: string }>;
  /** kolonner: one per drawn column. A `null` or zero value has none. */
  columns: Array<{
    series: number;
    slot: number;
    x: number;
    width: number;
    base: number;
    end: number;
    front: boolean;
    /** Stacked middle segments meet flat; only the outer end of a stack rounds. */
    rounded: boolean;
  }>;
  /** linje: a point per value, `null` where the line breaks. */
  points: Array<Array<{ x: number; y: number } | null>>;
}

/**
 * Which month labels to show and on how many lines. Too many to fit become
 * every 2nd, 3rd, 6th or 12th month, counted from January so January is
 * always one of them and «jan.» does not wander from chart to chart; the year
 * goes under the first label and under every January, so thirteen months
 * across a new year read «nov. 2025 … jan. 2026 …».
 */
function xLabels(axis: ChartAxis, slotWidth: number): Array<string[] | null> {
  if (axis.kind === 'kategori') {
    const every = Math.max(1, Math.ceil(MIN_LABEL_SPACING / slotWidth));
    return axis.labels.map((label, index) =>
      index % every === 0 ? [truncate(label, slotWidth * every - 6)] : null,
    );
  }
  const every = [1, 2, 3, 6, 12].find((k) => k * slotWidth >= MIN_LABEL_SPACING) ?? 12;
  const months = axis.labels.map(parseMonth);
  const shown = months.map((month, index) => (month === null ? index % every === 0 : month.month % every === 0));
  // The first column carries the year, so it gets a label of its own whenever
  // the next one leaves room for it.
  const next = shown.indexOf(true);
  if (next !== 0 && (next < 0 || next * slotWidth >= MIN_LABEL_SPACING) && shown.length > 0) shown[0] = true;
  let first = true;
  return axis.labels.map((label, index) => {
    if (!shown[index]) return null;
    const month = months[index] ?? null;
    const lines = [monthShort(label)];
    if (month !== null && (first || month.month === 0)) lines.push(month.year);
    first = false;
    return lines;
  });
}

function slotTitle(chart: Chart, axis: ChartAxis, index: number, texts: Array<Array<string | null>>): string {
  const name = xName(axis, index);
  if (chart.series.length === 1) return `${name}: ${texts[0]?.[index] ?? NO_BOOKS.toLowerCase()}`;
  const lines = chart.series.map(
    (series, s) => `${series.label}: ${texts[s]?.[index] ?? NO_BOOKS.toLowerCase()}`,
  );
  return [name, ...lines].join('\n');
}

/**
 * Where everything in a kolonner or linje chart goes, at a given width,
 * without touching the DOM, so the tests can check the geometry and the
 * renderer only has to draw it.
 */
export function layoutSeries(chart: Chart, width = DEFAULT_WIDTH): SeriesLayout {
  const axis = chart.x ?? { kind: 'kategori' as const, labels: [] };
  const values = chart.series.map((series) => series.values.map(amount));
  const stacked = chart.stacked === true && chart.view === 'kolonner';
  let minValue = 0;
  let maxValue = 0;
  if (stacked) {
    for (let i = 0; i < axis.labels.length; i++) {
      let pos = 0;
      let neg = 0;
      for (const series of values) {
        const v = series[i] ?? null;
        if (v === null) continue;
        if (v > 0) pos += v;
        else if (v < 0) neg += v;
      }
      if (pos > maxValue) maxValue = pos;
      if (neg < minValue) minValue = neg;
    }
  } else {
    const flat = values.flat().filter((value): value is number => value !== null);
    minValue = Math.min(0, ...flat);
    maxValue = Math.max(0, ...flat);
  }
  const ticks = niceTicks(minValue, maxValue, 5, chart.unit === 'antall');
  const texts = tickTexts(ticks, chart.unit);
  const lo = ticks[0] ?? 0;
  const hi = ticks.length > 1 ? (ticks[ticks.length - 1] ?? 1) : lo + 1;

  const gutter = Math.ceil(Math.max(...texts.map((text) => text.length)) * CHAR) + 10;
  const plotLeft = gutter;
  const plotRight = width - 4;
  const plotTop = PLOT_TOP;
  const plotBottom = SERIES_HEIGHT - X_BAND;
  const y = (value: number): number => plotBottom - ((value - lo) / (hi - lo)) * (plotBottom - plotTop);
  const zeroY = y(0);

  const count = Math.max(1, axis.labels.length);
  const slotWidth = (plotRight - plotLeft) / count;
  const labels = xLabels(axis, slotWidth);
  const valueTexts = chart.series.map((series) => columnTexts(chart.unit, series.values, true));
  const slots = axis.labels.map((_, index) => {
    const cx = plotLeft + slotWidth * (index + 0.5);
    const label = labels[index] ?? null;
    const half = (Math.max(0, ...(label ?? []).map((line) => line.length)) * CHAR) / 2;
    return {
      cx,
      x: cx - slotWidth / 2,
      width: slotWidth,
      label,
      // The last label of a wide range would hang off the right edge.
      labelX: Math.max(half + 1, Math.min(cx, width - half - 1)),
      title: slotTitle(chart, axis, index, valueTexts),
    };
  });

  // Columns: stacked per slot when `stacked`, else comparison overlapping behind
  // the current year, else side by side.
  const columns: SeriesLayout['columns'] = [];
  if (stacked) {
    const columnWidth = Math.min(24, slotWidth * 0.6);
    for (let index = 0; index < axis.labels.length; index++) {
      const cx = slots[index]?.cx ?? 0;
      const x = cx - columnWidth / 2;
      let posSum = 0;
      let negSum = 0;
      const posSegments: Array<{ series: number; from: number; to: number }> = [];
      const negSegments: Array<{ series: number; from: number; to: number }> = [];
      for (let s = 0; s < values.length; s++) {
        const v = values[s]?.[index] ?? null;
        if (v === null || v === 0) continue;
        if (v > 0) {
          posSegments.push({ series: s, from: posSum, to: posSum + v });
          posSum += v;
        } else {
          negSegments.push({ series: s, from: negSum, to: negSum + v });
          negSum += v;
        }
      }
      posSegments.forEach((seg, i) => {
        const base = y(seg.from);
        let end = y(seg.to);
        if (Math.abs(end - base) < 1) end = base - 1;
        columns.push({
          series: seg.series,
          slot: index,
          x,
          width: columnWidth,
          base,
          end,
          front: false,
          rounded: i === posSegments.length - 1,
        });
      });
      negSegments.forEach((seg, i) => {
        const base = y(seg.from);
        let end = y(seg.to);
        if (Math.abs(end - base) < 1) end = base + 1;
        columns.push({
          series: seg.series,
          slot: index,
          x,
          width: columnWidth,
          base,
          end,
          front: false,
          rounded: i === negSegments.length - 1,
        });
      });
    }
  } else {
    const compare = chart.series.findIndex((series) => series.role === 'sammenligning');
    const overlap = chart.series.length === 2 && compare >= 0;
    const drawOrder = overlap ? [compare, 1 - compare] : chart.series.map((_, index) => index);
    const n = chart.series.length;
    const columnWidth = overlap
      ? Math.min(20, slotWidth * 0.5)
      : Math.min(n === 1 ? 24 : 16, n === 1 ? slotWidth * 0.6 : (slotWidth * 0.8 - 2 * (n - 1)) / n);
    for (const s of drawOrder) {
      values[s]?.forEach((value, index) => {
        if (value === null || value === 0) return;
        const cx = slots[index]?.cx ?? 0;
        let x: number;
        if (overlap) {
          // Offset by 60 % of a column: less, and the year before shows as a
          // sliver nobody reads as a column.
          const shift = columnWidth * 0.3;
          x = cx - columnWidth / 2 + (s === compare ? -shift : shift);
        } else {
          x = cx - (n * columnWidth + 2 * (n - 1)) / 2 + s * (columnWidth + 2);
        }
        let end = y(value);
        // A figure too small for a pixel still is not zero.
        if (Math.abs(end - zeroY) < 1) end = zeroY + (value < 0 ? 1 : -1);
        columns.push({
          series: s,
          slot: index,
          x,
          width: columnWidth,
          base: zeroY,
          end,
          front: overlap && s !== compare,
          rounded: true,
        });
      });
    }
  }

  const points = values.map((series) =>
    series.map((value, index) => (value === null ? null : { x: slots[index]?.cx ?? 0, y: y(value) })),
  );

  return {
    width,
    height: SERIES_HEIGHT,
    plotLeft,
    plotRight,
    plotTop,
    plotBottom,
    zeroY,
    ticks: ticks.map((value, index) => ({ value, y: y(value), text: texts[index] ?? '' })),
    slots,
    columns,
    points,
  };
}

export interface BarsLayout {
  width: number;
  height: number;
  zeroX: number;
  rows: Array<{
    top: number;
    label: string;
    value: string;
    title: string;
    bar: { from: number; end: number } | null;
  }>;
}

/**
 * stolper: a row per slice, the name on a line of its own above the bar and
 * the figure at the right end of that line, so a long supplier name gets the
 * whole width instead of a third of it, and on a phone too. One hue: the bars
 * are one quantity, and colouring them would suggest they were not.
 */
export function layoutBars(chart: Chart, width = DEFAULT_WIDTH): BarsLayout {
  const slices = chart.slices ?? [];
  const values = slices.map((slice) => amount(slice.value) ?? 0);
  const lo = Math.min(0, ...values);
  const hi = Math.max(0, ...values);
  const span = hi - lo;
  const x = (value: number): number => (span === 0 ? 0 : ((value - lo) / span) * width);
  const zeroX = x(0);
  const texts = columnTexts(chart.unit, slices.map((slice) => slice.value), true);
  const shares = percentTexts(slices.map((slice) => slice.share));
  const rows = slices.map((slice, index) => {
    const value = texts[index] ?? slice.value;
    const number = values[index] ?? 0;
    const room = width - value.length * BAR_CHAR - 12;
    let end = x(number);
    if (number !== 0 && Math.abs(end - zeroX) < 1) end = zeroX + (number < 0 ? -1 : 1);
    return {
      top: index * BAR_ROW,
      label: truncate(slice.label, room, BAR_CHAR),
      value,
      title: `${slice.label}: ${value} (${shares[index] ?? ''})`,
      bar: number === 0 ? null : { from: zeroX, end },
    };
  });
  return { width, height: Math.max(BAR_ROW, slices.length * BAR_ROW) - 6, zeroX, rows };
}

/**
 * Each slice's angles, from twelve o'clock clockwise, in proportion to its
 * share. A slice of zero or less has no angle (a credit-note supplier cannot
 * be a negative wedge); it still has its row in the legend and the table.
 */
export function donutAngles(slices: ChartSlice[]): Array<{ start: number; end: number } | null> {
  const shares = slices.map((slice) => Math.max(0, amount(slice.share) ?? 0));
  const sum = shares.reduce((a, b) => a + b, 0);
  let at = 0;
  return shares.map((share) => {
    if (share <= 0 || sum <= 0) return null;
    const start = at;
    at += (share / sum) * Math.PI * 2;
    return { start, end: at };
  });
}

// --- DOM ----------------------------------------------------------------------------

function svgNode<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attributes: Record<string, string | number> = {},
  parent?: Element,
): SVGElementTagNameMap[K] {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) {
    node.setAttribute(name, typeof value === 'number' ? px(value) : value);
  }
  parent?.appendChild(node);
  return node;
}

function svgText(parent: Element, text: string, attributes: Record<string, string | number>): SVGTextElement {
  const node = svgNode('text', attributes, parent);
  node.textContent = text;
  return node;
}

function svgTitle(parent: Element, text: string): void {
  svgNode('title', {}, parent).textContent = text;
}

function htmlNode<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className = '',
  text?: string,
  parent?: Element,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className !== '') node.className = className;
  if (text !== undefined) node.textContent = text;
  parent?.appendChild(node);
  return node;
}

function svgRoot(width: number, height: number, label: string): SVGSVGElement {
  return svgNode('svg', {
    viewBox: `0 0 ${px(width)} ${px(height)}`,
    role: 'img',
    'aria-label': label,
    class: 'chart-svg',
  });
}

/** Hairlines on whole pixels plus a half, so a 1px line is one pixel and not two grey ones. */
function crisp(value: number): number {
  return Math.round(value) + 0.5;
}

function drawSeries(chart: Chart, width: number, label: string): SVGSVGElement {
  const layout = layoutSeries(chart, width);
  const root = svgRoot(layout.width, layout.height, label);
  const colours = seriesColours(chart.series);

  const grid = svgNode('g', { class: 'chart-grid' }, root);
  for (const tick of layout.ticks) {
    if (tick.value !== 0) {
      svgNode('line', { x1: layout.plotLeft, x2: layout.plotRight, y1: crisp(tick.y), y2: crisp(tick.y) }, grid);
    }
    svgText(root, tick.text, { x: layout.plotLeft - 8, y: tick.y + 4, 'text-anchor': 'end', class: 'chart-tick' });
  }

  const slots = layout.slots.map((slot) => {
    const group = svgNode('g', { class: 'chart-slot' }, root);
    svgTitle(group, slot.title);
    // The whole month is the target, not the column: a 6px column on a phone
    // is too small to find, and a null month has no column at all.
    svgNode('rect', {
      class: 'chart-hit',
      x: slot.x,
      y: layout.plotTop,
      width: slot.width,
      height: layout.plotBottom - layout.plotTop,
    }, group);
    return group;
  });

  const axis = (): void => {
    svgNode('line', {
      class: 'chart-axis',
      x1: layout.plotLeft,
      x2: layout.plotRight,
      y1: crisp(layout.zeroY),
      y2: crisp(layout.zeroY),
    }, root);
  };

  if (chart.view === 'kolonner') {
    for (const column of layout.columns) {
      const parent = slots[column.slot] ?? root;
      const d = column.rounded
        ? barPath(true, column.x, column.width, column.base, column.end)
        : `M${px(column.x)} ${px(column.base)}V${px(column.end)}H${px(column.x + column.width)}V${px(column.base)}Z`;
      svgNode('path', {
        class: `chart-mark ${colours[column.series] ?? 'chart-c1'}${column.front ? ' chart-front' : ''}`,
        d,
      }, parent);
    }
    // After the columns, so a loss and a profit meet at one crisp line.
    axis();
  } else {
    axis();
    const order = chart.series.map((_, index) => index).sort((a, b) => {
      const rank = (index: number): number => (chart.series[index]?.role === 'sammenligning' ? 0 : 1);
      return rank(a) - rank(b);
    });
    for (const s of order) {
      const points = layout.points[s] ?? [];
      const colour = colours[s] ?? 'chart-c1';
      let d = '';
      let pen = false;
      points.forEach((point) => {
        if (point === null) {
          pen = false;
          return;
        }
        d += `${pen ? 'L' : 'M'}${px(point.x)} ${px(point.y)}`;
        pen = true;
      });
      if (d !== '') svgNode('path', { class: `chart-line ${colour}`, d }, root);
      // A point with no neighbour is no line at all, so it gets a dot; so does
      // the last point, where the eye looks for «where are we now».
      const last = points.reduce((found, point, index) => (point === null ? found : index), -1);
      points.forEach((point, index) => {
        if (point === null) return;
        const alone = (points[index - 1] ?? null) === null && (points[index + 1] ?? null) === null;
        if (alone || index === last) {
          svgNode('circle', { class: `chart-dot ${colour}`, cx: point.x, cy: point.y, r: 4 }, root);
        }
      });
    }
  }

  const labels = svgNode('g', { class: 'chart-xlabels' }, root);
  for (const slot of layout.slots) {
    if (slot.label === null) continue;
    slot.label.forEach((line, index) => {
      svgText(labels, line, {
        x: slot.labelX,
        y: layout.plotBottom + 17 + index * 15,
        'text-anchor': 'middle',
        class: index === 0 ? 'chart-tick' : 'chart-tick chart-year',
      });
    });
  }
  return root;
}

function drawBars(chart: Chart, width: number, label: string): SVGSVGElement {
  const layout = layoutBars(chart, width);
  const root = svgRoot(layout.width, layout.height, label);
  for (const row of layout.rows) {
    const group = svgNode('g', { class: 'chart-slot' }, root);
    svgTitle(group, row.title);
    svgNode('rect', { class: 'chart-hit', x: 0, y: row.top, width: layout.width, height: BAR_ROW - 6 }, group);
    svgText(group, row.label, { x: 0, y: row.top + 13, class: 'chart-label' });
    svgText(group, row.value, { x: layout.width, y: row.top + 13, 'text-anchor': 'end', class: 'chart-label chart-value' });
    if (row.bar !== null) {
      svgNode('path', {
        class: 'chart-mark chart-c1',
        d: barPath(false, row.top + BAR_TOP, BAR_THICKNESS, row.bar.from, row.bar.end),
      }, group);
    }
    // Zero, only when a bar goes left of it (otherwise the bars' square ends
    // already line up on it), and only beside the bars: a line the whole
    // height would run through the first letter of every name.
    if (layout.zeroX > 0.5) {
      svgNode('line', {
        class: 'chart-axis',
        x1: crisp(layout.zeroX),
        x2: crisp(layout.zeroX),
        y1: row.top + BAR_TOP - 2,
        y2: row.top + BAR_TOP + BAR_THICKNESS + 2,
      }, group);
    }
  }
  return root;
}

function point(radius: number, angle: number): string {
  return `${px(100 + radius * Math.sin(angle))} ${px(100 - radius * Math.cos(angle))}`;
}

function arcPath(start: number, end: number): string {
  const outer = 96;
  const inner = 60;
  // A whole ring cannot be one arc (start and end are the same point), so it
  // is drawn as two halves.
  if (end - start >= Math.PI * 2 - 1e-6) {
    return arcPath(start, start + Math.PI) + arcPath(start + Math.PI, end - 1e-6);
  }
  const large = end - start > Math.PI ? 1 : 0;
  return [
    `M${point(outer, start)}`,
    `A${outer} ${outer} 0 ${large} 1 ${point(outer, end)}`,
    `L${point(inner, end)}`,
    `A${inner} ${inner} 0 ${large} 0 ${point(inner, start)}Z`,
  ].join('');
}

function drawDonut(chart: Chart, label: string): SVGSVGElement {
  const slices = chart.slices ?? [];
  const root = svgRoot(200, 200, label);
  const texts = columnTexts(chart.unit, slices.map((slice) => slice.value), true);
  const shares = percentTexts(slices.map((slice) => slice.share));
  donutAngles(slices).forEach((angles, index) => {
    const slice = slices[index];
    if (angles === null || slice === undefined) return;
    const path = svgNode('path', { class: `chart-slice ${sliceColour(index)}`, d: arcPath(angles.start, angles.end) }, root);
    svgTitle(path, `${slice.label}: ${texts[index] ?? slice.value} (${shares[index] ?? ''})`);
  });
  return root;
}

function swatch(colour: string, parent: Element): void {
  const node = htmlNode('span', `chart-swatch ${colour}`, undefined, parent);
  node.setAttribute('aria-hidden', 'true');
}

function seriesKey(chart: Chart): HTMLUListElement {
  const list = htmlNode('ul', 'chart-key');
  const colours = seriesColours(chart.series);
  chart.series.forEach((series, index) => {
    const item = htmlNode('li', '', undefined, list);
    swatch(colours[index] ?? 'chart-c1', item);
    htmlNode('span', '', series.label, item);
  });
  return list;
}

function donutLegend(chart: Chart): HTMLUListElement {
  const slices = chart.slices ?? [];
  const texts = columnTexts(chart.unit, slices.map((slice) => slice.value), true);
  const shares = percentTexts(slices.map((slice) => slice.share));
  const list = htmlNode('ul', 'chart-legend');
  slices.forEach((slice, index) => {
    const item = htmlNode('li', '', undefined, list);
    swatch(sliceColour(index), item);
    htmlNode('span', 'chart-legend-label', slice.label, item);
    htmlNode('span', 'chart-legend-value', texts[index] ?? slice.value, item);
    htmlNode('span', 'chart-legend-share', shares[index] ?? '', item);
  });
  if (chart.total !== null) {
    const item = htmlNode('li', 'chart-legend-sum', undefined, list);
    htmlNode('span', 'chart-legend-label', 'Sum', item);
    htmlNode('span', 'chart-legend-value', valueText(chart.unit, chart.total, true), item);
  }
  return list;
}

// --- The table ----------------------------------------------------------------------

function headerCell(row: HTMLTableRowElement, text: string, numeric = false): void {
  const cell = htmlNode('th', numeric ? 'num' : '', text, row);
  cell.setAttribute('scope', 'col');
}

function unitHeading(unit: ChartUnit): string {
  return unit === 'kr' ? 'Beløp' : unit === 'prosent' ? 'Prosent' : 'Antall';
}

/**
 * The same figures as the drawing, every one of them, as the backend sent
 * them: months as rows, series as columns, «Ingen bilag» for a month with no
 * books (never «0,00 kr», which is a different fact). Slices get their share
 * and the backend's total under them; this file never adds anything up.
 */
export function dataTable(chart: Chart, caption = true): HTMLTableElement {
  const table = htmlNode('table', 'data');
  if (caption) htmlNode('caption', 'visually-hidden', chart.title, table);
  const head = htmlNode('tr', '', undefined, htmlNode('thead', '', undefined, table));
  const body = htmlNode('tbody', '', undefined, table);

  if (chart.slices !== null) {
    const texts = columnTexts(chart.unit, chart.slices.map((slice) => slice.value));
    const shares = percentTexts(chart.slices.map((slice) => slice.share));
    headerCell(head, 'Navn');
    headerCell(head, unitHeading(chart.unit), true);
    headerCell(head, 'Andel', true);
    chart.slices.forEach((slice, index) => {
      const row = htmlNode('tr', '', undefined, body);
      htmlNode('th', '', slice.label, row).setAttribute('scope', 'row');
      htmlNode('td', 'num', texts[index] ?? slice.value, row);
      htmlNode('td', 'num', shares[index] ?? '', row);
    });
    if (chart.total !== null) {
      const row = htmlNode('tr', '', undefined, htmlNode('tfoot', '', undefined, table));
      htmlNode('th', '', 'Sum', row).setAttribute('scope', 'row');
      htmlNode('td', 'num', valueText(chart.unit, chart.total), row);
      htmlNode('td', 'num', '', row);
    }
    return table;
  }

  const axis = chart.x;
  headerCell(head, axis?.kind === 'maned' ? 'Måned' : 'Navn');
  for (const series of chart.series) headerCell(head, series.label, true);
  const texts = chart.series.map((series) => columnTexts(chart.unit, series.values));
  const rows = axis?.labels.length ?? Math.max(0, ...chart.series.map((series) => series.values.length));
  for (let index = 0; index < rows; index++) {
    const row = htmlNode('tr', '', undefined, body);
    // «september 2026» on one line: a table of months that wrap is twice as tall.
    const month = axis?.kind === 'maned' ? 'chart-month' : '';
    htmlNode('th', month, axis === null ? String(index + 1) : xName(axis, index), row).setAttribute('scope', 'row');
    texts.forEach((column) => {
      const text = column[index] ?? null;
      htmlNode('td', text === null ? 'num chart-none' : 'num', text ?? NO_BOOKS, row);
    });
  }
  return table;
}

function scrolling(table: HTMLTableElement): HTMLDivElement {
  // Three series and a month column are wider than a phone.
  const wrap = htmlNode('div', 'table-scroll');
  wrap.appendChild(table);
  return wrap;
}

// --- The gist -----------------------------------------------------------------------

function extreme(series: ChartSeries, pick: (a: number, b: number) => boolean): number {
  let found = -1;
  series.values.forEach((value, index) => {
    const number = amount(value);
    if (number === null) return;
    const best = found < 0 ? null : amount(series.values[found] ?? null);
    if (best === null || pick(number, best)) found = index;
  });
  return found;
}

/**
 * What the SVG says, in one or two sentences, for `aria-label`: the period,
 * and the highest and (when it is a loss) the lowest of the main series, with
 * the backend's figures. The table under it has the rest.
 */
export function gist(chart: Chart): string {
  const view = drawnView(chart);
  if ((view === 'kolonner' || view === 'linje') && chart.x !== null) {
    const axis = chart.x;
    const main = chart.series.find((series) => series.role !== 'sammenligning') ?? chart.series[0];
    const first = axis.labels[0] ?? '';
    const last = axis.labels[axis.labels.length - 1] ?? '';
    let range = '';
    if (axis.kind === 'maned' && first !== '' && last !== '') {
      const a = parseMonth(first);
      const b = parseMonth(last);
      range = a !== null && b !== null && a.year === b.year
        ? `, ${MONTHS_LONG[a.month]} til ${monthLong(last)}`
        : `, ${monthLong(first)} til ${monthLong(last)}`;
    }
    const parts = [`${chart.title}${range}.`];
    if (main !== undefined) {
      const high = extreme(main, (a, b) => a > b);
      const low = extreme(main, (a, b) => a < b);
      const highValue = main.values[high] ?? null;
      const lowValue = main.values[low] ?? null;
      const allZero = main.values.every((value) => value === null || amount(value) === 0);
      if (high < 0) {
        parts.push('Ingen bilag i perioden.');
      } else if (allZero) {
        parts.push(`Alle er ${valueText(chart.unit, '0', true)}.`);
      } else if (highValue !== null) {
        parts.push(`Høyest ${xName(axis, high)}: ${valueText(chart.unit, highValue, true)}.`);
        if (lowValue !== null && (amount(lowValue) ?? 0) < 0) {
          parts.push(`Lavest ${xName(axis, low)}: ${valueText(chart.unit, lowValue, true)}.`);
        }
      }
    }
    return parts.join(' ');
  }
  const slices = chart.slices ?? [];
  if (view === 'smultring') {
    const shares = percentTexts(slices.map((slice) => slice.share));
    return `${chart.title}: ${slices.map((slice, index) => `${slice.label} ${shares[index] ?? ''}`).join(', ')}.`;
  }
  if (view === 'stolper') {
    const texts = columnTexts(chart.unit, slices.map((slice) => slice.value), true);
    const top = slices.slice(0, 3).map((slice, index) => `${slice.label} ${texts[index] ?? slice.value}`);
    const more = slices.length > 3 ? `, og ${slices.length - 3} til` : '';
    return `${chart.title}. Størst: ${top.join(', ')}${more}.`;
  }
  return chart.title;
}

// --- The figure ---------------------------------------------------------------------

/**
 * The view a spec can actually be drawn as. The backend validates the shape
 * before it sends it (docs/analyse.md, «Rules of the shape»), but a page that
 * throws on a bad spec loses every chart after it, and a table of whatever
 * did arrive is still true.
 */
function drawnView(chart: Chart): ChartView {
  switch (chart.view) {
    case 'kolonner':
    case 'linje': {
      const x = chart.x;
      return x !== null && chart.series.length > 0 && chart.series.every((s) => s.values.length === x.labels.length)
        ? chart.view
        : 'tabell';
    }
    case 'smultring':
    case 'stolper':
      return chart.slices !== null && chart.slices.length > 0 ? chart.view : 'tabell';
    case 'tall':
      return chart.total !== null ? 'tall' : 'tabell';
    default:
      return 'tabell';
  }
}

/**
 * Whether the spec holds a single figure to show. A split with nothing booked
 * comes as `slices: []` (docs/analyse.md, «Rules of the shape»), and a model's
 * dataset can ask for a period with no months; the checks follow `dataTable`,
 * which reads `slices` before `series`.
 */
function hasFigures(chart: Chart): boolean {
  if (drawnView(chart) === 'tall') return true;
  if (chart.slices !== null) return chart.slices.length > 0;
  const rows = chart.x?.labels.length ?? Math.max(0, ...chart.series.map((series) => series.values.length));
  return chart.series.length > 0 && rows > 0;
}

/** The title and one sentence, with the caption if the spec has one, and no «Vis tallene» of nothing. */
function emptyChart(chart: Chart): HTMLElement {
  const figure = htmlNode('figure', 'chart chart-empty');
  figure.dataset.chart = chart.id;
  htmlNode('figcaption', 'chart-title', chart.title, figure);
  htmlNode('p', 'chart-nothing', NOTHING_TO_SHOW, figure);
  if (chart.caption !== null && chart.caption !== '') {
    htmlNode('p', 'chart-caption', chart.caption, figure);
  }
  return figure;
}

/**
 * Draws `draw(width)` into `holder` and draws it again whenever the holder's
 * width changes, so the viewBox always is the box. The ResizeObserver's first
 * call comes after layout and before paint, so the 480-wide first drawing is
 * never seen. Without a ResizeObserver (tests, very old browsers) the first
 * drawing stays, scaled.
 */
function drawToWidth(holder: HTMLElement, draw: (width: number) => SVGSVGElement): void {
  let width = DEFAULT_WIDTH;
  let svg = draw(width);
  holder.appendChild(svg);
  if (typeof ResizeObserver === 'undefined') return;
  new ResizeObserver((entries) => {
    const measured = Math.round(entries[0]?.contentRect.width ?? 0);
    if (measured <= 0 || measured === width) return;
    width = measured;
    const next = draw(width);
    svg.replaceWith(next);
    svg = next;
  }).observe(holder);
}

/**
 * One chart spec as a `<figure class="chart">`: the title as `figcaption`, a
 * key when there are two or more series, the drawing, the caption, and
 * «Vis tallene» with every figure in a table.
 *
 * It sets no `id`: a report opened in place on /analyse/ can hold a chart with
 * the same spec id as the page's own, and two equal ids break every anchor and
 * `aria-*` reference that uses them. The spec id is on `data-chart`; a page
 * that wants an anchor gives the figure an id it knows is unique.
 */
export function renderChart(chart: Chart): HTMLElement {
  if (!hasFigures(chart)) return emptyChart(chart);
  const view = drawnView(chart);
  const figure = htmlNode('figure', `chart chart-${view}`);
  figure.dataset.chart = chart.id;
  htmlNode('figcaption', 'chart-title', chart.title, figure);

  const label = gist(chart);
  if (view === 'kolonner' || view === 'linje') {
    if (chart.series.length > 1) figure.appendChild(seriesKey(chart));
    const plot = htmlNode('div', 'chart-plot', undefined, figure);
    drawToWidth(plot, (width) => drawSeries(chart, width, label));
  } else if (view === 'stolper') {
    const plot = htmlNode('div', 'chart-plot', undefined, figure);
    drawToWidth(plot, (width) => drawBars(chart, width, label));
  } else if (view === 'smultring') {
    const plot = htmlNode('div', 'chart-donut', undefined, figure);
    plot.appendChild(drawDonut(chart, label));
    plot.appendChild(donutLegend(chart));
  } else if (view === 'tall') {
    htmlNode('p', 'chart-figure', valueText(chart.unit, chart.total ?? '0', true), figure);
  } else {
    figure.appendChild(scrolling(dataTable(chart, false)));
  }

  if (chart.caption !== null && chart.caption !== '') {
    htmlNode('p', 'chart-caption', chart.caption, figure);
  }

  if (view !== 'tabell' && view !== 'tall') {
    const details = htmlNode('details', 'chart-numbers', undefined, figure);
    htmlNode('summary', '', 'Vis tallene', details);
    details.appendChild(scrolling(dataTable(chart)));
  }
  return figure;
}
