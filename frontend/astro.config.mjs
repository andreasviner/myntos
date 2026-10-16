// @ts-check
import { fileURLToPath } from 'node:url';

import cloudflare from '@astrojs/cloudflare';
import sitemap from '@astrojs/sitemap';
import { defineConfig } from 'astro/config';

import { isIndexable } from './src/lib/indexing.ts';

/**
 * `site` is what makes the sitemap emit absolute URLs, so it has to match the
 * canonical host exactly — a mismatch there is silently accepted by the build
 * and rejected by Search Console.
 */
export default defineConfig({
  site: 'https://myntos.no',
  output: 'static',
  /**
   * Astro 7 made `'jsx'` the default, which applies React's whitespace rules:
   * a run of whitespace that holds a line break, at the edge of a text node, is
   * deleted. Every paragraph here wraps its source before a link, a <strong> or
   * a comment in braces, so the front page read
   * «under<a>Mva-melding</a>.SAF-T-filen» with no error anywhere. `true` is the lossless mode the site was written
   * for: it still strips whitespace, but never the space between two words.
   */
  compressHTML: true,
  adapter: cloudflare({
    // Cloudflare cannot run sharp at runtime; optimize images at build time.
    imageService: 'compile',
  }),
  vite: {
    server: {
      fs: {
        // /llms.txt and /llms/<skill>.md are read from docs/agents at build
        // time (src/pages/llms*), which is outside this project, so the dev
        // server has to be allowed to read it. That folder and nothing wider:
        // allowing the repository root would let the dev server hand out the
        // repo's .env and secrets/ to anything that can reach it.
        allow: [
          fileURLToPath(new URL('.', import.meta.url)),
          fileURLToPath(new URL('../docs/agents', import.meta.url)),
        ],
      },
    },
  },
  integrations: [
    sitemap({
      // The public site only. The application pages send a signed-out visitor
      // (and Google's renderer) to the login, and the pages a mailed link lands
      // on say nothing without its token. Which is which is decided in
      // src/lib/indexing.ts, the same list that writes the robots meta, because
      // the hand list that used to stand here named five pages and let every
      // later one into the sitemap.
      filter: (page) => isIndexable(new URL(page).pathname),
    }),
  ],
});
