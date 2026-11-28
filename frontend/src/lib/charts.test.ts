/**
 * The chart kit, drawn from the fixtures in charts.fixtures.ts.
 *
 * What is worth pinning down here is where a chart can lie without looking
 * broken: a month with no books drawn as a zero, a loss drawn above the axis,
 * an empty year dividing by zero, a share rounded in a float, a table that
 * says something other than the drawing, and a counterparty name that turns
 * into markup.
 *
 * There is no jsdom (vitest.config.ts says why), and the kit needs only a
 * handful of DOM calls, so `document` is a small fake that records the tree.
 * Its `innerHTML` throws: if anything in the kit ever parses a string as
 * markup, every test here fails, not just the one about escaping.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Chart } from './api';
import {
  dataTable,
  donutAngles,
  gist,
  layoutBars,
  layoutSeries,
  monthLong,
  monthShort,
  niceTicks,
  percentTexts,
  renderChart,
  tickTexts,
  valueText,
} from './charts';
import {
  FIXTURES,
  alleNuller,
  inntekterPerHva,
  inntekterPerKunde,
  inntekterPerManed,
  inntekterTall,
  kostnaderLinje,
  kostnaderPerKonto,
  kostnaderPerLeverandor,
  resultatTabell,
  resultatTrettenManeder,
} from './charts.fixtures';

const HTML_NS = 'http://www.w3.org/1999/xhtml';

class FakeText {
  parent: FakeElement | null = null;
  constructor(readonly data: string) {}
  get textContent(): string {
    return this.data;
  }
}

class FakeElement {
  parent: FakeElement | null = null;
  readonly attributes = new Map<string, string>();
  readonly childNodes: Array<FakeElement | FakeText> = [];
  readonly dataset: Record<string, string> = {};

  constructor(
    readonly namespaceURI: string,
    readonly localName: string,
  ) {}

  get children(): FakeElement[] {
    return this.childNodes.filter((node): node is FakeElement => node instanceof FakeElement);
  }

  setAttribute(name: string, value: string): void {
    this.attributes.set(name, String(value));
  }

  getAttribute(name: string): string | null {
    return this.attributes.get(name) ?? null;
  }

  get className(): string {
    return this.getAttribute('class') ?? '';
  }

  set className(value: string) {
    this.setAttribute('class', value);
  }

  get classes(): string[] {
    return this.className.split(/\s+/).filter(Boolean);
  }

  appendChild<T extends FakeElement | FakeText>(node: T): T {
    if (node.parent !== null) {
      const siblings = node.parent.childNodes;
      siblings.splice(siblings.indexOf(node), 1);
    }
    node.parent = this;
    this.childNodes.push(node);
    return node;
  }

  replaceWith(node: FakeElement): void {
    const parent = this.parent;
    if (parent === null) return;
    parent.childNodes[parent.childNodes.indexOf(this)] = node;
    node.parent = parent;
    this.parent = null;
  }

  get textContent(): string {
    return this.childNodes.map((node) => node.textContent).join('');
  }

  set textContent(value: string) {
    this.childNodes.length = 0;
    this.appendChild(new FakeText(value));
  }

  get innerHTML(): string {
    throw new Error('innerHTML read: the chart kit must build with DOM calls');
  }

  set innerHTML(_value: string) {
    throw new Error('innerHTML written: the chart kit must never parse a string as markup');
  }

  insertAdjacentHTML(): void {
    throw new Error('insertAdjacentHTML: the chart kit must never parse a string as markup');
  }
}

const fakeDocument = {
  createElement: (tag: string) => new FakeElement(HTML_NS, tag.toLowerCase()),
  createElementNS: (namespace: string, tag: string) => new FakeElement(namespace, tag),
  createTextNode: (text: string) => new FakeText(text),
};

beforeEach(() => {
  vi.stubGlobal('document', fakeDocument);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function draw(chart: Chart): FakeElement {
  return renderChart(chart) as unknown as FakeElement;
}

function all(root: FakeElement, test: (node: FakeElement) => boolean): FakeElement[] {
  const found: FakeElement[] = [];
  const walk = (node: FakeElement): void => {
    if (test(node)) found.push(node);
    node.children.forEach(walk);
  };
  walk(root);
  return found;
}

const byTag = (root: FakeElement, tag: string): FakeElement[] => all(root, (node) => node.localName === tag);
const byClass = (root: FakeElement, name: string): FakeElement[] => all(root, (node) => node.classes.includes(name));

function one(root: FakeElement, test: (node: FakeElement) => boolean): FakeElement {
  const found = all(root, test);
  expect(found).toHaveLength(1);
  return found[0] as FakeElement;
}

/** The text with no-break spaces as plain ones and Unicode minus as a hyphen, for readable expectations. */
function plain(text: string): string {
  return text.replace(/ /g, ' ').replace(/−/g, '-');
}

