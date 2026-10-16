/**
 * The words /godkjenning/ uses for a waiting request.
 *
 * A request shown as something milder than it is would be approved blind, so
 * the heading, the finality and the outcome are asserted rather than trusted.
 */

import { describe, expect, it } from 'vitest';

import {
  approvalHeading,
  isFinal,
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

describe('isFinal', () => {
  it('marks what cannot be undone', () => {
    expect(isFinal({ scope: 'post' })).toBe(true);
    expect(isFinal({ scope: 'write' })).toBe(false);
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
