/**
 * Which step /skattemelding/ offers, and how it reads what Skatteetaten said.
 *
 * The page is the last look before a filing, so a «Send inn» offered for a
 * check the books have moved past, or a finding with nowhere to go, are the
 * failures worth a test.
 */

import { describe, expect, it } from 'vitest';

import type { SkattemeldingFiling, SkattemeldingPost } from './api';
import {
  checkableFrom,
  fixLink,
  isFiled,
  nextStep,
  payoutKroner,
  payoutSuggestion,
  postGroups,
  sendable,
  splitAvvik,
  yearFromHash,
} from './skattemelding';

function filing(overrides: Partial<SkattemeldingFiling>): SkattemeldingFiling {
  return {
    id: 1,
    company_id: 7,
    year: 2025,
    status: 'validert',
    validation_status: 'validertOK',
    validated_at: '2026-03-01T10:00:00Z',
    aarsresultat: '7020.00',
    skattemessig_resultat: '9000.00',
    beregnet_skatt: '1980',
    has_auditor: false,
    ytelser_til_aksjonaerer: false,
    findings: [],
    avvik: [],
    veiledninger: [],
    reasons: [],
    matches_books: true,
    altinn_instance_id: null,
    viewer_url: null,
    submitted_at: null,
    submitted_by_name: null,
    outcome: null,
    outcome_text: null,
    feedback_received_at: null,
    error: null,
    documents: [],
    ...overrides,
  };
}

function post(category: string, code: string): SkattemeldingPost {
  return { category, code, name: code, amount: '1.00', subtracted: false, accounts: [code] };
}

describe('nextStep', () => {
  const ready = { is_over: true, blocked: false, submission_available: true };
  const loggedIn = { available: true, logged_in: true };

  it('offers the check only when everything is in place', () => {
    expect(nextStep(ready, loggedIn, true)).toBe('check');
  });

  it('says why when it is not', () => {
    expect(nextStep({ ...ready, is_over: false }, loggedIn, true)).toBe('not_over');
    expect(nextStep({ ...ready, blocked: true }, loggedIn, true)).toBe('blocked');
    expect(nextStep(ready, loggedIn, false)).toBe('read_only');
    expect(nextStep({ ...ready, submission_available: false }, loggedIn, true)).toBe('not_configured');
    expect(nextStep(ready, { available: false, logged_in: false }, true)).toBe('not_configured');
    expect(nextStep(ready, { available: true, logged_in: false }, true)).toBe('login');
  });

  it('puts a running year before everything else', () => {
    expect(nextStep({ is_over: false, blocked: true, submission_available: false }, loggedIn, false)).toBe(
      'not_over',
    );
  });
});

describe('sendable', () => {
  it('offers the newest check while the books still say the same', () => {
    expect(sendable([filing({ id: 2 }), filing({ id: 1 })])?.id).toBe(2);
  });

  it('never a check the books have moved past', () => {
    expect(sendable([filing({ matches_books: false })])).toBeNull();
    expect(sendable([filing({ matches_books: null })])).toBeNull();
  });

  it('never one Skatteetaten refused, nor one already sent', () => {
    expect(sendable([filing({ status: 'avvist' })])).toBeNull();
    expect(sendable([filing({ status: 'sendt' })])).toBeNull();
    // An older check is history, even when the newest cannot be sent.
    expect(sendable([filing({ id: 2, status: 'avvist' }), filing({ id: 1 })])).toBeNull();
    expect(sendable([])).toBeNull();
  });
});

describe('isFiled', () => {
  it('counts a filing waiting for the revisor as sent', () => {
    expect(isFiled('hos_revisor')).toBe(true);
    expect(isFiled('mottatt')).toBe(true);
    expect(isFiled('validert')).toBe(false);
    expect(isFiled('feilet')).toBe(false);
  });
});