function titleOf(node: FakeElement): string {
  const title = node.children.find((child) => child.localName === 'title');
  return title?.textContent ?? '';
}

function tableRows(figure: FakeElement): string[][] {
  const table = one(figure, (node) => node.localName === 'table');
  const body = byTag(table, 'tbody')[0] as FakeElement;
  return byTag(body, 'tr').map((row) => row.children.map((cell) => cell.textContent));
}

describe('every fixture', () => {
  it.each(FIXTURES.map((chart) => [chart.id, chart] as const))('%s is a figure with its title first', (_, chart) => {
    const figure = draw(chart);
    expect(figure.localName).toBe('figure');
    expect(figure.classes).toContain('chart');
    expect(figure.dataset.chart).toBe(chart.id);
    expect(figure.getAttribute('id')).toBeNull();
    const caption = figure.children[0] as FakeElement;
    expect(caption.localName).toBe('figcaption');
    expect(caption.textContent).toBe(chart.title);
  });

  it.each(FIXTURES.filter((chart) => !['tabell', 'tall'].includes(chart.view)).map((c) => [c.id, c] as const))(
    '%s draws an SVG with a gist and the numbers under it',
    (_, chart) => {
      const figure = draw(chart);
      const svg = one(figure, (node) => node.localName === 'svg');
      expect(svg.getAttribute('viewBox')).toMatch(/^0 0 \d+(\.\d)? \d+(\.\d)?$/);
      expect(svg.getAttribute('role')).toBe('img');
      expect(svg.getAttribute('aria-label')).toBe(gist(chart));
      expect(svg.getAttribute('aria-label')?.startsWith(chart.title)).toBe(true);
      const details = one(figure, (node) => node.localName === 'details');
      expect(byTag(details, 'summary')[0]?.textContent).toBe('Vis tallene');
      expect(byTag(details, 'table')[0]?.classes).toContain('data');
    },
  );

  it('puts the caption under the drawing, and leaves it out when there is none', () => {
    const figure = draw(resultatTrettenManeder);
    const kinds = figure.children.map((node) => node.className || node.localName);
    expect(kinds.indexOf('chart-caption')).toBeGreaterThan(kinds.indexOf('chart-plot'));
    expect(kinds.indexOf('chart-caption')).toBeLessThan(kinds.indexOf('chart-numbers'));
    expect(byClass(figure, 'chart-caption')[0]?.textContent).toBe(resultatTrettenManeder.caption);
    expect(byClass(draw(inntekterPerManed), 'chart-caption')).toHaveLength(0);
  });
});

describe('ticks', () => {
  it('steps by 1, 2, 2,5 or 5 times a power of ten and always includes zero', () => {
    expect(niceTicks(0, 44800)).toEqual([0, 10000, 20000, 30000, 40000, 50000]);
    expect(niceTicks(-12850, 15400)).toEqual([-20000, -10000, 0, 10000, 20000]);
    expect(niceTicks(0, 1_200_000)).toEqual([0, 250000, 500000, 750000, 1_000_000, 1_250_000]);
    expect(niceTicks(9400, 26300)).toEqual([0, 10000, 20000, 30000]);
    expect(niceTicks(0, 0.3)).toEqual([0, 0.1, 0.2, 0.3]);
  });

  it('never steps a count by 2,5', () => {
    expect(niceTicks(0, 3, 5, true)).toEqual([0, 1, 2, 3]);
    expect(niceTicks(0, 12, 5, true)).toEqual([0, 5, 10, 15]);
  });

  it('is the single tick 0 when everything is zero', () => {
    expect(niceTicks(0, 0)).toEqual([0]);
  });

  it('writes «40 000», and «1,25 mill.» for the whole axis once it reaches a million', () => {
    expect(tickTexts([0, 20000, 40000], 'kr')).toEqual(['0', '20 000', '40 000']);
    expect(tickTexts([-20000, 0, 20000], 'kr')).toEqual(['−20 000', '0', '20 000']);
    expect(tickTexts(niceTicks(0, 1_200_000), 'kr').map(plain)).toEqual([
      '0', '0,25 mill.', '0,5 mill.', '0,75 mill.', '1 mill.', '1,25 mill.',
    ]);
    expect(tickTexts([0, 0.1, 0.2], 'prosent').map(plain)).toEqual(['0', '10 %', '20 %']);
  });

  it('draws the ticks it chose', () => {
    const layout = layoutSeries(inntekterPerManed);
    expect(layout.ticks.map((tick) => tick.value)).toEqual([0, 10000, 20000, 30000, 40000, 50000]);
    const svg = byTag(draw(inntekterPerManed), 'svg')[0] as FakeElement;
    const drawn = byClass(svg, 'chart-tick').map((node) => plain(node.textContent));
    expect(drawn.slice(0, 6)).toEqual(['0', '10 000', '20 000', '30 000', '40 000', '50 000']);
    // Top tick at the top of the plot, zero on its floor.
    expect(layout.ticks[5]?.y).toBeCloseTo(layout.plotTop);
    expect(layout.zeroY).toBeCloseTo(layout.plotBottom);
  });
});

