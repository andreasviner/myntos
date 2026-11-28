/**
 * One chart spec of each view, written from docs/analyse.md («The chart
 * pipeline») before the backend that sends them existed.
 *
 * They are the contract seen from this side: the tests in charts.test.ts draw
 * each of them, and the scratch page used to look at the kit in light and dark
 * draws the same ones. Each fixture carries the case it exists for, because
 * those cases are where a chart lies without anyone noticing: a month with no
 * books drawn as a bad month, a loss drawn above the axis, an empty year drawn
 * as a broken chart, thirteen months crowding their own labels.
 *
 * The figures are invented, but they add up the way the backend's would
 * (slices to the total, shares to 1), so a test can compare what is drawn
 * against what was sent without the frontend ever adding anything.
 */

import type { Chart } from './api';

const THIS_YEAR = { fra: '2026-01-01', til: '2026-11-26', sammenlign: 'true' };

/**
 * The first chart on /analyse/: income per month this year, with the same
 * months a year earlier behind it. July has no books at all (`null`, a gap,
 * not a zero column), and the company started in May 2025, so the comparison
 * has no January to April.
 */
export const inntekterPerManed: Chart = {
  id: 'inntekter_per_maned',
  view: 'kolonner',
  title: 'Inntekter per måned',
  caption: null,
  unit: 'kr',
  x: {
    kind: 'maned',
    labels: [
      '2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06',
      '2026-07', '2026-08', '2026-09', '2026-10', '2026-11',
    ],
  },
  series: [
    {
      key: '2026',
      label: '2026',
      role: 'hoved',
      values: [
        '28400.00', '31250.50', '41230.00', '36900.00', '39120.00', '44800.00',
        null, '30150.00', '42600.00', '38750.00', '21400.00',
      ],
    },
    {
      key: '2025',
      label: '2025',
      role: 'sammenligning',
      values: [
        null, null, null, null, '22000.00', '27500.00',
        '12300.00', '25800.00', '33100.00', '31900.00', '18200.00',
      ],
    },
  ],
  slices: null,
  total: '354600.50',
  dataset: { name: 'inntekter_per_maned', params: THIS_YEAR },
};

/**
 * «Siste 12 måneder» from 26 November: thirteen calendar months, the first
 * and last of them partial. Three months are losses, so their columns go below
 * the axis; one of them is in December 2025, so the year changes inside the
 * chart.
 */
export const resultatTrettenManeder: Chart = {
  id: 'resultat_per_maned',
  view: 'kolonner',
  title: 'Resultat før skatt per måned',
  caption: 'Desember, juni og august gikk med underskudd.',
  unit: 'kr',
  x: {
    kind: 'maned',
    labels: [
      '2025-11', '2025-12', '2026-01', '2026-02', '2026-03', '2026-04', '2026-05',
      '2026-06', '2026-07', '2026-08', '2026-09', '2026-10', '2026-11',
    ],
  },
  series: [
    {
      key: 'resultat',
      label: 'Resultat før skatt',
      role: 'hoved',
      values: [
        '4200.00', '-12850.00', '6300.00', '3100.00', '15400.00', '9800.00', '11200.00',
        '-2750.50', '1950.00', '-6400.00', '12300.00', '8700.00', '5100.00',
      ],
    },
  ],
  slices: null,
  total: '58049.50',
  dataset: {
    name: 'resultat_per_maned',
    params: { fra: '2025-11-26', til: '2026-11-26', sammenlign: 'false' },
  },
};

/**
 * Books that exist and came to nothing: every month is zero, not `null`. The
 * page shows no empty charts for empty books, but a dataset a model asked for
 * can still land here, and the chart must draw an axis and no columns rather
 * than divide by zero.
 */
export const alleNuller: Chart = {
  id: 'kostnader_per_maned_null',
  view: 'kolonner',
  title: 'Kostnader per måned',
  caption: null,
  unit: 'kr',
  x: {
    kind: 'maned',
    labels: ['2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06'],
  },
  series: [
    {
      key: '2026',
      label: '2026',
      role: 'hoved',
      values: ['0.00', '0.00', '0.00', '0.00', '0.00', '0.00'],
    },
  ],
  slices: null,
  total: '0.00',
  dataset: {
    name: 'kostnader_per_maned',
    params: { fra: '2026-01-01', til: '2026-06-30', sammenlign: 'false' },
  },
};

