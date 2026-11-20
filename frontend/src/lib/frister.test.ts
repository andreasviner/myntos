/**
 * The deadline rows: what each says, and which the overview card picks.
 *
 * See frister.ts for why: `done: null` read as «not sent» is a wrong «late»
 * that nothing on the screen would contradict.
 */

import { describe, expect, it } from 'vitest';

import type { Frist } from './api';
import { distance, nextUp, placeFor, statusText, toneOf } from './frister';

function frist(extra: Partial<Frist> = {}): Frist {
  return {
    kind: 'mva_melding',
    label: 'Mva-melding for 5. termin 2026 (september-oktober)',
    deadline: '2026-12-10',
    year: 2026,
    recipient: 'skatteetaten',
    files_from_myntos: true,
    done: false,
    done_by: null,
    is_overdue: false,
    note: null,
    termin: 5,
    naering: null,
    ...extra,
  };
}

describe('statusText and toneOf', () => {
  it('says late only when the backend does', () => {
    const late = frist({ is_overdue: true });
    expect(statusText(late)).toBe('Over fristen');
    expect(toneOf(late)).toBe('over');
  });

  it('says nothing about what Myntos cannot know', () => {
    const payment = frist({ kind: 'forskuddsskatt', done: null, files_from_myntos: false });
    expect(statusText(payment)).toBe('');
    expect(toneOf(payment)).toBe('unknown');
  });

  it('names who says it is met', () => {
    expect(statusText(frist({ done: true, done_by: 'myntos' }))).toBe('Sendt fra Myntos');
    expect(statusText(frist({ done: true, done_by: 'annet_program' }))).toBe(
      'Sendt fra et annet program',
    );
    expect(statusText(frist({ kind: 'arsregnskap', done: true, done_by: 'regnskapsregisteret' }))).toBe(
      'Mottatt i Regnskapsregisteret',
    );
    expect(statusText(frist({ done: false }))).toBe('Ikke sendt ennå');
  });
});

describe('distance', () => {
  it('counts calendar days from the backend’s today', () => {
    expect(distance('2026-11-06', '2026-11-06')).toBe('i dag');
    expect(distance('2026-11-07', '2026-11-06')).toBe('i morgen');
    expect(distance('2026-11-05', '2026-11-06')).toBe('i går');
    expect(distance('2026-12-10', '2026-11-06')).toBe('om 34 dager');
    expect(distance('2026-10-12', '2026-11-06')).toBe('25 dager siden');
  });

  it('is not thrown by a change to summer time', () => {
    expect(distance('2027-04-01', '2027-03-20')).toBe('om 12 dager');
  });
});

describe('placeFor', () => {
  it('points at the page where Myntos files it, and nowhere else', () => {
    expect(placeFor(frist())?.href).toBe('/mva/');
    expect(placeFor(frist({ kind: 'skattemelding' }))?.href).toBe('/skattemelding/');
    expect(placeFor(frist({ kind: 'skattemelding', files_from_myntos: false }))).toBeNull();
    expect(placeFor(frist({ kind: 'aksjonaerregisteroppgave', files_from_myntos: false }))).toBeNull();
  });
});

describe('nextUp', () => {
  it('puts what is missed first, leaves out what is met, and stops at the limit', () => {
    const met = frist({ label: 'met', deadline: '2026-11-10', done: true, done_by: 'myntos' });
    const soon = frist({ label: 'soon', deadline: '2026-12-10' });
    const unknown = frist({ label: 'unknown', kind: 'forskuddsskatt', deadline: '2027-02-15', done: null });
    const later = frist({ label: 'later', deadline: '2027-05-31' });
    const missed = frist({ label: 'missed', deadline: '2026-10-12', is_overdue: true });
    const rows = [missed, met, soon, unknown, later];
    expect(nextUp(rows, 3).map((row) => row.label)).toEqual(['missed', 'soon', 'unknown']);
    expect(nextUp([met], 3)).toEqual([]);
  });
});