describe('months', () => {
  it('names them the way the doc writes them', () => {
    expect(['2026-01', '2026-02', '2026-03', '2026-06', '2026-09', '2026-12'].map(monthShort)).toEqual([
      'jan.', 'feb.', 'mars', 'juni', 'sep.', 'des.',
    ]);
    expect(monthLong('2026-03')).toBe('mars 2026');
    expect(monthShort('Salg av varer')).toBe('Salg av varer');
    expect(monthLong('2026-13')).toBe('2026-13');
  });

  it('puts the year under the first column and every January', () => {
    const wide = layoutSeries(resultatTrettenManeder, 1000);
    const labels = wide.slots.map((slot) => slot.label?.join(' ') ?? null);
    expect(labels).toEqual([
      'nov. 2025', 'des.', 'jan. 2026', 'feb.', 'mars', 'apr.', 'mai',
      'juni', 'juli', 'aug.', 'sep.', 'okt.', 'nov.',
    ]);
  });

  it('thins thirteen months to every second, counted from January', () => {
    const narrow = layoutSeries(resultatTrettenManeder, 480);
    expect(narrow.slots.map((slot) => slot.label?.join(' ') ?? null)).toEqual([
      'nov. 2025', null, 'jan. 2026', null, 'mars', null, 'mai',
      null, 'juli', null, 'sep.', null, 'nov.',
    ]);
    const phone = layoutSeries(resultatTrettenManeder, 280);
    const shown = phone.slots.filter((slot) => slot.label !== null);
    expect(shown.length).toBeLessThanOrEqual(7);
    expect(shown.some((slot) => slot.label?.[0] === 'jan.')).toBe(true);
    // Labels never closer than they can be read, and none hangs off the edge.
    const xs = shown.map((slot) => slot.labelX);
    xs.slice(1).forEach((x, index) => expect(x - (xs[index] ?? 0)).toBeGreaterThanOrEqual(30));
    expect(Math.max(...xs)).toBeLessThan(280);
  });

  it('labels the first column with its year when the next label leaves room', () => {
    const labels = Array.from({ length: 24 }, (_, i) => {
      const month = i + 1; // February 2024 onwards
      return `${2024 + Math.floor(month / 12)}-${String((month % 12) + 1).padStart(2, '0')}`;
    });
    const chart: Chart = {
      ...resultatTrettenManeder,
      x: { kind: 'maned', labels },
      series: [{ key: 'r', label: 'R', role: 'hoved', values: labels.map(() => '100.00') }],
    };
    const shown = layoutSeries(chart, 300).slots.flatMap((slot) => (slot.label === null ? [] : [slot.label.join(' ')]));
    expect(shown).toEqual(['feb. 2024', 'juli', 'jan. 2025', 'juli', 'jan. 2026']);
  });

  it('shows every month of a year at desktop width', () => {
    const svg = byTag(draw(inntekterPerManed), 'svg')[0] as FakeElement;
    const labels = byClass(svg, 'chart-xlabels')[0] as FakeElement;
    expect(labels.children.map((node) => node.textContent)).toEqual([
      'jan.', '2026', 'feb.', 'mars', 'apr.', 'mai', 'juni', 'juli', 'aug.', 'sep.', 'okt.', 'nov.',
    ]);
  });
});

