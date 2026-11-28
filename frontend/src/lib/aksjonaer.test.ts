import { describe, expect, it } from 'vitest';

import {
  confirmSentence,
  eventTypeLabel,
  filingStatusLabel,
  shareholderIdentifier,
  shareholderKindLabel,
  submissionStep,
  systemUserSentence,
} from './aksjonaer';
import type { SystemUserStatus } from './api';

describe('shareholderIdentifier', () => {
  it('shows masked fnr for persons and spaced orgnr for companies', () => {
    expect(
      shareholderIdentifier({
        kind: 'person',
        national_id_masked: '268293 *****',
        orgnr: null,
        foreign_id: null,
        country_code: 'NO',
      }),
    ).toBe('268293 *****');

    expect(
      shareholderIdentifier({
        kind: 'selskap',
        national_id_masked: null,
        orgnr: '910000012',
        foreign_id: null,
        country_code: 'NO',
      }),
    ).toBe('910 000 012');

    expect(
      shareholderIdentifier({
        kind: 'utenlandsk',
        national_id_masked: null,
        orgnr: null,
        foreign_id: 'UTL123456789',
        country_code: 'SE',
      }),
    ).toBe('UTL123456789 (SE)');
  });
});

describe('submissionStep', () => {
  it('gates read-only, blocking findings, configuration, and Altinn systembruker in order', () => {
    expect(
      submissionStep(
        { blocked: false, submission_available: true, systembruker_approved: true },
        false,
      ),
    ).toBe('read_only');
    expect(
      submissionStep(
        { blocked: true, submission_available: true, systembruker_approved: true },
        true,
      ),
    ).toBe('blocked');
    expect(
      submissionStep(
        { blocked: false, submission_available: false, systembruker_approved: false },
        true,
      ),
    ).toBe('not_configured');
    expect(
      submissionStep(
        { blocked: false, submission_available: true, systembruker_approved: false },
        true,
      ),
    ).toBe('needs_systembruker');
    expect(
      submissionStep(
        { blocked: false, submission_available: true, systembruker_approved: true },
        true,
      ),
    ).toBe('ready');
  });
});

describe('confirmSentence and labels', () => {
  it('names company, orgnr, year, share class, and underskjema count', () => {
    const text = confirmSentence({
      companyName: 'Fjord Tech AS',
      orgnr: '910000004',
      year: 2026,
      shareClassName: 'Ordinære aksjer',
      shareholderCount: 2,
    });
    expect(text).toContain('Fjord Tech AS');
    expect(text).toContain('910000004');
    expect(text).toContain('2026');
    expect(text).toContain('2 underskjemaer');
  });

  it('uses singular for one shareholder', () => {
    expect(
      confirmSentence({
        companyName: 'Solo AS',
        orgnr: '910000004',
        year: 2026,
        shareClassName: 'Ordinære aksjer',
        shareholderCount: 1,
      }),
    ).toContain('1 underskjema)');
  });

  it('provides bokmål labels for kinds, event types, and filing statuses', () => {
    expect(shareholderKindLabel('selskap')).toBe('Selskap');
    expect(eventTypeLabel('nyemisjon')).toBe('Nyemisjon');
    expect(filingStatusLabel('bekreftet')).toContain('Bekreftet');
  });
});

describe('systemUserSentence', () => {
  const base: SystemUserStatus = {
    company_id: 1,
    available: true,
    approved: false,
    system_id: '938193045_myntos',
    system_user: null,
    resource_labels: {
      'ske-innrapportering-aksjonaerregisteroppgave': 'Aksjonærregisteroppgaven (RF-1086)',
    },
  };

  it('describes unconfigured, pending, and approved states', () => {
    expect(systemUserSentence({ ...base, available: false })).toContain('ikke satt opp');
    expect(systemUserSentence(base)).toContain('godkjent systembruker i Altinn');
    expect(
      systemUserSentence({
        ...base,
        system_user: {
          id: 1,
          system_id: '938193045_myntos',
          status: 'new',
          approved: false,
          resources: ['ske-innrapportering-aksjonaerregisteroppgave'],
          access_packages: [],
          confirm_url: 'https://am.ui.tt02.altinn.no/...',
          requested_at: '2026-11-27T12:00:00Z',
          checked_at: null,
          approved_at: null,
        },
      }),
    ).toContain('ventende forespørsel');
    expect(
      systemUserSentence({
        ...base,
        approved: true,
        system_user: {
          id: 1,
          system_id: '938193045_myntos',
          status: 'accepted',
          approved: true,
          resources: ['ske-innrapportering-aksjonaerregisteroppgave'],
          access_packages: [],
          confirm_url: null,
          requested_at: '2026-11-27T12:00:00Z',
          checked_at: '2026-11-27T12:05:00Z',
          approved_at: '2026-11-27T12:05:00Z',
        },
      }),
    ).toContain('Aksjonærregisteroppgaven (RF-1086)');
  });
});