describe('fixLink', () => {
  it('sends each finding to the page that puts it right', () => {
    expect(fixLink('aret_er_apent')?.href).toBe('/arsavslutning/');
    expect(fixLink('konto_uten_post')?.href).toBe('/kontoplan/');
    expect(fixLink('revisor_ukjent')?.href).toBe('/organisasjon/#selskap');
    expect(fixLink('avskrivning_ikke_fort')?.href).toBe('/anlegg/');
  });

  it('gives a merknad nothing to click', () => {
    expect(fixLink('formue_fra_regnskapet')).toBeNull();
  });
});

describe('postGroups', () => {
  it('follows the form, merging the sections that share a heading', () => {
    const groups = postGroups([
      post('kortsiktigGjeld', '2500'),
      post('annenDriftskostnad', '6300'),
      post('salgsinntekt', '3000'),
      post('varekostnad', '4005'),
      post('balanseverdiForOmloepsmiddel', '1920'),
    ]);
    expect(groups.map((group) => [group.title, group.result, group.posts.map((p) => p.code)])).toEqual([
      ['Driftsinntekter', true, ['3000']],
      ['Driftskostnader', true, ['4005', '6300']],
      ['Omløpsmidler', false, ['1920']],
      ['Kortsiktig gjeld', false, ['2500']],
    ]);
  });
});

describe('splitAvvik', () => {
  it('counts what Skatteetaten filled in and shows the rest', () => {
    const avvik = [
      { stage: 'avvikEtterBeregning', type: 'manglerNaeringsopplysninger', path: null, received: null, computed: '1' },
      { stage: 'avvikEtterBeregning', type: 'manglerSkattemelding', path: null, received: null, computed: '2' },
      { stage: 'avvikVedValidering', type: 'avvikNaeringsopplysninger', path: '/x', received: '1', computed: '2' },
    ];
    const split = splitAvvik(avvik);
    expect(split.derived).toBe(2);
    expect(split.other.map((item) => item.type)).toEqual(['avvikNaeringsopplysninger']);
  });
});

describe('yearFromHash', () => {
  it('reads a year and nothing else', () => {
    expect(yearFromHash('#2025')).toBe(2025);
    expect(yearFromHash('')).toBeNull();
    expect(yearFromHash('#2025-3')).toBeNull();
  });
});

describe('payoutSuggestion', () => {
  it('shows the books’ figure in whole kroner', () => {
    expect(payoutSuggestion('29500.00')).toBe('29500');
    expect(payoutSuggestion('0.00')).toBe('0');
  });

  it('rounds half up, on the string', () => {
    expect(payoutSuggestion('29500.50')).toBe('29501');
    expect(payoutSuggestion('29500.49')).toBe('29500');
    expect(payoutSuggestion('99999999999999999.5')).toBe('100000000000000000');
  });

  it('suggests nothing it cannot read, or for no figure', () => {
    expect(payoutSuggestion(null)).toBe('');
    expect(payoutSuggestion('-5.00')).toBe('');
    expect(payoutSuggestion('tjue')).toBe('');
  });
});

describe('payoutKroner', () => {
  it('reads whole kroner as written', () => {
    expect(payoutKroner('29500')).toBe(29500);
    expect(payoutKroner('29 500')).toBe(29500);
    expect(payoutKroner('29 500,00')).toBe(29500);
    expect(payoutKroner('0')).toBe(0);
  });

  it('refuses øre and anything else rather than guessing', () => {
    expect(payoutKroner('29500,50')).toBeNull();
    expect(payoutKroner('')).toBeNull();
    expect(payoutKroner('-100')).toBeNull();
    expect(payoutKroner('ca. 30 000')).toBeNull();
  });
});

describe('checkableFrom', () => {
  it('is the day after the last day', () => {
    expect(checkableFrom('2026-07-21')).toBe('2026-07-22');
    expect(checkableFrom('2026-12-31')).toBe('2027-01-01');
    expect(checkableFrom('2028-02-28')).toBe('2028-02-29');
  });
});
