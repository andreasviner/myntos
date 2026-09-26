/**
 * When the register says who entered a voucher, and what it says when nobody
 * recorded it.
 *
 * The rule is a judgement about a page rather than a calculation, which is
 * exactly why it is asserted here: it is easy to change by accident while
 * editing the table that reads it, and the failure — a mark on every row of a
 * sole trader's register, or no mark at all on books two people share — is one
 * nobody notices in a diff.
 */

import { describe, expect, it } from 'vitest';

import type { CashFlowEntry } from './api';
import { UNKNOWN_AUTHOR, authorCount, authorOf, namesAuthors } from './authors';

/** An entry with only the fields these tests care about filled in. */
function entry(author: Partial<CashFlowEntry>): CashFlowEntry {
  return {
    id: 1,
    voucher_no: '2026-0001',
    occurred_on: '2026-03-14',
    direction: 'ut',
    type_code: 'varekjop',
    type_label: 'Varekjøp',
    gross_amount: '100.00',
    net_amount: '80.00',
    vat_amount: '20.00',
    account_number: '4300',
    account_description: 'Varekjøp',
    tax_code: '1',
    party_id: null,
    party_name: null,
    payment_account: '1920',
    description: 'Test',
    is_reversal: false,
    reversed_by_id: null,
    corrects_id: null,
    created_by_id: null,
    created_by_name: null,
    attachments: [],
    ...author,
  };
}

const KARI = { created_by_id: 1, created_by_name: 'Kari Nordmann' };
const OLA = { created_by_id: 2, created_by_name: 'Ola Nordmann' };

describe('namesAuthors', () => {
  it('says nothing on a register one person wrote', () => {
    expect(namesAuthors([entry(KARI), entry(KARI), entry(KARI)])).toBe(false);
  });

  it('marks the rows as soon as a colleague has entered one', () => {
    expect(namesAuthors([entry(KARI), entry(OLA)])).toBe(true);
  });

  it('leaves an empty register alone', () => {
    expect(namesAuthors([])).toBe(false);
  });

  it('says nothing when no entry has an author at all', () => {
    // Books that predate the column. Every row would read «ukjent», which is a
    // column of noise rather than an answer to anything.
    expect(namesAuthors([entry({}), entry({})])).toBe(false);
  });

  it('marks the rows when some entries are attributed and some are not', () => {
    // The case the mark matters most for: it is what says which rows nobody
    // can be asked about.
    expect(namesAuthors([entry(KARI), entry({})])).toBe(true);
  });
});

describe('authorCount', () => {
  it('counts people and not entries', () => {
    expect(authorCount([entry(KARI), entry(KARI), entry(OLA)])).toBe(2);
  });

  it('tells two colleagues with the same name apart', () => {
    const namesake = { created_by_id: 3, created_by_name: 'Ola Nordmann' };
    expect(authorCount([entry(OLA), entry(namesake)])).toBe(2);
  });
});

describe('authorOf', () => {
  it('prints the name the backend recorded', () => {
    expect(authorOf(entry(KARI))).toBe('Kari Nordmann');
  });

  it('says «ukjent» rather than leaving the mark blank', () => {
    expect(authorOf(entry({}))).toBe(UNKNOWN_AUTHOR);
    expect(authorOf(entry({ created_by_id: 4, created_by_name: '   ' }))).toBe(UNKNOWN_AUTHOR);
  });
});