describe('kolonner', () => {
  it('draws one column per value that is neither null nor zero, and a slot per month', () => {
    const figure = draw(inntekterPerManed);
    expect(byClass(figure, 'chart-slot')).toHaveLength(11);
    // 10 months of 2026 (July has no books) and 7 of the year before.
    expect(byClass(figure, 'chart-mark')).toHaveLength(17);
    expect(byClass(figure, 'chart-cc')).toHaveLength(7 + 1); // the columns and the key's swatch
    expect(byClass(figure, 'chart-front')).toHaveLength(10);
  });

  it('draws the comparison behind and to the left of the year itself', () => {
    const layout = layoutSeries(inntekterPerManed);
    const june = layout.columns.filter((column) => column.slot === 5);
    expect(june.map((column) => column.series)).toEqual([1, 0]); // compare first, so it is behind
    expect(june[0]?.x).toBeLessThan(june[1]?.x ?? 0);
    expect(june[0]?.front).toBe(false);
    expect(june[1]?.front).toBe(true);
  });

  it('leaves a null month as a gap, not a zero column', () => {
    const layout = layoutSeries(inntekterPerManed);
    expect(layout.columns.filter((column) => column.slot === 6).map((column) => column.series)).toEqual([1]);
    expect(layout.columns.filter((column) => column.series === 1 && column.slot < 4)).toEqual([]);
    const july = byClass(draw(inntekterPerManed), 'chart-slot')[6] as FakeElement;
    expect(byClass(july, 'chart-mark')).toHaveLength(1);
    expect(plain(titleOf(july))).toBe('juli 2026\n2026: ingen bilag\n2025: 12 300 kr');
  });

  it('draws a loss below the axis and a profit above it', () => {
    const layout = layoutSeries(resultatTrettenManeder);
    expect(layout.zeroY).toBeGreaterThan(layout.plotTop);
    expect(layout.zeroY).toBeLessThan(layout.plotBottom);
    const december = layout.columns.find((column) => column.slot === 1);
    const november = layout.columns.find((column) => column.slot === 0);
    expect(december?.base).toBe(layout.zeroY);
    expect(december?.end).toBeGreaterThan(layout.zeroY); // SVG y grows downwards
    expect(november?.end).toBeLessThan(layout.zeroY);
    expect(layout.columns).toHaveLength(13);
    const losses = layout.columns.filter((column) => column.end > column.base).map((column) => column.slot);
    expect(losses).toEqual([1, 7, 9]);
  });

  it('draws only an axis for a chart of zeros, and does not divide by zero', () => {
    const figure = draw(alleNuller);
    expect(byClass(figure, 'chart-mark')).toHaveLength(0);
    expect(byClass(figure, 'chart-axis')).toHaveLength(1);
    const layout = layoutSeries(alleNuller);
    expect(layout.ticks.map((tick) => tick.text)).toEqual(['0']);
    expect(layout.zeroY).toBeCloseTo(layout.plotBottom);
    expect(Number.isFinite(layout.zeroY)).toBe(true);
    expect(plain(gist(alleNuller))).toBe('Kostnader per måned, januar til juni 2026. Alle er 0 kr.');
    expect(tableRows(figure).map((row) => plain(row[1] ?? ''))).toEqual(Array(6).fill('0,00 kr'));
  });

  it('stands three series side by side in three colours', () => {
    const chart: Chart = { ...resultatTabell, view: 'kolonner' };
    const layout = layoutSeries(chart);
    const august = layout.columns.filter((column) => column.slot === 0);
    expect(august.map((column) => column.series)).toEqual([0, 1, 2]);
    expect((august[1]?.x ?? 0) - (august[0]?.x ?? 0)).toBeGreaterThanOrEqual(august[0]?.width ?? 0);
    const figure = draw(chart);
    const key = byClass(figure, 'chart-key')[0] as FakeElement;
    expect(key.children.map((item) => item.textContent)).toEqual(['Inntekter', 'Kostnader', 'Resultat']);
    const swatches = byClass(key, 'chart-swatch').map((node) => node.classes[1]);
    expect(swatches).toEqual(['chart-c1', 'chart-c2', 'chart-c3']);
  });

  it('gives no key to a single series', () => {
    expect(byClass(draw(resultatTrettenManeder), 'chart-key')).toHaveLength(0);
  });
});

describe('linje', () => {
  it('breaks the line at a null month instead of diving to zero', () => {
    const svg = byTag(draw(kostnaderLinje), 'svg')[0] as FakeElement;
    const lines = byClass(svg, 'chart-line');
    expect(lines).toHaveLength(2);
    // The comparison is drawn first, under the year itself.
    expect(lines[0]?.classes).toContain('chart-cc');
    const main = lines[1]?.getAttribute('d') ?? '';
    expect(main.match(/M/g)).toHaveLength(2);
    expect(main.match(/L/g)).toHaveLength(8);
    expect(lines[0]?.getAttribute('d')?.match(/M/g)).toHaveLength(1);
  });

  it('marks the last point of each line', () => {
    const svg = byTag(draw(kostnaderLinje), 'svg')[0] as FakeElement;
    const dots = byClass(svg, 'chart-dot');
    expect(dots).toHaveLength(2);
    const last = layoutSeries(kostnaderLinje).slots[10]?.cx ?? 0;
    dots.forEach((dot) => expect(Number(dot.getAttribute('cx'))).toBeCloseTo(last, 0));
  });

  it('gives a point with no neighbours a dot of its own', () => {
    const chart: Chart = {
      ...kostnaderLinje,
      series: [{ ...kostnaderLinje.series[0]!, values: ['100.00', null, '200.00', null, null, null, null, null, null, null, '50.00'] }],
    };
    const svg = byTag(draw(chart), 'svg')[0] as FakeElement;
    expect(byClass(svg, 'chart-dot')).toHaveLength(3);
  });
});

