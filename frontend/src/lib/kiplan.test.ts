import { describe, expect, it } from 'vitest';

import type { KiBillingInvoice, KiPeriod, KiStatus, KiSubscription, KiWeeklyUsage } from './api';
import {
  buyNote,
  deliveryLabel,
  mayBuy,
  mayManageSubscription,
  monthLine,
  nextLine,
  purchaseOutcome,
  returnedReference,
} from './kiplan';

function period(overrides: Partial<KiPeriod> = {}): KiPeriod {
  return {
    reference: 'ref',
    plan: 'pluss',
    plan_name: 'KI Pluss',
    status: 'paid',
    remaining_percent: 75,
    used_percent: 25,
    amount: '186.25',
    starts_at: '2026-11-25T10:00:00Z',
    ends_at: '2026-12-25T10:00:00Z',
    test: false,
    ...overrides,
  };
}

function subscription(overrides: Partial<KiSubscription> = {}): KiSubscription {
  return {
    plan: 'pluss',
    plan_name: 'KI Pluss',
    status: 'active',
    quota_label: '3× ukekvote',
    price_excl_vat: '149.00',
    vat_amount: '37.25',
    price_incl_vat: '186.25',
    currency: 'NOK',
    billing_email: 'faktura@bygg.no',
    started_at: '2026-11-25T10:00:00Z',
    current_period_start: '2026-11-25T10:00:00Z',
    current_period_end: '2026-12-25T10:00:00Z',
    cancel_at_period_end: false,
    canceled_at: null,
    ...overrides,
  };
}

function weekly(overrides: Partial<KiWeeklyUsage> = {}): KiWeeklyUsage {
  return {
    week_start: '2026-11-23',
    resets_at: '2026-11-29T23:00:00Z', // 2026-11-30 00:00 Europe/Oslo
    remaining_percent: 80,
    used_percent: 20,
    ...overrides,
  };
}

function status(overrides: Partial<KiStatus> = {}): KiStatus {
  return {
    ki_enabled: true,
    model_available: true,
    plans_on_sale: true,
    can_buy: true,
    subscription_available: false,
    can_manage_subscription: false,
    levels: [],
    subscription: null,
    weekly: null,
    current: null,
    upcoming: [],
    ...overrides,
  };
}

const TODAY = new Date('2026-11-25T12:00:00Z');

describe('coming back from Mollie', () => {
  it('says a month that starts now starts today', () => {
    const outcome = purchaseOutcome(period(), TODAY);
    expect(outcome).toEqual({
      text: 'KI Pluss er betalt, og gjelder fra i dag til 25. desember.',
      tone: 'ok',
      settled: true,
    });
  });

  it('names the day a month bought ahead starts, never «i dag»', () => {
    const ahead = period({ starts_at: '2026-12-25T10:00:00Z', ends_at: '2027-01-25T10:00:00Z' });
    expect(purchaseOutcome(ahead, TODAY).text).toBe(
      'KI Pluss er betalt, og gjelder fra 25. desember til 25. januar.',
    );
  });

  it('says when no money moved', () => {
    expect(purchaseOutcome(period({ test: true }), TODAY).text).toContain('testmodus');
  });

  it('keeps asking while Mollie has not decided', () => {
    expect(purchaseOutcome(period({ status: 'open' })).settled).toBe(false);
  });

  it('says nothing was charged when it did not go through', () => {
    for (const failed of ['failed', 'canceled', 'expired'] as const) {
      const outcome = purchaseOutcome(period({ status: failed }));
      expect(outcome.tone).toBe('error');
      expect(outcome.text).toContain('ingenting er trukket');
    }
  });
});

describe('the reference in the link', () => {
  it('is read from ?betaling=', () => {
    expect(returnedReference('?betaling=AbC123_def-456')).toBe('AbC123_def-456');
  });

  it('is ignored when it is not shaped like one of ours', () => {
    expect(returnedReference('?betaling=../../companies')).toBeNull();
    expect(returnedReference('?betaling=kort')).toBeNull();
    expect(returnedReference('')).toBeNull();
  });
});

