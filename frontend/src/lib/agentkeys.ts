/**
 * What the Tilgang page says about keys for agents, in one place.
 *
 * The page is a person's end of docs/agents-and-api.md: switch keys on, make
 * one, hand it to an agent, see what became of it. Most of what it shows is a
 * translation of the API's states and scopes into words a non-technical person
 * reads correctly, and those words are worth testing, because a wrong one is a
 * person believing their agent can do less than it can.
 *
 * The one thing here that is not a label is `pasteText`: what the person
 * copies. It is the key plus one sentence telling the agent where its
 * instructions are, because most people will paste it into a chat with no
 * explanation, and an agent that is told nothing guesses at the API instead of
 * exchanging the key at once.
 */

import type { AgentKey, AgentScope } from './api';

/** Where every agent starts. Absolute, because it is read by somebody else's software. */
export function llmsUrl(site: string | undefined): string {
  return new URL('/llms.txt', site ?? 'https://myntos.no').toString();
}

/**
 * The text the person pastes into the conversation with their agent.
 *
 * Two languages on purpose. The first line is for the person, who reads what
 * they paste; the second is for the agent, which follows instructions in
 * English most reliably whatever language the conversation is in.
 */
export function pasteText(companyName: string, connectionKey: string, site?: string): string {
  const url = llmsUrl(site);
  return (
    `Nøkkel til regnskapet for ${companyName} i Myntos: ${connectionKey}\n` +
    `Agent: read ${url} and exchange this key as it says before doing anything else with it.`
  );
}

/**
 * What a key may do with one right: on its own, only by asking, or not at all.
 *
 * Asking means each request waits on /godkjenning/ until the key's person
 * approves it (docs/agents-and-api.md rule 13). The same three choices for an
 * AI assistant and for a program somebody wrote, because a key is a key.
 */
export type Mode = 'self' | 'ask' | 'no';

export const MODES: { value: Mode; label: string }[] = [
  { value: 'self', label: 'Selv' },
  { value: 'ask', label: 'Spør meg først' },
  { value: 'no', label: 'Nei' },
];

/** The two rights a person decides on. Reading every key does; filing no key does. */
export const RIGHTS: { scope: 'write' | 'post'; label: string; hint: string }[] = [
  {
    scope: 'write',
    label: 'Forberede',
    hint:
      'Utkast til fakturaer, kunder og leverandører, koble banklinjer, kontoplanen. ' +
      'Kan endres eller slettes igjen.',
  },
  {
    scope: 'post',
    label: 'Bokføre',
    hint:
      'Føre bilag, utstede fakturaer og kreditnotaer, importere kontoutskrifter, avslutte ' +
      'året. Kan ikke angres, bare rettes med en ny postering.',
  },
];

/**
 * The choice the form starts on: ask first, for both.
 *
 * Nothing irreversible happens without the person pressing a button, so it is
 * as safe as reading only, and it is the choice that makes the key useful
 * (docs/agents-and-api.md rule 6 and 13).
 */
export const DEFAULT_MODE: Mode = 'ask';

const REACH: Record<Mode, number> = { self: 2, ask: 1, no: 0 };

/**
 * Keep posting from reaching further than preparing, as the backend requires.
 *
 * A key that could issue an invoice on its own but had to ask to make the
 * draft would be a key whose person meant something else, so the form moves
 * «Bokføre» down to match rather than send a choice the backend refuses.
 */
export function settleModes(write: Mode, post: Mode): { write: Mode; post: Mode } {
  return { write, post: REACH[post] > REACH[write] ? write : post };
}

/** The scopes the backend takes for the two choices: what it does, what it asks for. */
export function scopesFor(
  write: Mode,
  post: Mode,
): { scopes: AgentScope[]; approvalScopes: AgentScope[] } {
  const settled = settleModes(write, post);
  const scopes: AgentScope[] = ['read'];
  const approvalScopes: AgentScope[] = [];
  for (const [scope, mode] of [
    ['write', settled.write],
    ['post', settled.post],
  ] as const) {
    if (mode === 'self') scopes.push(scope);
    if (mode === 'ask') approvalScopes.push(scope);
  }
  return { scopes, approvalScopes };
}

/** What a key does with one right. */
export function modeOf(key: Pick<AgentKey, 'scopes' | 'approval_scopes'>, scope: AgentScope): Mode {
  if (key.scopes.includes(scope)) return 'self';
  if ((key.approval_scopes ?? []).includes(scope)) return 'ask';
  return 'no';
}

/**
 * What a key may do, in a few words, for the list.
 *
 * Never narrower than the truth: a key that posts on its own says so first,
 * because a person believing their agent cannot post when it can is the
 * mistake this label exists to prevent.
 */
export function rightsLabel(key: Pick<AgentKey, 'scopes' | 'approval_scopes'>): string {
  const parts: string[] = [];
  const write = modeOf(key, 'write');
  const post = modeOf(key, 'post');
  if (post === 'self') parts.push('bokfører selv');
  if (write === 'self') parts.push('forbereder selv');
  if (write === 'ask') parts.push('forbereder etter godkjenning');
  if (post === 'ask') parts.push('bokfører etter godkjenning');
  if (parts.length === 0) return 'Bare lese';
  const text = parts.join(', ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Why a key stopped, as the person should read it. */
const STOPPED_BECAUSE: Record<string, string> = {
  person: 'Stoppet her',
  agent: 'Stoppet av agenten',
  reuse: 'Stoppet: brukt fra to steder',
  refusals: 'Stoppet: prøvde for mye',
  access_off: 'Stoppet: nøkler ble slått av',
  member_removed: 'Stoppet: personen har ikke tilgang lenger',
  password_reset: 'Stoppet: passordet ble tilbakestilt',
  bankid: 'Stoppet: kontoen bruker BankID',
  account_deleted: 'Stoppet: kontoen er slettet',
  company_closed: 'Stoppet: selskapet ble slettet',
};

/** What the list says about a key's state, in a few words. */
export function stateLabel(key: AgentKey): string {
  switch (key.state) {
    case 'waiting':
      return 'Venter på agenten';
    case 'active':
      return 'I bruk';
    case 'expired':
      return 'Utløpt';
    case 'stopped':
      return STOPPED_BECAUSE[key.revoked_reason ?? ''] ?? 'Stoppet';
  }
}

/** Whether a key is still something a person might want to stop. */
export function isLive(key: AgentKey): boolean {
  return key.state === 'waiting' || key.state === 'active';
}