describe('smultring', () => {
  it('draws a slice per positive share, in the chart colours in order', () => {
    const figure = draw(kostnaderPerKonto);
    const slices = byClass(figure, 'chart-slice');
    expect(slices).toHaveLength(6);
    expect(slices.map((slice) => slice.classes[1])).toEqual([
      'chart-c1', 'chart-c2', 'chart-c3', 'chart-c4', 'chart-c5', 'chart-c6',
    ]);
    expect(plain(titleOf(slices[0] as FakeElement))).toBe('4000 Innkjøp av varer: 114 570 kr (38,0 %)');
  });

  it('turns the shares into angles that close the ring, and skips a share of zero or less', () => {
    const angles = donutAngles(kostnaderPerKonto.slices ?? []);
    expect(angles[0]?.start).toBe(0);
    expect(angles[5]?.end).toBeCloseTo(Math.PI * 2);
    const withNothing = donutAngles([
      { key: 'a', label: 'A', value: '10.00', share: '1.00' },
      { key: 'b', label: 'B', value: '0.00', share: '0.00' },
    ]);
    expect(withNothing[1]).toBeNull();
    expect(withNothing[0]?.end).toBeCloseTo(Math.PI * 2);
  });

  it('lists label, kroner and share in the legend, and the backend total as the sum', () => {
    const legend = byClass(draw(kostnaderPerKonto), 'chart-legend')[0] as FakeElement;
    const rows = legend.children.map((item) => item.children.slice(1).map((cell) => plain(cell.textContent)));
    expect(rows[0]).toEqual(['4000 Innkjøp av varer', '114 570 kr', '38,0 %']);
    expect(rows[3]).toEqual(['6540 Inventar', '24 120 kr', '8,0 %']);
    expect(rows[6]).toEqual(['301 500 kr']);
    expect(legend.children[6]?.children[0]?.textContent).toBe('Sum');
  });

  it('says the split in its gist', () => {
    expect(plain(gist(inntekterPerHva))).toBe(
      'Inntektene fordelt: Salg av tjenester 62 %, Salg av varer 30 %, Ikke lest ennå 8 %.',
    );
  });
});

describe('stolper', () => {
  it('draws a row per slice in one colour, with the figure at the end of the name line', () => {
    const figure = draw(kostnaderPerLeverandor);
    const rows = byClass(figure, 'chart-slot');
    expect(rows).toHaveLength(9);
    const marks = byClass(figure, 'chart-mark');
    expect(marks).toHaveLength(9);
    expect(new Set(marks.map((mark) => mark.classes[1]))).toEqual(new Set(['chart-c1']));
    const first = byClass(rows[0] as FakeElement, 'chart-label').map((node) => plain(node.textContent));
    expect(first).toEqual(['Lyreco Norge AS', '48 200 kr']);
  });

  it('shortens a name too long for the line, but never in the tooltip or the table', () => {
    const layout = layoutBars(kostnaderPerLeverandor, 360);
    const long = layout.rows[6];
    expect(long?.label.endsWith('…')).toBe(true);
    // 7.4px a character is what charts.ts assumes for the 13px names.
    expect(((long?.label.length ?? 0) + (long?.value.length ?? 0)) * 7.4).toBeLessThanOrEqual(360);
    expect(long?.title.startsWith('Statens vegvesen, bompengeinnkreving og ferjebillettering region vest:')).toBe(true);
    const rows = tableRows(draw(kostnaderPerLeverandor));
    expect(rows[6]?.[0]).toBe('Statens vegvesen, bompengeinnkreving og ferjebillettering region vest');
  });

  it('draws a credit-note supplier left of zero', () => {
    const layout = layoutBars(kostnaderPerLeverandor);
    expect(layout.zeroX).toBeGreaterThan(0);
    const negative = layout.rows[8]?.bar;
    expect(negative?.from).toBe(layout.zeroX);
    expect(negative?.end).toBeLessThan(layout.zeroX);
    expect(layout.rows[0]?.bar?.end).toBeCloseTo(layout.width);
  });

  it('names the three largest in its gist', () => {
    expect(plain(gist(kostnaderPerLeverandor))).toBe(
      'Kostnader per leverandør. Størst: Lyreco Norge AS 48 200 kr, Telenor Norge AS 18 450 kr, ' +
        'Circle K Norge AS 14 980 kr, og 6 til.',
    );
  });
});

