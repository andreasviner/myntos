/**
 * Sending as EHF, as the invoice page and the company page say it.
 *
 * What a person relies on: an invoice to a business on Peppol goes as EHF by
 * the press that issues it, and the review says so with the address before
 * that press; an invoice that went is never offered again unless it failed,
 * nothing is offered before the company can send, and the company page names
 * the one step still missing.
 */

import { describe, expect, it } from 'vitest';

import type { EhfSending, PeppolStanding } from './api';
import {
  delivery,
  deliverySentence,
  sendable,
  sendingLabel,
  sendingSentence,
  standingSentence,
} from './peppol';

const READY: PeppolStanding = {
  available: true,
  company_confirmed: true,
  registered: true,
  verified: true,
  can_send: true,
};

function sending(status: EhfSending['status'], message: string | null = null): EhfSending {
  return {
    status,
    recipient: '0192:910000012',
    sent_at: '2026-11-01T10:00:00Z',
    status_checked_at: null,
    failure_category: status === 'failed' ? 'transport' : null,
    failure_message: message,
  };
}

const ISSUED = {
  status: 'utstedt',
  has_ehf: true,
  buyer_orgnr: '910000012',
  ehf_sending: null as EhfSending | null,
};

describe('sendable', () => {
  it('offers an issued invoice to a business once the company can send', () => {
    expect(sendable(ISSUED, READY)).toBe(true);
  });

  it('never offers an invoice again that went, unless it failed', () => {
    expect(sendable({ ...ISSUED, ehf_sending: sending('pending') }, READY)).toBe(false);
    expect(sendable({ ...ISSUED, ehf_sending: sending('delivered') }, READY)).toBe(false);
    expect(sendable({ ...ISSUED, ehf_sending: sending('failed') }, READY)).toBe(true);
  });

  it('offers nothing before the company can send, or to a buyer without an orgnr', () => {
    expect(sendable(ISSUED, null)).toBe(false);
    expect(sendable(ISSUED, { ...READY, verified: false, can_send: false })).toBe(false);
    expect(sendable({ ...ISSUED, buyer_orgnr: null }, READY)).toBe(false);
    expect(sendable({ ...ISSUED, status: 'utkast', has_ehf: false }, READY)).toBe(false);
  });
});

const BUSINESS = { buyer_name: 'Kunde AS', buyer_orgnr: '910000012', buyer_is_business: true };

describe('delivery', () => {
  it('sends to a business on Peppol, and when the lookup could not be made', () => {
    expect(delivery(BUSINESS, READY, true)).toBe('ehf');
    expect(delivery(BUSINESS, READY, null)).toBe('ehf');
  });

  it('leaves the PDF to the person when the customer is not on Peppol', () => {
    expect(delivery(BUSINESS, READY, false)).toBe('not-on-peppol');
  });

  it('says so when a business customer meets a company that cannot send yet', () => {
    const unregistered = { ...READY, registered: false, verified: false, can_send: false };
    expect(delivery(BUSINESS, unregistered, null)).toBe('not-registered');
    expect(delivery({ ...BUSINESS, buyer_is_business: false }, unregistered, null)).toBe('pdf');
  });

  it('has nothing to send without an orgnr or an access point', () => {
    expect(delivery({ ...BUSINESS, buyer_orgnr: null }, READY, null)).toBe('pdf');
    expect(delivery(BUSINESS, { ...READY, available: false, can_send: false }, true)).toBe('pdf');
    expect(delivery(BUSINESS, null, true)).toBe('pdf');
  });
});

describe('deliverySentence', () => {
  it('names the customer and the address before the press that sends', () => {
    const sentence = deliverySentence('ehf', BUSINESS, '2027-01-01');
    expect(sentence).toContain('Kunde AS (910000012)');
    expect(sentence).toContain('kan ikke kalles tilbake');
  });

  it('names the date the law starts from when the company cannot send', () => {
    expect(deliverySentence('not-registered', BUSINESS, '2027-01-01')).toContain('2027');
  });

  it('says nothing where there is nothing to say', () => {
    expect(deliverySentence('pdf', BUSINESS, '2027-01-01')).toBeNull();
  });
});

describe('sendingLabel and sendingSentence', () => {
  it('says nothing about an invoice that was never sent', () => {
    expect(sendingLabel(null)).toBeNull();
  });

  it('quotes the access point when a send failed', () => {
    expect(sendingLabel(sending('failed'))).toBe('EHF kom ikke fram');
    expect(sendingSentence(sending('failed', 'rejected by receiver'))).toContain(
      '«rejected by receiver»',
    );
  });

  it('does not claim delivery before the receipt', () => {
    expect(sendingLabel(sending('pending'))).toBe('Sendt som EHF');
    expect(sendingSentence(sending('pending'))).toContain('venter på kvittering');
  });
});

describe('standingSentence', () => {
  it('shows no step when the installation has no access point', () => {
    expect(standingSentence({ ...READY, available: false })).toBeNull();
  });

  it('names only the first missing step', () => {
    const unconfirmed = {
      ...READY,
      company_confirmed: false,
      registered: false,
      verified: false,
      can_send: false,
    };
    expect(standingSentence(unconfirmed)).toContain('ID-porten');
    const unregistered = { ...unconfirmed, company_confirmed: true };
    expect(standingSentence(unregistered)).toContain('ikke registrert');
    const unchecked = { ...unregistered, registered: true };
    expect(standingSentence(unchecked)).toContain('identitetskontrollen');
    expect(standingSentence(READY)).toContain('sendes som EHF når de utstedes');
  });
});
