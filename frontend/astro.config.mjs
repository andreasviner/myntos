// @ts-check
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
  integrations: [
    sitemap({
      // The application pages prerender as empty shells — everything on them
      // arrives after a session check — and the invitation page is reached
      // through a single-use token. Listing either invites Google to index a
      // page that says nothing, so the sitemap covers the public site only.
      filter: (page) =>
        !['/kontantstrom/', '/organisasjon/', '/parter/', '/invitasjon/'].includes(
          new URL(page).pathname,
        ),
    }),
  ],
});
