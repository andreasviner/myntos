/**
 * The words and the link format for /tilbakemelding/ (docs/feedback.md).
 *
 * The page can be opened with what to send already filled in, in the
 * fragment: `/tilbakemelding/#type=feil&tekst=…&side=/faktura/`. That is for
 * an AI helping somebody who uses Myntos without a key: it cannot send
 * anything itself, but it can write the report and hand the person a link,
 * and the person reads it and presses the button. The fragment, not the query
 * string, because a fragment never reaches a server, and the text is whatever
 * somebody was looking at.
 *
 * The same fragment carries `ref=` when the page shows one item to the person
 * who sent it. The API answers with that link (`url` on the answer to a send).
 */

export type FeedbackKind = 'bug' | 'improvement' | 'request';
export type FeedbackStatus = 'new' | 'read' | 'planned' | 'done' | 'declined';

export const KIND_LABELS: Record<FeedbackKind, string> = {
  bug: 'Feil',
  improvement: 'Forbedring',
  request: 'Ønske',
};

export const STATUS_LABELS: Record<FeedbackStatus, string> = {
  new: 'Ikke lest ennå',
  read: 'Lest',
  planned: 'Planlagt',
  done: 'Ferdig',
  declined: 'Blir ikke gjort',
};

// Bokmål as the page and a person say it, and the API's own words, so a link
// written by an AI that read either works.
const KIND_WORDS: Record<string, FeedbackKind> = {
  feil: 'bug',
  bug: 'bug',
  forbedring: 'improvement',
  improvement: 'improvement',
  onske: 'request',
  ønske: 'request',
  request: 'request',
};

/** The kind a word names, or null for a word that names none. */
export function kindFromWord(word: string | null): FeedbackKind | null {
  if (word === null) return null;
  return KIND_WORDS[word.trim().toLowerCase()] ?? null;
}

export interface Prefill {
  kind: FeedbackKind | null;
  text: string | null;
  page: string | null;
  reference: string | null;
}

function nonEmpty(value: string | null): string | null {
  const trimmed = value?.trim() ?? '';
  return trimmed === '' ? null : trimmed;
}

/** What a `#type=…&tekst=…&side=…` or `#ref=…` fragment asks the page to show. */
export function prefillFromFragment(hash: string): Prefill {
  const params = new URLSearchParams(hash.startsWith('#') ? hash.slice(1) : hash);
  return {
    kind: kindFromWord(params.get('type')),
    text: nonEmpty(params.get('tekst')),
    page: nonEmpty(params.get('side')),
    reference: nonEmpty(params.get('ref')),
  };
}

/** The link that opens the form filled in. What an AI without a key hands a person. */
export function feedbackLink(
  base: string,
  fields: { kind: FeedbackKind; text: string; page?: string | null },
): string {
  const params = new URLSearchParams({ type: fields.kind, tekst: fields.text });
  if (fields.page) params.set('side', fields.page);
  return `${base.replace(/\/$/, '')}/tilbakemelding/#${params.toString()}`;
}

/**
 * The page somebody came from, when they followed a link here from Myntos itself.
 *
 * The path only: the query string can carry ids, and the fragment can carry
 * a token. Null for another site, for this page and for an unreadable
 * referrer, because a guess in that field is worse than an empty one.
 */
export function pageFromReferrer(referrer: string, origin: string): string | null {
  if (referrer === '') return null;
  let url: URL;
  try {
    url = new URL(referrer);
  } catch {
    return null;
  }
  if (url.origin !== origin || url.pathname.startsWith('/tilbakemelding')) return null;
  return url.pathname;
}
