/**
 * What a proposed invoice may write into the form on /faktura/, and what it
 * must leave alone.
 *
 * The box at the top of the form takes whatever the seller pastes, usually the
 * customer's own e-mail with their invoice details, and the backend answers
 * with the buyer, the terms and the lines it found there. Writing that into the
 * form is a rule, and the rule is the part that can quietly go wrong: a
 * proposal that overwrites a buyer the person already typed, or mixes the
 * proposed street with a typed name, puts a buyer on the invoice that nobody
 * wrote. So it lives here, where it is asserted.
 *
 * Two rules:
 *
 * - **A field is the form's to fill only while it holds what the form put
 *   there.** Empty, the default the form starts with (today's date, the
 *   company's town), or what an earlier proposal wrote. Anything else the
 *   person typed, and it stays.
 * - **The buyer is one thing.** Either the whole buyer is filled in, or none of
 *   it: if the person has chosen a customer or typed any part of a buyer, the
 *   proposal's buyer is left out entirely and the page says so.
 */

import type { FakturaDraft, Party } from './api';

/** The form's fields a proposal may fill, by their `name` in the form. */
export type FillableField =
  | 'buyer_name'
  | 'buyer_orgnr'
  | 'buyer_address_line'
  | 'buyer_postal_code'
  | 'buyer_city'
  | 'delivery_date'
  | 'delivery_place'
  | 'due_date'
  | 'buyer_reference';

const BUYER_FIELDS = [
  'buyer_name',
  'buyer_orgnr',
  'buyer_address_line',
  'buyer_postal_code',
  'buyer_city',
] as const satisfies readonly FillableField[];

const TERM_FIELDS = [
  'delivery_date',
  'due_date',
  'delivery_place',
  'buyer_reference',
] as const satisfies readonly FillableField[];

/** Every field a proposal may fill, buyer first and then the terms. */
export const FILLABLE_FIELDS: readonly FillableField[] = [...BUYER_FIELDS, ...TERM_FIELDS];

export interface FormState {
  /** The «Kunde» select's value: '' while the buyer is typed in. */
  partyId: string;
  /** What each fillable field holds now. */
  values: Record<FillableField, string>;
  /**
   * What the form itself put in a field: its starting default, or an earlier
   * proposal's value. A field still holding that has not been touched.
   */
  untouched: Partial<Record<FillableField, string>>;
}

export interface FillPlan {
  /** A registered customer to choose in «Kunde», or null to leave it. */
  partyId: number | null;
  /** The fields to set, and to what. */
  fields: Partial<Record<FillableField, string>>;
  /** «Kjøperen er bokføringspliktig» to tick or untick, or null to leave it. */
  business: boolean | null;
  /** The text named a buyer, and the form already had one, so it was kept. */
  buyerKept: boolean;
}

function isUntouched(form: FormState, name: FillableField): boolean {
  const current = form.values[name];
  return current.trim() === '' || current === form.untouched[name];
}

/** Whether the proposal says anything about who the buyer is. */
function namesBuyer(proposal: FakturaDraft): boolean {
  return proposal.party_id !== null || BUYER_FIELDS.some((name) => proposal[name] !== null);
}

/**
 * What to write into the form for `proposal`.
 *
 * `parties` is the customer list the page has loaded. A `party_id` that is not
 * in it is not chosen: the backend matched against this company's register, so
 * that only happens when the list is stale, and choosing an option the select
 * does not have would show the wrong customer without saying so.
 */
export function planFill(proposal: FakturaDraft, form: FormState, parties: Party[]): FillPlan {
  const plan: FillPlan = { partyId: null, fields: {}, business: null, buyerKept: false };

  const buyerFree =
    form.partyId === '' && BUYER_FIELDS.every((name) => isUntouched(form, name));
  if (buyerFree) {
    const party =
      proposal.party_id === null ? undefined : parties.find((p) => p.id === proposal.party_id);
    if (party !== undefined) {
      plan.partyId = party.id;
    } else {
      for (const name of BUYER_FIELDS) {
        const value = proposal[name];
        if (value !== null) plan.fields[name] = value;
      }
    }
    if (plan.partyId !== null || Object.keys(plan.fields).length > 0) {
      plan.business = proposal.buyer_is_business;
    }
  } else {
    plan.buyerKept = namesBuyer(proposal);
  }

  for (const name of TERM_FIELDS) {
    const value = proposal[name];
    if (value !== null && value !== form.values[name] && isUntouched(form, name)) {
      plan.fields[name] = value;
    }
  }
  return plan;
}

const FIELD_WORDS: Record<(typeof TERM_FIELDS)[number], string> = {
  delivery_date: 'leveringsdatoen',
  due_date: 'forfallsdatoen',
  delivery_place: 'leveringsstedet',
  buyer_reference: 'referansen',
};

/** «a», «a og b», «a, b og c». */
function listed(parts: string[]): string {
  if (parts.length <= 1) return parts.join('');
  return `${parts.slice(0, -1).join(', ')} og ${parts.at(-1) ?? ''}`;
}

/**
 * One sentence on what was filled in, for the status under the box.
 *
 * Said out loud as well as tinted, because a tint is not read by a screen
 * reader and a field filled in by a model must not pass for one the person
 * typed.
 */
export function describeFill(plan: FillPlan, lineCount: number): string {
  const parts: string[] = [];
  if (plan.partyId !== null) parts.push('kunden');
  else if (BUYER_FIELDS.some((name) => plan.fields[name] !== undefined)) parts.push('kjøperen');
  for (const name of TERM_FIELDS) {
    if (plan.fields[name] !== undefined) parts.push(FIELD_WORDS[name]);
  }
  if (lineCount > 0) parts.push(lineCount === 1 ? 'en linje' : `${lineCount} linjer`);

  const sentences = [
    parts.length > 0 ? `Fylte inn ${listed(parts)}.` : 'Det var ingenting i teksten å fylle inn.',
  ];
  if (plan.buyerKept) {
    sentences.push('Kjøperen sto allerede i skjemaet, så den er ikke endret.');
  }
  return sentences.join(' ');
}