/**
 * Costs per month as a line, the view a model may pick for a trend. July is
 * `null` again, so the line breaks there instead of diving to zero and back.
 */
export const kostnaderLinje: Chart = {
  id: 'kostnader_per_maned_linje',
  view: 'linje',
  title: 'Kostnader per måned',
  caption: 'Kostnadene har ligget jevnt over fjorårets.',
  unit: 'kr',
  x: {
    kind: 'maned',
    labels: [
      '2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06',
      '2026-07', '2026-08', '2026-09', '2026-10', '2026-11',
    ],
  },
  series: [
    {
      key: '2026',
      label: '2026',
      role: 'hoved',
      values: [
        '18200.00', '21400.00', '19800.00', '24100.00', '22600.00', '26300.00',
        null, '20900.00', '25400.00', '23800.00', '17600.00',
      ],
    },
    {
      key: '2025',
      label: '2025',
      role: 'sammenligning',
      values: [
        '15100.00', '16800.00', '17900.00', '19200.00', '18800.00', '21000.00',
        '9400.00', '17300.00', '20100.00', '19900.00', '14600.00',
      ],
    },
  ],
  slices: null,
  total: '220100.00',
  dataset: { name: 'kostnader_per_maned', params: THIS_YEAR },
};

/**
 * The costs pie with KI off: the five largest accounts and «Andre». Shares
 * come to three decimals, so the legend reads one decimal of a percent, and
 * they sum to exactly 1 because the backend let «Andre» take the rounding.
 */
export const kostnaderPerKonto: Chart = {
  id: 'kostnader_per_konto',
  view: 'smultring',
  title: 'Kostnadene fordelt',
  caption: null,
  unit: 'kr',
  x: null,
  series: [],
  slices: [
    { key: '4000', label: '4000 Innkjøp av varer', value: '114570.00', share: '0.380' },
    { key: '5000', label: '5000 Lønn til ansatte', value: '67536.00', share: '0.224' },
    { key: '6300', label: '6300 Leie lokaler', value: '36180.00', share: '0.120' },
    { key: '6540', label: '6540 Inventar', value: '24120.00', share: '0.080' },
    { key: '7140', label: '7140 Reisekostnad, ikke oppgavepliktig', value: '15075.00', share: '0.050' },
    { key: 'andre', label: 'Andre', value: '44019.00', share: '0.146' },
  ],
  total: '301500.00',
  dataset: { name: 'kostnader_per_konto', params: { ...THIS_YEAR, topp: '5' } },
};

/**
 * The income pie of a small company: three slices, shares to two decimals
 * (whole percents), and one of them «Ikke lest ennå», the slice a split by
 * `hva` keeps for what KI has not read yet.
 */
export const inntekterPerHva: Chart = {
  id: 'inntekter_per_hva',
  view: 'smultring',
  title: 'Inntektene fordelt',
  caption: 'Fordelt etter hva bilagene gjelder, lest av KI.',
  unit: 'kr',
  x: null,
  series: [],
  slices: [
    { key: 'salg-av-tjenester', label: 'Salg av tjenester', value: '255440.00', share: '0.62' },
    { key: 'salg-av-varer', label: 'Salg av varer', value: '123600.00', share: '0.30' },
    { key: 'ikke-lest', label: 'Ikke lest ennå', value: '32960.00', share: '0.08' },
  ],
  total: '412000.00',
  dataset: { name: 'inntekter_per_hva', params: { ...THIS_YEAR, topp: '5' } },
};

/**
 * Costs per supplier as bars: the longest name longer than any narrow screen,
 * «Uten motpart» for lines with no party, and a supplier whose credit notes
 * came to more than its invoices, so one bar goes left of zero (the backend
 * sends a split with a negative part as bars, never as a donut). Shares come
 * to four decimals, the way the backend rounds them.
 */
