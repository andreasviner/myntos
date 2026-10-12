// @ts-check
import { fileURLToPath } from 'node:url';

import cloudflare from '@astrojs/cloudflare';
import sitemap from '@astrojs/sitemap';
import { defineConfig } from 'astro/config';

/**
 * `site` is what makes the sitemap emit absolute URLs, so it has to match the
 * canonical host exactly — a mismatch there is silently accepted by the build
 * and rejected by Search Console.
 */
export default defineConfig({
  site: 'https://myntos.no',
  output: 'static',
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
      // The application pages prerender as empty shells — everything on them
      // arrives after a session check — and the invitation page is reached
      // through a single-use token. Listing either invites Google to index a
      // page that says nothing, so the sitemap covers the public site only.
      filter: (page) =>
        !['/kontantstrom/', '/organisasjon/', '/parter/', '/invitasjon/', '/nytt-selskap/'].includes(
          new URL(page).pathname,
        ),
    }),
  ],
});
