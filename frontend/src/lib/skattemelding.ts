/**
 * What /skattemelding/ says, and which step it offers, for one income year.
 *
 * The page is the person's last look before a skattemelding goes to
 * Skatteetaten, so what decides the step it offers lives here and is tested:
 * a «Send inn» shown for a check the books have moved past would file figures
 * nobody looked at (the backend refuses it too, with
 * `skattemelding_changed`, but the page should not offer it), and a finding
 * that says «rett det under Kontoplan» without a link to Kontoplan is a dead
 * end (docs/frontend-conventions.md, «The company's own details live in one
 * place»).
 */

import type {
  SkattemeldingAvvik,
  SkattemeldingFiling,
  SkattemeldingPost,
  SkattemeldingStatus,
  SkattemeldingYear,
} from './api';

export const STATUS_LABELS: Record<SkattemeldingStatus, string> = {
  validert: 'Kontrollert, ikke sendt',
  avvist: 'Avvist i kontrollen',
  sendt: 'Sendt, venter på svar',
  hos_revisor: 'Sendt, venter på revisor',
  mottatt: 'Sendt og mottatt',
  feilet: 'Stoppet underveis',
};

export const STATUS_TONES: Partial<Record<SkattemeldingStatus, 'ok' | 'error'>> = {
  sendt: 'ok',
  hos_revisor: 'ok',
  mottatt: 'ok',
  avvist: 'error',
  feilet: 'error',
};

/** Sent from Myntos: there is an Altinn instance, and Skatteetaten's answer to fetch. */
export function isFiled(status: SkattemeldingStatus): boolean {
  return status === 'sendt' || status === 'hos_revisor' || status === 'mottatt';
}

// Where each finding the review can raise is put right. The message says it in
// words; the page adds the link, because a message cannot carry one
// (setStatus writes text, never markup).
const FIX_LINKS: Record<string, { href: string; label: string }> = {
  aret_er_apent: { href: '/arsavslutning/', label: 'Årsavslutning' },
  balansen_gar_ikke_opp: { href: '/arsavslutning/', label: 'Årsavslutning' },
  konto_uten_post: { href: '/kontoplan/', label: 'Kontoplan' },
  ukjent_post: { href: '/kontoplan/', label: 'Kontoplan' },
  post_ikke_tillatt: { href: '/kontoplan/', label: 'Kontoplan' },
  post_ikke_stottet: { href: '/kontoplan/', label: 'Kontoplan' },
  revisor_ukjent: { href: '/organisasjon/#selskap', label: 'Selskapet' },
  avskrivning_ikke_fort: { href: '/anlegg/', label: 'Anlegg og avskrivning' },
  anlegg_ikke_stottet: { href: '/anlegg/', label: 'Anlegg og avskrivning' },
};

/** The page where a finding is put right, or null when it is only for reading. */
export function fixLink(code: string): { href: string; label: string } | null {
  return FIX_LINKS[code] ?? null;
}

// The næringsspesifikasjon's sections, in the order Skatteetaten's own form
// shows them, with the headings a person reads.
const SECTIONS: [string, string][] = [
  ['salgsinntekt', 'Driftsinntekter'],
  ['annenDriftsinntekt', 'Driftsinntekter'],
  ['varekostnad', 'Driftskostnader'],
  ['loennskostnad', 'Driftskostnader'],
  ['annenDriftskostnad', 'Driftskostnader'],
  ['finansinntekt', 'Finansinntekter'],
  ['finanskostnad', 'Finanskostnader'],
  ['skattekostnad', 'Skattekostnad'],
  ['balanseverdiForAnleggsmiddel', 'Anleggsmidler'],
  ['balanseverdiForOmloepsmiddel', 'Omløpsmidler'],
  ['egenkapital', 'Egenkapital'],
  ['langsiktigGjeld', 'Langsiktig gjeld'],
  ['kortsiktigGjeld', 'Kortsiktig gjeld'],
];

const RESULT_SIDE = new Set(SECTIONS.slice(0, 8).map(([category]) => category));

export interface PostGroup {
  title: string;
  /** On the result side; otherwise the balance. */
  result: boolean;
  posts: SkattemeldingPost[];
}

