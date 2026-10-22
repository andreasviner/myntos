/**
 * The sitemap once listed every application page by default, because the list
 * that kept them out was written before most of them existed. These hold the
 * list in lib/indexing.ts to what the pages actually do.
 */

import { describe, expect, it } from 'vitest';

import { isIndexable, PUBLIC_PAGES } from './indexing';

/**
 * Every page's source, read the way Vite reads it for the build. `?raw` hands
 * over the text without compiling the `.astro`, which is all a grep needs.
 */
const sources = import.meta.glob<string>('../pages/**/*.astro', {
  query: '?raw',
  import: 'default',
  eager: true,
});

/** `../pages/kom-i-gang/index.astro` → `/kom-i-gang/`, `../pages/mva.astro` → `/mva/`. */
function pathOf(file: string): string {
  const route = file.replace('../pages/', '').replace(/\.astro$/, '').replace(/(^|\/)index$/, '');
  return route === '' ? '/' : `/${route}/`;
}

const pages = Object.entries(sources).map(([file, source]) => ({ path: pathOf(file), source }));

describe('isIndexable', () => {
  it('never indexes a page that sends a signed-out visitor to the login', () => {
    const redirecting = pages.filter((page) => page.source.includes('redirectToLogin'));
    // If this ever finds nothing, the grep is broken, not the site.
    expect(redirecting.length).toBeGreaterThan(10);
    expect(redirecting.filter((page) => isIndexable(page.path)).map((page) => page.path)).toEqual([]);
  });

  it('lists only pages that exist', () => {
    const onDisk = new Set(pages.map((page) => page.path));
    expect(PUBLIC_PAGES.filter((path) => !onDisk.has(path))).toEqual([]);
  });

  it('keeps out the pages a mailed link lands on', () => {
    for (const path of ['/bekreft-epost/', '/nytt-passord/', '/invitasjon/', '/fullfor-registrering/']) {
      expect(isIndexable(path)).toBe(false);
    }
  });

  it('indexes every moving guide, with or without the trailing slash', () => {
    expect(isIndexable('/kom-i-gang/fiken/')).toBe(true);
    expect(isIndexable('/kom-i-gang/fiken')).toBe(true);
    expect(isIndexable('/')).toBe(true);
  });

  it('indexes every comparison with another program', () => {
    expect(isIndexable('/sammenlign/')).toBe(true);
    expect(isIndexable('/sammenlign/tripletex/')).toBe(true);
  });
});