describe('tabell and tall', () => {
  it('shows a table of slices with shares and the total, and nothing to draw', () => {
    const figure = draw(inntekterPerKunde);
    expect(byTag(figure, 'svg')).toHaveLength(0);
    expect(byTag(figure, 'details')).toHaveLength(0);
    const table = byTag(figure, 'table')[0] as FakeElement;
    expect(byTag(table, 'th').slice(0, 3).map((cell) => cell.textContent)).toEqual(['Navn', 'Beløp', 'Andel']);
    expect(tableRows(figure).map((row) => row.map(plain))).toEqual([
      ['Bergen Bygg AS', '182 400,00 kr', '44,3 %'],
      ['Fjord Eiendom AS', '96 300,00 kr', '23,4 %'],
      ['Hansen & Sønn', '58 900,00 kr', '14,3 %'],
      ['Uten motpart', '74 400,00 kr', '18,0 %'],
    ]);
    const foot = byTag(table, 'tfoot')[0] as FakeElement;
    expect(foot.textContent.startsWith('Sum')).toBe(true);
    expect(plain(foot.textContent)).toContain('412 000,00 kr');
  });

  it('shows a table of series with months as rows, losses with their sign', () => {
    const rows = tableRows(draw(resultatTabell)).map((row) => row.map(plain));
    expect(rows[0]).toEqual(['august 2026', '30 150,00 kr', '36 550,00 kr', '-6 400,00 kr']);
  });

  it('shows one figure for tall', () => {
    const figure = draw(inntekterTall);
    expect(plain(byClass(figure, 'chart-figure')[0]?.textContent ?? '')).toBe('412 000 kr');
    expect(byTag(figure, 'svg')).toHaveLength(0);
    expect(byClass(figure, 'chart-caption')[0]?.textContent).toBe(inntekterTall.caption);
  });

  it('falls back to a table when a spec cannot be drawn as its view', () => {
    const figure = draw({ ...inntekterPerManed, x: null });
    expect(figure.classes).toContain('chart-tabell');
    expect(byTag(figure, 'svg')).toHaveLength(0);
    expect(byTag(figure, 'table')).toHaveLength(1);
  });

  // The backend sends a split with nothing booked as `slices: []`.
  it.each([
    ['a donut with nothing booked', { ...kostnaderPerKonto, slices: [], total: '0.00' }],
    ['bars with nothing booked', { ...kostnaderPerLeverandor, slices: [], total: '0.00' }],
    ['a table of no slices', { ...inntekterPerKunde, slices: [] }],
    ['columns over no months', { ...inntekterPerManed, x: { kind: 'maned' as const, labels: [] }, series: [] }],
  ])('says there is nothing to show for %s, instead of an empty ring or table', (_, chart) => {
    const figure = draw(chart);
    expect(figure.classes).toEqual(['chart', 'chart-empty']);
    expect(figure.dataset.chart).toBe(chart.id);
    expect(figure.children.map((node) => node.localName)).toEqual(['figcaption', 'p']);
    expect(figure.children[0]?.textContent).toBe(chart.title);
    expect(byClass(figure, 'chart-nothing')[0]?.textContent).toBe('Ingen tall å vise for perioden.');
    expect(byTag(figure, 'svg')).toHaveLength(0);
    expect(byTag(figure, 'table')).toHaveLength(0);
    expect(byTag(figure, 'details')).toHaveLength(0);
  });

  it('keeps the caption of an empty chart', () => {
    const figure = draw({ ...inntekterPerHva, slices: [] });
    expect(byClass(figure, 'chart-caption')[0]?.textContent).toBe(inntekterPerHva.caption);
  });

  it('still draws all zeros, which are figures and not nothing', () => {
    expect(draw(alleNuller).classes).not.toContain('chart-empty');
  });
});