/** The posts under their section headings, in the form's order; empty sections left out. */
export function postGroups(posts: SkattemeldingPost[]): PostGroup[] {
  const groups: PostGroup[] = [];
  for (const [category, title] of SECTIONS) {
    const found = posts.filter((post) => post.category === category);
    if (found.length === 0) continue;
    const last = groups[groups.length - 1];
    if (last !== undefined && last.title === title) last.posts.push(...found);
    else groups.push({ title, result: RESULT_SIDE.has(category), posts: [...found] });
  }
  return groups;
}

/**
 * What the action area offers for the year.
 *
 * - `not_over`: the year is still running; Skatteetaten has no melding for it.
 * - `blocked`: the review has a feil; the findings say what, with links.
 * - `read_only`: the person may read the books but not file for them.
 * - `not_configured`: this installation cannot check or file at all.
 * - `login`: ID-porten first.
 * - `check`: the question and «Kontroller hos Skatteetaten».
 */
export type NextStep = 'not_over' | 'blocked' | 'read_only' | 'not_configured' | 'login' | 'check';

export function nextStep(
  year: Pick<SkattemeldingYear, 'is_over' | 'blocked' | 'submission_available'>,
  government: { available: boolean; logged_in: boolean },
  mayWrite: boolean,
): NextStep {
  if (!year.is_over) return 'not_over';
  if (year.blocked) return 'blocked';
  if (!mayWrite) return 'read_only';
  if (!(government.available && year.submission_available)) return 'not_configured';
  if (!government.logged_in) return 'login';
  return 'check';
}

/**
 * The checked filing the review card offers to send, or null.
 *
 * Only the newest, only one Skatteetaten would take, and only while the books
 * still build what was checked: an older check is history, and a check the
 * books have moved past is one to make again.
 */
export function sendable(filings: SkattemeldingFiling[]): SkattemeldingFiling | null {
  const latest = filings[0];
  if (latest === undefined || latest.status !== 'validert' || latest.matches_books !== true) return null;
  return latest;
}

/**
 * Skatteetaten's avvik, split into what it filled in itself and the rest.
 *
 * Myntos never sends a value Skatteetaten derives (sums, the result, a
 * saldo's closing value), so an accepted melding comes back with a dozen
 * `mangler…` avvik that are Skatteetaten saying it computed them. They are
 * expected, and shown as a count; anything else is shown in full.
 */
export function splitAvvik(avvik: SkattemeldingAvvik[]): {
  derived: number;
  other: SkattemeldingAvvik[];
} {
  const other = avvik.filter((item) => !item.type.startsWith('mangler'));
  return { derived: avvik.length - other.length, other };
}

/** The year a visit lands on: the one in the address, else the API's own choice. */
export function yearFromHash(hash: string): number | null {
  const match = /^#(\d{4})$/.exec(hash);
  return match === null ? null : Number(match[1]);
}

/**
 * The payout to the shareholders as the field shows it: whole kroner, from the
 * decimal string the API suggests, rounded half up as Skatteetaten rounds.
 * Worked on the string, never through a float (CLAUDE.md rule 4).
 */
export function payoutSuggestion(decimal: string | null): string {
  if (decimal === null) return '';
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(decimal.trim());
  if (match === null) return '';
  const [, sign, whole, fraction = ''] = match;
  if (sign === '-') return '';
  const roundUp = Number((fraction + '0').charAt(0)) >= 5;
  if (!roundUp) return String(BigInt(whole ?? '0'));
  return String(BigInt(whole ?? '0') + 1n);
}

/**
 * What the person typed as the payout, as whole kroner, or null when it is not one.
 *
 * Spaces (also the non-breaking ones a copied amount carries) are ignored, and
 * a trailing «,00» or «.00» is allowed, because that is how an amount is
 * written; anything with øre in it is refused rather than rounded, since the
 * person is stating a figure, not having one guessed.
 */
export function payoutKroner(text: string): number | null {
  const cleaned = text.replace(/[\s  ]/g, '').replace(/[.,]00$/, '');
  if (!/^\d{1,15}$/.test(cleaned)) return null;
  return Number(cleaned);
}

/** The first day the year can be checked: the day after its last day. */
export function checkableFrom(lastDay: string): string {
  const [year, month, day] = lastDay.split('-').map(Number);
  if (year === undefined || month === undefined || day === undefined) return lastDay;
  const next = new Date(Date.UTC(year, month - 1, day + 1));
  return next.toISOString().slice(0, 10);
}
