import { describe, expect, it } from 'vitest';

import { CHAT_SUGGESTIONS, formatThreadTime, parseBlocks, parseInline } from './kichat';

describe('kichat markdown parser', () => {
  it('parses inline bold and code while keeping raw HTML literal', () => {
    const tokens = parseInline('Konto `6300` er **12 500,00 kr** og <script>alert(1)</script>');
    expect(tokens).toEqual([
      { kind: 'text', value: 'Konto ' },
      { kind: 'code', value: '6300' },
      { kind: 'text', value: ' er ' },
      { kind: 'strong', value: '12 500,00 kr' },
      { kind: 'text', value: ' og <script>alert(1)</script>' },
    ]);
  });

  it('parses headings, lists, paragraphs, and tables cleanly', () => {
    const md = [
      '## Kostnader',
      '',
      'Her er oversikten:',
      '',
      '- Husleie: **10 000 kr**',
      '- Telefon: 990 kr',
      '',
      '| Konto | Beløp |',
      '| --- | --- |',
      '| 6300 | 10 000 kr |',
      '| 6900 | 990 kr |',
    ].join('\n');

    const blocks = parseBlocks(md);
    expect(blocks).toEqual([
      { kind: 'heading', level: 2, text: 'Kostnader' },
      { kind: 'paragraph', text: 'Her er oversikten:' },
      { kind: 'ul', items: ['Husleie: **10 000 kr**', 'Telefon: 990 kr'] },
      {
        kind: 'table',
        headers: ['Konto', 'Beløp'],
        rows: [
          ['6300', '10 000 kr'],
          ['6900', '990 kr'],
        ],
      },
    ]);
  });

  it('provides non-empty Norwegian starter suggestions', () => {
    expect(CHAT_SUGGESTIONS.length).toBeGreaterThanOrEqual(3);
    for (const s of CHAT_SUGGESTIONS) {
      expect(s.endsWith('?')).toBe(true);
    }
  });

  it('formats same-day thread times as HH:MM and older dates as day + month', () => {
    const now = new Date('2026-11-27T15:00:00Z');
    expect(formatThreadTime('2026-11-27T13:30:00Z', now)).toBe('14:30');
    expect(formatThreadTime('2026-11-20T10:00:00Z', now)).toMatch(/20\. nov/);
  });
});