describe('the month and weekly line', () => {
  it('says there is no plan, and that «Gratis» stays unlimited', () => {
    expect(monthLine(status())).toBe(
      'Selskapet har ingen KI-plan nå. «Gratis» er alltid med, uten grense.',
    );
  });

  it('shows weekly percentage remaining and Monday reset for an active subscription', () => {
    const line = monthLine(status({ subscription: subscription(), weekly: weekly() }));
    expect(line).toContain('KI Pluss: 80 % igjen denne uken');
    expect(line).toContain('mandag 30. november');
    expect(line).toContain('Neste fakturaperiode starter 25. desember.');
  });

  it('says «Gratis» answers until Monday when weekly quota is spent', () => {
    const line = monthLine(
      status({
        subscription: subscription({ cancel_at_period_end: true }),
        weekly: weekly({ remaining_percent: 0, used_percent: 100 }),
      }),
    );
    expect(line).toContain('ukekvoten er brukt opp (0 % igjen)');
    expect(line).toContain('«Gratis» svarer til mandag 30. november.');
    expect(line).toContain('Avsluttes 25. desember.');
  });

  it('supports legacy one-off periods', () => {
    expect(monthLine(status({ current: period() }))).toBe(
      'KI Pluss: 75 % igjen, til 25. desember.',
    );
  });
});

describe('months paid ahead', () => {
  it('are not mentioned when there are none', () => {
    expect(nextLine(status())).toBeNull();
  });

  it('name the next one and when it starts', () => {
    const next = period({ plan_name: 'KI Start', starts_at: '2026-12-25T10:00:00Z' });
    expect(nextLine(status({ upcoming: [next] }))).toBe(
      'Neste måned er betalt: KI Start, fra 25. desember.',
    );
  });
});

describe('the line under the plans', () => {
  it('tells someone who may not manage plans who can', () => {
    expect(buyNote(status({ can_buy: false }), 'Bygg AS')).toBe(
      'Bare eieren av Bygg AS kan velge eller endre en KI-plan.',
    );
  });

  it('explains monthly EHF/email invoicing when subscriptions are available', () => {
    const note = buyNote(
      status({ subscription_available: true, can_manage_subscription: true }),
      'Bygg AS',
    );
    expect(note).toContain('ukentlig kvote');
    expect(note).toContain('EHF');
  });

  it('points to company settings when KI is turned off', () => {
    expect(buyNote(status({ ki_enabled: false }), 'Bygg AS')).toContain(
      'Innstillinger for selskapet',
    );
  });
});

describe('action buttons and invoice delivery labels', () => {
  it('gates subscription management cleanly', () => {
    expect(
      mayManageSubscription(
        status({ subscription_available: true, can_manage_subscription: true }),
      ),
    ).toBe(true);
    expect(
      mayManageSubscription(
        status({ subscription_available: true, can_manage_subscription: false }),
      ),
    ).toBe(false);
    expect(mayBuy(status({ ki_enabled: false }))).toBe(false);
  });

  it('formats invoice delivery statuses in bokmål', () => {
    const base: KiBillingInvoice = {
      id: 1,
      plan: 'pluss',
      plan_name: 'KI Pluss',
      period_start: '2026-11-01',
      period_end: '2026-12-01',
      invoice_number: '2026-0001',
      amount_excl_vat: '149.00',
      vat_amount: '37.25',
      amount_incl_vat: '186.25',
      currency: 'NOK',
      issued_on: '2026-11-01',
      due_date: '2026-11-15',
      delivery_method: 'ehf',
      delivery_status: 'delivered',
      delivered_at: '2026-11-01T10:00:00Z',
      has_pdf: true,
    };
    expect(deliveryLabel(base)).toBe('Levert som EHF');
    expect(deliveryLabel({ ...base, delivery_method: 'email', delivery_status: 'sent' })).toBe(
      'Sendt på e-post',
    );
    expect(deliveryLabel({ ...base, delivery_method: 'portal', delivery_status: 'pending' })).toBe(
      'Klar for nedlasting',
    );
    expect(deliveryLabel({ ...base, delivery_status: 'failed' })).toContain('Sending feilet');
  });
});
