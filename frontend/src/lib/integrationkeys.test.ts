/**
 * The words the Integrasjoner section uses for a server's key.
 *
 * The warning is asserted word for word because it is the one sentence that
 * keeps the secret out of an AI assistant, and a stopped key's reason because
 * its owner reads it to know what to fix.
 */

import { describe, expect, it } from 'vitest';

import {
  addressesLabel,
  integrationStateLabel,
  parseAddresses,
  PURPOSE_LABEL,
  SECRET_WARNING,
} from './integrationkeys';

describe('parseAddresses', () => {
  it('reads one per line, or a list with commas, semicolons or spaces', () => {
    expect(parseAddresses('127.0.0.1\n203.0.113.0/24')).toEqual(['127.0.0.1', '203.0.113.0/24']);
    expect(parseAddresses(' 127.0.0.1, 2001:db8::1 ;203.0.113.7 ')).toEqual([
      '127.0.0.1',
      '2001:db8::1',
      '203.0.113.7',
    ]);
  });

  it('drops blanks and repeats', () => {
    expect(parseAddresses('\n\n127.0.0.1\n127.0.0.1\n')).toEqual(['127.0.0.1']);
    expect(parseAddresses('   ')).toEqual([]);
  });
});

describe('integrationStateLabel', () => {
  it('says a live key is in use', () => {
    expect(integrationStateLabel({ state: 'active', revoked_reason: null })).toBe('I bruk');
  });

  it('says why a key stopped', () => {
    expect(integrationStateLabel({ state: 'stopped', revoked_reason: 'person' })).toBe(
      'Stoppet her',
    );
    expect(integrationStateLabel({ state: 'stopped', revoked_reason: 'not_owner' })).toBe(
      'Stoppet: personen er ikke lenger eier',
    );
    expect(integrationStateLabel({ state: 'stopped', revoked_reason: 'refusals' })).toBe(
      'Stoppet: ble avvist for mange ganger',
    );
  });

  it('still says stopped for a reason it does not know', () => {
    expect(integrationStateLabel({ state: 'stopped', revoked_reason: 'something_new' })).toBe(
      'Stoppet',
    );
  });
});

describe('the fixed texts', () => {
  it('names the purpose and warns against an AI assistant', () => {
    expect(PURPOSE_LABEL).toBe('Fakturering: lage, utstede og sende fakturaer');
    expect(SECRET_WARNING).toBe(
      'Lim den inn i innstillingene til serveren som skal bruke den. Lim den aldri inn i en ' +
        'KI-assistent: den sender fakturaer uten å spørre deg.',
    );
  });

  it('lists the addresses on one line', () => {
    expect(addressesLabel({ allowed_ips: ['127.0.0.1', '203.0.113.0/24'] })).toBe(
      '127.0.0.1, 203.0.113.0/24',
    );
  });
});
