/**
 * Confirming a company somebody else has confirmed: asked first, every time.
 *
 * The backend allows a second confirmed set of books for an orgnr only when
 * the request carries the person's yes. What is worth holding here is the
 * other half: that the yes is never sent without the question having been put,
 * and that «Avbryt» sends nothing at all.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Company } from './api';

const COMPANY_ID = 7;

function fakeStorage(initial: Record<string, string> = {}) {
  const items = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => void items.set(key, value),
    removeItem: (key: string) => void items.delete(key),
    clear: () => items.clear(),
    key: (index: number) => [...items.keys()][index] ?? null,
    get length() {
      return items.size;
    },
  };
}

const answer = (status: number, body: unknown): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const claimed = (): Response =>
  answer(409, {
    detail: 'Selskapet har allerede et regnskap registrert hos noen andre.',
    code: 'company_already_claimed',
  });

const confirmed = (): Response =>
  answer(200, { id: COMPANY_ID, name: 'DIALGA AS', verified: true });

/** A fresh `lib/ownership.ts` whose `fetch` answers `responses` in turn. */
async function load(...responses: Response[]) {
  const fetchMock = vi.fn(async () => responses.shift() ?? answer(500, {}));
  const stored = {
    'myntos.company': JSON.stringify({ id: COMPANY_ID, name: 'DIALGA AS', verified: false }),
  };
  vi.stubGlobal('window', { localStorage: fakeStorage(stored) });
  vi.stubGlobal('fetch', fetchMock);
  vi.resetModules();
  return { ownership: await import('./ownership'), fetchMock };
}

const company = { id: COMPANY_ID, name: 'DIALGA AS' } as Company;

/** What each request said about the yes: true, or absent. */
const yeses = (fetchMock: ReturnType<typeof vi.fn>): (boolean | undefined)[] =>
  fetchMock.mock.calls.map((call) => {
    const init = (call as unknown[])[1] as RequestInit | undefined;
    return init?.body === undefined
      ? undefined
      : (JSON.parse(String(init.body)) as { even_if_claimed?: boolean }).even_if_claimed;
  });

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe('confirming a company nobody else has confirmed', () => {
  it('asks nothing and sends no yes', async () => {
    const { ownership, fetchMock } = await load(confirmed());
    const ask = vi.fn(async () => true);
    const saved = await ownership.confirmOwnership(company, { ask });
    expect(saved?.verified).toBe(true);
    expect(ask).not.toHaveBeenCalled();
    expect(yeses(fetchMock)).toEqual([undefined]);
  });
});

describe('confirming a company somebody else has confirmed', () => {
  it('asks, and «Ja» confirms with the yes', async () => {
    const { ownership, fetchMock } = await load(claimed(), confirmed());
    const ask = vi.fn(async () => true);
    const saved = await ownership.confirmOwnership(company, { ask });
    expect(ask).toHaveBeenCalledWith('DIALGA AS');
    expect(saved?.verified).toBe(true);
    expect(yeses(fetchMock)).toEqual([undefined, true]);
  });

  it('asks, and «Avbryt» sends nothing more', async () => {
    const { ownership, fetchMock } = await load(claimed());
    const saved = await ownership.confirmOwnership(company, { ask: async () => false });
    expect(saved).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('is not asked twice when the page asked already', async () => {
    const { ownership, fetchMock } = await load(confirmed());
    const ask = vi.fn(async () => false);
    const saved = await ownership.confirmOwnership(company, { agreed: true, ask });
    expect(saved?.verified).toBe(true);
    expect(ask).not.toHaveBeenCalled();
    expect(yeses(fetchMock)).toEqual([true]);
  });
});

describe('every other refusal', () => {
  it('is thrown as it came, without asking', async () => {
    const { ownership } = await load(
      answer(403, {
        detail: 'Altinn viser ikke at du kan opptre for dette selskapet.',
        code: 'not_authorized_for_party',
      }),
    );
    const ask = vi.fn(async () => true);
    await expect(ownership.confirmOwnership(company, { ask })).rejects.toMatchObject({
      status: 403,
      code: 'not_authorized_for_party',
    });
    expect(ask).not.toHaveBeenCalled();
  });
});
