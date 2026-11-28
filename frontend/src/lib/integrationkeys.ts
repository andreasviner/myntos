/**
 * What the Integrasjoner section says about a server's integration key.
 *
 * An integration key is not an agent key: an owner makes it for a server, it
 * works only from the addresses given, calls only the invoicing routes and
 * sends invoices without asking anybody (docs/agents-and-api.md rule 15). So
 * its words are its own, and the warning that goes with its secret is the one
 * thing on the page a person must not miss: pasted into an AI assistant, it
 * would let the assistant send invoices with nobody asked.
 */

import type { IntegrationKey } from './api';

/** The one purpose there is, as the form shows it. Fixed text, not a choice. */
export const PURPOSE_LABEL = 'Fakturering: lage, utstede og sende fakturaer';

/** Shown under the secret, once, every time. */
export const SECRET_WARNING =
  'Lim den inn i innstillingene til serveren som skal bruke den. Lim den aldri inn i en ' +
  'KI-assistent: den sender fakturaer uten å spørre deg.';

/** As many as the backend takes; it refuses more. */
export const MAX_ADDRESSES = 10;

/**
 * The addresses a person typed, one entry each.
 *
 * One per line is what the hint asks for, but a list pasted with commas,
 * semicolons or spaces between is just as clear, so any of them separates.
 * Duplicates go; whether each entry is an address is the backend's to judge,
 * because it is the one that has to be right about it.
 */
export function parseAddresses(text: string): string[] {
  const seen = new Set<string>();
  for (const word of text.split(/[\s,;]+/)) {
    if (word) seen.add(word);
  }
  return [...seen];
}

/** Why a key stopped, as its owner should read it. */
const STOPPED_BECAUSE: Record<string, string> = {
  person: 'Stoppet her',
  agent: 'Stoppet av serveren',
  refusals: 'Stoppet: ble avvist for mange ganger',
  not_owner: 'Stoppet: personen er ikke lenger eier',
  member_removed: 'Stoppet: personen har ikke tilgang lenger',
  password_reset: 'Stoppet: passordet ble tilbakestilt',
  reclaimed: 'Stoppet: e-postadressen ble bekreftet av eieren',
  bankid: 'Stoppet: kontoen bruker BankID',
  account_deleted: 'Stoppet: kontoen er slettet',
  company_closed: 'Stoppet: selskapet ble slettet',
};

/** What the list says about a key's state, in a few words. */
export function integrationStateLabel(key: Pick<IntegrationKey, 'state' | 'revoked_reason'>): string {
  if (key.state === 'active') return 'I bruk';
  return STOPPED_BECAUSE[key.revoked_reason ?? ''] ?? 'Stoppet';
}

/** The addresses, as one line for the list. */
export function addressesLabel(key: Pick<IntegrationKey, 'allowed_ips'>): string {
  return key.allowed_ips.join(', ');
}