describe('shares and figures as text', () => {
  it('moves the decimal point in the string, and the list shares its decimals', () => {
    expect(percentTexts(['0.380', '0.224', '0.120', '0.080', '0.050', '0.146']).map(plain)).toEqual([
      '38,0 %', '22,4 %', '12,0 %', '8,0 %', '5,0 %', '14,6 %',
    ]);
    expect(percentTexts(['0.62', '0.30', '0.08']).map(plain)).toEqual(['62 %', '30 %', '8 %']);
    expect(percentTexts(['1']).map(plain)).toEqual(['100 %']);
  });

  it('rounds half up to one decimal in the string, never through a float', () => {
    expect(percentTexts(['0.12345']).map(plain)).toEqual(['12,3 %']);
    expect(percentTexts(['0.12351']).map(plain)).toEqual(['12,4 %']);
    // 0.145 * 100 is 14.499999999999998 as a float, which would round down.
    expect(percentTexts(['0.1455']).map(plain)).toEqual(['14,6 %']);
    expect(percentTexts(['0.9996']).map(plain)).toEqual(['100 %']);
  });

  it('keeps the sign of a negative share, but not of one that rounds to zero', () => {
    expect(percentTexts(['-0.004'])).toEqual(['−0,4 %']);
    expect(percentTexts(['-0.0004']).map(plain)).toEqual(['0 %']);
  });

  it('formats each unit from the string', () => {
    expect(plain(valueText('kr', '-41230.00'))).toBe('-41 230,00 kr');
    expect(plain(valueText('kr', '-41230.00', true))).toBe('-41 230 kr');
    expect(plain(valueText('prosent', '0.125'))).toBe('12,5 %');
    expect(plain(valueText('antall', '1200'))).toBe('1 200');
    expect(plain(valueText('antall', '-3'))).toBe('-3');
  });

  it('rounds drawn kroner half away from zero on the string, and never shows -0', () => {
    expect(plain(valueText('kr', '31250.50', true))).toBe('31 251 kr');
    expect(plain(valueText('kr', '31250.49', true))).toBe('31 250 kr');
    expect(plain(valueText('kr', '999.50', true))).toBe('1 000 kr');
    expect(plain(valueText('kr', '-2750.50', true))).toBe('-2 751 kr');
    expect(plain(valueText('kr', '-0.40', true))).toBe('0 kr');
  });
});

describe('the table says what the drawing says', () => {
  const seriesCharts = [inntekterPerManed, resultatTrettenManeder, alleNuller, kostnaderLinje];

  it.each(seriesCharts.map((chart) => [chart.id, chart] as const))('%s: a row per slot, same figures', (_, chart) => {
    const figure = draw(chart);
    const slots = byClass(figure, 'chart-slot');
    const rows = tableRows(figure);
    expect(rows).toHaveLength(slots.length);
    // The table keeps the øre and the drawing rounds to whole kroner, so
    // both are checked against the spec's own figure for that slot.
    rows.forEach((row, index) => {
      const lines = titleOf(slots[index] as FakeElement).split('\n');
      const [name, ...cells] = row;
      const values = chart.series.map((series) => series.values[index] ?? null);
      cells.forEach((cell, s) => {
        const value = values[s] ?? null;
        expect(cell).toBe(value === null ? 'Ingen bilag' : valueText(chart.unit, value));
      });
      const drawn = values.map((value) => (value === null ? 'ingen bilag' : valueText(chart.unit, value, true)));
      if (chart.series.length === 1) {
        expect(lines).toEqual([`${name}: ${drawn[0]}`]);
      } else {
        expect(lines[0]).toBe(name);
        drawn.forEach((text, s) => {
          expect(lines[s + 1]).toBe(`${chart.series[s]?.label ?? ''}: ${text}`);
        });
      }
    });
  });

  it('marks a month with no books as such, never as 0,00 kr', () => {
    const rows = tableRows(draw(inntekterPerManed));
    expect(rows[6]?.slice(1).map(plain)).toEqual(['Ingen bilag', '12 300,00 kr']);
    expect(rows[0]?.slice(1).map(plain)).toEqual(['28 400,00 kr', 'Ingen bilag']);
  });

  it.each([kostnaderPerKonto, inntekterPerHva, kostnaderPerLeverandor].map((chart) => [chart.id, chart] as const))(
    '%s: a row per slice, same figures as the slice tooltips',
    (_, chart) => {
      const figure = draw(chart);
      const marks = chart.view === 'smultring' ? byClass(figure, 'chart-slice') : byClass(figure, 'chart-slot');
      const rows = tableRows(figure);
      expect(rows).toHaveLength(chart.slices?.length ?? 0);
      expect(marks).toHaveLength(rows.length);
      rows.forEach(([name, value, share], index) => {
        const figure = chart.slices?.[index]?.value ?? '';
        expect(value).toBe(valueText(chart.unit, figure));
        expect(titleOf(marks[index] as FakeElement)).toBe(`${name}: ${valueText(chart.unit, figure, true)} (${share})`);
      });
    },
  );

  it('gives the hidden table caption the chart title, so the table stands on its own', () => {
    const table = dataTable(inntekterPerManed) as unknown as FakeElement;
    const caption = byTag(table, 'caption')[0] as FakeElement;
    expect(caption.textContent).toBe('Inntekter per måned');
    expect(caption.classes).toContain('visually-hidden');
  });
});

