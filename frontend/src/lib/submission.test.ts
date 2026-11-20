/**
 * What a filing that went wrong says: a sent melding is never told it stopped,
 * and a person is sent to Altinn only when there is something there to find.
 */

import { describe, expect, it } from 'vitest';

import { errorText } from './submission';

describe('errorText', () => {
  it('says nothing when nothing went wrong', () => {
    expect(errorText({ status: 'sendt', error: null, altinn_instance_id: '1/abc' }, 'meldingen')).toBeNull();
  });

  it('says only what failed afterwards for a melding that was sent', () => {
    const error = 'Meldingen er sendt, men oppgjøret mot 2740 ble ikke ført: året er avsluttet';
    const text = errorText({ status: 'mottatt', error, altinn_instance_id: '1/abc' }, 'meldingen');
    expect(text).toBe(error);
  });

  it('says nothing was sent when Altinn never made the instance', () => {
    const text = errorText(
      { status: 'feilet', error: 'opprettelse av instans: POST /instances failed (500)', altinn_instance_id: null },
      'meldingen',
    );
    expect(text).toContain('Ingenting ble sendt');
    expect(text).toContain('Kontroller på nytt og send igjen');
    expect(text).not.toContain('altinn.no');
  });

  it('names the inbox on altinn.no when the instance exists', () => {
    const text = errorText(
      { status: 'feilet', error: 'process/next failed (409)', altinn_instance_id: '1/abc' },
      'skattemeldingen',
    );
    expect(text).toContain('innboksen til selskapet på altinn.no om skattemeldingen ligger der som sendt');
    expect(text).toContain('Feilen: process/next failed (409)');
  });
});
