/**
 * What a proposed invoice may write into the form, and what it must leave alone.
 *
 * Asserted because both ways of getting it wrong are silent: a proposal that
 * overwrites what the person typed puts a buyer nobody wrote on an invoice
 * that can only be undone by kreditnota, and one that mixes its street into a
 * typed buyer makes an address that belongs to no one.
 */

import { describe, expect, it } from 'vitest';

import type { FakturaDraft, Party } from './api';
import { describeFill, planFill, type FormState } from './invoice-proposal';

function proposal(overrides: Partial<FakturaDraft> = {}): FakturaDraft {
  return {
    party_id: null,
    party_match: null,
    buyer_name: null,
    buyer_orgnr: null,
    buyer_address_line: null,
    buyer_postal_code: null,
    buyer_city: null,
    buyer_is_business: null,
    invoice_email: null,
    delivery_date: null,
    delivery_place: null,
    due_date: null,
    buyer_reference: null,
    lines: [],
    warnings: [],
    ...overrides,
  };
}

/** A new form: no buyer, and the three defaults the page starts with. */
function blankForm(overrides: Partial<FormState['values']> = {}): FormState {
  const defaults = { delivery_date: '2026-10-23', due_date: '2026-11-06', delivery_place: 'Oslo' };
  return {
    partyId: '',
    values: {
      buyer_name: '',
      buyer_orgnr: '',
      buyer_address_line: '',
      buyer_postal_code: '',
      buyer_city: '',
      buyer_reference: '',
      ...defaults,
      ...overrides,
    },
    untouched: { ...defaults },
  };
}

function customer(id: number, name: string, orgnr: string | null = null): Party {
  return {
    id,
    party_type: 'kunde',
    party_id: String(10000 + id),
    name,
    orgnr,
    address_line: null,
    postal_code: null,
    city: null,
    balance_account_id: 1,
  };
}

// The customer's e-mail from the real case, names replaced.
const FROM_EMAIL = proposal({
  buyer_name: 'Fjord Aluminium AS',
  buyer_orgnr: '910000039',
  buyer_address_line: 'Postboks 53',
  buyer_postal_code: '6601',
  buyer_city: 'Sunndalsøra',
  buyer_is_business: true,
  invoice_email: 'faktura@fjordaluminium.no',
  buyer_reference: 'GS-HR00003-02-05, Att: Kari Nordmann',
});

describe('planFill', () => {
  it('fills a new form with the whole buyer and the reference', () => {
    const plan = planFill(FROM_EMAIL, blankForm(), []);

    expect(plan.partyId).toBeNull();
    expect(plan.fields).toEqual({
      buyer_name: 'Fjord Aluminium AS',
      buyer_orgnr: '910000039',
      buyer_address_line: 'Postboks 53',
      buyer_postal_code: '6601',
      buyer_city: 'Sunndalsøra',
      buyer_reference: 'GS-HR00003-02-05, Att: Kari Nordmann',
    });
    expect(plan.business).toBe(true);
    expect(plan.buyerKept).toBe(false);
  });

  it('chooses a registered customer rather than typing their details', () => {
    const parties = [customer(7, 'Fjord Aluminium AS', '910000039')];
    const plan = planFill({ ...FROM_EMAIL, party_id: 7, party_match: 'orgnr' }, blankForm(), parties);

    expect(plan.partyId).toBe(7);
    expect(plan.fields.buyer_name).toBeUndefined();
    expect(plan.fields.buyer_address_line).toBeUndefined();
  });

  it('does not choose a customer the page does not list', () => {
    const plan = planFill({ ...FROM_EMAIL, party_id: 99 }, blankForm(), [customer(7, 'Annen AS')]);

    expect(plan.partyId).toBeNull();
    expect(plan.fields.buyer_name).toBe('Fjord Aluminium AS');
  });

  it('leaves the whole buyer alone once any part of one is typed', () => {
    const plan = planFill(FROM_EMAIL, blankForm({ buyer_city: 'Bergen' }), []);

    for (const name of ['buyer_name', 'buyer_orgnr', 'buyer_address_line', 'buyer_city'] as const) {
      expect(plan.fields[name]).toBeUndefined();
    }
    expect(plan.business).toBeNull();
    expect(plan.buyerKept).toBe(true);
  });

  it('leaves the buyer alone once a customer is chosen', () => {
    const form = { ...blankForm(), partyId: '3' };
    const plan = planFill(FROM_EMAIL, form, [customer(3, 'Kari Nordmann')]);

    expect(plan.partyId).toBeNull();
    expect(plan.fields.buyer_name).toBeUndefined();
    expect(plan.buyerKept).toBe(true);
    // The terms are a separate question, and the reference is still empty.
    expect(plan.fields.buyer_reference).toBe('GS-HR00003-02-05, Att: Kari Nordmann');
  });

  it('replaces the defaults the form started with, and nothing typed over them', () => {
    const said = proposal({ delivery_date: '2026-10-14', delivery_place: 'Bergen', due_date: '2026-11-22' });

    const fresh = planFill(said, blankForm(), []);
    expect(fresh.fields).toEqual({
      delivery_date: '2026-10-14',
      delivery_place: 'Bergen',
      due_date: '2026-11-22',
    });

    const typed = planFill(said, blankForm({ delivery_place: 'Stavanger', due_date: '2026-12-01' }), []);
    expect(typed.fields).toEqual({ delivery_date: '2026-10-14' });
  });

  it('may refill what an earlier proposal wrote', () => {
    const form = blankForm({ buyer_reference: 'PO-1' });
    form.untouched.buyer_reference = 'PO-1';

    expect(planFill(proposal({ buyer_reference: 'PO-2' }), form, []).fields).toEqual({
      buyer_reference: 'PO-2',
    });
  });

  it('says nothing about the business tick when there is no buyer to say it of', () => {
    expect(planFill(proposal({ buyer_reference: 'PO-1' }), blankForm(), []).business).toBeNull();
  });
});

describe('describeFill', () => {
  it('names what was filled in, in the order of the form', () => {
    const plan = planFill(
      { ...FROM_EMAIL, delivery_date: '2026-10-14', due_date: '2026-11-22' },
      blankForm(),
      [],
    );

    expect(describeFill(plan, 2)).toBe(
      'Fylte inn kjøperen, leveringsdatoen, forfallsdatoen, referansen og 2 linjer.',
    );
  });

  it('says «kunden» for a registered customer, and one line without an accent', () => {
    const plan = planFill({ ...FROM_EMAIL, party_id: 7 }, blankForm(), [customer(7, 'Fjord AS')]);

    expect(describeFill(plan, 1)).toBe('Fylte inn kunden, referansen og en linje.');
  });

  it('says so when the buyer was kept', () => {
    const plan = planFill(FROM_EMAIL, blankForm({ buyer_name: 'Noen Andre AS' }), []);

    expect(describeFill(plan, 0)).toBe(
      'Fylte inn referansen. Kjøperen sto allerede i skjemaet, så den er ikke endret.',
    );
  });

  it('says so when there was nothing to fill in', () => {
    expect(describeFill(planFill(proposal(), blankForm(), []), 0)).toBe(
      'Det var ingenting i teksten å fylle inn.',
    );
  });
});