describe('text from outside is text', () => {
  const hostile = '<img src=x onerror="alert(1)"> & <b>fet</b>';

  it('puts titles, labels and captions in as text, never as markup', () => {
    const charts: Chart[] = [
      { ...inntekterPerManed, title: hostile, caption: hostile, series: inntekterPerManed.series.map((s) => ({ ...s, label: hostile })) },
      { ...kostnaderPerKonto, title: hostile, slices: (kostnaderPerKonto.slices ?? []).map((s) => ({ ...s, label: hostile })) },
      { ...kostnaderPerLeverandor, slices: (kostnaderPerLeverandor.slices ?? []).map((s) => ({ ...s, label: hostile })) },
      { ...resultatTabell, series: resultatTabell.series.map((s) => ({ ...s, label: hostile })) },
      { ...inntekterTall, title: hostile, caption: hostile },
    ];
    for (const chart of charts) {
      const figure = draw(chart);
      expect(all(figure, (node) => ['img', 'b', 'script'].includes(node.localName))).toEqual([]);
      expect(figure.textContent).toContain(chart.view === 'stolper' ? '<img src=x' : hostile);
    }
    const figure = draw(charts[0] as Chart);
    expect(byTag(figure, 'figcaption')[0]?.textContent).toBe(hostile);
    expect(byTag(figure, 'svg')[0]?.getAttribute('aria-label')?.startsWith(hostile)).toBe(true);
  });
});

describe('stacked columns (WP6 hva_per_maned)', () => {
  const stackedChart: Chart = {
    id: 'hva_per_maned',
    view: 'kolonner',
    title: 'Kostnader per måned etter hva de gjelder',
    caption: 'Fordelt etter hva bilagene gjelder, lest av KI.',
    unit: 'kr',
    x: { kind: 'maned', labels: ['2026-01', '2026-02', '2026-03'] },
    series: [
      { key: 'drivstoff', label: 'Drivstoff', role: 'serie', values: ['10000.00', null, '15000.00'] },
      { key: 'husleie', label: 'Husleie', role: 'serie', values: ['20000.00', null, '25000.00'] },
      { key: 'programvare', label: 'Programvare', role: 'serie', values: ['5000.00', null, '10000.00'] },
    ],
    slices: null,
    total: '85000.00',
    stacked: true,
    dataset: { name: 'hva_per_maned', params: { fra: '2026-01-01', til: '2026-03-31', topp: '5' } },
  };

  it('sizes the axis from the stacked monthly total, not the largest single series', () => {
    const layout = layoutSeries(stackedChart, 480);
    const topTick = layout.ticks[layout.ticks.length - 1]?.value ?? 0;
    // March stacks 15 000 + 25 000 + 10 000 = 50 000, well above any individual series (25 000).
    expect(topTick).toBeGreaterThanOrEqual(50000);
  });

  it('stacks segments vertically in one column per slot and skips null months', () => {
    const layout = layoutSeries(stackedChart, 480);
    const jan = layout.columns.filter((c) => c.slot === 0);
    const feb = layout.columns.filter((c) => c.slot === 1);
    const mar = layout.columns.filter((c) => c.slot === 2);
    expect(jan).toHaveLength(3);
    expect(feb).toHaveLength(0);
    expect(mar).toHaveLength(3);
    // All stacked segments in a slot share the same x and chain base -> end upwards.
    expect(jan[0]?.x).toBe(jan[1]?.x);
    expect(jan[1]?.base).toBeCloseTo(jan[0]?.end ?? 0, 5);
    expect(jan[2]?.base).toBeCloseTo(jan[1]?.end ?? 0, 5);
    // Only the top segment of a stack is rounded.
    expect(jan.map((c) => c.rounded)).toEqual([false, false, true]);
  });

  it('draws every series in the legend and table', () => {
    const figure = draw(stackedChart);
    const keyItems = byTag(byClass(figure, 'chart-key')[0] as FakeElement, 'li');
    expect(keyItems).toHaveLength(3);
    const marks = byClass(figure, 'chart-mark');
    expect(marks).toHaveLength(6);
  });
});
