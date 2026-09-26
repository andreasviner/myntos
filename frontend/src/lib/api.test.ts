/**
 * The wall in `request()`: no page may ask for, or display, another company.
 *
 * Every call to the API goes through that one function, which is what makes
 * this a wall rather than a habit. The backend has two test files whose whole
 * job is the *access* boundary (a company the caller may not reach, and a
 * sibling company they may); this is the other failure, where the API answers
 * correctly and the page puts the answer under the wrong name.
 *
 * `fetch` is stubbed rather than mocked at the module level: the point is to
 * prove that a refused request never reaches the network at all.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

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

/** The stored record for a company the page is about. */
function storedCompany(id: number, name = 'DIALGA AS') {
  return { 'myntos.company': JSON.stringify({ id, name, verified: true }) };
}

/**
 * A fresh `lib/api.ts` over the given storage, plus the `fetch` it saw.
 *
 * Fresh because `lib/tenant.ts` decides which company the page is about once
 * per page load, and every test here is about a different page.
 */
async function load(stored: Record<string, string>, answer: unknown = {}) {
  const fetchMock = vi.fn(
    async () =>
      new Response(JSON.stringify(answer), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
  );
  vi.stubGlobal('window', { localStorage: fakeStorage(stored) });
  vi.stubGlobal('fetch', fetchMock);
  vi.resetModules();
  return { api: await import('./api'), fetchMock };
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe('asking for the wrong company', () => {
  it('is refused before the request is sent', async () => {
    // What a page holding a stale Company object across a switch would do.
    const { api, fetchMock } = await load(storedCompany(12));

    await expect(api.getSummary(99)).rejects.toBeInstanceOf(api.WrongCompanyError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('is refused as an ApiError, so every page already shows the message', async () => {
    // Pages branch on `error instanceof ApiError ? error.message : <fallback>`.
    // A plain Error here would land in the fallback and say "noe gikk galt".
    const { api } = await load(storedCompany(12));

    const error = await api.getSummary(99).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(api.ApiError);
    expect((error as InstanceType<typeof api.ApiError>).code).toBe('wrong-company');
    expect((error as Error).message).toContain('annet selskap');
  });

  it('lets the company the page is about through', async () => {
    const { api, fetchMock } = await load(storedCompany(12), { financial_year: 2026 });

    await expect(api.getSummary(12)).resolves.toMatchObject({ financial_year: 2026 });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it('leaves account-wide endpoints alone', async () => {
    // /auth/me and /companies are how the choice gets settled in the first
    // place. Walling them would deadlock the first page load of a session.
    const { api, fetchMock } = await load(storedCompany(12), []);

    await expect(api.listCompanies()).resolves.toEqual([]);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it('allows anything while no company has been chosen', async () => {
    // A fresh browser has nothing for the request to contradict, and the first
    // company-scoped call of a session is made on the way to settling which
    // company that is.
    const { api, fetchMock } = await load({}, { financial_year: 2026 });

    await expect(api.getSummary(12)).resolves.toBeTruthy();
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});

describe('an answer about the wrong company', () => {
  it('is refused rather than rendered', async () => {
    // This half has never caught anything. It is here so that it goes on
    // looking without anybody having to remember it.
    const { api } = await load(storedCompany(12), { id: 5, company_id: 99 });

    await expect(api.getSummary(12)).rejects.toBeInstanceOf(api.WrongCompanyError);
  });

  it('is refused when one row of a list belongs elsewhere', async () => {
    const rows = [
      { id: 1, company_id: 12 },
      { id: 2, company_id: 99 },
    ];
    const { api } = await load(storedCompany(12), rows);

    await expect(api.listCashFlow(12)).rejects.toBeInstanceOf(api.WrongCompanyError);
  });

  it('accepts an answer that names the company it was asked about', async () => {
    const rows = [
      { id: 1, company_id: 12 },
      { id: 2, company_id: 12 },
    ];
    const { api } = await load(storedCompany(12), rows);

    await expect(api.listCashFlow(12)).resolves.toHaveLength(2);
  });
});

describe('watching a bilag being read', () => {
  /** A `fetch` that answers with the given server-sent frames, in the given chunks. */
  async function streaming(chunks: string[]) {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        const encoder = new TextEncoder();
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    });
    const fetchMock = vi.fn(
      async () =>
        new Response(body, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        }),
    );
    vi.stubGlobal('window', { localStorage: fakeStorage(storedCompany(12)) });
    vi.stubGlobal('fetch', fetchMock);
    vi.resetModules();
    return { api: await import('./api'), fetchMock };
  }

  const FRAMES = [
    'event: dokumenter\ndata: [{"filename":"faktura.pdf","content_type":"application/pdf","chars":704}]\n\n',
    'event: beskrivelse\ndata: {"tekst":"Kjøp av"}\n\n',
    'event: beskrivelse\ndata: {"tekst":" ett batteri."}\n\n',
    'event: forslag\ndata: {"company_id":12,"description":"Faktura 1075, Kjøp av ett batteri."}\n\n',
  ];

  it('reports the pieces as they arrive and resolves with the proposal', async () => {
    const { api } = await streaming(FRAMES);
    const pieces: string[] = [];
    const documents: unknown[] = [];

    const suggestion = await api.interpretDocumentStreaming(12, [], undefined, {
      onDescription: (piece) => void pieces.push(piece),
      onDocuments: (read) => void documents.push(...read),
    });

    expect(pieces).toEqual(['Kjøp av', ' ett batteri.']);
    expect(documents).toHaveLength(1);
    expect(suggestion.description).toBe('Faktura 1075, Kjøp av ett batteri.');
  });

  it('does not care where the network split the bytes', async () => {
    // A frame arrives in two reads, and two frames arrive in one. Both happen,
    // and a parser that assumed one frame per chunk would drop text on the
    // floor — which on screen is a sentence missing its middle.
    const whole = FRAMES.join('');
    const { api } = await streaming([whole.slice(0, 90), whole.slice(90, 260), whole.slice(260)]);
    const pieces: string[] = [];

    const suggestion = await api.interpretDocumentStreaming(12, [], undefined, {
      onDescription: (piece) => void pieces.push(piece),
    });

    expect(pieces.join('')).toBe('Kjøp av ett batteri.');
    expect(suggestion.description).toBe('Faktura 1075, Kjøp av ett batteri.');
  });

  it('refuses a stream that carries another company’s proposal', async () => {
    // The same wall as every other call. A streamed answer is still an answer.
    const { api } = await streaming([
      'event: forslag\ndata: {"company_id":99,"description":"noe"}\n\n',
    ]);

    await expect(api.interpretDocumentStreaming(12, [], undefined)).rejects.toBeInstanceOf(
      api.ApiError,
    );
  });

  it('turns a mid-stream failure into the message it carried', async () => {
    // The status line said 200 before anything went wrong, so the failure can
    // only arrive as a frame — and it still has to reach the user as an ApiError.
    const { api } = await streaming([
      'event: dokumenter\ndata: []\n\n',
      'event: feil\ndata: {"melding":"Vi fikk ikke lest bilaget."}\n\n',
    ]);

    const error = await api
      .interpretDocumentStreaming(12, [], undefined)
      .catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(api.ApiError);
    expect((error as Error).message).toContain('ikke lest');
  });

  it('says so when the stream ends without a proposal', async () => {
    // A dropped connection. Half a painted form is worse than a sentence
    // saying the reading was cut short.
    const { api } = await streaming(['event: beskrivelse\ndata: {"tekst":"Kjøp"}\n\n']);

    await expect(api.interpretDocumentStreaming(12, [], undefined)).rejects.toThrow(/avbrutt/);
  });
});
