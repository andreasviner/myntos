/**
 * How the one motpart box reads its text back into a choice.
 *
 * Asserted because getting it wrong is silent in both directions: a registered
 * name read as new registers a duplicate party, and a prefix read as a match
 * books the entry against somebody the user never named.
 */

import { describe, expect, it } from 'vitest';

import type { Party } from './api';
import { partiesMatching, resolveParty } from './counterparty';

function party(id: number, name: string, party_type: Party['party_type'] = 'leverandor'): Party {
  return {
    id,
    party_type,
    party_id: String(10000 + id),
    name,
    orgnr: null,
    address_line: null,
    postal_code: null,
    city: null,
    balance_account_id: 1,
  };
}

const REGISTER = [
  party(1, 'Telenor Norge AS'),
  party(2, 'Telenor', 'kunde'),
  party(3, 'Telenor', 'leverandor'),
  party(4, 'MA-3D Design'),
];

describe('resolveParty', () => {
  it('reads a typed registered name as that party, whatever the case and spacing', () => {
    expect(resolveParty('  ma-3d   design ', REGISTER, 'leverandor')?.id).toBe(4);
  });

  it('never reads a prefix as a match', () => {
    expect(resolveParty('MA-3D', REGISTER, 'leverandor')).toBeNull();
  });

  it('prefers the side the money is going when two parties share a name', () => {
    expect(resolveParty('Telenor', REGISTER, 'leverandor')?.id).toBe(3);
    expect(resolveParty('Telenor', REGISTER, 'kunde')?.id).toBe(2);
  });

  it('reads an empty box as no party', () => {
    expect(resolveParty('   ', REGISTER, 'leverandor')).toBeNull();
  });
});

describe('partiesMatching', () => {
  it('lists everything for an empty query', () => {
    expect(partiesMatching('', REGISTER)).toHaveLength(REGISTER.length);
  });

  it('matches anywhere in the name, and on the party number', () => {
    expect(partiesMatching('norge', REGISTER).map((p) => p.id)).toEqual([1]);
    expect(partiesMatching('10004', REGISTER).map((p) => p.id)).toEqual([4]);
  });
});