export const kostnaderPerLeverandor: Chart = {
  id: 'kostnader_per_leverandor',
  view: 'stolper',
  title: 'Kostnader per leverandør',
  caption: null,
  unit: 'kr',
  x: null,
  series: [],
  slices: [
    { key: 'p-12', label: 'Lyreco Norge AS', value: '48200.00', share: '0.4231' },
    { key: 'p-4', label: 'Telenor Norge AS', value: '18450.00', share: '0.1620' },
    { key: 'p-31', label: 'Circle K Norge AS', value: '14980.00', share: '0.1315' },
    { key: 'p-7', label: 'Visma Software AS', value: '12000.00', share: '0.1053' },
    { key: 'uten', label: 'Uten motpart', value: '9870.00', share: '0.0866' },
    { key: 'p-19', label: 'Elkjøp Nordic AS', value: '6540.00', share: '0.0574' },
    {
      key: 'p-22',
      label: 'Statens vegvesen, bompengeinnkreving og ferjebillettering region vest',
      value: '3200.00',
      share: '0.0281',
    },
    { key: 'p-40', label: 'Kiwi', value: '1120.00', share: '0.0098' },
    { key: 'p-9', label: 'Clas Ohlson AS', value: '-450.00', share: '-0.0040' },
  ],
  total: '113910.00',
  dataset: { name: 'kostnader_per_leverandor', params: { ...THIS_YEAR, topp: '10' } },
};

/** Income per customer as a table, the view for figures that are read rather than seen. */
export const inntekterPerKunde: Chart = {
  id: 'inntekter_per_kunde',
  view: 'tabell',
  title: 'Inntekter per kunde',
  caption: null,
  unit: 'kr',
  x: null,
  series: [],
  slices: [
    { key: 'p-2', label: 'Bergen Bygg AS', value: '182400.00', share: '0.443' },
    { key: 'p-5', label: 'Fjord Eiendom AS', value: '96300.00', share: '0.234' },
    { key: 'p-8', label: 'Hansen & Sønn', value: '58900.00', share: '0.143' },
    { key: 'uten', label: 'Uten motpart', value: '74400.00', share: '0.180' },
  ],
  total: '412000.00',
  dataset: { name: 'inntekter_per_kunde', params: { ...THIS_YEAR, topp: '10' } },
};

/** A table over series instead of slices: three equal series, one loss among them. */
export const resultatTabell: Chart = {
  id: 'resultat_tabell',
  view: 'tabell',
  title: 'Inntekter, kostnader og resultat',
  caption: null,
  unit: 'kr',
  x: { kind: 'maned', labels: ['2026-08', '2026-09', '2026-10'] },
  series: [
    { key: 'inntekter', label: 'Inntekter', role: 'serie', values: ['30150.00', '42600.00', '38750.00'] },
    { key: 'kostnader', label: 'Kostnader', role: 'serie', values: ['36550.00', '30300.00', '30050.00'] },
    { key: 'resultat', label: 'Resultat', role: 'serie', values: ['-6400.00', '12300.00', '8700.00'] },
  ],
  slices: null,
  total: null,
  dataset: {
    name: 'resultat_per_maned',
    params: { fra: '2026-08-01', til: '2026-10-31', sammenlign: 'false' },
  },
};

/** One figure, the view for a number that needs no chart. */
export const inntekterTall: Chart = {
  id: 'inntekter_i_ar',
  view: 'tall',
  title: 'Inntekter hittil i år',
  caption: '12 % mer enn samme tid i fjor.',
  unit: 'kr',
  x: null,
  series: [],
  slices: null,
  total: '412000.00',
  dataset: { name: 'inntekter_per_maned', params: THIS_YEAR },
};

/** Every fixture, in the order the scratch page shows them. */
export const FIXTURES: Chart[] = [
  inntekterPerManed,
  resultatTrettenManeder,
  alleNuller,
  kostnaderLinje,
  kostnaderPerKonto,
  inntekterPerHva,
  kostnaderPerLeverandor,
  inntekterPerKunde,
  resultatTabell,
  inntekterTall,
];
