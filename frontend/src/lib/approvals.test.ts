/**
 * The words /godkjenning/ uses for a waiting request.
 *
 * A request shown as something milder than it is would be approved blind, so
 * the heading, the finality and the outcome are asserted rather than trusted.
 */

import { describe, expect, it } from 'vitest';

import {
  approvalHeading,
  consequence,
  filingPage,
  isFinal,
  needsLogin,
  outcomeText,
  pageFor,
  requestFields,
  waitingBadge,
} from './approvals';

describe('approvalHeading', () => {
  it('names the key and what its route does', () => {
    expect(approvalHeading({ kind: 'request', key_name: 'Claude', label: 'føre en betaling' })).toBe(
      '«Claude» vil føre en betaling',
    );
  });

  it('reads a request to file as a request to the person', () => {
    expect(
      approvalHeading({
        kind: 'filing',
        key_name: 'Regnskapsskript',
        label: 'at du sender mva-meldingen for 3. termin 2026',
      }),
    ).toBe('«Regnskapsskript» ber deg om at du sender mva-meldingen for 3. termin 2026');
  });
});

describe('filingPage', () => {
  it('sends an MVA request to its termin, and to the right melding', () => {
    expect(filingPage({ type: 'mva_melding', aar: 2026, termin: 3, naering: null })).toEqual({
      href: '/mva/#2026-3',
      label: 'Gå til MVA-siden',
    });
    expect(filingPage({ type: 'mva_melding', aar: 2026, termin: 3, naering: 'alminnelig' }).href).toBe(
      '/mva/#alminnelig-2026-3',
    );
  });

  it('sends a skattemelding request to its year', () => {
    expect(filingPage({ type: 'skattemelding', aar: 2025, termin: null, naering: null })).toEqual({
      href: '/skattemelding/#2025',
      label: 'Gå til skattemeldingen',
    });
  });
});

describe('isFinal', () => {
  it('marks what cannot be undone', () => {
    expect(isFinal({ scope: 'post' })).toBe(true);
    expect(isFinal({ scope: 'write' })).toBe(false);
  });
});

describe('consequence', () => {
  const request = { kind: 'request' as const, scope: 'post', sends_out: false, needs_id_porten: false };

  it('says a filing goes to Skatteetaten with the person’s own login', () => {
    expect(consequence({ ...request, sends_out: true, needs_id_porten: true })).toMatch(
      /^Når du godkjenner, sendes dette til Skatteetaten med din egen ID-porten-innlogging/,
    );
  });

  it('says anything else that leaves Myntos cannot be called back', () => {
    expect(consequence({ ...request, sends_out: true })).toMatch(/ut av Myntos.*hentes tilbake/);
  });

  it('keeps a posting’s warning, and says nothing for what can be changed back', () => {
    expect(consequence(request)).toMatch(/^Dette kan ikke angres/);
    expect(consequence({ ...request, scope: 'write' })).toBeNull();
    expect(consequence({ ...request, kind: 'filing', sends_out: true, needs_id_porten: true })).toBeNull();
  });
});

describe('needsLogin', () => {
  const filing = { kind: 'request' as const, is_own: true, needs_id_porten: true };
  const out = { available: true, logged_in: false };

  it('offers the login to the person who approves, while they have none', () => {
    expect(needsLogin(filing, out)).toBe(true);
    expect(needsLogin(filing, { ...out, logged_in: true })).toBe(false);
  });

  it('offers nothing where it cannot help', () => {
    expect(needsLogin({ ...filing, is_own: false }, out)).toBe(false);
    expect(needsLogin({ ...filing, needs_id_porten: false }, out)).toBe(false);
    expect(needsLogin(filing, { ...out, available: false })).toBe(false);
  });
});

describe('pageFor', () => {
  it('finds the page a request touches', () => {
    expect(pageFor('/api/v1/companies/7/cashflow')?.href).toBe('/kontantstrom/');
    expect(pageFor('/api/v1/companies/7/faktura/12/utsted')?.href).toBe('/faktura/');
    expect(pageFor('/api/v1/companies/7/mva/spesifikasjoner')?.href).toBe('/mva-justeringer/');
    expect(pageFor('/api/v1/companies/7/mva/innstillinger')?.href).toBe('/mva/');
    expect(pageFor('/api/v1/companies/7/manuelt-bilag/12/reversering')?.href).toBe('/manuelt-bilag/');
    expect(pageFor('/api/v1/companies/7/anlegg/avskrivning')?.href).toBe('/anlegg/');
    expect(pageFor('/api/v1/companies/7/dimensjoner/3/verdier')?.href).toBe('/avdelinger/');
    expect(pageFor('/api/v1/companies/7/dimensjonsverdier/9')?.href).toBe('/avdelinger/');
  });

  it('does not guess at a path it does not know', () => {
    expect(pageFor('/api/v1/companies/7/noe-annet')).toBeNull();
  });
});

describe('requestFields', () => {
  it('lists what was sent and leaves out what is empty', () => {
    expect(
      requestFields({ gross_amount: '1250.00', description: 'Kontor', party_id: null, lines: [1] }),
    ).toEqual([
      ['gross_amount', '1250.00'],
      ['description', 'Kontor'],
      ['lines', '[\n  1\n]'],
    ]);
  });

  it('shows nothing for a body that is not an object', () => {
    expect(requestFields(null)).toEqual([]);
    expect(requestFields('tekst')).toEqual([]);
  });
});

describe('outcomeText', () => {
  it('says plainly that it was done', () => {
    expect(outcomeText({ status: 'done', result: {}, attachments: [] })).toBe('Utført.');
  });

  it('names a receipt that did not reach the archive', () => {
    expect(
      outcomeText({
        status: 'done',
        result: {},
        attachments: [{ filename: 'kvittering.pdf', status: 415, body: null }],
      }),
    ).toContain('«kvittering.pdf» kom ikke i bilagsarkivet');
  });

  it('carries the reason Myntos gave for refusing it', () => {
    expect(
      outcomeText({ status: 'failed', result: { detail: 'året er avsluttet' }, attachments: [] }),
    ).toBe('Myntos avviste det: året er avsluttet');
  });
});

describe('waitingBadge', () => {
  it('is empty when nothing waits, so the badge hides', () => {
    expect(waitingBadge(0)).toBe('');
    expect(waitingBadge(3)).toBe('3');
  });
});
