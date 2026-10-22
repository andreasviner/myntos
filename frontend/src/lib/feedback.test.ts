import { describe, expect, it } from 'vitest';

import { feedbackLink, kindFromWord, pageFromReferrer, prefillFromFragment } from './feedback';

describe('the prefilled link an AI hands a person', () => {
  it('opens the form with the kind, the text and the page filled in', () => {
    const link = feedbackLink('https://myntos.no/', {
      kind: 'bug',
      text: 'Knappen «Lagre» gjør ingenting & ingenting skjer',
      page: '/faktura/',
    });

    expect(link.startsWith('https://myntos.no/tilbakemelding/#')).toBe(true);
    expect(prefillFromFragment(new URL(link).hash)).toEqual({
      kind: 'bug',
      text: 'Knappen «Lagre» gjør ingenting & ingenting skjer',
      page: '/faktura/',
      reference: null,
    });
  });

  it('reads the bokmål words a person or an AI is likely to write', () => {
    expect(kindFromWord('feil')).toBe('bug');
    expect(kindFromWord('Ønske')).toBe('request');
    expect(kindFromWord('onske')).toBe('request');
    expect(kindFromWord('forbedring')).toBe('improvement');
    expect(kindFromWord('improvement')).toBe('improvement');
    expect(kindFromWord('klage')).toBeNull();
    expect(kindFromWord(null)).toBeNull();
  });

  it('treats blank fields as missing rather than as empty answers', () => {
    expect(prefillFromFragment('#type=&tekst=%20&side=')).toEqual({
      kind: null,
      text: null,
      page: null,
      reference: null,
    });
  });

  it('finds the reference the API linked to', () => {
    expect(prefillFromFragment('#ref=abc123DEF_-xyz').reference).toBe('abc123DEF_-xyz');
  });
});

describe('the page somebody came from', () => {
  const origin = 'https://myntos.no';

  it('is the path of a page on the site, without its query or fragment', () => {
    expect(pageFromReferrer('https://myntos.no/faktura/?id=12#token=x', origin)).toBe('/faktura/');
  });

  it('is nothing for another site, for this page, or for no referrer', () => {
    expect(pageFromReferrer('https://example.com/faktura/', origin)).toBeNull();
    expect(pageFromReferrer('https://myntos.no/tilbakemelding/', origin)).toBeNull();
    expect(pageFromReferrer('', origin)).toBeNull();
    expect(pageFromReferrer('not a url', origin)).toBeNull();
  });
});
